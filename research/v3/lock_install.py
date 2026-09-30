#!/usr/bin/env python3
"""PREREG-V3 A8 C2 (the auditor's Q-A8-1, O-a): a product arm's venv, installed from a hashed lock pip itself resolved.

install_v3_data's one-level rule refuses extras and markers, so it cannot install mem0ai or cognee; here pip's own
resolver decides the tree, and nothing else about the install is left to it:

1. the venv is created fresh from its DECLARED base (VENVS[venv]["base"], a fetch_py_base window - py-base-312 for §2.2's
   product venvs): the interpreter given must be that base's polygon/<dest>/python.exe by realpath, or nothing starts
   (the auditor's LI-1) - an offline contract spawn under its own boundary check; pip's version and its certifi bundle
   are recorded (install_v3_data.pip_facts);
2. ONE declared window ``a8-pypi-<venv>``, hosts exactly pypi.org and files.pythonhosted.org, through the catcher:
   a) pip, as a child: ``install --dry-run --ignore-installed --only-binary=:all: --report report.json <specs>`` -
      attempt 1 takes wheels only;
   b) the harness turns the report into the lock (lock_step): every spec is an exact pin found in the report - its
      distribution requested, at its version, with every extra the spec asks among those pip took (B-NLP: a dropped
      [nlp] is refused by name); one line per distribution, ``name==version
      --hash=sha256:<the index's sha256>``; a direct URL, a VCS or local source, a yanked file, a host other than
      files.pythonhosted.org, a missing or malformed sha256, a repeated name, and any file that is not a wheel are
      refused by name (a distribution that ships only an sdist is attempt 2's: built alone, with a backend pinned by
      wheels in the same window, recorded - never here);
   c) a fetch child downloads every locked wheel from files.pythonhosted.org, each checked against its sha256 as it
      streams;
3. after the window, offline: the harness re-reads every wheel from disk against the lock (verify_wheels - the index's
   sha256 "checked against the disk"), then pip installs ``--require-hashes --no-deps --no-index --find-links <the
   wheels>`` from the lock - no network, under its own boundary check;
4. the installed set (install_v3_data.installed_set over every locked distribution) and the site checks (its
   site_problems: nothing on the site that no locked distribution installed, no new .pth or *customize*, every
   hashed RECORD row matching the disk) come BEFORE any interpreter starts in the venv; then, isolated and without
   bytecode, the declared imports, the venv's own python version (it must be the base's declared version, LI-1) and
   importlib.metadata's version of every requested distribution, which must be the locked one (check_versions).
Both pip children get the same declared environment (offline_env: PIP_CONFIG_FILE = os.devnull - pip then reads no
global, user or site file, which --isolated would not skip - the cache in the polygon, no input; the resolver adds the
index URL) - the auditor's LI-2.

The record (<runs>/_install/a8-pypi-<venv>/<run>/install_record.json): the specs, pip's facts, the lock and its
sha256, each distribution's licence as its METADATA gives it, the wheels' verification, pip's output tails, every
boundary check, the installed-set digest, the imports' versions, and every problem by name. A problem stops what
comes after it.

A product venv (an arm's product; the auditor's Q-SPLIT-1..6, 2026-09-30) goes in two phases with M31 between them, so
its install is impossible without a clean M31 of the very wheels it installs:
* --download-only: step 2 and the wheels' check - pip resolves in a resolver venv inside the run's own directory (never
  the product venv, which is not made); the record download_record.json;
* M31 (research/v3/m31_check.py) reads that download's wheels and writes m31.json there, bound to the record's sha256;
* --install-from <run>, offline and only on the auditor's GO: refused by name before the venv is made unless the
  download is clean, its m31.json is that record's with nothing unprovided and no problem, nothing was installed from
  it before, the venv is not there, and every wheel is still the lock's sha256; the venv's pip must be the resolver's;
  then steps 3-4 into install_record.json beside the download record.
One call (steps 1-4 as above) remains only for a venv that is no product (the scorer's, A4).

    python research/v3/lock_install.py --venv scorer_v3 --run l1
    python research/v3/lock_install.py --venv mem0_v3 --run d1 --download-only
    python research/v3/lock_install.py --venv mem0_v3 --install-from d1
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
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOW_PREFIX = "a8-pypi-"
INDEX_HOST, FILES_HOST = "pypi.org", "files.pythonhosted.org"
HOSTS = [INDEX_HOST, FILES_HOST]
REPORT = "report.json"
WHEEL_MAX = 512 * (1 << 20)
INDEX_PROBE_MAX = 16 * (1 << 20)                 # R-PIP-ISS: one /simple/ page, read for its TLS issuer
PTH_RULE = ("Q-SC-PTH (the auditor, 2026-09-30) - allowed by name, by its owner's RECORD at the lock's version and by its "
            "bytes' sha256")
#: Why each declared (distribution, .pth) is allowed - every pth_allowed pair of VENVS has its line here, and a declared
#: file without one is never allowed. mem0_v3's two (T34: the auditor's order of 2026-09-30, pth allow by RECORD) are the
#: .pth files download a8-pypi-mem0_v3 d1's wheels carry.
PTH_WHY = {("setuptools", "distutils-precedence.pth"):
           "setuptools' distutils shim - pulled on 3.12 by torch (scorer_v3; the scorer never imports distutils, an E5 "
           "line) and by spacy and thinc (mem0_v3's [nlp])",
           ("pywin32", "pywin32.pth"):
           "pywin32's path and DLL bootstrap - pulled by portalocker, qdrant-client's file lock, on Windows (mem0_v3: "
           "mem0's local Qdrant store)"}
DISK_FLOOR = 100 * (1 << 30)                     # the auditor's Q-A3-2 floor (fetch_manifest.json "disk")
_FILE = re.compile(r"[A-Za-z0-9._+-]{1,200}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
#: The product venvs of §2.2 this module installs - each its pinned specs, the imports that must work, and the
#: distributions whose installed version must be the locked one. A venv is added here, with its pin, before its window.
#: B-NLP (the auditor, 07:19): mem0 WITH its [nlp] extra (T22) - spacy is imported and version-checked, so an install
#: that silently dropped the extra fails by name.
#: C5e (the auditor's Q-C5e-1 = O-a, 11:13): graphiti, langmem and cognee pinned as their rows read them, as mem0 is;
#: Q-A8-4 applies only if a version does not install. Q-C5e-2 = O-a (T31): graphiti WITH its [falkordb] extra - the
#: client its FalkorDB driver imports - so falkordb and the driver are imported and falkordb version-checked. langgraph
#: has no spec of its own: langmem's resolution pins it, the lock records it and the import check reads its version.
VENVS = {"mem0_v3": {"base": "py-base-312",
                     # T34 (the auditor's order of 2026-09-30: pth allow by RECORD): the two .pth files of download d1's
                     # wheels - pywin32 312 (portalocker's, qdrant-client's lock) and setuptools 84.0.0 (spacy's, thinc's)
                     "pth_allowed": [["pywin32", "pywin32.pth"], ["setuptools", "distutils-precedence.pth"]],
                     # A5 (T34, the auditor's Q-M0-OLLAMA/Q-M0-FE = (a)): what mem0's selected paths import and [nlp]
                     # does not bring - the Ollama client of its embedder and mem0-store's LLM, the BM25 encoder of its
                     # Qdrant store
                     "specs": ["mem0ai[nlp]==2.2.0", "ollama==0.6.3", "fastembed==0.8.1"],
                     "imports": ["mem0", "spacy", "ollama", "fastembed"],
                     "dists": ["mem0ai", "spacy", "ollama", "fastembed"]},
         "graphiti_v3": {"base": "py-base-312", "specs": ["graphiti-core[falkordb]==0.30.2"],
                         "imports": ["graphiti_core", "falkordb", "graphiti_core.driver.falkordb_driver"],
                         "dists": ["graphiti-core", "falkordb"]},
         "langmem_v3": {"base": "py-base-312", "specs": ["langmem==0.0.30"], "imports": ["langmem", "langgraph"],
                        "dists": ["langmem", "langgraph"]},
         "cognee_v3": {"base": "py-base-312", "specs": ["cognee==1.6.1"], "imports": ["cognee"], "dists": ["cognee"]},
         # A4 (T33, the auditor's Q-SCR-1..5): the scorer's own venv - no arm; the eleven versions window a8-pypi-d p1
         # read, every further package the lock's to resolve and record by its wheel's sha256
         # Q-SC-PTH = O-a (the auditor, 2026-09-30): torch pulls setuptools on 3.12, whose wheel ships
         # distutils-precedence.pth (its distutils shim) - allowed by name, owner and RECORD sha (site_check)
         "scorer_v3": {"base": "py-base-312", "pth_allowed": [["setuptools", "distutils-precedence.pth"]],
                       "specs": ["sentence-transformers==6.1.0", "nltk==3.10.3", "scipy==1.18.1",
                                 "torch==2.14.0", "transformers==5.17.0", "huggingface-hub==1.33.0",
                                 "tokenizers==0.23.2", "numpy==2.5.3", "scikit-learn==1.9.1",
                                 "typing-extensions==4.16.0", "tqdm==4.70.1"],
                       "imports": ["sentence_transformers", "nltk", "scipy", "torch"],
                       "dists": ["sentence-transformers", "nltk", "scipy", "torch",
                                 "transformers", "huggingface-hub", "tokenizers", "numpy",
                                 "scikit-learn", "typing-extensions", "tqdm"]}}
#: PREREG-V3 §2.2's row of each venv, quoted word for word from its named source (the auditor's method rule, B-NLP;
#: Q-C5e-3: revision 1 is frozen, an amended row lives in the amendments file beside it): its distribution, extras,
#: version and base - a suite row checks every VENVS spec against it.
_REV1, _AMENDMENTS = "research/v3/PREREG-V3-rev1.md", "research/v3/PREREG-V3-AMENDMENTS.md"
PREREG_22 = {"mem0_v3": {"row": ("| mem0 | product | mem0ai, latest stable at freeze, with `[nlp]` (T22); 2.2.0 on "
                                 "2026-09-23; with ollama==0.6.3 and fastembed==0.8.1 (T34) | 3.12, fresh venv mem0_v3 | "
                                 "vendor-recommended:https://github.com/mem0ai/memory-benchmarks | to install |"),
                         "source": _AMENDMENTS, "dist": "mem0ai", "extras": ["nlp"], "version": "2.2.0",
                         "pins": [("mem0ai", ["nlp"], "2.2.0"), ("ollama", [], "0.6.3"), ("fastembed", [], "0.8.1")],
                         "base": "py-base-312"},
             "graphiti_v3": {"row": "| zep-graphiti | product | graphiti-core, latest stable at freeze (0.30.2 read "
                                    "2026-09-26); with `[falkordb]` (T31); FalkorDB image by digest | 3.12, fresh venv "
                                    "graphiti_v3 | vendor-recommended:https://github.com/getzep/graphiti | to install, to "
                                    "adapt |",
                             "source": _AMENDMENTS, "dist": "graphiti-core", "extras": ["falkordb"], "version": "0.30.2",
                             "base": "py-base-312"},
             "langmem_v3": {"row": "| langmem | product | langmem, latest stable (0.0.30); langgraph pinned | 3.12, fresh "
                                   "venv langmem_v3 |",
                            "source": _REV1, "dist": "langmem", "extras": [], "version": "0.0.30", "base": "py-base-312"},
             "cognee_v3": {"row": "| cognee | product | cognee, latest stable (1.6.1) | 3.12, fresh venv cognee_v3 |",
                           "source": _REV1, "dist": "cognee", "extras": [], "version": "1.6.1", "base": "py-base-312"},
             # A4's paragraph (the amendments file, 2.2): a venv of eleven pins, each (dist, extras, version)
             "scorer_v3": {"row": ("Its venv scorer_v3 (3.12, a fresh venv on the base py-base-312 p1) holds exactly\n"
                                   "sentence-transformers==6.1.0, nltk==3.10.3, scipy==1.18.1 and torch==2.14.0 (the PyPI CPU wheel; no CUDA index is\n"
                                   "declared), and the requirements sentence-transformers 6.1.0 declares without an extra: transformers==5.17.0,\n"
                                   "huggingface-hub==1.33.0, tokenizers==0.23.2, numpy==2.5.3, scikit-learn==1.9.1, typing-extensions==4.16.0 and\n"
                                   "tqdm==4.70.1 - each the newest stable release its specifier allows (huggingface-hub: 2.0.0 is newer, the specifier\n"
                                   "<2.0.0,>=1.3.0 takes 1.33.0), read by the window a8-pypi-d p1."),
                           "source": _AMENDMENTS,
                           "pins": [("sentence-transformers", [], "6.1.0"), ("nltk", [], "3.10.3"), ("scipy", [], "1.18.1"),
                                    ("torch", [], "2.14.0"), ("transformers", [], "5.17.0"), ("huggingface-hub", [], "1.33.0"),
                                    ("tokenizers", [], "0.23.2"), ("numpy", [], "2.5.3"), ("scikit-learn", [], "1.9.1"),
                                    ("typing-extensions", [], "4.16.0"), ("tqdm", [], "4.70.1")],
                           "base": "py-base-312"}}
#: Q-C5e-1 (the auditor, 11:13): the freeze check of every product pin, declared now as data - "read" is the date the
#: row read the version (revision 1's own date, 2026-09-26, where the row names none). The scorer (A4) is no product: its
#: eleven versions were read on 2026-09-30 by window a8-pypi-d p1.
FREEZE_NEWER_CHECK = {
    "rule": ("at FREEZE-V3 (A10), a metadata read of each product pin's index entry - no install - records the newest "
             "stable version; if it is newer than the pin: an E5 line \"pinned X (read D1); newest at freeze Y\" and a "
             "question to the auditor before the campaign - never a silent move"),
    "pins": {"mem0_v3": {"dist": "mem0ai", "version": "2.2.0", "read": "2026-09-23"},
             "graphiti_v3": {"dist": "graphiti-core", "version": "0.30.2", "read": "2026-09-26"},
             "langmem_v3": {"dist": "langmem", "version": "0.0.30", "read": "2026-09-26 (rev1's date; the row names none)"},
             "cognee_v3": {"dist": "cognee", "version": "1.6.1", "read": "2026-09-26 (rev1's date; the row names none)"}}}
_SPEC = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[([A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*)\])?==([A-Za-z0-9.+!-]+)")


class LockRefused(RuntimeError):
    """A report, a lock, a wheel or an install this module's rules do not allow - named, never worked around."""


