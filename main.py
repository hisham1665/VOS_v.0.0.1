import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aos_v0.agents.manager_agent import ManagerAgent
from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.agents.integrator_agent import IntegratorAgent


def run(user_prompt: str, image_path: str | None = None) -> str:
    manager = ManagerAgent()
    graph = manager.create_plan(user_prompt, image_path)

    executor = GraphExecutor()
    graph = executor.run(graph, image_path)

    integrator = IntegratorAgent()
    final_output = integrator.integrate(graph)

    print("\n=== FINAL OUTPUT ===")
    print(final_output)
    return final_output


def _extract_image(text: str) -> tuple[str, str | None]:
    """Extract --image <path> from text, returning cleaned text and path."""
    match = re.search(r"--image\s+(\S+)", text)
    if match:
        image_path = match.group(1)
        cleaned = text[: match.start()] + text[match.end() :]
        return cleaned.strip(), image_path
    return text, None


if __name__ == "__main__":
    args = sys.argv[1:]
    image = None

    if "--image" in args:
        idx = args.index("--image")
        if idx + 1 < len(args):
            image = args[idx + 1]
            args = args[:idx] + args[idx + 2 :]
        else:
            print("Error: --image requires a path argument")
            sys.exit(1)

    prompt = " ".join(args) if args else input("Enter your prompt: ")

    prompt, image_from_text = _extract_image(prompt)
    if image_from_text:
        image = image_from_text

    if not prompt:
        print("Error: no prompt provided")
        sys.exit(1)

    run(prompt, image)
