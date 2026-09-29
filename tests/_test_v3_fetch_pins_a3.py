#!/usr/bin/env python3
"""PREREG-V3 plan step A3.f/A3.g (.loop/A3F-PLAN-2026-09-26.md): research/v3/fetch_pins_a3.py, offline.

Part 1, the pure layer - against a fake pair of discovery records built here:

* the discovery records are read only when their sha256 is the pinned one (S1);
* a pin's expectation comes from the tree the discovery record holds AT the pin's revision: an HF LFS file is its
  sha256 and size, any other file its git blob sha1 and size; a tree at another revision (S4), a truncated tree (S3)
  or a file missing from the tree (S2) stops the plan by name;
* the licence found is the discovery's own: an HF card's cardData.license, a GitHub repository's spdx id; NOASSERTION
  or none is no licence (the LoCoMo text rule reads the licence file instead: CC BY-NC 4.0, never ND);
* every URL carries the 40-hex revision, never a branch (S5); a file over 10 GiB (S6) or a path too long for Windows
  (S7) stops; the disk floor is max(100 GiB, 3 x the window's total).

    python tests/_test_v3_fetch_pins_a3.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CP = _load("v3_corpus_pin_fp", ROOT / "research" / "v3" / "corpus_pin_v3.py")
P = _load("v3_fetch_pins", ROOT / "research" / "v3" / "fetch_pins_a3.py")
# The table fetch_pins_a3 itself loaded (its own module instance, not CP): the real-table guards below watch this one,
# from before the first check.
REAL_PINS_BEFORE = copy.deepcopy(P.CP.PINS)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def stopped(fn, code: str, words: str = "") -> bool:
    """``fn`` raises P.Stop with ``code`` - and, when ``words`` is given, a message naming them."""
    try:
        fn()
    except P.Stop as e:
        return e.code == code and words in str(e)
    except Exception:  # noqa: BLE001 - another exception is a failure too
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetchpins_"))
REV_HF, REV_GH, REV_M0 = "a" * 40, "b" * 40, "c" * 40
LFS_SHA, BLOB = "d" * 64, "e" * 40


def _is40(s) -> bool:
    return isinstance(s, str) and len(s) == 40 and all(ch in "0123456789abcdef" for ch in s)


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(obj).encode("utf-8") if not isinstance(obj, bytes) else obj)


def fake_discovery(base: Path, *, hf_rev=REV_HF, gh_echo=REV_GH, truncated=False, spdx="MIT", card_licence="mit"):
    """A d1 (jobs 0-3) and a d2 (jobs 0-2) in the layout the discovery plans save."""
    units = {}
    for rec, n in (("d1", 4), ("d2", 3)):
        for j in range(n):
            units[(rec, j)] = base / rec / f"j{j}"
            units[(rec, j)].mkdir(parents=True, exist_ok=True)
    write(units[("d1", 0)] / "meta/datasets/org__ds/revision.json", {"sha": hf_rev, "cardData": {"license": card_licence}})
    write(units[("d1", 1)] / "meta/datasets/org__ds/tree.json", [
        {"type": "file", "path": "data/x.parquet", "oid": "1" * 40, "size": 1000, "lfs": {"oid": LFS_SHA, "size": 1000}},
        {"type": "file", "path": "tokenizer_config.json", "oid": BLOB, "size": 44}])
    write(units[("d1", 2)] / "gh/owner__repo/repo.json", {"full_name": "owner/repo", "license": {"spdx_id": spdx}})
    write(units[("d1", 3)] / "gh/owner__repo/tree.json", {"sha": gh_echo, "truncated": truncated, "tree": [
        {"path": "src/eval.py", "type": "blob", "sha": BLOB, "size": 321}, {"path": "src", "type": "tree", "sha": "9" * 40}]})
    write(units[("d2", 2)] / "gh/mem0ai__mem0/tree_parent.json", {"sha": REV_M0, "truncated": False, "tree": [
        {"path": "evaluation/prompts.py", "type": "blob", "sha": "f" * 40, "size": 77}]})
    write(units[("d2", 0)] / "gh/AMA-Bench__AMA-Hub/repo.json", {"full_name": "AMA-Bench/AMA-Bench", "license": {"spdx_id": "MIT"}})
    tree_req = {"id": "tree:datasets:org/ds", "url": f"https://huggingface.co/api/datasets/org/ds/tree/{hf_rev}?recursive=1",
                "save": "meta/datasets/org__ds/tree.json"}
    d1 = {"jobs": [{"index": j, "unit": str(units[("d1", j)]), "job": {"requests": [tree_req] if j == 1 else []},
                    "summary": []} for j in range(4)]}
    d2 = {"jobs": [{"index": j, "unit": str(units[("d2", j)]), "job": {"requests": []}, "summary": []} for j in range(3)]}
    shas = {}
    for name, rec in (("d1", d1), ("d2", d2)):
        f = base / "_fetch" / "a3-discovery" / name / "record.json"
        write(f, (json.dumps(rec) + "\n").encode("utf-8"))
        shas[name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return shas


def pin(source, repo, path, revision, licence="MIT", role="evaluation", window="a3-hf"):
    return {"role": role, "stands": ["S1"], "source": source, "repo": repo, "path": path, "revision": revision,
            "revision_from": "a3-discovery d1 x", "licence": licence, "window": window, "prereg": 1, "note": "",
            "cross_check": None, "sha256": None, "bytes": None, "licence_found": None}


base = TMP / "disc"
SHAS = fake_discovery(base)
print("\n- the discovery records, pinned by sha256 (S1) -")
D = P.load_discovery(base, shas=SHAS)
check("the records load when their sha256 is the pinned one", set(D.records) == {"d1", "d2"})
bad = dict(SHAS, d2="0" * 64)
check("S1: a record one byte off (another sha256) is refused by name",
      stopped(lambda: P.load_discovery(base, shas=bad), "S1", "a3-discovery d2"))
check("S1: a missing record is refused by name", stopped(lambda: P.load_discovery(TMP / "none", shas=SHAS), "S1", "missing"))
check("the real tool pins the two records the auditor received",
      P.DISCOVERY_SHAS == {"d1": CP.DISCOVERY_D1, "d2": CP.DISCOVERY_D2})

print("\n- expectations from the tree at the pin's revision (S2-S4) -")
hf_lfs = pin("hf-dataset", "org/ds", "data/x.parquet", REV_HF)
hf_small = pin("hf-dataset", "org/ds", "tokenizer_config.json", REV_HF, role="tokenizer")
gh = pin("github", "owner/repo", "src/eval.py", REV_GH, role="prompt", window="a3-github")
check("an HF LFS file expects its sha256 and size", P.expectation(D, hf_lfs) == {"sha256": LFS_SHA, "size": 1000},
      str(P.expectation(D, hf_lfs)))
check("an HF non-LFS file expects its git blob sha1 and size", P.expectation(D, hf_small) == {"git_blob_sha1": BLOB, "size": 44})
check("a GitHub file expects its git blob sha1 and size", P.expectation(D, gh) == {"git_blob_sha1": BLOB, "size": 321})
check("mem0 is read from d2's tree at the deletion's parent, never d1's head tree",
      P.tree_source({**gh, "repo": "mem0ai/mem0", "revision": REV_M0}) == ("d2", 2, "gh/mem0ai__mem0/tree_parent.json")
      and P.expectation(D, {**gh, "repo": "mem0ai/mem0", "path": "evaluation/prompts.py", "revision": REV_M0})
      == {"git_blob_sha1": "f" * 40, "size": 77})
check("AMA-Bench and BEAM are read from d2's trees (AMA-Bench under the old AMA-Hub name)",
      P.tree_source({**gh, "repo": "AMA-Bench/AMA-Bench"}) == ("d2", 1, "gh/AMA-Bench__AMA-Hub/tree.json")
      and P.tree_source({**gh, "repo": "mohammadtavakoli78/BEAM"}) == ("d2", 1, "gh/mohammadtavakoli78__BEAM/tree.json"))
check("S2: a file missing from the tree stops by name",
      stopped(lambda: P.expectation(D, {**gh, "path": "src/none.py"}), "S2", "src/none.py"))
check("S2: a tree entry that is not a file stops", stopped(lambda: P.expectation(D, {**gh, "path": "src"}), "S2"))
check("S4: a GitHub tree at another revision stops (its echoed sha is not the pin's)",
      stopped(lambda: P.expectation(D, {**gh, "revision": "0" * 40}), "S4"))
check("S4: an HF pin at another revision than the record's stops",
      stopped(lambda: P.expectation(D, {**hf_lfs, "revision": "0" * 40}), "S4"))
b2 = TMP / "disc_trunc"
S2_ = fake_discovery(b2, truncated=True)
check("S3: a truncated tree stops", stopped(lambda: P.expectation(P.load_discovery(b2, shas=S2_), gh), "S3"))
b3 = TMP / "disc_hfrev"
S3_ = fake_discovery(b3, hf_rev=REV_HF)
D3 = P.load_discovery(b3, shas=S3_)
D3.records["d1"]["jobs"][1]["job"]["requests"][0]["url"] = "https://huggingface.co/api/datasets/org/ds/tree/main?recursive=1"
check("S4: an HF tree fetched at a branch name, not the revision, stops", stopped(lambda: P.expectation(D3, hf_lfs), "S4"))

print("\n- the licence found, from the discovery -")
check("an HF pin's licence is its card's cardData.license", P.licence_found(D, hf_lfs) == ("mit", "d1 card org/ds"))
check("a GitHub pin's licence is the repository's spdx id", P.licence_found(D, gh) == ("MIT", "d1 repo owner/repo"))
check("AMA-Bench's licence is read from d2 (the repository the redirect led to)",
      P.licence_found(D, {**gh, "repo": "AMA-Bench/AMA-Bench"}) == ("MIT", "d2 repo AMA-Bench/AMA-Bench"))
b4 = TMP / "disc_noassert"
S4_ = fake_discovery(b4, spdx="NOASSERTION")
check("NOASSERTION is no licence found", P.licence_found(P.load_discovery(b4, shas=S4_), gh) == (None, "d1 repo owner/repo"))
BYNC = "Attribution-NonCommercial 4.0 International Public License\n..."
check("the LoCoMo text rule: CC BY-NC 4.0 text is CC-BY-NC-4.0", P.locomo_licence(BYNC) == "CC-BY-NC-4.0")
check("the LoCoMo text rule: an ND text is refused, never read as BY-NC",
      P.locomo_licence("Attribution-NonCommercial-NoDerivatives 4.0 International") is None
      and P.locomo_licence("Attribution-NonCommercial 4.0 International ... NoDerivs") is None)
check("the LoCoMo text rule: any other text is no licence", P.locomo_licence("MIT License") is None)

print("\n- URLs, sizes, paths, the floor (S5-S7) -")
check("an HF URL carries the 40-hex revision",
      P.hf_url(hf_lfs) == f"https://huggingface.co/datasets/org/ds/resolve/{REV_HF}/data/x.parquet"
      and P.hf_url({**hf_small, "source": "hf-model", "repo": "BAAI/bge-m3"})
      == f"https://huggingface.co/BAAI/bge-m3/resolve/{REV_HF}/tokenizer_config.json")
check("a GitHub URL is raw.githubusercontent.com at the commit",
      P.raw_url(gh) == f"https://raw.githubusercontent.com/owner/repo/{REV_GH}/src/eval.py")
check("S5: a branch name, a short sha or an upper-case sha is refused in every URL",
      all(stopped(lambda r=r, f=f: f({**(hf_lfs if f is P.hf_url else gh), "revision": r}), "S5")
          for r in ("main", "a" * 39, "A" * 40) for f in (P.hf_url, P.raw_url)))
check("a path with a space or a non-ASCII character is quoted, its slashes kept",
      P.raw_url({**gh, "path": "a b/ü.py"}) == f"https://raw.githubusercontent.com/owner/repo/{REV_GH}/a%20b/%C3%BC.py")
check("S6: a file over 10 GiB stops", stopped(lambda: P.size_ok("big", 10 * P.GIB + 1), "S6") and P.size_ok("ok", 10 * P.GIB))
check("S7: a path of 240 characters or more stops", stopped(lambda: P.path_ok("p", "x" * 240), "S7") and P.path_ok("p", "x" * 239))
check("the floor is max(100 GiB, 3 x the window's total)",
      P.need_bytes(5 * P.GIB) == 100 * P.GIB and P.need_bytes(40 * P.GIB) == 120 * P.GIB)
check("the floor is the manifest's rule, read from it",
      P.FLOOR_GIB == json.loads((ROOT / "research/v3/fetch_manifest.json").read_text(encoding="utf-8"))["disk"]["floor_gb"]
      and P.MULTIPLE == 3 and P.MAX_FILE == 10 * P.GIB)
print("\n- the plan of a window (S8, S9, dedupe, jobs, floor) -")
HUB, PR, UR = TMP / "hub", TMP / "pins", TMP / "runs" / "_fetch.a3-hf" / "r1" / "fetch"
T = {"hf_lfs": hf_lfs, "hf_small": hf_small, "hf_twin": dict(hf_lfs, role="bracket"),
     "gh_a": gh, "tk": {**pin("url", None, "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken", None,
                              role="tokenizer", window="a3-tiktoken"), "cross_check": {"sha256": "7" * 64}}}
MANI = {"windows": {"a3-hf": {"hosts": ["huggingface.co", "us.aws.cdn.hf.co"], "pins": ["hf_lfs", "hf_small", "hf_twin"],
                              "max_redirects": 1},
                    "a3-github": {"hosts": ["raw.githubusercontent.com"], "pins": ["gh_a"], "max_redirects": 0},
                    "a3-tiktoken": {"hosts": ["openaipublic.blob.core.windows.net"], "pins": ["tk"], "max_redirects": 0}}}
plan = P.plan_window("a3-hf", disc=D, pins=T, manifest=MANI, hf_hub=HUB, pins_root=PR, unit_root=UR)
check("a3-hf: two pins on one file are one item with both names; each item has its expectation and destination",
      [(it.names, it.expect) for it in plan.items] == [(["hf_lfs", "hf_twin"], {"sha256": LFS_SHA, "size": 1000}),
                                                      (["hf_small"], {"git_blob_sha1": BLOB, "size": 44})]
      and plan.items[0].dest == HUB / "datasets--org--ds" / "snapshots" / REV_HF / "data" / "x.parquet",
      str([(it.names, it.save) for it in plan.items]))
check("a3-hf: one job for the repository, the window's hosts, one redirect, every request carrying its expectation",
      len(plan.jobs) == 1 and plan.jobs[0]["hosts"] == ["huggingface.co", "us.aws.cdn.hf.co"] and plan.jobs[0]["max_redirects"] == 1
      and [r["expect"] for r in plan.jobs[0]["requests"]] == [it.expect for it in plan.items]
      and all(r["max_bytes"] == it.expect["size"] for r, it in zip(plan.jobs[0]["requests"], plan.items)), str(plan.jobs))
check("the floor and the licences come with the plan", plan.total == 1044 and plan.need_bytes == 100 * P.GIB
      and plan.licences["hf_lfs"] == ("mit", "d1 card org/ds") and plan.arm == "fetch")
check("the Q-A3F-11 timeout: max(3600 s, the bytes at 256 KiB/s)",
      P.job_timeout(10) == 3600 and P.job_timeout(2_737_100_077) == 10442 and plan.jobs[0]["timeout_s"] == 3600)
check("S8: a manifest whose pins are not the table's stops",
      stopped(lambda: P.plan_window("a3-hf", disc=D, pins=T, manifest={"windows": {"a3-hf": {**MANI["windows"]["a3-hf"],
              "pins": ["hf_lfs"]}}}, hf_hub=HUB, pins_root=PR, unit_root=UR), "S8"))
check("S9: a URL off the window's hosts stops",
      stopped(lambda: P.plan_window("a3-github", disc=D, pins=T, manifest={"windows": {"a3-github": {
          **MANI["windows"]["a3-github"], "hosts": ["api.github.com"]}}}, hf_hub=HUB, pins_root=PR, unit_root=UR), "S9"))
check("S7: a unit path too long for Windows stops before anything starts",
      stopped(lambda: P.plan_window("a3-hf", disc=D, pins=T, manifest=MANI, hf_hub=HUB, pins_root=PR,
                                    unit_root=TMP / ("u" * 220)), "S7"))
gp = P.plan_window("a3-github", disc=D, pins=T, manifest=MANI, hf_hub=HUB, pins_root=PR, unit_root=UR)
check("a3-github: raw.githubusercontent.com at the commit, no redirect, placed under _pins/github/<commit>",
      gp.jobs[0]["max_redirects"] == 0 and gp.jobs[0]["requests"][0]["url"].startswith("https://raw.githubusercontent.com/")
      and gp.items[0].dest == PR / "github" / REV_GH / "src" / "eval.py")
tp = P.plan_window("a3-tiktoken", disc=D, pins=T, manifest=MANI, hf_hub=HUB, pins_root=PR, unit_root=UR)
check("a3-tiktoken: the committed cross_check is the expectation, the file placed under _pins/url/<that sha256>",
      tp.items[0].expect == {"sha256": "7" * 64} and tp.items[0].dest == PR / "url" / ("7" * 64) / "cl100k_base.tiktoken"
      and tp.jobs[0]["requests"][0]["max_bytes"] == P.URL_MAX_BYTES)
md = TMP / "METADATA"
md.write_text("Metadata-Version: 2.4\nName: tiktoken\nVersion: 0.14.0\nLicense: MIT License\n", encoding="utf-8")
check("Q-A3F-4: the tiktoken licence is its package METADATA's, with the file's sha256 as its source",
      P.metadata_licence(md) == ("MIT", f"METADATA {md} sha256 {hashlib.sha256(md.read_bytes()).hexdigest()}")
      and P.plan_window("a3-tiktoken", disc=D, pins=T, manifest=MANI, hf_hub=HUB, pins_root=PR, unit_root=UR,
                        url_licence=P.metadata_licence(md)).licences["tk"][0] == "MIT"
      and tp.licences["tk"] == (None, "no METADATA given"))
md2 = TMP / "METADATA2"
md2.write_text("License: Apache 2.0\n", encoding="utf-8")
check("a METADATA licence other than MIT is no licence found", P.metadata_licence(md2)[0] is None)
check("S10: a URL pin with no committed cross_check stops",
      stopped(lambda: P.plan_window("a3-tiktoken", disc=D, pins=dict(T, tk={**T["tk"], "cross_check": None}), manifest=MANI,
                                    hf_hub=HUB, pins_root=PR, unit_root=UR), "S10"))
ev = dict(T, gh_a={**gh, "repo": "mem0ai/mem0", "path": "evaluation/prompts.py", "revision": REV_M0})
ep = P.plan_window("a3-github", disc=D, pins=ev, manifest=MANI, hf_hub=HUB, pins_root=PR, unit_root=UR)
check("a repository with a licence-evidence pin takes its licence from that file, never its current spdx",
      ep.licences["gh_a"] == (None, "evidence mem0_licence"))
check("the evidence rules: LoCoMo CC BY-NC, mem0 Apache-2.0 - titles and version, never ND",
      P.EVIDENCE_RULES["mem0_licence"]("Apache License\n Version 2.0, January 2004\n") == "Apache-2.0"
      and P.EVIDENCE_RULES["mem0_licence"]("Apache License Version 1.1") is None
      and P.EVIDENCE_RULES["locomo_licence_txt"] is P.locomo_licence)

print("\n- after the window: re-hash, place, fill (P1-P15, F1-F3) -")
BODY_LFS, BODY_SMALL, BODY_GH = b"parquet bytes " * 50, b'{"tok": 1}\n', b"print('eval')\n"
LIC_TXT = b"Apache License\n Version 2.0, January 2004\n"


def blob1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def real_discovery(base: Path, *, card_licence="mit"):
    """A d1/d2 pair whose trees carry the real hashes of the bodies above."""
    shas = fake_discovery(base, card_licence=card_licence)
    d1 = json.loads((base / "_fetch/a3-discovery/d1/record.json").read_bytes())
    u = {j: Path(d1["jobs"][j]["unit"]) for j in range(4)}
    write(u[1] / "meta/datasets/org__ds/tree.json", [
        {"type": "file", "path": "data/x.parquet", "oid": blob1(BODY_LFS), "size": len(BODY_LFS),
         "lfs": {"oid": hashlib.sha256(BODY_LFS).hexdigest(), "size": len(BODY_LFS)}},
        {"type": "file", "path": "tokenizer_config.json", "oid": blob1(BODY_SMALL), "size": len(BODY_SMALL)}])
    d2 = json.loads((base / "_fetch/a3-discovery/d2/record.json").read_bytes())
    write(Path(d2["jobs"][2]["unit"]) / "gh/mem0ai__mem0/tree_parent.json", {"sha": REV_M0, "truncated": False, "tree": [
        {"path": "evaluation/prompts.py", "type": "blob", "sha": blob1(BODY_GH), "size": len(BODY_GH)},
        {"path": "LICENSE", "type": "blob", "sha": blob1(LIC_TXT), "size": len(LIC_TXT)}]})
    return shas


def fake_record(plan, bodies: dict, base: Path, *, hosts=None, issuer="Amazon", problems=(), summary_edit=None):
    """A window record as run_child_window writes it, with each item's body saved in one unit."""
    unit = base / "unit_j0"
    summary = []
    for it in plan.items:
        body = bodies[it.names[0]]
        f = unit / it.save
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
        r = {"id": f"pin:{it.names[0]}", "ok": True, "final_host": "huggingface.co", "issuer_o": issuer,
             "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        summary.append({**r, **((summary_edit or {}).get(it.names[0]) or {})})
    return {"hosts": list(hosts if hosts is not None else plan.hosts), "problems": list(problems),
            "jobs": [{"index": 0, "rc": 0, "unit": str(unit), "summary": summary}]}


POLY = TMP / "poly"
HUB2, PR2 = POLY / "hf_cache" / "hub", POLY / "runs" / "_pins"
bp = TMP / "disc_real"
DR = P.load_discovery(bp, shas=real_discovery(bp))
T2 = {"hf_lfs": hf_lfs, "hf_small": hf_small, "hf_twin": dict(hf_lfs, role="bracket")}
BOD = {"hf_lfs": BODY_LFS, "hf_small": BODY_SMALL}


def placed(tag, *, pins=T2, bodies=BOD, rec_kw=None, before=None, hasher=None, disc=DR, window="a3-hf", man=MANI):
    base = TMP / "place" / tag
    base.mkdir(parents=True)
    pl = P.plan_window(window, disc=disc, pins=pins, manifest=man, hf_hub=base / "poly/hf_cache/hub",
                       pins_root=base / "poly/runs/_pins", unit_root=base / "units")
    rec = fake_record(pl, bodies, base, **(rec_kw or {}))
    if before:
        before(pl, rec, base)
    out = P.place_window(rec, pl, pins=pins, polygon_root=base / "poly", hf_hub=base / "poly/hf_cache/hub",
                         pins_root=base / "poly/runs/_pins", place_path=base / "place_record.json",
                         **({"hasher": hasher} if hasher else {}))
    return pl, out, base


pl, out, base = placed("ok")
dests = [Path(e["dest"]) for e in out["entries"].values()]
check("a clean window places every file at its destination, verified, and names nothing",
      out["problems"] == [] and all(e["state"] == "verified-placed" for e in out["entries"].values())
      and all(d.is_file() for d in dests) and dests[0].read_bytes() == BODY_LFS, str(out["problems"]))
ref = base / "poly/hf_cache/hub/datasets--org--ds/refs/main"
check("HF: refs/main holds the revision", ref.is_file() and ref.read_text(encoding="ascii") == REV_HF)
check("the place record on disk is the returned one",
      json.loads((base / "place_record.json").read_bytes())["entries"] == json.loads(json.dumps(out["entries"])))
inputs, probs = P.fill_inputs(pl, out, T2)
check("fill: every pin of every placed file gets revision, sha256, bytes and the discovery's licence; the dry run passes",
      probs == [] and sorted(inputs) == ["hf_lfs", "hf_small", "hf_twin"]
      and inputs["hf_lfs"] == {"revision": REV_HF, "sha256": hashlib.sha256(BODY_LFS).hexdigest(), "bytes": len(BODY_LFS),
                               "licence_found": "mit", "licence_source": "d1 card org/ds"}, str((probs, inputs.get("hf_lfs"))))
check("fill never touches the real table: its values are the declared ones or FILLED's, nothing else",
      all(P.CP.PINS[n]["sha256"] == (P.CP.FILLED.get(n) or {}).get("sha256", P.CP.PINS_DECLARED[n]["sha256"]) for n in P.CP.PINS))
_, out2, base2 = placed("again", before=lambda pl, rec, b: [
    (Path(it.dest).parent.mkdir(parents=True, exist_ok=True), Path(it.dest).write_bytes(BOD[it.names[0]])) for it in pl.items])
check("P12: an identical file already in place is 'already-placed'", out2["problems"] == []
      and all(e["state"] == "already-placed" for e in out2["entries"].values()), str(out2))
_, out3, _ = placed("occupied", before=lambda pl, rec, b: (Path(pl.items[0].dest).parent.mkdir(parents=True, exist_ok=True),
                                                          Path(pl.items[0].dest).write_bytes(b"other")))
check("P12: other bytes at the destination stop - never overwritten",
      any(x.startswith("P12 ") for x in out3["problems"]) and Path(out3["entries"]["hf_lfs"]["dest"]).read_bytes() == b"other")
_, o, _ = placed("dirty", rec_kw={"problems": ["request x: status 404"]})
check("P1: a window with a problem places nothing", o["problems"] == ["P1 window_not_clean: request x: status 404"] and o["entries"] == {})
_, o, _ = placed("hosts", rec_kw={"hosts": ["huggingface.co"]})
check("P2: a record whose hosts are not the plan's places nothing", any(x.startswith("P2 ") for x in o["problems"]) and o["entries"] == {})
_, o, _ = placed("noissuer", rec_kw={"issuer": None})
check("P3: an unrecorded issuer places nothing", any(x.startswith("P3 issuer_unrecorded") for x in o["problems"]))
_, o, _ = placed("mitm", rec_kw={"issuer": "Evil Proxy CA"})
for org in sorted(P.PUBLIC_ISSUER_ORGS):
    _, o, _ = placed(f"org{len(org)}{org[:3]}", rec_kw={"issuer": org})
    check(f"P3: the public organisation {org!r} passes, matched exactly", o["problems"] == [], str(o["problems"]))
for label, org in (("a longer name", "Amazon Web Services"), ("an empty O", "")):
    _, o, _ = placed(f"orgbad{len(org)}", rec_kw={"issuer": org})
    check(f"P3: {label} is not a public organisation - host, O and CN named",
          any(x.startswith("P3 issuer_not_public: huggingface.co presented O") and repr(org) in x and "CN" in x
              for x in o["problems"]), str(o["problems"]))
check("the place record carries each host's issuer as [O, CN]",
      out["issuers"] == {"huggingface.co": [["Amazon", None]]}, str(out.get("issuers")))
_, o, _ = placed("mitm2", rec_kw={"issuer": "Evil Proxy CA"})
check("P3: an issuer outside the public set places nothing",
      any(x.startswith("P3 issuer_not_public") and "Evil Proxy CA" in x for x in o["problems"]) and o["entries"] == {})
_, o, b9 = placed("tampered", before=lambda pl, rec, b: (b / "unit_j0" / pl.items[0].save).write_bytes(b"X" * len(BODY_LFS)))
check("P7: bytes changed after the window are named by the re-hash, and not placed",
      any(x.startswith("P7 hf_lfs: hash_mismatch") for x in o["problems"]) and o["entries"]["hf_lfs"]["state"] == "unverified"
      and not Path(o["entries"]["hf_lfs"]["dest"]).exists())
_, o, _ = placed("short", before=lambda pl, rec, b: (b / "unit_j0" / pl.items[0].save).write_bytes(BODY_LFS[:-1]))
check("P6: a size that is not the expected one stops", any(x.startswith("P6 ") for x in o["problems"]))
_, o, _ = placed("claim", rec_kw={"summary_edit": {"hf_lfs": {"sha256": "0" * 64}}})
check("P8: a child's claim that differs from the disk stops", any(x.startswith("P8 ") for x in o["problems"]))
def behind_link(pl, rec, b):
    """T1: the download's directory is a junction (Windows, no privilege needed) or a symlink (POSIX) to outside."""
    import subprocess  # noqa: PLC0415
    outside = b / "outside_hf"
    (b / "unit_j0" / "hf").rename(outside)
    link = b / "unit_j0" / "hf"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        os.symlink(outside, link, target_is_directory=True)


import os  # noqa: E402

_, o, b_link = placed("junction", before=behind_link)
check("T1: a download that sits behind a junction or symlink is P5, and nothing is placed from it",
      any(x.startswith("P5 hf_lfs: download_is_link or outside its unit") for x in o["problems"])
      and not Path(o["entries"]["hf_lfs"]["dest"]).exists(), str(o["problems"]))
_, o, _ = placed("gone", before=lambda pl, rec, b: (b / "unit_j0" / pl.items[0].save).unlink())
check("P5: a missing download stops", any(x.startswith("P5 hf_lfs: download_missing") for x in o["problems"]))


def boom(path):
    raise OSError("locked")


_, o, b10 = placed("hashfail", hasher=boom)
on_disk = json.loads((b10 / "place_record.json").read_bytes())
check("a3: the entry is written 'unverified' before any hash - a failed hash leaves it so, on disk, and places nothing",
      any(x.startswith("P9 hf_lfs: hash_failed") for x in o["problems"]) and on_disk["entries"]["hf_lfs"]["state"] == "unverified"
      and not Path(on_disk["entries"]["hf_lfs"]["dest"]).exists())
check("a3: a placement with a problem gives no fill inputs", P.fill_inputs(pl, o, T2)[0] == {})
_, o, _ = placed("outside", before=lambda pl, rec, b: setattr(pl.items[0], "dest", TMP / "repo" / "x.parquet"))
check("P10: a destination outside the polygon (the repo, say) stops and moves nothing",
      any(x.startswith("P10 ") for x in o["problems"]) and not (TMP / "repo" / "x.parquet").exists())


def other_ref(pl, rec, b):
    r = b / "poly/hf_cache/hub/datasets--org--ds/refs/main"
    r.parent.mkdir(parents=True, exist_ok=True)
    r.write_bytes(b"f" * 40)


_, o, b11 = placed("refs", before=other_ref)
check("P15: a refs/main at another revision is left, named, never repointed (Q-A3F-9)",
      any(x.startswith("P15 ") for x in o["problems"])
      and (b11 / "poly/hf_cache/hub/datasets--org--ds/refs/main").read_bytes() == b"f" * 40)
bn = TMP / "disc_nolic"
DN = P.load_discovery(bn, shas=real_discovery(bn, card_licence=None))
pln, on, _ = placed("nolic", disc=DN)
check("F1: a pin with no licence found gives no fill inputs, by name",
      P.fill_inputs(pln, on, T2)[0] == {} and sorted(P.fill_inputs(pln, on, T2)[1])
      == ["F1 hf_lfs: licence_found_missing (d1 card org/ds)", "F1 hf_small: licence_found_missing (d1 card org/ds)",
          "F1 hf_twin: licence_found_missing (d1 card org/ds)"], str(P.fill_inputs(pln, on, T2)))
ba = TMP / "disc_apache"
DA = P.load_discovery(ba, shas=real_discovery(ba, card_licence="apache-2.0"))
pla, oa, _ = placed("mismatch", disc=DA)
check("F2: a found licence the table refuses (Apache for a pin declared MIT) is named, nothing filled",
      P.fill_inputs(pla, oa, T2)[0] == {} and any(x.startswith("F2 hf_lfs: fill_refused") for x in P.fill_inputs(pla, oa, T2)[1]))
TA = dict(T2, hf_twin={**hf_lfs, "role": "bracket", "alias_of": "v2:x", "sha256": hashlib.sha256(BODY_LFS).hexdigest(),
                       "bytes": len(BODY_LFS)})
pl_a, o_a, _ = placed("alias", pins=TA)
inp_a, pr_a = P.fill_inputs(pl_a, o_a, TA)
check("Q-A3F-8: an alias whose bytes are its pinned value is confirmed, never filled",
      pr_a == [] and inp_a["hf_twin"] == {"alias_confirmed": "v2:x", "sha256": hashlib.sha256(BODY_LFS).hexdigest(),
                                          "bytes": len(BODY_LFS)})
TB = dict(TA, hf_twin={**TA["hf_twin"], "sha256": "0" * 64})
pl_b, o_b, _ = placed("aliasbad", pins=TB)
check("F3: an alias whose bytes are not its pinned value is named", any(x.startswith("F3 hf_twin") for x in P.fill_inputs(pl_b, o_b, TB)[1]))
TE = {"gh_p": {**gh, "repo": "mem0ai/mem0", "path": "evaluation/prompts.py", "revision": REV_M0, "licence": "Apache-2.0"},
      "gh_lic": {**gh, "repo": "mem0ai/mem0", "path": "LICENSE", "revision": REV_M0, "licence": "Apache-2.0",
                 "role": "licence-evidence"}}
MANE = {"windows": {"a3-github": {"hosts": ["raw.githubusercontent.com"], "pins": ["gh_p", "gh_lic"], "max_redirects": 0}}}
orig = dict(P.EVIDENCE_RULES)
P.EVIDENCE_RULES["gh_lic"] = P.apache_licence
P.EVIDENCE["mem0ai/mem0"] = "gh_lic"
try:
    pl_e, o_e, _ = placed("evidence", pins=TE, bodies={"gh_p": BODY_GH, "gh_lic": LIC_TXT}, window="a3-github", man=MANE,
                          rec_kw={"issuer": "Sectigo Limited"})
    inp_e, pr_e = P.fill_inputs(pl_e, o_e, TE)
finally:
    P.EVIDENCE_RULES.clear()
    P.EVIDENCE_RULES.update(orig)
    P.EVIDENCE["mem0ai/mem0"] = "mem0_licence"
check("the evidence file is placed first, and every pin of its repository takes its licence from its text",
      pl_e.items[0].names == ["gh_lic"] and pr_e == []
      and inp_e["gh_p"]["licence_found"] == inp_e["gh_lic"]["licence_found"] == "Apache-2.0"
      and inp_e["gh_p"]["licence_source"] == "evidence gh_lic", str((pr_e, inp_e.get("gh_p"))))

print("\n- A3.g: after the window, the catcher's tunnel and the clone (G1, G2) -")
GPLAN = P.Plan("a3-git", ["github.com"], "fetch-git", [], [], 0, 0, {"amem_x": ("MIT", "d1 repo agiresearch/A-mem")})
bare_rec = {"problems": [], "hosts": ["github.com"], "catcher": [], "jobs": [
    {"index": 0, "rc": 0, "unit": str(TMP), "job": {"hosts": ["github.com"]},
     "summary": [{"id": "probe:github.com", "ok": True, "final_host": "github.com", "issuer_o": "Sectigo Limited",
                  "issuer_cn": "x"}]}]}
gi, gp = P._git_after(None, None, bare_rec, GPLAN, pins={"amem_x": pin("git", "agiresearch/A-mem", None, "c" * 40, window="a3-git")},
                      disc=None, run="r1", parent_env={}, native=None, fs=None, git_exe=P.GIT_EXE, via_port=1,
                      pins_root=TMP / "gp", base=TMP / "gbase", issuer_orgs=P.PUBLIC_ISSUER_ORGS, pre={})
def t2_case():
    """T2: git_verify passes, but the listing that lands in place is not the verified one - the re-check names it."""
    base = TMP / "t2"
    unit = base / "unit"
    (unit / "A-mem.git").mkdir(parents=True)
    (base / "gb" / "verify").mkdir(parents=True)
    (base / "gb" / "verify" / "ls-tree.txt").write_bytes(b"100644 blob x\ta.py\n")
    (base / "gb" / "verify" / "A-mem.tar").write_bytes(b"tar")
    rec_ok = {"problems": [], "hosts": ["github.com"],
              "catcher": [{"arm": "fetch-git", "host": "github.com", "tunnelled": True, "via": "127.0.0.1:1"}],
              "jobs": [{"index": 0, "rc": 0, "unit": str(unit), "job": {"hosts": ["github.com"]},
                        "summary": [{"id": "probe:github.com", "ok": True, "final_host": "github.com",
                                     "issuer_o": "Sectigo Limited", "issuer_cn": "x"}]},
                       {"index": 1, "rc": 0, "unit": str(unit), "job": {"child": "git"}, "summary": []}]}
    facts = {"resolved": "c" * 40, "listing_sha256": hashlib.sha256(b"100644 blob x\ta.py\n").hexdigest(),
             "listing_bytes": 20, "tar_sha256": "0" * 64, "git_version": "git version x", "head_at_fetch": "c" * 40}
    orig_gv, orig_replace = P.git_verify, P.os.replace

    def replace_tampering(a, b_):
        orig_replace(a, b_)
        if str(b_).endswith("ls-tree.txt"):
            Path(b_).write_bytes(b"other bytes\n")
    P.git_verify = lambda *a, **k: (dict(facts), [])
    P.os.replace = replace_tampering
    try:
        import types  # noqa: PLC0415
        return P._git_after(types.SimpleNamespace(polygon_root=base / "poly"), None, rec_ok, GPLAN, pins={"amem_x": pin("git", "agiresearch/A-mem", None, "c" * 40, window="a3-git")},
                            disc=None, run="r1", parent_env={}, native=None, fs=None, git_exe=P.GIT_EXE, via_port=1,
                            pins_root=base / "pins", base=base / "gb", issuer_orgs=P.PUBLIC_ISSUER_ORGS, pre={})
    finally:
        P.git_verify, P.os.replace = orig_gv, orig_replace


t2_inputs, t2_probs = t2_case()
check("T2: a placed listing that is not the verified one is P7, and nothing is filled",
      t2_inputs == {} and any(x.startswith("P7 amem_x: hash_mismatch: the placed listing") for x in t2_probs), str(t2_probs))
check("G2: a clone with no catcher tunnel for fetch-git through the hop is named; G1: no clone is named; nothing filled",
      gi == {} and any(x.startswith("G2 ") for x in gp) and any(x.startswith("G1 ") for x in gp)
      and json.loads((TMP / "gbase" / "place_record.json").read_bytes())["problems"] == gp, str(gp))

print("\n- A3.g: the git child's jobs and the version-free listing (Q-A3F-1..3) -")
amem = pin("git", "agiresearch/A-mem", None, "c" * 40, window="a3-git", role="arm-source")
cj = P.git_clone_job(amem, git_exe=Path("G:/git.exe"), ca_bundle=Path("G:/ca.crt"))
check("the clone job: OpenSSL with the given bundle, no redirect, fsck on receipt, no credential helper, --bare",
      cj["child"] == "git" and cj["exe"] == str(Path("G:/git.exe"))
      and cj["argv"] == ["-c", "http.sslBackend=openssl", "-c", f"http.sslCAInfo={Path('G:/ca.crt')}",
                         "-c", "http.followRedirects=false", "-c", "transfer.fsckObjects=true", "-c", "credential.helper=",
                         "clone", "--bare", "--", "https://github.com/agiresearch/A-mem.git", "A-mem.git"], str(cj["argv"]))
check("the clone job's own variables: no system config, no prompt of any kind",
      cj["env"] == {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
      and cj["timeout_s"] == P.GIT_TIMEOUT)
check("a git without an SSL-backend choice drops only that option",
      P.git_clone_job(amem, ssl_backend=None)["argv"][:2] == ["-c", f"http.sslCAInfo={P.GIT_CA_BUNDLE}"])
pj = P.git_probe_job(amem)
check("Q-A3F-2: the probe is one info/refs GET on github.com, nothing saved, capped at 1 MiB",
      pj == {"hosts": ["github.com"], "max_redirects": 0, "requests": [
          {"id": "probe:github.com", "url": "https://github.com/agiresearch/A-mem.git/info/refs?service=git-upload-pack",
           "save": None, "max_bytes": 1 << 20}]})
raw_ls = b"100644 blob " + b"2" * 40 + b"\tz.py\n040000 tree " + b"3" * 40 + b"\ta\n100644 blob " + b"4" * 40 + b"\tb/c.md\n"
check("Q-A3F-1: the listing is path-sorted, LF-joined, one final LF - whatever order git printed",
      P.canonical_listing(raw_ls) == b"040000 tree " + b"3" * 40 + b"\ta\n100644 blob " + b"4" * 40 + b"\tb/c.md\n100644 blob "
      + b"2" * 40 + b"\tz.py\n" and P.canonical_listing(raw_ls.replace(b"\n", b"\r\n")) == P.canonical_listing(raw_ls))
check("the git layer's constants: the fetch-git arm, the system git, Git for Windows' bundle",
      P.GIT_ARM == "fetch-git" and P.GIT_EXE == Path(r"C:\Program Files\Git\cmd\git.exe")
      and P.GIT_CA_BUNDLE == Path(r"C:\Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt"))

GIT = shutil.which("git")
if GIT is None:
    print("  SKIP git_verify on a local bare repository: no git on PATH (not passed)")
else:
    import os  # noqa: E402
    import subprocess  # noqa: E402
    L = _load("v3_launch_fp", ROOT / "research" / "v3" / "launch.py")

    class AnySampler:
        def processes(self):
            return []

        def identity(self, pid):
            return 1.0

        def connections(self, pids):
            return [], 0

        def listeners(self):
            return {}

    GIT_REAL = Path(GIT).resolve()
    EXC = {sys.executable: "the test interpreter", str(GIT_REAL): "the system git"}
    gbase = TMP / "git"
    (gbase / "watched").mkdir(parents=True)
    (gbase / "watched" / "idle.txt").write_bytes(b"idle")
    GC = L.Contract(polygon_root=gbase / "polygon", runs_root=gbase / "polygon" / "runs" / "v3", repo_root=gbase / "repo",
                    owner_home=gbase / "owner", secrets_dir=gbase / "secrets", quarantine_root=gbase / "quarantine",
                    conservation_root=gbase / "conservation", binary_exceptions=EXC,
                    system_dirs=(Path(sys.executable).parent, GIT_REAL.parent))
    src = gbase / "src"
    genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    run_git = lambda *a, cwd=None: subprocess.run([GIT, *a], cwd=cwd, env=genv, capture_output=True, check=True)  # noqa: E731
    src.mkdir()
    run_git("init", "-q", "-b", "main", str(src))
    (src / "b").mkdir()
    (src / "z.py").write_bytes(b"print(1)\n")
    (src / "b" / "c.md").write_bytes(b"# c\n")
    run_git("add", "-A", cwd=src)
    run_git("commit", "-q", "-m", "one", cwd=src)
    first = run_git("rev-parse", "HEAD", cwd=src).stdout.decode().strip()
    (src / "z.py").write_bytes(b"print(2)\n")
    run_git("commit", "-q", "-am", "two", cwd=src)
    bare = gbase / "polygon" / "runs" / "v3" / "_fetch.a3-git" / "r1" / "fetch-git" / "j1" / "A-mem.git"
    bare.parent.mkdir(parents=True)
    run_git("clone", "-q", "--bare", str(src), str(bare))
    NAT = L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None)
    FSW = L.FsWitness([L.WatchSpec("watched", gbase / "watched")])
    pin_g = dict(amem, revision=first)
    facts, gprobs = P.git_verify(GC, L, bare=bare, pin=pin_g, run="r1", parent_env=os.environ, native=NAT, fs=FSW,
                                 git_exe=Path(GIT), work=gbase / "work")
    want_ls = P.canonical_listing(run_git("ls-tree", "-r", "--full-tree", first, cwd=src).stdout)
    check("git_verify: the pin commit resolves to itself; fsck passes; the listing is written and hashed; the tar beside",
          gprobs == [] and facts["resolved"] == first and facts["listing_sha256"] == hashlib.sha256(want_ls).hexdigest()
          and (gbase / "work" / "ls-tree.txt").read_bytes() == want_ls and len(facts["tar_sha256"]) == 64
          and facts["git_version"].startswith("git version"), str((gprobs, facts)))
    check("Q-A3F-10: a HEAD that moved past the pin is recorded, not a problem",
          facts["head_at_fetch"] != first and _is40(facts["head_at_fetch"]), str(facts.get("head_at_fetch")))
    facts2, gprobs2 = P.git_verify(GC, L, bare=bare, pin=dict(amem, revision="d" * 40), run="r2", parent_env=os.environ,
                                   native=NAT, fs=FSW, git_exe=Path(GIT), work=gbase / "work2")
    check("G3: a pin commit the clone lacks is named, and nothing is listed",
          len(gprobs2) == 1 and gprobs2[0].startswith("G3 agiresearch/A-mem: pin_commit_absent")
          and not (gbase / "work2" / "ls-tree.txt").exists(), str(gprobs2))
    facts3, gprobs3 = P.git_verify(GC, L, bare=bare, pin=dict(amem, revision="HEAD"), run="r3",
                                   parent_env=os.environ, native=NAT, fs=FSW, git_exe=Path(GIT), work=gbase / "work3")
    check("G4: a revision that resolves to another commit (a ref name, say) is named, never passed as the pin",
          len(gprobs3) == 1 and gprobs3[0].startswith("G4 agiresearch/A-mem: resolved_not_the_pin") and "resolved" not in facts3,
          str(gprobs3))
    bad_bare = gbase / "polygon" / "runs" / "v3" / "_fetch.a3-git" / "r1" / "fetch-git" / "j2" / "A-mem.git"
    shutil.copytree(bare, bad_bare)
    (bad_bare / "objects" / "00").mkdir(exist_ok=True)
    (bad_bare / "objects" / "00" / ("1" * 38)).write_bytes(b"not a zlib object")
    facts5, gprobs5 = P.git_verify(GC, L, bare=bad_bare, pin=pin_g, run="r5", parent_env=os.environ, native=NAT, fs=FSW,
                                   git_exe=Path(GIT), work=gbase / "work5")
    check("G5: a clone whose objects fail fsck --strict is named, and nothing is listed",
          any(x.startswith("G5 agiresearch/A-mem: fsck_failed") for x in gprobs5) and not (gbase / "work5" / "ls-tree.txt").exists(),
          str(gprobs5))

print("\n- the windows end to end: a fake index behind a fake hop, the real children -")
import os  # noqa: E402
import subprocess  # noqa: E402

import _tls_fake as TF  # noqa: E402

LE = _load("v3_launch_fpe", ROOT / "research" / "v3" / "launch.py")
FE = _load("v3_fetch_a3_fpe", ROOT / "research" / "v3" / "fetch_a3.py")


class Quiet:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


E_HOSTS = ("huggingface.co", "us.aws.cdn.hf.co", "raw.githubusercontent.com", "openaipublic.blob.core.windows.net",
           "github.com")
made = TF.make_test_cert(TMP / "cert", E_HOSTS[0], extra_hosts=E_HOSTS[1:], org="Amazon")
GIT_E = shutil.which("git")
if made is None:
    print("  SKIP the end-to-end windows: neither cryptography nor openssl is available (not passed)")
else:
    TK_BODY = b"Y2wxMDBr 0\n" * 40
    routes = {
        f"/datasets/org/ds/resolve/{REV_HF}/data/x.parquet": (302, [("Location", "https://us.aws.cdn.hf.co/blob/lfs1")], b""),
        "/blob/lfs1": (200, [("Content-Type", "application/octet-stream")], BODY_LFS),
        f"/datasets/org/ds/resolve/{REV_HF}/tokenizer_config.json": (200, [("Content-Type", "application/json")], BODY_SMALL),
        f"/mem0ai/mem0/{REV_M0}/evaluation/prompts.py": (200, [("Content-Type", "text/plain")], BODY_GH),
        f"/mem0ai/mem0/{REV_M0}/LICENSE": (200, [("Content-Type", "text/plain")], LIC_TXT),
        "/encodings/cl100k_base.tiktoken": (200, [("Content-Type", "application/octet-stream")], TK_BODY),
    }
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    EXC_E = {sys.executable: "the test interpreter"}
    if getattr(sys, "_base_executable", sys.executable) != sys.executable:
        EXC_E[sys._base_executable] = "the test interpreter's base"
    if GIT_E:
        EXC_E[str(Path(GIT_E).resolve())] = "the system git (test)"

    def econtract(tag):
        base = TMP / "e2e" / tag
        (base / "watched").mkdir(parents=True)
        (base / "watched" / "idle.txt").write_bytes(b"idle")
        (base / "repo").mkdir()
        dirs = (Path(sys.executable).parent, *((Path(GIT_E).resolve().parent,) if GIT_E else ()))
        c = LE.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=base / "repo",
                        owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                        conservation_root=base / "conservation", binary_exceptions=EXC_E, system_dirs=dirs)
        return c, base

    def go(tag, window, **kw):
        c, base = econtract(tag)
        res = P.run_pin_window(c, LE, FE, window=window, run="r1", python=Path(sys.executable), via_port=hop.port,
                               parent_env=os.environ, native=LE.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
                               fs=LE.FsWitness([LE.WatchSpec("watched", base / "watched")]),
                               child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP, need_bytes_override=1, **kw)
        return res, c

    res, ce = go("hf", "a3-hf", disc=DR, pins=T2, manifest=MANI)
    fill = json.loads((ce.runs_root / "_fetch/a3-hf/r1/pin_fill.json").read_bytes()) if res.get("pin_fill") else {}
    snap = ce.polygon_root / "hf_cache/hub/datasets--org--ds/snapshots" / REV_HF
    wrec = json.loads((ce.runs_root / "_fetch/a3-hf/r1/record.json").read_bytes())
    check("a3-hf end to end: one listed redirect followed, each file re-hashed and placed in the hub layout, filled",
          res["problems"] == [] and sorted(fill.get("pins", {})) == ["hf_lfs", "hf_small", "hf_twin"]
          and (snap / "data" / "x.parquet").read_bytes() == BODY_LFS and (snap / "tokenizer_config.json").read_bytes() == BODY_SMALL
          and (ce.polygon_root / "hf_cache/hub/datasets--org--ds/refs/main").is_file()
          and (ce.polygon_root / "hf_cache/hub/datasets--org--ds/refs/main").read_bytes() == REV_HF.encode(),
          str(res))
    check("a3-hf: the catcher tunnelled exactly the window's hosts through the hop, the check clean, nothing past it",
          {x["host"] for x in wrec["catcher"] if x["tunnelled"]} == {"huggingface.co", "us.aws.cdn.hf.co"}
          and wrec["check"]["complete"] and wrec["check"]["native_hits"] == 0 and wrec["check"]["window_hosts"] == []
          and wrec["arm"] == "fetch" and wrec["log_problems"] == [], str(wrec["check"]))
    orig_ev, orig_rules = dict(P.EVIDENCE), dict(P.EVIDENCE_RULES)
    P.EVIDENCE["mem0ai/mem0"], P.EVIDENCE_RULES["gh_lic"] = "gh_lic", P.apache_licence
    try:
        rg, cg = go("gh", "a3-github", disc=DR, pins=TE, manifest=MANE)
    finally:
        P.EVIDENCE.clear()
        P.EVIDENCE.update(orig_ev)
        P.EVIDENCE_RULES.clear()
        P.EVIDENCE_RULES.update(orig_rules)
    gfill = json.loads((cg.runs_root / "_fetch/a3-github/r1/pin_fill.json").read_bytes()) if rg.get("pin_fill") else {}
    check("a3-github end to end: raw at the commit, blob sha1 checked, the licence read from the placed LICENSE",
          rg["problems"] == [] and gfill["pins"]["gh_p"]["licence_found"] == "Apache-2.0"
          and (cg.runs_root / "_pins/github" / REV_M0 / "evaluation" / "prompts.py").read_bytes() == BODY_GH, str(rg))
    TK = {"tk": {**pin("url", None, "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken", None,
                       role="tokenizer", window="a3-tiktoken"), "cross_check": {"sha256": hashlib.sha256(TK_BODY).hexdigest()}}}
    rt, ct = go("tk", "a3-tiktoken", disc=DR, pins=TK, manifest=MANI, url_licence=("MIT", "METADATA test"))
    tfill = json.loads((ct.runs_root / "_fetch/a3-tiktoken/r1/pin_fill.json").read_bytes()) if rt.get("pin_fill") else {}
    tsha = hashlib.sha256(TK_BODY).hexdigest()
    check("a3-tiktoken end to end: the committed cross_check and the bytes agree; revision = the sha256; licence MIT",
          rt["problems"] == [] and tfill["pins"]["tk"] == {"revision": tsha, "sha256": tsha, "bytes": len(TK_BODY),
                                                           "licence_found": "MIT", "licence_source": "METADATA test"}
          and (ct.runs_root / "_pins/url" / tsha / "cl100k_base.tiktoken").read_bytes() == TK_BODY, str(rt))
    TKB = {"tk": {**TK["tk"], "cross_check": {"sha256": "0" * 64}}}
    rb, cb = go("tkbad", "a3-tiktoken", disc=DR, pins=TKB, manifest=MANI, url_licence=("MIT", "METADATA test"))
    check("t1: bytes that are not the committed cross_check are refused by the child - nothing placed, no fill",
          rb["problems"] != [] and "pin_fill" not in rb and not (cb.runs_root / "_pins").exists(), str(rb))
    if GIT_E is None:
        print("  SKIP the a3-git window: no git on PATH (not passed)")
    else:
        genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        g = lambda *a_, cwd=None: subprocess.run([GIT_E, *a_], cwd=cwd, env=genv, capture_output=True, check=True)  # noqa: E731
        esrc = TMP / "e2e_src"
        g("init", "-q", "-b", "main", str(esrc))
        (esrc / "a.py").write_bytes(b"print('a')\n")
        g("add", "-A", cwd=esrc)
        g("commit", "-q", "-m", "one", cwd=esrc)
        ecommit = g("rev-parse", "HEAD", cwd=esrc).stdout.decode().strip()
        served = TMP / "e2e_served.git"
        g("clone", "-q", "--bare", str(esrc), str(served))
        g("repack", "-q", "-a", "-d", cwd=served)
        g("update-server-info", cwd=served)
        for f in served.rglob("*"):
            if f.is_file():
                routes["/agiresearch/A-mem.git/" + f.relative_to(served).as_posix()] = (
                    200, [("Content-Type", "application/octet-stream")], f.read_bytes())
        routes["/agiresearch/A-mem.git/info/refs?service=git-upload-pack"] = (
            200, [("Content-Type", "text/plain")], (served / "info" / "refs").read_bytes())
        d1u2 = Path(DR.records["d1"]["jobs"][2]["unit"])
        write(d1u2 / "gh/agiresearch__A-mem/repo.json", {"full_name": "agiresearch/A-mem", "license": {"spdx_id": "MIT"}})
        GP = {"amem": {**pin("git", "agiresearch/A-mem", None, ecommit, window="a3-git", role="arm-source"), "path": None}}
        GM = {"windows": {"a3-git": {"hosts": ["github.com"], "pins": ["amem"], "arms": ["fetch-git"]}}}
        rgit, cgit = go("git", "a3-git", disc=DR, pins=GP, manifest=GM, git_exe=Path(GIT_E).resolve(), ca_bundle=made[0],
                        ssl_backend="openssl" if os.name == "nt" else None)
        gf = json.loads((cgit.runs_root / "_fetch/a3-git/r1/pin_fill.json").read_bytes()) if rgit.get("pin_fill") else {}
        want = P.canonical_listing(g("ls-tree", "-r", "--full-tree", ecommit, cwd=esrc).stdout)
        check("a3-git end to end: probe and clone through the catcher on fetch-git; the pin commit verified; the listing "
              "is the sha256, the bare clone and the tar placed",
              rgit["problems"] == [] and gf["pins"]["amem"]["revision"] == ecommit
              and gf["pins"]["amem"]["sha256"] == hashlib.sha256(want).hexdigest() and gf["pins"]["amem"]["licence_found"] == "MIT"
              and (cgit.runs_root / "_pins/git" / ecommit / "A-mem.git").is_dir()
              and (cgit.runs_root / "_pins/git" / ecommit / "ls-tree.txt").read_bytes() == want
              and (cgit.runs_root / "_pins/git" / ecommit / "A-mem.tar").is_file()
              and gf.get("ca_bundle_sha256") == hashlib.sha256(Path(made[0]).read_bytes()).hexdigest(), str(rgit))
        grec = json.loads((cgit.runs_root / "_fetch/a3-git/r1/record.json").read_bytes())
        check("a3-git: the probe recorded github.com's issuer; the catcher tunnelled only github.com, on fetch-git, via the hop",
              {x["host"] for x in grec["catcher"] if x["tunnelled"]} == {"github.com"}
              and all(x["arm"] == "fetch-git" and x["via"] == f"127.0.0.1:{hop.port}" for x in grec["catcher"])
              and any(i[0] == "github.com" and i[1] == "Amazon" for i in grec["issuers"]), str(grec["issuers"]))
        GPB = {"amem": {**GP["amem"], "revision": "e" * 40}}
        rgb, _ = go("gitbad", "a3-git", disc=DR, pins=GPB, manifest=GM, git_exe=Path(GIT_E).resolve(), ca_bundle=made[0],
                    ssl_backend="openssl" if os.name == "nt" else None)
        check("G3: a clone without the pin commit is named; nothing is placed or filled",
              any(x.startswith("G3 ") for x in rgb["problems"]) and "pin_fill" not in rgb, str(rgb["problems"]))
    hop.close()
    srv.close()

