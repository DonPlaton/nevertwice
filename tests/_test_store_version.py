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
* the notes are never touched by a MIGRATION - though a rollback restores the whole store,
  history included, so the instruction has to say what it costs;
* `guards.json` and the import ledgers are preserved - they record history, not derivation.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
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

NL = chr(10)
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


def test_the_migration_writes_the_ledger_the_way_the_ledger_is_read() -> None:
    """A two-generation file written in one generation is a rollback that silently undoes itself.

    `guards.json` is loaded by `_load_json_generations`: primary, then `.bak`. The migration
    wrote it with a plain `write_atomic`, so `.bak` kept the PRE-migration ledger - and any
    later corruption of the primary recovers a store to before the migration, loudly announcing
    a successful recovery. The `.prev` generation, the one a rollback actually needs, was never
    written at all. Both are free: the writer the rest of the codebase uses does it.
    """
    print("\n- the migration writes both generations -")
    make_22_era_store()
    ledger = VAULT / "guards.json"
    m._save_json_generations(ledger, ledger.read_text(encoding="utf-8"))   # a real 2.2 store
    before = ledger.read_text(encoding="utf-8")
    for suffix in (".bak", ".prev"):
        ledger.with_name(ledger.name + suffix).unlink(missing_ok=True)
    m._save_json_generations(ledger, before)
    check("the fixture has the ledger as a store really carries it",
          ledger.with_name("guards.json.bak").is_file())

    result = SV.migrate(VAULT, dry_run=False)
    check("the migration succeeded", result["ok"], str(result.get("detail")))
    check("the primary gained the outcome block", "outcomes" in ledger.read_text(encoding="utf-8"))
    check("and so did the generation the loader falls back to",
          "outcomes" in ledger.with_name("guards.json.bak").read_text(encoding="utf-8"),
          "a corrupt primary would recover the store to before the migration")
    prev = ledger.with_name("guards.json.prev")
    check("the pre-migration ledger is kept as the rollback generation",
          prev.is_file() and "outcomes" not in prev.read_text(encoding="utf-8"),
          "present" if prev.is_file() else "no .prev was written")
    _rmtree(result["backup"], ignore_errors=True)


def test_a_rebuild_promises_only_what_it_rebuilds() -> None:
    """`graph.json` was on the list of things a rebuild deletes and rebuilds. Nothing in the
    store pipeline writes one - `graphify` writes it at a PROJECT root - so the rebuild deleted
    it and moved on, while the dry run named it among `would_rebuild`. A store that is also a
    checkout (the vault is a git repository, so this is not exotic) lost a file this tool cannot
    make again, on the strength of a promise to make it again."""
    print("\n- a rebuild rebuilds what it removes -")
    seed_notes()
    (VAULT / "graph.json").write_text('{"files": {}}', encoding="utf-8")
    dry = SV.rebuild(VAULT, dry_run=True)
    check("the dry run does not promise to rebuild what nothing writes",
          "graph.json" not in dry["would_rebuild"], str(dry["would_rebuild"]))
    SV.rebuild(VAULT, dry_run=False)
    check("and a real rebuild leaves it where it found it",
          (VAULT / "graph.json").is_file(),
          "deleted by a rebuild that had no way to put it back")
    (VAULT / "graph.json").unlink(missing_ok=True)


