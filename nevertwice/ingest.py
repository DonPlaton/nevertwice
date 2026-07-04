#!/usr/bin/env python3
"""Generic ingestion entrypoint — memory for ANY agent, not just Claude Code.

Any tool that can run a command can push a finished session here. The same
extraction → Patterns/Mistakes/Decisions → Context → embeddings pipeline runs,
tagged with the agent's name. No Claude Code JSONL or ~/.claude layout required.

    # inline text
    python ingest.py --project project_delta --agent my-bot --text "...transcript..."

    # from a file — a transcript, OR a document (.pdf / .docx / .md / .html / .txt)
    python ingest.py --project project_delta --agent my-bot --file run.log
    python ingest.py --project research --file paper.pdf      # mine a paper into memory

    # JSON on stdin (fields: project, agent, text/transcript_text, cwd, session_id)
    echo '{"project":"project_delta","agent":"my-bot","text":"..."}' | python ingest.py

    # SWEEP a whole directory of transcripts (turnkey auto-capture for ANY agent that
    # logs to disk — Cursor, Cline, Aider, Codex, …). Idempotent: an unchanged file is
    # skipped, a changed one re-ingested. Point it at the agent's log dir on a schedule:
    python ingest.py --dir ~/.codex/sessions --project myproj --agent codex
    python ingest.py --dir ./agent_logs --recursive --glob "*.jsonl,*.md"
    python ingest.py --dir ~/research/papers --glob "*.pdf,*.docx,*.md" --project research

`--project` is recommended; without it the project is derived from `--cwd` like
the live hook. A stable `--session-id` makes re-ingestion idempotent; otherwise a
fresh id is generated each call. In `--dir` mode the id is derived from the file path
+ content hash, so the same sweep is safe to re-run (e.g. from cron / Task Scheduler).
"""
import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m
import docparse                  # .pdf/.docx/.html/.md → text, so any document is ingestible

# DoS guard for --dir sweeps: skip files larger than this (a swept dir could hold a
# huge/sparse file that would block the vault lock for the whole read). audit 2026-06-18.
MAX_SWEEP_BYTES = int(os.environ.get("NEVERTWICE_MAX_SWEEP_BYTES", str(10 * 1024 * 1024)))


def _payload_from_stdin() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def sweep_session_id(path: Path, text: str) -> str:
    """A stable per-file session id for --dir sweeps: path-hash keeps re-runs of the
    SAME file idempotent (the processed-db skips it); content-hash makes a CHANGED file
    re-ingest under a fresh id. So a cron sweep over a growing log dir captures new
    work without ever double-mining an unchanged transcript."""
    hp = hashlib.sha1(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:8]
    ht = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
    return f"ingest-file-{hp}-{ht}"


