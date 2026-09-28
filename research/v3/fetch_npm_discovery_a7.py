#!/usr/bin/env python3
"""PREREG-V3 (the auditor's GO after window a7-npm g1 met a 404): window a7-npm-d - discovery, METADATA ONLY, on the npm
registry: does the package rev1 names (supermemory-server) exist, with which versions and dist-tags, and which
packages a registry search for "supermemory" returns. Every fetch is a contract child through the catcher and the
declared hop (research/v3/fetch_a3.run_child_window: the trap and the witnesses).

* the window - its one host, the package and the search - is fetch_manifest.json's "a7-npm-d" entry, the single
  source; a manifest that does not declare it as one host with no redirect refuses;
* exactly two GETs, each a job of its own (a 404 on the first - no such package - is a finding, and the search still
  runs): https://registry.npmjs.org/<package> (the full document: the fetch child sends no Accept header, so the
  abbreviated install-v1 form is not asked for) and https://registry.npmjs.org/-/v1/search?text=<text>&size=<n>;
* nothing else: no link in either document is followed or fetched - they are data;
* after the window: a discovery record (runs/v3/_fetch/a7-npm-d/<run>/discovery.json) - whether the package exists,
  its dist-tags and versions, and each search hit's name, version, description and repository link as text; nothing
  is placed as a pin. The auditor decides from it (a rev1 erratum and a download window, or blocked:unavailable).

    python research/v3/fetch_npm_discovery_a7.py --run d1 --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "fetch_manifest.json"
WINDOW = "a7-npm-d"
WINDOW_KEYS = {"hosts", "purpose", "package", "search_text", "search_size", "max_redirects"}
DOC_MAX = 16 << 20
SEARCH_MAX = 1 << 20
JOB_TIMEOUT_S = 900
_NAME = re.compile(r"(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*")


class ManifestError(ValueError):
    """The manifest does not declare this window as a one-host, no-redirect, metadata-only window."""


def window_decl(manifest: dict) -> dict:
    w = (manifest.get("windows") or {}).get(WINDOW)
    if not isinstance(w, dict):
        raise ManifestError(f"the manifest declares no window {WINDOW}")
    if set(w) != WINDOW_KEYS:
        raise ManifestError(f"the {WINDOW} window has the keys {sorted(w)}, not {sorted(WINDOW_KEYS)}")
    if not (isinstance(w["hosts"], list) and len(w["hosts"]) == 1 and isinstance(w["hosts"][0], str)):
        raise ManifestError(f"the {WINDOW} window has exactly one host, not {w['hosts']!r}")
    if w["max_redirects"] != 0:
        raise ManifestError(f"the {WINDOW} window follows no redirect")
    if not (isinstance(w["package"], str) and _NAME.fullmatch(w["package"])):
        raise ManifestError(f"the {WINDOW} window names a valid npm package, not {w['package']!r}")
    if not (isinstance(w["search_text"], str) and w["search_text"] and isinstance(w["search_size"], int)
            and 1 <= w["search_size"] <= 250):
        raise ManifestError(f"the {WINDOW} window names its search text and a size in 1..250")
    return w


_DECL = window_decl(json.loads(MANIFEST.read_text(encoding="utf-8")))
HOST = _DECL["hosts"][0]
PACKAGE = _DECL["package"]
DOC_URL = f"https://{HOST}/{urllib.parse.quote(PACKAGE, safe='@')}"
SEARCH_URL = f"https://{HOST}/-/v1/search?" + urllib.parse.urlencode({"text": _DECL["search_text"],
                                                                        "size": _DECL["search_size"]})
DOC_SAVE = "npm/package_document.json"
SEARCH_SAVE = "npm/search.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def jobs() -> list[dict]:
    return [{"hosts": [HOST], "max_redirects": 0, "timeout_s": JOB_TIMEOUT_S,
             "requests": [{"id": "npm:package-document", "method": "GET", "url": DOC_URL, "save": DOC_SAVE,
                           "max_bytes": DOC_MAX, "expect": None}]},
            {"hosts": [HOST], "max_redirects": 0, "timeout_s": JOB_TIMEOUT_S,
             "requests": [{"id": "npm:search", "method": "GET", "url": SEARCH_URL, "save": SEARCH_SAVE,
                           "max_bytes": SEARCH_MAX, "expect": None}]}]


def _text(v, limit: int = 300) -> str | None:
    return v[:limit] if isinstance(v, str) else None


def summarise_document(raw: bytes) -> dict:
    """The package document's facts, as data: its name, dist-tags and version list; nothing is followed."""
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("the package document is not a JSON object")
    versions = doc.get("versions") if isinstance(doc.get("versions"), dict) else {}
    tags = doc.get("dist-tags") if isinstance(doc.get("dist-tags"), dict) else {}
    return {"name": _text(doc.get("name")), "dist_tags": {k: _text(v, 64) for k, v in sorted(tags.items())},
            "versions": sorted(versions), "repository": _text((doc.get("repository") or {}).get("url"))
            if isinstance(doc.get("repository"), dict) else _text(doc.get("repository"))}


