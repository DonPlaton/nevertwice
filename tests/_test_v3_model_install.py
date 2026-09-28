#!/usr/bin/env python3
"""PREREG-V3 A8 B-NLP NLP-2b (the auditor's rule, 07:19): research/v3/model_install.py - spawns NO child. The window is
a fake fetch_a3 whose run_child_window leaves the gh_model job's file; every offline step is an injected callable,
and one row drives install_v3_data._step through a stub spawn that runs launch's REAL checks:

* before anything, offline: the mem0_v3 install record with no problem, spaCy's LOCKED version from its lock, pip's
  own args from it, and the model's NAME from the installed mem0's own source (probe_a8's m0_nlp_model fact) - a
  missing record, a problem, no spacy in the lock, or a blocked fact refuses by name before the window;
* the window: the gh_model job for exactly that model and spaCy, on exactly the manifest's hosts; a host reached with no
  catcher tunnel, a window problem, or a failed job stops everything before pip;
* after it: the file re-read from the disk against the job's sha256 and the asset's size (the digest too when there is
  one); pip offline - --require-hashes --no-deps --no-index --find-links <the model dir>, a one-line requirement
  <model>==<version> --hash=sha256:<sha>, the declared offline env; then an isolated check that spacy.util.is_package
  and importlib.metadata name the model at its version; every step's own boundary check must be clean;
* the manifest declares the a8-spacy-model window exactly as the code does (hosts, CDN hosts, repo, compat path,
  one redirect).

    python tests/_test_v3_model_install.py
"""
from __future__ import annotations

import atexit
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


MI = _load("v3_model_install_t", ROOT / "research" / "v3" / "model_install.py")
L = _load("v3_launch_model_t", ROOT / "research" / "v3" / "launch.py")
LI = _load("v3_lock_install_model_t", ROOT / "research" / "v3" / "lock_install.py")
PASSED = FAILED = 0
RAISED: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def ok(fn) -> bool:
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_model_install_"))
atexit.register(shutil.rmtree, TMP, True)
MODEL, SPACY, V = "en_core_web_sm", "3.8.7", "3.8.10"
#: the declared hosts, written here - independent of the code's constants, which the rows compare against
RAW, API, WEB = "raw.githubusercontent.com", "api.github.com", "github.com"
CDN1, CDN2 = "objects.githubusercontent.com", "release-assets.githubusercontent.com"
NAME = f"{MODEL}-{V}-py3-none-any.whl"
WHL = b"PK\x03\x04 the model " * 300
SHA = hashlib.sha256(WHL).hexdigest()
CLEAN = {"complete": True, "native": {"hits": 0, "loopback_hits": 0}, "fs": {"fs_hits": 0}}
SPM = (b"_nlp_full = None\n_nlp_lemma = None\n_load_failed_full = False\n_load_failed_lemma = False\n\n\n"
       b"def _ensure_model_available():\n    import spacy\n    if not spacy.util.is_package(\"en_core_web_sm\"):\n"
       b"        download(\"en_core_web_sm\")\n")


def _b64(d: bytes) -> str:
    import base64  # noqa: PLC0415
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def dist(site: Path, name: str, version: str, files: dict) -> None:
    """A distribution on the site: its files and its dist-info with a hashed RECORD (pip's own shape)."""
    di = f"{name}-{version}.dist-info"
    rows = []
    for rel, data in {**files, f"{di}/METADATA": f"Name: {name}\nVersion: {version}\n".encode()}.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)
        rows.append(f"{rel},sha256={_b64(hashlib.sha256(data).digest())},{len(data)}")
    (site / di / "RECORD").write_text("\n".join(rows + [f"{di}/RECORD,,"]) + "\n", encoding="utf-8")


def contract(tag):
    base = TMP / tag
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "q",
                      conservation_root=base / "cv")


