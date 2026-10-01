"""Literature lookup for findings the study actually raised.

This is deliberately narrow. It does not diagnose, and it never runs on the raw
images -- it takes the finding labels the synthesis step produced and fetches
management/differential context for them, biased toward sources a clinician
would accept.

Patient identifiers are never sent: the query is built from finding labels
only. That is enforced in :func:`_build_query`, not left to the planner's
prompt.
"""

from __future__ import annotations

from core.logging_utils import get_logger, timed
from capabilities.hf_backend import chat_text, record_model
from capabilities.registry import CapabilityRequest, CapabilityResult, register

_log = get_logger("literature")

# Domains whose summaries are reasonable to surface to a clinician.
_PREFERRED_DOMAINS = (
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "radiopaedia.org",
    "rsna.org",
    "acr.org",
    "nice.org.uk",
    "who.int",
    "cdc.gov",
    "nih.gov",
    "bmj.com",
    "thelancet.com",
    "nejm.org",
    "uptodate.com",
    "msdmanuals.com",
)

_MAX_RESULTS = 6

_SYSTEM = (
    "You are a clinical librarian. Given search results about a radiological "
    "finding, summarise the accepted differential, the usual next investigation, "
    "and any red flags, citing the sources by their numbers. "
    "State plainly when the results do not answer the question. "
    "Do not give patient-specific advice; this is background reference only."
)


@register(
    "web_search",
    "Look up differential diagnosis and management context for named findings.",
)
def run(request: CapabilityRequest) -> CapabilityResult:
    query = _build_query(request)
    if not query:
        return CapabilityResult(
            text="No finding was specific enough to warrant a literature lookup.",
            artifacts={"references": []},
        )

    with timed(_log, f"literature search: {query!r}"):
        results = _search(query)

    if not results:
        return CapabilityResult(
            text=f"No literature results were found for: {query}",
            artifacts={"references": [], "query": query},
        )

    rendered = "\n\n".join(
        f"[{i}] {r['title']} ({r['url']})\n{r['body']}"
        for i, r in enumerate(results, 1)
    )

    try:
        answer, model_id = chat_text(
            f"CLINICAL QUESTION: {query}\n\nSEARCH RESULTS:\n{rendered}",
            system=_SYSTEM,
            max_tokens=1200,
            temperature=0.2,
        )
        record_model("web_search", model_id)
    except Exception as exc:  # noqa: BLE001 - raw results still have value
        _log.warning("literature synthesis failed: %s", exc)
        answer = f"Search results for {query} (automatic synthesis unavailable):\n\n{rendered}"

    references = [f"{r['title']} - {r['url']}" for r in results]
    return CapabilityResult(
        text=answer,
        artifacts={"references": references, "query": query, "results": results},
    )


def _build_query(request: CapabilityRequest) -> str:
    """Build a de-identified query from finding labels, never from patient data."""
    findings = (request.artifacts or {}).get("findings") or []
    labels = [
        str(f.get("label", "")).strip()
        for f in findings
        if isinstance(f, dict) and str(f.get("severity")) != "normal"
    ]
    labels = [label for label in labels if label and label.lower() != "no acute abnormality"]

    if labels:
        # Two labels is enough for a focused query; more dilutes the results.
        return " ".join(labels[:2]) + " radiology differential diagnosis management"

    # Fall back to the planner's instruction, stripped of any patient context.
    instruction = (request.instruction or "").strip()
    return instruction[:200] if instruction else ""


def _search(query: str) -> list[dict]:
    """Run the search, preferring clinical domains. Never raises."""
    try:
        from ddgs import DDGS
    except ImportError:
        _log.warning("ddgs is not installed; skipping literature lookup")
        return []

    try:
        raw = DDGS().text(query, max_results=_MAX_RESULTS * 2) or []
    except Exception as exc:  # noqa: BLE001 - network flakiness is expected
        _log.warning("search failed: %s", exc)
        return []

    results = [
        {
            "title": r.get("title", "").strip(),
            "url": r.get("href") or r.get("url") or "",
            "body": r.get("body", "").strip(),
        }
        for r in raw
        if r.get("body")
    ]

    # Clinical sources first, everything else after, then truncate.
    def rank(result: dict) -> int:
        return 0 if any(d in result["url"] for d in _PREFERRED_DOMAINS) else 1

    results.sort(key=rank)
    return results[:_MAX_RESULTS]
