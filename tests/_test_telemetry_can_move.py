#!/usr/bin/env python3
"""The counters can move, and the one that matters does not lie in the case it exists for.

`telemetry.py` documents five counters. Three of them - `record_search`,
`record_extraction_failure`, `record_outcome` - had **no call site anywhere in the package**, so
they could only ever read zero. The module's own docstring says of extraction failures: "the 2026
stall showed up here as a flat store and nowhere else." It could not have: nothing called the
recorder.

And `render()` reported the fifth counter backwards in exactly the case it was built for. The
capture-lag line reads "keeping up" when `lag_seconds` is falsy - but `refresh_capture_lag` stores
`None` when sessions exist and **no note has ever been written from one**, which is the total stall.
Sessions arriving, nothing extracted, and the dashboard says capture is keeping up.

    python tests/_test_telemetry_can_move.py
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
import telemetry as t  # noqa: E402

print("# the stall the capture counter exists for is not reported as health")
(m.VAULT / "Sessions").mkdir(parents=True, exist_ok=True)
(m.VAULT / "Sessions" / "2026-09-19-demo-session.md").write_text("# s\n", encoding="utf-8")
lag = t.refresh_capture_lag()
check("sessions on disk and no note written from any of them", lag["last_session"] is not None
      and lag["last_note"] is None, repr(lag))
line = t.render()
capture = next((ln for ln in line.splitlines() if ln.strip().startswith("capture")), "")
check("the dashboard does not call a total stall 'keeping up'", "keeping up" not in capture,
      f"printed {capture.strip()!r} while no note has ever been written")
check("and it says what is actually wrong", "no note" in capture.lower(),
      f"printed {capture.strip()!r}")

print("# ... while a store that IS caught up still reads as caught up")
(m.VAULT / "Patterns").mkdir(parents=True, exist_ok=True)
note = m.VAULT / "Patterns" / "2026-09-19-demo-pattern-p.md"
note.write_text("---\ntype: pattern\n---\n\n# p\n", encoding="utf-8")
import os  # noqa: E402

later = time.time() + 5
os.utime(note, (later, later))
t.refresh_capture_lag()
capture = next((ln for ln in t.render().splitlines() if ln.strip().startswith("capture")), "")
check("a caught-up store reads 'keeping up'", "keeping up" in capture, f"printed {capture.strip()!r}")

print("# every counter the module documents has a caller in the package")
import subprocess  # noqa: E402

#: Grep for the recorder NAME, not for the import line that brings the module in - the call is
#: on a different line from `from . import telemetry as _tel`, so a grep for the import sees the
#: module and misses every use of it. The first version of this check did exactly that and could
#: not have gone green on any correct fix.
for fn in ("record_search", "record_extraction_failure", "record_outcome"):
    hits = subprocess.run(["git", "grep", "-n", fn, "--", "nevertwice/"],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout.splitlines()
    callers = [ln for ln in hits if not ln.startswith("nevertwice/telemetry.py")]
    check(f"{fn} is called from the package", bool(callers),
          "no call site: the counter can only ever read zero")

print("# and a recorded value actually lands in the ledger")
t.record_search(12.5)
t.record_extraction_failure("no usable json")
t.record_outcome("accepted")
snap = t.snapshot()
check("the search sample is counted", snap["search_latency"]["count"] == 1,
      repr(snap["search_latency"]))
check("the failure reason is slugified and counted",
      snap["extraction_failures"]["by_reason"].get("no_usable_json") == 1,
      repr(snap["extraction_failures"]))
check("the outcome is counted under D4's vocabulary",
      snap["intervention_outcomes"].get("accepted") == 1, repr(snap["intervention_outcomes"]))

print()
print(f"telemetry can move: {P} passed, {F} failed")
sys.exit(1 if F else 0)
