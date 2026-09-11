# AOS Shared Memory — Architecture Plan

**Status:** proposal, not yet implemented
**Target version:** v0.0.6
**Source inspiration:** *Learning to Share: Selective Memory for Efficient Parallel Agentic Systems* (arXiv 2602.05965v1, Fioresi et al., UCF)
**Decisions locked:** request + session scope; heuristic admission controller with decision logging for later RL; no extra LLM calls in v1.

---

## 1. What the paper does

LTS targets a specific waste in **M1-Parallel** style systems: `K` independent MagenticOne teams solve the same task in parallel, and each team independently re-discovers the same intermediate results (same web search, same table parse, same code). Parallel execution buys reliability but pays for it in wall-clock time.

Their mechanism has four moving parts:

| Part | What it is |
|---|---|
| **Global shared memory bank** `M = {(s_i, o_i)}` | Textual key-value store. Key `s_i` = short natural-language summary of a step. Value `o_i` = the raw agent output. |
| **Key-only exposure** | Orchestrators see *only the summary keys*, never all values. They ask for a value by key when they want it. This is what stops context from exploding. |
| **Learned admission controller** | Lightweight Qwen3-0.6B + LoRA. Input = frozen embeddings of `(task query, all existing memory keys, current step triplet: agent input, agent output, step summary)` projected into token space. Output = one binary token, `YES`/`NO`. ~0.2% of wall-clock. |
| **Stepwise RL training** | Group-relative advantage (GRPO-like), usage-aware reward shaping (`+beta` only for entries actually recalled *and* on a rewarded trajectory), plus an explicit sparsity penalty to stop degenerate "always admit". |

Their headline results (GPT-5.1, K=3): GAIA 47.9 -> 49.1 accuracy with runtime 1005s -> 781s (-25%); AssistantBench 24.0 -> 26.7 with 1389s -> 882s (-44.6%).

The ablation is the important part for us:

| Variant | AssistantBench acc / runtime | GAIA acc / runtime |
|---|---|---|
| No memory | 24.0 / 1389s | 47.9 / 1005s |
| **AddAll** (store everything) | 23.0 / 784s | 44.2 / 967s |
| **LLM judge** | 25.7 / 856s | 45.8 / 853s |
| **Learned (LTS)** | 26.7 / 882s | 49.1 / 792s |

**Reading:** storing everything is *fast but dumber*. Selectivity is what preserves accuracy. Any shared memory we build must be selective from day one, or we will reproduce the AddAll row — cheaper and worse.

Their own stated limitations, which are our opportunities: memory is per-task only (no session reuse), and the controller **never deletes or revises** entries.

---

## 2. Why AOS cannot copy it literally

AOS v0.0.5 is not a K-parallel-team system. `GraphExecutor.run()` builds waves via `build_waves(graph)` and runs the nodes *within one wave* concurrently (`src/aos_v0/agents/graph_executor.py`). There is one DAG, one orchestrator (`ManagerAgent`), one terminal synthesis node. There is no "team 2" to share with.

So "cross-team redundancy" does not exist here. What does exist:

| ID | Redundancy source in AOS today | Where |
|---|---|---|
| **R1** | Sibling nodes in the same wave doing near-identical work — e.g. the planner emits two `web_search` nodes whose queries overlap heavily. They run concurrently and never see each other. | `graph_executor.py` wave loop |
| **R2** | The substitution ladder re-runs a failed node on the next resource **with no memory of the first attempt**. A partial answer from attempt 1 is thrown away. | `failure_manager.py:259 execute()` |
| **R3** | Downstream nodes re-derive facts that are already sitting in a parent's raw output, because that output arrives as an undifferentiated blob with no index. | `graph_executor.py` `_run_node` input assembly |
| **R4** | In `InteractiveService`, every `submit()` re-plans and re-executes from zero. A follow-up question about the same uploaded PDF re-extracts the same PDF. | `interactive.py:submit()` |
| **R5** | **Unbounded context injection.** Today the executor concatenates *every* parent's full output, then unconditionally prepends *every* vision artifact, *every* audio transcription, and *every* extracted document to *every* non-matching node's input. No byte cap anywhere. On a 3-wave graph with a 40-page PDF this multiplies the same text across every node's prompt. | `graph_executor.py` lines ~104-190 |

