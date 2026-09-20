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

print("# a generating pass does not overwrite decisions made while it ran")
# `generate_from_vault` loads the ledger, spends up to ~33 s per mistake in the model, and then
# saves its own snapshot whole. Its window is therefore the WHOLE loop - 6105 s for a
# 185-mistake backlog - and every decision recorded inside it was overwritten on the way out.
# This is the one writer for which "a concurrent change survives" IS the measurement: the
# others re-load the ledger themselves, which is why that same assertion measured nothing when
# this suite first tried it on them. `main()`'s `pack` has the same shape over a shorter window.
for title in ("a repeated slip", "another repeated slip"):
    m.write_typed_note("Mistakes", {"title": title, "description": "It happened twice.",
                                    "prevention": "Do not do it again."},
                       "demo", "2026-01-01", [], "mistake")

minted: list[int] = []


def _propose(note, use_llm=True):
    """Stands in for the model call the pass spends its minutes in - the window a decision
    arrives in."""
    minted.append(1)
    n = len(minted)
    if n == 1:
        G.feedback("gA", "accepted", session_id="s-during-the-pass")
    return {"id": "gen" + str(n), "pattern": "zzz" + str(n), "message": "generated",
            "scope": {"project": "demo"}, "status": "advisory",
            "born_from": [note.get("stem", "")], "born_date": "2026-05-01",
            "corroborations": 0, "fired": 0, "helped": 0, "false_positives": 0,
            "seen_sessions": [], "overrides": []}


seed()
real_propose = G.propose_from_mistake
G.propose_from_mistake = _propose
try:
    added = G.generate_from_vault(use_llm=False)
finally:
    G.propose_from_mistake = real_propose
after = ledger()
check("the pass adds the guards it generated", added == 2 and "gen1" in after and "gen2" in after,
      str(sorted(after)))
check("and the decision recorded while it ran survives the pass",
      ((after.get("gA") or {}).get("outcomes") or {}).get("counts", {}).get("accepted") == 1,
      json.dumps((after.get("gA") or {}).get("outcomes"), ensure_ascii=False))

G.propose_from_mistake = _propose
try:
    under_lock("generate_from_vault",
               lambda: G.generate_from_vault(use_llm=False),
               lambda a: "gen3" in a or "gen4" in a)
finally:
    G.propose_from_mistake = real_propose

print("# and the pass reports what LANDED, not what its snapshot would have added")
# The merge runs against the ledger as it is at write time, so another writer can install part
# of what this pass minted while the pass was still in the model. `register` dedups by id and
# says so by returning False; counting the in-memory snapshot instead reports guards this call
# did not add. Same defect, same shape, as `main()`'s `pack` - fixed there in this batch.


def _propose_racing(note, use_llm=True):
    minted.append(1)
    n = len(minted)
    g = {"id": "race" + str(n), "pattern": "yyy" + str(n), "message": "generated",
         "scope": {"project": "demo"}, "status": "advisory",
         "born_from": [note.get("stem", "")], "born_date": "2026-05-01",
         "corroborations": 0, "fired": 0, "helped": 0, "false_positives": 0,
         "seen_sessions": [], "overrides": []}
    if n == 2:                       # another writer installs the FIRST one while we are here
        _first = dict(g, id="race1", pattern="yyy1", born_from=[])
        G.persist_under_lock(lambda rows: G.register(rows, _first), 5.0)
    return g


seed()
minted.clear()
G.propose_from_mistake = _propose_racing
try:
    landed = G.generate_from_vault(use_llm=False)
finally:
    G.propose_from_mistake = real_propose
after = ledger()
check("both guards are in the ledger", "race1" in after and "race2" in after, str(sorted(after)))
check("and the pass reports the one it actually added", landed == 1, str(landed))


def _cli_pack() -> None:
    """`guards pack` installs the shipped pack into the LIVE ledger - a load-mutate-save with
    no lock, like the pass above."""
    argv = sys.argv[:]
    sys.argv = ["guards", "pack"]
    try:
        G.main()
    finally:
        sys.argv = argv


under_lock("guards pack", _cli_pack, lambda a: any(g.get("pack") for g in a.values()))

print("# the door works from inside a lock this process already holds")
# `consolidate --apply` runs the generating pass under the vault lock it took itself, and
# `acquire_lock` is not reentrant: a writer that re-takes the lock from inside a holder spins
# out its timeout and writes nothing, and a writer that releases on the way out hands the
# caller's critical section to somebody else. The door has to notice it is already inside.
seed()
check("the lock is free before we take it", not _held_by_us())
assert m.acquire_lock(timeout_s=10)
try:
    ok = G.persist_under_lock(lambda rows: bool(rows.append(
        {"id": "gInside", "pattern": "i", "message": "written from inside", "scope": {},
         "status": "advisory", "born_from": [], "born_date": "2026-05-01", "corroborations": 0,
         "fired": 0, "helped": 0, "false_positives": 0, "seen_sessions": [],
         "overrides": []}) or True), 5.0)
    check("a write from inside the holder goes through", ok is True)
    check("and the lock is still ours afterwards", _held_by_us())
