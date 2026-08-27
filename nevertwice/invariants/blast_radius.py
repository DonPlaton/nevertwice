#!/usr/bin/env python3
"""Blast-radius guard: a structural invariant for Nevertwice.

A *scar* remembers a mistake that already happened. An *invariant* states
something that must stay true, and is checkable before any mistake happens.
This module is the first invariant, and it answers one question:

    "You changed a contract. Who else depends on it, and did you update them?"

It is deliberately dumb. There is no model in the loop, no embedding, no
network. Symbols and references are extracted with ``ast``; the changed line
ranges come from ``difflib``; the file list comes from ``git``. That is the
whole point: an LLM cannot see that spaghetti is spaghetti, so the judgement
must live outside the LLM, in something it cannot talk its way past.

Design rules this file obeys, and any future invariant should too:

  * stdlib only (Python 3.10+), no imports from the rest of Nevertwice;
  * import-safe -- importing it costs nothing and touches no disk;
  * advisory by default -- exit code 0 unless ``--enforce`` is passed, so
    wiring it into a hook can never break someone's workflow;
  * killable -- ``NEVERTWICE_BLAST_RADIUS=0`` makes it a no-op immediately;
  * crash-proof -- any internal failure degrades to "clean", never to a
    traceback in the caller's face.

CLI
---
    python -m nevertwice.invariants.blast_radius
    python -m nevertwice.invariants.blast_radius --scope L0 --enforce
    python -m nevertwice.invariants.blast_radius --base main --json

API
---
    from nevertwice.invariants import check_working_tree, check_sources
"""

from __future__ import annotations

import argparse
import ast
import difflib
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Symbol",
    "ContractChange",
    "Ref",
    "Reexport",
    "Verdict",
    "check_sources",
    "check_working_tree",
    "find_reexports",
    "resolve_facades",
    "main",
]

# --------------------------------------------------------------------------
# tunables
# --------------------------------------------------------------------------

#: A reference is treated as "handled" if its line is inside a changed range,
#: give or take this many lines. Edits rarely land on the exact same line.
LINE_FUZZ = 2

#: Budgets per scope class: (max files, max directories, max changed lines).
#: Over-reach is a violation just as much as under-reach.
#:
#: Calibrated on 2026-08-27 against 150 commits of this repository - see
#: research/BLAST_RADIUS_THRESHOLDS.md for the rule that chose them (95th percentile of the
#: class's own observed distribution) and research/BLAST_RADIUS_CALIBRATION.md for the
#: distribution itself. The shipped guesses were (3, 1, 150) and (12, 4, 500).
BUDGETS = {
    "L0": (10, 3, 300),
    "L1": (19, 3, 350),
    "L2": (None, None, None),
}

#: When a budget applies. ``"declared"`` charges a diff only against a class the caller
#: actually declared; ``"always"`` also charges it against the class inferred from the diff.
#:
#: "always" was the shipped behaviour and it is incoherent. ``_infer_scope`` calls a diff L0
#: *because* it changed no contract, and the budget then flags it for touching four files -
#: two rules reading the same evidence and reaching opposite conclusions. Replayed over 150
#: commits it flagged 83% of them and produced no dependency finding at all. A declaration is
#: a promise the caller made and may be held to; an inference is this module's own guess and
#: is not evidence of anything. The constant stays so the old behaviour remains measurable.
BUDGET_SCOPE = "declared"

#: Force the L2 plan requirement on every repository, rather than only those that opted in.
#: Opting in means having a ``.nevertwice/`` directory: a convention a codebase does not use
#: must not manufacture a problem in it. Kept as a constant for the same reason as above.
PLAN_ALWAYS_REQUIRED = False

#: Directories never scanned for references.
SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        "node_modules", "vendor", "third_party",
        "venv", ".venv", "env", ".env", "site-packages",
        "build", "dist", ".eggs", "htmlcov", ".nevertwice",
    }
)

#: Names so common that a reference match means very little on its own.
AMBIGUOUS_NAMES = frozenset(
    """
    add apply build call check clear close config count create data delete
    dump execute file filter first flush format get handle id index init items
    key keys last load main model name next open parse path process read remove
    reset result run save send set setup size start state step stop test text
    to_dict train type update validate value values write
    """.split()
)

#: A name shorter than this is treated as low-confidence regardless.
SHORT_NAME_LEN = 4

#: A symbol with more references than this is almost certainly ambiguous.
NOISE_REF_CAP = 200

#: Files larger than this are skipped during the reference scan.
MAX_FILE_BYTES = 1_000_000

#: Hard ceiling on files scanned, so a monorepo cannot hang a hook.
MAX_SCAN_FILES = 20_000

#: Hard ceiling on references collected. Past this the names are noise by
#: definition, and collecting more cannot change the verdict.
MAX_TOTAL_REFS = 5_000

_MISSING = object()
_ENV_DISABLE = "NEVERTWICE_BLAST_RADIUS"
_IGNORE_FILE = Path(".nevertwice") / "blast_radius.ignore"
_PLAN_FILE = Path(".nevertwice") / "plan.md"


