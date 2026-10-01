"""Command-line entry point for Medical AOS.

    python main.py --study ./studies/patient-001
    python main.py --study ./scan.dcm --history "3 days fever" --question "pneumonia?"
    python main.py --doctor
    python main.py --ask "compare these two chest films" --study ./studies/p1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python main.py` from inside the package directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.errors import MedicalAOSError  # noqa: E402
from core.logging_utils import configure, get_logger  # noqa: E402

_log = get_logger("cli")


def _force_utf8_output() -> None:
    """Stop the Windows console from crashing on the report's symbols.

    The default Windows code page is cp1252, which cannot encode the severity
    badges. Without this, a completed run dies in its final print statement.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # a redirected or closed stream; not worth failing over


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medical-aos",
        description=(
            "Interpret a study folder containing images, PDFs, laboratory "
            "reports, notes and office documents; produce a preliminary "
            "report with patient status, for clinician review."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--study",
        "-s",
        metavar="PATH",
        help="study folder or a single image, PDF, text, or office document",
    )
    parser.add_argument(
        "--history",
        metavar="TEXT",
        help="clinical history; substantially improves the read",
    )
    parser.add_argument(
        "--question",
        metavar="TEXT",
        help="the referring clinician's specific question",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="directory for the generated report (default: outputs/reports)",
    )
    parser.add_argument(
        "--no-literature",
        action="store_true",
        help="skip the literature lookup stage (faster, no network search)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the report as JSON on stdout instead of a summary",
    )
    parser.add_argument(
        "--ask",
        metavar="PROMPT",
        help="run a free-form job through the LLM planner instead of the fixed plan",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="check models, credentials and dependencies, then exit",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="delete cached preprocessed images and model outputs, then exit",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="debug-level logging"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _force_utf8_output()
    configure("DEBUG" if args.verbose else "INFO")

    if args.clear_cache:
        from core import cache

        removed = cache.clear()
        print(f"Cleared {removed} cached file(s).")
        return 0

    if args.doctor:
        return _run_doctor()

    if args.ask:
        return _run_freeform(args)

    if not args.study:
        build_parser().print_help()
        print("\nError: --study is required (or use --doctor / --ask).", file=sys.stderr)
        return 2

    return _run_study(args)


def _run_study(args) -> int:
    from pipeline import analyze_study
    from reporting.report_builder import render_summary

    try:
        report, study, _graph = analyze_study(
            args.study,
            clinical_history=args.history,
            referring_question=args.question,
            with_literature=not args.no_literature,
            output_dir=Path(args.out) if args.out else None,
        )
    except MedicalAOSError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        print(
            "\nRun `python main.py --doctor` to check the environment.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    if args.json:
        print(report.to_json())
    else:
        print(render_summary(report))

    # Exit code carries triage, so a caller can alert on it.
    return 3 if report.status.triage == "immediate" else 0


def _run_freeform(args) -> int:
    from pipeline import run_freeform

    try:
        graph = run_freeform(args.ask, args.study)
    except MedicalAOSError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print("\n=== OUTPUT ===")
    from graph_utils import get_sink_nodes

    for node in get_sink_nodes(graph):
        print(f"\n--- {node.id} ({node.capability}) [{node.status}] ---")
        print(node.output or node.error or "(no output)")
    return 0


def _run_doctor() -> int:
    from pipeline import preflight

    checks = preflight()
    print(json.dumps({k: v for k, v in checks.items() if k != "problems"}, indent=2))

    if checks["problems"]:
        print("\nIssues found:")
        for problem in checks["problems"]:
            print(f"  - {problem}")
    else:
        print("\nAll checks passed.")

    print(f"\nReady to run studies: {'yes' if checks['ready'] else 'NO'}")
    return 0 if checks["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
