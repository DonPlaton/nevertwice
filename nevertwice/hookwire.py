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

import ast
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


#: Characters that cannot be made safe inside a `python -c` payload embedded in one
#: double-quoted shell token, common to POSIX shells (bash/sh) and PowerShell: `"` and `` ` ``
#: end or escape the outer quoting, `$` triggers interpolation in both, and `'` is what the
#: payload's OWN string literals use - a path containing one would flip Python's `repr()` to
#: double quotes and break the outer wrapping (`repr("it's")` == `"it's"`).
_C_PAYLOAD_FORBIDDEN = ('"', "'", "`", "$")


def _c_safe(path) -> str:
    """A forward-slashed path, or a `ValueError` naming exactly what is wrong with it - raised
    at install time and never swallowed, because a path this file cannot make safe would
    otherwise wire a hook command that is safe to READ and unsafe to RUN."""
    s = str(path).replace("\\", "/")
    bad = sorted({c for c in _C_PAYLOAD_FORBIDDEN if c in s})
    if bad:
        raise ValueError(
            f"cannot wire a hook through {s!r}: it contains {''.join(bad)!r}, which cannot be "
            "made safe inside the command Claude Code runs. Move the checkout (and the Claude "
            'Code settings/store locations) to a path with no ", \', ` or $ in it.')
    return s


def hook_payload(shim, engine) -> str:
    """The Python source `hook_command` embeds after `-c` - one expression per statement, no
    compound (`if`/`for`) statements, because this has to be valid on the single logical line
    every shell Claude Code runs a hook command through gives it.

    Security: `python -c` puts `''` (THE CURRENT DIRECTORY) at `sys.path[0]`, and Claude Code
    runs a hook command with cwd = the user's PROJECT - untrusted, from this payload's point of
    view. A project containing its own `os.py` / `json.py` / ... would be imported INSTEAD of
    the real standard-library module for anything not frozen into the interpreter (frozen only
    from Python 3.11; this project supports 3.10). So the payload's FIRST action, before
    importing anything but `sys` itself (a true builtin - compiled into the interpreter, never
    filesystem-resolved), is to drop `''` and `'.'` from `sys.path`. Every import after that
    statement - in this payload, in the shim it may hand off to, and in the engine that shim
    may hand off to in turn - is safe from a same-named file planted in the project the hook
    fires in. Verified by planting a `json.py` and a `pathlib.py` that each write a marker on
    import and confirming neither ever fires (`tests/_test_hook_shim.py`).

    Exit-code contract, ONE ternary EXPRESSION rather than an `and`/`or` chain (an earlier
    draft relied on `runpy.run_path`'s return value staying truthy - true in practice, never a
    documented guarantee, and a habit of "clever" code this project does not want): the shim
    script runs, unmodified, exactly as `python <shim>` would - `SystemExit` included, never
    caught here, so a denial the engine's own guard means stays a denial; the shim missing
    writes one line to stderr and the script falls off its own end, which is CPython's
    ordinary exit 0 - there is no file left for the interpreter itself to fail to open.
    """
    return (
        "import sys;"
        "sys.path[:]=[p for p in sys.path if p not in ('','.')];"
        "import os,runpy;"
        f"s={_c_safe(shim)!r};e={_c_safe(engine)!r};"
        "sys.argv=[s,e];"
        "runpy.run_path(s,run_name='__main__') if os.path.isfile(s) "
        "else sys.stderr.write('[nevertwice] hook shim missing: '+s+chr(10))"
    )


def hook_command(python, shim, engine) -> str:
    """The exact command string a hook entry carries: the interpreter, `-c`, and a payload
    that never depends on a separate FILE being handed to Python as its own script argument -
    `python <path>` for a path that is not there is CPython's own exit 2, and Claude Code
    treats exit 2 from PreToolUse/UserPromptSubmit as a BLOCK. A hand-deleted `hook_shim.py`
    used to hit exactly that - one layer above the ENGINE going missing, which the shim itself
    already handled. `-c` never opens a file at all; the code IS the argument, so there is
    nothing left for interpreter startup to fail to open.

    Why `-c` and not a POSIX `[ -f ... ]` shell guard: Claude Code runs a hook command through
    whatever shell is native to the platform, and on Windows without Git Bash that is
    PowerShell, where POSIX test syntax is a syntax error on every call - reintroducing the
    exact block this whole track exists to remove, at the shell level instead of the file
    level.

    Deliberately NOT `-P` (isolates `sys.path[0]`, the same effect `hook_payload` achieves
    explicitly): absent before Python 3.11, and this project supports 3.10 - an unknown flag
    is a hard error, exit 2, a block on every single call. Not `-I` either: it also disables
    `PYTHONPATH`, user site packages and `PYTHONUTF8`/`PYTHONIOENCODING`, behaviour changes
    this project has not measured and does not want as a side effect of a security fix.
    """
    py = _c_safe(python)
    payload = hook_payload(shim, engine)
    return f'"{py}" "-c" "{payload}"'


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


