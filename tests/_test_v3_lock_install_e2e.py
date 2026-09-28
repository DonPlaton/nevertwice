#!/usr/bin/env python3
"""PREREG-V3 A8 C2 (Q-A8-1 O-a): research/v3/lock_install.py end to end - real processes (this suite spawns children:
the venv step, pip in the window, the fetch child, pip offline, the import check), against a fake PyPI (pypi.org and
files.pythonhosted.org behind a fake hop and a local TLS server with a throwaway certificate) and wheels built here:

* E1: pip's own resolver picks the tree - nvt3a==1.0.0 pulls its dependency nvt3b (>=0.1, the newest 0.2.0) - and the
  lock holds both with the index's sha256s; the wheels are downloaded, checked from the disk, installed offline with
  --require-hashes --no-deps --no-index; the installed set is hashed, the imports give the locked versions, and every
  step ran under a clean boundary check of its own; the window tunnelled exactly the two index hosts;
* E2: the offline install is a spawn with no proxy variable, and its argv names --no-index and the lock;
* E3 (attempt 1): a distribution that ships only an sdist fails pip's wheels-only resolution by name - nothing is
  downloaded and nothing installed;
* E4: a wheel whose bytes are not the index's sha256 is refused while it streams - nothing installed.
* B-NLP: with real pip, a spec's extra brings its distribution (the lock records the extra, the install and the
  imports pass), and a package that does not provide the extra is refused at the lock by name, before any wheel
  is downloaded;

    python tests/_test_v3_lock_install_e2e.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import platform
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
for _exe in list(_TEST_EXC):
    _TEST_EXC.setdefault(os.path.realpath(_exe), "the test interpreter's real path")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_lie", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_lie", ROOT / "research" / "v3" / "fetch_a3.py")
LI = _load("v3_lock_install_lie", ROOT / "research" / "v3" / "lock_install.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_lock_e2e_"))
PY, FILES = "pypi.org", "files.pythonhosted.org"


def wheel(name: str, version: str, requires: tuple = ()) -> tuple[str, bytes]:
    fn = f"{name}-{version}-py3-none-any.whl"
    di = f"{name}-{version}.dist-info"
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nLicense: MIT\n" + "".join(
        f"Requires-Dist: {r}\n" for r in requires)
    files = {f"{name}/__init__.py": f'__version__ = "{version}"\n'.encode(), f"{di}/METADATA": meta.encode(),
             f"{di}/WHEEL": b"Wheel-Version: 1.0\nGenerator: nvt3-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    files[f"{di}/RECORD"] = ("".join(f"{p},sha256={_b64(hashlib.sha256(b).digest())},{len(b)}\n" for p, b in files.items())
                             + f"{di}/RECORD,,\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return fn, buf.getvalue()


def _provides(whl: tuple[str, bytes], extra: str) -> tuple[str, bytes]:
    """The same wheel, its METADATA declaring Provides-Extra (and its RECORD rehashed)."""
    fn, data = whl
    src = zipfile.ZipFile(io.BytesIO(data))
    files = {n: src.read(n) for n in src.namelist() if not n.endswith("/RECORD")}
    di = next(n.split("/")[0] for n in files if n.endswith("/METADATA"))
    files[f"{di}/METADATA"] = files[f"{di}/METADATA"].replace(b"License: MIT\n", f"License: MIT\nProvides-Extra: {extra}\n".encode())
    files[f"{di}/RECORD"] = ("".join(f"{p},sha256={_b64(hashlib.sha256(b).digest())},{len(b)}\n" for p, b in files.items())
                             + f"{di}/RECORD,,\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return fn, buf.getvalue()


def _b64(d: bytes) -> str:
    import base64  # noqa: PLC0415
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def index(packages: dict, *, served_bytes: dict | None = None) -> dict:
    """packages: name -> [(filename, bytes)]; each /simple/<name>/ page links its files with the index's sha256."""
    routes = {}
    for name, files in packages.items():
        links = "".join(f'<a href="https://{FILES}/packages/{fn}#sha256={hashlib.sha256(data).hexdigest()}">{fn}</a>'
                        for fn, data in files)
        routes[f"/simple/{name}/"] = (200, [("Content-Type", "text/html")], f"<html><body>{links}</body></html>".encode())
        for fn, data in files:
            routes[f"/packages/{fn}"] = (200, [("Content-Type", "application/octet-stream")],
                                         (served_bytes or {}).get(fn, data))
    return routes


made = TF.make_test_cert(TMP / "cert", PY, extra_hosts=(FILES,))
if made is None:
    print("  SKIP the lock install checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 lock install e2e: {PASSED} passed, {FAILED} failed")
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


