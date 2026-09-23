#!/usr/bin/env python3
"""A1 (Q5): the extraction prompt asks for a `principle` field, and `NEVERTWICE_CROSS_PROJECT`
grows from a plain on/off switch into three modes.

Two behaviours, checked together because they share one constant block in
`nevertwice/_engine_config.py`:

  * `PRINCIPLE_FIELD` gates BOTH halves of the prompt addition - the schema line on the
    pattern/mistake items and the FIELD rubric explaining it - as a single unit: off means the
    prompt is byte-for-byte what it was before this field existed, not "half-added".
  * The rubric lives in the STATIC part of the prompt, before `Known parameters` / `SESSION`,
    unlike every other FIELD explanation in this file (which sit after the transcript). That
    placement is the whole point (prefix-cache friendliness, plan A1) and is the one thing a
    reader cannot verify by eyeballing the schema, so it gets its own check.
  * `NEVERTWICE_CROSS_PROJECT` parses to exactly one of "off"/"all"/"universal" for every input
    the plan lists, and `INJECT_CROSS_PROJECT` stays a simple derived on/off gate
    (`MODE != "off"`) so the suites that already rebind it keep working unchanged.

    python tests/_test_principle_prompt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _fill() -> str:
    return m.EXTRACTION_PROMPT.format(
        transcript="a session transcript", project_hint="demo", tag_vocab="a, b",
        existing_patterns="(none)", existing_mistakes="(none)", existing_decisions="(none)",
        brain_block="", language_rule="Write in ENGLISH.",
        principle_rubric=m._principle_prompt_rubric(),
        principle_schema=m._principle_schema_field())


def test_constants_exist_with_the_planned_defaults() -> None:
    # C8 (2026-09-24): the "planned defaults" reverted - PREREG-Q3Q5:83-85, Q5's gates did not
    # hold on the fresh reading (.loop/explore/G5_READING.md: G5.1 VOID/underpowered, G5.3 not
    # distinguishable, G5.5 not measured). PRINCIPLE_FIELD is now off and CROSS_PROJECT_MODE is
    # now "all" with no env vars set; both are still real, working, OPT-IN values, exercised
    # explicitly elsewhere in this file and in _test_cross_mode.py.
    print("\n- the Q5 constants exist with the planned (post-C8) defaults -")
    check("PRINCIPLE_FIELD defaults off", m.PRINCIPLE_FIELD is False)
    check("PRINCIPLE_MAX_CHARS is 200", m.PRINCIPLE_MAX_CHARS == 200, str(m.PRINCIPLE_MAX_CHARS))
    check("UNIVERSAL_PROJECT is 'universal'", m.UNIVERSAL_PROJECT == "universal")
    check("CROSS_PROJECT_MODE defaults to 'all'", m.CROSS_PROJECT_MODE == "all",
          m.CROSS_PROJECT_MODE)
    check("INJECT_CROSS_PROJECT is derived (MODE != 'off')",
          m.INJECT_CROSS_PROJECT == (m.CROSS_PROJECT_MODE != "off"))


def test_cross_project_mode_parses_every_input() -> None:
    print("\n- NEVERTWICE_CROSS_PROJECT parses to exactly one of off/all/universal -")
    # C8 (2026-09-24): the no-env-var and unrecognised-value defaults reverted from
    # "universal" to "all" - PREREG-Q3Q5:83-85, Q5's gates did not hold
    # (.loop/explore/G5_READING.md). "universal" stays a real, parseable value.
    cases = [(None, "all"), ("", "all"), ("0", "off"), ("off", "off"),
             ("OFF", "off"), ("1", "all"), ("all", "all"), ("ALL", "all"),
             ("universal", "universal"), ("UNIVERSAL", "universal"),
             ("bogus-value", "all"), ("  1  ", "all")]
    for raw, expect in cases:
        got = _parse_with(raw)
        check(f"{raw!r} -> {expect!r}", got == expect, f"got {got!r}")


def _parse_with(raw) -> str:
    """Drive `_parse_cross_project_mode` through the real env var it reads, so the check
    exercises the shipped parser rather than a re-implementation of its rules."""
    import os
    saved = os.environ.get("NEVERTWICE_CROSS_PROJECT")
    had = "NEVERTWICE_CROSS_PROJECT" in os.environ
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


def test_an_unrecognised_value_logs_once_and_degrades_safe() -> None:
    # C8 (2026-09-24): degrades to "all" now, same reversion as the no-env-var default above.
    print("\n- an unrecognised value degrades to 'all' with one queued warning -")
    before = len(m._EARLY_WARNINGS)
    got = _parse_with("not-a-real-mode")
    check("degrades to 'all'", got == "all", got)
    check("a warning was queued", len(m._EARLY_WARNINGS) == before + 1,
          str(m._EARLY_WARNINGS[before:]))
    check("the warning names the bad value", "not-a-real-mode" in m._EARLY_WARNINGS[-1])
    m._EARLY_WARNINGS.pop()   # do not leak a fixture warning into the next log() flush


def test_principle_field_on_adds_both_halves_together() -> None:
    print("\n- PRINCIPLE_FIELD on adds the schema line AND the rubric, as one unit -")
    saved = m.PRINCIPLE_FIELD
    try:
        m.PRINCIPLE_FIELD = True
        filled = _fill()
        check("the schema line is present", '"principle": "one de-identified' in filled)
        check("the FIELD rubric is present", "FIELD principle (pattern/mistake only)" in filled)
        check("the schema addition lands on the pattern item, right after confidence",
              '"confidence": 0.9, "principle"' in filled)
        check("the schema addition lands on the mistake item too",
              filled.count('"confidence": 0.9, "principle"') == 2,
              str(filled.count('"confidence": 0.9, "principle"')))
        check("decisions get no principle key (A1 scopes it to pattern/mistake)",
              '"alternative-to", "target": "entity"}], "confidence": 0.9, "principle"'
              not in filled)
    finally:
        m.PRINCIPLE_FIELD = saved


def test_principle_field_off_removes_both_halves_byte_for_byte() -> None:
    print("\n- PRINCIPLE_FIELD off removes the schema line AND the rubric, fully -")
    saved = m.PRINCIPLE_FIELD
    try:
        m.PRINCIPLE_FIELD = False
        filled = _fill()
        check("no mention of 'principle' anywhere in the prompt",
              "principle" not in filled.lower())
        m.PRINCIPLE_FIELD = True
        filled_on = _fill()
        m.PRINCIPLE_FIELD = False
        filled_off_again = _fill()
        check("off is deterministic (re-toggling produces the identical prompt)",
              filled == filled_off_again)
        check("on and off actually differ (the toggle does something)",
              filled_on != filled_off_again)
    finally:
        m.PRINCIPLE_FIELD = saved


def test_the_rubric_sits_before_the_transcript() -> None:
    """The whole point of A1's placement choice: prefix-cache friendliness only holds if the
    rubric is in the part of the prompt that is IDENTICAL across sessions, i.e. before the
    per-session `SESSION:`/transcript block - not appended at the end like the other FIELD
    explanations and `brain_block`."""
    print("\n- the principle rubric sits BEFORE SESSION/the transcript, not after -")
    saved = m.PRINCIPLE_FIELD
    try:
        m.PRINCIPLE_FIELD = True
        filled = _fill()
        idx_rubric = filled.find("FIELD principle")
        idx_session = filled.find("SESSION:")
        check("the rubric is present", idx_rubric != -1)
        check("SESSION: is present", idx_session != -1)
        check("the rubric appears strictly before SESSION:", 0 <= idx_rubric < idx_session,
              f"rubric at {idx_rubric}, SESSION: at {idx_session}")
    finally:
        m.PRINCIPLE_FIELD = saved


def test_no_env_vars_at_all_yields_the_c8_defaults_and_a_reversion_is_caught() -> None:
    """C8 item 4 (2026-09-24): with NEVERTWICE_CROSS_PROJECT, NEVERTWICE_PRINCIPLE and
    NEVERTWICE_PRINCIPLE_T simultaneously absent, the shipped defaults are mode='all',
    PRINCIPLE_FIELD=False, T_PRINCIPLE=0.75 (PREREG-Q3Q5:83-85, .loop/explore/G5_READING.md,
    G5.6 / .loop/explore/principle_twins.json).

    Proved in a FRESH SUBPROCESS, not by reading `m.PRINCIPLE_FIELD`/`pr.T_PRINCIPLE` in THIS
    process: those two are one-shot reads resolved once at import, already baked in from
    whatever env THIS test file's own top-of-file `import memory_hook as m` saw - which could
    be right by accident if the caller's shell happens to have neither var set, not because the
    shipped default is actually correct. `_parse_cross_project_mode`, unlike the other two, IS a
    real re-callable parser, so its mutation proof runs in-process instead."""
    print("\n- fresh subprocess, no env vars at all: mode='all', PRINCIPLE_FIELD=False, T=0.75 -")
    import os
    import subprocess
    scrub = ("NEVERTWICE_CROSS_PROJECT", "NEVERTWICE_PRINCIPLE", "NEVERTWICE_PRINCIPLE_T")
    env = {k: v for k, v in os.environ.items() if k not in scrub}
    script = ("import sys; sys.path.insert(0, sys.argv[1]); import memory_hook as m; "
             "import principles as pr; "
             "print(m.CROSS_PROJECT_MODE); print(m.PRINCIPLE_FIELD); print(pr.T_PRINCIPLE)")
    proc = subprocess.run([sys.executable, "-c", script, str(ROOT / "nevertwice")],
                          env=env, cwd=str(ROOT / "nevertwice"),
                          capture_output=True, text=True, timeout=60)
    check("fresh no-env subprocess exits cleanly", proc.returncode == 0,
         f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr[-800:]!r}")
    out_lines = (proc.stdout.strip().splitlines() + ["", "", ""])[:3]
    mode_s, field_s, t_s = out_lines
    check("fresh-import, no-env CROSS_PROJECT_MODE is 'all'", mode_s == "all", mode_s)
    check("fresh-import, no-env PRINCIPLE_FIELD is False", field_s == "False", field_s)
    check("fresh-import, no-env T_PRINCIPLE is 0.75", t_s == "0.75", t_s)

    print("\n- mutation: reverting the mode parser's no-input branch to 'universal' is caught -")
    saved_parse = m._parse_cross_project_mode

    def _pre_c8_no_input_branch():
        raw = os.environ.get("NEVERTWICE_CROSS_PROJECT")
        if raw is None or not raw.strip():
            return "universal"          # the exact pre-C8 shape this test must catch
        return saved_parse()

    had = "NEVERTWICE_CROSS_PROJECT" in os.environ
    prior = os.environ.pop("NEVERTWICE_CROSS_PROJECT", None)
    m._parse_cross_project_mode = _pre_c8_no_input_branch
    try:
        reverted = m._parse_cross_project_mode()
    finally:
        m._parse_cross_project_mode = saved_parse
        if had:
            os.environ["NEVERTWICE_CROSS_PROJECT"] = prior
    check("mutation: the PRE-C8 no-input branch disagrees with the shipped no-env default "
         "(would FAIL 'fresh-import, no-env CROSS_PROJECT_MODE is all' by name)",
         reverted != "all", reverted)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_constants_exist_with_the_planned_defaults,
               test_cross_project_mode_parses_every_input,
               test_an_unrecognised_value_logs_once_and_degrades_safe,
               test_principle_field_on_adds_both_halves_together,
               test_principle_field_off_removes_both_halves_byte_for_byte,
               test_the_rubric_sits_before_the_transcript,
               test_no_env_vars_at_all_yields_the_c8_defaults_and_a_reversion_is_caught):
        fn()
    print(f"\nprinciple prompt: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
