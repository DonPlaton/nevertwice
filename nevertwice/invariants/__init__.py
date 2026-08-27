"""Structural invariants for Nevertwice.

A *scar* is knowledge induced from something that already broke. An
*invariant* is a falsifiable claim about the codebase that can be checked
before anything breaks. Both live as notes in the same vault, both compile
to guards, and both cost zero context tokens until they fire.

This package holds the checkers. They are deterministic by construction --
no model, no embedding, no network -- because a language model cannot see
its own blind spot, and the whole reason invariants exist is to cover it.

Nothing here is imported eagerly: attribute access pulls the module in on
first use, so ``import nevertwice.invariants`` stays free.

    >>> from nevertwice.invariants import check_working_tree
    >>> verdict = check_working_tree()
    >>> verdict.ok
    True
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "ContractChange",
    "Ref",
    "Symbol",
    "Verdict",
    "check_sources",
    "check_working_tree",
]

#: Registry of available invariants. Future checkers append here; each entry
#: maps a stable id to the module that implements it. Keeping it declarative
#: means an external package can register a checker without editing core.
INVARIANTS: dict[str, str] = {
    "blast_radius": "nevertwice.invariants.blast_radius",
}

_EXPORTS = {name: "blast_radius" for name in __all__}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache, so this happens once
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .blast_radius import (  # noqa: F401
        ContractChange,
        Ref,
        Symbol,
        Verdict,
        check_sources,
        check_working_tree,
    )
