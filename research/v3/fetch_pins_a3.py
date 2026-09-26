#!/usr/bin/env python3
"""PREREG-V3 plan step A3.f/A3.g (.loop/A3F-PLAN-2026-09-26.md and the auditor's Q-A3F rulings): the pinned files.

Every pinned file - datasets, tokenizer files, the official prompt and scoring files, licence evidence, the tiktoken
BPE, the A-MEM source - is fetched by a contract child through the catcher and the hop, in one declared window per
GO (a3-hf, a3-github, a3-tiktoken, a3-git). Nothing here fetches: this module plans a window from the discovery
records, and after the window closes it re-hashes, places and fills.

The pure layer (this part):

* the discovery records are read only when their sha256 is the pinned one (S1);
* a pin's expectation comes from the tree the discovery record holds at the pin's revision - an HF LFS file is its
  sha256 and size, any other file its git blob sha1 and size; a tree at another revision (S4), a truncated tree
  (S3) or a file missing from it (S2) stops the plan;
* the licence found is the discovery's own (an HF card's cardData.license, a GitHub repository's spdx id);
  NOASSERTION is none, and LoCoMo's is read from its LICENSE.txt (Q-A3F-7);
* every URL carries the 40-hex revision (S5); a file over the manifest's single-file cap (S6) or a path too long
  for Windows (S7) stops; the disk floor is max(the manifest's floor, 3 x the window's total).

A stop is a :class:`Stop` with its code; the window runner turns every one into a named problem.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CP = _load("v3_corpus_pin_fpa3", HERE / "corpus_pin_v3.py")
MANIFEST = json.loads((HERE / "fetch_manifest.json").read_text(encoding="utf-8"))

GIB = 1 << 30
FLOOR_GIB = MANIFEST["disk"]["floor_gb"]
MULTIPLE = MANIFEST["disk"]["multiple_of_window_total"]
MAX_FILE = MANIFEST["disk"]["stop_single_file_gb"] * GIB
#: Windows' MAX_PATH is 260; a download is written as "<path>.partial", and nothing here relies on long paths.
SAFE_PATH = 240
#: The two discovery records the auditor received; nothing is read from any other.
DISCOVERY_SHAS = {"d1": CP.DISCOVERY_D1, "d2": CP.DISCOVERY_D2}
_SHA40 = re.compile(r"[0-9a-f]{40}")

#: Where each GitHub repository's tree at its pinned commit was saved, when not d1's phase 4 (the auditor's B3:
#: d1's mem0 tree is at d1's head; AMA-Bench was saved under the name the card linked, AMA-Hub).
GH_TREES = {"mem0ai/mem0": ("d2", 2, "gh/mem0ai__mem0/tree_parent.json"),
            "AMA-Bench/AMA-Bench": ("d2", 1, "gh/AMA-Bench__AMA-Hub/tree.json"),
            "mohammadtavakoli78/BEAM": ("d2", 1, "gh/mohammadtavakoli78__BEAM/tree.json")}
GH_REPOS = {"AMA-Bench/AMA-Bench": ("d2", 0, "gh/AMA-Bench__AMA-Hub/repo.json"),
            "mohammadtavakoli78/BEAM": ("d2", 0, "gh/mohammadtavakoli78__BEAM/repo.json")}


class Stop(Exception):
    """A plan or a placement the rules do not allow; ``code`` names the rule (S1..S12, P1..P15, G1..G7, F1..F2)."""

    def __init__(self, code: str, subject: str, detail: str):
        super().__init__(f"{code} {subject}: {detail}")
        self.code = code


def _safe(repo: str) -> str:
    return repo.replace("/", "__")


@dataclass
class Discovery:
    records: dict

    def unit(self, rec: str, job: int) -> Path:
        return Path(self.records[rec]["jobs"][job]["unit"])

    def read(self, rec: str, job: int, rel: str):
        f = self.unit(rec, job) / rel
        return json.loads(f.read_bytes()) if f.is_file() else None

    def request_url(self, rec: str, job: int, rid: str) -> str | None:
        for r in (self.records[rec]["jobs"][job].get("job") or {}).get("requests") or []:
            if r.get("id") == rid:
                return r.get("url")
        return None


def load_discovery(runs_root: Path, *, shas: dict | None = None) -> Discovery:
    """The discovery records under ``runs_root``, each refused unless its sha256 is the pinned one (S1)."""
    records = {}
    for name, want in (shas or DISCOVERY_SHAS).items():
        f = Path(runs_root) / "_fetch" / "a3-discovery" / name / "record.json"
        if not f.is_file():
            raise Stop("S1", f"a3-discovery {name}", "the record is missing")
        raw = f.read_bytes()
        got = hashlib.sha256(raw).hexdigest()
        if got != want:
            raise Stop("S1", f"a3-discovery {name}", f"sha256 {got[:12]}... is not the pinned {want[:12]}...")
        records[name] = json.loads(raw)
    return Discovery(records)


def _kind(pin: dict) -> str:
    return "datasets" if pin["source"] == "hf-dataset" else "models"


def tree_source(pin: dict) -> tuple[str, int, str]:
    """(record, job, saved path) of the tree that holds ``pin``'s file at its revision."""
    if pin["source"] in ("hf-dataset", "hf-model"):
        return "d1", 1, f"meta/{_kind(pin)}/{_safe(pin['repo'])}/tree.json"
    if pin["source"] == "github":
        return GH_TREES.get(pin["repo"], ("d1", 3, f"gh/{_safe(pin['repo'])}/tree.json"))
    raise Stop("S2", pin.get("repo") or "?", f"a {pin['source']} pin has no discovery tree")


