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


VER12 = "3.12.10"
FLAT = "/v3-flatcontainer/python/index.json"
PKGP12 = f"/v3-flatcontainer/python/{VER12}/python.{VER12}.nupkg"


def routes12(versions):
    """A 3.12 base: registration, catalog and package for VER12, and NuGet's flat index of every version."""
    reg, cat = f"/v3/registration5-semver1/python/{VER12}.json", f"/v3/catalog0/data/2026.09.28/python.{VER12}.json"
    return {reg: (200, [("Content-Type", "application/json")], json.dumps(
                {"catalogEntry": f"https://api.nuget.org{cat}", "packageContent": f"https://api.nuget.org{PKGP12}"}).encode()),
            cat: (200, [("Content-Type", "application/json")], json.dumps(
                {"id": "python", "version": VER12, "packageHashAlgorithm": "SHA512",
                 "packageHash": base64.b64encode(hashlib.sha512(PKG).digest()).decode()}).encode()),
            PKGP12: (200, [], PKG),
            FLAT: (200, [("Content-Type", "application/json")], json.dumps({"versions": versions}).encode())}


def served12(versions):
    srv = TF.TlsHttpServer(made[0], made[1], routes12(versions))
    return srv, TF.TunnelHop(srv.port)


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
    print("\n- C1 (Q-A8-2): the 3.12 base is the newest stable 3.12.x on NuGet, checked in the window -")
    srv, hop = served12(["3.12.9", "3.12.10", "3.13.1", "3.12.11-rc1", "3.11.9"])
    cl = F.NugetClient(hop.port, ssl_context=TRUST)
    try:
        _p, info12 = F.fetch_package(cl, VER12, newest_of="3.12")
        err12 = None
    except Exception as e:  # noqa: BLE001 - a refusal FAILs the row by name
        info12, err12 = {}, e
    cl.close()
    srv.close(), hop.close()
    nc = info12.get("newest_check") or {}
    check("C1: NuGet's flat index is read first; the declared 3.12.10 is the newest stable 3.12.x (a pre-release, 3.13 "
          "and 3.11 do not count) - fetched, and the check is recorded",
          err12 is None and cl.requests[0] == FLAT and PKGP12 in cl.requests and nc.get("of") == "3.12"
          and nc.get("newest") == "3.12.10" and nc.get("declared") == "3.12.10", f"{err12} {cl.requests} {nc}")
    srv, hop = served12(["3.12.9", "3.12.10", "3.12.11"])
    cl = F.NugetClient(hop.port, ssl_context=TRUST)
    try:
        F.fetch_package(cl, VER12, newest_of="3.12")
        check("C1: a newer 3.12.11 on NuGet refuses the declared 3.12.10 by name, before the package is asked for", False)
    except Exception as e:  # noqa: BLE001 - only the window's own refusal, by name, passes
        check("C1: a newer 3.12.11 on NuGet refuses the declared 3.12.10 by name, before the package is asked for",
              isinstance(e, F.FetchRefused) and "3.12.11" in str(e) and "declared" in str(e)
              and PKGP12 not in cl.requests, f"{type(e).__name__}: {e} {cl.requests}")
    cl.close()
    srv.close(), hop.close()
    check("C1: the declared bases - py-base-314 (done, unchanged: no flat index) and py-base-312, each with its version "
          "and its own target directory", F.BASES.get("py-base-314") == {"version": "3.14.4", "dest": "py314", "newest_of": None}
          and F.BASES.get("py-base-312") == {"version": "3.12.10", "dest": "py312", "newest_of": "3.12"}, str(F.BASES))

MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
mb = MANW.get("py-base-312") or {}
check("C3a: the manifest declares py-base-312 as the code does - api.nuget.org only, its version and its target, no "
      "redirect", mb.get("hosts") == [F.NUGET_HOST] and mb.get("version") == F.BASES["py-base-312"]["version"]
      and mb.get("dest") == F.BASES["py-base-312"]["dest"] and mb.get("max_redirects") == 0, str(mb))

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
Cw2 = L.Contract(polygon_root=TMP / "pw2", runs_root=TMP / "pw2" / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "o",
                 secrets_dir=TMP / "s", quarantine_root=TMP / "q", conservation_root=TMP / "cv",
                 polygon_idle=("core_bare", "py314", "py312"))
