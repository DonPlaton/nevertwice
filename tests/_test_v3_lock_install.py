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
* B-NLP (the auditor, 07:19): mem0_v3 is mem0ai[nlp]==2.2.0 with spacy imported and version-checked; every VENVS spec is
  its PREREG §2.2 row (quoted, and found word for word in the tracked PREREG); a spec is an exact pin; the window's
  lock is lock_step's - the spec requested at its version with its extras, every declared distribution in the lock
  (a missing one refused before any download); check_versions never passes None against None;
* the seam (B-C4B-CWD, the auditor): install_v3_data._step with the offline pip's declared env hands a stub spawn
  that runs launch's REAL check_cwd, assert_env and assert_argv - no reason.

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


print("\n- B-NLP: the spec's extras carried into the lock -")
check("a spec is an exact pin: its normalised name, its extras sorted and normalised, its version",
      LI.spec_parts("mem0ai[nlp]==2.2.0") == ("mem0ai", ["nlp"], "2.2.0")
      and LI.spec_parts("Qdrant_Client[B_x, a]==1.15.1") == ("qdrant-client", ["a", "b-x"], "1.15.1")
      and LI.spec_parts("spacy==3.8.7") == ("spacy", [], "3.8.7"), str(LI.spec_parts("mem0ai[nlp]==2.2.0")))
for sp in ("mem0ai>=2.2.0", "mem0ai", "mem0ai[nlp]", "mem0ai==2.2.0; python_version<'4'", "mem0ai[]==2.2.0"):
    check(f"a spec that is not an exact pin is refused by name: {sp!r}", "not an exact pin" in refusal(lambda: LI.spec_parts(sp)))
REP_NLP = copy.deepcopy(REPORT)
REP_NLP["install"][0]["requested_extras"] = ["NLP"]
try:
    lk = LI.lock_step(REP_NLP, ["mem0ai[nlp]==2.2.0"])
except Exception as e:  # noqa: BLE001 - a refusal FAILs the row by name
    lk = [{"name": f"refused: {e}"}]
check("the lock records each requested distribution's extras as pip took them (normalised)",
      [(e["name"], e.get("requested_extras")) for e in lk] == [("mem0ai", ["nlp"]), ("qdrant-client", [])], str(lk))
check("the lock's lines and sha256 stay name==version --hash (the extras are the report's, not the install's)",
      LI.lock_lines(lk)[0] == f"mem0ai==2.2.0 --hash=sha256:{H1}", str(LI.lock_lines(lk)[:1]))
check("B-NLP: a report that took mem0ai WITHOUT the extra the spec asks is refused by name - pip dropped [nlp]",
      "without its extra(s) ['nlp']" in refusal(lambda: LI.lock_step(copy.deepcopy(REPORT), ["mem0ai[nlp]==2.2.0"])))
nreq = copy.deepcopy(REP_NLP)
nreq["install"][0]["requested"] = False
check("a spec's distribution the report does not mark requested is refused by name",
      "does not install mem0ai[nlp]==2.2.0 as requested" in refusal(lambda: LI.lock_step(nreq, ["mem0ai[nlp]==2.2.0"])))
check("a spec whose version the report does not install is refused by name",
      "does not install mem0ai[nlp]==2.2.1 as requested" in refusal(lambda: LI.lock_step(REP_NLP, ["mem0ai[nlp]==2.2.1"])))
check("a spec with no extra passes on a report without requested_extras",
      refusal(lambda: LI.lock_step(copy.deepcopy(REPORT), ["mem0ai==2.2.0"])) == "accepted")
check("B-NLP: a declared distribution the lock lacks is refused by name before any download - pip lists an extra the "
      "package does not provide as requested, so only the missing distribution shows it",
      "lacks the declared distribution(s) ['spacy']" in refusal(lambda: LI.lock_step(REP_NLP, ["mem0ai[nlp]==2.2.0"],
                                                                                        dists=["mem0ai", "spacy"])))
