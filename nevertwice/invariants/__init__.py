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

The registry is currently **empty**. `blast_radius` was here and failed its gates
in `research/invariants_lab/BLAST_RADIUS_D5.md`; the declared consequence of failing
a gate is deletion, and this is what that looks like from inside the package.

    >>> from nevertwice.invariants import INVARIANTS
    >>> INVARIANTS
    {}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__: list[str] = []

#: Registry of available invariants. Future checkers append here; each entry
#: maps a stable id to the module that implements it. Keeping it declarative
#: means an external package can register a checker without editing core.
#:
#: **Empty, and that is a measurement rather than an omission.** `blast_radius` lived
#: here and was removed by T4: it failed two of its four declared gates in D5 --
#: precision 0.396 against a floor of 0.50, and a flag rate of 0.278 against a ceiling
#: of 0.05 -- and the declared consequence of failing a gate is deletion. The checker,
#: its 159 regressions and the measurement that closed it are kept in
#: `research/invariants_lab/`, because a negative result is only worth something if the
#: thing it is about can still be read.
INVARIANTS: dict[str, str] = {}

_EXPORTS: dict[str, str] = {name: INVARIANTS[name] for name in __all__}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value  # cache, so this happens once
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


# No TYPE_CHECKING imports: there is nothing in the package to import yet.