def _subject(pin: dict) -> str:
    return f"{pin['repo']}@{str(pin['revision'])[:12]}:{pin['path']}"


def expectation(disc: Discovery, pin: dict) -> dict:
    """What the file must be: {sha256, size} for an HF LFS file, {git_blob_sha1, size} for any other."""
    rec, job, rel = tree_source(pin)
    subj = _subject(pin)
    tree = disc.read(rec, job, rel)
    if tree is None:
        raise Stop("S2", subj, f"no tree saved at {rec} j{job} {rel}")
    if pin["source"] in ("hf-dataset", "hf-model"):
        revision = (disc.read("d1", 0, f"meta/{_kind(pin)}/{_safe(pin['repo'])}/revision.json") or {}).get("sha")
        url = disc.request_url(rec, job, f"tree:{_kind(pin)}:{pin['repo']}") or ""
        if revision != pin["revision"] or f"/tree/{pin['revision']}" not in url:
            raise Stop("S4", subj, f"the record's revision {str(revision)[:12]} / tree URL is not the pin's")
        entries = [e for e in tree if isinstance(e, dict) and e.get("path") == pin["path"]]
        if len(entries) != 1 or entries[0].get("type") != "file":
            raise Stop("S2", subj, "the file is not in the tree")
        e = entries[0]
        if isinstance(e.get("lfs"), dict):
            return {"sha256": e["lfs"]["oid"], "size": int(e["lfs"]["size"])}
        return {"git_blob_sha1": e["oid"], "size": int(e["size"])}
    if tree.get("sha") != pin["revision"]:
        raise Stop("S4", subj, f"the tree was read at {str(tree.get('sha'))[:12]}, not at the pin")
    if tree.get("truncated") is not False:
        raise Stop("S3", subj, "the tree is truncated")
    entries = [e for e in tree.get("tree") or [] if isinstance(e, dict) and e.get("path") == pin["path"]]
    if len(entries) != 1 or entries[0].get("type") != "blob":
        raise Stop("S2", subj, "the file is not in the tree")
    return {"git_blob_sha1": entries[0]["sha"], "size": int(entries[0]["size"])}


def licence_found(disc: Discovery, pin: dict) -> tuple[str | None, str]:
    """(the licence the discovery records state for ``pin``'s repository, where it was read). NOASSERTION is none."""
    repo = pin["repo"]
    if pin["source"] in ("hf-dataset", "hf-model"):
        rev = disc.read("d1", 0, f"meta/{_kind(pin)}/{_safe(repo)}/revision.json") or {}
        return (rev.get("cardData") or {}).get("license"), f"d1 card {repo}"
    rec, job, rel = GH_REPOS.get(repo, ("d1", 2, f"gh/{_safe(repo)}/repo.json"))
    spdx = ((disc.read(rec, job, rel) or {}).get("license") or {}).get("spdx_id")
    return (None if spdx in (None, "NOASSERTION") else spdx), f"{rec} repo {repo}"


def locomo_licence(text: str) -> str | None:
    """Q-A3F-7: LoCoMo's LICENSE.txt is CC-BY-NC-4.0 when it carries that licence's title and never the ND rule."""
    if "Attribution-NonCommercial 4.0 International" in text and not CP._ND.search(text):
        return "CC-BY-NC-4.0"
    return None


def _revision(pin: dict) -> str:
    rev = pin.get("revision")
    if not (isinstance(rev, str) and _SHA40.fullmatch(rev)):
        raise Stop("S5", str(pin.get("repo")), f"the revision {rev!r} is not a 40-hex commit")
    return rev


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def hf_url(pin: dict) -> str:
    prefix = "datasets/" if pin["source"] == "hf-dataset" else ""
    return f"https://huggingface.co/{prefix}{pin['repo']}/resolve/{_revision(pin)}/{_quote(pin['path'])}"


def raw_url(pin: dict) -> str:
    return f"https://raw.githubusercontent.com/{pin['repo']}/{_revision(pin)}/{_quote(pin['path'])}"


def size_ok(name: str, size: int) -> bool:
    if size > MAX_FILE:
        raise Stop("S6", name, f"{size} bytes is over the single-file cap - stop and ask")
    return True


def path_ok(name: str, path) -> bool:
    if len(str(path)) >= SAFE_PATH:
        raise Stop("S7", name, f"a path of {len(str(path))} characters is too long for Windows")
    return True


def need_bytes(total: int) -> int:
    """The auditor's Q-A3-2 floor: max(the floor, 3 x the window's discovered total)."""
    return max(FLOOR_GIB * GIB, MULTIPLE * total)


# ── the plan of a window (a3-hf, a3-github, a3-tiktoken): its files, jobs and floor ──────────────────

#: Repositories whose licence found is read from a licence file at the pinned commit, not from the repository's
#: current spdx (Q-A3F-6, Q-A3F-7): the evidence pin, and the rule that reads its text.
EVIDENCE = {"snap-research/locomo": "locomo_licence_txt", "mem0ai/mem0": "mem0_licence"}
#: The hop's bandwidth floor the auditor's Q-A3F-11 timeout assumes.
MIN_RATE = 256 * 1024
URL_MAX_BYTES = 16 * 1024 * 1024


