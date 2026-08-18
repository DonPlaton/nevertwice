#!/usr/bin/env python3
"""Regression tests for the injection receipt (receipt.py + the SessionStart assembly).

The receipt reports what an injection cost, what the budget REFUSED, and what it saved. Two
properties matter more than its formatting and are pinned hardest here:

  1. It never displaces content: it takes NO reservation - assembly is byte-identical with
     or without it, and the line lands only in leftover slack, degrading or vanishing
     rather than pushing a lesson out.
  2. It is counted inside the budget, so the audited invariant "the cap bounds the WHOLE
     payload" (M-d) still holds with the receipt attached.

No embedder, no vault, no network.

    python _test_receipt.py
"""
import contextlib as ctx
import io
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m          # noqa: E402
import receipt as rc             # noqa: E402

_ROOT = r"D:\Projects" if os.name == "nt" else "/projects"
m.PROJECT_ROOTS = [_ROOT]

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


# ── pure arithmetic ───────────────────────────────────────────────────────────
print("receipt arithmetic")
r = rc.Receipt(2200)
r.show(3); r.show(); r.hold(2)
check("show/hold accumulate", (r.shown, r.held) == (4, 2))
r.hold(-5); r.show(-1)
check("negative deltas are ignored (a miscount must not go backwards)",
      (r.shown, r.held) == (4, 2))
r.seal("x" * 1720, 11200)
check("seal records payload size", r.chars == 1720)
check("est_tokens ~ chars/4", rc.est_tokens("x" * 400) == 100)
check("as_dict is complete", rc.Receipt(10).as_dict().keys() >=
      {"budget", "shown", "held_back", "chars", "tokens", "saved_tokens"})

print("human magnitudes")
check("thousands", rc.human(11200) == "11.2k")
check("trailing zeros trimmed", rc.human(11000) == "11k")
check("below 1k is literal", rc.human(950) == "950")
check("millions", rc.human(2_400_000) == "2.4M")
check("999_999 promotes to 1M, never '1000k' (mirrors stats._human)",
      rc.human(999_999) == "1M")

print("receipt line")
line = rc.Receipt(2200)
line.show(5); line.hold(2); line.seal("x" * 1720, 11200)
txt = line.line()
check("reports cost in tokens", "~430 tok" in txt)
check("reports what was refused", "(2 held back)" in txt)
check("reports what it saved", "saved ~11.2k" in txt)
check("italic metadata, not another lesson", txt.startswith("_") and txt.endswith("_"))
one = rc.Receipt(2200); one.show(1); one.seal("x" * 400, 0)
check("singular noun for one lesson", "1 lesson ·" in one.line() or
      one.line().endswith("1 lesson_"))
check("no 'held back' when nothing was refused", "held back" not in one.line())
check("no 'saved' before the ledger knows the store", "saved" not in one.line())

print("graceful degradation")
deg = rc.Receipt(2200); deg.show(5); deg.hold(2); deg.seal("x" * 1720, 11200)
vs = deg.variants()
check("three levels of detail, richest first", len(vs) == 3 and len(vs[0]) > len(vs[-1]))
check("the savings clause is dropped first", "saved" in vs[0] and "saved" not in vs[1])
check("the refusal count survives to the last variant", "held back" in vs[-1])
plain = rc.Receipt(2200); plain.show(3); plain.seal("x" * 400, 0)
check("nothing held back → no minimal variant to fall back to", len(plain.variants()) == 1)

print("no-displacement guarantee")
check("the richest form is used when there is room", deg.best_line(100) == vs[0])
check("a tight leftover degrades instead of overshooting",
      deg.best_line(2200 - len(vs[1]) - 1) == vs[1])
check("the last thing to survive is the refusal count",
      deg.best_line(2200 - len(vs[2]) - 1) == vs[2])
check("no room at all → no receipt, never a truncated one", deg.best_line(2200) == "")
check("a receipt is never longer than the room it was given",
      all(len(deg.best_line(n)) + n + 1 <= 2200 or deg.best_line(n) == ""
          for n in range(1500, 2201, 25)))
check("no budget → always fits", rc.Receipt(0).fits(10_000, 100))


# ── end-to-end through the real assembly ──────────────────────────────────────

