"""Does this call site bind against this definition? CPython's rule, one implementation.

Extracted from `mutate.py` in F1 because two different things now need it: the mutation
generator, which uses it to decide that a planted call really is broken, and the checker's
abstention policy, which uses it to decide whether it can *say* anything at all.

Sharing this is deliberate and is not the circularity `ABSTENTION_F1.md` §1 warns about.
What is shared is the meaning of "cannot bind" -- CPython's argument-binding semantics,
which `verify_mutants.py` checked against `inspect.Signature.bind` and found agreeing 868
times out of 868. What is *not* shared, and must not be, is the rule deciding when a
finding is undecidable: the answer key's oracle owns that, the abstention ladder owns its
own, and their agreement is a consistency check rather than evidence.

Conservative in one direction only. Anything the analysis cannot settle returns None, so
an unproved candidate is dropped rather than counted.
"""

from __future__ import annotations

import ast

from sigscan import Def, RefSite


def call_fails(site: RefSite, d: Def) -> str | None:
    """Why this call raises `TypeError` against this definition, or None if it works.

    `*args` or `**kwargs` at the *call* site makes the arity unknowable statically, and
    such calls are never used as evidence of breakage.
    """
    if d.kind != "func" or site.has_star:
        return None
    required, n_pos, star, kwonly_required, names = d.arity()
    kwargs_sink = any(p.kind == "kwarg" for p in d.params)

    supplied_by_kw = site.kwnames & names
    if site.n_pos > n_pos and not star:
        return f"passes {site.n_pos} positional arguments, the callee takes at most {n_pos}"
    if not kwargs_sink:
        unknown = sorted(site.kwnames - names)
        if unknown:
            return f"passes keyword {unknown[0]!r}, which the callee does not accept"
    missing_kwonly = sorted(kwonly_required - site.kwnames)
    if missing_kwonly:
        return f"omits required keyword-only parameter {missing_kwonly[0]!r}"
    # Positional shortfall: count what the call supplies positionally plus by keyword.
    covered = site.n_pos + len(supplied_by_kw)
    if covered < required and not site.kwnames - names:
        return f"supplies {covered} of {required} required parameters"
    return None


def module_of(path: str) -> str:
    mod = path.replace("\\", "/").removesuffix(".py").replace("/", ".")
    return mod.removesuffix(".__init__")


def import_sources(source: str, name: str) -> list[str]:
    """Module names this file imports `name` from, absolute suffixes only.

    Relative imports are resolved to their dotted tail (`from ._utils import x` ->
    `_utils`); the comparison against a defining file is a suffix match, so a tail is
    enough to decide whether the symbol came from *that* module without reconstructing
    the whole package hierarchy.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if any(a.name == name for a in node.names):
                out.append(node.module)
    return out


def defines(module_suffix: str, defining_path: str) -> bool:
    a = module_suffix.split(".")
    b = module_of(defining_path).split(".")
    return len(a) <= len(b) and b[-len(a):] == a
