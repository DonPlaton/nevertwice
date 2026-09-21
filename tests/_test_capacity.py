#!/usr/bin/env python3
"""M5: the capacity curve points down, nothing was promoted, and v1 is not reproducible.

Three things are pinned. That no arm cleared the promotion rule and every checkpoint was deleted;
that the curve past r4 is monotonically worse, which is the argument against the shipped rank;
and that re-running v1's own recipe does not land on v1 - a fact the model card has to carry, and
exactly the kind that gets forgotten between a research note and a release.

Run:  python tests/_test_capacity.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
from _untracked import Gate  # noqa: E402  an untracked artifact is abstained, never failed

ART = ROOT / "research" / "embed_universal" / "heldout" / "capacity_sweep.json"
THRESHOLD = ROOT / "research" / "EMBED_M5_THRESHOLD.md"
DOC = ROOT / "research" / "EMBED_CAPACITY.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

ARMS = ("r1", "r4", "r16", "r64")

PASSED = 0
FAILED = 0


#: The shipped embedder's weights are gigabytes and deliberately not in git
#: (`research/embed_universal/models/.gitignore`), so the directory exists on the machine that
#: trained them and nowhere else. Asserting on it there and FAILING everywhere else would claim
#: the property was tested and does not hold, when it was not testable; skipping would report
#: green for having done nothing. The abstention is loud and counted instead - see _untracked.py.
_MODELS_GATE = Gate("the trained embedder",
                    ROOT / "research" / "embed_universal" / "models" / "universal_v1_merged",
                    "gigabytes of weights, ignored by "
                    "research/embed_universal/models/.gitignore")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


DATA = json.loads(ART.read_text(encoding="utf-8")) if ART.is_file() else {}


def _sit5(arm: str) -> float:
    return DATA["arms"][arm]["retrieval_situation"]["recall@5"]["value"]


def test_nothing_was_promoted_and_nothing_was_kept() -> None:
    print("\n- the promotion rule -")
    check("all four arms were run", set(DATA.get("arms", {})) == set(ARMS),
          str(sorted(DATA.get("arms", {}))))
    check("no arm cleared S1", not DATA.get("promoted"), str(DATA.get("promoted")))
    for arm in ARMS:
        block = DATA["arms"][arm]
        d5 = block["paired_vs_shipped"]["retrieval_situation"]["delta_recall@5"]
        check(f"{arm}'s interval still contains zero or is negative",
              not (d5["low"] is not None and d5["low"] > 0),
              f"{d5['value']} [{d5['low']}, {d5['high']}] - it now CLEARS S1")
        check(f"{arm}'s checkpoint is recorded as deleted",
              block["checkpoint"] == "deleted", block["checkpoint"])
    models = ROOT / "research" / "embed_universal" / "models"
    check("the sweep left no checkpoints on disk", not (models / "_sweep").exists())
    if _MODELS_GATE.present:
        check("the shipped model is untouched", _MODELS_GATE.path.is_dir())
    else:
        _MODELS_GATE.abstain("the shipped model is untouched")


def test_the_curve_points_down() -> None:
    """The argument against the shipped rank. If it inverts, the write-up is wrong."""
    print("\n- more capacity buys less -")
    check("r4 is the best arm", _sit5("r4") == max(_sit5(a) for a in ARMS),
          str({a: _sit5(a) for a in ARMS}))
    check("r16 is worse than r4", _sit5("r16") < _sit5("r4"))
    check("r64 is worse than r16", _sit5("r64") < _sit5("r16"))
    params = {a: DATA["arms"][a]["trainable_parameters"] for a in ARMS}
    check("parameters really do scale with the rank",
          params["r64"] > params["r16"] > params["r4"] > params["r1"], str(params))
    check("r64 has about 64x r1's trainable parameters",
          60 <= params["r64"] / params["r1"] <= 68, str(params["r64"] / params["r1"]))


def test_v1_does_not_reproduce_from_its_own_recipe() -> None:
    """r16 IS v1's recipe. It does not land on v1, and the model card has to say so."""
    print("\n- the reproducibility finding -")
    d5 = DATA["arms"]["r16"]["paired_vs_shipped"]["retrieval_situation"]["delta_recall@5"]
    check("re-running v1's recipe does not reproduce v1", abs(d5["value"]) > 0.001,
          f"{d5['value']} - it now reproduces, so the write-up must be revisited")
    check("the difference is nonetheless inside the noise",
          d5["low"] is None or d5["low"] <= 0 <= (d5["high"] or 0)
          or abs(d5["value"]) < 0.05, f"{d5['value']} [{d5['low']}, {d5['high']}]")
    check("what was held constant is recorded",
          "everything but the rank" in DATA.get("held_constant", ""),
          DATA.get("held_constant", "")[:60])


def test_the_saturated_axis_is_still_saturated_everywhere() -> None:
    print("\n- the twin axis cannot rank models at any capacity -")
    aucs = {a: DATA["arms"][a]["twin"]["auc"]["value"] for a in ARMS}
    aucs["shipped"] = DATA["shipped_baseline"]["twin"]["auc"]["value"]
    check("every arm is at a perfect AUC", min(aucs.values()) >= 0.99, str(aucs))
    check("so the belief S2 replaced could never have been resolved there",
          max(aucs.values()) - min(aucs.values()) < 0.01, str(aucs))


def test_both_languages_are_reported_for_every_arm() -> None:
    print("\n- the bilingual check -")
    for arm in ARMS:
        langs = DATA["arms"][arm]["by_language"]["retrieval_situation"]
        check(f"{arm} reports both languages", {"en", "ru"} <= set(langs), str(sorted(langs)))
    shipped = DATA["shipped_baseline"]["by_language"]["retrieval_situation"]
    check("Russian is not behind English on the shipped model",
          shipped["ru"]["recall@5"] >= shipped["en"]["recall@5"],
          f"ru {shipped['ru']['recall@5']} vs en {shipped['en']['recall@5']}")
    check("the smaller language's sample size is published", shipped["ru"]["n"] > 0)


def test_the_unrunnable_half_is_declared_not_hidden() -> None:
    print("\n- the half that could not run -")
    note = DATA.get("cross_lingual_note", "")
    check("the artifact says why the cross-lingual arm could not run",
          "same-language twins only" in note, note[:70])
    check("it refuses to offer the per-language table as a substitute",
          "NOT a substitute" in note or "not a substitute" in note.lower())
    threshold = " ".join(THRESHOLD.read_text(encoding="utf-8").replace("*", "").split())
    check("the threshold document said so BEFORE the run",
          "Not runnable from committed data" in threshold)


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/capacity_sweep.json"]
    check("both results are registered", len(claims) >= 2, str(len(claims)))
    for claim in claims:
        node = DATA
        for part in claim["pointer"].split("."):
            node = node[part]
        check(f"{claim['id']} agrees with the artifact",
              abs(float(node) - float(claim["value"])) < 1e-4, f"artifact says {node}")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_nothing_was_promoted_and_nothing_was_kept,
               test_the_curve_points_down,
               test_v1_does_not_reproduce_from_its_own_recipe,
               test_the_saturated_axis_is_still_saturated_everywhere,
               test_both_languages_are_reported_for_every_arm,
               test_the_unrunnable_half_is_declared_not_hidden,
               test_the_claims_resolve_into_the_artifact):
        fn()
    print(f"\ncapacity sweep: {PASSED} passed, {FAILED} failed"
          f"{_MODELS_GATE.summary}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