def _emit(budget, receipt_on, n_facts=9, top_k=None):
    """Build a real SessionStart payload against a throwaway store.

    Patches EVERY vault-derived path constant, not just m.VAULT: EMBED_CACHE / EMBED_META /
    LOG_FILE are import-time constants computed from the ORIGINAL vault, so patching VAULT
    alone makes save_embed_cache() write fixture data over the LIVE store's cache AND its
    .bak in one call (review 2026-08: this exact harness poisoned a real deployment - the
    live cache was left holding the fixture entries and semantic recall served ~1% of the
    store until a rebuild). Mirrors tests/_test_memory_v3.py's sandbox()."""
    d = tempfile.mkdtemp()
    _v, _pr = m.VAULT, m.PROMPT_RECALL_STATE_DIR
    _ec, _em, _lf = m.EMBED_CACHE, m.EMBED_META, m.LOG_FILE
    _b, _r = m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT
    _k = m.RETRIEVAL_TOP_K
    _it, _dp, _al = m.is_tracked_project, m.derive_project_from_cwd, m.ollama_alive
    m.VAULT = Path(d)
    m.EMBED_CACHE = Path(d) / ".embeddings_cache.json"
    m.EMBED_META = Path(d) / ".embeddings_meta.json"
    m.LOG_FILE = Path(d) / ".logs" / "memory_hook.log"
    m.PROMPT_RECALL_STATE_DIR = Path(d) / ".pr"
    m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT = budget, receipt_on
    if top_k:
        m.RETRIEVAL_TOP_K = top_k
    m.is_tracked_project = lambda cwd: True
    m.derive_project_from_cwd = lambda cwd: "proj"
    m.ollama_alive = lambda timeout_s=4: False
    try:
        cache = {}
        for i in range(n_facts):
            cache[f"2026-06-0{i + 1}-proj-mistake-bug{i}"] = {
                "vec": [0.1], "ntype": "mistake", "project": "proj",
                "title": f"mistake number {i}",
                "desc": "a recurring problem in the training pipeline that wastes gpu hours",
                "recurrence": 1}
        m.save_embed_cache(cache)
        buf = io.StringIO()
        with ctx.redirect_stdout(buf):
            m.emit_session_start_context("X:/proj")
        out = buf.getvalue().strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    finally:
        m.VAULT, m.PROMPT_RECALL_STATE_DIR = _v, _pr
        m.EMBED_CACHE, m.EMBED_META, m.LOG_FILE = _ec, _em, _lf
        m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT = _b, _r
        m.RETRIEVAL_TOP_K = _k
        m.is_tracked_project, m.derive_project_from_cwd, m.ollama_alive = _it, _dp, _al


print("assembly integration")
on = _emit(2200, True)
off = _emit(2200, False)
check("a receipt is attached when enabled", "\n_memory: " in on)
check("exactly one receipt line", on.count("_memory: ") == 1)
check("it is the last line", on.strip().splitlines()[-1].startswith("_memory: "))
check("disabled → no receipt at all", "_memory: " not in off)
check("disabled payload is the pre-receipt payload", off == on[:on.rindex("\n_memory: ")])
check("payload stays within the budget with the receipt attached",
      len(on) <= 2200)

# THE structural guarantee: with no reservation, assembly is byte-identical whether or not a
# receipt is attached - at EVERY budget, not just the roomy default. A receipt can therefore
# never cost a lesson; it only ever consumes slack.
print("no-displacement, across budgets")
for _b in (2200, 1600, 1200, 900, 700, 300, 60):
    a = _emit(_b, True, n_facts=30, top_k=30)
    b = _emit(_b, False, n_facts=30, top_k=30)
    stripped = a[:a.rindex("\n_memory: ")] if "\n_memory: " in a else a
    check(f"budget={_b}: content identical with and without the receipt", stripped == b)
    check(f"budget={_b}: same number of lessons shown",
          stripped.count("- **mistake number") == b.count("- **mistake number"))

tiny = _emit(60, True)
check("a stub budget carries no receipt (content is never displaced)",
      "_memory: " not in tiny)
check("M-15 trimming still holds under a tiny budget",
      tiny.count("- **mistake number") <= 1)

# More candidates than the budget can carry. On a payload that fills its budget the receipt
# is DROPPED, deliberately: when there is only room for one more line, a lesson is worth more
# than a notice that a lesson was omitted. So the honest contract is conditional - if a
# receipt appears at all, it must be truthful and must not have cost anything.
print("squeezed budgets")
for _b in (2200, 1600, 1200, 900, 700):
    sq = _emit(_b, True, n_facts=30, top_k=30)
    body = sq[:sq.rindex("\n_memory: ")] if "\n_memory: " in sq else sq
    plain = _emit(_b, False, n_facts=30, top_k=30)
    has_receipt = "\n_memory: " in sq
    truncated = plain.count("- **mistake number") < 30
    check(f"budget={_b}: a receipt, if present, fits inside the budget",
          (not has_receipt) or len(sq) <= _b or len(plain) > _b)
    check(f"budget={_b}: a receipt on a truncated payload names the refusals",
          (not has_receipt) or (not truncated) or "held back" in sq)
    check(f"budget={_b}: the show-at-least-one guarantee survives",
          "- **mistake number" in body)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
