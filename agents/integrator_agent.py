from aos_v0.models import Plan


class IntegratorAgent:
    def integrate(self, plan: Plan) -> str:
        print("[integrator-agent] combining outputs from all sub-agents")
        # sequential case: last subtask's output IS the final result,
        # because each subtask already consumed the previous one's output.
        final = plan.subtasks[-1].output
        print("[integrator-agent] done")
        return final
