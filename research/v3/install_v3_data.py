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
  not a resolver, and --no-deps would install whatever the requirements file says;
* the chosen release must be the index's latest (``info.version``): PyPI's ``info`` - Requires-Dist included -
  describes that version only, so any other choice is refused, naming each newer release and why it was skipped;
* the tool installs one level: every hard requirement of every chosen release must already be chosen, its lower
  bounds met; a marker ``extra == ...`` without ``or`` is extra-only and dropped, one with ``or`` is refused;
* pip's trust is OpenSSL with its vendored certifi (``--use-deprecated=legacy-certs`` from pip 24.2, where truststore
  became the default and would ask the machine's CryptoAPI, whose AIA and root-update fetches go past the catcher);
  the record carries pip's version and the sha256 of the certifi bundle.

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
#: The auditor's Q-A3-2 floor (fetch_manifest.json "disk"): max(100 GB, 3 x the window). A pyarrow window is tens of
#: MB, so the floor is the 100 GB; it is checked before the venv is made, and again when the window opens.
DISK_FLOOR = 100 * (1 << 30)


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


def _finals(meta: dict) -> list[str]:
    return sorted((v for v in meta.get("releases", {}) if re.fullmatch(r"\d+(\.\d+)*", v)), key=_vkey, reverse=True)


def _verdict(files: list, tag: str, pure_ok: bool) -> tuple[dict | None, str]:
    """(the one wheel, "ok") for a release, or (None, why not): no tag wheel, yanked, ambiguous, unhashed."""
    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"]
    why = "no tag wheel"
    for t in (tag, PURE_TAG) if pure_ok else (tag,):
        tagged = [f for f in wheels if f.get("filename", "").endswith(f"-{t}.whl")]
        live = [f for f in tagged if not f.get("yanked")]
        if len(live) == 1 and re.fullmatch(r"[0-9a-f]{64}", (live[0].get("digests") or {}).get("sha256", "")):
            return live[0], "ok"
        if live:                                        # a tag wheel that is ambiguous or unhashed: never fall back
            return None, "ambiguous" if len(live) > 1 else "unhashed"
        if tagged and why == "no tag wheel":
            why = "yanked"
    return None, why


def pick_release(meta: dict, tag: str, *, pure_ok: bool = False) -> tuple[str, dict]:
    """The declared rule: the newest version (PyPI's own ordering of release keys by packaging's order is not
    available in the standard library, so: the highest version by numeric components, pre-releases excluded) that
    ships exactly one wheel for ``tag`` - or, with ``pure_ok`` (dependencies only), exactly one py3-none-any wheel
    when it has none for ``tag``. Yanked files never count. Returns (version, the wheel's url entry)."""
    for v in _finals(meta):
        wheel, _ = _verdict(meta["releases"][v], tag, pure_ok)
        if wheel is not None:
            return v, wheel
    raise InstallRefused(f"no release ships exactly one {tag} wheel" + (f" or one {PURE_TAG} wheel" if pure_ok else ""))


def skipped_newer(meta: dict, tag: str, version: str, *, pure_ok: bool = False) -> list[tuple[str, str]]:
    """Each final release newer than ``version``, with why the rule skipped it."""
    return [(v, _verdict(meta["releases"][v], tag, pure_ok)[1]) for v in _finals(meta) if _vkey(v) > _vkey(version)]


def pick_latest(meta: dict, tag: str, *, pure_ok: bool = False) -> tuple[str, dict]:
    """F-P2-1: :func:`pick_release`, which must pick the index's latest - PyPI's ``info`` (Requires-Dist included)
    describes ``info.version`` only, so a choice of another release would install it with the latest's dependencies."""
    version, wheel = pick_release(meta, tag, pure_ok=pure_ok)
    latest = (meta.get("info") or {}).get("version")
    if version != latest:
        raise InstallRefused(f"the chosen release {version} is not the index's latest {latest!r}, the one its metadata "
                             f"describes; skipped: {skipped_newer(meta, tag, version, pure_ok=pure_ok)}")
    return version, wheel


