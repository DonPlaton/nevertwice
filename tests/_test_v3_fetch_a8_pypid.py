#!/usr/bin/env python3
"""PREREG-V3 A8 (the auditor's Q-SCR-2 = O-a, 2026-09-30): research/v3/fetch_a3.py plan d9 - the a8-pypi-d window,
metadata only - offline, with real processes under the contract (the catcher-only proxy, fetch-child jobs, a fake
pypi.org behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a8-pypi-d" entry, the single source: pypi.org only, no redirect, the scorer venv's
  packages (distinct in PEP 503's normal form), deps_of one of them, the target cp312 on win_amd64 - anything else is
  refused before any spawn;
* job 0 reads each declared package's JSON at its normal name; job 1 the JSON of deps_of's newest stable release; job 2
  the JSON of each dependency that release declares (no extra) and that is not declared - nothing else, no link
  followed, nothing installed;
* the report: per package its newest stable release (never a pre-release, never one whose files are all yanked), the
  newest meeting deps_of's specifier for it, and that release's wheel for cp312 on win_amd64 (cp312, then abi3, then a
  pure py3 wheel) with the index's sha256 and size; deps_of's requirements, the ones under an extra named apart; a
  missing or non-JSON answer, another package's name, no stable release, a specifier not evaluated here and no wheel
  for the target are problems by name.

    python tests/_test_v3_fetch_a8_pypid.py
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
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


L = _load("v3_launch_d9", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d9", ROOT / "research" / "v3" / "fetch_a3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def holds(fn):
    """(value, None) or (None, the exception named) - a call that raises FAILs its own row, never the suite."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
REAL = MANI["windows"].get("a8-pypi-d") or {}
print("- the manifest's a8-pypi-d entry (the auditor's Q-SCR-2 = O-a) -")
decl, derr = holds(lambda: F.d9_decl(MANI))
check("PD-1: the manifest declares a8-pypi-d as plan d9 reads it: pypi.org only, no redirect, sentence-transformers, nltk, "
      "scipy and torch, the dependencies of sentence-transformers, the target cp312 on win_amd64",
      derr is None and decl["hosts"] == ["pypi.org"] and decl["max_redirects"] == 0
      and decl["packages"] == ["sentence-transformers", "nltk", "scipy", "torch"] and decl["deps_of"] == "sentence-transformers"
      and decl["target"] == {"python": "cp312", "platform": "win_amd64"}, str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "index_url": "https://download.pytorch.org/whl/cu128"},
    "another host": {**REAL, "hosts": ["pypi.org", "files.pythonhosted.org"]},
    "a redirect": {**REAL, "max_redirects": 1},
    "no packages": {**REAL, "packages": []},
    "two spellings of one package": {**REAL, "packages": ["torch", "Torch"], "deps_of": "torch"},
    "a name that climbs": {**REAL, "packages": ["../torch"], "deps_of": "../torch"},
    "deps_of not declared": {**REAL, "deps_of": "transformers"},
    "another target": {**REAL, "target": {"python": "cp311", "platform": "win_amd64"}},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d9_decl({"windows": {"a8-pypi-d": entry}}))
    refused[label] = bool(e) and e.startswith("D9ManifestError")
check("PD-2: an entry with anything but the declared shape is refused by name (D9ManifestError) - " + ", ".join(bad_cases),
      bool(REAL) and all(refused.values()), str([k for k, v in refused.items() if not v]))


def whl(name: str, ver: str, tag: str, *, yanked: bool = False) -> dict:
    fn = f"{name.replace('-', '_')}-{ver}-{tag}.whl"
    return {"filename": fn, "digests": {"sha256": hashlib.sha256(fn.encode()).hexdigest()}, "size": 1000 + len(fn),
            "yanked": yanked, "requires_python": ">=3.9", "packagetype": "bdist_wheel"}


