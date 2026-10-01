# Interactive chat (TUI)

A full-screen terminal chat for Medical AOS: attach scans, PDFs and notes,
ask questions, ask follow-ups that remember everything in the session, and
come back to the same conversation later.

```bash
python main.py --chat                     # new session
python main.py --chat --name patient-001  # new, named session
python main.py --resume                   # pick a saved session from a list
python main.py --resume 3f9a2c            # resume by id (or unique name)
python main.py --sessions                 # list saved sessions, no UI
python -m tui                             # same as --chat
```

```
 ✚ Medical AOS   patient-001 · id 3f9a2c · 2 file(s) · 1 run(s) · HIGH / immediate
┌──────────────────────────────────────────────────────────┬─────────────────────┐
│        ▌You                                              │ Session             │
│        ▌Is there pneumonia? anything abnormal in labs?   │ patient-001         │
│        ▌📎 download.jpg  📎 lab_report.pdf                │ id 3f9a2c           │
│ ╭ Analysis ─────────────────────────────────────────────╮│ History  …          │
│ │ Analysis run 1 — 🟠 HIGH · triage IMMEDIATE            ││ Latest analysis     │
│ │ | Severity | Finding | Seen on | Confidence |          ││ run 1: HIGH         │
│ ╰───────────────────────────────────────────────────────╯│ Memory              │
│ ▌Medical AOS · openai/gpt-oss-120b                       │ 31 chunks, 4 turns  │
│ ▌Haemoglobin: 11.8 g/dL (low) [lab_report.pdf#page=1]    │ Files …             │
├──────────────────────────────────────────────────────────┴─────────────────────┤
│ ╭ Ask a question, or attach with @path / drag files here…  (/help)           ╮ │
│  ⠹ wave 3: running 1 node(s) [read0]                                           │
│ ^q Quit  ^b Sidebar  ^o Sessions  ^n New  ^l Clear                             │
```

---

## Using it

### Attaching material

Any of these, anywhere in a message:

| Form | Example |
|---|---|
| `@path` | `@scans/ct.jpg what is this?` |
| `@"path with spaces"` | `@"C:\Users\me\Desktop\chest x ray.png"` |
| Drag & drop | Drag files or a folder onto the terminal window; the terminal pastes the quoted path. |
| Bare path | `C:\studies\patient-004\lab_report.pdf summarise` |
| Folder | Attaches every supported file inside, recursively (max 500). |

Supported: PNG, JPG, BMP, TIFF, WebP, DICOM (`.dcm` or extensionless with
`DICM` magic), PDF, TXT/MD/LOG, CSV/TSV, JSON, XML/HTML, YAML, RTF, DOCX, XLSX.
`@` + `→` (right arrow) accepts path completion; `/` + `→` completes commands.

Attaching files **copies** them into the session folder and runs the full
analysis pipeline (triage → screening cascade → VLM reads → document reads →
consolidation → report) over *everything* uploaded so far. Text in the same
message becomes the referring question. Model outputs are cached by file
hash, so re-analysis after adding one file only pays for the new file plus
the consolidation step. A file already in the session (same content) is
skipped.

### Commands

| Command | |
|---|---|
| `/help` | Everything below |
| `/attach <paths>` | Add files/folders without asking a question |
| `/analyze [question]` | Re-run the analysis on all uploads |
| `/history <text>` | Clinical history passed to every analysis (`/analyze` to apply) |
| `/files` | Uploaded files |
| `/report [n]` | Full report of the latest (or *n*-th) run |
| `/memory` | Rolling summary + what is indexed |
| `/literature on\|off` | Web literature lookup during analysis (off by default — slow) |
| `/sessions` | Saved sessions |
| `/resume [id\|name]` | Switch session (picker without argument) |
| `/new [name]` · `/rename <name>` | New session · rename this one (folder too) |
| `/clear` · `/quit` | Clear screen (history kept) · exit |

Keys: `Ctrl+O` sessions, `Ctrl+N` new, `Ctrl+B` sidebar, `Ctrl+L` clear,
`↑/↓` input history, `Ctrl+Q` quit, `Ctrl+P` command palette.

---

## Where everything is stored

One folder per session, named after the session:

```
sessions/
└── patient-001_3f9a2c/
    ├── session.json        id, name, history, files, analysis runs, rolling summary
    ├── transcript.jsonl    every event, one JSON per line (source of truth)
    ├── terminal.log        human-readable copy of everything shown, headed with
    │                       session id + name; every line tagged [3f9a2c]
    ├── logs/
    │   ├── pipeline.log    every pipeline log record (all stages, all models)
    │   └── console.log     anything libraries print to stdout/stderr
    ├── uploads/            the attached files (de-duplicated by SHA-256)
    ├── reports/run-001/    report .md + .json + plan.md for each analysis run
    └── memory/
        ├── chunks.jsonl    retrieval index (documents, reads, findings, turns)
        ├── embeddings.json cached dense vectors
        └── brief.md        latest structured session brief
```

* **What is logged.** The engine has a single `emit()` path; it writes the
  transcript and terminal log *before* the UI renders anything, so the logs
  cannot miss something that was shown. Commands, user messages (with raw
  input and attachment names), progress lines, analyses, answers (with model
  and cited sources), memory events and errors are all recorded.
* **Crash safety.** Every write is append-and-close; a torn final JSON line
  is skipped on resume; `session.json` is written atomically (tmp + replace).
* **`sessions/` is git-ignored** — it holds patient data.

### Resuming

`--resume` / `/resume` reloads `session.json`, replays `transcript.jsonl` into
the chat view, and reopens the memory index. Nothing is recomputed: the
assistant immediately answers follow-ups with the same files, findings,
summary and history it had before.

