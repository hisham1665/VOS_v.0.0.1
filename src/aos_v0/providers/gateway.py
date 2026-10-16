"""Lazy, bounded provider access for the AOS core.

Model selection belongs to YAML and capability routing; SDK construction belongs
here. Clients are created only when a model-backed capability is actually used,
which keeps local paper evaluation and configuration validation API-key free.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from contextvars import ContextVar
from pathlib import Path
from threading import Lock

import httpx

from aos_v0.settings import AOSSettings, load_settings

_usage: ContextVar[dict | None] = ContextVar("aos_usage", default=None)


def begin_usage():
    return _usage.set({"calls": 0, "input_tokens": 0, "output_tokens": 0, "models": []})


def end_usage(token) -> dict:
    result = _usage.get() or {}
    _usage.reset(token)
    return result


class ModelGateway:
    """Create provider clients lazily and record request-scoped usage."""

    def __init__(self, settings: AOSSettings | None = None):
        self.settings = settings or load_settings()
        self._clients: dict[str, object] = {}
        self._lock = Lock()

    def client(self, role: str):
        with self._lock:
            if role in self._clients:
                return self._clients[role]
            if role not in self.settings.models:
                raise ValueError(f"unknown model profile '{role}'")
            profile = self.settings.models[role]
            key = os.environ.get(profile.api_key_env, "") if profile.api_key_env else ""
            if not key and profile.provider not in {"ollama"}:
                raise ValueError(
                    f"Model profile '{role}' requires {profile.api_key_env}; set it in .env"
                )
            kwargs = {
                "api_key": key or "ollama",
                "timeout": self.settings.runtime.call_timeout_s,
                "max_retries": 0,
                "http_client": httpx.Client(verify=self.settings.runtime.verify_tls),
            }
            if profile.base_url:
                kwargs["base_url"] = profile.base_url
            if profile.provider == "groq":
                from groq import Groq

                client = Groq(**kwargs)
            else:
                from openai import OpenAI

                client = OpenAI(**kwargs)
            self._clients[role] = client
            return client

    def complete(self, role: str, **kwargs):
        profile = self.settings.models[role]
        kwargs.setdefault("model", profile.model)
        kwargs.setdefault("max_tokens", profile.max_tokens)
        response = self.client(role).chat.completions.create(**kwargs)
        self._record(response, profile.model)
        return response

    @staticmethod
    def _record(response, model: str) -> None:
        usage = _usage.get()
        if usage is None:
            return
        usage["calls"] += 1
        metrics = getattr(response, "usage", None)
        usage["input_tokens"] += getattr(metrics, "prompt_tokens", 0) or 0
        usage["output_tokens"] += getattr(metrics, "completion_tokens", 0) or 0
        if model not in usage["models"]:
            usage["models"].append(model)

    def text(
        self,
        role: str,
        text: str,
        system: str,
        instruction: str | None = None,
        structured: bool = False,
    ) -> str:
        if instruction:
            system += f"\n\nTHIS TASK: {instruction}"
        kwargs = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        if structured:
            kwargs["response_format"] = {"type": "json_object"}
        response = self.complete(role, **kwargs)
        return response.choices[0].message.content or ""

    def image(self, path: str, instruction: str | None = None) -> str:
        file = Path(path)
        if not file.is_file():
            raise FileNotFoundError(f"Vision requires an image file: {path}")
        mime = mimetypes.guess_type(path)[0] or "image/png"
        data = base64.b64encode(file.read_bytes()).decode("ascii")
        response = self.complete(
            "vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction or "Describe this image in detail."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
                    ],
                }
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""

    def transcribe(self, path: str, instruction: str | None = None) -> str:
        profile = self.settings.models["audio"]
        with Path(path).open("rb") as file:
            response = self.client("audio").audio.transcriptions.create(
                model=profile.model, file=file
            )
        usage = _usage.get()
        if usage is not None:
            usage["calls"] += 1
            usage["models"].append(profile.model)
        return response.text

    def close(self) -> None:
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if close:
                close()
        self._clients.clear()
