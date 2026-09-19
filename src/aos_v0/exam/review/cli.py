"""CLI for the review dashboard / queue (Phase 11 deliverable).

Usage::

    python -m aos_v0.exam.review <queue_dir> list [--all]
    python -m aos_v0.exam.review <queue_dir> show <item-id>
    python -m aos_v0.exam.review <queue_dir> resolve <item-id> \\
            --decision accept|modify|escalate [--marks N] [--reviewer X] [--note S]
    python -m aos_v0.exam.review <queue_dir> stats
    python -m aos_v0.exam.review <queue_dir> history --paper <paper-id> [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .dashboard import render_card, render_queue, render_stats
from .models import ReviewDecision
from .store import ReviewStore


def review_cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aos_v0.exam.review",
        description="Human review queue + audit log for uncertain evaluations.",
    )
    parser.add_argument("queue_dir", help="directory of the review store")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show the review queue")
    p_list.add_argument("--all", action="store_true", help="include reviewed items")

    p_show = sub.add_parser("show", help="print one review card")
    p_show.add_argument("item_id")

    p_resolve = sub.add_parser("resolve", help="accept / modify / escalate a card")
    p_resolve.add_argument("item_id")
    p_resolve.add_argument(
        "--decision", choices=[d.value for d in ReviewDecision], required=True
    )
    p_resolve.add_argument("--marks", type=float)
    p_resolve.add_argument("--reviewer", default="")
    p_resolve.add_argument("--note", default="")

    p_stats = sub.add_parser("stats", help="queue summary")

    p_history = sub.add_parser("history", help="evaluation history for one paper")
    p_history.add_argument("--paper", required=True)
    p_history.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    store = ReviewStore(args.queue_dir)

    if args.command == "list":
        print(render_queue(store.all() if args.all else store.pending()))
    elif args.command == "show":
        item = store.get(args.item_id)
        if item is None:
            print(f"no review item '{args.item_id}' in the queue", file=sys.stderr)
            return 2
        print(render_card(item))
    elif args.command == "resolve":
        decision = ReviewDecision(args.decision)
        try:
            item = store.resolve(
                args.item_id,
                decision,
                final_marks=args.marks,
                reviewer=args.reviewer,
                note=args.note,
            )
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(
            f"resolved {item.id} -> {decision.value}"
            + (f"  marks={item.final_marks:g}" if item.final_marks is not None else "")
        )
    elif args.command == "stats":
        print(render_stats(store.stats()))
    elif args.command == "history":
        items = store.evaluation_history(args.paper)
        if args.json:
            print(json.dumps([item.to_plan_json() for item in items], indent=2))
        else:
            print(render_queue(items))
    return 0