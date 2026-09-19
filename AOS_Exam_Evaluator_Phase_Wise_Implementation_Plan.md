# AOS Exam Evaluator — Phase-wise Implementation Plan

## Project Objective

Build an AI-powered exam evaluation system on top of the existing **Adaptive Domain Independent Multi-AI Agent Orchestration System (AOS)**.

The system will:

- Accept individual exam papers and large batches/folders of answer sheets.
- Perform OCR and document understanding.
- Extract student name, roll number, register number and other metadata.
- Identify and map questions and answers.
- Compare answers against a configurable answer key.
- Evaluate semantic/conceptual meaning rather than exact wording.
- Support rubrics and partial marks.
- Use two independent evaluation agents.
- Detect and resolve disagreement.
- Use the existing AOS Capability DNA.
- Use existing Dynamic Model Selection.
- Use existing DAG generation/orchestration.
- Use existing fault-recovery strategies.
- Calculate evaluation confidence.
- Route uncertain cases to human review.
- Generate CSV files, reports and analytics.

## Core Principle

The system must evaluate **meaning and conceptual correctness**, not merely exact textual similarity.

Example:

**Answer key:**

> TCP is a connection-oriented transport-layer protocol that provides reliable communication.

**Student answer:**

> TCP establishes a connection between endpoints and ensures that data is delivered reliably.

The answer should be recognized as semantically correct even though the wording differs.

---

# High-Level Architecture

```text
                         EXAMINER
                            |
                            v
                 +----------------------+
                 | Exam Configuration   |
                 | Questions            |
                 | Answer Keys          |
                 | Rubrics              |
                 | Marking Rules        |
                 +----------+-----------+
                            |
                            v
                    +---------------+
                    |      AOS      |
                    | Task Planner  |
                    | Capability    |
                    | Registry      |
                    | Capability DNA|
                    | Model Select  |
                    | DAG Generator |
                    | Fault Recovery|
                    +-------+-------+
                            |
              +-------------+-------------+
              |             |             |
              v             v             v
             OCR          Vision       Reasoning
              |             |             |
              +-------------+-------------+
                            |
                            v
                  Answer Sheet Parser
                            |
                            v
                    Question Mapping
                            |
                  +---------+---------+
                  |                   |
                  v                   v
            Evaluator Agent     Verifier Agent
                Agent 1             Agent 2
                  |                   |
                  +---------+---------+
                            |
                            v
                  Agreement Analysis
                            |
                  +---------+---------+
                  |                   |
                Agree             Disagree
                  |                   |
                  |             Reconciliation
                  |                   |
                  +---------+---------+
                            |
                            v
                       Confidence
                            |
                  +---------+---------+
                  |                   |
              Auto Accept        Human Review
                  |                   |
                  +---------+---------+
                            |
                            v
                      Final Results
                            |
             +--------------+--------------+
             |              |              |
             v              v              v
            CSV          Reports       Analytics
```

---

# PHASE 0 — Freeze and Understand Existing AOS

## Objective

Understand the existing AOS implementation and integrate the exam evaluator without replacing the current orchestration algorithms.

## Existing Components to Preserve

- AOS Controller
- Task representation
- Capability Registry
- Capability DNA
- Dynamic Model Selection
- Constraint Satisfaction
- DAG Generation
- DAG Execution
- Agent management
- Fault recovery
- Model adapters
- Result aggregation
- Logging
- Configuration

## Tasks

1. Audit the current AOS repository.
2. Document the current architecture.
3. Identify interfaces for adding a new domain.
4. Document the Capability DNA schema.
5. Document the Capability Registry.
6. Document model registration.
7. Document dynamic model selection.
8. Document DAG creation.
9. Document execution and failure handling.
10. Define an `ExamEvaluationTask`.
11. Add integration tests without changing existing behavior.

## Deliverables

```text
docs/
└── AOS_INTEGRATION_SPEC.md
```

Also produce:

- Existing architecture diagram.
- Exam evaluation task interface.
- Integration contract.
- Regression tests.

## Success Criteria

All existing AOS functionality continues to pass its current tests.

---

# PHASE 1 — Hugging Face Model Survey and Selection

## Objective

Identify suitable Hugging Face models for every capability required by the exam evaluator.

Model selection must be **capability-driven**, using the requirements represented by Capability DNA.

## Required Capabilities

