#!/usr/bin/env python3
"""PREREG-V3 plan step A3 (the auditor's P2 ruling): research/v3/install_v3_data.py, offline, real processes.

Against a fake PyPI (pypi.org and files.pythonhosted.org behind a fake hop and a local TLS server with a throwaway
certificate) and a wheel built here, under the launch contract:

* the venv is created fresh from the given interpreter (a contract spawn), and an existing one refuses the install;
  a failed boundary check of that offline step stops the install before any window opens;
* one window, exactly the index hosts: a fetch child reads the package's JSON; the declared rule picks the newest
  release with exactly one wheel for the target tag (pre-releases and yanked files never); hard dependencies (no
  ``extra`` marker) are named and fetched the same way, a py3-none-any wheel allowed for them, lower bounds only -
  an upper bound, a pin, an extra or a marker is refused by name, and pip never runs;
* a failed metadata request is a named problem, and nothing after it runs;
* pip runs as a child: --require-hashes --no-deps --only-binary=:all:, from a requirements file whose every line
  carries the sha256 PyPI gave; its cache is inside the polygon and PIP_CONFIG_FILE is os.devnull (no global, user or
  site file); a wheel whose bytes do not match its hash is refused by pip and named;
* afterwards the import is checked in the venv under its own boundary check (judged), the imported version must be
  the pinned one, and the installed set is hashed; the record carries the requirements, the wheels, the dependencies
  and every check.

    python tests/_test_v3_install_v3_data.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402

_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_inst", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_inst", ROOT / "research" / "v3" / "fetch_a3.py")
I = _load("v3_install", ROOT / "research" / "v3" / "install_v3_data.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, exc, words: str = "") -> bool:
    """``fn`` raises ``exc`` - and, when ``words`` is given, its message names them."""
    try:
        fn()
    except exc as e:
        return words in str(e)
    except Exception:  # noqa: BLE001 - the wrong exception is a failure too
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_install_"))
PY, FILES = "pypi.org", "files.pythonhosted.org"
TAG = "py3-none-any"


def wheel(name: str, version: str, code_version: str | None = None) -> tuple[str, bytes]:
    """A minimal valid wheel: one module with __version__ (``code_version`` when given), METADATA, WHEEL, RECORD."""
    fn = f"{name}-{version}-{TAG}.whl"
    di = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": f'__version__ = "{code_version or version}"\n'.encode(),
             f"{di}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
             f"{di}/WHEEL": b"Wheel-Version: 1.0\nGenerator: nvt3-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = "".join(f"{p},,\n" for p in files) + f"{di}/RECORD,,\n"
    files[f"{di}/RECORD"] = record.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return fn, buf.getvalue()


def index_routes(name: str, *, good: bool = True, deps=None, code_version=None):
    fn, data = wheel(name, "1.2.0", code_version)
    old_fn, old_data = wheel(name, "1.1.0")
    digest = hashlib.sha256(data if good else b"not the wheel").hexdigest()
    rel = lambda f, d, dg: {"packagetype": "bdist_wheel", "filename": f, "url": f"https://{FILES}/packages/{f}",  # noqa: E731
                            "digests": {"sha256": dg}, "yanked": False}
    meta = {"info": {"name": name, "version": "1.2.0", "requires_dist": deps or []},
            "releases": {"1.2.0": [rel(fn, data, digest)], "1.1.0": [rel(old_fn, old_data, hashlib.sha256(old_data).hexdigest())],
                         "1.3.0rc1": [rel(f"{name}-1.3.0rc1-{TAG}.whl", b"x", "0" * 64)]}}
    simple = (f'<html><body><a href="https://{FILES}/packages/{fn}#sha256={digest}">{fn}</a>'
              f'<a href="https://{FILES}/packages/{old_fn}#sha256={hashlib.sha256(old_data).hexdigest()}">{old_fn}</a>'
              "</body></html>").encode()
    return {f"/pypi/{name}/json": (200, [("Content-Type", "application/json")], json.dumps(meta).encode()),
            f"/simple/{name}/": (200, [("Content-Type", "text/html")], simple),
            f"/packages/{fn}": (200, [("Content-Type", "application/octet-stream")], data),
            f"/packages/{old_fn}": (200, [("Content-Type", "application/octet-stream")], old_data)}, digest


MAN = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]["a3-pyarrow"]
print("\n- the declared window -")
check("the tool's window, hosts, packages and tag are the manifest's",
      I.WINDOW == "a3-pyarrow" and sorted(I.HOSTS) == sorted(MAN["hosts"]) and list(I.PACKAGES) == MAN["packages"]
      and I.TARGET_TAG == MAN["tag"])

print("\n- the release rule and the dependency rule -")
meta = {"releases": {"2.0.0": [{"packagetype": "bdist_wheel", "filename": "p-2.0.0-cp314-cp314-win_amd64.whl",
                                "digests": {"sha256": "a" * 64}}],
                     "10.0.0": [{"packagetype": "bdist_wheel", "filename": "p-10.0.0-cp313-cp313-win_amd64.whl",
                                 "digests": {"sha256": "b" * 64}}],
                     "3.0.0rc1": [{"packagetype": "bdist_wheel", "filename": "p-3.0.0rc1-cp314-cp314-win_amd64.whl",
                                   "digests": {"sha256": "c" * 64}}],
                     "2.5.0": [{"packagetype": "bdist_wheel", "filename": "p-2.5.0-cp314-cp314-win_amd64.whl",
                                "digests": {"sha256": "d" * 64}, "yanked": True}]}}
v, w = I.pick_release(meta, "cp314-cp314-win_amd64")
check("the newest final release with exactly one wheel for the tag (no pre-release, no yanked, no other tag)",
      v == "2.0.0" and w["digests"]["sha256"] == "a" * 64, v)
check("a version compares by its numbers (10 > 2)", I.pick_release(meta, "cp313-cp313-win_amd64")[0] == "10.0.0")
check("hard dependencies are the Requires-Dist entries without an extra marker",
      I.hard_dependencies(["numpy>=1.16", "pandas; extra == 'pandas'", 'tzdata; sys_platform == "win32"'])
      == ["numpy", "tzdata"])
check("a requirement is read as (name, specifier, marker, extras), the old parenthesised form too",
      I.hard_requirements(["numpy>=1.16", 'tzdata; sys_platform == "win32"', "foo[bar]>=1", "Old_Pkg (>=2.0)"])
      == [("numpy", ">=1.16", "", ""), ("tzdata", "", 'sys_platform == "win32"', ""), ("foo", ">=1", "", "[bar]"),
          ("old_pkg", ">=2.0", "", "")], str(I.hard_requirements(["Old_Pkg (>=2.0)"])))
pure = lambda v, dg, tag="py3-none-any": {"packagetype": "bdist_wheel", "filename": f"d-{v}-{tag}.whl",  # noqa: E731
                                          "digests": {"sha256": dg * 64}}
meta_d = {"releases": {"3.0.0": [pure("3.0.0", "e")], "2.0.0": [pure("2.0.0", "f", "cp314-cp314-win_amd64")],
                       "4.0.0": [pure("4.0.0", "1", "cp314-cp314-win_amd64"), pure("4.0.0", "2", "cp314-cp314-win_amd64"),
                                 pure("4.0.0", "3")]}}
check("a package never takes a py3-none-any wheel; a dependency may, when its release has none for the tag",
      I.pick_release(meta_d, "cp314-cp314-win_amd64")[0] == "2.0.0"
      and I.pick_release(meta_d, "cp314-cp314-win_amd64", pure_ok=True) == ("3.0.0", pure("3.0.0", "e")))
check("an ambiguous tag release is skipped, never replaced by its pure wheel",
      I.pick_release(meta_d, "cp314-cp314-win_amd64", pure_ok=True)[0] != "4.0.0")
for spec, version, want in ((">=1.16", "2.0", True), (">=1.16.0", "1.16", True), (">1.16", "1.16", False),
                            (">=1.16,>1.0", "1.17", True), ("", "0.1", True), ("<2", "1.0", False),
                            ("==1.0", "1.0", False), ("~=1.0", "1.1", False), ("!=1.5", "2.0", False),
                            (">=1.0,<2", "1.5", False), (">=2.0", "1.9", False), (">= 1.2", "1.2.0", True)):
    check(f"specifier {spec!r} with version {version}: {'met' if want else 'refused'}", I.lower_bounds_ok(spec, version) is want)
check("the dependency rule refuses an unmet or non-lower bound by naming it",
      I.pick_dependency(meta_d, "cp314-cp314-win_amd64", ">=2")[0] == "3.0.0"
      and all(refused(lambda s=s: I.pick_dependency(meta_d, "cp314-cp314-win_amd64", s), I.InstallRefused, repr(s))
              for s in (">=3.1", "<4", "==3.0.0")))
metas = {"a": {"info": {"requires_dist": ["x>=1", "y", "a", "x>2; extra == 'z'"]}}, "b": {"info": {"requires_dist": ["x>=1.5"]}}}
check("the wanted dependencies join each one's specifiers and skip the packages themselves",
      I.wanted_dependencies(metas, ("a", "b")) == {"x": ">=1,>=1.5", "y": ""}, str(I.wanted_dependencies(metas, ("a", "b"))))
check("a dependency with a marker or an extra is refused, by name",
      refused(lambda: I.wanted_dependencies({"a": {"info": {"requires_dist": ['t; sys_platform == "win32"']}}}, ("a",)),
              I.InstallRefused, "the dependency t")
      and refused(lambda: I.wanted_dependencies({"a": {"info": {"requires_dist": ["u[v]>=1"]}}}, ("a",)),
                  I.InstallRefused, "the dependency u[v]"))
OKC = {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0}
check("a clean step check has no problem", I.check_problems("venv", OKC) == [])
for label, change, want in (("not complete", {"complete": False}, "not complete"), ("one egress hit", {"native_hits": 1}, "egress"),
                            ("an unknown hit count", {"native_hits": None}, "egress"), ("a watched change", {"fs_hits": 1}, "watched"),
                            ("an unknown watched count", {"fs_hits": None}, "watched")):
    got = I.check_problems("venv", {**OKC, **change})
    check(f"a step check with {label}: exactly one named problem", len(got) == 1 and want in got[0] and "venv" in got[0], str(got))

made = TF.make_test_cert(TMP / "cert", PY, extra_hosts=(FILES,))
if made is None:
    print("  SKIP the install window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 install v3_data: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def contract(tag):
    base = TMP / tag
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                      conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                      system_dirs=(Path(sys.executable).parent,)), base


def flaky(suffix):
    """The launch module with one boundary check reported incomplete: the check whose id ends with ``suffix``."""
    class Flaky(L.Witnesses):
        def end_check(self, check_id):
            got = super().end_check(check_id)
            return {**got, "complete": False} if check_id.endswith(suffix) else got
    ns = types.SimpleNamespace(**{k: getattr(L, k) for k in dir(L) if not k.startswith("__")})
    ns.Witnesses = Flaky
    return ns


def install(tag, routes, name="nvt3fake", L_=L):
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    c, base = contract(tag)
    try:
        rec = I.run_install(c, L_, F, python=Path(sys.executable), venv=c.polygon_root / "v3_data", run="i1",
                            via_port=hop.port, parent_env=os.environ, packages=(name,), tag=TAG,
                            native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                            fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                            child_env_extra={"SSL_CERT_FILE": str(made[0]), "PIP_CERT": str(made[0])})
    except Exception as e:  # noqa: BLE001 - a crash fails the checks that follow, by their names
        rec = {"problems": [f"crash: {type(e).__name__}: {e}"], "pip_ran": None, "crashed": True}
    finally:
        hop.close()
        srv.close()
    return rec, c, base


print("\n- a whole install window -")
routes, digest = index_routes("nvt3fake")
rec, C, BASE = install("ok", routes)
check("the install has no problem", rec["problems"] == [], str(rec["problems"]))
check("the requirements pin the newest final release with PyPI's sha256",
      rec["requirements"] == [f"nvt3fake==1.2.0 --hash=sha256:{digest}"]
      and rec["wheels"]["nvt3fake"]["filename"] == f"nvt3fake-1.2.0-{TAG}.whl", str(rec.get("requirements")))
check("no hard dependency was named, and none fetched", rec["dependencies"] == [])
check("the import in the venv gives the pinned version", rec["import_rc"] == 0 and rec["import_version"] == "1.2.0",
      str((rec.get("import_rc"), rec.get("import_version"))))
check("the installed set is hashed", len(rec["installed_set_sha256"]) == 64 and rec["installed_files"] >= 3)
check("the window tunnelled only the index hosts", set(rec["window_record"]["tunnelled_hosts"]) <= {PY, FILES}
      and PY in rec["window_record"]["tunnelled_hosts"] and FILES in rec["window_record"]["tunnelled_hosts"],
      str(rec["window_record"]))
check("the venv step and the import step each ran under a clean check of their own, and pip ran",
      rec["venv_check"] == rec["import_check"] == {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0}
      and rec["pip_ran"] is True and rec["window"] == "a3-pyarrow", str((rec.get("venv_check"), rec.get("import_check"))))
pip_spawn = [s for s in F._jsonl(L.spawns_log(C)) if s.get("role") == "fetch" and "PIP_CACHE_DIR" in s["env_names"]]
check("pip ran as a required, witnessed child, with its cache and an empty config inside the polygon",
      len(pip_spawn) == 1 and pip_spawn[0]["witness"]["requirement"] == "required"
      and {"PIP_CONFIG_FILE", "PIP_INDEX_URL", "PIP_NO_INPUT"} <= set(pip_spawn[0]["env_names"]))
win_rec = json.loads((C.runs_root / "_fetch" / "a3-pyarrow" / "i1" / "record.json").read_bytes())
pip_env = [j["job"] for j in win_rec["jobs"] if j["job"].get("child") == "pip"][0]["env"]
check("pip's cache points inside the polygon and its config file is os.devnull (pip reads no global, user or site file)",
      Path(pip_env["PIP_CACHE_DIR"]) == C.polygon_root / "pip_cache" and pip_env["PIP_CONFIG_FILE"] == os.devnull
      and pip_env["PIP_INDEX_URL"] == f"https://{PY}/simple", str(pip_env))
check("the record is written", json.loads((C.runs_root / "_install/a3-pyarrow/i1/install_record.json").read_bytes())
      ["problems"] == [])

print("\n- refusals -")
try:
    I.run_install(C, L, F, python=Path(sys.executable), venv=C.polygon_root / "v3_data", run="i2", via_port=1,
                  parent_env=os.environ, packages=("nvt3fake",), tag=TAG)
    check("an existing venv refuses the install", False)
except I.InstallRefused:
    check("an existing venv refuses the install", True)
bad_routes, _ = index_routes("nvt3fake", good=False)
rec2, _, _ = install("badhash", bad_routes)
check("a wheel whose bytes do not match PyPI's sha256 is refused by pip, and the install names it",
      rec2["problems"] != [] and any("pip" in p or "exited" in p for p in rec2["problems"])
      and rec2.get("import_rc") is None, str(rec2["problems"]))

print("\n- dependencies -")
main_r, main_dg = index_routes("nvt3fake", deps=["nvt3dep>=1.0"])
dep_r, dep_dg = index_routes("nvt3dep")
rec3, _, _ = install("dep", {**main_r, **dep_r})
check("a hard dependency is named, fetched, pinned with its sha256 and installed; the import holds",
      rec3["problems"] == [] and rec3["dependencies"] == ["nvt3dep"]
      and rec3["requirements"] == [f"nvt3fake==1.2.0 --hash=sha256:{main_dg}", f"nvt3dep==1.2.0 --hash=sha256:{dep_dg}"]
      and rec3["wheels"]["nvt3dep"]["version"] == "1.2.0" and rec3["import_version"] == "1.2.0",
      str((rec3["problems"], rec3.get("requirements"))))
main_up, _ = index_routes("nvt3fake", deps=["nvt3dep<1.0"])
rec4, c4, _ = install("depup", {**main_up, **dep_r})
check("a dependency with an upper bound is refused by name; pip never runs; the record is written",
      any(p.startswith("refused: the dependency nvt3dep") for p in rec4["problems"]) and rec4["pip_ran"] is False
      and "import_rc" not in rec4
      and json.loads((c4.runs_root / "_install/a3-pyarrow/i1/install_record.json").read_bytes())["problems"] == rec4["problems"],
      str(rec4["problems"]))

main_mk, _ = index_routes("nvt3fake", deps=['nvt3dep; sys_platform == "win32"'])
rec4m, _, _ = install("depmark", {**main_mk, **dep_r})
check("a dependency with a marker is refused by name, and pip never runs (not even for the package alone)",
      any(p.startswith("refused: the dependency nvt3dep") for p in rec4m["problems"]) and rec4m["pip_ran"] is False,
      str(rec4m["problems"]))

print("\n- a failed request, a failed check -")
no_meta = {k: v for k, v in routes.items() if not k.startswith("/pypi/")}
rec5, _, _ = install("nometa", no_meta)
check("a failed metadata request is a named problem; nothing after it runs",
      any("pypi:nvt3fake" in p for p in rec5["problems"]) and rec5["pip_ran"] is False and "import_rc" not in rec5
      and "requirements" not in rec5, str(rec5["problems"]))
rec6, c6, _ = install("venvchk", routes, L_=flaky("-venv"))
check("an incomplete check of the venv step stops the install before any window opens",
      rec6["problems"] == ["the venv check is not complete"] and not (c6.runs_root / "_fetch").exists()
      and "window_record" not in rec6, str(rec6["problems"]))
rec7, _, _ = install("impchk", routes, L_=flaky("-check"))
check("an incomplete check of the import step is a named problem",
      rec7["problems"] == ["the import check is not complete"] and rec7["import_rc"] == 0, str(rec7["problems"]))
vm_r, _ = index_routes("nvt3fake", code_version="9.9")
rec8, _, _ = install("vermis", vm_r)
check("an imported version that is not the pinned one is a named problem",
      rec8["problems"] == ["the imported version '9.9' is not the pinned '1.2.0'"], str(rec8["problems"]))
check("no install above crashed", not any(r.get("crashed") for r in (rec, rec2, rec3, rec4, rec4m, rec5, rec6, rec7, rec8)))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 install v3_data: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
