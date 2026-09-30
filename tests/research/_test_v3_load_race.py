#!/usr/bin/env python3
"""B-LOAD-RACE (the auditor; R-SMOKE-ATTR): a lazy loader that puts a module into sys.modules before its exec_module
ends hands a second thread a half-made module - "AttributeError: module 'v3_llm_proxy_for_plan' has no attribute
'request_key'" once in three smokes, when two question-stage threads made the Answerer's first call at once.

* the census: every loader in research/ that assigns sys.modules[...] before exec_module in the same scope is either
  under its file's _LOAD_LOCK or listed in NOT_FROM_THREADS with the caller that makes it single-threaded; an
  unlisted loader, and a listed one that is gone, fail by name. Its limit, said plainly: it reads the loaders, not the
  call graph - a listed loader that someone later calls from a pool is not seen here; only review catches that.
* the race: five threads, a slow exec - run_v3_plan._load and scheduler._arm_base (the two loaders the scheduler's
  write and question pools reach) hand every thread the whole module, never a half.
* a failed exec: the key goes out of sys.modules again - the next call loads (or fails) anew, never returns a half.
  scheduler's three loaders go through its one locked _load; _status_log and _artifact are the main thread's and share
  it for the file's uniformity only (no row and no mutant of their own: by the call graph one would be equivalent).

    python tests/research/_test_v3_load_race.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PL = _load("v3_run_plan_for_load_race_t", ROOT / "research" / "v3" / "run_v3_plan.py")
SC = _load("v3_scheduler_for_load_race_t", ROOT / "research" / "v3" / "scheduler.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


#: the loaders the pools reach, each under its file's _LOAD_LOCK (scheduler's _arm_base, _status_log and _artifact all
#: go through its one _load: the last two are the main thread's and share it for the file's uniformity)
LOCKED = {"research/v3/run_v3_plan.py::_load", "research/v3/scheduler.py::_load"}

_CLI = "a one-shot command-line tool; its process starts no thread"
#: every other loader, with the caller that keeps it to one thread (B-LOAD-RACE, the auditor's census)
NOT_FROM_THREADS = {
    "research/_idle_probe.py::_launch": "main() and append_chained from main(); the sampler thread (_run) loads nothing",
    "research/_llm_proxy.py::_embed_tokenizer": "Proxy.__init__, once, before any handler thread starts",
    "research/_llm_proxy_selftest.py::load_proxy": "the selftest's own flow, before its fake upstream serves",
    "research/blast_radius_calibration.py::_load_checker": "a research script; no thread",
    "research/blast_radius_precision.py::_load_checker": "a research script; no thread",
    "research/invariants_lab/facade_shapes.py::<module>": "module level, at import",
    "research/invariants_lab/measure_blast_radius.py::<module>": "module level, at import",
    "research/v3/ab_harness.py::_rv": "the A/B harness's main flow; its legs run one after another",
    "research/v3/ab_rule.py::hop_benchmark": "ab_harness.run (main flow) and ab_rule's __main__; its fake hop's "
                                             "threads (_loop, _conn) load nothing",
    "research/v3/arms/runner_nevertwice.py::_load": "bind(), in the arm's own child process, from its protocol loop",
    "research/v3/bin_install.py::_load": _CLI,
    "research/v3/capture_deepseek.py::_load_launch": _CLI,
    "research/v3/d1_tag.py::main": _CLI,
    "research/v3/dataset_facts.py::_load": _CLI,
    "research/v3/fetch_a3.py::_load": _CLI,
    "research/v3/fetch_npm_a7.py::_load": _CLI,
    "research/v3/fetch_npm_discovery_a7.py::_load": _CLI,
    "research/v3/fetch_pins_a3.py::_load": _CLI,
    "research/v3/fetch_py_base.py::_load_launch": _CLI,
    "research/v3/freeze_a3.py::_load": _CLI,
    "research/v3/freeze_a7.py::_load": _CLI,
    "research/v3/image_install.py::_iv": _CLI,
    "research/v3/image_install.py::_load": _CLI,
    "research/v3/install_v3_data.py::_load": _CLI,
    "research/v3/judge_prompts.py::_sibling": "module level (TP = _sibling('templates')), at import",
    "research/v3/lists_build.py::_load": _CLI,
    "research/v3/loaders.py::_render": "_ama_unit, building the stand's units before the scheduler starts",
    "research/v3/loaders.py::_smoke_rules": "s7_smoke, building the stand's units before the scheduler starts",
    "research/v3/loaders.py::read_pinned": "run_v3.real_smoke_deps' lme_records and the loaders, before the stand",
    "research/v3/lock_install.py::_iv": _CLI,
    "research/v3/lock_install.py::_fpb": _CLI,
    "research/v3/lock_install.py::_load": _CLI,
    "research/v3/ls1.py::_load": _CLI,
    "research/v3/m31_check.py::_load": _CLI,
    "research/v3/model_install.py::_load": _CLI,
    "research/v3/ollama_inventory.py::main": _CLI,
    "research/v3/pins_apply.py::_load_table": _CLI,
    "research/v3/place_local_v2.py::_load": _CLI,
    "research/v3/probe_a8.py::_mod": _CLI,
    "research/v3/run_v3.py::load": "run_smoke / main / probe() setup, the main thread before the pools and the gate "
                                   "thread start (probe's send() closure loads nothing)",
    "research/v3/run_v3_gate.py::_load": "_incidents() in GateDriver.poll - the one gate thread, and stop() after its "
                                         "join: never two at once",
    "research/v3/run_v3_hooks.py::_load": "Hooks.tree_check / model_probe / preflight, the stand's main thread",
    "research/v3/run_v3_proxy.py::_load": "build_config / build_secrets / start, the main thread",
    "research/v3/run_v3_smoke.py::_load": "summarize after the stand, the main thread",
    "research/v3/sched_ctl.py::_scheduler": "module level (STAGES), at import",
    "research/v3/status_log.py::_file_lock": "StatusLog.__init__, the main thread",
    "research/v3/templates.py::_cp": "stand_template / freeze_fragment, before the stand",
    "research/v3/templates.py::_locomo_category": "locomo_question, building the questions before the stand",
}


def loader_sites() -> dict[str, bool]:
    """{"<file>::<function or <module>>": under a _LOAD_LOCK} - every scope in research/ that assigns sys.modules[...]
    and calls exec_module."""
    out: dict[str, bool] = {}
    for p in sorted((ROOT / "research").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8")
        if "exec_module" not in text:
            continue
        tree = ast.parse(text)
        parent = {ch: node for node in ast.walk(tree) for ch in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript)
                                                         and ast.unparse(t.value) == "sys.modules" for t in node.targets)):
                continue
            fn, locked, a = None, False, node
            while a in parent:
                a = parent[a]
                if isinstance(a, ast.With) and any("_LOAD_LOCK" in ast.unparse(i.context_expr) for i in a.items):
                    locked = True
                if fn is None and isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = a
            if "exec_module" not in (ast.unparse(fn) if fn is not None else text):
                continue
            key = f"{rel}::{fn.name if fn is not None else '<module>'}"
            out[key] = out.get(key, True) and locked
    return out


print("- the census: every sys.modules-before-exec loader is locked or listed with its caller -")
sites = loader_sites()
unlisted = sorted(k for k, locked in sites.items() if not locked and k not in NOT_FROM_THREADS)
dead = sorted(k for k in NOT_FROM_THREADS if k not in sites)
check("B-LOAD-RACE census: every loader in research/ that puts a module into sys.modules before its exec_module is "
      "under its file's _LOAD_LOCK or listed as not from threads, with its caller", not unlisted and len(sites) >= 40,
      f"{len(sites)} sites; unlocked and unlisted: {unlisted}")
check("B-LOAD-RACE census: no dead line - every loader listed as not from threads still exists", not dead, str(dead))
check("B-LOAD-RACE census: the loaders the pools reach are locked (and listed nowhere else)",
      all(sites.get(k) is True for k in LOCKED) and not LOCKED & set(NOT_FROM_THREADS),
      str({k: sites.get(k) for k in sorted(LOCKED)}))


print("\n- the race: five threads, a slow load, one whole module each, executed once -")
TMP = Path(tempfile.mkdtemp(prefix="nvt3_load_race_"))
(TMP / "slow.py").write_text("import sys\nimport time\nsys.__dict__.setdefault('_nvt3_load_race_execs', []).append(__name__)\n"
                             "time.sleep(0.6)\ndef request_key(body, arm):\n    return 'k'\n", encoding="utf-8")
(TMP / "boom.py").write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
real_spec = importlib.util.spec_from_file_location
PRE_S, EXEC_S, WAVE_S = 0.2, 0.6, 0.4       # spec made (before the insert) - exec (after it) - the second wave's start


def in_waves(fn, first: int = 2, then: int = 3) -> tuple[list, list]:
    """``fn`` from ``first`` threads at once, then from ``then`` more WAVE_S later. The first wave reads the name before
    anything is inserted (a lookup outside the lock then loads it once per thread: copies, the auditor's M1); the
    second comes after the insert and before the exec ends (an unlocked lookup then hands out the half-made module:
    R-SMOKE-ATTR's AttributeError). Under the lock neither can happen, whatever the timing."""
    got, errs = [], []

    def one():
        try:
            got.append(fn())
        except Exception as e:  # noqa: BLE001 - counted, the row names it
            errs.append(f"{type(e).__name__}: {e}")
    ts = [threading.Thread(target=one) for _ in range(first + then)]
    for t in ts[:first]:
        t.start()
    time.sleep(WAVE_S)
    for t in ts[first:]:
        t.start()
    for t in ts:
        t.join(timeout=60)
    return got, errs


def slowed(name: str, *, fail: bool = False, execs: list | None = None, exec_s: float = EXEC_S):
    """importlib.util.spec_from_file_location that, for ``name``, takes PRE_S to make the spec (before the loader's
    insert) and whose exec sleeps ``exec_s`` first (or raises); each exec counted in ``execs``."""
    def spec_for(n, *a, **k):
        spec = real_spec(n, *a, **k)
        if n == name:
            time.sleep(PRE_S)
            exec_ = spec.loader.exec_module

            def slow(m):
                if execs is not None:
                    execs.append(n)
                time.sleep(exec_s)
                if fail:
                    raise RuntimeError("the exec failed")
                exec_(m)
            spec.loader.exec_module = slow
        return spec
    return spec_for


def whole(load, attr: str):
    """The module ``load`` gives - its ``attr`` read in the thread, as the Answerer reads request_key."""
    m = load()
    getattr(m, attr)
    return m


PN = "v3_slow_for_load_race_plan"
importlib.util.spec_from_file_location = slowed(PN, exec_s=0.0)       # the module itself sleeps EXEC_S in its exec
try:
    got, errs = in_waves(lambda: whole(lambda: PL._load(PN, TMP / "slow.py"), "request_key"))
finally:
    importlib.util.spec_from_file_location = real_spec
execs = [n for n in getattr(sys, "_nvt3_load_race_execs", []) if n == PN]
check("B-LOAD-RACE: run_v3_plan._load from five threads, a slow load - every thread gets the whole module (reads the "
      "Answerer's request_key in the thread), no AttributeError", len(got) == 5 and not errs,
      f"{len(got)} ok, errors {errs[:2]}")
check("B-LOAD-RACE (the auditor's M1p): ... and the module is executed exactly once - all five threads hold the same "
      "object (a lookup outside the lock re-runs the exec and hands out copies)",
      len(execs) == 1 and len(got) == 5 and all(m is got[0] for m in got),
      f"execs={len(execs)} distinct objects={len({id(m) for m in got})}")

AB = "v3_arm_base_for_scheduler"
old_ab = sys.modules.pop(AB, None)
ab_execs: list = []
importlib.util.spec_from_file_location = slowed(AB, execs=ab_execs)
try:
    got, errs = in_waves(lambda: whole(SC._arm_base, "ArmClient"))
finally:
    importlib.util.spec_from_file_location = real_spec
    fresh_ab = sys.modules.pop(AB, None)
    if old_ab is not None:
        sys.modules[AB] = old_ab
check("B-LOAD-RACE: scheduler._arm_base from five threads, a slow load - every thread gets the whole module (reads "
      "ArmClient in the thread), no AttributeError", len(got) == 5 and not errs and fresh_ab is not None,
      f"{len(got)} ok, errors {errs[:2]}")
check("B-LOAD-RACE (the auditor's M1s): ... and arms/base.py is executed exactly once - all five threads hold the same "
      "module, so an ArmError raised in one is the class every other thread's except names",
      len(ab_execs) == 1 and len(got) == 5 and all(m is got[0] for m in got) and got[0] is fresh_ab,
      f"execs={len(ab_execs)} distinct objects={len({id(m) for m in got})}")


print("\n- a failed exec takes its half-made module out again -")
first = second = None
try:
    PL._load("v3_boom_for_load_race_plan", TMP / "boom.py")
except RuntimeError as e:
    first = str(e)
left = "v3_boom_for_load_race_plan" in sys.modules
try:
    second = PL._load("v3_boom_for_load_race_plan", TMP / "boom.py")
except RuntimeError as e:
    second = str(e)
check("B-LOAD-RACE: run_v3_plan._load whose exec raises leaves nothing in sys.modules - the next call runs the exec "
      "again and fails the same way, never returns a half-made module",
      first == "boom at import" and not left and second == "boom at import", f"{first!r} left={left} {second!r}")

old_ab = sys.modules.pop(AB, None)
importlib.util.spec_from_file_location = slowed(AB, fail=True)
r1 = r2 = None
try:
    try:
        SC._arm_base()
    except RuntimeError as e:
        r1 = str(e)
    left = AB in sys.modules
    try:
        r2 = SC._arm_base()
    except RuntimeError as e:
        r2 = str(e)
finally:
    importlib.util.spec_from_file_location = real_spec
    sys.modules.pop(AB, None)
    if old_ab is not None:
        sys.modules[AB] = old_ab
check("B-LOAD-RACE: scheduler._arm_base whose exec raises leaves nothing in sys.modules - the next call fails again, "
      "never returns a half-made module", r1 == "the exec failed" and not left and r2 == "the exec failed",
      f"{r1!r} left={left} {r2!r}")

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 load race: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
