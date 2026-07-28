from typing import Optional

from groq import Groq

from aos_v0.config import GROQ_API_KEY

_client = Groq(api_key=GROQ_API_KEY)

_MODEL = "llama-3.3-70b-versatile"

_DEFAULT_SYSTEM = (
    "You are a factual summarizer. Given a body of text, extract and "
    "preserve ALL specific facts, numbers, dates, statistics, and names. "
    "Produce a detailed summary that keeps every concrete data point. "
    "Never use vague language like 'multiple', 'not specified', or 'various' "
    "when exact numbers are available in the source text. "
    "Return only the summary text with no preamble."
)


def run(text: str, instruction: Optional[str] = None) -> str:
    system = instruction if instruction else _DEFAULT_SYSTEM
    response = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""
