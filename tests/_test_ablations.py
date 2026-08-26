#!/usr/bin/env python3
"""The full-loop ablations, checked (GOAL F4).

F4's exit criterion is that the paper can name **which** mechanism causes the improvement. Two
things have to hold before any such name is worth writing down:

* **the reference is the shipped system.** The ablations are a re-implementation of
  `risk_score` with switches in it, so the `full` variant is checked against the real function
  on every episode. If it ever disagrees, every delta in the table is measured against a system
  nobody runs - which is the failure F1 already had once, when its arm scored `risk_score`
  without the IDF table `anticipate()` builds.
* **each variant removes exactly one thing.** An ablation that changes two mechanisms names
  neither of them.

The rest is about what the table is allowed to say: a delta inside the sampling interval is
*not shown to matter* and never *does nothing*; a variant that loses the zero-false-alarm
operating point entirely gets its own verdict rather than a subtraction against a missing value;
and the five mechanisms this surface cannot reach are listed as **not exercised**, because "we
did not measure it" and "we measured it and it did nothing" are different sentences.
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
import ablations as AB            # noqa: E402
import anticipate as A            # noqa: E402
import matched_conditions as MC   # noqa: E402

PASSED = 0
FAILED = 0

#: The five GOAL names this surface cannot reach. Named here so a silent removal from
#: `NOT_EXERCISED` fails rather than quietly shrinking what the paper has to disclose.
UNREACHED = {"code_validation", "temporal_decay", "graph_hops",
             "self_retirement", "consolidation"}

REPORT = AB.build(save=False)
CORPUS = MC.load_corpus()


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════ the reference really is the shipped system ════════════════

def test_the_full_variant_reproduces_the_shipped_scorer() -> None:
    """Without this, every delta below is against a system nobody runs."""
    print("\n- the reference row IS anticipate.risk_score -")
    sigs = CORPUS["signatures"]
    idf = A.build_idf(sigs)
    full = AB.scorer()
    drift = []
    for ep in CORPUS["episodes"]:
        traj = A._content_tokens(ep["text"][:A.MAX_CHECK_CHARS])
        for sig in sigs:
            mine, theirs = full(traj, sig, idf), A.risk_score(traj, sig, idf)
            if abs(mine - theirs) > 1e-12:
                drift.append(f"{ep['id']}/{sig['stem']}: {mine} vs {theirs}")
    check("THE REFERENCE: the full variant equals risk_score on every pair", not drift,
          "; ".join(drift[:3]))
    print(f"       ({len(CORPUS['episodes']) * len(sigs)} pairs compared)")

    arm = AB.arm_for(full)
    mismatched = [ep["id"] for ep in CORPUS["episodes"]
                  if arm(ep["text"], sigs) != MC.arm_nevertwice(ep["text"], sigs)]
    check("and the full arm equals F1's arm episode for episode", not mismatched,
          str(mismatched[:4]))


def test_each_variant_removes_exactly_one_thing() -> None:
    print("\n- one mechanism at a time, or it names nothing -")
    for name, spec in AB.VARIANTS.items():
        if name == "full":
            check("the reference removes nothing", spec["kwargs"] == {}, str(spec["kwargs"]))
            continue
        check(f"{name} flips exactly one switch", len(spec["kwargs"]) == 1, str(spec["kwargs"]))
        check(f"{name} switches it OFF", all(v is False for v in spec["kwargs"].values()),
              str(spec["kwargs"]))
        check(f"{name} names the mechanism it removes",
              spec.get("mechanism") and spec["mechanism"] != "-", str(spec.get("mechanism")))
        check(f"{name} says what removing it means in words", len(spec["removes"]) > 40,
              spec["removes"])
    switches = [k for spec in AB.VARIANTS.values() for k in spec["kwargs"]]
    check("no mechanism is ablated twice", len(switches) == len(set(switches)), str(switches))


def test_the_switches_actually_change_the_score() -> None:
    """A switch that changes nothing is an ablation that proves nothing."""
    print("\n- each switch is wired to something -")
    sigs = CORPUS["signatures"]
    idf = A.build_idf(sigs)
    traj = A._content_tokens(CORPUS["episodes"][0]["text"])
    full = AB.scorer()
    for name, spec in AB.VARIANTS.items():
        if name == "full":
            continue
        variant = AB.scorer(**spec["kwargs"])
        differs = any(abs(variant(traj, s, idf) - full(traj, s, idf)) > 1e-12
                      for s in sigs)
        check(f"{name} scores differently from full somewhere", differs,
              "the switch is not wired to anything")


# ═════════════════ what the table is allowed to say ════════════════════

def test_a_delta_inside_the_interval_is_not_called_nothing() -> None:
    print("\n- THE EXIT CRITERION: only a mechanism outside the noise may be named -")
    half = REPORT["recall_half_width_95"]
    check("an interval is computed", half is not None and half > 0, str(half))

    overclaimed, misworded = [], []
    for name, row in REPORT["variants"].items():
        if name == "full":
            continue
        delta = row["delta_recall"]
        if delta is None:
            continue
        if row["verdict"].startswith("load-bearing") and abs(delta) <= half:
            overclaimed.append(f"{name}: delta {delta} <= half-width {half}")
        if abs(delta) <= half and row["verdict"] != "not shown to matter":
            misworded.append(f"{name}: {row['verdict']!r} for a delta of {delta}")
    check("THE EXIT CRITERION: nothing inside the interval is named load-bearing",
          not overclaimed, "; ".join(overclaimed))
    check("and such a delta is worded 'not shown to matter'", not misworded,
          "; ".join(misworded))
    check("no verdict claims a mechanism 'does nothing'",
          not any("does nothing" in r["verdict"] for r in REPORT["variants"].values()),
          "an unresolved delta is not a null result")


def test_losing_the_zero_false_alarm_point_is_its_own_verdict() -> None:
    """Subtracting a missing value would report the strongest result as an ordinary one."""
    print("\n- a variant that can never stop crying wolf -")
    lost = {n: r for n, r in REPORT["variants"].items() if r["zero_false_alarms"] is None}
    for name, row in lost.items():
        check(f"{name}: no numeric delta is invented", row["delta_recall"] is None,
              str(row["delta_recall"]))
        check(f"{name}: it is called load-bearing", row["verdict"].startswith("load-bearing"),
              row["verdict"])
        check(f"{name}: and the verdict says WHY", "zero-false-alarm" in row["verdict"],
              row["verdict"])
    if not lost:
        print("       (no variant lost the operating point in this run)")
    check("the reference itself has a zero-false-alarm point",
          REPORT["variants"]["full"]["zero_false_alarms"] is not None,
          "if the shipped system cannot reach zero false alarms, the whole table is moot")


def test_the_answer_names_mechanisms_not_variant_keys() -> None:
    print("\n- the paper needs a mechanism's name, not a dict key -")
    answer = " ".join(REPORT["answer"])
    for name, row in REPORT["variants"].items():
        if name == "full" or not row["verdict"].startswith("load-bearing"):
            continue
        check(f"{row['mechanism']!r} is named in the answer", row["mechanism"] in answer,
              answer[:160])
        check(f"the variant key {name!r} is NOT what the answer says",
              name not in answer, "a paper cannot cite 'no_coverage' as a mechanism")
    check("the answer states the interval it used", str(REPORT["recall_half_width_95"])
          in answer, answer[-200:])


# ══════════════ what this surface cannot reach ═════════════════════════

def test_the_unreached_mechanisms_are_declared_not_measured() -> None:
    print("\n- five of GOAL's seven do not participate here -")
    declared = set(REPORT["not_exercised"])
    check("every unreached mechanism is listed", UNREACHED <= declared,
          str(sorted(UNREACHED - declared)))
    for name, spec in REPORT["not_exercised"].items():
        check(f"{name}: says why it is inert here", len(spec["why"]) > 40, spec["why"])
        check(f"{name}: names the surface that would reach it",
              len(spec["surface_that_would"]) > 30, spec["surface_that_would"])
    ablated = {v["mechanism"] for v in REPORT["variants"].values()}
    overlap = declared & {a.lower().replace(" ", "_") for a in ablated}
    check("nothing is both ablated and declared unreachable", not overlap, str(overlap))

    answer = " ".join(REPORT["answer"]).lower()
    check("the answer distinguishes unmeasured from measured-null",
          "did not measure it" in answer and "different sentences" in answer,
          "the one confusion that would let the paper claim a null it never ran")


# ══════════════════════ outcome feedback ═══════════════════════════════

def test_outcome_feedback_is_measured_on_this_corpus() -> None:
    """A mechanism over time, so it is measured over time rather than as a score delta."""
    print("\n- the crying-wolf bar, on a trajectory from this corpus -")
    of = REPORT["outcome_feedback"]
    check("it was measured", of.get("measured") is True, str(of.get("why")))
    if not of.get("measured"):
        return
    check("a crying-wolf mode is silenced", of["silenced"] is True, str(of))
    check("it takes more than one false alarm to silence it",
          of["false_alarms_to_silence"] >= 2, str(of["false_alarms_to_silence"]))
    check("the bar rose above the base threshold", of["bar_after"] > A.BASE_TAU,
          f"{of['bar_after']} vs {A.BASE_TAU}")
    check("ANOTHER failure mode is not silenced with it",
          of["other_mode_unaffected"] is True,
          "one mode's record must not raise the bar for an unrelated one")
    check("and a strong signal still breaks through",
          of["strong_signal_still_breaks_through"] is True,
          "a permanently suppressible predictor is a predictor that stops working")


def test_the_corpus_limitation_travels_with_the_answer() -> None:
    print("\n- one corpus, and it says so -")
    check("limitations are carried into the artifact",
          len(REPORT["corpus"]["limitations"]) >= 3,
          str(len(REPORT["corpus"]["limitations"])))
    check("the surface is named", "anticipation" in REPORT["surface"], REPORT["surface"])
    check("it is the same corpus F1 measured",
          REPORT["corpus"]["episodes"] == len(CORPUS["episodes"]),
          "an ablation on a different corpus cannot be read beside F1")


def main() -> int:
    for fn in (test_the_full_variant_reproduces_the_shipped_scorer,
               test_each_variant_removes_exactly_one_thing,
               test_the_switches_actually_change_the_score,
               test_a_delta_inside_the_interval_is_not_called_nothing,
               test_losing_the_zero_false_alarm_point_is_its_own_verdict,
               test_the_answer_names_mechanisms_not_variant_keys,
               test_the_unreached_mechanisms_are_declared_not_measured,
               test_outcome_feedback_is_measured_on_this_corpus,
               test_the_corpus_limitation_travels_with_the_answer):
        fn()
    print(f"\nablations: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
