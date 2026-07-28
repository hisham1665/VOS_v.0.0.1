from typing import Optional

from ddgs import DDGS
from groq import Groq

from aos_v0.config import GROQ_API_KEY

_client = Groq(api_key=GROQ_API_KEY)
_MODEL = "llama-3.3-70b-versatile"


def run(query: str, instruction: Optional[str] = None) -> str:
    search_query = instruction if instruction else query

    results_text = _search_web(search_query)
    if not results_text:
        results_text = "No web results found."

    response = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a research assistant. Given web search results, "
                    "extract ALL specific facts, numbers, dates, statistics, "
                    "and data points. Be extremely precise — include exact "
                    "numbers (e.g. '5 World Cup titles', '1966', '1-0'). "
                    "Never say 'not specified' or 'numerous' — if the data "
                    "is in the search results, state it. If truly not in the "
                    "results, say 'not found in search results'. "
                    "Return only the factual answer with no preamble."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"QUERY: {search_query}\n\n"
                    f"WEB SEARCH RESULTS:\n{results_text}"
                ),
            },
        ],
        temperature=0.3,
        max_tokens=2048,
    )
    return response.choices[0].message.content or "No results found."


def _search_web(query: str, max_results: int = 5) -> str:
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return ""
        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            parts.append(f"[{i}] {title}\n{body}")
        return "\n\n".join(parts)
    except Exception as exc:
        return f"[Search error: {exc}]"
