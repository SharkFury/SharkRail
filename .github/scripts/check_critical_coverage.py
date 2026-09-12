"""Enforce per-module branch coverage floors for high-risk runtime surfaces."""

from __future__ import annotations

import json
import sys
from pathlib import Path

CRITICAL_COVERAGE = {
    "src/sharkrail/cli.py": 75.0,
    "src/sharkrail/runtime/backends.py": 60.0,
    "src/sharkrail/service/master.py": 65.0,
    "src/sharkrail/service/ownership.py": 80.0,
}


def check(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    files = report.get("files")
    if not isinstance(files, dict):
        return ["coverage report has no files mapping"]

    failures = []
    for path, minimum in CRITICAL_COVERAGE.items():
        details = files.get(path)
        if not isinstance(details, dict):
            failures.append(f"{path}: missing from coverage report")
            continue
        summary = details.get("summary")
        actual = summary.get("percent_covered") if isinstance(summary, dict) else None
        if not isinstance(actual, (int, float)):
            failures.append(f"{path}: missing percent_covered")
            continue
        print(f"{path}: {actual:.2f}% (minimum {minimum:.2f}%)")
        if actual < minimum:
            failures.append(f"{path}: {actual:.2f}% is below {minimum:.2f}%")
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_critical_coverage.py COVERAGE_JSON", file=sys.stderr)
        return 2
    failures = check(Path(sys.argv[1]))
    if failures:
        for failure in failures:
            print(f"critical coverage failure: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
