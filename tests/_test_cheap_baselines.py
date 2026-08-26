#!/usr/bin/env python3
"""The cheap-baseline suite, checked (GOAL F2).

F2's exit criterion has two branches and no third: *the central claim survives beyond every
cheap alternative, or is narrowed in writing.* A suite that only checked the arms ran would let
the interesting failure through - a harness can run all six baselines and still quietly report a
win it did not earn.

So the checks here are about the honesty of the comparison rather than its outcome:

* all six B5 baselines are present, and the arm that could not be run is **named as not run**
  instead of being dropped;
* the two baselines that hurt are built to be **strong**, not to be beatable: the curated file is
  charged its full length on every episode, and the linter arm is an explicit oracle upper bound
  that never cries wolf;
* the verdict is **derived** from the numbers, and when the claim does not survive, a written
  narrowing exists and says which arms it does not beat;
* a difference smaller than the interval the sample supports is reported as *not distinguished*,
  never as a win.

The last one is the check that would have caught this project publishing a tie as a result.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import cheap_baselines as CB      # noqa: E402
import matched_conditions as MC   # noqa: E402

PASSED = 0
FAILED = 0

#: The six the policy names, plus the extra arm this project already ran. `llm_session_summary`
#: appears as a *stub* here and must never be reported under its own name.
B5 = {"no_memory", "full_history", "lexical_recall", "curated_agents_md",
      "llm_session_summary", "linter_or_test"}

REPORT = CB.build(save=False)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═════════════════ all six baselines, none quietly dropped ═════════════

def test_every_b5_baseline_is_accounted_for() -> None:
    print("\n- the policy names six; all six are here -")
    ran = set(REPORT["arms"])
    not_run = set(REPORT["arms_not_run"])
    for name in sorted(B5):
        if name == "llm_session_summary":
            check(f"{name} is declared not-run rather than dropped", name in not_run,
                  "an unrun baseline that vanishes from the table is a baseline nobody checks")
            continue
        check(f"{name} ran", name in ran, str(sorted(ran)))
    check("the stub is never reported under the LLM arm's name",
          "llm_session_summary" not in ran,
          "a deterministic summariser reported as an LLM result is a false claim")
    check("and the stub is labelled as extractive",
          "session_summary_extractive" in ran, str(sorted(ran)))
    check("the not-run arm says what the owner must do",
          "GATED" in REPORT["arms_not_run"]["llm_session_summary"],
          REPORT["arms_not_run"]["llm_session_summary"][:80])


def test_the_added_arms_are_built_to_be_strong() -> None:
    """A baseline built to lose proves nothing about the thing that beats it."""
    print("\n- the two arms that hurt are not strawmen -")
    curated = REPORT["arms"]["curated_agents_md"]
    check("the curated file is charged on EVERY episode, not only when it helps",
          curated["standing_tokens_per_episode"] > 0,
          "an always-injected file billed per firing is billed wrong")
    check("and that standing cost dominates its per-episode total",
          curated["tokens_per_episode"] >= curated["standing_tokens_per_episode"],
          f"{curated['tokens_per_episode']} vs {curated['standing_tokens_per_episode']}")

    linter = REPORT["arms"]["linter_or_test"]
    zero = linter["zero_false_alarms"]
    check("the linter arm reaches a zero-false-alarm operating point", zero is not None,
          "a linter that flags ordinary work is not a linter anybody runs")
    if zero:
        check("it never cries wolf", zero["fp"] == 0, str(zero))
        # The real quantity: every POSITIVE EPISODE whose failure class a tool covers.
        # The first version of this check compared against the count of covered *classes*
        # and then `or`-ed in `tp > 0`, so it passed for the wrong reason either way.
        covered_stems = {c["stem"] for c in REPORT["linter_coverage"] if c["caught"]}
        expected = sum(1 for e in MC.load_corpus()["episodes"]
                       if e["label"] in covered_stems)
        check("and it catches every instance of a class it covers",
              zero["tp"] == expected, f"{zero['tp']} of an expected {expected}")
    covered = [c for c in REPORT["linter_coverage"] if c["caught"]]
    check("its coverage is enumerated per failure class, with a reason",
          len(covered) >= 4 and all(c["by"] for c in REPORT["linter_coverage"]),
          f"{len(covered)} covered of {len(REPORT['linter_coverage'])}")
    check("and the uncovered classes give a reason too, not a blank",
          all(c["by"] for c in REPORT["linter_coverage"] if not c["caught"]))


def test_the_linter_oracle_is_declared_an_oracle() -> None:
    """An upper bound presented as a measurement would overstate the baseline AND the winner."""
    print("\n- the strongest honest form, said out loud -")
    doc = CB.arm_linter_or_test.__doc__ or ""
    check("the arm's docstring calls it an oracle upper bound",
          "ORACLE" in doc and "ceiling" in doc.lower(), doc[:80])
    check("it is scored from ground truth, deliberately",
          "ground truth" in doc.lower())


# ═════════════════════════ THE EXIT CRITERION ══════════════════════════

def test_the_verdict_is_derived_not_asserted() -> None:
    print("\n- the verdict comes from the numbers -")
    v = REPORT["verdict"]
    check("a verdict exists", isinstance(v.get("survives_every_cheap_alternative"), bool),
          str(v.get("survives_every_cheap_alternative")))
    check("the criterion is quoted in the artifact", "narrowed in writing" in v["criterion"])

    ours = REPORT["arms"]["nevertwice"]["zero_false_alarms"] or {}
    our_recall = ours.get("recall") or 0.0
    half = v["recall_half_width_95"]
    check("a sampling interval is computed", half is not None and half > 0, str(half))

    misfiled = []
    for name in REPORT["arms"]:
        if name == "nevertwice":
            continue
        theirs = REPORT["arms"][name]["zero_false_alarms"]
        if theirs is None:
            bucket = "cannot_operate_at_zero_false_alarms"
        else:
            gap = our_recall - (theirs.get("recall") or 0.0)
            bucket = ("indistinguishable_at_this_sample_size" if abs(gap) <= half
                      else "beaten_decisively" if gap > 0 else "beats_the_memory_arm")
        if name not in v[bucket]:
            misfiled.append(f"{name} should be in {bucket}")
    check("every arm is filed by its own numbers", not misfiled, "; ".join(misfiled[:3]))


def test_a_difference_inside_the_noise_is_never_called_a_win() -> None:
    """The check that would have caught publishing a tie as a result."""
    print("\n- THE EXIT CRITERION: a win inside the interval is not a win -")
    v = REPORT["verdict"]
    ours = (REPORT["arms"]["nevertwice"]["zero_false_alarms"] or {}).get("recall") or 0.0
    half = v["recall_half_width_95"]

    overclaimed = []
    for name in v["beaten_decisively"]:
        theirs = (REPORT["arms"][name]["zero_false_alarms"] or {}).get("recall") or 0.0
        if ours - theirs <= half:
            overclaimed.append(f"{name}: gap {ours - theirs:.4f} <= half-width {half}")
    check("THE EXIT CRITERION: nothing is 'ruled out' on a difference inside the noise",
          not overclaimed, "; ".join(overclaimed))

    if v["indistinguishable_at_this_sample_size"]:
        check("survival is NOT claimed while an arm is indistinguishable",
              v["survives_every_cheap_alternative"] is False,
              "a claim cannot survive an alternative it has not been distinguished from")


def test_a_failed_claim_is_narrowed_in_writing() -> None:
    print("\n- the other branch of the exit criterion -")
    v = REPORT["verdict"]
    narrowing = " ".join(v["narrowing"]).lower()
    check("a written narrowing exists", bool(v["narrowing"]), str(v["narrowing"]))
    if not v["survives_every_cheap_alternative"]:
        check("it says the claim is NARROWED", "narrowed" in narrowing,
              "an unsurvived criterion with no narrowing is an unreported failure")
        named = v["beats_the_memory_arm"] + v["indistinguishable_at_this_sample_size"]
        missing = [n for n in named if n.lower() not in narrowing]
        check("and it names every arm the claim does not beat", not missing, str(missing))
    check("the narrowing warns that the LLM arm has not run",
          "not been run" in narrowing or "not run" in narrowing,
          "a missing arm that goes unmentioned reads as an arm that was beaten")
    check("and it says the linter arm is the one to read hardest",
          "linter" in narrowing)


def test_every_narrowing_branch_is_exercised() -> None:
    """The branches the current data does not reach still ship, and can still be wrong.

    On this corpus nothing lands in `beats_the_memory_arm` - the two close arms fall inside the
    sampling interval instead - so the branch that names an arm which BEATS the memory arm is
    never taken by the real run. A mutation that deleted its wording survived the whole suite.
    A branch that only fires on data we do not have is exactly the branch to unit-test.
    """
    print("\n- the narrowing branches the real data never reaches -")
    beats = CB._narrowing([], ["lexical_recall"], [], 0.17)
    text = " ".join(beats).lower()
    check("a beaten claim is narrowed", "narrowed" in text, str(beats[:1]))
    check("and the arm that beats it is named by name", "lexical_recall" in text,
          "'results were mixed' names nobody and warns nobody")

    close = CB._narrowing([], [], ["linter_or_test"], 0.17)
    text = " ".join(close).lower()
    check("an indistinguishable arm is named too", "linter_or_test" in text, str(close[:1]))
    check("and the honest wording is prescribed",
          "not distinguished from" in text, str(close[:1]))

    clean = CB._narrowing(["no_memory"], [], [], 0.17)
    text = " ".join(clean).lower()
    check("a claim that survives says so", "survives every cheap alternative" in text,
          str(clean))
    check("and still says it is one corpus", "one corpus" in text, str(clean))

    for narrowing in (beats, close, clean):
        joined = " ".join(narrowing).lower()
        check("every branch warns the LLM arm has not run",
              "not been run" in joined or "not run" in joined, str(narrowing[-2:]))


def test_the_corpus_limitation_travels_with_the_verdict() -> None:
    print("\n- a verdict without its corpus is a verdict nobody can weigh -")
    check("the artifact carries the corpus limitations",
          len(REPORT["corpus"]["limitations"]) >= 3,
          str(len(REPORT["corpus"]["limitations"])))
    check("including that it is single-author",
          any("single-author" in lim or "author" in lim
              for lim in REPORT["corpus"]["limitations"]))
    check("the same corpus F1 used", REPORT["corpus"]["episodes"] ==
          len(MC.load_corpus()["episodes"]),
          "two harnesses disagreeing about the corpus makes both unreadable")


def test_the_gated_arm_refuses_rather_than_pretending() -> None:
    print("\n- asking for the model arm does not silently give the stub -")
    rc = CB.main(["--summariser=model"])
    check("requesting the model arm exits non-zero", rc != 0, str(rc))
    rc_ok = CB.main(["--summariser=extractive", "--json"])
    check("and the extractive arm still runs", rc_ok == 0, str(rc_ok))


def main() -> int:
    for fn in (test_every_b5_baseline_is_accounted_for,
               test_the_added_arms_are_built_to_be_strong,
               test_the_linter_oracle_is_declared_an_oracle,
               test_the_verdict_is_derived_not_asserted,
               test_a_difference_inside_the_noise_is_never_called_a_win,
               test_a_failed_claim_is_narrowed_in_writing,
               test_every_narrowing_branch_is_exercised,
               test_the_corpus_limitation_travels_with_the_verdict,
               test_the_gated_arm_refuses_rather_than_pretending):
        fn()
    print(f"\ncheap baselines: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
