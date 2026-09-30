#!/usr/bin/env python3
"""PREREG-V3 A7 (the auditor's Q-F7 = O-a, 2026-09-30): research/v3/freeze_a7.py - the A7 fragment of FREEZE-V3, a thin
wrapper over freeze_a3.build with PINS_A7 and FILLED_A7 - on a temporary runs tree, offline; freeze_a3.json is never
touched.

* the declared lists are exactly the auditor's: four cleared runs (a7-discovery d1, a7-docs d1, a7-cognee-tag d1,
  a7-npm-d d1 - eight record files by sha256, a7-npm-d's two problems by its ruling note) and one failed run (a7-npm g1);
  a window after them joins only as a line written after the auditor's checks of its run;
* every record is read by its sha256 - a file whose bytes moved is refused by name, nothing written;
* every PINS_A7 pin is filled, or the build stops ("A7 is not complete");
* the prereg section is the sha256 of revision 1 AND of the amendments file as git blobs at the anchor commit - never the
  working copy - and a section without either is refused;
* the same records give the same bytes; the command writes research/v3/freeze_a7.json and never freeze_a3.json.

    python tests/_test_v3_freeze_a7.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
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


RAISED: list = []
try:
    F7 = _load("v3_freeze_a7_t", ROOT / "research" / "v3" / "freeze_a7.py")
except Exception as e:  # noqa: BLE001 - on the old tree the module is missing; every row then FAILs by name
    RAISED.append(f"load: {type(e).__name__}: {e}")
    F7 = None
CP = _load("v3_corpus_pin_f7", ROOT / "research" / "v3" / "corpus_pin_v3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def ok(fn) -> bool:
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - FreezeRefused is freeze_a3's class, reached through freeze_a7
        return type(e).__name__ == "FreezeRefused" and words in str(e)
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_freeze_a7_"))
#: the auditor's list (2026-09-30 01:3x, m6 --window PASS on each), written here - independent of the code's
WANT_CLEARED = {
    ("a7-discovery", "d1"): {"_fetch/a7-discovery/d1/record.json": "1689534abb103971d502774dfd2a4566d6947c3efaeda552dbe3704b72ed037e",
                             "_fetch/a7-discovery/d1/d3_report.json": "dff73cfea405f3963f32880abd1ced9c14d1da7dec1888778e517244bee6eca9"},
    ("a7-docs", "d1"): {"_fetch/a7-docs/d1/record.json": "22cf0b04276ae8f7b5aa66a62e644bc589cdaec4cc2f74ea061a4032606dfffa",
                        "_fetch/a7-docs/d1/d4_report.json": "492f92b77098b80eeca7494bb3550a3988aa596873850ca1d1f269bae6a9fcd8"},
    ("a7-cognee-tag", "d1"): {"_fetch/a7-cognee-tag/d1/record.json": "136e0bf762df91ded98b80a53e282c89cb1424035933d4df8c458675001fd721",
                              "_fetch/a7-cognee-tag/d1/d5_report.json": "1b5d58bfb9f484249ba0ac32f14c673c0db27f140a0f07825426ae34934e6620"},
    ("a7-npm-d", "d1"): {"_fetch/a7-npm-d/d1/record.json": "8aeed9ecfc3b86edc80573d2b552dd7d08f197d10e6231f8b010ef7c6b1e9ce2",
                         "_fetch/a7-npm-d/d1/discovery.json": "8e3ef3ff8c6f73673ce6e6e7ba26cdd0fda5865ca75824433aab0f75af9af434"},
    # the auditor's clearing of a7-github g1 (2026-09-30 03:0x), a line after his checks of the run
    ("a7-github", "g1"): {"_fetch/a7-github/g1/record.json": "e61f6d9d7f3d0878e108eb90c375750c674bf462f3d859a5ad321063a3b61334",
                          "_fetch/a7-github/g1/place_record.json": "41a809505ec2f4bddf1ba5c6a09d729c0544dfee1a99bb18829e70a95c6ffcef",
                          "_fetch/a7-github/g1/pin_fill.json": "acae9e52bc935a5752f384fbcc0cd26521e20c97878056d3318963f782296472"},
    ("a7-hf-d", "d1"): {"_fetch/a7-hf-d/d1/record.json": "8a6f7663317561f82842637334e1701a3769ba498c194912d52185b10cc4ed82",
                        "_fetch/a7-hf-d/d1/d7_report.json": "2ec9f5f251e755860f5cc6ea887766f939a8fe2d93b6d7a3ba165220c69c6ca4"},
    ("a7-arxiv", "d2"): {"_fetch/a7-arxiv/d2/record.json": "8ec76a098a21ce6d2f82ca1baf7c7900363f252b712d1b17d11387309fedb3c5",
                         "_fetch/a7-arxiv/d2/d6_report.json": "9c93889a05577ce03e077e1b68e2f826e03f211b72f9b7e7a4023d5c11d3e638"},
    ("a8-supermemory-bin", "b2"): {
        "_fetch/a8-supermemory-bin/b2/record.json": "83b5bc3652ad1a4119fa29a6b93b181d5b05bc7894c664018a664603334cbc91",
        "_install/a8-supermemory-bin/b2/bin_record.json": "6de665abf22c26c012c8aebc2588c71ea08ad1ab2559c128369277d7a8ad3b7d"},
    # the auditor's clearing of a7-hf h1 (2026-09-30 05:3x): the ten MiniLM files
    ("a7-hf", "h1"): {"_fetch/a7-hf/h1/record.json": "970344e56358bcb3c137d2f8c5c7ab9aa855c792f434d9ef31d7c33adbfcdbd7",
                      "_fetch/a7-hf/h1/place_record.json": "3aa4ab44d4d9d1e37249b57052f3405edafd1e8679ef3784010c10d81175ff48",
                      "_fetch/a7-hf/h1/pin_fill.json": "449a89fca362ab4aa85d6408236cb8ba37ab56e04c2a6ddfeea75f59e8094afe"},
    # the auditor's clearing of W2 py-base-312 p1 (2026-09-30 06:4x): the product venvs' base
    ("py-base-312", "p1"): {"_tools/py-base-312/py-base-312.json": "52d4aeae4c3701b098ed1dacbc5ba04c0181c13a06abf580d5442b0772846862"},
    # the auditor's clearing of a7-arxiv-src s3 (2026-09-30 09:2x): Zep's arXivRaw record and e-print (Q-ZT-1)
    ("a7-arxiv-src", "s3"): {"_fetch/a7-arxiv-src/s3/record.json": "8d298d088a29a7f55e70e808184b6be23dfbf2b9f2527b44c549c42e6d2bb847",
                             "_fetch/a7-arxiv-src/s3/d8_report.json": "8870d618f48874fe6244b74845bed011c2f882d319ce41f58a93afff31e195e4"},
    # the auditor's clearing of a7-github-2 g1 (2026-09-30 07:5x): LME's run_generation.sh and AMA's method code
    ("a7-github-2", "g1"): {"_fetch/a7-github-2/g1/record.json": "ad6ff8fb61bc456db6c30c5d1aef03382580cdfc792f3565beaf2a288ebd4272",
                            "_fetch/a7-github-2/g1/place_record.json": "aedefc7e632e9a46101287b47ad0f82629e184047db0a4a88cb8d55c64be9f9c",
                            "_fetch/a7-github-2/g1/pin_fill.json": "fa5375b6678530c023ec875bed830accd447e6e132abf5aec167207e6b3a28b7"}}
WANT_FAILED = {("a7-npm", "g1"): {"_fetch/a7-npm/g1/record.json": "fb4212e565e4233c86ef4432ec48a3e09d2bd031f6ffc84a875cc46e2ff84f8d"},
               ("a7-arxiv", "d1"): {"_fetch/a7-arxiv/d1/record.json": "854b13dd774ada2c5d94ef19ac4ba6b34685590ab293d49338c9f6eaa9ca11a6",
                                    "_fetch/a7-arxiv/d1/d6_report.json": "e3d01b8e784ba0af38c16df0632f98204ef6f9dc4e014b37b2e937deaddc36e4"},
               ("a8-supermemory-bin", "b1"): {
                   "_fetch/a8-supermemory-bin/b1/record.json": "f8680bc9a89083539d36335958ad89c7508856133eb903513e964d54429316db",
                   "_install/a8-supermemory-bin/b1/bin_record.json": "2151a437255987690da9c1831877ed1d06bb23c40c3c3f7bbea3567b7b0dd817"},
               ("a7-arxiv-src", "s1"): {"_fetch/a7-arxiv-src/s1/record.json": "88e21cb00817182c538bed636ea8f748714f43f2ed49740b647b3c776d013d29",
                                        "_fetch/a7-arxiv-src/s1/d8_report.json": "98c828f16e0e88d2821c084ce9f0a973736bf5cf14803ef22200944412ae8803"},
               ("a7-arxiv-src", "s2"): {"_fetch/a7-arxiv-src/s2/record.json": "d53ebd4c2f6e868b16aa04285eec65a950a81a58b510cb5fea712d6f509337c6",
                                        "_fetch/a7-arxiv-src/s2/d8_report.json": "71381d3cf47e30e49e081f6d812676274283ab82f5cb7a598595717109866fa8"}}
NPM_PROBLEMS = ["job 0: the fetch child exited with 3", "job 0 request npm:package-document: Refused: status 404"]
PREREG = {"research/v3/PREREG-V3-rev1.md": "1" * 64, "research/v3/PREREG-V3-AMENDMENTS.md": "2" * 64}

print("- the declared lists are the auditor's -")
check("F7-1: CLEARED_A7 is exactly the auditor's twelve runs - the four of Q-F7, a7-github g1, a7-hf-d d1, a7-arxiv d2, "
      "a8-supermemory-bin b2, a7-hf h1, py-base-312 p1, a7-github-2 g1 and a7-arxiv-src s3 - with their twenty-six "
      "record files by sha256, "
      "and FAILED_A7 is "
      "a7-npm g1 (npm answers 404; revision 1's channel, erratum A3 T32), a7-arxiv d1 (429, nothing read), "
      "a8-supermemory-bin b1 (R-GHR-ISS, superseded by b2), a7-arxiv-src s1 (301 to oaipmh.arxiv.org, the e-print not "
      "asked for) and a7-arxiv-src s2 (the e-print's 301 to /src/, not followed), each by its records' sha256 with the "
      "reason named",
      ok(lambda: {(e["window"], e["run"]): e["files"] for e in F7.CLEARED_A7} == WANT_CLEARED
         and {(e["window"], e["run"]): e["files"] for e in F7.FAILED_A7} == WANT_FAILED
         and "404" in F7.FAILED_A7[0]["reason"] and "T32" in F7.FAILED_A7[0]["reason"]
         and "429" in F7.FAILED_A7[1]["reason"] and "R-GHR-ISS" in F7.FAILED_A7[2]["reason"]
         and "superseded by b2" in F7.FAILED_A7[2]["reason"] and "oaipmh.arxiv.org" in F7.FAILED_A7[3]["reason"]
         and "not asked for" in F7.FAILED_A7[3]["reason"] and "/src/2501.13956v1" in F7.FAILED_A7[4]["reason"]),
      str([(e["window"], e["run"]) for e in getattr(F7, "CLEARED_A7", [])]))
check("F7-2: a7-npm-d d1 is cleared by its ruling note - its two problems verbatim, the one excluded request its 404",
      ok(lambda: (npm := next(e for e in F7.CLEARED_A7 if e["window"] == "a7-npm-d"))["problems_verbatim"] == NPM_PROBLEMS
         and npm["excluded"] == [{"job": 0, "requests": ["npm:package-document"]}] and "404" in npm["note"]
         and all(not e.get("problems_verbatim") for e in F7.CLEARED_A7 if e["window"] != "a7-npm-d")), "")


def put(root: Path, rel: str, obj: dict) -> str:
    f = root / Path(*rel.split("/"))
    f.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj).encode("utf-8")
    f.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


B2_SHA = "d8fb2ac0d52eeb230ad15dc8bf70dbc2ae481f0f8cfaeec70d97f7955ce71c47"


def bin_rec(docs_sha: str) -> dict:
    """a8-supermemory-bin b2's bin_record in its real shape - the fields the binaries section reads (Q-BIN-1 = O-a)."""
    exe = "supermemory-server-windows-x64.exe"
    return {"window": "a8-supermemory-bin", "run": "b2", "repo": "supermemoryai/supermemory", "tag": "server-v0.0.8",
            "commit": "5d2b5855fe492a3682a1cde4a255e2db0c4db595", "version": "0.0.8", "sha256": B2_SHA,
            "bytes": 291315712, "binary_started": False, "problems": [], "sums_line": {"name": exe, "sha256": B2_SHA},
            "docs_record": {"path": "D:/Coding/_nevertwice_polygon/runs/v3/_fetch/a7-docs/d1/d4_report.json",
                            "sha256": docs_sha},
            "job": {"assets": [{"name": exe, "size": 291315712, "digest": "sha256:" + B2_SHA},
                               {"name": exe + ".sha256", "size": 101, "digest": "sha256:" + "c4" * 32}]}}


