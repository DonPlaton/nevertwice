#!/usr/bin/env python3
"""A retracted note never comes back, and that is now enforced rather than incidental.

This store's central claim against an ADD-only competitor is that a superseded fact stops
being returned. Mem0 2.0.19, measured live on 2026-08-30, returns the retracted fact FIRST
by design -- UPDATE and DELETE were removed in April 2026 and both facts survive on
purpose.

Here the exclusion held only STRUCTURALLY: live folders are flat-globbed and Superseded/
is a subdirectory. But a hit reaches a person from an INDEX, not from the folder, so a
stale SQLite row or a cached vector that outlived the file could still surface a retired
note -- and the 2026-09 review found five basenames existing live and retired at once,
which defeated the filter entirely.

A claim that rests on a glob is not a guarantee. One stat per delivered hit makes it one.
"""
import _env_guard  # noqa: F401
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402
import _engine_source  # noqa: E402  the engine's text, one path for every suite

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


STEM = "2026-09-02-proj-mistake-a-retracted-fact"
orig = m.VAULT
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    folder = m.VAULT / m.TYPE_FOLDER["mistake"]
    folder.mkdir(parents=True)

    print("\n- a live note is delivered -")
    (folder / f"{STEM}.md").write_text("live", encoding="utf-8")
    check("a note present in its folder is live", m._live_note_exists(STEM, "mistake") is True)

    print("\n- once retired, it is not -")
    (folder / "Superseded").mkdir()
    (folder / f"{STEM}.md").rename(folder / "Superseded" / f"{STEM}.md")
    check("a retired note is refused even though an index row could still name it",
          m._live_note_exists(STEM, "mistake") is False)

    print("\n- recall degrades toward showing too much, never toward hiding -")
    check("an unknown ntype is kept, not dropped",
          m._live_note_exists(STEM, "not-a-type") is True)
    check("an empty stem is kept rather than silently filtered",
          m._live_note_exists("", "mistake") is True)

    print("\n- the guarantee is applied where hits are built -")
    src = _engine_source.SRC
    check("retrieve_relevant filters its hits", "_live_note_exists(_h.get(" in src)
    check("the filter runs on every retrieval path, not one caller",
          src.count("_live_note_exists(") >= 2)

m.VAULT = orig
print(f"\nsupersession guarantee: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