check("... and every declared distribution in the lock passes", refusal(lambda: LI.lock_step(
      REP_NLP, ["mem0ai[nlp]==2.2.0"], dists=["mem0ai", "Qdrant_Client"])) == "accepted")
cvn = LI.check_versions({"python": "3.12.10", "dists": {"mem0ai": "2.2.0", "spacy": None}},
                        [{"name": "mem0ai", "version": "2.2.0"}], ["mem0ai", "spacy"], "3.12.10")
check("B-NLP: check_versions names a declared distribution that is not in the lock - None against None is no pass",
      cvn == ["the declared distribution spacy is not in the lock"], str(cvn))
import ast as _ast  # noqa: E402
_tree = _ast.parse((ROOT / "research" / "v3" / "lock_install.py").read_text(encoding="utf-8"))
_run = next(n for n in _tree.body if isinstance(n, _ast.FunctionDef) and n.name == "run_lock_install")
_called = [c.func.id for c in _ast.walk(_run) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)]
_kw = [k.arg for c in _ast.walk(_run) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)
       and c.func.id == "lock_step" for k in c.keywords]
check("B-NLP: the window's lock is lock_step's, with the venv's declared distributions - run_lock_install never calls "
      "lock_from_report itself (the real-pip case is _test_v3_lock_install_e2e's, under the lock)",
      _called.count("lock_step") == 1 and "lock_from_report" not in _called and "dists" in _kw, str(_called))


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
    ("a file over plain http", bad(("install", 0, "download_info", "url"), "http://files.pythonhosted.org/x/mem0ai-2.2.0-py3-none-any.whl"), "is not https://files.pythonhosted.org"),
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
import contextlib as _cl  # noqa: E402
import io as _io  # noqa: E402
import platform as _pf  # noqa: E402
# the auditor's gate on b60190a (core, bare 3.10): "pip" is no distribution every interpreter has - a bare venv has none
# at all - so the probe reads one this row puts on the path itself, with a version of its own
_site = TMP / "probe_site"
(_site / "nvt3_probe_dist-1.2.3.dist-info").mkdir(parents=True)
(_site / "nvt3_probe_dist-1.2.3.dist-info" / "METADATA").write_text(
    "Metadata-Version: 2.1\nName: nvt3-probe-dist\nVersion: 1.2.3\n", encoding="utf-8")
_buf = _io.StringIO()
sys.path.insert(0, str(_site))
try:
    with _cl.redirect_stdout(_buf):
        exec(compile(LI.version_probe(["json"], ["nvt3-probe-dist"]), "<probe>", "exec"), {})
    probed = json.loads(_buf.getvalue().strip().splitlines()[-1])
except Exception as e:  # noqa: BLE001
    probed = {"error": f"{type(e).__name__}: {e}"}
finally:
    sys.path.remove(str(_site))
check("the import probe imports the declared modules and prints the venv's own python version and each requested "
      "distribution's version as JSON", probed.get("python") == _pf.python_version()
      and (probed.get("dists") or {}).get("nvt3-probe-dist") == "1.2.3", str(probed))
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
check("the declared venvs: mem0_v3 = mem0ai[nlp] 2.2.0 (PREREG §2.2, T22 - B-NLP) on the declared base py-base-312 "
      "(LI-1); its imports and distributions prove the extra landed (mem0 and spacy)",
      LI.VENVS.get("mem0_v3") == {"base": "py-base-312", "specs": ["mem0ai[nlp]==2.2.0"], "imports": ["mem0", "spacy"],
                                  "dists": ["mem0ai", "spacy"]}, str(LI.VENVS))
