#!/usr/bin/env python3
"""`nevertwice watch` - always-on auto-capture for ANY agent that logs to disk.

Claude Code is captured by hooks (zero config). Every *other* agent that writes its
sessions to files - Codex, Cline, Roo Code, Aider, Gemini CLI … - gets the same
"magic" here: a tiny stdlib polling daemon that watches the known log directories and
idempotently mines finished sessions into memory. No new dependencies, no native hooks
required, no cron to configure.

    python -m nevertwice.watch                 # auto-detect known agent log dirs, poll every 60s
    python -m nevertwice.watch --list          # show what WOULD be watched, then exit
    python -m nevertwice.watch --once          # one sweep then exit (great for cron / a smoke test)
    python -m nevertwice.watch --interval 30
    python -m nevertwice.watch --dir ~/logs --project myproj --agent mybot   # add an explicit target
    python -m nevertwice.watch --no-auto --dir ~/logs --agent mybot          # ONLY explicit targets

Idempotency is inherited from the `--dir` sweep: a file is keyed by path+content hash, so
an unchanged transcript is never mined twice and a changed one is re-mined once. The whole
cycle takes ONE vault lock with a short timeout and yields immediately if Claude Code's
hook is mid-write, so the daemon never starves the live agent.

Why polling, not native file events: zero dependencies and identical behaviour on every
OS. A finished session is captured within one interval - that is the honest scope.
"""
import argparse
import os
import signal
import sys
import time
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m
import ingest as ig

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# A directory to watch: agent label, the dir, filename globs, recurse?, project (None =
# derive per-file like the live hook does).
Target = namedtuple("Target", "agent dir globs recursive project")

# Cap new transcripts mined per cycle so the daemon never holds the vault lock for minutes
# on a first run over a huge log dir - the remainder is caught on the next sweep.
MAX_PER_CYCLE = m.env_int("NEVERTWICE_WATCH_MAX_PER_CYCLE", 40)
# A transcript is only mined once its mtime has settled: a LIVE session file grows on every
# poll, and since the content hash keys the processed-db, each growth would mint a fresh
# session id → one new Session note + one LLM extraction per poll interval for an hours-long
# session (code-review 2026-07, HIGH). Waiting until the file stops changing means one mine
# per finished session. 0 disables (tests).
SETTLE_S = m.env_int("NEVERTWICE_WATCH_SETTLE_S", 120)

_STOP = False


def _vscode_globalstorage_bases() -> list[Path]:
    """`<editor>/User/globalStorage` for the VSCode-family editors, cross-platform.
    Cline / Roo Code (and most chat extensions) keep per-task transcripts under here."""
    home = Path.home()
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        root = home / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    editors = ["Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf"]
    return [root / e / "User" / "globalStorage" for e in editors]


def _project_roots() -> list[Path]:
    """Roots that hold real projects (for per-project log files like Aider's)."""
    roots = [Path.cwd()]
    raw = os.environ.get("NEVERTWICE_PROJECT_ROOTS", "")
    roots += [Path(os.path.expanduser(p.strip())) for p in raw.split(os.pathsep) if p.strip()]
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def known_targets() -> list[Target]:
    """Every known agent-log location that ACTUALLY EXISTS on this machine. Adding an
    agent is one row here - the daemon and `--list` both read this registry.

    Deliberately NOT included: Claude Code (`~/.claude/projects`) is already captured by
    the hooks - sweeping it too would double-mine. Cursor/Windsurf *chat* lives in a
    `state.vscdb` SQLite blob, not plain files, so it can't be swept directly (export it
    first - see docs/INTEGRATIONS.md); their extension transcripts (Cline/Roo) ARE files
    and are covered below."""
    home = Path.home()
    out: list[Target] = []
    # Auto-detected agent logs are labelled by AGENT name, not by a project derived from
    # the log directory: a central log dir (e.g. ~/.codex/sessions) carries no real project,
    # and deriving one from it could mislabel a session - and, if $HOME happens to be a git
    # repo, route a sensitive project's transcript to the cloud past the local-only gate
    # (audit 2026-06-18). Deterministic per-agent labels are safe; add an agent name to
    # NEVERTWICE_LOCAL_ONLY to keep that agent's captures off any cloud backend.

    # Codex CLI - JSONL rollouts under ~/.codex
    for d in (home / ".codex" / "sessions", home / ".codex" / "history"):
        if d.is_dir():
            out.append(Target("codex", d, ["*.jsonl"], True, "codex"))

    # Gemini CLI - JSON session logs under ~/.gemini/tmp
    g = home / ".gemini" / "tmp"
    if g.is_dir():
        out.append(Target("gemini-cli", g, ["*.json"], True, "gemini-cli"))

    # VSCode-family chat extensions that store per-task transcript files
    ext_agents = [("saoudrizwan.claude-dev", "cline"),
                  ("rooveterinaryinc.roo-cline", "roo")]
    for base in _vscode_globalstorage_bases():
        for ext_id, agent in ext_agents:
            tasks = base / ext_id / "tasks"
            if tasks.is_dir():
                out.append(Target(agent, tasks, ["*.json"], True, agent))

    # Aider - a per-project .aider.chat.history.md in each project root. Here the dir IS a
    # real project root, so derive the project per-file (project=None).
    for root in _project_roots():
        if list(root.glob(".aider.chat.history.md")) or list(root.glob("*/.aider.chat.history.md")):
            out.append(Target("aider", root, [".aider.chat.history.md"], True, None))

    return out