print("\n- A7 phase 2 (Q-A7-P2-1 O-a): the window a7-github plans PINS_A7's pins, never PINS' -")
A7_BEFORE = copy.deepcopy(getattr(P.CP, "PINS_A7", {}))
REAL_MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
#: The blobs and sizes the auditor verified against the discovery trees (2026-09-29 23:2x): repo -> path -> (blob, size).
A7_TREES = {
    "xiaowu0162/LongMemEval": ("d1", 3, "9e0b455f4ef0e2ab8f2e582289761153549043fc", {
        "README.md": ("3490db4f796c14903788ecb3e33f056cab438bb0", 15970)}),
    "HUST-AI-HYZ/MemoryAgentBench": ("d1", 3, "538026089d1a8a8eff05121d0db89b388f360eba", {
        "agent.py": ("d1eba634a8289524291e210d96ce85559c5798d3", 55655),
        "main.py": ("7247c34fa41f8332d8f4ba5d4911cd08b11c2512", 8802),
        "initialization.py": ("b94ba63e2295e0dbaa7abae7bcc2d0e80451e168", 14159),
        "conversation_creator.py": ("2612def9d82f176a7f36b13b698c43a4d8ef5074", 12924),
        "README.md": ("b09a77858a47fd50869f78052f377be91c1f7264", 6905)}),
    "mohammadtavakoli78/BEAM": ("d2", 1, "b2da22eac88bb0874c64665f13457eb99835774a", {
        "src/answer_probing_questions/answer_generation.py": ("02045eb88662bc4ce3e2a17c52aedfb8e0f991c7", 16548),
        "src/answer_probing_questions/long_term_memory_methods.py": ("e1e2775ba045afc721fea6267acc01c7231031a4", 24223),
        "src/answer_probing_questions/light.py": ("e826014cde434d51ed95cdbd6f371ee0c700c644", 22629),
        "src/answer_probing_questions/answer_generation.sh": ("4d4edaa89f71f50a2f86702d07f5b47d54d00b92", 1853),
        "README.md": ("4fcb69138bf9ce3566cc5f315b267f583fc76445", 14549)}),
    "AMA-Bench/AMA-Bench": ("d2", 1, "ddfd319e0be33424288c13806f1eafc63e625b59", {
        "src/agent_harness.py": ("37beb165ec932c61bf73fb24c185082c81531808", 10272),
        "src/run.py": ("75dde9f669a2479458ae4f042a9a5fe8eb0556a9", 17241),
        "src/method/ama_agent_core/prompt.py": ("905c8a424918f641986aa7e7c982dcfb74671f41", 12732),
        "utils/extract_final_answer.py": ("43755b4a68937e943a21c524296429550154b033", 1085),
        "configs/ama_agent.yaml": ("f4d1d901590b440ff7bf2ba3e947ac8cbcd35ca0", 893),
        "README.md": ("c9721cd6859b103f85e74c3280e0840a13609d8f", 15131)})}
