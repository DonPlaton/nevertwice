#!/usr/bin/env python3
"""B3: capturing one note writes one note's worth of bytes, not the whole vector cache.

The premortem's N4 (2026-09-25): every capture parsed and rewrote the WHOLE JSON embedding cache,
primary and `.bak` - 115 MB on the owner's store, about 280 MB of writes per captured session, and
linear in the store (the auditor's probe: 13.0 MB at 500 notes, 51.8 MB at 2,000). The cache keeps
its format - the snapshot `.embeddings_cache.json` is what every existing store and reader has - and
gains an append-only journal beside it: a capture appends its records, and the journal is folded
into the snapshot once it passes max(4 MiB, a tenth of the snapshot). A journal is written against
one snapshot (its size, mtime and a hash of both ends); a journal whose snapshot was rewritten by
anything else is set aside, never replayed onto a snapshot it does not describe.

What this suite holds it to, the auditor's acceptance for B3:
  (1) a one-note save leaves the snapshot's bytes untouched, and what it appends does not grow
      with the store;
  (2) a cold reader sees the new note with its vector, and no old entry is lost;
  (3) a torn append, an interrupted fold and a foreign rewrite each cost at most the record in
      flight, never a prior entry or the `.bak`;
  (4) two writers in two processes, one after the other under the vault lock, both land.

    python tests/_test_embed_journal.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

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


def rec(i: int, dim: int = 1024) -> dict:
    """A cache entry of the real shape: a 1024-dim vector, as bge-m3 writes it."""
    return {"ntype": "mistake", "project": "b3", "title": f"note {i}", "desc": f"what went wrong {i}",
            "prevention": "", "recurrence": 1, "vec": [((i * 7 + j) % 97) / 97.0 for j in range(dim)]}


def fresh(n: int) -> Path:
    """A sandbox store with an n-entry snapshot on disk and nothing else."""
    d = make_sandbox(m, "b3_", offline=True)
    m._save_json_generations(m.EMBED_CACHE, json.dumps({f"s{i}": rec(i) for i in range(n)}), prev=False)
    cold()
    return d


def cold() -> None:
    """What a new process sees: no memo."""
    m._EMBED_CACHE_MEMO["sig"] = None
    m._EMBED_CACHE_MEMO["data"] = None


def journal() -> Path:
    return m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".journal")


def one_note_save(stem: str, i: int) -> None:
    cache = m.load_embed_cache()
    cache[stem] = rec(i)
    m.save_embed_cache(cache, put={stem: cache[stem]})


print("\n- (1) a one-note save appends one note, and the snapshot is not rewritten -")
grew = {}
for n in (500, 2000):
    d = fresh(n)
    snap_before = m.EMBED_CACHE.read_bytes()
    bak = m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".bak")
    bak_before = bak.read_bytes() if bak.exists() else None
    one_note_save("new", 999_999)
    check(f"N={n}: a one-note save leaves the snapshot's bytes untouched",
          m.EMBED_CACHE.read_bytes() == snap_before)
    check(f"N={n}: ... and the .bak's", (bak.read_bytes() if bak.exists() else None) == bak_before)
    grew[n] = journal().stat().st_size if journal().exists() else None
check("what one note appends does not grow with the store (N=2000 against N=500)",
      grew[500] is not None and grew[2000] is not None and grew[2000] <= 1.05 * grew[500],
      str(grew))
check("... and is about one record, not a snapshot",
      grew[500] is not None and grew[500] < 4 * len(json.dumps(rec(1))), str(grew))
try:
    import psutil  # noqa: PLC0415 - measured when the machine has it; the checks above hold without
    wb = {}
    for n in (500, 2000):
        fresh(n)
        io0 = psutil.Process().io_counters().write_bytes
        one_note_save("new", 999_999)
        wb[n] = psutil.Process().io_counters().write_bytes - io0
    check("bytes the process wrote for one note, at 4N against N, <= 1.2 (auditor's 1a)",
          wb[2000] <= 1.2 * max(wb[500], 1), str(wb))
except ImportError:
    print("  --   psutil not installed: the write_bytes ratio is not measured on this machine")

print("\n- (1b) amortised over a fold, the bytes written per note do not grow with the store -")
#: Bytes are counted where they are written: every snapshot write (the fold) is its text written
#: twice, primary and .bak; every append is what it added to the journal.
per_note = {}
for n in (500, 2000):
    fresh(n)
    written, notes, folds = [0], 0, [0]
    real_save, real_append = m._save_json_generations, m._journal_append

    def counting_save(path, text, prev=True):
        if Path(path) == m.EMBED_CACHE:
            written[0] += 2 * len(text.encode("utf-8"))
            folds[0] += 1
        return real_save(path, text, prev=prev)

    def counting_append(put, delete):
        before = journal().stat().st_size if journal().exists() else 0
        real_append(put, delete)
        written[0] += journal().stat().st_size - before

    with mock.patch.object(m, "_save_json_generations", counting_save), \
            mock.patch.object(m, "_journal_append", counting_append):
        while folds[0] == 0 and notes < 5000:
            one_note_save(f"k{notes}", 10_000 + notes)
            notes += 1
    per_note[n] = written[0] / max(notes, 1)
check("a fold happened within the run at both sizes (the amortisation is measured, not assumed)",
      all(v > 0 for v in per_note.values()), str(per_note))
check("bytes per note through one full fold cycle, at 4N against N, <= 1.5 (auditor's 1b)",
      per_note[2000] <= 1.5 * per_note[500], {k: round(v) for k, v in per_note.items()})

print("\n- (2) a cold reader sees the new note with its vector, and nothing old is lost -")
fresh(300)
one_note_save("new", 5)
cold()
c = m.load_embed_cache()
check("the new note is there, with its vector", (c.get("new") or {}).get("vec") == rec(5)["vec"])
check("every old entry is still there", all(f"s{i}" in c for i in range(300)) and len(c) == 301, str(len(c)))
cache = m.load_embed_cache()
cache.pop("s7")
m.save_embed_cache(cache, delete=["s7"])
cold()
check("a deletion goes through the journal too", "s7" not in m.load_embed_cache())
fresh(1)
cache = m.load_embed_cache()
cache.pop("s0")
check("deleting the LAST entry through the journal is not refused as an empty overwrite",
      m.save_embed_cache(cache, delete=["s0"]) is True)
cold()
check("... and the store reads empty afterwards", m.load_embed_cache() == {})
fresh(300)
one_note_save("new", 5)
cache = m.load_embed_cache()
cache.pop("s7")
m.save_embed_cache(cache, delete=["s7"])
cold()
child = subprocess.run(
    [sys.executable, "-c",
     "import sys, json; sys.path.insert(0, sys.argv[1]); import _env_guard; sys.path.insert(0, sys.argv[2]); "
     "import memory_hook as m; m._rebase_vault(sys.argv[3]); c = m.load_embed_cache(); "
     "print(json.dumps({'n': len(c), 'new': 'vec' in (c.get('new') or {}), 's7': 's7' in c}))",
     str(HERE), str(ROOT / "nevertwice"), str(m.VAULT)],
    capture_output=True, text=True, encoding="utf-8", timeout=300)
try:
    seen = json.loads(child.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    seen = {"error": child.stderr[-300:]}
check("a separate process reads the same cache: the new note, and not the deleted one",
      seen == {"n": 300, "new": True, "s7": False}, str(seen))

print("\n- (3) a torn append, an interrupted fold, a foreign rewrite -")
fresh(50)
one_note_save("a", 1)
with open(journal(), "ab") as fh:              # a crash mid-append: half a line, no newline
    fh.write(b'{"put": "torn", "rec": {"ntype": "mis')
cold()
c = m.load_embed_cache()
check("a torn last line costs that line, not the cache", "a" in c and "torn" not in c and len(c) == 51,
      str(len(c)))
one_note_save("b", 2)
cold()
c = m.load_embed_cache()
check("the next append lands whole after a torn tail", "a" in c and "b" in c and "torn" not in c)

fresh(3)                                        # small, so the second record triggers a fold
with mock.patch.object(m, "EMBED_JOURNAL_FOLD_MIN", 10 ** 9):
    one_note_save("a", 1)                       # appended, no fold yet
snap, bak = m.EMBED_CACHE.read_bytes(), m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".bak").read_bytes()
cache = m.load_embed_cache()
cache["b"] = rec(2)
folds = []


def _fold_dies(*a, **k):
    folds.append(1)
    raise OSError("disk full mid-fold")


with mock.patch.object(m, "EMBED_JOURNAL_FOLD_MIN", 1), mock.patch.object(m, "_save_json_generations", _fold_dies):
    ok = m.save_embed_cache(cache, put={"b": cache["b"]})
check("the fold was attempted (the scenario is real, not vacuous)", folds == [1], str(folds))
check("an interrupted fold still reports the record on disk - it is in the journal", ok is True, repr(ok))
check("... leaves the snapshot and the .bak exactly as they were",
      m.EMBED_CACHE.read_bytes() == snap
      and m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".bak").read_bytes() == bak)
cold()
c = m.load_embed_cache()
check("... and loses no record, the one in flight included", "a" in c and "b" in c and len(c) == 5, str(len(c)))

fresh(50)
one_note_save("ghost", 3)
m._save_json_generations(m.EMBED_CACHE, json.dumps({f"s{i}": rec(i) for i in range(40)}), prev=False)
cold()                                          # an older writer rewrote the snapshot underneath
c = m.load_embed_cache()
check("a journal written against another snapshot is not replayed onto this one",
      "ghost" not in c and len(c) == 40, str(len(c)))
stale = sorted(p.name for p in m.EMBED_CACHE.parent.glob(m.EMBED_CACHE.name + ".journal.stale-*"))
check("... it is set aside, named, not deleted", len(stale) == 1 and not journal().exists(), str(stale))

print("\n- the base catches a same-length rewrite of the middle, and appends are fsync'd -")
fresh(20)                                        # ~400 kB: its middle is outside both 64 KiB ends
one_note_save("ghost2", 7)
raw = bytearray(m.EMBED_CACHE.read_bytes())
mid = len(raw) // 2
i = next(j for j in range(mid, len(raw)) if chr(raw[j]).isdigit() and chr(raw[j]) != "7")
raw[i] = ord("7")                                # one digit, same length, both ends intact
m.EMBED_CACHE.write_bytes(bytes(raw))
cold()
check("a snapshot rewritten in its middle at the same length is still another snapshot (mtime)",
      "ghost2" not in m.load_embed_cache())
fresh(5)
synced = []
_real_fsync = os.fsync
with mock.patch.object(m.os, "fsync", lambda fd: (synced.append(fd), _real_fsync(fd))[1]):
    one_note_save("durable", 8)
check("an append is fsync'd before the save reports success", len(synced) >= 1, str(synced))

print("\n- the near-duplicate gate sees a note that only the journal holds -")
fresh(3)
_V = [1.0] + [0.0] * 7
with mock.patch.object(m, "embed_text", lambda *a, **k: list(_V)), \
        mock.patch.object(m, "embed_cache_usable", lambda: True), \
        mock.patch.object(m, "WRITE_DEDUP_MODE", "cosine"), mock.patch.object(m, "WRITE_DEDUP_SIM", 0.9):
    folder = m.VAULT / "Mistakes"
    folder.mkdir(parents=True, exist_ok=True)
    m._NDUP_PENDING.clear()
    m._near_duplicate_paths(folder, "b3", "mistake", "t", "d", "", set())      # primes its memo
    twin = "2026-09-25-b3-mistake-journal-only-twin"
    (folder / f"{twin}.md").write_text("# twin\n", encoding="utf-8")
    other = dict(m.load_embed_cache())           # another writer's dict, not the memo's own
    other[twin] = {"ntype": "mistake", "project": "b3", "title": "twin", "desc": "d", "vec": list(_V)}
    m.save_embed_cache(other, put={twin: other[twin]})
    m._NDUP_PENDING.clear()
    found = [p.stem for p in m._near_duplicate_paths(folder, "b3", "mistake", "t", "d", "", set())]
check("the near-duplicate memo refreshes on a journal append (keyed on snapshot + journal)",
      twin in found, str(found))

print("\n- emptying the cache by pops is written; a failed load's empty dict is still refused -")
fresh(2)
cache = m.load_embed_cache()                     # the memo's own dict, from a good snapshot
cache.pop("s0"), cache.pop("s1")
check("the loaded dict emptied by pops and saved WHOLE is written, not refused",
      m.save_embed_cache(cache) is True)
cold()
check("... and the vectors are gone from the disk (migrate.revert's last-entry case)",
      m.load_embed_cache() == {})
fresh(2)
check("a fresh {} - what a failed load hands back - still does not overwrite a non-empty cache",
      m.save_embed_cache({}) is False and len(m.load_embed_cache()) == 2)

print("\n- the journal is folded, and a failed save never leaves memory ahead of the disk -")
fresh(3)                                        # a record is more than a tenth of this snapshot
with mock.patch.object(m, "EMBED_JOURNAL_FOLD_MIN", 1):
    one_note_save("f", 4)
check("past the fold threshold the journal is folded into the snapshot and removed",
      not journal().exists() and "f" in json.loads(m.EMBED_CACHE.read_text(encoding="utf-8")))
fresh(50)
cache = m.load_embed_cache()
cache["lost"] = rec(9)
with mock.patch.object(m, "_journal_append", side_effect=OSError("disk full")):
    ok = m.save_embed_cache(cache, put={"lost": cache["lost"]})
check("F13: a save that did not reach the disk says so", ok is False, repr(ok))
check("F13: ... and the next load reads the disk, not the mutated memo", "lost" not in m.load_embed_cache())

fresh(50)
sig0 = m._embed_cache_sig()
one_note_save("n1", 6)
check("the cache signature moves when the journal does (memos keyed on it refresh)",
      m._embed_cache_sig() != sig0)

print("\n- (4) two writers, two processes, one after the other under the vault lock -")
fresh(20)
WRITER = ("import sys, json; sys.path.insert(0, sys.argv[1]); import _env_guard; sys.path.insert(0, sys.argv[2]); "
          "import memory_hook as m; m._rebase_vault(sys.argv[3]); assert m.acquire_lock(timeout_s=60); "
          "c = m.load_embed_cache(); c[sys.argv[4]] = {'ntype': 'mistake', 'project': 'b3', 'title': sys.argv[4], "
          "'vec': [0.5] * 8}; m.save_embed_cache(c, put={sys.argv[4]: c[sys.argv[4]]}); m.release_lock()")
for who in ("hook", "sweep"):
    subprocess.run([sys.executable, "-c", WRITER, str(HERE), str(ROOT / "nevertwice"), str(m.VAULT), who],
                   capture_output=True, text=True, timeout=300, check=False)
cold()
c = m.load_embed_cache()
check("both writers' records are on disk, and the snapshot's too",
      "hook" in c and "sweep" in c and len(c) == 22, str(sorted(k for k in c if not k.startswith("s"))))

print("\n- the files that name the cache name its journal -")
hooks_src = (ROOT / "nevertwice" / "_engine_hooks.py").read_text(encoding="utf-8")
install_src = (ROOT / "install.py").read_text(encoding="utf-8")
check("an auto-initialised store ignores the journal (_VAULT_GITIGNORE)",
      ".embeddings_cache.json.journal*" in m._VAULT_GITIGNORE, str(m._VAULT_GITIGNORE))
check("install.py's store .gitignore ignores it", '".embeddings_cache.json.journal*"' in install_src)
sys.path.insert(0, str(ROOT / "nevertwice"))
import store_version  # noqa: E402
check("store_version counts it with the expensive derived files",
      ".embeddings_cache.json.journal" in store_version.EXPENSIVE, str(store_version.EXPENSIVE))
import _golden_store  # noqa: E402
check("the golden store does not certify it (written only when an embedder answered)",
      ".embeddings_cache.json.journal" in _golden_store.DERIVED_FROM_EMBEDDER)

print(f"\nembed journal: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
