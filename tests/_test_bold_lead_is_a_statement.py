#!/usr/bin/env python3
"""A lesson that starts in bold is still a lesson, and is not retired for saying nothing.

`_parse_note_body` picks the description as the first plain line after the heading, excluding lines
that start with any of `** # - _ --- [[ |`. The `**` in that list is meant for the note's structural
bold lines - `**Prevention:**`, `**Project:**`, `**Date:**` - but it matches ANY bold lead, and a
lesson whose first sentence opens with emphasis ("**Never** commit on a red suite") is ordinary
prose, not markup.

Such a note reads back with `desc == ""`. `_same_replacement` then reaches its empty-old-statement
shortcut - "an EMPTY old statement with no facts carries nothing to preserve" - and returns
`(True, "restated")`, so the next note with the same slug ABSORBS it unconditionally. The note did
carry something to preserve; the parser would not read it.

    python tests/_test_bold_lead_is_a_statement.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


make_sandbox(m, offline=True)

BOLD_LEAD = "**Never** commit on a red suite - the register stamps the commit that produced it."

print("# the parser reads a bold-leading sentence as the description")
body = ["# a lesson about commits", "", BOLD_LEAD, "",
        "**Prevention:** run pytest after git add", "",
        "**Project:** [[demo]]", "**Date:** 2026-09-19", "", "#project/demo #mistake"]
title, desc, prevention = m._parse_note_body(body)
check("the title is read", title == "a lesson about commits", repr(title))
check("the bold-leading sentence is the description", desc == BOLD_LEAD, repr(desc))
check("and the prevention line is still the prevention", prevention == "run pytest after git add",
      repr(prevention))

print("# ... while the structural bold lines are still not mistaken for it")
for stray in ("**Prevention:** x", "**Project:** [[demo]]", "**Date:** 2026-09-19",
              "**Как избежать:** y"):
    _, d, _ = m._parse_note_body(["# t", "", stray])
    check(f"{stray.split(':')[0]}: not read as a description", d == "", repr(d))

print("# a note that says something is not absorbed as if it said nothing")
m.write_typed_note("Mistakes", {"title": "a lesson about commits", "description": BOLD_LEAD},
                   "demo", "2026-09-19", [], "mistake")
old = m.VAULT / "Mistakes" / "2026-09-19-demo-mistake-a-lesson-about-commits.md"
check("the note was written", old.exists())
_, on_disk, _ = m._parse_note_body(old.read_text(encoding="utf-8").split("\n"))
check("and reads back with its statement intact", on_disk == BOLD_LEAD, repr(on_disk))

ok, rule = m._same_replacement(old, "a lesson about commits",
                               "Commits are fine now, nothing to worry about.", "", "")
check("an unrelated later note does not absorb it as 'restated'",
      not (ok and rule == "restated"),
      f"returned ({ok!r}, {rule!r}) - the still-true statement would be overwritten")

print("# and a genuinely empty old statement still takes the shortcut it was written for")
m.write_typed_note("Mistakes", {"title": "bare title", "description": ""},
                   "demo", "2026-09-19", [], "mistake")
bare = m.VAULT / "Mistakes" / "2026-09-19-demo-mistake-bare-title.md"
ok, rule = m._same_replacement(bare, "bare title", "now it has a description", "", "")
check("an actually empty note is still 'restated'", ok and rule == "restated", f"({ok!r}, {rule!r})")

print()
print(f"bold lead is a statement: {P} passed, {F} failed")
sys.exit(1 if F else 0)
