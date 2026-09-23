#!/usr/bin/env python3
"""`code_sessions_eval.py`'s `contexts` cache key includes everything that changes what a
`contexts` entry actually holds - `--seed-pairs` (the collision an earlier commit on this branch
flagged and left unfixed: a seeded and an unseeded run of the same corpus used to share ONE
`nevertwice_full` cache file, so the second run silently served the first run's ranking), the
corpus's own content (not just its self-declared "name" field - two different files can share
one), and the recall top-k. Competitor arms (naive, mem0_infer) never read our engine and are
unaffected by seed_pairs/top-k, so their cache path stays untouched by either.

    python tests/research/_test_code_sessions_ctx_cache_key.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
import code_sessions_eval as cse  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _reset(corpus_name="test-corpus", corpus_sha="0" * 32, seed_pairs=0) -> None:
    cse.CORPUS_NAME = corpus_name
    cse.CORPUS_SHA = corpus_sha
    cse.SEED_PAIRS_KEY = seed_pairs


def test_seeded_and_unseeded_runs_never_share_a_file() -> None:
    print("\n- the exact collision flagged earlier: seed_pairs now changes the cache path -")
    _reset(seed_pairs=0)
    unseeded = cse._ctx_path("nevertwice_full")
    _reset(seed_pairs=2)
    seeded = cse._ctx_path("nevertwice_full")
    check("different seed_pairs -> different cache files", unseeded != seeded,
          f"{unseeded.name} vs {seeded.name}")
    check("the seeded path names its seed count", "seed2" in seeded.name, seeded.name)
    check("the unseeded path names no seed suffix at all", "seed" not in unseeded.name,
          unseeded.name)


def test_two_different_seed_counts_also_differ() -> None:
    print("\n- seed_pairs=2 and seed_pairs=3 are also distinct files -")
    _reset(seed_pairs=2)
    p2 = cse._ctx_path("nevertwice_full")
    _reset(seed_pairs=3)
    p3 = cse._ctx_path("nevertwice_full")
    check("distinct", p2 != p3, f"{p2.name} vs {p3.name}")


def test_a_different_corpus_content_never_shares_a_file_even_with_the_same_name() -> None:
    print("\n- two corpus files sharing a 'name' field but different content get different "
          "cache files (the corpus PATH concern) -")
    _reset(corpus_name="same-name", corpus_sha="aaaa" * 8, seed_pairs=0)
    p_a = cse._ctx_path("nevertwice_full")
    _reset(corpus_name="same-name", corpus_sha="bbbb" * 8, seed_pairs=0)
    p_b = cse._ctx_path("nevertwice_full")
    check("same declared name, different content -> different cache files", p_a != p_b,
          f"{p_a.name} vs {p_b.name}")


def test_recall_top_k_is_named_and_folded_into_the_key() -> None:
    print("\n- RECALL_K is a named constant, used at the recall call site AND in the cache key -")
    check("RECALL_K exists and is the historical default", cse.RECALL_K == 10, str(cse.RECALL_K))
    _reset(seed_pairs=0)
    check("the cache path names the k value", f"_k{cse.RECALL_K}" in cse._ctx_path("nevertwice_full").name,
          cse._ctx_path("nevertwice_full").name)
    saved = cse.RECALL_K
    try:
        cse.RECALL_K = 7
        p_k7 = cse._ctx_path("nevertwice_full")
    finally:
        cse.RECALL_K = saved
    p_k10 = cse._ctx_path("nevertwice_full")
    check("a different RECALL_K value changes the cache path too", p_k7 != p_k10,
          f"{p_k7.name} vs {p_k10.name}")


def test_competitor_arms_are_not_affected_by_seed_pairs_or_k() -> None:
    print("\n- naive/mem0_infer never read our engine - their cache path ignores seed_pairs/k -")
    for arm in ("naive", "mem0_infer"):
        check(f"{arm} is not an engine arm", arm not in cse.ENGINE_ARMS, str(cse.ENGINE_ARMS))
        _reset(corpus_name="c", corpus_sha="0" * 32, seed_pairs=0)
        p0 = cse._ctx_path(arm)
        _reset(corpus_name="c", corpus_sha="0" * 32, seed_pairs=5)
        p5 = cse._ctx_path(arm)
        check(f"{arm}: seed_pairs does not change its cache path", p0 == p5,
              f"{p0.name} vs {p5.name}")
        check(f"{arm}: no 'seed' or '_k' in its filename at all", "seed" not in p0.name
              and f"_k{cse.RECALL_K}" not in p0.name, p0.name)


def test_mutation_dropping_the_seed_suffix_reintroduces_the_collision() -> None:
    """A monkeypatch of `_ctx_path` back to the OLD, collision-prone form (corpus name and arm
    only), restored in `finally` - proves the fix is what keeps a seeded and unseeded run apart."""
    print("\n- mutation: the old (corpus, arm)-only cache path collides seeded with unseeded -")
    saved = cse._ctx_path

    def old_ctx_path(arm: str):
        return cse.DATA / f"codesess_{cse.CORPUS_NAME}_contexts_{arm}_cache.json"
    cse._ctx_path = old_ctx_path
    try:
        _reset(seed_pairs=0)
        unseeded = cse._ctx_path("nevertwice_full")
        _reset(seed_pairs=2)
        seeded = cse._ctx_path("nevertwice_full")
    finally:
        cse._ctx_path = saved
    check("mutation: WITHOUT the fix, seeded and unseeded collide on one file (would FAIL "
          "'different seed_pairs -> different cache files' above)", unseeded == seeded,
          f"{unseeded.name} vs {seeded.name}")
    # sanity: the real, unmutated _ctx_path still keeps them apart on the same globals.
    _reset(seed_pairs=0)
    unseeded2 = cse._ctx_path("nevertwice_full")
    _reset(seed_pairs=2)
    seeded2 = cse._ctx_path("nevertwice_full")
    check("and the unmutated function still keeps them apart", unseeded2 != seeded2,
          f"{unseeded2.name} vs {seeded2.name}")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_seeded_and_unseeded_runs_never_share_a_file,
               test_two_different_seed_counts_also_differ,
               test_a_different_corpus_content_never_shares_a_file_even_with_the_same_name,
               test_recall_top_k_is_named_and_folded_into_the_key,
               test_competitor_arms_are_not_affected_by_seed_pairs_or_k,
               test_mutation_dropping_the_seed_suffix_reintroduces_the_collision):
        fn()
    print(f"\ncode sessions contexts cache key: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
