"""The end-to-end study pipeline: folder in, report out.

    study folder
        -> ingest      (decode, de-identify, preprocess, cache)
        -> plan        (deterministic DAG; LLM planner for free-form jobs)
        -> execute     (wave-parallel, failure-contained)
        -> integrate   (typed report assembly)
        -> render      (markdown + JSON)

Every stage is separately callable, so a service can cache the ingest step, or
a test can drive the executor with a hand-built graph.
"""

from __future__ import annotations

import time
from pathlib import Path

from agents.graph_executor import GraphExecutor
from agents.integrator_agent import IntegratorAgent
from agents.manager_agent import ManagerAgent
from agents.planner import build_radiology_plan, estimate_cost
from config import OUTPUT_DIR, RUNTIME
from core.logging_utils import get_logger, timed
from ingest.study_loader import load_study
from models import Graph, RadiologyReport, Study
from reporting.report_builder import write_report

_log = get_logger("pipeline")


def analyze_study(
    folder: str | Path,
    *,
    clinical_history: str | None = None,
    referring_question: str | None = None,
    with_literature: bool = True,
    write_output: bool = True,
    output_dir: Path | None = None,
) -> tuple[RadiologyReport, Study, Graph]:
    """Interpret every supported input in ``folder`` and return the run objects."""
    started = time.perf_counter()
    # The plan belongs next to the report it produced, not in a single global
    # location that concurrent runs (and tests) would overwrite.
    plan_path = Path(output_dir or OUTPUT_DIR) / "plan.md"

    with timed(_log, "stage 1/5 ingest"):
        study = load_study(
            folder,
            clinical_history=clinical_history,
            referring_question=referring_question,
        )

    with timed(_log, "stage 2/5 plan"):
        graph = build_radiology_plan(study, with_literature=with_literature)
        _log.info("plan cost estimate: %s", estimate_cost(graph))
        ManagerAgent().write_plan(graph, plan_path)

    with timed(_log, "stage 3/5 execute"):
        graph = GraphExecutor().run(graph, study=study)

    with timed(_log, "stage 4/5 integrate"):
        report = IntegratorAgent().integrate(
            graph, study, runtime_s=time.perf_counter() - started
        )

    if write_output:
        with timed(_log, "stage 5/5 render"):
            paths = write_report(report, study, output_dir=output_dir)
            _log.info("markdown: %s", paths["markdown"])
            _log.info("json:     %s", paths["json"])
            # Refresh the plan with the executed graph so node statuses show.
            ManagerAgent().write_plan(graph, plan_path)

    _log.info(
        "study %s complete in %.2fs (status=%s)",
        study.study_id,
        report.runtime_s,
        report.status.overall_severity.value,
    )
    return report, study, graph


def run_freeform(prompt: str, folder: str | Path | None = None) -> Graph:
    """Run an open-ended job through the LLM planner rather than the fixed plan.

    Kept for non-radiology questions ("compare these two guidelines"), where
    the pipeline shape genuinely is not known in advance.
    """
    study = None
    if folder is not None:
        study = load_study(folder)

    manager = ManagerAgent()
    graph = manager.create_plan(prompt, study=study)
    manager.write_plan(graph, OUTPUT_DIR / "plan.md")

    graph = GraphExecutor().run(graph, study=study)
    manager.write_plan(graph, OUTPUT_DIR / "plan.md")
    return graph


def preflight() -> dict:
    """Report what this machine can run, without touching a study.

    Surfaced by ``python main.py --doctor``; this is the first thing to check
    when a run fails for environmental reasons.
    """
    from capabilities import registry
    from capabilities.hf_backend import backend_report
    from capabilities.model_zoo import coverage_table
    from capabilities.xrv_backend import available as xrv_available
    from capabilities.vision import OLLAMA_MODEL, _ollama_reachable
    from config import describe
    from ingest.dicom_utils import PYDICOM_AVAILABLE
    import importlib.util

    checks = {
        "config": describe(),
        "backends": backend_report(),
        "capabilities": registry.names(),
        "model_zoo": coverage_table(),
        "torchxrayvision": xrv_available(),
        "dicom_support": PYDICOM_AVAILABLE,
        "document_support": {
            "pdf": bool(importlib.util.find_spec("pypdfium2")),
            "docx": bool(importlib.util.find_spec("docx")),
            "xlsx": bool(importlib.util.find_spec("openpyxl")),
        },
        "ollama": {"reachable": _ollama_reachable(), "model": OLLAMA_MODEL},
        "cache_dir": str(RUNTIME.cache_dir),
    }

    problems: list[str] = []
    backends = checks["backends"]
    if not (backends["hf_api"] or checks["ollama"]["reachable"] or backends["transformers"]):
        problems.append(
            "No vision backend available. Set HF_TOKEN in .env, run Ollama with a "
            "medical VLM, or install transformers."
        )
    if not checks["config"]["groq_key_present"] and not backends["hf_token"]:
        problems.append(
            "No text backend available. Set GROQ_API_KEY or HF_TOKEN in .env."
        )
    if not PYDICOM_AVAILABLE:
        problems.append(
            "pydicom is not installed; .dcm files will be skipped. "
            "Install with: pip install pydicom pylibjpeg pylibjpeg-libjpeg"
        )
    if not checks["document_support"]["pdf"]:
        problems.append(
            "pypdfium2 is not installed; PDF text and scanned pages cannot be read. "
            "Install with: pip install pypdfium2"
        )
    if not checks["torchxrayvision"]:
        problems.append(
            "torchxrayvision is not installed; the chest X-ray pneumothorax/"
            "effusion/fracture specialist is skipped. Install torchvision matching "
            "your torch first, then: pip install torchxrayvision"
        )
    if not backends["transformers"]:
        problems.append(
            "transformers is not installed; local screening models cannot run "
            "and every image call goes remote. Install with: pip install transformers"
        )

    checks["problems"] = problems
    checks["ready"] = not any(
        p.startswith("No vision backend") or p.startswith("No text backend")
        for p in problems
    )
    return checks
