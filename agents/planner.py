"""Deterministic plan construction for an imaging study.

The original system asked an LLM to invent the DAG for every job, then asked a
second LLM call to audit that DAG for completeness. For a radiology read that
is two round trips spent re-deriving a pipeline that does not vary: triage, then
screen, then read each image, then consolidate, then look up context, then
write the report. The shape is fixed; only the number of image nodes changes.

Building it in code instead removes both calls from the critical path, and --
more importantly -- makes the plan auditable. A regulator can read this
function and know exactly what will run.

:mod:`agents.manager_agent` still offers the LLM planner for open-ended jobs
that are not a study read.
"""

from __future__ import annotations

from config import RUNTIME
from core.logging_utils import get_logger
from core.task_understanding import bind_plan, understand_task
from models import AcceptanceCriterion, Graph, Node, Study, TaskBudget, TaskEvidence

_log = get_logger("planner")

# Beyond this many images, reading each one as its own node produces a very
# wide wave. They are grouped instead so concurrency stays bounded and the
# consolidation prompt stays inside its context window.
_MAX_VISION_NODES = 12
_MAX_DOCUMENT_NODES = 24


def build_radiology_plan(study: Study, *, with_literature: bool = True, budget: TaskBudget | None = None) -> Graph:
    """Return the fixed plan for reading ``study``."""
    nodes: list[Node] = []

    # --- Waves 0-2: triage, then the two-stage screening cascade ----------
    # These are strictly sequential. Screening used to run in the same wave as
    # triage, so it saw every non-DICOM image as modality "Unknown" and ran the
    # chest X-ray model on CT and ultrasound slices. Routing needs triage done.
    all_image_ids = [img.image_id for img in study.images]
    image_root_ids: list[str] = []
    cascade_ids: list[str] = []

    if all_image_ids:
        image_root_ids = ["quality", "triage", "screen", "confirm"]
        cascade_ids = ["screen", "confirm"]
        nodes.append(Node(id="quality", description="Check technical pixel quality and supplied series/frame coverage",
                          capability="image_quality", artifacts={"image_ids": all_image_ids}))
        nodes.append(
            Node(
                id="triage",
                description=(
                    "Identify the imaging modality and body region of every image, "
                    "and flag any file that is not a medical image."
                ),
                capability="modality_triage",
                depends_on=["quality"],
                artifacts={"image_ids": all_image_ids},
            )
        )
        nodes.append(
            Node(
                id="screen",
                description=(
                    "Route every image to the screener trained for its modality and "
                    "region, and report candidate conditions with probabilities."
                ),
                capability="image_classification",
                depends_on=["triage"],
                artifacts={"image_ids": all_image_ids},
            )
        )
        nodes.append(
            Node(
                id="confirm",
                description=(
                    "Re-check each screening candidate, and each condition named in "
                    "the referring question, with a disease-specific specialist model."
                ),
                capability="specialist_confirmation",
                depends_on=["screen"],
                artifacts={"image_ids": all_image_ids},
            )
        )

    # --- Wave 1: one vision-language read per image (or per group) --------
    groups = _group_images(all_image_ids, study=study)
    read_ids: list[str] = []
    for index, group in enumerate(groups):
        node_id = f"read{index}"
        read_ids.append(node_id)
        target = group[0] if len(group) == 1 else f"{len(group)} images"
        nodes.append(
            Node(
                id=node_id,
                description=(
                    f"Produce a structured radiological read of {target}, "
                    "describing modality, image quality, observations with "
                    "severity, and a short impression."
                ),
                capability="vision",
                depends_on=image_root_ids,
                # A failed cascade stage must not cost us the read itself.
                allow_partial=True,
                artifacts={"image_ids": group},
            )
        )

    # --- Wave 1b: inspect each document (pages/chunks are parallel) --------
    document_nodes = _document_nodes(study)
    nodes.extend(document_nodes)
    document_read_ids = [node.id for node in document_nodes]

    # --- Wave 2: consolidate all independent reads -------------------------
    # The cascade feeds consolidation directly too, so a candidate that no
    # narrative read mentions can still be surfaced as a discrepancy.
    upstream_reads = read_ids + document_read_ids + cascade_ids
    nodes.append(
        Node(
            id="consolidate",
            description=(
                "Merge the per-image and per-document reads into one coherent, "
                "de-duplicated findings list. Preserve exact document source "
                "references and reconcile disagreements without inventing facts."
            ),
            capability="cross_image_synthesis",
            depends_on=upstream_reads,
            allow_partial=True,
            partial_requires=read_ids + document_read_ids,
        )
    )

    report_depends = ["consolidate"]

    # --- Wave 3: optional literature context ------------------------------
    if with_literature:
        nodes.append(
            Node(
                id="literature",
                description=(
                    "Look up the accepted differential, next investigation and "
                    "red flags for the findings identified in this study."
                ),
                capability="web_search",
                depends_on=["consolidate"],
            )
        )
        report_depends.append("literature")

    # --- Wave 4: the deliverable ------------------------------------------
    nodes.append(
        Node(
            id="report",
            description=(
                "Write the impression, overall patient status, triage level and "
                "recommended next steps for the referring clinician."
            ),
            capability="report_synthesis",
            depends_on=report_depends,
            allow_partial=True,
        )
    )

    job = _describe_job(study)
    _log.info(
        "planned %d node(s): %d image read(s)%s",
        len(nodes),
        len(read_ids),
        ", with literature lookup" if with_literature else "",
    )
    evidence = [TaskEvidence(source_id=image.image_id, kind="direct_image", limitations=image.warnings)
                for image in study.images]
    evidence += [TaskEvidence(source_id=f"{doc.document_id}#page={page.page_number}", kind="document_page",
                              available=bool(page.text.strip() or page.image_path), limitations=page.warnings)
                 for doc in study.documents for page in doc.pages]
    missing = list(study.warnings)
    if not study.patient.clinical_history:
        missing.append("Clinical history was not supplied")
    if study.patient.prior_studies:
        missing.append("Named prior studies are context only unless their images/reports are supplied")
    graph = Graph(job=job, nodes=nodes, deterministic=True,
                  task=understand_task(job, evidence=evidence, missing_evidence=missing, budget=budget,
                                      acceptance_criteria=[
                                          AcceptanceCriterion(id="source_coverage", requirement="Read every assigned image and document page",
                                                              source_ids=[item.source_id for item in evidence], coverage_keys=["reads", "document_reads"]),
                                          AcceptanceCriterion(id="assessment", requirement="Produce a structured source-linked clinical assessment",
                                                              output_keys=["clinical_assessment", "status"]),
                                      ]))
    bind_plan(graph)
    return graph


