"""Generate a deterministic CycloneDX SBOM for a built SharkRail wheel."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
import zipfile
from email.parser import Parser
from pathlib import Path


def _dependency_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    if match is None:
        raise ValueError(f"invalid Requires-Dist value: {requirement}")
    return match.group(0)


def generate(wheel: Path) -> dict[str, object]:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError("wheel must contain exactly one METADATA file")
        metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode("utf-8"))

    name = metadata["Name"]
    version = metadata["Version"]
    if not name or not version:
        raise ValueError("wheel metadata must contain Name and Version")
    root_ref = f"pkg:pypi/{name.lower()}@{version}"
    requirements = sorted(set(metadata.get_all("Requires-Dist", [])))
    components = []
    dependency_refs = []
    for requirement in requirements:
        dependency = _dependency_name(requirement)
        reference = f"pkg:pypi/{dependency.lower()}"
        dependency_refs.append(reference)
        components.append(
            {
                "type": "library",
                "bom-ref": reference,
                "name": dependency,
                "purl": reference,
                "properties": [
                    {"name": "sharkrail:declared-requirement", "value": requirement}
                ],
            }
        )
    return {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, digest)}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "library",
                "bom-ref": root_ref,
                "name": name,
                "version": version,
                "purl": root_ref,
                "licenses": [{"license": {"id": "MIT"}}],
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        },
        "components": components,
        "dependencies": [{"ref": root_ref, "dependsOn": dependency_refs}],
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate_sbom.py WHEEL OUTPUT")
    wheel = Path(sys.argv[1])
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(generate(wheel), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
