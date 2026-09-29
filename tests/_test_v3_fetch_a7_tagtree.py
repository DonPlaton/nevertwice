#!/usr/bin/env python3
"""PREREG-V3 A7 phase 2 (the auditor's R2 on cognee's BEAM harness): research/v3/fetch_a3.py plan d5 - the a7-cognee-tag
window, metadata only - offline, with real processes under the contract (the catcher-only proxy, the fetch-child jobs,
a fake api.github.com behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-cognee-tag" entry, the single source: one host (api.github.com), no redirect, the
  repository, the tag the pinned product carries, the commit the discovery says the tag names, and the selection (path
  prefixes and single files) - anything else in the entry is refused before any spawn;
* job 1 asks which commit the tag names now; job 2, the tag's recursive tree at the declared commit, runs ONLY when the
  tag still names it - a tag that moved is a problem by name and no tree is read; nothing else is requested;
* the report: the tag's commit and whether it is the declared one, the tree's size and truncation, the selected blobs
  (path, blob sha1, size) and the declared single files the tag does not hold - never a guess at HEAD.

    python tests/_test_v3_fetch_a7_tagtree.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
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


L = _load("v3_launch_d5", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d5", ROOT / "research" / "v3" / "fetch_a3.py")
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
REAL = MANI["windows"].get("a7-cognee-tag") or {}
print("- the manifest's a7-cognee-tag entry (the auditor's R2) -")
decl, derr = holds(lambda: F.d5_decl(MANI))
check("the manifest declares a7-cognee-tag as plan d5 reads it: api.github.com only, no redirect, topoteretes/cognee at "
      "the tag v1.6.1 the pinned cognee==1.6.1 carries, the commit eb90d037 the discovery says it names, the BEAM "
      "harness selection (cognee/eval_framework/beam/ and three single files)", derr is None
      and decl["hosts"] == ["api.github.com"] and decl["max_redirects"] == 0 and decl["repo"] == "topoteretes/cognee"
      and decl["tag"] == "v1.6.1" and decl["commit"] == "eb90d03740755f5252b8b12cce91fd09970f2d81"
      and decl["prefixes"] == ["cognee/eval_framework/beam/"]
      and decl["files"] == ["cognee/eval_framework/benchmark_adapters/beam_adapter.py",
                            "cognee/eval_framework/answer_generation/beam_router.py",
                            "cognee/eval_framework/run_beam_eval.py"], str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "follow": True},
    "another host": {**REAL, "hosts": ["api.github.com", "github.com"]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a commit that is a branch": {**REAL, "commit": "main"},
    "a tag with a slash": {**REAL, "tag": "../v1.6.1"},
    "a prefix without its slash": {**REAL, "prefixes": ["cognee/eval_framework/beam"]},
    "a prefix going up": {**REAL, "prefixes": ["../"]},
    "a file with a query": {**REAL, "files": ["run_beam_eval.py?ref=main"]},
    "no selection at all": {**REAL, "prefixes": [], "files": []},
    "a repository that is no name": {**REAL, "repo": "topoteretes"},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d5_decl({"windows": {"a7-cognee-tag": entry}}))
    refused[label] = bool(e) and e.startswith("D5ManifestError")
check("DF: an entry with anything but the declared shape is refused by name (D5ManifestError) - "
      + ", ".join(bad_cases), all(refused.values()), str([k for k, v in refused.items() if not v]))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d5_"))
GH = "api.github.com"
made = TF.make_test_cert(TMP / "cert", GH, org="Sectigo Limited")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 tag tree: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

REPO, TAG, C1, C2 = "topoteretes/cognee", "v1.6.1", "1" * 40, "2" * 40
DECL = {"hosts": ["api.github.com"], "purpose": "test", "repo": REPO, "tag": TAG, "commit": C1,
        "prefixes": ["cognee/eval_framework/beam/"],
        "files": ["cognee/eval_framework/run_beam_eval.py", "cognee/eval_framework/answer_generation/beam_router.py"],
        "max_redirects": 0}
J = lambda o, st=200: (st, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
TREE = {"sha": "t" * 40, "truncated": False, "tree": [
    {"path": "cognee/eval_framework/beam", "type": "tree", "sha": "3" * 40},
    {"path": "cognee/eval_framework/beam/run_sweep.py", "type": "blob", "sha": "4" * 40, "size": 111},
    {"path": "cognee/eval_framework/beam/eval/registry.py", "type": "blob", "sha": "5" * 40, "size": 222},
    {"path": "cognee/eval_framework/run_beam_eval.py", "type": "blob", "sha": "6" * 40, "size": 333},
    {"path": "cognee/eval_framework/beamish.py", "type": "blob", "sha": "7" * 40, "size": 1},        # not the prefix
    {"path": "cognee/api/v1/add.py", "type": "blob", "sha": "8" * 40, "size": 2}]}


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def window(run: str, tag_commit: str):
    """One offline window: the fake answers the tag with ``tag_commit`` and holds the tree at C1 only."""
    routes = {f"/repos/{REPO}/commits/{TAG}": J({"sha": tag_commit, "commit": {"message": "release"}}),
              f"/repos/{REPO}/git/trees/{C1}?recursive=1": J(TREE)}
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d5_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-cognee-tag", hosts=F.D5_HOSTS, jobs=jobs, python=Path(sys.executable), via_port=hop.port,
        run=run, parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    rep, perr = holds(lambda: F.d5_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return asked, rec, rep, perr


try:
    print("\n- the window, offline: the tag still names the declared commit -")
    asked, rec, rep, perr = window("t1", C1)
    check("D5-1: exactly the declared requests - the tag's commit, then the recursive tree at the DECLARED commit; "
          "nothing else", perr is None and asked == [f"/repos/{REPO}/commits/{TAG}",
                                                    f"/repos/{REPO}/git/trees/{C1}?recursive=1"], f"{perr} {asked}")
    rep = rep or {}
    check("D5-2: the report names the tag's commit and that it is the declared one; the tree read whole",
          rep.get("tag_commit") == C1 and rep.get("tag_matches") is True and rep.get("tree_entries") == 6
          and rep.get("tree_truncated") is False, json.dumps({k: rep.get(k) for k in (
              "tag_commit", "tag_matches", "tree_entries", "tree_truncated")}))
    check("D5-3: the selection - every blob under the prefix and each declared single file the tag holds, with its blob "
          "sha1 and size, in path order; a near name outside the prefix and the rest of the tree are not selected",
          rep.get("selected") == [{"path": "cognee/eval_framework/beam/eval/registry.py", "blob": "5" * 40, "size": 222},
                                  {"path": "cognee/eval_framework/beam/run_sweep.py", "blob": "4" * 40, "size": 111},
                                  {"path": "cognee/eval_framework/run_beam_eval.py", "blob": "6" * 40, "size": 333}],
          json.dumps(rep.get("selected")))
    check("D5-4: a declared single file the tag does not hold is named, never looked for at HEAD",
          rep.get("missing_files") == ["cognee/eval_framework/answer_generation/beam_router.py"]
          and rep.get("problems") == [], json.dumps({k: rep.get(k) for k in ("missing_files", "problems")}))
    check("D5-5: the window's check is complete with no native hit and no problem",
          rec is not None and rec["check"]["complete"] and rec["check"]["native_hits"] == 0 and rec["problems"] == [],
          str((rec or {}).get("problems")))

    print("\n- the tag moved: it names another commit -")
    asked2, rec2, rep2, perr2 = window("t2", C2)
    check("D5-6: a tag that names another commit is a problem by name, and NO tree is read (the declared commit is not "
          "silently replaced)", perr2 is None and asked2 == [f"/repos/{REPO}/commits/{TAG}"]
          and (rep2 or {}).get("tag_matches") is False and (rep2 or {}).get("selected") == []
          and any(TAG in p and C2 in p for p in (rep2 or {}).get("problems") or []), f"{perr2} {asked2} {rep2}")
    trunc = {"jobs": [{"index": 0, "unit": str(TMP / "tr0"), "summary": []},
                      {"index": 1, "unit": str(TMP / "tr1"), "summary": []}]}
    (TMP / "tr0").mkdir()
    (TMP / "tr1").mkdir()
    (TMP / "tr0" / "tag_commit.json").write_text(json.dumps({"sha": C1}))
    (TMP / "tr1" / "tree.json").write_text(json.dumps({**TREE, "truncated": True}))
    rt, rterr = holds(lambda: F.d5_report(trunc, DECL))
    check("D5-7: a truncated tree is a problem by name - a selection from part of a tree is no selection",
          rterr is None and any("truncated" in p for p in rt["problems"]), f"{rterr} {rt}")

    print("\n- main(): plan d5 only in window a7-cognee-tag, on its declared entry - refused before any spawn -")
    import contextlib  # noqa: E402,PLC0415
    import io  # noqa: E402,PLC0415
    from types import SimpleNamespace  # noqa: E402,PLC0415

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
    m1 = run_main(["--window", "a7-cognee-tag", "--plan", "d4", *tail], MANI)
    m2 = run_main(["--window", "a7-docs", "--plan", "d5", *tail], MANI)
    check("D5-8: plan d5 outside window a7-cognee-tag, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d5 runs in window a7-cognee-tag" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-cognee-tag", {})["hosts"] = ["api.github.com", "github.com"]
    m3 = run_main(["--window", "a7-cognee-tag", "--plan", "d5", *tail], other)
    m4 = run_main(["--window", "a7-cognee-tag", "--plan", "d5", *tail], MANI)
    check("D5-9: a manifest whose a7-cognee-tag entry is not the declared shape is refused (rc 2, named); the real one "
          "gets to the window", m3[0] == 2 and "a7-cognee-tag" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 tag tree: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
