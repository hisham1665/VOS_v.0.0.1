# AOS Shared Memory — Phase-wise Execution Plan

Companion to `docs/SHARED_MEMORY_PLAN.md`. That document is the *what and why*. This one is the *who, in what order, with which prompt*.

Every phase below is a self-contained unit of work for one coding agent. Phases are strictly ordered except where marked parallel-safe. Each phase ends with a green test suite; no phase is "done" because the code exists.

---

## 0. Conventions that apply to every phase

**Branch:** all work on `v0.0.6-shared-memory`, branched from `v0.0.5`.

**House style, to be repeated into every agent prompt:**
- Python 3.11+, Pydantic v2 (`BaseModel`, `Field`, `field_validator`), matching `src/aos_v0/core/models.py`.
- Module docstring explaining *why the module exists*, not what it does. Match the register of `core/models.py` and `agents/sub_agent.py` — those files explain design decisions, not mechanics.
- Comments only where a decision is non-obvious. No comment restates the line below it.
- No new third-party dependencies in Phases 0-6. Standard library only (`hashlib`, `json`, `threading`, `time`, `re`, `math`, `collections`).
- Type-annotate every public function.
- Never use `print()` in new core modules; emit events or return values. `print()` stays confined to `cli.py`, `graph_executor.py` and `sub_agent.py`, where it already lives.
- Tests go in `tests/`, named `test_<module>.py`, plain `pytest`, no fixtures framework beyond `pytest` builtins. Follow `tests/test_artifacts.py` for structure.

**Definition of done for every phase:**
1. `python -m pytest tests/ -q` passes with zero failures.
2. `python -c "import aos_v0.cli"` succeeds (no import cycles).
3. A smoke run still works: `aos "summarize the current state of quantum error correction"`.
4. The agent reports, in its final message, exactly which files it created or modified and why.

**Agent configuration legend:**

| Field | Meaning |
|---|---|
| `subagent_type` | Which agent definition to launch |
| `model` | Model override |
| `isolation` | `worktree` when the phase touches many files and you want a clean diff to review |
| `tools` | Tools the agent must have; inherited from the agent definition unless noted |

---

## Phase 0 — Baseline instrumentation

**Why first:** every later claim ("−40% injected characters") is unfalsifiable without a before number. This phase changes no behaviour. It only measures.

**Depends on:** nothing.

**Files:**
- `src/aos_v0/services/memory_eval.py` (new)
- `src/aos_v0/core/runtime.py` (add `MemoryTelemetry`, all fields defaulting to 0)
- `tests/test_memory_eval.py` (new)

**Deliverables:**
1. `MemoryTelemetry` dataclass on `Telemetry`: `admitted`, `discarded`, `recalls`, `reuse_hits`, `evictions`, `chars_injected`, `chars_saved_est`, `calls_skipped`, `controller_ms`. All default `0`.
2. `services/memory_eval.py` exposing `run_suite(prompts: list[str], memory: bool) -> EvalReport` and a `__main__` entry. It runs each prompt through `aos_v0.cli.run`, capturing per-request: wall-clock seconds, node count, sum of `len(node.input)` across all nodes, sum of `len(node.output)`, degraded-node count, and the final answer text.
3. A committed baseline: `docs/memory_baseline.json`, produced by running the suite once with memory off.
4. A fixed prompt suite of 6 prompts in `services/memory_eval.py` — two single-wave, two multi-wave text-only, one with a document artifact, one follow-up pair against the same artifact (to exercise session reuse later).

**Acceptance criteria:**
- `python -m aos_v0.services.memory_eval --out docs/memory_baseline.json` writes a valid report.
- Zero behaviour change: `git diff` touches only `runtime.py` (additive) and the two new files.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `sonnet` |
| `isolation` | none |
| `description` | `Baseline memory instrumentation` |

**Prompt:**

```
You are working in the AOS agent-orchestration repo at C:\Users\Asus\Desktop\project\aos0.0.5
on branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md sections 2 and 10 first, then read
src/aos_v0/cli.py, src/aos_v0/core/runtime.py and src/aos_v0/agents/graph_executor.py.

TASK: Build the measurement baseline for the shared-memory work. This phase changes
NO runtime behaviour. It only adds measurement.

1. In src/aos_v0/core/runtime.py add a MemoryTelemetry dataclass with integer/float
   fields all defaulting to 0: admitted, discarded, recalls, reuse_hits, evictions,
   chars_injected, chars_saved_est, calls_skipped, controller_ms. Add it to the
   existing Telemetry dataclass as a field named `memory`, using
   field(default_factory=MemoryTelemetry). Do not change any existing field.

2. Create src/aos_v0/services/memory_eval.py. It must expose:
     - PROMPT_SUITE: a list of 6 dicts, each {"id": str, "prompt": str,
       "inputs": dict | None, "follow_up": str | None}. Two prompts must produce
       single-wave graphs, two must produce multi-wave graphs, one must take a
       document artifact (use data/artifacts/ for a sample PDF path if one exists,
       otherwise leave inputs None and note it), and one must carry a follow_up
       string so session reuse can be exercised in a later phase.
     - @dataclass RequestMeasurement with: prompt_id, wall_clock_s, node_count,
       total_input_chars, total_output_chars, degraded_nodes, final_answer.
     - @dataclass EvalReport with: memory_enabled: bool, measurements:
       list[RequestMeasurement], and a totals() method returning aggregate sums.
     - run_suite(prompts=PROMPT_SUITE, memory: bool = False) -> EvalReport
     - a main() with argparse supporting --out PATH and --memory/--no-memory,
       writing the report as JSON.
   Register it in pyproject.toml [project.scripts] as aos-memory-eval.

   IMPORTANT for measurement: aos_v0.cli.run returns only the final string. To get
   per-node input/output character counts, subscribe to the event stream via the
   event_sink parameter of run(), OR — simpler and preferred — have run_suite build
   its own RequestContext and pass an event_sink that records agent_started and
   agent_completed payloads. If the payloads do not currently carry the character
   counts you need, DO NOT change graph_executor.py in this phase. Instead measure
   what the events already give you (node_count, per-node elapsed_ms, status) and
   leave total_input_chars/total_output_chars at 0 with a clear TODO comment naming
   Phase 4 as the phase that fills them in. Measuring wall-clock and node counts
   correctly now is worth more than a hack that pollutes the executor.

3. Write tests/test_memory_eval.py covering: EvalReport.totals() arithmetic,
   RequestMeasurement serialisation round-trip, and that PROMPT_SUITE has exactly
   6 entries with the required shape. Do NOT write a test that calls a live model.

4. Run the suite once for real with memory off and commit the output to
   docs/memory_baseline.json. If live model calls are unavailable in your
   environment, say so explicitly in your final report and leave the baseline file
   uncommitted rather than fabricating numbers.

STYLE: Python 3.11, standard library only, no new dependencies. Module docstring
explains why the module exists. Type-annotate public functions. No print() outside
main(). Match the code register of src/aos_v0/core/artifacts.py.

DONE WHEN: python -m pytest tests/ -q passes, python -c "import aos_v0.cli" succeeds,
and git diff shows only runtime.py (additive) plus the two new files.

Report back: files created/modified, whether the live baseline ran, and any
measurement you could not capture without touching the executor.
```

