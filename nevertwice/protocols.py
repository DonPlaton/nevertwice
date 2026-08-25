#!/usr/bin/env python3
"""The five seams a third party can implement - and the promise that implementing one costs
nothing but this file.

Every extension point in this system currently means importing `memory_hook`: a 6,000-line
module that resolves a vault path at import time, reads config, and pulls in half the engine.
So "swap the store for Postgres" or "add my agent as an episode source" is not an afternoon's
work, it is a decision to depend on the whole thing. That is the difference between a project
with a plugin surface and a project that merely has functions you *could* call.

This module is the plugin surface. It declares five protocols and **imports nothing from the
engine** - only `typing` and `schemas`, which is itself dependency-free. A third-party
implementer imports this file, writes a class, registers it, and is done. `tests/_test_protocols.py`
proves that literally: it builds a store and a host in a subprocess and asserts `memory_hook`
never reaches `sys.modules`.

The five:

* `MemoryStore`      - where notes live. Markdown on disk today; Postgres, S3 or a hosted API
  are all somebody else's business, and none of them should have to care how the default one
  writes frontmatter.
* `Retriever`        - how a query becomes ranked notes. The shipped fusion is one answer.
* `Extractor`        - how a transcript becomes lessons. The router, an LLM, or a human.
* `EpisodeSource`    - where sessions come from. `hosts.HostAdapter` is the shipped family;
  this is the shape it satisfies, so a new host is a class rather than an edit to three files.
* `InterventionSink` - where a fired guard goes. stdout today; a terminal UI, an editor
  extension or a webhook are the same shape.

**`runtime_checkable` is not enough, and that is why `conforms()` exists.** `isinstance()`
against a `Protocol` checks that the *names* are present and almost nothing else. Both of these
pass it, verified rather than assumed:

    class WrongSig:    def search(self): ...        # isinstance -> True. First call: TypeError.
    class NonCallable: search = "not a function"    # isinstance -> True. First call: TypeError.

`conforms()` checks that each member is callable and that its signature can actually accept the
arguments the caller will pass, and it returns the reasons rather than a bare False - because
"your class does not fit" is not an error message a person can act on.

    from nevertwice import protocols

    class PostgresStore:
        def write(self, note): ...
        def read(self, stem): ...
        def all(self, project=None): ...
        def delete(self, stem): ...

    problems = protocols.conforms(PostgresStore(), "MemoryStore")   # [] means it fits
    protocols.register("MemoryStore", "postgres", PostgresStore)

Standard library only.
"""
from __future__ import annotations

import inspect
from typing import Any, Iterable, Protocol, runtime_checkable

__all__ = ["MemoryStore", "Retriever", "Extractor", "EpisodeSource", "InterventionSink",
           "PROTOCOLS", "REQUIRED", "conforms", "register", "get", "providers", "unregister"]

SCHEMA_VERSION = 1


# ── the five seams ──────────────────────────────────────────────────────

@runtime_checkable
class MemoryStore(Protocol):
    """Where notes live.

    Deliberately CRUD and nothing else. Every temptation to add `search` here has to be
    refused: a store that also ranks is a store you cannot replace without reimplementing
    retrieval, which is exactly the coupling this file exists to break.
    """

    def write(self, note: dict) -> str:
        """Persist one note (a `schemas.NoteMeta`); return its stem."""

    def read(self, stem: str) -> dict | None:
        """One note by stem, or None."""

    def all(self, project: str | None = None) -> Iterable[dict]:
        """Every live note, optionally for one project."""

    def delete(self, stem: str) -> bool:
        """Remove one note. True when something was removed."""


@runtime_checkable
class Retriever(Protocol):
    """How a query becomes ranked notes.

    Returning `schemas.RetrievalHit` shapes rather than store rows is what lets a retriever be
    swapped without the caller learning a new dict.
    """

    def search(self, query: str, *, project: str | None = None, k: int = 5) -> list[dict]:
        """Ranked `RetrievalHit`s, best first. An empty list is a valid answer and means
        'nothing worth returning' - never a confident wrong one."""


@runtime_checkable
class Extractor(Protocol):
    """How a transcript becomes lessons."""

    def extract(self, transcript: str, *, project: str) -> list[dict]:
        """Zero or more lesson dicts (`type`, `title`, `description`, `prevention`).

        Zero is a real answer: most sessions teach nothing durable, and an extractor that
        always finds something is an extractor that manufactures noise.
        """