check("B-NLP (the auditor's method rule): every VENVS spec is its PREREG §2.2 row - the distribution, its extras and "
      "its version, quoted in PREREG_22 - and every venv has that row",
      set(LI.PREREG_22) == set(LI.VENVS) and all(
          [LI.spec_parts(x) for x in LI.VENVS[v]["specs"]]
          == ([tuple(p) for p in LI.PREREG_22[v]["pins"]] if "pins" in LI.PREREG_22[v]
              else [(LI.PREREG_22[v]["dist"], LI.PREREG_22[v]["extras"], LI.PREREG_22[v]["version"])])
          and LI.PREREG_22[v]["base"] == LI.VENVS[v]["base"] for v in LI.VENVS),
      str(LI.PREREG_22))
REV1, AMD = "research/v3/PREREG-V3-rev1.md", "research/v3/PREREG-V3-AMENDMENTS.md"
_SRC = {s: (ROOT / s).read_text(encoding="utf-8") if (ROOT / s).is_file() else "" for s in (REV1, AMD)}
check("... and each quoted row is its named source's own §2.2 line, word for word (its extras included) - revision 1, "
      "or the amendments file beside it (Q-C5e-3)",
      all(LI.PREREG_22[v].get("source") in _SRC and _SRC[LI.PREREG_22[v]["source"]].count(LI.PREREG_22[v]["row"]) == 1
          and all(f"with `[{x}]`" in LI.PREREG_22[v]["row"] for x in LI.PREREG_22[v].get("extras", [])) for v in LI.PREREG_22),
      str([(v.get("source"), v["row"][:60]) for v in LI.PREREG_22.values()]))
check("C5e (Q-C5e-1 = O-a, Q-C5e-2 = O-a): graphiti_v3 = graphiti-core[falkordb] 0.30.2 (T31: its FalkorDB driver's "
      "client, imported with the driver), langmem_v3 = langmem 0.0.30 with langgraph pinned by the lock and checked, "
      "cognee_v3 = cognee 1.6.1 - each on py-base-312",
      {v: LI.VENVS.get(v) for v in ("graphiti_v3", "langmem_v3", "cognee_v3")} == {
          "graphiti_v3": {"base": "py-base-312", "specs": ["graphiti-core[falkordb]==0.30.2"],
                          "imports": ["graphiti_core", "falkordb", "graphiti_core.driver.falkordb_driver"],
                          "dists": ["graphiti-core", "falkordb"]},
          "langmem_v3": {"base": "py-base-312", "specs": ["langmem==0.0.30"], "imports": ["langmem", "langgraph"],
                         "dists": ["langmem", "langgraph"]},
          "cognee_v3": {"base": "py-base-312", "specs": ["cognee==1.6.1"], "imports": ["cognee"], "dists": ["cognee"]}},
      str({v: LI.VENVS.get(v) for v in ("graphiti_v3", "langmem_v3", "cognee_v3")})[:400])
_rev1_g = next((ln for ln in _SRC[REV1].splitlines() if ln.startswith("| zep-graphiti | product |")), "")
_ins = "with `[falkordb]` (T31); "
_amd_g = (LI.PREREG_22.get("graphiti_v3") or {}).get("row", "")
check("Q-C5e-3: graphiti's quote is the amendments file's row, and that row is revision 1's plus exactly the inserted "
      "text - nothing else of the row drifts in by the amendment; revision 1 still holds its own row, unchanged",
      LI.PREREG_22.get("graphiti_v3", {}).get("source") == AMD and _amd_g.count(_ins) == 1
      and _amd_g.replace(_ins, "", 1) == _rev1_g and _SRC[REV1].count(_rev1_g) == 1 and _ins not in _SRC[REV1]
      and all(LI.PREREG_22[v]["source"] == REV1 for v in LI.PREREG_22 if v not in ("graphiti_v3", "scorer_v3")), _amd_g[:200])
check("Q-C5e-3: the amendment carries its id, trap, date, ruling, reason and its trap-closure line",
      all(s in _SRC[AMD] for s in ("## A1 - T31", "**Date:** 2026-09-28", "Q-C5e-2 (2026-09-28, 11:13)",
                                   "the client its FalkorDB driver imports (arm_graphiti.py:162)",
                                   "| T31 graphiti's FalkorDB client | §2.2 |")))
