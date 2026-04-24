#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.issue_validation import validate_issue_data  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 scripts/validate_issue.py <issue-json> [<issue-json> ...]", file=sys.stderr)
        return 2

    had_errors = False
    for raw_path in argv[1:]:
        path = Path(raw_path)
        try:
            issue = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"ERROR {path}: file not found", file=sys.stderr)
            had_errors = True
            continue
        except json.JSONDecodeError as exc:
            print(f"ERROR {path}: invalid JSON ({exc})", file=sys.stderr)
            had_errors = True
            continue

        errors = validate_issue_data(issue)
        if errors:
            had_errors = True
            print(f"ERROR {path}: {len(errors)} validation error(s)", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        else:
            print(f"OK {path}")

    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
