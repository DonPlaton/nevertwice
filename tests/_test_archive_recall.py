#!/usr/bin/env python3
"""B2: an archived note is old, not retracted - recall still finds it.

K46 (campaign v2): a stand fed sessions dated January to May, and `archive_old_typed` - which
counts ninety days back from the machine's clock - moved every note it wrote into `Archive/` and
dropped it from the vector cache and the SQLite index in the same pass. Recall reads candidates
from those two only, so the stand measured an empty context for 420 of 420 questions. The same
happens to anyone importing old transcripts, and to every lesson older than ninety days on a live
store: the engine's own rule says the opposite (`_live_note_exists`: "Archive/ is age, not
retraction ... must still be recallable"), and the archive pass contradicted it.

Now the age pass moves the FILE (the live folders stay small, which is what it was for) and keeps
the note's cache entry and index row, re-keyed when a name collision renames the file and marked
`archived`. Ranking already ages old notes (`_salience_mult`: half-life 365 days, floor 0.5 - no
new parameter). What the CONSOLIDATOR archives (a merged duplicate, a note over the opt-in
per-project cap) is a different decision and still leaves recall.

    python tests/_test_archive_recall.py
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

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


OLD = "2020-01-10"                     # far outside any window, whatever the machine's clock says
PROJECT = "archproj"


def note(d: Path, stem: str, title: str, body: str) -> Path:
    p = d / "Mistakes" / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: mistake\nproject: {PROJECT}\n---\n# {title}\n\n{body}\n", encoding="utf-8")
    return p


def cold() -> None:
    m._EMBED_CACHE_MEMO["sig"] = None
    m._EMBED_CACHE_MEMO["data"] = None


def recalled(query: str) -> list:
    return [h["stem"] for h in m.retrieve_relevant(PROJECT, query, k=5, recency_fallback=False)]


print("\n- an age-archived note stays recallable -")
d = make_sandbox(m, "b2_", offline=True)
stem = f"{OLD}-{PROJECT}-mistake-pgbouncer-drops-prepared-statements"
note(d, stem, "pgbouncer drops prepared statements",
     "transaction pooling in pgbouncer silently drops server-side prepared statements")
m.update_embeddings([(stem, "mistake", PROJECT, "pgbouncer drops prepared statements",
                      "transaction pooling in pgbouncer silently drops server-side prepared statements", "")])
check("before archiving, recall finds it", stem in recalled("pgbouncer prepared statements"))
moved = m.archive_old_typed()
check("the age pass moves the file into Archive/",
      moved == 1 and (d / "Mistakes" / "Archive" / f"{stem}.md").exists()
      and not (d / "Mistakes" / f"{stem}.md").exists(), str(moved))
cold()
entry = m.load_embed_cache().get(stem)
check("its cache entry stays, marked archived", isinstance(entry, dict) and entry.get("archived") is True,
      str(entry)[:120])
check("and recall still finds it from the JSON cache", stem in recalled("pgbouncer prepared statements"))
m.ensure_scale_index()
check("and from the SQLite index", stem in [s for s, _ in (m._scale_candidates(PROJECT, cross=False,
                                                                                  query="pgbouncer") or [])])

print("\n- a collision-renamed archive keeps its entry under its new name -")
d = make_sandbox(m, "b2c_", offline=True)
stem = f"{OLD}-{PROJECT}-mistake-redis-eviction-policy"
(d / "Mistakes" / "Archive").mkdir(parents=True)
(d / "Mistakes" / "Archive" / f"{stem}.md").write_text("an older note of the same name\n", encoding="utf-8")
note(d, stem, "redis eviction policy", "allkeys-lru evicted the session keys under memory pressure")
m.update_embeddings([(stem, "mistake", PROJECT, "redis eviction policy",
                      "allkeys-lru evicted the session keys under memory pressure", "")])
m.archive_old_typed()
cold()
cache = m.load_embed_cache()
check("the entry follows the file to its collision-safe name",
      f"{stem}-2" in cache and stem not in cache and (d / "Mistakes" / "Archive" / f"{stem}-2.md").exists(),
      str(sorted(k for k in cache if "redis" in k)))
check("and recall finds it by that name", f"{stem}-2" in recalled("redis eviction allkeys-lru session keys"))

print("\n- a note the consolidator archives as a merged duplicate still leaves recall -")
check("the consolidator's dedup path still pops what it archives (a different decision)",
      "cache.pop(src.stem, None)" in (ROOT / "nevertwice" / "consolidate_memory.py").read_text(encoding="utf-8"))

print("\n- embed_index re-embeds archived notes, not merged duplicates -")
d = make_sandbox(m, "b2e_", offline=True)
live_old = f"{OLD}-{PROJECT}-mistake-lost-before-this-fix"
dup = f"{OLD}-{PROJECT}-mistake-a-merged-duplicate"
arch = d / "Mistakes" / "Archive"
arch.mkdir(parents=True)
(arch / f"{live_old}.md").write_text("---\ntype: mistake\n---\n# lost before this fix\n\narchived under the old rule\n",
                                     encoding="utf-8")
(arch / f"{dup}.md").write_text("---\ntype: mistake\nduplicate_of: somewhere\n---\n# a merged duplicate\n\nx\n",
                                encoding="utf-8")
import embed_index  # noqa: E402
embed_index.m = m
embed_index._run_embed(False)
cold()
cache = m.load_embed_cache()
check("an archived note dropped by the old rule is back in the cache (text-only without an embedder)",
      live_old in cache and cache[live_old].get("archived") is True, str(sorted(cache))[:200])
check("a merged duplicate is not", dup not in cache)

print(f"\narchive recall: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
