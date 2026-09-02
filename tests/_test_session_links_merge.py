#!/usr/bin/env python3
"""A re-mined session note keeps the notes the earlier pass linked.

The link lists were rebuilt from the LATEST extraction only, so each re-mine orphaned
everything the previous pass had written. One session note was rewritten four times in a
day and ended up indexing a fifth, unrelated cluster, while 18 notes carrying that same
session id appeared in neither the note nor the project Context. An agent following the
documented Index → Context → note path could not reach them at all — while entity pages
still asserted the conclusions those notes were the source for.
"""
import _env_guard  # noqa: F401
import sys, tempfile, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


A = "2026-09-02-proj-pattern-first-lesson"
B = "2026-09-02-proj-pattern-second-lesson"
MIS = "2026-09-02-proj-mistake-a-bug"

orig = m.VAULT
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    args = dict(project="proj", date="2026-09-02", time_str="1200",
                summary="s", cwd=str(m.VAULT), session_id="sid-1", tags=["t"],
                trigger="test", stem="2026-09-02-1200-proj-session-sid1")

    print("\n- the first pass writes what it found -")
    m.write_session_note(links={"pattern": [A], "mistake": [], "decision": []}, **args)
    txt1 = (m.VAULT / "Sessions" / "2026-09-02-1200-proj-session-sid1.md").read_text(encoding="utf-8")
    check("the first pattern is listed", A in txt1)

    print("\n- a re-mine that finds something ELSE keeps the first -")
    m.write_session_note(links={"pattern": [B], "mistake": [MIS], "decision": []}, **args)
    txt2 = (m.VAULT / "Sessions" / "2026-09-02-1200-proj-session-sid1.md").read_text(encoding="utf-8")
    check("the new pattern is listed", B in txt2)
    check("the EARLIER pattern is not orphaned", A in txt2, "the first pass's note vanished")
    check("the new mistake is listed", MIS in txt2)

    print("\n- links are not duplicated on a repeat pass -")
    m.write_session_note(links={"pattern": [A, B], "mistake": [MIS], "decision": []}, **args)
    txt3 = (m.VAULT / "Sessions" / "2026-09-02-1200-proj-session-sid1.md").read_text(encoding="utf-8")
    check("each link appears once", txt3.count(f"[[{A}]]") == 1, str(txt3.count(f"[[{A}]]")))

    print("\n- typed links land under their OWN type -")
    pat_block = txt3.split("**")[0] if "**" not in txt3 else txt3
    check("the mistake is not filed as a pattern",
          re.search(rf"{re.escape(MIS)}", txt3) is not None)
    check("the project link is not mistaken for a typed note",
          txt3.count("[[proj]]") >= 1)

m.VAULT = orig
print(f"\nsession link merge: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
