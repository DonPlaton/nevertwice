#!/usr/bin/env python3
"""The billable generator, checked without ever billing anything (gate G8).

`research/_frontier.py` is the one module in this repository that can spend the owner's money.
Everything it does before the network call is therefore the thing worth testing, and all of it
is testable offline:

* **two locks, not one.** A key in the environment is not approval. The owner's machine exports
  `ANTHROPIC_API_KEY` for ordinary work, so a suite that drove the frontier entry point would
  have billed them for checking that the refusal works. Spending needs the key *and* an
  explicit switch, and this suite asserts the switch is load-bearing.
* **the refusal says which half is shut**, because "gated" without a reason is a dead end for
  whoever hits it.
* **the price table carries its date**, and every model a slot can resolve to is in it - a
  cost estimate that silently falls back to "unknown" is worse than no estimate.
* **the cell declares its decoding.** The local cells are greedy at 70 tokens; a frontier cell
  is not, and a grid that let the two look like the same procedure would be comparing a model
  against a sampler.
* **no key ever reaches the output.** The module reports presence, never a value.

The one thing this suite cannot check is that a real call returns something useful. That is the
run itself, and it is the owner's to approve.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import _frontier as F             # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


class scrubbed:
    """Run a block with every credential and approval variable removed."""

    VARS = (*F.KEY_VARS, F.OPT_IN_VAR)

    def __enter__(self):
        self.saved = {v: os.environ.pop(v, None) for v in self.VARS}
        return self

    def __exit__(self, *exc):
        for v in self.VARS:
            os.environ.pop(v, None)
        for var, value in self.saved.items():
            if value is not None:
                os.environ[var] = value
        return False


# ═══════════════════ the gate has two locks ════════════════════════════

def test_a_key_alone_is_not_approval_to_spend() -> None:
    print("\n- a key in the environment is not consent -")
    with scrubbed():
        check("nothing set: not allowed", not F.billable_allowed())
        check("nothing set: reason names the key variable",
              F.KEY_VARS[0] in F.why_blocked(), F.why_blocked())

        os.environ[F.KEY_VARS[0]] = "sk-not-a-real-key"
        check("key only: credentials are seen", F.credentials_present())
        check("key only: still NOT allowed to spend", not F.billable_allowed(),
              "a stray key would have billed the owner")
        check("key only: reason says the spend is unapproved",
              "not approved" in F.why_blocked() and F.OPT_IN_VAR in F.why_blocked(),
              F.why_blocked())

        os.environ[F.OPT_IN_VAR] = "1"
        check("key + approval: allowed", F.billable_allowed())

        del os.environ[F.KEY_VARS[0]]
        check("approval without a key: not allowed", not F.billable_allowed())


def test_generate_refuses_before_it_imports_anything() -> None:
    """The refusal must happen before the SDK import, or a machine without `anthropic`
    installed reports a missing package where the real answer is 'you have not approved this'."""
    print("\n- the refusal comes first -")
    with scrubbed():
        try:
            F.generate("claude-opus-5", "this must never be sent")
        except F.NoCredentials as exc:
            check("raises NoCredentials", True)
            check("and says nothing was billed", "nothing was billed" in str(exc), str(exc))
        except ImportError:
            check("raises NoCredentials rather than ImportError", False,
                  "it tried to import the SDK before checking the gate")
        else:
            check("raises rather than returning", False, "generate() returned with no gate open")


def test_the_opt_in_is_not_satisfied_by_any_truthy_string() -> None:
    """`NEVERTWICE_ALLOW_BILLABLE=0` must mean no. A bare truthiness test on the string would
    read "0", "false" and "no" as approval, which is the wrong direction to be wrong in."""
    print("\n- the switch reads its value, not its presence -")
    with scrubbed():
        os.environ[F.KEY_VARS[0]] = "sk-not-a-real-key"
        for value in ("0", "false", "no", "off", "", "  "):
            os.environ[F.OPT_IN_VAR] = value
            check(f"{value!r} is not approval", not F.billable_allowed())
        for value in ("1", "true", "yes", "on", " 1 "):
            os.environ[F.OPT_IN_VAR] = value
            check(f"{value!r} is approval", F.billable_allowed())


# ═══════════════════ the cost table is honest ══════════════════════════

def test_every_slot_resolves_into_the_price_table() -> None:
    print("\n- a slot's default model has a published price -")
    with scrubbed():
        for slot in F.SLOTS:
            model = F.resolve(slot)
            check(f"{slot} -> {model}", F.price(model) is not None, model)
        check("the table carries the date it was checked",
              isinstance(F.PRICES_AS_OF, str) and F.PRICES_AS_OF.count("-") == 2,
              F.PRICES_AS_OF)


def test_a_slot_can_be_repointed_and_the_cell_says_where() -> None:
    print("\n- the slot is a slot, and the artifact names the occupant -")
    with scrubbed():
        var, default = F.SLOTS["frontier-a"]
        os.environ[var] = "claude-haiku-4-5"
        try:
            check("the override wins", F.resolve("frontier-a") == "claude-haiku-4-5")
            note = F.decoding_note(F.resolve("frontier-a"))
            check("the note records the resolved model, not the slot",
                  note["resolved_model"] == "claude-haiku-4-5", str(note["resolved_model"]))
        finally:
            del os.environ[var]
        check("removing the override restores the default", F.resolve("frontier-a") == default)
    try:
        F.resolve("frontier-z")
    except KeyError:
        check("an unknown slot raises rather than inventing a model", True)
    else:
        check("an unknown slot raises rather than inventing a model", False)


def test_an_unpriced_model_reports_unknown_rather_than_zero() -> None:
    """Estimating a model that is not in the table must not quietly cost $0.00 - that reads as
    free, which is the most expensive possible way to be wrong here."""
    print("\n- an unknown model costs 'unknown', never nothing -")
    est = F.estimate("some-model-that-does-not-exist", 10_000, 10_000)
    check("usd is None", est["usd"] is None, str(est.get("usd")))
    check("and it says why", "not in the" in est.get("note", ""), est.get("note", ""))


def test_the_arithmetic_is_per_million_tokens() -> None:
    print("\n- the cost maths -")
    est = F.estimate("claude-opus-5", 1_000_000, 1_000_000)
    inp, out = F.PRICES_2026_08_26["claude-opus-5"]
    check("one million of each costs input+output", abs(est["usd"] - (inp + out)) < 1e-6,
          str(est["usd"]))
    half = F.estimate("claude-opus-5", 500_000, 0)
    check("input scales linearly", abs(half["usd"] - inp / 2) < 1e-6, str(half["usd"]))


# ═══════════════════ the decoding difference is declared ═══════════════

def test_the_cell_declares_that_it_is_not_matched_decoding() -> None:
    print("\n- a frontier row does not pretend to be a rung of the ladder -")
    note = F.decoding_note("claude-opus-5")
    check("it names the effort and the ceiling",
          F.EFFORT in note["decoding"] and str(F.MAX_TOKENS) in note["decoding"],
          note["decoding"])
    check("it states plainly that this is NOT the local decoding",
          "NOT the same decoding" in note["caveat"], note["caveat"][:80])
    lowered = note["caveat"].lower()
    check("it explains why (thinking / no temperature)",
          "temperature" in lowered and "thinking" in lowered, note["caveat"][:120])


def test_no_key_value_is_ever_returned_or_printed() -> None:
    """Presence, never value. `why_blocked()` is printed to stderr on every refusal, so it is
    the one string most likely to end up in a log or a pasted issue."""
    print("\n- the module reports presence, not secrets -")
    with scrubbed():
        secret = "sk-ant-THIS-IS-THE-SECRET-VALUE"
        os.environ[F.KEY_VARS[0]] = secret
        check("why_blocked() does not contain the key", secret not in F.why_blocked(),
              F.why_blocked())
        note = F.decoding_note(F.resolve("frontier-a"))
        check("the decoding note does not contain the key",
              secret not in repr(note))
        est = F.estimate(F.resolve("frontier-a"), 100, 100)
        check("the estimate does not contain the key", secret not in repr(est))


def main() -> int:
    for fn in (test_a_key_alone_is_not_approval_to_spend,
               test_generate_refuses_before_it_imports_anything,
               test_the_opt_in_is_not_satisfied_by_any_truthy_string,
               test_every_slot_resolves_into_the_price_table,
               test_a_slot_can_be_repointed_and_the_cell_says_where,
               test_an_unpriced_model_reports_unknown_rather_than_zero,
               test_the_arithmetic_is_per_million_tokens,
               test_the_cell_declares_that_it_is_not_matched_decoding,
               test_no_key_value_is_ever_returned_or_printed):
        fn()
    print(f"\nfrontier generator: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