def install(tag, routes, specs, imports, dists, parent_env=None):
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    c, base = contract(tag)
    # LI-1: the declared base is polygon/py312/python.exe - here a junction to this interpreter's directory, its version
    # this interpreter's (the test world's base; the real one is fetch_py_base's py-base-312)
    c.polygon_root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(Path(sys.executable).parent), str(c.polygon_root / "py312"))
    else:
        os.symlink(Path(sys.executable).parent, c.polygon_root / "py312", target_is_directory=True)
    try:
        rec = LI.run_lock_install(c, L, F, python=c.polygon_root / "py312" / Path(sys.executable).name,
                                  venv=c.polygon_root / "t_v3", venv_name="t_v3", base="py-base-312",
                                  bases={"py-base-312": {"version": platform.python_version(), "dest": "py312",
                                                         "newest_of": None}},
                                  run="l1", via_port=hop.port, parent_env=parent_env or os.environ, specs=specs,
                                  imports=imports,
                                  dists=dists, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                                  fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                                  child_env_extra={"SSL_CERT_FILE": str(made[0]), "PIP_CERT": str(made[0])},
                                  need_bytes=1, volume=TMP)
    except Exception as e:  # noqa: BLE001 - a crash fails the checks that follow, by their names
        rec = {"problems": [f"crash: {type(e).__name__}: {e}"], "crashed": True}
    finally:
        hop.close()
        srv.close()
        link = c.polygon_root / "py312"
        if link.exists():
            os.rmdir(link) if os.name == "nt" else link.unlink()      # the junction only, never its target
    return rec, c, base


A = wheel("nvt3a", "1.0.0", requires=("nvt3b>=0.1",))
B1, B2 = wheel("nvt3b", "0.1.0"), wheel("nvt3b", "0.2.0")
CLEAN = {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0}

print("- E1, E2: a whole lock install -")
POISON = {"PIP_INDEX_URL": "https://evil.example/simple", "PIP_EXTRA_INDEX_URL": "https://evil.example/extra",
          "PIP_CONFIG_FILE": str(TMP / "evil_pip.ini"), "PIP_FIND_LINKS": "https://evil.example/links"}
rec, C, BASE = install("ok", index({"nvt3a": [A], "nvt3b": [B1, B2]}), ["nvt3a==1.0.0"], ["nvt3a", "nvt3b"],
                       ["nvt3a", "nvt3b"], parent_env={**os.environ, **POISON})
sha = {fn: hashlib.sha256(d).hexdigest() for fn, d in (A, B1, B2)}
check("E1: the install has no problem", rec.get("problems") == [], str(rec.get("problems")))
check("E1: pip's own resolver picked the tree - nvt3a 1.0.0 and its dependency nvt3b at the newest 0.2.0 - and the lock "
      "holds both with the index's sha256s", [(e["name"], e["version"], e["sha256"], e["requested"]) for e in rec.get("lock") or []]
      == [("nvt3a", "1.0.0", sha[A[0]], True), ("nvt3b", "0.2.0", sha[B2[0]], False)]
      and rec.get("lock_sha256") == LI.lock_sha256(rec["lock"]), str(rec.get("lock")))
check("E1: the licences as METADATA gives them", rec.get("licences") == {"nvt3a": "MIT", "nvt3b": "MIT"}, str(rec.get("licences")))
check("E1: the wheels were checked from the disk, pip installed offline, the imports give the locked versions and the "
      "venv's python is the base's", rec.get("wheel_problems") == [] and rec.get("install_rc") == 0
      and (rec.get("import_versions") or {}).get("dists") == {"nvt3a": "1.0.0", "nvt3b": "0.2.0"}
      and rec.get("venv_python_version") == platform.python_version(),
      str({k: rec.get(k) for k in ("wheel_problems", "install_rc", "import_versions", "venv_python_version")}))
check("E1: the installed set is hashed; the venv, the install and the import each ran under a clean check of its own",
      len(rec.get("installed_set_sha256") or "") == 64 and rec.get("installed_files", 0) >= 4
      and rec.get("venv_check") == rec.get("install_check") == rec.get("import_check") == CLEAN,
      str({k: rec.get(k) for k in ("venv_check", "install_check", "import_check")}))
check("E1: the window tunnelled exactly the two index hosts, and its check is clean",
      sorted((rec.get("window_record") or {}).get("tunnelled_hosts") or []) == [FILES, PY]
      and ((rec.get("window_record") or {}).get("check") or {}).get("native_hits") == 0, str(rec.get("window_record")))
spawns = F._jsonl(L.spawns_log(C)) if (L.spawns_log(C)).exists() else []
offline = [s for s in spawns if s.get("role") == "install" and s.get("arm") == "pip"]
check("E2: the offline install is one spawn with no proxy variable, --no-index and the lock in its argv",
      len(offline) == 1 and not any(n.upper() in ("HTTPS_PROXY", "HTTP_PROXY") for n in offline[0]["env_names"])
      and "--no-index" in (rec.get("install_argv") or []) and (rec.get("install_argv") or [""])[-1].endswith("lock.txt"),
      str(offline[:1])[:200] + str(rec.get("install_argv"))[:200])
