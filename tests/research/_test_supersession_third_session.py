#!/usr/bin/env python3
"""A9 (PLAN-Q3Q5.md, `research/supersession_bench.py --third-session`): the pure helpers behind
the flag, hermetic. `supersession_bench.py` has no `--dry` mode of its own - the "nevertwice" arm
always calls the real extractor, filler session included, so this suite cannot run `--third-session`
end to end without Ollama (forbidden while a measurement campaign uses the GPU). It tests the pure
pieces instead: the token-delta bookkeeping, the slug reader `--third-session`'s own "wrote no
note on the case's slug" check is built on, and the CLI wiring, read from source rather than run.

    python tests/research/_test_supersession_third_session.py
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
import supersession_bench as sb  # noqa: E402
import memory_hook as m  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_filler_session_matches_plans_wording_exactly() -> None:
    print("\n- FILLER_SESSION renders PLAN's own filler line -")
    check("the template", sb.FILLER_SESSION == "Back on {domain}; reran the suite, nothing else changed.",
          sb.FILLER_SESSION)
    rendered = sb.FILLER_SESSION.format(domain="the API client")
    check("a real case's domain slots in cleanly",
          rendered == "Back on the API client; reran the suite, nothing else changed.", rendered)


def test_llm_stats_snapshot_and_delta() -> None:
    print("\n- _llm_stats_snapshot/_llm_stats_delta read _LLM_STATS purely -")
    saved = dict(m._LLM_STATS)
    try:
        m._LLM_STATS.clear()
        check("a snapshot before any tokens are recorded is (0, 0)",
              sb._llm_stats_snapshot(m) == (0, 0), str(sb._llm_stats_snapshot(m)))
        before = sb._llm_stats_snapshot(m)
        m._LLM_STATS["prompt_tokens"] = 120
        m._LLM_STATS["eval_tokens"] = 30
        after = sb._llm_stats_snapshot(m)
        check("the delta after one capture matches what was added",
              sb._llm_stats_delta(before, after) == {"prompt_tokens": 120, "eval_tokens": 30},
              str(sb._llm_stats_delta(before, after)))
        before2 = sb._llm_stats_snapshot(m)
        m._LLM_STATS["prompt_tokens"] += 45
        m._LLM_STATS["eval_tokens"] += 5
        after2 = sb._llm_stats_snapshot(m)
        check("a SECOND delta is against the running total, not from zero again",
              sb._llm_stats_delta(before2, after2) == {"prompt_tokens": 45, "eval_tokens": 5},
              str(sb._llm_stats_delta(before2, after2)))
    finally:
        m._LLM_STATS.clear()
        m._LLM_STATS.update(saved)


def test_project_slugs_reads_real_notes_and_stays_isolated_per_project() -> None:
    print("\n- _project_slugs reads the real store, live and retired, one project at a time -")
    # No make_sandbox here (deliberately): it rebases m.VAULT directly and leaves
    # sandbox_guard's own _STORE pointing at the OLD directory, while `_project_slugs`
    # (like `_store_state`, the function it mirrors) reads the store through
    # `sandbox_guard.store()` - the two would silently point at different directories.
    # The module-level `sandbox_guard.isolate()` `_env_guard`/`supersession_bench` already
    # ran keeps `sg.store()` and `m.VAULT` in agreement, so distinct project names are the
    # isolation between this function's writes and every other test's.
    check("an empty project has no slugs", sb._project_slugs("noproj-pslugs") == set())

    m.write_typed_note("Decisions", {"title": "the deploy timeout", "description": "30 seconds"},
                       "thirdp1", "2026-01-01", ["t"], "decision")
    m.write_typed_note("Patterns", {"title": "retry with backoff", "description": "d"},
                       "thirdp1", "2026-01-01", ["t"], "pattern")
    slugs = sb._project_slugs("thirdp1")
    check("both slugs are read back", slugs == {"the-deploy-timeout", "retry-with-backoff"},
          str(slugs))

    m.write_typed_note("Decisions", {"title": "an unrelated note", "description": "x"},
                       "otherproj1", "2026-01-01", ["t"], "decision")
    check("a different project's slug does not leak in",
          "an-unrelated-note" not in sb._project_slugs("thirdp1"))
    check("and IS read under its own project",
          sb._project_slugs("otherproj1") == {"an-unrelated-note"})

    # a retired note (moved to Superseded/, as a same-slug-another-day retirement would leave it)
    # must still count - "wrote no note on the case's slug" means no note anywhere, not just live.
    live_path = (m.VAULT / "Decisions" / "2026-01-01-thirdp1-decision-the-deploy-timeout.md")
    check("the live file exists at the expected stem", live_path.exists(), str(live_path))
    sup_dir = m.VAULT / "Decisions" / "Superseded"
    sup_dir.mkdir(parents=True, exist_ok=True)
    live_path.rename(sup_dir / live_path.name)
    slugs_after_retire = sb._project_slugs("thirdp1")
    check("a RETIRED note's slug is still counted (Superseded/ is globbed too)",
          "the-deploy-timeout" in slugs_after_retire, str(slugs_after_retire))


def test_third_session_new_slugs_is_empty_for_a_truly_silent_filler() -> None:
    """The exact check `run_nevertwice(third_session=True)` runs around the filler capture,
    reproduced here without ever calling the extractor: slugs before == slugs after a NO-OP."""
    print("\n- the before/after slug diff run_nevertwice uses is empty when nothing was written -")
    m.write_typed_note("Decisions", {"title": "the timeout", "description": "30 seconds"},
                       "silentp", "2026-01-01", ["t"], "decision")
    before = sb._project_slugs("silentp")
    # nothing written here - the filler capture this stands in for is a no-op by construction
    after = sb._project_slugs("silentp")
    new_slugs = sorted(after - before)
    check("no new slugs after a no-op capture", new_slugs == [], str(new_slugs))
    check("third_session_silent would read True", not new_slugs)

    # sanity: the diff DOES catch a genuine new slug, so the check above is not vacuously true.
    m.write_typed_note("Decisions", {"title": "a second fact", "description": "y"},
                       "silentp", "2026-01-01", ["t"], "decision")
    after_real_write = sb._project_slugs("silentp")
    check("sanity: a genuinely NEW note IS caught by the same diff",
          sorted(after_real_write - before) == ["a-second-fact"], str(after_real_write - before))


def test_cli_flag_is_wired_to_the_engine_arm_only() -> None:
    """Read from source, not run: `--third-session` must reach `run_nevertwice` (the only arm
    with a real third session to add) and the `--runs N` subprocess launcher (or a pooled
    multi-run measurement would silently drop the flag on runs 2..N)."""
    print("\n- --third-session is wired into the CLI, the engine-arm call, and --runs forwarding -")
    src = Path(sb.__file__).read_text(encoding="utf-8")
    check("the flag is declared", '"--third-session"' in src)
    check("it reaches run_nevertwice's call site",
          "third_session=args.third_session" in src)
    check("it is forwarded to the --runs N subprocess launcher (or run 2..N silently drop it)",
          '"--third-session"' in src.split("third_session=args.third_session", 1)[1])
    check("run_nevertwice's own signature accepts it",
          "def run_nevertwice(cases: list[dict], k: int, sleep: bool = False, "
          "third_session: bool = False)" in src)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_filler_session_matches_plans_wording_exactly,
               test_llm_stats_snapshot_and_delta,
               test_project_slugs_reads_real_notes_and_stays_isolated_per_project,
               test_third_session_new_slugs_is_empty_for_a_truly_silent_filler,
               test_cli_flag_is_wired_to_the_engine_arm_only):
        fn()
    print(f"\nsupersession third-session (pure helpers, no Ollama): {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
