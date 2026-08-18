#!/usr/bin/env python3
"""Generic ingestion entrypoint - memory for ANY agent, not just Claude Code.

Any tool that can run a command can push a finished session here. The same
extraction → Patterns/Mistakes/Decisions → Context → embeddings pipeline runs,
tagged with the agent's name. No Claude Code JSONL or ~/.claude layout required.

    # inline text
    python ingest.py --project project_delta --agent my-bot --text "...transcript..."

    # from a file - a transcript, OR a document (.pdf / .docx / .md / .html / .txt)
    python ingest.py --project project_delta --agent my-bot --file run.log
    python ingest.py --project research --file paper.pdf      # mine a paper into memory

    # JSON on stdin (fields: project, agent, text/transcript_text, cwd, session_id)
    echo '{"project":"project_delta","agent":"my-bot","text":"..."}' | python ingest.py

    # SWEEP a whole directory of transcripts (turnkey auto-capture for ANY agent that
    # logs to disk - Cursor, Cline, Aider, Codex, …). Idempotent: an unchanged file is
    # skipped, and a GROWN text/jsonl file is delta-mined - only the appended tail is
    # extracted, tracked by a per-file byte watermark. Point it at the agent's log dir:
    python ingest.py --dir ~/.codex/sessions --project myproj --agent codex
    python ingest.py --dir ./agent_logs --recursive --glob "*.jsonl,*.md"
    python ingest.py --dir ~/research/papers --glob "*.pdf,*.docx,*.md" --project research

`--project` is recommended; without it the project is derived from `--cwd` like
the live hook. A stable `--session-id` makes re-ingestion idempotent; otherwise a
fresh id is generated each call. In `--dir` mode idempotency is two-tier: a per-file
WATERMARK (`.ingest_watermarks.json` in the store) records how many chars have been
mined, so a resumed/growing transcript costs one delta extraction instead of a full
re-mine (review 2026-08: content-hash-only ids re-mined one growing Codex rollout six
times - a full cloud extraction each - and minted duplicate notes every sweep); the
processed-db id (path+content hash) stays as the second tier, so re-running the same
sweep over the same state is still free.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime
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
MAX_SWEEP_BYTES = m.env_int("NEVERTWICE_MAX_SWEEP_BYTES", 10 * 1024 * 1024)


def _flatten_agent_jsonl(text: str) -> str:
    """Turn an agent-log JSONL transcript into just its conversational turns. Codex rollout logs
    are `{timestamp, type, payload}` per line where a single `session_meta`/`turn_context` line
    is 10-25KB of system scaffolding - so treating the file as flat text let that scaffolding
    consume the entire truncation head budget and mine ZERO real message content (critic R3,
    measured on a real 57MB corpus). Extract the user/assistant turns; fall back to the raw text
    if this doesn't look like agent JSONL."""
    def _s(v):                       # coerce any content value to str - a non-string 'text'
        return v if isinstance(v, str) else ("" if v is None else str(v))

    lines = text.splitlines()
    out, parsed = [], 0
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        parsed += 1
        obj = o.get("payload") if isinstance(o.get("payload"), dict) else o
        ptype = obj.get("type") or o.get("type")
        role = obj.get("role")
        content = obj.get("content")
        if isinstance(content, list):
            parts = [_s(c.get("text") or c.get("input_text")) if isinstance(c, dict) else _s(c)
                     for c in content]
            body = " ".join(p for p in parts if p).strip()
        else:
            body = _s(content).strip()
        if role in ("user", "assistant") and body:
            out.append(f"{role}: {body}")
        # capture the technical substance too - tool calls and their output are where the
        # real work (and the mistakes worth remembering) live (fix-review R3)
        elif ptype == "function_call":
            name = _s(obj.get("name")).strip()
            args = _s(obj.get("arguments")).strip()[:2000]
            if name or args:
                out.append(f"tool[{name}]: {args}")
        elif ptype in ("function_call_output", "tool_result"):
            res = _s(obj.get("output") or obj.get("result") or content).strip()[:2000]
            if res:
                out.append(f"tool_output: {res}")
    # only rewrite if it really parsed as JSONL and yielded content, else keep the original
    if parsed >= max(2, len(lines) // 2) and out:
        return "\n\n".join(out)
    return text


def _payload_from_stdin() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        # Agents pipe UTF-8 JSON; on a cp1251 console Python decoded the pipe with the
        # locale codec - a Russian transcript either died on byte 0x98 ('И'), silently
        # swallowed below, or was ingested as mojibake and extracted into permanent
        # garbage notes (review 2026-08 P3).
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        # bounded read: the sweep path caps file sizes, so the pipe path gets the same
        # cap instead of buffering an arbitrarily large payload (critic 2026-07)
        raw = sys.stdin.read(MAX_SWEEP_BYTES + 1)
        if len(raw) > MAX_SWEEP_BYTES:
            print(f"[ingest] stdin payload over {MAX_SWEEP_BYTES} bytes - refused "
                  f"(raise NEVERTWICE_MAX_SWEEP_BYTES to override)", file=sys.stderr)
            return {}
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[ingest] stdin payload unreadable ({type(e).__name__}: {e})",
              file=sys.stderr)
        return {}


