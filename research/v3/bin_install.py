#!/usr/bin/env python3
"""PREREG-V3 A8, T32 (PREREG-V3-AMENDMENTS.md A3; the auditor's Q-A8-10 and Q-SM-1..4 = O-a, 2026-09-30): the
supermemory-local server - the vendor's release binary supermemory-server-windows-x64.exe of the GitHub release
server-v0.0.8 - fetched in ONE declared window and placed in the polygon, never started here.

1. Before anything, offline: the expectation is the a7-docs record d1 (d4_report.json) the auditor fixed by its sha256
   (DOCS_REPORT_SHA256) - another file is refused; so is a record with problems, of another repository, tag or tag
   commit, a release that is a draft or a prerelease, or one that does not hold the binary and its .sha256 each exactly
   once with a sha256 digest. The binary's destination <polygon>/supermemory_v3/bin/<binary> must not hold a file (a
   binary is never overwritten), and the run label must be unused.
2. ONE window ``a8-supermemory-bin`` - hosts exactly api.github.com, github.com and the two GitHub CDN hosts
   (fetch_manifest.json) - running fetch_child's gh_release job with that expectation: the tag must still name the
   commit, the live release must agree with the record on each asset's size and digest, each file is fetched through
   at most one redirect to a declared CDN host and checked against its digest as it streams. Every host the job reached
   must have been tunnelled by the catcher; the job's assets must be the expectation.
3. After the window, offline: the binary re-read from the disk against the digest and the size; the .sha256 file is one
   line "<hex>  <name>" or "<hex> *<name>" naming the binary's own sha256 and its asset name; only then is the binary
   copied to its destination (through a .partial file) and re-read there. Q-SM-3: the install never starts the binary -
   its version and its start (the update check the vendor's quickstart mentions) are the A8 probe's, under the catcher.

The record: <runs>/_install/a8-supermemory-bin/<run>/bin_record.json.

    python research/v3/bin_install.py --run b1
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOW = "a8-supermemory-bin"
API, WEB = "api.github.com", "github.com"
CDN_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
REPO, TAG, VERSION = "supermemoryai/supermemory", "server-v0.0.8", "0.0.8"
COMMIT = "5d2b5855fe492a3682a1cde4a255e2db0c4db595"
BINARY = "supermemory-server-windows-x64.exe"
SUMS = BINARY + ".sha256"
DEST = "supermemory_v3"
#: The auditor's fixing of the window a7-docs d1 (2026-09-29 23:23): its report holds the release's asset list with
#: each asset's size and digest - the expectation, fixed before this window.
DOCS_WINDOW, DOCS_RUN, DOCS_REPORT = "a7-docs", "d1", "d4_report.json"
DOCS_REPORT_SHA256 = "492f92b77098b80eeca7494bb3550a3988aa596873850ca1d1f269bae6a9fcd8"
META_MAX = 16 * (1 << 20)
ASSET_MAX = 512 * (1 << 20)
DISK_FLOOR = 100 * (1 << 30)
_SUMS_LINE = re.compile(r"([0-9a-f]{64}) [ *]([^\s/\\]+)\n?")


class BinRefused(RuntimeError):
    """A record, a destination or a label this module's rules do not allow - named, before the window."""


def _sha256_file(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def expectation(c, *, docs_sha256: str = DOCS_REPORT_SHA256) -> tuple[list[dict], dict]:
    """(the two assets' {name, size, digest}, the record's {path, sha256}) from the a7-docs record - see step 1."""
    f = c.runs_root / "_fetch" / DOCS_WINDOW / DOCS_RUN / DOCS_REPORT
    if not f.is_file():
        raise BinRefused(f"no a7-docs record at {f} - the expectation comes first")
    raw = f.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != docs_sha256:
        raise BinRefused(f"{f.name} sha256 {got[:12]}... is not the fixed record {docs_sha256[:12]}...")
    rec = json.loads(raw)
    if rec.get("problems"):
        raise BinRefused(f"the a7-docs record has problems: {str(rec['problems'])[:200]}")
    if rec.get("repo") != REPO:
        raise BinRefused(f"the a7-docs record names the repository {rec.get('repo')!r}, not {REPO!r}")
    if rec.get("ref_name") != TAG:
        raise BinRefused(f"the a7-docs record names the tag {rec.get('ref_name')!r}, not {TAG!r}")
    if rec.get("commit") != COMMIT:
        raise BinRefused(f"the a7-docs record names commit {rec.get('commit')}, not the tag's {COMMIT}")
    rel = rec.get("release") or {}
    if rel.get("tag_name") != TAG:
        raise BinRefused(f"the record's release is {rel.get('tag_name')!r}, not {TAG!r}")
    if rel.get("draft") is not False or rel.get("prerelease") is not False:
        raise BinRefused(f"the record's release {TAG} is a draft or a prerelease")
    out = []
    for name in (BINARY, SUMS):
        found = [a for a in rel.get("assets") or [] if isinstance(a, dict) and a.get("name") == name]
        if len(found) != 1:
            raise BinRefused(f"the record's release holds {len(found)} assets named {name}, not one")
        a = found[0]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(a.get("digest"))):
            raise BinRefused(f"the record's {name} has no sha256 digest ({str(a.get('digest'))[:80]!r})")
        if not (isinstance(a.get("size"), int) and not isinstance(a.get("size"), bool) and 0 < a["size"] <= ASSET_MAX):
            raise BinRefused(f"the record's {name} has a size {a.get('size')!r} outside 1..{ASSET_MAX}")
        out.append({"name": name, "size": a["size"], "digest": a["digest"]})
    return out, {"path": str(f), "sha256": got}


