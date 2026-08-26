#!/usr/bin/env python3
"""The matched-condition harness, checked (GOAL F1).

F1's exit criterion is a single sentence: *no result can be produced by a looser firing
threshold.* That is a property of the harness, not of the numbers it happens to print, and it
decomposes into four things that have to be true at once:

* the harness measures the **shipped** path - if `arm_nevertwice` disagrees with what
  `anticipate()` actually does, every number is about a system nobody runs;
* the **whole curve** is published, so the operating point can be located on it rather than
  taken on trust;
* every cross-arm comparison happens at a **matched** false-alarm rate, and an arm that cannot
  reach that rate is reported as unreachable rather than quietly dropped;
* the **corpus cannot be trivially satisfied** - an episode that reuses its own note's wording
  measures string matching and reports it as anticipation.

The last one is the easiest to get wrong and the hardest to notice, so it is checked
numerically: every positive episode is required to share only a minority of its content tokens
with the signature it is labelled against.
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
import anticipate as A            # noqa: E402
import matched_conditions as MC   # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


CORPUS = MC.load_corpus()
REPORT = MC.build(save=False)


# ═══════════════════ it measures the shipped system ════════════════════

def test_the_harness_measures_the_shipped_path() -> None:
    """A harness that scores something other than the product measures nothing."""
    print("\n- the arm under test IS anticipate() -")
    sigs = CORPUS["signatures"]
    disagreements = []
    for ep in CORPUS["episodes"]:
        stem, score = MC.arm_nevertwice(ep["text"], sigs)
        hits = A.anticipate(ep["text"], sigs=sigs, state={}, k=1)
        fired_here = stem is not None and score >= A.BASE_TAU
        if bool(hits) != fired_here:
            disagreements.append(f"{ep['id']}: harness fired={fired_here} anticipate={bool(hits)}")
        elif hits and hits[0]["stem"] != stem:
            disagreements.append(f"{ep['id']}: harness {stem} vs anticipate {hits[0]['stem']}")
    check("the harness agrees with anticipate() on every episode", not disagreements,
          "; ".join(disagreements[:3]))
    check("and it agrees at the SHIPPED threshold, not a convenient one",
          abs(REPORT["shipped_threshold"] - A.BASE_TAU) < 1e-9,
          f"{REPORT['shipped_threshold']} vs {A.BASE_TAU}")


# ═════════════════════ the whole curve is published ════════════════════

def test_the_full_curve_is_published() -> None:
    print("\n- the curve, not a point -")
    for name, arm in REPORT["arms"].items():
        curve = arm["curve"]
        check(f"{name}: every sampled threshold is present",
              len(curve) == len(MC.GRID), f"{len(curve)} of {len(MC.GRID)}")
        thresholds = [r["threshold"] for r in curve]
        check(f"{name}: the sweep spans silence to firing at everything",
              thresholds[0] == 0.0 and thresholds[-1] == 1.0, f"{thresholds[0]}..{thresholds[-1]}")

    curve = REPORT["arms"]["nevertwice"]["curve"]
    recalls = [r["recall"] for r in curve if r["recall"] is not None]
    check("recall is non-increasing as the bar rises",
          all(a >= b - 1e-9 for a, b in zip(recalls, recalls[1:])),
          "a raised threshold that INCREASES recall means the sweep is wrong")
    fprs = [r["false_positive_rate"] for r in curve if r["false_positive_rate"] is not None]
    check("and so is the false-alarm rate",
          all(a >= b - 1e-9 for a, b in zip(fprs, fprs[1:])), str(fprs[:6]))

    op = REPORT["operating_point"]
    check("the shipped operating point is a point ON the published curve",
          op is not None and op in curve,
          "an operating point not on the curve is a number with no context")


def test_the_degenerate_arms_behave_degenerately() -> None:
    """If 'always fire' and 'never fire' do not look like themselves, the scoring is broken."""
    print("\n- the two arms whose answers are known in advance -")
    always = REPORT["arms"]["full_history"]["curve"]
    at_zero = next(r for r in always if r["threshold"] == 0.0)
    check("firing every time gives a 100% false-alarm rate",
          at_zero["false_positive_rate"] == 1.0, str(at_zero["false_positive_rate"]))
    check("and it is never silent", at_zero["tn"] == 0, str(at_zero))

    never = REPORT["arms"]["no_memory"]["curve"]
    check("never firing gives no false alarms at any threshold",
          all(r["false_positive_rate"] == 0.0 for r in never),
          "the floor arm is firing")
    check("and no true positives either",
          all(r["tp"] == 0 for r in never), "the floor arm is scoring")


# ══════════════════════ THE EXIT CRITERION ═════════════════════════════

def test_no_arm_is_read_at_a_looser_rate_than_its_rivals() -> None:
    """F1's exit criterion, as an executable statement."""
    print("\n- THE EXIT CRITERION: nothing here comes from firing more often -")
    target = REPORT["matched_conditions"]["false_positive_rate"]
    check("a matched false-alarm rate is declared", target is not None, str(target))

    violations = []
    for name, entry in REPORT["matched_fpr_comparison"].items():
        point = entry["point"]
        if point is None:
            continue
        if point["false_positive_rate"] > target + 1e-9:
            violations.append(f"{name}: read at {point['false_positive_rate']} > {target}")
    check("THE EXIT CRITERION: no arm is read above the matched rate", not violations,
          "; ".join(violations))

    check("an arm that cannot reach the rate is reported, not dropped",
          set(REPORT["matched_fpr_comparison"]) == set(MC.ARMS),
          f"{sorted(REPORT['matched_fpr_comparison'])} vs {sorted(MC.ARMS)}")
    check("and 'fire every time' is exactly such an arm",
          REPORT["matched_fpr_comparison"]["full_history"]["reachable"] is False,
          "an arm with a 100% false-alarm rate must not be comparable at a 25% one")


