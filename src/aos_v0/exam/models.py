"""Exam-evaluation domain models -- the Phase 0 ExamEvaluationTask contract.

Scope
-----
Pure typed data plus one adapter. No scheduler, registry or provider is
imported here; the only kernel dependency is `aos_v0.core.models` (Graph/Node)
so `ExamEvaluationTask.build_graph()` can emit a structurally valid AOS DAG
skeleton. This keeps the domain layer replaceable and lets the kernel remain
exactly what it is today.

Capability flags
----------------
`EXAM_CAPABILITY_FLAGS` names the capability vocabulary the exam domain will
register in Phase 1 (Capability Registry entries + Capability DNA). These are
NOT yet members of `aos_v0.core.models.CAPABILITY_FLAGS`: adding them is a
Phase-1 action, because both the DNA validator and the manifest validator draw
from that single vocabulary. Until then `build_graph()` emits nodes with
coarse capability strings and no DNA, which is sufficient for interface and
structural-validity purposes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Dict, List, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from aos_v0.core.models import Graph, Node


# ---------------------------------------------------------------------------
# Exam-domain capability flag targets (Phase 1 registration list)
# ---------------------------------------------------------------------------

# Full Phase-8 capability list from the implementation plan (Capabilities to
# Add to AOS). Used when the domain registers resources with the kernel.
EXAM_CAPABILITY_FLAGS = frozenset({
    "document.ocr",
    "handwriting.ocr",
    "document.layout",
    "student_id.extraction",
    "question.segmentation",
    "answer.extraction",
    "text.normalization",
    "semantic.embedding",
    "concept.extraction",
    "semantic.answer_evaluation",
    "rubric.evaluation",
    "mathematical.evaluation",
    "diagram.evaluation",
    "answer.verification",
    "evaluation.reconciliation",
    "confidence.estimation",
    "batch.processing",
    "report.generation",
    "csv.generation",
})

# Subset a single-paper task must satisfy during evaluation. Everything on the
# dependency spine of ExamEvaluationTask.build_graph() must appear here.
EXAM_REQUIRED_FLAGS = frozenset({
    "document.ocr",
    "document.layout",
    "student_id.extraction",
    "question.segmentation",
    "answer.extraction",
    "semantic.answer_evaluation",
    "answer.verification",
    "evaluation.reconciliation",
    "confidence.estimation",
    "report.generation",
})


def _node_id(*parts: str) -> str:
    """Build a deterministic, graph-safe node id from arbitrary parts.

    Question ids can contain punctuation ("Q3(a)"); the DAG validator only
    needs uniqueness and stable references, so we fold every part to lowercase
    alphanumerics and join via a "-" that cannot collide with the result.
    """
    return "".join(ch for ch in "-".join(parts) if ch.isalnum()).lower()


# ---------------------------------------------------------------------------
# Question / rubric / exam configuration
# ---------------------------------------------------------------------------


class QuestionType(StrEnum):
    MCQ = "mcq"
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    LONG_ANSWER = "long_answer"
    NUMERICAL = "numerical"
    MATHEMATICAL = "mathematical"
    DIAGRAM = "diagram"
    TABLE = "table"
    PROGRAMMING = "programming"
    MIXED = "mixed"


class RubricCriterion(BaseModel):
    """One mark-bearing criterion in a rubric (implementation plan Phase 2)."""

    model_config = ConfigDict(extra="forbid")

    criterion: str
    marks: float = Field(ge=0.0)
    description: Optional[str] = None


class Rubric(BaseModel):
    """Distribution of marks across evaluated criteria."""

    model_config = ConfigDict(extra="forbid")

    criteria: List[RubricCriterion] = Field(min_length=1)

    def criterion_map(self) -> Dict[str, RubricCriterion]:
        return {c.criterion: c for c in self.criteria}

    @property
    def total(self) -> float:
        return round(sum(c.marks for c in self.criteria), 4)


class ConceptRelationshipType(StrEnum):
    """How one expected concept is linked to another in the answer key.

    * REQUIRES    -- the source concept is only fully satisfied when the target
                     concept is also present in the answer.
    * IMPLIES     -- satisfying the source concept counts toward the target
                     (e.g. "transport layer" implies "network layer context").
    * ALTERNATIVE -- the source and target are accepted alternative ways to
                     demonstrate the same idea (mutually substitutable).
    * CONFLICTS   -- the source and target are mutually exclusive; showing both
                     is a contradiction the evaluator must flag, not reward.
    """

    REQUIRES = "requires"
    IMPLIES = "implies"
    ALTERNATIVE = "alternative"
    CONFLICTS = "conflicts"


class ConceptRelationship(BaseModel):
    """A typed link between two expected concepts (Answer-Key layer)."""

    model_config = ConfigDict(extra="forbid")

    source: str
    relationship: ConceptRelationshipType = ConceptRelationshipType.REQUIRES
    target: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    description: Optional[str] = None


class PartialCreditRule(BaseModel):
    """How many marks a partially-satisfied rubric criterion earns.

    `fraction` is the portion of the *criterion's* marks awarded when the
    criterion is present but incomplete (implementation plan, Partial Credit
    Rules). Applied on top of the question-level `partial_credit` toggle.
    """

    model_config = ConfigDict(extra="forbid")

    criterion: str
    fraction: float = Field(ge=0.0, le=1.0)
    description: Optional[str] = None


class AnswerKey(BaseModel):
    """What knowledge is expected -- never the exact sentence the student writes.

    The plan's Answer-Key Layers map onto the fields as follows:

      Reference Answer      -> `reference_answers`   (conventional model answer)
      Expected Concepts     -> `expected_concepts`   (what must be demonstrated)
      Accepted Alternatives -> `accepted_alternatives` (valid terminology / approaches)
      Keywords              -> `keywords`            (supporting evidence only)
      Concept Relationships -> `concept_relationships` (typed links above)
      Rubric                -> lives on `Question` (how marks are distributed)
    """

    model_config = ConfigDict(extra="forbid")

    reference_answers: List[str] = Field(default_factory=list)
    expected_concepts: List[str] = Field(default_factory=list)
    accepted_alternatives: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    concept_relationships: List[ConceptRelationship] = Field(default_factory=list)


class Question(BaseModel):
    """A configurable exam question plus its marking knowledge.

    The answer key is carried as an `AnswerKey` (reference answers, expected
    concepts, accepted alternatives, keywords, concept relationships). The four
    flat legacy fields (`expected_concepts`, `reference_answers`,
    `accepted_alternatives`, `keywords`) are still accepted at construction time
    and folded into `answer_key`, preserving the Phase-0 contract; they are
    exposed as read-only convenience properties delegating to `answer_key`.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    # Accept either the canonical key "text" or the plan's illustrative
    # "question"; dumps always use "text".
    text: str = Field(
        validation_alias=AliasChoices("text", "question"), serialization_alias="text"
    )
    max_marks: float = Field(gt=0.0)
    question_type: QuestionType = Field(
        default=QuestionType.LONG_ANSWER,
        validation_alias=AliasChoices("question_type", "type"),
        serialization_alias="question_type",
    )

    answer_key: AnswerKey = Field(default_factory=AnswerKey)
    rubric: Optional[Rubric] = None
    partial_credit: bool = True
    partial_credit_rules: List[PartialCreditRule] = Field(default_factory=list)
    negative_marks: float = Field(default=0.0, ge=0.0)
    special_rules: Optional[str] = None

    # -- legacy compatibility ---------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _fold_flat_answer_key_fields(cls, data):
        """Fold the Phase-0 flat fields into `answer_key`.

        Any of `expected_concepts` / `reference_answers` / `accepted_alternatives`
        / `keywords` passed alongside a constructed `answer_key` is merged in
        (list union, order-preserving); otherwise they seed the answer key. The
        flat keys are removed so `extra="forbid"` does not reject them.
        """
        if not isinstance(data, dict):
            return data
        flat = ("expected_concepts", "reference_answers",
                "accepted_alternatives", "keywords")
        provided = {k: data[k] for k in flat if k in data}
        if not provided:
            return data

        answer_key = data.get("answer_key")
        if answer_key is None:
            answer_key = {}
        elif hasattr(answer_key, "model_dump"):
            answer_key = answer_key.model_dump()
        elif not isinstance(answer_key, dict):
            raise ValueError("answer_key must be an AnswerKey or dict")
        answer_key = dict(answer_key)

        for key, value in provided.items():
            existing = answer_key.get(key, [])
            answer_key[key] = list(existing) + [
                v for v in value if v not in existing
            ]

        data = dict(data)
        for key in flat:
            data.pop(key, None)
        data["answer_key"] = answer_key
        return data

    # -- read-only convenience accessors (Phase-0 field names) -------------

    @property
    def expected_concepts(self) -> List[str]:
        return self.answer_key.expected_concepts

    @property
    def reference_answers(self) -> List[str]:
        return self.answer_key.reference_answers

    @property
    def accepted_alternatives(self) -> List[str]:
        return self.answer_key.accepted_alternatives

    @property
    def keywords(self) -> List[str]:
        return self.answer_key.keywords