labels12 = {s.label for s in F.watch_specs(Cw2, L, window="py-base-312")}
check("R5 (the auditor): the py-base-312 window leaves out exactly its own target py312, by name - py314 stays watched",
      "polygon_py312" not in labels12 and "polygon_py314" in labels12 and "polygon_core_bare" in labels12,
      str(sorted(labels12)))
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
    def __init__(self, *, home_ok=True, write_pyc=False, venv_rc=0, version="3.14.4", home=None):
        self.calls = []
        self.home_ok, self.write_pyc, self.venv_rc, self.version, self.home = home_ok, write_pyc, venv_rc, version, home

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        tools = Path(argv[0]).parent
        if self.write_pyc:
            (tools / "__pycache__").mkdir(exist_ok=True)
            (tools / "__pycache__" / "x.pyc").write_bytes(b"pyc")
        out = ""
        if argv[3:4] == ["-c"] and "sys.version" in argv[4]:
            out = f"{self.version} (tags/v{self.version}) [MSC v.1944 64 bit (AMD64)]"
        elif argv[3:4] == ["-c"]:
            out = "25.2"
        elif argv[3:5] == ["-m", "venv"]:
            v = Path(argv[-1])
            (v / "Scripts").mkdir(parents=True)
            (v / "Scripts" / "python.exe").write_bytes(b"MZ")
            home = self.home if self.home is not None else (tools if self.home_ok else TMP)
            (v / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
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
alias = TMP / "alias_of_the_base"
if os.name == "nt":
    import _winapi  # noqa: E402
    _winapi.CreateJunction(str(dest), str(alias))
else:
    os.symlink(dest, alias, target_is_directory=True)
res = F.run_checks(dest, work, runner=FakeRunner(home=alias))
check("M25: a venv whose home names the base another way (a junction here; an 8.3 name on the owner's machine) is ok - "
      "realpath on both sides", res["venv_ok"] is True and str(alias) != str(dest), str(res))
os.rmdir(alias) if os.name == "nt" else alias.unlink()
alias2 = TMP / "the_base_by_another_name"          # A8C1-1: the TOOLS side reached through another spelling
if os.name == "nt":
    _winapi.CreateJunction(str(dest), str(alias2))
else:
    os.symlink(dest, alias2, target_is_directory=True)
res = F.run_checks(alias2, work, runner=FakeRunner(home=dest))
check("M25 (A8C1-1): a base reached through another spelling while the venv names the real path is ok too - realpath "
      "on the tools side as well", res["venv_ok"] is True, str(res))
os.rmdir(alias2) if os.name == "nt" else alias2.unlink()
res = F.run_checks(dest, work, runner=FakeRunner(version="3.12.10"), expected_version="3.12.10")
check("C1: the base's own version is checked against the declared one - 3.12.10 is 3.12.10", res["version_ok"] is True, str(res))
res = F.run_checks(dest, work, runner=FakeRunner(version="3.12.10"), expected_version="3.12.1")
check("C1: ... and 3.12.10 is not 3.12.1 (a prefix is no match)", res["version_ok"] is False, str(res.get("version")))

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
    C12, base12 = contract("w312")
    C12 = L.Contract(polygon_root=C12.polygon_root, runs_root=C12.runs_root, repo_root=ROOT, owner_home=base12 / "owner",
                     secrets_dir=base12 / "secrets", quarantine_root=base12 / "quarantine",
                     conservation_root=base12 / "conservation", polygon_idle=("py314", "py312"))
    (base12 / "watched").mkdir(parents=True)
    srv, hop = served12(["3.12.9", "3.12.10"])
    nat = L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None)
    try:
        r12 = F.run_window(C12, L, version=VER12, dest=C12.polygon_root / "py312", work=C12.runs_root / "_tools" / "py-base-312",
                           via_port=hop.port, window="py-base-312",
                           client_factory=lambda p: F.NugetClient(p, ssl_context=TRUST), native=nat,
                           fs=L.FsWitness([L.WatchSpec("watched", base12 / "watched")]),
                           runner=FakeRunner(version="3.12.10"), hop_sampler=ListenSampler(hop.port, 4343), settle_s=0)
        e12 = None
    except Exception as e:  # noqa: BLE001 - a crash FAILs the row by name
        r12, e12 = {}, e
    srv.close(), hop.close()
    wl12 = [json.loads(x) for x in (C12.runs_root / "_launch" / "windows.jsonl").read_bytes().decode().splitlines()] \
        if (C12.runs_root / "_launch" / "windows.jsonl").exists() else []
    chk12 = json.loads((C12.runs_root / "_witness" / "py-base-312.json").read_bytes()) \
        if (C12.runs_root / "_witness" / "py-base-312.json").exists() else {"native": {}}
    check("C1: a py-base-312 window - START names py-base-312, the hop allowed and revoked under that name, py312 (only) left "
          "out of the watched set, the newest check and the checks' version_ok in the record, written as py-base-312.json",
          e12 is None and [w["window"] for w in wl12] == ["py-base-312", "py-base-312"]
          and chk12["native"].get("allowed_listeners") == [{"reason": "declared hop for window py-base-312", "pid": 4343}]
          and r12.get("window") == "py-base-312" and r12.get("fs_watched_excludes") == ["polygon_py312"]
          and (r12.get("newest_check") or {}).get("newest") == "3.12.10" and r12["checks"]["version_ok"] is True
          and (C12.runs_root / "_tools" / "py-base-312" / "py-base-312.json").is_file()
          and (C12.polygon_root / "py312" / "python.exe").is_file(), f"{e12} {wl12} {r12.get('fs_watched_excludes')}")
    C9, base9 = contract("unknown")
    work9 = C9.runs_root / "_tools" / "py-base-999"
    try:
        F.run_window(C9, L, version="3.99.0", dest=C9.polygon_root / "py399", work=work9, via_port=1, window="py-base-999",
                     native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None))
        check("A8C1-3: an undeclared window is refused by name before anything is made", False)
    except Exception as e:  # noqa: BLE001 - only the window's own refusal, naming the window, passes
        check("A8C1-3: an undeclared window is refused by name before anything is made - no work directory, no "
              "window line", isinstance(e, F.FetchRefused) and "py-base-999" in str(e) and not work9.exists()
              and not (C9.runs_root / "_launch" / "windows.jsonl").exists(), f"{type(e).__name__}: {e}")
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