def world(tag, *, record="ok", spm=SPM):
    """A contract, a mem0_v3 venv with the source, and its install record."""
    c = contract(tag)
    venv = c.polygon_root / "mem0_v3"
    site = venv / "Lib" / "site-packages"
    (site / "mem0" / "utils").mkdir(parents=True)
    if spm is not None:
        dist(site, "mem0ai", "2.2.0", {"mem0/__init__.py": b"", "mem0/utils/spacy_models.py": spm})
    rec_dir = c.runs_root / "_install" / "a8-pypi-mem0_v3" / "i1"
    rec_dir.mkdir(parents=True)
    rec = {"problems": [], "pip": {"args": ["--use-deprecated=legacy-certs"]}, "venv_top_level": [],
           "lock": [{"name": "mem0ai", "version": "2.2.0"}, {"name": "spacy", "version": SPACY}]}
    if record == "problem":
        rec["problems"] = ["pip's offline install failed (exit 1)"]
    elif record == "nospacy":
        rec["lock"] = [{"name": "mem0ai", "version": "2.2.0"}]
    if record != "none":
        (rec_dir / "install_record.json").write_text(json.dumps(rec), encoding="utf-8")
    return c, venv


class FakeWindow:
    def __init__(self, *, summary=None, problems=(), untunnelled=None, tamper=False):
        self.summary = summary
        self.problems, self.untunnelled, self.tamper = list(problems), untunnelled, tamper
        self.seen: dict = {}

    def run_child_window(self, c, L_, **kw):
        self.seen.update(kw)
        unit = Path(c.runs_root) / "_fetch" / kw["window"] / kw["run"] / "j0"
        (unit / "model").mkdir(parents=True)
        (unit / "model" / NAME).write_bytes(WHL[:-1] + b"!" if self.tamper else WHL)
        summ = self.summary or {"ok": True, "kind": "gh_model", "model": MODEL, "version": V,
                                "asset": {"name": NAME, "size": len(WHL), "digest": f"sha256:{SHA}", "path": "/x"},
                                "file": {"sha256": SHA, "bytes": len(WHL), "path": f"model/{NAME}"}, "tls_only": False,
                                "cdn_host": CDN2,
                                "requests": [{"host": h} for h in (RAW, API, WEB, CDN2)]}
        hosts = [h for h in kw["hosts"] if h != self.untunnelled]
        return {"problems": list(self.problems), "check": {"complete": True}, "issuers": [],
                "catcher": [{"host": h, "tunnelled": True} for h in hosts], "jobs": [{"unit": str(unit), "summary": [summ]}]}


class Steps:
    """The offline steps: (argv, arm, declared) -> (rc, stdout, stderr, check)."""

    def __init__(self, *, pip_rc=0, answer=None, dirty=(), site=None, pth=False, nodist=False, check_rc=0, partial=False):
        self.calls: list = []
        self.pip_rc, self.dirty, self.site, self.pth, self.nodist, self.check_rc = pip_rc, set(dirty), site, pth, nodist, check_rc
        self.partial = partial
        self.answer = answer if answer is not None else {"is_package": True, "version": V}

    def __call__(self, argv, arm, declared):
        self.calls.append({"argv": list(argv), "arm": arm, "declared": dict(declared or {})})
        chk = {**CLEAN, "native": {"hits": 1, "loopback_hits": 0}} if arm in self.dirty else CLEAN
        if arm == "pip":
            if self.site is not None and (self.pip_rc == 0 or self.partial) and not self.nodist:
                files = {f"{MODEL}/__init__.py": b"__version__ = '3.8.10'\n"}
                if self.pth:
                    files["nvt3_evil.pth"] = b"import os\n"
                dist(self.site, MODEL, V, files)
            return self.pip_rc, b"Successfully installed", b"", chk
        return self.check_rc, (json.dumps(self.answer) + "\n").encode(), b"", chk


