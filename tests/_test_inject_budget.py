#!/usr/bin/env python3
"""The SessionStart budget invariant: INJECT_BUDGET_CHARS bounds the WHOLE payload.

The assembly has always claimed this (audit M-15/M-d) but two leaks broke it on real data:

  1. The "show at least one lesson" guarantee appended the first line of EVERY section
     regardless of the cap, so a payload could overshoot by a full fact line per section.
  2. Section headings ("**Do not repeat these mistakes:**", ...) were never charged to the
     budget at all - three of them leaked ~110 characters past a margin of 40.

Measured on the live store before the fix: 11 of 12 projects overshot a 1200-char budget,
and one reached 2418 against a cap of 2200. These tests pin the invariant with content long
enough to trigger both leaks.

    python _test_inject_budget.py
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


# ── the trimmer ───────────────────────────────────────────────────────────────
print("_fit_fact_line")
LINE = "- **A memorable title** - and a long snippet that follows it for a while"
check("a line that already fits is untouched", m._fit_fact_line(LINE, 500) == LINE)
cut = m._fit_fact_line(LINE, 40)
check("a long line is trimmed to the room", len(cut) <= 40)
check("the trim is marked", cut.endswith("…"))
check("the title survives the trim", "**A memorable title**" in cut)
check("no room → nothing", m._fit_fact_line(LINE, 0) == "" and m._fit_fact_line(LINE, -5) == "")
check("not even the title fits → nothing, never a half-name",
      m._fit_fact_line(LINE, 10) == "")
exact = m._fit_fact_line(LINE, len("- **A memorable title**"))
check("room for exactly the title → the bare title, no ellipsis",
      exact == "- **A memorable title**")
xline = "- [proj] **Cross project title** - snippet here"
check("the cross-project shape keeps its title too (closing **, not the opening one)",
      "**Cross project title**" in m._fit_fact_line(xline, 40))


# ── the invariant, end to end ─────────────────────────────────────────────────

def emit(budget, n_facts=12, title_len=90, receipt=True):
    # Patches every vault-derived path constant (EMBED_CACHE/EMBED_META/LOG_FILE are
    # import-time constants - patching VAULT alone lets save_embed_cache clobber the LIVE
    # store's cache; review 2026-08). Mirrors _test_memory_v3.sandbox().
    d = tempfile.mkdtemp()
    saved = (m.VAULT, m.PROMPT_RECALL_STATE_DIR, m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT,
             m.RETRIEVAL_TOP_K, m.is_tracked_project, m.derive_project_from_cwd, m.ollama_alive)
    saved_paths = (m.EMBED_CACHE, m.EMBED_META, m.LOG_FILE)
    m.VAULT = Path(d)
    m.EMBED_CACHE = Path(d) / ".embeddings_cache.json"
    m.EMBED_META = Path(d) / ".embeddings_meta.json"
    m.LOG_FILE = Path(d) / ".logs" / "memory_hook.log"
    m.PROMPT_RECALL_STATE_DIR = Path(d) / ".pr"
    m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT = budget, receipt
    m.RETRIEVAL_TOP_K = n_facts
    m.is_tracked_project = lambda cwd: True
    m.derive_project_from_cwd = lambda cwd: "proj"
    m.ollama_alive = lambda timeout_s=4: False
    try:
        cache = {}
        for i in range(n_facts):
            # long titles are what makes a single fact line overshoot a whole section
            cache[f"2026-06-{i + 1:02d}-proj-mistake-bug{i}"] = {
                "vec": [0.1], "ntype": "mistake" if i % 2 else "pattern", "project": "proj",
                "title": f"lesson {i} " + "x" * title_len,
                "desc": "a recurring problem in the training pipeline " * 3,
                "recurrence": 1}
        m.save_embed_cache(cache)
        buf = io.StringIO()
        with ctx.redirect_stdout(buf):
            m.emit_session_start_context("X:/proj")
        out = buf.getvalue().strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    finally:
        (m.VAULT, m.PROMPT_RECALL_STATE_DIR, m.INJECT_BUDGET_CHARS, m.INJECT_RECEIPT,
         m.RETRIEVAL_TOP_K, m.is_tracked_project, m.derive_project_from_cwd,
         m.ollama_alive) = saved
        (m.EMBED_CACHE, m.EMBED_META, m.LOG_FILE) = saved_paths


print("the payload never exceeds its budget")
# The dense sweep includes the review's boundary probes (279/309/509/688/784): a line that
# exactly fills the bare remainder used to land the payload at budget+1 because the accept
# predicate omitted the joining newline the accounting then charged (review 2026-08).
for b in (2200, 1600, 1200, 900, 784, 700, 688, 509, 500, 400, 309, 300, 279, 200):
    p = emit(b)
    check(f"budget={b}: payload {len(p)} <= {b}", len(p) <= b)

print("both leaks, isolated")
# One very long lesson against a small budget: the show-at-least-one path.
solo = emit(900, n_facts=1, title_len=400)
check("a single oversized lesson is trimmed, not appended whole", len(solo) <= 900)
check("...and it is still shown, title intact (the guarantee holds)", "**lesson 0" in solo)
# Pathological: not even the title fits. Showing half a name would be worse than showing
# nothing - a truncated title reads as a DIFFERENT lesson - so the guarantee yields here.
starved = emit(400, n_facts=1, title_len=400)
check("when not even the title fits, the lesson is dropped rather than corrupted",
      "**lesson 0" not in starved and len(starved) <= 400)
# Many lessons across two sections: the unaccounted-heading path.
multi = emit(900, n_facts=12, title_len=60)
check("multi-section payload respects the cap", len(multi) <= 900)
check("headings are charged, so a section that appears is fully paid for",
      multi.count("**") >= 2)

print("the receipt is not what keeps it in bounds")
for b in (2200, 1200, 700):
    check(f"budget={b}: still bounded with the receipt off", len(emit(b, receipt=False)) <= b)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
