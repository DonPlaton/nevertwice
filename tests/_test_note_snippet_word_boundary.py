#!/usr/bin/env python3
"""C5 (2026-09-23): `_note_snippet` (`nevertwice/_engine_recall.py` ~797) used a plain
`out[:max_chars].rstrip()` char-slice to cap the text cross-project recall injects into
another session - this can cut a plain word in half (the H6 recount's `difficul`/`docum`/
`funct`/`loc`/`oper`/`propagati` fragments, C2) OR split an identifier-shaped token
mid-string (`svc-a000.internal` -> `svc-a000.` is a DIFFERENT, real hostname-shaped string,
not a truncation marker). `_cut_word_boundary` (`_engine_write.py`, already used for the
`principle` field's own cap, same shared namespace) either keeps a boundary-crossing word/
token WHOLE or drops it entirely - never a fragment, never a split identifier.

No model, no GPU, no Ollama - pure text, a stub sandbox note written to disk and read back
through the real `_note_snippet`.

    python tests/_test_note_snippet_word_boundary.py
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
from _sandbox import make_sandbox  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _write_note(project: str, title: str, description: str) -> str:
    return m.write_typed_note(m.TYPE_FOLDER["pattern"],
                              {"title": title, "description": description,
                               "principle": "", "entities": []},
                              project, "2026-09-23", [], "pattern")


def test_a_word_crossing_the_boundary_ends_whole_or_is_dropped() -> None:
    """A plain word straddles the 220-char cut - the OLD plain slice fragments it
    ("extraordi"); `_cut_word_boundary` either keeps it whole or drops it, never a partial
    word. Built so the boundary provably falls INSIDE the word (asserted, not assumed)."""
    print("\n- (a) a word crossing the 220-char boundary ends whole or is dropped -")
    filler = "x" * 210
    word = "extraordinarily"
    tail = " more words follow after this one to pad the description out nicely for the test."
    desc = f"{filler} {word}{tail}"
    boundary = 220
    check("setup: the word really does straddle the boundary (sanity on the fixture itself)",
         len(filler) + 1 < boundary < len(filler) + 1 + len(word),
         f"word spans [{len(filler) + 1}, {len(filler) + 1 + len(word)}), boundary={boundary}")

    old_cut = desc[:boundary].rstrip()
    check("setup: the OLD plain char-slice DOES fragment the word (proves the fixture bites)",
         old_cut.endswith(word[:boundary - len(filler) - 1]) and not old_cut.endswith(word),
         repr(old_cut[-20:]))

    stem = _write_note("c5_word_boundary", "long description fixture", desc)
    check("the fixture note was written", bool(stem), str(stem))
    snippet = m._note_snippet(stem, "pattern", max_chars=boundary)
    # The specific fragment the OLD plain slice produced, e.g. "extraordi" - the direct
    # regression signature. Any OTHER proper prefix of `word` shorter than the whole word
    # would be an equally invalid fragment, so check the general shape: the snippet's own
    # last whitespace-delimited chunk is either the WHOLE word or not a prefix of it at all.
    last_chunk = snippet.rsplit(" ", 1)[-1] if snippet else ""
    is_fragment = last_chunk != word and last_chunk != "" and word.startswith(last_chunk)
    check("the NEW snippet's last word is never a partial prefix of the straddling word "
         "(whole or dropped, never a fragment)", not is_fragment,
         f"last_chunk={last_chunk!r} snippet_tail={snippet[-20:]!r}")
    check("the OLD-style fragment specifically ('extraordi') is gone from the new snippet",
         not snippet.endswith(old_cut[len(filler):]) or old_cut[len(filler):] == "",
         repr(snippet[-20:]))


def test_b_identifier_crossing_the_boundary_is_never_split() -> None:
    """An identifier-shaped token (`svc-a000.internal`, hyphen+digit+dot) straddles the
    220-char cut - the OLD plain slice splits it into `svc-a000.` (itself a DIFFERENT,
    hostname-shaped string - not a truncation marker, a wrong answer); the fix keeps it
    whole or drops it, same word-boundary rule (no space inside the identifier, so it is
    one indivisible unit to `_cut_word_boundary`)."""
    print("\n- (b) an identifier crossing the boundary is never split mid-token -")
    ident = "svc-a000.internal"
    filler = "x" * 215
    tail = " more text after this identifier pads the description out for the fixture here."
    desc = f"{filler} {ident}{tail}"
    boundary = 220
    check("setup: the identifier really does straddle the boundary",
         len(filler) + 1 < boundary < len(filler) + 1 + len(ident),
         f"ident spans [{len(filler) + 1}, {len(filler) + 1 + len(ident)}), boundary={boundary}")

    old_cut = desc[:boundary].rstrip()
    split_prefix = ident[:boundary - len(filler) - 1]
    check("setup: the OLD plain char-slice DOES split the identifier mid-token",
         old_cut.endswith(split_prefix) and split_prefix != ident, repr(old_cut[-15:]))

    stem = _write_note("c5_ident_boundary", "identifier fixture", desc)
    check("the fixture note was written", bool(stem), str(stem))
    snippet = m._note_snippet(stem, "pattern", max_chars=boundary)
    check("the identifier is either present WHOLE or absent - never a split fragment of it",
         ident in snippet or split_prefix not in snippet, repr(snippet[-20:]))
    check("specifically, the split fragment the old code produced is gone",
         not snippet.endswith(split_prefix) or snippet.endswith(ident), repr(snippet[-20:]))


def test_c_no_boundary_at_all_returns_empty() -> None:
    """C5b (2026-09-23, the auditor's edge case on 490ee47): when the FIRST token alone
    already exceeds `max_chars` - no space anywhere before the cut - C5's own fix still fell
    back to the raw fragment (`_cut_word_boundary`'s pre-C5b-only branch). `_note_snippet`
    now asks for `require_boundary=True`: no boundary before the cap means NOTHING, not a
    fragment. `"a" * 300` is one giant 300-char "word" with no space in it at all."""
    print("\n- (c) no word boundary anywhere before the cap - returns empty, not a fragment -")
    desc = "a" * 300
    check("setup: this description really has no space anywhere", " " not in desc, desc[:20])
    stem = _write_note("c5b_no_boundary", "no boundary fixture", desc)
    check("the fixture note was written", bool(stem), str(stem))
    snippet = m._note_snippet(stem, "pattern", max_chars=220)
    check("the snippet is empty - no 220-char fragment of 'a's", snippet == "", repr(snippet))


def test_d_long_url_with_no_early_boundary_returns_empty() -> None:
    """C5b: the auditor's other example - a URL with no space until well past the 220-char
    cap (`_PRINCIPLE_PATH_RE`-shaped in spirit, though this text never goes through
    principle_scan). The OLD code (490ee47, pre-C5b) would return the URL cut mid-path -
    itself a different, wrong URL, not a truncation marker. Now: empty."""
    print("\n- (d) a long URL with no early boundary - returns empty, not a mid-path cut -")
    url = "https://example.com/" + "p" * 250
    desc = f"{url} and then words follow after this to pad the fixture out a little more."
    check("setup: the URL alone already exceeds the 220-char cap with no space inside it",
         len(url) > 220 and " " not in url, str(len(url)))
    stem = _write_note("c5b_long_url", "long url fixture", desc)
    check("the fixture note was written", bool(stem), str(stem))
    snippet = m._note_snippet(stem, "pattern", max_chars=220)
    check("the snippet is empty - no mid-path cut of the URL", snippet == "", repr(snippet))


def test_e_a_normal_sentence_is_unchanged_by_c5b() -> None:
    """C5b changes ONE edge only (no boundary before the cap at all) - an ordinary
    description, well under the cap, is untouched, and one where SOME earlier word crosses
    the boundary (C5's own case, tests (a)/(b) above) keeps behaving exactly as C5 left it."""
    print("\n- (e) a normal sentence is unchanged - C5b only touches the no-boundary edge -")
    short = "Keep migrations idempotent and reversible."
    stem = _write_note("c5b_normal_short", "short fixture", short)
    check("the fixture note was written", bool(stem), str(stem))
    snippet = m._note_snippet(stem, "pattern", max_chars=220)
    check("a short description under the cap is returned unchanged", snippet == short,
         repr(snippet))


def test_f_principle_cap_keeps_the_old_behaviour_by_default() -> None:
    """C5b's explicit constraint: `_cut_word_boundary`'s DEFAULT (`require_boundary=False`)
    must be byte-for-byte the OLD (pre-C5b) behaviour, because `write_typed_note`'s own
    `principle` cap (A3/W8, `_engine_write.py`) calls it with no keyword argument at all - a
    principle whose first token alone exceeds `PRINCIPLE_MAX_CHARS` is capped to a fragment,
    exactly as it was before C5b, never emptied. This is the one place C5b was told NOT to
    change."""
    print("\n- (f) principle cap with no early boundary: unchanged, still a fragment -")
    limit = m.PRINCIPLE_MAX_CHARS
    one_giant_token = "x" * (limit + 80)
    result_default = m._cut_word_boundary(one_giant_token, limit)
    check("default require_boundary=False: still returns the old fragment, length == limit",
         result_default == one_giant_token[:limit], repr(result_default[-10:]))
    result_explicit_false = m._cut_word_boundary(one_giant_token, limit, require_boundary=False)
    check("require_boundary=False given explicitly: identical to the default",
         result_explicit_false == result_default, repr(result_explicit_false[-10:]))
    result_required = m._cut_word_boundary(one_giant_token, limit, require_boundary=True)
    check("require_boundary=True (the NEW, opt-in-only behaviour): empty instead",
         result_required == "", repr(result_required))

    # End to end through the real write path, not just the helper in isolation.
    stem = m.write_typed_note(m.TYPE_FOLDER["pattern"],
                              {"title": "principle cap fixture", "description": "",
                               "principle": one_giant_token, "entities": []},
                              "c5b_principle_cap", "2026-09-23", [], "pattern")
    check("the fixture note was written", bool(stem), str(stem))
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    written = fm.get("principle") or ""
    check("the WRITTEN principle is still capped to a fragment (old behaviour), not dropped",
         0 < len(written) <= limit and written == one_giant_token[:len(written)],
         repr(written[-10:]))


def test_g_caller_rendering_on_an_empty_snippet_has_no_dangling_separator() -> None:
    """C5b's instruction: check what the callers actually render on an empty snippet. Both
    `_fact_line` and `_cross_line` (`_engine_recall.py`) already guard with
    `(f" - {snip}" if snip else "")` - an empty snippet was ALWAYS a valid return (a missing
    file, an OSError, a genuinely empty description), so this contract predates C5b. Proven
    here by monkeypatching `_note_snippet` itself (the same pattern `tests/_test_memory_v3.py`
    already uses for this exact function) rather than trusting the fixture to hit the
    no-boundary edge through the full read path."""
    print("\n- (g) callers render title-only on an empty snippet, no dangling ' - ' -")
    saved = m._note_snippet
    m._note_snippet = lambda stem, ntype, max_chars=220: ""
    try:
        fact = m._fact_line({"stem": "s1", "ntype": "pattern", "title": "a title",
                             "recurrence": 1})
        cross = m._cross_line({"stem": "s2", "ntype": "pattern", "title": "another title",
                               "project": "acme"})
    finally:
        m._note_snippet = saved
    check("_fact_line has no dangling ' - ' separator when the snippet is empty",
         " - " not in fact, repr(fact))
    check("_fact_line still shows the title", "a title" in fact, repr(fact))
    check("_cross_line has no dangling ' - ' separator when the snippet is empty",
         " - " not in cross, repr(cross))
    check("_cross_line still shows the title", "another title" in cross, repr(cross))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code - enforced project-
    wide by tests/_test_the_harness_agrees_with_itself.py."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    make_sandbox(m, "note_snippet_wb_", offline=True)
    for fn in (test_a_word_crossing_the_boundary_ends_whole_or_is_dropped,
              test_b_identifier_crossing_the_boundary_is_never_split,
              test_c_no_boundary_at_all_returns_empty,
              test_d_long_url_with_no_early_boundary_returns_empty,
              test_e_a_normal_sentence_is_unchanged_by_c5b,
              test_f_principle_cap_keeps_the_old_behaviour_by_default,
              test_g_caller_rendering_on_an_empty_snippet_has_no_dangling_separator):
        fn()
    print(f"\nnote snippet word boundary: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
