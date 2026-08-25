#!/usr/bin/env python3
"""Bring the memory you already have - with its provenance, and with a way back out.

`import_memory.py` already brings four sources in. What it does not do is the part that makes
a migration safe to *try*: it forgets where each note came from, and once written there is no
undo. Both matter more than the parsing.

**Provenance.** A note imported from somewhere else is not the same thing as a note this
system learned. Who wrote it, when, and from which record are the facts that let you audit it
later, decide whether to trust it, and tell an imported claim apart from an earned one. Each
note gets `imported_from`, `source_author`, `source_created`, `source_ref` and `import_batch`
in its own frontmatter, so the answer lives in the note and survives `git log`, a clone, and
this module being deleted.

**Reversibility.** An import you cannot undo is a decision you have to be sure about in
advance, which is exactly the wrong shape for "try it and see". Every apply records a batch;
`revert()` removes precisely the notes that batch created and nothing else. It is dry-run by
default, it verifies each note still declares the batch it is about to be removed for, and it
refuses to touch a note that has been edited into a different batch or superseded since.

Five sources: Claude auto-memory, a claude-mem SQLite export, a Mem0 JSON export, a Letta
MemFS archive, and generic Markdown/JSONL. The last is the honest fallback, and it is what a
sixth source starts as.

    nevertwice-migrate --from mem0 --path export.json --project acme --dry-run
    nevertwice-migrate --from mem0 --path export.json --project acme
    nevertwice-migrate --list
    nevertwice-migrate --revert <batch-id>            # dry run
    nevertwice-migrate --revert <batch-id> --apply

Writes go through `api.remember_lessons`, the same path as everything else - injection-shaped
content is rejected, secrets are redacted, one lock and one commit per batch. Standard library
only; `sqlite3` ships with Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api                      # noqa: E402
import memory_hook as m         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:               # noqa: BLE001 - a redirected stream may not support it
    pass

SCHEMA_VERSION = 1

SOURCES = ("claude-memory", "claude-mem", "mem0", "letta", "generic")

MAX_TITLE = 120
MAX_BODY = 1500

#: Frontmatter keys this module owns. Named once so `revert` and the provenance writer cannot
#: disagree about what an imported note looks like.
PROVENANCE_KEYS = ("imported_from", "source_author", "source_created", "source_ref",
                   "import_batch")


# ── records ─────────────────────────────────────────────────────────────

def record(title: str, *, source: str, ntype: str = "pattern", description: str = "",
           prevention: str = "", author: str = "", created: str = "", ref: str = "") -> dict:
    """One thing to import, with where it came from attached from the start.

    Provenance is built here rather than bolted on at write time, so a parser that forgets to
    record an author produces a record that visibly has none, instead of a note that silently
    claims to be ours.
    """
    return {"type": ntype if ntype in ("pattern", "mistake", "decision") else "pattern",
            "title": _clip(title, MAX_TITLE),
            "description": _clip(description, MAX_BODY),
            "prevention": _clip(prevention, MAX_BODY),
            "source": source, "author": author or "", "created": created or "", "ref": ref or ""}


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iso(value) -> str:
    """A timestamp we can store, or "" - never today's date standing in for an unknown one.

    Defaulting a missing `created` to now would quietly claim the memory was made during the
    import, which is the one thing provenance exists to prevent.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:                              # seconds or milliseconds since the epoch
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text[:32]


# ── parsers ─────────────────────────────────────────────────────────────

def parse_claude_memory(path: Path) -> list[dict]:
    """`~/.claude/projects/<slug>/memory/*.md` - Claude Code's own auto-memory."""
    out = []
    root = Path(path)
    files = sorted(root.rglob("*.md")) if root.is_dir() else ([root] if root.is_file() else [])
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        fm, body = _frontmatter(text)
        title = fm.get("title") or _first_heading(body) or file.stem.replace("-", " ")
        out.append(record(title, source="claude-memory",
                          ntype=str(fm.get("type") or "pattern"),
                          description=_strip_markdown(body),
                          author=str(fm.get("author") or "claude-code"),
                          created=_iso(fm.get("created") or fm.get("date")),
                          ref=file.name))
    return out


