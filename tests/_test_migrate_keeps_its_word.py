#!/usr/bin/env python3
"""An import stamps the record it came from, and a revert takes the note out of recall.

Two defects in the one module whose whole job is to be reversible.

* `apply()` paired `zip(stems, records)`. `api.remember_lessons` returns stems only for lessons it
  actually wrote - a lesson with no title, or one the injection gate refuses, is skipped, not
  raised - so one skip shortens `stems` and every later note is stamped with the PREVIOUS record's
  author, date and source reference. The provenance is not missing, which would be visible; it is
  wrong, which is not. `revert` then reads those stamps to decide what it may remove.
* `revert()` unlinked the markdown and called `rebuild_index()`, which rebuilds `Index.md`. The
  note's embedding vector stayed in the cache and its row stayed in the SQLite index, so a reverted
  note kept being retrieved and injected - with no file on disk to explain where it came from.
  `supersede_note` and the archive sweep both do this correctly; this path was written without them.

    python tests/_test_migrate_keeps_its_word.py
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

import migrate  # noqa: E402


def _rec(title: str, ref: str) -> dict:
    return {"type": "pattern", "title": title, "description": f"body of {ref}.",
            "prevention": "", "source": "generic", "author": f"author-{ref}",
            "created": "2026-01-01", "ref": ref}


#: The middle record is untitled, so `remember_lessons` skips it - the commonest way a batch of N
#: records becomes N-1 stems, and the one an importer cannot prevent from the outside.
RECORDS = [_rec("first imported lesson", "r1"), _rec("", "r2"), _rec("third imported lesson", "r3")]

migrate.PARSERS = dict(migrate.PARSERS)
migrate.PARSERS["generic"] = lambda path: list(RECORDS)

src = m.VAULT / "export.json"
src.write_text("[]", encoding="utf-8")

print("# every written note is stamped with the record it actually came from")
out = migrate.apply("generic", str(src), project="demo")
check("the import reports itself ok", out.get("ok") is not False, repr(out)[:200])
stems = out.get("stems") or [b for b in migrate._ledger_load() if b["id"] == out.get("batch")][0]["stems"]
check("one record was skipped, so two notes were written", len(stems) == 2, f"{len(stems)} stem(s)")

by_ref = {}
for stem in stems:
    prov = migrate.provenance(stem)
    by_ref[stem] = prov.get("source_ref")
    print(f"    {stem} <- {prov.get('source_ref')!r} / {prov.get('source_author')!r}")

titles = {stem: m.parse_typed_stem(stem)["slug"] for stem in stems}
for stem, ref in by_ref.items():
    want = "r1" if "first" in titles[stem] else "r3"
    check(f"the note from {want} carries {want}'s reference, not another record's",
          ref == want, f"stamped {ref!r}")
check("no record's reference is stamped on two notes", len(set(by_ref.values())) == len(by_ref),
      f"stamps {sorted(by_ref.values())}")
check("the skipped record's reference is on no note at all", "r2" not in set(by_ref.values()))

print("# a reverted note leaves recall, not just the filesystem")
batch = out.get("batch") or migrate._ledger_load()[-1]["id"]
live = stems[0]
cache = m.load_embed_cache()
cache[live] = [0.1] * 8
m.save_embed_cache(cache)
try:
    m.sync_scale_index(upsert=[live])
except Exception:                                      # noqa: BLE001 - index optional in sandbox
    pass
check("the note is in the embedding cache before the revert", live in m.load_embed_cache())

migrate.revert(batch, dry_run=False)
check("the markdown is gone", not any(
    (m.VAULT / f / f"{live}.md").exists() for f in m.TYPE_FOLDER.values()))
check("and the embedding vector is gone with it", live not in m.load_embed_cache(),
      "the vector survived the revert, so the note keeps being retrieved and injected with no "
      "file on disk to explain it")

print("# an exported field cannot write its own frontmatter")
#: `_stamp` spliced `rec['source']`, `rec['author']`, `rec['created']`, `rec['ref']` and the batch
#: id into YAML as bare scalars. A newline in any of them closes the line and opens a top-level
#: key of the export's choosing. `confidence` is the one with teeth: recall reads it, and a
#: maximum puts an imported note ahead of everything the owner wrote themselves. The values come
#: out of someone else's export file, which is the definition of untrusted.
CLEAN = _rec("imported lesson with an ordinary author", "r4")
POISON = _rec("imported lesson with a poisoned author", "r5")
POISON["author"] = "someone" + chr(10) + "confidence: 1.0"
#: Both in one batch, so the control is a note written by the same code path in the same second -
#: the earlier two are gone, the revert above took the whole batch with it.
migrate.PARSERS["generic"] = lambda path: [CLEAN, POISON]
out2 = migrate.apply("generic", str(src), project="demo")
stems2 = out2.get("stems") or []
check("both records were imported", len(stems2) == 2, repr(out2)[:200])


def _note_path(stem: str):
    return next((p for p in (m.VAULT / f / f"{stem}.md" for f in m.TYPE_FOLDER.values())
                 if p.exists()), None)


def _top_level_keys(text: str) -> set[str]:
    """The keys a YAML reader sees at the top level of the frontmatter.

    Asked structurally rather than by searching for the payload: after the fix the value is
    quoted and collapsed onto one line, so the characters `confidence: 1.0` are still IN the
    file - inside a scalar, where they are data. The question worth asking is whether the
    export opened a KEY, and the answer is the same for any payload it might have chosen.
    """
    head = text.split("\n---", 1)[0].lstrip("-").lstrip("\n")
    return {ln.split(":", 1)[0] for ln in head.split("\n")
            if ":" in ln and ln[:1].strip() and not ln.startswith(("-", " ", "\t"))}


if len(stems2) == 2:
    poisoned = _note_path(next(s for s in stems2 if "poisoned" in s))
    control = _note_path(next(s for s in stems2 if "ordinary" in s))
    check("both notes are on disk", poisoned is not None and control is not None)
    if poisoned is not None and control is not None:
        want = _top_level_keys(control.read_text(encoding="utf-8"))
        got = _top_level_keys(poisoned.read_text(encoding="utf-8"))
        check("the poisoned import opened no key a clean one does not",
              got == want, f"extra {sorted(got - want)}, missing {sorted(want - got)}")
        fm, _ = migrate._frontmatter(poisoned.read_text(encoding="utf-8"))
        check("so the reader sees no maximum confidence",
              str(fm.get("confidence", "")) != "1.0", f"confidence={fm.get('confidence')!r}")
        check("and the author itself is kept, on one line",
              "someone" in str(fm.get("source_author", "")),
              f"source_author={fm.get('source_author')!r}")

print()
print(f"migrate keeps its word: {P} passed, {F} failed")
sys.exit(1 if F else 0)
