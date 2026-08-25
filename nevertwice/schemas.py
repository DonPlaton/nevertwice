#!/usr/bin/env python3
"""The eight boundaries, written down - and checked against what the code actually passes.

Every value that crosses a seam in this system is a plain `dict`, and every reader of one is
defensive about it: `n.get("desc", "")`, `(params or {}).get(...)`, `isinstance(x, dict)`.
That defensiveness is not paranoia, it is the absence of a contract. Nobody can say which keys
a note meta has without reading the writer, so every new reader re-derives the shape, guesses
one key wrong, and adds another `.get` with a default that silently hides the mistake.

This module states the eight shapes once. It is deliberately **not** a validation framework
and adds no dependency: `TypedDict` for the shape, a small structural `conforms()` for the
check, and `characterize()` to learn a shape from real values so a test can prove the
declaration matches production rather than the author's memory of it.

Nothing here changes behaviour. It is imported by tests today and by readers as they are
converted, one module at a time (GOAL D2). The point of landing it first is that the next
person to write a reader can look up the answer instead of inferring it.

The eight:

* `EpisodeEvent`   - what a host hands the hook: Claude Code, an MCP call, ingest, the daemon.
* `Frontmatter`    - the YAML block as written to a note file.
* `NoteMeta`       - a parsed note, the internal currency of retrieval.
* `RetrievalHit`   - what `api.recall` returns to a caller. The public shape.
* `Intervention`   - what a guard returns when it fires. The 0-token hot path.
* `WhyFired`       - the whole reason it fired, rendered by all four surfaces (GOAL D3).
* `JsonState`      - the two-generation JSON files the engine keeps beside the notes.
* `McpRequest`     - one JSON-RPC 2.0 message.
"""
from __future__ import annotations

from typing import Any, TypedDict, get_type_hints

__all__ = ["EpisodeEvent", "Frontmatter", "NoteMeta", "RetrievalHit", "Intervention",
           "WhyFired", "JsonState", "McpRequest", "REQUIRED", "conforms", "characterize"]


class EpisodeEvent(TypedDict, total=False):
    """A host handing over one moment of a session. Only `hook_event_name` always exists;
    which of the rest are present depends on the event, which is why this is `total=False`
    and why `REQUIRED` below carries the real obligation."""
    hook_event_name: str
    session_id: str
    cwd: str
    source: str
    prompt: str
    tool_name: str
    tool_input: dict


class Frontmatter(TypedDict, total=False):
    """The YAML block at the top of a note, as written. `type` here is `ntype` once parsed -
    the rename happens at the boundary, and forgetting that is a recurring bug."""
    type: str
    project: str
    title: str
    created: str
    tags: list
    entities: list
    recurrence: int
    resolved: bool


class NoteMeta(TypedDict, total=False):
    """A parsed note. The one shape retrieval, ranking and the graph all read."""
    ntype: str
    project: str
    title: str
    stem: str
    desc: str
    prevention: str
    tags: list
    entities: list
    recurrence: int
    date: str
    resolved: bool


class RetrievalHit(TypedDict, total=False):
    """What a caller of `api.recall` gets. Public: changing a key here is a breaking change.

    Note the rename from `NoteMeta`: `desc` becomes `description`, `prevention` stays. Two
    names for one field across one boundary is exactly the kind of thing this file exists to
    make visible rather than discoverable by bug report.
    """
    ntype: str
    project: str
    title: str
    stem: str
    description: str
    prevention: str
    entities: list
    recurrence: int
    date: str
    score: float


class Intervention(TypedDict, total=False):
    """A guard that fired. The whole 0-token argument rests on this being small and rare."""
    id: str
    status: str
    message: str
    scope: dict


class WhyFired(TypedDict, total=False):
    """Why an intervention fired - the object every surface renders (GOAL D3).

    `Intervention` is what crosses the hot path: small, and rare enough that the zero-token
    argument survives. This is the expensive half, built only after something has already
    fired. Keeping them as two shapes is the point - the cheap one cannot grow a field that
    would cost something to compute on every tool call.

    `signals` may carry `None` with a stated reason: a guard fires on a regex, so it has no
    lexical or semantic contribution, and reporting `0.0` there would be a lie shaped like a
    measurement.
    """
    schema_version: int
    kind: str
    id: str
    status: str
    message: str
    scope: dict
    checked: dict
    match: dict
    source: dict
    recurrence: int
    confidence: float
    confidence_basis: str
    age_days: int
    fired: int
    last_fired: str
    policy: dict
    signals: dict
    cost: dict
    feedback: str