def _path_hash(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:8]


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]


def sweep_session_id(path: Path, text: str) -> str:
    """The processed-db id for a FULL-file mine in --dir sweeps: path-hash + content-hash,
    so re-running a sweep over the same file state is idempotent. This id alone is NOT
    enough for growing transcripts - a resumed rollout gets a fresh content-hash every
    sweep and would be re-mined in full each time (measured: one Codex rollout mined six
    times, another sixteen). The watermark tier in `ingest_files` exists for exactly that:
    a grown text file is delta-mined from its recorded offset instead."""
    return f"ingest-file-{_path_hash(path)}-{_text_hash(text)}"


# ── Per-file watermarks: how much of each swept text file has already been mined ──────
# {path_hash: {"chars": int, "hash8": sha1(text[:chars])[:8], "path": str, "last": iso}}.
# The prefix hash detects a REWRITTEN file (rotation, truncation): if the recorded prefix
# no longer matches, the watermark is abandoned and the file re-mines in full once.
# Delta-mining applies only to line-oriented text formats; binary documents (.pdf/.docx)
# have no meaningful "appended tail" and keep pure content-hash behavior.
_WATERMARK_SUFFIXES = {".jsonl", ".json", ".md", ".txt", ".log"}
_WATERMARK_CAP = 2000            # entries; pruned oldest-first when exceeded


def _watermark_path() -> Path:
    return m.VAULT / ".ingest_watermarks.json"


def load_watermarks() -> dict:
    for f in (_watermark_path(), _watermark_path().with_suffix(".json.bak")):
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            if isinstance(d, dict):
                return d
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return {}


def save_watermarks(wm: dict) -> None:
    if len(wm) > _WATERMARK_CAP:                       # bound the file; oldest entries go
        for k in sorted(wm, key=lambda k: wm[k].get("last", ""))[:len(wm) - _WATERMARK_CAP]:
            wm.pop(k, None)
    text = json.dumps(wm, ensure_ascii=False)
    try:
        m.write_atomic(_watermark_path(), text)
        m.write_atomic(_watermark_path().with_suffix(".json.bak"), text)
    except OSError as e:
        # NOT silent (review 2026-08 I8): a persistently unwritable watermark file
        # means every sweep re-mines every growing transcript in full, forever -
        # the exact cost this tier exists to kill. Still non-fatal.
        print(f"[ingest] WARNING: cannot save watermarks ({e}) - "
              f"growing transcripts will re-mine until this is fixed", file=sys.stderr)


