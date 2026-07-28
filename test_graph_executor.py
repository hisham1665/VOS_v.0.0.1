import sys
sys.path.insert(0, "C:/Users/Asus/Desktop/project/tiny/aos_v0")

from aos_v0.models import Node, Graph
from aos_v0.agents.graph_executor import GraphExecutor

graph = Graph(
    job="Compare solar energy and wind energy as renewable sources.",
    nodes=[
        Node(id="a", description="Search for information about solar energy", capability="web_search", depends_on=[]),
        Node(id="b", description="Search for information about wind energy", capability="web_search", depends_on=[]),
        Node(id="c", description="Summarize findings about solar energy", capability="summarization", depends_on=["a"]),
        Node(id="d", description="Summarize findings about wind energy", capability="summarization", depends_on=["b"]),
        Node(id="e", description="Compare solar energy and wind energy based on the summaries", capability="summarization", depends_on=["c", "d"]),
    ],
)

print("=" * 60)
print("Running 5-node graph with real capability calls")
print("=" * 60)

executor = GraphExecutor()
result = executor.run(graph)

print()
print("=" * 60)
print("FINAL OUTPUT OF NODE 'e' (comparison)")
print("=" * 60)
print(result.nodes[-1].output)

print()
print("=" * 60)
print("WAVE EXECUTION CONFIRMATION")
print("=" * 60)
for n in result.nodes:
    print(f"  node '{n.id}': status={n.status}, performed_by={n.performed_by}, output_len={len(n.output) if n.output else 0}")
