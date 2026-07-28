from pydantic import BaseModel
from typing import Optional, List


class Node(BaseModel):
    id: str
    description: str
    capability: str
    depends_on: List[str] = []
    input: Optional[str] = None
    output: Optional[str] = None
    status: str = "pending"  # pending | running | done | failed
    performed_by: Optional[str] = None


class Graph(BaseModel):
    job: str
    nodes: List[Node]
