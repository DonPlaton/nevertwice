#!/usr/bin/env python3
"""M6: the served artifact is the measured artifact, and the model card depends on it.

Every number in M1-M5 was measured on a safetensors checkpoint; every user gets an f16 GGUF
through Ollama. This suite pins the check that lets those numbers be quoted as the shipped
model's - if the agreement ever breaks, the model card is making a claim about a file nobody
runs, and that is exactly the failure this directory's review notes already record once.

Run:  python tests/_test_serving.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ART = ROOT / "research" / "embed_universal" / "heldout" / "serving_check.json"
THRESHOLD = ROOT / "research" / "EMBED_M6_THRESHOLD.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


DATA = json.loads(ART.read_text(encoding="utf-8")) if ART.is_file() else {}


def test_the_two_paths_still_agree() -> None:
    print("\n- V1: the metrics -")
    check("V1 is recorded as passing", DATA.get("V1_pass") is True)
    tol = DATA.get("v1_tolerance", 0.02)
    for axis, key in (("retrieval_situation", "delta_recall@5"),
                      ("retrieval_title", "delta_recall@1")):
        d = DATA["comparison_served_vs_local"][axis][key]
        check(f"{axis} {key} is within +/-{tol} in BOTH directions",
              abs(d["value"]) <= tol, f"{d['value']} [{d['low']}, {d['high']}]")


def test_the_vectors_themselves_still_agree() -> None:
    """A metric can agree while the vectors do not. This asks the sharper question."""
    print("\n- V2: the vectors -")
    va = DATA["vector_agreement"]
    check("V2 is recorded as passing", DATA.get("V2_pass") is True)
    check("the median cosine is at least the declared floor",
          va["median"] >= DATA.get("v2_median_cosine", 0.99), str(va["median"]))
    check("no vector falls below 0.99", va["below_0.99"] == 0, str(va["below_0.99"]))
    check("the worst single vector is still close", va["min"] >= 0.99, str(va["min"]))
    check("the comparison covered the whole frozen set", va["n"] > 2000, str(va["n"]))


def test_the_control_separates_packaging_from_quantisation() -> None:
    """Without it, agreement could be luck rather than a property of the pipeline."""
    print("\n- the stock control -")
    gap = DATA.get("stock_serving_gap")
    check("a stock control was run", isinstance(gap, dict), str(gap))
    if isinstance(gap, dict):
        check("stock shows the same agreement", gap["median_cosine"] >= 0.99,
              str(gap["median_cosine"]))


def test_the_latency_the_product_actually_pays_is_published() -> None:
    print("\n- the served cost -")
    s = DATA["seconds"]
    check("both paths were timed", s.get("local", 0) > 0 and s.get("served", 0) > 0, str(s))
    #: The direction is NOT asserted any more (restore #2, 2026-09-25): which path is faster is a
    #: timing, and a timing from a machine that was not idle is not a fact (PREREG-V2 P5; the
    #: latency claim on this artifact stays pending as machine-not-idle). The campaign's P2 re-run
    #: had served 29.78 s against local 29.91 s; flipping the expectation to match that one draw
    #: would be the same mistake in the other direction. What the stand gates is V1/V2 above.
    print(f"  (timing recorded, direction not asserted - machine-not-idle, P5: {s})")
    check("the text count is recorded so the per-text figure is derivable", s.get("texts", 0) > 0)


def test_nothing_was_started_stopped_or_pulled() -> None:
    """The owner's Ollama is live tooling; the task's courtesy is part of its contract."""
    print("\n- courtesy -")
    check("the artifact records that the run was read-only",
          "read-only" in DATA.get("courtesy", ""), DATA.get("courtesy", "")[:60])
    text = " ".join(THRESHOLD.read_text(encoding="utf-8").replace("*", "").split())
    check("the threshold document promised that before the run",
          "starts nothing, stops nothing, and pulls nothing" in text)
    check("it also promised what a V1 failure would cost",
          "republished with a stated caveat" in text)


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/serving_check.json"]
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
    for fn in (test_the_two_paths_still_agree,
               test_the_vectors_themselves_still_agree,
               test_the_control_separates_packaging_from_quantisation,
               test_the_latency_the_product_actually_pays_is_published,
               test_nothing_was_started_stopped_or_pulled,
               test_the_claims_resolve_into_the_artifact):
        fn()
    print(f"\nserving check: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