def sdist(name: str, ver: str) -> dict:
    fn = f"{name}-{ver}.tar.gz"
    return {"filename": fn, "digests": {"sha256": hashlib.sha256(fn.encode()).hexdigest()}, "size": 500, "yanked": False,
            "requires_python": ">=3.9", "packagetype": "sdist"}


PURE = "py3-none-any"
CP = "cp312-cp312-win_amd64"
ST_REQ = ["transformers<5.0.0,>=4.41.0", "tqdm", "torch>=1.11.0", "scikit-learn", "scipy", "huggingface-hub>=0.20.0",
          "Pillow", "typing_extensions>=4.5.0", 'accelerate>=0.20.3; extra == "train"']
INDEX = {  # normal name -> (the index's own name, {version: files})
    "sentence-transformers": ("sentence-transformers", {"5.1.0": [whl("sentence-transformers", "5.1.0", PURE)],
                                                        "5.1.1": [whl("sentence-transformers", "5.1.1", PURE, yanked=True)],
                                                        "5.2.0rc1": [whl("sentence-transformers", "5.2.0rc1", PURE)]}),
    "nltk": ("nltk", {"3.9.1": [whl("nltk", "3.9.1", PURE)], "3.9.2": [whl("nltk", "3.9.2", PURE), sdist("nltk", "3.9.2")]}),
    "scipy": ("scipy", {"1.16.2": [whl("scipy", "1.16.2", CP), whl("scipy", "1.16.2", "cp311-cp311-win_amd64")]}),
    "torch": ("torch", {"2.9.0": [whl("torch", "2.9.0", CP), whl("torch", "2.9.0", "cp312-cp312-manylinux_2_28_x86_64")],
                        "2.10.0.dev20260901": [whl("torch", "2.10.0.dev20260901", CP)]}),
    "transformers": ("transformers", {"4.56.2": [whl("transformers", "4.56.2", PURE)],
                                      "5.0.0": [whl("transformers", "5.0.0", PURE)]}),
    "tqdm": ("tqdm", {"4.67.1": [whl("tqdm", "4.67.1", "py3-none-any")]}),
    "scikit-learn": ("scikit-learn", {"1.7.2": [whl("scikit-learn", "1.7.2", CP)]}),
    "huggingface-hub": ("huggingface-hub", {"0.35.3": [whl("huggingface_hub", "0.35.3", PURE)]}),
    "pillow": ("pillow", {"11.3.0": [whl("pillow", "11.3.0", CP)]}),
    "typing-extensions": ("typing_extensions", {"4.15.0": [whl("typing_extensions", "4.15.0", PURE)]}),
}


def pkg_json(n: str) -> bytes:
    name, rel = INDEX[n]
    return json.dumps({"info": {"name": name, "version": max(rel)}, "releases": rel}).encode()


ST_V = json.dumps({"info": {"name": "sentence-transformers", "version": "5.1.0", "requires_dist": ST_REQ},
                   "urls": INDEX["sentence-transformers"][1]["5.1.0"]}).encode()
ROUTES = {f"/pypi/{n}/json": (200, [("Content-Type", "application/json")], pkg_json(n)) for n in INDEX}
ROUTES["/pypi/sentence-transformers/5.1.0/json"] = (200, [("Content-Type", "application/json")], ST_V)
DECL = {"hosts": ["pypi.org"], "purpose": "test", "packages": ["sentence-transformers", "nltk", "scipy", "torch"],
        "deps_of": "sentence-transformers", "target": {"python": "cp312", "platform": "win_amd64"}, "max_redirects": 0}
DEPS = ["huggingface-hub", "pillow", "scikit-learn", "tqdm", "transformers", "typing-extensions"]

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d9_"))
PY = "pypi.org"
made = TF.make_test_cert(TMP / "cert", PY, org="Google Trust Services")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a8 pypi-d: {PASSED} passed, {FAILED} failed")
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


