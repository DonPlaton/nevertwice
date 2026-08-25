#!/usr/bin/env python3
"""Budgets, and abstention as a decision rather than a side effect.

The payload has always had a cap: sections are added by priority until the character budget
runs out and the rest is dropped. That is truncation, and it has two properties this suite
exists to hold the replacement to.

**Truncation cannot refuse something that fits.** A worthless lesson gets injected whenever
there happens to be room. So the first thing asserted here is that an item is refused *while
the budget is nearly untouched*, purely because its expected value is below the threshold. No
length-based mechanism can produce that outcome, which makes it the sharpest available proof
that the decision is about worth rather than about room.

**Truncation cannot distinguish two items of the same size.** So the second assertion is that
two items with identical token cost get opposite decisions when their values differ - and,
because order is where this kind of thing usually breaks, that the answer does not depend on
which one was offered first.

GOAL D9's exit criterion is *abstention is a policy decision with a logged reason, not an
output-length side effect*, and it is checked directly: every decision carries a reason from a
closed set, every abstention explains itself in a sentence, and the reason distinguishes "could
not afford it" from "could afford it and declined".

The honesty checks matter as much as the mechanism. `avoided` is caller-supplied and requires
an attribution, because an unattributed saving is precisely the kind of number task B8 spent an
iteration withdrawing from this repository, and `net` is labelled an estimate wherever it is
reported.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import budget as B              # noqa: E402
import guards as G              # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ------------------------------------ the exit criterion, stated two ways


def test_an_affordable_item_can_still_be_refused() -> None:
    """The assertion no length-based mechanism can pass."""
    print("\n- refused with the budget nearly untouched -")
    policy = B.Policy(per_turn_tokens=10_000, per_session_tokens=100_000, min_value=0.30)
    ledger = B.Ledger()

    decision = policy.decide(ledger, item="a-weak-lesson", tokens=40, value=0.05)
    check("it was refused", decision.abstained, str(decision))
    check("and the reason is about worth, not room",
          decision.reason == "below_value_threshold", decision.reason)
    check("the budget really was available",
          ledger.turn_tokens_left(policy) > 9_000, str(ledger.turn_tokens_left(policy)))
    check("the explanation says so in a sentence",
          "still available" in decision.detail and "judgement" in decision.detail,
          decision.detail)
    check("nothing was consumed by a refusal", ledger.session_tokens == 0)

    report = ledger.report(policy)
    check("the report separates 'not worth it' from 'could not afford it'",
          report["refused_though_affordable"] == 1, str(report["by_reason"]))


def test_two_items_of_the_same_size_get_different_answers() -> None:
    print("\n- identical cost, opposite decisions, either order -")
    for first_value, second_value in ((0.05, 0.95), (0.95, 0.05)):
        policy = B.Policy(per_turn_tokens=10_000, min_value=0.30)
        ledger = B.Ledger()
        a = policy.decide(ledger, item="a", tokens=100, value=first_value)
        b = policy.decide(ledger, item="b", tokens=100, value=second_value)
        low, high = (a, b) if first_value < second_value else (b, a)
        check(f"the low-value item is refused (offered "
              f"{'first' if first_value < second_value else 'second'})", low.abstained)
        check("the high-value item of identical size is spent", high.spend)
        check("only the spent one consumed budget", ledger.session_tokens == 100,
              str(ledger.session_tokens))


def test_every_decision_carries_a_reason_from_a_closed_set() -> None:
    print("\n- a reason you can count, on every decision -")
    policy = B.Policy(per_turn_tokens=100, per_turn_latency_ms=50, min_value=0.2)
    ledger = B.Ledger()
    policy.decide(ledger, item="spent", tokens=40, value=0.9)
    policy.decide(ledger, item="cheap-but-weak", tokens=10, value=0.01)
    policy.decide(ledger, item="too-big", tokens=500, value=0.9)
    policy.decide(ledger, item="too-slow", tokens=10, value=0.9, latency_ms=5_000)
    policy.decide(ledger, item="", tokens=0, value=0.9)

    check("every decision has a reason", all(d.reason for d in ledger.decisions))
    check("every reason is from the declared set",
          all(d.reason in B.REASONS for d in ledger.decisions),
          str([d.reason for d in ledger.decisions]))
    check("every decision explains itself in a sentence",
          all(len(d.detail) > 20 for d in ledger.decisions),
          str([d.detail for d in ledger.decisions]))
    reasons = [d.reason for d in ledger.decisions]
    check("the four distinct outcomes are distinguishable",
          reasons == ["spent", "below_value_threshold", "turn_tokens_exhausted",
                      "turn_latency_exhausted", "nothing_to_spend_on"], str(reasons))
    check("an unknown reason cannot be constructed at all",
          _raises(lambda: B.Decision(item="x", tokens=1, value=1.0, spend=False,
                                     reason="because", threshold=0.1)),
          "a free-text reason is a reason nobody will ever aggregate")


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:               # noqa: BLE001 - any refusal is the point
        return True
    return False


# --------------------------------------------------------- the budgets


def test_turn_and_session_budgets_are_both_enforced() -> None:
    print("\n- a per-turn cap alone lets a long session spend without limit -")
    policy = B.Policy(per_turn_tokens=100, per_session_tokens=250, min_value=0.0)
    ledger = B.Ledger()

    for turn in range(3):
        first = policy.decide(ledger, item=f"t{turn}-a", tokens=100, value=1.0)
        second = policy.decide(ledger, item=f"t{turn}-b", tokens=100, value=1.0)
        if turn < 2:
            check(f"turn {turn}: the first item fits", first.spend, str(first))
            check(f"turn {turn}: the second exhausts the turn budget",
                  second.reason == "turn_tokens_exhausted", second.reason)
        ledger.end_turn()

    check("the session cap eventually stops it", ledger.session_tokens <= 250,
          str(ledger.session_tokens))
    check("and the reason names the session, not the turn",
          any(d.reason == "session_tokens_exhausted" for d in ledger.decisions),
          str([d.reason for d in ledger.decisions]))
    check("ending a turn resets the turn counter but not the session",
          ledger.turn_tokens == 0 and ledger.session_tokens > 0,
          f"turn={ledger.turn_tokens} session={ledger.session_tokens}")


def test_latency_is_budgeted_as_well_as_tokens() -> None:
    print("\n- context is not the only thing being spent -")
    policy = B.Policy(per_turn_latency_ms=100, per_session_latency_ms=150, min_value=0.0)
    ledger = B.Ledger()
    check("a fast item is spent",
          policy.decide(ledger, item="fast", tokens=10, value=1.0, latency_ms=90).spend)
    check("a slow one is refused for latency, not tokens",
          policy.decide(ledger, item="slow", tokens=10, value=1.0,
                        latency_ms=90).reason == "turn_latency_exhausted")
    ledger.end_turn()
    check("the session latency cap outlives the turn",
          policy.decide(ledger, item="slow2", tokens=10, value=1.0,
                        latency_ms=90).reason == "session_latency_exhausted")


# ---------------------------------------------------- honest accounting


def test_avoided_tokens_are_never_invented() -> None:
    print("\n- a saving this module did not measure is not reported -")
    policy = B.Policy(min_value=0.0)
    ledger = B.Ledger()
    policy.decide(ledger, item="x", tokens=120, value=1.0)

    report = ledger.report(policy)
    check("consumed is what was actually spent", report["consumed_tokens"] == 120)
    check("avoided is zero until someone can defend a figure",
          report["avoided_tokens"] == 0, str(report["avoided_tokens"]))
    check("net says it is an estimate", "estimate" in report["net_basis"])
    check("and says a zero means nothing was claimed",
          "nothing was claimed" in report["net_basis"], report["net_basis"])

    ledger.credit_avoided(0, because="nothing")
    check("a zero credit is ignored", ledger.avoided_tokens == 0)
    ledger.credit_avoided(500, because="")
    check("an unattributed credit is refused", ledger.avoided_tokens == 0,
          "a saving with no reason is the kind of number that ends up on a badge")

    ledger.credit_avoided(500, because="the agent would have re-read billing/invoices.py")
    check("an attributed credit is recorded", ledger.avoided_tokens == 500)
    check("the attribution is kept with it",
          any("re-read billing" in d.detail for d in ledger.decisions))
    check("net is avoided minus consumed",
          ledger.report(policy)["net_tokens"] == 500 - 120)
    check("the rendered line marks it as an estimate",
          "estimate" in B.render(ledger.report(policy)),
          B.render(ledger.report(policy)))


def test_the_report_is_countable() -> None:
    print("\n- the operator question: why did memory go quiet -")
    policy = B.Policy(per_turn_tokens=100, min_value=0.5)
    ledger = B.Ledger()
    policy.decide(ledger, item="a", tokens=30, value=0.9)
    policy.decide(ledger, item="b", tokens=30, value=0.1)
    policy.decide(ledger, item="c", tokens=30, value=0.1)
    report = ledger.report(policy)
    check("reasons are tallied", report["by_reason"]["below_value_threshold"] == 2,
          str(report["by_reason"]))
    check("the spent count is right", report["spent"] == 1)
    check("the abstained count is right", report["abstained"] == 2)
    check("the policy travels with the report",
          report["policy"]["min_value"] == 0.5, str(report["policy"]))
    check("every decision is in the report", len(report["decisions"]) == 3)


# ------------------------------------------------- wired to a real surface


def test_the_hot_path_spends_under_the_policy() -> None:
    print("\n- guards_check, budgeted -")
    ledger = G.load_guards()
    weak = G.make_guard(r"weak_marker_xyz", "past mistake: a guard nobody has corroborated",
                        project="acme")
    strong = G.make_guard(r"strong_marker_xyz", "past mistake: a well-corroborated guard",
                          project="acme")
    strong["confidence"] = 0.95
    G.register(ledger, weak)
    G.register(ledger, strong)
    G.save_guards(ledger)
    text = "weak_marker_xyz and strong_marker_xyz in one action"

    plain = api.guards_check(text, project="acme")
    check("both guards fire without a budget", len(plain) == 2, str(len(plain)))

    spend = api.budget_ledger()
    policy = api.budget_policy(min_value=0.9, per_turn_tokens=10_000)
    kept = api.guards_check(text, project="acme", budget=spend, policy=policy)

    check("the weak guard is withheld even though there was room", len(kept) == 1,
          str([h["id"] for h in kept]))
    check("the one kept is the corroborated one", kept[0]["id"] == strong["id"])
    check("the withholding is recorded with its reason",
          any(d.reason == "below_value_threshold" for d in spend.decisions),
          str([d.reason for d in spend.decisions]))
    check("the report counts it as affordable-but-refused",
          spend.report(policy)["refused_though_affordable"] == 1)

    # A blocking guard is a hard stop. Withholding one to save context would be the budget
    # overruling safety, and that is not a trade this project makes.
    ledger = G.load_guards()
    for g in ledger:
        if g["id"] == weak["id"]:
            g["status"] = "blocking"
    G.save_guards(ledger)
    spend2 = api.budget_ledger()
    kept2 = api.guards_check(text, project="acme", budget=spend2,
                             policy=api.budget_policy(min_value=0.99))
    check("a blocking guard is never withheld for being low-value",
          any(h["id"] == weak["id"] for h in kept2), str([h["id"] for h in kept2]))
    check("but it still consumes budget, so the accounting stays honest",
          spend2.session_tokens > 0, str(spend2.session_tokens))
    check("and the exemption is stated, not silent",
          any("not withheld to save context" in d.detail for d in spend2.decisions),
          str([d.detail for d in spend2.decisions]))


def test_no_budget_argument_changes_nothing() -> None:
    """The budget is opt-in. The default hot path must be byte-identical to before."""
    print("\n- the default path is untouched -")
    text = "weak_marker_xyz and strong_marker_xyz in one action"
    hits = api.guards_check(text, project="acme")
    check("no budget key is attached by default",
          all("budget" not in h for h in hits), str(hits[:1]))
    check("the hit shape is unchanged",
          all(set(h) == {"id", "status", "message", "scope"} for h in hits),
          str([sorted(h) for h in hits]))


def main() -> int:
    for fn in (test_an_affordable_item_can_still_be_refused,
               test_two_items_of_the_same_size_get_different_answers,
               test_every_decision_carries_a_reason_from_a_closed_set,
               test_turn_and_session_budgets_are_both_enforced,
               test_latency_is_budgeted_as_well_as_tokens,
               test_avoided_tokens_are_never_invented,
               test_the_report_is_countable,
               test_the_hot_path_spends_under_the_policy,
               test_no_budget_argument_changes_nothing):
        fn()
    print(f"\nbudget: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
