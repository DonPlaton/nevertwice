#!/usr/bin/env python3
"""Emit a CycloneDX 1.5 SBOM for the built distribution.

Most projects reach for an SBOM action here. This one ships **zero runtime
dependencies**, so the whole bill of materials is the package itself plus whatever an
optional extra would pull in - which is small enough to read, and small enough that
generating it from the built wheel's own metadata is more trustworthy than trusting a
scanner to agree with `pyproject.toml`.

The components are read out of the wheel's METADATA, so the SBOM describes the artifact
that will actually be uploaded, not the source tree it was built from.

Usage:

    python tools/make_sbom.py --dist dist --out dist/sbom.cdx.json

Standard library only. Deterministic: given the same wheel it emits the same bytes,
apart from the serial number and timestamp, which are supplied by the caller so that a
reproducibility check can pin them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_VERSION = "1.5"


def wheel_metadata(wheel: Path) -> dict[str, list[str]]:
    with zipfile.ZipFile(wheel) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        text = z.read(name).decode("utf-8", "replace")
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        if line[0].isspace():
            continue
        key, sep, value = line.partition(": ")
        if sep:
            fields.setdefault(key, []).append(value.strip())
    return fields


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requirement_name(spec: str) -> tuple[str, str]:
    """('torch>=2 ; extra == "reranker"',) -> ('torch', 'reranker')."""
    requirement, _, marker = spec.partition(";")
    name = re.split(r"[<>=!~\[ ]", requirement.strip(), maxsplit=1)[0]
    extra = ""
    m = re.search(r'extra\s*==\s*[\'"]([^\'"]+)[\'"]', marker)
    if m:
        extra = m.group(1)
    return name, extra


def build_sbom(dist: Path, serial: str, timestamp: str) -> dict:
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise SystemExit("no wheel in the dist directory - run `python -m build` first")
    wheel = wheels[0]
    fields = wheel_metadata(wheel)
    name = fields["Name"][0]
    version = fields["Version"][0]
    licence = (fields.get("License-Expression") or ["NOASSERTION"])[0]

    artifacts = sorted(p for p in dist.iterdir()
                       if p.suffix == ".whl" or p.name.endswith(".tar.gz"))

    components = []
    for spec in fields.get("Requires-Dist", []):
        dep, extra = _requirement_name(spec)
        components.append({
            "type": "library",
            "name": dep,
            "bom-ref": f"pkg:pypi/{dep}",
            "purl": f"pkg:pypi/{dep}",
            "scope": "optional",
            "description": (f"optional dependency, only installed with the "
                            f"`{extra}` extra" if extra else "dependency"),
            "properties": [{"name": "nevertwice:requirement", "value": spec}],
        })

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [{"vendor": "nevertwice", "name": "tools/make_sbom.py",
                       "version": version}],
            "component": {
                "type": "application",
                "name": name,
                "version": version,
                "bom-ref": f"pkg:pypi/{name}@{version}",
                "purl": f"pkg:pypi/{name}@{version}",
                "licenses": [{"expression": licence}],
                "description": (fields.get("Summary") or [""])[0],
                "hashes": [{"alg": "SHA-256", "content": sha256(p)}
                           for p in artifacts],
                "properties": [{"name": "nevertwice:artifact", "value": p.name}
                               for p in artifacts],
            },
        },
        # Zero runtime dependencies is the claim the README makes; an SBOM that lists
        # only optional extras is what backs it.
        "components": components,
        "dependencies": [{
            "ref": f"pkg:pypi/{name}@{version}",
            "dependsOn": [],
        }],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", type=Path, default=ROOT / "dist",
                    help="directory holding the built sdist and wheel")
    ap.add_argument("--out", type=Path, help="where to write the SBOM")
    ap.add_argument("--serial", default="urn:uuid:00000000-0000-0000-0000-000000000000",
                    help="SBOM serial number (pass a real UUID in a release run)")
    ap.add_argument("--timestamp", default="1970-01-01T00:00:00Z",
                    help="ISO-8601 build timestamp")
    args = ap.parse_args(argv)

    sbom = build_sbom(args.dist, args.serial, args.timestamp)
    text = json.dumps(sbom, indent=2, sort_keys=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        component = sbom["metadata"]["component"]
        print(f"wrote {args.out} - {component['name']} {component['version']}, "
              f"{len(sbom['components'])} optional component(s), "
              f"{len(component['hashes'])} artifact hash(es)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