def base_rec() -> dict:
    """W2 py-base-312 p1's record in its real shape - the fields the bases section and the issuers read."""
    return {"window": "py-base-312", "version": "3.12.10", "sha512_verified": True,
            "nupkg_sha256": "0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8",
            "python_exe_sha256": "4d6f5f81a4bca11191c4c7c6b43632694d0a4ce74e068619d8fdc161d469859a",
            "tools_tree_sha256": "41905088391907e4de849461bbeac119f1d1dd2c1a3ceacbcf202d15186ab017", "files": 1322,
            "package": "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg",
            "peer": {"issuer_o": "Microsoft Corporation", "issuer_cn": "Microsoft TLS G2 ECC CA OCSP 02",
                     "subject_cn": "api.nuget.org"},
            "checks": {"version_ok": True, "venv_ok": True, "tools_unchanged_by_checks": True},
            "check": {"complete": True, "native_hits": 0, "fs_hits": 0}}


def world(tag: str, *, npm_problems=NPM_PROBLEMS, gh_org="Sectigo Limited"):
    """A fake runs tree shaped like the real one: (runs_root, cleared, failed), each file by its fake sha256."""
    r = TMP / tag / "runs"
    gh = ["api.github.com", gh_org, "Sectigo Public Server Authentication CA DV E36"]
    rec = {"a7-discovery": {"window": "a7-discovery", "run": "d1", "problems": [], "hosts": ["api.github.com", "huggingface.co"],
                            "issuers": [gh, ["huggingface.co", "Amazon", "Amazon RSA 2048 M01"]]},
           "a7-docs": {"window": "a7-docs", "run": "d1", "problems": [], "hosts": ["api.github.com"], "issuers": [gh]},
           "a7-cognee-tag": {"window": "a7-cognee-tag", "run": "d1", "problems": [], "hosts": ["api.github.com"], "issuers": [gh]},
           "a7-npm-d": {"window": "a7-npm-d", "run": "d1", "problems": list(npm_problems), "hosts": ["registry.npmjs.org"],
                        "issuers": [["registry.npmjs.org", "Google Trust Services", "WE1"]]},
           "a7-github": {"window": "a7-github", "run": "g1", "problems": [], "hosts": ["raw.githubusercontent.com"],
                         "issuers": [["raw.githubusercontent.com", "Let's Encrypt", "YR1"]]},
           "a7-hf-d": {"window": "a7-hf-d", "run": "d1", "problems": [], "hosts": ["huggingface.co"],
                       "issuers": [["huggingface.co", "Amazon", "Amazon RSA 2048 M01"]]},
           "a7-arxiv": {"window": "a7-arxiv", "run": "d2", "problems": [], "hosts": ["export.arxiv.org"],
                        "issuers": [["export.arxiv.org", "Certainly", "Certainly Intermediate R1"]]},
           # s3's form: the three declared hosts, each tunnelled, each issued by Certainly (Fastly)
           "a7-arxiv-src": {"window": "a7-arxiv-src", "run": "s3", "problems": [],
                            "hosts": ["export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org"],
                            "catcher": [{"host": h, "port": 443, "tunnelled": True, "refused": False, "hop_status": 200}
                                        for h in ("export.arxiv.org", "oaipmh.arxiv.org", "arxiv.org")],
                            "issuers": [[h, "Certainly", "Certainly Intermediate R1"]
                                        for h in ("arxiv.org", "export.arxiv.org", "oaipmh.arxiv.org")]},
           "a7-github-2": {"window": "a7-github-2", "run": "g1", "problems": [], "hosts": ["raw.githubusercontent.com"],
                           "catcher": [{"host": "raw.githubusercontent.com", "port": 443, "tunnelled": True, "refused": False,
                                        "hop_status": 200}],
                           "issuers": [["raw.githubusercontent.com", "Let's Encrypt", "YR1"]]},
           "a7-hf": {"window": "a7-hf", "run": "h1", "problems": [], "hosts": ["huggingface.co", "us.aws.cdn.hf.co"],
                     "catcher": [{"host": h, "port": 443, "tunnelled": True, "refused": False, "hop_status": 200}
                                 for h in ("huggingface.co", "us.aws.cdn.hf.co")],
                     "issuers": [["huggingface.co", "Amazon", "Amazon RSA 2048 M01"],
                                 ["us.aws.cdn.hf.co", "Amazon", "Amazon RSA 2048 M04"]]},
           # b2's form: four declared hosts, its catcher tunnelled three (Q-DH-1: objects.githubusercontent.com never)
           "a8-supermemory-bin": {"window": "a8-supermemory-bin", "run": "b2", "problems": [],
                                  "hosts": ["api.github.com", "github.com", "objects.githubusercontent.com",
                                            "release-assets.githubusercontent.com"],
                                  "catcher": [{"host": h, "port": 443, "tunnelled": True, "refused": False, "hop_status": 200}
                                              for h in ("api.github.com", "api.github.com", "github.com",
                                                        "release-assets.githubusercontent.com")],
                                  "issuers": [["api.github.com", "Sectigo Limited", "Sectigo Public Server Authentication CA DV E36"],
                                              ["github.com", "Sectigo Limited", "Sectigo Public Server Authentication CA DV E36"],
                                              ["release-assets.githubusercontent.com", "Let's Encrypt", "YR1"]]}}
    cleared, shas = [], {}
    for e in copy.deepcopy(F7.CLEARED_A7):
        w = e["window"]
        files = {}
        for rel in e["files"]:
            obj = (rec[w] if rel.endswith("/record.json")
                   else bin_rec(shas["_fetch/a7-docs/d1/d4_report.json"]) if rel.endswith("/bin_record.json")
                   else base_rec() if rel.startswith("_tools/")
                   else {"window": w, "problems": []})
            files[rel] = shas[rel] = put(r, rel, obj)
        e["files"] = files
        cleared.append(e)
    failed = copy.deepcopy(F7.FAILED_A7)
    for e in failed:
        e["files"] = {rel: put(r, rel, {"window": e["window"], "run": e["run"],
                                        "problems": ["job 0: the fetch child exited with 3"]}) for rel in e["files"]}
    return r, cleared, failed