class MarkingRules(BaseModel):
    """Exam-wide default marking policy (per-question fields override these)."""

    model_config = ConfigDict(extra="forbid")

    negative_marking_default: float = Field(default=0.0, ge=0.0)
    partial_credit_default: bool = True
    max_marks_floor: float = Field(default=0.0, ge=0.0)
    rounding: Literal["none", "half_up", "truncate"] = "none"


class ExamConfiguration(BaseModel):
    """The full definition of an examination (Phase 2 deliverable shape).

    Structure (implementation plan Phase 2):

        Exam
        ├── Metadata        -> exam_id, title, subject, version, institution,
        │                      exam_date, duration_minutes, instructions
        ├── Questions       -> `questions` (each question embeds its own answer
        │                      key, rubric, negative marking and partial credit)
        ├── Rubrics         -> per-question `rubric`
        ├── Marking Rules   -> `marking_rules`
        ├── Negative Marking-> per-question `negative_marks` + default in rules
        ├── Partial Credit  -> per-question `partial_credit`/`partial_credit_rules`
        └── Evaluation Settings -> carried by the surrounding task, not here
    """

    model_config = ConfigDict(extra="forbid")

    exam_id: str
    title: str
    subject: Optional[str] = None
    version: str = "1"
    institution: Optional[str] = None
    exam_date: Optional[date] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0)
    instructions: Optional[str] = None
    questions: List[Question] = Field(min_length=1)
    marking_rules: MarkingRules = Field(default_factory=MarkingRules)

    @field_validator("questions")
    @classmethod
    def _unique_question_ids(cls, questions: List[Question]) -> List[Question]:
        ids = [q.question_id for q in questions]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"duplicate question_id(s): {sorted(dupes)}")
        return questions

    @property
    def total_marks(self) -> float:
        return round(sum(q.max_marks for q in self.questions), 4)

    @property
    def question_count(self) -> int:
        return len(self.questions)


