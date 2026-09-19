#!/usr/bin/env python3
"""There is one engine per process, and a sandbox moves all of it.

`memory_hook.py` compiles `_engine.py` and execs it into its own `globals()`. Import it twice under
two names and you get two module objects, each with its own `VAULT`, its own caches and its own
`_rebase_vault`. A sibling that writes `import memory_hook as m` while the caller holds
`nevertwice.memory_hook` is therefore not sandboxed by anything: `_rebase_vault` moves one copy, and
the module keeps operating on the store the other copy points at - the owner's real one.

Two modules crossed that line, and they are the two whose job is destructive: `store_version.py`
(`--rebuild --apply` deletes the index, the graph and `Index.md`) and `migrate.py` (`revert` unlinks
notes). `tests/_env_guard.py` records the incident this class produced on 2026-08-18, when a routine
test batch overwrote a 4,319-entry production cache.

Every other sibling already writes the try-relative-then-flat pair. This suite is the check that the
pair is not optional, for every module in the package that binds the engine at all.

    python tests/_test_one_engine.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


# A bare `import memory_hook` at module scope, with no `from . import` beside it, is the defect.
BARE = re.compile(r"^import (memory_hook|api|store_state|index_sqlite|outcomes|guards|"
                  r"consolidate_memory|memory_search|digest|dashboard|anticipate|causal|graph|"
                  r"lenses|why_fired|telemetry|budget|embed_index)\b")
RELATIVE = re.compile(r"^\s*from \. import ", re.M)

print("# no module in the package binds a sibling flat outside an ImportError fallback")
offenders = []
for path in sorted((ROOT / "nevertwice").glob("*.py")):
    if path.name in ("memory_hook.py", "_engine.py"):
        continue                      # the loader and the engine itself are the one exception
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        # indented inside the `except ImportError:` arm is the fallback and is fine; at column
        # zero it is the only binding, and a package caller gets a second engine.
        if BARE.match(line):
            offenders.append(f"{path.name}:{i} {line.strip()}")
check("every sibling import is the try-relative-then-flat pair", not offenders,
      "; ".join(offenders[:6]) + (f" (+{len(offenders) - 6} more)" if len(offenders) > 6 else ""))
check("and the pair is actually used somewhere in the package",
      any(RELATIVE.search(p.read_text(encoding="utf-8"))
          for p in (ROOT / "nevertwice").glob("*.py")))

print("# and the two destructive modules share the caller's engine object")
import nevertwice.memory_hook as pkg_engine  # noqa: E402

for name in ("store_version", "migrate", "api"):
    mod = __import__(f"nevertwice.{name}", fromlist=[name])
    bound = getattr(mod, "m", None)
    check(f"nevertwice.{name} binds the same engine the caller holds",
          bound is pkg_engine,
          f"{name}.m is a second module object with VAULT="
          f"{getattr(bound, 'VAULT', '?')} while the caller's is {pkg_engine.VAULT}")

print("# a rebase reaches them, which is the property the sandbox depends on")
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    before = pkg_engine.VAULT
    try:
        pkg_engine._rebase_vault(Path(td))
        import nevertwice.store_version as sv  # noqa: E402
        check("a rebase moves store_version's view of the store too",
              sv.m.VAULT == Path(td), f"{sv.m.VAULT}")
    finally:
        pkg_engine._rebase_vault(before)

print()
print(f"one engine: {P} passed, {F} failed")
sys.exit(1 if F else 0)
