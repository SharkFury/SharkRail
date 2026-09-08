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


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_optional(requirement: str) -> bool:
    """Return whether a wheel requirement is activated only by an extra."""

    _, separator, marker = requirement.partition(";")
    return bool(
        separator and re.search(r"\bextra\s*(?:==|!=|\bin\b|\bnot\s+in\b)", marker)
    )


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
    root_ref = f"pkg:pypi/{_canonical_name(name)}@{version}"
    requirements = sorted(set(metadata.get_all("Requires-Dist", []) or []))
    components = []
    dependency_refs = []
    runtime_requirements: dict[str, tuple[str, list[str]]] = {}
    for requirement in requirements:
        if _is_optional(requirement):
            continue
        dependency = _dependency_name(requirement)
        canonical = _canonical_name(dependency)
        _display_name, declarations = runtime_requirements.setdefault(
            canonical, (dependency, [])
        )
        declarations.append(requirement)

    for canonical, (dependency, declarations) in sorted(runtime_requirements.items()):
        reference = f"pkg:pypi/{canonical}"
        dependency_refs.append(reference)
        components.append(
            {
                "type": "library",
                "bom-ref": reference,
                "name": dependency,
                "purl": reference,
                "scope": "required",
                "properties": [
                    {
                        "name": "sharkrail:declared-requirements",
                        "value": json.dumps(declarations, separators=(",", ":")),
                    }
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
