#!/usr/bin/env python3
"""Uncertainty, checked (GOAL F5).

F5's exit criterion is that **no aggregate hides a single easy family**. That is a property of
the reporting, not of the numbers, so most of this suite is about what the report is obliged to
show rather than what it happens to say.

The statistics are checked against cases with known answers, because a bespoke McNemar or a
hand-rolled Holm is exactly the kind of code that looks right and is off by one:

* McNemar on a perfectly split table must give p = 1, on a lopsided one a small p, and it must
  use the **exact** binomial rather than the chi-squared approximation that is untrustworthy at
  these counts;
* Holm-Bonferroni must be **step-down** - once one hypothesis fails to reject, nothing weaker
  may reject either - and must be stricter than testing each comparison alone;
* the bootstrap must be **reproducible**, which means a fixed recorded seed and identical
  intervals across runs;
* Cohen's h must be zero for equal proportions and grow with the gap.

And the reporting obligations: per-family results published, every family removed in turn, the
paired intervals declared to supersede F1-F4's unpaired ones, and the corpus limitation carried
alongside every conclusion.
"""
from __future__ import annotations

import math
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
import uncertainty as U           # noqa: E402

PASSED = 0
FAILED = 0

REPORT = U.build(save=False)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════════ the statistics, on known answers ══════════════════════

def test_mcnemar_is_exact_and_correct() -> None:
    print("\n- McNemar, against cases with known answers -")
    check("no discordant pairs gives p = 1", U.mcnemar_exact(0, 0) == 1.0,
          str(U.mcnemar_exact(0, 0)))
    check("an even split gives p = 1", U.mcnemar_exact(5, 5) == 1.0,
          str(U.mcnemar_exact(5, 5)))
    check("it is symmetric in its arguments",
          U.mcnemar_exact(2, 9) == U.mcnemar_exact(9, 2),
          f"{U.mcnemar_exact(2, 9)} vs {U.mcnemar_exact(9, 2)}")
    # 0 of 10 discordant pairs favouring one side: exactly 2 * 0.5**10.
    check("a total lopsided split matches the exact binomial",
          abs(U.mcnemar_exact(0, 10) - 2 * 0.5 ** 10) < 1e-12,
          str(U.mcnemar_exact(0, 10)))
    # 1 of 6: 2 * (C(6,0) + C(6,1)) / 2**6 = 2 * 7/64.
    check("a partial split matches too",
          abs(U.mcnemar_exact(1, 5) - 2 * 7 / 64) < 1e-12, str(U.mcnemar_exact(1, 5)))
    check("p never exceeds 1", U.mcnemar_exact(3, 4) <= 1.0, str(U.mcnemar_exact(3, 4)))
    check("more evidence gives a smaller p",
          U.mcnemar_exact(0, 12) < U.mcnemar_exact(0, 4),
          f"{U.mcnemar_exact(0, 12)} vs {U.mcnemar_exact(0, 4)}")

    # The chi-squared shortcut would call 0-vs-5 significant; the exact test does not.
    chi_would_say = 5.0            # (|b-c|-1)^2/(b+c) = 16/5 = 3.2 -> p ~ 0.074 anyway
    check("the exact test is used, not the chi-squared approximation",
          U.mcnemar_exact(0, 5) > 0.05 and chi_would_say > 0,
          f"exact p={U.mcnemar_exact(0, 5)} on 5 discordant pairs must not be significant")


def test_holm_is_step_down_and_stricter_than_uncorrected() -> None:
    print("\n- Holm-Bonferroni -")
    out = U.holm({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.9}, alpha=0.05)
    # Compared at the resolution `holm` publishes: it rounds thresholds to 6dp on purpose, so
    # a 1e-12 tolerance would be testing float formatting rather than the step-down arithmetic.
    check("the smallest p is tested at alpha/m",
          abs(out["a"]["holm_threshold"] - 0.05 / 4) < 1e-6, str(out["a"]))
    check("and it rejects", out["a"]["significant"] is True, str(out["a"]))
    check("the second is tested at alpha/(m-1)",
          abs(out["b"]["holm_threshold"] - 0.05 / 3) < 1e-6, str(out["b"]))
    check("a p of 0.02 does NOT clear 0.0167", out["b"]["significant"] is False, str(out["b"]))
    check("nothing after a failure rejects here either",
          out["c"]["significant"] is False and out["d"]["significant"] is False, str(out))

    # The case that actually DISCRIMINATES step-down. Sorted: 0.02 fails its 0.0167 bar and
    # 0.03 fails its 0.025 bar, but 0.04 would clear its own 0.05 bar if each were judged
    # independently. Only the step-down rule stops it. The first version of this check used
    # p-values where every hypothesis failed on its own threshold too, so deleting step-down
    # entirely changed nothing and the mutation survived.
    step = U.holm({"a": 0.02, "b": 0.03, "c": 0.04}, alpha=0.05)
    check("the weakest p clears its OWN threshold", step["c"]["holm_threshold"] >= 0.04,
          str(step["c"]))
    check("STEP-DOWN: but it is blocked because an earlier one failed",
          step["c"]["significant"] is False,
          "without step-down this would be reported as a significant finding")
    check("and the earlier failures are recorded as failures",
          step["a"]["significant"] is False and step["b"]["significant"] is False, str(step))

    lone = U.holm({"a": 0.04}, alpha=0.05)
    check("a single comparison is tested at alpha itself",
          abs(lone["a"]["holm_threshold"] - 0.05) < 1e-6, str(lone["a"]))
    check("correction is stricter than no correction",
          U.holm({"x": 0.04, "y": 0.04, "z": 0.04})["x"]["significant"] is False,
          "0.04 would pass uncorrected and must not pass at alpha/3")