def metadata_licence(path: Path) -> tuple[str | None, str]:
    """Q-A3F-4: the tiktoken package's own METADATA licence (License-Expression, else License), as an SPDX id, with
    its source - the file and its sha256. Anything but MIT is no licence found (F1)."""
    raw = Path(path).read_bytes()
    fields = dict(line.split(":", 1) for line in raw.decode("utf-8", "replace").splitlines()
                  if line.startswith(("License-Expression:", "License:")))
    value = (fields.get("License-Expression") or fields.get("License") or "").strip()
    spdx = {"mit": "MIT", "mit license": "MIT"}.get(value.casefold())
    return spdx, f"METADATA {Path(path).name} sha256 {hashlib.sha256(raw).hexdigest()[:12]}"


def apache_licence(text: str) -> str | None:
    """mem0's LICENSE at the pinned commit is Apache-2.0 when it carries that licence's title and version."""
    if "Apache License" in text and "Version 2.0, January 2004" in text and not CP._ND.search(text):
        return "Apache-2.0"
    return None


EVIDENCE_RULES = {"locomo_licence_txt": locomo_licence, "mem0_licence": apache_licence}


@dataclass
class Item:
    """One distinct file of a window: the pins it serves, where it comes from, what it must be, where it goes."""
    names: list
    url: str
    save: str
    expect: dict
    max_bytes: int
    dest: Path


@dataclass
class Plan:
    window: str
    hosts: list
    arm: str
    items: list
    jobs: list
    total: int
    need_bytes: int
    licences: dict


