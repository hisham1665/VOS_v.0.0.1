"""
Execution Telemetry & Diagnostics Utility.

This provide human-readable console summary outputs for monitoring memory 
tier utilization and execution graph node routing performance.
"""

def _print_memory_summary(bank) -> None:
    stats = bank.get_stats()
    print("\n=== MEMORY SUMMARY ===")
    print(f"  Request tier: {len(bank.entries)} entries")
    if bank.session_bank:
        print(f"  Session tier: {len(bank.session_bank.entries)} entries")
    print(f"  Total bytes: {stats.total_bytes}")
    for entry in bank.get_request_entries():
        if entry.recall_count > 0:
            print(f"  [RECALLED {entry.recall_count}x] {entry.id}: {entry.key}")
        else:
            print(f"  [UNUSED] {entry.id}: {entry.key}")

def _print_routing_summary(graph) -> None:
    print("\n=== ROUTING SUMMARY ===")
    dna_routed = 0
    relaxed_routed = 0
    for node in graph.nodes:
        flags = ", ".join(node.dna.flags) if node.dna and node.dna.flags else "-"
        mode = node.routing_mode or "-"
        if mode == "dna":
            dna_routed += 1
        elif mode == "relaxed":
            relaxed_routed += 1
        status_tag = node.status.upper() if node.status in ("done", "degraded", "failed") else node.status
        print(
            f"  {node.id:<8} {mode:<8} {status_tag:<9} -> "
            f"{node.bound_resource or '-':<22} flags=[{flags}]"
        )
    total = len(graph.nodes)
    print(
        f"  {dna_routed}/{total} exact DNA, "
        f"{relaxed_routed}/{total} relaxed (degraded), "
        f"{total - dna_routed - relaxed_routed}/{total} other"
    )