def parse_claude_mem(path: Path) -> list[dict]:
    """A claude-mem SQLite export.

    Column names differ between versions, so the reader looks for the first table with a
    text-ish content column rather than hardcoding a schema. A migration that only works
    against the exact version its author had is a migration that works once.
    """
    out = []
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return out
    try:
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for table in tables:
            try:
                rows = conn.execute(f'SELECT * FROM "{table}" LIMIT 5000').fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                data = {k: row[k] for k in row.keys()}
                body = _first_of(data, ("content", "memory", "text", "body", "summary",
                                        "value", "observation"))
                if not str(body or "").strip():
                    continue
                title = _first_of(data, ("title", "name", "label", "key")) or _headline(body)
                out.append(record(title, source="claude-mem",
                                  ntype=str(_first_of(data, ("type", "kind", "category"))
                                            or "pattern"),
                                  description=str(body),
                                  author=str(_first_of(data, ("author", "user", "user_id",
                                                              "agent")) or ""),
                                  created=_iso(_first_of(data, ("created_at", "created",
                                                                "timestamp", "ts", "date"))),
                                  ref=f"{table}#{_first_of(data, ('id', 'rowid')) or ''}"))
    finally:
        conn.close()
    return out


def parse_mem0(path: Path) -> list[dict]:
    """A Mem0 JSON export: a list, or `{results|memories: [...]}`."""
    data = _load_json(path)
    items = data if isinstance(data, list) else []
    if isinstance(data, dict):
        for key in ("results", "memories", "items", "data"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        body = _first_of(item, ("memory", "text", "content", "data"))
        if not str(body or "").strip():
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        out.append(record(_first_of(item, ("title", "name")) or _headline(body),
                          source="mem0", ntype=str(_first_of({**meta, **item},
                                                             ("type", "category")) or "pattern"),
                          description=str(body),
                          author=str(_first_of(item, ("user_id", "agent_id", "actor_id",
                                                      "author")) or ""),
                          created=_iso(_first_of(item, ("created_at", "updated_at",
                                                        "timestamp"))),
                          ref=str(_first_of(item, ("id", "memory_id")) or "")))
    return out


def parse_letta(path: Path) -> list[dict]:
    """A Letta (MemGPT) MemFS archive: a directory of blocks, or a JSON archive.

    Letta's core memory is labelled blocks (`human`, `persona`, …) and its archival memory is
    a list of passages. Both are imported, and the label is kept as the ref, because a block
    named `human` is a different kind of claim from an archival passage and losing that
    distinction would flatten the import into undifferentiated prose.
    """
    root = Path(path)
    out = []
    if root.is_dir():
        for file in sorted(root.rglob("*")):
            if file.is_file() and file.suffix.lower() in (".md", ".txt"):
                body = file.read_text(encoding="utf-8", errors="replace")
                if body.strip():
                    out.append(record(file.stem.replace("_", " "), source="letta",
                                      description=_strip_markdown(body),
                                      ref=f"block:{file.stem}"))
        return out

    data = _load_json(root)
    blocks = []
    if isinstance(data, dict):
        for key in ("blocks", "memory", "core_memory"):
            value = data.get(key)
            if isinstance(value, list):
                blocks += value
            elif isinstance(value, dict):
                blocks += [{"label": k, "value": v} for k, v in value.items()]
        for key in ("archival", "archival_memory", "passages"):
            if isinstance(data.get(key), list):
                blocks += [{**p, "_archival": True} if isinstance(p, dict)
                           else {"value": p, "_archival": True} for p in data[key]]
    elif isinstance(data, list):
        blocks = data
    for block in blocks:
        if not isinstance(block, dict):
            continue
        body = _first_of(block, ("value", "text", "content", "memory"))
        if not str(body or "").strip():
            continue
        label = str(_first_of(block, ("label", "name", "id")) or "")
        kind = "archival" if block.get("_archival") else "block"
        out.append(record(label or _headline(body), source="letta", description=str(body),
                          author=str(_first_of(block, ("agent_id", "user_id", "author")) or ""),
                          created=_iso(_first_of(block, ("created_at", "timestamp"))),
                          ref=f"{kind}:{label}" if label else kind))
    return out


def parse_generic(path: Path) -> list[dict]:
    """Markdown bullets or JSONL records - the fallback, and what a new source starts as."""
    root = Path(path)
    files = ([root] if root.is_file()
             else sorted(p for p in root.rglob("*")
                         if p.is_file() and p.suffix.lower() in (".md", ".jsonl", ".txt")))
    out = []
    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        if file.suffix.lower() == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                body = _first_of(item, ("memory", "text", "content", "description", "body"))
                if not str(body or "").strip():
                    continue
                out.append(record(_first_of(item, ("title", "name")) or _headline(body),
                                  source="generic",
                                  ntype=str(_first_of(item, ("type",)) or "pattern"),
                                  description=str(body),
                                  author=str(_first_of(item, ("author", "user")) or ""),
                                  created=_iso(_first_of(item, ("created", "created_at",
                                                                "date", "timestamp"))),
                                  ref=file.name))
            continue
        for line in text.splitlines():
            bullet = re.match(r"^\s*[-*+]\s+(.{6,})$", line)
            if bullet:
                body = bullet.group(1).strip()
                out.append(record(_headline(body), source="generic", description=body,
                                  ref=file.name))
    return out


PARSERS = {"claude-memory": parse_claude_memory, "claude-mem": parse_claude_mem,
           "mem0": parse_mem0, "letta": parse_letta, "generic": parse_generic}


def _first_of(data: dict, keys):
    for key in keys:
        if isinstance(data, dict) and data.get(key) not in (None, ""):
            return data[key]
    return None


def _headline(body) -> str:
    text = " ".join(str(body or "").split())
    cut = re.split(r"(?<=[.!?])\s", text)[0] if text else ""
    return _clip(cut or text, MAX_TITLE)


def _strip_markdown(text: str) -> str:
    text = re.sub(r"^#+\s*", "", str(text or ""), flags=re.M)
    return " ".join(text.split())


def _first_heading(body: str) -> str:
    match = re.search(r"^#+\s*(.+)$", str(body or ""), flags=re.M)
    return match.group(1).strip() if match else ""


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[3:end].split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            fm[key.strip()] = value.strip().strip('"')
    return fm, text[end + 4:]


def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


# ── the batch ledger ────────────────────────────────────────────────────

def _ledger_path() -> Path:
    return m.VAULT / ".migrations.json"


def _ledger_load() -> list[dict]:
    try:
        data = json.loads(_ledger_path().read_text(encoding="utf-8"))
        return [b for b in data if isinstance(b, dict) and b.get("id")] \
            if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _ledger_save(batches: list[dict]) -> None:
    m.VAULT.mkdir(parents=True, exist_ok=True)
    m.write_atomic(_ledger_path(), json.dumps(batches, indent=1, ensure_ascii=False))


def _batch_id(source: str, records: list[dict], existing=None) -> str:
    """Deterministic in its content, and unique against the ledger it is joining.

    The content hash makes the id meaningful and the timestamp orders it, but neither makes it
    unique: the first version stamped to the second, and two imports of one export that both
    finished inside the same second got the *same* id - so the second `apply` appended a
    duplicate entry and `revert` matched whichever came first. It passed locally only because
    each import spent over a second failing to reach an absent embedder, and failed on CI,
    where that wait does not happen. A faster machine is not a stress test; it is Tuesday.

    So uniqueness is established against the ledger rather than assumed from the clock:
    microseconds narrow the window, and the collision check closes it.
    """
    payload = "|".join(f"{r['source']}:{r['title']}:{r['ref']}" for r in records)
    digest = hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:8]
    taken = {b["id"] for b in (existing if existing is not None else _ledger_load())}
    stamp = f"{datetime.now():%Y%m%d%H%M%S%f}"
    candidate = f"{source}-{stamp}-{digest}"
    suffix = 1
    while candidate in taken:
        candidate = f"{source}-{stamp}-{digest}-{suffix}"
        suffix += 1
    return candidate


