"""Process_NOW: full scan over ALL Claude Code transcripts on the machine.

Triggered manually via Process_NOW.bat. Streams progress to stdout so the
user can watch sessions being processed in real time.

This is the Claude Code catch-up adapter (it walks Claude's JSONL store). Other
agents push sessions directly via ingest.py instead.

Semantics:
  - Walks every *.jsonl in NEVERTWICE_PROJECTS_ROOT, no time cutoff.
  - Skips sessions already in .processed_sessions.json (delete that file to
    force full reprocessing).
  - Sessions whose cwd is not a tracked project (a configured root or a git
    repo, excluding system/agent-internal paths) are recorded as skipped -
    the same rule as the live hook (audit C2).
"""

import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as _mh  # noqa: E402  (single import style; everything is _mh.<name>)

BAR = "=" * 72


def main():
    print(BAR)
    print("  Nevertwice - Process NOW (full scan, no date cutoff)")
    print(BAR)
    print(f"  Vault          : {_mh.VAULT}")
    print(f"  Projects root  : {_mh.PROJECTS_ROOT}")
    print(f"  Extraction LLM : {_mh.llm_backend_desc()}")
    print(BAR)
    print()

    if not _mh.PROJECTS_ROOT.exists():
        print(f"[ERROR] Projects dir not found: {_mh.PROJECTS_ROOT}")
        sys.exit(1)

    if not _mh.acquire_lock(timeout_s=60):
        print("[ERROR] Could not take the vault lock - another process holds it. Exiting.")
        sys.exit(2)

    try:
        _run(t0=time.time())
    finally:
        _mh.release_lock()


def _stat_or_none(p: Path):
    try:
        return p.stat()
    except OSError:
        return None


def _run(t0: float):
    db = _mh.load_processed()

    # One stat() per transcript - cache mtime AND size in one pass.
    seen = []
    for jl in _mh.PROJECTS_ROOT.rglob("*.jsonl"):   # recursive: don't miss nested (audit LOW)
        st = _stat_or_none(jl)
        if st is not None:
            seen.append((jl, st.st_mtime, st.st_size))
    seen.sort(key=lambda x: x[1])
    total = len(seen)
    print(f"Transcripts found   : {total}")
    print(f"Already processed   : {len(db)}")
    print()

    new = skipped_outside = skipped_done = failed = 0
    run_log: list[dict] = []

    for i, (jl, _, size) in enumerate(seen, 1):
        sid = jl.stem
        prefix = f"[{i:3d}/{total}]"

        if sid in db:
            skipped_done += 1
            print(f"{prefix} {jl.parent.name[:32]:<32} {sid[:8]} - already processed",
                  flush=True)
            continue

        size_kb = size // 1024
        cwd = _mh.read_session_meta(str(jl)).get("cwd") or str(jl.parent)

        if not _mh.is_tracked_project(cwd):
            _mh.mark_processed(db, sid, str(jl))
            skipped_outside += 1
            print(f"{prefix} {jl.parent.name[:32]:<32} {sid[:8]} ({size_kb:>5} KB) "
                  f"- outside {_mh.PROJECT_ROOT_DISPLAY}, skip", flush=True)
            continue

        print(f"{prefix} {jl.parent.name[:32]:<32} {sid[:8]} ({size_kb:>5} KB) "
              f"- extracting...", flush=True)
        ts = time.time()
        ok = _mh.process_session(sid, cwd, str(jl), "process_now", db, run_log=run_log)
        dt = time.time() - ts
        if ok:
            new += 1
            last = run_log[-1] if run_log else {}
            print(f"           OK in {dt:5.1f}s  project={last.get('project','?')}  "
                  f"P={last.get('patterns',0)} M={last.get('mistakes',0)} "
                  f"D={last.get('decisions',0)}", flush=True)
        else:
            failed += 1
            print(f"           FAIL after {dt:5.1f}s  (LLM extraction failed - check "
                  f"Gemini key + Ollama; see status.txt)", flush=True)

    if new:
        _mh.rebuild_index()
        _mh.archive_old_sessions()
        _mh.archive_old_typed()
        _mh.prune_processed_db(db)
    # LLM context-summary compaction belongs to this non-interactive heavy path,
    # not the live hook (which stays GPU-free under the vault lock - audit C4)
    _mh.maintain_contexts()
    _mh.write_status("ProcessNOW", "manual_button", run_log, 0, "process_now_full_scan")

    elapsed = time.time() - t0
    print()
    print(BAR)
    print(f"DONE in {elapsed:.1f}s")
    print(f"  New sessions processed  : {new}")
    print(f"  Previously processed    : {skipped_done}")
    print(f"  Outside {_mh.PROJECT_ROOT_DISPLAY} (skip): {skipped_outside}")
    print(f"  Failures                : {failed}")
    print(BAR)
    if new:
        print("Index.md rebuilt. Details in status.txt.")
    else:
        print("No new sessions. Index.md unchanged.")
    print()


if __name__ == "__main__":
    main()