def filled_a7() -> tuple[dict, dict]:
    """PINS_A7 as the window a7-github would leave it: every pin filled through fill() on a copy."""
    pins = copy.deepcopy(CP.PINS_A7_DECLARED)
    filled = {}
    for n, p in sorted(pins.items()):
        v = {"revision": p["revision"], "sha256": hashlib.sha256(n.encode()).hexdigest(), "bytes": 9,
             "licence_found": {"CC-BY-SA-4.0 (data); MIT (code)": "MIT"}.get(p["licence"], p["licence"]),
             "licence_source": "test", "from": "a7-github g1 pin_fill 0123456789ab"}
        CP.fill(n, revision=v["revision"], sha256=v["sha256"], size=v["bytes"], licence_found=v["licence_found"], pins=pins)
        pins[n]["filled_from"] = v["from"]
        filled[n] = v
    return pins, filled


print("\n- a whole build -")
try:
    R, CL, FA = world("ok")
    PINS7, FILLED7 = filled_a7()
    OUT, oerr = F7.build(R, pins=PINS7, filled=FILLED7, prereg=PREREG, cleared=CL, failed=FA), None
except Exception as e:  # noqa: BLE001
    OUT, oerr = {}, f"{type(e).__name__}: {e}"
    R = CL = FA = PINS7 = FILLED7 = None
