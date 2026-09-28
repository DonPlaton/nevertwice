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
   b) the harness turns the report into the lock (lock_from_report): one line per distribution, ``name==version
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

    python research/v3/lock_install.py --venv mem0_v3 --run l1 --python D:\\Coding\\_nevertwice_polygon\\py312\\python.exe
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
DISK_FLOOR = 100 * (1 << 30)                     # the auditor's Q-A3-2 floor (fetch_manifest.json "disk")
_FILE = re.compile(r"[A-Za-z0-9._+-]{1,200}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
#: The product venvs of §2.2 this module installs - each its pinned specs, the imports that must work, and the
#: distributions whose installed version must be the locked one. A venv is added here, with its pin, before its window.
VENVS = {"mem0_v3": {"base": "py-base-312", "specs": ["mem0ai==2.2.0"], "imports": ["mem0"], "dists": ["mem0ai"]}}


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
                    "requested": bool(it.get("requested")), "licence": _licence(md)})
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
        if got["dists"].get(d) != v:
            problems.append(f"the installed {d} is {got['dists'].get(d)}, not the locked {v}")
    return problems


# ── the whole install ─────────────────────────────────────────────────────────────────────────────────────────

def _write(base: Path, record: dict) -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / "install_record.json").write_bytes(
        (json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


def run_lock_install(c, L, F, *, python: Path, venv: Path, venv_name: str, run: str, via_port: int, parent_env,
                     specs: list[str] | None = None, imports: list[str] | None = None, dists: list[str] | None = None,
                     base: str | None = None, bases: dict | None = None, native=None, fs=None,
                     child_env_extra: dict | None = None, index_host: str = INDEX_HOST, files_host: str = FILES_HOST,
                     need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """The whole install (see the module docstring). ``F`` is research/v3/fetch_a3 (its window mechanism);
    ``child_env_extra``, the hosts, ``bases`` (a test world's base) and a lower floor are for tests only."""
    IV = _iv()
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
    base_dir.mkdir(parents=True)
    stand = f"_install.{window}"
    record: dict = {"window": window, "run": run, "venv": str(venv), "base_python": str(python), "specs": list(specs),
                    "base": {"window": base, "version": bases[base]["version"], "python": str(want_py)},
                    "imports": list(imports), "dists": list(dists), "attempt": 1, "offline_env": offline_env(c),
                    "problems": []}
    # 1. the venv, offline
    rc, _, err, chk0 = IV._step(c, L, stand=stand, run=run, arm="venv",
                                argv=[os.fspath(python), "-I", "-B", "-m", "venv", os.fspath(venv)],
                                path_dirs=[python.parent], parent_env=parent_env, native=native, fs=fs,
                                check_id=f"install-{window}-{run}-venv")
    record["venv_rc"], record["venv_check"] = rc, IV.check_summary(chk0)
    record["problems"] += IV.check_problems("venv", record["venv_check"])
    if rc != 0 or not IV.venv_python(venv).is_file():
        record["problems"].append(f"the venv could not be created (exit {rc}): "
                                  + err.decode("utf-8", "replace")[-200:].replace("\n", " "))
    if not record["problems"]:
        try:
            record["pip"] = IV.pip_facts(venv)
        except IV.InstallRefused as e:
            record["problems"].append(f"refused: {e}")
        site0 = next((s for s in venv.rglob("site-packages") if s.is_dir()), None)
        record["venv_top_level"] = IV.top_level(site0) if site0 is not None else []
    if record["problems"]:
        return _write(base_dir, record)
    venv_py = IV.venv_python(venv)
    lock: list[dict] = []
    refused: list[str] = []

    def resolve(results):
        return resolve_job(venv_py, record["pip"]["args"], specs, index_host=index_host,
                           pip_cache=c.polygon_root / "pip_cache")

    def download(results):
        if not results or results[0]["rc"] != 0:
            refused.append("pip's resolution failed (attempt 1, wheels only): "
                           + (results[0]["stderr_tail"] if results else "no result")[-240:].replace("\n", " "))
            return None
        try:
            report = json.loads((Path(results[0]["unit"]) / REPORT).read_bytes())
            lock.extend(lock_from_report(report, files_host=files_host))
        except (OSError, ValueError) as e:
            refused.append(f"pip's report could not be read: {type(e).__name__}")
            return None
        except LockRefused as e:
            refused.append(f"refused: {e}")
            return None
        record["pip_report_version"] = report.get("pip_version")
        return download_job(lock, files_host=files_host)

    hosts = [index_host, files_host]
    rec = F.run_child_window(c, L, window=window, hosts=hosts, jobs=[resolve, download], python=python,
                             via_port=via_port, run=run, parent_env=parent_env, native=native, fs=fs,
                             child_env_extra=child_env_extra, need_bytes=need_bytes, volume=volume)
    record["window_record"] = {k: rec[k] for k in ("problems", "check", "issuers")}
    record["window_record"]["tunnelled_hosts"] = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["problems"] += list(rec["problems"]) + refused
    record["lock"] = lock
    record["lock_lines"] = lock_lines(lock) if lock else []
    record["lock_sha256"] = lock_sha256(lock) if lock else None
    record["licences"] = {e["name"]: e["licence"] for e in lock}
    if record["problems"]:
        return _write(base_dir, record)
    # 3. the wheels from the disk, then pip offline
    wheel_dir = Path(rec["jobs"][1]["unit"]) / "wheels"
    record["wheel_problems"] = verify_wheels(wheel_dir, lock)
    record["problems"] += record["wheel_problems"]
    if record["problems"]:
        return _write(base_dir, record)
    lock_path = base_dir / "lock.txt"
    lock_path.write_bytes(("\n".join(record["lock_lines"]) + "\n").encode("utf-8"))
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
    # 4. the installed set and the site, before any interpreter starts in the venv; then the imports
    site = next((p for p in venv.rglob("site-packages") if p.is_dir()), None)
    if site is None:
        record["problems"].append("the venv has no site-packages")
        return _write(base_dir, record)
    names = [e["name"] for e in lock]
    try:
        record["installed_set_sha256"], record["installed_files"] = IV.installed_set(site, names)
    except IV.InstallRefused as e:
        record["problems"].append(f"refused: {e}")
    record["problems"] += IV.site_problems(site, names, record["venv_top_level"])
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
    record["problems"] += check_versions(got, lock, dists, bases[base]["version"])
    return _write(base_dir, record)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A8: a product venv from a hashed lock (Q-A8-1 O-a)")
    ap.add_argument("--venv", required=True, choices=sorted(VENVS))
    ap.add_argument("--run", required=True)
    ap.add_argument("--python", type=Path, default=None,
                    help="the declared base's python.exe (default: polygon/<the venv's base>/python.exe)")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    F = _load("v3_fetch_a3", HERE / "fetch_a3.py")
    c = L.Contract.default()
    args.python = args.python or base_python(c, VENVS[args.venv]["base"], _fpb().BASES)
    port = L.network_via_port(c)
    if port is None:
        print("no declared hop: <runs>\\_config\\network.json is missing", file=sys.stderr)
        return 2
    rec = run_lock_install(c, L, F, python=args.python, venv=c.polygon_root / args.venv, venv_name=args.venv,
                           run=args.run, via_port=port, parent_env=dict(os.environ))
    print(json.dumps({k: rec.get(k) for k in ("window", "lock_sha256", "installed_set_sha256", "import_versions",
                                               "problems")}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
