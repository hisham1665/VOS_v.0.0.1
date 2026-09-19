"""Allow `python -m aos_v0.exam.health`."""

from __future__ import annotations

import sys
from typing import List, Optional


def health_cli_main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .healthcheck import healthcheck, render_health_table

    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.health",
        description="Model/storage/queue healthcheck for the exam pipeline.",
    )
    parser.add_argument("--review-dir", default=None, help="review store dir to inspect")
    parser.add_argument("--storage-dir", default=None, help="dir to probe for write access")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = healthcheck(review_dir=args.review_dir, storage_dir=args.storage_dir)
    if args.json:
        import json

        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(render_health_table(report))
    return 0 if report.overall == "healthy" else 1


def main(argv: Optional[List[str]] = None) -> int:
    return health_cli_main(argv)


if __name__ == "__main__":
    sys.exit(health_cli_main())