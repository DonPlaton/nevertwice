#!/usr/bin/env python3
"""The hook's import pulls in nothing the hot path does not use.

PreToolUse fires on every tool call and pays a whole process. Two of the modules it was loading are
used once each, on the write path, which PreToolUse never reaches: `hashlib` for a single `sha1` in
`_sid8` (3.0 ms), `threading` for one `get_ident()` in `write_atomic` (0.9 ms) - and that one is
`_thread.get_ident`, the same function object, under a wrapper that costs the millisecond.

This suite is the gate for M1b, and M1b exists because M1 was written on the wrong instrument. The
first gate asked for a fall in the PreToolUse median **with disjoint ranges**, which an effect of
about 2 ms cannot produce against a host spread of 1.5 ms at any sample size - the range only widens
with n. The mechanism was reverted by its own rule, the miss published, and re-gated here on what
separates it exactly: which modules load, and what `-X importtime` reports. The millisecond is still
checked, one-sided: it may not rise.

The second half of the suite is what makes the saving legitimate - the deferred imports still WORK.
A lazy import nobody exercises is not a saving, it is a latent AttributeError on the write path.

    python tests/_test_hot_import.py
"""
from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PKG))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0

#: Each with the cost it carried, so a future reader can weigh putting it back.
DEFERRED = {"hashlib": "3.0 ms, one sha1 in _sid8", "threading": "0.9 ms, one get_ident()"}


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


print("# gate 1: the hot import does not pull the write path's dependencies")

probe = ("import sys;sys.path.insert(0, r'%s');import memory_hook;"
         "print(','.join(sorted(k for k in sys.modules if '.' not in k)))" % PKG)
r = subprocess.run([sys.executable, "-S", "-c", probe], capture_output=True, text=True)
loaded = set((r.stdout or "").strip().split(",")) if r.returncode == 0 else set()
check("the probe imported the engine", r.returncode == 0, r.stderr[-300:])
for mod, why in DEFERRED.items():
    check(f"importing the hook does not pull {mod} ({why})", mod not in loaded)


print("# gate 2: -X importtime falls, on min and median, over repeats")


def import_ms(n: int = 5) -> list[float]:
    """Total import time for the engine, as `-X importtime` reports it, one fresh process each."""
    out = []
    for _ in range(n):
        p = subprocess.run([sys.executable, "-X", "importtime", "-c",
                            f"import sys;sys.path.insert(0, r'{PKG}');import memory_hook"],
                           capture_output=True, text=True)
        for line in reversed(p.stderr.splitlines()):
            if line.startswith("import time:") and line.rstrip().split("|")[-1].strip() == "memory_hook":
                out.append(int(line.split("|")[1].strip()) / 1000)
                break
    return sorted(out)


now = import_ms()
check("importtime reports the engine", len(now) >= 5, f"{len(now)} samples")
#: The base is the committed figure from the M1 measurement at cc07b29: 28.1 ms marginal.
BASE_MS = 28.1
if now:
    print(f"       min {now[0]:.1f} ms, median {statistics.median(now):.1f} ms, "
          f"max {now[-1]:.1f} ms  (base {BASE_MS} ms)")
    check("min below the base", now[0] < BASE_MS, f"{now[0]:.1f} vs {BASE_MS}")
    check("median below the base", statistics.median(now) < BASE_MS,
          f"{statistics.median(now):.1f} vs {BASE_MS}")


print("# gate 3: nothing the deferred imports serve has stopped working")

sandbox = make_sandbox(m, "hotimp_")

a, b = m._sid8("ingest-file-aaaa-1"), m._sid8("ingest-file-aaaa-2")
check("_sid8 returns 8 hex characters", len(a) == 8 and all(c in "0123456789abcdef" for c in a))
check("_sid8 separates ids that differ at the tail", a != b)
check("_sid8 is stable across calls", a == m._sid8("ingest-file-aaaa-1"))

target = sandbox / "atomic.md"
m.write_atomic(target, "one\ntwo\n")
check("write_atomic wrote the file", target.read_text(encoding="utf-8") == "one\ntwo\n")
m.write_atomic(target, "three\n")
check("write_atomic replaced it", target.read_text(encoding="utf-8") == "three\n")
check("write_atomic left no temp file behind",
      not [p for p in sandbox.iterdir() if p.suffix == ".tmp"])

print()
print(f"hot import: {P} passed, {F} failed")
sys.exit(1 if F else 0)