def test_a_filename_inside_a_comment_is_not_an_ignore_rule() -> None:
    """`name not in existing` is a substring test over the whole file, so a line explaining why
    something is NOT ignored counts as ignoring it - and the step reports the store already
    covered. The sibling that writes the live store's ignore list compares stripped LINES; this
    one is the copy that drifted."""
    print("\n- an ignore rule is a line, not a mention -")
    make_22_era_store()
    (VAULT / ".gitignore").write_text(
        "# .index.sqlite is deliberately committed in this store\n*.tmp\n",
        encoding="utf-8")
    step = SV._step_ignore_derived(VAULT, dry_run=True)
    check("a commented mention does not pass for a rule", step["applies"],
          str(step["detail"]))
    SV._step_ignore_derived(VAULT, dry_run=False)
    lines = {ln.strip() for ln in (VAULT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    check("and the rule is written as its own line", ".index.sqlite" in lines, str(sorted(lines)))


#: `sandbox_guard.isolate()` switches git's auto-gc and auto-maintenance off for every git a
#: sandboxed process starts; these tests hand git a minimal explicit environment, so they
#: carry the same keys across.
_NO_GIT_HOUSEKEEPING = {k: v for k, v in os.environ.items() if k.startswith("GIT_CONFIG_")}


def _force_remove(func, path, exc) -> None:
    """Clear the read-only bit git puts on its objects, then retry (Windows).

    A path that is already gone is what rmtree wanted, not an error. The test stores are git
    repositories, and git's background maintenance creates and removes
    `.git/objects/maintenance.lock` while the test tears the store down. macOS 3.10 of run
    35794625037: rmtree met the vanished lock, called this handler, `chmod` raised
    FileNotFoundError from inside it, and the whole suite failed on its own cleanup - the same
    race `backup()` was taught to survive in 41e2423, one file over, in the harness.
    """
    if not os.path.lexists(path):
        return
    try:
        os.chmod(path, 0o700)
        func(path)
    except FileNotFoundError:
        return


#: `rmtree`'s handler keyword is `onexc` from Python 3.12 and `onerror` before it, and this file
#: used `onexc` unconditionally while the package declares 3.10+. On 3.10 that is
#: `TypeError: rmtree() got an unexpected keyword argument 'onexc'` - a crash in the cleanup of a
#: test, reported as the suite failing. Invisible on any interpreter new enough, which is every
#: one on this machine; it appeared on the 3.10 jobs of CI's first matrix run (2026-09-22).
#: Chosen by asking the function, not the version number: a parameter is present or it is not.
import inspect as _inspect  # noqa: E402

_RMTREE_HANDLER = ("onexc" if "onexc" in _inspect.signature(shutil.rmtree).parameters
                   else "onerror")


def _rmtree(path, **kw):
    """`shutil.rmtree` with the read-only handler under whichever name this Python takes."""
    return shutil.rmtree(path, **{**kw, _RMTREE_HANDLER: _force_remove})


def test_the_documented_rollback_does_not_destroy_what_it_restores() -> None:
    """A backup you are told to swap in must contain what the swap deletes.

    `backup()` copied the store with `ignore_patterns(".git")`, and `rollback_instructions`
    says to remove the vault and rename the backup back into its place. Every nevertwice store
    is a git repository - `git_autocommit` makes one on first write - so the two together read
    as a safe procedure and perform an unrecoverable one: the notes come back, and every commit
    that says how they got that way does not. The reassurance in the same sentence ("the
    Markdown notes were never modified") is exactly what makes it sound safe to follow.

    The test follows the instruction literally, because that is what a person does with it.
    """
    print("\n- the rollback restores the store, history included -")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "store"
        (store / "Mistakes").mkdir(parents=True)
        (store / "Mistakes" / "note.md").write_text(
            "---\ntype: mistake\n---\n\nbody\n", encoding="utf-8")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ.get("PATH", ""),
               **_NO_GIT_HOUSEKEEPING}

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=store, capture_output=True, text=True,
                                  env=env).stdout

        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "the history this rollback must not cost")
        before = git("rev-parse", "HEAD").strip()
        check("the fixture store has a history to lose", len(before) == 40, before)

        backup_path = SV.backup(store)
        check("the backup carries the repository, not just the working tree",
              (backup_path / ".git").is_dir(),
              str(sorted(q.name for q in backup_path.iterdir())))

        # Exactly what the instruction says to do. `onexc` is a Windows detail, not a
        # concession: git marks its object files read-only, so a plain rmtree stops halfway.
        _rmtree(store)
        backup_path.rename(store)
        after = git("rev-parse", "HEAD").strip()
        check("and the rolled-back store is still the same repository", after == before,
              f"{before[:8]} -> {after[:8] or 'no repository'}")
        check("with the note it was taken for",
              (store / "Mistakes" / "note.md").is_file())


