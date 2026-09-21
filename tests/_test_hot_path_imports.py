#!/usr/bin/env python3
"""What PreToolUse loads is what PreToolUse uses.

`guards.py` is imported before every Edit, Write and Bash the agent runs. It used to import
`subprocess` and `hashlib` at module level, and neither is reachable from the four functions that
path actually calls. Measured marginally, after `json`, `re`, `os`, `sys` and `marshal` are
already in: `subprocess` 3.91 ms, `hashlib` 3.16 ms. Seven milliseconds of every tool call spent
loading code that could not run.

This suite pins the property behaviourally rather than by grepping the import lines, because the
grep passes on a file that imports `subprocess` through something else. It imports the module in
a fresh interpreter and asks `sys.modules` - the question a profiler would ask.

It also pins the reason: a call-graph walk from the four entry points the hot path uses, so that
moving one of the three functions onto that path goes red here rather than quietly costing seven
milliseconds again.

    python tests/_test_hot_path_imports.py
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths

PASSED = FAILED = 0

#: What PreToolUse calls into `guards.py`. `emit_pretooluse_guard` in the engine reaches exactly
#: these; everything else in the module belongs to guard creation, feedback or the CLI.
HOT_ENTRIES = ("load_guards", "check", "already_delivered", "record_fired")

#: Modules no hot-path function needs, and what each cost when it was loaded anyway.
BANNED = {"subprocess": "3.91 ms", "hashlib": "3.16 ms"}


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("# importing guards does not drag in what the hot path cannot call")

probe = ("import sys; sys.path.insert(0, %r); import guards; "
         "print(','.join(sorted(m for m in %r if m in sys.modules)))"
         % (str(PKG), tuple(BANNED)))
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
loaded = [m for m in r.stdout.strip().split(",") if m]
check("guards imports cleanly in a bare interpreter", r.returncode == 0, r.stderr[-300:])
check(f"neither of {sorted(BANNED)} is loaded by importing guards", not loaded,
      "loaded: " + ", ".join(f"{m} ({BANNED[m]})" for m in loaded))


print("# and the reason still holds: no hot-path function reaches them")

tree = ast.parse((PKG / "guards.py").read_text(encoding="utf-8"))
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
check(f"all {len(HOT_ENTRIES)} hot-path entry points exist",
      all(e in funcs for e in HOT_ENTRIES),
      str([e for e in HOT_ENTRIES if e not in funcs]))

calls = {name: {c.func.id for c in ast.walk(n)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
         for name, n in funcs.items()}
reach, queue = set(), list(HOT_ENTRIES)
while queue:
    f = queue.pop()
    if f in reach or f not in calls:
        continue
    reach.add(f)
    queue += list(calls[f])

#: Which function owns each banned import, by reading the source rather than restating it: a
#: fourth use added tomorrow is covered on the day it is written.
owners: dict[str, set[str]] = {m: set() for m in BANNED}
for name, node in funcs.items():
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name in owners:
                    owners[alias.name].add(name)
for mod, where in sorted(owners.items()):
    check(f"{mod} is imported inside a function, not at module scope", bool(where),
          "no function imports it - did it go back to the top of the file?")
    on_hot = sorted(where & reach)
    check(f"and none of its {len(where)} owner(s) is reachable from the hot path", not on_hot,
          f"{on_hot} is called from {sorted(HOT_ENTRIES)} - either the hot path grew or the "
          f"import has to move again")

module_level = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
check("neither name is imported at module scope either", not (module_level & set(BANNED)),
      str(sorted(module_level & set(BANNED))))
print(f"       (the walk reaches {len(reach)} of {len(funcs)} functions from "
      f"{', '.join(HOT_ENTRIES)})")

print()
print(f"hot-path imports: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