---

## Phase 1 — The bank itself

**Why:** everything else depends on this type. Built in isolation with no integration, so it can be tested exhaustively and cheaply.

**Depends on:** Phase 0 (for `MemoryTelemetry` only).

**Files:**
- `src/aos_v0/core/shared_memory.py` (new)
- `src/aos_v0/core/memory_summary.py` (new)
- `tests/test_shared_memory.py` (new)

**Deliverables:** the data model and bank from plan §3.3-3.4 — `MemoryEntry`, `MemoryKey`, `MemoryCandidate`, `AdmissionDecision`, `MemoryStats`, `SharedMemoryBank` — plus `build_summary_key()`.

**Acceptance criteria:**
- Bank is thread-safe under a 16-thread concurrent-write test.
- Eviction respects `max_entries` and `max_total_bytes` and evicts lowest-recall-then-oldest.
- `lookup_reusable` returns `None` on capability mismatch even when `input_hash` matches.
- Session-tier promotion moves the entry and preserves `recall_count`.
- `build_summary_key()` is deterministic: same input, same output, always.
- 100% of new public methods covered.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | none |
| `description` | `SharedMemoryBank core` |

**Prompt:**

```
You are working in the AOS agent-orchestration repo at C:\Users\Asus\Desktop\project\aos0.0.5
on branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md sections 3.3 and 3.4 in full. Read
src/aos_v0/core/models.py to absorb the Pydantic style and the docstring register
used in this codebase.

TASK: Implement the shared memory bank. This phase adds NO integration -- nothing
calls this code yet. Build it as a clean, exhaustively tested unit.

CREATE src/aos_v0/core/memory_summary.py:
  build_summary_key(node_id: str, capability: str, description: str,
                    value: str) -> str
  Deterministic, template-based, NO model calls. Format:
    "{capability} on '{description truncated to 60 chars}' -> {digest}"
  where digest is a compact factual gloss built from the value: the first
  non-empty line truncated to 80 chars, plus a size note like "(3.1k chars)".
  Collapse all whitespace. The whole key must be <= 200 chars. Same inputs must
  always produce the same output -- assert this in a test.

CREATE src/aos_v0/core/shared_memory.py with:

  class MemoryEntry(BaseModel) -- exactly the fields listed in plan section 3.3.

  class MemoryKey(BaseModel) -- the read-side projection a node sees:
    id, key, capability, node_id, tokens_est. No value field. This type existing
    separately is the point: it makes it structurally impossible to leak a value
    into a prompt that only asked for keys.

  class MemoryCandidate(BaseModel) -- what the write side proposes:
    node_id, capability, description, resource_id, status, value,
    agent_input, dna_demand: float, fanout: int.

  class AdmissionDecision(BaseModel) -- admitted: bool, score: float,
    reason: str, signals: dict[str, float], entry_id: str | None.

  class MemoryStats(BaseModel) -- entries, total_bytes, admitted, discarded,
    recalls, evictions, by_capability: dict[str, int].

  class SharedMemoryBank:
    __init__(self, request_id: str, session_bank: "SharedMemoryBank | None" = None,
             max_entries: int = 64, max_value_bytes: int = 32_000,
             max_total_bytes: int = 512_000, scope: str = "request")

    Thread-safe: every mutating method takes a threading.RLock. Waves run under
    ThreadPoolExecutor so this is a real requirement, not defensive coding.

    admit(self, candidate, score=1.0, admitted_by="forced", reason="") -> MemoryEntry
      Assigns the next sequential id ("M1", "M2", ...). Truncates value to
      max_value_bytes, appending a marker noting how many chars were dropped.
      Computes input_hash = sha256 of normalised f"{capability}|{agent_input}" and
      value_hash = sha256 of the normalised value. "Normalised" means: lowercase,
      collapse all runs of whitespace to one space, strip. Evicts if needed, then
      inserts. Returns the entry.

    keys(self, include_session: bool = True) -> list[MemoryKey]
      Request tier first, then session tier. Never returns values.

    get(self, entry_id: str, recalled_by: str | None = None) -> MemoryEntry | None
      When recalled_by is given, append it to recalled_by (no duplicates) and
      increment recall_count. This counter is the paper's usage set U -- it is what
      makes offline RL possible later, so it must be accurate.

    lookup_reusable(self, capability: str, input_hash: str) -> MemoryEntry | None
      Exact match on BOTH capability and input_hash. Never fuzzy. Searches request
      tier then session tier. Returns None if the entry's value is empty.

    contains_value_hash(self, value_hash: str) -> bool  -- searches both tiers.

    promote(self, predicate: Callable[[MemoryEntry], bool]) -> int
      Move matching entries into the session bank (if one was given), setting
      scope="session" and preserving recall_count. Returns how many moved. No-op
      returning 0 when session_bank is None.

    stats(self) -> MemoryStats
    clear(self) -> None

    Private _evict(self, needed_bytes: int) -> list[MemoryEntry]
      Evict until both max_entries and max_total_bytes are satisfied. Order:
      lowest recall_count first, ties broken by oldest created_at. Return the
      evicted entries so the caller can emit events. Never evict from the session
      tier on behalf of a request-tier insert.

CREATE tests/test_shared_memory.py. Required cases:
  - admit assigns sequential ids
  - admit truncates an over-size value and records the drop
  - contains_value_hash detects a duplicate after whitespace/case differences
  - lookup_reusable hits on exact (capability, input_hash)
  - lookup_reusable returns None when capability differs but hash matches
  - get(recalled_by=...) increments recall_count and does not duplicate node ids
  - eviction respects max_entries
  - eviction respects max_total_bytes
  - eviction order is lowest recall_count, then oldest
  - promote moves only matching entries and preserves recall_count
  - reads see session-tier entries when a session bank is attached
  - 16 threads each admitting 20 entries produce exactly 20*16 admissions minus
    evictions, with no lost updates and no exception
  - build_summary_key is deterministic and <= 200 chars
  - stats() arithmetic is correct after a mixed admit/evict sequence

STYLE: Pydantic v2. Python 3.11. Standard library only. Module docstring explains
why the module exists, in the register of core/models.py. Type-annotate every
public method. No print(). No logging calls -- event emission is Phase 4's job.

DONE WHEN: python -m pytest tests/ -q passes and python -c
"from aos_v0.core.shared_memory import SharedMemoryBank" succeeds.

Report back: the public API you settled on, any place the spec above was ambiguous
and what you chose, and the test count.
```

