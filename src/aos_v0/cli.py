import re
import sys
import time
from pathlib import Path

from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.agents.integrator_agent import IntegratorAgent
from aos_v0.agents.manager_agent import ManagerAgent
from aos_v0.core.constraint_policy import ConstraintPolicy
from aos_v0.core.dna_extractor import DNAExtractor
from aos_v0.core.failure_manager import FailureManager
from aos_v0.core.events import EventType, OrchestrationEvent
from aos_v0.core.models import Artifact
from aos_v0.core.runtime import RequestContext
from aos_v0.logbook import SessionLogger
from aos_v0.services.resource_registration import build_hf_enabled_registry

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BUDGET_USD = 0.50

# File extensions recognised for auto-detection.
_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".wma", ".aac"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".pdf"}


def run(
    user_prompt: str,
    inputs: dict[str, str] | None = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
    context: RequestContext | None = None,
    event_sink=None,
    session_bank=None,
) -> str:
    """Execute the established kernel, optionally publishing real lifecycle events."""
    from aos_v0.core.shared_memory import SharedMemoryBank
    from aos_v0.config import AOS_MEMORY
    
    with SessionLogger(prompt=user_prompt, tag="run") as _logger:
        request_id = context.request_id if context else ""
        started = time.monotonic()

        # Initialize Request-tier Memory Bank if AOS_MEMORY is enabled
        request_bank = SharedMemoryBank(request_id, session_bank) if AOS_MEMORY else None

        def emit(kind: EventType, **payload) -> None:
            if event_sink:
                event_sink(OrchestrationEvent(kind, request_id, payload))

        emit(EventType.REQUEST_RECEIVED, prompt=user_prompt,
             artifacts=[artifact.model_dump() for artifact in (context.artifacts if context else [])])
        registry = build_hf_enabled_registry()

        manager = ManagerAgent()
        graph = manager.create_plan(user_prompt, inputs)
        if context:
            graph.artifacts.update({artifact.id: artifact for artifact in context.artifacts})
        emit(EventType.INTENT_DETECTED, planned_capabilities=[node.capability for node in graph.nodes])

        capability_started = time.monotonic()
        emit(EventType.CAPABILITY_ANALYSIS_STARTED, node_count=len(graph.nodes))
        graph = DNAExtractor().extract_graph(graph)
        emit(EventType.CAPABILITY_DETECTED, elapsed_ms=round((time.monotonic() - capability_started) * 1000, 2),
             nodes=[{"node_id": node.id, "flags": node.dna.flags if node.dna else []} for node in graph.nodes])

        ConstraintPolicy(registry, job_budget_usd=budget_usd).apply(graph)
        _check_satisfiable(graph, registry)
        manager.write_plan(graph)

        failure_manager = FailureManager(registry)
        emit(EventType.ROUTING_STARTED, node_count=len(graph.nodes))
        # Pass memory bank to GraphExecutor
        graph = GraphExecutor(registry, failure_manager, event_sink=event_sink, request_id=request_id, memory_bank=request_bank).run(graph, inputs)

        final_output = IntegratorAgent().integrate(graph)
        emit(EventType.RESULT_READY, result=final_output)
        emit(EventType.REQUEST_COMPLETED, elapsed_ms=round((time.monotonic() - started) * 1000, 2),
             status="completed")

        _print_routing_summary(graph)
        if request_bank:
            _print_memory_summary(request_bank)
            request_bank.promote_to_session()
            dump_file = f"log/memory_dump_{request_id or int(time.time())}.txt"
            request_bank.dump_to_text(dump_file)
            print(f"  [memory] detailed dump saved to: {dump_file}")
            
        print("\n" + failure_manager.report())

        print("\n=== FINAL OUTPUT ===")
        try:
            print(final_output)
        except UnicodeEncodeError:
            print(final_output.encode("ascii", "replace").decode("ascii"))
        print(f"\n[logbook] session log saved to: {_logger.log_path}")
        return final_output


def _check_satisfiable(graph, registry) -> None:
    """Fail fast, naming the missing capability, before any node executes."""
    problems = []
    for node in graph.nodes:
        if not node.dna:
            continue
        missing = registry.unsatisfiable_flags(node.dna)
        if missing:
            problems.append(f"node '{node.id}' requires {missing}")
    if problems:
        raise RuntimeError(
            "[admission-control] plan rejected — no registered resource provides: "
            + "; ".join(problems)
        )
    print("[admission-control] all DNA flags satisfiable by registered resources")


