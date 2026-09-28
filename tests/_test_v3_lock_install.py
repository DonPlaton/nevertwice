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
lock = LI.lock_from_report(copy.deepcopy(REPORT))
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
check("the sdist refusal names the distribution and says attempt 1 takes wheels only",
      "qdrant_client-1.15.1.tar.gz" in refusal(lambda: LI.lock_from_report(cases[9][1]))
      and "wheels only" in refusal(lambda: LI.lock_from_report(cases[9][1])))

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
code = LI.version_probe(["json"], ["pip"])
check("the import probe imports the declared modules and prints each requested distribution's version as JSON",
      code.startswith("import json; ") and "version(d) for d in ['pip']" in code, code)
check("... and takes plain names only", "not a plain name" in refusal(lambda: LI.version_probe(["os; import x"], ["a"])))
check("the declared venvs: mem0_v3 = mem0ai 2.2.0 (PREREG §2.2), its import and distribution",
      LI.VENVS.get("mem0_v3") == {"specs": ["mem0ai==2.2.0"], "imports": ["mem0"], "dists": ["mem0ai"]}, str(LI.VENVS))

print("\n- refusals before any spawn -")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "q", conservation_root=TMP / "cv")
Fake = SimpleNamespace(disk_floor_ok=lambda v, n: (True, 1 << 40), run_child_window=None)
spawned: list = []
Lspy = SimpleNamespace(**{k: getattr(L, k) for k in dir(L) if not k.startswith("__")})
Lspy.spawn = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError("a spawn"))
kw = dict(python=TMP / "py312" / "python.exe", via_port=1, parent_env={}, need_bytes=0, volume=TMP)
r1 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "nope", venv_name="nope_v3", run="l1", **kw))
(TMP / "polygon" / "mem0_v3").mkdir(parents=True)
r2 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "mem0_v3", venv_name="mem0_v3", run="l1", **kw))
(C.runs_root / "_install" / "a8-pypi-mem0_v3" / "l1").mkdir(parents=True, exist_ok=True)
r3 = refusal(lambda: LI.run_lock_install(C, Lspy, Fake, venv=TMP / "polygon" / "fresh_v3", venv_name="mem0_v3", run="l1", **kw))
check("an undeclared venv, an existing venv and a used run label are refused by name, before any spawn",
      "declares no specs" in r1 and "already exists" in r2 and "used before" in r3 and spawned == [], f"{r1} | {r2} | {r3}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 lock install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
