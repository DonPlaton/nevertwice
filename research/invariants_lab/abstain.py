"""Emit only what can be decided -- the untried lever, as a ladder of six policies.

`BLAST_RADIUS_D5.md`: of 16,716 findings, 11,763 (70%) are undecidable; on the silence
pool 3,916 of 4,298 (91%) are, and the checker reports every one. It is not confidently
wrong there, it is *unable to say*, at volume. This module is the part that lets it keep
quiet, and `ABSTENTION_F1.md` declares the ladder and the decision rule before the run.

## The rule this module must obey

**No policy may consult the answer key.** A rule fitted to `measure_blast_radius`'s oracle
would make the precision column a measurement of two implementations agreeing with each
other, and would contaminate the recall column with the same circularity.
`tests/_test_abstention.py` asserts it statically: this file may not name the oracle, the
mutants, or any measurement harness.

What it *does* share is `binding.call_fails` -- CPython's argument-binding semantics,
verified against `inspect.Signature.bind` 868 times of 868. The meaning of "this call
cannot bind" is not the property in question; the decision of when to speak is.

## The ladder

Nested by construction, so `kept(P_i+1)` is always a subset of `kept(P_i)` and naming the
best is a choice among six rather than a search over 64 subsets:

* `emit-all`            -- the mechanism as measured in D5
* `no-bare-mentions`    -- a signature change cannot break a mention that is not a call
* `no-star-calls`       -- arity through `f(*args)` is not statically knowable
* `no-unknown-receiver` -- `obj.method(...)` on an unbound receiver may be any class
* `no-kind-changes`     -- what a function-became-a-class breaks depends on use
* `decidable-only`      -- emit only where the failure can be simulated
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import blast_radius_deleted as br
import binding
import sigscan

POLICIES: tuple[str, ...] = (
    "emit-all",
    "no-bare-mentions",
    "no-star-calls",
    "no-unknown-receiver",
    "no-kind-changes",
    "decidable-only",
)

_LEVEL = {name: i for i, name in enumerate(POLICIES)}


@dataclass(frozen=True)
class Finding:
    """One thing the checker would print, with everything a policy needs to judge it."""

    qualname: str
    path: str
    lineno: int
    kind: str    # call | name | import
    reason: str  # signature | removed | kind


class _FileFacts:
    """Per-file AST answers, computed once. A policy over 16,716 findings re-asks a lot."""

    def __init__(self, sources: dict[str, str]) -> None:
        self._sources = sources
        self._trees: dict[str, ast.Module | None] = {}
        self._bound: dict[str, set[str]] = {}
        self._refs: dict[tuple[str, str], list[sigscan.RefSite]] = {}
        self._defs: dict[str, dict[str, sigscan.Def]] = {}

    def source(self, path: str) -> str | None:
        return self._sources.get(path)

    def tree(self, path: str) -> ast.Module | None:
        if path not in self._trees:
            src = self._sources.get(path)
            try:
                self._trees[path] = ast.parse(src) if src is not None else None
            except (SyntaxError, ValueError, RecursionError):
                self._trees[path] = None
        return self._trees[path]

    def bound_names(self, path: str) -> set[str]:
        """Module-level names this file binds to a class or a module."""
        if path not in self._bound:
            tree = self.tree(path)
            self._bound[path] = br._bound_here(tree) if tree is not None else set()
        return self._bound[path]

    def call_sites(self, path: str, short: str) -> list[sigscan.RefSite]:
        key = (path, short)
        if key not in self._refs:
            src = self._sources.get(path) or ""
            self._refs[key] = sigscan.scan_refs(src, {short})
        return self._refs[key]

    def defs(self, path: str) -> dict[str, sigscan.Def]:
        if path not in self._defs:
            self._defs[path] = sigscan.scan_defs(self._sources.get(path) or "")
        return self._defs[path]


# ---------------------------------------------------------------------------
# the rules, one function each, in ladder order
# ---------------------------------------------------------------------------


def _is_bare_mention(f: Finding) -> bool:
    """P1. A signature growing a parameter cannot break `handler = render`.

    This is the one rung that is not about undecidability: it is a *known negative*, and
    it is first so the curve has a point where recall should not move at all.
    """
    return f.reason == "signature" and f.kind == "name"


def _is_star_call(f: Finding, facts: _FileFacts) -> bool:
    """P2. `render(*args)` has no statically knowable arity."""
    if f.kind != "call":
        return False
    sites = [s for s in facts.call_sites(f.path, f.qualname.rsplit(".", 1)[-1])
             if s.is_call and s.lineno == f.lineno]
    return any(s.has_star for s in sites)


def _is_unknown_receiver(f: Finding, facts: _FileFacts) -> bool:
    """P3. Does `x.member(...)` reach *this* class? Not decidable from a name.

    The checker already drops receivers it can see are a *different* class
    (`br._foreign_receiver`, which was 1,092 of 1,299 false findings when it was added).
    This rung drops receivers it cannot see at all -- a parameter, a local, a call
    result -- which is the majority of them and is where the recall is traded.
    """
    if "." not in f.qualname:
        return False  # a module-level function has no receiver
    owner = f.qualname.rpartition(".")[0].rsplit(".", 1)[-1]
    short = f.qualname.rsplit(".", 1)[-1]
    tree = facts.tree(f.path)
    if tree is None:
        return True  # cannot read the site: cannot decide it
    bound = facts.bound_names(f.path)
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) != f.lineno:
            continue
        if isinstance(node, ast.Name) and node.id == short:
            return False  # bound directly, not through a receiver
        if isinstance(node, ast.Attribute) and node.attr == short:
            recv = node.value
            if isinstance(recv, ast.Name) and recv.id == owner and recv.id in bound:
                return False  # `Mailer.send(...)` -- the receiver is the class itself
    return True


def _cannot_simulate(f: Finding, facts: _FileFacts,
                     changes: dict[str, br.ContractChange]) -> bool:
    """P5. Emit only where the failure can actually be reproduced by reading the code.

    Two shapes qualify, and they are the two the answer key's positive class is built
    from -- not because the oracle says so, but because they are the only two failures a
    static reader can prove: a call that cannot bind, and an import of a name that is no
    longer there.
    """
    change = changes.get(f.qualname)
    if change is None:
        return True

    if change.reason == "signature":
        short = f.qualname.rsplit(".", 1)[-1]
        new_def = facts.defs(change.path).get(f.qualname)
        if new_def is None or new_def.kind != "func":
            return True
        sites = [s for s in facts.call_sites(f.path, short)
                 if s.is_call and s.lineno == f.lineno and not s.has_star]
        return not any(binding.call_fails(s, new_def) is not None for s in sites)

    if change.reason == "removed":
        if "." in f.qualname:
            return True  # a removed *method* reached through any receiver is not provable
        defining = facts.defs(change.path)
        src = facts.source(change.path) or ""
        if f.qualname in defining or f.qualname in sigscan.import_bindings(src):
            return True  # still there under another binding: no breakage to prove
        if f.path == change.path:
            return False  # the reference is inside the module the symbol left
        ref_src = facts.source(f.path)
        if ref_src is None:
            return True
        short = f.qualname.rsplit(".", 1)[-1]
        return not any(binding.defines(s, change.path)
                       for s in binding.import_sources(ref_src, short))

    return True  # `kind`, and anything a later version of the checker invents


# ---------------------------------------------------------------------------


def decide(verdict, sources: dict[str, str], policy: str) -> list[Finding]:
    """The findings the checker emits under `policy`.

    `sources` is the post-edit tree the verdict was computed over -- what
    `check_sources` was handed as `scan`/`after`. The policies read it to look at the
    call site; nothing here re-derives the verdict.
    """
    if policy not in _LEVEL:
        raise ValueError(
            "unknown abstention policy " + repr(policy) + "; the ladder is "
            + ", ".join(POLICIES) + ". Falling back to emit-all would report the "
            "strictest policy's row with the baseline's numbers."
        )
    level = _LEVEL[policy]
    changes = {c.qualname: c for c in verdict.contract_changes}
    facts = _FileFacts(sources)

    out: list[Finding] = []
    for qualname, refs in verdict.unhandled.items():
        change = changes.get(qualname)
        reason = change.reason if change is not None else ""
        for ref in refs:
            if ref.confidence != "high":
                continue
            f = Finding(qualname, ref.path, ref.lineno, ref.kind, reason)
            if level >= 1 and _is_bare_mention(f):
                continue
            if level >= 2 and _is_star_call(f, facts):
                continue
            if level >= 3 and _is_unknown_receiver(f, facts):
                continue
            if level >= 4 and f.reason == "kind":
                continue
            if level >= 5 and _cannot_simulate(f, facts, changes):
                continue
            out.append(f)
    return sorted(out, key=lambda f: (f.qualname, f.path, f.lineno))