# ---------------------------------------------------------------------------
# Paper / student identity / evaluation settings
# ---------------------------------------------------------------------------


class PaperReference(BaseModel):
    """Reference to one student's answer sheets.

    `file_path` and `artifact_id` are alternative ways to locate the scan: the
    domain accepts either a raw filesystem path (Phase 3 document intake) or an
    artifact already registered with the kernel's ArtifactManager.
    """

    paper_id: str
    file_path: Optional[str] = None
    artifact_id: Optional[str] = None
    pages: Optional[str] = None


class RosterEntry(BaseModel):
    roll_no: str
    name: Optional[str] = None


class Roster(BaseModel):
    """Optional institutional roster used to validate extracted student ids."""

    entries: List[RosterEntry] = Field(min_length=1)


class EvalReviewReason(StrEnum):
    LOW_OCR_CONFIDENCE = "low_ocr_confidence"
    AGENT_DISAGREEMENT = "agent_disagreement"
    AMBIGUOUS_ANSWER = "ambiguous_answer"
    IDENTITY_UNCERTAIN = "identity_uncertain"
    DIAGRAM_UNCERTAIN = "diagram_uncertain"
    MATHEMATICAL_UNCERTAINTY = "mathematical_uncertainty"
    MISSING_PAGE = "missing_page"
    MULTIPLE_ANSWERS = "multiple_answers"
    LOW_EVALUATION_CONFIDENCE = "low_evaluation_confidence"
    RECOVERY_FAILED = "recovery_failed"


