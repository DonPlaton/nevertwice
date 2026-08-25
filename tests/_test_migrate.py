#!/usr/bin/env python3
"""Migrations in: five sources, provenance kept, and a way back out.

`import_memory.py` already parses four sources. What made D7 worth doing is the two things it
does not do, and both matter more than the parsing.

**Provenance.** A note imported from somewhere else is not a note this system learned. Who
wrote it, when, and from which record are what let you audit it later and tell an imported
claim apart from an earned one. Losing that turns a migration into laundering: every borrowed
memory arrives looking like something the store worked out for itself.

**Reversibility.** An import you cannot undo is a decision you have to be certain about in
advance - the wrong shape for "try it and see", which is the only shape a migration ever really
has.

So this suite round-trips every source: parse a recorded export, import it, prove each note
carries its origin, revert, and prove the store is back where it started. It also holds the
parts that are easy to get wrong under pressure:

* an unknown timestamp stays empty rather than defaulting to today, which would quietly claim
  the memory was made during the import;
* `revert` re-checks each note's own `import_batch` stamp before deleting, because the ledger
  records what *was* written while the note records what it *is* now - and deleting from
  someone's memory on a stale index entry is the failure worth engineering against;
* a dry run really writes nothing.

Every fixture is hand-written from a documented shape. No account, no network, nobody's real
memory.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures" / "migrate"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m         # noqa: E402
import migrate                  # noqa: E402

PASSED = 0
FAILED = 0

PATH_FOR = {
    "claude-memory": FIXTURES / "claude-memory",
    "claude-mem": FIXTURES / "claude-mem.sqlite",
    "mem0": FIXTURES / "mem0-export.json",
    "letta": FIXTURES / "letta-archive.json",
    "generic": FIXTURES / "generic",
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def live_stems() -> set:
    return {n["stem"] for n in m._iter_all_notes()}


# ---------------------------------------------------------- the parsers


def test_every_source_parses_and_keeps_its_origin() -> None:
    print("\n- five sources, and every record knows where it came from -")
    check("five sources are declared", set(migrate.SOURCES) == set(PATH_FOR),
          str(migrate.SOURCES))
    for source, path in PATH_FOR.items():
        records = migrate.PARSERS[source](path)
        check(f"{source} parsed records", bool(records), "nothing parsed")
        check(f"{source} labels every record with its source",
              all(r["source"] == source for r in records))
        check(f"{source} gives every record a title",
              all(r["title"].strip() for r in records))
        check(f"{source} gives every record a valid type",
              all(r["type"] in ("pattern", "mistake", "decision") for r in records),
              str({r["type"] for r in records}))
        check(f"{source} carries a reference back to the original record",
              all(r["ref"] for r in records), str([r["ref"] for r in records]))

    # Each fixture is awkward in one specific way; these are the ways.
    mem0 = migrate.PARSERS["mem0"](PATH_FOR["mem0"])
    check("mem0 finds the type hidden in metadata",
          any(r["type"] == "decision" for r in mem0), str([r["type"] for r in mem0]))
    check("mem0 keeps the user as the author",
          all(r["author"] == "platon" for r in mem0), str([r["author"] for r in mem0]))

    cm = migrate.PARSERS["claude-mem"](PATH_FOR["claude-mem"])
    check("claude-mem finds the content column without being told its name",
          len(cm) == 2, str(len(cm)))
    check("claude-mem converts an epoch timestamp to a date",
          all(r["created"].startswith("20") for r in cm), str([r["created"] for r in cm]))
    check("claude-mem keeps the table and row as the reference",
          all(r["ref"].startswith("observations#") for r in cm), str([r["ref"] for r in cm]))

    letta = migrate.PARSERS["letta"](PATH_FOR["letta"])
    kinds = {r["ref"].split(":")[0] for r in letta}
    check("letta keeps core blocks and archival passages apart",
          kinds == {"block", "archival"}, str(kinds))

    generic = migrate.PARSERS["generic"](PATH_FOR["generic"])
    check("generic reads both markdown bullets and jsonl", len(generic) == 3, str(len(generic)))


def test_an_unknown_timestamp_stays_unknown() -> None:
    """Defaulting a missing date to today would claim the memory was made during the import -
    the one thing provenance exists to prevent."""
    print("\n- a missing timestamp is empty, never today -")
    check("an absent value gives an empty string", migrate._iso(None) == ""
          and migrate._iso("") == "")
    check("an ISO datetime keeps its date", migrate._iso("2026-05-11T08:15:00Z") == "2026-05-11")
    check("epoch seconds convert", migrate._iso(1775000000).startswith("20"))
    check("epoch milliseconds convert too",
          migrate._iso(1775000000000) == migrate._iso(1775000000))
    check("a nonsense value is kept verbatim rather than invented",
          migrate._iso("last tuesday") == "last tuesday")

    # Claude auto-memory has a known author even when the file omits it - the directory it
    # came from is the attribution. Inferring from the *source* is not the same as inventing
    # one, so the default stands and the gap count for this source is honestly zero.
    records = migrate.PARSERS["claude-memory"](PATH_FOR["claude-memory"])
    check("a claude-memory note with no author line is still attributed to its source",
          all(r["author"] == "claude-code" for r in records),
          str([r["author"] for r in records]))

    # Letta core blocks genuinely carry no author, so the gap mechanism has somewhere real to
    # fire - a coverage report that can never report a gap is not a coverage report.
    preview = migrate.plan("letta", PATH_FOR["letta"], "imported")
    check("a source with genuinely missing authors reports the gap",
          preview["provenance_gaps"]["missing_author"] > 0, str(preview["provenance_gaps"]))
    check("and the records that do have one are still counted",
          preview["with_author"] > 0, str(preview["with_author"]))


# ------------------------------------------------------------- dry run


def test_a_dry_run_counts_and_writes_nothing() -> None:
    print("\n- the plan, with nothing written -")
    before = live_stems()
    for source, path in PATH_FOR.items():
        preview = migrate.plan(source, path, "imported")
        check(f"{source} plan succeeds", preview["ok"], str(preview))
        check(f"{source} plan counts what it found", preview["found"] > 0)
        check(f"{source} plan breaks the count down by type", bool(preview["by_type"]))
        check(f"{source} plan reports provenance coverage",
              "with_author" in preview and "with_timestamp" in preview)
        check(f"{source} plan names its provenance gaps",
              set(preview["provenance_gaps"]) == {"missing_author", "missing_timestamp"})
        check(f"{source} plan wrote nothing", preview["written"] == 0 and preview["dry_run"])
    check("no note was created by any plan", live_stems() == before,
          str(live_stems() - before))
    check("an unknown source is refused", not migrate.plan("nope", ".", "x")["ok"])


# ------------------------------------------- the exit criterion: round trip


def test_every_source_round_trips() -> None:
    """GOAL D7's exit criterion, per source: import, verify provenance, revert, compare."""
    print("\n- import, prove the origin, revert, and be back where we started -")
    for source, path in PATH_FOR.items():
        before = live_stems()
        result = migrate.apply(source, path, "imported")
        check(f"{source} imported", result["written"] > 0, str(result))
        created = live_stems() - before
        check(f"{source} created the notes it says it did",
              len(created) == result["written"], f"{len(created)} vs {result['written']}")

        stamped = 0
        for stem in created:
            origin = migrate.provenance(stem)
            if origin.get("imported_from") == source and origin.get("import_batch") == result["batch"]:
                stamped += 1
        check(f"{source} stamped every note with its origin and batch",
              stamped == len(created), f"{stamped}/{len(created)}")

        sample = migrate.provenance(sorted(created)[0])
        check(f"{source} records the source reference in the note itself",
              bool(sample.get("source_ref")) or source == "generic", str(sample))
        check(f"{source} provenance survives in the file, not just in memory",
              set(sample) <= set(migrate.PROVENANCE_KEYS) and "imported_from" in sample,
              str(sample))

        listed = [b for b in migrate.batches() if b["id"] == result["batch"]]
        check(f"{source} batch is on record", len(listed) == 1, str(migrate.batches()))

        preview = migrate.revert(result["batch"])
        check(f"{source} revert dry-run lists the notes",
              len(preview["would_remove"]) == len(created) and not preview["removed"],
              str(preview))
        check(f"{source} revert dry-run removed nothing", live_stems() == before | created)

        done = migrate.revert(result["batch"], dry_run=False)
        check(f"{source} revert removed the notes", len(done["removed"]) == len(created),
              str(done))
        check(f"{source} the store is back where it started", live_stems() == before,
              str(live_stems() ^ before))
        check(f"{source} the batch is no longer on record",
              not any(b["id"] == result["batch"] for b in migrate.batches()))


