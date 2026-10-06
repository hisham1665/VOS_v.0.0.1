# Recovered project context

Review date: 2026-10-05. Working branch: `feature/aos-patient-continuity`.

## Sources recovered

| Assistant | Local source | Relevant context |
|---|---|---|
| Claude | `C:\Users\Asus\.claude\projects\C--Users-Asus-Desktop-project-medical-aos\` | Two project transcripts and project memory files |
| OpenCode | `C:\Users\Asus\.local\share\opencode\opencode.db` | Six project sessions, including a saved exploration session |
| Omnirush | `/home/hisham/.local/share/omnirush/opencode.db` | A greeting session and the current upgrade request |

The SQLite session/message/part tables were queried read-only, filtered to the
Medical AOS project directory. Claude project JSONL and memory were read from
the matching project. Textual project-history extracts were placed under
`/tmp/omnirush/` for review. This document records their architectural context;
credential files are not part of that context.

## Development history relevant to the current problems

1. **Initial OpenCode work:** identified conflicting `aos_v0` imports and an
   unavailable hard-coded Groq checkpoint. The account was using
   `openai/gpt-oss-120b`. Rate limiting was already observed during development.
2. **Claude pipeline rewrite:** consistent imports, DICOM/document ingest,
   bounded concurrency, content-hash caching, structured report assembly,
   failure containment and severity floors. Earlier tests found a cold-start
   registry race and an incorrect NORMAL status when nothing was read.
3. **Claude screening cascade:** replaced the universal pneumonia classifier
   with modality/region routing, general screening and independent specialist
   checks. Models failing development benchmarks were disabled; unsupported
   modalities are deliberately reported as not screened. Later work added
   TorchXRayVision as a chest specialist.
4. **Claude chat TUI:** per-session folders, logs, resumable transcripts,
   layered summary/recent/RAG context. Live testing exposed a blocking embedding
   download and a haemoglobin/hemoglobin retrieval mismatch; both were fixed.
   Named startup was explicitly designed as *new named session*, which explains
   the repeated `patient-001` conversations now reported by the user.
5. **OpenCode re-analysis investigation:** plain text asking to analyze an image
   again reached only the chat answer path. Bare image names were not attachments;
   duplicate attachments were skipped; only `/analyze` performed a rerun. It also
   documented the ultrasound/abdominal-CT coverage gaps and the permanently
   memoized Ollama availability probe.
6. **Later OpenCode branch discussion:** the user wanted the medical branch to
   reflect `feature/multiformat-analysis`. The current working tree was on that
   feature branch at commit `35d01ca` when this task began.
7. **Omnirush:** the current request adds explicit AOS-foundation auditing,
   combined differential reasoning, follow-up exclusion memory and efficiency.
   There is no independent earlier Omnirush implementation narrative in the
   recovered project sessions.

## Conclusions carried into this implementation

- Retain the modality-matched cascade; an unsupported screener does not imply
  that a VLM or a written imaging report contains no evidence.
- Keep clinical interpretation in registered capabilities and generic selection
  in a domain-neutral layer.
- Reuse the existing persistent session store and evidence index, adding a
  structured differential rather than relying on a lossy conversation summary.
- Evaluate artifacts, provenance and degradation as well as fluent prose.
- Measure the current document-only runs: earlier image preprocessing benchmarks
  do not explain their 40–60s synthesis stages.

Detailed implementation and remaining foundations are in
[AOS_IMPROVEMENTS.md](AOS_IMPROVEMENTS.md); archived case findings are in
[TEST_RUN_ANALYSIS.md](TEST_RUN_ANALYSIS.md).
