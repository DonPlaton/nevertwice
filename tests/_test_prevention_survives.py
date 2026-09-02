#!/usr/bin/env python3
"""A rewrite never leaves a mistake note without its "how to avoid" half.

The absorb rewrite moved the old description AND the old prevention into a "Previous
statement" block. When the new extraction supplied no prevention of its own, the note
came out with no **Prevention:** line at all — telling an agent that something broke and
not what to do differently, which is the one thing a mistake note exists to carry.
CLAUDE.md promises SessionStart injects lessons "с телом (описание + «как избежать»)".
659 of 1549 live mistakes were in that state at the 2026-09 review.
"""
import _env_guard  # noqa: F401
import sys, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
SRC = (Path(__file__).resolve().parents[1] / "nevertwice" / "memory_hook.py").read_text(encoding="utf-8")
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the parser finds a prevention line, which the carry depends on -")
note = ["---", "type: mistake", "---", "", "# x", "", "It broke.", "",
        "**Prevention:** always pin the seed.", ""]
_t, _d, _p = m._parse_note_body(note)
check("the old prevention is parsed", _p.strip() == "always pin the seed.", repr(_p))

print("\n- the carry is wired, and only when the new one is absent -")
seg = SRC.split("absorbed_prev: list[str] = []")[1][:1400]
check("an empty new prevention inherits the old",
      'if not (prevention or "").strip() and (_p_old or "").strip():' in seg)
check("it assigns the previous value", "prevention = _p_old.strip()" in seg)
check("the inherited value is then NOT duplicated into Previous statement",
      "_frag not in prevention" in seg)

print("\n- a note whose prevention was parsed keeps it -")
note2 = ["---", "type: mistake", "---", "", "# y", "", "Body.", ""]
_t2, _d2, _p2 = m._parse_note_body(note2)
check("a note with no prevention parses as empty", (_p2 or "").strip() == "")

print("\n- the legacy Russian marker is still understood -")
note3 = ["---", "type: mistake", "---", "", "# z", "", "Body.", "",
         "**Как избежать:** проверять перед записью.", ""]
_t3, _d3, _p3 = m._parse_note_body(note3)
check("the pre-2.2.1 marker still yields a prevention", bool((_p3 or "").strip()), repr(_p3))

print(f"\nprevention survives: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