R5 is the single largest cost item and the cheapest to fix, and it is *exactly* the problem the paper's key-only exposure solves. R1-R4 are step-level redundancy — the paper's actual target — reframed for a DAG.

**Therefore:** we keep the paper's *mechanism* (key/value bank, summary-only exposure, selective admission, usage-aware decision logging) and change its *scope* from "across teams" to "across nodes, across retries, and across requests in a session".

---

## 3. Target architecture

```
                       +----------------------------------------+
                       |        SharedMemoryBank                |
                       |  request tier  (ephemeral, per run)    |
                       |  session tier  (promoted, per session) |
                       |  entry = (key: summary, value: output) |
                       +------+--------------------------+------+
            propose(candidate)|                          |select(node, budget)
                              |                          |
                   +----------v---------+     +----------v---------+
                   | AdmissionController|     |  MemoryRetriever   |
                   |  (heuristic, free) |     | (lexical, free)    |
                   |  + DecisionLog     |     | + ContextBudget    |
                   +----------+---------+     +----------+---------+
                              |                          |
   +--------------------------v--------------------------v----------------+
   |                          GraphExecutor                               |
   |   wave loop                                                          |
   |     |- [READ]  build node.input under a byte budget, inject          |
   |     |          selected memory KEYS + only the values that scored    |
   |     |- [REUSE] input_hash lookup -> identical work already done?     |
   |     |          yes -> adopt output, skip the model call entirely     |
   |     |- SubAgent.perform(node) -- FailureManager reads memory on retry|
   |     +- [WRITE] summarize step -> propose to bank -> admit / discard  |
   +----------------------------------------------------------------------+
```

### 3.1 New modules

| File | Contents |
|---|---|
| `src/aos_v0/core/shared_memory.py` | `MemoryEntry`, `MemoryKey`, `MemoryCandidate`, `AdmissionDecision`, `MemoryStats`, `SharedMemoryBank` |
| `src/aos_v0/core/memory_summary.py` | `build_summary_key()` — deterministic, template-based, zero model calls |
| `src/aos_v0/core/memory_admission.py` | `AdmissionController` protocol, `HeuristicController`, `AdmissionSignals`, `DecisionLog` |
| `src/aos_v0/core/memory_retrieval.py` | `MemoryRetriever`, `ContextBudget`, `RecallResult` |
| `src/aos_v0/services/memory_eval.py` | A/B harness: run a prompt with memory on/off, diff tokens + wall-clock |

### 3.2 Modules touched

| File | Change |
|---|---|
| `core/events.py` | `+MEMORY_ADMITTED`, `+MEMORY_DISCARDED`, `+MEMORY_RECALLED`, `+MEMORY_REUSE_HIT`, `+MEMORY_EVICTED` |
| `core/runtime.py` | `MemoryTelemetry` dataclass on `Telemetry`; `Session` holds the session-tier bank |
| `agents/graph_executor.py` | Read side (budgeted injection), reuse short-circuit, write side (propose after each node) |
| `core/failure_manager.py` | `execute()` accepts an optional bank; substitution attempts receive the previous attempt's partial output |
| `cli.py` | Create the request-tier bank, pass it down, print a memory summary next to the routing summary |
| `interactive.py` | Own the session-tier bank; promote surviving entries at request end |
| `config.py` | Memory settings + env overrides |

### 3.3 Data model

```python
class MemoryEntry(BaseModel):
    id: str                  # "M1", "M2", ... stable within a bank
    key: str                 # the summary -- this is what nodes see
    value: str               # raw agent output -- injected only when selected
    node_id: str
    capability: str
    resource_id: str | None
    input_hash: str          # sha256(normalized capability + input) -- reuse lookup
    value_hash: str          # sha256(normalized value) -- exact-duplicate rejection
    tokens_est: int          # len(value) // 4, good enough for budgeting
    scope: str               # "request" | "session"
    admitted_by: str         # "heuristic" | "llm" | "learned" | "forced"
    admission_score: float
    created_at: float
    recalled_by: list[str]   # node ids that pulled the value -- the usage set U
    recall_count: int
```