---

## Phase 2 — Admission controller + decision log

**Why:** the AddAll ablation in the paper is the warning. Selectivity is load-bearing and must exist before anything is wired into the executor.

**Depends on:** Phase 1.

**Files:**
- `src/aos_v0/core/memory_admission.py` (new)
- `tests/test_memory_admission.py` (new)

**Acceptance criteria:**
- Every hard-reject rule has a test that proves it fires.
- Adaptive threshold measurably rises as the bank fills.
- `DecisionLog` writes valid JSONL, one record per line, and never raises on an unwritable path (degrades to a no-op with a single warning).
- Deciding on a synthetic candidate takes < 5ms with 64 entries in the bank (assert with `time.perf_counter`).

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | none |
| `description` | `Heuristic admission controller` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md section 4 in full -- it specifies the exact signals
and weights. Read src/aos_v0/core/shared_memory.py (built in Phase 1) and
src/aos_v0/core/failure_manager.py (specifically _gap_marker, so you can detect its
output shape).

CONTEXT YOU MUST INTERNALISE: the source paper ablates three admission strategies.
Storing everything ("AddAll") made the system FASTER but LESS ACCURATE -- GAIA
accuracy fell 47.9 -> 44.2. Selectivity is the entire reason this component exists.
When in doubt, reject. A missed admission costs one redundant step; a bad admission
poisons every downstream prompt.

TASK: Implement the admission controller and its decision log.

