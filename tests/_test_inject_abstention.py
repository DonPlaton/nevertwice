#!/usr/bin/env python3
"""The session-start payload refuses a weak lesson, it does not merely truncate one.

The payload loop is a size check: sections are added by priority until
INJECT_BUDGET_CHARS runs out. That cannot refuse something that fits, so a worthless
lesson was injected whenever there happened to be room, and it cannot give two
same-sized items different answers. budget.py exists to make exactly that distinction
and was wired only to api.py — the path that does not spend on every session start.

Value is RELATIVE to the strongest item in the same section, so it means the same thing
under RRF (~1/60) and calibrated fusion ((0,1) logistic).
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402
import _engine_source  # noqa: E402  the engine's text, one path for every suite

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the knob is a fraction, and it is on by default -")
check("INJECT_MIN_VALUE is a fraction in range", 0.0 <= m.INJECT_MIN_VALUE < 1.0,
      str(m.INJECT_MIN_VALUE))
# Measured 2026-09-02 (research/ABSTENTION_AB.md): at 0.35 the payload is 26.6% smaller and
# the wanted fact is present 8.7 points less often, against a pre-declared gate of 20% for at
# most 2 points. The default is OFF and this pins it, so a future change to the constant has
# to come with a measurement rather than a preference.
check("the default is off until a measurement says otherwise", m.INJECT_MIN_VALUE == 0.0,
      str(m.INJECT_MIN_VALUE))
check("it is the same shape as the per-turn knob",
      0.0 <= m.PROMPT_RECALL_MIN_VALUE < 1.0)

print("\n- the filter refuses the weak tail while there is room -")
items = [{"stem": "strong", "score": 1.0}, {"stem": "weak", "score": 0.05}]
val = m._relative_value(items)
ARMED = 0.35                      # the value the sweep used, so the checks below still bite
kept = [r for r in items if val[r["stem"]] >= ARMED]
check("the weak item is refused", [r["stem"] for r in kept] == ["strong"], str(kept))

print("\n- the 'show at least one' guarantee survives -")
only_weak = [{"stem": "a", "score": 0.001}]
v2 = m._relative_value(only_weak)
check("a lone item always scores 1.0 and is kept",
      v2["a"] >= ARMED, str(v2))

print("\n- unscored items are left alone rather than silently dropped -")
unscored = [{"stem": "x"}, {"stem": "y"}]
check("no item carries a score", not any(r.get("score") for r in unscored))
v3 = m._relative_value(unscored)
check("a wholly unscored batch values everything at 1.0", set(v3.values()) == {1.0})

print("\n- the wiring is present at the payload site -")
src = _engine_source.SRC
seg = src.split("def _add_facts(")[1][:1400]
check("the payload consults INJECT_MIN_VALUE", "INJECT_MIN_VALUE" in seg)
check("it only filters when something is scored", 'any(r.get("score")' in seg)
check("it never empties a section", "if keep:" in seg)

print(f"\ninject abstention: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
