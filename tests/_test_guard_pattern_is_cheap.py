#!/usr/bin/env python3
"""A pattern that passes validation cannot stall the hot path, and cannot match everything.

`guards.py` states it plainly: "combined with the 20k input cap in check(), a guard that passes here
cannot stall the agent." It was not true, for one reason and one omission.

The reason is a number. `_redos_safe` runs the candidate against inputs of at most 96 characters,
while `check()` scans `MAX_CHECK_CHARS = 20000`. Exponential backtracking shows up at 96; polynomial
backtracking does not, and it is fatal at twenty thousand. The reviewer measured `open\\(.*\\).*encoding`
- an ordinary shape for a model-written guard - at **16.46 seconds** inside `check()` on one 20,000
character line, with `emit_pretooluse_guard` looping every guard in the ledger before every Edit,
Write, MultiEdit and Bash. Four rounds of this file have chased new *shapes*; the mismatch was the
probe's input size.

The omission is that nothing checked what the pattern matches. `.*`, `.`, `\\s` and `(?:)` all passed:
one generation emitting `.*` instead of the documented empty-pattern escape hatch mints a guard that
fires on every tool call, spends a context line each time, and promotes itself to `blocking` after
three sessions.

    python tests/_test_guard_pattern_is_cheap.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


make_sandbox(m, offline=True)
import guards as g  # noqa: E402

print("# a pattern whose cost is quadratic in its input stays cheap, because the input is a line")
QUADRATIC = [
    (r"open\(.*\).*encoding", "the shape a model writes for 'always pass encoding='"),
    (r"[^!]*[^!]*!x", "two unbounded runs over the same class"),
    (r"\s*\s*\s*=", "three adjacent whitespace runs"),
]
check("check() matches per line, not against one blob", g.MAX_LINE_CHARS < g.MAX_CHECK_CHARS,
      f"line cap {getattr(g, 'MAX_LINE_CHARS', None)} vs scan cap {g.MAX_CHECK_CHARS}")
for pat, why in QUADRATIC:
    check(f"still allowed, because it is not slow per line: {pat!r} ({why})", g.safe_pattern(pat))

print("# and one that merely fires often is still allowed, because often is not slow")
ORDINARY = [
    r"subprocess\.run\([^)]*shell=True",
    r"except\s*:",
    r"pickle\.loads?\(",
    r"\.format\([^)]*\)\s*%",
]
for pat in ORDINARY:
    check(f"allowed: {pat!r}", g.safe_pattern(pat))

print("# a pattern that matches everything is not a guard")
MATCH_ALL = [".*", ".", r"\s", "(?:)", "", "a|", ".*?", "[\\s\\S]*"]
for pat in MATCH_ALL:
    check(f"refused: {pat!r}", not g.safe_pattern(pat))

print("# and the hot path stays fast on the worst text it will ever be handed")
#: The reviewer's measurement was 16.46 seconds for ONE guard on one full-cap line, with the hook
#: looping the whole ledger before every Edit, Write, MultiEdit and Bash. The cap that matters is
#: therefore per call with a realistic ledger, on the nastiest text the caller can pass.
LEDGER = [gd for gd in (g.make_guard(pat, "a message", project="demo") for pat, _ in QUADRATIC)
          if gd]
LEDGER += [gd for gd in (g.make_guard(p2, "m", project="demo") for p2 in ORDINARY) if gd]
check("the probe ledger is not empty", bool(LEDGER))
for name, text in (("one enormous line", ("open(x)" * (g.MAX_CHECK_CHARS // 7))),
                   ("solid word characters", "x" * (g.MAX_CHECK_CHARS - 1) + "!"),
                   ("ordinary code", ("value = compute(a, b)  # note" + chr(10)) * 700)):
    started = time.perf_counter()
    g.check(text, project="demo", guards=LEDGER)
    took = time.perf_counter() - started
    check(f"a full check over {len(LEDGER)} guards on {name} is under 200 ms", took < 0.200,
          f"took {took * 1000:.0f} ms")

print()
print(f"guard pattern is cheap: {P} passed, {F} failed")
sys.exit(1 if F else 0)