def window(run: str, routes: dict):
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d9_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a8-pypi-d", hosts=["pypi.org"], jobs=jobs, python=Path(sys.executable), via_port=hop.port, run=run,
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    rep, perr = holds(lambda: F.d9_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return asked, rec, rep, perr


def offline(tag: str, job0: dict, job1: dict | None = None, job2: dict | None = None):
    """d9_report on saved answers, no window: {relpath: bytes} per job."""
    jobs = []
    for i, files in enumerate((job0, job1, job2)):
        if files is None:
            continue
        u = TMP / "off" / tag / f"j{i}"
        for rel, data in files.items():
            (u / rel).parent.mkdir(parents=True, exist_ok=True)
            (u / rel).write_bytes(data)
        u.mkdir(parents=True, exist_ok=True)
        jobs.append({"index": i, "unit": str(u), "summary": []})
    return holds(lambda: F.d9_report({"jobs": jobs}, DECL))


J0 = {f"pypi/{n}.json": pkg_json(n) for n in DECL["packages"]}
J1 = {"pypi/sentence-transformers@5.1.0.json": ST_V}
J2 = {f"pypi/{n}.json": pkg_json(n) for n in DEPS}

try:
    print("\n- the window, offline -")
    asked, rec, rep, perr = window("t1", ROUTES)
    check("PD-3: the requests, in order - the four declared packages at their normal names, sentence-transformers at its "
          "newest stable 5.1.0, then the six dependencies it declares that are not declared (not accelerate: an extra; "
          "not torch or scipy again)", perr is None and asked == [
              "/pypi/sentence-transformers/json", "/pypi/nltk/json", "/pypi/scipy/json", "/pypi/torch/json",
              "/pypi/sentence-transformers/5.1.0/json", *[f"/pypi/{n}/json" for n in DEPS]], f"{perr} {asked}")
    rep = rep or {}
    pk = rep.get("packages") or {}
    check("PD-4: newest stable, never a pre-release, never a release all of whose files are yanked, nor a dev build - "
          "sentence-transformers 5.1.0 (not 5.2.0rc1, not the yanked 5.1.1), torch 2.9.0 (not the dev build)",
          (pk.get("sentence-transformers") or {}).get("newest_stable") == "5.1.0"
          and (pk.get("torch") or {}).get("newest_stable") == "2.9.0" and (pk.get("nltk") or {}).get("newest_stable") == "3.9.2",
          json.dumps({n: (v or {}).get("newest_stable") for n, v in pk.items()}))
    check("PD-5: the wheel for cp312 on win_amd64 at the version taken, with the index's sha256 and size - torch's cp312 "
          "wheel (not manylinux), scipy's cp312 (not cp311), a pure py3 wheel for sentence-transformers and nltk",
          ((pk.get("torch") or {}).get("wheel") or {}).get("filename") == f"torch-2.9.0-{CP}.whl"
          and ((pk.get("torch") or {}).get("wheel") or {}).get("sha256") == INDEX["torch"][1]["2.9.0"][0]["digests"]["sha256"]
          and ((pk.get("scipy") or {}).get("wheel") or {}).get("filename") == f"scipy-1.16.2-{CP}.whl"
          and ((pk.get("nltk") or {}).get("wheel") or {}).get("filename") == f"nltk-3.9.2-{PURE}.whl"
          and ((pk.get("sentence-transformers") or {}).get("wheel") or {}).get("filename")
          == f"sentence_transformers-5.1.0-{PURE}.whl", json.dumps({n: (v or {}).get("wheel") for n, v in pk.items()})[:600])
    dp = rep.get("deps") or {}
    check("PD-6: deps_of's requirements as 5.1.0 declares them - eight, accelerate named apart as under an extra; each "
          "dependency with its specifier and the newest release meeting it (transformers: newest stable 5.0.0, taken "
          "4.56.2 under <5.0.0; torch: >=1.11.0 met by 2.9.0)",
          dp.get("of") == "sentence-transformers" and dp.get("version") == "5.1.0" and len(dp.get("requires") or []) == 8
          and dp.get("extras_excluded") == ['accelerate>=0.20.3; extra == "train"']
          and (pk.get("transformers") or {}).get("newest_stable") == "5.0.0"
          and (pk.get("transformers") or {}).get("newest_satisfying") == "4.56.2"
          and ((pk.get("transformers") or {}).get("wheel") or {}).get("filename") == f"transformers-4.56.2-{PURE}.whl"
          and (pk.get("torch") or {}).get("spec") == ">=1.11.0" and (pk.get("torch") or {}).get("newest_satisfying") == "2.9.0"
          and (pk.get("pillow") or {}).get("role") == "dependency of sentence-transformers"
          and set(pk) == set(DECL["packages"]) | set(DEPS), json.dumps(dp)[:500])
    check("PD-7: the window's check is complete, no native hit, no problem; the catcher tunnelled pypi.org only",
          rec is not None and rec["check"]["complete"] and rec["check"]["native_hits"] == 0 and rec["problems"] == []
          and rep.get("problems") == [] and {x["host"] for x in rec["catcher"] if x.get("tunnelled")} == {"pypi.org"},
          str((rec or {}).get("problems")) + str(rep.get("problems")))

    print("\n- the report's reading -")
    sdist_only = dict(J0)
    sdist_only["pypi/nltk.json"] = json.dumps({"info": {"name": "nltk"}, "releases": {"3.9.2": [sdist("nltk", "3.9.2")]}}).encode()
    html = dict(J0)
    html["pypi/scipy.json"] = b"<!DOCTYPE html><html><body>Too many requests</body></html>"
    other = dict(J0)
    other["pypi/scipy.json"] = json.dumps({"info": {"name": "scipy-extra"}, "releases": INDEX["scipy"][1]}).encode()
    rc_only = dict(J0)
    rc_only["pypi/torch.json"] = json.dumps({"info": {"name": "torch"}, "releases": {
        "2.10.0rc1": [whl("torch", "2.10.0rc1", CP)]}}).encode()
    cases = {
        "a newest release with no wheel for the target (an sdist only)": (offline("sdist", sdist_only, J1, J2),
                                                                          "nltk 3.9.2: no wheel for cp312 on win_amd64"),
        "an answer that is not a JSON object (an HTML page)": (offline("html", html, J1, J2), "scipy: the index's answer was not a JSON object"),
        "an index that names another package": (offline("other", other, J1, J2), "scipy: the index names another package"),
        "no stable release (a release candidate only)": (offline("rc", rc_only, J1, J2), "torch: no stable release"),
        "deps_of's release metadata not saved": (offline("nometa", J0, {}, J2), "sentence-transformers 5.1.0: its release metadata was not saved"),
    }
    got = {k: (r, e, [p for p in ((r or {}).get("problems") or [])]) for k, ((r, e), _w) in cases.items()}
    check("PD-8: problems by name - " + ", ".join(cases),
          all(e is None and any(w in p for p in probs) for (r, e, probs), (_x, w) in zip(got.values(), cases.values())),
          str({k: (e or probs) for k, (r, e, probs) in got.items()})[:900])
    ok_r, ok_e = offline("ok", J0, J1, J2)
    check("PD-8b: the same reading of the sound answers has no problem", ok_e is None and ok_r.get("problems") == [],
          str(ok_e or ok_r.get("problems")))
    odd = {"pypi/sentence-transformers@5.1.0.json": json.dumps({"info": {"requires_dist": ST_REQ + ["tqdm===4.67.1"]}}).encode()}
    odd_r, odd_e = offline("odd", J0, odd, J2)
    check("PD-8c: a specifier this cannot evaluate (===) is a problem by name, never a guess",
          odd_e is None and any("tqdm" in p and "not evaluated here" in p for p in odd_r.get("problems") or []),
          str(odd_e or (odd_r or {}).get("problems")))

    print("\n- versions and wheels, as the report reads them -")
    sat = [("1.0", "==1"), ("2.0.0", ">=2"), ("1.4.2", "~=1.4"), ("1.4.2", "==1.4.*"), ("4.56.2", ">=4.41.0,<5.0.0"),
           ("1.0.post1", ">1.0")]
    unsat = [("2.0", "~=1.4"), ("1.5.0", "==1.4.*"), ("5.0.0", "<5.0.0"), ("1.0", "!=1.0.0")]
    s_ok = [holds(lambda v=v, s=s: F._d9_satisfies(v, s)) for v, s in sat]
    u_ok = [holds(lambda v=v, s=s: F._d9_satisfies(v, s)) for v, s in unsat]
    check("PD-9: PEP 440's comparisons on stable releases - trailing zeros equal, ~= and ==X.* as prefixes, a post release "
          "after its release", all(r is True and e is None for r, e in s_ok) and all(r is False and e is None for r, e in u_ok),
          str((s_ok, u_ok)))
    wh = [holds(lambda fs=fs: F._d9_wheel(fs)) for fs in (
        [whl("x", "1", "cp38-abi3-win_amd64")], [whl("x", "1", "cp313-cp313-win_amd64")],
        [whl("x", "1", "cp312-cp312-manylinux_2_28_x86_64")], [whl("x", "1", "py2.py3-none-any")],
        [whl("x", "1", PURE), whl("x", "1", CP)], [whl("x", "1", CP, yanked=True)])]
    check("PD-10: the wheel for the target - an abi3 one taken, a cp313 or a manylinux one never, a py2.py3 pure one taken, "
          "cp312 preferred to pure, a yanked file never",
          all(e is None for _r, e in wh) and (wh[0][0] or {}).get("filename") == "x-1-cp38-abi3-win_amd64.whl"
          and wh[1][0] is None and wh[2][0] is None and (wh[3][0] or {}).get("filename") == "x-1-py2.py3-none-any.whl"
          and (wh[4][0] or {}).get("filename") == f"x-1-{CP}.whl" and wh[5][0] is None, str(wh)[:500])

    print("\n- main(): plan d9 only in window a8-pypi-d, on its declared entry - refused before any spawn -")

    class _WouldSpawn(Exception):
        pass

    def run_main(argv, manifest):
        mp = TMP / "manifest_main.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        saved = (F._load, F.MANIFEST, F.run_child_window)
        stub_l = SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(runs_root=TMP)),
                                 network_via_port=lambda c_: 47999)
        F._load = lambda name, path: stub_l if name == "v3_launch" else SimpleNamespace(PINS={})
        F.MANIFEST = mp

        def no_spawn(*a_, **k_):
            raise _WouldSpawn("main reached the window")
        F.run_child_window = no_spawn
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = F.main(argv)
        except _WouldSpawn:
            rc = "spawned"
        except SystemExit as e:
            rc = f"exit {e.code}"
        finally:
            F._load, F.MANIFEST, F.run_child_window = saved
        return rc, err.getvalue()

    tail = ["--run", "t", "--python", sys.executable]
    m1 = run_main(["--window", "a8-pypi-d", "--plan", "d7", *tail], MANI)
    m2 = run_main(["--window", "a7-hf-d", "--plan", "d9", *tail], MANI)
    check("PD-11: plan d9 outside window a8-pypi-d, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d9 runs in window a8-pypi-d" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    bad = json.loads(json.dumps(MANI))
    bad["windows"].setdefault("a8-pypi-d", {})["hosts"] = ["pypi.org", "download.pytorch.org"]
    m3 = run_main(["--window", "a8-pypi-d", "--plan", "d9", *tail], bad)
    m4 = run_main(["--window", "a8-pypi-d", "--plan", "d9", *tail], MANI)
    check("PD-12: a manifest whose a8-pypi-d entry is not the declared shape is refused (rc 2, named); the real one gets "
          "to the window", m3[0] == 2 and "a8-pypi-d" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a8 pypi-d: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