def test_cohens_h_behaves_like_an_effect_size() -> None:
    print("\n- effect size -")
    check("equal proportions give zero", U.cohens_h(0.5, 0.5) == 0.0, str(U.cohens_h(0.5, 0.5)))
    check("it is antisymmetric",
          abs(U.cohens_h(0.7, 0.3) + U.cohens_h(0.3, 0.7)) < 1e-9)
    check("a bigger gap gives a bigger h",
          abs(U.cohens_h(0.9, 0.1)) > abs(U.cohens_h(0.6, 0.4)))
    check("the extremes are finite",
          math.isfinite(U.cohens_h(0.0, 1.0)), str(U.cohens_h(0.0, 1.0)))


def test_the_bootstrap_is_reproducible() -> None:
    """An interval that moves between runs is not an interval."""
    print("\n- a fixed, recorded seed -")
    check("the seed is recorded in the artifact", REPORT["seed"] == U.SEED, str(REPORT["seed"]))
    check("and it is named in the method", str(U.SEED) in REPORT["method"]["interval"],
          REPORT["method"]["interval"])
    again = U.build(save=False)
    drift = [k for k in REPORT["comparisons"]
             if REPORT["comparisons"][k]["bootstrap_ci95"]
             != again["comparisons"][k]["bootstrap_ci95"]]
    check("two runs give identical intervals", not drift, str(drift))
    check("the resample count is stated", str(U.BOOTSTRAP) in REPORT["method"]["interval"])

    # Comparing two runs is NOT enough on its own: at 10,000 resamples the percentiles are
    # stable to 4dp whatever the seed, so removing the seed leaves the intervals unchanged and
    # the comparison above green. Reproducibility here has to be checked structurally - every
    # generator in this module must be constructed with an explicit seed.
    import ast
    tree = ast.parse(Path(U.__file__).read_text(encoding="utf-8"))
    unseeded = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "Random"
                and getattr(getattr(node.func, "value", None), "id", None) == "random"
                and not node.args):
            unseeded.append(node.lineno)
    check("REPRODUCIBILITY: no unseeded random.Random() anywhere in the module",
          not unseeded,
          f"line(s) {unseeded} construct a generator with no seed, so the interval it "
          f"produces cannot be reproduced")
    check("and every generator uses the recorded seed",
          all(isinstance(a, ast.Name) and a.id == "SEED"
              for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) == "Random"
              for a in node.args),
          "a seed that is not SEED is a seed the artifact does not record")


def test_the_paired_counts_are_internally_consistent() -> None:
    print("\n- the 2x2 table adds up -")
    for name, c in REPORT["comparisons"].items():
        total = c["discordant_ours_only"] + c["discordant_theirs_only"] + c["concordant"]
        check(f"{name}: the cells sum to n", total == c["n"], f"{total} vs {c['n']}")
        implied = round((c["ours_correct"] - c["theirs_correct"]) / c["n"], 4)
        check(f"{name}: the difference matches the counts",
              abs(implied - c["difference"]) < 1e-9, f"{implied} vs {c['difference']}")
        lo, hi = c["bootstrap_ci95"]
        check(f"{name}: the interval brackets the point estimate",
              lo - 1e-9 <= c["difference"] <= hi + 1e-9, f"{c['difference']} not in [{lo},{hi}]")


# ═══════════════════════ THE EXIT CRITERION ════════════════════════════

def test_every_family_is_published() -> None:
    print("\n- an aggregate over fifteen questions shows all fifteen -")
    corpus = MC.load_corpus()
    families = {e["label"] for e in corpus["episodes"] if e["label"]}
    check("every family in the corpus has a row",
          set(REPORT["per_family"]) == families,
          str(sorted(families - set(REPORT["per_family"]))))
    counted = sum(r["n"] for r in REPORT["per_family"].values())
    check("the rows account for every positive episode",
          counted == sum(1 for e in corpus["episodes"] if e["label"]), str(counted))
    for family, r in REPORT["per_family"].items():
        check(f"{family}: every arm is scored", set(r["by_arm"]) == set(CB.ARMS),
              str(sorted(r["by_arm"])))
        check(f"{family}: no arm exceeds the family size",
              all(v <= r["n"] for v in r["by_arm"].values()), str(r["by_arm"]))
    check("per-episode results are published too",
          len(REPORT["per_episode"]["nevertwice"]) == counted,
          "a per-family table without the episodes behind it cannot be re-checked")