def test_the_matched_point_is_the_best_admissible_one() -> None:
    """Picking the *nearest* point instead of the best would understate a rival."""
    print("\n- matched means best-within-budget, not nearest -")
    target = REPORT["matched_conditions"]["false_positive_rate"]
    for name, entry in REPORT["matched_fpr_comparison"].items():
        if not entry["reachable"]:
            continue
        curve = REPORT["arms"][name]["curve"]
        best = max((r["recall"] or 0.0) for r in curve
                   if r["false_positive_rate"] is not None
                   and r["false_positive_rate"] <= target + 1e-9)
        check(f"{name}: the reported point is its best within the budget",
              abs((entry["point"]["recall"] or 0.0) - best) < 1e-9,
              f"reported {entry['point']['recall']}, best admissible {best}")


def test_a_win_bought_with_tokens_or_latency_is_visible() -> None:
    print("\n- the conditions that are not the variable are measured -")
    mc = REPORT["matched_conditions"]
    check("the reader is stated", "none" in mc["reader_model"].lower())
    check("the token budget is stated", "token" in mc["context_token_budget"])
    check("the latency tier is stated", "cpu" in mc["latency_tier"])
    for name, entry in REPORT["matched_fpr_comparison"].items():
        check(f"{name}: latency per episode is reported",
              entry["latency_ms_per_episode"] is not None)
        if entry["reachable"]:
            check(f"{name}: token cost per episode is reported",
                  entry["tokens_per_episode"] is not None)
    silent = next(r for r in REPORT["arms"]["nevertwice"]["curve"] if r["threshold"] == 1.0)
    check("staying silent costs zero context tokens", silent["tokens_spent"] == 0,
          str(silent["tokens_spent"]))


# ═══════════════════════ the corpus is not rigged ══════════════════════

def test_the_corpus_has_both_classes_and_resolvable_labels() -> None:
    print("\n- the corpus -")
    sigs = {s["stem"] for s in CORPUS["signatures"]}
    episodes = CORPUS["episodes"]
    positives = [e for e in episodes if e["label"]]
    negatives = [e for e in episodes if not e["label"]]
    check("there are positives", len(positives) >= 20, str(len(positives)))
    check("there are negatives", len(negatives) >= 20, str(len(negatives)))
    check("a corpus of only positives cannot measure a false-alarm rate",
          len(negatives) >= len(positives) // 2, f"{len(positives)}/{len(negatives)}")
    unresolved = [e["id"] for e in positives if e["label"] not in sigs]
    check("every label names a real signature", not unresolved, str(unresolved[:4]))
    ids = [e["id"] for e in episodes]
    check("episode ids are unique", len(ids) == len(set(ids)),
          str([i for i in ids if ids.count(i) > 1][:4]))
    check("more than one signature is exercised",
          len({e["label"] for e in positives}) >= 8,
          str(len({e['label'] for e in positives})))


def test_no_episode_quotes_its_own_note() -> None:
    """The check that keeps this from being a string-matching benchmark in disguise."""
    print("\n- an episode that quotes its note measures nothing -")
    by_stem = {s["stem"]: s for s in CORPUS["signatures"]}
    too_close = []
    for ep in CORPUS["episodes"]:
        if not ep["label"]:
            continue
        ep_toks = set(A._content_tokens(ep["text"]))
        sig_toks = set(by_stem[ep["label"]]["tokens"])
        if not ep_toks:
            continue
        overlap = len(ep_toks & sig_toks) / len(ep_toks)
        if overlap > 0.5:
            too_close.append(f"{ep['id']}: {overlap:.0%} of its tokens are the note's")
    check("no positive episode shares a majority of its tokens with its note",
          not too_close, "; ".join(too_close[:3]))

    identical = [e["id"] for e in CORPUS["episodes"]
                 if e["label"] and e["text"].strip() == by_stem[e["label"]]["text"].strip()]
    check("and none is a copy of it", not identical, str(identical))
    check("the corpus states its own limitations",
          len(CORPUS["limitations"]) >= 3, str(len(CORPUS["limitations"])))


def test_the_findings_state_the_negative_result() -> None:
    """A harness that only ever confirms is a harness nobody should trust."""
    print("\n- what the harness found, including what it did not find -")
    findings = " ".join(REPORT["findings"]).lower()
    check("the findings are published with the numbers",
          len(REPORT["findings"]) >= 3, str(len(REPORT["findings"])))
    check("the negative result is stated plainly", "negative result" in findings,
          "the arms tie on this corpus; a findings list that omits it is selective reporting")
    check("it names what would settle the question",
          "f2" in findings and "did not write" in findings,
          "a negative result with no next step is an excuse")
    check("the unreachable arm is explained rather than omitted",
          "full_history" in findings or "firing every time" in findings)


def main() -> int:
    for fn in (test_the_harness_measures_the_shipped_path,
               test_the_full_curve_is_published,
               test_the_degenerate_arms_behave_degenerately,
               test_no_arm_is_read_at_a_looser_rate_than_its_rivals,
               test_the_matched_point_is_the_best_admissible_one,
               test_a_win_bought_with_tokens_or_latency_is_visible,
               test_the_corpus_has_both_classes_and_resolvable_labels,
               test_no_episode_quotes_its_own_note,
               test_the_findings_state_the_negative_result):
        fn()
    print(f"\nmatched conditions: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
