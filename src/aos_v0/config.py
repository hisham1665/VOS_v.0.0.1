"""Compatibility constants; new integrations use settings.load_settings()."""

import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUG", "")
HF_PROVIDER = os.environ.get("HF_PROVIDER", "auto")
HF_MODEL = os.environ.get("HF_MODEL", "")
_MODEL = "openai/gpt-oss-20b"
DEFAULT_BUDGET_USD = 0.50
AOS_MEMORY = os.environ.get("AOS_MEMORY", "on").lower() in {"1", "true", "yes", "on"}
MEMORY_MAX_ENTRIES = 64
MEMORY_MAX_TOTAL_BYTES = 512000
MEMORY_MIN_SUBSTANCE_CHARS = 200
AOS_MAX_WORKERS = max(1, int(os.environ.get("AOS_MAX_WORKERS", "4")))


def make_groq(api_key: str):
    from groq import Groq
    return Groq(api_key=api_key, timeout=90, max_retries=0)
