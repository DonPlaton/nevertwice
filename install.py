#!/usr/bin/env python3
"""One-command installer for Nevertwice.

    python install.py                 # wire hooks + create the store (safe, idempotent)
    python install.py --ollama        # also pull the local models (bge-m3, qwen3)
    python install.py --tasks         # also register the safety-net jobs (Task Scheduler on
                                      #   Windows, crontab on macOS/Linux)
    python install.py --profile research   # turn on the Brain layer (research/general/coding)
    python install.py --print         # dry-run: show what would change, write nothing

What it does (all idempotent, re-runnable):
  1. Create the memory store dir (default ~/.nevertwice; honours NEVERTWICE_HOME).
  2. Merge the Claude Code hooks into ~/.claude/settings.json — backing the file up
     first — so SessionStart / UserPromptSubmit / SessionEnd / PreCompact run the
     engine, and PreToolUse runs the active-memory guard before a code-writing tool
     (Edit/Write/Bash) — silent unless a past mistake is about to repeat. Existing
     hooks from other tools are preserved.
  3. Print the MCP-client snippet (Cursor / Claude Desktop / Cline / Zed).
  4. (--ollama) pull bge-m3 + qwen3.  (--tasks) register the scheduled tasks.
     (--profile) persist NEVERTWICE_PROFILE so the opt-in Brain layer turns on.
     On macOS/Linux --tasks installs three `# nevertwice`-tagged crontab jobs; on
     Windows it registers per-user scheduled tasks. Both are idempotent.
  5. Close with the options the user has: profiles, and how to bring in projects
     they already have — so a first-time install is self-explanatory.

Uses the current interpreter (sys.executable); no hard-coded paths.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:                                       # never crash printing on a non-UTF-8 (e.g. cp1251) console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PKG = Path(__file__).resolve().parent / "nevertwice"
HOOK = PKG / "memory_hook.py"
MCP = PKG / "mcp_server.py"
PYTHON = sys.executable.replace("\\", "/")
SETTINGS = Path.home() / ".claude" / "settings.json"
# event -> matcher. "" matches all; PreToolUse is scoped to code-writing tools so the guard
# hook (active memory, axis A) only spawns before an edit/command, never on a Read/Grep.
EVENTS = {
    "SessionStart": "",
    "UserPromptSubmit": "",
    "SessionEnd": "",
    "PreCompact": "",
    "PreToolUse": "Edit|Write|MultiEdit|NotebookEdit|Bash",
}
DRY = "--print" in sys.argv


def _cmd() -> str:
    return f'"{PYTHON}" "{str(HOOK).replace(chr(92), "/")}"'


def store_dir() -> Path:
    explicit = (os.environ.get("NEVERTWICE_HOME") or os.environ.get("NEVERTWICE_VAULT")
                or os.environ.get("ANAMNESIS_HOME") or os.environ.get("ANAMNESIS_VAULT"))
    if explicit:
        return Path(os.path.expanduser(explicit))
    new, old = Path.home() / ".nevertwice", Path.home() / ".anamnesis"
    # v1 installs keep their existing store in place — we never relocate user data silently
    return old if (old.exists() and not new.exists()) else new


# Derived / machine-local files: regenerated every run, so they are gitignored —
# cross-machine sync then only ever merges real memory (typed notes, Context,
# Sessions), never these (audit H4). Index.md and User/profile.md are rebuilt each
# session; .processed_sessions.json is per-machine processing state.
_GITIGNORE_LINES = [
    ".lock", "*.tmp", "*.bak", "__pycache__/", "*.pyc",
    ".prompt_recall/", ".logs/",
    ".embeddings_cache.json", ".embeddings_meta.json",
    ".index.sqlite", ".index.sqlite-wal", ".index.sqlite-shm",
    ".processed_sessions.json",
    "graph.json", "status.txt", "health.txt",
    "eval_results.json", "temporal_graph.json", "contradiction_candidates.json",
    "Index.md", "User/profile.md",
]


def ensure_gitignore(d: Path) -> None:
    """Create/extend the store's .gitignore so derived & machine-local files stay
    out of git (audit H4). Idempotent: only missing lines are appended; an
    existing user .gitignore is preserved."""
    gi = d / ".gitignore"
    existing = ""
    if gi.exists():
        try:
            existing = gi.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    present = {ln.strip() for ln in existing.splitlines()}
    missing = [ln for ln in _GITIGNORE_LINES if ln not in present]
    if not missing:
        return
    if DRY:
        print(f"  would add {len(missing)} line(s) to .gitignore")
        return
    header = "" if existing else ("# Nevertwice store — derived/local files "
                                  "(regenerated; kept out of git for clean sync)\n")
    body = (existing.rstrip() + "\n" if existing.strip() else "") + header + \
        "\n".join(missing) + "\n"
    gi.write_text(body, encoding="utf-8")
    print(f"  + .gitignore ({len(missing)} line(s))")


def ensure_store() -> None:
    d = store_dir()
    print(f"[store] {d}")
    if DRY:
        ensure_gitignore(d)
        return
    d.mkdir(parents=True, exist_ok=True)
    if not (d / ".git").exists() and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=str(d), capture_output=True)
        print("[store] git initialised")
    ensure_gitignore(d)


def _our_hook_entries(groups: list):
    """Every hook dict in these groups that points at A memory_hook.py (ours or a stale copy)."""
    for g in groups or []:
        for h in g.get("hooks", []):
            if "memory_hook.py" in (h.get("command") or ""):
                yield h


def wire_hooks() -> None:
    print(f"[hooks] {SETTINGS}")
    settings = {}
    if SETTINGS.exists():
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! could not parse settings.json ({exc}); aborting hook wiring")
            return
    hooks = settings.setdefault("hooks", {})
    added = updated = 0
    for ev, matcher in EVENTS.items():
        groups = hooks.setdefault(ev, [])
        ours = list(_our_hook_entries(groups))
        if ours:
            # a bare substring match used to report "already present" even when the hook
            # pointed at a MOVED/stale copy, so a reinstall never repointed it (code-review
            # 2026-07). Update the command in place — never append a second hook beside a
            # stale one (that would double-fire every event).
            for h in ours:
                if h.get("command") != _cmd():
                    h["command"] = _cmd()
                    updated += 1
            continue
        groups.append({"matcher": matcher,
                       "hooks": [{"type": "command", "command": _cmd()}]})
        added += 1
    if added == 0 and updated == 0:
        print("  = all hooks already present and current")
        return
    if DRY:
        print(f"  would add {added} / repoint {updated} hook(s); command: {_cmd()}")
        return
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        bak = SETTINGS.with_name(
            f"settings.json.bak-nevertwice-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(SETTINGS, bak)
        print(f"  backup → {bak.name}")
    # atomic: a crash mid-write must not leave settings.json corrupt (the backup exists,
    # but recovery shouldn't be needed for a routine install)
    tmp = SETTINGS.with_name(SETTINGS.name + ".tmp-nevertwice")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS)
    if added:
        print(f"  + added {added} hook(s)")
    if updated:
        print(f"  ~ repointed {updated} stale hook command(s) → {_cmd()}")


def mcp_snippet() -> None:
    cfg = {"mcpServers": {"nevertwice": {
        "command": PYTHON, "args": [str(MCP).replace("\\", "/")]}}}
    print("[mcp] add to your MCP client (Cursor / Claude Desktop / Cline / Zed):")
    print("\n".join("    " + l for l in json.dumps(cfg, indent=2).splitlines()))


def pull_models() -> None:
    if not shutil.which("ollama"):
        print("[ollama] not found on PATH — skip (install from https://ollama.com)")
        return
    # Embedder + extraction model defaults, read from the package so install never
    # drifts from the runtime defaults (audit L-b).
    sys.path.insert(0, str(PKG))
    try:
        import memory_hook as _m
        models = (_m.EMBED_MODEL, _m.OLLAMA_MODEL)
    except Exception:
        models = ("bge-m3", "qwen3:8b")
    for model in models:
        print(f"[ollama] pull {model}")
        if not DRY:
            subprocess.run(["ollama", "pull", model])


# Periodic jobs: catch-up (4h), health (hourly), consolidation (weekly Sun 03:00).
_JOBS = [("process_now.py", "", "0 */4 * * *"),
         ("health_check.py", "", "0 * * * *"),
         ("consolidate_memory.py", "--apply", "0 3 * * 0")]
_CRON_MARK = "# nevertwice"


def register_tasks() -> None:
    if os.name == "nt":
        return _register_tasks_windows()
    return _register_tasks_cron()


def _register_tasks_windows() -> None:
    sys.path.insert(0, str(PKG))
    import manage_tasks
    targets = {"Nevertwice_Catchup": "process_now.py",
               "Nevertwice_Health": "health_check.py",
               "Nevertwice_Consolidate": "consolidate_memory.py --apply"}
    for spec in manage_tasks.TASKS:
        script = targets.get(spec["name"], "process_now.py")
        if not DRY:
            # escape % so cmd.exe never expands a %VAR%-shaped substring in a path
            # (e.g. a profile dir containing '%') when the scheduler runs the .bat
            _py = str(PYTHON).replace("%", "%%")
            _sc = str(PKG / script.split()[0]).replace("%", "%%")
            spec["bat"].write_text(
                f'@echo off\r\n"{_py}" "{_sc}" '
                f'{" ".join(script.split()[1:])}\r\n', encoding="utf-8")
    print("[tasks] registering Windows scheduled tasks")
    if not DRY:
        manage_tasks.cmd_register(force=True)


def _register_tasks_cron() -> None:
    """Idempotently install the periodic jobs into the user's crontab (POSIX).
    Each line is tagged `# nevertwice` so re-running replaces, never duplicates."""
    if not shutil.which("crontab"):
        print("[cron] `crontab` not found — add these lines to your scheduler manually:")
        for script, args, sched in _JOBS:
            print(f"    {sched} {PYTHON} {PKG / script} {args}".rstrip())
        return
    log = store_dir() / ".logs" / "cron.log"
    new = [f'{sched} {PYTHON} "{PKG / script}" {args} >> "{log}" 2>&1 {_CRON_MARK}'.replace("  ", " ")
           for script, args, sched in _JOBS]
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    kept = [l for l in (current.stdout or "").splitlines() if _CRON_MARK not in l]
    merged = "\n".join(kept + new).strip() + "\n"
    print("[cron] installing 3 jobs (tagged # nevertwice):")
    for l in new:
        print("    " + l)
    if not DRY:
        (store_dir() / ".logs").mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["crontab", "-"], input=merged, text=True)
        print("  + crontab updated" if p.returncode == 0 else "  ! crontab update failed")


def detect_backends() -> None:
    """Show the zero-config backend auto-detection: what extraction + recall will
    use right now, with no env edits. Read straight from the runtime so install and
    runtime can never disagree (audit L-b)."""
    sys.path.insert(0, str(PKG))
    try:
        import memory_hook as _m
        print("[backends] auto-detected (zero config — override in .env only if you want to):")
        print(_m.backend_report())
    except Exception as exc:                       # detection is best-effort, never fatal
        print(f"[backends] detection skipped ({type(exc).__name__})")


def configure_profile() -> str | None:
    """Honour `--profile <name[,name]>`: persist NEVERTWICE_PROFILE into the package
    .env that the hook already loads, so the opt-in Brain layer turns on for this
    install. Validated against the known profiles; upserts (never clobbers other keys).
    Returns the active profile string, or None when the flag is absent/invalid."""
    if "--profile" not in sys.argv:
        return None
    i = sys.argv.index("--profile")
    raw = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    valid = {"coding", "research", "general"}
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts or any(p not in valid for p in parts):
        print(f"[profile] --profile expects a comma-list of {sorted(valid)}; got {raw!r} — skipped")
        return None
    value = ",".join(dict.fromkeys(parts))         # dedup, preserve order
    envf = PKG / ".env"
    kept = []
    if envf.exists():
        kept = [l for l in envf.read_text(encoding="utf-8").splitlines()
                if not l.strip().startswith("NEVERTWICE_PROFILE=")]
    if DRY:
        print(f"[profile] would set NEVERTWICE_PROFILE={value} in {envf}")
        return value
    envf.write_text("\n".join(kept + [f"NEVERTWICE_PROFILE={value}"]) + "\n", encoding="utf-8")
    print(f"[profile] NEVERTWICE_PROFILE={value} → {envf.name}")
    return value


def print_next_steps(profile: str | None) -> None:
    """The closing message: spell out the choices a new user actually has — which
    profile is active and how to change it, and how to bring in projects they already
    have — so onboarding never leaves the Brain layer or backfill undiscovered."""
    active = profile or "coding"
    brain_on = any(p in active for p in ("research", "general"))
    print("\n--- Your options " + "-" * 47)
    print("Profiles -- how much Nevertwice remembers (active now: %s):" % active)
    print("  coding (default)    lean operational memory: mistakes, patterns, decisions")
    print("  research / general  + an opt-in Brain layer: a self-wiring knowledge graph")
    print("                      (papers, methods, datasets, ...) with per-entity cards and")
    print("                      timelines. Pull-only -- never enlarges the token budget.")
    if not brain_on:
        print("  -> doing research? re-run:  python install.py --profile research")
        print("     (or set NEVERTWICE_PROFILE=research in your .env) to switch the Brain layer on.")
    print("\nBring in projects you already have:")
    print("  * Every new session is captured automatically from now on.")
    print("  * Past Claude Code sessions backfill on the catch-up sweep (no action needed).")
    print("  * Seed a rich card for a big existing project right now:")
    print("        python -m nevertwice.bootstrap_contexts /path/to/project")
    print("\nLearn more:  docs/CONFIG.md (all tunables) and docs/BRAIN_LAYER_DESIGN.md (Brain layer)")
    print("-" * 64)


def main() -> int:
    print(f"Nevertwice installer ({'DRY-RUN' if DRY else 'apply'}) — python {PYTHON}\n")
    ensure_store()
    wire_hooks()
    profile = configure_profile()
    if "--ollama" in sys.argv:
        pull_models()
    if "--tasks" in sys.argv:
        register_tasks()
    print()
    detect_backends()
    print()
    mcp_snippet()
    print_next_steps(profile)
    print("\nDone. Restart your agent so the new hooks load."
          + ("  (dry-run — nothing was written)" if DRY else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