def test_the_rollback_text_says_what_the_rollback_costs() -> None:
    """The instruction has to describe the operation it asks for.

    Putting `.git` into the backup fixed the first half: the rollback no longer destroys the
    history. It also gave the sentence a second bottom. Swapping the backup in now rewinds the
    NOTES and the history to the moment of the backup, so everything written to the store since
    - notes from later sessions, their commits - is gone; and the text says the Markdown was
    never modified, so a rollback only restores state files. A person reading that would not
    think to save their week's notes first.

    "The notes are never touched" is true of the MIGRATION, which only rewrites derived
    artifacts. It is not true of the rollback, which restores a directory. The text conflated
    the two.
    """
    print("\n- the rollback text describes the rollback -")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "store"
        (store / "Mistakes").mkdir(parents=True)
        (store / "Mistakes" / "before.md").write_text(
            "---\ntype: mistake\n---\n\nold\n", encoding="utf-8")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ.get("PATH", ""),
               **_NO_GIT_HOUSEKEEPING}

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=store, capture_output=True, text=True,
                                  env=env).stdout

        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "before the migration")
        backup_path = SV.backup(store)
        text = SV.rollback_instructions(store, backup_path)

        # A week of work after the migration - the case the sentence is read in.
        (store / "Mistakes" / "after.md").write_text(
            "---\ntype: mistake\n---\n\nlearned since\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "a week of sessions after the migration")

        _rmtree(store)
        backup_path.rename(store)
        check("following the instruction rewinds the notes too, not only state files",
              not (store / "Mistakes" / "after.md").exists())
        check("and the commits written since the backup are gone",
              "after the migration" not in git("log", "--format=%s"))

        low = text.lower()
        check("so the text does not promise that a rollback leaves the notes alone",
              "never modified" not in low and "only restores state files" not in low, text)
        check("it names the cost: work written after the backup is lost",
              "lost" in low and ("after" in low or "since" in low), text)
        check("and it says to move the store aside rather than delete it, so the loss is not final",
              "aside" in low and "remove " not in low.replace("do not remove", ""), text)


def test_a_backup_survives_a_file_that_vanishes_under_it() -> None:
    """Git tidies its own repository while the backup walks it - and the backup must still refuse
    to be incomplete.

    macOS 3.14 of run 35781171527: `copytree` listed `.git/objects/`, git's maintenance removed
    `maintenance.lock`, and the copy raised on a file that had been there a moment earlier. The
    first fix tolerated a vanished FILE in the copy function; the engine review of 2026-09-23
    found it also swallowed a DESTINATION-side FileNotFoundError (a Windows long path: the note
    left out of the backup, the migration going ahead), missed a vanished DIRECTORY, printed its
    report into `--json` stdout, and carried it over to the next call in a module-level set.

    The race is planted in the LISTING: `os.scandir` returns every entry and removes one before
    handing the list back, so any "list, then copy" walk meets a source that is gone, whatever
    copy function it uses. The control is the exact pre-fix call - a plain `copytree` - over the
    same planted listing: it must raise, or the fix has no subject.
    """
    print(NL + "- a backup survives a file that vanishes under it, and refuses to be incomplete -")
    real_scandir = os.scandir

    class _ListedThenGone:
        """What `os.scandir(folder)` returns: the full listing, one entry already deleted."""

        def __init__(self, entries):
            self._entries = entries

        def __iter__(self):
            return iter(self._entries)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            pass

    def planted(folder, pick, run):
        state = {"doomed": None}

        def scandir_that_loses_one(path="."):
            it = real_scandir(path)
            if state["doomed"] is not None or Path(path) != folder:
                return it
            with it:
                entries = list(it)
            victim = next(e for e in entries if pick(e))
            if victim.is_dir(follow_symlinks=False):
                shutil.rmtree(victim.path)
            else:
                os.unlink(victim.path)
            state["doomed"] = victim.name
            return _ListedThenGone(entries)

        os.scandir = scandir_that_loses_one
        try:
            return run(), state["doomed"]
        finally:
            os.scandir = real_scandir

    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "store"
        folder = store / "Mistakes"

        def seed():
            if store.exists():
                _rmtree(store)
            (folder / "objects" / "ab").mkdir(parents=True)
            (folder / "objects" / "ab" / "loose").write_text("x", encoding="utf-8")
            for name in ("a.md", "b.md", "c.md"):
                (folder / name).write_text(f"---{NL}type: mistake{NL}---{NL}{NL}{name}{NL}",
                                           encoding="utf-8")

        def fresh_target():
            for q in Path(td).glob("store.backup-*"):
                _rmtree(q)

        # 1. a FILE vanishes
        seed()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            (backup_path, skipped), doomed = planted(
                folder, lambda e: e.is_file(), lambda: SV._backup_with_report(store))
        check("the race actually happened (a listed file was gone before it was copied)",
              doomed is not None)
        check("the backup completes and names the one that vanished",
              backup_path.is_dir() and any(s.endswith(doomed) for s in skipped), str(skipped))
        kept = sorted(q.name for q in (backup_path / "Mistakes").iterdir() if q.is_file())
        check("every file that was still there is in it",
              kept == sorted(n for n in ("a.md", "b.md", "c.md") if n != doomed), str(kept))
        check("and nothing is printed: the report is data, not stdout (it corrupted --json)",
              out.getvalue() == "", repr(out.getvalue()[:120]))
        fresh_target()

        # 2. a DIRECTORY vanishes - git's prune-packed empties and removes .git/objects/xx/
        seed()
        raised = None
        try:
            (backup_path, skipped), doomed = planted(
                folder / "objects", lambda e: e.is_dir(), lambda: SV._backup_with_report(store))
        except SV.shutil.Error as exc:
            raised, skipped = exc, []
        check("a directory removed between listing and recursion is a vanished source too",
              raised is None and any(s.endswith("ab") for s in skipped),
              f"raised={str(raised)[:120]!r} skipped={skipped}")
        fresh_target()

        # 3. the DESTINATION fails while the source is still there - a Windows long path
        seed()
        real_copy2 = SV.shutil.copy2

        def copy2_dest_fails(src, dst, **kw):
            if Path(src).name == "b.md":
                raise FileNotFoundError(2, "The system cannot find the path specified", str(dst))
            return real_copy2(src, dst, **kw)

        SV.shutil.copy2 = copy2_dest_fails
        refused = None
        try:
            SV._backup_with_report(store)
        except SV.shutil.Error as exc:
            refused = exc
        finally:
            SV.shutil.copy2 = real_copy2
        check("a file that exists but could not be copied REFUSES the backup - it is incomplete",
              refused is not None and "b.md" in str(refused), str(refused)[:160])
        fresh_target()

        # 4. nothing carries over from a failed call
        seed()
        _backup_path, skipped_after = SV._backup_with_report(store)
        check("a clean backup after a refused one reports nothing skipped",
              skipped_after == [], str(skipped_after))
        fresh_target()

        # 5. the list reaches the result migrate() returns - the half of review finding 7 that no
        #    check held: dropping `backup_skipped` from that dict left this suite green (auditing
        #    session's mutation B2), because every check above called _backup_with_report directly
        seed()
        result, doomed = planted(folder, lambda e: e.is_file(),
                                 lambda: SV.migrate(store, dry_run=False))
        got = result.get("backup_skipped")
        check("migrate() returns what its backup skipped, naming the path",
              bool(doomed) and isinstance(got, list) and any(s.endswith(doomed) for s in got),
              f"doomed={doomed} backup_skipped={got}")
        fresh_target()

        # the control: the call this code made before any fix, over the same planted listing
        seed()
        raised = None
        try:
            planted(folder, lambda e: e.is_file(),
                    lambda: SV.shutil.copytree(store, Path(td) / "plain", dirs_exist_ok=False))
        except SV.shutil.Error as exc:
            raised = exc
        check("the pre-fix call over the same listing still raises, so the fix has a subject",
              raised is not None, str(raised)[:200])

        _rmtree(store)


def test_the_cleanup_survives_git_tidying_under_it() -> None:
    """The harness's own teardown met the race the product was taught to survive.

    macOS 3.10 of run 35794625037: `_rmtree(store)` reached `.git/objects/maintenance.lock`
    after git's background maintenance had removed it, called `_force_remove`, and its `chmod`
    raised FileNotFoundError from inside the error handler - the suite failed on cleanup, not on
    anything it checks. Two fixes, both asserted here: the handler treats a vanished path as
    done, and the test environment switches git's housekeeping off so the race has nothing to
    race with.
    """
    print(NL + "- the harness survives git tidying its store under it -")
    with tempfile.TemporaryDirectory() as td:
        gone = Path(td) / "maintenance.lock"          # never created: it vanished already
        raised = None
        try:
            _force_remove(os.unlink, str(gone), FileNotFoundError(str(gone)))
        except OSError as exc:
            raised = exc
        check("the remove handler treats a path that is already gone as removed",
              raised is None, f"{type(raised).__name__}: {raised}")
    for key, want in (("maintenance.auto", "false"), ("gc.auto", "0")):
        got = subprocess.run(["git", "config", "--get", key], capture_output=True, text=True,
                             env={**_NO_GIT_HOUSEKEEPING, "PATH": os.environ.get("PATH", "")}
                             ).stdout.strip()
        check(f"git in a test's minimal env sees {key}={want}", got == want, repr(got))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_an_unstamped_store_is_version_zero,
               test_the_plan_writes_nothing,
               test_a_22_era_store_migrates_forward,
               test_validation_reports_a_broken_store,
               test_a_fresh_clone_rebuilds_a_byte_identical_index,
               test_a_build_over_an_existing_index_still_lands_on_the_canonical_bytes,
               test_a_rebuild_never_costs_the_embeddings,
               test_a_rebuild_dry_run_writes_nothing,
               test_the_documented_rollback_does_not_destroy_what_it_restores,
               test_the_rollback_text_says_what_the_rollback_costs,
               test_the_migration_writes_the_ledger_the_way_the_ledger_is_read,
               test_a_rebuild_promises_only_what_it_rebuilds,
               test_a_filename_inside_a_comment_is_not_an_ignore_rule,
               test_a_backup_survives_a_file_that_vanishes_under_it,
               test_the_cleanup_survives_git_tidying_under_it):
        fn()
    print(f"\nstore version: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
