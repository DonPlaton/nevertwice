#!/usr/bin/env python3
"""Recall-behavior contracts added in 2.2:

1. xrerank auto-resolution: an explicit NEVERTWICE_XRERANK=1/0 always wins; unset means
   "on exactly when the [reranker] extra is installed" (probed via find_spec, no import).
2. PreCompact resets the per-session recall dedup: compaction wipes injected notes out
   of the agent context, so the "already shown" state must go with them - while a plain
   SessionEnd leaves the state alone (the session is over; resume still has its context).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "nevertwice"
sys.path.insert(0, str(PKG))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import reranker_ce as rc        # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    print(("  ok  " if cond else "  FAIL ") + name)
    P += 1 if cond else 0
    F += 0 if cond else 1


def test_xrerank_resolution():
    with mock.patch.dict(os.environ, {"NEVERTWICE_XRERANK": "1"}):
        check("explicit 1 forces on (even with no deps)", rc.enabled() is True)
    with mock.patch.dict(os.environ, {"NEVERTWICE_XRERANK": "0"}):
        check("explicit 0 forces off", rc.enabled() is False)
    with mock.patch.dict(os.environ, {"NEVERTWICE_XRERANK": "auto"}), \
         mock.patch.object(rc.importlib.util, "find_spec", lambda name: None):
        check("auto + extra not installed -> off", rc.enabled() is False)
    with mock.patch.dict(os.environ, {"NEVERTWICE_XRERANK": "auto"}), \
         mock.patch.object(rc.importlib.util, "find_spec", lambda name: object()), \
         mock.patch.object(rc, "_model_cached", lambda: False):
        check("auto + deps present but model not downloaded -> off (no surprise 2 GB)",
              rc.enabled() is False)
    with mock.patch.dict(os.environ, {"NEVERTWICE_XRERANK": "auto"}), \
         mock.patch.object(rc.importlib.util, "find_spec", lambda name: object()), \
         mock.patch.object(rc, "_model_cached", lambda: True):
        check("auto + deps + model cached -> on", rc.enabled() is True)
    env_no = {k: v for k, v in os.environ.items() if k != "NEVERTWICE_XRERANK"}
    with mock.patch.dict(os.environ, env_no, clear=True), \
         mock.patch.object(rc.importlib.util, "find_spec", lambda name: object()), \
         mock.patch.object(rc, "_model_cached", lambda: True):
        check("unset behaves as auto", rc.enabled() is True)


def _hook(evt, env):
    return subprocess.run([sys.executable, str(PKG / "memory_hook.py")],
                          input=json.dumps(evt), capture_output=True, text=True,
                          env=env, timeout=120)


def test_precompact_resets_dedup_state():
    with tempfile.TemporaryDirectory() as t:
        vault = Path(t) / "store"
        env = {k: v for k, v in os.environ.items()
               if not any(s in k for s in ("CEREBRAS", "GROQ", "DEEPSEEK", "GEMINI", "OPENAI"))}
        env.update({"NEVERTWICE_HOME": str(vault), "NEVERTWICE_CLOUD": "none",
                    "OLLAMA_URL": "http://127.0.0.1:1"})
        state_dir = vault / ".prompt_recall"
        state_dir.mkdir(parents=True)
        (state_dir / "sess_pc.json").write_text('{"injected": ["a"], "count": 3}',
                                                encoding="utf-8")
        (state_dir / "sess_end.json").write_text('{"injected": ["b"], "count": 1}',
                                                 encoding="utf-8")
        r1 = _hook({"hook_event_name": "PreCompact", "session_id": "sess_pc", "cwd": t}, env)
        check("PreCompact hook exits 0", r1.returncode == 0)
        check("PreCompact dropped the session's dedup state",
              not (state_dir / "sess_pc.json").exists())
        r2 = _hook({"hook_event_name": "SessionEnd", "session_id": "sess_end", "cwd": t}, env)
        check("SessionEnd exits 0", r2.returncode == 0)
        check("SessionEnd keeps the dedup state (resume still has its context)",
              (state_dir / "sess_end.json").exists())


if __name__ == "__main__":
    print("=== recall-behavior self-checks ===")
    test_xrerank_resolution()
    test_precompact_resets_dedup_state()
    print(f"\nrecall-behavior: {P} passed, {F} failed")
    sys.exit(1 if F else 0)