check("E1: the record is written", (C.runs_root / "_install" / "a8-pypi-t_v3" / "l1" / "install_record.json").is_file())
venv_py = LI._iv().venv_python(C.polygon_root / "t_v3")
pip_children = [s for s in spawns if "PIP_CACHE_DIR" in s.get("env_names", []) or (s.get("role") == "install" and s.get("arm") == "pip")]
check("E2b (the auditor): pip runs on the venv's own interpreter (never the harness's), isolated (-I), and no PIP_* of the "
      "parent's environment - PIP_INDEX_URL, PIP_EXTRA_INDEX_URL, PIP_CONFIG_FILE, PIP_FIND_LINKS were set there - "
      "reaches either pip child; only the window's own declared PIP_* reach the resolving one",
      len(pip_children) == 2
      and all(os.path.normcase(os.path.realpath((s.get("binary") or {}).get("path", ""))) ==
              os.path.normcase(os.path.realpath(venv_py)) for s in pip_children)
      and (rec.get("install_argv") or [None, None])[1] == "-I"
      and not any(n in POISON for n in offline[0]["env_names"] if n != "PIP_CONFIG_FILE")
      and {"PIP_CONFIG_FILE", "PIP_CACHE_DIR", "PIP_NO_INPUT"} <= set(offline[0]["env_names"]) if offline else False,
      str([((s.get("binary") or {}).get("path"), [n for n in s.get("env_names", []) if n.startswith("PIP_")])
           for s in pip_children])[:400])

print("\n- E3: attempt 1 takes wheels only -")
sd = ("nvt3c-1.0.0.tar.gz", b"not a wheel")
print("\n- B-NLP: a spec's extra with real pip -")
X = wheel("nvt3x", "1.0.0", ('nvt3e; extra == "nlp"',))
XE = wheel("nvt3e", "1.0.0")
recx, _, _ = install("extra", index({"nvt3x": [_provides(X, "nlp")], "nvt3e": [XE]}), ["nvt3x[nlp]==1.0.0"],
                     ["nvt3x", "nvt3e"], ["nvt3x", "nvt3e"])
check("B-NLP: nvt3x[nlp] brings its extra's distribution - the lock records the extra and holds nvt3e, the install "
      "and the imports pass", recx.get("problems") == []
      and [(e["name"], e.get("requested_extras")) for e in recx.get("lock") or []] == [("nvt3e", []), ("nvt3x", ["nlp"])],
      str({k: recx.get(k) for k in ("problems", "lock")})[:400])
recn, _, _ = install("noextra", index({"nvt3x": [X], "nvt3e": [XE]}), ["nvt3x[nlp]==1.0.0"], ["nvt3x", "nvt3e"],
                     ["nvt3x", "nvt3e"])
check("B-NLP: a package that does not provide the extra is refused at the lock, by name - before any wheel is "
      "downloaded or installed", any("lacks the declared distribution(s) ['nvt3e']" in p for p in recn.get("problems") or [])
      and recn.get("install_rc") is None, str(recn.get("problems"))[:300])
rec3, C3, _ = install("sdist", index({"nvt3c": [sd]}), ["nvt3c==1.0.0"], ["nvt3c"], ["nvt3c"])
check("E3: a distribution that ships only an sdist fails pip's wheels-only resolution by name; nothing is downloaded "
      "or installed", any("pip's resolution failed (attempt 1, wheels only)" in p for p in rec3.get("problems") or [])
      and not rec3.get("lock") and "install_rc" not in rec3, str(rec3.get("problems"))[:400])

print("\n- E4: a wheel that is not the index's sha256 -")
rec4, C4, _ = install("badwheel", index({"nvt3a": [A], "nvt3b": [B1, B2]}, served_bytes={B2[0]: b"tampered bytes"}),
                      ["nvt3a==1.0.0"], ["nvt3a", "nvt3b"], ["nvt3a", "nvt3b"])
check("E4: a wheel whose bytes are not the index's sha256 is refused - a named problem, nothing installed",
      rec4.get("problems") and "install_rc" not in rec4 and not (C4.polygon_root / "t_v3" / "Lib" / "site-packages" / "nvt3a").exists(),
      str(rec4.get("problems"))[:400])

print("\n- E5: a wheel that puts a .pth on the site -")
fn5, data5 = wheel("nvt3d", "1.0.0")
with zipfile.ZipFile(io.BytesIO(data5)) as z:
    members = {n: z.read(n) for n in z.namelist()}
members["nvt3d_hook.pth"] = b"import os\n"
di5 = "nvt3d-1.0.0.dist-info"
members[f"{di5}/RECORD"] = ("".join(f"{p},sha256={_b64(hashlib.sha256(b).digest())},{len(b)}\n" for p, b in members.items()
                                    if p != f"{di5}/RECORD") + f"{di5}/RECORD,,\n").encode()
buf5 = io.BytesIO()
with zipfile.ZipFile(buf5, "w") as z:
    for p, b in members.items():
        z.writestr(p, b)
rec5, C5, _ = install("pth", index({"nvt3d": [(fn5, buf5.getvalue())]}), ["nvt3d==1.0.0"], ["nvt3d"], ["nvt3d"])
check("E5: a locked wheel that adds a .pth is a named site problem, and no interpreter starts in the venv (no import step)",
      any("nvt3d_hook.pth" in p and "every interpreter start" in p for p in rec5.get("problems") or [])
      and rec5.get("import_skipped") and "import_rc" not in rec5, str(rec5.get("problems"))[:400])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 lock install e2e: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
