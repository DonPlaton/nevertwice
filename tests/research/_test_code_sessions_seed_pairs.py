#!/usr/bin/env python3
"""A9 (PLAN-Q3Q5.md, `research/code_sessions_eval.py --seed-pairs`): the pure/LLM-free helpers
behind the flag. `contexts_nevertwice_full` always calls the real extractor for the corpus's own
sessions, so this suite cannot run it end to end without Ollama (forbidden while a measurement
campaign uses the GPU). It tests what IS LLM-free: `seed_ride_pairs` (api.remember only - no
extraction call at all), the token-delta bookkeeping, and the CLI wiring, read from source.

    python tests/research/_test_code_sessions_seed_pairs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
import code_sessions_eval as cse  # noqa: E402
import memory_hook as m  # noqa: E402
from nevertwice import api  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_llm_stats_snapshot_and_delta() -> None:
    print("\n- _llm_stats_snapshot/_llm_stats_delta read _LLM_STATS purely -")
    saved = dict(m._LLM_STATS)
    try:
        m._LLM_STATS.clear()
        check("a snapshot before any tokens are recorded is (0, 0)",
              cse._llm_stats_snapshot(m) == (0, 0), str(cse._llm_stats_snapshot(m)))
        before = cse._llm_stats_snapshot(m)
        m._LLM_STATS["prompt_tokens"] = 88
        m._LLM_STATS["eval_tokens"] = 12
        after = cse._llm_stats_snapshot(m)
        check("the delta matches what was added",
              cse._llm_stats_delta(before, after) == {"prompt_tokens": 88, "eval_tokens": 12},
              str(cse._llm_stats_delta(before, after)))
    finally:
        m._LLM_STATS.clear()
        m._LLM_STATS.update(saved)


def test_seed_ride_pairs_writes_real_contested_pairs_with_no_llm_call() -> None:
    print("\n- seed_ride_pairs writes real contested pairs through api.remember, no extractor call -")
    saved_gj = m.generate_json
    m.generate_json = lambda *a, **k: (_ for _ in ()).throw(   # noqa: E731
        AssertionError("seed_ride_pairs must never reach the extractor"))
    try:
        n = cse.seed_ride_pairs(api, "seedp1", 2)
    finally:
        m.generate_json = saved_gj
    check("both pairs were seeded (no LLM call raised)", n == 2, str(n))

    contested, _ = m._iter_contested_both("seedp1")
    check("two earlier notes are stamped contested", len(contested) == 2, str(len(contested)))
    check("each carries exactly one sibling stem",
          all(len(row["new_stems"]) == 1 for row in contested), str(contested))
    titles = {row["title"] for row in contested}
    check("the two seeded titles are distinct", titles == {"seed contested pair 0", "seed contested pair 1"},
          str(titles))

    # every seeded note is a live '-2' sibling pair - both statements on disk, neither absorbed.
    live_decisions = list((m.VAULT / "Decisions").glob("*-seedp1-decision-*.md"))
    check("4 live notes on disk (2 pairs x 2 statements, none absorbed into the other)",
          len(live_decisions) == 4, str(len(live_decisions)))


def test_seed_ride_pairs_counts_only_pairs_where_both_sides_wrote() -> None:
    """A monkeypatch of `api.remember` that refuses every SECOND call, restored in `finally` -
    proves the count reflects BOTH sides writing, not just the attempt."""
    print("\n- mutation: a refused second write is not counted as a seeded pair -")
    saved_remember = api.remember
    calls = {"n": 0}

    def half_refusing(*a, **k):
        calls["n"] += 1
        return None if calls["n"] % 2 == 0 else saved_remember(*a, **k)
    api.remember = half_refusing
    try:
        n = cse.seed_ride_pairs(api, "seedp2", 3)
    finally:
        api.remember = saved_remember
    check("mutation: every pair had its second write refused, so 0 pairs are counted seeded "
          "(would FAIL 'both pairs were seeded' above if this counting were broken)", n == 0,
          str(n))
    # sanity: the unmutated api.remember seeds normally on the same call shape.
    n2 = cse.seed_ride_pairs(api, "seedp2b", 3)
    check("sanity: unmutated, all 3 pairs seed normally", n2 == 3, str(n2))


def test_cli_flag_is_wired_to_the_nevertwice_full_arm_only() -> None:
    """Read from source, not run: `--seed-pairs` must reach `contexts_nevertwice_full` specifically
    (the only arm with a real store to seed) and not silently apply to naive/mem0_infer."""
    print("\n- --seed-pairs is wired into the CLI and the nevertwice_full arm call only -")
    src = Path(cse.__file__).read_text(encoding="utf-8")
    check("the flag is declared", '"--seed-pairs"' in src)
    check("it reaches contexts_nevertwice_full's call site",
          "seed_pairs=args.seed_pairs" in src)
    check("gated to the nevertwice_full arm",
          'if args.arm == "nevertwice_full" else fn(corpus)' in src)
    check("contexts_nevertwice_full's own signature accepts it",
          "def contexts_nevertwice_full(corpus: dict, seed_pairs: int = 0)" in src)
    check("contexts_naive/contexts_mem0_infer signatures are untouched (corpus only)",
          "def contexts_naive(corpus: dict)" in src and "def contexts_mem0_infer(corpus: dict)" in src)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_llm_stats_snapshot_and_delta,
               test_seed_ride_pairs_writes_real_contested_pairs_with_no_llm_call,
               test_seed_ride_pairs_counts_only_pairs_where_both_sides_wrote,
               test_cli_flag_is_wired_to_the_nevertwice_full_arm_only):
        fn()
    print(f"\ncode sessions seed-pairs (pure/LLM-free helpers): {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
