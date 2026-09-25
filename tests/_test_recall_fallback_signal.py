#!/usr/bin/env python3
"""B6: when recall falls back to word matching, the injected memory says so.

`retrieve_relevant` ranks by embedding similarity only when the embedder answers a one-second ping
and embeds the query within the hook's budget; a cold-loading bge-m3 misses both, and ranking
silently falls to word overlap. The CLI already returns its mode (`memory_search.py`); the hook
path wrote one log line and injected lexical hits as if nothing had changed - the agent and the
person reading the hook output had no way to know recall was running at half strength.

Now `retrieve_relevant` records WHY semantic ranking did or did not run (`_RECALL_LAST`), and
both injections - per prompt and at SessionStart - add one line when it fell back on a store
that HAS vectors (a text-only store is word matching by design, and says nothing). SessionStart
appends the line only into room the budget has left, like the receipt: content is never displaced.

    python tests/_test_recall_fallback_signal.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


PROJECT = "fbproj"
STEM = f"2026-09-20-{PROJECT}-mistake-pgbouncer-drops-prepared-statements"
V = [1.0] + [0.0] * 7


def store(with_vectors: bool) -> Path:
    d = make_sandbox(m, "b6_", offline=True)
    (d / "Mistakes").mkdir(parents=True, exist_ok=True)
    (d / "Mistakes" / f"{STEM}.md").write_text("# pgbouncer drops prepared statements\n\ntransaction pooling "
                                               "drops server-side prepared statements\n", encoding="utf-8")
    e = {"ntype": "mistake", "project": PROJECT, "title": "pgbouncer drops prepared statements",
         "desc": "transaction pooling drops server-side prepared statements", "prevention": "", "recurrence": 1}
    if with_vectors:
        e["vec"] = list(V)
    m._save_json_generations(m.EMBED_CACHE, json.dumps({STEM: e}), prev=False)
    m._EMBED_CACHE_MEMO["sig"] = None
    m.is_tracked_project = lambda cwd: True
    m.derive_project_from_cwd = lambda cwd: PROJECT
    return d


def embedder(up: bool, vec_ok: bool = True) -> None:
    m.embedder_available = lambda *a, **k: up
    m.embed_cache_usable = lambda: True
    m.embed_text = (lambda *a, **k: list(V)) if vec_ok else (lambda *a, **k: None)


def prompt_injection(sid: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.emit_prompt_recall("D:\\Coding\\x", "why does pgbouncer drop the prepared statements", sid)
    out = buf.getvalue().strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


print("\n- a cold embedder is announced, and so is an embed that failed -")
store(with_vectors=True)
embedder(up=False)
ctx = prompt_injection("s1")
check("the lexical hit is still injected", "pgbouncer" in ctx.lower(), ctx[:120])
check("and the injection says recall fell back to word matching", "word matching" in ctx.lower(), ctx[-200:])
check("retrieve_relevant records why: the embedder did not answer",
      m._RECALL_LAST.get("semantic") == "fallback:embedder_unreachable", str(m._RECALL_LAST))
store(with_vectors=True)
embedder(up=True, vec_ok=False)
ctx = prompt_injection("s2")
check("an embed call that returned nothing is a fallback too, and is announced",
      m._RECALL_LAST.get("semantic") == "fallback:embed_failed" and "word matching" in ctx.lower(),
      str(m._RECALL_LAST))

print("\n- a healthy embedder, and a text-only store, add no line -")
store(with_vectors=True)
embedder(up=True)
ctx = prompt_injection("s3")
check("with the embedder answering, recall is semantic and the injection has no notice",
      m._RECALL_LAST.get("semantic") in ("ok", "abstained:low_confidence") and "word matching" not in ctx.lower(),
      str(m._RECALL_LAST))
store(with_vectors=False)
embedder(up=False)
ctx = prompt_injection("s4")
check("a store with no vectors at all is word matching by design: no notice",
      "pgbouncer" in ctx.lower() and "word matching" not in ctx.lower(), ctx[-200:])

print("\n- SessionStart says it too, only into room the budget leaves -")
store(with_vectors=True)
embedder(up=False)
(m.VAULT / "Context").mkdir(parents=True, exist_ok=True)
(m.VAULT / "Context" / f"{PROJECT}.md").write_text("# fbproj\n\npgbouncer prepared statements\n", encoding="utf-8")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.emit_session_start_context("D:\\Coding\\x")
out = buf.getvalue().strip()
ss = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
check("the SessionStart injection carries the notice", "word matching" in ss.lower(), ss[-240:])
check("and stays within its budget", len(ss) <= m.INJECT_BUDGET_CHARS, f"{len(ss)} > {m.INJECT_BUDGET_CHARS}")

print("\n- a healthy embedder that found nothing confident is not a fallback -")
store(with_vectors=True)
m.embedder_available = lambda *a, **k: True
m.embed_cache_usable = lambda: True
m.embed_text = lambda *a, **k: [0.0, 1.0] + [0.0] * 6          # orthogonal to every stored vector
ctx = prompt_injection("s5")
check("low-confidence vectors are recorded as an abstention, not a degradation",
      m._RECALL_LAST.get("semantic") == "abstained:low_confidence" and m._RECALL_LAST.get("degraded") is False,
      str(m._RECALL_LAST))
check("... and the injection (lexical hits still) carries no notice",
      "pgbouncer" in ctx.lower() and "word matching" not in ctx.lower(), ctx[-200:])

print("\n- at the edge of the SessionStart budget the notice gives way, the payload never grows past it -")
store(with_vectors=True)
embedder(up=False)
(m.VAULT / "Context").mkdir(parents=True, exist_ok=True)
(m.VAULT / "Context" / f"{PROJECT}.md").write_text("# fbproj\n\npgbouncer prepared statements\n", encoding="utf-8")
_notice = m.recall_notice() or "_(recall ran on word matching only: x)_"
_saved_budget, _saved_receipt = m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT
try:
    m.INJECT_RECEIPT = False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.emit_session_start_context("D:\\Coding\\x")
    full = json.loads(buf.getvalue().strip())["hookSpecificOutput"]["additionalContext"]
    body = full.rsplit("\n", 1)[0] if "word matching" in full.rsplit("\n", 1)[-1] else full
    m.INJECT_BUDGET_CHARS = len(body) + 5                     # room for the content, not the notice
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m.emit_session_start_context("D:\\Coding\\x")
    tight = json.loads(buf.getvalue().strip())["hookSpecificOutput"]["additionalContext"]
    check("with no room left, the notice is not appended", "word matching" not in tight.lower(), tight[-160:])
    check("and the payload stays within the budget", len(tight) <= m.INJECT_BUDGET_CHARS,
          f"{len(tight)} > {m.INJECT_BUDGET_CHARS}")
finally:
    m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT = _saved_budget, _saved_receipt

print(f"\nrecall fallback signal: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