def run(tag, *, window=None, steps=None, record="ok", spm=SPM, run_label="m1"):
    c, venv = world(tag, record=record, spm=spm)
    w, st = window or FakeWindow(), steps or Steps()
    if st.site is None:
        st.site = venv / "Lib" / "site-packages"
    try:
        rec = MI.run_model_install(c, L, w, run=run_label, install_run="i1", venv=venv, via_port=1, parent_env={},
                                   python=Path("py.exe"), step=st)
        err = None
    except Exception as e:  # noqa: BLE001 - a refusal is the row's to read
        rec, err = {}, e
    return rec, c, venv, w, st, err


print("- a whole model install -")
rec, C, VENV, W, ST, err = run("ok")
rec_ok, st_ok = rec, ST
check("the install has no problem", ok(lambda: err is None and rec["problems"] == []), f"{err!r} {rec.get('problems')}")
check("before the window: spaCy's locked version, the model's name from the installed mem0's own source (file:line)",
      ok(lambda: rec["spacy"] == SPACY and rec["model"] == MODEL
         and rec["model_source"].startswith("mem0/utils/spacy_models.py:9@sha256:")), str({k: rec.get(k) for k in ("spacy", "model", "model_source")}))
check("the window: the gh_model job for exactly that model and spaCy on exactly the declared hosts",
      ok(lambda: W.seen["window"] == MI.WINDOW and W.seen["hosts"] == [RAW, API, WEB, CDN1, CDN2]
         and W.seen["jobs"] == [MI.gh_job(MODEL, SPACY)] and MI.gh_job(MODEL, SPACY)["kind"] == "gh_model"),
      str(W.seen.get("jobs")))
