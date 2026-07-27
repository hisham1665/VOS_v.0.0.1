from google import genai
from google.genai.types import Tool, GoogleSearch

from aos_v0.config import GEMINI_API_KEY

_client = genai.Client(api_key=GEMINI_API_KEY)
_tool = Tool(google_search=GoogleSearch())


def run(query: str) -> str:
    response = _client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config=genai.types.GenerateContentConfig(tools=[_tool]),
    )

    chunks = []
    if response.text:
        chunks.append(response.text)

    grounding = getattr(response, "grounding_metadata", None)
    if grounding:
        for chunk in getattr(grounding, "grounding_chunks", []) or []:
            snippet = getattr(chunk, "snippet", None) or getattr(chunk, "text", None)
            if snippet:
                chunks.append(snippet)

    return "\n\n".join(chunks).strip() if chunks else "No results found."
