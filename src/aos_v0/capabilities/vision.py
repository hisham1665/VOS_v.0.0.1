from typing import Optional

from aos_v0.providers.gateway import ModelGateway

_DEFAULT_PROMPT = "Describe this image in detail."


def run(image_path: str, instruction: Optional[str] = None) -> str:
    prompt = instruction if instruction else _DEFAULT_PROMPT

    from pathlib import Path

    if not Path(image_path).is_file():
        raise FileNotFoundError(
            f"Vision capability requires a valid image file, "
            f"got: '{image_path}'\n"
            f"Run with: python -m aos_v0 \"prompt\" --image path/to/image.jpg"
        )

    gateway = ModelGateway()
    try:
        return gateway.image(image_path, prompt)
    finally:
        gateway.close()
