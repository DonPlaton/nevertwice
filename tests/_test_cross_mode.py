#!/usr/bin/env python3
"""A4 (Q5): `NEVERTWICE_CROSS_PROJECT` has three modes, and "universal" is a real boundary,
not just a smaller "all" - project B must never see project A's own notes through the
cross-project section, only what the sleep-time promoter (A5) already de-identified and
independently corroborated in the synthetic `universal` project.

    python tests/_test_cross_mode.py
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _parse(raw) -> str:
    import os
    saved, had = os.environ.get("NEVERTWICE_CROSS_PROJECT"), "NEVERTWICE_CROSS_PROJECT" in os.environ
    try:
        if raw is None:
            os.environ.pop("NEVERTWICE_CROSS_PROJECT", None)
        else:
            os.environ["NEVERTWICE_CROSS_PROJECT"] = raw
        return m._parse_cross_project_mode()
    finally:
        if had:
            os.environ["NEVERTWICE_CROSS_PROJECT"] = saved
        else:
            os.environ.pop("NEVERTWICE_CROSS_PROJECT", None)


def test_mode_parses_every_input() -> None:
    print("\n- NEVERTWICE_CROSS_PROJECT parses to off/all/universal for every input -")
    # C8 (2026-09-24): the no-env-var and unrecognised-value defaults reverted from
    # "universal" to "all" - PREREG-Q3Q5:83-85, Q5's gates did not hold
    # (research/Q5_PRINCIPLE_LAYER.md). "universal" is still a real, parseable value - only the
    # DEFAULT changed - so it stays in this table unmoved.
    for raw, expect in ((None, "all"), ("", "all"), ("0", "off"), ("off", "off"),
                        ("1", "all"), ("all", "all"), ("universal", "universal"),
                        ("junk", "all")):
        got = _parse(raw)
        check(f"{raw!r} -> {expect!r}", got == expect, f"got {got!r}")


def _seed(project: str, title: str, description: str, tags=("gpu",)) -> str:
    """Write a live pattern note AND embed it into the cache - `write_typed_note` alone leaves
    the embedding cache untouched (that is a separate pipeline step, `update_embeddings`,
    normally run once per session over every note it just wrote); without it there is nothing
    for `_retrieval_candidates` to return, offline sandbox or not."""
    stem = m.write_typed_note("Patterns", {"title": title, "description": description},
                              project, "2026-09-23", list(tags), "pattern")
    if stem:
        m.update_embeddings([(stem, "pattern", project, title, description, "")])
    return stem


def test_universal_with_an_empty_pool_is_silent_and_makes_no_embed_call() -> None:
    print("\n- universal mode + empty pool: no cross section, no embed call, no leak -")
    make_sandbox(m, "cm_empty_", offline=True)
    _seed("project_a", "tune the retry backoff", "Retry backoff tuned for the flaky API host.")

    calls = []
    orig = m.embed_text
    m.embed_text = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    try:
        hits = m.retrieve_cross_project("project_b", "retry backoff tuning", mode="universal")
    finally:
        m.embed_text = orig
    check("no hits from an empty universal pool", hits == [], str(hits))
    check("no embed call was made for an empty pool", calls == [], str(len(calls)))

    # C8 (2026-09-24): CROSS_PROJECT_MODE now defaults to "all", not "universal" - this test is
    # specifically about UNIVERSAL mode's guarantee through the full emit_session_start_context /
    # emit_prompt_recall entry points, neither of which takes an explicit mode argument (they read
    # the live module constant, per _engine_recall.py's `mode=CROSS_PROJECT_MODE` at its own call
    # site). Forcing the constant here - restored in `finally` alongside the other monkeypatches -
    # keeps this check testing "universal" itself rather than silently starting to test "all"'s
    # (expected, F4-documented) leak instead.
    _orig_fns = {n: getattr(m, n) for n in
                ("is_tracked_project", "derive_project_from_cwd", "retrieve_relevant",
                 "CROSS_PROJECT_MODE")}
    try:
        m.is_tracked_project = lambda cwd: True
        m.derive_project_from_cwd = lambda cwd: "project_b"
        m.retrieve_relevant = lambda *a, **k: []
        m.CROSS_PROJECT_MODE = "universal"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.emit_session_start_context("D:\\Coding\\project_b")
        out = buf.getvalue()
        check("SessionStart carries no foreign title with an empty universal pool",
              "retry backoff tuned" not in out.lower(), out)

        state_before = dict(m._load_prompt_recall_state("sid-empty"))
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            m.emit_prompt_recall("D:\\Coding\\project_b",
                                 "how should I tune the retry backoff for a flaky host",
                                 "sid-empty")
        out2 = buf2.getvalue()
        check("UserPromptSubmit carries no foreign title with an empty universal pool",
              "retry backoff tuned" not in out2.lower(), out2)
    finally:
        for n, v in _orig_fns.items():
            setattr(m, n, v)


def test_universal_with_a_pool_returns_only_universal_stems() -> None:
    print("\n- universal mode + a populated pool: every hit is a universal-project stem -")
    make_sandbox(m, "cm_pool_", offline=True)
    _seed("project_a", "shared retry rule", "Cap the retry backoff before comparing two hosts.")
    _seed("project_c", "another note", "Something entirely unrelated about caching layers.")
    u_stem = _seed(m.UNIVERSAL_PROJECT, "cap the retry backoff",
                   "Cap the retry backoff before comparing two runs of a flaky call.")
    hits = m.retrieve_cross_project("project_b", "cap the retry backoff before comparing",
                                    mode="universal")
    check("at least one hit came back", bool(hits), str(hits))
    check("every hit's stem lives in the universal pool",
          all(h.get("stem", "").startswith("2026-09-23-universal-") for h in hits), str(hits))
    check("every hit's shown project is 'universal'",
          all(h.get("project") == m.UNIVERSAL_PROJECT for h in hits), str(hits))
    check("the seeded universal stem is among the hits",
          any(h.get("stem") == u_stem for h in hits), str(hits))


def test_project_as_identifiers_are_absent_in_universal_and_present_in_all() -> None:
    """The leak-proof claim, proved both ways: universal mode cannot see project A's own
    material, and "all" mode - unrestricted by design (O-U1 rejected) - still can, so this
    test is not merely observing that nothing was returned."""
    print("\n- universal mode hides project A's own notes from project B; 'all' mode leaks them -")
    make_sandbox(m, "cm_leak_", offline=True)
    _seed("project_a", "rotate the shared api key",
         "Rotate the shared api key on host db-primary before the next deploy window.")
    query = "when should we rotate the shared api key before the deploy window"

    universal_hits = m.retrieve_cross_project("project_b", query, mode="universal")
    all_hits = m.retrieve_cross_project("project_b", query, mode="all")

    check("universal mode returns nothing for project A's own note (no pool yet)",
          not any("rotate" in (h.get("title") or "").lower() for h in universal_hits),
          str(universal_hits))
    check("'all' mode DOES surface project A's note (proves the test can see a leak)",
          any("rotate" in (h.get("title") or "").lower() for h in all_hits), str(all_hits))
    check("'all' mode shows project A's real project name",
          any(h.get("project") == "project_a" for h in all_hits), str(all_hits))


def test_mutation_universal_falling_through_to_cross_true() -> None:
    """A monkeypatch that RECORDS what `_retrieval_candidates` is called with, driven through
    the real, shipped `retrieve_cross_project` - proves universal mode calls it with
    `(UNIVERSAL_PROJECT, cross=False)`, never `(project, cross=True)` (the "all" shape). This
    is the exact regression the plan names: "universal falling through to cross=True"."""
    print("\n- mutation-sensitive: universal mode never calls _retrieval_candidates with cross=True -")
    make_sandbox(m, "cm_mut_", offline=True)
    _seed(m.UNIVERSAL_PROJECT, "a universal note", "A de-identified rule with no source name.")
    calls = []
    orig = m._retrieval_candidates

    def spy(project, cross, cache=None, query=None):
        calls.append((project, cross))
        return orig(project, cross, cache=cache, query=query)

    m._retrieval_candidates = spy
    try:
        m.retrieve_cross_project("project_b", "a de-identified rule", mode="universal")
    finally:
        m._retrieval_candidates = orig
    check("_retrieval_candidates was called with (UNIVERSAL_PROJECT, cross=False)",
          (m.UNIVERSAL_PROJECT, False) in calls, str(calls))
    check("it was NEVER called with cross=True in universal mode",
          not any(cross is True for _p, cross in calls), str(calls))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_mode_parses_every_input,
               test_universal_with_an_empty_pool_is_silent_and_makes_no_embed_call,
               test_universal_with_a_pool_returns_only_universal_stems,
               test_project_as_identifiers_are_absent_in_universal_and_present_in_all,
               test_mutation_universal_falling_through_to_cross_true):
        fn()
    print(f"\ncross mode: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