check("F7-3: the fragment holds the four cleared runs and the failed one, each file by sha256 with its problem count",
      ok(lambda: oerr is None and [(w["window"], w["run"]) for w in OUT["windows"]] == [(e["window"], e["run"]) for e in CL]
         and OUT["windows"][3]["problems"] == {k: (2 if k.endswith("record.json") else 0) for k in CL[3]["files"]}
         and [(f["window"], f["run"]) for f in OUT["failed_runs"]] == [("a7-npm", "g1"), ("a7-arxiv", "d1"),
                                                                        ("a8-supermemory-bin", "b1"),
                                                                        ("a7-arxiv-src", "s1"),
                                                                        ("a7-arxiv-src", "s2")]), str(oerr))
check("F7-16: the fragment's binaries section pins supermemory-local's server (Q-BIN-1 = O-a) - a8-supermemory-bin b2: "
      "server-v0.0.8 at 5d2b5855, the Windows asset, its sha256 = the release digest, its size, the a7-docs record it was "
      "read from and its window record, each by sha256",
      ok(lambda: oerr is None and OUT["binaries"] == {"a8-supermemory-bin": {
          "run": "b2", "repo": "supermemoryai/supermemory", "tag": "server-v0.0.8",
          "commit": "5d2b5855fe492a3682a1cde4a255e2db0c4db595", "asset": "supermemory-server-windows-x64.exe",
          "sha256": B2_SHA, "bytes": 291315712, "digest": "sha256:" + B2_SHA,
          "docs_record": {"path": "_fetch/a7-docs/d1/d4_report.json",
                          "sha256": {r: s for e in CL for r, s in e["files"].items()}["_fetch/a7-docs/d1/d4_report.json"]},
          "window_record": {"path": "_fetch/a8-supermemory-bin/b2/record.json",
                            "sha256": {r: s for e in CL for r, s in e["files"].items()}[
                                "_fetch/a8-supermemory-bin/b2/record.json"]}}}), str(oerr))
