#!/usr/bin/env python3
"""Entry point of the memory hook: the engine's body, run in THIS module's namespace.

Why this file is small. The five hooks in the agent's settings run `python memory_hook.py`, so
this file is `__main__`, and CPython never serves `__main__` from `__pycache__`: the engine's
7,685 lines were recompiled on every single tool call. Measured on 2026-09-18 - compiling the
body costs 48.6 ms of a 98.6 ms PreToolUse, against 21.3 ms of interpreter start and 5.6 ms of
the guard's own work. Loading the body through a file loader instead reads the cached bytecode,
so the compile is paid once per source change rather than once per tool call.

Why `exec` into `globals()` and not `import` or a facade. This module's contract is its
*namespace*: tests rebind `memory_hook.VAULT`, `memory_hook.generate_json` and some eighty other
names, `_rebase_vault` moves every vault-derived constant with a `global` statement, and both
hand-rolled `cache_clear` hooks close over dictionaries that live in it. Executing the body here
keeps ONE namespace - `memory_hook.__dict__` - so all of that keeps working untouched, and the
module is byte-for-byte the same object graph it was before. A `from _engine import *` facade
would copy the values into a second namespace and silently strand every rebind, which is exactly
the class of the 2026-08-13 and 2026-08-18 live-cache incidents.

`tests/_test_entry_point.py` pins both halves: the namespace is identical name for name, and the
five hook events produce identical output.
"""
import importlib.util
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().with_name("_engine.py")

if not _ENGINE.exists():                                  # a half-copied install, not a bug here
    sys.stderr.write(
        f"[memory_hook] the engine body is missing: {_ENGINE}\n"
        "[memory_hook] this file is only the entry point; _engine.py must sit beside it.\n"
        "[memory_hook] copy it across (tools/sync_install.py --apply copies the whole directory).\n")
    raise SystemExit(0)                                   # never fail the agent's tool call

_spec = importlib.util.spec_from_file_location("_engine", _ENGINE)
#: `get_code` reads `__pycache__` when it is current and writes it when it is not. That is the
#: whole point: one compile per edit instead of one per tool call. It also means the traceback
#: for anything in the engine names `_engine.py` and its real line, which is what we want.
_code = _spec.loader.get_code("_engine")
del _ENGINE, _spec                                        # keep the namespace exactly the engine's
#: `__file__` stays this file's path, which is what the engine wants: its three uses of it all
#: resolve a sibling in the same directory (twin_calibration.json, graphify.py, process_now.py),
#: and `memory_hook.__file__` is what a test walks up from to reach the repository root.
exec(_code, globals())                                    # noqa: S102 - our own cached bytecode
del _code
