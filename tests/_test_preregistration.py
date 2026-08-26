#!/usr/bin/env python3
"""The preregistration, checked (GOAL F8).

F8's exit criterion is *preregistered hypotheses and endpoints*. A document that lists four
hypotheses and calls itself a preregistration satisfies the words and none of the purpose, so
these checks are about the properties that make a preregistration bind:

* **every hypothesis has an endpoint, a test, a support rule AND a falsification rule.** A
  hypothesis with no way to fail is not a hypothesis, and it is the easiest thing to write by
  accident.
* **the hypothesis the exploratory phase expects to LOSE is registered.** A claim that quietly
  disappears between exploration and write-up is the failure preregistration exists to prevent,
  and this project has already withdrawn one published headline.
* **exploration is not offered as confirmation.** The document has to say, in its own words,
  that F1-F6 generated these hypotheses and therefore cannot confirm them.
* **the sample size is computed, not asserted** - and it must be computed with an exact test,
  because at these discordant-pair counts the normal approximation reports power the design
  does not have.

The power arithmetic is checked against cases with known answers: power must rise with the
sample, an effect of zero must be undetectable at any size, and a certain effect must need
fewer episodes than a marginal one.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "research"))
import preregistration as P       # noqa: E402

PASSED = 0
FAILED = 0

DOC = ROOT / "research" / "PREREGISTRATION.md"
REPORT = P.build(save=False)
TEXT = DOC.read_text(encoding="utf-8") if DOC.is_file() else ""


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════ every hypothesis can actually fail ════════════════════════

def test_every_hypothesis_is_falsifiable() -> None:
    """A hypothesis with no way to fail is the easiest thing to write by accident."""
    print("\n- THE EXIT CRITERION: hypotheses, endpoints, and a way to lose -")
    check("THE EXIT CRITERION: hypotheses are registered", len(P.HYPOTHESES) >= 3,
          str(len(P.HYPOTHESES)))
    ids = [h["id"] for h in P.HYPOTHESES]
    check("their ids are unique", len(ids) == len(set(ids)), str(ids))
    # The specific comparisons must be registered, not merely SOME four hypotheses. Renaming
    # or dropping the vs-lexical one left every other check green, because a generic "at least
    # one is expected to fail" passes whichever hypothesis happens to carry that note.
    comparisons = " ".join(h["comparison"] for h in P.HYPOTHESES).lower()
    for arm, why in (("lexical_recall", "the cheap baseline the exploratory phase could not beat"),
                     ("linter_or_test", "the arm it ties with and complements"),
                     ("coverage", "the only mechanism an ablation could name")):
        check(f"a hypothesis is registered against {arm}", arm.split("_")[0] in comparisons, why)
    for h in P.HYPOTHESES:
        for field in ("statement", "endpoint", "comparison", "test",
                      "supported_if", "falsified_if"):
            check(f"{h['id']}: has {field}", bool(h.get(field)) and len(h[field]) > 15,
                  str(h.get(field)))
        check(f"{h['id']}: support and falsification are not the same sentence",
              h["supported_if"] != h["falsified_if"], h["supported_if"])
        check(f"{h['id']}: the endpoint names a measurable quantity",
              any(w in h["endpoint"].lower() for w in ("rate", "lift", "count", "time")),
              h["endpoint"])
        check(f"{h['id']}: the test is named", "mcnemar" in h["test"].lower(), h["test"])


def test_the_losing_hypothesis_is_registered() -> None:
    """The whole reason preregistration exists."""
    print("\n- a claim that vanishes between exploration and write-up -")
    expected_to_fail = [h for h in P.HYPOTHESES
                        if "expected to fail" in h.get("exploratory_finding", "").lower()
                        or "expects to fail" in h.get("exploratory_finding", "").lower()]
    check("at least one hypothesis is registered as expected to FAIL",
          bool(expected_to_fail),
          "a preregistration containing only hypotheses we expect to win is a wish list")
    for h in expected_to_fail:
        check(f"{h['id']}: it is still fully specified",
              bool(h["endpoint"] and h["falsified_if"]),
              "an unfavourable hypothesis must be as testable as a favourable one")
    status = " ".join(REPORT["status"])
    check("the artifact says so in words", "expects to FAIL" in status or
          "expected to fail" in status.lower(), status[:200])
    check("and the document does too", "expect to fail" in TEXT.lower(), TEXT[:200])


def test_exploration_is_not_offered_as_confirmation() -> None:
    print("\n- the corpus that suggested a hypothesis is not evidence for it -")
    status = " ".join(REPORT["status"])
    check("the artifact states nothing has been confirmed",
          "NOTHING HERE HAS BEEN CONFIRMED" in status, status[:160])
    check("and says why using the same corpus would be circular",
          "circular" in status.lower(), status[:300])
    for phrase in ("exploratory", "confirmatory"):
        check(f"the document distinguishes {phrase} work", phrase in TEXT.lower(),
              "a preregistration that does not name the distinction is not making it")
    check("the document refuses to describe the research as confirmed",
          "Nothing here has been confirmed" in TEXT, TEXT[-400:])
    # Not just that the WORD appears somewhere - the document must actually assert the
    # relationship. Changing "so far is exploratory" to "so far is settled" left the word
    # "exploratory" elsewhere on the page and every check green.
    flat = " ".join(TEXT.split()).lower()
    check("it states that the work so far IS exploratory",
          "so far is **exploratory**".lower() in flat or "so far is exploratory" in flat,
          "the claim that the prior work is exploratory is the load-bearing one")
    for forbidden in ("so far is settled", "has been confirmed by", "confirms the claim"):
        check(f"it does not claim settledness: {forbidden!r}", forbidden not in flat,
              "a preregistration that describes its own prior work as settled is not one")
    check("and it says exploration cannot confirm its own hypotheses",
          "cannot also confirm them" in flat, TEXT[:800])


def test_the_narrowings_are_carried_forward() -> None:
    """What F1-F6 already established must constrain the confirmatory run."""
    print("\n- the confirmatory run inherits the narrowing, not just the claim -")
    for phrase, why in (
            ("strict subset", "F5's finding that the memory arm won nothing lexical missed"),
            ("linter", "F2/F5's tie with static analysis"),
            ("coverage normalisation", "F4's only nameable mechanism"),
            ("override burden", "F6's safety finding")):
        check(f"the document carries: {phrase}", phrase in TEXT.lower(), why)
    # Blockquote markers stripped, THEN whitespace-normalised: the claim wraps across lines
    # inside a `>` quote, so both the line break and the leading `>` land in the middle of the
    # sentence. Flattening whitespace alone left "repeated failures > through proactive" and
    # the check failed on the markdown rather than on the wording.
    import re as _re
    flat = " ".join(_re.sub(r"(?m)^\s*>\s?", "", TEXT).split())
    check("the systems claim is quoted verbatim",
          "reduce repeated failures through proactive inspectable interventions" in flat,
          "a preregistration that paraphrases the claim is testing a different one")
    check("retrieval is kept secondary", "secondary" in TEXT.lower())


# ═══════════════════ the arithmetic ════════════════════════════════════

def test_the_power_maths_behaves() -> None:
    print("\n- power, against cases with known answers -")
    check("power rises with the sample",
          P.power_at(200, 0.3, 0.9) > P.power_at(40, 0.3, 0.9),
          f"{P.power_at(200, 0.3, 0.9)} vs {P.power_at(40, 0.3, 0.9)}")
    check("a coin-flip effect is undetectable at any size",
          P.power_at(500, 0.3, 0.5) < 0.10, str(P.power_at(500, 0.3, 0.5)))
    check("a certain effect needs fewer episodes than a marginal one",
          (P.required_n(0.3, 1.0) or 10**9) < (P.required_n(0.3, 0.65) or 10**9),
          f"{P.required_n(0.3, 1.0)} vs {P.required_n(0.3, 0.65)}")
    check("more discordance means fewer episodes",
          (P.required_n(0.5, 0.9) or 10**9) <= (P.required_n(0.1, 0.9) or 10**9),
          f"{P.required_n(0.5, 0.9)} vs {P.required_n(0.1, 0.9)}")
    check("a zero-direction effect returns no sample size",
          P.required_n(0.3, 0.5) is None, str(P.required_n(0.3, 0.5)))
    check("power is a probability", 0.0 <= P.power_at(50, 0.3, 0.8) <= 1.0,
          str(P.power_at(50, 0.3, 0.8)))


def test_the_pmf_is_right() -> None:
    """The recurrence replaced a version using math.comb that never finished."""
    print("\n- the binomial the power table is built on -")
    check("it sums to one", abs(sum(P._pmf_row(40, 0.3)) - 1.0) < 1e-9,
          str(sum(P._pmf_row(40, 0.3))))
    check("it sums to one at a size that underflows the naive form",
          abs(sum(P._pmf_row(1500, 0.3)) - 1.0) < 1e-6,
          str(sum(P._pmf_row(1500, 0.3))))
    check("a fair coin is symmetric",
          abs(P.binom_pmf(2, 10, 0.5) - P.binom_pmf(8, 10, 0.5)) < 1e-12)
    check("it matches a hand-computed cell",
          abs(P.binom_pmf(2, 4, 0.5) - 6 / 16) < 1e-12, str(P.binom_pmf(2, 4, 0.5)))
    check("p=0 puts all mass at zero", P._pmf_row(5, 0.0)[0] == 1.0)
    check("p=1 puts all mass at n", P._pmf_row(5, 1.0)[5] == 1.0)


def test_mcnemar_rejection_region_is_exact() -> None:
    print("\n- the exact test, not the approximation -")
    check("no discordant pairs never rejects", not P.mcnemar_rejects(0, 0))
    check("an even split never rejects", not P.mcnemar_rejects(5, 10))
    check("five all-one-way pairs do NOT reject at 0.05",
          not P.mcnemar_rejects(0, 5),
          "the chi-squared shortcut would; the exact test does not, which is the point")
    check("six all-one-way pairs do reject", P.mcnemar_rejects(0, 6),
          "2 * 0.5**6 = 0.03125 <= 0.05")
    check("it is symmetric", P.mcnemar_rejects(0, 8) == P.mcnemar_rejects(8, 8))
    doc = (P.power_at.__doc__ or "") + (P.__doc__ or "")
    check("the module says it uses no normal approximation",
          "approximation" in doc.lower(), doc[:120])


def test_the_sample_size_is_published_and_uncomfortable() -> None:
    """"We ran what we had and it was not significant" must not become evidence of no effect."""
    print("\n- the number that stops a null being reported as a result -")
    rows = {r["id"]: r for r in REPORT["power"]}
    check("every hypothesis has a power row", set(rows) == {h["id"] for h in P.HYPOTHESES},
          str(sorted(rows)))
    for hid, r in rows.items():
        check(f"{hid}: power at the exploratory size is reported",
              isinstance(r["power_at_30_episodes"], float), str(r))
        check(f"{hid}: either a required n or a stated reason there is none",
              r["episodes_for_80_percent_power"] is not None or "no effect" in r["note"],
              str(r))
    status = " ".join(REPORT["status"])
    check("the artifact says the exploratory corpus is underpowered",
          "underpowered" in status, status[:300])
    check("and that a small null is not evidence of no effect",
          "not evidence of no effect" in status, status[:400])
    check("the document points at the command that computes it",
          "preregistration.py" in TEXT, TEXT[:400])


def test_the_corpus_requirements_bind() -> None:
    print("\n- the ways a result could be weakened afterwards -")
    check("requirements are preregistered", len(P.CORPUS_REQUIREMENTS) >= 4,
          str(len(P.CORPUS_REQUIREMENTS)))
    joined = " ".join(P.CORPUS_REQUIREMENTS).lower()
    check("THE BINDING ONE: a different author", "other than the author" in joined,
          "every exploratory result rests on an author-written corpus")
    check("labelling happens before the arms run", "before any arm is run" in joined, joined[:200])
    check("episodes may not paraphrase their own notes", "paraphrased" in joined, joined[:200])
    check("results are published per-episode", "per-episode" in joined, joined[:200])
    check("the document names the binding requirement too",
          "other than the author" in TEXT.lower(), TEXT[:600])


def test_the_analysis_plan_is_fixed_in_advance() -> None:
    print("\n- no outcome-dependent choices -")
    d = REPORT["design"]
    check("the test is fixed", "mcnemar" in d["test"].lower(), d["test"])
    check("alpha is fixed", d["alpha"] == P.ALPHA, str(d["alpha"]))
    check("the correction is fixed", "holm" in d["correction"].lower(), d["correction"])
    check("and the absence of an approximation is stated",
          "exact" in d["approximation"].lower(), d["approximation"])
    check("the document forbids outcome-dependent choices",
          "outcome-dependent" in TEXT.lower(), TEXT[:800])
    check("submission is named as a gate, not performed",
          "G7" in TEXT, "a preregistration that submits itself is not a preregistration")


def main() -> int:
    for fn in (test_every_hypothesis_is_falsifiable,
               test_the_losing_hypothesis_is_registered,
               test_exploration_is_not_offered_as_confirmation,
               test_the_narrowings_are_carried_forward,
               test_the_power_maths_behaves,
               test_the_pmf_is_right,
               test_mcnemar_rejection_region_is_exact,
               test_the_sample_size_is_published_and_uncomfortable,
               test_the_corpus_requirements_bind,
               test_the_analysis_plan_is_fixed_in_advance):
        fn()
    print(f"\npreregistration: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
