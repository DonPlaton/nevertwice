#!/usr/bin/env python3
"""PREREG-V3 A7 phase 3 (the auditor's Q-A7-P3 = O-a, 2026-09-30): research/v3/fetch_a3.py plan d7 - the a7-hf-d window,
metadata only - offline, with real processes under the contract (the catcher-only proxy, one fetch-child job, a fake
huggingface.co behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-hf-d" entry, the single source: huggingface.co only, no redirect followed, the
  model's repository, the revision a7-discovery d1 found and the one file - anything else is refused before any spawn;
* exactly one request, a HEAD on the file's resolve URL at that revision: the redirect's host is recorded and never
  followed, nothing is downloaded, the signed query is never kept;
* the report: the status, the redirect host (None when huggingface.co answers the file itself - no problem), and a
  failed HEAD as a problem by name - from which the auditor fixes a7-hf's hosts.

    python tests/_test_v3_fetch_a7_hfd.py
"""
from __future__ import annotations

import contextlib
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


L = _load("v3_launch_d7", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d7", ROOT / "research" / "v3" / "fetch_a3.py")
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


REPO_, REV_ = "sentence-transformers/all-MiniLM-L6-v2", "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
PATH_ = f"/{REPO_}/resolve/{REV_}/model.safetensors"
CDN = "cas-bridge.xethub.hf.co"
SIG = "X-Amz-Signature=deadbeef&X-Amz-Credential=secret"
MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
REAL = MANI["windows"].get("a7-hf-d") or {}
print("- the manifest's a7-hf-d entry (the auditor's Q-A7-P3 = O-a) -")
decl, derr = holds(lambda: F.d7_decl(MANI))
check("HFD-1: the manifest declares a7-hf-d as plan d7 reads it: huggingface.co only, no redirect, all-MiniLM-L6-v2 at "
      "the revision a7-discovery d1 found (1110a243...), the one file model.safetensors",
      derr is None and decl["hosts"] == ["huggingface.co"] and decl["max_redirects"] == 0 and decl["repo"] == REPO_
      and decl["revision"] == REV_ and decl["path"] == "model.safetensors", str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "url": "https://huggingface.co/x"},
    "another host": {**REAL, "hosts": ["huggingface.co", CDN]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a revision that is no commit": {**REAL, "revision": "main"},
    "a path that climbs": {**REAL, "path": "../model.safetensors"},
    "an absolute path": {**REAL, "path": "/model.safetensors"},
    "a path with a query": {**REAL, "path": "model.safetensors?download=1"},
    "a repository that climbs": {**REAL, "repo": "../all-MiniLM-L6-v2"},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d7_decl({"windows": {"a7-hf-d": entry}}))
    refused[label] = bool(e) and e.startswith("D7ManifestError")
check("HFD-2: an entry with anything but the declared shape is refused by name (D7ManifestError) - " + ", ".join(bad_cases),
      bool(REAL) and all(refused.values()), str([k for k, v in refused.items() if not v]))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d7_"))
HF = "huggingface.co"
made = TF.make_test_cert(TMP / "cert", HF, org="Amazon")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 hf-d: {PASSED} passed, {FAILED} failed")
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


DECL = {"hosts": ["huggingface.co"], "purpose": "test", "repo": REPO_, "revision": REV_, "path": "model.safetensors",
        "max_redirects": 0}


def window(run: str, route: tuple):
    srv = TF.TlsHttpServer(made[0], made[1], {PATH_: route})
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d7_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-hf-d", hosts=["huggingface.co"], jobs=jobs, python=Path(sys.executable), via_port=hop.port, run=run,
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    heads = list(srv.heads)
    rep, perr = holds(lambda: F.d7_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return heads, rec, rep, perr


def offline(summary: dict | None):
    """d7_report on a record whose job 0 left this request summary (or none), no window."""
    rec = {"jobs": [{"index": 0, "unit": str(TMP), "summary": [] if summary is None else [summary]}]}
    return holds(lambda: F.d7_report(rec, DECL))


try:
    print("\n- the window, offline -")
    heads, rec, rep, perr = window("t1", (302, [("Location", f"https://{CDN}/xet-bridge-us/abc/def?{SIG}")], b""))
    check("HFD-3: exactly one request, a HEAD on the file's resolve URL at the revision - the redirect not followed, "
          "nothing downloaded", perr is None and [h.split(b" ")[:2] for h in heads] == [[b"HEAD", PATH_.encode()]],
          f"{perr} {heads}")
    rep = rep or {}
    check("HFD-4: the report names the status and the redirect's host, with no problem; the signed query is kept "
          "nowhere (report, record)", rep.get("status") == 302 and rep.get("redirect_host") == CDN
          and rep.get("problems") == [] and "Signature" not in json.dumps(rep) and "Signature" not in json.dumps(rec, default=str),
          json.dumps(rep)[:400])
    check("HFD-5: the window's check is complete, no native hit, no problem", rec is not None and rec["check"]["complete"]
          and rec["check"]["native_hits"] == 0 and rec["problems"] == [], str((rec or {}).get("problems")))
    print("\n- the report's reading -")
    r6, e6 = offline({"id": "head:model", "status": 200, "final_host": HF, "redirect_host": None, "ok": True, "error": None})
    check("HFD-6: huggingface.co answering the file itself is no problem - the redirect host None, said so",
          e6 is None and r6["status"] == 200 and r6["redirect_host"] is None and r6["problems"] == []
          and "served by huggingface.co itself" in r6["note"], str(e6 or r6))
    r7, e7 = offline({"id": "head:model", "status": 404, "final_host": HF, "redirect_host": None, "ok": False,
                      "error": "Refused: status 404"})
    r7b, e7b = offline(None)
    check("HFD-7: a failed HEAD, and a job that left no answer, are problems by name",
          e7 is None and any("the HEAD failed: Refused: status 404" in p for p in r7["problems"])
          and e7b is None and any("no answer" in p for p in r7b["problems"]), str((e7 or r7, e7b or r7b)))
    r7c, e7c = offline({"id": "head:model", "status": 302, "final_host": HF, "redirect_host": None, "ok": True, "error": None})
    check("HFD-7b: a redirect with no host to record is a problem by name",
          e7c is None and any("no redirect host" in p for p in r7c["problems"]), str(e7c or r7c))

    print("\n- main(): plan d7 only in window a7-hf-d, on its declared entry - refused before any spawn -")

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
    m1 = run_main(["--window", "a7-hf-d", "--plan", "d6", *tail], MANI)
    m2 = run_main(["--window", "a7-arxiv", "--plan", "d7", *tail], MANI)
    check("HFD-8: plan d7 outside window a7-hf-d, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d7 runs in window a7-hf-d" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-hf-d", {})["max_redirects"] = 1
    m3 = run_main(["--window", "a7-hf-d", "--plan", "d7", *tail], other)
    m4 = run_main(["--window", "a7-hf-d", "--plan", "d7", *tail], MANI)
    check("HFD-9: a manifest whose a7-hf-d entry is not the declared shape is refused (rc 2, named); the real one gets to "
          "the window", m3[0] == 2 and "a7-hf-d" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 hf-d: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