check("F7-17: the fragment's bases section pins W2's py-base-312 p1 - 3.12.10, the nupkg's, python.exe's and the tools "
      "tree's sha256, 1322 files, its record by sha256 - where the product venvs' base lies in FREEZE-V3",
      ok(lambda: oerr is None and OUT["bases"] == {"py-base-312": {
          "run": "p1", "version": "3.12.10",
          "nupkg_sha256": "0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8",
          "python_exe_sha256": "4d6f5f81a4bca11191c4c7c6b43632694d0a4ce74e068619d8fdc161d469859a",
          "tools_tree_sha256": "41905088391907e4de849461bbeac119f1d1dd2c1a3ceacbcf202d15186ab017", "files": 1322,
          "record": {"path": "_tools/py-base-312/py-base-312.json",
                     "sha256": {r: s for e in CL for r, s in e["files"].items()}["_tools/py-base-312/py-base-312.json"]}}}),
      str(oerr))
check("F7-15: a8-supermemory-bin b2's window names objects.githubusercontent.com declared_not_reached (Q-DH-1 = O-a) - "
      "declared, never tunnelled, so no issuer is asked of it; no other window carries the list",
      ok(lambda: oerr is None and {w["window"]: w.get("declared_not_reached") for w in OUT["windows"]
                                   if "declared_not_reached" in w} == {"a8-supermemory-bin": ["objects.githubusercontent.com"]}
         and "objects.githubusercontent.com" not in OUT["issuers"]), str(oerr))
