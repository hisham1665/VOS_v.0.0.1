"""Backward-compatible environment constants for older AOS integrations.

The validated operator configuration lives in :mod:`aos_v0.settings`. These
constants remain intentionally small so importing the package never creates a
network client or requires an API key.
"""

import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
HF_TOKEN: str = os.environ.get("HF_TOKEN") or os.environ.get("HUG", "")
HF_PROVIDER: str = os.environ.get("HF_PROVIDER", "auto")
HF_MODEL: str = os.environ.get("HF_MODEL", "")
_MODEL = os.environ.get("AOS_MODEL", "openai/gpt-oss-20b")
DEFAULT_BUDGET_USD = 0.50
AOS_MEMORY = os.environ.get("AOS_MEMORY", "on").lower() in {"1", "true", "yes", "on"}
MEMORY_MAX_ENTRIES = 64
MEMORY_MAX_TOTAL_BYTES = 512000
MEMORY_MIN_SUBSTANCE_CHARS = 200
AOS_MAX_WORKERS = max(1, int(os.environ.get("AOS_MAX_WORKERS", "4")))


def make_groq(api_key: str):
    """Legacy client factory; new code should use ``ModelGateway``."""
    from groq import Groq
    return Groq(api_key=api_key, timeout=90, max_retries=0)
