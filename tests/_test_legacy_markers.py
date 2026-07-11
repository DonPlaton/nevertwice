#!/usr/bin/env python3
"""The 2.2.1 language migration contract: the engine now writes English markers, and
every marker a pre-2.2.1 (Russian-marker) store already contains keeps being read
forever - the store is the user's data and is never migrated.

Pinned here:
  - a legacy note's `**Как избежать:**` line still yields its prevention;
  - a new note carries `**Prevention:**` and parses identically;
  - a legacy Context with `## Накопленное состояние` still splits/compacts correctly,
    and a freshly compacted block is written with the English header;
  - the legacy consolidation headers are recognized so re-runs don't duplicate blocks."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import memory_hook as m          # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    print(("  ok  " if cond else "  FAIL ") + name)
    P += 1 if cond else 0
    F += 0 if cond else 1


def test_prevention_marker_both_generations():
    legacy = ["# ⚠️ Старая заметка", "", "Описание бага.", "",
              "**Как избежать:** пиши через tmp + os.replace", ""]
    title, desc, prevention = m._parse_note_body(legacy)
    check("legacy RU prevention still parsed", prevention == "пиши через tmp + os.replace")
    modern = ["# ⚠️ New note", "", "What went wrong.", "",
              "**Prevention:** write via tmp + os.replace", ""]
    _, _, prev2 = m._parse_note_body(modern)
    check("new EN prevention parsed", prev2 == "write via tmp + os.replace")


def test_new_notes_write_english_markers():
    with tempfile.TemporaryDirectory() as t:
        with mock.patch.object(m, "VAULT", Path(t)), \
             mock.patch.object(m, "git_autocommit"):
            m.collect_existing_titles.cache_clear()
            stem = m.write_typed_note("Mistakes", {"title": "CUDA OOM at 64",
                                                   "description": "ran out of VRAM",
                                                   "prevention": "lower the batch"},
                                      "proj", "2026-07-11", ["t"], "mistake")
            text = (Path(t) / "Mistakes" / f"{stem}.md").read_text(encoding="utf-8")
        check("new note uses **Prevention:**", "**Prevention:** lower the batch" in text)
        check("no Russian markers in a new note",
              "Как избежать" not in text and "Проект:" not in text)


def test_legacy_context_still_splits_and_compacts_english():
    legacy_ctx = ("---\nproject: p\ntype: context\n---\n\n# Context: p\n\nseed line\n\n"
                  "## Накопленное состояние (сжато)\n\nold compacted knowledge\n\n"
                  "## 2026-05-01 10:00\nentry one\n\n## 2026-05-02 10:00\nentry two\n")
    head, entries = m._split_context(legacy_ctx)
    check("legacy state header starts the entry list",
          len(entries) == 3 and entries[0].startswith("## Накопленное состояние"))
    check("seed head preserved", "seed line" in head and "## 2026" not in head)

    # a compaction over a legacy store writes the ENGLISH state header
    with tempfile.TemporaryDirectory() as t:
        ctx_dir = Path(t) / "Context"
        ctx_dir.mkdir(parents=True)
        fp = ctx_dir / "p.md"
        entries_txt = "\n\n".join(
            f"## 2026-05-{i+1:02d} 10:00\nnote {i}. " + "x" * 1400 for i in range(20))
        fp.write_text(legacy_ctx.split("## 2026-05-01")[0] + entries_txt + "\n",
                      encoding="utf-8")
        with mock.patch.object(m, "VAULT", Path(t)), \
             mock.patch.object(m, "generate_json",
                               lambda *a, **k: {"state": "SUMMARY " + "z" * 120}), \
             mock.patch.object(m, "git_autocommit"):
            m.compact_context_if_needed(fp, "p")
        out = fp.read_text(encoding="utf-8")
        check("compaction writes the English state header",
              "## Accumulated state (compacted)" in out)
        check("legacy RU state text was absorbed, not duplicated",
              out.count("## Накопленное состояние") <= 1)


if __name__ == "__main__":
    print("=== legacy-marker compat self-checks ===")
    test_prevention_marker_both_generations()
    test_new_notes_write_english_markers()
    test_legacy_context_still_splits_and_compacts_english()
    print(f"\nlegacy-markers: {P} passed, {F} failed")
    sys.exit(1 if F else 0)
