from aos_v0.capabilities import web_search, summarization
from aos_v0.models import Subtask

CAPABILITY_MAP = {
    "web_search": web_search.run,
    "summarization": summarization.run,
}


class SubAgent:
    def __init__(self, name: str, capability: str):
        self.name = name
        self.capability = capability

    def perform(self, subtask: Subtask) -> Subtask:
        subtask.status = "running"
        subtask.performed_by = self.name
        print(f"[{self.name}] performing '{subtask.description}' (capability: {subtask.capability})")
        fn = CAPABILITY_MAP[self.capability]
        subtask.output = fn(subtask.input)
        subtask.status = "done"
        print(f"[{self.name}] done -> output length {len(subtask.output)} chars")
        return subtask