`recalled_by` is not cosmetic. It is the paper's utilised-memory set `U(i)`, and it is the signal that makes usage-aware credit assignment possible later without re-running anything.

### 3.4 Bank semantics

- **Thread-safe.** Waves execute under `ThreadPoolExecutor`; every mutation takes an `RLock`.
- **Two tiers.** Writes land in the *request* tier. At the end of a successful request, entries with `recall_count > 0` or `capability in PROMOTABLE` (document extraction, transcription, web search) are promoted to the *session* tier. Reads see both, request tier first.
- **Bounded.** `max_entries=64`, `max_value_bytes=32_000` per entry, `max_total_bytes=512_000` per tier. Eviction is lowest `recall_count`, then oldest. Eviction emits `MEMORY_EVICTED`. *This is the deletion policy the paper explicitly does not have.*
- **Ephemeral by default.** Nothing touches disk except the decision log.

---

## 4. Admission controller (v1: heuristic, zero marginal cost)

The paper needs a learned controller because it has no structural signal — MagenticOne steps are untyped text. AOS has far more structure available for free: node status, DNA flags, DNA ordinals, bound resource, failure-manager detections, capability class. We exploit that instead of paying for a model call.

`HeuristicController.decide(candidate, bank) -> AdmissionDecision`

**Hard rejects (short-circuit, score = 0):**
1. `node.status` is `degraded` or `failed`.
2. Output contains a `FailureManager._gap_marker` signature.
3. Output matches error markers: `Traceback`, `^Error:`, `[artifact '...' not found]`, `I cannot`, `I'm unable`, `document extraction failed`.
4. `value_hash` already present in either tier — exact duplicate.
5. `len(value) < MIN_SUBSTANCE_CHARS` (default 200) — trivial step.

**Weighted score (admit when `>= threshold`, default 0.50):**

| Signal | Weight | Definition |
|---|---|---|
| `capability_prior` | 0.30 | Table lookup. `web_search` 0.95, `document_extraction` 0.95, `speech_transcription` 0.95, `vision` 0.85, `code.generation` 0.80, `text.summarization` 0.45, `answer.synthesis` 0.05 (terminal — nobody downstream consumes it). |
| `novelty` | 0.30 | `1 - max(jaccard(tok(value), tok(e.value)) for e in bank)`. Near-duplicate content is refused even when the hash differs. |
| `cost_prior` | 0.20 | `node.dna.ordinals.demand()` — expensive steps are the ones worth never repeating. |
| `substance` | 0.10 | Length band, ramping 200 -> 2000 chars then flat. Rewards real content, does not reward verbosity. |
| `fanout_prior` | 0.10 | Number of graph nodes that depend (transitively) on this node, normalised. Something many nodes need is by definition broadly useful. |

**Adaptive sparsity** — this replaces the paper's `L_sparse`: as the bank approaches `max_total_bytes`, raise the threshold linearly from `0.50` to `0.85`. Under pressure only outstanding entries get in. Same effect as a learned sparsity penalty, achieved with arithmetic.

### 4.1 Decision log — the RL dataset, collected for free

Every `propose()` writes one JSONL record to `log/memory_decisions/<request_id>.jsonl`:

```json
{"ts": 0, "request_id": "req_x", "node_id": "n3", "capability": "web_search",
 "context": {"query": "<task>", "existing_keys": ["...", "..."],
             "agent_input": "<trunc 2k>", "agent_output": "<trunc 4k>", "summary": "..."},
 "decision": "ADMIT", "score": 0.71, "signals": {}, "entry_id": "M4"}
```

At request end one outcome record is appended:

```json
{"ts": 0, "record": "outcome", "request_id": "req_x",
 "reward_proxy": {"completed": true, "degraded_nodes": 1, "failures": 0},
 "utilised": ["M1", "M4"], "runtime_s": 61.2, "total_injected_chars": 9840}
```

Those two record types are precisely `(c_t, z_t)` and `(R(tau), U(i))` from Equations 5-10 of the paper. Once enough runs accumulate, a learned controller can be trained **offline, on logs, with zero re-execution**. That is the whole reason to log from day one.

---

## 5. Retrieval (v1: lexical, zero marginal cost)

The paper asks an orchestrator LLM to emit `SELECTED_KEYS = [...]`. That is one extra model call per step. We skip it in v1.

`MemoryRetriever.select(node, bank, budget) -> RecallResult`

1. Build a query from `node.description + node.capability + head(node.input, 500)`.
2. Score each key with IDF-weighted token overlap against `entry.key` (the summary), with a `+0.15` boost when the entry's `node_id` is a transitive ancestor of `node` and a `+0.10` boost when `entry.capability` differs from `node.capability` (cross-capability information is what a node cannot produce itself).
3. Exclude entries produced by this node and entries whose `value_hash` already appears verbatim in `node.input`.
4. Greedily take entries above `MIN_RELEVANCE` (default 0.15) until `budget.total_chars` is spent, truncating each to `budget.per_entry_chars`.
5. Record recall on each taken entry (`recalled_by.append(node.id)`), emit `MEMORY_RECALLED`.

**Injection format** — keys always, values only when selected. This is the paper's core insight applied verbatim:

```
SHARED MEMORY -- results already produced by earlier steps in this workflow.
Available (summary only):
  [M1] web_search on 'find 2024 revenue figures' -> 8 results, top source sec.gov
  [M2] document_extraction on 'uploaded 10-K' -> 41 pages, 138k chars extracted
  [M5] vision on 'chart image' -> bar chart, 5 series, y-axis in $M

Retrieved in full for this step:
  [M2] document_extraction on 'uploaded 10-K' -> 41 pages, 138k chars extracted
  <value, truncated to 4000 chars>
  [...truncated, 134000 chars remain in shared memory as M2]

---
```

Everything a node does not need costs it one line instead of 138k characters.

### 5.1 Context budget replaces unbounded injection

`ContextBudget(total_chars=12_000, per_entry_chars=4_000)` governs the whole of `node.input`. The current unconditional blocks in `graph_executor.py` (vision context, audio context, document context, parent outputs) are all routed through it. A parent output that exceeds `per_entry_chars` is **admitted to the bank in full** and injected as summary + head slice + a pointer. Nothing is lost; it just stops being copied `N` times.

---

## 6. Reuse short-circuit — the biggest win, and it is not in the paper

The paper reduces the *number* of redundant steps by making information available. We can go further and **eliminate the model call outright** when the work is provably identical.

Before `agent.perform(node)`:

```python
input_hash = sha256(normalize(f"{node.capability}|{node.input}"))
hit = bank.lookup_reusable(node.capability, input_hash)
if hit and node.capability in REUSABLE_CAPABILITIES:
    node.output = hit.value
    node.status = "done"
    node.performed_by = "memory-reuse"
    node.bound_resource = hit.resource_id
    node.routing_mode = "memory"
    emit(MEMORY_REUSE_HIT, saved_tokens=hit.tokens_est)
    return node          # zero model calls, zero dollars, near-zero latency
```

`REUSABLE_CAPABILITIES` defaults to `{web_search, document_extraction, speech_transcription, vision}` — capabilities whose output is a function of their input. `answer.synthesis` and anything with `reasoning.deep` are excluded: we want those regenerated.

This fires hardest on R4 (session follow-ups) and on R1 (sibling nodes whose planner-generated inputs collapse to the same normalised string).

---

## 7. Retry memory — R2

`FailureManager.execute()` currently discards attempt *n*'s output when it escalates to attempt *n+1*. With the bank in hand, the reformulated instruction for the substitute resource carries the prior partial result:

```
A previous attempt on resource '<id>' produced this partial result before failing
(<failure_class>). Use it; do not redo work it already completed:
<partial, truncated to 2000 chars>
```

Small change, disproportionate payoff: the substitution ladder is where AOS currently burns the most duplicated tokens.

---

## 8. Improvements over the paper, explicitly

| # | Improvement | Why it beats LTS as published |
|---|---|---|
| 1 | **Execution short-circuit on `input_hash`** | LTS makes redundant work *cheaper*; hash reuse makes it *free*. Paper never skips a step, only informs it. |
| 2 | **Heuristic controller using DNA + node status** | LTS pays a 0.6B model per step because MagenticOne steps are untyped. AOS already types every node (flags, ordinals, status, bound resource). Same selectivity, zero marginal cost. |
| 3 | **Deletion and eviction policy** | The paper names this as an explicit limitation ("does not reason about memory deletion or revision"). LRU-by-recall eviction plus `max_total_bytes` gives us bounded memory they do not have. |
| 4 | **Session tier** | The paper's memory is per-task and thrown away. AOS's `InteractiveService` is a multi-turn session; a promoted tier makes "ask another question about the same PDF" nearly free. Also named future work in the paper. |
| 5 | **Adaptive sparsity threshold** | Replaces the learned `L_sparse` penalty with pressure-based thresholding — same anti-"always admit" pressure, no training. |
| 6 | **Byte-budgeted context assembly** | LTS controls context growth only via key/value exposure. AOS additionally hard-caps total injected characters per node, so worst-case prompt size is bounded by construction, not by policy. |
| 7 | **Retry-aware memory** | LTS has no failure ladder. AOS does, and it is a redundancy source the paper does not model at all. |
| 8 | **Decision log as an RL dataset from day one** | LTS collects 5 trajectories per question per epoch by re-running the system on an H100. We collect the identical `(c_t, z_t, R, U)` tuples as a side effect of normal operation. Training later costs no execution. |
| 9 | **Cross-capability recall boost** | LTS scores relevance uniformly. Preferring entries from a *different* capability targets exactly the information a node cannot generate for itself. |

---

## 9. Cost and risk

**Marginal cost of v1: zero LLM calls.** Summaries are template-built, admission is arithmetic, retrieval is lexical. The only new I/O is one JSONL append per node.

CPU overhead per node: one sha256, one Jaccard sweep over <=64 entries, one IDF scoring pass over <=64 keys. Comfortably under 10ms — well inside the paper's own 0.2% overhead figure.

| Risk | Mitigation |
|---|---|
| Stale session-tier entry answers a changed question | Session entries carry `input_hash`; reuse requires an exact hash match, never a fuzzy one. `/memory clear` in the TUI. |
| Lexical retrieval misses a semantically relevant entry | Keys are template-built from the node description, so they share vocabulary with the querying node by construction. Phase 7 can add embedding scoring via the existing `embedding.generation` capability. |
| Memory injection confuses a node (the AddAll failure mode) | Hard rejects on failed/degraded/error output; strict relevance floor; A/B harness in Phase 0 and Phase 7 measures accuracy, not just runtime. |
| Behaviour change breaks existing runs | Everything sits behind `AOS_MEMORY` / `--no-memory`. Default on only after Phase 7 shows no regression. |

## 10. Success criteria

Measured by `services/memory_eval.py` over a fixed prompt suite, memory-off vs memory-on:

| Metric | Target |
|---|---|
| Total characters injected into node prompts | **-40% or better** |
| Wall-clock per request (multi-wave graphs) | **-15% or better** |
| Model calls skipped by reuse, session follow-ups | **>= 1 per follow-up request** |
| Final-answer quality | **no regression** on the suite (this is the AddAll trap; it is the gate) |
| Memory admission rate | **30-60%** of steps (LTS lands at 84.9%; our hard rejects are stricter, lower is expected and fine) |
| Cross-node recall rate | **> 20%** of admitted entries recalled at least once |
