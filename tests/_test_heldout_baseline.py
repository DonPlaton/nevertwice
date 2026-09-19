#!/usr/bin/env python3
"""The M1 baseline table says what the artifact says, and the artifact is internally consistent.

The table's headline is a *negative*: the shipped fine-tune's advantage over stock bge-m3 does
not survive a paired test on external material. That is exactly the kind of conclusion that gets
quietly softened later, so it is pinned here - not the number, the CONCLUSION. If a rerun makes
the interval exclude zero, this suite fails and the document has to be rewritten rather than
drifting.

It also recomputes the metrics from the published per-query ranks. A table whose cells cannot be
re-derived from the raw rows it ships beside is a table nobody can check.

Run:  python tests/_test_heldout_baseline.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARTIFACT = ROOT / "research" / "embed_universal" / "heldout" / "baseline_v1.json"
BENCH_MANIFEST = ROOT / "research" / "embed_universal" / "heldout" / "MANIFEST.json"
DOC = ROOT / "research" / "EMBED_HELDOUT_BASELINE.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

MODELS = ("stock bge-m3", "nevertwice-embed", "bge-reranker-v2-m3")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


ART = json.loads(ARTIFACT.read_text(encoding="utf-8")) if ARTIFACT.is_file() else {}


def test_the_table_was_measured_on_the_frozen_benchmark() -> None:
    print("\n- the benchmark it was measured on -")
    check("the artifact is committed", bool(ART))
    bench = json.loads(BENCH_MANIFEST.read_text(encoding="utf-8"))
    check("it records the benchmark hash it ran against",
          ART.get("benchmark_sha256") == bench["sha256"],
          f"{ART.get('benchmark_sha256', '')[:12]} vs {bench['sha256'][:12]}")
    check("all three models are present", set(ART.get("models", {})) == set(MODELS),
          str(sorted(ART.get("models", {}))))
    boot = ART.get("bootstrap", {})
    check("the interval method and seed are recorded",
          boot.get("method") == "percentile" and boot.get("seed") and boot.get("resamples", 0) >= 1000,
          str(boot))


def test_every_cell_can_be_recomputed_from_the_published_rows() -> None:
    """A table that cannot be re-derived from its own raw data is a table nobody can check."""
    print("\n- the cells follow from the rows -")
    for label in MODELS:
        for axis in ("retrieval_title", "retrieval_situation"):
            ranks = [r["rank"] for r in ART["per_query"][label][axis]]
            block = ART["models"][label][axis]
            check(f"{label}/{axis}: n matches the published rows",
                  block["n"] == len(ranks), f"{block['n']} vs {len(ranks)}")
            for k, hit in (("recall@1", lambda r: r == 1), ("recall@5", lambda r: 1 <= r <= 5)):
                recomputed = sum(1 for r in ranks if hit(r)) / len(ranks)
                check(f"{label}/{axis}: {k} recomputes",
                      abs(recomputed - block[k]["value"]) < 1e-3,
                      f"{recomputed:.4f} vs {block[k]['value']}")


def test_every_interval_contains_its_point_estimate() -> None:
    print("\n- the intervals are sane -")
    bad = []
    for label in MODELS:
        for axis, keys in (("twin", ("auc", "recall@1%fpr", "recall@0fp")),
                           ("retrieval_title", ("recall@1", "recall@5", "mrr@10")),
                           ("retrieval_situation", ("recall@1", "recall@5", "mrr@10"))):
            for k in keys:
                b = ART["models"][label][axis][k]
                if b["low"] is not None and not (b["low"] <= b["value"] <= b["high"]):
                    bad.append(f"{label}/{axis}/{k}")
    check("every interval brackets its point estimate", not bad, "; ".join(bad))


def test_the_negative_conclusion_still_holds() -> None:
    """The headline is that the fine-tune's advantage does NOT resolve. Pin the conclusion."""
    print("\n- the paired result the write-up rests on -")
    paired = ART["paired_vs_stock"]["nevertwice-embed"]["retrieval_situation"]
    d1 = paired["delta_recall@1"]
    check("the paired recall@1 interval still contains zero",
          d1["low"] <= 0 <= d1["high"],
          f"{d1['value']} [{d1['low']}, {d1['high']}] - the write-up says it cannot be resolved")
    d5 = paired["delta_recall@5"]
    check("the paired recall@5 interval still contains zero",
          d5["low"] <= 0 <= d5["high"], f"{d5['value']} [{d5['low']}, {d5['high']}]")
    check("McNemar's discordant counts are published",
          paired["mcnemar"]["discordant"] == paired["mcnemar"]["reference_only"]
          + paired["mcnemar"]["challenger_only"])

    reranker = ART["paired_vs_stock"]["bge-reranker-v2-m3"]["retrieval_situation"]
    r5 = reranker["delta_recall@5"]
    check("the cross-encoder's recall@5 gain still excludes zero",
          r5["low"] > 0, f"{r5['value']} [{r5['low']}, {r5['high']}]")