# --------------------------------------------------------------------------
# output encoding
# --------------------------------------------------------------------------
#
# This project has already lost an MCP server to a cp1251 console (a Cyrillic
# byte 0x98 in a payload), and the first draft of this checker crashed with
# UnicodeEncodeError the moment it printed its own warning sign on the
# author's default terminal. A guard whose whole promise is "it can never
# break your workflow" must not die printing its verdict.
#
# The repair deliberately does NOT follow the rest of the package's idiom of
# ``sys.stdout.reconfigure(encoding="utf-8")`` at import: this module is
# imported by a library and by an MCP server, and silently rebinding the
# caller's stdout is a global side effect a checker has no business causing.
# Instead ``render`` asks the stream what it can encode and downgrades only
# what it must.

#: Decorative glyphs, and the ASCII that means the same thing. Only these are
#: substituted; everything else falls through to the last-resort replace, so a
#: Cyrillic path name degrades to '?' rather than taking the process down.
ASCII_FALLBACK = {
    "\u2713": "OK",     # check mark
    "\u26a0": "!",      # warning sign
    "\u2022": "*",      # bullet
    "\u2014": "-",      # em dash
    "\u2192": "->",     # rightwards arrow
    "\u2026": "...",    # horizontal ellipsis
}


def _stream_encoding(stream: object | None) -> str | None:
    """The encoding *stream* writes with, or None when anything goes.

    ``io.StringIO`` and friends have no ``encoding`` at all and accept every
    code point; treating that as "no constraint" keeps tests unaffected.
    """
    if stream is None:
        return None
    encoding = getattr(stream, "encoding", None)
    return encoding if isinstance(encoding, str) and encoding else None


def encode_safely(text: str, stream: object | None = None) -> str:
    """*text*, guaranteed to survive ``stream.write`` without raising.

    Three stages, cheapest first: if the stream already encodes the text,
    return it untouched; otherwise swap decorative glyphs for ASCII; only if
    that still does not fit, replace the remaining unencodable code points.
    """
    encoding = _stream_encoding(stream)
    if encoding is None:
        return text
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    downgraded = text
    for glyph, plain in ASCII_FALLBACK.items():
        downgraded = downgraded.replace(glyph, plain)
    try:
        downgraded.encode(encoding)
        return downgraded
    except UnicodeEncodeError:
        return downgraded.encode(encoding, "replace").decode(encoding, "replace")
    except LookupError:  # pragma: no cover - unknown codec name
        return downgraded


def _write(stream, text: str) -> None:
    """Print *text* to *stream*, degrading rather than raising."""
    try:
        stream.write(encode_safely(text, stream) + "\n")
    except UnicodeEncodeError:  # pragma: no cover - stream lied about itself
        stream.write(text.encode("ascii", "replace").decode("ascii") + "\n")


def _disabled() -> bool:
    return os.environ.get(_ENV_DISABLE, "").strip().lower() in {"0", "false", "off", "no"}


# --------------------------------------------------------------------------
# symbols
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """A callable's parameter list, without annotations or defaults' values.

    The rendered ``signature`` is what a human reads; this is what a caller can
    actually observe. Two signatures whose *text* differs can accept exactly the
    same calls -- a widening -- and comparing the text cannot tell the difference.
    That confusion was 33% of the findings in the precision census.
    """

    params: tuple[tuple[str, str, bool], ...]  # (name, kind, has_default)
    decorators: tuple[str, ...]

    def positional(self) -> list[tuple[str, str, bool]]:
        return [p for p in self.params if p[1] in ("posonly", "pos")]

    def kwonly(self) -> dict[str, bool]:
        return {p[0]: p[2] for p in self.params if p[1] == "kwonly"}

    def has(self, kind: str) -> bool:
        return any(p[1] == kind for p in self.params)

    def keyword_names(self) -> set[str]:
        return {p[0] for p in self.params if p[1] in ("pos", "kwonly")}


