#!/usr/bin/env python3
"""What actually happened after a guard fired - the only thing that can falsify it.

A memory that warns you is easy to build and impossible to trust. The hard part is the loop
that decides which warnings deserve to keep interrupting you, and the only honest input to
that decision is what happened *after* each one. This module owns that vocabulary, its
arithmetic, and the one rule that makes the whole thing non-circular:

    **Firing is not evidence of success.** A guard that fires a thousand times has proved
    nothing except that its regex matches. `fired` is telemetry; it never touches the
    lifecycle, and `support()` cannot see it.

Five outcomes, because three were hiding a distinction that matters:

* `prevented_failure` - the warning fired and a real repeat was demonstrably avoided. The
  strongest evidence there is, and the only one that means the guard did its job.
* `accepted`          - the agent heeded it and changed course. Evidence for.
* `overridden`        - the agent proceeded anyway. The guard did not earn compliance. This
  is a statement about **burden**, not about correctness.
* `false_positive`    - the guard was wrong: it fired on a case that was fine. This is a
  statement about **correctness**.
* `unknown`           - recorded and counted for nothing. Naming it is the point: an
  unresolved outcome must be visible as unresolved rather than silently
  imputed to whichever side flatters the guard.

The old vocabulary collapsed `overridden` into `false_positive` - the docstring said so
outright: "'false_positive' - overridden, or fired on a case that was actually fine". Under
that reading a guard that is *right* but annoying retires for being right, and precision and
override burden can never be told apart. Both still demote, because a warning nobody complies
with is not earning its interruption, but they are counted separately so the two questions
have two answers.

**Both directions are calibrated on distinct sessions.** Before this module only promotion
was: `corroborations` deduped by session id while `false_positives` counted every call. One
frustrated session could therefore retire a guard it could not have promoted, and a caller
that passed no session id could promote a guard by calling K times in a row. Falsification
should be at least as hard to fake as confirmation.

Standard library only. No behaviour is imposed on the hot path: nothing here is imported by
`guards.check()`.
"""
from __future__ import annotations

import math

SCHEMA_VERSION = 1

#: Evidence for the guard.
SUPPORT = ("prevented_failure", "accepted")
#: Evidence against it - separately, because they answer different questions.
AGAINST = ("false_positive", "overridden")
#: Recorded, counts for nothing, and says so.
NEUTRAL = ("unknown",)

OUTCOMES = SUPPORT + AGAINST + NEUTRAL

# The vocabulary the ledger used before D4. Kept working, and mapped rather than reinterpreted:
# `helped` was the heed signal, `corroborated` the weaker "relevant, no decision taken".
LEGACY = {
    "helped": "accepted",
    "corroborated": "accepted",
    "false_positive": "false_positive",
}


def normalise(outcome: str) -> str | None:
    """The canonical outcome name, or None if it is not one we accept.

    Returning None rather than defaulting is deliberate: a typo'd outcome that silently
    became `unknown` would be indistinguishable from a genuinely unresolved one, and the
    caller would never learn that its feedback went nowhere.
    """
    outcome = (outcome or "").strip().lower()
    if outcome in OUTCOMES:
        return outcome
    return LEGACY.get(outcome)


def blank() -> dict:
    """The accounting block a guard carries. Every outcome is present at zero, so a reader
    never has to distinguish "never happened" from "this ledger predates the field"."""
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": {name: 0 for name in OUTCOMES},
        # Distinct sessions per direction. Lists, not sets, because this round-trips JSON.
        "sessions": {"support": [], "against": []},
    }


def block(guard: dict) -> dict:
    """The guard's outcome block, created on first use. Mutates `guard` in place."""
    existing = guard.get("outcomes")
    if not isinstance(existing, dict) or "counts" not in existing:
        existing = blank()
        # Carry the pre-D4 counters forward so a guard that already earned its status does not
        # silently reset to zero evidence on upgrade.
        existing["counts"]["accepted"] = int(guard.get("helped") or 0)
        existing["counts"]["false_positive"] = int(guard.get("false_positives") or 0)
        existing["sessions"]["support"] = list(guard.get("seen_sessions") or [])
        guard["outcomes"] = existing
    for name in OUTCOMES:
        existing["counts"].setdefault(name, 0)
    existing.setdefault("sessions", {"support": [], "against": []})
    existing["sessions"].setdefault("support", [])
    existing["sessions"].setdefault("against", [])
    return existing


