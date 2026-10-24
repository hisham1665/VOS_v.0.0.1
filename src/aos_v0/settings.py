"""Validated, hot-read operator configuration; credentials stay in the environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelProfile(SettingsModel):
    provider: Literal["groq", "openai", "ollama", "compatible"] = "groq"
    model: str = "openai/gpt-oss-20b"
    api_key_env: str = "GROQ_API_KEY"
    base_url: str | None = None
    max_tokens: int = Field(default=2048, ge=1)


class RuntimeSettings(SettingsModel):
    max_workers: int = Field(default=4, ge=1, le=64)
    call_timeout_s: float = Field(default=90, gt=0)
    max_elapsed_s: float = Field(default=600, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_nodes: int = Field(default=64, ge=1, le=512)
    max_replans: int = Field(default=2, ge=0, le=8)
    max_added_nodes: int = Field(default=4, ge=0, le=32)
    context_chars: int = Field(default=24000, ge=1024)
    per_source_chars: int = Field(default=6000, ge=256)
    budget_usd: float = Field(default=0.50, ge=0)
    completeness_check: bool = False
    verify_tls: bool = True
    output_dir: str = "data/outputs"
    log_dir: str = "log"


class MemorySettings(SettingsModel):
    enabled: bool = True
    reuse: bool = True
    max_entries: int = Field(default=64, ge=1)
    max_value_bytes: int = Field(default=32000, ge=256)
    max_total_bytes: int = Field(default=512000, ge=256)
    min_substance_chars: int = Field(default=200, ge=1)
    min_relevance: float = Field(default=0.15, ge=0, le=1)
    ttl_s: float = Field(default=3600, gt=0)
    log_decisions: bool = False
    log_content: bool = False


class CapabilitySettings(SettingsModel):
    handler: Literal["chat", "python"] = "chat"
    description: str = ""
    flags: list[str] = Field(min_length=1)
    input_type: Literal["text", "image", "audio", "document", "structured"] = "text"
    output_type: Literal["text", "structured"] = "text"
    output_schema: dict | None = None
    model_profile: str = "text"
    system_prompt: str = "Complete the requested task using the supplied evidence. Report gaps explicitly."
    callable: str | None = None
    enabled: bool = True
    estimated_cost_usd: float = Field(default=0, ge=0)
    latency_ms: int = Field(default=5000, ge=0)
    quality: float = Field(default=0.7, ge=0, le=1)
    risk: Literal["low", "medium", "high"] = "low"

    @model_validator(mode="after")
    def check_handler(self):
        if self.handler == "python" and not self.callable:
            raise ValueError("python capabilities require callable: package.module:function")
        if self.handler == "chat" and self.input_type != "text":
            raise ValueError("chat capabilities accept text; use a python adapter for media")
        return self


def _models() -> dict[str, ModelProfile]:
    return {
        "text": ModelProfile(),
        "planner": ModelProfile(model="openai/gpt-oss-120b", max_tokens=4096),
        "dna": ModelProfile(),
        "dna_strong": ModelProfile(model="openai/gpt-oss-120b"),
        "synthesis": ModelProfile(model="openai/gpt-oss-120b", max_tokens=4096),
        "vision": ModelProfile(provider="ollama", model="gemma3:4b", api_key_env="", base_url="http://localhost:11434/v1"),
        "audio": ModelProfile(model="whisper-large-v3-turbo"),
    }


class AOSSettings(SettingsModel):
    schema_version: Literal[1] = 1
    domain: str = "general"
    instructions: str = ""
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    models: dict[str, ModelProfile] = Field(default_factory=_models)
    capabilities: dict[str, CapabilitySettings] = Field(default_factory=dict)
    disabled_capabilities: list[str] = Field(default_factory=list)
    hf_catalog: bool = False

    @model_validator(mode="after")
    def check_profiles(self):
        required = {"text", "planner", "dna", "dna_strong", "synthesis", "vision", "audio"}
        missing = required - self.models.keys()
        if missing:
            raise ValueError(f"missing model profiles: {sorted(missing)}")
        for name, capability in self.capabilities.items():
            if capability.handler == "chat" and capability.model_profile not in self.models:
                raise ValueError(f"capability {name!r}: unknown model profile {capability.model_profile!r}")
        return self


def load_settings(path: str | Path | None = None) -> AOSSettings:
    """Read YAML per request so an operator can change profiles without restarting."""
    explicit = path or os.environ.get("AOS_CONFIG")
    config_path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parents[2] / "aos.yaml"
    if explicit and not config_path.is_file():
        raise ValueError(f"AOS configuration not found: {config_path}")
    load_dotenv(config_path.parent / ".env", override=False)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("AOS configuration must be a YAML mapping")
    defaults = AOSSettings().model_dump()
    # Profiles can be partially overridden without copying every built-in role.
    for role, values in raw.get("models", {}).items():
        defaults["models"][role] = {**defaults["models"].get(role, {}), **values}
    raw = {**raw, "models": defaults["models"]}
    settings = AOSSettings.model_validate(raw)
    if "AOS_MEMORY" in os.environ:
        settings.memory.enabled = os.environ["AOS_MEMORY"].lower() in {"1", "on", "true", "yes"}
    return settings