@dataclass(frozen=True)
class Symbol:
    """One named, externally-visible thing in a module."""

    qualname: str
    kind: str  # function | async function | method | class | constant
    lineno: int
    signature: str  # the contract: what callers are allowed to rely on
    body_hash: str  # normalised implementation, for "body changed" only
    shape: Shape | None = None  # callables only; None for classes and constants

    @property
    def short(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


def accepts_everything(old: Shape, new: Shape) -> bool:
    """Does *new* accept every call *old* accepted?

    A widening. The conditions are each one way a caller can be written, and each
    is a way this can be false:

    * a positional argument the old signature took must still be takeable at that
      index, under the same name unless the old one was positional-only -- so a
      rename, a reorder, or a promotion to keyword-only all break callers;
    * a parameter the old signature defaulted must still default, or a caller that
      omitted it now fails;
    * an added parameter must default, or a caller that never knew about it fails;
    * every keyword the old accepted must still be accepted, by name or by
      ``**kwargs``;
    * ``*args`` and ``**kwargs`` cannot disappear;
    * and the decorator list must be identical, because a decorator can change what
      the call returns without touching a single parameter.
    """
    if old.decorators != new.decorators:
        return False

    o, n = old.positional(), new.positional()
    for i, (name, kind, defaulted) in enumerate(o):
        if i >= len(n):
            if not new.has("vararg"):
                return False
            if kind != "posonly" and not new.has("kwarg"):
                return False   # a keyword caller of this name has nowhere to go
            continue
        nname, nkind, ndefault = n[i]
        if kind != "posonly":
            if nname != name or nkind == "posonly":
                return False   # renamed, reordered, or keyword access withdrawn
        if defaulted and not ndefault:
            return False       # a caller that omitted it now fails
    for name, kind, defaulted in n[len(o):]:
        if not defaulted:
            return False       # a new required positional
    if old.has("vararg") and not new.has("vararg"):
        return False
    if old.has("kwarg") and not new.has("kwarg"):
        return False

    okw, nkw = old.kwonly(), new.kwonly()
    for name, defaulted in okw.items():
        if name in nkw:
            if defaulted and not nkw[name]:
                return False
        elif name in new.keyword_names():
            pass               # became an ordinary parameter: still keyword-callable
        elif not new.has("kwarg"):
            return False
    for name, defaulted in nkw.items():
        if not defaulted and name not in okw:
            return False       # a new required keyword-only parameter
    return True


def _unparse(node: ast.AST | None, limit: int = 80) -> str:
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total in practice
        return "<?>"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _arg_repr(arg: ast.arg, default: object = _MISSING) -> str:
    out = arg.arg
    if arg.annotation is not None:
        out += ": " + _unparse(arg.annotation, 40)
    if default is not _MISSING:
        out += "=" + _unparse(default, 30)  # type: ignore[arg-type]
    return out


def _func_shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Shape:
    a = node.args
    params: list[tuple[str, str, bool]] = []
    positional = list(a.posonlyargs) + list(a.args)
    pad = len(positional) - len(a.defaults)
    for i, arg in enumerate(positional):
        params.append(
            (arg.arg, "posonly" if i < len(a.posonlyargs) else "pos", i >= pad)
        )
    if a.vararg is not None:
        params.append((a.vararg.arg, "vararg", True))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append((arg.arg, "kwonly", default is not None))
    if a.kwarg is not None:
        params.append((a.kwarg.arg, "kwarg", True))
    return Shape(tuple(params), tuple(_unparse(d, 40) for d in node.decorator_list))


def _func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    pad = len(positional) - len(a.defaults)
    defaults: list[object] = [_MISSING] * pad + list(a.defaults)

    parts: list[str] = []
    for i, arg in enumerate(positional):
        parts.append(_arg_repr(arg, defaults[i]))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            parts.append("/")
    if a.vararg is not None:
        parts.append("*" + _arg_repr(a.vararg))
    elif a.kwonlyargs:
        parts.append("*")
    for arg, kwd in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(_arg_repr(arg, _MISSING if kwd is None else kwd))
    if a.kwarg is not None:
        parts.append("**" + _arg_repr(a.kwarg))

    sig = f"{node.name}({', '.join(parts)})"
    if node.returns is not None:
        sig += " -> " + _unparse(node.returns, 40)
    for dec in node.decorator_list:
        sig = "@" + _unparse(dec, 40) + " " + sig
    return sig


def _class_signature(node: ast.ClassDef) -> str:
    bases = [_unparse(b, 40) for b in node.bases]
    bases += [f"{k.arg}={_unparse(k.value, 30)}" for k in node.keywords]
    public = sorted(
        n.name
        for n in node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    )
    sig = f"class {node.name}({', '.join(bases)}) :: {', '.join(public)}"
    for dec in node.decorator_list:
        sig = "@" + _unparse(dec, 40) + " " + sig
    return sig


def _body_hash(nodes: list[ast.stmt]) -> str:
    try:
        text = "\n".join(ast.unparse(s) for s in nodes)
    except Exception:  # pragma: no cover
        return ""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


class _Collector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.symbols: dict[str, Symbol] = {}

    def _q(self, name: str) -> str:
        return ".".join(self.stack + [name])

    def _record(self, node, name: str, kind: str, signature: str, body: list,
                shape: "Shape | None" = None) -> None:
        q = self._q(name)
        self.symbols[q] = Symbol(q, kind, node.lineno, signature, _body_hash(body), shape)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, node.name, "class", _class_signature(node), node.body)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def _function(self, node, kind: str) -> None:
        self._record(node, node.name, kind, _func_signature(node), node.body,
                     _func_shape(node))
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, "method" if self.stack else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, "async method" if self.stack else "async function")

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.stack:
            return  # module-level constants only
        for target in node.targets:
            if isinstance(target, ast.Name):
                q = self._q(target.id)
                self.symbols[q] = Symbol(
                    q, "constant", node.lineno, "", _body_hash([node])
                )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.stack or not isinstance(node.target, ast.Name):
            return
        q = self._q(node.target.id)
        self.symbols[q] = Symbol(q, "constant", node.lineno, "", _body_hash([node]))