CREATE src/aos_v0/core/memory_admission.py with:

  class AdmissionSignals(BaseModel):
    capability_prior, novelty, cost_prior, substance, fanout_prior -- all floats
    in [0,1]. Plus threshold: float and total: float.

  class AdmissionController(Protocol):
    def decide(self, candidate: MemoryCandidate,
               bank: SharedMemoryBank) -> AdmissionDecision: ...
    Declaring this protocol now is what lets an LLM judge or a learned controller
    drop in later without touching a single call site.

  CAPABILITY_PRIORS: dict[str, float] -- exactly the table in plan section 4:
    web_search 0.95, document_extraction 0.95, speech_transcription 0.95,
    vision 0.85, code.generation 0.80, text.summarization 0.45,
    answer.synthesis 0.05. Unknown capability defaults to 0.5.

  ERROR_MARKERS: a compiled regex union covering: Traceback, a line starting with
    "Error:", "[artifact '...' not found]", "I cannot", "I'm unable",
    "document extraction failed", "(no output produced)". Case-insensitive.

  class HeuristicController:
    __init__(self, base_threshold: float = 0.50, max_threshold: float = 0.85,
             min_substance_chars: int = 200,
             weights: dict[str, float] | None = None)
    Default weights: capability_prior 0.30, novelty 0.30, cost_prior 0.20,
    substance 0.10, fanout_prior 0.10.

    decide(candidate, bank) -> AdmissionDecision

    HARD REJECTS, evaluated first, each returning score 0.0 with a distinct
    reason string:
      1. candidate.status in {"degraded", "failed"}      reason "status:<status>"
      2. ERROR_MARKERS matches candidate.value            reason "error_marker"
      3. bank.contains_value_hash(hash of value)          reason "duplicate_value"
      4. len(candidate.value.strip()) < min_substance_chars  reason "trivial"

    SCORING when no hard reject fires:
      capability_prior -- CAPABILITY_PRIORS lookup on candidate.capability
      novelty          -- 1 - max Jaccard similarity between the token set of
                          candidate.value and the token set of each existing
                          entry's value. Tokenise by lowercasing and splitting on
                          non-alphanumerics, dropping tokens shorter than 3 chars.
                          To keep this bounded, compare against at most the 32 most
                          recent entries.
      cost_prior       -- candidate.dna_demand, clamped to [0,1]
      substance        -- linear ramp: 0.0 at min_substance_chars, 1.0 at 2000
                          chars, flat 1.0 above
      fanout_prior     -- min(candidate.fanout / 4.0, 1.0)
      total            -- weighted sum

    ADAPTIVE THRESHOLD (this replaces the paper's learned sparsity penalty):
      pressure = bank.stats().total_bytes / bank.max_total_bytes, clamped to [0,1]
      threshold = base_threshold + (max_threshold - base_threshold) * pressure
      Admit when total >= threshold.

    Return AdmissionDecision with admitted, score=total, reason, signals populated,
    entry_id left None -- the CALLER admits to the bank, not the controller. Keeping
    the controller free of side effects is what makes it swappable and testable.

  class DecisionLog:
    __init__(self, request_id: str, log_dir: str | Path = "log/memory_decisions",
             enabled: bool = True)
    record(self, candidate, decision, existing_keys: list[str],
           task_query: str, entry_id: str | None) -> None
      Appends one JSON line matching the schema in plan section 4.1. Truncate
      agent_input to 2000 chars and agent_output to 4000 chars.
    record_outcome(self, reward_proxy: dict, utilised: list[str],
                   runtime_s: float, total_injected_chars: int) -> None
      Appends the outcome record from plan section 4.1.
    On ANY OSError the log must degrade to a silent no-op after emitting one
    warnings.warn -- a failed log write must never break a user's request.
    Writes must be safe from multiple threads (take a lock).

CREATE tests/test_memory_admission.py. Required cases:
  - each of the four hard rejects fires, with the right reason string
  - a genuine web_search result with 2000 chars of novel content is admitted
  - an answer.synthesis candidate is rejected by score (prior 0.05 is too low)
  - a near-duplicate (same content, reworded slightly) scores low on novelty
  - threshold rises as bank.total_bytes rises -- assert a full bank rejects a
    candidate that an empty bank admits
  - decide() on a bank holding 64 entries completes in under 5ms
    (time.perf_counter, assert the delta)
  - DecisionLog writes one valid JSON object per line
  - DecisionLog with an unwritable directory does not raise
  - DecisionLog is safe under concurrent writes from 8 threads

STYLE: Pydantic v2 for the models, plain classes elsewhere. Standard library only.
Type-annotate everything public. No print(). Module docstring should state the
AddAll finding as the justification for the component's strictness.

DONE WHEN: python -m pytest tests/ -q passes.

Report back: the final weights and threshold you shipped, the measured decide()
latency at 64 entries, and any signal you found hard to compute from
MemoryCandidate alone (that tells Phase 4 what extra fields to populate).
```

---

## Phase 3 — Retrieval and context budget

**Why:** the read side. Parallel-safe with Phase 2 — different files, no shared symbols beyond Phase 1 types.

**Depends on:** Phase 1. (Can run concurrently with Phase 2.)

**Files:**
- `src/aos_v0/core/memory_retrieval.py` (new)
- `tests/test_memory_retrieval.py` (new)

**Acceptance criteria:**
- Injected text never exceeds `budget.total_chars`. Property-tested with randomised bank contents.
- Keys block is always present when the bank is non-empty; values block appears only when something clears `MIN_RELEVANCE`.
- A node never recalls an entry it produced.
- Recall is recorded exactly once per (entry, node) pair.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | none |
| `description` | `Memory retrieval + budget` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md section 5 in full, including the exact injection
format block. Read src/aos_v0/core/shared_memory.py (Phase 1),
src/aos_v0/core/models.py (Node, Graph) and src/aos_v0/core/graph_utils.py.

CONTEXT YOU MUST INTERNALISE: the single most valuable idea in the source paper is
that agents are shown SUMMARY KEYS ONLY, and full values are injected only when
selected. That is what keeps context bounded while still making every prior result
reachable. Your injection format must preserve that property exactly: every entry
contributes one line to the keys block; only selected entries contribute a value.

TASK: Implement memory retrieval and the context budget.

CREATE src/aos_v0/core/memory_retrieval.py with:

  @dataclass ContextBudget:
    total_chars: int = 12_000
    per_entry_chars: int = 4_000
    min_relevance: float = 0.15
    max_entries: int = 4

  @dataclass RecallResult:
    keys_block: str          # always rendered when the bank is non-empty
    values_block: str        # "" when nothing cleared min_relevance
    selected: list[str]      # entry ids whose values were injected
    chars_used: int
    scores: dict[str, float] # entry id -> relevance, for telemetry and the log

    def render(self) -> str  # the complete text to prepend to node.input, or ""

  class MemoryRetriever:
    __init__(self, budget: ContextBudget | None = None)

    select(self, node: Node, bank: SharedMemoryBank,
           ancestors: set[str] | None = None) -> RecallResult

    Algorithm, exactly as specified in plan section 5:
      1. query = node.description + " " + node.capability + " " +
         first 500 chars of (node.input or "")
      2. Tokenise query and each entry KEY (not value) by lowercasing, splitting
         on non-alphanumerics, dropping tokens under 3 chars and a small English
         stopword set you define in the module.
      3. Score by IDF-weighted overlap. Compute IDF across the set of memory keys
         currently in the bank: idf(t) = log(1 + N / (1 + df(t))). Relevance is
         the sum of idf over shared tokens, normalised by the sum of idf over
         query tokens, giving a value in [0,1].
      4. Boost +0.15 when entry.node_id is in `ancestors`.
         Boost +0.10 when entry.capability != node.capability -- cross-capability
         information is precisely what this node cannot produce for itself.
         Clamp the final score to 1.0.
      5. Exclude entries where entry.node_id == node.id.
         Exclude entries whose value already appears verbatim in node.input
         (check the first 200 chars of the value as a substring).
      6. Sort descending, take entries scoring >= budget.min_relevance, up to
         budget.max_entries, spending at most budget.total_chars total and
         budget.per_entry_chars per entry.
      7. For each taken entry call bank.get(entry_id, recalled_by=node.id) so the
         usage counter is updated. Call it exactly once per entry.

    Render keys_block as:
      "SHARED MEMORY -- results already produced by earlier steps in this workflow."
      "Available (summary only):"
      "  [M1] <key>"
      ...
    Render values_block as:
      "Retrieved in full for this step:"
      "  [M2] <key>"
      "<value>"
      and when the value was truncated, append
      "[...truncated, <n> chars remain in shared memory as M2]"
    render() joins the blocks and terminates with a line of "---" and a blank line.
    render() returns "" when the bank is empty.

    fit(self, text: str, limit: int) -> tuple[str, int]
      Helper returning (truncated_text, chars_dropped). Truncate on a word
      boundary when one exists within the last 100 chars of the limit.

CREATE tests/test_memory_retrieval.py. Required cases:
  - rendered output never exceeds budget.total_chars -- run this over 50 randomised
    banks (random entry counts, random value sizes up to 50k) and assert the bound
    holds every time
  - a node never recalls an entry whose node_id equals its own
  - ancestor boost changes ordering (construct two entries with equal base
    relevance, one an ancestor, assert the ancestor is selected)
  - cross-capability boost changes ordering under the same construction
  - an entry whose value already appears in node.input is excluded
  - min_relevance floor: an entry sharing no meaningful tokens is not selected
  - bank.get is called with recalled_by exactly once per selected entry -- assert
    via recall_count, not via mocking
  - keys block renders for every entry even when zero values are selected
  - render() returns "" for an empty bank
  - fit() truncates on a word boundary and reports the right dropped count

STYLE: dataclasses for the value types (no Pydantic needed here -- nothing is
serialised across a boundary), standard library only, type-annotated,
no print(). Module docstring explains the key-only-exposure rationale.

DONE WHEN: python -m pytest tests/ -q passes.

Report back: the exact rendered format you shipped (paste one example), the IDF
formula as implemented, and the worst-case chars_used you observed in the
randomised property test.
```

---

## Phase 4 — Executor integration (read + write) and events

**Why:** where it all becomes real. The riskiest phase, hence `isolation: worktree` so the diff can be reviewed before it lands.

**Depends on:** Phases 1, 2, 3.

**Files:**
- `src/aos_v0/core/events.py` (add 5 event types)
- `src/aos_v0/agents/graph_executor.py` (major edit)
- `src/aos_v0/cli.py` (bank lifecycle, summary print)
- `src/aos_v0/config.py` (settings)
- `tests/test_memory_integration.py` (new)

**Acceptance criteria:**
- With `AOS_MEMORY=off`, `graph_executor` behaviour is byte-identical to v0.0.5. Prove it with a test that runs a fake-registry graph both ways and diffs every `node.input`.
- With memory on, no `node.input` exceeds `total_chars + len(node.input_before_injection)`.
- Every admit/discard/recall/evict emits its event exactly once.
- The existing suite still passes untouched.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | `worktree` |
| `description` | `Wire memory into executor` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory, in an isolated worktree.

Read docs/SHARED_MEMORY_PLAN.md sections 2, 3.2, 5.1 and 9. Then read, completely:
  src/aos_v0/agents/graph_executor.py
  src/aos_v0/core/events.py
  src/aos_v0/cli.py
  src/aos_v0/config.py
  src/aos_v0/core/shared_memory.py     (Phase 1)
  src/aos_v0/core/memory_admission.py  (Phase 2)
  src/aos_v0/core/memory_retrieval.py  (Phase 3)

THIS IS THE HIGHEST-RISK PHASE. The executor is the heart of the system. The
governing constraint is: with memory disabled, behaviour must be BYTE-IDENTICAL
to v0.0.5. You will write a test that proves this, and that test is the gate.

TASK: Wire the shared memory bank into GraphExecutor, both read and write sides.

1. src/aos_v0/core/events.py -- add to EventType:
     MEMORY_ADMITTED = "memory_admitted"
     MEMORY_DISCARDED = "memory_discarded"
     MEMORY_RECALLED = "memory_recalled"
     MEMORY_REUSE_HIT = "memory_reuse_hit"
     MEMORY_EVICTED = "memory_evicted"
   Nothing else in that file changes.

2. src/aos_v0/config.py -- add memory settings with environment overrides:
     AOS_MEMORY            ("on"/"off", default "on")
     AOS_MEMORY_BUDGET     total_chars, default 12000
     AOS_MEMORY_PER_ENTRY  per_entry_chars, default 4000
     AOS_MEMORY_MAX_ENTRIES default 64
     AOS_MEMORY_LOG        ("on"/"off", default "on")
   Follow whatever pattern config.py already uses. Do not invent a new one.

3. src/aos_v0/agents/graph_executor.py -- the substantive change.

   GraphExecutor.__init__ gains:  memory: SharedMemoryBank | None = None,
                                  controller: AdmissionController | None = None,
                                  retriever: MemoryRetriever | None = None,
                                  decision_log: DecisionLog | None = None
   All default None. When memory is None, EVERY memory code path must be skipped
   -- not executed-and-discarded, skipped. Guard with `if self.memory is None`.

   READ SIDE, inside _run_node, AFTER node.input has been assembled by the
   existing logic and BEFORE the vision/audio/document injection blocks:

     a. Compute the transitive ancestor set for this node from graph.depends_on.
        Add a helper to core/graph_utils.py named ancestors(graph, node_id) ->
        set[str] if one does not already exist; that module is the right home for
        it, not the executor.
     b. recall = self.retriever.select(node, self.memory, ancestors)
     c. Prepend recall.render() to node.input.
     d. Emit MEMORY_RECALLED with node_id, selected ids, scores, chars_used --
        once per node, only when recall.selected is non-empty.

   REPLACE THE UNBOUNDED INJECTION. The three existing blocks that unconditionally
   prepend vision context, audio context and document context (currently around
   lines 136-190) inject full artifact text into EVERY downstream node with no
   size cap. This is the largest single token cost in the system. Change them so
   that, WHEN MEMORY IS ENABLED:
     - if the context text exceeds budget.per_entry_chars, admit the full text to
       the bank as a forced admission (admitted_by="forced", these are load-bearing
       artifacts and must never be filtered out), and inject only
       summary + head slice + a pointer line naming the entry id
     - otherwise inject as today
   WHEN MEMORY IS DISABLED these blocks must behave exactly as they do now. Do not
   "clean up" or restructure them beyond what this change requires.

   The same treatment applies to the parent-output assembly in the `elif
   node.depends_on:` branch: a parent output over per_entry_chars goes to the bank
   and is injected as summary + head + pointer.

   WRITE SIDE, inside _run_node, AFTER agent.perform(node) returns and after the
   existing artifact registration:

     a. Build a MemoryCandidate: node_id, capability, description, resource_id =
        node.bound_resource, status = node.status, value = node.output,
        agent_input = node.input, dna_demand = node.dna.ordinals.demand() if
        node.dna else 0.0, fanout = number of nodes transitively depending on this
        one (another graph_utils helper: descendants(graph, node_id)).
     b. summary = build_summary_key(...)
     c. decision = self.controller.decide(candidate, self.memory)
     d. If admitted: entry = self.memory.admit(candidate, score=decision.score,
        admitted_by="heuristic", reason=decision.reason); emit MEMORY_ADMITTED.
        Else: emit MEMORY_DISCARDED with the reason.
     e. self.decision_log.record(...) in both cases.
     f. Emit MEMORY_EVICTED for anything admit() evicted.

   Time the controller call with time.perf_counter and include controller_ms in
   the emitted payload. We claim negligible overhead in the plan; this is how we
   verify it rather than assert it.

   ALSO add to the AGENT_COMPLETED payload: input_chars=len(node.input or ""),
   output_chars=len(node.output or ""). Phase 0 left a TODO for exactly this --
   find it and remove it.

4. src/aos_v0/cli.py -- in run():
     - Build the request-tier bank when config says memory is on:
       SharedMemoryBank(request_id=request_id, session_bank=<None for now;
       Phase 6 supplies it>)
     - Build HeuristicController, MemoryRetriever, DecisionLog
     - Pass all four to GraphExecutor
     - After execution, call decision_log.record_outcome(...) with a reward proxy
       built from the graph: completed=True, degraded_nodes=count of
       node.status=="degraded", failures from the failure manager report
     - Add a _print_memory_summary(bank) next to the existing
       _print_routing_summary(graph): entries admitted, discarded, recalls,
       bytes held, top 3 entries by recall_count.
     - Add CLI flags --no-memory and --memory-budget N, parsed in the same style
       as the existing flags in _pop_flag / main.

5. CREATE tests/test_memory_integration.py. Required cases, using a FAKE registry
   and FAKE run functions -- no live model calls anywhere in this file:
     - THE GATE TEST: build a 3-node, 2-wave graph with a fake registry. Run it
       once with memory=None and once with memory enabled but with a controller
       stubbed to reject everything. Assert every node.input is byte-identical
       between the two runs. If this test cannot be made to pass, stop and report
       -- do not weaken the assertion.
     - with memory on and a permissive controller, a wave-1 node's output appears
       in the bank and a wave-2 node's input contains the keys block
     - no node.input exceeds its pre-injection length plus budget.total_chars
     - MEMORY_ADMITTED, MEMORY_DISCARDED, MEMORY_RECALLED each fire exactly once
       per triggering occasion (count events on a recording sink)
     - a 40k-char document context is banked and injected as a pointer, not in full
     - controller_ms appears in the emitted payload and is under 20ms

CONSTRAINTS:
  - Do not refactor anything you were not asked to change. The executor has
    subtle modality-routing logic; leave it alone.
  - Do not change any existing function signature in a backward-incompatible way.
    All new parameters are keyword-only with defaults.
  - Standard library only.
  - The existing tests in tests/ must still pass unmodified. If one fails, the
    integration is wrong -- fix the integration, not the test.

DONE WHEN: python -m pytest tests/ -q passes, python -c "import aos_v0.cli"
succeeds, and `aos --no-memory "<a simple prompt>"` produces the same shape of
output as it did before.

Report back: a summary of the diff to graph_executor.py, whether the gate test
passes, the measured controller overhead, and anything in the executor you had to
touch that was not in this brief.
```

---

## Phase 5 — Reuse short-circuit + retry memory

**Why:** the two highest-leverage wins, separated from Phase 4 so a regression is attributable.

**Depends on:** Phase 4.

**Files:**
- `src/aos_v0/agents/graph_executor.py` (short-circuit)
- `src/aos_v0/core/failure_manager.py` (retry context)
- `src/aos_v0/config.py` (`REUSABLE_CAPABILITIES`)
- `tests/test_memory_reuse.py` (new)

**Acceptance criteria:**
- A graph with two byte-identical `web_search` nodes executes exactly one model call.
- No `answer.synthesis` node is ever short-circuited.
- A node with `dna.ordinals.reasoning_depth >= 3` is never short-circuited.
- Substitution attempt 2 receives attempt 1's partial output.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | none |
| `description` | `Reuse short-circuit + retry memory` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md sections 6 and 7. Read
src/aos_v0/core/failure_manager.py completely, especially execute() and
_reformulate(), and the memory integration added to graph_executor.py in Phase 4.

CONTEXT: the source paper makes redundant work CHEAPER by sharing information. It
never skips a step. This phase goes further and skips the model call entirely when
the work is provably identical, which is the single largest saving available to us.
Provably identical means an exact hash match on (capability, normalised input) --
never fuzzy, never semantic. A false reuse hit returns a wrong answer silently,
which is far worse than a redundant call.

TASK A -- reuse short-circuit in GraphExecutor.

1. In config.py add REUSABLE_CAPABILITIES, default
   {"web_search", "document_extraction", "speech_transcription", "vision"},
   overridable via AOS_MEMORY_REUSE_CAPS as a comma-separated list, plus
   AOS_MEMORY_REUSE ("on"/"off", default "on").

2. In graph_executor._run_node, immediately BEFORE `agent = SubAgent(...)`:
     - skip entirely when memory is disabled, reuse is disabled, or
       node.capability not in REUSABLE_CAPABILITIES
     - ALSO skip when node.dna and node.dna.ordinals.reasoning_depth >= 3 --
       we want genuine reasoning regenerated, never replayed
     - compute input_hash exactly as SharedMemoryBank.admit computes it, so the
       two can never drift. Put the normalisation + hashing in ONE function in
       shared_memory.py (e.g. compute_input_hash(capability, agent_input)) and
       call it from both places. Do not reimplement it.
     - hit = self.memory.lookup_reusable(node.capability, input_hash)
     - on a hit: set node.output = hit.value, node.status = "done",
       node.performed_by = "memory-reuse", node.bound_resource = hit.resource_id,
       node.routing_mode = "memory"; call
       self.memory.get(hit.id, recalled_by=node.id) so the usage counter is
       updated; emit MEMORY_REUSE_HIT with node_id, entry_id, saved_chars; emit
       AGENT_COMPLETED as the normal path would so downstream telemetry and any
       UI subscriber stay consistent; then return node without constructing a
       SubAgent.
     - print a line in the existing house style, e.g.
       "[graph-executor] node '<id>' REUSED memory <entry_id> (0 model calls)"

TASK B -- retry memory in FailureManager.

3. FailureManager.__init__ gains `memory: SharedMemoryBank | None = None`,
   defaulting to None. cli.py passes the bank when it constructs the manager.

4. In execute(), when escalating to a substitute resource, if a previous attempt
   produced any partial output, prepend to the instruction handed to the
   substitute:

     "A previous attempt on resource '<resource_id>' produced this partial result
      before failing (<failure_class>). Use it; do not redo work it already
      completed:
      <partial output, truncated to 2000 chars>"

   The partial output should come from the attempt record the manager already
   keeps -- read the existing RecoveryAttempt / NodeOutcome structures and use
   what is there rather than adding new state. Only add state if nothing suitable
   exists.

5. Additionally, when the bank is present, run the retrieval read path for the
   retry: the substitute should see the memory keys block too. If this turns out
   to require threading the retriever into FailureManager, do it via an optional
   constructor parameter with a None default -- do not make it mandatory.

CREATE tests/test_memory_reuse.py, fake registry only, no live calls:
  - two nodes with identical capability and identical input: exactly one call to
    the fake run function (assert with a call counter)
  - identical input differing only in whitespace and case: still exactly one call
    (proves normalisation)
  - same input, different capability: two calls (no cross-capability reuse)
  - an answer.synthesis node is never short-circuited even with a matching entry
  - a node with reasoning_depth=4 is never short-circuited
  - a reuse hit increments the source entry's recall_count
  - a reuse hit emits both MEMORY_REUSE_HIT and AGENT_COMPLETED
  - with AOS_MEMORY_REUSE=off, two calls happen
  - FailureManager substitution: attempt 2's instruction contains attempt 1's
    partial output
  - FailureManager with memory=None behaves exactly as before (regression guard)

CONSTRAINTS: standard library only; no existing signature broken; every new
parameter keyword-only with a default; the full existing suite must pass unchanged.

DONE WHEN: python -m pytest tests/ -q passes.

Report back: where you put compute_input_hash, the reuse-hit rate you observed on
the Phase 0 prompt suite if you were able to run it, and whether retry memory
needed new state in FailureManager.
```

---

## Phase 6 — Session tier, interactive service, TUI

**Why:** turns "faster within one request" into "nearly free follow-ups", which is the improvement the paper explicitly lists as future work.

**Depends on:** Phase 5.

**Files:**
- `src/aos_v0/core/runtime.py` (`Session` owns the session bank)
- `src/aos_v0/interactive.py` (promotion at request end)
- `src/aos_v0/cli.py` (accept an injected session bank)
- `src/aos_v0/tui_commands.py`, `src/aos_v0/tui.py` (`/memory` commands)
- `tests/test_memory_session.py` (new)

**Acceptance criteria:**
- Upload a PDF, ask question A, ask question B → the document-extraction node is short-circuited on B.
- `/memory` lists entries; `/memory clear` empties the session tier.
- A fresh `Session` starts with an empty bank; nothing leaks between sessions.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `sonnet` |
| `isolation` | none |
| `description` | `Session-tier memory + TUI` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md sections 3.4 and 8 (improvement #4). Read
src/aos_v0/interactive.py, src/aos_v0/core/runtime.py, src/aos_v0/tui.py,
src/aos_v0/tui_commands.py, docs/INTERACTIVE_ARCHITECTURE.md, and the memory
integration in cli.py from Phase 4.

CONTEXT: the source paper's memory is per-task and discarded afterwards -- the
authors name cross-episode memory as future work. AOS's InteractiveService is
inherently multi-turn: a user uploads a PDF and then asks three questions about it.
Today all three re-extract the same PDF. A session tier fixes that. This is the
phase where a user actually feels the difference.

TASK:

1. src/aos_v0/core/runtime.py -- Session gains
     memory: SharedMemoryBank = field(default_factory=...)
   constructed with scope="session" and request_id = the session_id. Each Session
   must get its OWN bank; a shared default instance across sessions would leak
   one user's data into another's and is the bug to avoid here.

2. src/aos_v0/cli.py -- run() gains a keyword-only parameter
     session_memory: SharedMemoryBank | None = None
   passed as the session_bank of the request-tier bank it builds. Default None
   preserves today's pure-CLI behaviour exactly.

3. src/aos_v0/interactive.py -- InteractiveService.submit() passes
   self.session.memory into run(). After run() returns, promote surviving entries:

     bank.promote(lambda e: e.recall_count > 0 or
                            e.capability in PROMOTABLE_CAPABILITIES)

   Define PROMOTABLE_CAPABILITIES in config.py, default
   {"document_extraction", "speech_transcription", "web_search", "vision"}.
   Rationale to capture in a comment: these are expensive, input-determined and
   stable across turns. A summary or a synthesis is turn-specific and must not
   survive into the next question.

   Also update _record() to fold the new memory events into
   session.telemetry.memory: memory_admitted -> admitted, memory_discarded ->
   discarded, memory_recalled -> recalls (+ chars_injected), memory_reuse_hit ->
   reuse_hits (+ calls_skipped, + chars_saved_est), memory_evicted -> evictions.

4. TUI commands in tui_commands.py, surfaced in tui.py:
     /memory          -- table: id, capability, key (truncated to 60 chars),
                         recall count, size. Session tier first, then request tier.
     /memory clear    -- empty the session tier, confirm with a count
     /memory off|on   -- toggle AOS_MEMORY for subsequent submits in this session
   Follow whatever command-registration pattern tui_commands.py already uses.
   Do not invent a new dispatch mechanism.

5. CREATE tests/test_memory_session.py, fake registry only:
     - two sequential submit() calls with the same document artifact: the second
       run short-circuits the document_extraction node (assert the fake extractor
       was called once, not twice)
     - a summarization entry is NOT promoted (not in PROMOTABLE_CAPABILITIES and
       recall_count 0)
     - an entry with recall_count > 0 IS promoted regardless of capability
     - two independent Session objects have independent banks -- writing to one
       leaves the other empty
     - /memory clear empties the session tier and leaves the request tier untouched
     - session telemetry counters update correctly from a synthetic event stream

CONSTRAINTS: standard library only; no existing signature broken; new parameters
keyword-only with defaults; existing tests pass unchanged. Do not put any planning,
routing or agent logic into interactive.py -- docs/INTERACTIVE_ARCHITECTURE.md says
that file is an adapter and nothing more, and that holds here.

DONE WHEN: python -m pytest tests/ -q passes and aos-tui starts without error.

Report back: the promotion predicate you shipped, the TUI command surface, and
whether the two-submit reuse test passes.
```

---

## Phase 7 — Evaluation, tuning, and the RL export

**Why:** the paper's own lesson is that a fast memory system can be a worse system. This phase is the gate that decides whether memory defaults on.

**Depends on:** Phase 6.

**Files:**
- `src/aos_v0/services/memory_eval.py` (extend to A/B)
- `src/aos_v0/services/memory_dataset.py` (new — RL export)
- `docs/SHARED_MEMORY_RESULTS.md` (new)
- `tests/test_memory_dataset.py` (new)

**Acceptance criteria:**
- A/B report over the Phase 0 suite, memory off vs on, against every target in plan §10.
- `memory_dataset.py` converts `log/memory_decisions/*.jsonl` into training tuples `(context, decision, base_advantage, usage_bonus)` implementing the paper's Eq. 6 and 7.
- `docs/SHARED_MEMORY_RESULTS.md` states plainly whether each target was met, including any that were missed.

**Agent configuration:**

| Field | Value |
|---|---|
| `subagent_type` | `general-purpose` |
| `model` | `opus` |
| `isolation` | none |
| `description` | `Memory eval + RL dataset export` |

**Prompt:**

```
You are working in the AOS repo at C:\Users\Asus\Desktop\project\aos0.0.5 on
branch v0.0.6-shared-memory.

Read docs/SHARED_MEMORY_PLAN.md sections 4.1, 8 and 10. Read
src/aos_v0/services/memory_eval.py (Phase 0) and
src/aos_v0/core/memory_admission.py (Phase 2, for the DecisionLog schema).
Read docs/memory_baseline.json if it exists.

CONTEXT YOU MUST INTERNALISE: in the source paper's ablation, the AddAll variant
made the system faster AND less accurate -- GAIA 47.9 -> 44.2. Runtime improvement
alone is not success. If accuracy regresses on the suite, the correct outcome of
this phase is to report that and leave memory defaulting OFF. Do not tune until the
numbers look good and then report the tuned numbers as if they were the first run.
Report what you actually measured, including failures.

TASK A -- A/B evaluation.

1. Extend services/memory_eval.py with:
     compare(baseline: EvalReport, treatment: EvalReport) -> ComparisonReport
   reporting, per prompt and in aggregate: wall-clock delta (abs and %), total
   injected chars delta, model calls skipped, degraded-node delta, and an
   answer-similarity score between the two final answers (token-level Jaccard is
   sufficient -- we are detecting drift, not grading quality).
   Add a --compare A.json B.json mode to main().

2. Run the suite three ways and save each report:
     docs/memory_off.json   -- AOS_MEMORY=off
     docs/memory_addall.json -- memory on, controller stubbed to admit everything
     docs/memory_on.json    -- memory on, HeuristicController
   The AddAll arm exists to reproduce the paper's ablation on our system. If our
   heuristic does not beat AddAll on answer similarity to the memory-off arm, the
   controller is not earning its place and you must say so.

3. Write docs/SHARED_MEMORY_RESULTS.md: a table of every target in plan section
   10 with the measured value and a met / not-met verdict, the three-arm ablation
   table, and a short recommendation on whether AOS_MEMORY should default to "on".
   If live model calls are unavailable, say so at the top and present whatever
   you could measure offline instead of estimating.

TASK B -- RL dataset export.

4. CREATE src/aos_v0/services/memory_dataset.py, which turns the decision logs
   into the training tuples the source paper's method needs. Implement:

     load_requests(log_dir) -> list[RequestTrace]
       One RequestTrace per request_id: its decision records plus its outcome
       record. Skip requests with no outcome record.

     episode_reward(trace) -> float
       The paper's Eq. 5 analogue, R = R_agg + lambda_first * R_first. We have no
       benchmark grader, so use the reward proxy in the outcome record:
       R_agg = 1.0 if completed and degraded_nodes == 0, 0.5 if completed with
       degraded nodes, 0.0 otherwise. Use a runtime term in place of R_first:
       normalised inverse runtime against the median runtime across the corpus.
       Document this substitution in the module docstring -- a reader must not
       mistake it for the paper's actual reward.

     group_advantage(traces) -> dict[request_id, float]
       Eq. 6: (R - mu_R) / (sigma_R + eps), computed per prompt-group where a
       group is all traces sharing the same task query. Fall back to the whole
       corpus when a group has fewer than 2 members.

     shaped_advantage(trace, base_advantage, beta=0.5) -> list[StepSample]
       Eq. 7: A_hat_t = A_base + beta * 1[t in U and R > 0], where U is the
       outcome record's `utilised` list. One StepSample per decision record,
       carrying: context dict, decision ("ADMIT"/"DISCARD"), advantage, and the
       signals dict.

     export(log_dir, out_path) -> int
       Write JSONL of StepSamples, return the count. Add a main() with
       --log-dir and --out. Register as aos-memory-dataset in pyproject.toml.

   This module must NOT train anything, import torch, or add a dependency. Its
   only job is to make training possible later at zero execution cost. That is
   the whole reason the decision log exists.

5. CREATE tests/test_memory_dataset.py with synthetic JSONL fixtures:
     - load_requests pairs decisions with the right outcome record
     - a request with no outcome record is skipped
     - episode_reward returns 1.0 / 0.5 / 0.0 in the three cases
     - group_advantage is zero-mean within a group of equal rewards
     - group_advantage falls back to corpus stats for a singleton group
     - shaped_advantage applies beta only to utilised steps on positive-reward
       traces, and never to a zero-reward trace
     - export writes one valid JSON object per line and returns the right count

TASK C -- tuning, only if warranted.

6. If the memory-on arm regresses answer similarity by more than 10% against the
   memory-off arm, tune ONE thing at a time and record each attempt in
   SHARED_MEMORY_RESULTS.md: first raise min_relevance, then raise
   base_threshold, then shrink per_entry_chars. Do not change more than one
   parameter per measurement. If regression persists after all three, recommend
   memory default OFF and write down why -- that is a legitimate and useful
   outcome, not a failure of the phase.

CONSTRAINTS: standard library only -- statistics module for mean/stdev, no numpy.
Type-annotated. No print() outside main().

DONE WHEN: python -m pytest tests/ -q passes and docs/SHARED_MEMORY_RESULTS.md
exists with real measured numbers or an explicit statement of what could not be
measured.

Report back: the three-arm table, the met/not-met verdict per target, your
default-on recommendation, and the number of training samples exported.
```

---

## Appendix A — Phase dependency graph

```
P0 baseline
  |
  v
P1 bank  ------+
  |            |
  v            v
P2 admission   P3 retrieval        (P2 and P3 are parallel-safe)
  |            |
  +-----+------+
        v
      P4 executor integration   [worktree, review the diff before merge]
        |
        v
      P5 reuse + retry memory
        |
        v
      P6 session tier + TUI
        |
        v
      P7 eval + RL export       [gate: decides whether memory defaults on]
```

## Appendix B — Review checkpoints

Run `/code-review` after Phase 4 and after Phase 7. Phase 4 is the one that can quietly break the executor; Phase 7 is the one whose conclusions carry weight.

For Phase 4 specifically, review with:

```
/code-review high
```

and check three things by hand regardless of what the review says:
1. The gate test (memory-off byte-identical) genuinely asserts equality of every `node.input`, not just the final answer.
2. Every new parameter is keyword-only with a default, so no existing caller broke.
3. No memory code path executes when `self.memory is None` — guarded, not merely inert.

## Appendix C — What is deliberately NOT in this plan

| Deferred | Why |
|---|---|
| Learned controller (Qwen3-0.6B + LoRA + GRPO) | Needs a GPU, a reward harness and weeks of training. Phase 7's dataset export makes it cheap to start later; it is not needed to capture most of the win. |
| Embedding-based retrieval | The `embedding.generation` capability exists in the registry but its `run_fn` is declared-only (see `providers/hf.py`). Lexical retrieval over template-built keys is adequate because the keys share vocabulary with the querying node by construction. Revisit if Phase 7 shows retrieval misses. |
| LLM key-selection (the paper's `SELECTED_KEYS` prompt) | One extra model call per node, against an explicit zero-marginal-cost constraint. The `AdmissionController` protocol and the `MemoryRetriever` seam both leave room to add it behind a flag. |
| Cross-session disk persistence | Staleness, invalidation and privacy questions that have not been answered. Session tier captures most of the practical benefit. |
| K-parallel team execution (true M1-Parallel) | A much larger change to `GraphExecutor`. Worth noting that the shared memory built here is exactly the substrate that would make it affordable, so this plan is a prerequisite for that work rather than an alternative to it. |