def batches() -> list[dict]:
    return _ledger_load()


# ── plan / apply / revert ───────────────────────────────────────────────

def plan(source: str, path, project: str) -> dict:
    """What an import would do, writing nothing. The dry run."""
    if source not in PARSERS:
        return {"ok": False, "detail": f"unknown source {source!r}; expected one of "
                                       f"{', '.join(SOURCES)}"}
    records = PARSERS[source](Path(path))
    with_author = sum(1 for r in records if r["author"])
    with_created = sum(1 for r in records if r["created"])
    kinds: dict[str, int] = {}
    for r in records:
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
    return {
        "ok": True, "source": source, "path": str(path), "project": project,
        "found": len(records), "by_type": kinds,
        "with_author": with_author, "with_timestamp": with_created,
        # Stated rather than silently tolerated: an import that loses provenance for most of
        # its records is one you want to know about before it lands, not after.
        "provenance_gaps": {"missing_author": len(records) - with_author,
                            "missing_timestamp": len(records) - with_created},
        "sample": [{"title": r["title"], "type": r["type"], "author": r["author"],
                    "created": r["created"], "ref": r["ref"]} for r in records[:5]],
        "written": 0, "dry_run": True,
    }


def apply(source: str, path, project: str) -> dict:
    """Import for real, and record a batch that can undo exactly this.

    Importing the same export twice does **not** duplicate: the shared write path derives a
    note's stem from its title and date, so the second run rewrites the same files and
    re-stamps them to the new batch. That is the behaviour you want - a re-run after a partial
    import converges instead of doubling - but it means the older batch's stems now belong to
    the newer batch. `revert` checks each note's own stamp for exactly this reason, so undoing
    the newer batch removes them and undoing the older one then finds nothing of its own left
    and says so, rather than deleting notes another batch has claimed.
    """
    preview = plan(source, path, project)
    if not preview.get("ok"):
        return preview
    records = PARSERS[source](Path(path))
    if not records:
        return {**preview, "dry_run": False, "detail": "nothing to import"}

    ledger = _ledger_load()
    batch = _batch_id(source, records, existing=ledger)
    stems = api.remember_lessons(
        [{"type": r["type"], "title": r["title"], "description": r["description"],
          "prevention": r["prevention"]} for r in records], project=project)

    stamped = []
    for stem, rec in zip(stems, records):
        if _stamp(stem, rec, batch):
            stamped.append(stem)

    entry = {"id": batch, "source": source, "path": str(path), "project": project,
             "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "stems": stamped, "found": len(records), "written": len(stems),
             "schema_version": SCHEMA_VERSION}
    # Re-read rather than reusing `ledger`: the writes above take time, and a ledger that was
    # appended to meanwhile must not be clobbered by a stale snapshot.
    _ledger_save(_ledger_load() + [entry])
    return {**preview, "dry_run": False, "written": len(stems), "batch": batch,
            "stamped": len(stamped), "stems": stamped,
            "detail": f"imported {len(stems)} note(s) as batch {batch}; "
                      f"revert with `nevertwice-migrate --revert {batch} --apply`"}