def _iv():
    mod = sys.modules.get("v3_install_v3_data_for_lock")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_install_v3_data_for_lock", HERE / "install_v3_data.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_install_v3_data_for_lock"] = mod
        spec.loader.exec_module(mod)
    return mod


def _fpb():
    mod = sys.modules.get("v3_fetch_py_base_for_lock")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_fetch_py_base_for_lock", HERE / "fetch_py_base.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_fetch_py_base_for_lock"] = mod
        spec.loader.exec_module(mod)
    return mod


def base_python(c, base: str, bases: dict) -> Path:
    """The declared base's interpreter: polygon/<its dest>/python.exe (fetch_py_base.BASES)."""
    return c.polygon_root / bases[base]["dest"] / "python.exe"


def offline_env(c) -> dict:
    """The declared environment of every pip child (LI-2): no config file at all, the cache in the polygon, no input."""
    return {"PIP_CACHE_DIR": os.fspath(c.polygon_root / "pip_cache"), "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1"}


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


# ── 2a: pip's own resolution, wheels only ──────────────────────────────────────────────────────────────────────

def resolve_job(venv_py: Path, pip_args: list[str], specs: list[str], *, index_host: str, pip_cache: Path) -> dict:
    """The window's pip child: a dry run that only writes pip's report (report.json in its unit directory)."""
    return {"child": "pip", "python": os.fspath(venv_py),
            "argv": ["-I", "-B", "-m", "pip", "install", *pip_args, "--dry-run", "--ignore-installed",
                     "--only-binary=:all:", "--report", REPORT, "--no-input", "--disable-pip-version-check", *specs],
            "env": {"PIP_CACHE_DIR": os.fspath(pip_cache), "PIP_CONFIG_FILE": os.devnull,
                    "PIP_INDEX_URL": f"https://{index_host}/simple", "PIP_NO_INPUT": "1"}}   # offline_env + the index


