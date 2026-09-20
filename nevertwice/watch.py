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
an unchanged transcript is never mined twice and a changed one is re-mined once.

The daemon yields to the live agent in three ways, and the third is the one that was missing:
it discovers files BEFORE taking the vault lock, it gives up acquiring after 30 s if Claude
Code's hook is mid-write, and it gives the lock BACK within MAX_LOCK_S plus the one extraction
already running. A cap on the count of transcripts is not a cap on the duration - 40 of them at
~33 s each is twenty minutes, and a hook waiting on that lock gives up after 180 s and logs
"Aborting", losing the session. What the budget defers, the next cycle mines.

That second term is not small and this module does not pretend it is: `lock_budget_report()`
adds MAX_LOCK_S to `memory_hook.extraction_ceiling_s()` - the worst case of the cloud chain
followed by the Ollama chain, both with retries - and compares the sum to the hook's own wait.
On the shipped timeouts the sum is larger, so the daemon says so at startup and prints what
each cycle actually held whenever it overran its budget. Bounding an extraction that is
already running would mean either cutting it (the transcript defers; nothing is lost, but a
slow one never mines) or moving extraction out of the lock entirely. Neither is this module's
call to make, and neither is served by a comment asserting a bound that is not there.

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
try:
    from . import memory_hook as m
except ImportError:                 # run as a script, not as a package
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
# ...and cap it in the unit that actually matters. A COUNT is not a duration: 40 transcripts at
# up to ~33 s each on the Ollama fallback is a twenty-minute hold, while a SessionEnd or
# PreCompact hook waits 180 s for that lock and then logs "Aborting" - the cost of which is the
# whole session's knowledge. The budget is checked before each transcript is started, so the
# worst hold is this plus the one extraction already running - which is NOT bounded by it, and
# on the shipped timeouts exceeds the hook's whole wait on its own (see `lock_budget_report`).
# What the budget defers, the next cycle mines.
MAX_LOCK_S = m.env_float("NEVERTWICE_WATCH_MAX_LOCK_S", 120.0)
# The index rebuild, the two archive passes and the git commit run under the same lock AFTER
# the mining loop. They used to run after the budget was already spent, so MAX_LOCK_S described
# a part of the hold and the rest was whatever it was. The mining deadline now stops this much
# early to leave them room inside it. Never more than a fifth of the budget: you cannot reserve
# what you do not have, and a tiny budget (tests) must still mine something.
HOUSEKEEPING_RESERVE_S = m.env_float("NEVERTWICE_WATCH_HOUSEKEEPING_S", 20.0)
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
    """Roots that hold real projects (for per-project log files like Aider's).
    Delegates the env parsing to memory_hook._split_roots - this local copy split
    only on os.pathsep while the hook also accepts commas, so the SAME config value
    silently meant different things in the two consumers and comma-separated roots
    were never watched (review 2026-08 C6)."""
    roots = [Path.cwd()]
    # m.PROJECT_ROOTS is the hook's already-parsed constant (same env pair, same
    # split rule, expanduser applied inside _split_roots) - re-reading the env here
    # had already drifted once (local expanduser the hook lacked, review 2026-08).
    roots += [Path(p) for p in m.PROJECT_ROOTS]
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


def lock_hold_ceiling_s() -> float:
    """Worst case for how long ONE cycle can hold the vault lock: the mining budget plus the
    one extraction the budget cannot interrupt, whose ceiling the engine derives from its own
    timeouts. Not an estimate of the usual cycle - the usual cycle is a few seconds."""
    return MAX_LOCK_S + m.extraction_ceiling_s()


