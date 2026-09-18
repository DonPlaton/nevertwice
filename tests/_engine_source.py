"""The engine's source text, for the suites that assert on the code rather than run it.

A dozen checks in this directory are static: they read the engine as text or parse it, and ask
questions like "is this field carried across an absorb" or "does the vault dry run only ever
open files for reading". They are the right shape for what they pin - a behavioural test cannot
easily prove the *absence* of a write - but each of them used to spell the path itself.

When the engine's body moved out of `nevertwice/memory_hook.py` (which is now the loader that
runs it from cached bytecode, see `_test_entry_point.py`), seven of those suites went red at
once, all for the same reason and none of them about the thing they were pinning. The path lives
here now, so the next move costs one line instead of seven.

`memory_hook.py` remains the module those suites IMPORT - the namespace is unchanged and every
monkeypatch point is still on it. Only the text is here.
"""
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "nevertwice" / "_engine.py"
SRC = PATH.read_text(encoding="utf-8")