def _licence(md: dict) -> str | None:
    """The licence as the distribution's METADATA gives it: License-Expression, else License, else its classifiers."""
    if md.get("license_expression"):
        return str(md["license_expression"])
    if md.get("license"):
        return str(md["license"])[:200]
    cls = [c for c in md.get("classifier") or md.get("classifiers") or [] if str(c).startswith("License ::")]
    return "; ".join(cls) or None


def _norm_extra(x: str) -> str:
    return re.sub(r"[-_.]+", "-", str(x)).lower()


def spec_parts(spec: str) -> tuple[str, list[str], str]:
    """(normalised name, extras sorted and normalised, version) of an exact pin ``name[extra,...]==version`` - any other
    spec is refused by name: a venv's pin is exact (§2.2)."""
    m = _SPEC.fullmatch(str(spec).strip())
    if not m:
        raise LockRefused(f"the spec {spec!r} is not an exact pin name[extras]==version")
    extras = sorted({_norm_extra(x.strip()) for x in (m.group(2) or "").split(",") if x.strip()})
    return norm_name(m.group(1)), extras, m.group(3)


def lock_step(report: dict, specs: list[str], *, dists: list[str] | tuple = (), files_host: str = FILES_HOST) -> list[dict]:
    """B-NLP: the lock from pip's report, and every spec found in it - its distribution requested, at its version, with
    every extra the spec asks among the extras pip took; and every declared distribution in the lock (an extra the
    package does not provide is only a warning to pip, and its report still lists the extra as requested - the
    distributions it should have brought are what shows it). Refused by name, before any wheel is downloaded."""
    lock = lock_from_report(report, files_host=files_host)
    by = {e["name"]: e for e in lock}
    for spec in specs:
        name, extras, version = spec_parts(spec)
        e = by.get(name)
        if e is None or not e["requested"] or e["version"] != version:
            raise LockRefused(f"the report does not install {spec} as requested")
        missing = sorted(set(extras) - set(e["requested_extras"]))
        if missing:
            raise LockRefused(f"the report took {name} without its extra(s) {missing} - pip dropped what the spec asks "
                              f"(B-NLP)")
    absent = sorted(d for d in dists if norm_name(d) not in by)
    if absent:
        raise LockRefused(f"the lock lacks the declared distribution(s) {absent} - the specs' extras brought nothing "
                          f"(B-NLP)")
    return lock


