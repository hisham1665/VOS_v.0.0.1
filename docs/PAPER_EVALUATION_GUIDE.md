# Paper-evaluation guide

This guide describes the intended operator workflow for AOS paper evaluation.

## Exam configuration

Create an exam file with `aos exam init`, then define one `Question` per
question. Include:

- `question_id`, `text`, `max_marks`, and `question_type`.
- `answer_key.reference_answers` for conventional solutions.
- `answer_key.expected_concepts` for meaning-based marking.
- `answer_key.accepted_alternatives` for equivalent terminology.
- `answer_key.keywords` as supporting evidence, never the sole decision rule.
- `answer_key.concept_relationships` for required, implied, alternative, or conflicting concepts.
- `rubric.criteria` for mark-bearing criteria.
- `partial_credit_rules`, `negative_marks`, and `special_rules` when needed.

Validate before processing papers. Warnings are useful: a rubric whose total
does not equal the question's maximum may be intentional, but should be
confirmed by the examiner.

## One paper

```bash
aos exam evaluate exam.json scans/student_001.pdf --out outputs/student_001.json
```

The AOS-backed paper DAG is:

```text
intake/OCR -> layout + identity + segmentation
                   -> primary evaluator ┐
                   -> independent verifier ├-> reconciliation -> report
```

The graph is routed through exam Capability DNA and the registry. Local
structural transports make this path deterministic and key-free. Production
operators can register OCR/VLM/LLM alternatives under the same flags.

## Review policy

The evaluator does not turn uncertainty into a mark. It routes these cases to
review with the evidence attached:

- low OCR or answer-extraction confidence;
- missing, ambiguous, or multiple answers;
- identity uncertainty or roster mismatch;
- mathematical/diagram uncertainty;
- independent-agent disagreement;
- exhausted recovery attempts.

The proposed mark, if one exists, remains visible to the reviewer. A human
decision is recorded separately and reporting uses the resolved decision.

## Batch and reports

```bash
aos exam batch exam.json scans/ --checkpoint outputs/batch.json --results outputs/results.jsonl
aos exam report exam.json outputs/results.jsonl --out reports
```

The checkpoint is atomically written after papers complete. Re-running resumes
unfinished papers and isolates failures. Reports never re-run grading and never
invent missing marks.

## Human review queue

The review package stores cards and an append-only audit history:

```bash
aos exam review review_store list
aos exam review review_store stats
aos exam review review_store resolve paper-001:Q3 \
  --decision accept --reviewer examiner-1
```

Use `modify --marks N` when the examiner changes the proposed mark. Use
`escalate` when the evidence needs a second human decision. Reporting consumes
these decisions without re-running the model pipeline.

## Research evaluation

Use `benchmark_dataset/entries.jsonl` as the human-annotated source of truth.
Each entry should carry the question, student answer, human marks, maximum
marks, question type, and optional OCR truth. Run the benchmark command to
produce MAE, exact/tolerance agreement, false acceptance/rejection, semantic
acceptance, agent agreement, recovery, dynamic-selection, OCR, and throughput
measurements.

Do not treat the generated sample dataset as evidence for a paper; it only
checks that the research pipeline is connected.
