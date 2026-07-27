import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