pip = next((c for c in ST.calls if c["arm"] == "pip"), {})
mdir = Path(W.seen.get("run") and (C.runs_root / "_fetch" / MI.WINDOW / "m1" / "j0" / "model"))
req = Path(pip.get("argv", ["x"])[-1]) if pip else None
check("pip offline: the recorded pip args, --require-hashes --no-deps --no-index, only the model's directory, one "
      "requirement line with the file's sha256, the declared offline env",
      ok(lambda: pip["argv"][:6] == [str(VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "-I", "-B",
                                     "-m", "pip", "install"]
         and pip["argv"][6] == "--use-deprecated=legacy-certs"
         and {"--require-hashes", "--no-deps", "--no-index"} <= set(pip["argv"])
         and pip["argv"][pip["argv"].index("--find-links") + 1] == str(mdir)
         and req.read_text(encoding="utf-8") == f"{MODEL}=={V} --hash=sha256:{SHA}\n"
         and pip["declared"] == LI.offline_env(C)), str(pip)[:400])
chk_call = next((c for c in ST.calls if c["arm"] == "check"), {})
check("the check: isolated, spacy.util.is_package and importlib.metadata name the model at its version",
      ok(lambda: chk_call["argv"][1:4] == ["-I", "-B", "-c"] and "spacy.util.is_package" in chk_call["argv"][4]
         and rec["check"] == {"is_package": True, "version": V}), str(rec.get("check")))
check("the record: the model, its version, the sha256, the CDN host, TLS-only or not",
      ok(lambda: {k: rec[k] for k in ("version", "sha256", "cdn_host", "tls_only")}
         == {"version": V, "sha256": SHA, "cdn_host": CDN2, "tls_only": False}),
      str({k: rec.get(k) for k in ("version", "sha256", "cdn_host", "tls_only")}))
check("the record is written under the runs tree", ok(lambda: json.loads((C.runs_root / "_install" / MI.WINDOW / "m1"
                                                                          / "model_record.json").read_text(encoding="utf-8"))["problems"] == []))

print("\n- refusals and problems by name -")
for label, kw, want in (("no install record", {"record": "none"}, "no mem0_v3 install record"),
                        ("an install record with a problem", {"record": "problem"}, "has problems"),
                        ("no spacy in the lock", {"record": "nospacy"}, "no spacy in the mem0_v3 lock"),
                        ("no spacy_models in the installed mem0", {"spm": None}, "blocked:source-missing:m0_nlp_model")):
    rec, C, VENV, W, ST, err = run(label.replace(" ", "_"), **kw)
    check(f"{label}: refused by name before the window", ok(lambda: isinstance(err, MI.ModelRefused) and want in str(err)
                                                           and not W.seen and not ST.calls), repr(err))
rec, C, VENV, W, ST, err = run("win_problem", window=FakeWindow(problems=["the window check is not complete"]))
check("a window problem stops everything before pip", ok(lambda: "the window check is not complete" in rec["problems"]
                                                         and not ST.calls), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("job_failed", window=FakeWindow(summary={"ok": False, "error": "Refused: 2 assets"}))
check("a failed job stops everything before pip", ok(lambda: any("the gh_model job failed" in p for p in rec["problems"])
                                                     and not ST.calls), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("past", window=FakeWindow(untunnelled=CDN2))
check("a host reached with no catcher tunnel stops everything before pip",
      ok(lambda: any("past the catcher" in p for p in rec["problems"]) and not ST.calls), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("tamper", window=FakeWindow(tamper=True))
check("a file changed on the disk after the window stops everything before pip",
      ok(lambda: any("not the job's sha256" in p for p in rec["problems"]) and not ST.calls), str(rec.get("problems")))
NODIG = {"ok": True, "kind": "gh_model", "model": MODEL, "version": V, "tls_only": True, "cdn_host": CDN2,
         "asset": {"name": NAME, "size": len(WHL), "digest": None, "path": "/x"},
         "file": {"sha256": SHA, "bytes": len(WHL), "path": f"model/{NAME}"},
         "requests": [{"host": h} for h in (RAW, API, WEB, CDN2)]}
rec, C, VENV, W, ST, err = run("tamper_nodigest", window=FakeWindow(summary=NODIG, tamper=True))
check("with no digest (TLS-only), a same-size file changed after the window is still caught by the job's own sha256",
      ok(lambda: any("not the job's sha256" in p for p in rec["problems"]) and not ST.calls), str(rec.get("problems")))
check("C-NLP2b-1 (the auditor): the model's installed set is hashed, and the site checks pass, BEFORE any interpreter "
      "starts in the venv", ok(lambda: len(rec_ok["model_installed_set_sha256"]) == 64 and rec_ok["model_installed_files"] == 2   # its module and its METADATA; RECORD itself is never in the set
                               and [c["arm"] for c in st_ok.calls] == ["pip", "check"]),
      str({k: rec_ok.get(k) for k in ("model_installed_set_sha256", "model_installed_files")}))
rec, C, VENV, W, ST, err = run("pth", steps=Steps(pth=True))
check("C-NLP2b-1: a wheel that adds a .pth is a site problem by name, and no interpreter starts (no check step)",
      ok(lambda: any("nvt3_evil.pth" in p and "interpreter start" in p for p in rec["problems"])
         and [c["arm"] for c in ST.calls] == ["pip"]), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("nodist", steps=Steps(nodist=True))
check("pip claiming success with no model dist-info on the site is a problem by name, and no interpreter starts",
      ok(lambda: any("the model's installed set" in p for p in rec["problems"]) and [c["arm"] for c in ST.calls] == ["pip"]),
      str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("check_exit", steps=Steps(check_rc=1))
check("MIl (the auditor): a check that prints a valid answer and then exits 1 is a problem naming the exit",
      ok(lambda: any("the model check failed (exit 1)" in p for p in rec["problems"])), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("pip_partial", steps=Steps(pip_rc=1, partial=True))
check("pip failing after it wrote the model's files is still a stop: no site check can make a failed install pass, and "
      "no interpreter starts", ok(lambda: any("pip's offline install of the model failed" in p for p in rec["problems"])
                                  and [c["arm"] for c in ST.calls] == ["pip"]), str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("pip_fail", steps=Steps(pip_rc=1))
check("pip's failure is a problem by name, and no check runs",
      ok(lambda: any("pip's offline install of the model failed" in p for p in rec["problems"])
         and [c["arm"] for c in ST.calls] == ["pip"]), str(rec.get("problems")))
for label, ans, want in (("not a package", {"is_package": False, "version": V}, "is_package"),
                         ("another version", {"is_package": True, "version": "3.8.0"}, "3.8.0")):
    rec, C, VENV, W, ST, err = run(f"check_{label.replace(' ', '_')}", steps=Steps(answer=ans))
    check(f"the check naming the model {label} is a problem by name", ok(lambda: any(want in p for p in rec["problems"])),
          str(rec.get("problems")))
rec, C, VENV, W, ST, err = run("dirty_pip", steps=Steps(dirty={"pip"}))
check("pip's own boundary check counting an egress hit is a problem, and no check runs",
      ok(lambda: any("the pip check counted 1 egress" in p for p in rec["problems"]) and [c["arm"] for c in ST.calls] == ["pip"]),
      str(rec.get("problems")))
c2, v2 = world("used")
(c2.runs_root / "_install" / MI.WINDOW / "m1").mkdir(parents=True)
try:
    MI.run_model_install(c2, L, FakeWindow(), run="m1", install_run="i1", venv=v2, via_port=1, parent_env={},
                         python=Path("py"), step=Steps())
    used = "accepted"
except Exception as e:  # noqa: BLE001
    used = f"{type(e).__name__}: {e}"
check("a used run label is refused by name", "used before" in used, used)

print("\n- the seam: install_v3_data._step with the model's declared env passes launch's own checks -")
seam: dict = {}


class _W:
    def __init__(self, *a, **k):
        self.native = None

    def begin_check(self, cid):
        pass

    def end_check(self, cid):
        return CLEAN


class _Proc:
    returncode = 0

    def communicate(self, timeout=None):
        return b"", b""


def _spawn(c, argv, **kw):
    seam["reasons"] = (L.check_cwd(c, kw["cwd"], kw["record"])
                       + L.assert_env(c, kw["env"], parent_env=kw["parent_env"], catcher_url=kw["catcher_url"])
                       + L.assert_argv(c, argv))
    return SimpleNamespace(process=_Proc(), kill_tree=lambda: None)


Lseam = SimpleNamespace(make_unit_dirs=L.make_unit_dirs, build_env=L.build_env, Witnesses=_W, spawn=_spawn,
                        NativeEgressWitness=lambda: None, FsWitness=lambda s: None, watched_set=lambda c: [])
cs, vs = world("seam")
st = MI.default_step(cs, Lseam, stand="_install.a8-spacy-model", run="s1", venv=vs,
                     parent_env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")}, native=None, fs=None)
st([str(vs / "Scripts" / "python.exe"), "-I", "-B", "-m", "pip", "install", "--no-index"], "pip", LI.offline_env(cs))
check("B-C4B-CWD's seam: what the model's pip step hands the spawn passes check_cwd, assert_env and assert_argv",
      seam.get("reasons") == [], str(seam.get("reasons")))

print("\n- the manifest -")
MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
mw = MANW.get(MI.WINDOW) or {}
check("the manifest declares a8-spacy-model exactly as the code does - its five hosts, the two GitHub CDN hosts, the "
      "repository, the compatibility path, one redirect", ok(lambda: mw["hosts"] == [RAW, API, WEB, CDN1, CDN2]
                                                            and mw["hosts"] == [MI.RAW, MI.API, MI.WEB, *MI.CDN_HOSTS]
                                                            and mw["cdn_hosts"] == list(MI.CDN_HOSTS) == [CDN1, CDN2] and mw["repo"] == MI.REPO
                                                            and mw["compat_path"] == MI.COMPAT_PATH and mw["max_redirects"] == 1),
      str(mw)[:300])
check("no row's condition raised", RAISED == [], str(RAISED))
print(f"\nv3 model install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
