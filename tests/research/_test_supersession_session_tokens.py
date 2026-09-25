#!/usr/bin/env python3
"""(б) C4: supersession's per-session token cost is read off the engine that ran the capture.

`run_nevertwice` took its `_LLM_STATS` snapshots from a bare `import memory_hook` - a second engine
object, not `api.m`, whose counters no capture ever moved: all 320 `session_tokens` entries of
supersession_v1.json (fe6ddff) are 0/0, while code_sessions_eval, which reads `api.m`, has 148 of
150 non-zero. No registered claim reads them, but PREREG-V3's token-efficiency axis (T13) would
have read zero for our arm.

Here the running engine's model call is faked (no network): each call adds a known token count to
`api.m._LLM_STATS`, exactly where the real `call_ollama` adds Ollama's `prompt_eval_count`.

    python tests/research/_test_supersession_session_tokens.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402
import supersession_bench as sb  # noqa: E402
from nevertwice import api  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


PROMPT_T, EVAL_T = 120, 9


def fake_call_ollama(prompt, *a, **k):
    api.m._LLM_STATS["prompt_tokens"] = api.m._LLM_STATS.get("prompt_tokens", 0) + PROMPT_T
    api.m._LLM_STATS["eval_tokens"] = api.m._LLM_STATS.get("eval_tokens", 0) + EVAL_T
    return {"project_relevant": True, "patterns": [], "mistakes": [], "decisions": [],
            "session_summary": "a session", "context_update": ""}


case = json.loads((ROOT / "research" / "data" / "supersession_v1.json").read_text(encoding="utf-8"))["cases"][0]
api.m.call_ollama = fake_call_ollama
api.m.update_embeddings = lambda notes: None
api.m.embedder_available = lambda *a, **k: False
api.m.ollama_alive = lambda *a, **k: True          # the backend probe: the fake answers for it
api.m.llm_available = lambda *a, **k: True
res = sb.run_nevertwice([case], k=5)

print("\n- the per-session token delta comes from the engine that ran the capture -")
rows = res.get("rows") or res.get("cases") or []
tokens = (rows[0].get("session_tokens") if rows else None) or []
check("the arm ran (no blocked, one row, no error in it)",
      "blocked" not in res and len(rows) == 1 and not rows[0].get("error"), str(rows[0].get("error") if rows else res)[:300])
check("one token record per session of the case", len(tokens) == len(case["sessions"]), str(tokens))
check(f"each session's record carries the fake model's cost ({PROMPT_T} prompt / {EVAL_T} eval tokens per call)",
      bool(tokens) and all(t.get("prompt_tokens", 0) >= PROMPT_T and t.get("eval_tokens", 0) >= EVAL_T
                           for t in tokens), str(tokens))

print(f"\nsupersession session tokens: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