class EvaluationSettings(BaseModel):
    """Defaults for the two-agent, confidence-gated evaluation loop.

    Thresholds mirror the implementation plan (Phase 9): confidence is used to
    decide whether extra verification / human review is needed, never to
    override an evaluation silently.
    """

    two_agent_evaluation: bool = True
    agent_disagreement_fraction: float = Field(default=0.2, ge=0.0, le=1.0)
    confidence_high: float = Field(default=0.9, ge=0.0, le=1.0)
    confidence_low: float = Field(default=0.6, ge=0.0, le=1.0)
    auto_review_reasons: List[EvalReviewReason] = Field(
        default_factory=lambda: [
            EvalReviewReason.LOW_OCR_CONFIDENCE,
            EvalReviewReason.AGENT_DISAGREEMENT,
            EvalReviewReason.IDENTITY_UNCERTAIN,
            EvalReviewReason.LOW_EVALUATION_CONFIDENCE,
            EvalReviewReason.RECOVERY_FAILED,
        ]
    )
    ocr_fallback_enabled: bool = True
    preserve_image_evidence: bool = True

    # Phase 9 fault-recovery policy knobs. The recovery loop lives in
    # `exam.recovery` and reuses the kernel's failure taxonomy; these control
    # how much effort it spends before escalating to human review.
    recovery_enabled: bool = True
    recovery_max_attempts: int = Field(default=2, ge=1, le=6)


# ---------------------------------------------------------------------------
# Root task + AOS adapter
# ---------------------------------------------------------------------------

# Coarse capability strings used by the Phase-0 graph skeleton. They match the
# registered resource ids the domain will add in Phase 1 so that the
# exact-match fallback path (`SubAgent._route_exact`) can serve them even
# before DNA routing exists for the new vocabulary.
OCR_CAPABILITY = "document_ocr"
LAYOUT_CAPABILITY = "document_layout"
STUDENT_ID_CAPABILITY = "student_id_extraction"
SEGMENTATION_CAPABILITY = "question_segmentation"
EVALUATION_CAPABILITY = "semantic_answer_evaluation"
VERIFICATION_CAPABILITY = "answer_verification"
RECONCILIATION_CAPABILITY = "evaluation_reconciliation"
REPORT_CAPABILITY = "report_generation"


