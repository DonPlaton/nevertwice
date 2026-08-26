#!/usr/bin/env python3
"""The `store/state` seam: how bytes reach disk without ever being half-written.

The first seam extracted from `memory_hook.py` under GOAL E4. Everything here was in that
module; nothing here is new. `memory_hook` re-exports every name, so the public import surface
is unchanged and `tests/_test_characterize_store_state.py` - written against the code *before*
it moved, and run unchanged after - is what says so.

Two mechanisms, each bought with an incident:

**Atomic publish.** A write goes to a temp file beside the target and is then `os.replace`d
onto it. A crash mid-write can no longer truncate the live file, which is what caused the mass
re-processing in audit F1/F3/F30. The temp name carries the pid *and* the thread id: the pid
alone separates concurrent hook processes and does nothing for threads inside one, and eight
threads writing one state file raced on a single `.tmp` - `WinError 32` on Windows, and on
POSIX a silent last-writer-wins, which is worse because nothing reports it (GOAL E1).

**Two generations.** Every state file that must survive a truncated write is stored as
`<name>` plus `<name>.bak`, both written from the same known-good in-memory text - never a copy
of the possibly-corrupt on-disk primary. Loading tries the primary, then the `.bak`, and says so
out loud when it falls back. Decoding is deliberately strict: `errors="replace"` would turn a
bit flip inside a guard pattern into a valid-looking U+FFFD, so the guard silently stops
matching and the intact `.bak` is never consulted.

This module deliberately knows nothing about the vault. Every function takes the path it is
given, which is what makes it testable without a store and safe to call from anywhere. The one
thing it borrows from the host is `log`, imported late so there is no import cycle.

Standard library only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

#: How long `os.replace` may keep losing to a concurrent writer before we give up. Windows
#: fails a rename with a sharing violation while another handle to the target is briefly open -
#: which two threads replacing the same state file do to each other constantly - and the
#: failure is transient by nature. POSIX renames do not need this and pay nothing for it.
_REPLACE_RETRY_S = 2.0


def _log(msg: str) -> None:
    """The host's logger, resolved at call time.

    Imported late and per-call rather than at module load: `memory_hook` imports this module,
    so a module-level import back would be a cycle, and binding the function object once would
    freeze whichever `log` existed at import - tests that redirect stderr, and the early-warning
    queue `log` drains on first use, both depend on calling the live one.
    """
    try:
        from . import memory_hook as host        # type: ignore[attr-defined]
    except ImportError:                           # flat scripts dir, no package
        import memory_hook as host                # type: ignore[no-redef]
    host.log(msg)


# ── atomic publish ──────────────────────────────────────────────────────

def write_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Crash-safe write: temp file in the same dir + os.replace (atomic on
    NTFS). A crash mid-write can no longer truncate the live file (audit
    F1/F3/F30 - the corruption that triggered mass re-processing)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid AND thread id in the temp name. The pid alone separated concurrent hook processes
    # and not concurrent threads inside one - they share a pid, so eight threads writing the
    # same state file all raced on a single `.tmp`: on Windows the second `os.replace` fails
    # with WinError 32, and on POSIX it silently publishes whichever thread wrote last, which
    # is the worse outcome because nothing reports it (GOAL E1, concurrent writers).
    # Cleanup on failure leaves no orphaned .tmp in the synced vault (audit D3).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """`os.replace`, retried through a transient sharing violation.

    On Windows a rename onto a path another thread or process has open fails with
    PermissionError (WinError 5 / 32) even though nothing is wrong - the other writer is
    mid-replace and will be done in microseconds. Eight threads writing one state file raised
    it reliably (GOAL E1). Retrying briefly turns a spurious crash into a wait; giving up after
    a bounded window keeps a genuinely locked file from hanging the hook forever.
    """
    deadline = time.time() + _REPLACE_RETRY_S
    delay = 0.001
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.time() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


# ── two-generation JSON state files (<name> + <name>.bak) ───────────────
# The shared shape of every state file that must survive a truncated write:
# processed-DB, embed meta, ingest watermarks, guards ledger, anticipate state.
# Each carried its own copy of this pair until the 2026-08 cleanup.

def _load_json_generations(path: Path, label: str, expect: type = dict):
    """Primary then `.bak`, LOUDLY on recovery. Returns the parsed value, or None
    when neither generation parses to `expect` (the caller supplies its default
    and any domain-specific consequence message).

    Decoding is STRICT. `errors="replace"` would turn encoding-level corruption into
    valid-looking data - a bit flip inside a guard pattern parses fine as U+FFFD, so
    the guard silently stops matching and the intact `.bak` is never consulted
    (review 2026-08-24, reproduced end-to-end). Corruption must reach the fallback,
    not the caller."""
    primary_existed = path.exists()
    for fp in (path, path.with_name(path.name + ".bak")):
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            _log(f"{label} unreadable ({fp.name}): {type(e).__name__}: {e}")
            continue
        if isinstance(data, expect):
            # Only a primary that EXISTED and failed is a recovery; a merely absent
            # one used to log a data-corruption line that never happened - noise in
            # exactly the log someone greps while chasing a real one.
            if fp != path and primary_existed:
                _log(f"Primary {label} unreadable - recovered from .bak")
            return data
    return None


def _save_json_generations(path: Path, text: str) -> None:
    """Both copies from the same KNOWN-GOOD in-memory text (never a copy of the
    possibly-corrupt on-disk primary) so a good generation always survives a crash
    mid-save (audit D1). Primary FIRST, then .bak: each write is atomic, and on a
    crash between them the loader reads the already-updated primary, so the latest
    snapshot is never silently lost to a stale primary (audit LOW)."""
    write_atomic(path, text)
    write_atomic(path.with_name(path.name + ".bak"), text)
