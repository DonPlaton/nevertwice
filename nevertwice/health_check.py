#!/usr/bin/env python3
"""Liveness / backlog health check for the memory system (audit F29/F32).

Writes a one-line verdict to <vault>/health.txt and prints it; exit code
0 = healthy, 1 = degraded. Meant to run from Task Scheduler alongside
process_now.py so a stalled Ollama or a growing backlog becomes visible
instead of silent.

    python health_check.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

HEALTH_FILE = m.VAULT / "health.txt"


def backlog_count() -> int:
    """Tracked-but-unprocessed transcripts under the projects root."""
    if not m.PROJECTS_ROOT.exists():
        return 0
    db = m.load_processed()
    n = 0
    for jl in m.PROJECTS_ROOT.rglob("*.jsonl"):   # recursive: match the sweep (audit LOW)
        if jl.stem in db:
            continue
        cwd = m.read_session_meta(str(jl)).get("cwd") or str(jl.parent)
        if m.is_tracked_project(cwd):
            n += 1
    return n


def newest_processed(db: dict):
    newest = None
    for v in db.values():
        if isinstance(v, dict) and v.get("processed_at"):
            try:
                t = datetime.fromisoformat(v["processed_at"])
            except ValueError:
                continue
            if newest is None or t > newest:
                newest = t
    return newest


def tasks_status() -> tuple[str, bool]:
    """Best-effort scheduled-task status (audit I-17): surfaces whether the
    safety-net tasks are registered/enabled so a silently-deleted task becomes
    visible in health.txt. Never raises — a parsing/import problem just reports
    'n/a' and does not affect the verdict."""
    try:
        import manage_tasks
        return manage_tasks.tasks_health()
    except Exception:
        return "n/a", False


def main():
    ollama = m.ollama_alive()
    db = m.load_processed()
    # always show the real queue size — backlog_count is filesystem-only and never calls
    # Ollama, so hiding it behind a down backend just masked a large queue until recovery
    # (launch-round audit). `healthy` below still requires the backend up.
    backlog = backlog_count()
    newest = newest_processed(db)
    tasks_str, tasks_degraded = tasks_status()
    healthy = ollama and backlog == 0 and not tasks_degraded
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    verdict = "OK" if healthy else "DEGRADED"
    line = (f"[{ts}] {verdict} | cloud={m.ACTIVE_CLOUD} | "
            f"ollama={'up' if ollama else 'DOWN'} | "
            f"backlog={backlog if backlog >= 0 else 'n/a'} | tracked={len(db)} | "
            f"embeddings={len(m.load_embed_cache())} | tasks={tasks_str} | "
            f"newest_processed={newest}")
    routing = f"        routing: {m.local_routing_desc()}"
    print(line)
    print(routing)
    try:
        m.write_atomic(HEALTH_FILE, line + "\n" + routing + "\n")
    except OSError as e:
        print(f"[health] could not write {HEALTH_FILE.name}: {e}", file=sys.stderr)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
