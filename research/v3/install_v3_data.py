#!/usr/bin/env python3
"""PREREG-V3 plan step A3 (the auditor's P2 ruling, R-A3-2): the install window for the polygon venv v3_data.

BEAM and MemoryAgentBench ship parquet only, so reading them (A3.j, then the loaders) needs pyarrow. Under the launch
contract, in one declared window whose hosts are exactly pypi.org and files.pythonhosted.org:

1. before the window, the venv is created from the polygon's py314 (a contract spawn, no network); a problem in that
   step's own boundary check stops the install before any window opens;
2. a fetch child reads PyPI's JSON for each package (metadata only) - the harness then writes a requirements file:
   the one pinned version, the one wheel for the target tag, its sha256 from PyPI's own digests, and every hard
   dependency the metadata names (``Requires-Dist`` without an ``extra`` marker), each pinned and hashed the same way
   and listed in the record;
3. pip runs as a child under the contract: ``install --require-hashes --no-deps --only-binary=:all:`` from that file,
   its cache inside the polygon, PIP_CONFIG_FILE = os.devnull (pip then reads no global, user or site file),
   HTTPS_PROXY = the arm's catcher;
4. after the window: the import is checked (isolated, no bytecode) under its own boundary check, and the venv's
   installed set - every file under site-packages by (relpath, sha256) - is hashed for the FREEZE-V3 draft.

The rules are declared here, before any metadata is read:

* a package: the newest final release that ships exactly one wheel for the target tag;
* a dependency: the same, or - when that release has no wheel for the tag - exactly one py3-none-any wheel; its
  specifier may hold lower bounds only (>=, >), each met by the version chosen; an upper bound, a pin, a compatible-
  release or exclusion clause, an extra ``[x]`` or an environment marker is refused by name - picking "the newest" is
  not a resolver, and --no-deps would install whatever the requirements file says.

A refusal, a failed request or a failed check is a named problem in the record; nothing after it runs. The record
carries the version, the wheel names, their sha256s, the dependencies, pip's output tail, every check, and the
installed-set digest.

    python research/v3/install_v3_data.py --run i1 --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOW = "a3-pyarrow"
HOSTS = ["pypi.org", "files.pythonhosted.org"]
PACKAGES = ("pyarrow",)
TARGET_TAG = "cp314-cp314-win_amd64"
PURE_TAG = "py3-none-any"
META_MAX = 64 * 1024 * 1024
STEP_TIMEOUT = 600


class InstallRefused(RuntimeError):
    pass


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _vkey(v: str) -> tuple[int, ...]:
    """A final version's numbers, trailing zeros dropped (1.16 == 1.16.0)."""
    k = [int(x) for x in re.findall(r"\d+", v)]
    while k and k[-1] == 0:
        k.pop()
    return tuple(k)


def pick_release(meta: dict, tag: str, *, pure_ok: bool = False) -> tuple[str, dict]:
    """The declared rule: the newest version (PyPI's own ordering of release keys by packaging's order is not
    available in the standard library, so: the highest version by numeric components, pre-releases excluded) that
    ships exactly one wheel for ``tag`` - or, with ``pure_ok`` (dependencies only), exactly one py3-none-any wheel
    when it has none for ``tag``. Yanked files never count. Returns (version, the wheel's url entry)."""
    finals = [v for v in meta.get("releases", {}) if re.fullmatch(r"\d+(\.\d+)*", v)]
    for v in sorted(finals, key=_vkey, reverse=True):
        files = [f for f in meta["releases"][v] if f.get("packagetype") == "bdist_wheel" and not f.get("yanked")]
        for t in (tag, PURE_TAG) if pure_ok else (tag,):
            wheels = [f for f in files if f.get("filename", "").endswith(f"-{t}.whl")]
            if len(wheels) == 1 and re.fullmatch(r"[0-9a-f]{64}", (wheels[0].get("digests") or {}).get("sha256", "")):
                return v, wheels[0]
            if wheels:
                break                                   # a tag wheel that is ambiguous or unhashed: never fall back
    raise InstallRefused(f"no release ships exactly one {tag} wheel" + (f" or one {PURE_TAG} wheel" if pure_ok else ""))