print("\n- A8C1-2: main()'s verdict - every clause of it -")
import contextlib  # noqa: E402
import io as _io  # noqa: E402
from types import SimpleNamespace  # noqa: E402

GOOD = {"sha512_verified": True, "checks": {"venv_ok": True, "tools_unchanged_by_checks": True, "version_ok": True},
        "check": {"complete": True, "native_hits": 0, "fs_hits": 0}}
FLIPS = {"sha512_verified": ("sha512_verified",), "venv_ok": ("checks", "venv_ok"),
         "tools_unchanged_by_checks": ("checks", "tools_unchanged_by_checks"), "version_ok": ("checks", "version_ok"),
         "complete": ("check", "complete"), "native_hits": ("check", "native_hits"), "fs_hits": ("check", "fs_hits")}
_real_load, _real_run = F._load_launch, F.run_window
seen_kw: list = []


def rc_for(rec):
    F._load_launch = lambda: SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(
        polygon_root=TMP / "mainpoly", runs_root=TMP / "mainpoly" / "runs")), network_via_port=lambda c: 1)
    F.run_window = lambda *a, **k: (seen_kw.append(k), rec)[1]
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            return F.main(["--window", "py-base-312"])
    finally:
        F._load_launch, F.run_window = _real_load, _real_run


rc_good = rc_for(json.loads(json.dumps(GOOD)))
flipped = {}
for clause, path in FLIPS.items():
    rec = json.loads(json.dumps(GOOD))
    tgt = rec
    for k in path[:-1]:
        tgt = tgt[k]
    tgt[path[-1]] = 1 if clause in ("native_hits", "fs_hits") else False
    flipped[clause] = rc_for(rec)
check("A8C1-2: main() exits 0 only when every clause holds - the package's SHA512, the venv, the unchanged base, the "
      "base's own version, a complete check with 0 egress and 0 file-system hits; each one false alone exits 1",
      rc_good == 0 and all(v == 1 for v in flipped.values()), f"good={rc_good} {flipped}")
check("A8C1-2: main() runs the declared base's window - its version, its target, its work directory",
      seen_kw and seen_kw[0].get("window") == "py-base-312" and seen_kw[0].get("version") == "3.12.10"
      and Path(seen_kw[0].get("dest")).name == "py312" and Path(seen_kw[0].get("work")).name == "py-base-312",
      str(seen_kw[:1]))
n_before = len(seen_kw)
try:
    with contextlib.redirect_stderr(_io.StringIO()):
        rc_for_unknown = F.main(["--window", "py-base-999"])
except SystemExit as e:
    rc_for_unknown = ("argparse", e.code)
except Exception as e:  # noqa: BLE001 - any other failure is not the command line's refusal
    rc_for_unknown = (type(e).__name__, str(e))
check("A8C1-2: main() takes only a declared window - another is the command line's refusal, and no window runs",
      rc_for_unknown == ("argparse", 2) and len(seen_kw) == n_before, str(rc_for_unknown))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch py-base: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