```text
DOCUMENT_OCR
HANDWRITING_OCR
DOCUMENT_LAYOUT
VISION_UNDERSTANDING
SEMANTIC_EMBEDDING
SEMANTIC_EVALUATION
GENERAL_REASONING
MATHEMATICAL_REASONING
DIAGRAM_UNDERSTANDING
MULTILINGUAL_UNDERSTANDING
STUDENT_ID_EXTRACTION
QUESTION_SEGMENTATION
EVALUATION_RECONCILIATION
```

## Candidate Model Information

For every candidate record:

```text
Model Name
Hugging Face Repository
Architecture
Parameter Count
Modality
Input Format
Output Format
Context Length
Language Support
OCR Capability
Handwriting Capability
Vision Capability
Reasoning Capability
Quantization Options
VRAM Requirement
RAM Requirement
CPU Feasibility
Inference Framework
License
Expected Latency
Known Limitations
Fallback Candidates
```

## Evaluation Criteria

Compare models using:

- Accuracy
- OCR quality
- Semantic evaluation quality
- Handwriting quality
- Mathematical reasoning quality
- Diagram understanding
- Multilingual capability
- Latency
- VRAM usage
- RAM usage
- Model size
- Reliability
- Deployment complexity
- Quantization support
- Cost

## Benchmark Dataset

Create:

```text
model-benchmark/
├── exact_answers/
├── paraphrased_answers/
├── partial_answers/
├── incorrect_answers/
├── spelling_errors/
├── handwritten/
├── mathematical/
├── diagrams/
├── multilingual/
└── difficult_documents/
```

## Metrics

Measure:

- OCR accuracy
- Semantic similarity
- Concept extraction accuracy
- Evaluation agreement
- Latency
- VRAM usage
- RAM usage
- Failure rate

## Deliverables

```text
MODEL_SELECTION.md
MODEL_BENCHMARK.md
```

Also create the initial:

- Model registry
- Capability Registry entries
- Capability DNA definitions

## Success Criteria

Every important capability has:

- Primary model
- Fallback model
- Resource profile
- Capability DNA
- Selection constraints

---

# PHASE 2 — Exam Configuration and Answer-Key Engine

## Objective

Create the configuration system that defines how an examination should be evaluated.

The answer key must represent **knowledge and marking criteria**, not just one textual answer.

## Exam Structure

```text
Exam
├── Metadata
├── Questions
├── Answer Keys
├── Rubrics
├── Marking Rules
├── Negative Marking
├── Partial Credit Rules
└── Evaluation Settings
```

## Question Fields

Each question should support:

```text
Question ID
Question Text
Maximum Marks
Question Type
Expected Concepts
Reference Answers
Accepted Alternatives
Keywords
Rubric
Partial Credit Rules
Negative Marking
Special Evaluation Rules
```

## Example

```json
{
  "question_id": "Q5",
  "question": "Explain deadlock.",
  "max_marks": 10,
  "type": "long_answer",

  "expected_concepts": [
    "mutual exclusion",
    "hold and wait",
    "no preemption",
    "circular wait"
  ],

  "rubric": [
    {
      "criterion": "definition",
      "marks": 2
    },
    {
      "criterion": "mutual_exclusion",
      "marks": 2
    },
    {
      "criterion": "hold_and_wait",
      "marks": 2
    },
    {
      "criterion": "no_preemption",
      "marks": 2
    },
    {
      "criterion": "circular_wait",
      "marks": 2
    }
  ]
}
```

## Supported Question Types

- MCQ
- True/False
- Fill in the blank
- Short answer
- Long answer
- Numerical
- Mathematical derivation
- Diagram
- Table
- Programming/code
- Mixed/sub-question format

## Answer-Key Layers

### Reference Answer

Conventional model answer.

### Expected Concepts

Concepts the student must demonstrate.

### Accepted Alternatives

Valid terminology and valid alternative approaches.

### Keywords

Supporting evidence only.

### Concept Relationships

Relationships between required concepts.

### Rubric

How marks are distributed.

## Important Rule

The answer key defines **what knowledge is expected**, not the exact sentence the student must write.

## Deliverables

- Exam configuration schema
- Answer-key schema
- Rubric schema
- Exam configuration API
- Configuration UI

## Success Criteria

An examiner can configure an entire examination without changing source code.

---

# PHASE 3 — Document Intake and Pre-processing

## Objective

Accept real exam answer sheets and normalize them before OCR.

## Supported Inputs

```text
PDF
JPG
JPEG
PNG
TIFF
ZIP
Folder
```

