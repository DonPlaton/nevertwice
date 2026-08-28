"""I3: the pack that ships with the tool, because structural failure shapes are portable.

This is the delivery vehicle for the cold-start claim, and the claim is narrow enough to
state exactly:

> A scar requires you to fall first. On a new repository the store is empty, so every
> retrieve-and-inject system returns nothing **by construction** -- Nevertwice as it ships
> today included. An invariant checks a property of the code itself, so it can ship
> preconfigured and work from minute zero.

Whether that is *worth* anything is T3's question, measured on the `LIVE_VALIDATION.md`
stand against Nevertwice-with-an-empty-store. This module is only the part that makes the
question askable.

## What is allowed in here, and what is not

Every checker below is:

* **deterministic** -- no model, no embedding, no network. A language model cannot see its
  own blind spot, which is the entire reason invariants exist;
* **structural** -- it reads the diff, not the prose. "You probably meant" is not an
  invariant;
* **portable** -- true of Python as a language or of a shape any project can have, never of
  this project's habits. A pack full of one repository's idioms is a scar collection with a
  different name;
* **quiet by default** -- it fires on a property being violated, not on a property being
  present.

The pack is deliberately small. `BLAST_RADIUS_D5.md` measured what happens when a checker
that is right 91% of the time also fires on 28% of the commits that broke nothing, and the
lesson taken here is that the number of shipped invariants is a budget, not a feature list.
"""

from __future__ import annotations

import ast
from typing import Callable

from invariant_notes import Finding, make_invariant

# --------------------------------------------------------------------------
# checkers
# --------------------------------------------------------------------------


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except (SyntaxError, ValueError, RecursionError):
        return False


def mutable_default(before: dict[str, str], after: dict[str, str],
                    note: dict) -> list[Finding]:
    """A parameter defaulting to a list, dict or set, introduced by this diff.

    The default is evaluated once at definition time, so every call shares one object.
    It is the most-taught Python bug that still ships, it is decidable from the AST
    alone, and it is a property of the language rather than of anyone's codebase.

    Only *newly introduced* ones fire. A file that already had one is not this diff's
    problem, and reporting it would make every unrelated edit to that file noisy --
    which is how an invariant gets switched off in week one.
    """
    out: list[Finding] = []
    for path, new_src in after.items():
        if not path.endswith(".py") or not _parses(new_src):
            continue
        old = _mutable_defaults(before.get(path, ""))
        for qualname, arg in _mutable_defaults(new_src):
            if (qualname, arg) in old:
                continue
            out.append(Finding(
                note["id"], note["message"], subject=f"{path}::{qualname}({arg}=...)",
                rank=1.0,
                evidence="the default is created once at definition time, "
                         "so every call shares it",
            ))
    return out


def _mutable_defaults(source: str) -> set[tuple[str, str]]:
    if not source or not _parses(source):
        return set()
    found: set[tuple[str, str]] = set()
    tree = ast.parse(source)
    stack: list[tuple[list[str], ast.AST]] = [([], n) for n in tree.body]
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, ast.ClassDef):
            stack.extend((prefix + [node.name], c) for c in node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(prefix + [node.name])
            a = node.args
            positional = list(a.posonlyargs) + list(a.args)
            pad = len(positional) - len(a.defaults)
            pairs = [(positional[pad + i], d) for i, d in enumerate(a.defaults)]
            pairs += [(arg, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults)
                      if d is not None]
            for arg, default in pairs:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    found.add((qualname, arg.arg))
            stack.extend((prefix + [node.name], c) for c in node.body)
    return found


def bare_except(before: dict[str, str], after: dict[str, str],
                note: dict) -> list[Finding]:
    """`except:` with no exception type, introduced by this diff.

    It swallows `KeyboardInterrupt` and `SystemExit`, so it turns a Ctrl-C into a
    silent no-op and a deliberate shutdown into a hang. `except Exception:` is almost
    always what was meant, and the difference is invisible until the day it matters.

    `except Exception:` does **not** fire. It is a judgement call, and an invariant
    that fires on judgement calls spends its two-false-positive budget on the first
    reviewer who disagrees.
    """
    out: list[Finding] = []
    for path, new_src in after.items():
        if not path.endswith(".py") or not _parses(new_src):
            continue
        old = _bare_excepts(before.get(path, ""))
        for line, ctx in _bare_excepts(new_src):
            if any(c == ctx for _l, c in old):
                continue
            out.append(Finding(
                note["id"], note["message"], subject=f"{path}:{line}", rank=0.8,
                evidence="a bare except also catches KeyboardInterrupt and SystemExit",
            ))
    return out


def _bare_excepts(source: str) -> set[tuple[int, str]]:
    if not source or not _parses(source):
        return set()
    out: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            body = ""
            try:
                body = ast.unparse(node)[:120]
            except Exception:  # pragma: no cover
                body = ""
            out.add((node.lineno, body))
    return out


def assert_in_shipped_code(before: dict[str, str], after: dict[str, str],
                           note: dict) -> list[Finding]:
    """A bare `assert` introduced outside a test file.

    `python -O` removes it. Code whose correctness depends on an assertion that the
    interpreter is entitled to delete is code with a flag-dependent contract, and
    nobody discovers which flag until production runs with the other one.

    Test files are exempt because that is what `assert` is for there.
    """
    out: list[Finding] = []
    for path, new_src in after.items():
        low = path.replace("\\", "/").lower()
        if not path.endswith(".py") or not _parses(new_src):
            continue
        if "/test" in "/" + low or low.split("/")[-1].startswith(("test_", "_test")):
            continue
        old = _asserts(before.get(path, ""))
        for line, text in _asserts(new_src):
            if any(t == text for _l, t in old):
                continue
            out.append(Finding(
                note["id"], note["message"], subject=f"{path}:{line}", rank=0.6,
                evidence="python -O removes assert, so this contract depends on a flag",
            ))
    return out


def _asserts(source: str) -> set[tuple[int, str]]:
    if not source or not _parses(source):
        return set()
    out: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assert):
            try:
                out.add((node.lineno, ast.unparse(node)[:120]))
            except Exception:  # pragma: no cover
                out.add((node.lineno, ""))
    return out


# --------------------------------------------------------------------------
# the pack
# --------------------------------------------------------------------------


REGISTRY: dict[str, Callable[..., list[Finding]]] = {
    "mutable_default": mutable_default,
    "bare_except": bare_except,
    "assert_in_shipped_code": assert_in_shipped_code,
}


PACK = (
    ("mutable_default",
     "a mutable default is created once at definition time, so every call shares it"),
    ("bare_except",
     "a bare except also catches KeyboardInterrupt and SystemExit; "
     "`except Exception:` is almost certainly what was meant"),
    ("assert_in_shipped_code",
     "python -O deletes assert, so a contract enforced by one is enforced by a flag"),
)


def build_pack(date: str | None = None) -> list[dict]:
    """The notes that ship with the tool, all advisory, all `preconfigured`.

    Provenance is the whole point of the field: a reader can tell at a glance which
    findings came from something that happened here and which shipped in the box, and
    the two have different claims to authority.
    """
    return [
        make_invariant(checker, message, subject=checker,
                       provenance="preconfigured", date=date)
        for checker, message in PACK
    ]