class ExamEvaluationTask(BaseModel):
    """Phase 0 contract: evaluate one paper against one exam configuration.

    This is the seam between the exam domain and the AOS kernel. The exam
    adapter (later phases) consumes this task, and `build_graph()` currently
    materialises the structural DAG skeleton it will feed to the kernel's
    Manager/Executor once the Phase-1 capabilities are registered.
    """

    task_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exam: ExamConfiguration
    paper: PaperReference
    roster: Optional[Roster] = None
    settings: EvaluationSettings = Field(default_factory=EvaluationSettings)

    # -- domain capabilities ------------------------------------------------

    def requirement_flags(self) -> List[str]:
        """The capability requirements this task places on the registry."""
        flags = set(EXAM_REQUIRED_FLAGS)
        return sorted(flags)

    # -- AOS adapter --------------------------------------------------------

    def build_graph(self) -> Graph:
        """Emit a structurally valid AOS task-graph skeleton for this paper.

        Structure (per the implementation plan, Phase 8 Expected DAG):

            ocr
              └─ layout
                  ├─ student_id
                  └─ question_segmentation
                        ├─ eval_Qn_a ┐  (two independent agents per question)
                        ├─ eval_Qn_b ┘
                        └─ recon_Qn  (depends on both agents)
                              └─ report (depends on all recons + student_id)

        Nodes carry coarse capability strings only -- no DNA -- because the
        exam flags are a Phase-1 vocabulary addition. The graph is therefore
        the *interface* the adapter will feed to the kernel; it is not run
        until the corresponding resources are registered.
        """
        job = f"{self.exam.title} -- {self.paper.paper_id}"
        nodes: List[Node] = []

        def add(node_id: str, description: str, capability: str, depends_on: List[str]) -> None:
            nodes.append(
                Node(
                    id=node_id,
                    description=description,
                    capability=capability,
                    depends_on=depends_on,
                )
            )

        # Document spine.
        add(
            _node_id("ocr", self.paper.paper_id),
            (
                f"OCR the uploaded answer sheet for paper {self.paper.paper_id}. "
                f"Return text blocks with bounding boxes and per-block confidence. "
                f"Do not decide any marks."
            ),
            OCR_CAPABILITY,
            [],
        )
        ocr_id = _node_id("ocr", self.paper.paper_id)
        add(
            _node_id("layout", self.paper.paper_id),
            "Analyse the OCR'd page layout and identify text regions, question "
            "regions, answer regions, tables and diagrams.",
            LAYOUT_CAPABILITY,
            [ocr_id],
        )
        layout_id = _node_id("layout", self.paper.paper_id)

        add(
            _node_id("sid", self.paper.paper_id),
            "Extract the student name, roll number and register number from the "
            "answer sheet header and validate against the institutional roster "
            "when one is provided.",
            STUDENT_ID_CAPABILITY,
            [layout_id],
        )
        sid_id = _node_id("sid", self.paper.paper_id)

        add(
            _node_id("qseg", self.paper.paper_id),
            "Map every answer block to its question id (Q1, Q2, Q3(a), ...). "
            "Handle out-of-order and continuation answers. Combine pages that "
            "belong to the same question.",
            SEGMENTATION_CAPABILITY,
            [layout_id],
        )
        qseg_id = _node_id("qseg", self.paper.paper_id)

        # Per-question two-agent evaluation spine.
        recon_ids: List[str] = []
        for question in self.exam.questions:
            qkey = _node_id("q", question.question_id)
            eval_a = _node_id("eval", qkey, "a")
            eval_b = _node_id("eval", qkey, "b")
            eval_desc = (
                f"Evaluate the student answer for {question.question_id} "
                f"({question.text}) against the configured answer key and rubric. "
                f"Maximum marks: {question.max_marks:g}. Output marks, "
                f"concepts_satisfied, missing_concepts, reasoning and confidence."
            )
            add(eval_a, eval_desc, EVALUATION_CAPABILITY, [qseg_id])
            add(eval_b, eval_desc, VERIFICATION_CAPABILITY, [qseg_id])
            recon = _node_id("recon", qkey)
            add(
                recon,
                (
                    f"Reconcile evaluations for {question.question_id}: compare "
                    f"the two agents' marks, run disagreement analysis, and "
                    f"produce the final mark, final confidence and a review "
                    f"reason if the result must go to human review."
                ),
                RECONCILIATION_CAPABILITY,
                [eval_a, eval_b],
            )
            recon_ids.append(recon)

        # Terminal report node: the kernel appends its own synthesis node in
        # production runs; this domain skeleton keeps an explicit report node
        # as the leaf so the structural contract is self-contained here.
        add(
            _node_id("report", self.paper.paper_id),
            (
                f"Produce the final evaluation record for paper "
                f"{self.paper.paper_id}: per-question marks, total, confidence, "
                f"review flags and evidence references, ready for CSV/report "
                f"generation."
            ),
            REPORT_CAPABILITY,
            recon_ids + [sid_id],
        )

        return Graph(job=job, nodes=nodes)