def gh_job(assets: list[dict]) -> dict:
    """The window's fetch-child job (fetch_child.gh_release_job)."""
    return {"kind": "gh_release", "repo": REPO, "tag": TAG, "commit": COMMIT, "api_host": API, "web_host": WEB,
            "cdn_hosts": list(CDN_HOSTS), "hosts": [API, WEB, *CDN_HOSTS], "assets": [dict(a) for a in assets],
            "max_meta_bytes": META_MAX, "max_asset_bytes": ASSET_MAX}


def parse_sums(data: bytes) -> dict:
    """The .sha256 file's one line: {sha256, name}; anything else raises ValueError, named."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as e:
        raise ValueError("the .sha256 file is not one line (not ASCII)") from e
    m = _SUMS_LINE.fullmatch(text)
    if m is None:
        raise ValueError(f"the .sha256 file is not one line '<hex>  <name>' or '<hex> *<name>' ({text[:120]!r})")
    return {"sha256": m.group(1), "name": m.group(2)}


def _write(base: Path, record: dict) -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / "bin_record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def run_bin_install(c, L, F, *, run: str, via_port: int, parent_env, python: Path, native=None, fs=None,
                    child_env_extra: dict | None = None, need_bytes: int = DISK_FLOOR, volume: Path | None = None,
                    docs_sha256: str = DOCS_REPORT_SHA256) -> dict:
    """The whole install (see the module docstring). ``F`` is research/v3/fetch_a3; ``docs_sha256`` is the test's
    only: a real run keeps the auditor's fixing."""
    # 1. offline, before anything
    expect, docs = expectation(c, docs_sha256=docs_sha256)
    dest = c.polygon_root / DEST / "bin" / BINARY
    if dest.exists() or dest.with_name(dest.name + ".partial").exists():
        raise BinRefused(f"{dest.parent} already holds {BINARY} - a binary is never overwritten")
    base = c.runs_root / "_install" / WINDOW / run
    if base.exists():
        raise BinRefused("this install run label was used before")
    base.mkdir(parents=True)
    record: dict = {"window": WINDOW, "run": run, "repo": REPO, "tag": TAG, "commit": COMMIT, "expect": expect,
                    "docs_record": docs, "binary_started": False, "problems": []}
    # 2. the window: the gh_release job, through the catcher
    job = gh_job(expect)
    rec = F.run_child_window(c, L, window=WINDOW, hosts=list(job["hosts"]), jobs=[job], python=python, via_port=via_port,
                             run=run, parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=need_bytes, volume=volume)
    tunnelled = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["window_record"] = {**{k: rec[k] for k in ("problems", "check", "issuers")}, "tunnelled_hosts": tunnelled}
    record["problems"] += list(rec["problems"])
    summary = (rec["jobs"][0]["summary"] or [{}])[0] if rec.get("jobs") else {}
    record["job"] = summary
    past = sorted({r["host"] for r in summary.get("requests") or []} - set(tunnelled))
    if past:
        record["problems"].append(f"the gh_release job reached {past} with no catcher tunnel - past the catcher")
    if not summary.get("ok"):
        record["problems"].append(f"the gh_release job failed: {summary.get('error')}")
    elif [{k: a.get(k) for k in ("name", "size", "digest")} for a in summary.get("assets") or []] != expect:
        record["problems"].append("the job's assets are not the expectation")
    if record["problems"]:
        return _write(base, record)
    # 3. offline: the binary and its .sha256 from the disk, then the placing
    rdir = Path(rec["jobs"][0]["unit"]) / "release"
    got = {}
    for a in expect:
        f = rdir / a["name"]
        sha, n = _sha256_file(f) if f.is_file() else (None, 0)
        if f"sha256:{sha}" != a["digest"] or n != a["size"]:
            record["problems"].append(f"{a['name']} on the disk is not the expected sha256 and size ({n} bytes)")
        got[a["name"]] = sha
    if record["problems"]:
        return _write(base, record)
    try:
        line = parse_sums((rdir / SUMS).read_bytes())
    except ValueError as e:
        record["problems"].append(str(e))
        return _write(base, record)
    record["sums_line"] = line
    if line["sha256"] != got[BINARY]:
        record["problems"].append(f"the .sha256 file names sha256 {line['sha256']}, not the binary's {got[BINARY]}")
    if line["name"] != BINARY:
        record["problems"].append(f"the .sha256 file names the file {line['name']!r}, not {BINARY!r}")
    if record["problems"]:
        return _write(base, record)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".partial")
    with open(rdir / BINARY, "rb") as src, open(part, "xb") as out:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            out.write(chunk)
    os.replace(part, dest)
    placed, n = _sha256_file(dest)
    if placed != got[BINARY] or n != expect[0]["size"]:
        record["problems"].append(f"the placed binary is not the fetched one ({n} bytes)")
    record.update(version=VERSION, sha256=got[BINARY], bytes=expect[0]["size"], path=str(dest), placed_sha256=placed,
                  cdn_hosts=[a.get("cdn_host") for a in summary.get("assets") or []])
    return _write(base, record)


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8 T32: the supermemory-local server binary, into the polygon")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    c = L.Contract.default()
    port = L.network_via_port(c)
    if port is None:
        print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
        return 2
    try:
        rec = run_bin_install(c, L, F, run=args.run, via_port=port, parent_env=dict(os.environ),
                              python=c.polygon_root / "py314" / "python.exe")
    except BinRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: rec.get(k) for k in ("version", "sha256", "path", "cdn_hosts", "problems")}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
