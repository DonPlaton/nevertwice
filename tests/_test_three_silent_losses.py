#!/usr/bin/env python3
"""Three losses with no error path: a window that evicts what it exists to hold, a retraction that
comes back, and a ledger two processes overwrite.

* `collect_existing_titles` builds the dedup window the extractor is shown as "the day's own notes
  first, then the newest others fill the remainder". `other[-(max(0, TITLE_WINDOW - len(same))):]`
  is `other[-0:]` once the day has TITLE_WINDOW notes of its own - the WHOLE list, not none of it -
  and the trailing `window[-TITLE_WINDOW:]` then keeps the tail, which is all `other`. A busy day
  shows the extractor no note from that day at all, which is exactly when the window matters.
* `retrieve_cross_project` has no `_live_note_exists` filter. The main retrieval path added one
  with a comment saying a claim that rests on a glob is not a guarantee: an index row or a cached
  vector can outlive the file it describes. The cross-project path rests on the glob.
* `guards.py` writes the ledger with no vault lock anywhere in the module. `record_fired` and
  `forget_delivery` are read-modify-write over one JSON file on a path the hook runs before every
  Edit, Write, MultiEdit and Bash, so two tool calls in flight lose one of the two updates.

    python tests/_test_three_silent_losses.py
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
import _engine_source  # noqa: E402  the engine's text, one path for every suite

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

print("# a busy day still shows the extractor the notes it wrote that day")
DAY, OTHER = "2026-09-19", "2026-08-01"
folder = m.VAULT / m.TYPE_FOLDER["mistake"]
folder.mkdir(parents=True, exist_ok=True)
n = m.TITLE_WINDOW + 5
for i in range(n):                      # more same-day notes than the window holds
    (folder / f"{DAY}-demo-mistake-today-lesson-{i:03d}.md").write_text("x", encoding="utf-8")
for i in range(n):
    (folder / f"{OTHER}-demo-mistake-older-lesson-{i:03d}.md").write_text("x", encoding="utf-8")
m._TITLE_SLUGS.clear()

picked = m.collect_existing_titles("demo", for_date=DAY)["mistake"]
today = [s for s in picked if s.startswith("today-lesson")]
older = [s for s in picked if s.startswith("older-lesson")]
check("the window is not longer than its cap", len(picked) <= m.TITLE_WINDOW,
      f"{len(picked)} entries against a cap of {m.TITLE_WINDOW}")
check("the day's own notes are in it", bool(today),
      f"{len(today)} of the day's own, {len(older)} from other days - the day's notes were evicted "
      f"by the very branch meant to fill the REMAINDER after them")
check("and they are what fills it when the day has more than the cap",
      len(today) == len(picked), f"{len(today)} of {len(picked)}")

print("# ... while a quiet day still gets older notes to fill the remainder")
m._TITLE_SLUGS.clear()
for p in folder.glob(f"{DAY}-*"):
    p.unlink()
(folder / f"{DAY}-demo-mistake-only-one.md").write_text("x", encoding="utf-8")
m._TITLE_SLUGS.clear()
picked = m.collect_existing_titles("demo", for_date=DAY)["mistake"]
check("the day's one note is there", "only-one" in picked, repr(picked[:3]))
check("and older notes fill the rest", len([s for s in picked if s.startswith("older")]) > 0,
      repr(picked[:3]))

print("# a retracted note does not come back through the cross-project path")
#: `m.__file__` is the 3 KB loader and `_engine.py` is only the index over its parts; the
#: body itself is reassembled from them in one place, so this suite does not spell any of it.
src = _engine_source.SRC
seg = src[src.index("def retrieve_cross_project("):]
seg = seg[:seg.index("\ndef ", 1)]
check("cross-project retrieval filters on the note still existing",
      "_live_note_exists" in seg,
      "the main path filters and says a claim resting on a glob is not a guarantee; this path "
      "rests on the glob")

print("# a concurrent writer's change to the guard ledger survives our own write")
import guards as g  # noqa: E402

g.save_guards([{"id": "gA", "pattern": "a", "message": "m", "project": "demo", "fired": 0},
               {"id": "gB", "pattern": "b", "message": "m", "project": "demo", "fired": 0}])

#: What the race is: a caller loads the ledger, another process writes it, and the caller then
#: saves its own stale copy whole. The second write is not detected and not merged - it is simply
#: gone. This drives that order exactly.
mine = g.load_guards()                                   # the caller's copy, now stale-to-be
other = g.load_guards()
for row in other:
    if row["id"] == "gB":
        row["delivered_sessions"] = ["session-from-another-tool-call"]
g.save_guards(other)                                     # the concurrent write

g.record_fired(["gA"], guards=mine, session="my-session")

after = {row["id"]: row for row in g.load_guards()}
check("our own counter was recorded", after["gA"].get("fired") == 1,
      f"fired={after['gA'].get('fired')!r}")
check("and the other tool call's delivery record is still there",
      after["gB"].get("delivered_sessions") == ["session-from-another-tool-call"],
      f"gB carries {after['gB'].get('delivered_sessions')!r} - saving a stale copy whole "
      f"dropped a delivery, so that advisory is re-injected for the rest of the session")

print("# ... and the same holds for the PreCompact path that clears a delivery")
g.save_guards([{"id": "gA", "pattern": "a", "message": "m", "project": "demo",
                "delivered_sessions": ["s1"]},
               {"id": "gB", "pattern": "b", "message": "m", "project": "demo", "fired": 0}])
mine = g.load_guards()
other = g.load_guards()
for row in other:
    if row["id"] == "gB":
        row["fired"] = 7
g.save_guards(other)

g.forget_delivery("s1", guards=mine)
after = {row["id"]: row for row in g.load_guards()}
check("the delivery is forgotten", after["gA"].get("delivered_sessions") == [],
      repr(after["gA"].get("delivered_sessions")))
check("and the concurrent counter survived", after["gB"].get("fired") == 7,
      f"gB fired={after['gB'].get('fired')!r}")

print()
print(f"three silent losses: {P} passed, {F} failed")
sys.exit(1 if F else 0)