## Pipeline

```text
Upload
  |
  v
File Validation
  |
  v
Page Extraction
  |
  v
Image Normalization
  |
  v
Rotation Detection
  |
  v
Deskew
  |
  v
Quality Detection
  |
  v
Page Ordering
```

## Quality Checks

Detect:

- Blurry pages
- Low resolution
- Excessive darkness
- Excessive brightness
- Rotation
- Skew
- Cropping
- Blank pages
- Duplicate pages
- Damaged files
- Unsupported formats

## Edge Cases

- Missing page
- Duplicate page
- Page out of order
- Blank page
- Multiple answer booklets
- Mixed page sizes
- Partial scan
- Corrupted PDF

## Deliverables

- Document ingestion service
- Pre-processing pipeline
- Quality scoring
- Page metadata system

---

# PHASE 4 — OCR and Document Understanding

## Objective

Convert physical answer sheets into machine-readable content.

OCR answers:

> What is physically written on the page?

OCR should not decide marks.

## Pipeline

```text
Page Image
    |
    v
OCR Model
    |
    +---- Text
    +---- Bounding Boxes
    +---- Confidence
    |
    v
Layout Analysis
    |
    +---- Text Regions
    +---- Question Regions
    +---- Answer Regions
    +---- Tables
    +---- Diagrams
```

## OCR Output

```json
{
  "page": 2,
  "blocks": [
    {
      "type": "text",
      "text": "TCP is...",
      "bbox": [100, 200, 800, 400],
      "confidence": 0.93
    }
  ]
}
```

## Low OCR Confidence

```text
OCR Model A
     |
     v
Confidence Check
     |
     +---- High → Continue
     |
     +---- Low
             |
             v
        OCR Fallback
             |
             v
        Vision Fallback
             |
             v
        Human Review
```

## Vision Branch

Special handling for:

- Handwriting
- Equations
- Diagrams
- Tables
- Mathematical notation
- Poor handwriting

## Important Rule

OCR failure must never automatically become zero marks.

## Deliverables

- OCR service
- OCR model adapters
- Layout model adapter
- Confidence extraction
- OCR fallback mechanism
- Structured OCR output

---

# PHASE 5 — Answer Sheet Structuring

## Objective

Transform OCR output into a structured student answer sheet.

## Student Information

Extract:

```text
Student Name
Roll Number
Register Number
Class
Department
Exam
Subject
```

## Question Mapping

Identify:

```text
Q1
Q2
Q3(a)
Q3(b)
Q4
...
```

## Out-of-Order Answers

Example:

```text
Q1
Q2
Q5
Q3
Q4
```

The system must still correctly map each answer.

## Continuation Answers

Example:

```text
Q3 → Page 2
     Page 3
     Page 4
```

These pages must be combined into one logical answer.

## Important Edge Cases

- Answer in margin
- Answer on next page
- Question number OCR error
- Multiple subquestions
- Missing question number
- Crossed-out answer
- Multiple attempts
- Blank answer
- Answer outside normal answer region

## Student Identity Validation

If an institutional roster exists:

```text
Roll No | Name
23CS041 | Rahul
23CS042 | Anjali
23CS043 | Student
```

Use the roster as validation evidence.

## Output

```json
{
  "student": {
    "name": "Anjali",
    "roll_no": "23CS042"
  },

  "answers": [
    {
      "question_id": "Q1",
      "pages": [1],
      "text": "...",
      "regions": [...]
    }
  ]
}
```

## Deliverables

- Answer-sheet parser
- Student identity extractor
- Question detector
- Question-to-answer mapper
- Page grouping mechanism

---

# PHASE 6 — Semantic Evaluation Engine

## Objective

Evaluate answers using meaning, concepts, correctness, completeness and the configured rubric.

## Core Pipeline

```text
Question
   +
Student Answer
   +
Answer Key
   +
Rubric
      |
      v
Text Normalization
      |
      v
Concept Extraction
      |
      v
Semantic Analysis
      |
      v
Concept Matching
      |
      v
Rubric Evaluation
      |
      v
Marks
```

## Evaluation Levels

### Level 1 — Surface Analysis

Check:

- Keywords
- Terminology
- Named entities
- Expressions
- Equations

Surface similarity is supporting evidence only.

### Level 2 — Semantic Analysis

Determine whether different wording expresses the same idea.

### Level 3 — Conceptual Correctness

Determine whether required concepts are actually demonstrated.