def extract_symbols(source: str) -> dict[str, Symbol]:
    """Parse *source* into a qualname -> Symbol map. Never raises."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    collector = _Collector()
    try:
        collector.visit(tree)
    except RecursionError:  # pragma: no cover - pathological nesting
        return {}
    return collector.symbols


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractChange:
    """A change callers can observe: signature, disappearance, or nature."""

    qualname: str
    path: str
    lineno: int
    reason: str  # signature | removed | kind
    before: str
    after: str


def changed_lines(old: str, new: str) -> set[int]:
    """1-based line numbers in *new* that differ from *old*."""
    a = old.splitlines()
    b = new.splitlines()
    out: set[int] = set()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if j1 == j2:  # pure deletion: flag the seam
            out.add(max(1, j1))
            continue
        out.update(range(j1 + 1, j2 + 1))
    return out


def contract_changes(
    old_src: str, new_src: str, path: str
) -> tuple[list[ContractChange], set[str], set[str]]:
    """Return (contract changes, added qualnames, body-only-changed qualnames)."""
    old = extract_symbols(old_src)
    new = extract_symbols(new_src)

    changes: list[ContractChange] = []
    body_only: set[str] = set()

    for qualname, sym in new.items():
        prev = old.get(qualname)
        if prev is None:
            continue
        if prev.kind != sym.kind:
            changes.append(
                ContractChange(qualname, path, sym.lineno, "kind", prev.kind, sym.kind)
            )
        elif prev.signature != sym.signature:
            # The text differs; that is not the same as a caller being able to tell.
            if (
                prev.shape is not None
                and sym.shape is not None
                and accepts_everything(prev.shape, sym.shape)
            ):
                # Callers cannot observe it, so it is an implementation change.
                if prev.body_hash != sym.body_hash:
                    body_only.add(qualname)
                continue
            changes.append(
                ContractChange(
                    qualname, path, sym.lineno, "signature", prev.signature, sym.signature
                )
            )
        elif prev.body_hash != sym.body_hash:
            body_only.add(qualname)

    for qualname, sym in old.items():
        if qualname not in new:
            changes.append(
                ContractChange(qualname, path, sym.lineno, "removed", sym.signature or sym.kind, "")
            )

    added = set(new) - set(old)
    return changes, added, body_only


# --------------------------------------------------------------------------
# compatibility facades
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reexport:
    """A module-level name that forwards to a definition living somewhere else."""

    name: str            # the name callers still import from this module
    via: str             # how it is written, for the note: "_store_state.write_atomic"
    module_hint: str | None   # dotted hint at the module it came from, if one is visible
    target: str          # the name to look for inside that module


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Module-level names that stand for a module, and the dotted module they stand for.

    Three shapes, because a facade is only as findable as the alias in front of it:

      import pkg.mod as m              -> m   -> pkg.mod
      m = importlib.import_module("x") -> m   -> x
      m = _sibling("store_state")      -> m   -> store_state

    The third is a guess and is deliberately shallow: any call with exactly one string argument
    is treated as naming a module. It costs nothing when wrong - the hint only ever picks which
    file to *look in*, and a lookup that finds nothing degrades to a note rather than a verdict.
    """
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            args = node.value.args
            if len(args) == 1 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = args[0].value
    return aliases


def find_reexports(source: str) -> dict[str, Reexport]:
    """Module-level names bound to a definition that lives elsewhere.

    Only the forms where the *exported name is preserved* count, because that is what makes a
    caller safe: ``write_atomic = _store_state.write_atomic`` keeps every
    ``from memory_hook import write_atomic`` working. A rebinding to a differently-named thing
    (``write_atomic = _legacy_writer``) is not treated as a facade - the name resolves, but
    nothing structural says the two are the same function.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    aliases = _module_aliases(tree)
    out: dict[str, Reexport] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            hint = (node.module or "").lstrip(".")
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                out[bound] = Reexport(bound, f"{hint}.{alias.name}" if hint else alias.name,
                                      hint or None, alias.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            base = node.value.value
            for target in node.targets:
                if not isinstance(target, ast.Name) or node.value.attr != target.id:
                    continue
                base_name = base.id if isinstance(base, ast.Name) else None
                via = f"{base_name}.{node.value.attr}" if base_name else node.value.attr
                out[target.id] = Reexport(target.id, via,
                                          aliases.get(base_name or ""), node.value.attr)
    return out


def _module_source(hint: str, corpus: dict[str, str], root: Path | None) -> str | None:
    """The source of the module *hint* names, from the scanned corpus or from disk.

    Matched by dotted suffix: a hint of ``store_state`` finds ``nevertwice/store_state.py``, and
    ``nevertwice.store_state`` finds it too. Suffix matching rather than exact resolution because
    the checker never imports anything - it has no sys.path and no package context, only files.
    """
    parts = [p for p in hint.split(".") if p]
    if not parts:
        return None
    tail = "/".join(parts) + ".py"
    candidates = [rel for rel in corpus if rel == tail or rel.endswith("/" + tail)]
    if candidates:
        return corpus[min(candidates, key=len)]
    if root is None:
        return None
    listed = _tracked_files(root, {".py"}) or []
    for path in listed:
        rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
        if rel == tail or rel.endswith("/" + tail):
            return _read(path)
    return None


def _signature_of(source: str, name: str) -> tuple[str, str] | None:
    """(kind, signature) of the top-level *name* in *source*, or None when it is not there."""
    symbol = extract_symbols(source).get(name)
    return (symbol.kind, symbol.signature) if symbol else None


def resolve_facades(
    changes: list[ContractChange],
    before: dict[str, str],
    after: dict[str, str],
    corpus: dict[str, str],
    root: Path | None,
) -> tuple[list[ContractChange], list[str]]:
    """Drop the changes explained by a compatibility facade; return the notes that replace them.

    Three outcomes per facade, and the middle one is the reason this follows the pointer instead
    of just recognising it:

      * the target is found and its signature is unchanged -> not a contract change at all;
      * the target is found and its signature DIFFERS      -> still a contract change, now with
        the real before/after across the move;
      * the target cannot be found                         -> a note. The name still resolves for
        every caller, and nothing visible here says more than that.
    """
    kept: list[ContractChange] = []
    notes: list[str] = []
    reexports: dict[str, dict[str, Reexport]] = {}
    was: dict[str, dict[str, Symbol]] = {}

    for change in changes:
        if change.reason not in ("removed", "kind") or "." in change.qualname:
            kept.append(change)
            continue
        source = after.get(change.path)
        if source is None:
            kept.append(change)
            continue
        if change.path not in reexports:
            reexports[change.path] = find_reexports(source)
        facade = reexports[change.path].get(change.qualname)
        if facade is None:
            kept.append(change)
            continue

        target = None
        if facade.module_hint:
            module_source = _module_source(facade.module_hint, corpus, root)
            if module_source is not None:
                target = _signature_of(module_source, facade.target)

        # The old signature comes from the before-source, not from the change: a `kind` change
        # carries kinds ("function" -> "constant"), and comparing a signature against the word
        # "function" can never match, which silently turned every facade back into a finding.
        if change.path not in was:
            was[change.path] = extract_symbols(before.get(change.path, ""))
        previous = was[change.path].get(change.qualname)
        old_signature = previous.signature if previous else ""

        if target is None or not target[1] or not old_signature:
            notes.append(
                f"note: {change.qualname} is re-exported from {facade.via}; callers still "
                f"resolve it, but the definition it points at cannot be checked from here"
            )
            continue
        if target[1] == old_signature:
            notes.append(
                f"note: {change.qualname} moved to {facade.via} with an unchanged signature - "
                f"a compatibility facade, callers unaffected"
            )
            continue
        kept.append(
            ContractChange(change.qualname, change.path, change.lineno, "signature",
                           old_signature, target[1])
        )
    return kept, notes


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    path: str
    lineno: int
    name: str
    confidence: str  # high | low
    kind: str = "name"  # call | name | import


#: Which reference kinds actually have to change, per contract change reason.
#: An ``import`` line does not care that a signature grew an argument, but it
#: very much cares that the symbol vanished. Getting this wrong is the single
#: biggest source of false positives, and false positives kill the guard.
RELEVANT_KINDS = {
    "signature": frozenset({"call", "name"}),
    "kind": frozenset({"call", "name"}),
    "removed": frozenset({"call", "name", "import"}),
}


class _RefFinder(ast.NodeVisitor):
    def __init__(self, names: set[str], path: str, skip_lines: set[int]) -> None:
        self.names = names
        self.path = path
        self.skip_lines = skip_lines
        self.call_funcs: set[int] = set()
        self.refs: list[Ref] = []

    def _hit(self, name: str, node: ast.AST, kind: str | None = None) -> None:
        if name not in self.names:
            return
        lineno = getattr(node, "lineno", 0)
        if lineno in self.skip_lines:
            return
        if kind is None:
            kind = "call" if id(node) in self.call_funcs else "name"
        self.refs.append(Ref(self.path, lineno, name, "high", kind))

    def visit_Call(self, node: ast.Call) -> None:
        self.call_funcs.add(id(node.func))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self._hit(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._hit(node.attr, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._hit(alias.name, node, "import")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._hit(alias.name.split(".")[-1], node, "import")


def _definition_lines(tree: ast.AST, names: set[str]) -> set[int]:
    """Lines holding a ``def``/``class`` for one of *names*, to exclude."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in names:
                out.add(node.lineno)
    return out