def norm_name(name: str) -> str:
    """PEP 503's normalised project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def hard_requirements(requires_dist: list | None) -> list[tuple[str, str, str, str]]:
    """(name, specifier, marker, extras) for each Requires-Dist entry pip would install without --no-deps. A marker
    ``extra == ...`` with no ``or`` is extra-only (a conjunction with an extra is one) and is dropped; any other
    marker - ``A or extra == "x"`` included, a hard dependency whenever A holds - is kept, for the caller to refuse.
    The old "name (>=1)" form is read too."""
    out = []
    for req in requires_dist or []:
        head, _, marker = req.partition(";")
        if re.search(r"\bextra\s*==", marker) and not re.search(r"\bor\b", marker):
            continue
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", head)
        if m:
            spec = m.group(3).strip()
            if spec.startswith("(") and spec.endswith(")"):
                spec = spec[1:-1].strip()
            out.append((norm_name(m.group(1)), spec, marker.strip(), m.group(2) or ""))
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
    """The dependency rule: the release :func:`pick_latest` picks with ``pure_ok``, whose version meets ``spec``,
    lower bounds only."""
    version, wheel = pick_latest(meta, tag, pure_ok=True)
    if not lower_bounds_ok(spec, version):
        raise InstallRefused(f"{spec!r} is not lower bounds met by the newest release {version}")
    return version, wheel


def one_level(metas: dict[str, dict], versions: dict[str, str]) -> None:
    """F-P2-2: the tool installs one level. Every hard requirement of every chosen release (``metas`` describe the
    chosen versions, by F-P2-1) must already be chosen, with its lower bounds met; a marker or an extra is refused."""
    for n in sorted(metas):
        for name, spec, marker, extras in hard_requirements((metas[n].get("info") or {}).get("requires_dist")):
            if marker or extras:
                raise InstallRefused(f"{n} requires {name}{extras} ({marker or 'no marker'}): an extra or a marker the "
                                     "tool does not evaluate")
            if name not in versions:
                raise InstallRefused(f"the tool installs one level: {n} requires {name}, which is not among the "
                                     f"chosen {sorted(versions)}")
            if not lower_bounds_ok(spec, versions[name]):
                raise InstallRefused(f"{n} requires {name} {spec!r}, not lower bounds met by the chosen {versions[name]}")


def pip_trust(version: str) -> tuple[list[str], str]:
    """F-P2-4: pip's trust is OpenSSL with its vendored certifi. From pip 24.2 truststore is the default (the
    machine's CryptoAPI chain engine, whose AIA and root-update fetches run in-process past the catcher), so the
    old path is asked for by name; an older pip has only that path."""
    if _vkey(version) >= (24, 2):
        return ["--use-deprecated=legacy-certs"], "certifi (legacy-certs)"
    return [], "certifi (pip < 24.2)"


def metadata_version(text: str) -> str:
    """The one "Version:" line of a METADATA file, a final release; anything else is refused (never a default)."""
    found = [line[len("Version:"):].strip() for line in text.splitlines() if line.startswith("Version:")]
    if len(found) != 1 or not re.fullmatch(r"\d+(\.\d+)*", found[0]):
        raise InstallRefused(f"pip's METADATA version is missing, ambiguous or unparseable: {found}")
    return found[0]


def pip_facts(venv: Path) -> dict:
    """The venv's pip: its version (its dist-info METADATA "Version:" line) and the sha256 of its certifi bundle."""
    site = next((s for s in venv.rglob("site-packages") if s.is_dir()), None)
    infos = sorted(site.glob("pip-*.dist-info")) if site is not None else []
    if len(infos) != 1:
        raise InstallRefused(f"the venv holds {len(infos)} pip dist-info directories, not one")
    meta = infos[0] / "METADATA"
    version = metadata_version(meta.read_text(encoding="utf-8", errors="replace") if meta.is_file() else "")
    cacert = site / "pip" / "_vendor" / "certifi" / "cacert.pem"
    if not cacert.is_file():
        raise InstallRefused("the venv's pip has no vendored certifi bundle")
    args, trust = pip_trust(version)
    return {"version": version, "certifi_sha256": hashlib.sha256(cacert.read_bytes()).hexdigest(), "trust": trust,
            "args": args}


def requirements_line(name: str, version: str, wheel: dict) -> str:
    return f"{name}=={version} --hash=sha256:{wheel['digests']['sha256']}"


def top_level(site_packages: Path) -> list[str]:
    return sorted(p.name for p in site_packages.iterdir()) if site_packages.is_dir() else []


