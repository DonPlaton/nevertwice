#!/usr/bin/env python3
"""The injected line carries what the reader needs and not what it already has.

Part 3.1 of `.loop/GOAL-FINISH-B.md`: be lighter than Mem0 and better at the same time, with "at
least we lose nothing" explicitly not accepted as an excuse for the weight. The payload was
decomposed from the strings the supersession stand actually returned (track N in the ledger), and
it is made of three parts a query: about 100 characters of `[facts]`, about 104-130 characters of
attached earlier statement, and the note itself.

Two of those three are avoidable, for different reasons.

* The `[facts]` block is a list of verified literals appended to the description. The write path
  reads it (rules 2 and 2', and the shape rule) and so does the note on disk - but the *reader*
  gets the same literals a second time, inside the sentence they were harvested from. K1 measured
  the block's effect on ranking and did not confirm it; nobody had measured its effect on weight.
* The attached earlier statement is the price of losing nothing. The K8-C audit measured that it
  carries the only copy of the fact in 3 of 20 explicit and 7 of 20 implicit controls - so in most
  pairs it repeats what the newer note already says, and a repeat is weight with no reader.

Both are switchable, because the gate is measured on a stand and a missed gate is reverted rather
than argued with.

    python tests/_test_payload_weight.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


sandbox = make_sandbox(m, "weight_")

print("# the served snippet does not repeat the literals it was harvested from")

DESC = ("The HTTP client timeout was raised to 5 seconds after the pairing session."
        "  [facts] the HTTP client timeout is 5 seconds")
check("the engine can strip the block from a served description",
      hasattr(m, "_served_text"), "no _served_text on the module")
if hasattr(m, "_served_text"):
    served = m._served_text(DESC)
    check("the block is gone", "[facts]" not in served)
    check("the statement survives whole", served.startswith("The HTTP client timeout was raised"))
    check("nothing else is trimmed", served.endswith("pairing session."))
    check("a description with no block is returned unchanged",
          m._served_text("plain sentence.") == "plain sentence.")
    #: the switch exists because a missed gate is reverted, not argued with
    m.SERVE_FACTS_BLOCK = True
    check("the switch restores the old payload", "[facts]" in m._served_text(DESC))
    m.SERVE_FACTS_BLOCK = False

print("# the earlier statement is attached only when it says something new")

check("the engine can decide whether an earlier line is worth its characters",
      hasattr(m, "_earlier_is_informative"), "no _earlier_is_informative on the module")
if hasattr(m, "_earlier_is_informative"):
    #: a moved value - the reader cannot recover "30 seconds" from the newer note
    check("a differing literal keeps the line",
          m._earlier_is_informative("the HTTP client timeout is 30 seconds",
                                    "the HTTP client timeout is 5 seconds"))
    #: the same value said twice - the line is a duplicate and the reader loses nothing
    check("a repeated literal drops the line",
          not m._earlier_is_informative("the HTTP client timeout is 5 seconds",
                                        "the HTTP client timeout is 5 seconds"))
    #: no literal to compare: keep it. Silence is not evidence of a duplicate, and the whole
    #: point of the K8 package is that an unproven pair is never resolved against the reader
    check("an earlier line with no literal is kept",
          m._earlier_is_informative("we moved the handler into its own module", "the handler is fast"))
    check("an empty earlier line is dropped", not m._earlier_is_informative("", "anything"))
    m.ATTACH_EARLIER_ALWAYS = True
    check("the switch restores the unconditional attachment",
          m._earlier_is_informative("the HTTP client timeout is 5 seconds",
                                    "the HTTP client timeout is 5 seconds"))
    m.ATTACH_EARLIER_ALWAYS = False

print("# the attached line carries the value, not the sentence around it")

check("the engine can reduce an earlier line to what differs",
      hasattr(m, "_earlier_delta"), "no _earlier_delta on the module")
if hasattr(m, "_earlier_delta"):
    EARLIER = "The HTTP client timeout was configured to 30 seconds for slow services."
    CURRENT = "The HTTP client timeout was raised to 5 seconds after the pairing session."
    d = m._earlier_delta(EARLIER, CURRENT)
    check("the replaced value survives", "30" in d)
    check("the sentence around it does not", "slow services" not in d and len(d) < 30, repr(d))
    check("a value the newer note already carries is not repeated", "5" not in d.replace("30", ""))
    check("nothing verifiable differing yields an empty delta, and the caller keeps the sentence",
          m._earlier_delta("we split the handler out", "the handler is its own module") == "")
    m.ATTACH_EARLIER_ALWAYS = True
    check("the switch restores the full earlier sentence", m._earlier_delta(EARLIER, CURRENT) == "")
    m.ATTACH_EARLIER_ALWAYS = False

print("# and the write path still sees every literal")

check("_facts_in still reads the block from a stored description",
      m._facts_in(DESC) == m._facts_in(DESC) and bool(m._facts_in(DESC)),
      repr(m._facts_in(DESC)))

print()
print(f"payload weight: {P} passed, {F} failed")
sys.exit(1 if F else 0)
