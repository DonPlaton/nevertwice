#!/usr/bin/env python3
"""PREREG-V3 plan step A3.k: research/v3/freeze_a3.json, the A3 fragment of FREEZE-V3 (TB6b's writer reads it).

Built only from the records the auditor cleared, each read from the runs tree and bound by its sha256 - a record that
is missing or whose bytes moved stops the build (FreezeRefused), nothing is written. It holds:
* windows: every cleared run - its window, run, kind, record files by sha256 and each record's problem count; a
  cleared record WITH problems stops the build unless its entry carries the auditor's ruling note (a3-discovery d1);
* failed_runs: every run that was NOT cleared, by record sha256 and a one-line reason, so the audit trail keeps what
  did not pass (the auditor, A3.k);
* issuers: per host, the TLS issuers the windows recorded (a fetch record's "issuers", a tool record's "peer") - every
  organisation public (R-A3-7) or the build stops; every host a cleared record contacted has a recorded issuer or a
  declared reason in issuers_unrecorded (pip's own hosts: its vendored certifi verified them, no child recorded them).
  A record with a catcher contacted the hosts its catcher tunnelled (lines with tunnelled True and refused False - the
  auditor's Q-DH-1 = O-a); a host it declared and never tunnelled made no TLS, needs no issuer and is named in its
  window's declared_not_reached (written only when there is one); a catcher line that is neither a tunnel nor a refusal
  stops the build. A record without a catcher (a tool record) contacted its request URLs' hosts;
* pins: the v3 pin table as filled (research/v3/corpus_pin_v3.py) - every non-alias pin filled or the build stops;
* binaries: every cleared binary run (kind "binary", the auditor's Q-BIN-1 = O-a: FREEZE-V3 3a pins the competitors'
  versions explicitly) by its window - tag, commit, the asset its checksum line names, sha256, size, digest, the docs
  record it was read from (its sha256 and its path) and its window record, each by sha256; the build stops by name
  when the bin_record or the
  window record is not among the entry's cleared files, the binary ran (binary_started not False), the record has
  problems, no release asset is the checksum line's binary, the sha256 is not that asset's digest or the size its
  size, or the docs record is not a cleared file; no binary run, no section;
* models: the A3.h inventory's pinned models (digest and the 12-hex pin) and the Ollama version;
* d1_tag: the D1 tag's digest, its base and its Modelfile sha256 (A3.i) - every embedding stand checks the tag at this
  digest before its first embed;
* local_v2: the v2 pins' polygon places (O2);
* venvs: py314 (the NuGet python 3.14.4) and v3_data (pyarrow);
* facts: the dataset-facts record the auditor cleared as the source of rev2's slot (A3.j j3);
* prereg_rev1: the sha256 of research/v3/PREREG-V3-rev1.md.
The output is sorted JSON with LF line ends; the same records give the same bytes.

    python research/v3/freeze_a3.py --out research/v3/freeze_a3.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

#: The cleared runs (the auditor's verdicts in .loop/STATE-C.md), each record file by its sha256 under <runs_root>.
CLEARED = [
    {"window": "py-base-314", "run": "b1", "kind": "tool", "files": {
        "_tools/py-base-314/py-base-314.json": "7b1f8db8ad9d927de1356222a2f8b54bebbf7855662ab28809d66a53b3abb1e1"}},
    {"window": "a3-discovery", "run": "d1", "kind": "discovery", "files": {
        "_fetch/a3-discovery/d1/record.json": "db131b4983a91629ef3f8906e5b1f6659a7c3f17b4713a2b84e4b9afdff18210"},
     "note": "3 problems, all in job 2 (the AMA-Hub 301 on its gh: and ghhead: requests, and the job's exit), ruled P1: "
             "AMA-Hub re-read as d2 on api.github.com only; d1's other 44 job-2 requests and jobs 0, 1, 3 feed the pins "
             "(fetch_pins_a3 pins d1 and d2 by sha256)",
     "problems_verbatim": ["job 2: the fetch child exited with 3",
                           "job 2 request gh:AMA-Bench/AMA-Hub: Refused: more redirects than the job allows",
                           "job 2 request ghhead:AMA-Bench/AMA-Hub: Refused: more redirects than the job allows"],
     "excluded": [{"job": 2, "requests": ["gh:AMA-Bench/AMA-Hub", "ghhead:AMA-Bench/AMA-Hub"]}],
     "feeds_pins": {"0": "HF revisions and card licences", "1": "HF trees",
                    "2": "GitHub repo licences through gh:<repo> (never AMA-Hub)", "3": "GitHub trees"}},
    {"window": "a3-discovery", "run": "d2", "kind": "discovery", "files": {
        "_fetch/a3-discovery/d2/record.json": "f793c59a6ec980a94255a8abe0530a3c72d3c782d213df30375adabbd8a360d4"}},
    {"window": "a3-pyarrow", "run": "i1", "kind": "install", "files": {
        "_fetch/a3-pyarrow/i1/record.json": "56c2d09a823cdd6dfb97192b5c1a5171031caf4f5578b63a61d50453e190efb6",
        "_install/a3-pyarrow/i1/install_record.json": "fe9069008b16e200264d7f130700563bbdeb6baf8528011e05a37bbe52b273b7"}},
    {"window": "a3-hf", "run": "h2", "kind": "fetch", "files": {
        "_fetch/a3-hf/h2/record.json": "6e81496dead1c8e88f00870658c7da7af8bdd1f72de1991e08653e807575e2b3",
        "_fetch/a3-hf/h2/place_record.json": "49cba9b3bfabd5923f3709d7cc2096ab8794ac6e27ad1632ce6a0bd5862449c8",
        "_fetch/a3-hf/h2/pin_fill.json": "c23ea9cd354963e63305ccc280a553ac1870a4fdf9942575540dec9ba587a3eb"}},
    {"window": "a3-github", "run": "g1", "kind": "fetch", "files": {
        "_fetch/a3-github/g1/record.json": "55217f18d7090a22207991eefe396ce4bc136ada1e8ab2ed4175874fba33014a",
        "_fetch/a3-github/g1/place_record.json": "b639cc1e1b6aaf9c311a82686c629700d9d6bac5b2885233ca6ae449d580bd9b",
        "_fetch/a3-github/g1/pin_fill.json": "0949dd56da65c36adae21dfa1613b6d8af3d559bade22c9348ac7460f536c4b0"}},
    {"window": "a3-tiktoken", "run": "t1", "kind": "fetch", "files": {
        "_fetch/a3-tiktoken/t1/record.json": "c832ad298b1fac3a5d05e0eb73dad9bba0a486b343efdb49277efe809a1e5732",
        "_fetch/a3-tiktoken/t1/place_record.json": "f146829800201a912e16e4f1715dfe3e03bcb333ea0c55504dc85b47904fca0f",
        "_fetch/a3-tiktoken/t1/pin_fill.json": "ab54c52a11176332a72d10851d83a5cad9133cc0b646f395ee8520c7b5459a97"}},
    {"window": "a3-git", "run": "r1", "kind": "fetch", "files": {
        "_fetch/a3-git/r1/record.json": "fd92d9809cb7939cfe0047b5b44a30caca1556d0861a5950a3e39def602d99df",
        "_fetch/a3-git/r1/place_record.json": "267473f0fb64feee64cf92931cba10b47e347016d8ee7a95d5d8d8d0d1a00d2d",
        "_fetch/a3-git/r1/pin_fill.json": "ba0f9248f430b15878c975ed3295059e11e7747a1f43902a6da2e8dafc4e8ab6"}},
    {"window": "facts", "run": "j3", "kind": "facts", "files": {
        "_facts/j3/facts.json": "b2d373f0d40d8c4ed885fb799416cf5c682ab35acdb7c636d09af786c3c14af8"}},
    {"window": "ollama", "run": "o1", "kind": "inventory", "files": {
        "_inventory/ollama/o1/record.json": "ee7c167f663f6544e476340dd1eb430cd67fb12a3b52584d7430f0d8f4161ae7"}},
    {"window": "local-v2", "run": "l1", "kind": "local-v2", "files": {
        "_fetch/local-v2/l1/place_record.json": "4ee8dd1863933bcb9fdb709490945ca7a477b9027fc6d16c0b9fccb535696514"}},
    {"window": "d1-tag", "run": "d1", "kind": "d1-tag", "files": {
        "_d1tag/d1/record.json": "684c97b73b11ed29f7fa49f93e26ade0f20105f38b5c32042c77af3fb4cbfe5c"}},
]
#: The runs that were NOT cleared - kept by sha256 with the reason, never used.
FAILED = [
    {"window": "a3-hf", "run": "h1", "files": {
        "_fetch/a3-hf/h1/record.json": "a0348e6b2350a574b8c37ff527080ba7b1237d61754814ebc85a54594850e432",
        "_fetch/a3-hf/h1/place_record.json": "1eda1b711dc84ef114c426e114aa7537415b030e26ccc7b4876d472149207532"},
     "reason": "the lme_s download stalled at about 97% - stopped, never applied; ruled O1: re-run as h2"},
    {"window": "facts", "run": "j1", "files": {
        "_facts/j1/facts.json": "d771d9fc3ff4ff85d53bb1bc3f7a1efb6b4b6b5efa06501d39cc86d18bc6fe9b"},
     "reason": "the local-v2 files (gitignored research/data) are absent from a clean worktree - fixed by O2"},
    {"window": "facts", "run": "j2", "files": {
        "_facts/j2/facts.json": "cc0db2e79185878cbad9d984bc47520d374196109be6e40582b8bba0977b8e36"},
     "reason": "BEAM's source_chat_ids are nested deeper than one level (TypeError in the child) - fixed by P-J11"},
]
#: Hosts reached with no issuer recorded, each with the reason (the i1 install record's own issuer_notes, F-P2-4).
ISSUERS_UNRECORDED = {"files.pythonhosted.org": "reached by pip only, so no fetch child records its issuer; pip's trust "
                                                "is its vendored certifi (--use-deprecated=legacy-certs, F-P2-4)"}
#: "Certainly" (the auditor, 2026-09-30, from window a7-arxiv d1's record: export.arxiv.org is served through Fastly, whose
#: CA's roots "Certainly Root R1" and "Certainly Root E1", O=Certainly, are in the Mozilla set certifi 2026.02.25 carries).
PUBLIC_ISSUER_ORGS = frozenset({"Amazon", "Sectigo Limited", "DigiCert Inc", "Let's Encrypt", "Google Trust Services",
                                "GlobalSign nv-sa", "Microsoft Corporation", "Certainly"})


class FreezeRefused(RuntimeError):
    pass


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read(runs_root: Path, rel: str, sha256: str) -> dict:
    f = Path(runs_root) / Path(*rel.split("/"))
    if not f.is_file():
        raise FreezeRefused(f"{rel}: no such record")
    raw = f.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != sha256:
        raise FreezeRefused(f"{rel}: sha256 {got[:12]}... is not the cleared {sha256[:12]}...")
    return json.loads(raw)


def pins_section(pins: dict) -> dict:
    """The v3 pins as filled; a non-alias v3 pin still unfilled stops the build."""
    out = {}
    for name, p in sorted(pins.items()):
        if p["source"] != "local-v2" and not p.get("alias_of") and p.get("sha256") is None:
            raise FreezeRefused(f"{name}: not filled - A3 is not complete")
        out[name] = {k: list(v) if isinstance(v, tuple) else v    # JSON has no tuple: the fragment must round-trip
                     for k, v in ((k, p.get(k)) for k in ("role", "stands", "source", "repo", "path", "revision", "sha256",
                                                          "bytes", "licence", "licence_found", "window", "alias_of",
                                                          "filled_from"))}
    return out


def _hosts(rec: dict) -> set:
    """The hosts a record says it contacted: a fetch record's "hosts"; a tool record's request URLs."""
    if rec.get("hosts"):
        return set(rec["hosts"])
    return {urlparse(rec[k]).hostname for k in ("package", "registration", "catalog_entry")
            if isinstance(rec.get(k), str) and rec[k].startswith("https://")}


def _tunnelled(rec: dict, rel: str) -> set | None:
    """The hosts a record's catcher tunnelled (Q-DH-1 = O-a): its lines with tunnelled True and refused False; None when
    the record has no catcher. A line that is neither a tunnel (True, False) nor a refusal (False, True) - the only two
    forms the v3 proxy writes - stops the build, so no TLS contact hides behind a malformed line."""
    lines = rec.get("catcher")
    if not isinstance(lines, list):
        return None
    for i, ln in enumerate(lines):
        t, r = ln.get("tunnelled"), ln.get("refused")
        if not ((t is True and r is False) or (t is False and r is True)):
            raise FreezeRefused(f"{rel}: catcher line {i} ({ln.get('host')}) has tunnelled {t!r} with refused {r!r} - "
                                "neither a tunnel nor a refusal")
    return {ln.get("host") for ln in lines if ln.get("tunnelled") is True and ln.get("refused") is False}


def _issuer_rows(rec: dict) -> list:
    """(host, organisation, CN) rows: a fetch record's "issuers", or a tool record's TLS "peer" (its subject's host)."""
    rows = [tuple(r) for r in rec.get("issuers") or []]
    peer = rec.get("peer")
    if isinstance(peer, dict) and peer.get("issuer_o") is not None:
        rows.append((peer.get("subject_cn"), peer.get("issuer_o"), peer.get("issuer_cn")))
    return rows


def _binary(e: dict, recs: dict, cleared: list) -> dict:
    """The binaries section's entry of one cleared binary run (Q-BIN-1 = O-a); FreezeRefused names the first clause
    that fails."""
    w, r = e["window"], e["run"]
    name = f"{w} {r}"
    brel = next((rel for rel in e["files"] if rel.endswith("/bin_record.json")), None)
    if brel is None:
        raise FreezeRefused(f"{name}: the binary entry names no bin_record.json")
    wrel = f"_fetch/{w}/{r}/record.json"
    if wrel not in e["files"]:
        raise FreezeRefused(f"{name}: its window record {wrel} is not among the cleared files")
    b = recs[(w, r, brel)]
    if b.get("problems"):
        raise FreezeRefused(f"{name}: its bin_record carries {len(b['problems'])} problem(s)")
    if b.get("binary_started") is not False:
        raise FreezeRefused(f"{name}: binary_started is {b.get('binary_started')!r}, not False - the binary must not have "
                            "run before the freeze")
    exe = (b.get("sums_line") or {}).get("name")
    asset = next((a for a in (b.get("job") or {}).get("assets") or [] if exe and a.get("name") == exe), None)
    if asset is None:
        raise FreezeRefused(f"{name}: no release asset is named as its checksum line's binary ({exe!r})")
    if asset.get("digest") != f"sha256:{b.get('sha256')}":
        raise FreezeRefused(f"{name}: its sha256 {str(b.get('sha256'))[:12]} is not the release asset's digest "
                            f"{str(asset.get('digest'))[:19]}")
    if asset.get("size") != b.get("bytes"):
        raise FreezeRefused(f"{name}: its size {b.get('bytes')} is not the release asset's size {asset.get('size')}")
    docs = b.get("docs_record") or {}
    docs_sha, docs_path = docs.get("sha256"), str(docs.get("path") or "").replace("\\", "/")
    drel = next((rel for c in cleared for rel, sha in c["files"].items()   # its bytes AND its place: never a twin's
                 if docs_sha and sha == docs_sha and docs_path.endswith("/" + rel)), None)
    if drel is None:
        raise FreezeRefused(f"{name}: its docs_record ({str(docs_sha)[:12]}) is not a cleared file")
    return {"run": r, "repo": b.get("repo"), "tag": b.get("tag"), "commit": b.get("commit"), "asset": asset["name"],
            "sha256": b["sha256"], "bytes": b["bytes"], "digest": asset["digest"],
            "docs_record": {"path": drel, "sha256": docs_sha}, "window_record": {"path": wrel, "sha256": e["files"][wrel]}}


def build(runs_root: Path, *, pins: dict, filled: dict | None = None, cleared: list | None = None,
          failed: list | None = None, prereg: Path | None = None, unrecorded: dict | None = None) -> dict:
    """The freeze fragment from the cleared records (each by sha256); raises FreezeRefused before anything is written."""
    cleared = CLEARED if cleared is None else cleared
    failed = FAILED if failed is None else failed
    recs = {(e["window"], e["run"], rel): _read(runs_root, rel, sha) for e in cleared for rel, sha in e["files"].items()}
    frecs = {(e["window"], e["run"], rel): _read(runs_root, rel, sha) for e in failed for rel, sha in e["files"].items()}

    def counts(e: dict, pool: dict) -> dict:
        return {rel: len(pool[(e["window"], e["run"], rel)].get("problems") or []) for rel in e["files"]}

    excluded_traces: dict = {}
    for e in cleared:
        probs = [p for rel in e["files"] for p in recs[(e["window"], e["run"], rel)].get("problems") or []]
        if probs and not (e.get("note") and e.get("excluded")):
            raise FreezeRefused(f"{e['window']} {e['run']}: its records carry {len(probs)} problem(s) and no ruling note "
                                "with an excluded list")
        if probs and probs != e.get("problems_verbatim"):
            raise FreezeRefused(f"{e['window']} {e['run']}: its problems are not the ones its entry names verbatim")
        by_job: dict = {}
        for x in e.get("excluded") or []:
            by_job.setdefault(x["job"], set()).update(x["requests"])
        ex_reqs = {r for reqs in by_job.values() for r in reqs}
        for p in probs:
            m = re.match(r"job (\d+)(?: request (\S+):|: the fetch child exited)", p)
            job_reqs = by_job.get(int(m.group(1))) if m else None
            # C2: an exit line needs an excluded request in its own job; a request line, that request in its own job
            if not m or not job_reqs or (m.group(2) is not None and m.group(2) not in job_reqs):
                raise FreezeRefused(f"{e['window']} {e['run']}: a problem no excluded request explains: {p[:80]}")
        traced = sorted({name for req in ex_reqs for name, v in (filled or {}).items()
                         if v.get("licence_source") == f"{e['run']} repo {req.split(':', 1)[1]}"
                         or pins.get(name, {}).get("repo") == req.split(":", 1)[1]})
        if ex_reqs:
            excluded_traces[f"{e['window']} {e['run']}"] = len(traced)
        if traced:
            raise FreezeRefused(f"{traced[0]}: traces to an excluded request of {e['window']} {e['run']}")
    out: dict = {"windows": [{**{k: e[k] for k in ("window", "run", "kind", "files", "note", "problems_verbatim",
                                                   "excluded", "feeds_pins") if k in e},
                              "problems": counts(e, recs)} for e in cleared],
                 "failed_runs": [{**{k: e[k] for k in ("window", "run", "files", "reason")}, "problems": counts(e, frecs)}
                                 for e in failed],
                 "pins": pins_section(pins), "issuers": {},
                 # C1: computed from the real FILLED table - every count 0, or the build stopped above
                 "pins_from_excluded_requests": excluded_traces}
    unrecorded = ISSUERS_UNRECORDED if unrecorded is None else unrecorded
    contacted: dict = {}
    not_reached: dict = {}
    for (w_, r_, rel), rec in sorted(recs.items()):
        if not (rel.endswith("/record.json") or rel.startswith("_tools/")):
            continue
        for host, org, cn in _issuer_rows(rec):
            if org not in PUBLIC_ISSUER_ORGS:
                raise FreezeRefused(f"{rel}: {host} was served by {org!r}, not a public issuer (R-A3-7)")
            out["issuers"].setdefault(host, [])
            if [org, cn] not in out["issuers"][host]:
                out["issuers"][host].append([org, cn])
        reached = _tunnelled(rec, rel)
        for host in (_hosts(rec) if reached is None else reached):
            contacted.setdefault(host, rel)
        if reached is not None and _hosts(rec) - reached:
            not_reached.setdefault((w_, r_), set()).update(_hosts(rec) - reached)
    for w in out["windows"]:
        gone = not_reached.get((w["window"], w["run"]))
        if gone:                                         # only when there is one: freeze_a3.json stays byte for byte
            w["declared_not_reached"] = sorted(gone)
    for host in out["issuers"]:
        out["issuers"][host].sort()
    for host, rel in sorted(contacted.items()):          # F2: every contacted host - an issuer, or a declared reason
        if host not in out["issuers"] and host not in unrecorded:
            raise FreezeRefused(f"{rel}: {host} was contacted with no recorded issuer and no declared reason")
    out["issuers_unrecorded"] = {h: unrecorded[h] for h in sorted(contacted) if h not in out["issuers"]}
    by_kind = {}
    for e in cleared:
        by_kind.setdefault(e["kind"], []).append(e)
    for e in by_kind.get("inventory", []):
        rec = recs[(e["window"], e["run"], next(iter(e["files"])))]
        out["models"] = {"ollama_version": rec.get("version"), "run": e["run"],
                         "pins": {n: {k: r.get(k) for k in ("role", "found", "digest", "digest12", "pin")}
                                  for n, r in sorted((rec.get("pins") or {}).items())}}
    for e in by_kind.get("d1-tag", []):
        rec = recs[(e["window"], e["run"], next(iter(e["files"])))]
        out["d1_tag"] = {k: rec.get(k) for k in ("tag", "base", "modelfile_sha256", "version")} | {"run": e["run"]}
    for e in by_kind.get("local-v2", []):
        rec = recs[(e["window"], e["run"], next(iter(e["files"])))]
        out["local_v2"] = {"run": e["run"], "placed": rec.get("placed")}
    for e in by_kind.get("tool", []):
        rec = recs[(e["window"], e["run"], next(iter(e["files"])))]
        out.setdefault("venvs", {})["py314"] = {k: rec.get(k) for k in ("version", "python_exe_sha256", "nupkg_sha256",
                                                                       "tools_tree_sha256")}
    for e in by_kind.get("install", []):
        rec = next(recs[(e["window"], e["run"], rel)] for rel in e["files"] if rel.endswith("install_record.json"))
        out.setdefault("venvs", {})["v3_data"] = {
            "wheels": rec.get("wheels"), "installed_set_sha256": rec.get("installed_set_sha256"),
            "installed_files": rec.get("installed_files"), "tag": rec.get("tag"),
            "pip": {k: (rec.get("pip") or {}).get(k) for k in ("version", "certifi_sha256", "trust")}}
    for e in by_kind.get("binary", []):
        out.setdefault("binaries", {})[e["window"]] = _binary(e, recs, cleared)
    for e in by_kind.get("facts", []):
        rel, sha = next(iter(e["files"].items()))
        out["facts"] = {"run": e["run"], "record": rel, "sha256": sha,
                        "use": "the source of rev2's dataset-facts slot, written only at A10"}
    prereg = prereg if prereg is not None else HERE / "PREREG-V3-rev1.md"
    out["prereg_rev1"] = hashlib.sha256(Path(prereg).read_bytes()).hexdigest()
    return out


def render(freeze: dict) -> bytes:
    return (json.dumps(freeze, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the A3 fragment of FREEZE-V3 (A3.k)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    CP = _load("v3_corpus_pin_freeze", HERE / "corpus_pin_v3.py")
    try:
        freeze = build(L.Contract.default().runs_root, pins=CP.PINS, filled=CP.FILLED)
    except FreezeRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    Path(args.out).write_bytes(render(freeze))
    print(json.dumps({"windows": len(freeze["windows"]), "failed_runs": len(freeze["failed_runs"]),
                      "pins": len(freeze["pins"]), "issuers": sorted(freeze["issuers"])}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
