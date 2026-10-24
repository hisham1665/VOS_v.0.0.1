"""Speech-to-text through a configured Groq/OpenAI-compatible audio profile."""

from aos_v0.providers.gateway import ModelGateway


def run(path: str, instruction: str | None = None) -> str:
    gateway = ModelGateway()
    try:
        return gateway.transcribe(path, instruction)
    finally:
        gateway.close()
