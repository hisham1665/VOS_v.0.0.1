import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aos_v0.agents.manager_agent import ManagerAgent
from aos_v0.agents.sub_agent import SubAgent
from aos_v0.agents.integrator_agent import IntegratorAgent


def run(user_prompt: str) -> str:
    manager = ManagerAgent()
    plan = manager.create_plan(user_prompt)

    previous_output = user_prompt
    for i, subtask in enumerate(plan.subtasks, start=1):
        subtask.input = previous_output
        agent = SubAgent(name=f"sub-agent-{i}", capability=subtask.capability)
        subtask = agent.perform(subtask)
        previous_output = subtask.output

    integrator = IntegratorAgent()
    final_output = integrator.integrate(plan)

    print("\n=== FINAL OUTPUT ===")
    print(final_output)
    return final_output


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = input("Enter your prompt: ")
    run(prompt)