def lock_from_report(report: dict, *, files_host: str = FILES_HOST) -> list[dict]:
    """pip's installation report (format "1") as the lock: one entry per distribution, sorted by name - see step 2b."""
    if not isinstance(report, dict) or report.get("version") != "1":
        raise LockRefused("the report is not pip's installation report, format 1")
    items = report.get("install")
    if not isinstance(items, list) or not items:
        raise LockRefused("the report installs nothing")
    out, seen = [], set()
    for it in items:
        md = it.get("metadata") or {}
        name, version = md.get("name"), md.get("version")
        if not (isinstance(name, str) and name and isinstance(version, str) and version):
            raise LockRefused("a report item carries no name or version")
        who = f"{name} {version}"
        if it.get("is_direct"):
            raise LockRefused(f"{who}: a direct URL requirement - the lock takes only the index's files")
        if it.get("is_yanked"):
            raise LockRefused(f"{who}: a yanked file")
        di = it.get("download_info") or {}
        ai = di.get("archive_info")
        if not isinstance(ai, dict):
            raise LockRefused(f"{who}: not an archive from the index (a VCS or local source)")
        u = urllib.parse.urlsplit(str(di.get("url") or ""))
        if u.scheme != "https" or u.hostname != files_host or u.port not in (None, 443) or u.username or u.query:
            raise LockRefused(f"{who}: its file is not https://{files_host}/...")
        fn = urllib.parse.unquote(u.path.rsplit("/", 1)[-1])
        if not _FILE.fullmatch(fn):
            raise LockRefused(f"{who}: an unsafe file name {fn[:60]!r}")
        if not fn.endswith(".whl"):
            raise LockRefused(f"{who}: no wheel ({fn}) - attempt 2 builds exactly this distribution with a backend "
                              f"pinned by wheels in the same window (Q-A8-1); attempt 1 takes wheels only")
        h = ai.get("hashes") if isinstance(ai.get("hashes"), dict) else {}
        sha = h.get("sha256") or (str(ai.get("hash") or "").partition("=")[2]
                                  if str(ai.get("hash") or "").startswith("sha256=") else None)
        if not (isinstance(sha, str) and _HEX64.fullmatch(sha)):
            raise LockRefused(f"{who}: the index gave no sha256 for {fn}")
        n = norm_name(name)
        if n in seen:
            raise LockRefused(f"{who}: the report names {n} twice")
        seen.add(n)
        out.append({"name": n, "version": version, "filename": fn, "url": di["url"], "sha256": sha,
                    "requested": bool(it.get("requested")), "licence": _licence(md),
                    "requested_extras": sorted({_norm_extra(x) for x in (it.get("requested_extras") or [])})})
    return sorted(out, key=lambda e: e["name"])


def lock_lines(entries: list[dict]) -> list[str]:
    return [f"{e['name']}=={e['version']} --hash=sha256:{e['sha256']}" for e in entries]


def lock_sha256(entries: list[dict]) -> str:
    return hashlib.sha256(("\n".join(lock_lines(entries)) + "\n").encode("utf-8")).hexdigest()


# ── 2c and 3: the wheels, downloaded, then checked from the disk ───────────────────────────────────────────────

def download_job(entries: list[dict], *, files_host: str = FILES_HOST) -> dict:
    """The window's fetch child: every locked wheel, each checked against its sha256 as it streams."""
    return {"hosts": [files_host], "max_redirects": 0, "requests": [
        {"id": f"wheel:{e['name']}", "method": "GET", "url": e["url"], "save": f"wheels/{e['filename']}",
         "max_bytes": WHEEL_MAX, "expect": {"sha256": e["sha256"]}} for e in entries]}


def verify_wheels(wheel_dir: Path, entries: list[dict]) -> list[str]:
    """The index's sha256, checked against the disk: every locked wheel present with that sha256, nothing else there."""
    problems = []
    want = {e["filename"]: e["sha256"] for e in entries}
    have = sorted(p.name for p in wheel_dir.iterdir()) if wheel_dir.is_dir() else []
    for fn, sha in sorted(want.items()):
        f = wheel_dir / fn
        if not f.is_file():
            problems.append(f"the wheel {fn} is not on the disk")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            problems.append(f"the wheel {fn} on the disk is not the sha256 the index gave")
    extra = [fn for fn in have if fn not in want]
    if extra:
        problems.append(f"the wheel directory holds files the lock does not name: {extra[:5]}")
    return problems


def install_argv(venv_py: Path, pip_args: list[str], lock_path: Path, wheel_dir: Path) -> list[str]:
    """The offline install: the lock's hashes, no dependency of pip's own choosing, no index - only the checked wheels."""
    return [os.fspath(venv_py), "-I", "-B", "-m", "pip", "install", *pip_args, "--require-hashes", "--no-deps",
            "--no-index", "--find-links", os.fspath(wheel_dir), "--only-binary=:all:", "--no-input",
            "--disable-pip-version-check", "-r", os.fspath(lock_path)]


def version_probe(imports: list[str], dists: list[str]) -> str:
    """The import check's code: the declared imports, then as JSON the venv's own python version and each requested
    distribution's installed version: {"python": x.y.z, "dists": {name: version}}."""
    for x in [*imports, *dists]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", x):
            raise LockRefused(f"an import or distribution name {x!r} is not a plain name")
    imp = "".join(f"import {m}; " for m in imports)
    return (f"{imp}import json, platform; from importlib.metadata import version; "
            f"print(json.dumps({{\"python\": platform.python_version(), "
            f"\"dists\": {{d: version(d) for d in {sorted(dists)!r}}}}}, sort_keys=True))")


def check_versions(got, lock: list[dict], dists: list[str], base_version: str) -> list[str]:
    """The probe's answer against the declarations: the venv's python is the base's version (LI-1), each requested
    distribution the locked version; an answer of another shape is a problem, never a pass."""
    if not (isinstance(got, dict) and isinstance(got.get("dists"), dict)):
        return [f"the import probe's answer is not {{python, dists}}: {str(got)[:120]}"]
    problems = []
    if got.get("python") != base_version:
        problems.append(f"the venv's python is {got.get('python')}, not the declared base's {base_version} (LI-1)")
    want = {d: next((e["version"] for e in lock if e["name"] == norm_name(d)), None) for d in dists}
    for d, v in sorted(want.items()):
        if v is None:                               # B-NLP: None against None is never a pass
            problems.append(f"the declared distribution {d} is not in the lock")
        elif got["dists"].get(d) != v:
            problems.append(f"the installed {d} is {got['dists'].get(d)}, not the locked {v}")
    return problems