## Example

Answer key:

```text
TCP
- Transport layer
- Connection-oriented
- Reliable communication
```

Student:

```text
TCP creates a connection between endpoints
and makes sure data is delivered reliably.
```

Evaluation:

```text
Connection-oriented → Satisfied
Reliable             → Satisfied
Transport layer      → Missing
```

The result must follow the configured rubric.

## Partial Credit

Example:

```text
Question = 10 marks

Concept A → 3
Concept B → 3
Concept C → 2
Explanation → 2
```

Student:

```text
A → Correct
B → Correct
C → Partial
Explanation → Missing
```

The engine applies the configured partial-credit rules.

## Mathematical Evaluation

For numerical questions evaluate:

```text
Formula
Substitution
Calculation
Units
Final Answer
```

A wrong final answer should not automatically erase correct intermediate work when the rubric awards step marks.

## Important Rules

The evaluator should:

- Accept valid paraphrases.
- Accept valid terminology variations.
- Handle spelling mistakes.
- Accept valid alternative approaches.
- Distinguish spelling errors from conceptual errors.
- Distinguish incomplete answers from OCR failures.
- Support multilingual responses when configured.
- Never automatically give zero because OCR failed.
- Avoid penalizing a correct alternative method.
- Preserve evidence supporting every mark decision.

## Deliverables

- Semantic evaluator
- Concept matcher
- Rubric evaluator
- Partial-mark engine
- Alternative-answer handling
- Mathematical evaluation capability

---

# PHASE 7 — Two-Agent Evaluation

## Objective

Use two independent agents to improve reliability.

## Agent 1 — Primary Evaluator

Input:

```text
Question
Student Answer
Answer Key
Rubric
```

Output:

```json
{
  "marks": 4,
  "max_marks": 5,
  "concepts_satisfied": [
    "reliability",
    "connection_oriented"
  ],
  "missing_concepts": [
    "transport_layer"
  ],
  "reasoning": "...",
  "confidence": 0.91
}
```

## Agent 2 — Independent Verifier

Agent 2 independently evaluates the same evidence.

It should initially **not see Agent 1's result**.

Output:

```json
{
  "marks": 3,
  "max_marks": 5,
  "concepts_satisfied": [
    "reliability"
  ],
  "missing_concepts": [
    "transport_layer",
    "connection_oriented"
  ],
  "reasoning": "...",
  "confidence": 0.84
}
```

## Why Independence Matters

If Agent 2 sees Agent 1's marks first, it can become biased toward the first evaluation.

## Comparison

```text
Agent 1 → 4/5
Agent 2 → 3/5
       |
       v
Difference = 1
       |
       v
Disagreement Analysis
```

## Deliverables

- Agent 1 evaluator
- Agent 2 verifier
- Independent evaluation prompts
- Structured agent output schemas
- Agent comparison engine

---

# PHASE 8 — AOS Dynamic Orchestration

## Objective

Integrate the complete exam evaluation workflow into the existing AOS.

The exam evaluator must not hardcode a fixed model pipeline.

## Architecture

```text
ExamEvaluationTask
       |
       v
AOS Controller
       |
       v
Capability Requirements
       |
       v
Capability Registry
       |
       v
Capability DNA
       |
       v
Dynamic Model Selection
       |
       v
DAG Generation
       |
       v
Execution
```

## New Capabilities

```text
DOCUMENT_OCR
HANDWRITING_OCR
DOCUMENT_LAYOUT
STUDENT_ID_EXTRACTION
QUESTION_SEGMENTATION
ANSWER_EXTRACTION
SEMANTIC_MATCHING
CONCEPT_EXTRACTION
RUBRIC_EVALUATION
MATHEMATICAL_EVALUATION
DIAGRAM_EVALUATION
ANSWER_VERIFICATION
EVALUATION_RECONCILIATION
CONFIDENCE_ESTIMATION
BATCH_PROCESSING
REPORT_GENERATION
CSV_GENERATION
```

## Capability DNA

Example:

```json
{
  "capability": "semantic_answer_evaluation",

  "input": [
    "question",
    "student_answer",
    "answer_key",
    "rubric"
  ],

  "output": [
    "marks",
    "concepts_satisfied",
    "missing_concepts",
    "reasoning",
    "confidence"
  ],

  "requirements": {
    "reasoning": "high",
    "multilingual": true,
    "context_window": 16000
  },

  "constraints": {
    "max_latency_ms": 10000
  },

  "quality": {
    "minimum_accuracy": 0.90
  }
}
```