def _print_routing_summary(graph) -> None:
    print("\n=== ROUTING SUMMARY ===")
    dna_routed = 0
    relaxed_routed = 0
    for node in graph.nodes:
        flags = ", ".join(node.dna.flags) if node.dna and node.dna.flags else "-"
        mode = node.routing_mode or "-"
        if mode == "dna":
            dna_routed += 1
        elif mode == "relaxed":
            relaxed_routed += 1
        status_tag = node.status.upper() if node.status in ("done", "degraded", "failed") else node.status
        print(
            f"  {node.id:<8} {mode:<8} {status_tag:<9} -> "
            f"{node.bound_resource or '-':<22} flags=[{flags}]"
        )
    total = len(graph.nodes)
    print(
        f"  {dna_routed}/{total} exact DNA, "
        f"{relaxed_routed}/{total} relaxed (degraded), "
        f"{total - dna_routed - relaxed_routed}/{total} other"
    )

def _print_memory_summary(bank) -> None:
    stats = bank.get_stats()
    print("\n=== MEMORY SUMMARY ===")
    print(f"  Request tier: {len(bank.entries)} entries")
    if bank.session_bank:
        print(f"  Session tier: {len(bank.session_bank.entries)} entries")
    print(f"  Total bytes: {stats.total_bytes}")
    for entry in bank.get_request_entries():
        if entry.recall_count > 0:
            print(f"  [RECALLED {entry.recall_count}x] {entry.id}: {entry.key}")
        else:
            print(f"  [UNUSED] {entry.id}: {entry.key}")


def _detect_input_type(path: str) -> str:
    """Auto-detect input type from file extension."""
    ext = Path(path).suffix.lower()
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    return "text"


def _scan_inputs_folder() -> dict[str, str]:
    """Scan the inputs/ folder for files with known extensions.

    Only called when the user passes `--use-inputs-folder` -- the folder is a
    convenience dropbox, not an implicit input source, so stale files left
    there cannot pollute unrelated text-only queries.

    Returns {type: path} for each found file. If multiple files of the same
    type exist, only the first one is used (subsequent calls would need a list
    API, which is out of scope for now).
    """
    inputs_dir = PROJECT_ROOT / "data" / "inputs"
    if not inputs_dir.is_dir():
        return {}

    found: dict[str, str] = {}
    for f in sorted(inputs_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            input_type = _detect_input_type(str(f))
            if input_type not in found:
                found[input_type] = str(f)
    return found


def _extract_image(text: str) -> tuple[str, str | None]:
    """Extract --image <path> from text, returning cleaned text and path."""
    match = re.search(r"--image\s+(\S+)", text)
    if match:
        image_path = match.group(1)
        cleaned = text[: match.start()] + text[match.end() :]
        return cleaned.strip(), image_path
    return text, None


def _pop_flag(args: list[str], flag: str) -> str | None:
    """Remove `flag <value>` from args, returning the value."""
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + 1 >= len(args):
        print(f"Error: {flag} requires a value")
        sys.exit(1)
    value = args[idx + 1]
    del args[idx : idx + 2]
    return value


def _collect_inputs(args: list[str]) -> dict[str, str]:
    """Collect typed inputs from CLI flags and (optionally) the inputs/ folder.

    Priority: explicit CLI flags (--input, --audio, --image) override the
    inputs/ folder scan. The folder scan is opt-in -- it only runs when
    --use-inputs-folder is passed -- so leftover files in data/inputs/ cannot
    silently derail unrelated queries. Returns {type: path}.
    """
    inputs: dict[str, str] = {}

    # 1. Folder scan (lowest priority) -- only when explicitly requested.
    if "--use-inputs-folder" in args:
        args.remove("--use-inputs-folder")
        inputs.update(_scan_inputs_folder())

    # 2. --input <path> (auto-detect type from extension, repeatable).
    while "--input" in args:
        idx = args.index("--input")
        if idx + 1 >= len(args):
            print("Error: --input requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: input file not found: {path}")
            sys.exit(1)
        input_type = _detect_input_type(path)
        inputs[input_type] = path

    # 3. --audio <path> (shorthand for --input with audio type).
    while "--audio" in args:
        idx = args.index("--audio")
        if idx + 1 >= len(args):
            print("Error: --audio requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: audio file not found: {path}")
            sys.exit(1)
        inputs["audio"] = path

    # 4. --image <path> (legacy flag, still supported).
    while "--image" in args:
        idx = args.index("--image")
        if idx + 1 >= len(args):
            print("Error: --image requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: image file not found: {path}")
            sys.exit(1)
        inputs["image"] = path

    return inputs


def main(args: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if args is None else args)

    budget_arg = _pop_flag(args, "--budget")
    budget = float(budget_arg) if budget_arg else DEFAULT_BUDGET_USD

    # Collect all typed inputs (--input, --audio, --image, inputs/ folder).
    inputs = _collect_inputs(args)

    prompt = " ".join(args) if args else input("Enter your prompt: ")

    # Also accept --image embedded in the prompt text (backward compat).
    prompt, image_from_text = _extract_image(prompt)
    if image_from_text:
        inputs["image"] = image_from_text

    if not prompt:
        print("Error: no prompt provided")
        sys.exit(1)

    run(prompt, inputs if inputs else None, budget)


if __name__ == "__main__":
    main()