def test_importing_twice_converges_instead_of_duplicating() -> None:
    """Re-running an import must not double the store, and the batches must not fight.

    The shared write path derives a stem from title and date, so a second import of the same
    export rewrites the same files rather than creating new ones - which is what makes a re-run
    after a partial import safe. The consequence is that the older batch's stems now carry the
    newer batch's stamp, and `revert` has to resolve that from the notes rather than from the
    ledger. This is the case that would quietly delete someone's memory if it did not.
    """
    print("\n- the same export twice converges, and revert resolves the overlap -")
    before = live_stems()
    first = migrate.apply("mem0", PATH_FOR["mem0"], "imported")
    created = live_stems() - before
    second = migrate.apply("mem0", PATH_FOR["mem0"], "imported")

    check("the two imports have different batch ids", first["batch"] != second["batch"],
          f"{first['batch']} vs {second['batch']}")

    # The regression, forced rather than waited for. The first version stamped the id to the
    # second, so two imports finishing inside one second collided - which never happened
    # locally, because each import spent over a second failing to reach an absent embedder,
    # and happened on the very first CI run, where it does not. Uniqueness is now established
    # against the ledger, so this holds with the clock standing still.
    # The clock is held still, because that is the only way to reach the collision branch:
    # with microseconds running, two calls differ anyway and the check never fires. A
    # regression test that passes for the wrong reason is how this bug got to CI in the first
    # place - a mutation dropping the collision check survived until the clock was frozen.
    same = migrate.PARSERS["mem0"](PATH_FOR["mem0"])

    class _FrozenClock:
        """`datetime.now()` stuck at one instant - CI's fast machine, made reproducible."""
        _at = migrate.datetime(2026, 8, 25, 17, 53, 41, 123456)

        @classmethod
        def now(cls):
            return cls._at

    real = migrate.datetime
    migrate.datetime = _FrozenClock
    try:
        ids = []
        for _ in range(3):
            ids.append(migrate._batch_id("mem0", same,
                                         existing=[{"id": i} for i in ids]))
    finally:
        migrate.datetime = real

    check("three imports in the same instant get three distinct ids",
          len(set(ids)) == 3, str(ids))
    check("and they all still name the source and its content",
          all(i.startswith("mem0-") and ids[0][:len(ids[0])] for i in ids), str(ids))
    check("the second import created no new notes", live_stems() - before == created,
          str(live_stems() - before - created))
    check("it re-stamped the same notes to the newer batch",
          all(migrate.provenance(stem).get("import_batch") == second["batch"]
              for stem in created),
          str({s: migrate.provenance(s).get("import_batch") for s in created}))

    done = migrate.revert(second["batch"], dry_run=False)
    check("reverting the newer batch removes the notes",
          len(done["removed"]) == len(created) and live_stems() == before, str(done))

    stale = migrate.revert(first["batch"], dry_run=False)
    check("reverting the older batch then finds nothing of its own to remove",
          stale["ok"] and not stale["removed"], str(stale))
    check("and says the notes are already gone rather than failing",
          all("gone already" in item["why"] for item in stale["skipped"]),
          str(stale["skipped"]))
    check("the store is untouched by the second revert", live_stems() == before,
          str(live_stems() ^ before))