def lock_budget_report() -> str:
    """One line of arithmetic, printed at startup, instead of a docstring's assurance.

    A hook that waits out `acquire_lock` does not crash: it logs "Aborting" and continues
    read-only, so that session's writes are simply never made. Whether that can happen is a
    subtraction between three numbers this module and the engine both already hold.

    The remedy it names is the one that SUBTRACTS. The first version offered
    NEVERTWICE_WATCH_MAX_LOCK_S first, and on the shipped timeouts that is advice that
    cannot work: the extraction ceiling alone is twice the hook's wait, so the gap survives
    a budget of zero and the operator has traded mining for nothing. A remedy printed where
    it has no effect is worse than none - it ends the search. So the mining budget is offered
    only when there is a budget to lower, with the bound it has to clear.
    """
    # The ceiling comes from lock_hold_ceiling_s() and nowhere else, so the line printed and
    # the number tested can never disagree; the extraction term is named separately because
    # it is the one the operator cannot subtract from by changing this module's budget.
    ceiling, extraction, wait = (lock_hold_ceiling_s(), m.extraction_ceiling_s(),
                                 m.HOOK_LOCK_WAIT_S)
    head = (f"vault lock: up to {MAX_LOCK_S:.0f}s of mining + one extraction "
            f"(worst case {extraction:.0f}s) = {ceiling:.0f}s; "
            f"a SessionEnd hook waits {wait:.0f}s for it")
    if ceiling <= wait:
        return head
    head += (" - so a cycle can hold it LONGER than the hook will wait, and that session's "
             "writes are skipped.")
    if extraction >= wait:
        return head + (f" Lowering the mining budget cannot close this: the extraction alone "
                       f"is {extraction:.0f}s against a {wait:.0f}s wait, and it is not "
                       f"interruptible. Only NEVERTWICE_TIMEOUT/NEVERTWICE_RETRIES subtract "
                       f"from that term - or run the daemon when no agent is live.")
    return head + (f" Lower NEVERTWICE_WATCH_MAX_LOCK_S below {wait - extraction:.0f}s, or "
                   f"lower NEVERTWICE_TIMEOUT/NEVERTWICE_RETRIES, or poll less often.")


def poll_cycle(targets: list[Target]) -> int:
    """One sweep over every target, idempotent, in a single BOUNDED vault lock.

    Returns the number of newly-mined transcripts. Yields (returns 0) immediately if no LLM
    backend is up or the vault is busy. Discovery runs BEFORE the lock: it reads the agents'
    log directories, not the store.

    The hold is MAX_LOCK_S - mining and housekeeping together - plus the one extraction that
    was already running when the budget ran out, which nothing here can interrupt. When the
    sum exceeds the budget the cycle says so on stderr with both terms, because a hook that
    waits out this lock skips that session's writes and logs only "Aborting"."""
    if not targets:
        return 0
    if not m.llm_available():
        return 0
    # Read-only over the agents' log dirs, and on a large one the slowest thing in the cycle
    # that is not an extraction. It has no business inside the store's lock.
    work: list[tuple[Target, list]] = []
    for t in targets:
        if not t.dir.is_dir():
            continue
        files = ig.collect_transcripts(t.dir, t.globs, t.recursive)
        if SETTLE_S:                           # skip files still being written (see SETTLE_S)
            now = time.time()
            files = [f for f in files if _mtime_ok(f, now)]
        if files:
            work.append((t, files))
    if not work:
        return 0
    if not m.acquire_lock(timeout_s=30):       # short: never starve the Claude Code hook
        return 0
    total_new = 0
    took = time.monotonic()
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        # The mining loop stops early enough for the housekeeping below to run inside the
        # budget: the budget is a promise about the HOLD, and the tail is part of the hold.
        deadline = took + max(0.0, MAX_LOCK_S - min(HOUSEKEEPING_RESERVE_S, MAX_LOCK_S * 0.2))
        db = m.load_processed()
        for t, files in work:
            budget = MAX_PER_CYCLE - total_new  # share the per-cycle cap across targets
            if budget <= 0 or time.monotonic() >= deadline:
                break                           # spent - the rest waits for the next cycle
            # settle_s=0: the SETTLE_S filter above already dropped still-being-written
            # files, so ingest_files must not re-apply its own live-flush heuristic
            # (it would defer a settled file's final unterminated line)
            new, skipped, stored, errors = ig.ingest_files(files, t.project, t.agent, db,
                                                           trigger="watch", max_new=budget,
                                                           settle_s=0, deadline=deadline)
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
        held = time.monotonic() - took
        if held > MAX_LOCK_S:
            # Say what it cost, with the term that is not bounded by the budget named. The
            # alternative is a silent overrun and an operator who finds out from a hook log
            # that a session was skipped, with nothing to connect the two.
            print(f"[watch] held the vault lock {held:.1f}s over a {MAX_LOCK_S:.1f}s budget "
                  f"- the extraction in flight is not interruptible by it "
                  f"(worst case {m.extraction_ceiling_s():.0f}s)", file=sys.stderr, flush=True)
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
    print(f"        {lock_budget_report()}", flush=True)
    for t in targets:
        print(f"        • {t.agent}: {t.dir}", flush=True)

    if args.once:
        try:
            n = poll_cycle(targets)
        except Exception as e:  # the scheduled --once run must report, not traceback:
            # its stderr lands in an unread %TEMP% log and the task keeps "succeeding"
            print(f"[watch] sweep failed ({type(e).__name__}: {e})", file=sys.stderr)
            return 1
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
