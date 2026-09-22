#!/usr/bin/env python3
"""Where the PreToolUse millisecond goes, decomposed by subtraction between real processes.

Part 2a of the work order asks for the hot path taken apart by a profile rather than by eye.
A profiler inside the process cannot see the part spent before the process has a profiler,
and on this path that is most of it: the interpreter has to exist, `site` has to run and the
engine has to import before a single line of our code executes. So the decomposition is built
out of processes that each stop one step further along, and the difference between two
consecutive rows is the cost of exactly one step.

    python tools/hotpath_decompose.py            # 15 processes per stage
    python tools/hotpath_decompose.py --n=30

Every stage is the minimum of N processes: the least contended observation, which a busy host
can push up and never down. The absolute numbers belong to the machine that printed them - the
`site` row especially, see below - so what travels is the SHAPE, and the shape is what decides
whether a millisecond is reachable by changing this repository.

Reading it: the first row is the interpreter itself and no change here can remove it. The
`site` row is a property of the MACHINE's site-packages, not of the engine: it processes the
`.pth` files of every installed distribution, so a developer box with three hundred of them
pays several milliseconds and a clean user machine pays a fraction of that. Quoting it as an
engine improvement would be the same error as a spread without its mode.

Stdlib only, throwaway store, no model and no network.
"""
from __future__ import annotations

import glob
import json
import os
import site
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "nevertwice"

sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo

sandbox_guard.isolate(prefix="nevertwice-hotpath-")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

EVENT = {"session_id": "hotpath-decompose", "hook_event_name": "PreToolUse",
         "tool_name": "Edit",
         "tool_input": {"file_path": "handler.py", "new_string": "value = eval(body)"}}


def _n() -> int:
    for arg in sys.argv:
        if arg.startswith("--n="):
            return max(3, int(arg.split("=", 1)[1]))
    return 15


def _ms(cmd: list[str], env: dict, n: int, stdin_text: str | None = None) -> float:
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, env=env,
                       timeout=120)
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def site_is_the_machines(env: dict, n: int) -> dict:
    """The `site` step, with the evidence that it belongs to the machine rather than to us."""
    bare = _ms([sys.executable, "-S", "-c", "pass"], env, n)
    full = _ms([sys.executable, "-c", "pass"], env, n)
    dirs = [d for d in site.getsitepackages() + [site.getusersitepackages()] if os.path.isdir(d)]
    return {"bare_ms": round(bare, 2), "with_site_ms": round(full, 2),
            "site_ms": round(full - bare, 2),
            "pth_files": sorted(os.path.basename(p) for d in dirs
                                for p in glob.glob(os.path.join(d, "*.pth"))),
            "distributions": sum(len(glob.glob(os.path.join(d, "*.dist-info"))) for d in dirs)}


def main() -> int:
    n = _n()
    store = Path(tempfile.mkdtemp(prefix="hotpath_"))
    env = dict(os.environ)
    env.update({"NEVERTWICE_VAULT": str(store), "NEVERTWICE_HOME": str(store),
                "NEVERTWICE_CLOUD": "none", "PYTHONUTF8": "1"})
    env.pop("NEVERTWICE_PROJECT_ROOTS", None)
    event = json.dumps({**EVENT, "cwd": str(ROOT)})

    stages = [
        ("the interpreter itself (-S)", [sys.executable, "-S", "-c", "pass"], None),
        ("+ site", [sys.executable, "-c", "pass"], None),
        ("+ importing the engine",
         [sys.executable, "-c",
          f"import sys;sys.path.insert(0,r'{PKG}');import memory_hook"], None),
        ("+ reading, deciding, answering", [sys.executable, str(PKG / "memory_hook.py")], event),
    ]

    print(f"PreToolUse, decomposed by subtraction. Minimum of {n} processes per stage.")
    print()
    rows, prev = [], 0.0
    for label, cmd, stdin_text in stages:
        t = _ms(cmd, env, n, stdin_text)
        rows.append((label, t, t - prev))
        prev = t
    total = rows[-1][1]
    print(f"{'stage':34} {'total ms':>9} {'step ms':>9} {'share':>7}")
    for label, t, step in rows:
        print(f"{label:34} {t:9.2f} {step:9.2f} {step / total * 100:6.1f}%")
    print()
    print(f"not the engine at all (the interpreter): {rows[0][1]:8.2f} ms "
          f"({rows[0][1] / total * 100:.1f}%)")
    print(f"the engine's own share:                 {total - rows[0][1]:8.2f} ms "
          f"({(total - rows[0][1]) / total * 100:.1f}%)")

    s = site_is_the_machines(env, n)
    print()
    print("- the `site` step belongs to this machine, not to the engine -")
    print(f"  bare {s['bare_ms']} ms, with site {s['with_site_ms']} ms, so site costs "
          f"{s['site_ms']} ms here")
    print(f"  because it walks {s['distributions']} installed distribution(s) and "
          f"{len(s['pth_files'])} .pth file(s): {', '.join(s['pth_files']) or 'none'}")
    print("  a clean user machine pays a fraction of this, so `-S` in the hook command is a "
          "saving of the MACHINE's, and quoting it as ours would be a number without its mode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