class JsonState(TypedDict, total=False):
    """A state file kept beside the notes, written through the two-generation path."""
    version: int
    updated: str


class McpRequest(TypedDict, total=False):
    """One JSON-RPC 2.0 message. `id` is absent on a notification, and that absence is
    load-bearing: a notification must not be answered."""
    jsonrpc: str
    id: Any
    method: str
    params: dict


# Keys that must be present, per shape. `TypedDict(total=False)` describes what may appear;
# this describes what a reader is entitled to assume. Keeping them apart is deliberate - the
# engine tolerates a partial dict in many places, and pretending otherwise would force
# defensive code back in at a different layer.
REQUIRED: dict = {
    "EpisodeEvent": ("hook_event_name",),
    "Frontmatter": ("type", "title"),
    "NoteMeta": ("ntype", "title", "stem"),
    "RetrievalHit": ("ntype", "title", "stem"),
    "Intervention": ("id", "message"),
    "WhyFired": ("schema_version", "kind", "id", "status", "message",
                 "match", "source", "policy", "signals", "cost"),
    "JsonState": (),
    "McpRequest": ("jsonrpc", "method"),
}

_SHAPES = {"EpisodeEvent": EpisodeEvent, "Frontmatter": Frontmatter, "NoteMeta": NoteMeta,
           "RetrievalHit": RetrievalHit, "Intervention": Intervention, "WhyFired": WhyFired,
           "JsonState": JsonState, "McpRequest": McpRequest}

# `from __future__ import annotations` makes every annotation a *string*, so reading
# `__annotations__` directly hands `isinstance()` the word "dict" and every type check
# silently passes. Resolve once, here, where it is visible.
_HINTS = {name: get_type_hints(shape) for name, shape in _SHAPES.items()}


def _type_ok(value, annotation) -> bool:
    """Structural, not exhaustive: `Any` accepts anything, containers are checked by their
    outer type only. A deeper check would need a dependency, and the failures this catches -
    a list where a string was expected, an int where a dict was - are the ones that happen."""
    if annotation is Any:
        return True
    origin = getattr(annotation, "__origin__", annotation)
    if origin is dict:
        return isinstance(value, dict)
    if origin is list:
        return isinstance(value, list)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    try:
        return isinstance(value, annotation)
    except TypeError:                                  # pragma: no cover - exotic annotation
        return True


def conforms(value, shape: str) -> list:
    """Problems with `value` against the named shape. Empty means it conforms.

    Returns a list rather than raising: the caller decides whether a mismatch is a failed
    test, a logged warning, or - at a host boundary, where the other side is not ours to
    fix - a value to repair and carry on with.
    """
    if shape not in _SHAPES:
        return [f"unknown shape {shape!r}"]
    if not isinstance(value, dict):
        return [f"expected a dict, got {type(value).__name__}"]

    annotations = _HINTS[shape]
    problems = [f"missing required key {key!r}" for key in REQUIRED[shape]
                if key not in value]
    problems += [f"unknown key {key!r}" for key in value if key not in annotations]
    problems += [f"{key!r} is {type(value[key]).__name__}, expected "
                 f"{getattr(annotations[key], '__name__', annotations[key])}"
                 for key in value
                 if key in annotations and not _type_ok(value[key], annotations[key])]
    return problems


def characterize(values) -> dict:
    """What a real stream of values actually looks like: which keys always appear, which
    sometimes, and the types seen for each.

    This is how the declarations above stay honest. A test drives the real code, characterizes
    what came out, and compares - so the file describes production rather than intent, and a
    drift shows up as a failing test instead of a stale docstring.
    """
    values = [v for v in values if isinstance(v, dict)]
    if not values:
        return {"n": 0, "always": [], "sometimes": [], "types": {}}
    keys = [set(v) for v in values]
    always = sorted(set.intersection(*keys))
    seen = sorted(set.union(*keys))
    types: dict = {}
    for key in seen:
        types[key] = sorted({type(v[key]).__name__ for v in values if key in v})
    return {"n": len(values), "always": always,
            "sometimes": [k for k in seen if k not in always], "types": types}
