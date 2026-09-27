#!/usr/bin/env python3
"""PREREG-V3 TB4.11b (A6): the incident gate, the halts, the balance rule and the repair plan (rev1 §4.5, P2, §5.6
repair blocks; the auditor's Q24).

* an upstream failure (Q24): a cloud-endpoint call with status >= 500, a 429, an upstream error (no status, not
  refused), or an incomplete response the client did not abandon - on any port role;
* the gate: >= 5 upstream failures across >= 2 arms within 60 s open an incident (STATUS INCIDENT START); while it is
  open no new unit starts; a canary is due every 60 s, and two CONSECUTIVE successes close it (INCIDENT END);
* halts: 401, 402 and 403 from upstream halt the campaign; a 402 waits for the owner;
* the balance: a stand starts only with a balance >= 2x its projected cost;
* the repair plan - from counters and STATUS only, and refused once any scoring of the stand has happened:
  exogenous (an incident window's transport_lost or failed-outcome units, a halt's in-flight units, any embed
  transport failure) re-runs the unit for EVERY arm and bracket in fresh stores; endogenous (one arm's crash,
  ceiling, or first P0b/P0h/P0j) re-runs it for that arm only, in fresh stores and processes, same model identity;
  there is never a lone-arm whole-run re-run; exogenous re-runs above 10 % of a stand's units make the stand invalid.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

WINDOW_S = 60
MIN_FAILURES = 5
MIN_ARMS = 2
CANARY_EVERY_S = 60
CLOSE_AFTER = 2
HALT_STATUSES = {401: "401", 402: "402", 403: "403"}
EXO_SHARE_MAX = 0.10


class IncidentError(ValueError):
    """A repair or gate request that the preregistration does not allow."""


def is_upstream_failure(call: Mapping[str, Any]) -> bool:
    """Q24, on any port role."""
    if call.get("refused") or call.get("client_abandoned"):
        return False
    st = call.get("status")
    if st is None:
        return True                                   # an upstream error: no status came back
    if not isinstance(st, int):
        return True
    if st >= 500 or st == 429:
        return True
    return not call.get("complete", True)


def halt_kind(status: Any) -> str | None:
    return HALT_STATUSES.get(status) if isinstance(status, int) else None


def balance_ok(balance: float, projected_cost: float) -> bool:
    return balance >= 2 * projected_cost


@dataclass
class IncidentGate:
    """The scheduler's incident gate. Feed it call outcomes and canary results with their times; it answers whether a
    new unit may start, when a canary is due, and the STATUS events."""
    failures: deque = field(default_factory=deque)
    open_since: float | None = None
    last_canary: float | None = None
    successes: int = 0
    events: list = field(default_factory=list)

    def observe(self, arm: str, t: float, call: Mapping[str, Any]) -> None:
        if not is_upstream_failure(call):
            return
        self.failures.append((t, arm))
        while self.failures and self.failures[0][0] < t - WINDOW_S:
            self.failures.popleft()
        if (self.open_since is None and len(self.failures) >= MIN_FAILURES
                and len({a for _, a in self.failures}) >= MIN_ARMS):
            self.open_since, self.successes, self.last_canary = t, 0, None
            self.events.append(("INCIDENT START", t))

    @property
    def is_open(self) -> bool:
        return self.open_since is not None

    def admits_new_unit(self) -> bool:
        return not self.is_open

    def canary_due(self, now: float) -> bool:
        return self.is_open and (self.last_canary is None or now - self.last_canary >= CANARY_EVERY_S)

    def canary(self, now: float, ok: bool) -> None:
        if not self.is_open:
            raise IncidentError("a canary is sent only while an incident is open")
        self.last_canary = now
        self.successes = self.successes + 1 if ok else 0
        if self.successes >= CLOSE_AFTER:
            self.events.append(("INCIDENT END", now))
            self.open_since, self.successes = None, 0
            self.failures.clear()


@dataclass(frozen=True)
class Rerun:
    kind: str            # "exogenous" | "endogenous"
    unit: str
    arms: tuple          # every arm (and bracket) for exogenous, one arm for endogenous
    reason: str
    fresh_store: bool = True


def repair_plan(stand: str, *, units: Iterable[str], arms: Iterable[str], scored: bool,
                incident_units: Iterable[str] = (), halted_units: Iterable[str] = (),
                embed_failure_units: Iterable[str] = (),
                endogenous: Iterable[tuple[str, str, str]] = ()) -> dict:
    """The repair blocks of a stand, decided before any scoring of it (P2). ``endogenous`` holds (arm, unit, reason)."""
    if scored:
        raise IncidentError(f"{stand}: the repair plan is decided before any scoring of the stand, never after")
    units = list(dict.fromkeys(units))
    arms = tuple(dict.fromkeys(arms))
    known = set(units)
    exo: dict[str, str] = {}
    for label, us in (("incident", incident_units), ("halt", halted_units), ("embed-transport", embed_failure_units)):
        for u in us:
            if u not in known:
                raise IncidentError(f"{stand}: an {label} unit {u!r} is not a unit of the stand")
            exo.setdefault(u, label)
    reruns = [Rerun("exogenous", u, arms, why) for u, why in sorted(exo.items())]
    for arm, unit, why in endogenous:
        if unit not in known:
            raise IncidentError(f"{stand}: an endogenous unit {unit!r} is not a unit of the stand")
        if arm not in arms:
            raise IncidentError(f"{stand}: an endogenous re-run names the unknown arm {arm!r}")
        if unit == "*":
            raise IncidentError("there are no lone-arm whole-run re-runs")
        if unit in exo:
            continue                                   # the exogenous re-run already covers every arm
        reruns.append(Rerun("endogenous", unit, (arm,), why))
    exo_share = len(exo) / len(units) if units else 0.0
    return {"stand": stand, "reruns": reruns, "exogenous_share": exo_share,
            "stand_invalid": exo_share > EXO_SHARE_MAX,
            "blocks": {"exogenous": sorted(exo), "endogenous": sorted({(r.arms[0], r.unit) for r in reruns
                                                                       if r.kind == "endogenous"})}}


def dropped_after_repair(first: Iterable[str], second: Iterable[str], *, kind: str) -> set:
    """A unit that fails again after its exogenous re-run, or twice after an endogenous one, is dropped for every
    arm."""
    if kind not in ("exogenous", "endogenous"):
        raise IncidentError(f"unknown repair kind {kind!r}")
    return set(first) & set(second)
