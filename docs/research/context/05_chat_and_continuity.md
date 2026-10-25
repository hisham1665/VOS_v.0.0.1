# Chat and longitudinal continuity

## 1. Purpose of the chat layer

The interactive TUI is a persistent patient/study workspace around the same
analysis pipeline. It supports file attachment, repeated analysis, follow-up
questions, named-session reopen, differential inspection, and evidence-grounded
answers.

The UI is not the reasoning engine. `ChatEngine` is UI-agnostic and can be used
by the Textual application, a test, a future web UI, or a simple command loop.

## 2. Session lifecycle

```text
message -> attachment parser -> session upload copy/hash
        -> analyze complete session folder
        -> store report + structured assessment
        -> index evidence
        -> answer follow-up from layered context
```

Files are copied into `sessions/<name>_<id>/uploads/` and deduplicated by SHA-256.
Adding a file reruns the analysis over the whole session, but content-addressed
page/image/model caches avoid repeating unchanged work.

`SessionStore.open_named()` reopens an exact case-insensitive name. It does not
use fuzzy startup matching. `/resume` can explicitly select a saved ID/name;
`/new` creates a new session.

## 3. Session filesystem

```text
sessions/<slug>_<id>/
├── session.json
├── transcript.jsonl
├── terminal.log
├── logs/
│   ├── pipeline.log
│   └── console.log
├── uploads/
├── reports/run-001/
│   ├── report.md
│   ├── report.json
│   └── plan.md
└── memory/
    ├── chunks.jsonl
    ├── embeddings.json
    └── brief.md
```

`session.json` is atomically replaced. The transcript is append-only and a torn
final JSON line is ignored on resume. The single `emit()` path writes transcript
and terminal log before the UI renders an event, preserving an audit trail of
what was shown.

## 4. Layered context strategy

`ChatEngine.build_context()` assembles a bounded prompt from several layers:

1. **Persistent longitudinal differential:** hypotheses, exclusions, evidence,
   unresolved questions, limitations, and suggested tests.
2. **Clinician updates:** statements stored with message/decision source IDs.
3. **Session brief:** uploaded files, detected types, latest status, findings,
   impression, and recommended actions.
4. **Rolling summary:** older conversation folded into a compact summary.
5. **Evidence retrieval:** document pages, source reads, image reads, findings,
   and screening chunks.
6. **Earlier exchanges:** retrieved separately and labeled as potentially
   mistaken, so past assistant prose cannot silently become source evidence.
7. **Recent conversation:** the most recent turns verbatim.
8. **Current question.**

Each layer has a character budget from `ChatConfig`. This provides predictable
context size without requiring a single ever-growing transcript prompt.

## 5. Retrieval

The memory index supports:

- BM25-like exact-term ranking;
- British/American spelling normalization (`haemoglobin`/`hemoglobin`);
- clinical abbreviation expansion (`Hb`, `WBC`, `CRP`, `CXR`, etc.);
- optional CPU dense embeddings using MiniLM; and
- Reciprocal Rank Fusion of lexical and dense rankings.

Until the embedder is ready, retrieval uses BM25 only. Dense embedding startup is
on a daemon thread so a blocked model download cannot freeze the first answer.
For small sessions, remaining evidence is filled into the prompt after ranked
hits, reducing retrieval-miss risk.

## 6. Longitudinal differential behavior

Analysis-derived chunks are replaced by the latest run; raw document chunks are
content-addressed and retained. The structured assessment is persisted in
`session.json`, independent of the rolling summary.

The evaluator:

- validates exact source references;
- rejects unsupported numeric claims;
- keeps prior hypotheses if a new answer omits them;
- retains an explicit prior exclusion unless new contradictory evidence is
  cited;
- allows a direct clinician statement to mark an unambiguous saved hypothesis as
  ruled out even when the model endpoint is unavailable; and
- treats negative tests as reducing likelihood rather than automatically ruling
  out disease.

This creates a distinction between:

model-generated possibility
documented source diagnosis
explicit clinician exclusion
```

That distinction is central to longitudinal safety and should be described in a
paper separately from conversational memory.

## 7. User-visible commands

| Command | Function |
|---|---|
| `/attach` | Add files/folders and analyze |
| `/analyze` | Re-run all uploaded material |
| `/history` | Set history for a later analysis |
| `/files` | Show uploaded files |
| `/report` | Display a selected report |
| `/memory` | Show summary and indexed chunk counts |
| `/differential` | Show persistent hypotheses/exclusions/tests |
| `/literature on|off` | Toggle literature stage |
| `/sessions` / `/resume` | Inspect or switch sessions |
| `/new` / `/rename` | Create or rename a session |

## 8. Important continuity limits

- One message is processed at a time per session.
- An analysis cannot be canceled mid-run through the current TUI.
- New uploads rerun consolidation/reporting over the accumulated session.
- Uploaded raw files are copied as-is; de-identification of derived DICOM metadata
  does not mean the raw upload folder is de-identified.
- Hosted text/image routes can send patient data off-machine.
- Structured source validation is not semantic clinical entailment.
- Controlled disease identity, synonym ontology, dates, episode boundaries, and
  unit-aware longitudinal trends remain future work.