---

## How the chat remembers (context strategy)

The industry-standard answer to "the assistant should remember what was
uploaded and said" is not a bigger prompt; it is **layered memory with a
budget per layer** (the pattern behind ChatGPT/Claude Projects and most
production RAG agents). Every answer is built from five sections, in this
order (`ChatEngine.build_context`):

| # | Layer | What | Why | Budget (`MAOS_CHAT_*`) |
|---|---|---|---|---|
| 1 | **Session brief** — structured state | Session name, clinical history, every file with detected modality / document type, latest status, triage, all findings with sources, impression, next steps | Exact, small, always relevant. Never retrieved, so it can never be missed. | `BRIEF_CHARS` 6000 |
| 2 | **Rolling summary** — summary-buffer memory | LLM-written summary of conversation that no longer fits verbatim | Long chats keep their thread at constant cost | `SUMMARY_CHARS` 3000 |
| 3 | **Evidence** — hybrid RAG over the session | Document pages, per-image VLM reads, document reads, findings, screening results | Grounds answers in source text with citable refs (`labs.pdf#page=2`) | `RETRIEVED_CHARS` 9000, top `RETRIEVE_K` 8 |
| 4 | **Earlier exchanges** — episodic memory | Up to 2 retrieved past Q&A turns, **labelled "may contain mistakes"** | Recall of what was discussed long ago, without letting an old wrong answer pose as evidence | (small) |
| 5 | **Recent conversation** — window memory | Last turns verbatim | Resolves "and the second one?" | `RECENT_CHARS` 9000 |

**Retrieval (layer 3)** is hybrid:

* **BM25** (in-house, no dependency) for exact terms — drug names, lab
  analytes, values. The tokenizer normalises British/American spelling
  (`haemoglobin`↔`hemoglobin`, `oedema`↔`edema`, …) and expands common
  abbreviations (`Hb`, `WBC`, `CRP`, `Na`, `K`, `Cr`, `CXR`, …).
* **Dense embeddings** (`sentence-transformers/all-MiniLM-L6-v2`, on CPU) for
  paraphrase ("inflammation markers" → CRP/ESR).
* Fused with **Reciprocal Rank Fusion** (k = 60), which needs no score
  calibration between the two.
* **Fill-to-budget:** after the ranked hits, the remaining session material is
  added in original order until the evidence budget is full. A typical
  session (a few files) therefore has **all** of its material in context and
  cannot suffer a retrieval miss; a large one degrades gracefully to top-k.

**Folding (layer 2).** After each answer, if the un-summarised conversation
exceeds `RECENT_CHARS`, everything except the last two exchanges is folded
into the rolling summary by the LLM (`/memory` shows it). If that call fails,
history simply stays verbatim.

**Re-analysis.** Analysis-derived chunks (reads, findings, screening, report)
are replaced wholesale by each new run — a re-analysis supersedes the old one.
Raw document text is content-addressed and kept.

**Answer rules** (system prompt): cite `[source]` for every patient-specific
statement; say plainly when the material does not contain the answer; label
general medical knowledge as such; keep automated image findings marked as
preliminary; never invent values.

### A real failure this design fixed

During testing the clinician asked *"What was the haemoglobin value?"* The PDF
said `Hemoglobin 11.8 g/dL`. BM25 saw `haemoglobin` and `hemoglobin` as
different words, the dense model was still downloading, and the assistant
answered "no haemoglobin value is available". That wrong answer was then
indexed as a conversation turn — memory poisoning. Three changes followed,
each with a regression test (`tests/test_chat.py::TestRetrievalRegressions`):
spelling/abbreviation normalisation, fill-to-budget evidence, and separating
past answers from evidence. The same question now returns
`11.8 g/dL (low) [lab_report.pdf#page=1]`.

---

## Architecture

```
tui/app.py ──(worker thread)──▶ chat/engine.py ──▶ pipeline.analyze_study
   ▲  renders events               │  emit() ──▶ session.log_event ──▶ transcript.jsonl
   └──────── call_from_thread ◀────┘                                  terminal.log
                                   ├─▶ chat/attachments.py   (parse @paths, folders)
                                   ├─▶ chat/session.py       (folders, files, metadata)
                                   ├─▶ chat/memory.py        (index, BM25+dense, RRF)
                                   └─▶ capabilities.hf_backend.chat_text (Groq → HF)
```

* The **engine is UI-agnostic** — the TUI, a test, or a future web UI drive
  the same `ChatEngine.handle(text)`. The analyzer and LLM are injectable;
  `tests/test_chat.py` runs the whole flow (including the Textual app via its
  headless pilot) with stubs and no network.
* The TUI runs the engine on a **worker thread** so the interface stays live
  (spinner + latest pipeline stage in the status line), turns console logging
  off (`set_console_logging(False)`) and captures library prints into
  `logs/console.log`, so nothing tears the full-screen display.
* The dense embedder loads on a **background daemon thread**. An answer never
  waits for a model download — a stalled Hugging Face transfer once hung the
  chat at exactly that point; until the model is ready, retrieval is BM25-only.

## Limits

* One message is processed at a time per session; the input is disabled
  while working. Analyses cannot be cancelled mid-run.
* Every new upload re-runs consolidation and the report over the whole
  session (seconds to a minute; image reads are cached).
* Answers come from the hosted text LLM (Groq, HF fallback): session text
  (not images) is sent to it. Image reads go to the VLM route configured in
  `docs/MODELS.md`. For real patient data use local backends (Ollama).
* Uploaded files are copied as-is (DICOM metadata is de-identified only in the
  derived images, not in `uploads/`). Treat `sessions/` as PHI.
* No streaming of answers yet (the full answer appears when ready).
