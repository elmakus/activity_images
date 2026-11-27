"""Enforce the cumulative HA quality-scale statement coverage requirements."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    root = Path("custom_components/activity_images")
    expected = {path.as_posix() for path in root.rglob("*.py")}
    report = json.loads(Path(sys.argv[1]).read_text())["files"]
    failed = False
    for name in sorted(expected):
        if name not in report:
            print(f"FAIL {name}: not measured")
            failed = True
            continue
        summary = report[name]["summary"]
        total, covered = summary["num_statements"], summary["covered_lines"]
        # Compare exact counts, not rounded percentages.
        if name.endswith("/config_flow.py"):
            passed = covered == total
        else:
            passed = total == 0 or covered * 100 > total * 95
        print(f"{'PASS' if passed else 'FAIL'} {name}: {covered}/{total} ({summary['percent_covered']:.2f}%)")
        failed |= not passed
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
