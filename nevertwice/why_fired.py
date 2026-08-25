#!/usr/bin/env python3
"""Why this intervention fired - one object, computed once, rendered by four surfaces.

A guard that fires interrupts the agent. The only thing that makes that defensible is being
able to answer, immediately and in the same breath, *why*: what text matched, which recorded
failure it came from, how often that failure has recurred, how confident the guard is, how old
it is, what policy turned it into a warning rather than a block, and what it cost to say.

Before this module each surface answered a different subset of that, in a different shape. The
CLI printed `[warn] (g-1234) message`. MCP printed a slightly different line. The Python API
returned `{id, status, message, scope}`. The dashboard did not mention guards at all. Anyone
comparing them would have concluded the four disagreed - and would have been right, because
nothing made them agree.

So the explanation is built **once**, here, and every surface renders this object. Adding a
field means adding it in one place; a surface that forgets to show it is a rendering gap, not
a different answer.

**It is off the hot path by construction.** `guards.check()` stays a regex-and-scope match that
costs nothing until it matches, and this module is only ever called *after* something has
already fired, when the agent is going to be interrupted anyway. `explain()` re-runs one
pattern to recover the span, reads at most a handful of notes, and only touches the causal
graph when `deep=True` asks it to.

**Where a signal does not apply, it says so.** A guard fires on a regex, not on retrieval, so
it has no lexical or semantic contribution to report. Printing `0.0` there would be a lie
dressed as a measurement; the field carries `None` and a reason instead.

    from nevertwice import why_fired
    hits = api.guards_check(text, project="acme")
    why  = why_fired.explain(hits[0]["id"], text, project="acme")

Standard library only.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guards as _guards            # noqa: E402 - the ledger and the policy constants

# The shape this module returns is declared in `schemas.WhyFired`, not imported here: the
# declaration is a contract for readers and tests, and making the hot-adjacent path import it
# at runtime would buy nothing. `tests/_test_why_fired.py` holds the two together.

SCHEMA_VERSION = 1

# The intervention kinds this object can describe. `guard` is the only one wired today; the
# others are named so that a later surface adds a branch rather than a second object.
KINDS = ("guard", "anticipation", "recall")

_NOT_RETRIEVAL = ("a guard fires on a regex match against the proposed action, not on a "
                  "retrieval - there is no ranked candidate set, so there is no contribution "
                  "to attribute")


def _sibling(name: str):
    """Import an optional sibling, or None. The live deployment is a flat selective copy of
    scripts, and an explanation must degrade rather than crash when a module it would have
    enriched is simply not there."""
    try:
        return __import__(name)
    except Exception:               # noqa: BLE001 - any import failure is the same answer here
        return None


def _age_days(born: str) -> int | None:
    try:
        return max(0, (datetime.now() - datetime.strptime(born[:10], "%Y-%m-%d")).days)
    except (ValueError, TypeError):
        return None


def _match(pattern: str, text: str) -> dict:
    """The span that fired, with a little context either side.

    Re-running the search is deliberate: `check()` throws the match object away because
    keeping it would put an allocation on a path that runs before every tool call. Paying for
    it here, once, after something has already fired, is the right side of that trade.
    """
    capped = (text or "")[:_guards.MAX_CHECK_CHARS]
    try:
        found = re.search(pattern, capped)
    except re.error as exc:
        return {"span": None, "text": None, "pattern": pattern,
                "note": f"the guard's pattern no longer compiles: {exc}"}
    if not found:
        return {"span": None, "text": None, "pattern": pattern,
                "note": "the guard is in the ledger but does not match this action text"}
    start, end = found.span()
    lo, hi = max(0, start - 40), min(len(capped), end + 40)
    return {"span": [start, end], "text": capped[start:end], "pattern": pattern,
            "context": capped[lo:hi]}


def _sources(guard: dict) -> dict:
    """The recorded failures this guard was distilled from.

    `born_from` holds note stems. Resolving them is what turns "some pattern says no" into
    "you did this on 2026-06-14 and it cost you an afternoon", which is the entire argument
    for a memory that acts rather than one that recalls.
    """
    stems = list(guard.get("born_from") or [])
    hook = _sibling("memory_hook")
    episodes, recurrence, unresolved = [], 0, []
    for stem in stems:
        meta = None
        if hook is not None and hasattr(hook, "_note_meta_for_stem"):
            try:
                meta = hook._note_meta_for_stem(stem)
            except Exception:       # noqa: BLE001 - a missing note must not break the answer
                meta = None
        if not meta:
            unresolved.append(stem)
            episodes.append({"stem": stem, "resolved": False})
            continue
        rec = int(meta.get("recurrence") or 1)
        recurrence = max(recurrence, rec)
        episodes.append({"stem": stem, "resolved": True,
                         "title": meta.get("title"), "ntype": meta.get("ntype"),
                         "project": meta.get("project"), "date": meta.get("date"),
                         "recurrence": rec,
                         "description": meta.get("desc") or meta.get("description")})
    out = {"episodes": episodes, "recurrence": recurrence or None,
           "born_date": guard.get("born_date"),
           "age_days": _age_days(guard.get("born_date") or "")}
    if unresolved:
        # A guard outliving its source note is normal - notes are superseded and archived -
        # but it is also exactly how a guard becomes unfalsifiable, so it is stated.
        out["unresolved_sources"] = unresolved
        out["note"] = ("the note this guard was born from is no longer in the live store "
                       "(superseded, archived or deleted); the guard still fires but its "
                       "evidence can no longer be read")
    if not stems:
        out["note"] = "this guard records no source note, so its evidence cannot be traced"
    return out


def _outcomes_block(guard: dict) -> dict:
    """Precision, override rate and their Wilson intervals - or a stated absence.

    Carried here so all four surfaces get the same numbers from the same place. What the
    guard has *earned* is the only defensible input to whether it may keep interrupting you,
    and `fired` is deliberately not part of it.
    """
    module = _sibling("outcomes")
    if module is None:
        return {"available": False,
                "note": "outcomes.py is not present in this install, so no outcome has been "
                        "recorded and precision cannot be reported"}
    summary = module.summary(guard)
    summary["available"] = True
    summary["verdict"] = module.verdict(guard, promote_at=_guards.K_PROMOTE,
                                        retire_at=_guards.M_RETIRE)
    return summary


def _policy(guard: dict) -> dict:
    """What turned this into a warning rather than a block, and what would change it."""
    status = guard.get("status", "advisory")
    corroborations = int(guard.get("corroborations") or 0)
    false_positives = int(guard.get("false_positives") or 0)
    pack = bool(guard.get("pack"))
    if pack:
        promotion = ("never - pack guards are cold-start heuristics and are pinned advisory "
                     "by design, so a shipped guess can never block your work")
    elif status == "blocking":
        promotion = "already blocking"
    else:
        need = max(0, _guards.K_PROMOTE - corroborations)
        promotion = (f"{need} more distinct-session corroboration(s) promote this to blocking"
                     if need else "the next corroboration promotes this to blocking")
    return {
        "decision": "block" if status == "blocking" else "warn",
        "status": status,
        "advisory_only": pack,
        "corroborations": corroborations,
        "promote_at": _guards.K_PROMOTE,
        "promotion": promotion,
        "false_positives": false_positives,
        "retire_at": _guards.M_RETIRE,
        "demotion": (f"{max(0, _guards.M_RETIRE - false_positives)} more false positive(s) "
                     f"demote this a rung ("
                     f"{'blocking -> advisory' if status == 'blocking' else 'advisory -> retired'})"),
        "overrides": list(guard.get("overrides") or []),
        "rule": ("K distinct-session corroborations promote, M false positives demote one rung; "
                 "the counts decide, never the confidence estimate"),
    }


def _signals(guard: dict, deep: bool) -> dict:
    """What ranked this into view. For a guard: nothing did, and that is the honest answer."""
    out = {
        "lexical": None,
        "semantic": None,
        "lexical_note": _NOT_RETRIEVAL,
        "semantic_note": _NOT_RETRIEVAL,
        "graph_path": None,
    }
    if not deep:
        out["graph_path_note"] = ("not computed - pass deep=True to walk the causal graph, "
                                  "which reads the whole store")
        return out
    causal = _sibling("causal")
    entity = (guard.get("scope") or {}).get("project")
    if causal is None or not entity:
        out["graph_path_note"] = ("no causal path: this guard is not scoped to a project, so "
                                  "there is no entity to walk from" if causal is not None
                                  else "the causal module is not present in this install")
        return out
    try:
        out["graph_path"] = causal.why(entity, entity)
    except Exception as exc:        # noqa: BLE001 - an explanation must not raise
        out["graph_path_note"] = f"the causal walk failed: {exc}"
    return out


def _cost(guard: dict, sources: dict) -> dict:
    """What this interruption cost, against what reading the evidence would have cost.

    The zero-token claim is the product's central argument, so it is stated as an arithmetic
    the reader can check rather than a slogan: the guard sat in a JSON ledger costing nothing
    until it matched, and what it then spent is one message against the notes it stands in for.
    """
    receipt = _sibling("receipt")
    est = getattr(receipt, "est_tokens", None) if receipt else None
    if est is None:                 # a flat install without receipt.py still gets an estimate
        def est(s: str) -> int:
            return max(1, len(s or "") // 4)
    shown = est(guard.get("message") or "")
    counterfactual = 0
    for episode in sources.get("episodes") or []:
        if episode.get("resolved"):
            counterfactual += est(" ".join(str(episode.get(k) or "")
                                           for k in ("title", "description")))
    return {
        "tokens_until_it_fired": 0,
        "tokens_spent_now": shown,
        "tokens_to_read_the_sources": counterfactual or None,
        "net_vs_reading_the_sources": (counterfactual - shown) if counterfactual else None,
        "basis": ("a ~4-characters-per-token estimate, not a tokenizer count; it is an order "
                  "of magnitude, and the claim it supports is an order-of-magnitude claim"),
    }


def explain(guard_id: str, action_text: str = "", *, project: str | None = None,
            path: str | None = None, tool: str | None = None,
            guards: list | None = None, deep: bool = False) -> dict | None:
    """The whole answer for one fired guard, or None if the id is not in the ledger.

    `action_text` is the text that was checked; without it the matched span cannot be
    recovered and the object says so rather than inventing one.
    """
    ledger = _guards.load_guards() if guards is None else guards
    guard = next((g for g in ledger if g.get("id") == guard_id), None)
    if guard is None:
        return None
    sources = _sources(guard)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "guard",
        "id": guard["id"],
        "status": guard.get("status", "advisory"),
        "message": guard.get("message", ""),
        "scope": dict(guard.get("scope") or {}),
        "checked": {"project": project, "path": path, "tool": tool},
        "match": _match(guard.get("pattern", ""), action_text),
        "source": sources,
        # Absent, not None, when no source note resolves - same reason as `last_fired` below:
        # an omitted key means "no such thing", a None-valued one means "an int that is null".
        **({"recurrence": sources["recurrence"]} if sources.get("recurrence") else {}),
        "confidence": guard.get("confidence", _guards._confidence(guard)),
        "confidence_basis": ("Laplace-smoothed helped/(helped+false positives); a display and "
                             "ranking estimate that never gates the lifecycle"),
        "age_days": sources.get("age_days"),
        "fired": int(guard.get("fired") or 0),
        # Absent, not None, when it has never fired: `WhyFired` is total=False, so an omitted
        # key means "no such thing" while a None-valued one means "a string that is null" -
        # and the second is the kind of half-truth the schema file exists to stop.
        **({"last_fired": guard["last_fired"]} if guard.get("last_fired") else {}),
        "policy": _policy(guard),
        "outcomes": _outcomes_block(guard),
        "signals": _signals(guard, deep),
        "cost": _cost(guard, sources),
        "feedback": ("call guard_feedback(id, 'helped'|'false_positive'|'corroborated'); an "
                     "override with a reason is stored as a learned exception"),
    }


def explain_hits(hits: list, action_text: str = "", *, project: str | None = None,
                 path: str | None = None, tool: str | None = None,
                 guards: list | None = None, deep: bool = False) -> list:
    """`explain` for a whole `guards.check()` result, sharing one ledger read."""
    ledger = _guards.load_guards() if guards is None else guards
    out = []
    for hit in hits or []:
        why = explain(hit.get("id", ""), action_text, project=project, path=path, tool=tool,
                      guards=ledger, deep=deep)
        if why is not None:
            out.append(why)
    return out


# ── rendering: one text form, so the CLI and MCP cannot drift apart ──

def render(why: dict, *, verbose: bool = True) -> str:
    """The human-readable form. The CLI and the MCP server both call this rather than each
    formatting the object, because two formatters is how two surfaces start disagreeing."""
    tag = "BLOCK" if why["status"] == "blocking" else "warn "
    lines = [f"  [{tag}] ({why['id']}) {why['message']}"]
    if not verbose:
        return "\n".join(lines)

    match = why.get("match") or {}
    if match.get("text"):
        lines.append(f"      matched: {match['text']!r} at {match['span']}")
    elif match.get("note"):
        lines.append(f"      matched: {match['note']}")

    source = why.get("source") or {}
    for episode in (source.get("episodes") or [])[:3]:
        if episode.get("resolved"):
            lines.append(f"      from:    {episode['stem']}"
                         + (f"  (recurrence {episode['recurrence']}x)"
                            if episode.get("recurrence", 0) > 1 else ""))
        else:
            lines.append(f"      from:    {episode['stem']}  (note no longer in the store)")
    if source.get("note"):
        lines.append(f"      source:  {source['note']}")

    age = why.get("age_days")
    lines.append(f"      trust:   confidence {why.get('confidence')}"
                 + (f" · {age}d old" if age is not None else "")
                 + f" · fired {why.get('fired')}x")

    outcomes = why.get("outcomes") or {}
    if outcomes.get("available") and outcomes.get("resolved"):
        precision = outcomes["precision"]
        override = outcomes["override_rate"]
        line = "      earned:  "
        if precision.get("point") is not None:
            line += (f"precision {precision['point']} "
                     f"[{precision['low']}-{precision['high']}, n={precision['n']}]")
        else:
            line += "precision undefined (no correctness outcome yet)"
        if override.get("point") is not None:
            line += f" · overridden {override['point']} of {override['n']}"
        unresolved = outcomes.get("unknown") or 0
        if unresolved:
            line += f" · {unresolved} unresolved"
        lines.append(line)

    policy = why.get("policy") or {}
    lines.append(f"      policy:  {policy.get('decision')} - {policy.get('promotion')}")

    cost = why.get("cost") or {}
    net = cost.get("net_vs_reading_the_sources")
    lines.append(f"      cost:    0 tokens until it fired, {cost.get('tokens_spent_now')} now"
                 + (f" (vs ~{cost.get('tokens_to_read_the_sources')} to read the sources, "
                    f"net {net:+d})" if net is not None else ""))
    return "\n".join(lines)


def main() -> None:
    """`python -m nevertwice.why_fired <guard-id> [--text ...] [--json] [--deep]`."""
    import json
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("--"):
        print("usage: why_fired <guard-id> [--text \"...\"] [--project P] [--json] [--deep]")
        return
    hook = _sibling("memory_hook")
    argval = getattr(hook, "argval", lambda a, n: None) if hook else (lambda a, n: None)
    why = explain(argv[0], argval(argv, "text") or "", project=argval(argv, "project"),
                  deep="--deep" in argv)
    if why is None:
        print(f"no such guard: {argv[0]}")
        raise SystemExit(1)
    print(json.dumps(why, indent=2, ensure_ascii=False) if "--json" in argv else render(why))


if __name__ == "__main__":
    main()