def _stamp(stem: str, rec: dict, batch: str) -> bool:
    """Write provenance into the note's own frontmatter.

    A single-line splice, like the inbox's `reviewed:` stamp and for the same reason: the
    frontmatter reader is deliberately tolerant and lossy, so re-serialising it would silently
    drop whatever it did not understand. Doing it after the write rather than inside it keeps
    the shared write path untouched by a one-shot migration concern.
    """
    parsed = m.parse_typed_stem(stem)
    if not parsed:
        return False
    folder = m.TYPE_FOLDER.get(parsed["ntype"])
    if not folder:
        return False
    path = m.VAULT / folder / f"{stem}.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    lines = [ln for ln in text[3:end].split("\n")
             if ln.strip() and not any(ln.strip().startswith(f"{k}:") for k in PROVENANCE_KEYS)]
    lines += [f"imported_from: {rec['source']}",
              f"source_author: {rec['author']}",
              f"source_created: {rec['created']}",
              f"source_ref: {rec['ref']}",
              f"import_batch: {batch}"]
    m.write_atomic(path, "---" + "\n".join([""] + lines) + text[end:])
    return True


def provenance(stem: str) -> dict:
    """What a note says about where it came from. `{}` when it was not imported."""
    parsed = m.parse_typed_stem(stem)
    folder = m.TYPE_FOLDER.get((parsed or {}).get("ntype", ""))
    if not folder:
        return {}
    path = m.VAULT / folder / f"{stem}.md"
    if not path.exists():
        return {}
    fm, _ = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return {k: fm[k] for k in PROVENANCE_KEYS if k in fm}