## Dynamic Model Selection

AOS should select models using:

```text
Accuracy
Latency
VRAM
RAM
Cost
Language
Modality
Context Length
Reliability
Task Complexity
```

## Expected DAG

```text
Document Intake
      |
      v
Pre-processing
      |
      v
OCR / Vision
      |
      v
Answer Extraction
      |
      v
Question Mapping
      |
      +------------------+
      |                  |
      v                  v
Evaluator Agent      Verifier Agent
      |                  |
      +---------+--------+
                |
                v
        Agreement Analysis
                |
                v
        Reconciliation if needed
                |
                v
             Result
```

## Deliverables

- Exam evaluation task adapter
- Capability DNA definitions
- Capability Registry entries
- Dynamic DAG integration
- Model selection integration

---

# PHASE 9 — Fault Recovery and Confidence

## Objective

Use the existing AOS fault-recovery mechanisms to make the evaluator robust.

## OCR Recovery

```text
OCR Model A
     |
     v
Low Confidence
     |
     v
OCR Model B
     |
     v
Still Low
     |
     v
Vision Model
     |
     v
Human Review
```

## Model Failure

```text
Primary Model
      |
      v
Timeout / Failure
      |
      v
Retry
      |
      v
Fallback Model
```

## Agent Disagreement

```text
Agent 1 = 9/10
Agent 2 = 5/10
      |
      v
Difference > Threshold
      |
      v
Reconciliation
      |
      v
Final Evaluation
```

## Confidence Components

Use evidence such as:

```text
OCR Confidence
Answer Extraction Confidence
Semantic Confidence
Rubric Confidence
Agent Agreement
```

## Confidence Categories

```text
HIGH
MEDIUM
LOW
```

Thresholds must be configurable and validated through benchmarking.

## Important Rule

Confidence is **not correctness**.

Confidence is primarily used to determine whether additional verification or human review is required.

## Deliverables

- Confidence engine
- Recovery policies
- Fallback routing
- Retry handling
- Disagreement escalation

---

# PHASE 10 — Mass Paper Evaluation

## Objective

Scale from one paper to hundreds or thousands of answer sheets.

## Input

```text
exam_answers/
├── student001.pdf
├── student002.pdf
├── student003.pdf
├── student004.pdf
└── ...
```

Or:

```text
exam_answers.zip
```

## Pipeline

```text
ZIP / Folder
      |
      v
File Discovery
      |
      v
Document Validation
      |
      v
Parallel Processing
      |
      +---- Student A
      +---- Student B
      +---- Student C
      +---- Student D
      +---- ...
      |
      v
Final Results
```

## Requirements

- Parallel processing
- Queue management
- Progress tracking
- Retry
- Failure isolation
- Checkpointing
- Resumability
- Per-document status
- Resource management

## Important Behavior

If paper 37 fails:

```text
Papers 1–36   → Completed
Paper 37      → Failed / Review
Papers 38–100 → Continue
```

The complete batch must not restart.

## Deliverables

- Batch processing engine
- Worker system
- Queue
- Progress tracking
- Checkpoint system
- Resume mechanism
- Batch status API

---

# PHASE 11 — Human Review and Audit

## Objective

Provide a review system for uncertain evaluations.

AI should produce **proposed marks with evidence**, not hide uncertainty.

## Review Reasons

```text
LOW_OCR_CONFIDENCE
AGENT_DISAGREEMENT
AMBIGUOUS_ANSWER
IDENTITY_UNCERTAIN
DIAGRAM_UNCERTAIN
MATHEMATICAL_UNCERTAINTY
MISSING_PAGE
MULTIPLE_ANSWERS
LOW_EVALUATION_CONFIDENCE
```

## Review Interface

```text
+---------------------------------------+
| REVIEW REQUIRED                       |
+---------------------------------------+
| Student: 23CS042                      |
| Question: Q7                          |
|                                       |
| [Original Answer Image]               |
|                                       |
| OCR Text                              |
| "TCP is a connection oriented..."     |
|                                       |
| Answer Key                            |
| - Transport layer                     |
| - Connection oriented                 |
| - Reliable communication              |
|                                       |
| Agent 1: 8/10                         |
| Agent 2: 5/10                         |
|                                       |
| Confidence: 61%                       |
|                                       |
| Final Marks: [       ] / 10           |
|                                       |
| [Accept] [Modify] [Escalate]          |
+---------------------------------------+
```

