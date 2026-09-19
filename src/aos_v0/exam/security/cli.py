"""Phase 14 security CLI: `python -m aos_v0.exam.security <exam.json> [opts]`.

The kernel-safe security surface -- validate an exam payload before it is
trusted, and summarize an access log. Enforcement of the role policy at an
HTTP boundary is documented as the API layer's job (spec Ph14).
"""

from __future__ import annotations

import json
import sys
from typing import List, Optional

from .access_log import AccessLog
from .input_validation import validate_exam_payload


def security_cli_main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="aos_v0.exam.security",
        description="Validate exam payloads and summarize access logs (Ph14).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate an exam config payload")
    p_validate.add_argument("source", help="path to a JSON/YAML exam config")
    p_validate.add_argument("--json", action="store_true")

    p_logs = sub.add_parser("logs", help="summarize an access log")
    p_logs.add_argument("dir", help="directory holding access.log.jsonl")
    p_logs.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "validate":
        import yaml

        path = args.source
        try:
            if path.endswith((".yaml", ".yml")):
                with open(path, encoding="utf-8") as handle:
                    data = yaml.safe_load(handle)
            else:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise SystemExit(f"error: cannot load {path}: {exc}")
        issues = validate_exam_payload(data)
        errors = [i for i in issues if i.error]
        if args.json:
            print(json.dumps([i.model_dump() for i in issues], indent=2))
        else:
            for issue in issues:
                prefix = "error" if issue.error else "warn"
                print(f"  [{prefix}] {issue.code}: {issue.message}")
            print(f"validation: {len(errors)} error(s), "
                  f"{len(issues) - len(errors)} warning(s)")
        return 1 if errors else 0

    if args.command == "logs":
        log = AccessLog(args.dir)
        counts = log.counts()
        if args.json:
            print(json.dumps(counts, indent=2))
        else:
            print(json.dumps(counts))
        return 0

    return 2


def main(argv: Optional[List[str]] = None) -> int:
    return security_cli_main(argv)


if __name__ == "__main__":
    sys.exit(security_cli_main())