def _refs_in_python(source: str, names: set[str], path: str) -> list[Ref]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        # Unparseable (wrong language, partial edit) -- fall back, low confidence.
        return _refs_in_text(source, names, path)
    finder = _RefFinder(names, path, _definition_lines(tree, names))
    try:
        finder.visit(tree)
    except RecursionError:  # pragma: no cover
        return []
    return finder.refs


def _refs_in_text(source: str, names: set[str], path: str) -> list[Ref]:
    if not names:
        return []
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names)) + r")\b")
    out: list[Ref] = []
    for i, line in enumerate(source.splitlines(), start=1):
        for match in pattern.finditer(line):
            out.append(Ref(path, i, match.group(1), "low"))
    return out


def _tracked_files(root: Path, extensions: set[str]) -> list[Path] | None:
    """The repository's own files, as git sees them, or None when git cannot say.

    Preferred over walking the tree. ``--exclude-standard`` applies .gitignore, which is the
    only reliable statement of what belongs to a project: SKIP_DIRS is a hand-kept list and
    cannot know that ``research/embed_universal/data/`` holds 5.3 GB of vendored clones. A
    reference inside somebody else's checked-out repository is not a caller of this one, so
    excluding it makes the answer more correct as well as faster.
    """
    listing = _git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], root)
    if listing is None:
        return None
    found: list[Path] = []
    for rel in listing.split("\0"):
        if not rel:
            continue
        if Path(rel).suffix not in extensions:
            continue
        parts = Path(rel).parts[:-1]
        if any(part in SKIP_DIRS for part in parts):
            continue
        path = root / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue  # listed but gone: a stale index entry, or a broken link
        found.append(path)
        if len(found) >= MAX_SCAN_FILES:
            break
    return found


def _iter_files(root: Path, extensions: set[str]) -> list[Path]:
    tracked = _tracked_files(root, extensions)
    if tracked is not None:
        return tracked
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if Path(filename).suffix not in extensions:
                continue
            path = Path(dirpath) / filename
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(path)
            if len(found) >= MAX_SCAN_FILES:
                return found
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class _Gate:
    """Two-stage pre-filter that runs before any AST parse.

    Parsing costs two orders of magnitude more than scanning text, and in a
    real repository almost no file mentions the changed symbols at all. So:

      1. a plain substring test rejects the bulk of files at C speed;
      2. only survivors pay for the word-boundary regex, which stops ``run``
         from matching ``runtime`` and dragging every file into the parser.

    Either stage alone is measurably worse than both: substring-only lets
    short names through everywhere, regex-only taxes every file up front.
    """

    __slots__ = ("literals", "pattern")

    def __init__(self, names: set[str]) -> None:
        ordered = sorted(names)
        self.literals = tuple(ordered)
        self.pattern = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in ordered) + r")\b"
        )

    def __call__(self, text: str) -> bool:
        for literal in self.literals:
            if literal in text:
                return self.pattern.search(text) is not None
        return False