def record(guard: dict, outcome: str, *, session_id: str | None = None) -> str | None:
    """Record one outcome. Returns the canonical name, or None if it was not recognised.

    An outcome with no session id counts toward the totals but toward **neither** distinct-
    session tally, so it can never move the lifecycle. Anonymous feedback is still worth
    having - it shows up in the override rate - but a mechanism that let an unattributed
    caller promote or retire a guard by repeating itself would not be a feedback loop, it
    would be a volume knob.
    """
    name = normalise(outcome)
    if name is None:
        return None
    acc = block(guard)
    acc["counts"][name] = int(acc["counts"].get(name, 0)) + 1

    sid = (session_id or "").strip()
    if sid:
        if name in SUPPORT and sid not in acc["sessions"]["support"]:
            acc["sessions"]["support"].append(sid)
        elif name in AGAINST and sid not in acc["sessions"]["against"]:
            acc["sessions"]["against"].append(sid)
    return name


def support_sessions(guard: dict) -> int:
    return len(block(guard)["sessions"]["support"])


def against_sessions(guard: dict) -> int:
    return len(block(guard)["sessions"]["against"])


def wilson(successes: int, trials: int, z: float = 1.96) -> dict:
    """A Wilson score interval for a proportion.

    Wilson rather than the normal approximation because these samples are tiny and often at
    the boundary: a guard with 3 successes and 0 failures has a normal interval of exactly
    [1.0, 1.0], which reads as certainty and is nonsense. Wilson stays inside (0, 1) and
    widens honestly when n is small, which is the whole reason to publish an interval rather
    than a point.
    """
    if trials <= 0:
        return {"point": None, "low": None, "high": None, "n": 0,
                "note": "no resolved outcome yet - precision is undefined, not zero"}
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    margin = (z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))) / denom
    return {"point": round(p, 3), "low": round(max(0.0, centre - margin), 3),
            "high": round(min(1.0, centre + margin), 3), "n": trials, "z": z}


def summary(guard: dict) -> dict:
    """Precision, override rate and their intervals - the numbers a lifecycle decision rests on.

    Precision answers "when it fires, is it right?" and is computed over *correctness*
    outcomes only: an override is not a wrong guard, it is an unheeded one. Override rate
    answers "is it worth the interruption?" and is computed over every resolved outcome.
    Keeping them apart is the point of the five-outcome vocabulary.
    """
    acc = block(guard)
    counts = acc["counts"]
    supported = sum(counts[name] for name in SUPPORT)
    wrong = counts["false_positive"]
    overridden = counts["overridden"]
    resolved = supported + wrong + overridden

    return {
        "schema_version": SCHEMA_VERSION,
        "counts": dict(counts),
        "resolved": resolved,
        "unknown": counts["unknown"],
        "precision": wilson(supported, supported + wrong),
        "precision_basis": ("share of correctness-resolved fires that were accepted or "
                            "prevented a failure; an override is excluded because it says the "
                            "guard went unheeded, not that it was wrong"),
        "override_rate": wilson(overridden, resolved),
        "override_rate_basis": ("share of all resolved fires the agent proceeded through; the "
                                "burden the guard imposes, which is a different question from "
                                "whether it is correct"),
        "distinct_sessions": {"support": len(acc["sessions"]["support"]),
                              "against": len(acc["sessions"]["against"])},
        "fired_is_not_evidence": ("`fired` counts how often the pattern matched and is not an "
                                  "input here; displaying a warning is not evidence that it "
                                  "helped"),
    }


def verdict(guard: dict, *, promote_at: int, retire_at: int) -> dict:
    """What the recorded outcomes say the guard's status should be, and why.

    Returns `{"action": "promote"|"demote"|"hold", "to": status|None, "because": str}`. It
    decides nothing on its own - `guards.feedback` applies it - so the rule can be read, and
    tested, without a ledger write.
    """
    status = guard.get("status", "advisory")
    supporting = support_sessions(guard)
    opposing = against_sessions(guard)

    if opposing >= retire_at:
        nxt = {"blocking": "advisory", "advisory": "retired"}.get(status, "retired")
        return {"action": "demote", "to": nxt,
                "because": (f"{opposing} distinct session(s) overrode it or called it a false "
                            f"positive, at or past the retire threshold of {retire_at}")}
    if guard.get("pack"):
        return {"action": "hold", "to": None,
                "because": ("a cold-start pack guard is pinned advisory by design - a shipped "
                            "heuristic never earns the right to block")}
    if status == "advisory" and supporting >= promote_at:
        return {"action": "promote", "to": "blocking",
                "because": (f"{supporting} distinct session(s) accepted it or recorded a "
                            f"prevented failure, at or past the promote threshold of "
                            f"{promote_at}")}
    return {"action": "hold", "to": None,
            "because": (f"{supporting}/{promote_at} distinct supporting session(s), "
                        f"{opposing}/{retire_at} opposing")}
