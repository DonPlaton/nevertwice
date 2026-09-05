#!/usr/bin/env python3
"""One observation stored twice counts once, so duplication cannot pose as corroboration.

An entity card's note count is read as strength of evidence. The 2026-09 review found the
same observation filed as BOTH a pattern and a decision — same slug, same date, same
session — counted twice; and three live decisions asserting the identical W-6 = 9 result
with no supersede between them, which the card presented as three confirmations while
mentions jumped 11 → 13 for zero new information.

quantum_prism is being prepared for arXiv, where independent confirmation is load-bearing,
so inflation is the dangerous direction. A genuine recurrence — the same lesson learned
again in a DIFFERENT session — must still count, because that signal is what recurrence
exists for.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def note(stem, session):
    return {"stem": stem, "session": session, "project": "p"}


print("\n- the same fact filed under two TYPES counts once -")
same = [note("2026-08-25-p-pattern-retry-with-recency-reminder", "s1"),
        note("2026-08-25-p-decision-retry-with-recency-reminder", "s1")]
check("two types, one observation, counted once", len(m._collapse_restatements(same)) == 1,
      str(len(m._collapse_restatements(same))))

print("\n- a genuine recurrence in a DIFFERENT session still counts -")
recur = [note("2026-08-25-p-pattern-a-lesson", "s1"),
         note("2026-08-27-p-pattern-a-lesson", "s2")]
check("a later re-learning is kept", len(m._collapse_restatements(recur)) == 2)

print("\n- two projects learning the same lesson on the same day are two observations -")
cross = [note("2026-08-25-p-mistake-output-token-limit-exceeded", "s1"),
         note("2026-08-25-q-mistake-output-token-limit-exceeded", "s1")]
check("the entity pool is cross-project, so the project is in the key",
      len(m._collapse_restatements(cross)) == 2, str(len(m._collapse_restatements(cross))))

print("\n- the session comes from the note itself -")
sess = [{"stem": "2026-08-25-p-pattern-a-lesson", "session": "sA"},
        {"stem": "2026-08-25-p-decision-a-lesson", "session": "sB"}]
check("same slug and day in two sessions is a recurrence, not a restatement",
      len(m._collapse_restatements(sess)) == 2)

print("\n- distinct facts are never merged -")
distinct = [note("2026-08-25-p-decision-first", "s1"),
            note("2026-08-25-p-decision-second", "s1")]
check("two different slugs stay two", len(m._collapse_restatements(distinct)) == 2)

print("\n- order is preserved, and the first occurrence wins -")
kept = m._collapse_restatements(same)
check("the first of the pair is the one kept",
      kept[0]["stem"].endswith("pattern-retry-with-recency-reminder"), kept[0]["stem"])

print("\n- degenerate input does not crash or over-collapse -")
check("an empty list stays empty", m._collapse_restatements([]) == [])
odd = [{"stem": "", "session": "s"}, {"stem": "", "session": "s"}]
check("unparseable stems are not silently merged into one",
      len(m._collapse_restatements(odd)) == 2, str(len(m._collapse_restatements(odd))))

print(f"\nrestatement collapse: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
