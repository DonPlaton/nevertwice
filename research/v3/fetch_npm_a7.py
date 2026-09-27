#!/usr/bin/env python3
"""PREREG-V3 (the auditor's Q-47-8g GO, 2026-09-27): window a7-npm - the supermemory-server@0.0.8 tarball from the npm
registry, DOWNLOAD ONLY: no npm, no install, no script run. Every fetch is a contract child through the catcher and
the declared hop (research/v3/fetch_a3.run_child_window: the trap and the witnesses); this module plans the two jobs
and, after the window closes, verifies and places.

* the window - its one host, the package and the version - is fetch_manifest.json's "a7-npm" entry, the single source
  the freeze and the auditor read too; a manifest that does not declare it as one host with no redirect refuses;
* job 1 fetches the version's registry document (https://registry.npmjs.org/supermemory-server/0.0.8);
* job 2 is built from it only when it names exactly this package and version, its dist.tarball is the canonical URL
  (https://registry.npmjs.org/supermemory-server/-/supermemory-server-0.0.8.tgz - a URL the document supplies is
  compared, never followed), its dist.integrity is a sha512 SRI and its dist.shasum 40 hex; otherwise no tarball is
  requested and the reason is a named problem;
* after the window: the window must be clean, on exactly its host, the host's certificate issuer recorded and in the
  public set (fetch_pins_a3.precheck's rule); the tarball's sha512 must equal the registry's integrity and its sha1
  the registry's shasum; its sha256 and size are recorded; the tarball and the registry document go to
  <runs>/_pins/npm/, each copy re-checked (the tarball's digests, the document's sha256) and both removed if either
  differs, with npm_pin.json naming every digest and the window record's sha256. The install scripts the
  document declares are recorded as data for A8 - nothing here runs them.

    python research/v3/fetch_npm_a7.py --run g1 --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "fetch_manifest.json"
WINDOW = "a7-npm"
WINDOW_KEYS = {"hosts", "purpose", "package", "version", "max_redirects"}


class ManifestError(ValueError):
    """The manifest does not declare this window as a one-host, no-redirect, download-only window."""


def window_decl(manifest: dict) -> dict:
    """The window as fetch_manifest.json declares it - the one source of its host, package and version (the
    auditor's gate on 6e92453): exactly one host, no redirect, a package and a version, nothing else."""
    w = (manifest.get("windows") or {}).get(WINDOW)
    if not isinstance(w, dict):
        raise ManifestError(f"the manifest declares no window {WINDOW}")
    if set(w) != WINDOW_KEYS:
        raise ManifestError(f"the {WINDOW} window has the keys {sorted(w)}, not {sorted(WINDOW_KEYS)}")
    if not (isinstance(w["hosts"], list) and len(w["hosts"]) == 1 and isinstance(w["hosts"][0], str)):
        raise ManifestError(f"the {WINDOW} window has exactly one host, not {w['hosts']!r}")
    if w["max_redirects"] != 0:
        raise ManifestError(f"the {WINDOW} window follows no redirect")
    if not all(isinstance(w[k], str) and w[k] for k in ("package", "version")):
        raise ManifestError(f"the {WINDOW} window names its package and version")
    return w


_DECL = window_decl(json.loads(MANIFEST.read_text(encoding="utf-8")))
HOST = _DECL["hosts"][0]
PACKAGE, VERSION = _DECL["package"], _DECL["version"]
MAX_REDIRECTS = _DECL["max_redirects"]
DOC_URL = f"https://{HOST}/{PACKAGE}/{VERSION}"
TARBALL_URL = f"https://{HOST}/{PACKAGE}/-/{PACKAGE}-{VERSION}.tgz"
DOC_SAVE = f"npm/{PACKAGE}-{VERSION}.json"
TARBALL_SAVE = f"npm/{PACKAGE}-{VERSION}.tgz"
DOC_MAX = 1 << 20
TARBALL_MAX = 64 << 20
NEED_BYTES = 3 * (DOC_MAX + TARBALL_MAX)
JOB_TIMEOUT_S = 1800
_SRI = re.compile(r"sha512-([A-Za-z0-9+/]{86}==)")
_HEX40 = re.compile(r"[0-9a-f]{40}")


class Stop(Exception):
    """A named reason the tarball is not requested or not placed."""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def doc_job() -> dict:
    return {"hosts": [HOST], "max_redirects": MAX_REDIRECTS, "timeout_s": JOB_TIMEOUT_S,
            "requests": [{"id": "npm:document", "method": "GET", "url": DOC_URL, "save": DOC_SAVE,
                          "max_bytes": DOC_MAX, "expect": None}]}


def read_document(raw: bytes) -> dict:
    """The registry document's facts this window relies on, or Stop naming what is wrong."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Stop("N1 document_not_json") from None
    if not isinstance(doc, dict) or doc.get("name") != PACKAGE or doc.get("version") != VERSION:
        raise Stop(f"N2 document_not_this_version: name {doc.get('name') if isinstance(doc, dict) else None!r}, "
                   f"version {doc.get('version') if isinstance(doc, dict) else None!r}")
    dist = doc.get("dist") if isinstance(doc.get("dist"), dict) else {}
    if dist.get("tarball") != TARBALL_URL:
        raise Stop(f"N3 tarball_url_not_canonical: {dist.get('tarball')!r}")
    integrity = dist.get("integrity")
    if not (isinstance(integrity, str) and _SRI.fullmatch(integrity)
            and len(base64.b64decode(integrity[7:])) == 64):
        raise Stop(f"N4 integrity_not_sha512: {integrity!r}")
    shasum = dist.get("shasum")
    if not (isinstance(shasum, str) and _HEX40.fullmatch(shasum)):
        raise Stop(f"N5 shasum_not_sha1: {shasum!r}")
    scripts = doc.get("scripts") if isinstance(doc.get("scripts"), dict) else {}
    return {"integrity": integrity, "shasum": shasum, "tarball": TARBALL_URL,
            "install_scripts": {k: scripts[k] for k in sorted(scripts) if k in ("preinstall", "install",
                                                                                   "postinstall", "prepare")},
            "dependencies": doc.get("dependencies") or {}, "engines": doc.get("engines") or {},
            "declared_file_count": dist.get("fileCount"), "declared_unpacked_size": dist.get("unpackedSize")}


def _saved(results: list, rid: str) -> tuple[Path | None, dict]:
    for j in results:
        for r in j.get("summary") or []:
            if r.get("id") == rid:
                return Path(j["unit"]), r
    return None, {}


def tarball_job(results: list, reasons: list):
    """Job 2, from job 1's saved document; None (and a named reason) when the document does not qualify."""
    unit, summary = _saved(results, "npm:document")
    if unit is None or not summary.get("ok"):
        reasons.append("N0 document_not_fetched")
        return None
    try:
        read_document((unit / DOC_SAVE).read_bytes())
    except (Stop, OSError) as e:
        reasons.append(str(e))
        return None
    return {"hosts": [HOST], "max_redirects": MAX_REDIRECTS, "timeout_s": JOB_TIMEOUT_S,
            "requests": [{"id": "npm:tarball", "method": "GET", "url": TARBALL_URL, "save": TARBALL_SAVE,
                          "max_bytes": TARBALL_MAX, "expect": None}]}


def digests(path: Path) -> dict:
    h512, h1, h256 = hashlib.sha512(), hashlib.sha1(), hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h512.update(block)
            h1.update(block)
            h256.update(block)
            size += len(block)
    return {"integrity": "sha512-" + base64.b64encode(h512.digest()).decode("ascii"), "shasum": h1.hexdigest(),
            "sha256": h256.hexdigest(), "size": size}


def place(record: dict, record_path: Path, pins_root: Path, *, precheck_problems: list) -> tuple[dict | None, list]:
    """Verify on disk against the registry's own digests and place; (pin, problems) - the pin only when clean."""
    problems = list(precheck_problems)
    unit_d, sum_d = _saved(record.get("jobs") or [], "npm:document")
    unit_t, sum_t = _saved(record.get("jobs") or [], "npm:tarball")
    if unit_d is None or unit_t is None or not (sum_d.get("ok") and sum_t.get("ok")):
        return None, problems + ["N6 tarball_not_fetched"]
    try:
        doc_raw = (unit_d / DOC_SAVE).read_bytes()
        facts = read_document(doc_raw)
    except (Stop, OSError) as e:
        return None, problems + [str(e)]
    src = unit_t / TARBALL_SAVE
    got = digests(src)
    for k in ("integrity", "shasum"):
        if got[k] != facts[k]:
            problems.append(f"N7 {k}_mismatch: the tarball's {k} is not the registry's")
    if got["sha256"] != sum_t.get("sha256") or got["size"] != sum_t.get("bytes"):
        problems.append("N8 child_claim_mismatch: the file on disk is not the one the child hashed")
    if problems:
        return None, problems
    dest = pins_root / "npm"
    dest.mkdir(parents=True, exist_ok=True)
    tgz, docf = dest / f"{PACKAGE}-{VERSION}.tgz", dest / f"{PACKAGE}-{VERSION}.json"
    for target in (tgz, docf):
        if target.exists():
            return None, [f"N9 already_placed: {target}"]
    doc_sha256 = hashlib.sha256(doc_raw).hexdigest()
    try:
        shutil.copyfile(src, tgz)
        shutil.copyfile(unit_d / DOC_SAVE, docf)
        bad = [n for n, ok in ((tgz.name, digests(tgz) == got),
                               (docf.name, hashlib.sha256(docf.read_bytes()).hexdigest() == doc_sha256)) if not ok]
    except OSError as e:
        bad = [f"{type(e).__name__}: {e}"]
    if bad:
        for target in (tgz, docf):                      # never half a placement: the next run would meet N9
            target.unlink(missing_ok=True)
        return None, [f"N10 placed_copy_differs: {bad}"]
    pin = {"package": PACKAGE, "version": VERSION, "registry": HOST, "tarball_url": TARBALL_URL,
           "integrity": got["integrity"], "shasum": got["shasum"], "sha256": got["sha256"], "size": got["size"],
           "document_sha256": doc_sha256, "install_scripts": facts["install_scripts"],
           "dependencies": facts["dependencies"], "engines": facts["engines"],
           "declared_file_count": facts["declared_file_count"],
           "declared_unpacked_size": facts["declared_unpacked_size"],
           "issuer": [sum_t.get("issuer_o"), sum_t.get("issuer_cn")],
           "window": {"name": WINDOW, "run": record.get("run"), "record": str(record_path),
                      "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest()},
           "tarball_path": str(tgz), "document_path": str(docf)}
    tmp = dest / "npm_pin.json.tmp"
    tmp.write_bytes((json.dumps(pin, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    os.replace(tmp, dest / "npm_pin.json")
    return pin, []


def window_problems(rec: dict, P, orgs) -> list[str]:
    """P1-P3 over a window record (fetch_pins_a3.precheck's rule): clean, on exactly the manifest's host, every host's
    certificate issuer recorded and its organisation in the public set."""
    pre = [f"P1 window_not_clean: {p}" for p in rec.get("problems") or []]
    if sorted(rec.get("hosts") or []) != [HOST]:
        pre.append(f"P2 hosts_not_manifest: {rec.get('hosts')} is not [{HOST!r}]")
    for host, pairs in P.host_issuers(rec).items():
        for org, cn in pairs:
            if org is None:
                pre.append(f"P3 issuer_unrecorded: {host} (O None, CN {cn!r})")
            elif org not in orgs:
                pre.append(f"P3 issuer_not_public: {host} presented O {org!r}, CN {cn!r}")
    return pre


def run_npm_window(c, L, F, P, *, run: str, python: Path, via_port: int, parent_env, native=None, fs=None,
                   child_env_extra: dict | None = None, volume: Path | None = None,
                   issuer_orgs=None) -> dict:
    """The window, then the placement. Nothing raises past here but a refusal of the window itself."""
    reasons: list[str] = []
    rec = F.run_child_window(c, L, window=WINDOW, hosts=[HOST], jobs=[doc_job(), lambda res: tarball_job(res, reasons)],
                             python=python, via_port=via_port, run=run, parent_env=parent_env, native=native, fs=fs,
                             child_env_extra=child_env_extra, need_bytes=NEED_BYTES, volume=volume)
    record_path = c.runs_root / "_fetch" / WINDOW / run / "record.json"
    pre = window_problems(rec, P, issuer_orgs if issuer_orgs is not None else P.PUBLIC_ISSUER_ORGS)
    pin, problems = place(rec, record_path, c.runs_root / "_pins", precheck_problems=pre + reasons)
    return {"window": WINDOW, "run": run, "problems": problems, "pin": pin,
            "window_problems": list(rec.get("problems") or [])}


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="window a7-npm: the supermemory-server@0.0.8 tarball, download only")
    ap.add_argument("--run", required=True)
    ap.add_argument("--python", required=True, help="the polygon's py314 interpreter")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    P = _load("v3_fetch_pins_a3", HERE / "fetch_pins_a3.py")
    c = L.Contract.default()
    via = L.network_via_port(c)
    if via is None:
        print("no declared hop (network.json)", file=sys.stderr)
        return 2
    res = run_npm_window(c, L, F, P, run=args.run, python=Path(args.python), via_port=via, parent_env=os.environ,
                         volume=Path("D:/"))
    print(json.dumps(res, indent=1, default=str))
    return 0 if not res["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
