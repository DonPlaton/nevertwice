#!/usr/bin/env python3
"""One definition of "the hook is wired" - the settings path, the command string, and what
makes a wired entry ours, foreign, or dead.

`install.py` writes the hook commands, `hosts.py` reads them back to answer "is Claude Code
wired", and until now the two carried separate copies of the same three ideas: which
settings.json, which path suffixes count as ours, and how the command string is built. Two
definitions drift - `install.py` read only `Path.home()` while `hosts.ClaudeCodeAdapter`
honoured `NEVERTWICE_CLAUDE_SETTINGS`, and that gap is what let a test's `--uninstall` write
five hooks into the owner's real settings.json (2026-09-23, restored from the installer's own
backup). This module is the one place both of those questions get answered, and it is
deliberately dependency-free: `install.py` imports it before `hosts.py` exists to be imported,
so it cannot import `hosts`, `memory_hook`, or any other sibling - standard library only.

The other half of what lives here: `dead_reason()`. A hook command that names a real file
today can point at nothing tomorrow - the clone was deleted, moved, or `pip uninstall`'d while
still wired - and `python <missing file>` exits 2, which Claude Code treats as a BLOCK from
PreToolUse and UserPromptSubmit: every edit, command and prompt refused, with no way left to
ask the agent to fix it. `hook_shim.py` (the template `write_shim` installs) exists so that
failure degrades to exit 0 instead, and `dead_reason()` is what lets `nevertwice-hosts` tell
the two failure shapes apart - a shim whose engine went missing is silent and non-blocking; an
old-style entry, or a missing shim itself, still blocks, because there is no shim in the way
to catch the interpreter's own file-not-found exit.

    from nevertwice import hookwire
    settings = hookwire.settings_path()
    shim = hookwire.shim_path(settings)
    hookwire.write_shim(settings, (pkg_dir / "hook_shim.py").read_bytes(), dry_run=False)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: Bumped when the shim template's own contract changes (its argv shape, what it reads from
#: stdin) in a way old wired commands would not survive - never for a text-only edit.
SHIM_VERSION = 1

#: The first line of every shim `write_shim` installs, and the one thing `remove_shim` will
#: ever delete without asking: a file at the shim's path with no marker is not ours, however
#: it got there, and survives.
SHIM_MARKER = "# nevertwice-hook-shim v"

SHIM_DIRNAME = "nevertwice"
SHIM_NAME = "hook_shim.py"

#: A command whose PATH ends in one of these is ours - checked as a path SUFFIX, never a bare
#: filename, so a hand-rolled `~/.claude/scripts/memory_hook.py` (a real, supported deployment
#: shape) is never claimed as ours by accident.
OUR_HOOK_SUFFIXES = ("nevertwice/hook_shim.py", "nevertwice/memory_hook.py",
                    "nevertwice/mcp_server.py")

#: Any command that runs a script by one of these bare names, wherever it lives - used only to
#: tell a foreign or hand-rolled copy apart from nothing being wired at all, never to remove it.
OUR_SCRIPT_NAMES = ("hook_shim.py", "memory_hook.py", "mcp_server.py")

_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')


def settings_path() -> Path:
    """Where Claude Code's settings.json is, on this machine or under the test override.

    The ONE resolution every caller here uses - `install.py`, `hosts.ClaudeCodeAdapter`, and
    every test. Two separate copies of `os.environ.get("NEVERTWICE_CLAUDE_SETTINGS") or
    Path.home() / ...` is exactly how the two used to disagree.
    """
    override = os.environ.get("NEVERTWICE_CLAUDE_SETTINGS")
    return Path(override) if override else Path.home() / ".claude" / "settings.json"


def shim_path(settings) -> Path:
    """The shim's own path, beside settings.json rather than inside any one clone - so
    deleting the clone that installed it does not delete it too."""
    return Path(settings).parent / SHIM_DIRNAME / SHIM_NAME


def hook_command(python, shim, engine) -> str:
    """The exact command string a hook entry carries: three quoted, forward-slashed tokens -
    the interpreter, the shim, and the engine the shim should run."""
    parts = (str(python), str(shim), str(engine))
    return " ".join('"' + p.replace("\\", "/") + '"' for p in parts)


def tokens(command: str) -> list[str]:
    """The quoted-or-bare tokens of a hook command string, backslashes normalised to `/`.

    Case is PRESERVED - existence checks in `dead_reason` are filesystem checks, and on a
    case-sensitive filesystem lower-casing a path before testing `Path.is_file()` reports a
    script that is really there as missing, or one that is not there as present.
    """
    if not command:
        return []
    out = []
    for m in _TOKEN_RE.finditer(command):
        tok = m.group(1) if m.group(1) is not None else m.group(2)
        out.append(tok.replace("\\", "/"))
    return out


def _command_lower(entry) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("command", "")).replace("\\", "/").lower()


def is_ours(entry) -> bool:
    """A hook entry whose command names one of our scripts, by path suffix."""
    cmd = _command_lower(entry)
    return any(suffix in cmd for suffix in OUR_HOOK_SUFFIXES)


def is_foreign_copy(entry) -> bool:
    """Runs one of our scripts by name, but not from a path this package would have wired -
    a hand-rolled deployment, reported and never touched by an uninstall."""
    cmd = _command_lower(entry)
    return (not is_ours(entry)) and any(name in cmd for name in OUR_SCRIPT_NAMES)


def dead_reason(entry) -> tuple[str, bool] | None:
    """Why a wired entry cannot run today, and whether that failure BLOCKS the agent.

    `None` when the entry looks alive (or is not one of ours to judge at all). Three shapes,
    checked in the order a command actually fails in:

    * the interpreter itself is a path and is not there - the shell reports "command not
      found" before Python ever runs, which is not exit 2 and does not block;
    * the script (the shim, or an old-style direct `memory_hook.py`/`mcp_server.py` entry) is
      not there - `python <missing file>` is CPython's own exit 2, and there is no shim in the
      way to catch it, so this DOES block;
    * the script is the shim and the engine argument after it is missing or absent - the shim
      itself handles this and exits 0, so this does not block; memory is simply off.
    """
    toks = tokens(str(entry.get("command", "")) if isinstance(entry, dict) else "")
    if len(toks) < 2:
        return None
    interpreter, script = toks[0], toks[1]
    if "/" in interpreter and not Path(interpreter).is_file():
        return (f"interpreter missing: {interpreter}", False)
    script_lower = script.lower()
    if not any(script_lower.endswith(suffix) for suffix in OUR_HOOK_SUFFIXES):
        return None
    if not Path(script).is_file():
        return (f"script missing: {script}", True)
    if script_lower.endswith(f"{SHIM_DIRNAME}/{SHIM_NAME}".lower()):
        engine = toks[2] if len(toks) > 2 else ""
        if not engine or not Path(engine).is_file():
            return (f"engine missing: {engine or '(no engine argument)'} - the shim exits 0, "
                    "memory is off", False)
    return None


def hook_python(executable: str, clone_root) -> str:
    """The interpreter a hook command should run under.

    `executable` unchanged, unless it resolves INSIDE `clone_root` - a venv created inside the
    checkout being wired, whose own interpreter would vanish along with the clone it lives in,
    defeating the whole point of a shim that is meant to survive that. In that one case, the
    venv's own base interpreter (`sys._base_executable`, falling back to a `python[.exe]` beside
    `sys.base_prefix`), which lives outside the clone by construction.
    """
    try:
        exe = Path(executable).resolve()
        root = Path(clone_root).resolve()
    except OSError:
        return executable
    if exe != root and root not in exe.parents:
        return executable
    base = getattr(sys, "_base_executable", "") or ""
    if base and Path(base).is_file():
        return base
    name = "python.exe" if os.name == "nt" else "python3"
    return str(Path(sys.base_prefix) / name)


def write_shim(settings, source: bytes, *, dry_run: bool = False) -> str:
    """Install the shim template at `shim_path(settings)`. Returns "written", "updated" or
    "current" - never raises on the read side, and never writes when the bytes already match."""
    path = shim_path(settings)
    existing = None
    if path.is_file():
        try:
            existing = path.read_bytes()
        except OSError:
            existing = None
    if existing == source:
        return "current"
    verb = "updated" if existing is not None else "written"
    if dry_run:
        return verb
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(source)
    os.replace(tmp, path)
    return verb


def remove_shim(settings, *, dry_run: bool = False) -> list[str]:
    """Remove the shim at `shim_path(settings)`, and only a file that is really ours.

    A file at that path whose first line does not start with `SHIM_MARKER` survives untouched
    - it is not one `write_shim` put there, so it is not this call's to delete. The containing
    directory is removed too, but only once it is empty; a shim removal that raises (a locked
    file, a read-only parent) raises out of here rather than being swallowed, so a caller that
    already rewrote settings.json can tell "nothing left to remove" from "tried and failed".
    """
    path = shim_path(settings)
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline()
    except OSError:
        return []
    if not first_line.startswith(SHIM_MARKER):
        return []
    if dry_run:
        return [str(path)]
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return [str(path)]