def _record_hash(value: str) -> str | None:
    """A RECORD row's "sha256=<urlsafe base64, no padding>" as hex, or None when the row carries no sha256."""
    import base64  # noqa: PLC0415
    if not value.startswith("sha256="):
        return None
    b64 = value[len("sha256="):]
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).hex()


def site_problems(site_packages: Path, names, baseline: list[str]) -> list[str]:
    """The auditor's two conditions on the installed set: (1) the top-level names are the venv's own (``baseline``,
    recorded before the window) plus the pinned distributions' - anything else is a problem, and a *.pth or a
    *customize*.py the window added always is one (either runs code at every interpreter start); (2) every RECORD row
    that carries a sha256 matches the file on disk - the pinned distributions' and the baseline's own (pip ran in
    the window; a change to its files would otherwise be invisible)."""
    problems, allowed = [], set(baseline) | {"__pycache__"}
    base_dists = [b[:-len(".dist-info")].rsplit("-", 1)[0] for b in baseline if b.endswith(".dist-info")]
    for name in [*names, *base_dists]:                  # (a): the baseline's own (pip) is checked too
        for d in site_packages.glob("*.dist-info"):
            if norm_name(d.name[:-len(".dist-info")].rsplit("-", 1)[0]) != norm_name(name) or not (d / "RECORD").is_file():
                continue
            allowed.add(d.name)
            for line in (d / "RECORD").read_text(encoding="utf-8").splitlines():
                if line.count(",") < 2:
                    continue
                rel, h, _ = line.rsplit(",", 2)
                parts = rel.split("/")
                if not rel or ".." in parts:
                    continue
                allowed.add(parts[0])
                want = _record_hash(h)
                f = site_packages.joinpath(*parts)
                if want is None:
                    continue
                if not f.is_file():                     # P5c: a hashed row whose file is gone
                    problems.append(f"the installed file {rel} that its RECORD lists is missing")
                elif hashlib.sha256(f.read_bytes()).hexdigest() != want:
                    problems.append(f"the installed file {rel} is not the sha256 its RECORD names")
    for top in top_level(site_packages):
        danger = top.endswith(".pth") or "customize" in top
        if danger and top not in baseline:             # even when a pinned distribution lists it
            problems.append(f"site-packages holds {top}, which the venv did not have - it runs code at every "
                            "interpreter start")
        elif top not in allowed:
            problems.append(f"site-packages holds {top}, which no pinned distribution and not the venv installed")
    return problems


