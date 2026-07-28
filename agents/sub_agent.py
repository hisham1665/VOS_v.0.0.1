from aos_v0.capabilities import web_search, summarization, vision
from aos_v0.models import Node

CAPABILITY_MAP = {
    "web_search": web_search.run,
    "summarization": summarization.run,
    "vision": vision.run,
}


class SubAgent:
    def __init__(self, name: str, capability: str):
        self.name = name
        self.capability = capability

    def perform(self, node: Node) -> Node:
        node.status = "running"
        node.performed_by = self.name
        print(f"[{self.name}] performing '{node.description}' (capability: {node.capability})")
        fn = CAPABILITY_MAP[self.capability]
        node.output = fn(node.input, instruction=node.description)
        node.status = "done"
        print(f"[{self.name}] done -> output length {len(node.output)} chars")
        return node