#: The cognee files the auditor chose (2026-09-30 00:43) with the blobs and sizes a7-cognee-tag d1 selected at the tag
#: v1.6.1's commit (d5_report.json 1b5d58bf...6620) - under cognee/eval_framework/.
COG_R = "eb90d03740755f5252b8b12cce91fd09970f2d81"
COG_FILES = {
    "answer_generation/beam_router.py": ("dcdaed6087af1c80866fdfcb6ef6ab0185cc6157", 8008),
    "beam/REPORT.md": ("a8a3f890b188355bb20ae5e666215609eeadcf1b", 25233),
    "beam/eval/aggregate_cross_run.py": ("c98bf501efcff396eb5cf48d226708d73b9676da", 5914),
    "beam/eval/beam_eval_adapter.py": ("17906ed750746183961f215483cedca4f051aaa1", 4172),
    "beam/eval/metrics/beam_rubric.py": ("cfc623625159e1331e103bdaa93a229ab513a667", 9470),
    "beam/eval/metrics/kendall_tau.py": ("150c2f7e5917478bc42249c9a09f70ad3e87778f", 9143),
    "beam/eval/registry.py": ("3106798406fb117f9d4315ea28d2a553cb17911e", 3956),
    "beam/eval/run_sweep.py": ("1955bab9f003589b2d061e675290287d091a3d76", 16453),
    "beam/eval/sweep.py": ("9fb8f0ef16bd4359b57e729d6f5b0a4f89a21f93", 8239),
    "beam/local_ingest.py": ("d63e46286717eecd2ebb67286949d07699e7b8f6", 21615),
    "beam/preprocessing/compression.py": ("0837480e9093678e20f1b22f903ff748d0fb82de", 20013),
    "beam/preprocessing/conversation_preprocessing.py": ("2eff500295de7eed2885493c7059f0daf16e9f03", 30642),
    "beam/preprocessing/loaders.py": ("4772cd620a842007ca663136c003b33f16c4eb1c", 3173),
    "beam/preprocessing/preprocess.py": ("f25aa1d260d9e46fdbc814dd48202869400187f7", 40478),
    "beam/preprocessing/prompts/beam_turn_compression_prompt.txt": ("63776534186ea0c7ee38633faaef29f90518e5fd", 2390),
    "beam/report_artifacts/100k_fixed/beam_hybrid_completion_20_20_qa_v1_config.json": ("71f15e3001490ae3bbe26f784e99554b3dab309b", 1439),
    "beam/report_artifacts/100k_fixed/hybrid_completion_20_20_qa_v1_cross_run_summary.json": ("ee3421adb3d6894609dd195ef99b170183620e66", 2942),
    "beam/report_artifacts/10m_routed/beam_qa_v1_hybrid_routing_configs.json": ("3db0b9af2b7be3c6521d119cfe95bf461ee32191", 9390),
    "beam/report_artifacts/10m_routed/routed_by_question_type_cross_run_summary.json": ("ee73ee433f6073a8d90f15917f40739610e47f9b", 3057),
    "beam/report_artifacts/10m_routed/routing.json": ("081623b2004359b728ecbab491dde895cd5bed26", 817),
    "beam/report_artifacts/README.md": ("bbd090f071ad1dddc2435a3c96769cde51c7a860", 400),
    "beam/report_artifacts/qa_prompts/abstention.txt": ("59fbfeef181e8bf15d39ec67843edac40ea1bf71", 356),
    "beam/report_artifacts/qa_prompts/contradiction_resolution.txt": ("56a9a7da55ed2c55264ca38d84d4b6d87d7ffd9c", 389),
    "beam/report_artifacts/qa_prompts/default.txt": ("6a63c6084c10e46d5a88459eab6a84f7a3ee1026", 104),
    "beam/report_artifacts/qa_prompts/event_ordering.txt": ("c7a687be7bef3289763cbd73c49759d2b17d6f0f", 361),
    "beam/report_artifacts/qa_prompts/information_extraction.txt": ("51e322cfcd9d2cdaa42570da6efddce4f8480134", 305),
    "beam/report_artifacts/qa_prompts/instruction_following.txt": ("f41a54695a9772fec011078dd7cdbdf7685cda84", 411),
    "beam/report_artifacts/qa_prompts/knowledge_update.txt": ("711d096d5de77a3f5901e11eafe1d91402db2f3d", 367),
    "beam/report_artifacts/qa_prompts/multi_session_reasoning.txt": ("40f7df196646df667551d6bbc548595b4e66dbbc", 365),
    "beam/report_artifacts/qa_prompts/preference_following.txt": ("be7a818a59396ff1c009cc94b387f67a5221b5fa", 334),
    "beam/report_artifacts/qa_prompts/summarization.txt": ("5743d8f06596d694abac1e3e415f41b5239ddc81", 345),
    "beam/report_artifacts/qa_prompts/temporal_reasoning.txt": ("f78caaf0346f3fe668e32c3b28b6523cc6997ce7", 264),
    "beam/session_io.py": ("097b9033ec53c8f634f2be7c091c05d930792b59", 3000),
    "benchmark_adapters/beam_adapter.py": ("a0b1f3081ec93c0553e88d5ccf16588cfd90fb5e", 9111),
    "run_beam_eval.py": ("10109a622626f2f14a834b8675bd3753b58d02dd", 2704),
}