## Audit Trail

For every question store:

```text
Student
Question
Original Image Reference
OCR Text
OCR Confidence
Answer-Key Version
Rubric Version
Agent 1 Result
Agent 2 Result
Disagreement
Reconciliation Result
Final Marks
Final Confidence
Review Status
Timestamp
Model Information
```

## Deliverables

- Review dashboard
- Review queue
- Manual mark editing
- Audit log
- Evaluation history

---

# PHASE 12 — Reports, CSV and Analytics

## Objective

Generate useful output from evaluated papers.

## CSV 1 — Student Results

```csv
Roll No,Student Name,Q1,Q2,Q3,Q4,Q5,Total,Percentage,Status
23CS041,Rahul,4,8,7,9,6,34,68,Evaluated
23CS042,Anjali,5,7,8,8,7,35,70,Evaluated
23CS043,Student,4,6,5,9,8,32,64,Review
```

## CSV 2 — Detailed Evaluation

```csv
Roll No,Question,Max Marks,Agent 1,Agent 2,Final Marks,Confidence,Review Required
23CS041,Q1,5,4,4,4,0.94,false
23CS041,Q2,10,8,7,8,0.82,true
```

## CSV 3 — Issues

```csv
Roll No,Page,Question,Issue,Severity,Resolution
23CS043,3,Q5,Low OCR Confidence,High,Human Review
```

## Individual Student Report

```text
Student: Rahul
Roll No: 23CS041

Total: 72/100

Q1: 8/10
- Concept A satisfied
- Concept B satisfied
- Example missing

Q2: 6/10
- Concept A satisfied
- Concept B partial
- Concept C missing
```

## Class Analytics

Possible metrics:

```text
Total Students
Average
Median
Highest
Lowest
Question-wise Average
Question-wise Difficulty
Review Rate
OCR Failure Rate
Agent Disagreement Rate
```

## Deliverables

- CSV generator
- JSON export
- Individual report generator
- Batch report generator
- Analytics API
- Analytics dashboard

---

# PHASE 13 — Benchmarking and Research Evaluation

## Objective

Evaluate whether the AOS-based system improves reliability, efficiency and resource usage.

This phase is critical because the system is also a practical application of the AOS research architecture.

## Ground-Truth Dataset

Create a dataset containing:

```text
Questions
Student Answers
Human Marks
Rubrics
Question Types
OCR Images
```

Human evaluators provide ground-truth marks.

## Compare

```text
Human Evaluation
       vs
AOS Evaluation
```

## Metrics

### Mean Absolute Error

Average difference between AI marks and human marks.

### Exact Agreement

```text
AI mark == Human mark
```

### Tolerance Agreement

```text
|AI - Human| <= 0.5
|AI - Human| <= 1
```

### False Rejection

Correct answers incorrectly marked wrong.

### False Acceptance

Incorrect answers incorrectly accepted.

### Semantic Acceptance

Whether valid paraphrases are correctly accepted.

### Partial-Mark Agreement

Whether AI applies partial marks consistently with human evaluation.

### OCR Accuracy

Especially for handwriting.

### Agent Agreement

Measure Agent 1 vs Agent 2 agreement.

### Reconciliation Success

How often disagreement is resolved correctly.

### Fault-Recovery Success

How often fallback models successfully recover failed tasks.

### Model-Selection Quality

Whether dynamically selected models satisfy:

```text
Accuracy Constraints
Latency Constraints
Resource Constraints
Capability Constraints
```

### AOS Overhead

Compare:

```text
Fixed Pipeline
      vs
AOS Dynamic Pipeline
```

Measure:

```text
Latency
VRAM
CPU
Memory
Model Switching
Failures
Cost
```

### Batch Throughput

Test:

```text
10 papers
50 papers
100 papers
500 papers
1000 papers
```

## Research Experiments

```text
Experiment A:
Single model vs two-agent evaluation

Experiment B:
Exact matching vs semantic evaluation

Experiment C:
Fixed model selection vs AOS dynamic selection

Experiment D:
No fault recovery vs AOS fault recovery

Experiment E:
Single-pass OCR vs OCR fallback

Experiment F:
Single model vs capability-based model routing
```

## Deliverables

```text
BENCHMARK_REPORT.md
evaluation_results.csv
benchmark_dataset/
experiment_results/
```

---

# PHASE 14 — Production Hardening