def find_references(
    root: Path,
    names: set[str],
    extra_extensions: set[str] | None = None,
    sources: dict[str, str] | None = None,
) -> list[Ref]:
    """All references to *names* under *root* (or in *sources*, for tests)."""
    if not names:
        return []
    extra = extra_extensions or set()
    refs: list[Ref] = []

    gate = _Gate(names)
    if sources is not None:
        for rel, text in sources.items():
            if not gate(text):
                continue
            if rel.endswith(".py"):
                refs.extend(_refs_in_python(text, names, rel))
            elif Path(rel).suffix in extra:
                refs.extend(_refs_in_text(text, names, rel))
        return refs

    for path in _iter_files(root, {".py"} | extra):
        text = _read(path)
        if not text or not gate(text):
            continue  # cheap regex gate: parsing is ~100x the cost of this
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            refs.extend(_refs_in_python(text, names, rel))
        else:
            refs.extend(_refs_in_text(text, names, rel))
        if len(refs) >= MAX_TOTAL_REFS:
            # Already far past the point where any of these names could be
            # trusted; more evidence cannot change a low-confidence verdict.
            break
    return refs


def _confidence(name: str, count: int) -> str:
    if len(name) <= SHORT_NAME_LEN or name in AMBIGUOUS_NAMES or count > NOISE_REF_CAP:
        return "low"
    return "high"


# --------------------------------------------------------------------------
# verdict
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    ok: bool = True
    #: True only when the killswitch stopped the check from running at all.
    #: Kept separate from ``ok`` because "nothing was checked" and "checked
    #: and clean" are the same boolean and very different facts.
    disabled: bool = False
    declared: str | None = None
    inferred: str = "L0"
    problems: list[str] = field(default_factory=list)
    contract_changes: list[ContractChange] = field(default_factory=list)
    unhandled: dict[str, list[Ref]] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- serialisation ----------------------------------------------------

    @property
    def verdict(self) -> str:
        """The judgement as one word, for a caller that wants only that.

        ``to_dict`` is what the MCP tool returns, and section 4.4 of the
        integration spec calls that payload a *verdict* - yet it carried no
        field of that name, so every consumer had to re-derive the judgement
        from ``ok`` plus a scan of ``notes``. Naming it here makes the wire
        format say what it is, and keeps a third state ("disabled")
        distinguishable: a killswitched guard and a clean tree both report
        ``ok`` and must not look alike to an agent deciding whether it was
        actually checked.
        """
        if self.disabled:
            return "disabled"
        return "clean" if self.ok else "violation"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "ok": self.ok,
            "declared": self.declared,
            "inferred": self.inferred,
            "problems": list(self.problems),
            "contract_changes": [
                {
                    "qualname": c.qualname,
                    "path": c.path,
                    "line": c.lineno,
                    "reason": c.reason,
                    "before": c.before,
                    "after": c.after,
                }
                for c in self.contract_changes
            ],
            "unhandled": {
                q: [
                    {
                        "path": r.path,
                        "line": r.lineno,
                        "kind": r.kind,
                        "confidence": r.confidence,
                    }
                    for r in refs
                ]
                for q, refs in self.unhandled.items()
            },
            "stats": dict(self.stats),
            "notes": list(self.notes),
        }

    # -- rendering --------------------------------------------------------

    def render(self, max_refs: int = 6, *, stream: object | None = None) -> str:
        """Human-readable verdict, safe to write to *stream*.

        Pass the stream it is headed for and every glyph that stream cannot
        encode is downgraded before it gets there; pass nothing and the full
        Unicode form comes back, which is what the tests want.
        """
        return encode_safely(self._render(max_refs), stream)

    def _render(self, max_refs: int = 6) -> str:
        s = self.stats
        shape = (
            f"{s.get('files', 0)} file(s), {s.get('dirs', 0)} dir(s), "
            f"{s.get('lines', 0)} line(s)"
        )
        if self.ok:
            total = s.get("references", 0)
            left = sum(len(v) for v in self.unhandled.values())
            tail = f", {total - left}/{total} reference(s) handled" if total else ""
            head = f"\u2713 blast radius clean: {self.inferred}, {shape}{tail}"
            return head + ("\n  " + "\n  ".join(self.notes) if self.notes else "")

        declared = f"declared {self.declared}, " if self.declared else ""
        out = [f"\u26a0 blast radius: {declared}diff is {self.inferred} \u2014 {shape}"]
        for problem in self.problems:
            out.append(f"  ! {problem}")

        if self.contract_changes:
            out.append(f"\n  contract changed ({len(self.contract_changes)}):")
            for change in self.contract_changes:
                out.append(f"    \u2022 {change.path}:{change.lineno}  {change.qualname}  [{change.reason}]")
                if change.reason == "signature":
                    out.append(f"        {change.before}")
                    out.append(f"        \u2192 {change.after}")
                refs = self.unhandled.get(change.qualname, [])
                if refs:
                    low = sum(1 for r in refs if r.confidence == "low")
                    flag = "  (low confidence)" if low == len(refs) else ""
                    out.append(f"        {len(refs)} unhandled reference(s){flag}:")
                    for ref in refs[:max_refs]:
                        out.append(f"          {ref.path}:{ref.lineno}")
                    if len(refs) > max_refs:
                        out.append(f"          \u2026 and {len(refs) - max_refs} more")

        out.extend("  " + n for n in self.notes)
        return "\n".join(out)


