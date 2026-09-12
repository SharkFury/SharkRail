import json
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / ".github" / "scripts" / "check_critical_coverage.py"
CRITICAL_FILES = {
    "src/sharkrail/cli.py": 75.0,
    "src/sharkrail/runtime/backends.py": 60.0,
    "src/sharkrail/service/master.py": 65.0,
    "src/sharkrail/service/ownership.py": 80.0,
}


def _write_report(path, percentages):
    path.write_text(
        json.dumps(
            {
                "files": {
                    name: {"summary": {"percent_covered": percentage}}
                    for name, percentage in percentages.items()
                }
            }
        ),
        encoding="utf-8",
    )


def test_critical_coverage_gate_accepts_each_module_at_its_floor(tmp_path):
    report = tmp_path / "coverage.json"
    _write_report(report, CRITICAL_FILES)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert all(name in result.stdout for name in CRITICAL_FILES)


def test_critical_coverage_gate_rejects_local_regression(tmp_path):
    report = tmp_path / "coverage.json"
    percentages = dict(CRITICAL_FILES)
    percentages["src/sharkrail/service/ownership.py"] = 79.99
    _write_report(report, percentages)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ownership.py: 79.99% is below 80.00%" in result.stderr
