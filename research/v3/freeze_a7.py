#!/usr/bin/env python3
"""PREREG-V3 A7 (the auditor's Q-F7 = O-a, 2026-09-30): research/v3/freeze_a7.json, the A7 fragment of FREEZE-V3.

A thin wrapper over freeze_a3.build (Q-A7-P2-1 O-a: freeze_a3.json is the A3 table as filled and is never rewritten), with
A7's own lists and table:
* windows: the A7 runs the auditor cleared himself (m6 --window PASS), each record file by its sha256 - a record that is
  missing or whose bytes moved stops the build, nothing is written; a cleared record WITH problems stops it unless its
  entry carries the ruling note, the problems verbatim and the excluded request (a7-npm-d d1: npm's 404 by construction);
* failed_runs: the runs that were not cleared, by sha256 and a one-line reason - A7's (FAILED_A7) and A8's (FAILED_A8,
  the auditor 2026-09-30: an A8 window's failure is never an A7 list's), each with its stage (A8 for every a8- window);
* issuers: every TLS issuer the records name is public (R-A3-7), every contacted host has one - contacted is what a
  record's catcher tunnelled (Q-DH-1 = O-a); a declared host never tunnelled is its window's declared_not_reached
  (a8-supermemory-bin b2: objects.githubusercontent.com);
* pins: PINS_A7 as filled (FILLED_A7) - every pin filled, or the build stops ("A7 is not complete");
* binaries: supermemory-local's server from a8-supermemory-bin b2 (Q-BIN-1 = O-a), by freeze_a3's binaries section;
* bases: W2's py-base-312 p1 (the product venvs' base), by freeze_a3's bases section - A8's windows are cleared here
  beside A7's (the supermemory binary already is), so FREEZE-V3 reads one fragment for both;
* prereg: the sha256 of every research/v3/PREREG-V3*.md tracked at the anchor commit, by its git blob - revision 1 and
  the amendments at least - and the anchor itself; never the working copy.
A window after these (a7-github, a7-arxiv, a8-supermemory-bin, a7-hf-d, a7-hf) joins CLEARED_A7 only as a line written
after the auditor's m6/m5/secret_scan of its run - never in advance. The output is sorted JSON with LF line ends.

    python research/v3/freeze_a7.py --out research/v3/freeze_a7.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PREREG_NEEDED = ("research/v3/PREREG-V3-rev1.md", "research/v3/PREREG-V3-AMENDMENTS.md")

#: The A7 runs the auditor cleared (2026-09-30 01:3x, m6 --window with --manifest-rev b322514: PASS on each).
CLEARED_A7 = [
    {"window": "a7-discovery", "run": "d1", "kind": "discovery", "files": {
        "_fetch/a7-discovery/d1/record.json": "1689534abb103971d502774dfd2a4566d6947c3efaeda552dbe3704b72ed037e",
        "_fetch/a7-discovery/d1/d3_report.json": "dff73cfea405f3963f32880abd1ced9c14d1da7dec1888778e517244bee6eca9"}},
    {"window": "a7-docs", "run": "d1", "kind": "report", "files": {
        "_fetch/a7-docs/d1/record.json": "22cf0b04276ae8f7b5aa66a62e644bc589cdaec4cc2f74ea061a4032606dfffa",
        "_fetch/a7-docs/d1/d4_report.json": "492f92b77098b80eeca7494bb3550a3988aa596873850ca1d1f269bae6a9fcd8"}},
    {"window": "a7-cognee-tag", "run": "d1", "kind": "report", "files": {
        "_fetch/a7-cognee-tag/d1/record.json": "136e0bf762df91ded98b80a53e282c89cb1424035933d4df8c458675001fd721",
        "_fetch/a7-cognee-tag/d1/d5_report.json": "1b5d58bfb9f484249ba0ac32f14c673c0db27f140a0f07825426ae34934e6620"}},
    {"window": "a7-npm-d", "run": "d1", "kind": "discovery", "files": {
        "_fetch/a7-npm-d/d1/record.json": "8aeed9ecfc3b86edc80573d2b552dd7d08f197d10e6231f8b010ef7c6b1e9ce2",
        "_fetch/a7-npm-d/d1/discovery.json": "8e3ef3ff8c6f73673ce6e6e7ba26cdd0fda5865ca75824433aab0f75af9af434"},
     "note": "2 problems, both npm's 404 for the package document of supermemory-server - the discovery's finding by "
             "construction (the auditor's m6 note): the package is not on npm, and revision 1's channel was wrong "
             "(erratum A3, T32); the registry search in the same job is what the window read",
     "problems_verbatim": ["job 0: the fetch child exited with 3",
                           "job 0 request npm:package-document: Refused: status 404"],
     "excluded": [{"job": 0, "requests": ["npm:package-document"]}]},
    # after the auditor's m6 --window, m6 --pins-rev, m5 --launch-dir --set-aside and secret_scan (2026-09-30 03:0x)
    {"window": "a7-github", "run": "g1", "kind": "fetch", "files": {
        "_fetch/a7-github/g1/record.json": "e61f6d9d7f3d0878e108eb90c375750c674bf462f3d859a5ad321063a3b61334",
        "_fetch/a7-github/g1/place_record.json": "41a809505ec2f4bddf1ba5c6a09d729c0544dfee1a99bb18829e70a95c6ffcef",
        "_fetch/a7-github/g1/pin_fill.json": "acae9e52bc935a5752f384fbcc0cd26521e20c97878056d3318963f782296472"}},
    # a7-hf's host (the auditor's fixing, 2026-09-30 03:0x: us.aws.cdn.hf.co), after his m6/m5/secret_scan of the run
    {"window": "a7-hf-d", "run": "d1", "kind": "report", "files": {
        "_fetch/a7-hf-d/d1/record.json": "8a6f7663317561f82842637334e1701a3769ba498c194912d52185b10cc4ed82",
        "_fetch/a7-hf-d/d1/d7_report.json": "2ec9f5f251e755860f5cc6ea887766f939a8fe2d93b6d7a3ba165220c69c6ca4"}},
    # Zep's paper (R3: arXiv 2501.13956 v1, the auditor's choice from this report), after his checks of the run
    {"window": "a7-arxiv", "run": "d2", "kind": "report", "files": {
        "_fetch/a7-arxiv/d2/record.json": "8ec76a098a21ce6d2f82ca1baf7c7900363f252b712d1b17d11387309fedb3c5",
        "_fetch/a7-arxiv/d2/d6_report.json": "9c93889a05577ce03e077e1b68e2f826e03f211b72f9b7e7a4023d5c11d3e638"}},
    # supermemory-local's server binary (T32), re-run after R-GHR-ISS with every request's issuer recorded
    {"window": "a8-supermemory-bin", "run": "b2", "kind": "binary", "files": {
        "_fetch/a8-supermemory-bin/b2/record.json": "83b5bc3652ad1a4119fa29a6b93b181d5b05bc7894c664018a664603334cbc91",
        "_install/a8-supermemory-bin/b2/bin_record.json": "6de665abf22c26c012c8aebc2588c71ea08ad1ab2559c128369277d7a8ad3b7d"}},
    # all-MiniLM-L6-v2's ten files (BEAM's alignment, S5), after the auditor's m6/m5/secret_scan of the run (05:3x)
    {"window": "a7-hf", "run": "h1", "kind": "fetch", "files": {
        "_fetch/a7-hf/h1/record.json": "970344e56358bcb3c137d2f8c5c7ab9aa855c792f434d9ef31d7c33adbfcdbd7",
        "_fetch/a7-hf/h1/place_record.json": "3aa4ab44d4d9d1e37249b57052f3405edafd1e8679ef3784010c10d81175ff48",
        "_fetch/a7-hf/h1/pin_fill.json": "449a89fca362ab4aa85d6408236cb8ba37ab56e04c2a6ddfeea75f59e8094afe"}},
    # LME's run_generation.sh and AMA's method code (Q-TPL-1 = O-b, Q-TPL-4), after the auditor's m6/m5/secret_scan (07:5x)
    {"window": "a7-github-2", "run": "g1", "kind": "fetch", "files": {
        "_fetch/a7-github-2/g1/record.json": "ad6ff8fb61bc456db6c30c5d1aef03382580cdfc792f3565beaf2a288ebd4272",
        "_fetch/a7-github-2/g1/place_record.json": "aedefc7e632e9a46101287b47ad0f82629e184047db0a4a88cb8d55c64be9f9c",
        "_fetch/a7-github-2/g1/pin_fill.json": "fa5375b6678530c023ec875bed830accd447e6e132abf5aec167207e6b3a28b7"}},
    # W2: the product venvs' base, CPython 3.12.10 from NuGet (the auditor's clearing, 2026-09-30 06:4x: the nupkg's
    # sha256 and sha512, python.exe, 3.12.10 the newest 3.12, m5 PASS, secret_scan 0)
    {"window": "py-base-312", "run": "p1", "kind": "base", "files": {
        "_tools/py-base-312/py-base-312.json": "52d4aeae4c3701b098ed1dacbc5ba04c0181c13a06abf580d5442b0772846862"}},
    # Zep's paper's arXivRaw record (CC BY-NC-SA 4.0, v1 only) and e-print (tar.gz, 22911 bytes, d98c3a61...), the
    # auditor's clearing (2026-09-30 09:2x: m6 --window PASS, m5 PASS, secret_scan 0) - the source of pin zep_paper_src
    {"window": "a7-arxiv-src", "run": "s3", "kind": "report", "files": {
        "_fetch/a7-arxiv-src/s3/record.json": "8d298d088a29a7f55e70e808184b6be23dfbf2b9f2527b44c549c42e6d2bb847",
        "_fetch/a7-arxiv-src/s3/d8_report.json": "8870d618f48874fe6244b74845bed011c2f882d319ce41f58a93afff31e195e4"}},
]
#: The A7 runs that were NOT cleared - kept by sha256 with the reason, never used.
FAILED_A7 = [
    {"window": "a7-npm", "run": "g1", "files": {
        "_fetch/a7-npm/g1/record.json": "fb4212e565e4233c86ef4432ec48a3e09d2bd031f6ffc84a875cc46e2ff84f8d"},
     "reason": "supermemory-server is not on npm (the registry answered 404): revision 1's channel was wrong - erratum A3 "
               "(T32), the vendor's release binary instead"},
    {"window": "a7-arxiv", "run": "d1", "files": {
        "_fetch/a7-arxiv/d1/record.json": "854b13dd774ada2c5d94ef19ac4ba6b34685590ab293d49338c9f6eaa9ca11a6",
        "_fetch/a7-arxiv/d1/d6_report.json": "e3d01b8e784ba0af38c16df0632f98204ef6f9dc4e014b37b2e937deaddc36e4"},
     "reason": "export.arxiv.org answered 429 to the one request (rate-limited, not retried): nothing was read - the "
               "auditor's Q-ARX-1, a new run after a pause"},
    {"window": "a8-supermemory-bin", "run": "b1", "files": {
        "_fetch/a8-supermemory-bin/b1/record.json": "f8680bc9a89083539d36335958ad89c7508856133eb903513e964d54429316db",
        "_install/a8-supermemory-bin/b1/bin_record.json": "2151a437255987690da9c1831877ed1d06bb23c40c3c3f7bbea3567b7b0dd817"},
     "reason": "TLS issuer not recorded by gh_release (R-GHR-ISS); superseded by b2; bytes identical"},
    {"window": "a7-arxiv-src", "run": "s1", "files": {
        "_fetch/a7-arxiv-src/s1/record.json": "88e21cb00817182c538bed636ea8f748714f43f2ed49740b647b3c776d013d29",
        "_fetch/a7-arxiv-src/s1/d8_report.json": "98c828f16e0e88d2821c084ce9f0a973736bf5cf14803ef22200944412ae8803"},
     "reason": "export.arxiv.org answered the OAI request with 301 to oaipmh.arxiv.org (not followed: no redirect was "
               "declared); the e-print was not asked for - the auditor's Q-D8-5 = O-b"},
    {"window": "a7-arxiv-src", "run": "s2", "files": {
        "_fetch/a7-arxiv-src/s2/record.json": "d53ebd4c2f6e868b16aa04285eec65a950a81a58b510cb5fea712d6f509337c6",
        "_fetch/a7-arxiv-src/s2/d8_report.json": "71381d3cf47e30e49e081f6d812676274283ab82f5cb7a598595717109866fa8"},
     "reason": "the e-print answered 301 to arxiv.org/src/2501.13956v1, not followed (0 redirects); its OAI part was "
               "read (CC BY-NC-SA 4.0, v1 only) and s3 reads it again - the auditor's Q-D8-6 = O-a"},
]
#: The A8 runs that were NOT cleared (the auditor 2026-09-30 12:5x: FREEZE-V3 sees both with their reasons) - kept by
#: sha256 with the reason, never used; their venvs moved to polygon\_failed.
FAILED_A8 = [
    {"window": "a8-pypi-scorer_v3", "run": "p1", "files": {
        "_fetch/a8-pypi-scorer_v3/p1/record.json": "cca20df56fc66f680075eb22ff52e552a5d2cefa71ae7b227f15a9a7d6deb148",
        "_install/a8-pypi-scorer_v3/p1/install_record.json":
            "a041db3f0a5bf0fa1418941f7fb7760e550f0129de68073a33e8ae4b36da1a59"},
     "reason": "site-packages held setuptools' distutils-precedence.pth, a site problem before the auditor's Q-SC-PTH "
               "(which then allowed exactly that file by owner, version and sha256); the venv moved to "
               "polygon\\_failed\\scorer_v3_p1 - p2 after the gate"},
    {"window": "a8-pypi-mem0_v3", "run": "x", "files": {
        "_fetch/a8-pypi-mem0_v3/x/record.json": "27df502d0565b05ee7cc8b94acff726d6e1df9684cec45ea9e09b546b614291a",
        "_install/a8-pypi-mem0_v3/x/install_record.json":
            "80a69f12f23c4d07970d3095847a569db7fe1d802ecfe92808839fdb7bfd74ca"},
     "reason": "test-triggered window without GO (SP-7), 2026-09-30 09:49 - a unit row called lock_install.main() "
               "unstubbed on the old code and it opened this window; never used, the venv moved to "
               "polygon\\_failed\\mem0_v3_x, and run label x is never taken again"},
]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


F3 = _load("v3_freeze_a3_for_a7", HERE / "freeze_a3.py")
FreezeRefused = F3.FreezeRefused
render = F3.render


def prereg_at(repo: Path, rev: str) -> dict:
    """{"anchor": <the commit>, "files": {path: sha256}} for every research/v3/PREREG-V3*.md tracked at ``rev``, each
    read as its git blob - never the working copy."""
    def git(*a) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, check=True).stdout
    anchor = git("rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    names = [n for n in git("ls-tree", "--name-only", anchor, "research/v3/").decode().splitlines()
             if re.fullmatch(r"research/v3/PREREG-V3[^/]*\.md", n)]
    return {"anchor": anchor, "files": {n: hashlib.sha256(git("cat-file", "blob", f"{anchor}:{n}")).hexdigest()
                                        for n in sorted(names)}}


def build(runs_root: Path, *, pins: dict, filled: dict, prereg: dict, cleared: list | None = None,
          failed: list | None = None, unrecorded: dict | None = None) -> dict:
    """The A7 fragment from the cleared records (each by sha256); raises FreezeRefused before anything is written."""
    for need in PREREG_NEEDED:
        if not re.fullmatch(r"[0-9a-f]{64}", str(prereg.get(need))):
            raise FreezeRefused(f"the prereg section has no sha256 for {need}")
    try:
        out = F3.build(runs_root, pins=pins, filled=filled, cleared=CLEARED_A7 if cleared is None else cleared,
                       failed=FAILED_A7 + FAILED_A8 if failed is None else failed,     # A7's, then A8's
                       unrecorded={} if unrecorded is None else unrecorded)
    except FreezeRefused as e:
        if "not filled - A3 is not complete" in str(e):
            raise FreezeRefused(str(e).replace("A3 is not complete", "A7 is not complete (after its windows only)")) from None
        raise
    out.pop("prereg_rev1", None)                   # A3's own line; A7's prereg is the anchor's blobs
    for f in out["failed_runs"]:                   # the auditor 2026-09-30: every failed run names its stage
        f["stage"] = "A8" if f["window"].startswith("a8-") else "A7"
    out["prereg"] = dict(sorted(prereg.items()))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the A7 fragment of FREEZE-V3 (Q-F7)")
    ap.add_argument("--out", default=str(HERE / "freeze_a7.json"))
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    CP = _load("v3_corpus_pin_freeze_a7", HERE / "corpus_pin_v3.py")
    at = prereg_at(REPO, "HEAD")
    try:
        freeze = build(L.Contract.default().runs_root, pins=CP.PINS_A7, filled=CP.FILLED_A7, prereg=at["files"])
    except FreezeRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    freeze["prereg_anchor"] = at["anchor"]
    Path(args.out).write_bytes(render(freeze))
    print(json.dumps({"windows": len(freeze["windows"]), "failed_runs": len(freeze["failed_runs"]),
                      "pins": len(freeze["pins"]), "issuers": sorted(freeze["issuers"]), "anchor": at["anchor"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