def _mtime_ok(f: Path, now: float) -> bool:
    """True when the file's mtime is at least SETTLE_S old (the session looks finished)."""
    try:
        return now - f.stat().st_mtime >= SETTLE_S
    except OSError:
        return False


def poll_cycle(targets: list[Target]) -> int:
    """One sweep over every target, idempotent, in a single short-held vault lock.
    Returns the number of newly-mined transcripts. Yields (returns 0) immediately if no
    LLM backend is up or the vault is busy - the live agent always wins the lock."""
    if not targets:
        return 0
    if not m.llm_available():
        return 0
    if not m.acquire_lock(timeout_s=30):       # short: never starve the Claude Code hook
        return 0
    total_new = 0
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        db = m.load_processed()
        for t in targets:
            if not t.dir.is_dir():
                continue
            files = ig.collect_transcripts(t.dir, t.globs, t.recursive)
            if SETTLE_S:                       # skip files still being written (see SETTLE_S)
                now = time.time()
                files = [f for f in files
                         if _mtime_ok(f, now)]
            if not files:
                continue
            budget = max(1, MAX_PER_CYCLE - total_new)     # share the per-cycle cap across targets
            new, skipped, stored, errors = ig.ingest_files(files, t.project, t.agent, db,
                                                           trigger="watch", max_new=budget)
            if new or errors:
                total_new += new
                print(f"[watch] {t.agent}: {new} new, {stored} produced memory"
                      + (f", {errors} errors" if errors else "") + f" ({t.dir})", flush=True)
        if total_new:                          # only touch the index/git when work happened
            m.rebuild_index()
            m.archive_old_sessions()
            m.archive_old_typed()
            m.prune_processed_db(db)
            m.git_autocommit()
    finally:
        m.release_lock()
    return total_new


def _resolve_targets(args) -> list[Target]:
    targets: list[Target] = [] if args.no_auto else known_targets()
    if args.dir:
        d = Path(os.path.expanduser(args.dir))
        globs = [g.strip() for g in args.glob.split(",") if g.strip()]
        targets.append(Target(args.agent or m.DEFAULT_AGENT, d, globs,
                              args.recursive, args.project))
    return targets


def _install_signal_handlers() -> None:
    def _handle(_sig, _frame):
        global _STOP
        _STOP = True
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):      # not on the main thread / unsupported
                pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Always-on auto-capture daemon for any agent that logs to disk.")
    ap.add_argument("--interval", type=int, default=60, help="seconds between sweeps (default 60)")
    ap.add_argument("--once", action="store_true", help="run one sweep then exit")
    ap.add_argument("--list", action="store_true", help="list detected targets and exit")
    ap.add_argument("--no-auto", action="store_true", help="skip auto-detection; only use --dir")
    ap.add_argument("--dir", help="add an explicit directory to watch")
    ap.add_argument("--glob", default="*.md,*.txt,*.log,*.jsonl,*.json",
                    help="--dir: comma-separated filename globs")
    ap.add_argument("--recursive", action="store_true", help="--dir: recurse into subdirectories")
    ap.add_argument("--project", help="--dir: project name (else derived per file)")
    ap.add_argument("--agent", help="--dir: agent label")
    args = ap.parse_args()
    if args.interval < 5:        # a sub-5s poll would take the vault lock too often and
        args.interval = 5        # starve the live agent; 5s is already near-instant capture
    targets = _resolve_targets(args)
    if args.list:
        if not targets:
            print("[watch] no known agent log dirs found on this machine.")
            print("        Point it at one explicitly:  python -m nevertwice.watch --dir <logs> --agent <name>")
            return 0
        print(f"[watch] {len(targets)} target(s):")
        for t in targets:
            print(f"  • {t.agent:10} {t.dir}  ({','.join(t.globs)}{', recursive' if t.recursive else ''})")
        return 0

    if not targets:
        print("[watch] nothing to watch - no known agent logs found and no --dir given.",
              file=sys.stderr)
        print("        Try:  python -m nevertwice.watch --list", file=sys.stderr)
        return 1

    print(f"[watch] watching {len(targets)} dir(s); backend: {m.llm_backend_desc()}", flush=True)
    for t in targets:
        print(f"        • {t.agent}: {t.dir}", flush=True)

    if args.once:
        n = poll_cycle(targets)
        print(f"[watch] one sweep done - {n} new transcript(s) captured.")
        return 0

    _install_signal_handlers()
    print(f"[watch] polling every {args.interval}s - Ctrl-C to stop.", flush=True)
    while not _STOP:
        try:
            poll_cycle(targets)
        except Exception as e:                 # a bad cycle must not kill the daemon
            print(f"[watch] cycle error ({type(e).__name__}: {e}) - continuing", file=sys.stderr)
        # sleep in short slices so a stop signal is honoured promptly
        for _ in range(max(1, args.interval)):
            if _STOP:
                break
            time.sleep(1)
    print("[watch] stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
