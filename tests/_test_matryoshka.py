#!/usr/bin/env python3
"""M4: the truncation table is the deliverable, and the mechanism that produced it was not.

The load-bearing result is that the SHIPPED model already truncates - a quarter of the index for
six points of recall@5, with no retraining. That is a product claim the model card will quote, so
it is pinned here along with the finding that Matryoshka training added nothing measurable over
it, and with the boundary the gate was missed by, which is not rounded across.

Run:  python tests/_test_matryoshka.py
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
ART = H / "matryoshka_v1.json"
TRAINING = H / "matryoshka_v1_training.json"
THRESHOLD = ROOT / "research" / "EMBED_M4_THRESHOLD.md"
DOC = ROOT / "research" / "EMBED_MATRYOSHKA.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

K1_MAX_REGRESSION = -0.02
K2_TOLERANCE = {"512": -0.03, "256": -0.06}

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


def test_the_shipped_model_still_truncates() -> None:
    """The deliverable. If this stops being true the model card is wrong."""
    print("\n- the shipped model truncates -")
    base = DATA["comparisons"]["K3_shipped_truncated_vs_own_full"]
    for width, bound in (("512", -0.05), ("256", -0.10)):
        d5 = base[width]["retrieval_situation"]["delta_recall@5"]
        check(f"{width}d still costs no more than {abs(bound):.2f} of situation recall@5",
              d5["value"] >= bound, f"{d5['value']} [{d5['low']}, {d5['high']}]")
    title = base["256"]["retrieval_title"]["delta_recall@1"]
    check("the easy axis barely moves at 256d", title["value"] >= -0.02, str(title["value"]))
    widths = DATA["models"]["nevertwice-embed"]["widths"]
    check("the twin axis does not move at any width",
          all(widths[w]["twin"]["auc"]["value"] >= 0.99 for w in ("1024", "512", "256")),
          str({w: widths[w]["twin"]["auc"]["value"] for w in widths}))


def test_the_index_saving_is_arithmetic_not_a_claim() -> None:
    print("\n- the saving -")
    widths = DATA["models"]["nevertwice-embed"]["widths"]
    full = widths["1024"]["index_bytes"]
    check("512d halves the index", abs(widths["512"]["index_bytes"] * 2 - full) <= 1,
          f"{widths['512']['index_bytes']} vs {full}")
    check("256d quarters it", abs(widths["256"]["index_bytes"] * 4 - full) <= 1,
          f"{widths['256']['index_bytes']} vs {full}")
    check("the dtype the bytes assume is recorded", DATA.get("bytes_per_dim") in (2, 4, 8),
          str(DATA.get("bytes_per_dim")))
    check("truncation is renormalised, not a raw prefix",
          "renormalise" in DATA.get("truncation", ""), DATA.get("truncation"))


def test_the_mechanism_added_nothing() -> None:
    """K3 is why this is known. A mechanism with no baseline cannot be found useless."""
    print("\n- Matryoshka vs naive truncation -")
    for width in ("512", "256"):
        trained = DATA["comparisons"]["K2_truncated_vs_own_full"][width]["retrieval_situation"]["delta_recall@5"]["value"]
        naive = DATA["comparisons"]["K3_shipped_truncated_vs_own_full"][width]["retrieval_situation"]["delta_recall@5"]["value"]
        check(f"at {width}d the trained model is no more graceful than naive truncation",
              trained <= naive + 0.01, f"trained {trained} vs naive {naive}")


def test_the_gates_are_reported_as_they_fell() -> None:
    print("\n- the gates, including the one missed by a hair -")
    k1 = DATA["comparisons"]["K1_full_vs_shipped"]
    check("K1 held on the title axis",
          k1["retrieval_title"]["delta_recall@1"]["value"] >= K1_MAX_REGRESSION,
          str(k1["retrieval_title"]["delta_recall@1"]["value"]))
    check("K1 held on the situation axis",
          k1["retrieval_situation"]["delta_recall@5"]["value"] >= K1_MAX_REGRESSION,
          str(k1["retrieval_situation"]["delta_recall@5"]["value"]))
    k2 = DATA["comparisons"]["K2_truncated_vs_own_full"]
    at512 = k2["512"]["retrieval_situation"]["delta_recall@5"]["value"]
    at256 = k2["256"]["retrieval_situation"]["delta_recall@5"]["value"]
    check("K2 held at 512d", at512 >= K2_TOLERANCE["512"], str(at512))
    check("K2 is still missed at 256d, and the write-up still says so",
          at256 < K2_TOLERANCE["256"],
          f"{at256} now clears {K2_TOLERANCE['256']} - the write-up's 'fail' must be revisited")
    text = " ".join(DOC.read_text(encoding="utf-8").replace("*", "").split())
    check("the write-up refuses to round across the line", "not being rounded across" in text)


def test_only_the_wrapper_changed() -> None:
    print("\n- one variable -")
    check("the training record says what changed",
          "Matryoshka wrapper" in TRAIN.get("changed_from_v1", ""))
    check("it did NOT reuse M2's failed triplets",
          "mined triplets" in TRAIN.get("changed_from_v1", ""))
    check("the recipe is v1's", TRAIN.get("seed") == 11 and TRAIN.get("epochs") == 3
          and TRAIN.get("lora", {}).get("r") == 16)
    check("all three widths were trained for",
          TRAIN.get("widths") == [1024, 512, 256], str(TRAIN.get("widths")))


def test_the_checkpoint_was_deleted() -> None:
    print("\n- the deletion was executed -")
    models = ROOT / "research" / "embed_universal" / "models"
    for name in ("matryoshka_v1", "matryoshka_v1_merged"):
        check(f"{name} is gone", not (models / name).exists())
    check("the shipped model is still there", (models / "universal_v1_merged").is_dir())
    check("the measurements are kept", ART.is_file() and TRAINING.is_file())


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/matryoshka_v1.json"]
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
    for fn in (test_the_shipped_model_still_truncates,
               test_the_index_saving_is_arithmetic_not_a_claim,
               test_the_mechanism_added_nothing,
               test_the_gates_are_reported_as_they_fell,
               test_only_the_wrapper_changed,
               test_the_checkpoint_was_deleted,
               test_the_claims_resolve_into_the_artifact):
        fn()
    print(f"\nmatryoshka: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
