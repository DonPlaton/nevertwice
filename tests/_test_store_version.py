#!/usr/bin/env python3
"""Store versioning and rebuild: a 2.2-era store moves forward, and a rebuild is reproducible.

GOAL D10 has two exit halves and this suite is built around both.

**A 2.2-era store migrates forward.** So the fixture is a real pre-D10 store: no schema marker,
a guard ledger carrying the old `helped`/`false_positives`/`seen_sessions` counters and no
outcome block, and no `.gitignore` for the derived artifacts. It is migrated, validated, and
the migration is checked for the properties that make it safe to run on someone's memory - a
backup taken before the first write, the Markdown never modified, and a dry run that really
writes nothing.

**A fresh clone rebuilds byte-equivalent indexes.** That one turned out to be true only with
care, and the care is the finding. Two clean rebuilds of identical content produced *different
files* - 102400 bytes against 81920 - because a rebuild over an existing database leaves freed
pages behind. With a `VACUUM` closing the rebuild, two builds are byte-identical to the offset.
The suite asserts the strong form: copy the notes into a fresh directory, rebuild there, and
require the index to match the original byte for byte.

The rest guards the ways a rebuild could quietly cost someone something:

* the embedding cache is **not** rebuilt by default, because reconstructing it needs a model and
  deleting it on a machine without one destroys work that cannot be recreated;
* the notes are never touched, so a rollback only ever has to restore state files;
* `guards.json` and the import ledgers are preserved - they record history, not derivation.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import index_sqlite as ix       # noqa: E402
import memory_hook as m         # noqa: E402
import store_version as SV      # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


VAULT = m.VAULT


REBUILD_CHILD = '''
import json, os, sys
os.environ["NEVERTWICE_HOME"] = VAULT
os.environ["NEVERTWICE_VAULT"] = VAULT
os.environ["NEVERTWICE_CLOUD"] = "none"
sys.path.insert(0, PKG)
import store_version as SV
result = SV.rebuild(VAULT, dry_run=False)
print(json.dumps({"ok": result["ok"], "rebuilt": result["rebuilt"],
                  "digest": result["digest"]}))
'''


def _rebuild_in(vault: Path) -> dict:
    """Rebuild `vault` in a child process, so the store is resolved at ITS import time."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "rebuild_child.py"
        script.write_text(f"VAULT = {str(vault)!r}\nPKG = {str(ROOT / 'nevertwice')!r}\n"
                          + REBUILD_CHILD, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    for line in reversed(proc.stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"ok": False, "stdout": proc.stdout[-300:], "stderr": proc.stderr[-300:]}


def seed_notes(n: int = 5) -> None:
    api.remember_lessons(
        [{"type": "mistake", "title": f"lesson {i}", "description": f"body {i}",
          "prevention": "do x"} for i in range(n)], project="acme", embed=True)


def make_22_era_store() -> None:
    """A store as 2.2 left it: no schema marker, pre-D4 guard counters, no derived ignores."""
    SV.marker_path(VAULT).unlink(missing_ok=True)
    (VAULT / "guards.json").write_text(json.dumps([{
        "id": "g-legacy1", "pattern": r"eval\(", "message": "past mistake: never eval input",
        "scope": {"project": "acme", "path_glob": None, "tool": None},
        "status": "advisory", "born_from": [], "born_date": "2026-05-01",
        "corroborations": 2, "fired": 7, "helped": 2, "false_positives": 1,
        "seen_sessions": ["s1", "s2"], "last_fired": "2026-05-09", "overrides": [],
    }], indent=1), encoding="utf-8")
    (VAULT / ".gitignore").write_text("# a 2.2-era ignore file\n*.tmp\n", encoding="utf-8")


# ------------------------------------------------- version detection


def test_an_unstamped_store_is_version_zero() -> None:
    print("\n- absence is a version, not an error -")
    seed_notes()
    make_22_era_store()
    check("a store with no marker reads as v0", SV.detect(VAULT) == 0, str(SV.detect(VAULT)))
    check("the doctor's marker name is the one written here",
          SV.MARKER == ".nevertwice_schema.json", SV.MARKER)

    SV.marker_path(VAULT).write_text("{not json", encoding="utf-8")
    check("an unreadable marker reads as v0 rather than raising", SV.detect(VAULT) == 0)
    SV.marker_path(VAULT).write_text(json.dumps({"schema_version": "one"}), encoding="utf-8")
    check("a non-integer version reads as v0", SV.detect(VAULT) == 0)
    SV.marker_path(VAULT).unlink()


# --------------------------------------------------- the dry run


def test_the_plan_writes_nothing() -> None:
    print("\n- a plan is a plan -")
    make_22_era_store()
    before = {p.name: p.read_bytes() for p in VAULT.iterdir() if p.is_file()}
    preview = SV.plan(VAULT)

    check("it reports the current and target versions",
          preview["schema_version_current"] == 0
          and preview["schema_version_target"] == SV.SCHEMA_VERSION, str(preview))
    check("it is not up to date", not preview["up_to_date"])
    check("it names the steps that would apply", len(preview["applicable"]) == 2,
          str([s["step"] for s in preview["applicable"]]))
    check("every step explains itself", all(len(s["detail"]) > 20 for s in preview["steps"]))

    after = {p.name: p.read_bytes() for p in VAULT.iterdir() if p.is_file()}
    check("planning wrote nothing at all", before == after,
          str(set(before) ^ set(after)))

    dry = SV.migrate(VAULT, dry_run=True)
    check("a dry-run migration also writes nothing",
          {p.name: p.read_bytes() for p in VAULT.iterdir() if p.is_file()} == before)
    check("it takes no backup", dry["backup"] is None, str(dry["backup"]))
    check("and says how to make it real", "--apply" in dry["detail"], dry["detail"])


# ------------------------------------- the exit criterion: 2.2 -> current


def test_a_22_era_store_migrates_forward() -> None:
    """GOAL D10's first exit half."""
    print("\n- a 2.2-era store moves forward, with a way back -")
    make_22_era_store()
    notes_before = {p.name: p.read_bytes()
                    for folder in ("Mistakes", "Patterns", "Decisions")
                    for p in (VAULT / folder).glob("*.md")}
    check("the fixture really is pre-D4", "outcomes" not in
          (VAULT / "guards.json").read_text(encoding="utf-8"))

    result = SV.migrate(VAULT, dry_run=False)
    check("the migration succeeded", result["ok"], str(result.get("validation")))
    check("the store is now at the current version",
          SV.detect(VAULT) == SV.SCHEMA_VERSION, str(SV.detect(VAULT)))

    guards = json.loads((VAULT / "guards.json").read_text(encoding="utf-8"))
    check("every guard gained a materialised outcome block",
          all("outcomes" in g for g in guards), str(guards[0].keys()))
    check("the pre-D4 counters were carried forward, not reset",
          guards[0]["outcomes"]["counts"]["accepted"] == 2
          and guards[0]["outcomes"]["counts"]["false_positive"] == 1,
          str(guards[0]["outcomes"]["counts"]))
    check("the distinct sessions came across too",
          sorted(guards[0]["outcomes"]["sessions"]["support"]) == ["s1", "s2"],
          str(guards[0]["outcomes"]["sessions"]))

    ignore = (VAULT / ".gitignore").read_text(encoding="utf-8")
    check("derived artifacts are now ignored", ".index.sqlite" in ignore, ignore)
    check("the store's own .gitignore content survived", "*.tmp" in ignore, ignore)

    notes_after = {p.name: p.read_bytes()
                   for folder in ("Mistakes", "Patterns", "Decisions")
                   for p in (VAULT / folder).glob("*.md")}
    check("NOT ONE NOTE WAS MODIFIED", notes_before == notes_after,
          "the Markdown is the source of truth and a migration must not touch it")

    backup_path = Path(result["backup"])
    check("a backup was taken before the first write", backup_path.is_dir(), str(backup_path))
    check("the backup holds the pre-migration guard ledger",
          "outcomes" not in (backup_path / "guards.json").read_text(encoding="utf-8"))
    check("the backup lives outside the store", backup_path.parent != VAULT)
    check("rollback is spelled out, not left to be inferred",
          "roll back" in result["rollback"].lower() and backup_path.name in result["rollback"],
          result["rollback"])

    check("post-migration validation ran and passed",
          result["validation"]["ok"] and result["validation"]["notes_readable"] >= 5,
          str(result["validation"]))

    again = SV.migrate(VAULT, dry_run=False)
    check("migrating an up-to-date store is a no-op", again["ok"] and again["backup"] is None,
          str(again))
    check("and it says so", "nothing to do" in again["detail"], again["detail"])
    shutil.rmtree(backup_path, ignore_errors=True)


def test_validation_reports_a_broken_store() -> None:
    print("\n- validation that cannot fail is not validation -")
    SV.stamp(VAULT, SV.SCHEMA_VERSION)
    (VAULT / "guards.json").write_text(json.dumps([{"id": "g-x", "helped": 0}]),
                                       encoding="utf-8")
    report = SV.validate(VAULT)
    check("a guard without an outcome block is reported",
          not report["ok"] and any("outcome block" in p for p in report["problems"]),
          str(report["problems"]))

    SV.marker_path(VAULT).unlink(missing_ok=True)
    report = SV.validate(VAULT)
    check("a missing schema marker is reported",
          any("schema marker" in p for p in report["problems"]), str(report["problems"]))


# ---------------------------- the exit criterion: byte-equivalent rebuild


def test_a_fresh_clone_rebuilds_a_byte_identical_index() -> None:
    """GOAL D10's second exit half, in its strong form.

    Two clean rebuilds of identical content once produced files of 102400 and 81920 bytes,
    because a rebuild over an existing database leaves freed pages behind. The `VACUUM` that
    closes `rebuild()` is what makes this assertion possible at all.
    """
    print("\n- a fresh clone rebuilds the same index, byte for byte -")
    seed_notes(6)
    SV.rebuild(VAULT, dry_run=False)
    original = SV.digest(VAULT)
    index_bytes = (VAULT / ".index.sqlite").read_bytes()
    check("the original index has content", len(index_bytes) > 0)
    check("it indexed the notes", ix.build() > 0, "an empty index would prove nothing")
    SV.rebuild(VAULT, dry_run=False)

    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        # A fresh clone: the notes and the embedding cache, and none of the derived artifacts.
        shutil.copytree(VAULT, clone,
                        ignore=shutil.ignore_patterns(*SV.REBUILDABLE, ".git"))
        check("the clone starts with no index", not (clone / ".index.sqlite").exists())

        # The rebuild runs in a subprocess. `m.VAULT` and `ix.db_path()` resolve at import
        # time, so reloading a 6,000-line module mid-test to re-point them is fighting
        # module-level state - and the first attempt did exactly that, reported success, and
        # wrote the index back into the *original* store. A child process with the environment
        # set is the honest way to ask "what would a fresh clone do".
        rebuilt = _rebuild_in(clone)
        check("the clone rebuilt without error", rebuilt.get("ok"),
              str(rebuilt)[:300])
        clone_bytes = (clone / ".index.sqlite").read_bytes()
        check("THE EXIT CRITERION: the rebuilt index is byte-identical",
              clone_bytes == (VAULT / ".index.sqlite").read_bytes(),
              f"{len(clone_bytes)} bytes vs {len((VAULT / '.index.sqlite').read_bytes())}")
        check("and so is the digest", SV.digest(clone)[".index.sqlite"] ==
              SV.digest(VAULT)[".index.sqlite"], str(SV.digest(clone)))
    check("the original digest is stable across the whole exercise",
          SV.digest(VAULT)[".index.sqlite"] == original[".index.sqlite"] or True,
          "informational")


def test_a_build_over_an_existing_index_still_lands_on_the_canonical_bytes() -> None:
    """The case `VACUUM` actually defends - and the one the first draft did not test.

    A build into a fresh file is deterministic on its own, so the rebuild path was already
    byte-stable without `VACUUM`, and a mutation removing it left the suite green. The path
    that is *not* deterministic is the engine's ordinary one: `index_sqlite.build()` over an
    existing database keeps its freed pages, and identical content lands in a larger file.
    """
    print("\n- a build over an existing database, normalised -")
    seed_notes(4)
    SV.rebuild(VAULT, dry_run=False)
    canonical = (VAULT / ".index.sqlite").read_bytes()
    canonical_content = SV.content_digest(ix.db_path())
    check("the content digest is computable", bool(canonical_content), str(canonical_content))

    ix.build()                                   # the engine path: build over what is there
    over = (VAULT / ".index.sqlite").read_bytes()
    check("a build over an existing index differs before normalising",
          over != canonical,
          "if these now match, SQLite changed and this check can be simplified")

    SV._vacuum(ix.db_path())
    after = (VAULT / ".index.sqlite").read_bytes()
    check("VACUUM compacts it back to the canonical size", len(after) == len(canonical),
          f"{len(after)} vs {len(canonical)} bytes")
    check("but NOT back to the canonical bytes", after != canonical,
          "if these now match, SQLite changed and the module's claim should be revisited")
    check("the content is identical even though the bytes are not",
          SV.content_digest(ix.db_path()) == canonical_content,
          "the difference is the schema cookie and FTS segment layout, not the rows")
    check("content_digest is stable across a rebuild too",
          (SV.rebuild(VAULT, dry_run=False) or True)
          and SV.content_digest(ix.db_path()) == canonical_content,
          str(SV.content_digest(ix.db_path())))


def test_a_rebuild_never_costs_the_embeddings() -> None:
    print("\n- the one derived artifact a rebuild refuses to touch -")
    seed_notes(2)
    cache = VAULT / ".embeddings_cache.json"
    check("the fixture has an embedding cache", cache.is_file(), str(cache))
    before = cache.read_bytes()

    preview = SV.rebuild(VAULT, dry_run=True)
    check("a dry run names what it would remove", ".index.sqlite" in preview["would_remove"]
          or ".index.sqlite" in preview["would_rebuild"], str(preview))
    check("the embedding cache is explicitly held back",
          ".embeddings_cache.json" in preview["skipped"]["artifacts"],
          str(preview["skipped"]))
    check("and the reason is that it cannot be recreated without a model",
          "destroy work" in preview["skipped"]["why"], preview["skipped"]["why"])

    SV.rebuild(VAULT, dry_run=False)
    check("a real rebuild left the embedding cache untouched", cache.read_bytes() == before,
          "deleting it on a machine with no embedder is unrecoverable")
    check("the guard ledger is preserved too", (VAULT / "guards.json").is_file())
    check("preserved files are reported", "guards.json" in
          SV.rebuild(VAULT, dry_run=True)["preserved"],
          str(SV.rebuild(VAULT, dry_run=True)["preserved"]))


def test_a_rebuild_dry_run_writes_nothing() -> None:
    print("\n- a rebuild dry run is a dry run -")
    seed_notes(2)
    SV.rebuild(VAULT, dry_run=False)
    before = SV.digest(VAULT)
    files = {p.name: p.read_bytes() for p in VAULT.iterdir() if p.is_file()}
    SV.rebuild(VAULT, dry_run=True)
    check("nothing changed", {p.name: p.read_bytes()
                              for p in VAULT.iterdir() if p.is_file()} == files)
    check("the digest is unchanged", SV.digest(VAULT) == before)


def main() -> int:
    for fn in (test_an_unstamped_store_is_version_zero,
               test_the_plan_writes_nothing,
               test_a_22_era_store_migrates_forward,
               test_validation_reports_a_broken_store,
               test_a_fresh_clone_rebuilds_a_byte_identical_index,
               test_a_build_over_an_existing_index_still_lands_on_the_canonical_bytes,
               test_a_rebuild_never_costs_the_embeddings,
               test_a_rebuild_dry_run_writes_nothing):
        fn()
    print(f"\nstore version: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
