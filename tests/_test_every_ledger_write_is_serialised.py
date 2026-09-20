#!/usr/bin/env python3
"""Every write to the guard ledger is one writer, and says so when it cannot be.

`guards._persist_under_lock` exists because a read-modify-write over one JSON file loses the
other writer's change: the caller loads the ledger, somebody else writes it, and the caller then
saves its own stale copy whole. Two of the module's writers - `record_fired` and
`forget_delivery` - go through that door. The lifecycle writer and every inbox action did not:

* `guards.feedback` is the LIFECYCLE write - an outcome, a promotion, a demotion - and it saved
  the caller's copy whole;
* `inbox.retire`, `inbox.edit` and `inbox._persist` (reached by `approve(promote=True)`) each
  load, mutate and save with no lock;
* `inbox.confirm` writes a human decision into a live note's frontmatter with no vault lock,
  while every other note writer in the store takes it.

Two properties, both measured on the effect and not on the source:

1. **The write happens while this process holds the lock.** Sampled INSIDE `save_guards`, by
   reading the lock file: the guarantee is mutual exclusion, and the only way to have it is to
   be holding the lock at the moment the bytes go down. A first draft of this suite asserted
   instead that a concurrent change to another guard survives - and passed before the fix,
   because these writers already re-load the ledger themselves. It measured nothing.
2. **A busy lock is reported, not ignored.** With the lock unavailable, the surface says nothing
   was recorded and the ledger on disk is untouched - the opposite of claiming a write that
   never happened.

    python tests/_test_every_ledger_write_is_serialised.py
"""
from __future__ import annotations

import json
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
        print("  ok   " + label)
    else:
        F += 1
        print("  FAIL " + label + (" - " + detail if detail else ""))


make_sandbox(m, offline=True)
import guards as G  # noqa: E402
import inbox as IB  # noqa: E402

def seed() -> None:
    G.save_guards([
        {"id": "gA", "pattern": "a", "message": "first", "scope": {"project": "demo"},
         "status": "advisory", "born_from": [], "born_date": "2026-05-01", "corroborations": 0,
         "fired": 0, "helped": 0, "false_positives": 0, "seen_sessions": [], "overrides": []},
        {"id": "gB", "pattern": "b", "message": "second", "scope": {"project": "demo"},
         "status": "advisory", "born_from": [], "born_date": "2026-05-01", "corroborations": 0,
         "fired": 0, "helped": 0, "false_positives": 0, "seen_sessions": [], "overrides": []},
    ])


def ledger() -> dict:
    return {row["id"]: row for row in G.load_guards()}


def _held_by_us() -> bool:
    """Is the vault lock held by THIS process right now? The file carries the holder's pid."""
    try:
        import os
        return (m._lock_file().read_text() or "").strip() == str(os.getpid())
    except OSError:
        return False


def under_lock(label: str, run, landed) -> None:
    """Run a writer and sample the lock at the moment it writes."""
    seed()
    held: list[bool] = []
    real_save = G.save_guards

    def sampling_save(rows):
        held.append(_held_by_us())
        real_save(rows)

    G.save_guards = sampling_save
    try:
        run()
    finally:
        G.save_guards = real_save
    after = ledger()
    check(label + ": the change landed", landed(after), str(after.get("gA")))
    check(label + ": it wrote the ledger", bool(held), "save_guards was never reached")
    check(label + ": and every write happened while we held the lock",
          bool(held) and all(held), str(held))


print("# every ledger write happens under the vault lock")
under_lock("feedback",
           lambda: G.feedback("gA", "accepted", session_id="s1"),
           lambda after: after["gA"]["outcomes"]["counts"]["accepted"] == 1)
under_lock("inbox.retire",
           lambda: IB.retire("gA", reason="no longer true"),
           lambda after: after["gA"]["status"] == "retired")
under_lock("inbox.edit",
           lambda: IB.edit("gA", "a clearer sentence"),
           lambda after: after["gA"]["message"] == "a clearer sentence")
under_lock("inbox.approve(promote)",
           lambda: IB.approve("gA", session_id="s1", promote=True, reason="operator"),
           lambda after: after["gA"]["status"] == "blocking")

print("# a busy lock is reported, and nothing is written")
real_acquire = m.acquire_lock
m.acquire_lock = lambda *a, **k: False
try:
    for name, call in (("inbox.retire", lambda: IB.retire("gA", reason="r")),
                       ("inbox.edit", lambda: IB.edit("gA", "another sentence")),
                       ("inbox.approve(promote)",
                        lambda: IB.approve("gA", session_id="s1", promote=True)),
                       ("inbox.override", lambda: IB.override("gA", "because"))):
        seed()
        before = json.dumps(G.load_guards(), sort_keys=True)
        res = call()
        check(name + ": says nothing was recorded", res.get("ok") is False, str(res))
        check(name + ": and the ledger on disk is untouched",
              json.dumps(G.load_guards(), sort_keys=True) == before)
finally:
    m.acquire_lock = real_acquire

print("# and a human decision written into a note takes the same lock")
m.write_typed_note("Mistakes", {"title": "a stale lesson", "description": "It was true once."},
                   "demo", "2026-01-01", [], "mistake")
stem = next(n["stem"] for n in m._iter_all_notes() if "stale-lesson" in n["stem"])
path = m.VAULT / "Mistakes" / (stem + ".md")
before_bytes = path.read_bytes()
m.acquire_lock = lambda *a, **k: False
try:
    res = IB.confirm(stem)
finally:
    m.acquire_lock = real_acquire
check("inbox.confirm says nothing was recorded", res.get("ok") is False, str(res))
check("and the note is byte-identical", path.read_bytes() == before_bytes)
res = IB.confirm(stem)
check("while a free lock still records the review", res.get("ok") is True, str(res))
check("and the note carries it", "reviewed:" in path.read_text(encoding="utf-8"))

print()
print("every ledger write is serialised: " + str(P) + " passed, " + str(F) + " failed")
sys.exit(1 if F else 0)
