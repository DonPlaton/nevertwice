#!/usr/bin/env python3
"""F6, the restamp trap: `judge`/`summary`/`--save` can make ZERO model calls on complete
answer/verdict caches, and `tools/remeasure.py` reads only the artifact's own mtime - so
re-running either stage today re-stamps an OLD engine measurement (a stale `contexts` cache) as
if it were made against current code. The guard: `frontier_eval.stamp_engine_commit` records the
commit a `contexts` cache was produced at, and `frontier_eval.check_engine_freshness` refuses
when that commit is not provably still current for the engine's closure
(`tools/produced_by.py`), or missing entirely (a legacy cache from before this check existed).
`code_sessions_eval.py` shares the SAME implementation (`fe.check_engine_freshness`) rather than
an independently-matched copy.

    python tests/research/_test_f6_restamp_guard.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
import frontier_eval as fe  # noqa: E402
import code_sessions_eval as cse  # noqa: E402

PASSED = 0
FAILED = 0

#: A real, old commit on this branch (before ride_block, splice_ride_block, A7, A9 ...) whose
#: nevertwice/ has genuinely moved since - not a monkeypatched git response, a real one.
OLD_COMMIT = "bf01e0d"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _stub_ctx_path(tmp: Path):
    def fn(arm: str) -> Path:
        return tmp / f"{arm}.json"
    return fn


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_git_head_and_engine_closure_are_sane() -> None:
    print("\n- git_head/engine_closure return something real, not placeholders -")
    head = fe.git_head()
    check("HEAD is a 40-char hex sha, not a '?' placeholder", len(head) == 40 and
          all(c in "0123456789abcdef" for c in head), head)
    closure = fe.engine_closure("python research/frontier_eval.py")
    check("the closure is non-trivial", len(closure) > 10, str(len(closure)))
    check("the entry file leads the closure", closure[0] == "research/frontier_eval.py", closure[0])
    check("the engine's own index is in the closure", "nevertwice/_engine.py" in closure, str(closure[:5]))
    check("a cross-cutting research dependency is in the closure too (head_to_head.py)",
          "research/head_to_head.py" in closure)


def test_stamp_engine_commit_records_head() -> None:
    print("\n- stamp_engine_commit records the current HEAD onto a contexts dict -")
    ctx = {"q1": [{"id": "s1", "text": "x"}]}
    fe.stamp_engine_commit(ctx)
    check("_engine_commit was added", ctx.get("_engine_commit") == fe.git_head(), str(ctx))
    check("nothing else in the dict was disturbed", ctx["q1"] == [{"id": "s1", "text": "x"}])


def test_a_cache_recorded_at_head_passes() -> None:
    print("\n- a cached contexts entry recorded at HEAD passes -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp / "nevertwice_whole.json", {"q1": [], "_engine_commit": fe.git_head()})
        problems = fe.check_engine_freshness(["nevertwice_whole"], fe.ENGINE_ARMS,
                                             _stub_ctx_path(tmp), "python research/frontier_eval.py")
        check("no problems reported", problems == [], str(problems))


def test_an_older_recorded_commit_is_refused_by_name() -> None:
    print("\n- a cache recorded at a genuinely older commit (nevertwice/ has since moved) is "
          "refused, naming the rerun command -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp / "nevertwice_snippet.json", {"q1": [], "_engine_commit": OLD_COMMIT})
        problems = fe.check_engine_freshness(["nevertwice_snippet"], fe.ENGINE_ARMS,
                                             _stub_ctx_path(tmp), "python research/frontier_eval.py")
        check("exactly one problem reported", len(problems) == 1, str(problems))
        check("it names the arm", problems and "nevertwice_snippet" in problems[0], str(problems))
        check("it names the exact rerun command",
              problems and "python research/frontier_eval.py contexts --arm nevertwice_snippet"
              in problems[0], str(problems))


def test_a_legacy_cache_with_no_recorded_commit_is_refused() -> None:
    print("\n- a legacy cache (no _engine_commit at all) is refused, not assumed fresh -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp / "nevertwice_full.json", {"q1": [], "_ingest": {"sessions": 3}})  # no commit key
        problems = fe.check_engine_freshness(["nevertwice_full"], fe.ENGINE_ARMS,
                                             _stub_ctx_path(tmp), "python research/frontier_eval.py")
        check("exactly one problem reported", len(problems) == 1, str(problems))
        check("it says 'legacy'", problems and "legacy" in problems[0], str(problems))
        check("it still names the rerun command",
              problems and "contexts --arm nevertwice_full" in problems[0], str(problems))


def test_competitor_arms_are_exempt() -> None:
    print("\n- a competitor arm (mem0) with a stale or absent cache is not flagged - it never "
          "reads our engine -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp / "mem0.json", {"q1": []})   # no _engine_commit - would refuse an ENGINE arm
        problems = fe.check_engine_freshness(["mem0"], fe.ENGINE_ARMS, _stub_ctx_path(tmp),
                                             "python research/frontier_eval.py")
        check("mem0 is exempt - no problems", problems == [], str(problems))
        check("mem0 is not itself an engine arm", "mem0" not in fe.ENGINE_ARMS, str(fe.ENGINE_ARMS))


def test_an_arm_with_no_cache_yet_is_not_this_checks_job() -> None:
    print("\n- an engine arm with NO cache at all yet is left to the stage that reads it -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        problems = fe.check_engine_freshness(["nevertwice_full"], fe.ENGINE_ARMS,
                                             _stub_ctx_path(tmp), "python research/frontier_eval.py")
        check("no problems - nothing cached to be stale", problems == [], str(problems))


def test_code_sessions_eval_shares_the_same_implementation() -> None:
    print("\n- code_sessions_eval.py's ENGINE_ARMS and freshness check share frontier_eval's -")
    check("ENGINE_ARMS is just nevertwice_full there", cse.ENGINE_ARMS == ("nevertwice_full",),
          str(cse.ENGINE_ARMS))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write(tmp / "nevertwice_full.json", {"q1": [], "_engine_commit": OLD_COMMIT})
        problems = fe.check_engine_freshness(cse.ENGINE_ARMS, cse.ENGINE_ARMS, _stub_ctx_path(tmp),
                                             "python research/code_sessions_eval.py")
        check("refused, naming code_sessions_eval.py's own rerun command",
              problems and "python research/code_sessions_eval.py contexts --arm nevertwice_full"
              in problems[0], str(problems))


def test_both_scripts_are_wired_to_refuse_before_judge_and_summary() -> None:
    """Read from source, not run: the guard must sit BEFORE judge_stage/summarise, or a stale
    cache would already have made zero-cost model-skipping progress before being refused."""
    print("\n- the guard is wired in main(), before judge_stage/summarise, in both files -")
    for path, judge_call, summarise_call in (
            (Path(fe.__file__), "judge_stage(arms, data, READER, JUDGE, JUDGE2, args.agree_n)",
             "res = summarise(arms, data, READER, JUDGE, JUDGE2)"),
            (Path(cse.__file__), "judge_stage(arms, qs, READER, JUDGE)",
             "res = summarise(arms, qs, corpus, READER, JUDGE)")):
        src = path.read_text(encoding="utf-8")
        # anchored on "problems = ...check_engine_freshness(" - the CALL site in main(), not the
        # function's own definition line (frontier_eval.py has both; the bare name matches the
        # def first).
        call_idx = src.find("check_engine_freshness(", src.find("problems = "))
        check(f"{path.name}: check_engine_freshness is called", call_idx != -1, path.name)
        check(f"{path.name}: the guard precedes judge_stage", call_idx != -1
              and judge_call in src and call_idx < src.index(judge_call), path.name)
        check(f"{path.name}: the guard precedes summarise", call_idx != -1
              and summarise_call in src and call_idx < src.index(summarise_call), path.name)
        check(f"{path.name}: a refusal returns nonzero (does not fall through to judge/summary)",
              call_idx != -1 and "return 2" in
              src[call_idx:src.index("if args.stage ==", call_idx)])


def test_mutation_a_broken_git_diff_quiet_lets_a_stale_cache_through() -> None:
    """A monkeypatch of `fe.git_diff_quiet` to always report "unchanged", restored in `finally` -
    proves the real git comparison is what refuses a genuinely stale cache (PLAN's own mutation:
    "a mutation that removes the check is red")."""
    print("\n- mutation: git_diff_quiet always saying 'unchanged' lets a stale cache pass -")
    saved = fe.git_diff_quiet
    fe.git_diff_quiet = lambda base, paths: True     # "nothing changed", no matter what
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "nevertwice_snippet.json", {"q1": [], "_engine_commit": OLD_COMMIT})
            problems = fe.check_engine_freshness(["nevertwice_snippet"], fe.ENGINE_ARMS,
                                                 _stub_ctx_path(tmp), "python research/frontier_eval.py")
    finally:
        fe.git_diff_quiet = saved
    check("mutation: WITHOUT a real git comparison, a genuinely stale cache is NOT refused "
          "(would FAIL 'an older recorded commit is refused' above)", problems == [], str(problems))
    # sanity: the real, unmutated check still refuses the identical input.
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        _write(tmp2 / "nevertwice_snippet.json", {"q1": [], "_engine_commit": OLD_COMMIT})
        problems2 = fe.check_engine_freshness(["nevertwice_snippet"], fe.ENGINE_ARMS,
                                              _stub_ctx_path(tmp2), "python research/frontier_eval.py")
    check("and the unmutated check still refuses it", len(problems2) == 1, str(problems2))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_git_head_and_engine_closure_are_sane,
               test_stamp_engine_commit_records_head,
               test_a_cache_recorded_at_head_passes,
               test_an_older_recorded_commit_is_refused_by_name,
               test_a_legacy_cache_with_no_recorded_commit_is_refused,
               test_competitor_arms_are_exempt,
               test_an_arm_with_no_cache_yet_is_not_this_checks_job,
               test_code_sessions_eval_shares_the_same_implementation,
               test_both_scripts_are_wired_to_refuse_before_judge_and_summary,
               test_mutation_a_broken_git_diff_quiet_lets_a_stale_cache_through):
        fn()
    print(f"\nF6 restamp guard: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
