#!/usr/bin/env python3
"""C5 (2026-09-24): `_note_snippet` (`nevertwice/_engine_recall.py` ~797) used a plain
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


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code - enforced project-
    wide by tests/_test_the_harness_agrees_with_itself.py."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    make_sandbox(m, "note_snippet_wb_", offline=True)
    for fn in (test_a_word_crossing_the_boundary_ends_whole_or_is_dropped,
              test_b_identifier_crossing_the_boundary_is_never_split):
        fn()
    print(f"\nnote snippet word boundary: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