def cog_report(**over) -> dict:
    """The a7-cognee-tag d1 report's shape (fetch_a3 plan d5): the 35 files plus two it selected that are not pinned."""
    selected = [{"path": "cognee/eval_framework/" + k, "blob": b, "size": z} for k, (b, z) in COG_FILES.items()]
    selected += [{"path": "cognee/eval_framework/beam/__init__.py", "blob": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", "size": 0},
                 {"path": "cognee/eval_framework/beam/report_artifacts/10m_routed/x_run0.json.gz", "blob": "1" * 40, "size": 9}]
    rep = {"repo": "topoteretes/cognee", "tag": "v1.6.1", "tag_commit": COG_R, "commit": COG_R, "tag_matches": True,
           "tree_entries": 4660, "tree_truncated": False, "selected": selected, "missing_files": [], "problems": []}
    rep.update(over)
    return rep

b7 = TMP / "disc_a7"
for rec_, n_ in (("d1", 4), ("d2", 3)):
    for j_ in range(n_):
        (b7 / rec_ / f"j{j_}").mkdir(parents=True, exist_ok=True)
for repo_, (rec_, job_, rev_, files_) in A7_TREES.items():
    saved = P.GH_TREES.get(repo_, (rec_, job_, f"gh/{P._safe(repo_)}/tree.json"))[2]
    write(b7 / rec_ / f"j{job_}" / saved, {"sha": rev_, "truncated": False, "tree": [
        {"path": pth, "type": "blob", "sha": blb, "size": sz} for pth, (blb, sz) in files_.items()]})
    lrec, ljob, lrel = P.GH_REPOS.get(repo_, ("d1", 2, f"gh/{P._safe(repo_)}/repo.json"))
    write(b7 / lrec / f"j{ljob}" / lrel, {"full_name": repo_, "license": {"spdx_id": "MIT"}})
D7 = P.Discovery({r: {"jobs": [{"index": j, "unit": str(b7 / r / f"j{j}"), "job": {"requests": []}, "summary": []}
                               for j in range(n)]} for r, n in (("d1", 4), ("d2", 3))})
write(b7 / "a7d1" / "j0" / "gh/topoteretes__cognee/repo.json", {"full_name": "topoteretes/cognee",
                                                                  "license": {"spdx_id": "Apache-2.0"}})
D7.records["a7d1"] = {"jobs": [{"index": 0, "unit": str(b7 / "a7d1" / "j0"), "job": {"requests": []}, "summary": []}]}
D7.reports = {"cogtag": cog_report()}          # set as an attribute: on a Discovery without reports every row FAILs by name
UR7 = TMP / "runs" / "_fetch.a7-github" / "r1" / "fetch"
try:                                          # a missing table FAILs these rows by name, never the suite
    p7, p7err = P.plan_window("a7-github", disc=D7, pins=P.CP.PINS_A7, manifest=REAL_MANI, hf_hub=HUB, pins_root=PR,
                              unit_root=UR7), None
except Exception as e:  # noqa: BLE001
    p7, p7err = None, f"{type(e).__name__}: {e}"
want7 = {(f"https://raw.githubusercontent.com/{repo_}/{rev_}/{pth}", blb, sz)
         for repo_, (_r, _j, rev_, files_) in A7_TREES.items() for pth, (blb, sz) in files_.items()}
want7 |= {(f"https://raw.githubusercontent.com/topoteretes/cognee/{COG_R}/cognee/eval_framework/{k}", b, z)
          for k, (b, z) in COG_FILES.items()}
got7 = {(it.url, it.expect.get("git_blob_sha1"), it.expect.get("size")) for it in (p7.items if p7 else [])}
check("A7-6: a7-github's plan from PINS_A7 and the real manifest - raw.githubusercontent.com only, no redirect, the 52 "
      "files each at its discovery commit (cognee's at the tag v1.6.1's), expected to be the tree's git blob and size, "
      "placed under _pins/github/",
      p7err is None and p7.hosts == ["raw.githubusercontent.com"] and len(p7.items) == 52 and got7 == want7
      and all(j.get("max_redirects") == 0 for j in p7.jobs)
      and all("/github/" in str(it.dest).replace("\\", "/") for it in p7.items),
      str(p7err or sorted(want7 ^ got7)[:3]))
check("A7-7: planned from A3's table instead, the window a7-github stops by name (S8): the manifest's pins are not "
      "that table's", stopped(lambda: P.plan_window("a7-github", disc=D7, pins=P.CP.PINS, manifest=REAL_MANI, hf_hub=HUB,
                                                    pins_root=PR, unit_root=UR7), "S8"))
seen_pins: list = []


class _PlanSeen(Exception):
    pass


def _spy(window, *, pins, **kw):
    seen_pins.append((window, pins))
    raise _PlanSeen(window)


_orig_plan = P.plan_window
P.plan_window = _spy
try:
    for w_ in ("a7-github", "a3-github"):
        try:
            P.run_pin_window(__import__("types").SimpleNamespace(polygon_root=TMP / "poly", runs_root=TMP / "runs7"),
                             None, None,
                             window=w_, run="t", python=Path(sys.executable), via_port=1, parent_env={}, disc=D7,
                             manifest=REAL_MANI)
        except _PlanSeen:
            pass
        except Exception as e:  # noqa: BLE001 - the row reads what was seen
            seen_pins.append((w_, f"{type(e).__name__}: {e}"))
finally:
    P.plan_window = _orig_plan
check("A7-8: run_pin_window with no table given plans a7-github from PINS_A7 and a3-github from PINS (table_for)",
      [(w, t is getattr(P.CP, "PINS_A7", None) if w == "a7-github" else t is P.CP.PINS) for w, t in seen_pins]
      == [("a7-github", True), ("a3-github", True)], str([(w, type(t).__name__, str(t)[:80]) for w, t in seen_pins]))
check("A7-9: main() takes the window a7-github", '"a7-github"' in __import__("inspect").getsource(P.main))

print("\n- the cognee pins (the auditor, 2026-09-30 00:43): a7-cognee-tag d1 holds their tree, a7-discovery d1 their licence -")


def _try(fn):
    """(value, None) or (None, the exception named) - a raise FAILs its own row, never the suite."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def a7_records(base: Path, *, report=None) -> dict:
    """a7-cognee-tag d1's report and a7-discovery d1's record (cognee's repo.json in its job 0) under ``base``."""
    r = base / "_fetch" / "a7-cognee-tag" / "d1" / "d5_report.json"
    write(r, (json.dumps(report if report is not None else cog_report(), indent=1) + "\n").encode("utf-8"))
    u = base / "a7disc_unit"
    write(u / "gh/topoteretes__cognee/repo.json", {"full_name": "topoteretes/cognee", "license": {"spdx_id": "Apache-2.0"}})
    f = base / "_fetch" / "a7-discovery" / "d1" / "record.json"
    write(f, (json.dumps({"jobs": [{"index": 0, "unit": str(u), "job": {"requests": []}, "summary": []}]}) + "\n").encode())
    return {"a7d1": hashlib.sha256(f.read_bytes()).hexdigest(), "cogtag": hashlib.sha256(r.read_bytes()).hexdigest()}


bc = TMP / "disc_cog"
CSH = fake_discovery(bc)
A7SH = a7_records(bc)
DC, dcerr = _try(lambda: P.load_discovery(bc, shas=CSH, a7=A7SH))
COG_NAMES = sorted(n for n, p_ in getattr(P.CP, "PINS_A7", {}).items() if p_["repo"] == "topoteretes/cognee")
a710, a710err = _try(lambda: dcerr is None and DC.reports["cogtag"]["commit"] == COG_R and "a7d1" in DC.records
                    and getattr(P, "A7_SHAS", None) == {
                        "a7d1": "1689534abb103971d502774dfd2a4566d6947c3efaeda552dbe3704b72ed037e",
                        "cogtag": "1b5d58bfb9f484249ba0ac32f14c673c0db27f140a0f07825426ae34934e6620"}
                    == {"a7d1": getattr(P.CP, "A7_DISCOVERY_D1", None), "cogtag": getattr(P.CP, "COGNEE_TAG_D1", None)})
check("A7-10: the cognee tag report and the a7-discovery record load when their sha256 are the pinned ones; the real "
      "tool pins exactly the auditor's two (d5_report.json 1b5d58bf...6620, record.json 1689534a...037e)",
      a710 is True, str(dcerr or a710err))
bn7 = TMP / "disc_cog_none"
CSN = fake_discovery(bn7)
DN7, dn7err = _try(lambda: P.load_discovery(bn7, shas=CSN, a7=A7SH))
check("A7-11: S1 - a cognee tag report one byte off is refused by name; with no A7 record on the disk the A3 records "
      "still load, and a cognee pin's expectation stops by name (S1)",
      stopped(lambda: P.load_discovery(bc, shas=CSH, a7={**A7SH, "cogtag": "0" * 64}), "S1", "a7-cognee-tag d1")
      and dn7err is None and bool(COG_NAMES)
      and stopped(lambda: P.expectation(DN7, P.CP.PINS_A7[COG_NAMES[0]]), "S1", "a7-cognee-tag d1"), str(dn7err))
ex7, ex7err = _try(lambda: {n: P.expectation(DC, P.CP.PINS_A7[n]) for n in COG_NAMES})
lic7, lic7err = _try(lambda: {P.licence_found(DC, P.CP.PINS_A7[n]) for n in COG_NAMES})
a712, a712err = _try(lambda: ex7err is None and len(COG_NAMES) == 35 and ex7 == {
    n: {"git_blob_sha1": COG_FILES[P.CP.PINS_A7[n]["path"][len("cognee/eval_framework/"):]][0],
        "size": COG_FILES[P.CP.PINS_A7[n]["path"][len("cognee/eval_framework/"):]][1]} for n in COG_NAMES}
    and lic7 == {("Apache-2.0", "a7d1 repo topoteretes/cognee")})
check("A7-12: each of the 35 cognee pins is expected to be the tag report's git blob and size for its path; the licence "
      "found is cognee's repository's (Apache-2.0, from a7-discovery d1)",
      a712 is True, str(ex7err or lic7err or a712err or lic7))
_one = COG_FILES["run_beam_eval.py"]
_gone = [x for x in cog_report()["selected"] if not x["path"].endswith("/run_beam_eval.py")]
_twice = cog_report()["selected"] + [{"path": "cognee/eval_framework/run_beam_eval.py", "blob": _one[0], "size": _one[1]}]
cog_stops = [("another commit", {"commit": "f" * 40}, "S4"), ("the tag not matched", {"tag_matches": False}, "S4"),
             ("another tag", {"tag": "v1.6.2"}, "S4"), ("a truncated tree", {"tree_truncated": True}, "S3"),
             ("truncation unknown", {"tree_truncated": None}, "S3"), ("another repository", {"repo": "someone/cognee"}, "S2"),
             ("the report's problems", {"problems": ["a tree call answered 500"]}, "S2"),
             ("the file not selected", {"selected": _gone}, "S2"), ("the file twice", {"selected": _twice}, "S2"),
             ("the tag's own commit another", {"tag_commit": "f" * 40}, "S4"),
             ("an entry without a blob", {"selected": _gone + [{"path": "cognee/eval_framework/run_beam_eval.py",
                                                              "blob": "not-a-sha", "size": _one[1]}]}, "S2"),
             ("an entry with a size that is no number", {"selected": _gone + [{"path": "cognee/eval_framework/run_beam_eval.py",
                                                                              "blob": _one[0], "size": "2704"}]}, "S2")]
seen7 = {}
for label, over, code in cog_stops:
    Dx = P.Discovery(dict(DC.records)) if dcerr is None else None
    if Dx is not None:
        Dx.reports = {"cogtag": cog_report(**over)}
    seen7[label] = Dx is not None and bool(COG_NAMES) and stopped(
        lambda Dx=Dx: P.expectation(Dx, P.CP.PINS_A7["cognee_run_beam_eval"]), code)
check("A7-13: a cognee pin stops by name when its tag report is not the pin's - " + ", ".join(seen7),
      all(seen7.values()), str([k for k, v in seen7.items() if not v]))
wrong_from = dict(getattr(P.CP, "PINS_A7", {}).get("cognee_run_beam_eval") or {}, revision_from="a7-cognee-tag d1 000000000000")
check("A7-14: a cognee pin whose revision_from names another tag record (not 1b5d58bfb9f4) stops by name (S1)",
      dcerr is None and bool(COG_NAMES) and stopped(lambda: P.expectation(DC, wrong_from), "S1", "a7-cognee-tag d1"))
check("nothing here touches the real A7 table", getattr(P.CP, "PINS_A7", {}) == A7_BEFORE)

import inspect  # noqa: E402

check("main() hands the children no CA file and no extra variable; a window's hosts are the manifest's only",
      "child_env_extra" not in inspect.getsource(P.main) and "SSL_CERT_FILE" not in inspect.getsource(P)
      and "manifest=" not in inspect.getsource(P.main) and "need_bytes_override" not in inspect.getsource(P.main)
      and P.run_pin_window.__kwdefaults__["need_bytes_override"] is None)
check("nothing here touches the real pin table (fetch_pins_a3's own, since the suite began)", P.CP.PINS == REAL_PINS_BEFORE)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch pins a3: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
