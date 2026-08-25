#!/usr/bin/env python3
"""Token and latency budgets, and abstention as a decision rather than a side effect.

The payload has always had a cap: sections are added by priority until `INJECT_BUDGET_CHARS`
runs out, and the rest is dropped. That is **truncation**, and it has two properties worth
naming. It cannot refuse something that fits - a worthless lesson gets injected whenever
there happens to be room. And it cannot say why anything was dropped, because nothing decided
to drop it; the string simply ended.

This module replaces "does it fit?" with "is it worth it?".

    policy = Policy(per_turn_tokens=400, min_value=0.3)
    ledger = Ledger()
    decision = policy.decide(ledger, item="mistake-sql-fstring", tokens=90, value=0.8)
    if decision.spend:
        ...inject it...

Every call returns a `Decision` carrying `reason` - one of `REASONS`, always populated, for
spends as well as abstentions. That is the exit criterion: an abstention is a policy decision
with a logged reason. Two consequences follow, and both are testable:

* an item can be **refused while there is plenty of room**, because its expected value is
  below the threshold. Truncation can never do that;
* two items of **identical size** get different decisions when their values differ. Truncation
  can never do that either.

**`avoided` is never invented.** The ledger records what memory cost and, separately, what the
caller says it saved - and only when the caller supplies an honest figure. `receipt.py` already
learned this the hard way: the only number available there was an upper bound against a
full-store re-paste, and quoting a counterfactual bound as a per-session fact overstated it. So
`avoided` defaults to zero, `net` is stated as an estimate, and a ledger with no supplied
savings reports `avoided: 0` rather than a flattering guess.

Standard library only, and no imports from the engine, so a budget can be reasoned about - and
tested - without a vault.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1

#: Every reason a decision can carry. Enumerated rather than free text: a reason you cannot
#: count is a reason nobody will ever aggregate, and "why did memory go quiet in this session"
#: is exactly the question an operator asks.
REASONS = (
    "spent",                        # within budget and worth it
    "below_value_threshold",        # affordable, and refused anyway - the point of this module
    "turn_tokens_exhausted",
    "session_tokens_exhausted",
    "turn_latency_exhausted",
    "session_latency_exhausted",
    "nothing_to_spend_on",          # a zero-cost or empty item
)

#: Reasons that mean "we could have afforded it and chose not to". Separated from the
#: budget-exhaustion reasons because they answer different operator questions: one says the
#: threshold is set too high, the other says the budget is too small.
VALUE_REASONS = ("below_value_threshold",)


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


@dataclass(frozen=True)
class Policy:
    """What may be spent, and what is worth spending on.

    `min_value` is the part that makes this a policy rather than a cap. It is a threshold on
    the caller's own expected-value estimate - recurrence, confidence, match strength, whatever
    the surface can defend - and an item below it is refused *whether or not there is room*.
    """

    per_turn_tokens: int = field(
        default_factory=lambda: _env_int("NEVERTWICE_BUDGET_TURN_TOKENS", 600))
    per_session_tokens: int = field(
        default_factory=lambda: _env_int("NEVERTWICE_BUDGET_SESSION_TOKENS", 6000))
    per_turn_latency_ms: int = field(
        default_factory=lambda: _env_int("NEVERTWICE_BUDGET_TURN_LATENCY_MS", 250))
    per_session_latency_ms: int = field(
        default_factory=lambda: _env_int("NEVERTWICE_BUDGET_SESSION_LATENCY_MS", 5000))
    min_value: float = field(
        default_factory=lambda: _env_float("NEVERTWICE_BUDGET_MIN_VALUE", 0.25))

    def as_dict(self) -> dict:
        return asdict(self)

    def decide(self, ledger: "Ledger", *, item: str, tokens: int, value: float,
               latency_ms: int = 0) -> "Decision":
        """Spend or abstain, with the reason recorded on the ledger either way.

        Order matters and is deliberate: **value is checked first**. Checking budgets first
        would make a low-value item look affordable right up until the budget filled, which is
        truncation wearing a policy's clothes - the same item would be taken or refused
        depending on what came before it rather than on what it is worth.
        """
        if tokens <= 0 or not str(item or "").strip():
            return ledger.record(Decision(
                item=str(item or ""), tokens=max(0, tokens), value=value, spend=False,
                reason="nothing_to_spend_on", threshold=self.min_value,
                detail="an item with no cost or no identity is not a spending decision"))

        if value < self.min_value:
            return ledger.record(Decision(
                item=item, tokens=tokens, value=value, spend=False,
                reason="below_value_threshold", threshold=self.min_value,
                detail=(f"expected value {value:.3f} is under the {self.min_value:.3f} "
                        f"threshold; {ledger.turn_tokens_left(self)} turn token(s) were "
                        f"still available, so this is a judgement, not a shortage")))

        for remaining, limit, reason in (
                (ledger.turn_tokens_left(self), self.per_turn_tokens, "turn_tokens_exhausted"),
                (ledger.session_tokens_left(self), self.per_session_tokens,
                 "session_tokens_exhausted")):
            if tokens > remaining:
                return ledger.record(Decision(
                    item=item, tokens=tokens, value=value, spend=False, reason=reason,
                    threshold=self.min_value,
                    detail=(f"{tokens} token(s) would exceed the {remaining} remaining of a "
                            f"{limit}-token budget")))

        for remaining, limit, reason in (
                (ledger.turn_latency_left(self), self.per_turn_latency_ms,
                 "turn_latency_exhausted"),
                (ledger.session_latency_left(self), self.per_session_latency_ms,
                 "session_latency_exhausted")):
            if latency_ms > remaining:
                return ledger.record(Decision(
                    item=item, tokens=tokens, value=value, spend=False, reason=reason,
                    threshold=self.min_value, latency_ms=latency_ms,
                    detail=(f"{latency_ms} ms would exceed the {remaining} ms remaining of a "
                            f"{limit} ms budget")))

        return ledger.record(Decision(
            item=item, tokens=tokens, value=value, spend=True, reason="spent",
            threshold=self.min_value, latency_ms=latency_ms,
            detail=f"expected value {value:.3f} clears {self.min_value:.3f} and it fits"))


@dataclass
class Decision:
    """One spend-or-abstain call, and why it went that way."""

    item: str
    tokens: int
    value: float
    spend: bool
    reason: str
    threshold: float
    latency_ms: int = 0
    detail: str = ""

    def __post_init__(self):
        if self.reason not in REASONS:
            raise ValueError(f"unknown reason {self.reason!r}; expected one of "
                             f"{', '.join(REASONS)}")

    @property
    def abstained(self) -> bool:
        return not self.spend

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ledger:
    """What a session has spent, refused, and been told it avoided."""

    decisions: list = field(default_factory=list)
    turn_tokens: int = 0
    turn_latency_ms: int = 0
    session_tokens: int = 0
    session_latency_ms: int = 0
    #: Caller-supplied only. See the module docstring: this module will not invent a saving.
    avoided_tokens: int = 0
    started: float = field(default_factory=time.time)

    def record(self, decision: Decision) -> Decision:
        self.decisions.append(decision)
        if decision.spend:
            self.turn_tokens += decision.tokens
            self.session_tokens += decision.tokens
            self.turn_latency_ms += decision.latency_ms
            self.session_latency_ms += decision.latency_ms
        return decision

    def end_turn(self) -> None:
        """Reset the per-turn counters. The session counters keep running, which is the whole
        reason there are two: a per-turn cap alone lets a long session spend without limit."""
        self.turn_tokens = 0
        self.turn_latency_ms = 0

    def credit_avoided(self, tokens: int, *, because: str) -> None:
        """Record tokens the caller can defend as avoided, with the reason it can defend them.

        `because` is required and stored. An unattributed saving is the kind of number that
        ends up on a badge and cannot be traced back, which is the failure task B8 spent an
        iteration undoing across this repository.
        """
        if tokens <= 0 or not str(because or "").strip():
            return
        self.avoided_tokens += int(tokens)
        self.decisions.append(Decision(
            item=f"avoided:{because}", tokens=0, value=0.0, spend=False,
            reason="nothing_to_spend_on", threshold=0.0,
            detail=f"credited {int(tokens)} avoided token(s): {because}"))

    def turn_tokens_left(self, policy: Policy) -> int:
        return max(0, policy.per_turn_tokens - self.turn_tokens)

    def session_tokens_left(self, policy: Policy) -> int:
        return max(0, policy.per_session_tokens - self.session_tokens)

    def turn_latency_left(self, policy: Policy) -> int:
        return max(0, policy.per_turn_latency_ms - self.turn_latency_ms)

    def session_latency_left(self, policy: Policy) -> int:
        return max(0, policy.per_session_latency_ms - self.session_latency_ms)

    @property
    def abstentions(self) -> list:
        return [d for d in self.decisions if d.abstained and d.reason != "nothing_to_spend_on"]

    def report(self, policy: Policy | None = None) -> dict:
        """Consumed, avoided and net - plus every abstention and its reason.

        `net` is `avoided - consumed`, and it is labelled an estimate because `avoided` is
        whatever the caller could defend. A report that presented it as measured would be
        making the exact claim this project withdrew 120 of elsewhere.
        """
        spent = [d for d in self.decisions if d.spend]
        by_reason: dict = {}
        for decision in self.decisions:
            by_reason[decision.reason] = by_reason.get(decision.reason, 0) + 1
        refused_though_affordable = [d for d in self.decisions
                                     if d.reason in VALUE_REASONS]
        return {
            "schema_version": SCHEMA_VERSION,
            "consumed_tokens": self.session_tokens,
            "avoided_tokens": self.avoided_tokens,
            "net_tokens": self.avoided_tokens - self.session_tokens,
            "net_basis": ("avoided minus consumed. `avoided` is caller-supplied and only ever "
                          "what the caller could defend, so this is an estimate; a zero here "
                          "means nothing was claimed, not that nothing was saved"),
            "latency_ms": self.session_latency_ms,
            "spent": len(spent),
            "abstained": len(self.abstentions),
            "refused_though_affordable": len(refused_though_affordable),
            "by_reason": by_reason,
            "decisions": [d.as_dict() for d in self.decisions],
            "policy": policy.as_dict() if policy else None,
        }


def render(report: dict) -> str:
    """The one-line operator summary."""
    net = report["net_tokens"]
    line = (f"budget: {report['consumed_tokens']} tok spent · {report['spent']} shown · "
            f"{report['abstained']} withheld")
    if report["refused_though_affordable"]:
        line += f" ({report['refused_though_affordable']} not worth it, not unaffordable)"
    if report["avoided_tokens"]:
        line += f" · ~{report['avoided_tokens']} avoided, net {net:+d} (estimate)"
    return line
