"""An answer-key signature scanner, written independently of the checker under test.

`nevertwice/invariants/blast_radius.py` already extracts symbols and diffs contracts.
Reusing it here would make the answer key a function of the instrument: a defect in
the extractor would silently become a defect in the ground truth, and the measurement
would confirm the checker's own blind spots instead of exposing them. Rule 2 of
`.loop/GOAL-INVARIANTS.md` §0 says positives must be confirmed *independently of the
mechanism under test*, and this module is what that costs.

Two consequences, both intentional:

* Tuple-unpacking targets (`A, B, C = load()`) are recorded here from the first line
  of code. The checker's failure to see them is D2, a defect to fix; it must not be
  a hole in the key that hides D2's own effect.
* The comparison is deliberately *coarser* than the checker's. It answers "did the
  parameter list of this callable change in a way a caller can observe", not "is
  this change compatible". Compatibility is the checker's job, and the key must not
  pre-judge it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# definitions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # posonly | pos | vararg | kwonly | kwarg
    has_default: bool


@dataclass(frozen=True)
class Def:
    """A callable or class, as callers can observe it."""

    qualname: str
    kind: str  # func | class | binding
    lineno: int
    params: tuple[Param, ...] = ()
    is_method: bool = False

    @property
    def short(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    def arity(self) -> tuple[int, int, bool, frozenset[str], frozenset[str]]:
        """(min positional, max positional, takes *args, required kwonly, all kwnames)."""
        pos = [p for p in self.params if p.kind in ("posonly", "pos")]
        offset = 1 if self.is_method and pos and pos[0].name in ("self", "cls") else 0
        pos = pos[offset:]
        required = sum(1 for p in pos if not p.has_default)
        star = any(p.kind == "vararg" for p in self.params)
        kwonly_req = frozenset(
            p.name for p in self.params if p.kind == "kwonly" and not p.has_default
        )
        names = frozenset(
            p.name for p in self.params if p.kind in ("pos", "kwonly")
        )
        return required, len(pos), star, kwonly_req, names


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[Param, ...]:
    a = node.args
    out: list[Param] = []
    positional = list(a.posonlyargs) + list(a.args)
    pad = len(positional) - len(a.defaults)
    for i, arg in enumerate(positional):
        kind = "posonly" if i < len(a.posonlyargs) else "pos"
        out.append(Param(arg.arg, kind, i >= pad))
    if a.vararg is not None:
        out.append(Param(a.vararg.arg, "vararg", True))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        out.append(Param(arg.arg, "kwonly", default is not None))
    if a.kwarg is not None:
        out.append(Param(a.kwarg.arg, "kwarg", True))
    return tuple(out)


class _DefScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.defs: dict[str, Def] = {}
        self._stack: list[str] = []
        self._in_class: list[bool] = []

    def _q(self, name: str) -> str:
        return ".".join([*self._stack, name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        q = self._q(node.name)
        self.defs[q] = Def(q, "class", node.lineno)
        self._stack.append(node.name)
        self._in_class.append(True)
        for child in node.body:
            self.visit(child)
        self._in_class.pop()
        self._stack.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        q = self._q(node.name)
        is_method = bool(self._in_class) and self._in_class[-1]
        self.defs[q] = Def(q, "func", node.lineno, _params(node), is_method)
        # Nested functions are invisible to callers outside the enclosing scope;
        # recording them would inflate the key with symbols nothing can reference.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _bind(self, target: ast.AST, lineno: int) -> None:
        """Record every name a binding introduces, including through tuples.

        `A, B, C = load()` binds three module-level names. The checker under test
        sees zero of them (D2); the answer key sees all three, from the start.
        """
        if isinstance(target, ast.Name):
            q = self._q(target.id)
            self.defs.setdefault(q, Def(q, "binding", lineno))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind(elt, lineno)
        elif isinstance(target, ast.Starred):
            self._bind(target.value, lineno)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(self._stack) <= 1:  # module level, or one class deep
            for t in node.targets:
                self._bind(t, node.lineno)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if len(self._stack) <= 1 and node.value is not None:
            self._bind(node.target, node.lineno)


#: Sources this module could not parse, by reason. A census that reaches back to
#: 2005 meets Python 2 syntax, and silently returning "no definitions" for a file
#: Python 3.14 cannot read would make an unreadable history look like a clean one.
PARSE_FAILURES: dict[str, int] = {}

#: Sources handed to `scan_defs`, so a failure count has a denominator.
PARSE_ATTEMPTS = [0]


def scan_defs(source: str) -> dict[str, Def]:
    PARSE_ATTEMPTS[0] += 1
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        PARSE_FAILURES[type(exc).__name__] = PARSE_FAILURES.get(type(exc).__name__, 0) + 1
        return {}
    scanner = _DefScanner()
    for node in tree.body:
        scanner.visit(node)
    return scanner.defs


# --------------------------------------------------------------------------
# references
# --------------------------------------------------------------------------


@dataclass
class RefSite:
    name: str
    lineno: int
    is_call: bool
    n_pos: int = 0
    kwnames: frozenset[str] = field(default_factory=frozenset)
    has_star: bool = False


class _RefScanner(ast.NodeVisitor):
    """Every mention of a watched name, with call shape where it is a call."""

    def __init__(self, names: set[str]) -> None:
        self.names = names
        self.sites: list[RefSite] = []

    def visit_Call(self, node: ast.Call) -> None:
        target = node.func
        name = None
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        if name in self.names:
            has_star = any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords
            )
            self.sites.append(
                RefSite(
                    name,
                    node.lineno,
                    True,
                    n_pos=sum(1 for a in node.args if not isinstance(a, ast.Starred)),
                    kwnames=frozenset(k.arg for k in node.keywords if k.arg),
                    has_star=has_star,
                )
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.names:
            self.sites.append(RefSite(node.id, node.lineno, False))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.names:
            self.sites.append(RefSite(node.attr, node.lineno, False))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in self.names:
                self.sites.append(RefSite(alias.name, node.lineno, False))


def scan_refs(source: str, names: set[str]) -> list[RefSite]:
    if not names:
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    scanner = _RefScanner(names)
    scanner.visit(tree)
    return scanner.sites


# --------------------------------------------------------------------------
# what changed
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SigDelta:
    qualname: str
    path: str
    kind: str  # params | removed
    before: str
    after: str


def _render(d: Def) -> str:
    if d.kind != "func":
        return d.kind
    bits = []
    for p in d.params:
        prefix = {"vararg": "*", "kwarg": "**"}.get(p.kind, "")
        bits.append(prefix + p.name + ("=..." if p.has_default and not prefix else ""))
    return f"{d.short}({', '.join(bits)})"


def import_bindings(source: str) -> set[str]:
    """Top-level names this module binds by importing them.

    A symbol can leave a module as a definition and arrive back as an import -- 
    ``from sphinx.addnodes import math_reference as eqref  # to keep compatibility``
    is real code from the corpus. Callers importing that name are unaffected, so it
    is not a removal. This is the compatibility-facade shape D4 exists to teach the
    checker; the answer key had the same blind spot, and C2's independent control
    found it before any measurement depended on it.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    out: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    for node in stack:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            stack.extend(node.body)
            stack.extend(getattr(node, "orelse", []))
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
    return out


def signature_deltas(old_src: str, new_src: str, path: str) -> list[SigDelta]:
    """Parameter-list changes and disappearances, ignoring annotations and bodies.

    Annotations are excluded *by construction* rather than filtered afterwards:
    the key must not depend on a rule the checker is also being asked to learn.
    """
    old = scan_defs(old_src)
    new = scan_defs(new_src)
    reimported = import_bindings(new_src)
    out: list[SigDelta] = []
    for q, d in old.items():
        n = new.get(q)
        if n is None:
            if "." not in q and q in reimported:
                continue  # left as a definition, came back as an import: a facade
            out.append(SigDelta(q, path, "removed", _render(d), ""))
        elif d.kind == "func" and n.kind == "func" and d.params != n.params:
            out.append(SigDelta(q, path, "params", _render(d), _render(n)))
    return out
