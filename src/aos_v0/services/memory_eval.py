import time
import os
from aos_v0.cli import run

def evaluate_memory(prompt: str, budget: float = 0.5):
    print("=== RUNNING WITHOUT MEMORY ===")
    os.environ["AOS_MEMORY"] = "0"
    start_no = time.monotonic()
    run(prompt, budget_usd=budget)
    end_no = time.monotonic()
    
    print("\n\n=== RUNNING WITH MEMORY ===")
    os.environ["AOS_MEMORY"] = "1"
    start_mem = time.monotonic()
    run(prompt, budget_usd=budget)
    end_mem = time.monotonic()
    
    print("\n\n=== EVALUATION RESULTS ===")
    print(f"Time without memory: {end_no - start_no:.2f}s")
    print(f"Time with memory: {end_mem - start_mem:.2f}s")
    if end_no - start_no > 0:
        improvement = ((end_no - start_no) - (end_mem - start_mem)) / (end_no - start_no) * 100
        print(f"Speedup: {improvement:.1f}%")

if __name__ == "__main__":
    import sys
    test_prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Find 2024 revenue figures for Microsoft and Apple, and summarize them."
    evaluate_memory(test_prompt)
