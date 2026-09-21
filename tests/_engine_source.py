"""The engine's source text, for the suites that assert on the code rather than run it.

A dozen checks in this directory are static: they read the engine as text or parse it, and ask
questions like "is this field carried across an absorb" or "does the vault dry run only ever
open files for reading". They are the right shape for what they pin - a behavioural test cannot
easily prove the *absence* of a write - but each of them used to spell the path itself.

When the engine's body moved out of `nevertwice/memory_hook.py` (which is now the loader that
runs it from cached bytecode, see `_test_entry_point.py`), seven of those suites went red at
once, all for the same reason and none of them about the thing they were pinning. The path lives
here now, so the next move costs one line instead of seven.

That move has since happened: the body is no longer one file either. `_engine.py` is an ordered
index that execs `_engine_config.py`, `_engine_text.py` and six more into `memory_hook`'s own
`globals()`, so the namespace is still a single dictionary and every rebind still reaches the
code that reads it - but the text a static suite wants is now spread across eight files. `SRC`
is that text put back together: the parts in load order with their headers removed, which is
byte for byte the body that used to sit in `_engine.py`. A suite that greps it or parses it sees
exactly what it saw before, and the reconstruction is not a convenience - `_test_entry_point.py`
pins it against what the loader actually runs.

`memory_hook.py` remains the module those suites IMPORT - the namespace is unchanged and every
monkeypatch point is still on it. Only the text is here.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "nevertwice"

#: The loader itself. Still called `PATH` because that is what the suites already ask for, but
#: it now holds the index rather than the body; `PATHS` is where the body lives.
PATH = PKG / "_engine.py"

#: The last line of a part's header. Everything above it is this project's prose about why the
#: parts exist; everything below is engine body, unmodified. A marker rather than "skip the
#: leading comments", because part one's slice opens with a shebang and a docstring of its own.
MARK = "#<<<ENGINE-PART-BODY>>>\n"


def _part_names() -> tuple[str, ...]:
    """The load order, read from the loader's own `ENGINE_PARTS` tuple.

    Parsed rather than imported: this module is what the *static* suites use, and importing the
    engine to find out where the engine's text lives would drag the whole store bake-out into a
    suite whose whole point is that it never runs the thing.
    """
    tree = ast.parse(PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ENGINE_PARTS" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise RuntimeError(f"{PATH} no longer declares ENGINE_PARTS; the loader's shape changed")


def _body(path: Path) -> str:
    """One part with its header stripped - the engine text it contributes, and nothing else."""
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition(MARK)
    if not sep:
        raise RuntimeError(f"{path.name} has no body marker; it is not an engine part")
    return rest


PART_NAMES: tuple[str, ...] = _part_names()

#: Every file the loader runs, in load order.
PATHS: tuple[Path, ...] = tuple(PKG / n for n in PART_NAMES)

#: The whole engine body, reassembled. Concatenation in load order is the right join because the
#: partition was a cut of one file and nothing was reordered or edited to make it.
SRC: str = "".join(_body(p) for p in PATHS)