check("F7-4: its pins are PINS_A7 as filled - all 75 (a7-github's 52, a7-hf's 10, a7-github-2's 12 and Zep's e-print), "
      "each with where "
      "it came from - "
      "and its issuers the public ones the records name",
      ok(lambda: sorted(OUT["pins"]) == sorted(CP.PINS_A7_DECLARED) and len(OUT["pins"]) == 75
                         and all(v["filled_from"] == "a7-github g1 pin_fill 0123456789ab" for v in OUT["pins"].values())
                         and sorted(OUT["issuers"]) == ["api.github.com", "api.nuget.org", "arxiv.org", "export.arxiv.org",
                                                        "github.com", "huggingface.co", "oaipmh.arxiv.org",
                                                        "raw.githubusercontent.com", "registry.npmjs.org",
                                                        "release-assets.githubusercontent.com", "us.aws.cdn.hf.co"]),
      str(oerr))
check("F7-5: the prereg section is the given revision 1 and amendments sha256 at the anchor - and nothing of A3's "
      "(no models, venvs, facts, d1_tag, prereg_rev1)", ok(lambda: OUT["prereg"] == PREREG and not set(OUT) & {
          "models", "venvs", "facts", "d1_tag", "local_v2", "prereg_rev1"}), str(sorted(OUT)))

