#!/usr/bin/env python3
"""M3's negative result, and the guard that predicted it, stay true.

Two gates failed and a checkpoint was deleted for it. What is pinned here is the decision and the
mechanism behind it: that the regression is measured rather than merely unresolved, that the
teacher really is worse than the student where the damage landed, that the supervision was set up
the way the write-up claims, and that the first attempt's failure is recorded as the
specification error it was rather than quietly dropped.

Run:  python tests/_test_distillation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

H = ROOT / "research" / "embed_universal" / "heldout"
ART = H / "distil_v1.json"
TRAINING = H / "distillation_v1.json"
BASELINE = H / "baseline_v1.json"
THRESHOLD = ROOT / "research" / "EMBED_M3_THRESHOLD.md"
DOC = ROOT / "research" / "EMBED_DISTILLATION.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

D2_MAX_REGRESSION = -0.02

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


DATA = json.loads(ART.read_text(encoding="utf-8")) if ART.is_file() else {}
TRAIN = json.loads(TRAINING.read_text(encoding="utf-8")) if TRAINING.is_file() else {}
BASE = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else {}


def test_both_gates_still_fail() -> None:
    print("\n- the gates -")
    sit = DATA["paired_vs_shipped"]["nevertwice-embed-distil"]["retrieval_situation"]
    d5 = sit["delta_recall@5"]
    check("D1 is still not cleared", not (d5["low"] is not None and d5["low"] > 0),
          f"{d5['value']} [{d5['low']}, {d5['high']}] - it now CLEARS D1")
    check("the regression is measured, not merely unresolved",
          d5["high"] is not None and d5["high"] < 0,
          f"{d5['value']} [{d5['low']}, {d5['high']}]")
    title = DATA["paired_vs_shipped"]["nevertwice-embed-distil"]["retrieval_title"]["delta_recall@1"]
    check("D2 is still breached on the title axis",
          title["value"] < D2_MAX_REGRESSION, str(title["value"]))
    check("that breach is itself outside the noise",
          title["high"] is not None and title["high"] < 0,
          f"{title['value']} [{title['low']}, {title['high']}]")


def test_the_mechanism_the_guard_named_is_real() -> None:
    """D2's second clause was written because the teacher is worse at recall@1. Check it still is."""
    print("\n- the teacher really is worse where the damage landed -")
    teacher = BASE["paired_vs_stock"]["bge-reranker-v2-m3"]["retrieval_situation"]
    check("the teacher gains nothing at recall@1",
          teacher["delta_recall@1"]["value"] <= 0, str(teacher["delta_recall@1"]["value"]))
    check("the teacher's advantage is at recall@5",
          teacher["delta_recall@5"]["low"] > 0, str(teacher["delta_recall@5"]))
    check("the student's damage is largest on a top-1 metric",
          abs(DATA["paired_vs_shipped"]["nevertwice-embed-distil"]["retrieval_title"]["delta_recall@1"]["value"])
          > abs(DATA["paired_vs_shipped"]["nevertwice-embed-distil"]["retrieval_title"]["delta_recall@5"]["value"]))


def test_the_supervision_was_what_the_writeup_says() -> None:
    print("\n- the supervision -")
    check("the teacher was read as logits, not saturated probabilities",
          "logits" in TRAIN.get("teacher_activation", ""), TRAIN.get("teacher_activation"))
    raw = TRAIN.get("raw_margin_quantiles", {})
    check("the raw margins really do exceed what a cosine margin can express",
          raw.get("max", 0) - raw.get("min", 0) > 4.0, str(raw))
    scaled = TRAIN.get("margin_quantiles", {})
    check("the scaled target lands inside [-2, 2] for the bulk of triples",
          abs(scaled.get("p25", 9)) <= 2 and abs(scaled.get("p75", 9)) <= 2, str(scaled))
    check("the divisor is derived from the data, not chosen",
          TRAIN.get("scale_rule") == "iqr" and TRAIN.get("margin_divisor", 0) > 0,
          f"{TRAIN.get('scale_rule')} {TRAIN.get('margin_divisor')}")
    check("the queries were situation-shaped and drawn from training lessons only",
          TRAIN.get("situation_queries", 0) > 500
          and TRAIN.get("situation_queries", 0) <= TRAIN.get("training_lessons", 0),
          f"{TRAIN.get('situation_queries')} of {TRAIN.get('training_lessons')}")
    check("the teacher disagreed with the labels often enough to be informative",
          0 < TRAIN.get("negative_margins", 0) < TRAIN.get("triples", 1),
          f"{TRAIN.get('negative_margins')} of {TRAIN.get('triples')}")


def _prose(path: Path) -> str:
    """Markdown flattened for matching: line wraps and emphasis are formatting, not content.

    The first versions of the two checks below failed on a line break inside the phrase they
    were looking for, which says nothing about the documents.
    """
    body = path.read_text(encoding="utf-8").replace("*", "").replace("`", "")
    return " ".join(body.split())


def test_the_first_attempt_is_recorded_as_an_error_not_as_evidence() -> None:
    print("\n- the failed first attempt is published -")
    text = _prose(DOC)
    check("the write-up says the first run was a specification error",
          "specification error" in text)
    check("it publishes both runs' numbers side by side", "first run" in text and "corrected" in text)
    check("the scale note lives in the artifact too, not only in prose",
          "cannot leave" in TRAIN.get("margin_scale_note", "")
          or "cosine margin" in TRAIN.get("margin_scale_note", ""),
          TRAIN.get("margin_scale_note", "")[:60])


def test_the_thresholds_were_written_first() -> None:
    print("\n- the ordering -")
    text = _prose(THRESHOLD)
    check("it states it was written before any teacher score",
          "before any teacher score was collected" in text)
    check("it carries the guard the run then breached",
          "worse at recall@1 can plausibly drag top-1 precision down" in text)
    check("it carries a deletion decision", "deleted" in text.lower())


def test_the_checkpoint_was_actually_deleted() -> None:
    print("\n- the deletion was executed -")
    models = ROOT / "research" / "embed_universal" / "models"
    for name in ("distil_v1", "distil_v1_merged"):
        check(f"{name} is gone", not (models / name).exists())
    check("the shipped model is still there", (models / "universal_v1_merged").is_dir())
    check("every measurement it produced is kept", ART.is_file() and TRAINING.is_file())


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/distil_v1.json"]
    check("both gates are registered", len(claims) >= 2, str(len(claims)))
    for claim in claims:
        node = DATA
        for part in claim["pointer"].split("."):
            node = node[part]
        check(f"{claim['id']} agrees with the artifact",
              abs(float(node) - float(claim["value"])) < 1e-4, f"artifact says {node}")


def main() -> int:
    for fn in (test_both_gates_still_fail,
               test_the_mechanism_the_guard_named_is_real,
               test_the_supervision_was_what_the_writeup_says,
               test_the_first_attempt_is_recorded_as_an_error_not_as_evidence,
               test_the_thresholds_were_written_first,
               test_the_checkpoint_was_actually_deleted,
               test_the_claims_resolve_into_the_artifact):
        fn()
    print(f"\ndistillation: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