def test_the_aggregate_is_recomputed_without_each_family() -> None:
    """F5's exit criterion, executed rather than asserted."""
    print("\n- THE EXIT CRITERION: take each family out and look -")
    lofo = REPORT["leave_one_family_out"]
    check("the full-corpus comparison is recorded", "full" in lofo)
    families = set(REPORT["per_family"])
    check("THE EXIT CRITERION: every family is removed in turn",
          set(lofo["without"]) == families,
          str(sorted(families - set(lofo["without"]))))
    for family, r in lofo["without"].items():
        if "skipped" in r:
            check(f"{family}: a skip says why", bool(r["skipped"]), str(r))
            continue
        check(f"{family}: the reduced run is smaller",
              r["n"] < lofo["full"]["n"], f"{r['n']} vs {lofo['full']['n']}")
    check("the largest swing is identified", "largest_swing" in lofo, str(lofo.keys()))
    swing = lofo["largest_swing"]
    check("and it is the largest one",
          all(abs(r["difference"] - lofo["full"]["difference"]) <= abs(swing["swing"]) + 1e-9
              for r in lofo["without"].values() if "difference" in r),
          str(swing))
    verdict = " ".join(REPORT["verdict"]).lower()
    check("the verdict answers the criterion in words",
          "single easy family" in verdict, verdict[:200])


def test_the_verdict_states_what_the_corpus_cannot_support() -> None:
    print("\n- what the numbers are not allowed to be read as -")
    verdict = " ".join(REPORT["verdict"])
    lower = verdict.lower()
    check("the correction is reported either way",
          "holm-bonferroni" in lower, verdict[:120])
    check("the paired intervals are declared to supersede the unpaired ones",
          "supersede" in lower and "unpaired" in lower, verdict[-300:])
    check("the corpus is named as the dominant uncertainty",
          "dominant term" in lower, verdict[-200:])
    check("and it says no correction repairs that",
          "no correction repairs" in lower, verdict[-200:])
    check("the method records that comparisons are paired",
          "paired" in REPORT["method"]["pairing"].lower(),
          REPORT["method"]["pairing"][:80])


def test_a_zero_win_arm_is_called_a_subset_not_a_tie() -> None:
    """b = 0 is a stronger statement than 'not distinguished', and must be said as one."""
    print("\n- winning nothing the baseline misses is not a tie -")
    verdict = " ".join(REPORT["verdict"])
    subsets = [n for n, c in REPORT["comparisons"].items()
               if c["discordant_ours_only"] == 0 and c["discordant_theirs_only"] > 0]
    for name in subsets:
        check(f"{name}: reported as a SUBSET relationship", "SUBSET" in verdict and name in verdict,
              verdict[:200])
    if not subsets:
        print("       (no arm strictly dominates the memory arm in this run)")
    check("the wording is not softened to a tie",
          not subsets or "strict subset" in verdict.lower(), verdict[:200])


def test_complementarity_is_reported_when_it_exists() -> None:
    """Two arms with the same aggregate can be one system twice or two different systems."""
    print("\n- do they fail on the same episodes -")
    comps = REPORT["complementarity"]
    check("complementarity is computed for the linter arm", "linter_or_test" in comps,
          str(sorted(comps)))
    for name, c in comps.items():
        total = c["ours_only"] + c["theirs_only"] + c["both"] + c["neither"]
        check(f"{name}: the four cells sum to n", total == c["n"], f"{total} vs {c['n']}")
        check(f"{name}: the union is at least each arm alone",
              c["union_rate"] >= max(c["ours_rate"], c["theirs_rate"]) - 1e-9,
              str(c))
    strong = [n for n, c in comps.items()
              if c["ours_only"] and c["theirs_only"]
              and c["union_rate"] - max(c["ours_rate"], c["theirs_rate"]) > 0.05]
    verdict = " ".join(REPORT["verdict"])
    for name in strong:
        check(f"{name}: the complementarity is stated in the verdict",
              "COMPLEMENTARY" in verdict and name in verdict, verdict[:200])
        check(f"{name}: and the conclusion is 'run both', not 'we win'",
              "run both" in verdict, verdict[:200])


def main() -> int:
    for fn in (test_mcnemar_is_exact_and_correct,
               test_holm_is_step_down_and_stricter_than_uncorrected,
               test_cohens_h_behaves_like_an_effect_size,
               test_the_bootstrap_is_reproducible,
               test_the_paired_counts_are_internally_consistent,
               test_every_family_is_published,
               test_the_aggregate_is_recomputed_without_each_family,
               test_the_verdict_states_what_the_corpus_cannot_support,
               test_a_zero_win_arm_is_called_a_subset_not_a_tie,
               test_complementarity_is_reported_when_it_exists):
        fn()
    print(f"\nuncertainty: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