def collect_transcripts(d: Path, globs, recursive: bool) -> list[Path]:
    """Files under `d` matching any glob, de-duplicated, with symlink/escape guards —
    the safe file list shared by the `--dir` sweep and the `watch` daemon. No side
    effects. A symlink (file or dir) whose REAL target escapes `d` is dropped, so a
    planted link to ~/.ssh/id_rsa or /etc/passwd can never be swept (audit 2026-06-18)."""
    base = d.resolve()

    def _inside(p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        return rp == base or base in rp.parents

    files = []
    for g in globs:
        files += (d.rglob(g) if recursive else d.glob(g))
    return [f for f in dict.fromkeys(files)                       # de-dup, keep order
            if f.is_file() and not f.is_symlink() and _inside(f)]


def ingest_files(files, project, agent, db, *, trigger="ingest-sweep",
                 max_new=None) -> tuple[int, int, int, int]:
    """Idempotently mine each transcript into memory against an ALREADY-LOADED
    processed-db, INSIDE an already-held vault lock. Returns (new, skipped, stored, errors).
    The caller owns the lock and the post-pass (rebuild_index / archive / commit) so a
    multi-directory sweep does one lock + one commit. Shared by `--dir` and `watch`,
    so both get the same DoS guard, content-hash idempotency and de-dup — no second copy
    of the logic to drift. A single bad file (exception in the extraction pipeline) is
    counted and skipped, never allowed to abort the rest of the sweep (audit 2026-06-18)."""
    new = skipped = stored = errors = 0
    for f in files:
        try:
            if f.stat().st_size > MAX_SWEEP_BYTES:        # DoS guard: skip a huge file
                skipped += 1                              # rather than block the lock on it
                continue
            txt = docparse.extract_text(f)                # .pdf/.docx/.html → text; else raw read
        except docparse.DocError as e:                    # missing PDF dep / corrupt doc — skip, don't abort
            print(f"[ingest] skip {f.name}: {e}", file=sys.stderr)
            skipped += 1
            continue
        except OSError:
            continue
        if len(txt) > MAX_SWEEP_BYTES:       # re-check after read: a file that grew past the cap
            skipped += 1                     # between stat() and read() (TOCTOU) is dropped here
            continue
        if not txt.strip():
            continue
        sid = sweep_session_id(f, txt)
        if sid in db:                          # unchanged file already mined → skip
            skipped += 1
            continue
        run_log: list[dict] = []
        try:
            ok = m.process_session(sid, str(f.parent), str(f), trigger, db,
                                   run_log=run_log, agent=agent, transcript_text=txt,
                                   project_override=project)
        except Exception as e:                 # one corrupt transcript must not abort the sweep
            print(f"[ingest] error on {f}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
            continue
        new += 1
        stored += 1 if ok else 0
        if max_new and new >= max_new:         # bound lock-hold per cycle; rest caught next sweep
            break
    return new, skipped, stored, errors


def _sweep(args, project, agent) -> None:
    """Ingest every matching transcript file under a directory, idempotently, in one
    vault lock. The turnkey cross-agent capture path: any tool that writes a session to
    disk is covered without bespoke hooks."""
    d = Path(args.dir)
    if not d.is_dir():
        print(f"[ingest] not a directory: {d}", file=sys.stderr)
        sys.exit(1)
    globs = [g.strip() for g in args.glob.split(",") if g.strip()]
    files = collect_transcripts(d, globs, args.recursive)
    if not files:
        print(f"[ingest] no files matching {args.glob!r} in {d}", file=sys.stderr)
        sys.exit(1)
    if not m.llm_available():
        print("[ingest] no LLM backend (cloud key unset + Ollama down) — aborting",
              file=sys.stderr)
        sys.exit(2)
    if not m.acquire_lock(timeout_s=120):
        print("[ingest] could not acquire vault lock — another process is busy",
              file=sys.stderr)
        sys.exit(3)
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        db = m.load_processed()
        new, skipped, stored, errors = ingest_files(files, project, agent, db)
        if new:                                  # only touch the index/git if work happened
            m.rebuild_index()
            m.archive_old_sessions()
            m.archive_old_typed()
            m.prune_processed_db(db)
            m.git_autocommit()
        print(f"[ingest] sweep of {d}: {new} new, {skipped} already-processed, "
              f"{stored} produced memory" + (f", {errors} errors" if errors else ""))
    finally:
        m.release_lock()


def main():
    ap = argparse.ArgumentParser(description="Push a finished agent session into memory.")
    ap.add_argument("--project", help="project name (recommended)")
    ap.add_argument("--agent", help="agent label stored on the session")
    ap.add_argument("--text", help="transcript text inline")
    ap.add_argument("--file", help="read transcript text from this file")
    ap.add_argument("--dir", help="SWEEP: ingest every transcript file in this dir (idempotent)")
    ap.add_argument("--glob", default="*.md,*.txt,*.log,*.jsonl,*.json,*.docx,*.html",
                    help="--dir mode: comma-separated filename globs (add *.pdf for PDFs — "
                         "needs `pip install pypdf`)")
    ap.add_argument("--recursive", action="store_true",
                    help="--dir mode: recurse into subdirectories")
    ap.add_argument("--cwd", help="working directory (for project derivation)")
    ap.add_argument("--session-id", help="stable id → idempotent re-ingestion")
    ap.add_argument("--trigger", default="ingest")
    args = ap.parse_args()

    if args.dir:
        agent = (args.agent or m.DEFAULT_AGENT).strip() or m.DEFAULT_AGENT
        _sweep(args, args.project, agent)
        return

    j = _payload_from_stdin()
    file_text = None
    if args.file:
        try:
            file_text = docparse.extract_text(args.file)   # transcript OR .pdf/.docx/.md/.html
        except docparse.DocError as e:
            print(f"[ingest] cannot read {args.file}: {e}", file=sys.stderr)
            sys.exit(1)
    text = args.text or file_text or j.get("text") or j.get("transcript_text")
    if not text or not text.strip():
        print("[ingest] no transcript text (use --text/--file or JSON stdin)",
              file=sys.stderr)
        sys.exit(1)

    project = args.project or j.get("project")
    agent = (args.agent or j.get("agent") or m.DEFAULT_AGENT).strip() or m.DEFAULT_AGENT
    cwd = args.cwd or j.get("cwd") or os.getcwd()
    sid = args.session_id or j.get("session_id") or f"ingest-{uuid.uuid4().hex[:16]}"
    trigger = args.trigger or j.get("trigger") or "ingest"

    if not m.llm_available():
        print("[ingest] no LLM backend (cloud key unset + Ollama down) — aborting",
              file=sys.stderr)
        sys.exit(2)

    if not m.acquire_lock(timeout_s=120):
        print("[ingest] could not acquire vault lock — another process is busy",
              file=sys.stderr)
        sys.exit(3)
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        db = m.load_processed()
        run_log: list[dict] = []
        ok = m.process_session(sid, cwd, "", trigger, db, run_log=run_log,
                               agent=agent, transcript_text=text,
                               project_override=project)
        if ok:
            m.rebuild_index()
            m.archive_old_sessions()
            m.archive_old_typed()
            m.prune_processed_db(db)
            m.git_autocommit()
            r = run_log[-1] if run_log else {}
            print(f"[ingest] OK — project={r.get('project','?')} agent={agent} "
                  f"P={r.get('patterns',0)} M={r.get('mistakes',0)} D={r.get('decisions',0)}")
        else:
            print("[ingest] nothing stored (empty/duplicate/off-topic or LLM failure) "
                  "— see status.txt / .logs", file=sys.stderr)
        m.write_status("Ingest", agent, run_log, 0, sid)
    finally:
        m.release_lock()


if __name__ == "__main__":
    main()