# ── the whole install ─────────────────────────────────────────────────────────────────────────────────────────

DOWNLOAD_RECORD = "download_record.json"


def _write(base: Path, record: dict, name: str = "install_record.json") -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / name).write_bytes(
        (json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


def is_product(venv_name: str) -> bool:
    """A product venv (an arm's product: FREEZE_NEWER_CHECK's pins) is installed in two phases - the download, M31 on its
    wheels, then the install from them (the auditor's Q-SPLIT-4 = O-a); the scorer (A4) is no product."""
    return venv_name in FREEZE_NEWER_CHECK["pins"]


def _declared(c, venv_name: str, *, python: Path, specs, imports, dists, base, bases) -> tuple:
    """The venv's declaration (VENVS, or a test's) and LI-1: the interpreter is the declared base's, by realpath."""
    declared = VENVS.get(venv_name) or {}
    specs, imports, dists = specs or declared.get("specs"), imports or declared.get("imports"), dists or declared.get("dists")
    if not specs or imports is None or not dists:
        raise LockRefused(f"{venv_name!r} declares no specs, imports and distributions (VENVS)")
    bases = bases if bases is not None else _fpb().BASES
    base = base or declared.get("base")
    if base not in bases:
        raise LockRefused(f"{venv_name!r} declares no base among {sorted(bases)} (LI-1)")
    want_py = base_python(c, base, bases)
    if os.path.normcase(os.path.realpath(python)) != os.path.normcase(os.path.realpath(want_py)):
        raise LockRefused(f"{python} is not the declared base {base}'s interpreter {want_py} (LI-1) - no venv is made")
    return specs, imports, dists, base, bases, want_py


def _make_venv(c, L, IV, *, python: Path, venv: Path, stand: str, run: str, parent_env, native, fs, check_id: str,
               record: dict, key: str = "venv") -> dict | None:
    """A venv from the base, offline (-I -B), under its own boundary check; pip's facts and the site's top level, or the
    problems, into ``record`` under ``key`` (the product's "venv", the download's "resolver")."""
    rc, _, err, chk = IV._step(c, L, stand=stand, run=run, arm=key,       # its own unit: download and install share a run
                               argv=[os.fspath(python), "-I", "-B", "-m", "venv", os.fspath(venv)],
                               path_dirs=[python.parent], parent_env=parent_env, native=native, fs=fs, check_id=check_id)
    record[f"{key}_rc"], record[f"{key}_check"] = rc, IV.check_summary(chk)
    record["problems"] += IV.check_problems(key, record[f"{key}_check"])
    if rc != 0 or not IV.venv_python(venv).is_file():
        record["problems"].append(f"the {'venv' if key == 'venv' else key + ' venv'} could not be created (exit {rc}): "
                                  + err.decode("utf-8", "replace")[-200:].replace("\n", " "))
    facts = None
    if not record["problems"]:
        try:
            facts = IV.pip_facts(venv)
        except IV.InstallRefused as e:
            record["problems"].append(f"refused: {e}")
        site0 = next((s for s in venv.rglob("site-packages") if s.is_dir()), None)
        record[f"{key}_top_level"] = IV.top_level(site0) if site0 is not None else []
    return facts


def _window(c, L, F, *, venv_py: Path, pip_args: list, specs: list, dists: list, window: str, run: str, python: Path,
            via_port: int, parent_env, native, fs, child_env_extra, need_bytes: int, volume: Path, index_host: str,
            files_host: str, record: dict) -> tuple[list, Path | None]:
    """Step 2: ONE declared window - pip's dry run in ``venv_py`` (its report), the lock from it, the wheels downloaded
    and checked as they stream. Returns (the lock, the wheels' directory - None when anything is wrong)."""
    lock: list[dict] = []
    refused: list[str] = []
    # R-PIP-ISS = O-a: pip's own connection records no TLS issuer, so the window's FIRST job is a fetch child's GET of the
    # index page of the first requested distribution - pypi.org's issuer recorded before pip resolves
    probe_url = f"https://{index_host}/simple/{norm_name(spec_parts(specs[0])[0])}/"
    probe = {"hosts": [index_host], "max_redirects": 0, "requests": [
        {"id": "index:probe", "method": "GET", "url": probe_url, "save": "index_probe.html", "max_bytes": INDEX_PROBE_MAX}]}

    def resolve(results):
        if not results or results[0]["rc"] != 0:
            refused.append("the index probe failed (R-PIP-ISS: pypi.org's issuer is recorded before pip resolves)")
            return None
        return resolve_job(venv_py, pip_args, specs, index_host=index_host, pip_cache=c.polygon_root / "pip_cache")

    def download(results):
        if len(results) < 2 or results[1]["rc"] != 0:
            refused.append("pip's resolution failed (attempt 1, wheels only): "
                           + (results[1]["stderr_tail"] if len(results) > 1 else "no result")[-240:].replace("\n", " "))
            return None
        try:
            report = json.loads((Path(results[1]["unit"]) / REPORT).read_bytes())
            lock.extend(lock_step(report, specs, dists=dists, files_host=files_host))
        except (OSError, ValueError) as e:
            refused.append(f"pip's report could not be read: {type(e).__name__}")
            return None
        except LockRefused as e:
            refused.append(f"refused: {e}")
            return None
        record["pip_report_version"] = report.get("pip_version")
        return download_job(lock, files_host=files_host)

    rec = F.run_child_window(c, L, window=window, hosts=[index_host, files_host], jobs=[probe, resolve, download],
                             python=python, via_port=via_port, run=run, parent_env=parent_env, native=native, fs=fs,
                             child_env_extra=child_env_extra, need_bytes=need_bytes, volume=volume)
    record["window_record"] = {k: rec[k] for k in ("problems", "check", "issuers")}
    record["window_record"]["tunnelled_hosts"] = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["window_record"]["jobs"] = [(j.get("job") or {}).get("child") or "fetch" for j in rec["jobs"]]
    record["window_record"]["index_probe"] = probe_url
    record["problems"] += list(rec["problems"]) + refused
    record["lock"] = lock
    record["lock_lines"] = lock_lines(lock) if lock else []
    record["lock_sha256"] = lock_sha256(lock) if lock else None
    record["licences"] = {e["name"]: e["licence"] for e in lock}
    return lock, (None if record["problems"] else Path(rec["jobs"][2]["unit"]) / "wheels")


def site_check(site: Path, lock: list[dict], names: list, baseline: list, allowed) -> tuple[list[str], list[dict]]:
    """install_v3_data.site_problems, with the .pth files a venv's declaration allows (Q-SC-PTH = O-a): each only by its
    name, by its owner's RECORD - the declared distribution, at the version the lock holds, lists it - and by its bytes'
    sha256 equal to that RECORD's. Any other .pth, or one of these failing a clause, stays the problem it was.
    Returns (the problems, the allowed files with the ruling)."""
    IV = _iv()
    versions = {norm_name(e["name"]): e["version"] for e in lock}
    ok_names, records, problems = [], [], []
    for dist, fname in allowed:
        f = Path(site) / fname
        if not f.is_file():
            continue
        v = versions.get(norm_name(dist))
        rec = next((d / "RECORD" for d in Path(site).glob("*.dist-info")
                    if v is not None and d.name[:-len(".dist-info")].rsplit("-", 1)[-1] == v
                    and norm_name(d.name[:-len(".dist-info")].rsplit("-", 1)[0]) == norm_name(dist)), None)
        row = None
        if rec is not None and rec.is_file():
            for line in rec.read_text(encoding="utf-8").splitlines():
                if line.count(",") >= 2 and line.rsplit(",", 2)[0] == fname:
                    row = line.rsplit(",", 2)[1]
        want = IV._record_hash(row) if row else None
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        why = PTH_WHY.get((dist, fname))
        if why is None:
            problems.append(f"{fname}: declared for {dist} without its reason in PTH_WHY - never allowed; a .pth runs "
                            f"code at every interpreter start")
        elif want is None:
            problems.append(f"{fname}: no RECORD row of {dist} at the lock's version {v} lists it - not its declared owner's "
                            f"(Q-SC-PTH); a .pth runs code at every interpreter start")
        elif got != want:
            problems.append(f"{fname}: its bytes are not the sha256 {dist} {v}'s RECORD names (Q-SC-PTH); a .pth runs "
                            f"code at every interpreter start")
        else:
            ok_names.append(fname)
            records.append({"file": fname, "dist": dist, "version": v, "sha256": got, "reason": f"{why} - {PTH_RULE}"})
    return problems + IV.site_problems(site, names, list(baseline) + ok_names), records


def _install(c, L, IV, *, venv: Path, lock: list, wheel_dir: Path, base_dir: Path, record: dict, imports: list,
             dists: list, base_version: str, stand: str, window: str, run: str, parent_env, native, fs,
             pth_allowed=()) -> dict:
    """Steps 3 (after the wheels were checked from the disk) and 4: pip offline from the lock, then the installed set
    and the site before any interpreter starts in the venv, then the imports and the versions; install_record.json."""
    venv_py = IV.venv_python(venv)
    lock_path = base_dir / "lock.txt"
    lock_path.write_bytes(("\n".join(lock_lines(lock)) + "\n").encode("utf-8"))
    record["install_argv"] = install_argv(venv_py, record["pip"]["args"], lock_path, wheel_dir)
    rc, out, err, chk1 = IV._step(c, L, stand=stand, run=run, arm="pip", argv=record["install_argv"],
                                  path_dirs=[venv_py.parent], parent_env=parent_env, native=native, fs=fs,
                                  check_id=f"install-{window}-{run}-pip", declared=offline_env(c))
    record["install_rc"], record["install_check"] = rc, IV.check_summary(chk1)
    record["install_stdout_tail"] = out.decode("utf-8", "replace")[-600:]
    record["problems"] += IV.check_problems("install", record["install_check"])
    if rc != 0:
        record["problems"].append(f"pip's offline install failed (exit {rc}): "
                                  + err.decode("utf-8", "replace")[-240:].replace("\n", " "))
    if record["problems"]:
        return _write(base_dir, record)
    site = next((p for p in venv.rglob("site-packages") if p.is_dir()), None)
    if site is None:
        record["problems"].append("the venv has no site-packages")
        return _write(base_dir, record)
    names = [e["name"] for e in lock]
    try:
        record["installed_set_sha256"], record["installed_files"] = IV.installed_set(site, names)
    except IV.InstallRefused as e:
        record["problems"].append(f"refused: {e}")
    site_probs, record["allowed_pth"] = site_check(site, lock, names, record["venv_top_level"], pth_allowed)
    record["problems"] += site_probs
    if record["problems"]:
        record["import_skipped"] = "the installed site has problems; no interpreter was started in the venv"
        return _write(base_dir, record)
    rc, out, _, chk2 = IV._step(c, L, stand=stand, run=run, arm="check",
                                argv=[os.fspath(venv_py), "-I", "-B", "-c", version_probe(imports, dists)],
                                path_dirs=[venv_py.parent], parent_env=parent_env, native=native, fs=fs,
                                check_id=f"install-{window}-{run}-check")
    record["import_rc"], record["import_check"] = rc, IV.check_summary(chk2)
    record["problems"] += IV.check_problems("import", record["import_check"])
    if rc != 0:
        record["problems"].append(f"the declared imports fail in the venv (exit {rc})")
        return _write(base_dir, record)
    try:
        got = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = None
    record["import_versions"] = got
    record["venv_python_version"] = got.get("python") if isinstance(got, dict) else None
    record["problems"] += check_versions(got, lock, dists, base_version)
    return _write(base_dir, record)


def run_lock_install(c, L, F, *, python: Path, venv: Path, venv_name: str, run: str, via_port: int, parent_env,
                     specs: list[str] | None = None, imports: list[str] | None = None, dists: list[str] | None = None,
                     base: str | None = None, bases: dict | None = None, native=None, fs=None,
                     child_env_extra: dict | None = None, index_host: str = INDEX_HOST, files_host: str = FILES_HOST,
                     need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """The whole install in one call (see the module docstring) - a venv that is no product only (Q-SPLIT-4 = O-a: a
    product's goes download, M31, install-from). ``F`` is research/v3/fetch_a3 (its window mechanism);
    ``child_env_extra``, the hosts, ``bases`` (a test world's base) and a lower floor are for tests only."""
    IV = _iv()
    specs, imports, dists, base, bases, want_py = _declared(c, venv_name, python=python, specs=specs, imports=imports,
                                                            dists=dists, base=base, bases=bases)
    window = WINDOW_PREFIX + venv_name
    if venv.exists():
        raise LockRefused("the venv already exists: an install window creates it fresh")
    volume = volume or Path(c.runs_root.anchor)
    ok, free = F.disk_floor_ok(volume, need_bytes)
    if not ok:
        raise LockRefused(f"the free space ({free >> 30} GB) is under the floor ({need_bytes >> 30} GB)")
    base_dir = c.runs_root / "_install" / window / run
    if base_dir.exists():
        raise LockRefused("this install run label was used before")
    if is_product(venv_name):
        raise LockRefused(f"{venv_name} is a product venv: it is installed in two phases - --download-only, M31 on its "
                          f"wheels, then --install-from (the auditor's Q-SPLIT-4 = O-a)")
    base_dir.mkdir(parents=True)
    stand = f"_install.{window}"
    record: dict = {"window": window, "run": run, "venv": str(venv), "base_python": str(python), "specs": list(specs),
                    "base": {"window": base, "version": bases[base]["version"], "python": str(want_py)},
                    "imports": list(imports), "dists": list(dists), "attempt": 1, "offline_env": offline_env(c),
                    "problems": []}
    # 1. the venv, offline
    facts = _make_venv(c, L, IV, python=python, venv=venv, stand=stand, run=run, parent_env=parent_env, native=native,
                       fs=fs, check_id=f"install-{window}-{run}-venv", record=record)
    if record["problems"]:
        return _write(base_dir, record)
    record["pip"] = facts
    lock, wheel_dir = _window(c, L, F, venv_py=IV.venv_python(venv), pip_args=record["pip"]["args"], specs=specs,
                              dists=dists, window=window, run=run, python=python, via_port=via_port,
                              parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                              need_bytes=need_bytes, volume=volume, index_host=index_host, files_host=files_host,
                              record=record)
    if record["problems"]:
        return _write(base_dir, record)
    # 3. the wheels from the disk, then pip offline
    record["wheel_problems"] = verify_wheels(wheel_dir, lock)
    record["problems"] += record["wheel_problems"]
    if record["problems"]:
        return _write(base_dir, record)
    return _install(c, L, IV, venv=venv, lock=lock, wheel_dir=wheel_dir, base_dir=base_dir, record=record,
                    imports=imports, dists=dists, base_version=bases[base]["version"], stand=stand, window=window,
                    run=run, parent_env=parent_env, native=native, fs=fs,
                    pth_allowed=(VENVS.get(venv_name) or {}).get("pth_allowed") or ())


def run_lock_download(c, L, F, *, python: Path, venv_name: str, run: str, via_port: int, parent_env,
                      specs: list[str] | None = None, imports: list[str] | None = None, dists: list[str] | None = None,
                      base: str | None = None, bases: dict | None = None, native=None, fs=None,
                      child_env_extra: dict | None = None, index_host: str = INDEX_HOST, files_host: str = FILES_HOST,
                      need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """Phase 1 of a product venv (the auditor's Q-SPLIT-1 = P-a): the window and the wheels, and NO product venv - pip
    resolves in a resolver venv inside the run's own directory (<runs>/_install/<window>/<run>/resolver, -I -B from the
    declared base; its pip's facts recorded: the install's pip must be the same). The wheels are checked from the disk;
    the record is download_record.json beside where M31's m31.json and the install's install_record.json will be."""
    IV = _iv()
    specs, imports, dists, base, bases, want_py = _declared(c, venv_name, python=python, specs=specs, imports=imports,
                                                            dists=dists, base=base, bases=bases)
    window = WINDOW_PREFIX + venv_name
    volume = volume or Path(c.runs_root.anchor)
    ok, free = F.disk_floor_ok(volume, need_bytes)
    if not ok:
        raise LockRefused(f"the free space ({free >> 30} GB) is under the floor ({need_bytes >> 30} GB)")
    base_dir = c.runs_root / "_install" / window / run
    if base_dir.exists():
        raise LockRefused("this install run label was used before")
    base_dir.mkdir(parents=True)
    stand = f"_install.{window}"
    resolver = base_dir / "resolver"
    record: dict = {"window": window, "run": run, "phase": "download", "venv_name": venv_name,
                    "base_python": str(python), "specs": list(specs),
                    "base": {"window": base, "version": bases[base]["version"], "python": str(want_py)},
                    "imports": list(imports), "dists": list(dists), "attempt": 1, "offline_env": offline_env(c),
                    "resolver": str(resolver), "problems": []}
    facts = _make_venv(c, L, IV, python=python, venv=resolver, stand=stand, run=run, parent_env=parent_env,
                       native=native, fs=fs, check_id=f"install-{window}-{run}-resolver", record=record, key="resolver")
    if record["problems"]:
        return _write(base_dir, record, DOWNLOAD_RECORD)
    record["pip"] = facts
    lock, wheel_dir = _window(c, L, F, venv_py=IV.venv_python(resolver), pip_args=facts["args"], specs=specs,
                              dists=dists, window=window, run=run, python=python, via_port=via_port,
                              parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                              need_bytes=need_bytes, volume=volume, index_host=index_host, files_host=files_host,
                              record=record)
    if record["problems"]:
        return _write(base_dir, record, DOWNLOAD_RECORD)
    record["wheel_dir"] = str(wheel_dir)
    record["wheel_problems"] = verify_wheels(wheel_dir, lock)
    record["problems"] += record["wheel_problems"]
    return _write(base_dir, record, DOWNLOAD_RECORD)


def run_lock_install_from(c, L, *, python: Path, venv: Path, venv_name: str, run: str, parent_env,
                          base: str | None = None, bases: dict | None = None, native=None, fs=None,
                          imports: list[str] | None = None, dists: list[str] | None = None,
                          product: bool | None = None) -> dict:
    """Phase 2 (the auditor's Q-SPLIT-1..6): the venv from the download ``run``'s own wheels, offline - only on his GO,
    and only after every check below, each a refusal by name BEFORE the venv is made: the download record there and
    clean; for a product, its m31.json there, bound to that record by sha256, with nothing unprovided and no problem;
    no install from it before (install_record.json); the venv not there; the record's lock its lock_sha256; every wheel
    still the lock's sha256 on the disk. Then the venv, whose pip must be the resolver's, and steps 3-4.
    ``product`` (None: is_product) and ``bases`` are for tests only."""
    IV = _iv()
    _s, imports, dists, base, bases, want_py = _declared(c, venv_name, python=python, specs=["-"], imports=imports,
                                                         dists=dists, base=base, bases=bases)
    window = WINDOW_PREFIX + venv_name
    base_dir = c.runs_root / "_install" / window / run
    dr_path = base_dir / DOWNLOAD_RECORD
    if not dr_path.is_file():
        raise LockRefused(f"{window} {run}: no download record ({DOWNLOAD_RECORD}) - --download-only first")
    raw = dr_path.read_bytes()
    dr = json.loads(raw)
    if dr.get("window") != window or dr.get("run") != run or dr.get("phase") != "download":
        raise LockRefused(f"{window} {run}: {DOWNLOAD_RECORD} is not this window run's download record")
    if dr.get("problems") != []:
        raise LockRefused(f"{window} {run}: the download has problems: {dr.get('problems')}")
    dr_sha = hashlib.sha256(raw).hexdigest()
    m31_sha = None
    if is_product(venv_name) if product is None else product:
        mp = base_dir / "m31.json"
        if not mp.is_file():
            raise LockRefused(f"{window} {run}: no m31.json - a product venv is installed only after a clean M31 of its "
                              f"download")
        mraw = mp.read_bytes()
        m = json.loads(mraw)
        if m.get("download_record_sha256") != dr_sha:
            raise LockRefused(f"{window} {run}: m31.json is not this download's (its download_record_sha256 "
                              f"{str(m.get('download_record_sha256'))[:12]} is not {dr_sha[:12]})")
        if m.get("unprovided") != []:
            raise LockRefused(f"{window} {run}: M31 names unprovided imports {m.get('unprovided')} - an amendment "
                              f"first, never a silent add")
        if m.get("problems") != []:
            raise LockRefused(f"{window} {run}: M31 has problems: {m.get('problems')}")
        m31_sha = hashlib.sha256(mraw).hexdigest()
    if (base_dir / "install_record.json").exists():
        raise LockRefused(f"{window} {run}: already installed from (install_record.json exists) - never twice")
    if venv.exists():
        raise LockRefused("the venv already exists: an install creates it fresh")
    lock = dr.get("lock") or []
    if not lock or lock_sha256(lock) != dr.get("lock_sha256"):
        raise LockRefused(f"{window} {run}: the record's lock is not its lock_sha256")
    wheel_dir = Path(dr.get("wheel_dir") or "")
    moved = verify_wheels(wheel_dir, lock)
    if moved:
        raise LockRefused(f"{window} {run}: the wheels moved since the download: {moved}")
    stand = f"_install.{window}"
    record: dict = {"window": window, "run": run, "phase": "install", "venv": str(venv), "base_python": str(python),
                    "specs": dr.get("specs"), "base": {"window": base, "version": bases[base]["version"],
                                                        "python": str(want_py)},
                    "imports": list(imports), "dists": list(dists), "offline_env": offline_env(c),
                    "download_record_sha256": dr_sha, "m31_sha256": m31_sha, "lock": lock,
                    "lock_sha256": dr["lock_sha256"], "wheel_dir": str(wheel_dir), "problems": []}
    facts = _make_venv(c, L, IV, python=python, venv=venv, stand=stand, run=run, parent_env=parent_env, native=native,
                       fs=fs, check_id=f"install-{window}-{run}-venv", record=record)
    if record["problems"]:
        return _write(base_dir, record)
    record["pip"] = facts
    if facts != dr.get("pip"):
        record["problems"].append(f"the install venv's pip {facts} is not the resolver's {dr.get('pip')} (Q-SPLIT-1)")
        return _write(base_dir, record)
    return _install(c, L, IV, venv=venv, lock=lock, wheel_dir=wheel_dir, base_dir=base_dir, record=record,
                    imports=imports, dists=dists, base_version=bases[base]["version"], stand=stand, window=window,
                    run=run, parent_env=parent_env, native=native, fs=fs,
                    pth_allowed=(VENVS.get(venv_name) or {}).get("pth_allowed") or ())


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8: a product venv from a hashed lock (Q-A8-1 O-a) - a product in two "
                                             "phases (Q-SPLIT): --download-only, M31, then --install-from")
    ap.add_argument("--venv", required=True, choices=sorted(VENVS))
    ap.add_argument("--run", help="the run label (a download's, or a one-call install's)")
    ap.add_argument("--download-only", action="store_true", help="phase 1: the window and the wheels, no product venv")
    ap.add_argument("--install-from", metavar="RUN", help="phase 2, offline: the venv from download RUN's wheels, after "
                                                          "its clean M31 (m31.json), on the auditor's GO")
    ap.add_argument("--python", type=Path, default=None,
                    help="the declared base's python.exe (default: polygon/<the venv's base>/python.exe)")
    args = ap.parse_args(argv)
    if args.download_only and args.install_from:
        print("--download-only and --install-from are the two phases: one at a time", file=sys.stderr)
        return 2
    if not args.download_only and not args.install_from and is_product(args.venv):
        print(f"{args.venv} is a product venv: it is installed in two phases - --download-only, M31 on its wheels, "
              f"then --install-from (Q-SPLIT-4)", file=sys.stderr)
        return 2
    if not args.install_from and not args.run:
        print("--run names the run", file=sys.stderr)
        return 2
    L = _load("v3_launch", HERE / "launch.py")
    c = L.Contract.default()
    args.python = args.python or base_python(c, VENVS[args.venv]["base"], _fpb().BASES)
    if args.install_from:
        rec = run_lock_install_from(c, L, python=args.python, venv=c.polygon_root / args.venv, venv_name=args.venv,
                                    run=args.install_from, parent_env=dict(os.environ))
    else:
        F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
        port = L.network_via_port(c)
        if port is None:
            print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
            return 2
        if args.download_only:
            rec = run_lock_download(c, L, F, python=args.python, venv_name=args.venv, run=args.run, via_port=port,
                                    parent_env=dict(os.environ))
        else:
            rec = run_lock_install(c, L, F, python=args.python, venv=c.polygon_root / args.venv, venv_name=args.venv,
                                   run=args.run, via_port=port, parent_env=dict(os.environ))
    print(json.dumps({k: rec.get(k) for k in ("window", "phase", "lock_sha256", "installed_set_sha256",
                                               "import_versions", "problems")}, indent=1))
    return 0 if not rec["problems"] else 1

if __name__ == "__main__":
    sys.exit(main())