FNC = getattr(LI, "FREEZE_NEWER_CHECK", {})
SC_SPECS = ['sentence-transformers==6.1.0', 'nltk==3.10.3', 'scipy==1.18.1', 'torch==2.14.0', 'transformers==5.17.0', 'huggingface-hub==1.33.0', 'tokenizers==0.23.2', 'numpy==2.5.3', 'scikit-learn==1.9.1', 'typing-extensions==4.16.0', 'tqdm==4.70.1']
check("SC-1 (A4, T33): scorer_v3 is the scorer's venv on py-base-312 - exactly the eleven versions window a8-pypi-d p1 "
      "read (Q-SCR-5 = O-a), sentence-transformers, nltk, scipy and torch imported, all eleven version-checked",
      LI.VENVS.get("scorer_v3") == {"base": "py-base-312", "specs": SC_SPECS,
                                    "imports": ["sentence_transformers", "nltk", "scipy", "torch"],
                                    "dists": [x.split("==")[0] for x in SC_SPECS]}, str(LI.VENVS.get("scorer_v3"))[:300])
check("SC-2 (A4): scorer_v3's PREREG_22 row is A4's own paragraph in the amendments file, once, naming every one of its "
      "eleven pins as name==version", LI.PREREG_22.get("scorer_v3", {}).get("source") == AMD
      and _SRC[AMD].count(LI.PREREG_22.get("scorer_v3", {}).get("row", "\x00")) == 1
      and all(x in LI.PREREG_22["scorer_v3"]["row"] for x in SC_SPECS), str(LI.PREREG_22.get("scorer_v3"))[:300])
check("Q-C5e-1: the freeze check of every product pin, declared as data now - at FREEZE-V3 a metadata read (no install) "
      "of the newest stable; a newer one is an E5 line and a question to the auditor, never a silent move - the scorer, "
      "no arm, is not a product: its versions are A4's (read 2026-09-30)",
      set(FNC.get("pins", {})) == set(LI.VENVS) - {"scorer_v3"}
      and all(FNC["pins"][v]["dist"] == LI.spec_parts(LI.VENVS[v]["specs"][0])[0]
              and FNC["pins"][v]["version"] == LI.spec_parts(LI.VENVS[v]["specs"][0])[2] for v in FNC["pins"])
      and FNC["pins"]["mem0_v3"]["read"] == "2026-09-23" and FNC["pins"]["graphiti_v3"]["read"] == "2026-09-26"
      and all(FNC["pins"][v]["read"] == "2026-09-26 (rev1's date; the row names none)" for v in ("langmem_v3", "cognee_v3"))
      and "no install" in FNC.get("rule", "") and "never a silent move" in FNC.get("rule", "")
      and 'pinned X (read D1); newest at freeze Y' in FNC.get("rule", ""), str(FNC)[:400])
import ast  # noqa: E402
_offs = []
for _f in sorted((ROOT / "research" / "v3").rglob("*.py")):
    for _n in ast.walk(ast.parse(_f.read_text(encoding="utf-8"))):
        if isinstance(_n, ast.keyword) and _n.arg == "hf_offline" and not (
                (isinstance(_n.value, ast.Constant) and _n.value.value is True)
                or (_f.name == "scheduler.py" and ast.unparse(_n.value) == "spec.hf_offline")):   # forwards the default
            _offs.append(f"{_f.name}:{_n.value.lineno}")
_SCH = (ROOT / "research" / "v3" / "scheduler.py").read_text(encoding="utf-8")
check("C5e HF-offline: every arm spawn runs with HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE - the scheduler's spec defaults "
      "to it, build_env sets both, and no caller in research/v3 passes hf_offline anything but True (graphiti, langmem "
      "and cognee included: none downloads a model at run time)",
      _offs == [] and "    hf_offline: bool = True\n" in _SCH
      and all(s in (ROOT / "research" / "v3" / "launch.py").read_text(encoding="utf-8")
              for s in ('env["HF_HUB_OFFLINE"] = env["TRANSFORMERS_OFFLINE"] = "1"',)), str(_offs))

MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
#: the index metadata window of plan d9 (the auditor's Q-SCR-2 = O-a) shares the prefix and installs nothing
META_W = "a8-pypi-d"
mw = {k[len(LI.WINDOW_PREFIX):]: v for k, v in MANW.items() if k.startswith(LI.WINDOW_PREFIX) and k != META_W}
check("C3a-meta: the one a8-pypi window that is no venv's is plan d9's metadata window - fetch_a3's D9_WINDOW, pypi.org "
      "only, no install, no venv", META_W in MANW and "venv" not in MANW[META_W] and "specs" not in MANW[META_W]
      and MANW[META_W]["hosts"] == ["pypi.org"]
      and 'D9_WINDOW = "a8-pypi-d"' in (ROOT / "research" / "v3" / "fetch_a3.py").read_text(encoding="utf-8"),
      str(MANW.get(META_W))[:200])
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

print("\n- the seam: what install_v3_data._step hands the spawn, with the offline pip's declared env -")
seam: dict = {}


class _SeamW:
    def __init__(self, *a, **k):
        self.native = None

    def begin_check(self, cid):
        pass

    def end_check(self, cid):
        return {"complete": True, "native": {"hits": 0, "loopback_hits": 0}, "fs": {"fs_hits": 0}}


class _SeamProc:
    returncode = 0

    def communicate(self, timeout=None):
        return b"", b""


def _seam_spawn(c, argv, **kw):
    """B-C4B-CWD's seam row (the auditor): the stub replaces the one function that checks, so it runs the REAL checks
    of launch.spawn - check_cwd, assert_env, assert_argv - on what _step hands it."""
    seam["reasons"] = (L.check_cwd(c, kw["cwd"], kw["record"])
                       + L.assert_env(c, kw["env"], parent_env=kw["parent_env"], catcher_url=kw["catcher_url"])
                       + L.assert_argv(c, argv))
    seam["env"] = dict(kw["env"])
    return SimpleNamespace(process=_SeamProc(), kill_tree=lambda: None)


IV = LI._iv()
CS = L.Contract(polygon_root=TMP / "ps", runs_root=TMP / "ps" / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner",
                secrets_dir=TMP / "secrets", quarantine_root=TMP / "q", conservation_root=TMP / "cv")
Lseam = SimpleNamespace(make_unit_dirs=L.make_unit_dirs, build_env=L.build_env, Witnesses=_SeamW, spawn=_seam_spawn,
                        NativeEgressWitness=lambda: None, FsWitness=lambda s: None, watched_set=lambda c: [])
vpy = CS.polygon_root / "mem0_v3" / "Scripts" / "python.exe"
seam_argv = LI.install_argv(vpy, [], CS.runs_root / "_install" / "a8-pypi-mem0_v3" / "s1" / "lock.txt",
                            CS.runs_root / "_fetch" / "a8-pypi-mem0_v3" / "s1" / "j1" / "wheels")
IV._step(CS, Lseam, stand="_install.a8-pypi-mem0_v3", run="s1", arm="pip", argv=seam_argv, path_dirs=[vpy.parent],
         parent_env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")}, native=None, fs=None, check_id="seam",
         declared=LI.offline_env(CS))
check("B-C4B-CWD seam (the auditor): the offline pip step - install_v3_data._step with lock_install's declared env - "
      "passes the contract's own spawn checks (a fresh EMPTY cwd, the env, the argv) with no reason",
      seam.get("reasons") == [] and seam.get("env", {}).get("PIP_CONFIG_FILE") == os.devnull, str(seam.get("reasons")))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 lock install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