print("\n- refusals, nothing written -")
try:
    R2, CL2, FA2 = world("moved")
    f_moved = R2 / "_fetch" / "a7-docs" / "d1" / "d4_report.json"
    f_moved.write_bytes(f_moved.read_bytes() + b" ")
    unfilled = copy.deepcopy(PINS7)
    unfilled["cognee_run_beam_eval"]["sha256"] = None
    R3, CL3, FA3 = world("npm", npm_problems=NPM_PROBLEMS + ["job 0 request npm:search: Refused: status 500"])
except Exception as e:  # noqa: BLE001 - the rows below FAIL by name
    RAISED.append(f"worlds: {type(e).__name__}: {e}")
    R2 = CL2 = FA2 = R3 = CL3 = FA3 = unfilled = R = CL = FA = PINS7 = FILLED7 = None
check("F7-6: a record whose bytes moved (another sha256 than the list's) is refused by name",
      refused(lambda: F7.build(R2, pins=PINS7, filled=FILLED7, prereg=PREREG, cleared=CL2, failed=FA2),
              "_fetch/a7-docs/d1/d4_report.json: sha256"))
check("F7-7: a PINS_A7 pin not filled stops the build, named - A7 is not complete",
      refused(lambda: F7.build(R, pins=unfilled, filled=FILLED7, prereg=PREREG, cleared=CL, failed=FA),
              "cognee_run_beam_eval: not filled - A7 is not complete"))
for label, pr in (("no amendments", {"research/v3/PREREG-V3-rev1.md": "1" * 64}),
                  ("no revision 1", {"research/v3/PREREG-V3-AMENDMENTS.md": "2" * 64}),
                  ("a sha that is no sha256", {**PREREG, "research/v3/PREREG-V3-rev1.md": "x"})):
    check(f"F7-8: a prereg section with {label} is refused, named",
          refused(lambda pr=pr: F7.build(R, pins=PINS7, filled=FILLED7, prereg=pr, cleared=CL, failed=FA), "prereg"))
check("F7-9: a7-npm-d with a problem its ruling note does not name verbatim is refused",
      refused(lambda: F7.build(R3, pins=PINS7, filled=FILLED7, prereg=PREREG, cleared=CL3, failed=FA3), "verbatim"))

print("\n- the prereg at the anchor, never the working copy -")
G = TMP / "repo"
(G / "research" / "v3").mkdir(parents=True)
env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*a):
    return subprocess.run(["git", "-C", str(G), *a], capture_output=True, env=env, check=True).stdout


try:
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    for name, body in (("PREREG-V3-rev1.md", b"rev1\n"), ("PREREG-V3-AMENDMENTS.md", b"A1\n"), ("other.md", b"x\n")):
        (G / "research" / "v3" / name).write_bytes(body)
    git("add", "-A")
    git("commit", "-q", "-m", "c")
    head = git("rev-parse", "HEAD").decode().strip()
    (G / "research" / "v3" / "PREREG-V3-AMENDMENTS.md").write_bytes(b"A1\nA2 not committed\n")    # the working copy moves
    got, gerr = F7.prereg_at(G, head), None
except Exception as e:  # noqa: BLE001
    got, gerr, head = None, f"{type(e).__name__}: {e}", None
check("F7-10: prereg_at(<repo>, <anchor>) gives every research/v3/PREREG-V3*.md tracked at the anchor by its git blob's "
      "sha256 - an edit in the working copy does not move it - and the anchor it was read at",
      ok(lambda: gerr is None and got == {"anchor": head, "files": {
          "research/v3/PREREG-V3-AMENDMENTS.md": hashlib.sha256(b"A1\n").hexdigest(),
          "research/v3/PREREG-V3-rev1.md": hashlib.sha256(b"rev1\n").hexdigest()}}), str(gerr or got))