# --------------------------------------------------------------------------
# core check
# --------------------------------------------------------------------------


def _load_ignore(root: Path) -> list[str]:
    path = root / _IGNORE_FILE
    if not path.exists():
        return []
    patterns = []
    for line in _read(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _ignored(qualname: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(qualname, p) for p in patterns)


def _infer_scope(n_contracts: int, files: int, dirs: int, lines: int, deleted: int) -> str:
    if deleted or dirs >= 4 or n_contracts >= 5 or lines >= 400:
        return "L2"
    if n_contracts:
        return "L1"
    return "L0"


def check_sources(
    before: dict[str, str],
    after: dict[str, str],
    *,
    scan: dict[str, str] | None = None,
    declared: str | None = None,
    root: Path | None = None,
    extra_extensions: set[str] | None = None,
    ignore: list[str] | None = None,
    plan_required: bool = False,
    plan_present: bool = False,
) -> Verdict:
    """Pure core. *before*/*after* map relative path -> file text.

    A path present in *before* but not *after* was deleted; absent from
    *before* means it is new. *scan* is the corpus searched for references;
    it defaults to *after* plus whatever is on disk under *root*.
    """
    verdict = Verdict(declared=declared)
    ignore = ignore if ignore is not None else (_load_ignore(root) if root else [])

    touched = sorted(set(before) | set(after))
    deleted = [p for p in touched if p not in after]

    all_changes: list[ContractChange] = []
    changed_ranges: dict[str, set[int]] = {}
    facade_inputs: dict[str, str] = {}
    total_lines = 0

    for path in touched:
        old_src = before.get(path, "")
        new_src = after.get(path, "")
        lines = changed_lines(old_src, new_src)
        changed_ranges[path] = lines
        total_lines += len(old_src.splitlines()) if path in deleted else len(lines)
        if not path.endswith(".py"):
            continue
        changes, _added, _body = contract_changes(old_src, new_src, path)
        all_changes.extend(c for c in changes if not _ignored(c.qualname, ignore))
        facade_inputs[path] = new_src

    # A symbol that left this module but is re-exported from it did not break anybody. This
    # runs BEFORE the scope is inferred, because a facade that is not a contract change must
    # not push the diff into L1 either - the classification and the problem come from the same
    # evidence, and E4's seam extraction is exactly the case that showed it.
    lookup = dict(scan) if scan is not None else {}
    lookup.update(after)
    all_changes, facade_notes = resolve_facades(
        all_changes, {p: before.get(p, "") for p in facade_inputs}, facade_inputs, lookup, root)
    verdict.notes.extend(facade_notes)

    dirs = {str(Path(p).parent) for p in touched}
    verdict.stats = {
        "files": len(touched),
        "dirs": len(dirs),
        "lines": total_lines,
        "deleted": len(deleted),
        "contract_changes": len(all_changes),
        "references": 0,
    }
    verdict.contract_changes = all_changes
    verdict.inferred = _infer_scope(len(all_changes), len(touched), len(dirs), total_lines, len(deleted))

    # --- references to every changed contract ---------------------------
    short_by_qual = {c.qualname: c.qualname.rsplit(".", 1)[-1] for c in all_changes}
    names = set(short_by_qual.values())
    refs: list[Ref] = []
    if names:
        if scan is not None:
            corpus = dict(scan)
            corpus.update(after)  # the post-edit state always wins
            refs = find_references(Path("."), names, extra_extensions, sources=corpus)
        elif root is not None:
            # On disk, a changed file already holds its "after" content, so
            # line numbers line up with the ranges computed above.
            refs = find_references(root, names, extra_extensions)
        else:
            refs = find_references(Path("."), names, extra_extensions, sources=dict(after))

    per_name: dict[str, list[Ref]] = {}
    for ref in refs:
        per_name.setdefault(ref.name, []).append(ref)
    verdict.stats["references"] = len(refs)

    for change in all_changes:
        short = short_by_qual[change.qualname]
        relevant = RELEVANT_KINDS.get(change.reason, frozenset({"call", "name"}))
        candidates = [r for r in per_name.get(short, []) if r.kind in relevant]
        conf = _confidence(short, len(candidates))
        unhandled = []
        for ref in candidates:
            touched_lines = changed_ranges.get(ref.path)
            if touched_lines is not None and any(
                abs(ref.lineno - line) <= LINE_FUZZ for line in touched_lines
            ):
                continue  # this call site moved in the same diff
            unhandled.append(Ref(ref.path, ref.lineno, ref.name, conf, ref.kind))
        if unhandled:
            verdict.unhandled[change.qualname] = sorted(
                unhandled, key=lambda r: (r.path, r.lineno)
            )

    # --- problems --------------------------------------------------------
    for qualname, refs_left in verdict.unhandled.items():
        high = [r for r in refs_left if r.confidence == "high"]
        if high:
            verdict.problems.append(
                f"{qualname}: contract changed, {len(high)} reference(s) left untouched"
            )
        else:
            verdict.notes.append(
                f"note: {qualname} has {len(refs_left)} possible reference(s), "
                f"name too common to be sure"
            )

    order = {"L0": 0, "L1": 1, "L2": 2}
    if declared in order and order[verdict.inferred] > order[declared]:
        verdict.problems.append(
            f"declared {declared} but the diff is {verdict.inferred} "
            f"({len(all_changes)} contract change(s), {len(deleted)} deletion(s))"
        )

    if declared in BUDGETS:
        budget_key = declared
    elif BUDGET_SCOPE == "always":
        budget_key = verdict.inferred
    else:
        budget_key = None
    max_files, max_dirs, max_lines = BUDGETS.get(budget_key, (None, None, None))
    if max_files is not None and len(touched) > max_files:
        verdict.problems.append(
            f"over-reach: {budget_key} allows {max_files} file(s), diff touches {len(touched)}"
        )
    if max_dirs is not None and len(dirs) > max_dirs:
        verdict.problems.append(
            f"over-reach: {budget_key} allows {max_dirs} director(y/ies), diff touches {len(dirs)}"
        )
    if max_lines is not None and total_lines > max_lines:
        verdict.problems.append(
            f"over-reach: {budget_key} allows {max_lines} line(s), diff changes {total_lines}"
        )
    if verdict.inferred == "L2" and not plan_present and (plan_required or PLAN_ALWAYS_REQUIRED):
        verdict.problems.append(
            f"L2 (architectural) requires a written plan at {_PLAN_FILE.as_posix()}"
        )

    verdict.ok = not verdict.problems
    return verdict


# --------------------------------------------------------------------------
# git front-end
# --------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _repo_root(start: Path) -> Path | None:
    out = _git(["rev-parse", "--show-toplevel"], start)
    return Path(out.strip()) if out and out.strip() else None


def _collect_git_state(
    root: Path, base: str, staged: bool
) -> tuple[dict[str, str], dict[str, str]] | None:
    args = ["diff", "--name-status", "--no-renames"]
    if staged:
        args.append("--cached")
    args.append(base)
    listing = _git(args, root)
    if listing is None:
        return None

    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[-1].strip()
        if status.startswith("D"):
            before[path] = _git(["show", f"{base}:{path}"], root) or ""
            continue
        if not status.startswith("A"):
            before[path] = _git(["show", f"{base}:{path}"], root) or ""
        if staged:
            after[path] = _git(["show", f":{path}"], root) or ""
        else:
            after[path] = _read(root / path)
    return before, after


def check_working_tree(
    root: Path | None = None,
    *,
    base: str = "HEAD",
    staged: bool = False,
    declared: str | None = None,
    extra_extensions: set[str] | None = None,
) -> Verdict:
    """Check the current working tree (or index) against *base*."""
    if _disabled():
        return Verdict(ok=True, disabled=True, notes=[f"{_ENV_DISABLE}=0, guard disabled"])

    start = Path(root) if root else Path.cwd()
    repo = _repo_root(start) or start
    state = _collect_git_state(repo, base, staged)
    if state is None:
        return Verdict(ok=True, notes=["not a git repository, or git unavailable"])

    before, after = state
    if not before and not after:
        return Verdict(ok=True, notes=["no changes against " + base])

    return check_sources(
        before,
        after,
        declared=declared,
        root=repo,
        extra_extensions=extra_extensions,
        # Opt-in: a repository signals that it works to the plan convention by having a
        # .nevertwice/ directory at all. Without one, an L2 diff is still reported as L2 -
        # the class is evidence - but it is not charged for a file it never agreed to write.
        plan_required=(repo / _PLAN_FILE.parent).is_dir(),
        plan_present=(repo / _PLAN_FILE).exists(),
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nevertwice-blast-radius",
        description="Structural invariant: did a contract change leave callers behind?",
    )
    p.add_argument("--root", default=None, help="repository root (default: cwd)")
    p.add_argument("--base", default="HEAD", help="git ref to compare against (default: HEAD)")
    p.add_argument("--staged", action="store_true", help="check the index instead of the working tree")
    p.add_argument(
        "--scope",
        choices=["L0", "L1", "L2"],
        default=os.environ.get("NEVERTWICE_SCOPE") or None,
        help="the change class the agent declared, for over-reach detection",
    )
    p.add_argument(
        "--also",
        default="",
        help="extra extensions to scan, comma separated (low confidence), e.g. .ts,.js",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", action="store_true", help="print nothing when clean")
    p.add_argument(
        "--enforce",
        action="store_true",
        help="exit 1 on violation (default is advisory: always exit 0)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    extra = {e if e.startswith(".") else "." + e for e in args.also.split(",") if e.strip()}

    try:
        verdict = check_working_tree(
            Path(args.root) if args.root else None,
            base=args.base,
            staged=args.staged,
            declared=args.scope,
            extra_extensions=extra or None,
        )
    except Exception as exc:  # never break the caller
        if args.json:
            # ensure_ascii keeps this printable on any console by construction.
            print(json.dumps({"verdict": "skipped", "ok": True, "error": str(exc)}))
        else:
            _write(sys.stderr, f"blast radius: skipped ({exc})")
        return 0

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))
    elif not (verdict.ok and args.quiet):
        _write(sys.stdout, verdict.render(stream=sys.stdout))

    return 1 if (args.enforce and not verdict.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