def _parse_c_payload(payload: str) -> tuple[str, str] | None:
    """(shim, engine) out of a `hook_payload` string, or `None` if this is not one of ours.

    Parsed as the Python source it actually is - `ast.parse` plus a walk for the `s = '...'` /
    `e = '...'` assignments - rather than a regex guessing at their shape. The payload is a
    complete, syntactically valid module on its own (that is the whole point of it), so this
    is exact, not a heuristic.
    """
    try:
        tree = ast.parse(payload)
    except SyntaxError:
        return None
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("s", "e")
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            values[node.targets[0].id] = node.value.value
    if "s" not in values or "e" not in values:
        return None
    return values["s"], values["e"]


def dead_reason(entry) -> tuple[str, bool] | None:
    """Why a wired entry cannot run today, and whether that failure BLOCKS the agent.

    `None` when the entry looks alive (or is not one of ours to judge at all). The interpreter
    check is common to every shape - if it names a path and that path is not there, the shell
    reports "command not found" before Python ever runs, which is not exit 2 and does not
    block. What happens after that depends on which of two shapes the rest of the command is:

    **The current `-c` form** (`hook_command`'s own output): the code itself is the argument,
    so there is no separate file for CPython's own startup to fail to open - NOTHING in this
    shape blocks. A missing shim degrades inside the payload (one stderr line, exit 0); a
    missing engine degrades one layer further in, inside the shim the payload found (same
    exit 0, same message the shim itself has always written).

    **A pre-`-c` entry** (`"<py>" "<shim>" "<engine>"`, or the oldest `"<py>" "<engine>"` with
    no shim at all - what an install from before this form existed left wired, until the next
    `install.py` run repoints it): here a missing SCRIPT (the shim, or an old-style direct
    `memory_hook.py`/`mcp_server.py` entry) IS `python <missing file>`, CPython's own exit 2,
    with nothing of ours in the way to catch it - this DOES block. Only a missing ENGINE past
    a live old-style shim does not, because the shim itself is what catches that one.
    """
    command = str(entry.get("command", "")) if isinstance(entry, dict) else ""
    toks = tokens(command)
    if len(toks) < 2:
        return None
    interpreter = toks[0]
    if "/" in interpreter and not Path(interpreter).is_file():
        return (f"interpreter missing: {interpreter}", False)

    if len(toks) >= 3 and toks[1] == "-c":
        parsed = _parse_c_payload(toks[2])
        if parsed is None:
            return None                     # a `-c` command, but not one of ours to judge
        shim, engine = parsed
        if not shim or not Path(shim).is_file():
            return (f"shim missing: {shim or '(none in the payload)'} - the -c wrapper "
                    "degrades to exit 0, memory is off", False)
        if not engine or not Path(engine).is_file():
            return (f"engine missing: {engine or '(none in the payload)'} - the shim exits 0, "
                    "memory is off", False)
        return None

    script = toks[1]
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