def revert(batch_id: str, *, dry_run: bool = True) -> dict:
    """Remove exactly the notes one batch created.

    Dry-run by default, and every note is re-checked against its own `import_batch` stamp
    before removal. The ledger alone is not enough: it records what *was* written, while the
    note records what it *is* now. A note that has since been superseded, edited into another
    batch, or hand-written over must be left alone - deleting from someone's memory on the
    strength of a stale index entry is the failure this check exists to prevent.
    """
    entry = next((b for b in _ledger_load() if b["id"] == batch_id), None)
    if entry is None:
        return {"ok": False, "detail": f"no such batch: {batch_id}",
                "known": [b["id"] for b in _ledger_load()]}

    removable, skipped = [], []
    for stem in entry.get("stems", []):
        stamp = provenance(stem)
        if not stamp:
            skipped.append({"stem": stem, "why": "the note is gone already"})
        elif stamp.get("import_batch") != batch_id:
            skipped.append({"stem": stem,
                            "why": f"the note now belongs to batch "
                                   f"{stamp.get('import_batch') or '(none)'}"})
        else:
            removable.append(stem)

    if dry_run:
        return {"ok": True, "batch": batch_id, "dry_run": True, "removed": [],
                "would_remove": removable, "skipped": skipped,
                "detail": f"would remove {len(removable)} note(s), leaving "
                          f"{len(skipped)} that no longer match this batch"}

    removed = []
    for stem in removable:
        parsed = m.parse_typed_stem(stem)
        path = m.VAULT / m.TYPE_FOLDER[parsed["ntype"]] / f"{stem}.md"
        try:
            path.unlink()
            removed.append(stem)
        except OSError:
            skipped.append({"stem": stem, "why": "the file could not be removed"})

    remaining = [b for b in _ledger_load() if b["id"] != batch_id]
    _ledger_save(remaining)
    try:
        m.rebuild_index()
    except Exception:           # noqa: BLE001 - a stale index must not fail the revert
        pass
    return {"ok": True, "batch": batch_id, "dry_run": False, "removed": removed,
            "skipped": skipped,
            "detail": f"removed {len(removed)} note(s); batch {batch_id} is no longer on record"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="nevertwice-migrate", description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="source", choices=SOURCES, help="which memory system")
    ap.add_argument("--path", help="the export file or directory")
    ap.add_argument("--project", default="imported", help="project to file the notes under")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--list", action="store_true", help="list import batches on record")
    ap.add_argument("--revert", metavar="BATCH", help="undo one batch (dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="make a --revert real")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list:
        found = batches()
        print(json.dumps(found, indent=2) if args.json else _render_batches(found))
        return 0
    if args.revert:
        result = revert(args.revert, dry_run=not args.apply)
    elif args.source and args.path:
        result = (plan if args.dry_run else apply)(args.source, Path(args.path), args.project)
    else:
        ap.print_help()
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json
          else _render(result))
    return 0 if result.get("ok", True) else 1


def _render_batches(found: list[dict]) -> str:
    if not found:
        return "\n  no import batches on record\n"
    lines = ["", f"  {len(found)} import batch(es) on record", "  " + "-" * 60]
    for b in found:
        lines.append(f"  {b['id']}")
        lines.append(f"      {b['source']} -> {b['project']} · {b['when']} · "
                     f"{len(b.get('stems', []))} note(s)")
    lines.append("")
    return "\n".join(lines)


def _render(result: dict) -> str:
    if not result.get("ok", True):
        return f"\n  FAILED  {result.get('detail', '')}\n"
    if "would_remove" in result or "removed" in result:
        lines = ["", f"  batch {result['batch']}"]
        if result["dry_run"]:
            lines.append(f"      would remove {len(result['would_remove'])} note(s)")
        else:
            lines.append(f"      removed {len(result['removed'])} note(s)")
        for item in result.get("skipped", []):
            lines.append(f"      skipped {item['stem']}: {item['why']}")
        lines.append("")
        return "\n".join(lines)
    lines = ["", f"  {result['source']} -> {result['project']}",
             f"      found {result['found']} record(s): "
             + ", ".join(f"{n} {t}" for t, n in sorted(result["by_type"].items())),
             f"      with an author: {result['with_author']} · "
             f"with a timestamp: {result['with_timestamp']}"]
    gaps = result["provenance_gaps"]
    if gaps["missing_author"] or gaps["missing_timestamp"]:
        lines.append(f"      provenance gaps: {gaps['missing_author']} without an author, "
                     f"{gaps['missing_timestamp']} without a timestamp")
    for item in result.get("sample", []):
        lines.append(f"      · {item['title'][:60]}")
    if result.get("dry_run"):
        lines.append("      (dry run - nothing was written)")
    else:
        lines.append(f"      {result.get('detail', '')}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
