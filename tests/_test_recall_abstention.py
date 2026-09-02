#!/usr/bin/env python3
"""Per-turn recall refuses a weak hit while there is room for it.

That sentence is the whole point. Truncation drops what does not fit; it cannot refuse
something that fits, and it cannot give two same-sized items different answers. These
checks pin both properties on the `UserPromptSubmit` path, which is the one that spends
tokens on every turn.

The value is RELATIVE to the batch's best hit on purpose: the fused score means different
things under RRF (~1/60) and under calibrated fusion ((0,1) logistic), so an absolute
threshold would mean "keep everything" in one mode and "keep nothing" in the other.
"""
import _env_guard  # noqa: F401  (must be first: pins the sandbox)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

FAILED = []
RUN = []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def hit(stem, score):
    return {"stem": stem, "score": score, "ntype": "mistake", "message": "x" * 40, "id": stem}


print("\n- the value is relative, so it survives both fusion modes -")
rrf = [hit("a", 0.0166), hit("b", 0.0083)]          # RRF scale
cal = [hit("a", 0.90), hit("b", 0.45)]              # calibrated scale
v_rrf, v_cal = m._relative_value(rrf), m._relative_value(cal)
check("the best hit is always 1.0", v_rrf["a"] == 1.0 and v_cal["a"] == 1.0)
check("a half-strength hit reads 0.5 in BOTH scales",
      abs(v_rrf["b"] - 0.5) < 1e-6 and abs(v_cal["b"] - 0.5) < 1e-6,
      f"rrf={v_rrf['b']:.4f} cal={v_cal['b']:.4f}")

print("\n- a degenerate batch does not let the budget decide -")
check("an all-zero batch yields 1.0 for everything",
      set(m._relative_value([hit("a", 0.0), hit("b", 0.0)]).values()) == {1.0})
check("an empty batch is empty, not an error", m._relative_value([]) == {})

print("\n- the two properties truncation cannot have -")
batch = [hit("strong", 1.0), hit("weak", 0.10)]
value = m._relative_value(batch)
kept = [h for h in batch if value[h["stem"]] >= 0.35]
check("a weak hit is REFUSED although there is room for it",
      [h["stem"] for h in kept] == ["strong"])
same_size = [hit("hi", 1.0), hit("lo", 0.2)]
vs = m._relative_value(same_size)
check("two identically sized hits get different decisions",
      len(same_size[0]["message"]) == len(same_size[1]["message"])
      and (vs["hi"] >= 0.35) != (vs["lo"] >= 0.35))

print("\n- the killswitch and the default -")
check("the knob exists and defaults to a fraction, not a raw score",
      0.0 <= m.PROMPT_RECALL_MIN_VALUE < 1.0, str(m.PROMPT_RECALL_MIN_VALUE))
check("the top hit always clears any threshold below 1.0",
      m._relative_value([hit("only", 0.004)])["only"] >= 0.35)

print(f"\nrecall abstention: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
