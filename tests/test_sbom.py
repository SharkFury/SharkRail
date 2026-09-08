import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "generate_sbom.py"


def test_sbom_describes_wheel_and_declared_dependencies(tmp_path):
    wheel = tmp_path / "sharkrail-1.2.3-py3-none-any.whl"
    metadata = """Metadata-Version: 2.4
Name: sharkrail
Version: 1.2.3
License-Expression: MIT
Requires-Dist: pywinpty>=3; sys_platform == 'win32'
Requires-Dist: Demo_Package>=1; python_version < '3.10'
Requires-Dist: demo-package>=2; python_version >= '3.10'
Requires-Dist: pytest>=8; extra == 'test'

"""
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sharkrail-1.2.3.dist-info/METADATA", metadata)
    output = tmp_path / "sbom.cdx.json"

    first = subprocess.run(
        [sys.executable, str(SCRIPT), str(wheel), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    original = output.read_bytes()
    second = subprocess.run(
        [sys.executable, str(SCRIPT), str(wheel), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert first.returncode == second.returncode == 0
    assert output.read_bytes() == original
    assert payload["bomFormat"] == "CycloneDX"
    assert payload["specVersion"] == "1.6"
    assert payload["metadata"]["component"]["purl"] == "pkg:pypi/sharkrail@1.2.3"
    references = [component["bom-ref"] for component in payload["components"]]
    assert references == ["pkg:pypi/demo-package", "pkg:pypi/pywinpty"]
    assert len(references) == len(set(references))
    assert "pkg:pypi/pytest" not in references
    assert payload["dependencies"][0]["dependsOn"] == references
    demo = payload["components"][0]
    assert demo["scope"] == "required"
    assert len(demo["properties"]) == 1
    assert len(json.loads(demo["properties"][0]["value"])) == 2
