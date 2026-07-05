#!/usr/bin/env python3
"""Cross-machine / multi-agent sync for the memory store (M-11).

The store is a git repo, so syncing it across your machines (or merging memories
written by parallel agents) is just git. This wraps the safe sequence:

    git add -A && commit (if dirty) → pull --rebase --autostash → push

Markdown notes rarely conflict (one file per fact); when they do, git surfaces it
like any other repo. Derived & machine-local files (Index.md, graph.json, the
embedding cache, the SQLite index, User/profile.md, the processed-sessions DB) are
gitignored, so sync merges ONLY real memory and never thrashes on regenerated
files (audit H4). Run on each machine, or from a scheduled task.

    python sync.py            # commit local changes, rebase on remote, push
    python sync.py --no-push  # pull/rebase only (e.g. read-only mirror)

Requires a configured `origin` remote on the store. No-op (clean exit) if the
store isn't a git repo or has no remote.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from . import memory_hook as m
except ImportError:
    import memory_hook as m


def _git(*args, check=False):
    return subprocess.run(["git", "-C", str(m.VAULT), *args],
                          capture_output=True, text=True, check=check,
                          encoding="utf-8", errors="replace")


def main() -> int:
    if not (m.VAULT / ".git").exists():
        print(f"[sync] {m.VAULT} is not a git repo - nothing to sync.", file=sys.stderr)
        return 0
    if not (_git("remote").stdout or "").strip():
        print("[sync] no git remote configured (add one: git remote add origin <url>).",
              file=sys.stderr)
        return 0
    # a previous sync may have stopped mid-rebase; committing on top would steamroll the
    # conflict state, so stop loudly instead (code-review 2026-07)
    gitdir = m.VAULT / ".git"
    if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
        print("[sync] a rebase is already in progress - resolve it first "
              "(git -C <vault> rebase --continue / --abort).", file=sys.stderr)
        return 1

    # 0) install the structured merge driver so a concurrent-write conflict on a note
    #    (recurrence bump / supersession) auto-resolves instead of stopping the sync (idempotent).
    try:
        from . import merge as _merge
    except ImportError:
        import merge as _merge
    _merge.register(m.VAULT)

    # 1) commit local changes (if any) - and only claim success when git agrees
    if (_git("status", "--porcelain").stdout or "").strip():
        _git("add", "-A")
        msg = f"sync: memory snapshot {datetime.now():%Y-%m-%d %H:%M}"
        commit = _git("commit", "-m", msg)
        if commit.returncode != 0:
            print("[sync] commit failed:\n" + (commit.stderr or commit.stdout), file=sys.stderr)
            return 1
        print(f"[sync] committed local changes: {msg}")

    # 2) rebase on remote (autostash keeps any leftover state out of the way). A fresh vault
    #    whose branch has no upstream yet is not a conflict - bootstrap it with push -u instead.
    pull = _git("pull", "--rebase", "--autostash")
    if pull.returncode != 0:
        err = (pull.stderr or "") + (pull.stdout or "")
        if "no tracking information" in err or "There is no tracking information" in err:
            branch = (_git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip() or "master"
            up = _git("push", "-u", "origin", branch)
            if up.returncode != 0:
                print("[sync] initial push -u failed:\n" + (up.stderr or up.stdout),
                      file=sys.stderr)
                return 1
            print(f"[sync] bootstrapped upstream: origin/{branch}")
            print("[sync] done")
            return 0
        print("[sync] pull --rebase failed (conflict?). Resolve manually:\n" + err,
              file=sys.stderr)
        return 1
    print("[sync] rebased on origin")

    # 3) push
    if "--no-push" not in sys.argv:
        push = _git("push")
        if push.returncode != 0:
            print("[sync] push failed:\n" + (push.stderr or push.stdout), file=sys.stderr)
            return 1
        print("[sync] pushed to origin")
    print("[sync] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