@runtime_checkable
class EpisodeSource(Protocol):
    """Where sessions come from. `hosts.HostAdapter` is the shipped family of these."""

    def discover(self) -> list:
        """Locations this source has on this machine. Empty is normal."""

    def read(self, cursor: dict | None = None) -> dict:
        """`{events, cursor, ...}` since `cursor`. The cursor is opaque to the caller."""

    def normalize(self, raw: str, *, source: Any = None) -> list[dict]:
        """The host's own shape turned into `schemas.EpisodeEvent`s."""


@runtime_checkable
class InterventionSink(Protocol):
    """Where a fired guard goes."""

    def emit(self, intervention: dict) -> None:
        """Deliver one `schemas.Intervention`. Must not raise: a sink that throws takes down
        the hot path it was supposed to report on."""


PROTOCOLS: dict = {"MemoryStore": MemoryStore, "Retriever": Retriever,
                   "Extractor": Extractor, "EpisodeSource": EpisodeSource,
                   "InterventionSink": InterventionSink}

#: The members an implementation must provide, and the arguments a caller will pass. The
#: second half is what `isinstance` cannot check: a method named right and shaped wrong fails
#: at the first call, in production, on someone else's machine.
REQUIRED: dict = {
    "MemoryStore": {"write": ("note",), "read": ("stem",), "all": (), "delete": ("stem",)},
    "Retriever": {"search": ("query",)},
    "Extractor": {"extract": ("transcript",)},
    "EpisodeSource": {"discover": (), "read": (), "normalize": ("raw",)},
    "InterventionSink": {"emit": ("intervention",)},
}


# ── conformance ─────────────────────────────────────────────────────────

def conforms(obj, protocol: str) -> list[str]:
    """Problems with `obj` against the named protocol. Empty means it fits.

    A list of reasons rather than a bool, for the same reason `schemas.conforms` returns one:
    the caller is usually a person who has just written a class and needs to know which part
    of it is wrong, not that some part is.
    """
    if protocol not in REQUIRED:
        return [f"unknown protocol {protocol!r}; expected one of {', '.join(sorted(REQUIRED))}"]
    problems = []
    for name, args in REQUIRED[protocol].items():
        member = getattr(obj, name, None)
        if member is None:
            problems.append(f"missing {name}()")
            continue
        if not callable(member):
            # One of the two gaps `isinstance` leaves open: a non-callable attribute of the
            # right name satisfies a runtime_checkable Protocol and fails at the first call.
            problems.append(f"{name} is not callable (it is {type(member).__name__})")
            continue
        problem = _signature_problem(member, name, args)
        if problem:
            problems.append(problem)
    return problems


def _signature_problem(member, name: str, args: tuple) -> str | None:
    """Can this member actually be called the way the engine will call it?"""
    try:
        signature = inspect.signature(member)
    except (TypeError, ValueError):          # a builtin or C callable - take it on trust
        return None
    try:
        signature.bind(*[None] * len(args))
    except TypeError as exc:
        expected = ", ".join(args) if args else "no positional arguments"
        return f"{name}({expected}) cannot be called: {exc}"
    return None


def implements(obj) -> list[str]:
    """Every protocol `obj` satisfies. Useful for a plugin that fills more than one seam."""
    return sorted(name for name in REQUIRED if not conforms(obj, name))


# ── the registry ────────────────────────────────────────────────────────

_REGISTRY: dict = {name: {} for name in PROTOCOLS}


def register(protocol: str, name: str, factory, *, replace: bool = False) -> None:
    """Register an implementation under a protocol.

    `replace=False` by default: silently shadowing an existing provider is how two plugins
    both think they are the store, and the loser finds out in production. Overriding is
    allowed, but it has to be said out loud.
    """
    if protocol not in _REGISTRY:
        raise ValueError(f"unknown protocol {protocol!r}; "
                         f"expected one of {', '.join(sorted(_REGISTRY))}")
    if not name or not str(name).strip():
        raise ValueError("a provider needs a name")
    if name in _REGISTRY[protocol] and not replace:
        raise ValueError(f"{protocol} provider {name!r} is already registered; "
                         f"pass replace=True to override it deliberately")
    _REGISTRY[protocol][name] = factory


def unregister(protocol: str, name: str) -> bool:
    return _REGISTRY.get(protocol, {}).pop(name, None) is not None


def get(protocol: str, name: str):
    return _REGISTRY.get(protocol, {}).get(name)


def providers(protocol: str | None = None) -> dict:
    """What is registered. `{protocol: [names]}`, or one protocol's names."""
    if protocol is not None:
        return {protocol: sorted(_REGISTRY.get(protocol, {}))}
    return {name: sorted(impls) for name, impls in _REGISTRY.items()}