## Security

Implement:

- Authentication
- Role-based authorization
- Examiner/admin roles
- Student data isolation
- Secure file storage
- API security
- Access logs
- Input validation

## Reliability

Implement:

- Retries
- Checkpoints
- Resumable batches
- Model health checks
- Queue management
- Failure isolation
- Monitoring

## Reproducibility

Store:

```text
Exam ID
Exam Version
Answer-Key Version
Rubric Version
Model
Model Version
Capability DNA
AOS Configuration
Timestamp
Evaluation Result
```

## Performance Testing

Test:

```text
10 papers
50 papers
100 papers
500 papers
1000 papers
```

Measure:

```text
Total Processing Time
Average Paper Processing Time
GPU Utilization
CPU Utilization
Memory
VRAM
Model Switching
Failure Rate
Recovery Rate
Queue Time
```

## Production Readiness

Before deployment verify:

- No silent failures.
- No data loss.
- No automatic zero for OCR failures.
- All uncertain results are traceable.
- All marks have supporting evidence.
- All model decisions are logged.
- Batch processing can resume after failure.
- Exam and rubric versions are immutable for completed evaluations.

---

# Complete End-to-End Workflow

```text
                    EXAMINER
                       |
                       v
             Configure Examination
                       |
                       +----------------------+
                       |                      |
                       v                      v
                  Answer Key              Rubric
                       |                      |
                       +----------+-----------+
                                  |
                                  v
                           Exam Definition
                                  |
                                  v
                           Upload Papers
                                  |
                                  v
                         Document Processing
                                  |
                                  v
                         Quality Validation
                                  |
                                  v
                           OCR / Vision
                                  |
                                  v
                       Student Identification
                                  |
                                  v
                       Question Segmentation
                                  |
                                  v
                         Answer Extraction
                                  |
                                  v
                     Semantic Answer Analysis
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
              Evaluator Agent             Verifier Agent
                  Agent 1                     Agent 2
                    |                           |
                    +-------------+-------------+
                                  |
                                  v
                         Agreement Analysis
                                  |
                    +-------------+-------------+
                    |                           |
                  Agree                      Disagree
                    |                           |
                    |                     Reconciliation
                    |                           |
                    +-------------+-------------+
                                  |
                                  v
                             Confidence
                                  |
                    +-------------+-------------+
                    |                           |
              High Confidence             Low Confidence
                    |                           |
                    v                           v
              Auto Accepted               Human Review
                    |                           |
                    +-------------+-------------+
                                  |
                                  v
                           Final Evaluation
                                  |
                    +-------------+-------------+
                    |             |             |
                    v             v             v
                   CSV         Reports      Analytics
```

---

# Major Edge Cases

## Document

- PDF
- JPG/PNG
- Multi-page PDF
- Rotated pages
- Upside-down pages
- Blurry scans
- Poor lighting
- Shadows
- Skewed scans
- Cropped pages
- Duplicate pages
- Missing pages
- Blank pages
- Mixed page sizes
- Corrupted files

## Identity

- Missing name
- Missing roll number
- OCR typo
- Handwritten roll number
- Similar student names
- Incorrect roll number
- Multiple answer booklets
- Student information on another page

## Answers

- Unanswered question
- Partially answered question
- Crossed-out answer
- Multiple answers
- Answer continues on another page
- Answer written in margin
- Answers out of order
- Diagrams
- Tables
- Equations
- Code
- Abbreviations
- Spelling errors
- Multilingual responses
- Mixed-language responses
- Alternative correct answers

## Evaluation

- Exact match
- Semantic match
- Paraphrasing
- Valid alternative answer
- Partial knowledge
- Incorrect terminology but correct concept
- Correct concept with poor explanation
- Correct method but wrong arithmetic
- Wrong method but correct final answer
- Incomplete reasoning
- Ambiguous answer
- Ambiguous answer key
- Agent disagreement
- Low OCR confidence
- Low evaluation confidence

---

# Capabilities to Add to AOS

```text
DOCUMENT_OCR
HANDWRITING_OCR
DOCUMENT_LAYOUT
STUDENT_ID_EXTRACTION
QUESTION_SEGMENTATION
ANSWER_EXTRACTION
TEXT_NORMALIZATION
SEMANTIC_EMBEDDING
CONCEPT_EXTRACTION
SEMANTIC_ANSWER_EVALUATION
RUBRIC_EVALUATION
MATHEMATICAL_EVALUATION
DIAGRAM_EVALUATION
ANSWER_VERIFICATION
EVALUATION_RECONCILIATION
CONFIDENCE_ESTIMATION
BATCH_PROCESSING
REPORT_GENERATION
CSV_GENERATION
```