def installed_set(site_packages: Path, names) -> tuple[str, int]:
    """F-P2-5: (sha256, file count) over the pinned distributions' own files - each ``names`` distribution's
    dist-info RECORD, the entries inside site-packages, by (relpath, sha256 on disk). Left out: bytecode (it carries
    the source's mtime), the RECORD itself, and anything outside site-packages (launchers embed the venv's path).
    pip's own files are not the pin (its version and certifi digest are recorded apart); a missing RECORD or a
    listed file missing on disk is refused."""
    rows = set()
    for name in names:
        infos = [d for d in site_packages.glob("*.dist-info")
                 if norm_name(d.name[:-len(".dist-info")].rsplit("-", 1)[0]) == norm_name(name)]
        if len(infos) != 1 or not (infos[0] / "RECORD").is_file():
            raise InstallRefused(f"the installed set: {name} has {len(infos)} dist-info directories with a RECORD, not one")
        for line in (infos[0] / "RECORD").read_text(encoding="utf-8").splitlines():
            rel = line.rsplit(",", 2)[0] if line.count(",") >= 2 else ""
            parts = rel.split("/")
            if not rel or ".." in parts or "__pycache__" in parts or rel.endswith(".pyc") or rel == f"{infos[0].name}/RECORD":
                continue
            f = site_packages.joinpath(*parts)
            if not f.is_file():
                raise InstallRefused(f"the installed set: {rel} is in {name}'s RECORD but not on disk")
            rows.add(f"{rel}\0{hashlib.sha256(f.read_bytes()).hexdigest()}")
    ordered = sorted(rows)
    return hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest(), len(ordered)


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
                index_host: str = "pypi.org", need_bytes: int = DISK_FLOOR, volume: Path | None = None) -> dict:
    """The whole install. ``child_env_extra`` is for tests only (a CA file for the fake index); a real run passes none,
    and keeps the default floor. ``F`` is research/v3/fetch_a3 (its window mechanism)."""
    if venv.exists():
        raise InstallRefused("the venv already exists: an install window creates it fresh")
    volume = volume or Path(c.runs_root.anchor)
    ok, free = F.disk_floor_ok(volume, need_bytes)
    if not ok:
        raise InstallRefused(f"the free space ({free >> 30} GB) is under the floor ({need_bytes >> 30} GB)")
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
    if not record["problems"]:
        try:
            record["pip"] = pip_facts(venv)
        except InstallRefused as e:
            record["problems"].append(f"refused: {e}")
        base_site = next((s for s in venv.rglob("site-packages") if s.is_dir()), None)
        record["venv_top_level"] = top_level(base_site) if base_site is not None else []
    if record["problems"]:
        return _write(base, record)                     # no window opens after a failed offline step
    req_path = base / "requirements.txt"
    pip_cache = c.polygon_root / "pip_cache"
    chosen: dict = {}
    package_metas: dict = {}
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
                chosen[p] = pick_latest(metas[p], tag)
            wanted = wanted_dependencies(metas, packages)
            package_metas.update(metas)
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
        metas = dict(package_metas)
        try:
            for d in record["dependencies"]:
                meta = json.loads((Path(results[1]["unit"]) / "pypi" / f"{d}.json").read_bytes())
                try:
                    version, wheel = pick_dependency(meta, tag, record["dependency_specifiers"][d])
                except InstallRefused as e:
                    raise InstallRefused(f"the dependency {d}: {e}") from None
                deps[d], metas[d] = (version, wheel), meta
                lines.append(requirements_line(d, version, wheel))
            one_level({norm_name(n): m for n, m in metas.items()},
                      {norm_name(n): v for n, (v, _) in {**chosen, **deps}.items()})
        except InstallRefused as e:
            refused.append(f"refused: {e}")
            return None
        req_path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        record["requirements"] = lines
        record["wheels"] = {p: {"version": v, "filename": w["filename"], "sha256": w["digests"]["sha256"]}
                            for p, (v, w) in {**chosen, **deps}.items()}
        return {"child": "pip", "python": os.fspath(venv_python(venv)),
                "argv": ["-I", "-B", "-m", "pip", "install", *record["pip"]["args"], "--require-hashes", "--no-deps",
                         "--only-binary=:all:", "--no-input", "--disable-pip-version-check", "-r", os.fspath(req_path)],
                "env": {"PIP_CACHE_DIR": os.fspath(pip_cache), "PIP_CONFIG_FILE": os.devnull,
                        "PIP_INDEX_URL": f"https://{index_host}/simple", "PIP_NO_INPUT": "1"}}

    # 2-3. one declared window: metadata, then pip
    rec = F.run_child_window(c, L, window=WINDOW, hosts=list(HOSTS) if index_host == "pypi.org" else [index_host, *HOSTS[1:]],
                             jobs=[meta_job, deps_job, pip_job], python=python, via_port=via_port, run=run,
                             parent_env=parent_env, native=native, fs=fs, child_env_extra=child_env_extra,
                             need_bytes=need_bytes, volume=volume)
    record["window_record"] = {k: rec[k] for k in ("problems", "check", "issuers")}
    record["window_record"]["tunnelled_hosts"] = sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")})
    record["window_record"]["issuer_notes"] = {
        HOSTS[1]: "reached by pip only, so no fetch child records its issuer; pip's trust is its vendored certifi "
                  "(--use-deprecated=legacy-certs, F-P2-4), and a local interception root fails pip's verification"}
    record["pip_ran"] = any((j.get("job") or {}).get("child") == "pip" for j in rec["jobs"])
    record["problems"] += list(rec["problems"]) + refused
    # 4. the check and the installed set
    site = next((p for p in venv.rglob("site-packages") if p.is_dir()), None)
    # P6: the installed set and the site checks come BEFORE any interpreter starts in the venv - `python -I` still
    # runs a site-packages .pth - and any problem skips the import step (its absence is recorded with the reason).
    if not record["problems"] and site is not None:
        try:
            record["installed_set_sha256"], record["installed_files"] = installed_set(site, list(record["wheels"]))
        except InstallRefused as e:
            record["problems"].append(f"refused: {e}")
        record["problems"] += site_problems(site, list(record["wheels"]), record["venv_top_level"])
        if record["problems"]:
            record["import_skipped"] = "the installed site has problems; no interpreter was started in the venv"
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
