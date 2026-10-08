from typing import Optional

def build_summary_key(node_description: str, capability: str, result_preview: str, max_chars: int = 150) -> str:
    """Deterministic, template-based, zero model calls summary generation."""
    # Clean the result preview, replacing newlines with spaces and truncating.
    preview_clean = " ".join(result_preview.split())
    if len(preview_clean) > max_chars:
        preview_clean = preview_clean[:max_chars].rsplit(' ', 1)[0] + "..."
        
    return f"{capability} on '{node_description}' -> {preview_clean}"
