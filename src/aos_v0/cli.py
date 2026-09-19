"""
This acts as the main CLI for the application.
It parses user execution flags, normalizes prompt,
media inputs from CLI flags or interactive prompt fallback,
executes the core workflow engine.
"""

import sys
from pathlib import Path

from aos_v0.file_manager.collect import _collect_inputs
from aos_v0.file_manager.image import _extract_image
from aos_v0.prompt.process import run

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from aos_v0.config import DEFAULT_BUDGET_USD

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