def hard_requirements(requires_dist: list | None) -> list[tuple[str, str, str, str]]:
    """(name, specifier, marker, extras) for each Requires-Dist entry with no ``extra ==`` marker: the dependencies
    pip would install without --no-deps. The old "name (>=1)" form is read too."""
    out = []
    for req in requires_dist or []:
        head, _, marker = req.partition(";")
        if "extra" in marker:
            continue
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", head)
        if m:
            spec = m.group(3).strip()
            if spec.startswith("(") and spec.endswith(")"):
                spec = spec[1:-1].strip()
            out.append((m.group(1).lower(), spec, marker.strip(), m.group(2) or ""))
    return out


def hard_dependencies(requires_dist: list | None) -> list[str]:
    """The names of :func:`hard_requirements`."""
    return [r[0] for r in hard_requirements(requires_dist)]


def lower_bounds_ok(spec: str, version: str) -> bool:
    """The declared dependency rule on a specifier: lower bounds only (>=, >), each met by ``version``."""
    if not spec:
        return True
    for clause in spec.split(","):
        m = re.fullmatch(r"\s*(>=|>)\s*(\d+(?:\.\d+)*)\s*", clause)
        if not m:
            return False
        have, bound = _vkey(version), _vkey(m.group(2))
        if not (have >= bound if m.group(1) == ">=" else have > bound):
            return False
    return True


def wanted_dependencies(metas: dict[str, dict], packages) -> dict[str, str]:
    """{dependency: its specifiers joined} over the packages' hard requirements; an extra or a marker is refused."""
    wanted: dict[str, str] = {}
    for p in packages:
        for name, spec, marker, extras in hard_requirements((metas[p].get("info") or {}).get("requires_dist")):
            if name in packages:
                continue
            if marker or extras:
                raise InstallRefused(f"the dependency {name}{extras} ({marker or 'no marker'}) of {p} carries an "
                                     "extra or a marker the tool does not evaluate")
            wanted[name] = ",".join(x for x in (wanted.get(name, ""), spec) if x)
    return wanted


def pick_dependency(meta: dict, tag: str, spec: str) -> tuple[str, dict]:
    """The dependency rule: the release :func:`pick_release` picks with ``pure_ok``, whose version meets ``spec``,
    lower bounds only."""
    version, wheel = pick_release(meta, tag, pure_ok=True)
    if not lower_bounds_ok(spec, version):
        raise InstallRefused(f"{spec!r} is not lower bounds met by the newest release {version}")
    return version, wheel


def requirements_line(name: str, version: str, wheel: dict) -> str:
    return f"{name}=={version} --hash=sha256:{wheel['digests']['sha256']}"


