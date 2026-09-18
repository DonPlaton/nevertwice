#!/usr/bin/env python3
"""An entity card's two counts say what they count, because they count different things.

The card printed "(N notes)" — live only — beside "mentions: M", which graph.py computes
over live AND superseded notes, as if the two were one quantity. 202 of 2739 cards
disagreed vault-wide and
one was born at "3 notes / 9 mentions", which reads as six missing notes rather than as
six retired ones. A reader treating the pair as a consistency check finds a phantom bug;
one treating it as evidence strength over-counts.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path
import _engine_source  # noqa: E402  the engine's text, one path for every suite

SRC = _engine_source.SRC

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the live count says it is live -")
check("the projects line says 'live notes'", '{len(notes)} live notes' in SRC)
check("the bare fallback says 'Live notes'", '**Live notes:**' in SRC)
check("the old unqualified label is gone", '({len(notes)} notes)' not in SRC)

print("\n- mentions is qualified only when it really differs -")
seg = SRC.split("mentions:")[0][-700:] + SRC.split("mentions:")[1][:400]
check("the qualifier exists", 'incl. superseded' in SRC)
check("it is conditional on the counts differing", '_mentions > len(notes)' in SRC)
check("mentions still falls back to the live count when no timeline exists",
      'tl.get("count", len(notes)) if tl else len(notes)' in SRC)

print("\n- the two numbers are still both shown, not silently unified -")
check("the note count is still rendered", 'len(notes)} live notes' in SRC)
check("the mentions figure is still rendered", 'mentions: {_mentions}' in SRC)

print(f"\ncard count labels: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