def job_timeout(nbytes: int) -> int:
    """Q-A3F-11: max(3600 s, the bytes at 256 KiB/s); a timeout is a failed window."""
    return max(3600, -(-nbytes // MIN_RATE))


def plan_window(window: str, *, disc: Discovery, pins: dict, manifest: dict, hf_hub: Path, pins_root: Path,
                unit_root: Path, url_licence: tuple | None = None) -> Plan:
    """The window's plan, every stop raised before anything starts: the manifest's pins are the table's (S8), each
    file's expectation from its discovery tree (S2-S4), 40-hex revisions (S5), the single-file cap (S6), paths short
    enough (S7), every URL on a window host (S9). ``unit_root`` is <runs>/_fetch.<window>/<run>/<arm>."""
    w = manifest["windows"][window]
    hosts, arm = list(w["hosts"]), (w.get("arms") or ["fetch"])[0]
    names = list(w["pins"])
    table = sorted(n for n, p in pins.items() if p["window"] == window)
    if sorted(names) != table:
        raise Stop("S8", window, f"the manifest's pins {sorted(names)} are not the table's {table}")
    items: dict = {}
    for name in names:
        pin = pins[name]
        if pin["source"] in ("hf-dataset", "hf-model", "github"):
            exp = expectation(disc, pin)
            url = hf_url(pin) if pin["source"] != "github" else raw_url(pin)
            kind = f"hf/{_kind(pin)}" if pin["source"] != "github" else "gh"
            save = f"{kind}/{_safe(pin['repo'])}/{pin['path']}"
            key = (pin["source"], pin["repo"], pin["revision"], pin["path"])
            dest = CP.location(name, hf_hub=hf_hub, pins_root=pins_root, pins=pins)
            max_bytes = exp["size"]
        elif pin["source"] == "url":
            sha = (pin.get("cross_check") or {}).get("sha256")
            if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
                raise Stop("S10", name, "a URL pin needs its committed cross_check sha256")
            exp, url, key = {"sha256": sha}, pin["path"], ("url", pin["path"])
            save = f"url/{pin['path'].rsplit('/', 1)[-1]}"
            dest = CP.location(name, hf_hub=hf_hub, pins_root=pins_root, sha256=sha, pins=pins)
            max_bytes = URL_MAX_BYTES
        else:
            raise Stop("S9", name, f"a {pin['source']} pin is not fetched by a file window")
        if urllib.parse.urlsplit(url).hostname not in hosts:
            raise Stop("S9", name, f"{urllib.parse.urlsplit(url).hostname} is not a host of {window}")
        size_ok(name, max_bytes)
        path_ok(name, dest)
        path_ok(name, Path(unit_root) / "j00" / (save + ".partial"))
        if key in items:                                # the expectation is a function of the key
            items[key].names.append(name)
        else:
            items[key] = Item([name], url, save, exp, max_bytes, Path(dest))
    ordered = sorted(items.values(), key=lambda it: (0 if set(it.names) & set(EVIDENCE.values()) else 1, it.save))
    jobs = _jobs(window, hosts, ordered, w.get("max_redirects", 0))
    total = sum(it.expect.get("size", it.max_bytes) for it in ordered)
    licences = {}
    for name in names:
        if pins[name]["source"] == "url":
            licences[name] = url_licence or (None, "no METADATA given")
            continue
        ev = EVIDENCE.get(pins[name]["repo"] or "")
        licences[name] = (None, f"evidence {ev}") if ev else licence_found(disc, pins[name])
    return Plan(window, hosts, arm, ordered, jobs, total, need_bytes(total), licences)


def _jobs(window: str, hosts: list, items: list, max_redirects: int) -> list:
    """One fetch-child job per repository (a file of 1 GiB or more gets a job of its own), each with the window's
    hosts, the manifest's redirect rule and its Q-A3F-11 timeout."""
    groups: dict = {}
    for it in items:
        parts = it.save.split("/")
        repo = "/".join(parts[:3] if parts[0] == "hf" else parts[:2])      # hf/<kind>/<repo>, gh/<repo>, url/<file>
        groups.setdefault(it.save if it.expect.get("size", 0) >= GIB else repo, []).append(it)
    jobs = []
    for group in groups.values():
        nbytes = sum(it.expect.get("size", it.max_bytes) for it in group)
        jobs.append({"hosts": list(hosts), "max_redirects": max_redirects, "timeout_s": job_timeout(nbytes),
                     "requests": [{"id": f"pin:{it.names[0]}", "url": it.url, "save": it.save, "max_bytes": it.max_bytes,
                                   "expect": dict(it.expect)} for it in group]})
    return jobs


# ── after the window: re-hash, place, fill ────────────────────────────────────────────────────────────

#: The certificate issuers (organisation) a window's hosts may present (R-A3-7: the catcher relays ciphertext, only
#: the child sees the peer's chain). Public Web PKI CAs; d1/d2 saw Amazon (huggingface.co) and Sectigo (GitHub). An
#: issuer outside this set stops placement and needs a ruling.
PUBLIC_ISSUER_ORGS = frozenset({"Amazon", "Sectigo Limited", "DigiCert Inc", "Let's Encrypt", "Google Trust Services",
                                "GlobalSign nv-sa", "Microsoft Corporation"})
REPLACE_RETRIES = (1.0, 2.0, 4.0)


def _hash_file(path: Path) -> tuple[str, str, int]:
    """(sha256, git blob sha1, size) of a file, streamed."""
    size = path.stat().st_size
    h256, h1 = hashlib.sha256(), hashlib.sha1(f"blob {size}\0".encode("ascii"))
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h256.update(block)
            h1.update(block)
    return h256.hexdigest(), h1.hexdigest(), size


def _within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def host_issuers(record: dict) -> dict:
    """{host: sorted [[O, CN], ...]} as the fetch children saw each host's certificate issuer."""
    seen: dict = {}
    for j in record.get("jobs") or []:
        for r in j.get("summary") or []:
            if r.get("ok") and r.get("final_host"):
                seen.setdefault(r["final_host"], set()).add((r.get("issuer_o"), r.get("issuer_cn")))
    return {h: sorted([list(x) for x in v], key=lambda x: (str(x[0]), str(x[1]))) for h, v in sorted(seen.items())}


def precheck(record: dict, plan: Plan, *, issuer_orgs=PUBLIC_ISSUER_ORGS) -> list[str]:
    """P1-P3: a window placed from must be clean, on exactly the plan's hosts, every host's issuer recorded and its
    organisation (O, matched exactly) in the public set - a P3 names the host, O and CN."""
    problems = [f"P1 window_not_clean: {p}" for p in record.get("problems") or []]
    if sorted(record.get("hosts") or []) != sorted(plan.hosts):
        problems.append(f"P2 hosts_not_manifest: {record.get('hosts')} is not {plan.hosts}")
    for host, pairs in host_issuers(record).items():
        for org, cn in pairs:
            if org is None:
                problems.append(f"P3 issuer_unrecorded: {host} (O None, CN {cn!r})")
            elif org not in issuer_orgs:
                problems.append(f"P3 issuer_not_public: {host} presented O {org!r}, CN {cn!r}")
    return problems


def _source(record: dict, item: Item) -> tuple[Path | None, dict]:
    rid = f"pin:{item.names[0]}"
    for j in record.get("jobs") or []:
        for r in j.get("summary") or []:
            if r.get("id") == rid:
                return Path(j["unit"]), r
    return None, {}


def _place_one(item: Item, record: dict, *, polygon_root: Path, roots: tuple, hasher) -> dict:
    """One file: re-hashed on disk against the discovery's expectation and the child's claim, then moved into place.
    Raises Stop with its P-code; returns the entry."""
    subj = item.names[0]
    unit, summary = _source(record, item)
    src = (unit / item.save) if unit is not None else None
    if src is None or not src.is_file():
        raise Stop("P5", subj, "download_missing")
    if src.is_symlink() or not _within(src, unit):
        raise Stop("P5", subj, "download_is_link or outside its unit")
    try:
        sha256, blob, size = hasher(src)
    except OSError as e:
        raise Stop("P9", subj, f"hash_failed: {type(e).__name__}") from None
    if "size" in item.expect and size != item.expect["size"]:
        raise Stop("P6", subj, f"size_mismatch: {size} is not the expected {item.expect['size']}")
    for k, got in (("sha256", sha256), ("git_blob_sha1", blob)):
        if k in item.expect and got != item.expect[k]:
            raise Stop("P7", subj, f"hash_mismatch: {k} {got[:12]} is not the expected {item.expect[k][:12]}")
    if summary.get("sha256") != sha256 or summary.get("bytes") != size:
        raise Stop("P8", subj, "disk_differs_from_child_summary")
    dest = Path(item.dest)
    if not _within(dest, polygon_root) or not any(_within(dest, r) for r in roots):
        raise Stop("P10", subj, f"dest_outside_polygon: {dest}")
    if len(str(dest)) >= SAFE_PATH:
        raise Stop("P11", subj, f"path_too_long: {len(str(dest))}")
    if dest.exists():
        have = hasher(dest) if dest.is_file() else (None, None, None)
        if have[0] == sha256 and have[2] == size:
            return {"state": "already-placed", "sha256": sha256, "bytes": size, "dest": str(dest)}
        raise Stop("P12", subj, f"dest_occupied: {dest} holds other bytes - never overwritten")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for i, wait in enumerate((0.0, *REPLACE_RETRIES)):
        time.sleep(wait)
        try:
            os.replace(src, dest)
            break
        except PermissionError:
            if i == len(REPLACE_RETRIES):
                raise Stop("P13", subj, "replace_failed: the file stayed locked") from None
        except OSError as e:
            if getattr(e, "errno", None) == 18 or getattr(e, "winerror", None) == 17:
                raise Stop("P14", subj, "cross_volume - never copied") from None
            raise Stop("P13", subj, f"replace_failed: {type(e).__name__}") from None
    if not dest.is_file() or dest.stat().st_size != size:
        raise Stop("P13", subj, "replace_failed: the destination is not the file")
    return {"state": "verified-placed", "sha256": sha256, "bytes": size, "dest": str(dest)}


def _refs_main(item: Item, pins: dict) -> None:
    """HF only: <hub>/<kind>--<org>--<name>/refs/main holds the revision; another one is never repointed (Q-A3F-9)."""
    pin = pins[item.names[0]]
    if pin["source"] not in ("hf-dataset", "hf-model"):
        return
    repo_dir = Path(item.dest)
    for _ in range(len(Path(pin["path"]).parts) + 2):   # up from <path> past <revision> and snapshots
        repo_dir = repo_dir.parent
    ref = repo_dir / "refs" / "main"
    if ref.is_file():
        if ref.read_text(encoding="ascii", errors="replace").strip() != pin["revision"]:
            raise Stop("P15", item.names[0], f"refs_main_elsewhere: {ref} - left, never repointed")
        return
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(pin["revision"].encode("ascii"))


def place_window(record: dict, plan: Plan, *, pins: dict, polygon_root: Path, hf_hub: Path, pins_root: Path,
                 place_path: Path, issuer_orgs=PUBLIC_ISSUER_ORGS, hasher=_hash_file) -> dict:
    """After the window: the precheck, then each file (evidence first) - its entry written "unverified" before any
    hash, then re-hashed and placed, then marked; the first problem stops the placement. The place record is
    rewritten after every step, so it never claims more than was done."""
    raw = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    out = {"window": plan.window, "record_sha256": hashlib.sha256(raw).hexdigest(), "issuers": host_issuers(record),
           "entries": {}, "problems": []}
    out["problems"] = precheck(record, plan, issuer_orgs=issuer_orgs)
    _write_json(place_path, out)
    if out["problems"]:
        return out
    for item in plan.items:
        key = item.names[0]
        out["entries"][key] = {"names": list(item.names), "state": "unverified", "dest": str(item.dest)}
        _write_json(place_path, out)
        try:
            entry = _place_one(item, record, polygon_root=polygon_root, roots=(hf_hub, pins_root), hasher=hasher)
            _refs_main(item, pins)
        except Stop as s:
            out["problems"].append(str(s))
            _write_json(place_path, out)
            return out
        out["entries"][key].update(entry)
        _write_json(place_path, out)
    return out


def fill_inputs(plan: Plan, placed: dict, pins: dict) -> tuple[dict, list[str]]:
    """{pin: {revision, sha256, bytes, licence_found, licence_source}} from a clean placement, and the problems:
    F1 a pin with no licence found, F2 a fill() the table refuses (a dry run on a copy), F3 an alias whose bytes are
    not its pinned value. An alias (the oracle) is confirmed, never filled."""
    import copy  # noqa: PLC0415
    problems = list(placed.get("problems") or [])
    if problems:
        return {}, problems
    evidence_text = {}
    for item in plan.items:
        for n in item.names:
            if n in EVIDENCE_RULES:
                evidence_text[n] = Path(placed["entries"][item.names[0]]["dest"]).read_text(encoding="utf-8", errors="replace")
    inputs, table = {}, copy.deepcopy(pins)
    for item in plan.items:
        e = placed["entries"][item.names[0]]
        for n in item.names:
            pin = pins[n]
            lic, src = plan.licences[n]
            if src.startswith("evidence "):
                ev = src.split(" ", 1)[1]
                lic = EVIDENCE_RULES[ev](evidence_text.get(ev, "")) if ev in evidence_text else None
            if pin.get("alias_of"):
                if (e["sha256"], e["bytes"]) != (pin["sha256"], pin["bytes"]):
                    problems.append(f"F3 {n}: the bytes are not its alias {pin['alias_of']}")
                else:
                    inputs[n] = {"alias_confirmed": pin["alias_of"], "sha256": e["sha256"], "bytes": e["bytes"]}
                continue
            if lic is None:
                problems.append(f"F1 {n}: licence_found_missing ({src})")
                continue
            revision = e["sha256"] if pin["source"] == "url" else pin["revision"]
            try:
                CP.fill(n, revision=revision, sha256=e["sha256"], size=e["bytes"], licence_found=lic, pins=table)
            except CP.PinRefused as r:
                problems.append(f"F2 {n}: fill_refused: {r}")
                continue
            inputs[n] = {"revision": revision, "sha256": e["sha256"], "bytes": e["bytes"], "licence_found": lic,
                         "licence_source": src}
    return (inputs if not problems else {}), problems


# ── A3.g: the A-MEM source through a git child (Q-A3F-1..3) ─────────────────────────────────────────────

GIT_EXE = Path(r"C:\Program Files\Git\cmd\git.exe")
#: Q-A3F-3: git's OpenSSL backend with Git for Windows' own bundle (schannel's in-process CRL/OCSP fetches would go
#: past the catcher); the bundle's path and sha256 go into the record.
GIT_CA_BUNDLE = Path(r"C:\Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt")
GIT_ARM = "fetch-git"
GIT_TIMEOUT = 1800
PROBE_MAX = 1 << 20
#: The git child's own variables: no system config, no prompt of any kind (the contract already gives empty
#: GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM files and a fake HOME, and refuses an override of them).
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def bare_name(pin: dict) -> str:
    return pin["repo"].split("/")[1] + ".git"


def git_probe_job(pin: dict) -> dict:
    """Q-A3F-2: one fetch-child GET of info/refs, only so the record holds github.com's issuer; nothing is saved."""
    return {"hosts": ["github.com"], "max_redirects": 0, "requests": [
        {"id": "probe:github.com", "url": f"https://github.com/{pin['repo']}.git/info/refs?service=git-upload-pack",
         "save": None, "max_bytes": PROBE_MAX}]}


def git_clone_job(pin: dict, *, git_exe: Path = GIT_EXE, ca_bundle: Path = GIT_CA_BUNDLE,
                  ssl_backend: str | None = "openssl", url: str | None = None) -> dict:
    """``git clone --bare`` as a child under the contract: OpenSSL with the given bundle, no redirect, every object
    checked on receipt, no credential helper, no prompt. ``ssl_backend`` None is for a git without that choice."""
    argv = [*(["-c", f"http.sslBackend={ssl_backend}"] if ssl_backend else []), "-c", f"http.sslCAInfo={ca_bundle}",
            "-c", "http.followRedirects=false", "-c", "transfer.fsckObjects=true", "-c", "credential.helper=",
            "clone", "--bare", "--", url or f"https://github.com/{pin['repo']}.git", bare_name(pin)]
    return {"child": "git", "exe": str(git_exe), "argv": argv, "timeout_s": GIT_TIMEOUT, "env": dict(GIT_ENV)}


def canonical_listing(out: bytes) -> bytes:
    """Q-A3F-1: `git ls-tree -r --full-tree <commit>` as a version-free digest input - one "mode type object\tpath"
    line per entry, path-sorted, LF-joined, one final LF."""
    lines = [line for line in out.decode("utf-8").splitlines() if line]
    return ("\n".join(sorted(lines, key=lambda s: s.split("\t", 1)[-1])) + "\n").encode("utf-8")


def step_problems(step: str, summary: dict) -> list[str]:
    """An offline step's own boundary check, judged like a window's (an unknown count is a problem)."""
    out = []
    if not summary.get("complete"):
        out.append(f"the {step} check is not complete")
    if summary.get("native_hits") != 0:
        out.append(f"the {step} check counted {summary.get('native_hits')} egress hit(s)")
    if summary.get("fs_hits") != 0:
        out.append(f"the {step} check counted {summary.get('fs_hits')} change(s) in the watched set")
    return out


def offline_step(c, L, *, stand: str, run: str, unit: str, argv: list, path_dirs: list, parent_env, native, fs,
                 timeout: float = 600) -> tuple:
    """One offline contract spawn (no proxy variables) under its own boundary check: (rc, stdout, stderr, check)."""
    import subprocess  # noqa: PLC0415
    u = L.make_unit_dirs(c, stand, run, "verify", unit)
    env = L.build_env(c, parent_env=parent_env, unit=u, path_dirs=path_dirs, declared=dict(GIT_ENV), catcher_url="",
                      proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    cid = f"{stand}-{run}-{unit}"
    W.begin_check(cid)
    try:
        child = L.spawn(c, argv, env=env, cwd=u.cwd, record={"role": "verify", "stand": None, "run": run, "arm": "verify",
                                                           "unit": unit},
                        parent_env=parent_env, catcher_url="", witnesses=W, requirement="required",
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = child.process.communicate(timeout=timeout)
            rc = child.process.returncode
        except subprocess.TimeoutExpired:
            child.kill_tree()
            rc, out, err = None, b"", b""
    finally:
        chk = W.end_check(cid)
    native_rec = chk.get("native") or {}
    summary = {"complete": chk.get("complete"), "native_hits": native_rec.get("hits"),
               "fs_hits": (chk.get("fs") or {}).get("fs_hits")}
    return rc, out, err, summary


def git_verify(c, L, *, bare: Path, pin: dict, run: str, parent_env, native=None, fs=None, git_exe: Path = GIT_EXE,
               work: Path) -> tuple[dict, list[str]]:
    """After the a3-git window, offline, each step under its own check: the pin commit resolves to itself (G3/G4),
    `fsck --strict` (G5), the ls-tree listing (the pin's sha256, Q-A3F-1), `git archive` (its tar sha256 beside it,
    G6), the git version and the HEAD at fetch (recorded only, Q-A3F-10). ``work`` receives listing and tar."""
    rev = pin["revision"]
    stand = "_verify.a3-git"
    facts: dict = {}
    problems: list[str] = []
    gd = ["--git-dir", str(bare)]

    def step(unit, args):
        rc, out, err, chk = offline_step(c, L, stand=stand, run=run, unit=unit, argv=[str(git_exe), *args],
                                         path_dirs=[Path(git_exe).parent], parent_env=parent_env, native=native, fs=fs)
        problems.extend(step_problems(unit, chk))
        return rc, out, err

    rc, out, _ = step("version", ["--version"])
    facts["git_version"] = out.decode("utf-8", "replace").strip() if rc == 0 else None
    rc, out, err = step("revparse", [*gd, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}"])
    got = out.decode("ascii", "replace").strip()
    if rc != 0:
        problems.append(f"G3 {pin['repo']}: pin_commit_absent: {err.decode('utf-8', 'replace')[-120:].strip()}")
        return facts, problems
    if not _SHA40.fullmatch(got) or got != rev:
        problems.append(f"G4 {pin['repo']}: resolved_not_the_pin: {got[:40]!r}")
        return facts, problems
    facts["resolved"] = got
    rc, out, _ = step("head", [*gd, "rev-parse", "HEAD"])
    facts["head_at_fetch"] = out.decode("ascii", "replace").strip() if rc == 0 else None
    rc, _, err = step("fsck", [*gd, "fsck", "--strict"])
    if rc != 0:
        problems.append(f"G5 {pin['repo']}: fsck_failed: {err.decode('utf-8', 'replace')[-120:].strip()}")
        return facts, problems
    rc, out, _ = step("lstree", [*gd, "ls-tree", "-r", "--full-tree", rev])
    if rc != 0 or not out:
        problems.append(f"G6 {pin['repo']}: ls_tree_failed")
        return facts, problems
    listing = canonical_listing(out)
    work.mkdir(parents=True, exist_ok=True)
    (work / "ls-tree.txt").write_bytes(listing)
    facts["listing_sha256"], facts["listing_bytes"] = hashlib.sha256(listing).hexdigest(), len(listing)
    tar = work / (bare_name(pin)[:-4] + ".tar")
    rc, _, _ = step("archive", [*gd, "archive", "--format=tar", "-o", str(tar), rev])
    if rc != 0 or not tar.is_file():
        problems.append(f"G6 {pin['repo']}: archive_failed")
        return facts, problems
    facts["tar_sha256"] = hashlib.sha256(tar.read_bytes()).hexdigest()
    return facts, problems


# ── the window runner (one GO each) ─────────────────────────────────────────────────────────────────────

def _file_sha(path: Path) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def run_pin_window(c, L, F, *, window: str, run: str, python: Path, via_port: int, parent_env, native=None, fs=None,
                   child_env_extra: dict | None = None, disc: Discovery | None = None, pins: dict | None = None,
                   manifest: dict | None = None, url_licence: tuple | None = None, git_exe: Path = GIT_EXE,
                   ca_bundle: Path = GIT_CA_BUNDLE, ssl_backend: str | None = "openssl", git_url: str | None = None,
                   issuer_orgs=PUBLIC_ISSUER_ORGS, volume: Path | None = None, need_bytes_override: int | None = None) -> dict:
    """One window, then its placement and its fill inputs. A plan-time stop starts nothing; every other problem is
    named in the result, and pin_fill.json is written only when there is none. Nothing raises past here but a
    refusal of the window itself (fetch_a3.WindowRefused: a used run label, the disk floor). ``need_bytes_override`` is for
    tests only (a small temp volume, as need_bytes_override); a real window keeps the plan's floor."""
    disc = disc if disc is not None else load_discovery(c.runs_root)
    pins = pins if pins is not None else CP.PINS
    manifest = manifest if manifest is not None else MANIFEST
    hf_hub, pins_root = c.polygon_root / "hf_cache" / "hub", c.runs_root / "_pins"
    arm = (manifest["windows"][window].get("arms") or ["fetch"])[0]
    try:
        if window == "a3-git":
            names = list(manifest["windows"][window]["pins"])
            if sorted(names) != sorted(n for n, p in pins.items() if p["window"] == window) or len(names) != 1:
                raise Stop("S8", window, f"the manifest's pins {names} are not the table's one git pin")
            pin = pins[names[0]]
            _revision(pin)
            if not Path(ca_bundle).is_file():
                raise Stop("S12", window, f"git_ca_bundle_missing: {ca_bundle}")
            jobs = [git_probe_job(pin), git_clone_job(pin, git_exe=git_exe, ca_bundle=ca_bundle, ssl_backend=ssl_backend,
                                                      url=git_url)]
            plan = Plan(window, list(manifest["windows"][window]["hosts"]), arm, [], jobs, 0, need_bytes(0),
                        {names[0]: licence_found(disc, pin)})
        else:
            plan = plan_window(window, disc=disc, pins=pins, manifest=manifest, hf_hub=hf_hub, pins_root=pins_root,
                               unit_root=c.runs_root / f"_fetch.{window}" / run / arm, url_licence=url_licence)
    except Stop as s:
        return {"window": window, "run": run, "started": False, "problems": [str(s)]}
    pre = {}
    if window == "a3-git":
        pre = {"git_exe": str(git_exe), "git_exe_sha256": _file_sha(Path(git_exe).parent.parent / "mingw64" / "bin" / "git.exe")
               or _file_sha(git_exe), "ca_bundle": str(ca_bundle), "ca_bundle_sha256": _file_sha(ca_bundle)}
    rec = F.run_child_window(c, L, window=window, hosts=plan.hosts, jobs=plan.jobs, python=python, via_port=via_port,
                             run=run, parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=plan.need_bytes if need_bytes_override is None else need_bytes_override, volume=volume,
                             arm=plan.arm)
    base = c.runs_root / "_fetch" / window / run
    result = {"window": window, "run": run, "started": True, "window_problems": list(rec["problems"]), **pre}
    if window != "a3-git":
        placed = place_window(rec, plan, pins=pins, polygon_root=c.polygon_root, hf_hub=hf_hub, pins_root=pins_root,
                              place_path=base / "place_record.json", issuer_orgs=issuer_orgs)
        inputs, problems = fill_inputs(plan, placed, pins)
    else:
        inputs, problems = _git_after(c, L, rec, plan, pins=pins, disc=disc, run=run, parent_env=parent_env, native=native,
                                      fs=fs, git_exe=git_exe, via_port=via_port, pins_root=pins_root, base=base,
                                      issuer_orgs=issuer_orgs, pre=pre)
    result["problems"] = problems
    if not problems:
        _write_json(base / "pin_fill.json", {"window": window, "run": run, "pins": inputs, **pre})
        result["pin_fill"] = str(base / "pin_fill.json")
    return result


def _git_after(c, L, rec: dict, plan: Plan, *, pins, disc, run, parent_env, native, fs, git_exe, via_port, pins_root,
               base: Path, issuer_orgs, pre: dict) -> tuple[dict, list[str]]:
    """a3-git after its window: the precheck (the probe's issuer), the catcher's tunnel for the git arm (G2), the
    clone (G1), then git_verify (G3-G6) offline, then the bare clone, its listing and its tar placed."""
    name = next(iter(plan.licences))
    pin = pins[name]
    problems = precheck(rec, plan, issuer_orgs=issuer_orgs)
    if not any(x.get("arm") == GIT_ARM and x.get("host") == "github.com" and x.get("tunnelled")
               and x.get("via") == f"127.0.0.1:{via_port}" for x in rec.get("catcher") or []):
        problems.append(f"G2 {pin['repo']}: no_catcher_connect for {GIT_ARM} through the hop")
    clone = next((j for j in rec.get("jobs") or [] if (j.get("job") or {}).get("child") == "git"), None)
    bare = Path(clone["unit"]) / bare_name(pin) if clone else None
    if clone is None or clone.get("rc") != 0 or not (bare and bare.is_dir()):
        problems.append(f"G1 {pin['repo']}: clone_failed: {(clone or {}).get('stderr_tail', '')[-160:]}")
    if problems:
        _write_json(base / "place_record.json", {"window": plan.window, "problems": problems, **pre})
        return {}, problems
    facts, gprobs = git_verify(c, L, bare=bare, pin=pin, run=run, parent_env=parent_env, native=native, fs=fs,
                               git_exe=git_exe, work=base / "verify")
    out = {"window": plan.window, "facts": facts, "problems": gprobs, "issuers": host_issuers(rec), **pre}
    if gprobs:
        _write_json(base / "place_record.json", out)
        return {}, gprobs
    dest_dir = Path(pins_root) / "git" / pin["revision"]
    listing = CP.location(name, hf_hub=c.polygon_root / "hf_cache" / "hub", pins_root=pins_root, pins=pins)
    moves = [(bare, dest_dir / bare_name(pin)), (base / "verify" / "ls-tree.txt", listing),
             (base / "verify" / (bare_name(pin)[:-4] + ".tar"), dest_dir / (bare_name(pin)[:-4] + ".tar"))]
    for src, dest in moves:
        if dest.exists():
            out["problems"].append(f"P12 {name}: dest_occupied: {dest} - never overwritten")
            _write_json(base / "place_record.json", out)
            return {}, out["problems"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src, dest in moves:
        os.replace(src, dest)
    if _file_sha(listing) != facts["listing_sha256"]:
        out["problems"].append(f"P7 {name}: hash_mismatch: the placed listing is not the verified one")
        _write_json(base / "place_record.json", out)
        return {}, out["problems"]
    out["placed"] = {"bare": str(dest_dir / bare_name(pin)), "listing": str(listing)}
    _write_json(base / "place_record.json", out)
    lic, src = plan.licences[name]
    if lic is None:
        return {}, [f"F1 {name}: licence_found_missing ({src})"]
    import copy  # noqa: PLC0415
    try:
        CP.fill(name, revision=facts["resolved"], sha256=facts["listing_sha256"], size=facts["listing_bytes"],
                licence_found=lic, pins=copy.deepcopy(pins))
    except CP.PinRefused as r:
        return {}, [f"F2 {name}: fill_refused: {r}"]
    return {name: {"revision": facts["resolved"], "sha256": facts["listing_sha256"], "bytes": facts["listing_bytes"],
                   "licence_found": lic, "licence_source": src, "tar_sha256": facts["tar_sha256"],
                   "git_version": facts["git_version"], "head_at_fetch": facts["head_at_fetch"]}}, []


TIKTOKEN_METADATA = Path(r"D:\Coding\_nevertwice_polygon\mem0_eval\.venv\Lib\site-packages\tiktoken-0.14.0.dist-info\METADATA")


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="one A3 pin window (a3-hf, a3-github, a3-tiktoken, a3-git), then place and fill")
    ap.add_argument("--window", required=True, choices=["a3-hf", "a3-github", "a3-tiktoken", "a3-git"])
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
    url_licence = metadata_licence(TIKTOKEN_METADATA) if args.window == "a3-tiktoken" else None
    res = run_pin_window(c, L, F, window=args.window, run=args.run, python=Path(args.python), via_port=via,
                         parent_env=os.environ, url_licence=url_licence, volume=Path("D:/"))
    print(json.dumps(res, indent=1, default=str))
    return 0 if not res["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
