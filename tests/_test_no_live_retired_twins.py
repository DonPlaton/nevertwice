#!/usr/bin/env python3
"""A retired stem is never re-minted live under the same name.

_unique_path checked only the live folder, so a stem already retired into Superseded/
could be handed back for a new note. The vault then asserted BOTH "retired, use the
replacement" and "current" for one name — five basenames collided that way in the 2026-09
review, and every consumer keyed on stem picks one arbitrarily: recall's Superseded
filter, the embedding index, memory_search, and Obsidian's link resolution.

That defeats supersession, which is the one mechanism this store has that an ADD-only
competitor does not.
"""
import _env_guard  # noqa: F401
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


STEM = "2026-09-02-proj-pattern-a-lesson"

with tempfile.TemporaryDirectory() as td:
    folder = Path(td) / "Patterns"
    folder.mkdir()

    print("\n- a free stem is used as-is -")
    check("an empty folder yields the bare stem",
          m._unique_path(folder, STEM).name == f"{STEM}.md")

    print("\n- a RETIRED twin makes the stem taken -")
    (folder / "Superseded").mkdir()
    (folder / "Superseded" / f"{STEM}.md").write_text("retired", encoding="utf-8")
    got = m._unique_path(folder, STEM)
    check("the retired name is not handed back", got.name != f"{STEM}.md", got.name)
    check("a distinct suffix is used instead", got.name == f"{STEM}-2.md", got.name)
    check("and the live folder really is still empty",
          not (folder / f"{STEM}.md").exists())

    print("\n- a live twin still makes it taken (unchanged behaviour) -")
    (folder / f"{STEM}-2.md").write_text("live", encoding="utf-8")
    check("the next free suffix is chosen",
          m._unique_path(folder, STEM).name == f"{STEM}-3.md")

    print("\n- Archive/ is deliberately NOT a collision -")
    arch_stem = "2026-09-02-proj-pattern-archived-lesson"
    (folder / "Archive").mkdir()
    (folder / "Archive" / f"{arch_stem}.md").write_text("archived", encoding="utf-8")
    check("an archived note does not block its name",
          m._unique_path(folder, arch_stem).name == f"{arch_stem}.md")

print(f"\nlive/retired twins: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
