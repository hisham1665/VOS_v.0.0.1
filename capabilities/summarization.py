from google import genai

from aos_v0.config import GEMINI_API_KEY

_client = genai.Client(api_key=GEMINI_API_KEY)

_SYSTEM_PROMPT = (
    "You are a summarizer. Given a body of text, produce a concise summary "
    "in a few sentences. Return only the summary text with no preamble."
)


def run(text: str) -> str:
    response = _client.models.generate_content(
        model="gemini-2.5-flash",
        contents=text,
        config=genai.types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
        ),
    )
    return response.text
