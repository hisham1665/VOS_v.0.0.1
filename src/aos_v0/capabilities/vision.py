"""Image understanding through the operator-selected vision profile."""

from aos_v0.providers.gateway import ModelGateway


def run(image_path: str, instruction: str | None = None) -> str:
    gateway = ModelGateway()
    try:
        return gateway.image(image_path, instruction)
    finally:
        gateway.close()