def _pyvenv_home(venv_root: Path) -> Path | None:
    """The `home` line out of `<venv_root>/pyvenv.cfg` - the directory the venv's OWN metadata
    says its base interpreter lives in, written by `venv`/`virtualenv` regardless of whether
    the venv uses symlinks or copies (`home` names the base either way). `None` if the file is
    missing, unreadable, or has no `home` line - a venv created some other way (or a bare copy
    of a Python install with no `pyvenv.cfg` at all) simply skips this candidate, it is one of
    several, never the only one `hook_python` tries."""
    try:
        text = (venv_root / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "home":
            value = value.strip()
            return Path(value) if value else None
    return None


def _base_prefix_fallback(base_prefix: str, os_name: str) -> str:
    """The last-resort candidate: a `python[.exe]` beside `base_prefix` - PLATFORM-SHAPED, not
    one path guessed at for both. A9 (2026-09): the original fallback was `base_prefix/python3`
    on every platform, which is simply wrong on POSIX - a base install's own prefix directory
    (`/usr`, `/usr/local`, a pyenv version dir, ...) does not carry a bare `python3` at its own
    root, only under `bin/`; only Windows ever had `base_prefix\\python.exe` right. Split into
    its own function, taking `os_name` as a plain argument rather than reading `os.name`
    itself, so a test can drive both shapes without monkeypatching global process state - and
    built with plain string joins, never `pathlib.Path` (whose `WindowsPath`/`PosixPath` split
    is bound to the REAL running OS, not to this `os_name` argument, so a POSIX shape computed
    on a Windows test runner - or vice versa - would otherwise come out with the wrong
    separators regardless of what `os_name` says)."""
    base = str(base_prefix).rstrip("/\\")
    if os_name == "nt":
        return f"{base}\\python.exe"
    return f"{base}/bin/python3"


def hook_python(executable: str, clone_root, *, base_executable: str | None = None,
                base_prefix: str | None = None, os_name: str | None = None
                ) -> tuple[str, str | None]:
    """The interpreter a hook command should run under, and a warning to print if none could
    be found - `(path, warning)`, `warning` `None` in the ordinary case.

    `executable` unchanged (and no warning) unless the clone CONTAINS it - a venv created
    inside the checkout being wired, whose own interpreter would vanish along with the clone it
    lives in, defeating the whole point of a shim that is meant to survive that. "Contains" is
    checked BOTH ways, `os.path.abspath(executable)` OR its `.resolve()`: a venv made with
    `python -m venv` on POSIX puts a SYMLINK at `.venv/bin/python3` pointing at the true base -
    `.resolve()` alone would follow that symlink OUT of the clone and conclude nothing needs
    rebasing, while the command actually wired would still be that symlink's OWN path INSIDE
    the clone (`abspath` catches this: the symlink file itself lives there, whichever direction
    `resolve()` reads off it).

    Once inside, the FIRST candidate that both exists AND resolves OUTSIDE the clone wins, in
    this order - a defence in depth, since more than one of these can be wrong on its own:

      (i) `executable`'s own RESOLVED real path - the fix for the symlinked-venv case above:
          following the symlink lands on the true base interpreter directly;
      (ii) `sys._base_executable` - right on every interpreter EXCEPT CPython 3.10 on POSIX
           with a COPIED venv (`venv.create(..., symlinks=False)`, this project's own O3b test
           and `python -m venv --copies`): there, `_base_executable` still names the venv's OWN
           copy, not the true base (fixed in 3.11), so it resolves right back inside the clone
           and this candidate is skipped, same as any other that fails to escape it;
      (iii) `pyvenv.cfg`'s own `home` directory (`_pyvenv_home`, above) plus `python3` or
            `python3.<minor>` (POSIX) / `python.exe` (Windows) - written by `venv` itself
            regardless of symlinks-vs-copies, so this is what actually rescues 3.10 POSIX;
      (iv) `base_prefix`'s own `bin/python3` (POSIX) / `python.exe` (Windows)
           (`_base_prefix_fallback`, above) - the last resort, when the interpreter that
           created this venv cannot be found by any of the above (a stripped-down or relocated
           install with no working `pyvenv.cfg`).

    If NONE of the four qualifies (e.g. a fully portable Python living inside the clone, with
    no reachable base at all) this function does NOT refuse: it returns `executable` UNCHANGED
    plus a WARNING string - `install.py` prints it loudly rather than failing the install, on
    the judgment that a hook which fires today and goes silent if the clone is later deleted or
    moved is better than no hook at all; the warning exists so that silence is not a surprise.

    `base_executable`/`base_prefix`/`os_name` default to this PROCESS's own
    `sys._base_executable`/`sys.base_prefix`/`os.name` - overridable ONLY so a test can drive
    every branch of the fallback chain without creating a real venv (or several) for each one.
    `install.py`'s one real call site never passes them; production behaviour is unchanged.
    """
    if base_executable is None:
        base_executable = getattr(sys, "_base_executable", "") or ""
    if base_prefix is None:
        base_prefix = sys.base_prefix
    if os_name is None:
        os_name = os.name

    def _resolved(p) -> Path | None:
        try:
            return Path(p).resolve()
        except OSError:
            return None

    try:
        root = Path(clone_root).resolve()
        abs_exe = Path(os.path.abspath(executable))
    except OSError:
        return executable, None

    def _under_clone(p: Path) -> bool:
        return p == root or root in p.parents

    real_exe = _resolved(abs_exe)
    inside = _under_clone(abs_exe) or (real_exe is not None and _under_clone(real_exe))
    if not inside:
        return executable, None

    def _qualifies(candidate) -> str | None:
        """A candidate is used only if it names a real file whose RESOLVED path lands outside
        the clone - a candidate that is itself a symlink pointing back inside is refused the
        same way `executable` itself is checked, above."""
        if not candidate:
            return None
        path = Path(candidate)
        if not path.is_file():
            return None
        resolved = _resolved(path)
        if resolved is None or _under_clone(resolved):
            return None
        return str(path)

    guesses: list[str | None] = [str(real_exe) if real_exe is not None else None,
                                 base_executable]
    venv_root = abs_exe.parent.parent            # .venv/bin/python3 or .venv/Scripts/python.exe
    home = _pyvenv_home(venv_root)
    if home is not None:
        if os_name == "nt":
            guesses.append(str(home / "python.exe"))
        else:
            guesses.append(str(home / "python3"))
            guesses.append(str(home / f"python3.{sys.version_info.minor}"))
    guesses.append(_base_prefix_fallback(base_prefix, os_name))

    for guess in guesses:
        found = _qualifies(guess)
        if found is not None:
            return found, None

    warning = (
        f"no interpreter outside the clone at {root} could be found to wire the hook to "
        f"(checked {executable!r}'s own resolved real path, sys._base_executable, "
        "pyvenv.cfg's own 'home', and the base prefix's own python binary - none of them "
        f"exists outside the clone). Wiring {executable} anyway: deleting or moving this "
        "checkout will turn memory off SILENTLY, with no error, because the interpreter "
        "Claude Code runs the hook with lives INSIDE the checkout it was told to remove."
    )
    return executable, warning


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