finally:
    m.release_lock()
check("the write landed", "gInside" in ledger())

seed()
G.propose_from_mistake = _propose
assert m.acquire_lock(timeout_s=10)
try:
    added = G.generate_from_vault(use_llm=False)
    check("the generating pass still writes under an outer lock (consolidate --apply)",
          added > 0 and any(k.startswith("gen") for k in ledger()), str(sorted(ledger())))
    check("and it did not release the outer lock", _held_by_us())
finally:
    m.release_lock()
    G.propose_from_mistake = real_propose

print("# a flat install without outcomes.py writes through the same door")
seed()
real_sibling = G._sibling
G._sibling = lambda name: None if name == "outcomes" else real_sibling(name)
try:
    under_lock("feedback (legacy lifecycle)",
               lambda: G.feedback("gA", "false_positive", session_id="s9", reason="noisy"),
               lambda a: a["gA"]["false_positives"] == 1)
finally:
    G._sibling = real_sibling

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

print("# including the CLI, which is where a person reads the answer")
# `guards feedback` called `feedback` bare. Once the lifecycle write became required, a busy
# lock raised `LedgerBusy` out of the console script: a traceback where the two words that
# matter are "nothing was recorded", and an exit code of 1 that says "crashed" rather than
# "try again".
import contextlib  # noqa: E402
import io as _io  # noqa: E402


def cli(*args) -> tuple[int, str, str]:
    argv, out, err = sys.argv[:], _io.StringIO(), _io.StringIO()
    sys.argv = ["guards", *args]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = G.main()
    finally:
        sys.argv = argv
    return (code or 0), out.getvalue(), err.getvalue()


for name, args in (("guards feedback", ("feedback", "gA", "accepted")),
                   ("guards pack", ("pack",))):
    seed()
    before = json.dumps(G.load_guards(), sort_keys=True)
    m.acquire_lock = lambda *a, **k: False
    try:
        code, out, err = cli(*args)
    except Exception as exc:                                # noqa: BLE001 - that is the finding
        check(name + ": a busy lock is an answer, not a traceback", False,
              type(exc).__name__ + ": " + str(exc))
        code, out, err = 0, "", ""
    else:
        check(name + ": a busy lock is an answer, not a traceback", True)
    finally:
        m.acquire_lock = real_acquire
    check(name + ": it says nothing was recorded",
          "busy" in (out + err).lower() and "not" in (out + err).lower(), repr(out + err))
    check(name + ": and exits non-zero so a script can retry", code != 0, str(code))
    check(name + ": and the ledger on disk is untouched",
          json.dumps(G.load_guards(), sort_keys=True) == before)

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

print("# and a busy lock is an exit code the SHELL sees, not a return value main() drops")
# `guards pack` and `guards feedback` return 1 on a busy lock - and the module's
# `if __name__ == "__main__": main()` threw that value away, so `python -m nevertwice.guards`
# exited 0 and any script branching on `$?` read "written" from a run that wrote nothing.
# The in-process checks above call `G.main()` and read its return, so they could never see
# this: the only instrument for a process's exit code is a real process.
import os as _os                    # noqa: E402
import subprocess as _sp            # noqa: E402

_blocked = Path(m.VAULT) / "not-a-directory"
_blocked.write_text("a regular file: no store can be created under it", encoding="utf-8")
_env = dict(_os.environ)
_env["NEVERTWICE_VAULT"] = str(_blocked / "store")
_env["NEVERTWICE_HOME"] = str(_blocked / "store")
_proc = _sp.run([sys.executable, "-m", "nevertwice.guards", "pack"],
                cwd=str(ROOT), env=_env, capture_output=True,
                encoding="utf-8", errors="replace", timeout=180)
check("guards pack: a busy lock exits non-zero from the real process",
      _proc.returncode == 1,
      "rc=" + str(_proc.returncode) + " out=" + repr(_proc.stdout) + " err=" + repr(_proc.stderr))
check("guards pack: and says why on stderr", "busy" in _proc.stderr.lower(), repr(_proc.stderr))

print()
print("every ledger write is serialised: " + str(P) + " passed, " + str(F) + " failed")
sys.exit(1 if F else 0)