Each capability should have its own Capability DNA.

---

# Recommended Implementation Order

Do not start with the complete UI.

```text
1. Existing AOS integration contract
        |
        v
2. Hugging Face model survey
        |
        v
3. Model benchmarking
        |
        v
4. Capability DNA definitions
        |
        v
5. Capability Registry integration
        |
        v
6. Exam + Answer-Key schema
        |
        v
7. Rubric engine
        |
        v
8. OCR pipeline
        |
        v
9. Answer-sheet parser
        |
        v
10. Question mapping
        |
        v
11. Semantic evaluator
        |
        v
12. Two independent agents
        |
        v
13. Agreement analysis
        |
        v
14. Reconciliation
        |
        v
15. AOS dynamic DAG integration
        |
        v
16. Fault recovery
        |
        v
17. Confidence engine
        |
        v
18. Single-paper end-to-end test
        |
        v
19. Mass evaluation
        |
        v
20. Human review
        |
        v
21. CSV/report generation
        |
        v
22. Benchmark against human evaluation
        |
        v
23. Performance optimization
        |
        v
24. Production hardening
```

---

# MVP Definition

The first working MVP should support:

```text
PDF answer sheet
       |
       v
OCR
       |
       v
Student ID
       |
       v
Question Mapping
       |
       v
Configured Answer Key
       |
       v
Semantic Evaluation
       |
       +-------------+
       |             |
       v             v
    Agent 1       Agent 2
       |             |
       +------+------+
              |
              v
       Agreement Check
              |
              v
         Final Marks
              |
              v
             CSV
```

The MVP must demonstrate:

1. Semantic rather than exact answer matching.
2. Configurable answer keys.
3. Rubric-based marks.
4. Two-agent evaluation.
5. Agent disagreement detection.
6. Existing AOS dynamic model selection.
7. Existing AOS fault recovery.
8. Question-wise marks.
9. Student-wise CSV output.

---

# Final Design Principles

1. Preserve the existing AOS architecture.
2. Use Capability DNA for every model/capability.
3. Use Dynamic Model Selection rather than hardcoded model routing.
4. Use semantic and conceptual evaluation rather than exact text matching.
5. Make answer keys and rubrics configurable.
6. Support partial marks explicitly.
7. Keep Agent 1 and Agent 2 independent.
8. Never hide model disagreement.
9. Use fault recovery for model/OCR failures.
10. Use confidence to identify uncertain cases.
11. Never interpret OCR failure as an unanswered question.
12. Preserve original answer images as evidence.
13. Maintain a complete audit trail.
14. Version answer keys and rubrics.
15. Make mass evaluation resumable.
16. Keep human review available for uncertain cases.
17. Benchmark against human-graded ground truth.
18. Measure accuracy, latency, resource usage and orchestration overhead.
19. Do not allow evaluation models to modify marking rules.
20. Treat the exam evaluator as a domain built on top of AOS, not as a replacement for AOS.

---

# Final Project Milestones

| Milestone | Result |
|---|---|
| M1 | Existing AOS integration understood |
| M2 | Hugging Face models benchmarked |
| M3 | Capability DNA + Registry extended |
| M4 | Exam configuration completed |
| M5 | OCR pipeline completed |
| M6 | Answer-sheet parser completed |
| M7 | Semantic evaluation completed |
| M8 | Two-agent evaluation completed |
| M9 | AOS dynamic orchestration integrated |
| M10 | Fault recovery + confidence completed |
| M11 | Single-paper evaluation working end-to-end |
| M12 | Mass evaluation completed |
| M13 | Human review completed |
| M14 | CSV/reporting completed |
| M15 | Benchmarking completed |
| M16 | Production hardening completed |

---

# Immediate Next Step

The next activity is **Phase 1: Hugging Face Model Survey and Selection**.

The process should be:

```text
Identify Hugging Face models
        |
        v
Benchmark candidates
        |
        v
Compare hardware requirements
        |
        v
Select primary + fallback models
        |
        v
Create Capability DNA
        |
        v
Add models to AOS Capability Registry
        |
        v
Begin implementation
```

Only after this model/capability layer is finalized should the actual exam evaluation workflow be implemented.
