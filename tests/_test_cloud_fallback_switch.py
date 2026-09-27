#!/usr/bin/env python3
"""PREREG-V3 TB3(a): the cloud-fallback switch - NEVERTWICE_CLOUD_FALLBACK, off and counted in v3 runs.

The shipped engine falls back to local Ollama whenever the cloud extraction call fails, and skips straight to Ollama
once the per-run circuit breaker has marked the cloud dead. A v3 run pins one LLM per stand, so a silent local
fallback would mix two writers in one arm. With NEVERTWICE_CLOUD_FALLBACK=0:
* a failed cloud call returns {} and is counted (fail, fallback_refused) - Ollama is never called;
* a tripped breaker returns {} and is counted (fail, breaker_skips), its failure slug "cloud_dead";
* the cloud's own failure slug (e.g. "truncated") survives the refusal, so a content failure is still bounded by
  the retry rule and a transport failure still waits;
* Ollama as the PRIMARY backend (no cloud key, or a local-only project) is unchanged - the switch refuses only the fallback.
Unset (or "1") keeps the shipped behaviour for every user. The counters start at a measured 0.

No socket is opened: call_cloud and call_ollama are replaced in the engine's own namespace.

    python tests/_test_cloud_fallback_switch.py
"""
import os
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


OK = {"patterns": [], "mistakes": [], "decisions": [], "session_summary": "s"}


def run(*, switch: str | None, cloud_result: dict | None = None, cloud_slug: str | None = None, dead: bool = False,
        key: bool = True, ollama_result: dict | None = None, local_only: bool = False) -> tuple[dict, dict, list, dict]:
    """generate_json once; returns (result, stats delta, calls, _LLM_LAST)."""
    calls: list[str] = []

    def fake_cloud(prompt):
        calls.append("cloud")
        if cloud_slug is not None:
            m._LLM_LAST["failure"] = cloud_slug
        return dict(cloud_result or {})

    def fake_ollama(prompt):
        calls.append("ollama")
        m._LLM_LAST["failure"] = None if ollama_result else "transport"
        return dict(ollama_result or {})

    env = {k: v for k, v in os.environ.items() if k != "NEVERTWICE_CLOUD_FALLBACK"}
    if switch is not None:
        env["NEVERTWICE_CLOUD_FALLBACK"] = switch
    before = dict(m._LLM_STATS)
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(m, "call_cloud", fake_cloud), \
            mock.patch.object(m, "call_ollama", fake_ollama), mock.patch.object(m, "cloud_key", lambda: "k" if key else ""), \
            mock.patch.object(m, "ACTIVE_CLOUD", "deepseek"), mock.patch.object(m, "_CLOUD_DEAD", dead), \
            mock.patch.object(m, "_OLLAMA_DOWN", False), mock.patch.object(m, "log", lambda *a, **k: None), \
            mock.patch.object(m, "is_local_only", lambda project: local_only):
        m._LLM_LAST["failure"] = None
        res = m.generate_json("p", project=None)
        last = dict(m._LLM_LAST)
    delta = {k: m._LLM_STATS.get(k, 0) - before.get(k, 0) for k in set(m._LLM_STATS) | set(before)}
    return res, {k: v for k, v in delta.items() if v}, calls, last


print("\n- the counters start at a measured 0 -")
check("_LLM_STATS carries fallback_refused and breaker_skips from the start (an artifact reads a measured 0)",
      {"cloud", "ollama", "fail", "fallback_refused", "breaker_skips"} <= set(m._LLM_STATS), str(m._LLM_STATS))

print("\n- unset: the shipped behaviour -")
r, d, c, _ = run(switch=None, cloud_result=None, ollama_result=OK)
check("1 unset, the cloud fails: Ollama is called once and its result returned (ollama +1)",
      c == ["cloud", "ollama"] and r == OK and d == {"ollama": 1}, str((c, d)))
r, d, c, _ = run(switch=None, cloud_result=OK)
check("2 unset, the cloud answers: no Ollama call (cloud +1)", c == ["cloud"] and r == OK and d == {"cloud": 1}, str((c, d)))

print("\n- NEVERTWICE_CLOUD_FALLBACK=0 -")
r, d, c, last = run(switch="0", cloud_result=None, cloud_slug="http", ollama_result=OK)
check("3 off, the cloud fails: Ollama is NOT called, {} returned, fail +1 and fallback_refused +1",
      c == ["cloud"] and r == {} and d == {"fail": 1, "fallback_refused": 1}, str((c, r, d)))
r, d, c, last = run(switch="0", dead=True, ollama_result=OK)
check("4 off, the breaker already tripped: neither backend is called, fail +1, breaker_skips +1, the slug cloud_dead",
      c == [] and r == {} and d == {"fail": 1, "breaker_skips": 1} and last["failure"] == "cloud_dead", str((c, d, last)))
r, d, c, last = run(switch="0", cloud_result=None, cloud_slug="truncated", ollama_result=OK)
check("5 off, a content failure: the cloud's own slug (truncated) survives the refusal",
      last["failure"] == "truncated" and c == ["cloud"], str(last))
r, d, c, _ = run(switch="0", cloud_result=OK)
check("6 off, the cloud answers: unchanged (cloud +1, no Ollama)", c == ["cloud"] and r == OK and d == {"cloud": 1}, str((c, d)))
r, d, c, _ = run(switch="0", key=False, ollama_result=OK)
check("7 off with no cloud key: Ollama as the PRIMARY backend is unchanged - the switch refuses only the fallback",
      c == ["ollama"] and r == OK and d == {"ollama": 1}, str((c, d)))
r, d, c, _ = run(switch="0", local_only=True, cloud_result=OK, ollama_result=OK)
check("7b off for a local-only project: the cloud is never touched, Ollama stays its only backend",
      c == ["ollama"] and r == OK and d == {"ollama": 1}, str((c, d)))

print("\n- the value -")
off = [v for v in ("0", "false", "off", "no", "FALSE", " 0 ") if not m.cloud_fallback_enabled_value(v)]
on = [v for v in (None, "", "1", "true", "on", "yes") if m.cloud_fallback_enabled_value(v)]
check("8 '0', 'false', 'off', 'no' (any case, trimmed) turn the fallback off; unset, empty, '1', 'true' keep it on",
      len(off) == 6 and len(on) == 6, str((off, on)))

print(f"\ncloud fallback switch: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
