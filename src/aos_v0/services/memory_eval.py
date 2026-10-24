"""Small, replayable memory measurement harness; it never fabricates live results."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PROMPT_SUITE = [
    {"id": "single_research", "prompt": "Find the current definition of a distributed system.", "inputs": None, "follow_up": None},
    {"id": "single_summary", "prompt": "Summarize the principles of reproducible research.", "inputs": None, "follow_up": None},
    {"id": "multi_compare", "prompt": "Research solar energy and wind energy, then compare them.", "inputs": None, "follow_up": None},
    {"id": "multi_plan", "prompt": "Research Python packaging and dependency locking, then make a checklist.", "inputs": None, "follow_up": None},
    {"id": "document", "prompt": "Extract and summarize the supplied PDF.", "inputs": {"document": "data/artifacts/art_5634db0154b3_AOS_Server_Access_Permission_Letter_Short.pdf"}, "follow_up": None},
    {"id": "document_follow_up", "prompt": "Summarize the supplied PDF.", "inputs": {"document": "data/artifacts/art_5634db0154b3_AOS_Server_Access_Permission_Letter_Short.pdf"}, "follow_up": "What are the key obligations in that PDF?"},
]


@dataclass
class RequestMeasurement:
    prompt_id: str
    wall_clock_s: float
    node_count: int
    total_input_chars: int
    total_output_chars: int
    degraded_nodes: int
    final_answer: str


@dataclass
class EvalReport:
    memory_enabled: bool
    measurements: list[RequestMeasurement]

    def totals(self) -> dict[str, float | int]:
        return {
            "wall_clock_s": round(sum(m.wall_clock_s for m in self.measurements), 3),
            "node_count": sum(m.node_count for m in self.measurements),
            "total_input_chars": sum(m.total_input_chars for m in self.measurements),
            "total_output_chars": sum(m.total_output_chars for m in self.measurements),
            "degraded_nodes": sum(m.degraded_nodes for m in self.measurements),
        }


def run_suite(prompts: list[dict] = PROMPT_SUITE, memory: bool = False) -> EvalReport:
    from aos_v0.prompt.process import run
    import os
    previous = os.environ.get("AOS_MEMORY")
    os.environ["AOS_MEMORY"] = "on" if memory else "off"
    measurements: list[RequestMeasurement] = []
    try:
        for item in prompts:
            events = []
            started = time.monotonic()
            answer = run(item["prompt"], item.get("inputs"), event_sink=events.append)
            measurements.append(RequestMeasurement(
                prompt_id=item["id"], wall_clock_s=round(time.monotonic() - started, 3),
                node_count=sum(event.type.value == "agent_completed" for event in events),
                total_input_chars=sum(event.payload.get("input_chars", 0) for event in events),
                total_output_chars=sum(event.payload.get("output_chars", 0) for event in events),
                degraded_nodes=sum(event.payload.get("status") == "degraded" for event in events),
                final_answer=answer,
            ))
            if item.get("follow_up"):
                # Follow-up is intentionally reported as a separate prompt only
                # when callers provide a session-aware runner in a later phase.
                pass
    finally:
        if previous is None:
            os.environ.pop("AOS_MEMORY", None)
        else:
            os.environ["AOS_MEMORY"] = previous
    return EvalReport(memory, measurements)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure AOS memory with real execution only")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--memory", dest="memory", action="store_true", default=False)
    parser.add_argument("--no-memory", dest="memory", action="store_false")
    args = parser.parse_args()
    report = run_suite(memory=args.memory)
    payload = {"memory_enabled": report.memory_enabled, "measurements": [asdict(item) for item in report.measurements], "totals": report.totals()}
    encoded = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
