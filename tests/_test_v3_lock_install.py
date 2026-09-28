#!/usr/bin/env python3
"""PREREG-V3 A8 C2 (Q-A8-1 O-a): research/v3/lock_install.py's own rules, in process (no child, no network):

* lock_from_report: pip's report (format "1") becomes one lock entry per distribution - name normalised, version,
  wheel file, the index's sha256 (from ``hashes`` or ``hash``), requested, the licence as METADATA gives it; a direct
  URL, a VCS or local source, a yanked file, a file off https://files.pythonhosted.org, an unsafe file name, a missing
  or malformed sha256, a repeated name, and an sdist (attempt 2's, named) are each refused by name;
* the lock's lines and its sha256; the window's jobs - pip's dry run (wheels only, a report, no config file, the cache
  in the polygon, the index's simple URL) and the fetch child's download (each wheel checked against its sha256 as it
  streams, saved under wheels/);
* verify_wheels re-reads every wheel from the disk against the lock, and a missing, changed or extra file is named;
* the offline install: --require-hashes --no-deps --no-index --find-links <the wheels> -r <the lock>, no index URL;
* the import probe takes plain names only; the declared venvs (mem0_v3 = mem0ai 2.2.0, PREREG §2.2);
* run_lock_install refuses before any spawn: an undeclared venv, an existing venv, a used run label.

    python tests/_test_v3_lock_install.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


LI = _load("v3_lock_install_t", ROOT / "research" / "v3" / "lock_install.py")
L = _load("v3_launch_lock_t", ROOT / "research" / "v3" / "launch.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refusal(fn) -> str:
    try:
        fn()
        return "accepted"
    except LI.LockRefused as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - another failure is not the named refusal
        return f"not refused by name: {type(e).__name__}: {e}"


H1, H2 = "a" * 64, "b" * 64
F = "https://files.pythonhosted.org/packages/aa/bb"
REPORT = {"version": "1", "pip_version": "25.2", "install": [
    {"download_info": {"url": f"{F}/mem0ai-2.2.0-py3-none-any.whl", "archive_info": {"hash": f"sha256={H1}",
                                                                                   "hashes": {"sha256": H1}}},
     "is_direct": False, "is_yanked": False, "requested": True,
     "metadata": {"name": "mem0ai", "version": "2.2.0", "license_expression": "Apache-2.0"}},
    {"download_info": {"url": f"{F}/Qdrant_Client-1.15.1-py3-none-any.whl", "archive_info": {"hash": f"sha256={H2}"}},
     "is_direct": False, "is_yanked": False, "requested": False,
     "metadata": {"name": "Qdrant_Client", "version": "1.15.1",
                  "classifier": ["Programming Language :: Python", "License :: OSI Approved :: Apache Software License"]}}]}

print("- the lock from pip's report -")
try:
    lock = LI.lock_from_report(copy.deepcopy(REPORT))
except Exception as e:  # noqa: BLE001 - LM27: a refusal here FAILs the row below by name, never crashes the suite
    lock = [{"name": f"refused: {type(e).__name__}: {e}", "version": "", "filename": "", "sha256": "", "requested": None,
             "licence": None, "url": ""}, {"name": "", "version": "", "filename": "", "sha256": "", "requested": None,
                                           "licence": None, "url": ""}]
check("one entry per distribution, sorted, the name normalised, the index's sha256 (hashes or hash), the wheel, "
      "requested, the licence as METADATA gives it",
      [(e["name"], e["version"], e["filename"], e["sha256"], e["requested"], e["licence"]) for e in lock]
      == [("mem0ai", "2.2.0", "mem0ai-2.2.0-py3-none-any.whl", H1, True, "Apache-2.0"),
          ("qdrant-client", "1.15.1", "Qdrant_Client-1.15.1-py3-none-any.whl", H2, False,
           "License :: OSI Approved :: Apache Software License")], str(lock))


def bad(path, value):
    r = copy.deepcopy(REPORT)
    tgt = r
    for k in path[:-1]:
        tgt = tgt[k]
    if value is KeyError:
        tgt.pop(path[-1], None)
    else:
        tgt[path[-1]] = value
    return r


cases = [
    ("a report of another format", bad(("version",), "0"), "format 1"),
    ("a report that installs nothing", bad(("install",), []), "installs nothing"),
    ("a direct URL requirement", bad(("install", 0, "is_direct"), True), "direct URL"),
    ("a yanked file", bad(("install", 0, "is_yanked"), True), "yanked"),
    ("a VCS or local source (no archive_info)", bad(("install", 0, "download_info", "archive_info"), KeyError), "not an archive"),
    ("a file over plain http", bad(("install", 0, "download_info", "url"), f"http://files.pythonhosted.org/x/mem0ai-2.2.0-py3-none-any.whl"), "is not https://files.pythonhosted.org"),
    ("a file on another host", bad(("install", 0, "download_info", "url"), "https://evil.example/mem0ai-2.2.0-py3-none-any.whl"), "is not https://files.pythonhosted.org"),
    ("a file with a query", bad(("install", 0, "download_info", "url"), f"{F}/mem0ai-2.2.0-py3-none-any.whl?x=1"), "is not https://files.pythonhosted.org"),
    ("a file on another port (LM6)", bad(("install", 0, "download_info", "url"), "https://files.pythonhosted.org:8443/packages/mem0ai-2.2.0-py3-none-any.whl"), "is not https://files.pythonhosted.org"),
    ("a file URL with userinfo (LM7)", bad(("install", 0, "download_info", "url"), "https://user@files.pythonhosted.org/packages/mem0ai-2.2.0-py3-none-any.whl"), "is not https://files.pythonhosted.org"),
    ("a sha256 with trailing garbage (LM11)", bad(("install", 0, "download_info", "archive_info"), {"hashes": {"sha256": "a" * 64 + "zz"}}), "no sha256"),
    ("an unsafe file name", bad(("install", 0, "download_info", "url"), f"{F}/..%2f..%2fevil.whl"), "unsafe file name"),
    ("an sdist - attempt 2's, named", bad(("install", 1, "download_info", "url"), f"{F}/qdrant_client-1.15.1.tar.gz"), "attempt 2"),
    ("no sha256 from the index", bad(("install", 1, "download_info", "archive_info"), {"hash": "md5=00"}), "no sha256"),
    ("a malformed sha256", bad(("install", 0, "download_info", "archive_info"), {"hashes": {"sha256": "ABC"}}), "no sha256"),
    ("a name without a version", bad(("install", 0, "metadata"), {"name": "mem0ai"}), "no name or version"),
]
dup = copy.deepcopy(REPORT)
dup["install"][1]["metadata"]["name"] = "Mem0_AI"
dup["install"][1]["metadata"]["version"] = "2.2.0"
dup["install"][0]["metadata"]["name"] = "mem0-ai"
cases.append(("a name the report gives twice (normalised)", dup, "twice"))
for label, rep, want in cases:
    got = refusal(lambda rep=rep: LI.lock_from_report(rep))
    check(f"refused by name: {label}", want in got, got)
SDIST = next(rep for label, rep, _w in cases if label.startswith("an sdist"))
check("the sdist refusal names the distribution and says attempt 1 takes wheels only",
      "qdrant_client-1.15.1.tar.gz" in refusal(lambda: LI.lock_from_report(SDIST))
      and "wheels only" in refusal(lambda: LI.lock_from_report(SDIST)))

rev = copy.deepcopy(REPORT)
rev["install"].reverse()
try:
    lock_rev = LI.lock_from_report(rev)
except Exception as e:  # noqa: BLE001
    lock_rev = [f"{type(e).__name__}: {e}"]
check("LM14: the lock is sorted by name whatever the report's order, and its sha256 does not depend on that order",
      [e["name"] for e in lock_rev] == ["mem0ai", "qdrant-client"] and LI.lock_sha256(lock_rev) == LI.lock_sha256(lock),
      str([e.get("name") if isinstance(e, dict) else e for e in lock_rev]))

print("\n- the lock's lines and the window's jobs -")
lines = LI.lock_lines(lock)
check("each lock line pins the version and the index's sha256",
      lines == [f"mem0ai==2.2.0 --hash=sha256:{H1}", f"qdrant-client==1.15.1 --hash=sha256:{H2}"], str(lines))
check("the lock's sha256 is over its lines, stable, and moves with any hash",
      LI.lock_sha256(lock) == hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()
      and LI.lock_sha256([{**lock[0], "sha256": "c" * 64}, lock[1]]) != LI.lock_sha256(lock))
TMP = Path(tempfile.mkdtemp(prefix="nvt3_lock_install_"))
rj = LI.resolve_job(TMP / "venv" / "Scripts" / "python.exe", ["--use-deprecated=legacy-certs"], ["mem0ai==2.2.0"],
                    index_host="pypi.org", pip_cache=TMP / "polygon" / "pip_cache")
check("pip's dry run: isolated, no bytecode, wheels only, --ignore-installed, the report in its unit, the specs last",
      rj["child"] == "pip" and rj["argv"][:6] == ["-I", "-B", "-m", "pip", "install", "--use-deprecated=legacy-certs"]
      and {"--dry-run", "--ignore-installed", "--only-binary=:all:", "--no-input", "--disable-pip-version-check"}
      <= set(rj["argv"]) and rj["argv"][rj["argv"].index("--report") + 1] == "report.json"
      and rj["argv"][-1] == "mem0ai==2.2.0", str(rj["argv"]))
check("... with no pip config file, the cache in the polygon, the index's simple URL",
      rj["env"] == {"PIP_CACHE_DIR": str(TMP / "polygon" / "pip_cache"), "PIP_CONFIG_FILE": os.devnull,
                    "PIP_INDEX_URL": "https://pypi.org/simple", "PIP_NO_INPUT": "1"}, str(rj["env"]))
dj = LI.download_job(lock)
check("the download: files.pythonhosted.org only, no redirect, each wheel saved under wheels/ and checked against its "
      "sha256 as it streams", dj["hosts"] == ["files.pythonhosted.org"] and dj["max_redirects"] == 0
      and [(r["url"], r["save"], r["expect"], r["method"]) for r in dj["requests"]]
      == [(e["url"], f"wheels/{e['filename']}", {"sha256": e["sha256"]}, "GET") for e in lock]
      and all(r["max_bytes"] == LI.WHEEL_MAX for r in dj["requests"]), str(dj["requests"][:1]))

print("\n- the wheels, from the disk -")
wd = TMP / "wheels"
wd.mkdir()
w1, w2 = b"wheel one", b"wheel two"
lk = [{**lock[0], "sha256": hashlib.sha256(w1).hexdigest()}, {**lock[1], "sha256": hashlib.sha256(w2).hexdigest()}]
(wd / lk[0]["filename"]).write_bytes(w1)
(wd / lk[1]["filename"]).write_bytes(w2)
check("every locked wheel on the disk with the index's sha256: no problem", LI.verify_wheels(wd, lk) == [],
      str(LI.verify_wheels(wd, lk)))
(wd / lk[1]["filename"]).write_bytes(b"changed on the disk")
p1 = LI.verify_wheels(wd, lk)
(wd / lk[1]["filename"]).unlink()
p2 = LI.verify_wheels(wd, lk)
(wd / lk[1]["filename"]).write_bytes(w2)
(wd / "stray-0.1-py3-none-any.whl").write_bytes(b"x")
p3 = LI.verify_wheels(wd, lk)
check("a wheel changed on the disk, a missing one and a file the lock does not name are each a problem by name",
      any("not the sha256" in x for x in p1) and any("is not on the disk" in x for x in p2)
      and any("does not name" in x and "stray" in x for x in p3), f"{p1} {p2} {p3}")

print("\n- the offline install and the import probe -")
ia = LI.install_argv(TMP / "v" / "python.exe", ["--use-deprecated=legacy-certs"], TMP / "lock.txt", wd)
check("the offline install: the lock's hashes, no dependency of pip's choosing, no index - only the checked wheels",
      {"--require-hashes", "--no-deps", "--no-index", "--only-binary=:all:"} <= set(ia)
      and ia[ia.index("--find-links") + 1] == str(wd) and ia[-2:] == ["-r", str(TMP / "lock.txt")]
      and not any("index-url" in a or a.startswith("http") for a in ia), str(ia))
check("LM26: the offline install runs pip isolated - argv[1] is -I", ia[1] == "-I", str(ia[:3]))
oe = LI.offline_env(SimpleNamespace(polygon_root=TMP / "polygon")) if hasattr(LI, "offline_env") else {}
check("LI-2 (the auditor): the offline pip gets the resolver's declared env - no config file (os.devnull skips the global, "
      "user and site files; --isolated would not), the cache in the polygon, no input - and no index at all",
      oe == {"PIP_CACHE_DIR": str(TMP / "polygon" / "pip_cache"), "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1"}
      and {k: v for k, v in rj["env"].items() if k != "PIP_INDEX_URL"} == oe, str(oe))
code = LI.version_probe(["json"], ["pip"])
import contextlib as _cl  # noqa: E402
import io as _io  # noqa: E402
import platform as _pf  # noqa: E402
_buf = _io.StringIO()
try:
    with _cl.redirect_stdout(_buf):
        exec(compile(LI.version_probe(["json"], ["pip"]), "<probe>", "exec"), {})
    probed = json.loads(_buf.getvalue().strip().splitlines()[-1])
except Exception as e:  # noqa: BLE001
    probed = {"error": f"{type(e).__name__}: {e}"}
check("the import probe imports the declared modules and prints the venv's own python version and each requested "
      "distribution's version as JSON", probed.get("python") == _pf.python_version()
      and isinstance((probed.get("dists") or {}).get("pip"), str), str(probed))
cv = getattr(LI, "check_versions", None)
lk2 = [{"name": "mem0ai", "version": "2.2.0"}, {"name": "qdrant-client", "version": "1.15.1"}]
ok_v = cv({"python": "3.12.10", "dists": {"mem0ai": "2.2.0"}}, lk2, ["mem0ai"], "3.12.10") if cv else ["no check_versions"]
bad_py = cv({"python": "3.14.4", "dists": {"mem0ai": "2.2.0"}}, lk2, ["mem0ai"], "3.12.10") if cv else []
bad_d = cv({"python": "3.12.10", "dists": {"mem0ai": "2.1.0"}}, lk2, ["mem0ai"], "3.12.10") if cv else []
bad_shape = cv(None, lk2, ["mem0ai"], "3.12.10") if cv else []
check("LI-1 (the auditor): the venv's own python must be the declared base's version, and each requested distribution "
      "the locked version - each mismatch named; an unreadable probe is a problem, never a pass",
      ok_v == [] and any("3.14.4" in p and "3.12.10" in p for p in bad_py) and any("mem0ai" in p and "2.1.0" in p for p in bad_d)
      and bad_shape != [], f"{ok_v} | {bad_py} | {bad_d} | {bad_shape}")
def _cv(got):
    try:
        return cv(got, lk2, ["mem0ai"], "3.12.10") if cv else []
    except Exception as e:  # noqa: BLE001 - a crash FAILs the row by name
        return [f"crash: {type(e).__name__}: {e}"]


no_dists, list_dists = _cv({"python": "3.12.10"}), _cv({"python": "3.12.10", "dists": ["x"]})
check("LN6 (the auditor): an answer without dists, or with dists that are not a mapping, is one named problem each - "
      "never a crash, never a pass", len(no_dists) == 1 and len(list_dists) == 1
      and all("not {python, dists}" in x[0] for x in (no_dists, list_dists)), f"{no_dists} | {list_dists}")
check("... and takes plain names only", "not a plain name" in refusal(lambda: LI.version_probe(["os; import x"], ["a"])))
check("the declared venvs: mem0_v3 = mem0ai 2.2.0 (PREREG §2.2) on the declared base py-base-312 (LI-1), its import and "
      "distribution", LI.VENVS.get("mem0_v3") == {"base": "py-base-312", "specs": ["mem0ai==2.2.0"], "imports": ["mem0"],
                                                  "dists": ["mem0ai"]}, str(LI.VENVS))

MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
mw = {k[len(LI.WINDOW_PREFIX):]: v for k, v in MANW.items() if k.startswith(LI.WINDOW_PREFIX)}
check("C3a: every declared venv has its a8-pypi window in the manifest - exactly the two index hosts, its base, its specs, "
      "no redirect - and the manifest declares no a8-pypi window the code does not",
      set(mw) == set(LI.VENVS) and all(sorted(mw[v]["hosts"]) == sorted(LI.HOSTS) and mw[v]["base"] == LI.VENVS[v]["base"]
                                       and mw[v]["specs"] == LI.VENVS[v]["specs"] and mw[v]["max_redirects"] == 0
                                       and mw[v]["venv"] == v for v in mw), str(mw)[:300])

print("\n- refusals before any spawn -")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "q", conservation_root=TMP / "cv")
Fake = SimpleNamespace(disk_floor_ok=lambda v, n: (True, 1 << 40), run_child_window=None)
spawned: list = []
Lspy = SimpleNamespace(**{k: getattr(L, k) for k in dir(L) if not k.startswith("__")})
Lspy.spawn = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError("a spawn"))
kw = dict(python=TMP / "py312" / "python.exe", via_port=1, parent_env={}, need_bytes=0, volume=TMP)
(TMP / "polygon" / "py312").mkdir(parents=True, exist_ok=True)
(TMP / "polygon" / "py312" / "python.exe").write_bytes(b"MZ")
(TMP / "elsewhere").mkdir(exist_ok=True)
(TMP / "elsewhere" / "python.exe").write_bytes(b"MZ")
r0 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "fresh0_v3", venv_name="mem0_v3", run="l0",
                                         **{**kw, "python": TMP / "elsewhere" / "python.exe"}))
(TMP / "polygon" / "fresh1_v3").mkdir(parents=True)
r0b = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "fresh1_v3", venv_name="mem0_v3", run="l0",
                                          **{**kw, "python": TMP / "polygon" / "py312" / "python.exe"}))
check("LI-1 (the auditor): an interpreter that is not the declared base's (polygon/py312/python.exe for py-base-312) is "
      "refused by name before any spawn; the base's own passes that check (and meets the next refusal)",
      "is not the declared base" in r0 and "py-base-312" in r0 and "already exists" in r0b and spawned == [], f"{r0} | {r0b}")
CJ = L.Contract(polygon_root=TMP / "pj", runs_root=TMP / "pj" / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner",
                secrets_dir=TMP / "secrets", quarantine_root=TMP / "q", conservation_root=TMP / "cv")
(TMP / "realbase").mkdir()
(TMP / "realbase" / "python.exe").write_bytes(b"MZ")
(TMP / "pj").mkdir()
if os.name == "nt":
    import _winapi  # noqa: E402
    _winapi.CreateJunction(str(TMP / "realbase"), str(TMP / "pj" / "py312"))
else:
    os.symlink(TMP / "realbase", TMP / "pj" / "py312", target_is_directory=True)
(TMP / "pj" / "taken_v3").mkdir()
rj_ = refusal(lambda: LI.run_lock_install(CJ, Lspy, Fake, venv=TMP / "pj" / "taken_v3", venv_name="mem0_v3", run="l0",
                                          **{**kw, "python": TMP / "realbase" / "python.exe"}))
os.rmdir(TMP / "pj" / "py312") if os.name == "nt" else (TMP / "pj" / "py312").unlink()
check("LI-1: the base is compared by realpath - the real path of a base the polygon reaches through a junction passes "
      "(and meets the next refusal)", "already exists" in rj_ and spawned == [], rj_)
kw["python"] = TMP / "polygon" / "py312" / "python.exe"
r1 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "nope", venv_name="nope_v3", run="l1", **kw))
(TMP / "polygon" / "mem0_v3").mkdir(parents=True)
r2 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "mem0_v3", venv_name="mem0_v3", run="l1", **kw))
(C.runs_root / "_install" / "a8-pypi-mem0_v3" / "l1").mkdir(parents=True, exist_ok=True)
r3 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "fresh_v3", venv_name="mem0_v3", run="l1", **kw))
check("an undeclared venv, an existing venv and a used run label are refused by name, before any spawn",
      "declares no specs" in r1 and "already exists" in r2 and "used before" in r3 and spawned == [], f"{r1} | {r2} | {r3}")

nb = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "nb_v3", venv_name="nb_v3", run="l7",
                                         specs=["x==1"], imports=["x"], dists=["x"], **kw))
b9 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "nb_v3", venv_name="nb_v3", run="l8",
                                         specs=["x==1"], imports=["x"], dists=["x"], base="py-base-999", **kw))
check("LN3 (the auditor): a venv that declares no base, or names an undeclared one, is refused by name (LI-1) before any "
      "directory is made", all("declares no base" in r and "LI-1" in r for r in (nb, b9))
      and not (C.runs_root / "_install" / "a8-pypi-nb_v3").exists() and spawned == [], f"{nb} | {b9}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 lock install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
