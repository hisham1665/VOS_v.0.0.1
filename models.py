from pydantic import BaseModel
from typing import Optional, List


class Subtask(BaseModel):
    id: str
    description: str
    capability: str
    input: Optional[str] = None
    output: Optional[str] = None
    status: str = "pending"  # pending | running | done | failed
    performed_by: Optional[str] = None


class Plan(BaseModel):
    job: str
    subtasks: List[Subtask]
