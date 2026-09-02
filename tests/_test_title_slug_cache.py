#!/usr/bin/env python3
"""One cache, three writers, one shape - or the second session of a sweep dies.

`_TITLE_SLUGS` grounds the extractor's dedup: it holds, per project and per note type, the
title slugs that already exist. On 2026-09-02 the window became date-aware so a re-mined
session sees its own day first, and `collect_existing_titles` started storing `(date, slug)`
pairs. The two other writers were not moved with it, and each broke in a different direction:

* `register_written_notes` kept appending a bare slug string. The next call with `for_date`
  unpacked it as a pair - `ValueError: too many values to unpack (expected 2)` - which took
  down the *second* session for a project in any single process: a sweep, `ingest.py`, the
  watch daemon, and `capture_session` twice in a row.
* `_unregister_slug` kept testing `slug in bucket` against a list of pairs, which is always
  False. It removed nothing, silently, so a note that had just been retired was still shown
  to the extractor as an existing title - the exact grounding error it exists to prevent.

The crash is the loud half and the silent half is the dangerous one, so both are pinned here.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


TODAY, EARLIER = "2026-09-02", "2026-08-01"
PROJ = "slugcache"


def _write(date, slug, ntype="pattern"):
    d = m.VAULT / m.TYPE_FOLDER[ntype]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date}-{PROJ}-{ntype}-{slug}.md"
    p.write_text(f"---\ndate: {date}\nproject: {PROJ}\ntype: {ntype}\n---\n\n{slug}\n",
                 encoding="utf-8")
    return p.stem


print("\n- a cache populated from disk holds (date, slug) pairs -")
old_stem = _write(EARLIER, "chose-cerebras-for-extraction")
m._TITLE_SLUGS.clear()
win = m.collect_existing_titles(PROJ, for_date=TODAY)
check("the note on disk is in the window", "chose-cerebras-for-extraction" in win["pattern"],
      str(win["pattern"]))
rows = m._TITLE_SLUGS[PROJ]["pattern"]
check("stored as a pair, not a bare slug",
      all(isinstance(r, tuple) and len(r) == 2 for r in rows), str(rows))

print("\n- the second session of a sweep does not crash on the first session's writes -")
new_stem = _write(TODAY, "switched-extraction-to-local-ollama")
m.register_written_notes(PROJ, ["extraction"], {"pattern": [new_stem]})
try:
    win2 = m.collect_existing_titles(PROJ, for_date=TODAY)
    crashed = ""
except Exception as e:                                   # the historical failure
    win2, crashed = {"pattern": ()}, f"{type(e).__name__}: {e}"
check("collect_existing_titles survives a registered write", not crashed, crashed)
check("the freshly written slug is visible to the next session",
      "switched-extraction-to-local-ollama" in win2["pattern"], str(win2["pattern"]))
check("and the day's own note leads the window",
      win2["pattern"] and win2["pattern"][0] == "switched-extraction-to-local-ollama",
      str(win2["pattern"]))

print("\n- retiring a note actually removes it from the grounding window -")
m._unregister_slug(old_stem)
win3 = m.collect_existing_titles(PROJ, for_date=TODAY)
check("the retired slug is gone", "chose-cerebras-for-extraction" not in win3["pattern"],
      str(win3["pattern"]))
check("the current one survives the removal",
      "switched-extraction-to-local-ollama" in win3["pattern"], str(win3["pattern"]))

print("\n- a re-statement of a retired title re-registers it -")
again = _write(TODAY, "chose-cerebras-for-extraction")
m.register_written_notes(PROJ, [], {"pattern": [again]})
win4 = m.collect_existing_titles(PROJ, for_date=TODAY)
check("re-stated title is grounded again",
      "chose-cerebras-for-extraction" in win4["pattern"], str(win4["pattern"]))
check("and is not duplicated",
      [sl for _d, sl in m._TITLE_SLUGS[PROJ]["pattern"]].count(
          "chose-cerebras-for-extraction") == 1,
      str(m._TITLE_SLUGS[PROJ]["pattern"]))

print("\n- an unknown stem is ignored rather than raising -")
try:
    m._unregister_slug("not-a-typed-stem")
    m.register_written_notes(PROJ, [], {"pattern": ["also-not-one"]})
    ok = True
except Exception as e:
    ok, detail = False, f"{type(e).__name__}: {e}"
check("malformed stems are survivable", ok, "" if ok else detail)

print(f"\ntitle slug cache: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