def _document_nodes(study: Study) -> list[Node]:
    """Create one independently runnable analyzer per document.

    The extractor preserves physical page boundaries. The capability can then
    process pages in order while the graph runs different files concurrently;
    this avoids dropping pages merely to keep the DAG within its hard limit.
    """
    assignments = [
        (document.document_id, [page.page_number for page in document.pages])
        for document in study.documents
    ]
    max_nodes = max(1, _MAX_DOCUMENT_NODES - len(_group_images([i.image_id for i in study.images])) - 5)
    if len(assignments) > max_nodes:
        grouped: list[tuple[list[str], list[int]]] = []
        for document_id, pages in assignments:
            if grouped and len(grouped) >= max_nodes:
                grouped[-1][0].append(document_id)
                grouped[-1][1].extend(pages)
            else:
                grouped.append(([document_id], list(pages)))
        assignments = [(ids, pages) for ids, pages in grouped]
    else:
        assignments = [([document_id], pages) for document_id, pages in assignments]

    nodes: list[Node] = []
    for index, (document_ids, pages) in enumerate(assignments):
        document_text = ", ".join(document_ids)
        page_text = ", ".join(str(page) for page in pages) if len(document_ids) == 1 else "all"
        nodes.append(
            Node(
                id=f"document{index}",
                description=(
                    f"Analyze document(s) {document_text}, page(s) {page_text}. "
                    "Classify each document and extract clinically relevant "
                    "observations and exact reported values, preserving source refs."
                ),
                capability="document_analysis",
                artifacts={
                    "document_ids": document_ids,
                    "document_page_numbers": pages if len(document_ids) == 1 else [],
                },
            )
        )
    return nodes


def _group_images(image_ids: list[str], *, study: Study | None = None) -> list[list[str]]:
    """One node per image, up to a ceiling; beyond that, batch evenly."""
    if not image_ids:
        return []
    if len(image_ids) <= _MAX_VISION_NODES:
        return [[image_id] for image_id in image_ids]

    if study and study.series and len(study.series) <= _MAX_VISION_NODES:
        # Preserve acquisition boundaries when batching large studies. Reserve
        # at least one node for every represented series before splitting it.
        series_groups = [[i for i in s.image_ids if i in set(image_ids)] for s in study.series]
        series_groups = [group for group in series_groups if group]
        assigned = {i for group in series_groups for i in group}
        if assigned == set(image_ids):
            while len(series_groups) < _MAX_VISION_NODES:
                largest = max(range(len(series_groups)), key=lambda i: len(series_groups[i]))
                group = series_groups[largest]
                if len(group) < 2:
                    break
                middle = len(group) // 2
                series_groups[largest:largest + 1] = [group[:middle], group[middle:]]
            return series_groups
    # Round up so no group is left over.
    per_group = -(-len(image_ids) // _MAX_VISION_NODES)
    return [
        image_ids[i : i + per_group] for i in range(0, len(image_ids), per_group)
    ]


def _describe_job(study: Study) -> str:
    modalities = ", ".join(m.value for m in study.modalities) or "unknown modality"
    parts = [
        f"Interpret {len(study.images)} image(s) and {len(study.documents)} "
        f"document(s) ({modalities}) from study {study.study_id} and report "
        "patient status."
    ]
    if study.patient.clinical_history:
        parts.append(f"History: {study.patient.clinical_history}")
    if study.patient.referring_question:
        parts.append(f"Question: {study.patient.referring_question}")
    return " ".join(parts)


def estimate_cost(graph: Graph) -> dict:
    """Rough call counts per backend, so a user can see what a run will cost."""
    counts: dict[str, int] = {}
    for node in graph.nodes:
        counts[node.capability] = counts.get(node.capability, 0) + 1
    document_only = bool(counts.get("document_analysis")) and not counts.get("vision")
    return {
        "nodes": len(graph.nodes),
        "vlm_calls": counts.get("vision", 0),
        "text_llm_calls": (
            (0 if document_only else counts.get("cross_image_synthesis", 0))
            + counts.get("report_synthesis", 0)
            + counts.get("summarization", 0)
            + counts.get("web_search", 0)
        ),
        "local_vision_passes": counts.get("image_classification", 0)
        + counts.get("modality_triage", 0)
        + counts.get("specialist_confirmation", 0),
        "document_analysis_calls": counts.get("document_analysis", 0),
        "max_concurrency": min(RUNTIME.max_workers, max(counts.values(), default=1)),
    }
