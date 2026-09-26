#!/usr/bin/env python3
"""PREREG-V3 R5 and the auditor's amended Q1: research/v3/fetch_py_base.py, offline, against a fake api.nuget.org.

* Through a loopback CONNECT hop to a local TLS server whose certificate is made at test time: the client tunnels to
  api.nuget.org:443, verifies TLS (an untrusted certificate is an ssl error, never a quiet fetch), records the
  issuer, and follows registration leaf -> catalog entry -> package, matching the package against the catalog's
  SHA512. A wrong hash, a catalog entry for another id or version, a package URL on another host (not even
  requested), a redirect and an encoded body are each refused by name.
* tools/ is unpacked into a fresh directory only: entries outside tools/ are skipped, an entry that would leave the
  directory (.., a drive) refuses the whole package with nothing written, an existing destination is refused; the
  (relpath, sha256) list is sorted and its digest is stable.
* The checks run the package's interpreter isolated and without bytecode (-I -B, PYTHONDONTWRITEBYTECODE=1, no
  PYTHONPATH/PYTHONHOME), remove their throwaway venv, and report a venv whose home is not the base, and any change
  in the tools directory's listing.
* A whole window with fake witnesses: START names the hop and api.nuget.org, END follows; the hop's pid is allowed
  inside the window and revoked at its end - also when the fetch is refused; the file-system witness leaves out
  py314 (the install target) and says so; the record carries the hashes, the issuer and the check.

No network. With neither ``cryptography`` nor ``openssl`` the tunnel checks print a named SKIP (not passed).

    python tests/_test_v3_fetch_py_base.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_fpb", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_py_base", ROOT / "research" / "v3" / "fetch_py_base.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_py_base_"))
VER = "3.14.4"


def nupkg(extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in {"python.nuspec": b"<package/>", "tools/python.exe": b"MZ-fake-python",
                           "tools/Lib/os.py": b"# os\n", "tools/Lib/venv/__init__.py": b"# venv\n",
                           "build/native/python.props": b"<Project/>", **(extra or {})}.items():
            z.writestr(name, data)
    return buf.getvalue()


PKG = nupkg()
REG = f"/v3/registration5-semver1/python/{VER}.json"
CAT = f"/v3/catalog0/data/2026.09.01/python.{VER}.json"
PKGP = f"/v3-flatcontainer/python/{VER}/python.{VER}.nupkg"


def routes(*, pkg=PKG, hash_of=PKG, cat_id="python", cat_ver=VER, pkg_url=f"https://api.nuget.org{PKGP}",
           pkg_status=200, pkg_headers=()):
    return {REG: (200, [("Content-Type", "application/json")], json.dumps(
                {"catalogEntry": f"https://api.nuget.org{CAT}", "packageContent": pkg_url}).encode()),
            CAT: (200, [("Content-Type", "application/json")], json.dumps(
                {"id": cat_id, "version": cat_ver, "packageHashAlgorithm": "SHA512",
                 "packageHash": base64.b64encode(hashlib.sha512(hash_of).digest()).decode()}).encode()),
            PKGP: (pkg_status, list(pkg_headers), pkg)}


made = TF.make_test_cert(TMP / "cert", "api.nuget.org")
TRUST = ssl.create_default_context(cafile=str(made[0])) if made else None


def served(**kw):
    srv = TF.TlsHttpServer(made[0], made[1], routes(**kw))
    hop = TF.TunnelHop(srv.port)
    return srv, hop


print("\n- the client, through the hop, TLS verified -")
if made is None:
    print("  SKIP the tunnel checks: neither cryptography nor openssl is available (not passed)")
else:
    print(f"       (throwaway certificate made with {made[2]})")
    srv, hop = served()
    cl = F.NugetClient(hop.port, ssl_context=TRUST)
    pkg, info = F.fetch_package(cl, VER)
    cl.close()
    # The method, host and port - not the HTTP version: http.client sends CONNECT as HTTP/1.0 before 3.11 (CI 76cb0e9, B2).
    check("the tunnel is CONNECT api.nuget.org:443", len(hop.connects) >= 1
          and hop.connects[0] in (b"CONNECT api.nuget.org:443 HTTP/1.1", b"CONNECT api.nuget.org:443 HTTP/1.0"),
          str(hop.connects))
    check("registration leaf -> catalog entry -> package, in that order, on one connection",
          cl.requests == [REG, CAT, PKGP] and srv.handshakes == 1, f"{cl.requests} handshakes={srv.handshakes}")
    check("the package matches the catalog's SHA512 and its sha256 is recorded",
          pkg == PKG and info["sha512_verified"] and info["nupkg_sha256"] == hashlib.sha256(PKG).hexdigest())
    check("the peer certificate's issuer is recorded", cl.peer and cl.peer["issuer_cn"] == "api.nuget.org"
          and cl.peer["tls_version"], str(cl.peer))
    check("identity encoding is asked for", all(b"Accept-Encoding: identity" in h for h in srv.heads))
    srv.close(), hop.close()
    srv, hop = served()
    try:
        F.fetch_package(F.NugetClient(hop.port), VER)          # the default context: the test cert is untrusted
        check("an untrusted certificate is an ssl error, not a fetch", False)
    except ssl.SSLError:
        check("an untrusted certificate is an ssl error, not a fetch", srv.handshakes == 0 and not srv.heads)
    srv.close(), hop.close()
    for label, kw, want, not_requested in (
            ("a package whose SHA512 does not match", {"hash_of": b"other bytes"}, "SHA512", None),
            ("a catalog entry for another id", {"cat_id": "pythonx86"}, "is not python", PKGP),
            ("a catalog entry for another version", {"cat_ver": "3.14.3"}, "is not python", PKGP),
            ("a package URL on another host", {"pkg_url": "https://evil.example/python.nupkg"}, "only https://api.nuget.org", "/python.nupkg"),
            ("a package URL over plain http", {"pkg_url": f"http://api.nuget.org{PKGP}"}, "only https://api.nuget.org", PKGP),
            ("a redirect", {"pkg_status": 302, "pkg_headers": [("Location", f"https://api.nuget.org{PKGP}")]}, "no redirect", None),
            ("an encoded body", {"pkg_headers": [("Content-Encoding", "gzip")]}, "encoded", None)):
        srv, hop = served(**kw)
        cl = F.NugetClient(hop.port, ssl_context=TRUST)
        try:
            F.fetch_package(cl, VER)
            check(f"{label} is refused", False)
        except F.FetchRefused as e:
            check(f"{label} is refused, by name", want in str(e) and (not_requested is None or not_requested not in cl.requests),
                  f"{e} / {cl.requests}")
        cl.close()
        srv.close(), hop.close()

print("\n- unpacking tools/ -")
dest = TMP / "py314"
files = F.unpack_tools(PKG, dest)
check("only tools/ is unpacked, sorted, with a sha256 per file",
      files == sorted([("Lib/os.py", hashlib.sha256(b"# os\n").hexdigest()),
                       ("Lib/venv/__init__.py", hashlib.sha256(b"# venv\n").hexdigest()),
                       ("python.exe", hashlib.sha256(b"MZ-fake-python").hexdigest())])
      and (dest / "python.exe").read_bytes() == b"MZ-fake-python" and not (dest / "python.nuspec").exists(), str(files))
check("no .partial directory is left behind", not (TMP / "py314.partial").exists())
d1 = F.tree_digest(files)
check("the tree digest is stable and changes with any file",
      d1 == F.tree_digest(list(reversed(files))) and d1 != F.tree_digest([(r, "0" * 64 if r == "python.exe" else h) for r, h in files]))
try:
    F.unpack_tools(PKG, dest)
    check("an existing destination is refused", False)
except F.FetchRefused:
    check("an existing destination is refused", True)
except Exception as e:  # noqa: BLE001 - any other failure is a failed check, by name
    check("an existing destination is refused", False, type(e).__name__)
Cw = L.Contract(polygon_root=TMP / "pw", runs_root=TMP / "pw" / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "o",
                secrets_dir=TMP / "s", quarantine_root=TMP / "q", conservation_root=TMP / "cv",
                polygon_idle=("core_bare", "py314"))
labels = {s.label for s in F.watch_specs(Cw, L)}
check("the window's file-system witness watches every idle polygon entry but py314, its install target",
      "polygon_core_bare" in labels and "polygon_py314" not in labels and "repo" in labels, str(sorted(labels)))
for label, name in (("a .. entry", "tools/../escaped.txt"), ("a drive entry", "tools/C:/escaped.txt"),
                    ("a backslash .. entry", "tools\\..\\escaped.txt")):
    d2 = TMP / f"py314-{abs(hash(name)) % 10000}"
    try:
        F.unpack_tools(nupkg({name: b"x"}), d2)
        check(f"{label} refuses the whole package", False)
    except F.FetchRefused:
        check(f"{label} refuses the whole package, nothing written", not d2.exists() and not (TMP / "escaped.txt").exists()
              and not d2.with_name(d2.name + ".partial").exists())

print("\n- the checks: isolated, no bytecode, a throwaway venv -")


class FakeRunner:
    def __init__(self, *, home_ok=True, write_pyc=False, venv_rc=0):
        self.calls = []
        self.home_ok, self.write_pyc, self.venv_rc = home_ok, write_pyc, venv_rc

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        tools = Path(argv[0]).parent
        if self.write_pyc:
            (tools / "__pycache__").mkdir(exist_ok=True)
            (tools / "__pycache__" / "x.pyc").write_bytes(b"pyc")
        out = ""
        if argv[3:4] == ["-c"] and "sys.version" in argv[4]:
            out = "3.14.4 (tags/v3.14.4) [MSC v.1944 64 bit (AMD64)]"
        elif argv[3:4] == ["-c"]:
            out = "25.2"
        elif argv[3:5] == ["-m", "venv"]:
            v = Path(argv[-1])
            (v / "Scripts").mkdir(parents=True)
            (v / "Scripts" / "python.exe").write_bytes(b"MZ")
            (v / "pyvenv.cfg").write_text(f"home = {tools if self.home_ok else TMP}\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.venv_rc, "", "")
        return subprocess.CompletedProcess(argv, 0, out, "")


work = TMP / "work"
work.mkdir()
fr = FakeRunner()
res = F.run_checks(dest, work, runner=fr)
check("every check runs the package's python isolated and without bytecode",
      all(a[:3] == [str(dest / "python.exe"), "-I", "-B"] for a, _ in fr.calls) and len(fr.calls) == 3, str([a[:4] for a, _ in fr.calls]))
envs = [kw["env"] for _, kw in fr.calls]
check("... with PYTHONDONTWRITEBYTECODE=1, no PYTHONPATH or PYTHONHOME, TEMP in the work directory",
      all(e.get("PYTHONDONTWRITEBYTECODE") == "1" and "PYTHONPATH" not in e and "PYTHONHOME" not in e
          and e.get("TEMP") == str(work) for e in envs))
check("version, the ensurepip/venv import and the venv are reported, the venv removed, the base unchanged",
      res["version"].startswith("3.14.4") and res["ensurepip_venv_import"] and res["venv_ok"]
      and res["venvcheck_removed"] and res["tools_unchanged_by_checks"], str(res))
res = F.run_checks(dest, work, runner=FakeRunner(write_pyc=True))
check("a check that writes into the base is reported (tools_unchanged_by_checks false)", res["tools_unchanged_by_checks"] is False)
shutil.rmtree(dest / "__pycache__", ignore_errors=True)
res = F.run_checks(dest, work, runner=FakeRunner(home_ok=False))
check("a venv whose home is not the base is not ok", res["venv_ok"] is False)
res = F.run_checks(dest, work, runner=FakeRunner(venv_rc=1))
check("a venv that fails is not ok", res["venv_ok"] is False)

print("\n- a whole window, fake witnesses -")


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


class ListenSampler(AnySampler):
    def __init__(self, port, pid):
        self.table = {("tcp", port): {pid}}

    def listeners(self):
        return self.table


def contract(tag):
    base = TMP / tag
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                      conservation_root=base / "conservation", polygon_idle=("py314",)), base


if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
else:
    for tag, kw, refused in (("ok", {}, False), ("bad-hash", {"hash_of": b"other"}, True)):
        C, base = contract(tag)
        (base / "watched").mkdir(parents=True)
        (base / "watched" / "idle.txt").write_bytes(b"idle")
        srv, hop = served(**kw)
        nat = L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None)
        try:
            rec = F.run_window(C, L, version=VER, dest=C.polygon_root / "py314", work=C.runs_root / "_tools" / F.WINDOW,
                               via_port=hop.port, client_factory=lambda p: F.NugetClient(p, ssl_context=TRUST),
                               native=nat, fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                               runner=FakeRunner(), hop_sampler=ListenSampler(hop.port, 4242), settle_s=0)
            err = None
        except F.FetchRefused as e:
            rec, err = json.loads((C.runs_root / "_tools" / F.WINDOW / f"{F.WINDOW}.json").read_bytes()), e
        wl = [json.loads(x) for x in (C.runs_root / "_launch" / "windows.jsonl").read_bytes().decode().splitlines()]
        chk = json.loads((C.runs_root / "_witness" / f"{F.WINDOW}.json").read_bytes())
        nrec = chk["native"]
        check(f"[{tag}] START names the hop and api.nuget.org, END follows",
              [w["event"] for w in wl] == ["START", "END"] and wl[0]["via"] == f"127.0.0.1:{hop.port}"
              and wl[0]["hosts"] == ["api.nuget.org"] and wl[0]["window"] == F.WINDOW, str(wl))
        check(f"[{tag}] the hop's pid is allowed inside the window and revoked at its end",
              nrec["allowed_listeners"] == [{"reason": "declared hop for window py-base-314", "pid": 4242}]
              and nrec["revoked_listeners"] == [{"reason": "declared hop for window py-base-314", "pid": 4242}],
              str((nrec["allowed_listeners"], nrec["revoked_listeners"])))
        check(f"[{tag}] the record says py314 is left out of the watched set, and names the hop",
              rec["fs_watched_excludes"] == ["polygon_py314"] and rec["via"] == {"host": "127.0.0.1", "port": hop.port})
        if not refused:
            check("[ok] the record carries the hashes, the issuer, the file count and the checks",
                  rec["sha512_verified"] and rec["nupkg_sha256"] == hashlib.sha256(PKG).hexdigest()
                  and rec["python_exe_sha256"] == hashlib.sha256(b"MZ-fake-python").hexdigest()
                  and rec["tools_tree_sha256"] == F.tree_digest(F.unpack_tools(PKG, base / "again"))
                  and rec["files"] == 3 and rec["peer"]["issuer_cn"] == "api.nuget.org" and rec["checks"]["venv_ok"],
                  str({k: rec.get(k) for k in ("sha512_verified", "files", "peer")}))
            check("[ok] the check is complete with 0 hits and 0 fs hits",
                  rec["check"] == {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0}, str(rec["check"]))
            check("[ok] the package is kept in the work directory, the base in its place",
                  (C.runs_root / "_tools" / F.WINDOW / f"python.{VER}.nupkg").read_bytes() == PKG
                  and (C.polygon_root / "py314" / "python.exe").is_file())
        else:
            check("[bad-hash] the refusal propagates by name, nothing is unpacked, the record and the check are written",
                  err is not None and "SHA512" in str(err) and not (C.polygon_root / "py314").exists()
                  and rec["check"]["complete"] is not None, str(err))
        srv.close(), hop.close()
    C, base = contract("taken")
    (C.runs_root / "_tools" / F.WINDOW).mkdir(parents=True)
    try:
        F.run_window(C, L, version=VER, dest=C.polygon_root / "py314", work=C.runs_root / "_tools" / F.WINDOW,
                     via_port=1, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None))
        check("a used work directory is refused before any window opens", False)
    except F.FetchRefused:
        check("a used work directory is refused before any window opens",
              not (C.runs_root / "_launch" / "windows.jsonl").exists())
    C, base = contract("nohop")
    (base / "watched").mkdir(parents=True)
    try:
        F.run_window(C, L, version=VER, dest=C.polygon_root / "py314", work=C.runs_root / "_tools" / F.WINDOW,
                     via_port=1, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                     fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), hop_sampler=AnySampler(), settle_s=0)
        check("no identifiable hop listener refuses the fetch", False)
    except F.FetchRefused as e:
        check("no identifiable hop listener refuses the fetch, by name", "hop's listener" in str(e), str(e))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch py-base: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