def installed_set(site_packages: Path) -> tuple[str, int]:
    rows = []
    for p in sorted(site_packages.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rows.append(f"{p.relative_to(site_packages).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(), len(rows)


def check_summary(chk: dict) -> dict:
    """A step's boundary check as the record keeps it (the window's own shape)."""
    native = chk.get("native") or {}
    return {"complete": chk.get("complete"), "native_hits": native.get("hits"),
            "loopback_hits": native.get("loopback_hits"), "fs_hits": (chk.get("fs") or {}).get("fs_hits")}


def check_problems(step: str, summary: dict) -> list[str]:
    """A step's own boundary check, judged like a window's: complete, no egress hit, no change in the watched set
    (an unknown count is a problem too)."""
    problems = []
    if not summary.get("complete"):
        problems.append(f"the {step} check is not complete")
    if summary.get("native_hits") != 0:
        problems.append(f"the {step} check counted {summary.get('native_hits')} egress hit(s)")
    if summary.get("fs_hits") != 0:
        problems.append(f"the {step} check counted {summary.get('fs_hits')} change(s) in the watched set")
    return problems


def _step(c, L, *, stand: str, run: str, arm: str, argv: list[str], path_dirs: list[Path], parent_env, native, fs,
          check_id: str) -> tuple[int | None, bytes, bytes, dict]:
    """One offline contract spawn (no proxy variables) under its own boundary check."""
    unit = L.make_unit_dirs(c, stand, run, arm, arm[0] + "1")
    env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=path_dirs, declared={}, catcher_url="",
                      proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    W.begin_check(check_id)
    try:
        child = L.spawn(c, argv, env=env, cwd=unit.cwd,
                        record={"role": "install", "stand": None, "run": run, "arm": arm, "unit": arm[0] + "1"},
                        parent_env=parent_env, catcher_url="", witnesses=W, requirement="required",
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = child.process.communicate(timeout=STEP_TIMEOUT)
            rc = child.process.returncode
        except subprocess.TimeoutExpired:
            child.kill_tree()
            rc, out, err = None, b"", b""
    finally:
        chk = W.end_check(check_id)
    return rc, out, err, chk


def _write(base: Path, record: dict) -> dict:
    record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (base / "install_record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


def run_install(c, L, F, *, python: Path, venv: Path, run: str, via_port: int, parent_env, packages=PACKAGES,
                tag: str = TARGET_TAG, native=None, fs=None, child_env_extra: dict | None = None,
                index_host: str = "pypi.org") -> dict:
    """The whole install. ``child_env_extra`` is for tests only (a CA file for the fake index); a real run passes none.
    ``F`` is research/v3/fetch_a3 (its window mechanism)."""
    if venv.exists():
        raise InstallRefused("the venv already exists: an install window creates it fresh")
    base = c.runs_root / "_install" / WINDOW / run
    if base.exists():
        raise InstallRefused("this install run label was used before")
    base.mkdir(parents=True)
    stand = f"_install.{WINDOW}"
    record: dict = {"window": WINDOW, "run": run, "venv": str(venv), "tag": tag, "packages": list(packages),
                    "problems": []}
    # 1. the venv, from py314, before the window (no network)
    rc, _, err, chk0 = _step(c, L, stand=stand, run=run, arm="venv", argv=[os.fspath(python), "-I", "-B", "-m", "venv",
                                                                        os.fspath(venv)],
                             path_dirs=[python.parent], parent_env=parent_env, native=native, fs=fs,
                             check_id=f"install-{WINDOW}-{run}-venv")
    record["venv_rc"] = rc
    record["venv_check"] = check_summary(chk0)
    record["problems"] += check_problems("venv", record["venv_check"])
    if rc != 0 or not venv_python(venv).is_file():
        record["problems"].append(f"the venv could not be created (exit {rc}): "
                                  + err.decode("utf-8", "replace")[-200:].replace("\n", " "))
    if record["problems"]:
        return _write(base, record)                     # no window opens after a failed offline step
    req_path = base / "requirements.txt"
    pip_cache = c.polygon_root / "pip_cache"
    chosen: dict = {}
    refused: list[str] = []

    def failed(results) -> bool:
        return any(r["rc"] != 0 or not all(s.get("ok") for s in r["summary"]) for r in results)

    def meta_job(results):
        return {"hosts": [index_host], "max_redirects": 0, "requests": [
            {"id": f"pypi:{p}", "url": f"https://{index_host}/pypi/{p}/json", "save": f"pypi/{p}.json",
             "max_bytes": META_MAX} for p in packages]}

    def deps_job(results):
        if failed(results):
            return None
        try:
            metas = {p: json.loads((Path(results[0]["unit"]) / "pypi" / f"{p}.json").read_bytes()) for p in packages}
            for p in packages:
                chosen[p] = pick_release(metas[p], tag)
            wanted = wanted_dependencies(metas, packages)
        except InstallRefused as e:
            refused.append(f"refused: {e}")
            return None
        record["dependencies"] = sorted(wanted)
        record["dependency_specifiers"] = dict(sorted(wanted.items()))
        if not wanted:
            return None
        return {"hosts": [index_host], "max_redirects": 0, "requests": [
            {"id": f"pypi:{d}", "url": f"https://{index_host}/pypi/{d}/json", "save": f"pypi/{d}.json",
             "max_bytes": META_MAX} for d in sorted(wanted)]}

    def pip_job(results):
        if refused or failed(results) or len(chosen) != len(packages):
            return None
        lines = [requirements_line(p, *chosen[p]) for p in packages]
        deps: dict = {}
        try:
            for d in record["dependencies"]:
                meta = json.loads((Path(results[1]["unit"]) / "pypi" / f"{d}.json").read_bytes())
                try:
                    version, wheel = pick_dependency(meta, tag, record["dependency_specifiers"][d])
                except InstallRefused as e:
                    raise InstallRefused(f"the dependency {d}: {e}") from None
                deps[d] = (version, wheel)
                lines.append(requirements_line(d, version, wheel))
        except InstallRefused as e:
            refused.append(f"refused: {e}")
            return None
        req_path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        record["requirements"] = lines
        record["wheels"] = {p: {"version": v, "filename": w["filename"], "sha256": w["digests"]["sha256"]}
                            for p, (v, w) in {**chosen, **deps}.items()}
        return {"child": "pip", "python": os.fspath(venv_python(venv)),
                "argv": ["-I", "-B", "-m", "pip", "install", "--require-hashes", "--no-deps", "--only-binary=:all:",
                         "--no-input", "--disable-pip-version-check", "-r", os.fspath(req_path)],
                "env": {"PIP_CACHE_DIR": os.fspath(pip_cache), "PIP_CONFIG_FILE": os.devnull,
                        "PIP_INDEX_URL": f"https://{index_host}/simple", "PIP_NO_INPUT": "1"}}

    # 2-3. one declared window: metadata, then pip
    rec = F.run_child_window(c, L, window=WINDOW, hosts=list(HOSTS) if index_host == "pypi.org" else [index_host, *HOSTS[1:]],
                             jobs=[meta_job, deps_job, pip_job], python=python, via_port=via_port, run=run,
                             parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             volume=Path(c.runs_root.anchor))
    record["window_record"] = {k: rec[k] for k in ("problems", "check", "issuers")}
    record["window_record"]["tunnelled_hosts"] = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["pip_ran"] = any((j.get("job") or {}).get("child") == "pip" for j in rec["jobs"])
    record["problems"] += list(rec["problems"]) + refused
    # 4. the check and the installed set
    site = next((p for p in venv.rglob("site-packages") if p.is_dir()), None)
    if not record["problems"] and site is not None:
        imports = "; ".join(f"import {p}" for p in packages)
        versions = ", ".join(f"{p}.__version__" for p in packages)
        rc, out, _, chk1 = _step(c, L, stand=stand, run=run, arm="check",
                                 argv=[os.fspath(venv_python(venv)), "-I", "-B", "-c", f"{imports}; print({versions})"],
                                 path_dirs=[venv_python(venv).parent], parent_env=parent_env, native=native, fs=fs,
                                 check_id=f"install-{WINDOW}-{run}-check")
        record["import_rc"], record["import_version"] = rc, out.decode("utf-8", "replace").strip()
        record["import_check"] = check_summary(chk1)
        record["problems"] += check_problems("import", record["import_check"])
        record["installed_set_sha256"], record["installed_files"] = installed_set(site)
        if rc != 0:
            record["problems"].append(f"the installed package does not import (exit {rc})")
        else:
            want = ", ".join(chosen[p][0] for p in packages)
            if record["import_version"] != want:
                record["problems"].append(f"the imported version {record['import_version']!r} is not the pinned {want!r}")
    elif not record["problems"]:
        record["problems"].append("the venv has no site-packages")
    return _write(base, record)


def _load(name: str, path: Path):
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the v3_data install window (pyarrow)")
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
    rec = run_install(c, L, F, python=Path(args.python), venv=c.polygon_root / "v3_data", run=args.run, via_port=via,
                      parent_env=os.environ)
    print(json.dumps({k: rec.get(k) for k in ("problems", "wheels", "dependencies", "import_version",
                                              "installed_set_sha256", "installed_files", "window_record")},
                     indent=1, default=list))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