print("\n- the command -")
check("F7-11: the command writes research/v3/freeze_a7.json by default and never names freeze_a3.json; it reads the "
      "prereg at the worktree's own HEAD and builds from PINS_A7 and FILLED_A7", ok(lambda: "freeze_a7.json" in inspect.getsource(F7.main)
                                             and "freeze_a3.json" not in inspect.getsource(F7.main)
                                             and "prereg_at(" in inspect.getsource(F7.main)
                                             and "pins=CP.PINS_A7, filled=CP.FILLED_A7" in inspect.getsource(F7.main)), "")
# the auditor's FZ7 (2026-09-30): main without its prereg_anchor line stayed green - the command itself is run here, on
# the fixture world (its lists swapped in, the contract and the table stubbed), reading the prereg at this tree's HEAD
from types import SimpleNamespace as _NS  # noqa: E402

main_out, main_err = None, None
if F7 is not None and R is not None:
    _saved = (F7._load, F7.CLEARED_A7, F7.FAILED_A7)
    try:
        F7._load = lambda name, path: {"v3_launch": _NS(Contract=_NS(default=lambda: _NS(runs_root=R))),
                                       "v3_corpus_pin_freeze_a7": _NS(PINS_A7=PINS7, FILLED_A7=FILLED7)}[name]
        F7.CLEARED_A7, F7.FAILED_A7 = CL, FA
        rc_ = F7.main(["--out", str(TMP / "freeze_a7_main.json")])
        main_out = (rc_, json.loads((TMP / "freeze_a7_main.json").read_bytes()))
    except Exception as e:  # noqa: BLE001
        main_err = f"{type(e).__name__}: {e}"
    finally:
        F7._load, F7.CLEARED_A7, F7.FAILED_A7 = _saved
try:
    head_ = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=True).stdout.decode().strip()
except Exception as e:  # noqa: BLE001
    head_ = f"{type(e).__name__}"
check("F7-13: the command's freeze_a7.json carries prereg_anchor - the 40-hex HEAD commit of the repository it was built "
      "in - and the prereg of that commit", ok(lambda: main_err is None and main_out[0] == 0
                                                 and main_out[1]["prereg_anchor"] == head_
                                                 and len(head_) == 40 and all(c in "0123456789abcdef" for c in head_)
                                                 and sorted(main_out[1]["prereg"]) == sorted(PREREG)),
      str(main_err or (main_out or [None, {}])[1].get("prereg_anchor")))
FIX7 = ROOT / "tests" / "fixtures" / "v3_pin_fill"
try:
    fix7 = {p_.relative_to(FIX7).parent.as_posix(): hashlib.sha256(p_.read_bytes()).hexdigest()
            for p_ in FIX7.rglob("pin_fill.json") if p_.relative_to(FIX7).parts[0].startswith("a7-")}
    decl7 = {rel.split("/", 2)[1] + "/" + rel.split("/")[2]: s for e in F7.CLEARED_A7 for rel, s in e["files"].items()
             if rel.endswith("/pin_fill.json")}
    f714 = (fix7 == decl7 == {"a7-github/g1": "acae9e52bc935a5752f384fbcc0cd26521e20c97878056d3318963f782296472",
                              "a7-hf/h1": "449a89fca362ab4aa85d6408236cb8ba37ab56e04c2a6ddfeea75f59e8094afe",
                              "a7-github-2/g1": "fa5375b6678530c023ec875bed830accd447e6e132abf5aec167207e6b3a28b7"}), None
except Exception as e:  # noqa: BLE001
    f714 = (False, f"{type(e).__name__}: {e}")
check("F7-14: the A7 pin_fill shas CLEARED_A7 names are the committed evidence's (tests/fixtures/v3_pin_fill/a7-*)",
      f714[0] is True, str(f714[1]))
check("F7-12: the same records give the same bytes (sorted JSON, LF)",
      ok(lambda: F7.render(OUT) == F7.render(json.loads(F7.render(OUT))) and b"\r\n" not in F7.render(OUT)), "")
check("no row's condition raised", RAISED == [], str(RAISED))
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 freeze a7: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