def collect_transcripts(d: Path, globs, recursive: bool) -> list[Path]:
    """Files under `d` matching any glob, de-duplicated, with symlink/escape guards -
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
                 max_new=None, settle_s: int | None = None) -> tuple[int, int, int, int]:
    """Idempotently mine each transcript into memory against an ALREADY-LOADED
    processed-db, INSIDE an already-held vault lock. Returns (new, skipped, stored, errors).
    The caller owns the lock and the post-pass (rebuild_index / archive / commit) so a
    multi-directory sweep does one lock + one commit. Shared by `--dir` and `watch`,
    so both get the same DoS guard, content-hash idempotency and de-dup - no second copy
    of the logic to drift. A single bad file (exception in the extraction pipeline) is
    counted and skipped, never allowed to abort the rest of the sweep (audit 2026-06-18)."""
    new = skipped = stored = errors = 0
    if settle_s is None:                       # watch pre-filters by mtime and passes 0
        settle_s = m.env_int("NEVERTWICE_SWEEP_SETTLE_S", 120)
    watermarks = load_watermarks()
    wm_dirty = False

    def _advance(hp: str, consumed: str, path: Path, size: int) -> None:
        """Record that `consumed` (a PREFIX of the file's text) has been mined.
        `size` is the file's byte size statted BEFORE reading - never stat here, the
        file may have grown during extraction and a late stat would make the size
        fast-skip below hide that growth (review 2026-08)."""
        nonlocal wm_dirty
        watermarks[hp] = {"chars": len(consumed), "hash8": _text_hash(consumed),
                          "bytes": size, "path": str(path),
                          "last": datetime.now().isoformat(timespec="seconds")}
        wm_dirty = True

    for f in files:
        suffix = f.suffix.lower()
        delta_capable = suffix in _WATERMARK_SUFFIXES
        hp = _path_hash(f)
        rec = watermarks.get(hp) if delta_capable else None
        if not isinstance(rec, dict):          # a corrupt entry crashed the whole sweep (Dc5)
            rec = None
        try:
            st_size = f.stat().st_size
        except OSError:
            continue
        # Size fast-skip (review 2026-08 D2): the old "cheapest possible skip" still
        # READ and prefix-HASHED the whole file every sweep - a 40MB rollout was fully
        # decoded every 4 hours just to be skipped. An unchanged byte size now skips
        # without a read; any change falls through to the prefix-hash proof.
        if rec and rec.get("bytes") == st_size and rec.get("chars"):
            skipped += 1
            continue
        try:
            if st_size > MAX_SWEEP_BYTES and not rec:
                # DoS guard: skip a huge UNWATERMARKED file (mining it whole would block the
                # vault lock). Say WHICH file, so an oversized session isn't silently absent
                # forever with no clue why (critic R3: a real 16.8MB Codex session was
                # invisibly excluded). A watermarked file is exempt from the stat-gate: only
                # its DELTA is mined, and the delta gets its own cap below.
                print(f"[ingest] skip {f.name}: {st_size} bytes > cap {MAX_SWEEP_BYTES} "
                      f"(raise NEVERTWICE_MAX_SWEEP_BYTES)", file=sys.stderr)
                skipped += 1                              # rather than block the lock on it
                continue
            if rec:
                # A watermarked file is by construction a plain-text format, and
                # docparse's separate MAX_DOC_BYTES gate raised DocError BEFORE the
                # delta logic could run - past 50MB the appended tail became permanently
                # invisible, with the skip message naming a different env var than the
                # one that could fix it (review 2026-08 D2). Read directly (same
                # utf-8/replace read docparse uses for text).
                raw = f.read_text(encoding="utf-8", errors="replace")
            else:
                raw = docparse.extract_text(f)            # .pdf/.docx/.html → text; else raw read
        except docparse.DocError as e:                    # missing PDF dep / corrupt doc - skip, don't abort
            print(f"[ingest] skip {f.name}: {e}", file=sys.stderr)
            skipped += 1
            continue
        except OSError:
            continue
        if not raw.strip():
            continue

        # ── Watermark tier (review 2026-08): a grown text file mines ONLY its appended
        # tail. The recorded prefix-hash proves the old content is unchanged underneath;
        # a rewritten/rotated file fails that check, DROPS the stale watermark and
        # re-mines in full once (under the normal unwatermarked size cap).
        delta_from = 0
        if rec:
            chars = rec.get("chars", 0)
            if (isinstance(chars, int) and 0 < chars <= len(raw)
                    and _text_hash(raw[:chars]) == rec.get("hash8")):
                if chars == len(raw):                     # unchanged content
                    _advance(hp, raw, f, st_size)         # refresh the byte size for the fast-skip
                    skipped += 1
                    continue
                delta_from = chars
            else:
                # rotation/truncation: abandon the watermark as documented - the stale
                # entry otherwise forced a full read + silent skip forever (D2)
                watermarks.pop(hp, None)
                wm_dirty = True
                rec = None
                if st_size > MAX_SWEEP_BYTES:             # back to the unwatermarked regime
                    print(f"[ingest] skip {f.name}: rewritten file, {st_size} bytes > cap "
                          f"{MAX_SWEEP_BYTES} (raise NEVERTWICE_MAX_SWEEP_BYTES)", file=sys.stderr)
                    skipped += 1
                    continue
        mine_raw = raw[delta_from:]
        if suffix == ".jsonl" and mine_raw and not mine_raw.endswith("\n"):
            # A LIVE writer may be mid-flush: consume only COMPLETE lines. The old code
            # recorded the watermark mid-line, so the head of the split line was dropped
            # by the flattener this sweep and its tail failed the JSON parse next sweep -
            # that turn was silently lost (review 2026-08 D10). The partial tail stays
            # beyond the watermark and mines once the line is whole. Applies only while
            # the file is still being written (mtime within settle_s): a SETTLED file's
            # unterminated final line is final content, not a flush in progress.
            try:
                live = settle_s > 0 and (time.time() - f.stat().st_mtime) < settle_s
            except OSError:
                live = False
            if live:
                cut = mine_raw.rfind("\n")
                if cut < 0:
                    skipped += 1                          # nothing complete yet; no advance
                    continue
                mine_raw = mine_raw[:cut + 1]
        consumed = raw[:delta_from + len(mine_raw)]
        # When the live-flush cut left a partial tail unconsumed, the stored byte size
        # must NOT equal the on-disk size: the size fast-skip would otherwise skip the
        # file forever if the writer never appends another byte (dies mid-flush), and
        # the final line would never mine even after settling. -1 forces the next sweep
        # through the prefix-hash path, which mines the tail once the file settles.
        wm_size = st_size if len(consumed) == len(raw) else -1
        # The cap is in BYTES; comparing len() (chars) let a Russian-language delta
        # through at nearly double the cap (review 2026-08 D2). Encode only when the
        # char lower-bound cannot prove the text is under it.
        mine_bytes = (len(mine_raw) if len(mine_raw) * 4 <= MAX_SWEEP_BYTES
                      else len(mine_raw.encode("utf-8", "replace")))
        if mine_bytes > MAX_SWEEP_BYTES:                  # cap applies to what is actually mined
            print(f"[ingest] skip {f.name}: {'delta ' if delta_from else ''}{mine_bytes} bytes "
                  f"> cap {MAX_SWEEP_BYTES} (raise NEVERTWICE_MAX_SWEEP_BYTES)", file=sys.stderr)
            skipped += 1                                  # watermark NOT advanced: content stays
            continue                                      # minable after the cap is raised

        txt = mine_raw
        if suffix == ".jsonl":
            try:
                txt = _flatten_agent_jsonl(mine_raw)      # keep the turns, drop 15-25KB of scaffolding
            except Exception:
                pass                                      # a malformed line must not abort the sweep
        if not txt.strip():                               # grew by non-content only (metadata lines,
            if delta_capable:                             # whitespace) - advance and move on, no LLM
                _advance(hp, consumed, f, wm_size)
            skipped += 1
            continue

        sid = (f"ingest-file-{hp}-w{delta_from}-{_text_hash(mine_raw)}" if delta_from
               else sweep_session_id(f, raw))
        # Pre-watermark builds hashed the FLATTENED .jsonl text, not the raw file, so no
        # legacy processed-db entry matches the raw-hash id - without this check the
        # first post-upgrade sweep re-mined every historical rollout in full, one cloud
        # extraction each (review 2026-08 D3).
        legacy_sid = (f"ingest-file-{hp}-{_text_hash(txt)}"
                      if not delta_from and txt is not mine_raw else None)
        if sid in db or (legacy_sid and legacy_sid in db):
            skipped += 1
            # Migration + belt-and-braces: a previously-mined file without a (current)
            # watermark gets one now, so its NEXT growth delta-mines instead of re-mining.
            if delta_capable and (not rec or rec.get("chars", 0) != len(raw)):
                _advance(hp, raw, f, st_size)
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
        # Advance the watermark only when the pipeline CONSUMED the text - process_session
        # marks the db on success and on deliberate skips, but an extraction failure leaves
        # the sid unmarked for retry, and the watermark must retry with it.
        if delta_capable and sid in db:
            _advance(hp, consumed, f, wm_size)
        if max_new and new >= max_new:         # bound lock-hold per cycle; rest caught next sweep
            break
    if wm_dirty:
        save_watermarks(watermarks)
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
        print("[ingest] no LLM backend (cloud key unset + Ollama down) - aborting",
              file=sys.stderr)
        sys.exit(2)
    if not m.acquire_lock(timeout_s=120):
        print("[ingest] could not acquire vault lock - another process is busy",
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
                    help="--dir mode: comma-separated filename globs (add *.pdf for PDFs - "
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

    # Read stdin only when the transcript is not already supplied by flag: the read
    # blocks until pipe EOF, so a wrapper that spawns `ingest --text ...` with an
    # open (never-closed) stdin pipe hung forever on text it would never use.
    j = {} if (args.text or args.file) else _payload_from_stdin()
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
        print("[ingest] no LLM backend (cloud key unset + Ollama down) - aborting",
              file=sys.stderr)
        sys.exit(2)

    if not m.acquire_lock(timeout_s=120):
        print("[ingest] could not acquire vault lock - another process is busy",
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
            print(f"[ingest] OK - project={r.get('project','?')} agent={agent} "
                  f"P={r.get('patterns',0)} M={r.get('mistakes',0)} D={r.get('decisions',0)}")
        else:
            print("[ingest] nothing stored (empty/duplicate/off-topic or LLM failure) "
                  "- see status.txt / .logs", file=sys.stderr)
        m.write_status("Ingest", agent, run_log, 0, sid)
    finally:
        m.release_lock()


if __name__ == "__main__":
    main()