def test_the_saturated_axis_is_still_saturated() -> None:
    """The write-up says the twin axis cannot rank models. If that changes, so must the text."""
    print("\n- the twin axis -")
    aucs = {label: ART["models"][label]["twin"]["auc"]["value"] for label in MODELS}
    check("every model is at or near a perfect AUC", min(aucs.values()) >= 0.99, str(aucs))
    check("no model separates from another on it",
          max(aucs.values()) - min(aucs.values()) < 0.01, str(aucs))


def test_the_reranker_cap_is_published() -> None:
    """Its recall is bounded by the shortlist it reranks; the bound has to be visible."""
    print("\n- the cascade's ceiling -")
    depth = ART.get("rerank_depth")
    check("the rerank depth is recorded", isinstance(depth, int) and depth > 0, str(depth))
    key = f"recall@{depth}"
    for axis in ("retrieval_title", "retrieval_situation"):
        cap = ART["models"]["stock bge-m3"][axis].get(key)
        check(f"the stock retriever's {key} is published for {axis}",
              isinstance(cap, (int, float)), str(cap))
        got = ART["models"]["bge-reranker-v2-m3"][axis]["recall@5"]["value"]
        check(f"the reranker does not exceed its own shortlist on {axis}",
              cap is None or got <= cap + 1e-9, f"{got} > {cap}")


def test_the_claims_resolve_into_the_artifact() -> None:
    print("\n- the registered claims -")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = [c for c in manifest["claims"]
              if c["raw"] == "research/embed_universal/heldout/baseline_v1.json"]
    check("the load-bearing numbers are registered", len(claims) >= 3, str(len(claims)))
    for claim in claims:
        node = ART
        for part in claim["pointer"].split("."):
            node = node[part]
        check(f"{claim['id']} agrees with the artifact",
              abs(float(node) - float(claim["value"])) < 1e-4, f"artifact says {node}")
        check(f"{claim['id']} names a registered dataset",
              claim["dataset"] in manifest["datasets"], claim["dataset"])
        check(f"{claim['id']} names a registered environment",
              claim["environment"] in manifest["environments"], claim["environment"])


def test_the_writeup_still_says_what_the_artifact_says() -> None:
    print("\n- the prose quotes the data -")
    text = DOC.read_text(encoding="utf-8")
    sit = ART["models"]
    for label, value in ((l, sit[l]["retrieval_situation"]["recall@1"]["value"]) for l in MODELS):
        check(f"the write-up prints {label}'s situation recall@1 ({value:.3f})",
              f"{value:.3f}" in text)
    check("it labels the private-vault number unreproducible",
          "unreproducible" in text.lower() or "cannot be" in text.lower())
    check("it says the twin axis cannot discriminate",
          "cannot discriminate" in text or "saturated" in text.lower())


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_table_was_measured_on_the_frozen_benchmark,
               test_every_cell_can_be_recomputed_from_the_published_rows,
               test_every_interval_contains_its_point_estimate,
               test_the_negative_conclusion_still_holds,
               test_the_saturated_axis_is_still_saturated,
               test_the_reranker_cap_is_published,
               test_the_claims_resolve_into_the_artifact,
               test_the_writeup_still_says_what_the_artifact_says):
        fn()
    print(f"\nheld-out baseline: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
