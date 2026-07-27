from pathlib import Path

from google import genai
from google.genai.types import Tool, FunctionDeclaration

from aos_v0.config import GEMINI_API_KEY
from aos_v0.models import Plan

_CAPABILITIES = ["web_search", "summarization"]

_SYSTEM_PROMPT = f"""\
You are a task planner. Given a user job, decompose it into 2-5 sequential \
subtasks. Each subtask must use EXACTLY ONE capability from this fixed list: \
{_CAPABILITIES}

You MUST call the create_plan function with the decomposed plan. Do not \
respond with plain text. The id for each subtask must be a simple string like \
"1", "2", etc. The capability field must be one of the allowed values exactly \
as listed above — never invent new capabilities."""

_CREATE_PLAN_FN = FunctionDeclaration(
    name="create_plan",
    description="Submit the decomposed task plan.",
    parameters={
        "type": "object",
        "properties": {
            "job": {
                "type": "string",
                "description": "The original user job.",
            },
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "capability": {"type": "string"},
                    },
                    "required": ["id", "description", "capability"],
                },
            },
        },
        "required": ["job", "subtasks"],
    },
)


class ManagerAgent:
    def __init__(self):
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    def create_plan(self, user_prompt: str) -> Plan:
        print("[manager-agent] decomposing job into subtasks...")

        try:
            plan = self._call_llm(user_prompt)
        except Exception as exc:
            plan = self._call_llm_with_retry(user_prompt, str(exc))

        self._write_plan_md(plan)

        print(
            "[manager-agent] plan written to outputs/plan.md "
            f"({len(plan.subtasks)} subtasks)"
        )
        return plan

    def _call_llm(self, user_prompt: str) -> Plan:
        response = self._client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                tools=[Tool(function_declarations=[_CREATE_PLAN_FN])],
                tool_config=genai.types.ToolConfig(
                    function_calling_config=genai.types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=["create_plan"],
                    ),
                ),
            ),
        )
        return Plan.model_validate(self._extract_fn_call(response))

    def _call_llm_with_retry(self, user_prompt: str, error_msg: str) -> Plan:
        response = self._client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                user_prompt,
                "I will create the plan.",
                (
                    "Your previous plan failed validation:\n"
                    f"{error_msg}\n\n"
                    "Please fix the errors and call create_plan again."
                ),
            ],
            config=genai.types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                tools=[Tool(function_declarations=[_CREATE_PLAN_FN])],
                tool_config=genai.types.ToolConfig(
                    function_calling_config=genai.types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=["create_plan"],
                    ),
                ),
            ),
        )
        return Plan.model_validate(self._extract_fn_call(response))

    @staticmethod
    def _extract_fn_call(response) -> dict:
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "create_plan":
                return dict(part.function_call.args)
        raise RuntimeError(
            "LLM did not return a create_plan function call. "
            f"Parts: {[p for p in response.candidates[0].content.parts]}"
        )

    @staticmethod
    def _write_plan_md(plan: Plan) -> None:
        lines = [
            "# Task Plan",
            f"**Job:** {plan.job}",
            "",
            "| # | Subtask | Capability |",
            "|---|---------|------------|",
        ]
        for st in plan.subtasks:
            lines.append(f"| {st.id} | {st.description} | {st.capability} |")

        path = Path(__file__).resolve().parent.parent / "outputs" / "plan.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