def summarise_search(raw: bytes) -> list[dict]:
    doc = json.loads(raw.decode("utf-8"))
    objs = doc.get("objects") if isinstance(doc, dict) and isinstance(doc.get("objects"), list) else None
    if objs is None:
        raise ValueError("the search answer has no objects list")
    out = []
    for o in objs:
        pkg = (o or {}).get("package") if isinstance(o, dict) else None
        if not isinstance(pkg, dict):
            continue
        links = pkg.get("links") if isinstance(pkg.get("links"), dict) else {}
        out.append({"name": _text(pkg.get("name"), 214), "version": _text(pkg.get("version"), 64),
                    "description": _text(pkg.get("description")), "repository": _text(links.get("repository")),
                    "npm": _text(links.get("npm"))})
    return out


def _result(record: dict, rid: str) -> tuple[Path | None, dict]:
    for j in record.get("jobs") or []:
        for r in j.get("summary") or []:
            if r.get("id") == rid:
                return Path(j["unit"]), r
    return None, {}


def discover(record: dict) -> dict:
    """The discovery record from a closed window: the package's existence and facts, the search hits; every other
    request the window made is a named problem (only the two GETs are allowed)."""
    out: dict = {"window": WINDOW, "run": record.get("run"), "package": PACKAGE, "problems": []}
    ids = [r.get("id") for j in record.get("jobs") or [] for r in j.get("summary") or []]
    if sorted(ids) != ["npm:package-document", "npm:search"]:
        out["problems"].append(f"the window made the requests {ids}, not exactly the two declared GETs")
    unit, r = _result(record, "npm:package-document")
    if r.get("status") == 404:
        out["package_exists"] = False
        out["package_status"] = 404
    elif r.get("ok") and unit is not None:
        raw = (unit / DOC_SAVE).read_bytes()
        out["package_exists"] = True
        out["package_document_sha256"] = hashlib.sha256(raw).hexdigest()
        try:
            out["package_document"] = summarise_document(raw)
        except (ValueError, UnicodeDecodeError) as e:
            out["problems"].append(f"the package document does not read: {type(e).__name__}: {e}")
    else:
        out["package_exists"] = None
        out["problems"].append(f"the package document: {r.get('error') or 'not fetched'}")
    unit, r = _result(record, "npm:search")
    if r.get("ok") and unit is not None:
        raw = (unit / SEARCH_SAVE).read_bytes()
        out["search_sha256"] = hashlib.sha256(raw).hexdigest()
        try:
            out["search"] = summarise_search(raw)
            total = json.loads(raw.decode("utf-8")).get("total")
            out["search_total"] = total if isinstance(total, int) and not isinstance(total, bool) else None
            out["search_page_full"] = len(out["search"]) >= _DECL["search_size"]   # B-NPMP: more may be past it
        except (ValueError, UnicodeDecodeError) as e:
            out["problems"].append(f"the search answer does not read: {type(e).__name__}: {e}")
    else:
        out["problems"].append(f"the search: {r.get('error') or 'not fetched'}")
    return out


def window_problems(rec: dict) -> list[str]:
    """The window's own problems, except the package document's 404 - that is the finding, not a fault."""
    return [p for p in rec.get("problems") or []
            if not (p.startswith("job 0") and ("status 404" in p or "exited with 3" in p))]


def run_discovery(c, L, F, *, run: str, python: Path, via_port: int, parent_env, native=None, fs=None,
                  child_env_extra: dict | None = None, volume: Path | None = None, need_bytes: int | None = None) -> dict:
    """The window, then its discovery record (see the module docstring). ``need_bytes``: the free-space floor - main()
    passes the manifest's (B-NPMFLOOR); by default 3x the two documents' caps."""
    rec = F.run_child_window(c, L, window=WINDOW, hosts=[HOST], jobs=jobs(), python=python, via_port=via_port, run=run,
                             parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=need_bytes if need_bytes is not None else 3 * (DOC_MAX + SEARCH_MAX),
                             volume=volume)
    out = discover(rec)
    out["window_problems"] = window_problems(rec)
    if sorted(rec.get("hosts") or []) != [HOST]:
        out["problems"].append(f"the window ran on {rec.get('hosts')}, not [{HOST!r}]")
    tunnelled = sorted({x.get("host") for x in rec.get("catcher") or []})
    if tunnelled not in ([], [HOST]):
        out["problems"].append(f"the catcher tunnelled to {tunnelled}")
    path = c.runs_root / "_fetch" / WINDOW / run / "discovery.json"
    path.write_bytes((json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    out["discovery_path"] = str(path)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="window a7-npm-d: npm metadata discovery (two GETs, nothing followed)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--python", required=True, help="the polygon's py314 interpreter")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    c = L.Contract.default()
    via = L.network_via_port(c)
    if via is None:
        print("no declared hop (network.json)", file=sys.stderr)
        return 2
    disk = json.loads(MANIFEST.read_text(encoding="utf-8"))["disk"]          # B-NPMFLOOR: every window's Q-A3-2 floor
    res = run_discovery(c, L, F, run=args.run, python=Path(args.python), via_port=via, parent_env=os.environ,
                        volume=Path("D:/"), need_bytes=max(int(disk["floor_gb"]) << 30, 3 * (DOC_MAX + SEARCH_MAX)))
    print(json.dumps(res, indent=1, default=str))       # B-NPMOUT: ASCII - a piped cp1251 stdout cannot fail on a hit
    return 0 if not (res["problems"] or res["window_problems"]) else 1


if __name__ == "__main__":
    sys.exit(main())
