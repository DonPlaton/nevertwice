#!/usr/bin/env python3
"""The dedup window shows the extractor this session's OWN notes.

Grounding dedup on the globally newest forty slugs is blind exactly when it matters. In
the live vault a project held 227 live patterns, 127 of them dated after the session
being re-mined, so the window contained ZERO notes from that session's date: the
extractor was told to avoid duplicates while being shown none of the ones it was about
to create. One session forked into 72 live and 41 retired notes that way.
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


PROJ = "wproj"
OLD_DAY, NEW_DAY = "2026-08-27", "2026-08-28"
rows = ([(OLD_DAY, f"old-{i}") for i in range(3)]
        + [(NEW_DAY, f"new-{i}") for i in range(m.TITLE_WINDOW + 20)])
m._TITLE_SLUGS[PROJ] = {nt: (rows if nt == "pattern" else []) for nt in m.TYPED_TYPES}

print("\n- without a date the window is the newest N, and that is the blindness -")
plain = m.collect_existing_titles(PROJ)["pattern"]
check("the plain window is capped", len(plain) == m.TITLE_WINDOW, str(len(plain)))
check("the plain window contains NONE of the older day's notes",
      not any(s.startswith("old-") for s in plain))

print("\n- with the session's date, its own notes are in the window -")
scoped = m.collect_existing_titles(PROJ, for_date=OLD_DAY)["pattern"]
check("every note from that day is present",
      all(f"old-{i}" in scoped for i in range(3)),
      str([s for s in scoped if s.startswith("old-")]))
check("the window is still capped", len(scoped) <= m.TITLE_WINDOW, str(len(scoped)))
check("newer notes still fill the remainder", any(s.startswith("new-") for s in scoped))

print("\n- a date with no notes degrades to the plain window -")
none_day = m.collect_existing_titles(PROJ, for_date="2020-01-01")["pattern"]
check("an empty day yields the newest N, not nothing",
      len(none_day) == m.TITLE_WINDOW and all(s.startswith("new-") for s in none_day))

print("\n- a type with no notes stays empty -")
check("an empty type is an empty tuple",
      m.collect_existing_titles(PROJ, for_date=OLD_DAY)["mistake"] == ())

m._TITLE_SLUGS.pop(PROJ, None)
print(f"\ndedup window: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
