#!/usr/bin/env python3
"""M2's negative result says what its artifacts say, and the gate it failed stays failed.

The conclusion is that hard-negative mining did not clear the threshold declared for it, and the
mined checkpoint was deleted for it. That is the kind of decision that gets quietly revisited, so
what is pinned here is the DECISION and its evidence: the gate, the one improvement that did
happen, the filter statistics that show the mining was real work rather than a pass-through, and
the fact that the thresholds were written before the run.

Run:  python tests/_test_hard_negatives.py
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

H = ROOT / "research" / "embed_universal" / "heldout"
ART = H / "hard_v1.json"
MINING = H / "mining_stats.json"
TRAINING = H / "training_hard_v1.json"
THRESHOLD = ROOT / "research" / "EMBED_M2_THRESHOLD.md"
DOC = ROOT / "research" / "EMBED_HARD_NEGATIVES.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: Restated from EMBED_M2_THRESHOLD.md. A threshold that lives only in prose cannot fail.
N1_REQUIRES_INTERVAL_ABOVE_ZERO = True
N2_MAX_TITLE_REGRESSION = -0.02

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
MINE = json.loads(MINING.read_text(encoding="utf-8")) if MINING.is_file() else {}
TRAIN = json.loads(TRAINING.read_text(encoding="utf-8")) if TRAINING.is_file() else {}


def test_the_gate_is_judged_against_the_shipped_model() -> None:
    print("\n- the comparison the threshold names -")
    check("the artifact pairs against the shipped model, not only against stock",
          "paired_vs_shipped" in DATA)
    check("the mined model is in the table",
          "nevertwice-embed-hard" in DATA.get("models", {}))
    check("the shipped model is in the same table, so the pairing is real",
          "nevertwice-embed" in DATA.get("models", {}))
    check("it was measured on the frozen benchmark",
          DATA.get("benchmark_sha256") == json.loads(
              (H / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"])


def test_n1_still_fails() -> None:
    """The whole decision rests on this. If a rerun clears it, the document is wrong."""
    print("\n- N1: situation recall@5 against the shipped model -")
    block = DATA["paired_vs_shipped"]["nevertwice-embed-hard"]["retrieval_situation"]
    d5 = block["delta_recall@5"]
    cleared = d5["low"] is not None and d5["low"] > 0
    check("the gate is still not cleared", not cleared,
          f"{d5['value']} [{d5['low']}, {d5['high']}] - it now CLEARS N1, so the write-up and "
          f"the deletion decision must be revisited")
    check("the point estimate is still not an improvement", d5["value"] <= 0, str(d5["value"]))
    check("McNemar's counts are published",
          block["mcnemar"]["discordant"] == block["mcnemar"]["reference_only"]
          + block["mcnemar"]["challenger_only"])


def test_n2_passed_and_is_recorded_as_such() -> None:
    print("\n- N2: the guard against collateral damage -")
    d1 = DATA["paired_vs_shipped"]["nevertwice-embed-hard"]["retrieval_title"]["delta_recall@1"]
    check("no regression beyond the declared bound on the easy axis",
          d1["value"] >= N2_MAX_TITLE_REGRESSION, str(d1["value"]))
    check("the one improvement's interval still excludes zero",
          d1["low"] is not None and d1["low"] > 0,
          f"{d1['value']} [{d1['low']}, {d1['high']}]")


def test_the_mining_did_real_work() -> None:
    """A filter that passes everything through, or rejects everything, explains nothing."""
    print("\n- the false-negative filter -")
    check("triplets were produced", MINE.get("triplets", 0) > 1000, str(MINE.get("triplets")))
    rate = MINE.get("rejection_rate", 0)
    check("the filter rejected some candidates but not all", 0.01 < rate < 0.5, str(rate))
    kept = MINE.get("kept_score_quantiles", {})
    rejected = MINE.get("rejected_score_quantiles", {})
    check("rejected candidates score clearly above kept ones",
          rejected.get("median", 0) > kept.get("median", 1),
          f"{rejected.get('median')} vs {kept.get('median')}")
    check("true positives score above both",
          MINE.get("true_score_quantiles", {}).get("median", 0) > rejected.get("median", 1))
    check("the filter rule is recorded in words, not only as a number",
          "decision boundary" in MINE.get("filter_rule", ""))
    check("almost every anchor kept a usable negative",
          MINE.get("anchors_with_no_survivor", 10**9) < 0.05 * MINE.get("pairs", 1),
          str(MINE.get("anchors_with_no_survivor")))


def test_only_one_thing_changed_from_v1() -> None:
    """A second change would make the result unattributable to mining."""
    print("\n- one variable -")
    check("the training record says what changed",
          "training example" in TRAIN.get("changed_from_v1", ""))
    check("the recipe is v1's", TRAIN.get("seed") == 11 and TRAIN.get("epochs") == 3
          and TRAIN.get("lr") == 1e-4 and TRAIN.get("lora", {}).get("r") == 16,
          json.dumps({k: TRAIN.get(k) for k in ("seed", "epochs", "lr", "lora")}))
    check("the loss is unchanged", TRAIN.get("loss") == "MultipleNegativesRankingLoss")


def test_the_hardware_decision_was_measured_not_assumed() -> None:
    print("\n- the VRAM margin the threshold document required -")
    before = TRAIN.get("vram_before", {})
    check("free VRAM was measured before the run", before.get("free_gb", 0) > 0, str(before))
    check("it cleared the declared margin",
          before.get("free_gb", 0) >= TRAIN.get("vram_margin_required_gb", 10**9),
          f"{before.get('free_gb')} vs {TRAIN.get('vram_margin_required_gb')}")
    check("the peak allocation is recorded", TRAIN.get("peak_allocated_gb", 0) > 0)
    # The record carries the decision under an `ollama` key; the property is that a REASON
    # is recorded there, not that the word appears inside its own value.
    check("the Ollama decision is written down rather than implied",
          len(TRAIN.get("ollama", "")) > 40, repr(TRAIN.get("ollama"))[:80])


def test_the_thresholds_were_written_first() -> None:
    print("\n- the ordering -")
    text = THRESHOLD.read_text(encoding="utf-8")
    check("the threshold document exists", bool(text))
    check("it states it was written before the run",
          "before any negative was mined" in text)
    check("it names the axis the gate may live on", "retrieval_situation" in text)
    check("it carries a deletion decision", "deleted" in text.lower())
    check("it states what the run can resolve, before the result",
          "and no smaller" in text)


def test_the_mined_checkpoint_was_actually_deleted() -> None:
    """The decision said delete. A decision nobody executed is a decision nobody made."""
    print("\n- the deletion was executed -")
    models = ROOT / "research" / "embed_universal" / "models"
    for name in ("hard_v1", "hard_v1_merged"):
        check(f"{name} is gone", not (models / name).exists())
    if _MODELS_GATE.present:
        check("the shipped model is still there", _MODELS_GATE.path.is_dir())
    else:
        _MODELS_GATE.abstain("the shipped model is still there")
    check("every measurement it produced is kept",
          ART.is_file() and MINING.is_file() and TRAINING.is_file())


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/hard_v1.json"]
    check("both load-bearing numbers are registered", len(claims) >= 2, str(len(claims)))
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
    for fn in (test_the_gate_is_judged_against_the_shipped_model,
               test_n1_still_fails,
               test_n2_passed_and_is_recorded_as_such,
               test_the_mining_did_real_work,
               test_only_one_thing_changed_from_v1,
               test_the_hardware_decision_was_measured_not_assumed,
               test_the_thresholds_were_written_first,
               test_the_mined_checkpoint_was_actually_deleted,
               test_the_claims_resolve_into_the_artifact):
        fn()
    print(f"\nhard negatives: {PASSED} passed, {FAILED} failed"
          f"{_MODELS_GATE.summary}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
