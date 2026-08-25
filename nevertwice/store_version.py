#!/usr/bin/env python3
"""What layout this store is in, how to move it forward, and how to rebuild what is derived.

A store that does not say which layout it is in cannot be migrated safely - every future
change has to guess from the shape of what it finds, and guessing wrong on someone's memory is
not a recoverable mistake. `nevertwice-doctor` has been asking for `.nevertwice_schema.json`
since task D1 and telling people to come back when the planner landed. This is the planner.

    nevertwice-store                       # what layout is this, and what would change
    nevertwice-store --migrate             # dry run: the plan, the backup path, nothing written
    nevertwice-store --migrate --apply     # back up, migrate, validate, stamp
    nevertwice-store --rebuild             # dry run
    nevertwice-store --rebuild --apply     # reconstruct every derived artifact
    nevertwice-store --digest              # the canonical digest of the derived artifacts

Three properties this module is built around:

**Nothing is destroyed without a copy.** A migration takes a backup before its first write and
returns the path; rollback is restoring that directory over the store, and it is printed rather
than left to be inferred.

**The notes are never touched.** Migration and rebuild operate on the *derived* artifacts and
the state files. The Markdown is the source of truth and stays exactly as written - which is
also what makes rollback cheap: the expensive half was never at risk.

**A rebuild is byte-reproducible, and the reason is measured rather than assumed.** Two clean
rebuilds of the same store produce byte-identical indexes, and what makes that true is that
`rebuild()` *removes* the index before building: two builds into a fresh file agree to the
byte, with or without `VACUUM`.

Building **over** an existing database does not, and no amount of tidying makes it. Freed pages
stay behind, so identical content lands in 102400 bytes instead of 81920; `VACUUM` recovers the
size, and 627 bytes still differ - the schema cookie, which counts schema statements, and the
FTS index's internal segment layout, which depends on insert history rather than on content. So
`_vacuum` exists for compaction on that in-place path, `content_digest()` answers "is this the
same index" across it, and byte-equality is claimed only for the fresh-build path.

(Two earlier drafts of this paragraph were wrong in opposite directions - first crediting
`VACUUM` with the determinism, then claiming it restored byte-equality - and `rebuild()` called
`_vacuum` for no reason at all. All three were found by mutations: removing the call left every
test green, which is what a redundant mechanism looks like from the outside.) The embedding
cache is the deliberate exception - see `REBUILDABLE`.

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_hook as m         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:               # noqa: BLE001 - a redirected stream may not support it
    pass

#: The layout this build writes. Bump it in the same commit as the step that needs it.
SCHEMA_VERSION = 1

#: The file `doctor.check_store_schema` looks for. Named there since D1; this module is what
#: finally writes it.
MARKER = ".nevertwice_schema.json"

#: Derived artifacts a rebuild reconstructs from the notes. Every one of these is a cache of
#: something the Markdown already says, so losing one costs time and nothing else.
REBUILDABLE = (".index.sqlite", "graph.json", "Index.md")

#: Derived in principle, and NOT rebuilt by default: reconstructing it needs an embedding model,
#: and on a machine without one, deleting it would destroy work that cannot be recreated. A
#: rebuild that quietly cost someone their embeddings would be worse than no rebuild at all.
EXPENSIVE = (".embeddings_cache.json", ".embeddings_meta.json")

#: State files a migration may read but never regenerate: they record history, not derivation.
PRESERVED = ("guards.json", ".processed_sessions.json", ".imported.json", ".migrations.json")


def marker_path(vault: Path) -> Path:
    return Path(vault) / MARKER


def detect(vault: Path) -> int:
    """The store's declared layout version. 0 means unstamped - a pre-D10 store.

    Absence is a version, not an error: every store that existed before this module is a v0
    store, and the migration's whole job is to move those forward.
    """
    path = marker_path(vault)
    if not path.is_file():
        return 0
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("schema_version")
    except (OSError, ValueError):
        return 0
    return version if isinstance(version, int) else 0


def stamp(vault: Path, version: int = SCHEMA_VERSION, *, note: str = "") -> Path:
    path = marker_path(vault)
    payload = {"schema_version": version,
               "stamped": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "by": "nevertwice-store"}
    if note:
        payload["note"] = note
    m.write_atomic(path, json.dumps(payload, indent=1) + "\n")
    return path


# ── the migration steps ─────────────────────────────────────────────────

def _step_materialise_outcomes(vault: Path, *, dry_run: bool) -> dict:
    """v0 -> v1: give every guard the D4 outcome block up front.

    A 2.2-era ledger carries `helped`, `false_positives` and `seen_sessions` and no `outcomes`.
    `outcomes.block()` back-fills it lazily on read, so nothing is broken - but the derived
    counters are then recomputed on every read and only persist when something happens to
    write. Materialising it once makes the ledger self-describing, which is the point of
    having a schema version at all.
    """
    ledger = Path(vault) / "guards.json"
    if not ledger.is_file():
        return {"step": "materialise_guard_outcomes", "applies": False,
                "detail": "no guards.json in this store"}
    try:
        guards = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"step": "materialise_guard_outcomes", "applies": False,
                "detail": f"guards.json is unreadable ({exc}); left untouched"}
    if not isinstance(guards, list):
        return {"step": "materialise_guard_outcomes", "applies": False,
                "detail": "guards.json is not a list; left untouched"}

    pending = [g for g in guards if isinstance(g, dict) and "outcomes" not in g]
    if not pending:
        return {"step": "materialise_guard_outcomes", "applies": False,
                "detail": "every guard already carries an outcome block"}
    if dry_run:
        return {"step": "materialise_guard_outcomes", "applies": True,
                "detail": f"{len(pending)} guard(s) would gain a materialised outcome block",
                "count": len(pending)}
    import outcomes as _outcomes
    for guard in pending:
        _outcomes.block(guard)               # back-fills from the pre-D4 counters
    m.write_atomic(ledger, json.dumps(guards, ensure_ascii=False, indent=1))
    return {"step": "materialise_guard_outcomes", "applies": True, "count": len(pending),
            "detail": f"{len(pending)} guard(s) gained a materialised outcome block"}


def _step_ignore_derived(vault: Path, *, dry_run: bool) -> dict:
    """v0 -> v1: keep derived artifacts out of the store's git history.

    A rebuilt index is a large binary that changes on every rebuild and says nothing the notes
    do not. Committing it makes every `git pull` of a shared store a merge conflict over a
    SQLite file.
    """
    path = Path(vault) / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing = [name for name in REBUILDABLE + EXPENSIVE
               if name not in existing and name != "Index.md"]
    if not missing:
        return {"step": "ignore_derived_artifacts", "applies": False,
                "detail": "the store already ignores its derived artifacts"}
    if dry_run:
        return {"step": "ignore_derived_artifacts", "applies": True, "count": len(missing),
                "detail": f"{len(missing)} derived artifact(s) would be added to .gitignore: "
                          f"{', '.join(missing)}"}
    block = ("\n# derived artifacts - rebuilt by `nevertwice-store --rebuild --apply`\n"
             + "\n".join(missing) + "\n")
    m.write_atomic(path, existing.rstrip("\n") + "\n" + block if existing else block.lstrip("\n"))
    return {"step": "ignore_derived_artifacts", "applies": True, "count": len(missing),
            "detail": f"added {len(missing)} entry(ies) to .gitignore"}


#: Ordered per target version. A step is a pure planner when `dry_run=True`.
STEPS: dict = {1: (_step_materialise_outcomes, _step_ignore_derived)}


def plan(vault: Path) -> dict:
    """What migrating this store would do. Writes nothing, ever."""
    vault = Path(vault)
    current = detect(vault)
    steps = []
    for version in range(current + 1, SCHEMA_VERSION + 1):
        for step in STEPS.get(version, ()):
            steps.append({**step(vault, dry_run=True), "to_version": version})
    return {"schema_version_current": current, "schema_version_target": SCHEMA_VERSION,
            "up_to_date": current >= SCHEMA_VERSION,
            "steps": steps,
            "applicable": [s for s in steps if s.get("applies")],
            "vault": str(vault)}


# ── backup and rollback ─────────────────────────────────────────────────

def backup(vault: Path) -> Path:
    """Copy the store beside itself before anything is written.

    Copied, not moved, and never into the store: a backup inside the thing being migrated is
    swept up by the next rebuild.
    """
    vault = Path(vault)
    target = vault.parent / f"{vault.name}.backup-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(vault, target, dirs_exist_ok=False,
                    ignore=shutil.ignore_patterns(".git"))
    return target


def rollback_instructions(vault: Path, backup_path: Path) -> str:
    return (f"To roll back: remove {vault} and rename {backup_path} back to {vault.name}. "
            f"The Markdown notes were never modified, so a rollback only restores state files "
            f"and derived artifacts - and `nevertwice-store --rebuild --apply` regenerates "
            f"those from the notes at any time.")


# ── migrate ─────────────────────────────────────────────────────────────

def migrate(vault: Path, *, dry_run: bool = True) -> dict:
    vault = Path(vault)
    preview = plan(vault)
    if preview["up_to_date"]:
        return {**preview, "ok": True, "dry_run": dry_run, "backup": None,
                "detail": f"already at schema v{SCHEMA_VERSION}; nothing to do"}
    if dry_run:
        return {**preview, "ok": True, "dry_run": True, "backup": None,
                "detail": (f"v{preview['schema_version_current']} -> v{SCHEMA_VERSION}: "
                           f"{len(preview['applicable'])} step(s) would apply. "
                           f"Re-run with --apply to take a backup and perform them.")}

    backup_path = backup(vault)
    applied = []
    for version in range(preview["schema_version_current"] + 1, SCHEMA_VERSION + 1):
        for step in STEPS.get(version, ()):
            applied.append({**step(vault, dry_run=False), "to_version": version})
    stamp(vault, SCHEMA_VERSION,
          note=f"migrated from v{preview['schema_version_current']}")

    validation = validate(vault)
    return {**preview, "ok": validation["ok"], "dry_run": False,
            "backup": str(backup_path), "applied": applied, "validation": validation,
            "schema_version_current": detect(vault),
            "rollback": rollback_instructions(vault, backup_path),
            "detail": (f"migrated to v{SCHEMA_VERSION}; backup at {backup_path.name}"
                       if validation["ok"]
                       else f"migration completed but validation failed: {validation['problems']}")}


def validate(vault: Path) -> dict:
    """Is the store coherent after a migration? Checked, not assumed."""
    vault = Path(vault)
    problems = []
    version = detect(vault)
    if version != SCHEMA_VERSION:
        problems.append(f"schema marker says v{version}, expected v{SCHEMA_VERSION}")

    ledger = vault / "guards.json"
    if ledger.is_file():
        try:
            guards = json.loads(ledger.read_text(encoding="utf-8"))
            if not isinstance(guards, list):
                problems.append("guards.json is not a list")
            else:
                missing = [g.get("id") for g in guards
                           if isinstance(g, dict) and "outcomes" not in g]
                if missing:
                    problems.append(f"{len(missing)} guard(s) still lack an outcome block")
        except (OSError, ValueError) as exc:
            problems.append(f"guards.json unreadable: {exc}")

    try:
        notes = m._iter_all_notes()
    except Exception as exc:                 # noqa: BLE001 - a broken scan IS the finding
        notes = []
        problems.append(f"the note scan failed: {type(exc).__name__}")
    return {"ok": not problems, "problems": problems, "schema_version": version,
            "notes_readable": len(notes)}


# ── rebuild ─────────────────────────────────────────────────────────────

def rebuild(vault: Path, *, dry_run: bool = True, include_embeddings: bool = False) -> dict:
    """Reconstruct every derived artifact from the notes.

    The index is **removed** before it is rebuilt, and that is the whole of why two rebuilds
    are byte-identical: a build into a fresh file is deterministic.

    This function deliberately does **not** VACUUM. It used to, and a mutation deleting that
    call left every test green - correctly, because a file that was just unlinked has no freed
    pages to reclaim. `_vacuum` stays as the tool for the *other* path, the engine's ordinary
    in-place build, where it recovers 102400 bytes back to 81920; calling it here was doing
    nothing but suggesting it was load-bearing.
    """
    vault = Path(vault)
    targets = list(REBUILDABLE) + (list(EXPENSIVE) if include_embeddings else [])
    present = [name for name in targets if (vault / name).exists()]

    skipped = None
    if not include_embeddings:
        held = [name for name in EXPENSIVE if (vault / name).exists()]
        skipped = {"artifacts": held,
                   "why": ("rebuilding these needs an embedding model; deleting them on a "
                           "machine without one would destroy work that cannot be recreated. "
                           "Pass --include-embeddings deliberately.")}

    if dry_run:
        return {"ok": True, "dry_run": True, "vault": str(vault),
                "would_remove": present, "would_rebuild": list(REBUILDABLE),
                "preserved": [n for n in PRESERVED if (vault / n).exists()],
                "skipped": skipped,
                "detail": f"{len(present)} derived artifact(s) would be rebuilt; the notes "
                          f"are never touched"}

    for name in targets:
        path = vault / name
        if path.exists():
            path.unlink()

    rebuilt = []
    import index_sqlite as _ix
    try:
        m.rebuild_index()
        rebuilt.append("lexical index")
    except Exception as exc:                 # noqa: BLE001 - report, never abort the rest
        rebuilt.append(f"lexical index FAILED: {type(exc).__name__}")
    try:
        _ix.build()
        rebuilt.append(".index.sqlite")
    except Exception as exc:                 # noqa: BLE001
        rebuilt.append(f".index.sqlite FAILED: {type(exc).__name__}")

    return {"ok": not any("FAILED" in r for r in rebuilt), "dry_run": False,
            "vault": str(vault), "removed": present, "rebuilt": rebuilt,
            "preserved": [n for n in PRESERVED if (vault / n).exists()],
            "skipped": skipped, "digest": digest(vault),
            "detail": f"rebuilt {len(rebuilt)} artifact(s) from the notes"}


def _vacuum(db: Path) -> None:
    """Normalise the container so identical content produces an identical file."""
    if not Path(db).exists():
        return
    con = sqlite3.connect(str(db))
    try:
        con.isolation_level = None
        con.execute("VACUUM")
    finally:
        con.close()


#: Byte ranges in the SQLite header that record *how many times the file has been written*,
#: not what it contains: the file change counter and the version-valid-for number. A database
#: written twice cannot equal one written once, however identical their contents, so a
#: content comparison has to mask them - and say that it does, rather than quietly widening
#: what "identical" means.
SQLITE_VOLATILE_HEADER = ((24, 28), (92, 96))


def canonical_bytes(path: Path) -> bytes:
    """A file's bytes with SQLite's write counters masked out.

    Everything else - page layout, freelist, row order, FTS internals - still counts. This
    masks exactly the two fields that are guaranteed to differ between a file written once
    and the same file written again, and nothing else.
    """
    return canonical_bytes_of(Path(path).read_bytes())


def canonical_bytes_of(raw: bytes) -> bytes:
    """`canonical_bytes` for a blob already in hand, so a caller can hold a snapshot."""
    buf = bytearray(raw)
    if buf[:15] != b"SQLite format 3":
        return bytes(buf)
    for start, end in SQLITE_VOLATILE_HEADER:
        if len(buf) >= end:
            buf[start:end] = bytes(end - start)
    return bytes(buf)


def content_digest(db: Path) -> str | None:
    """A digest of what the index *contains*, independent of how SQLite laid it out.

    Two builds of the same notes agree here even when their bytes do not: a build over an
    existing database differs in its schema cookie and in the FTS index's internal segments,
    neither of which is content.

    The row sort is **defensive and currently unfalsifiable**, and that is worth saying rather
    than implying otherwise. SQLite promises no row order without `ORDER BY`; on this schema it
    happens to return rowid order, so removing the sort changes no digest and no test can tell
    the two apart - a mutation dropping it survived. It stays because relying on an order the
    engine does not promise is how a digest silently starts answering a different question, and
    a schema change is exactly when that would happen and nobody would look.
    """
    db = Path(db)
    if not db.is_file():
        return None
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'notes_fts%'"))
        hasher = hashlib.sha256()
        for table in tables:
            hasher.update(table.encode())
            for row in sorted(str(r) for r in con.execute(f'SELECT * FROM "{table}"')):
                hasher.update(row.encode("utf-8", "replace"))
    except sqlite3.Error as exc:
        return f"unreadable: {type(exc).__name__}"
    finally:
        con.close()
    return hasher.hexdigest()[:16]


def digest(vault: Path) -> dict:
    """A content digest of the derived artifacts - what "byte-equivalent" is checked against."""
    vault = Path(vault)
    out = {}
    for name in REBUILDABLE:
        path = vault / name
        out[name] = (hashlib.sha256(canonical_bytes(path)).hexdigest()[:16]
                     if path.is_file() else None)
    return out


# ── cli ─────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(prog="nevertwice-store",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--vault", help="the store (defaults to the configured one)")
    ap.add_argument("--migrate", action="store_true", help="plan or perform a migration")
    ap.add_argument("--rebuild", action="store_true", help="reconstruct derived artifacts")
    ap.add_argument("--digest", action="store_true", help="print the derived-artifact digest")
    ap.add_argument("--include-embeddings", action="store_true",
                    help="also rebuild the embedding cache (needs an embedder)")
    ap.add_argument("--apply", action="store_true", help="make a --migrate or --rebuild real")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    vault = Path(args.vault) if args.vault else m.VAULT
    if args.digest:
        result = {"vault": str(vault), "schema_version": detect(vault),
                  "digest": digest(vault)}
    elif args.migrate:
        result = migrate(vault, dry_run=not args.apply)
    elif args.rebuild:
        result = rebuild(vault, dry_run=not args.apply,
                         include_embeddings=args.include_embeddings)
    else:
        result = plan(vault)

    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else _render(result))
    return 0 if result.get("ok", True) else 1


def _render(result: dict) -> str:
    lines = ["", f"  store: {result.get('vault', '')}"]
    if "schema_version_current" in result:
        lines.append(f"  layout: v{result['schema_version_current']} "
                     f"(this build writes v{SCHEMA_VERSION})")
    for step in result.get("steps", []):
        mark = "would apply" if step.get("applies") else "no change"
        lines.append(f"    [{mark}] {step['step']}: {step['detail']}")
    for step in result.get("applied", []):
        lines.append(f"    [applied]   {step['step']}: {step['detail']}")
    for key in ("would_remove", "rebuilt", "removed", "preserved"):
        if result.get(key):
            lines.append(f"    {key}: {', '.join(map(str, result[key]))}")
    if result.get("skipped"):
        lines.append(f"    kept: {', '.join(result['skipped']['artifacts']) or '(none)'}")
        lines.append(f"          {result['skipped']['why']}")
    if result.get("digest"):
        for name, value in result["digest"].items():
            lines.append(f"    digest {name}: {value}")
    if result.get("backup"):
        lines.append(f"    backup: {result['backup']}")
    if result.get("rollback"):
        lines.append(f"    {result['rollback']}")
    if result.get("detail"):
        lines.append(f"  {result['detail']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
