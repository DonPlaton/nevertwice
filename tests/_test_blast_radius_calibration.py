#!/usr/bin/env python3
"""The calibration contract: the thresholds keep being true, or this goes red.

`research/BLAST_RADIUS_CALIBRATION.md` publishes a verdict - T1 and T2 passed - and
`nevertwice/invariants/blast_radius.py` carries budgets derived from that same run. Both are the
kind of thing that quietly stops being true: someone retunes a constant, someone widens a
default, and the document still says the flag rate is 6.7%.

So this suite re-derives the verdict from the committed artifact rather than trusting the prose,
and pins the three policy decisions the calibration actually bought. It does NOT re-run the
replay - that takes a minute and a half and needs full history - it checks that what the artifact
says still agrees with what the code does and what the documents claim.

Run:  python tests/_test_blast_radius_calibration.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARTIFACT = ROOT / "research" / "blast_radius_calibration.json"
THRESHOLDS = ROOT / "research" / "BLAST_RADIUS_THRESHOLDS.md"
WRITEUP = ROOT / "research" / "BLAST_RADIUS_CALIBRATION.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

# The declared thresholds, restated here as executable constants. They are duplicated from
# BLAST_RADIUS_THRESHOLDS.md on purpose: a threshold that lives only in prose cannot fail.
T1_MAX_FLAG_RATE = 0.20
T2_MAX_BUDGET_OR_PLAN_FLAGS = 0
T3_PERCENTILE = 95
T4_MAX_MEDIAN_SECONDS = 2.00
MIN_COMMITS = 100  # "at least 100 commits", from the task

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _checker():
    path = ROOT / "nevertwice" / "invariants" / "blast_radius.py"
    spec = importlib.util.spec_from_file_location("_nt_br_cal_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ART = json.loads(ARTIFACT.read_text(encoding="utf-8"))
BR = _checker()


def test_the_set_is_what_was_declared() -> None:
    print("\n- the calibration set -")
    shas = ART["shas"]
    check(f"at least {MIN_COMMITS} commits were replayed", len(shas) >= MIN_COMMITS, str(len(shas)))
    check("every sha is a full 40-character hex id",
          all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas))
    check("no commit was replayed twice", len(set(shas)) == len(shas))
    check("the artifact names the thresholds document it is judged against",
          ART["thresholds"] == "research/BLAST_RADIUS_THRESHOLDS.md", ART["thresholds"])
    check("all three arms are present",
          set(ART["arms"]) == {"shipped", "undeclared", "declared"}, str(sorted(ART["arms"])))
    for arm in ART["arms"]:
        rows = ART["arms"][arm]["rows"]
        check(f"{arm}: every replayed commit produced a row", len(rows) == len(shas),
              f"{len(rows)} rows for {len(shas)} commits")


def test_t1_the_flag_rate_is_under_the_declared_ceiling() -> None:
    print("\n- T1: flag rate -")
    s = ART["arms"]["undeclared"]["summary"]
    check(f"flag rate {s['flag_rate']:.1%} is at most {T1_MAX_FLAG_RATE:.0%}",
          s["flag_rate"] <= T1_MAX_FLAG_RATE, f"{s['flag_rate']}")
    flagged = sum(1 for r in ART["arms"]["undeclared"]["rows"] if not r["ok"])
    recomputed = flagged / len(ART["shas"])
    check("the published rate equals the rate the rows imply",
          abs(recomputed - s["flag_rate"]) < 1e-4, f"{recomputed} vs {s['flag_rate']}")
    check("the shipped baseline is worse, or the change bought nothing",
          ART["arms"]["shipped"]["summary"]["flag_rate"] > s["flag_rate"])


def test_t2_what_survives_is_a_dependency_finding() -> None:
    print("\n- T2: composition -")
    s = ART["arms"]["undeclared"]["summary"]
    check("no commit is flagged only by a budget or plan complaint",
          s["commits_flagged_only_by_budget_or_plan"] <= T2_MAX_BUDGET_OR_PLAN_FLAGS,
          str(s["commits_flagged_only_by_budget_or_plan"]))
    kinds = set(s["problem_kinds"])
    check("every problem kind that fired is a dependency finding",
          kinds <= {"dependency"}, str(sorted(kinds)))
    check("the flagged commits are exactly the ones with a dependency finding",
          s["flagged"] == s["commits_with_a_dependency_finding"],
          f"{s['flagged']} flagged, {s['commits_with_a_dependency_finding']} with findings")
    check("the change cost no dependency findings",
          s["commits_with_a_dependency_finding"]
          >= ART["arms"]["shipped"]["summary"]["commits_with_a_dependency_finding"])


def test_t3_the_budgets_in_the_module_are_the_ones_the_data_chose() -> None:
    print("\n- T3: budgets -")
    proposed = ART["proposed_budgets"]
    for cls in ("L0", "L1"):
        want = tuple(proposed[cls])
        check(f"{cls} budget in the module matches the calibrated value {want}",
              tuple(BR.BUDGETS[cls]) == want, f"module has {tuple(BR.BUDGETS[cls])}")
    check("L2 stays unbounded", tuple(BR.BUDGETS["L2"]) == (None, None, None))
    check("the artifact was produced with the budgets now in the module",
          {k: list(v) for k, v in BR.BUDGETS.items()} == ART["budgets_in_effect"],
          f"{BR.BUDGETS} vs {ART['budgets_in_effect']}")
    check("the shipped guesses are recorded, so the baseline stays reproducible",
          ART["shipped_budgets"]["L0"] == [3, 1, 150]
          and ART["shipped_budgets"]["L1"] == [12, 4, 500])
    check(f"the thresholds document declares the {T3_PERCENTILE}th percentile rule",
          f"{T3_PERCENTILE}th percentile" in THRESHOLDS.read_text(encoding="utf-8"))


def test_t4_cost() -> None:
    print("\n- T4: cost -")
    seconds = ART["arms"]["undeclared"]["summary"]["seconds"]
    check(f"median {seconds['median']} s is at most {T4_MAX_MEDIAN_SECONDS} s",
          seconds["median"] <= T4_MAX_MEDIAN_SECONDS, str(seconds["median"]))
    check("p95 and max are published whichever way they fell",
          "p95" in seconds and "max" in seconds)


def test_the_policy_the_calibration_bought_is_still_in_force() -> None:
    """Three constants carry the whole result. Any of them flipping invalidates the document."""
    print("\n- the policy is still what was measured -")
    check("budgets apply only to a declared scope", BR.BUDGET_SCOPE == "declared", BR.BUDGET_SCOPE)
    check("the plan requirement is opt-in", BR.PLAN_ALWAYS_REQUIRED is False)
    check("the artifact records which scope policy produced it",
          ART["budget_scope"] == BR.BUDGET_SCOPE, ART["budget_scope"])

    before = {f"m{i}.py": "x = 1\n" for i in range(40)}
    after = {f"m{i}.py": "x = 2\n" for i in range(40)}
    verdict = BR.check_sources(before, after, scan={})
    check("a 40-file diff that changes no contract is not flagged", verdict.ok, verdict.render())
    declared = BR.check_sources(before, after, scan={}, declared="L0")
    check("the same diff declared L0 is flagged", not declared.ok)


def test_the_writeup_agrees_with_the_artifact() -> None:
    """The prose is checked against the data, not the other way round."""
    print("\n- the write-up quotes the artifact -")
    text = WRITEUP.read_text(encoding="utf-8")
    undeclared = ART["arms"]["undeclared"]["summary"]
    shipped = ART["arms"]["shipped"]["summary"]
    declared = ART["arms"]["declared"]["summary"]
    for label, value in (
        ("undeclared flag rate", f"{undeclared['flag_rate']:.1%}"),
        ("shipped flag rate", f"{shipped['flag_rate']:.1%}"),
        ("declared flag rate", f"{declared['flag_rate']:.1%}"),
    ):
        check(f"the write-up prints the {label} ({value})", value in text)
    check("the write-up prints the budget-or-plan-only count from the shipped arm",
          str(shipped["commits_flagged_only_by_budget_or_plan"]) in text)
    check("the write-up prints the calibrated budgets",
          all(str(v) in text for v in ART["proposed_budgets"]["L0"]))


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims point at real numbers -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Scoped by the artifact the claim names, not by its id prefix: I4 registered claims under
    # the same prefix that point at the precision census, and this suite is not their judge.
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/blast_radius_calibration.json"]
    check("the load-bearing numbers are registered", len(claims) >= 6, str(len(claims)))
    for claim in claims:
        node = ART
        for part in claim["pointer"].split("."):
            node = node[part]
        ok = abs(float(node) - float(claim["value"])) < 1e-4
        check(f"{claim['id']} = {claim['value']} agrees with the artifact", ok,
              f"artifact says {node}")
    check("the dataset every claim names is registered",
          all(c["dataset"] in manifest["datasets"] for c in claims))


#: The E4 seam extraction: four names moved to store_state.py behind a compatibility facade
#: that preserved every caller. It is the commit that motivated I2, and the first calibration
#: reported 42 untouched references to a function nobody had to touch.
E4_SEAM = "4e1f5fb67829de386f65624d943c7a9a806a4c98"
FACADE_SYMBOLS = ("write_atomic", "_load_json_generations", "_save_json_generations")


def test_the_seam_extraction_is_clean() -> None:
    """I2's exit criterion, on the real commit rather than a fixture."""
    print("\n- I2: the compatibility facade is understood -")
    rows = {r["sha"]: r for r in ART["arms"]["undeclared"]["rows"]}
    row = rows.get(E4_SEAM)
    check("the seam extraction is in the calibration set", row is not None, E4_SEAM[:9])
    if row is None:
        return
    check("it produces no problems at all", row["ok"] and not row["problems"],
          "; ".join(row["problems"]))
    for symbol in FACADE_SYMBOLS:
        named = [p for p in row["problems"] if p.startswith(symbol + ":")]
        check(f"no problem names {symbol}", not named, "; ".join(named))
    check("the facade is explained rather than silently dropped", row["notes"] >= len(FACADE_SYMBOLS),
          f"{row['notes']} notes")


def test_facades_did_not_silence_everything() -> None:
    """A facade layer that made every finding disappear would pass the test above and be useless."""
    print("\n- the facade layer is not a mute button -")
    summary = ART["arms"]["undeclared"]["summary"]
    check("dependency findings still survive on real commits",
          summary["commits_with_a_dependency_finding"] > 0,
          str(summary["commits_with_a_dependency_finding"]))
    check("the shipped arm still reports the same findings the calibrated arm does",
          ART["arms"]["shipped"]["summary"]["commits_with_a_dependency_finding"]
          == summary["commits_with_a_dependency_finding"])


def main() -> int:
    for fn in (test_the_set_is_what_was_declared,
               test_t1_the_flag_rate_is_under_the_declared_ceiling,
               test_t2_what_survives_is_a_dependency_finding,
               test_t3_the_budgets_in_the_module_are_the_ones_the_data_chose,
               test_t4_cost,
               test_the_policy_the_calibration_bought_is_still_in_force,
               test_the_writeup_agrees_with_the_artifact,
               test_the_claims_resolve_into_the_artifact,
               test_the_seam_extraction_is_clean,
               test_facades_did_not_silence_everything):
        fn()
    print(f"\nblast-radius calibration: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
