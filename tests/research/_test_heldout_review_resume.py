#!/usr/bin/env python3
"""The held-out review resumes: a page of only the unmarked cards, and both sittings collected.

The first review sitting stopped at its target, 73 of 200 candidates marked with the easiest
first, which is why the held-out number is a dev-set figure (§3.9). The clean number needs the
other 127 marked. This suite checks the two pieces that make the second sitting cheap for the
owner: `--page-only --skip-marked` builds a page of ONLY the undecided cards, and `--collect`
takes both marks files, with a later file winning where both mark the same card.

No model and no transcript: synthetic candidates in a temp directory (CODE_HELDOUT_DIR).

Run:  python tests/research/_test_heldout_review_resume.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_TMP = Path(tempfile.mkdtemp(prefix="nevertwice_review_resume_"))
os.environ["CODE_HELDOUT_DIR"] = str(_TMP)
sys.path.insert(0, str(ROOT / "research"))
import heldout_review as hr  # noqa: E402

#: the manifest lives in research/data: point it into the temp dir so a run moves nothing tracked
hr.MANIFEST = _TMP / "manifest.json"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _cand(i: int) -> dict:
    return {"id": f"rv{i:03d}", "question": f"q{i}?", "answer": f"a{i}", "quote": f"a{i} here",
            "passage": f"text a{i} here", "checks": {}, "passed": 5, "auto_accepted": True,
            "source": {"day": "2026-09-01"}}


def _run(*argv: str) -> int:
    old = sys.argv
    sys.argv = ["heldout_review.py", *argv]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return hr.main()
    except SystemExit as exc:            # argparse: an option this version does not know
        return exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.argv = old


def test_the_second_sitting() -> None:
    print("\n- a page of only the unmarked cards -")
    cands = [_cand(i) for i in range(10)]
    hr.CANDIDATES.write_text(json.dumps({"built_at": "x", "seed": 1, "candidates": cands}),
                             encoding="utf-8")
    first = _TMP / "marks_1.json"
    first.write_text(json.dumps({"marks": {"rv000": {"verdict": "yes"}, "rv001": {"verdict": "no"},
                                           "rv002": {"verdict": "yes"}}}), encoding="utf-8")
    rc = _run("--page-only", "--skip-marked", str(first))
    page = _TMP / "review_remaining.html"
    check("the resume page is written", rc == 0 and page.is_file(), f"rc={rc}")
    html = page.read_text(encoding="utf-8") if page.is_file() else ""
    check("it carries every unmarked card", all(f'"rv{i:03d}"' in html for i in range(3, 10)))
    check("and none of the marked ones", not any(f'"rv{i:03d}"' in html for i in range(3)),
          [i for i in range(3) if f'"rv{i:03d}"' in html])
    check("the original page is left alone", not hr.PAGE.exists())

    print("\n- both sittings collected, the later one winning -")
    second = _TMP / "marks_2.json"
    second.write_text(json.dumps({"marks": {"rv002": {"verdict": "no"}, "rv003": {"verdict": "yes"},
                                            "rv004": {"verdict": "yes", "question": "edited?",
                                                      "answer": "a4", "edited": True}}}),
                      encoding="utf-8")
    rc = _run("--collect", str(first), str(second))
    corpus = json.loads(hr.CORPUS.read_text(encoding="utf-8")) if hr.CORPUS.is_file() else {}
    kept = sorted(p["id"] for p in corpus.get("projects", []))
    check("the corpus keeps the yes marks of both sittings", kept == ["rv000", "rv003", "rv004"],
          str(kept))
    check("a card marked in both sittings takes the later verdict (rv002: yes, then no)",
          "rv002" not in kept, str(kept))
    man = json.loads(hr.MANIFEST.read_text(encoding="utf-8")) if hr.MANIFEST.is_file() else {}
    check("the manifest counts every card marked in either sitting",
          man.get("review", {}).get("marked") == 5, str(man.get("review")))

    print("\n- the clean corpus goes beside the dev-set one, not over it -")
    dev_before = hr.CORPUS.read_bytes() if hr.CORPUS.is_file() else b""
    rc = _run("--collect", str(first), str(second), "--name", "code_heldout_v3")
    clean = _TMP / "code_heldout_v3.json"
    check("--name writes its own corpus file", rc == 0 and clean.is_file(), f"rc={rc}")
    check("and names the corpus inside it",
          clean.is_file() and json.loads(clean.read_text(encoding="utf-8")).get("name")
          == "code_heldout_v3")
    check("and its own manifest beside the dev-set one",
          (hr.MANIFEST.parent / "code_heldout_v3_review_manifest.json").is_file())
    check("the dev-set corpus keeps its bytes",
          (hr.CORPUS.read_bytes() if hr.CORPUS.is_file() else b"") == dev_before)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    test_the_second_sitting()
    print(f"\nheldout review resume: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