# ----------------------------------------------------------- mutations


def test_revert_refuses_what_it_should() -> None:
    print("\n- revert deletes only what it can still prove it wrote -")
    before = live_stems()
    result = migrate.apply("letta", PATH_FOR["letta"], "imported")
    created = sorted(live_stems() - before)

    # A note edited into a different batch is no longer this batch's to delete. The ledger
    # still lists it; the note disagrees; the note wins.
    victim = created[0]
    parsed = m.parse_typed_stem(victim)
    path = m.VAULT / m.TYPE_FOLDER[parsed["ntype"]] / f"{victim}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(f"import_batch: {result['batch']}",
                                 "import_batch: someone-elses-batch"), encoding="utf-8")

    preview = migrate.revert(result["batch"])
    check("the reassigned note is not listed for removal",
          victim not in preview["would_remove"], str(preview["would_remove"]))
    check("and the reason is stated",
          any(s["stem"] == victim and "belongs to batch" in s["why"]
              for s in preview["skipped"]), str(preview["skipped"]))

    done = migrate.revert(result["batch"], dry_run=False)
    check("it survives the real revert", victim in live_stems(), str(done))
    check("everything else was removed",
          len(done["removed"]) == len(created) - 1, str(done["removed"]))

    path.unlink()                                    # clean up the note we hand-edited
    check("an unknown batch id is refused",
          not migrate.revert("no-such-batch")["ok"])
    check("and it says which batches exist",
          "known" in migrate.revert("no-such-batch"))
    try:
        m.rebuild_index()
    except Exception:                                # noqa: BLE001
        pass
    check("the store is back where it started", live_stems() == before,
          str(live_stems() ^ before))


def test_a_missing_or_broken_export_fails_softly() -> None:
    print("\n- a bad export is an empty plan, not a traceback -")
    missing = HERE / "fixtures" / "migrate" / "does-not-exist.json"
    for source in ("mem0", "letta", "claude-mem", "generic", "claude-memory"):
        try:
            records = migrate.PARSERS[source](missing)
            ok = records == []
        except Exception as exc:                     # noqa: BLE001
            ok = False
            print(f"       ({source} raised {type(exc).__name__})")
        check(f"{source} on a missing path returns nothing rather than raising", ok)

    broken = HERE / "fixtures" / "migrate" / "_broken.json"
    broken.write_text("{not json at all", encoding="utf-8")
    try:
        check("a malformed JSON export parses to nothing",
              migrate.PARSERS["mem0"](broken) == [])
    finally:
        broken.unlink(missing_ok=True)


def main() -> int:
    for fn in (test_every_source_parses_and_keeps_its_origin,
               test_an_unknown_timestamp_stays_unknown,
               test_a_dry_run_counts_and_writes_nothing,
               test_every_source_round_trips,
               test_importing_twice_converges_instead_of_duplicating,
               test_revert_refuses_what_it_should,
               test_a_missing_or_broken_export_fails_softly):
        fn()
    print(f"\nmigrate: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
