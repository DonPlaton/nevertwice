#!/usr/bin/env python3
"""The hook survives its own clone being deleted.

install.py wires Claude Code hooks to `"<python>" "<clone>/nevertwice/memory_hook.py"`. Delete
or move the clone while wired and `python <missing file>` exits 2 - CPython's own exit code for
a script that is not there - and Claude Code treats exit 2 from PreToolUse and UserPromptSubmit
as a BLOCK: every edit, command and prompt refused, with no way left to ask the agent to repair
its own settings.json. `nevertwice/hookwire.py` (A1) and `nevertwice/hook_shim.py` (A2) exist
to make that impossible; this suite proves it, and proves `nevertwice-hosts` can tell a dead
hook from a live one (that half is `tests/_test_hosts.py`).

Every subprocess env in this file is built by `_wall.walled()` - HOME, USERPROFILE and both
Claude Code path overrides pointed inside a throwaway temp directory, asserted before use. A
previous test of the installer wrote five hooks into the owner's real settings.json
(2026-09-23) because a test pinned one override while the code under test read another; a test
has to be safe against the code it tests being wrong, because that is exactly when it runs
against it.

    python tests/_test_hook_shim.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
import _wall  # noqa: E402 - the HOME/settings wall; not a project module, no ordering rule on it

sys.path.insert(0, str(PKG))
import hookwire  # noqa: E402

SHIM_SRC = PKG / "hook_shim.py"
TS = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

#: The EXACT Git Bash path, never the bare `bash` name: on this machine (and any dev machine
#: with WSL enabled) a bare `bash` on PATH resolves to `C:/Windows/System32/bash.exe` - WSL's
#: launcher, an entirely different shell running an entirely different filesystem - not the
#: MSYS bash Claude Code actually shells out to when it prefers Git Bash on Windows.
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _git_bash_available() -> bool:
    return GIT_BASH.is_file()


def _powershell_available() -> bool:
    from shutil import which
    return which("powershell") is not None


def _run_via_git_bash(command: str, *, cwd: Path, env: dict, input_text: str = "{}",
                      timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([str(GIT_BASH), "-c", command], cwd=str(cwd), env=env,
                          input=input_text, capture_output=True, text=True, timeout=timeout)


def _run_via_powershell(command: str, *, cwd: Path, env: dict, input_text: str = "{}",
                        timeout: int = 60) -> subprocess.CompletedProcess:
    """Windows PowerShell 5.1's `-Command` does NOT propagate a native process's exit code to
    its own without an explicit relay - verified empirically on this machine: `powershell
    -NoProfile -Command "& python -c \\"import sys;sys.exit(7)\\""` reports exit 1, every
    time, regardless of the real code, for ANY non-zero exit - not specific to this project's
    payload. `; exit $LASTEXITCODE` is the closest approximation this suite can construct to
    observe our command's REAL exit code on PowerShell 5.1.

    Whether Claude Code's own PowerShell invocation performs the same relay internally could
    NOT be established from here - its source is not in this repository, and the exact
    invocation shape it uses is not documented anywhere this session could reach. This is
    reported explicitly, not guessed at: the four/five exit-code cases below are measured
    THROUGH THIS RELAY, which is the most faithful reproduction this machine can construct,
    and the report this session hands back says so in those words.

    F2, measured directly rather than assumed either way: the `& ` prefix this helper adds is
    not decorative. `powershell -NoProfile -Command "<python> \\"-c\\" \\"print(123)\\""` -
    the SAME command, WITHOUT `&` - fails outright: `ParserError`, `FullyQualifiedErrorId:
    UnexpectedToken`, exit 1, before python ever runs. So this helper tests a TRANSFORMED
    command (`& ...; exit $LASTEXITCODE`), not necessarily the literal string Claude Code
    passes to PowerShell - if Claude Code's own invocation omits the `&` (or spells the relay
    differently, or does not relay at all), what actually happens there is exactly as
    unestablished as the exit-code relay above, for a related but distinct reason.
    """
    ps = f"& {command}; exit $LASTEXITCODE"
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], cwd=str(cwd), env=env,
                          input=input_text, capture_output=True, text=True, timeout=timeout)


def _posix_bash_path() -> str | None:
    """A `bash` on PATH - checked ONLY on POSIX. On Windows a bare `bash` on PATH resolves to
    WSL's launcher (see `GIT_BASH`'s own docstring above) - an entirely different shell and
    filesystem, never what "bash" means on this machine; Git Bash is reached through the
    hardcoded `GIT_BASH` path instead, which is why this helper refuses to answer on Windows at
    all rather than returning a WSL path that would silently test the wrong thing."""
    if os.name == "nt":
        return None
    return shutil.which("bash")


def _run_via_sh(command: str, *, cwd: Path, env: dict, input_text: str = "{}",
               timeout: int = 60) -> subprocess.CompletedProcess:
    """`sh -c` - Claude Code's docs say it "spawns `sh -c`" for a shell-form hook command on
    macOS and Linux, which makes this the product's MAIN path on two of the three operating
    systems it runs on, not an optional extra a missing-shell check may skip. `shutil.which`
    resolves whichever `/bin/sh` this machine actually has (dash, ash, bash-as-sh, ...) rather
    than assuming a path."""
    sh = shutil.which("sh")
    return subprocess.run([sh, "-c", command], cwd=str(cwd), env=env,
                          input=input_text, capture_output=True, text=True, timeout=timeout)


def _run_via_posix_bash(command: str, *, cwd: Path, env: dict, input_text: str = "{}",
                        timeout: int = 60) -> subprocess.CompletedProcess:
    """`bash -c` on POSIX (`_posix_bash_path`, above) - an EXTRA check alongside `sh`, never a
    substitute for it: `sh` is what Claude Code actually spawns there, `bash` is a common but
    not guaranteed `sh` provider (some systems point `/bin/sh` at dash or ash instead)."""
    bash = _posix_bash_path()
    return subprocess.run([bash, "-c", command], cwd=str(cwd), env=env,
                          input=input_text, capture_output=True, text=True, timeout=timeout)


def _available_shells(powershell_label: str = "PowerShell") -> list[tuple[str, object]]:
    """Every shell this machine can test the `-c` command through, matching Claude Code's own
    per-platform choice (its docs: PowerShell/Git Bash on Windows, `sh -c` on macOS/Linux) - so
    "no shell available" means "cannot test AT ALL" everywhere, never "skipped this platform's
    main path". Windows: Git Bash (the exact path, `GIT_BASH`) and PowerShell, as before -
    `bash`/`sh` are deliberately NOT probed there (see `_posix_bash_path`'s own docstring: a
    bare `bash` on PATH there is WSL, a different filesystem). POSIX (macOS/Linux, including a
    CI runner): `sh` - required, since it is the product's own primary path there and present
    on essentially every POSIX system - plus `bash` as an extra where present. This machine is
    Windows, so the POSIX branch is exercised by construction and CI, not locally - see (б)
    Cause 1's commit message for how that was checked."""
    shells: list[tuple[str, object]] = []
    if os.name == "nt":
        if _git_bash_available():
            shells.append(("Git Bash", _run_via_git_bash))
        if _powershell_available():
            shells.append((powershell_label, _run_via_powershell))
    else:
        if shutil.which("sh"):
            shells.append(("sh", _run_via_sh))
        if _posix_bash_path():
            shells.append(("bash", _run_via_posix_bash))
    return shells

#: The five events every hook is wired to (install.py's own EVENTS dict), each with a minimal
#: valid payload - reused from tests/_test_entry_point.py's own EVENTS shape.
FIVE_EVENTS = {
    "PreToolUse": {"hook_event_name": "PreToolUse", "session_id": "e1", "tool_name": "Edit",
                   "tool_input": {"file_path": "a.py", "new_string": "y = 1"}},
    "SessionStart": {"hook_event_name": "SessionStart", "session_id": "e2", "source": "startup"},
    "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit", "session_id": "e2",
                         "prompt": "why does the handler crash with failure mode 3"},
    "SessionEnd": {"hook_event_name": "SessionEnd", "session_id": "e2", "reason": "clear"},
    "PreCompact": {"hook_event_name": "PreCompact", "session_id": "e2", "trigger": "manual"},
}

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _engine_env(tmp: Path) -> dict:
    """`_wall.walled()` points NEVERTWICE_HOME/VAULT AND the transcript sweep root
    (NEVERTWICE_PROJECTS_ROOT/CLAUDE_PROJECTS_ROOT) inside `tmp` - the sweep root was missing
    from the wall until an auditing pass caught it: a run against a live `memory_hook.py`
    with no override reads the owner's real ~/.claude/projects (found the hard way while
    first smoke-testing this shim: it swept real, unrelated project transcripts through a
    real extractor). Kept as a thin alias so call sites in this file say what they mean."""
    return _wall.walled(tmp)


def _payload_parses(payload: str) -> bool:
    try:
        ast.parse(payload)
        return True
    except SyntaxError:
        return False


def _key_pattern(key_node, params: set) -> str | None:
    """'{}' for a KEY expression that is a bare parameter name, 'PREFIX{}' for an f-string of
    one literal prefix plus exactly one parameter - `None` for anything else (a shape this
    scanner cannot safely expand, e.g. a suffix after the parameter, or more than one
    substitution)."""
    if isinstance(key_node, ast.Name) and key_node.id in params:
        return "{}"
    if isinstance(key_node, ast.JoinedStr):
        prefix_parts: list[str] = []
        param_seen = 0
        for v in key_node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                if param_seen:
                    return None                  # literal text AFTER the parameter
                prefix_parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                if (param_seen == 0 and isinstance(v.value, ast.Name)
                        and v.value.id in params and v.format_spec is None
                        and v.conversion == -1):
                    param_seen += 1
                else:
                    return None
            else:
                return None
        if param_seen == 1:
            return "".join(prefix_parts) + "{}"
    return None


def _direct_env_patterns(node, params: set) -> set[str]:
    """Patterns from `os.environ.get(...)` / `os.getenv(...)` calls directly inside `node`,
    keyed off one of `params` (`_key_pattern`, above)."""
    patterns: set[str] = set()
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and n.args):
            continue
        target = n.func
        is_environ_get = (isinstance(target, ast.Attribute) and target.attr == "get"
                          and isinstance(target.value, ast.Attribute)
                          and target.value.attr == "environ")
        is_getenv = isinstance(target, ast.Attribute) and target.attr == "getenv"
        if not (is_environ_get or is_getenv):
            continue
        pattern = _key_pattern(n.args[0], params)
        if pattern:
            patterns.add(pattern)
    return patterns


#: (в)4: three independent guards against a fixpoint that never settles - a function that
#: calls itself with a growing literal prefix (auditor's W5: `_loop(name, depth=0)` returns
#: `os.environ.get(name, '')` when `depth`, else `_loop(f'X_{name}', 1)` - a real direct
#: `os.environ.get` AND a call to itself, both found by a static AST walk that does not
#: evaluate the ternary) composed a longer prefix ("X_{}", "X_X_{}", ...) every pass, forever -
#: the scanner at 4e13d14 did not finish this shape within a 12s bound in this session's own
#: reproduction (the auditor reported 60s+). Any ONE of the three guards below stops W5; all
#: three are kept because a future shape might defeat one but not the others:
#:   - a self-call (a function whose own body calls back into itself, by ANY resolvable name -
#:     bare or `module.own_name(...)`) is never composed AT ALL - the one shape that made W5
#:     grow without bound is refused before it can add anything;
#:   - no composed pattern is ever kept past MAX_PATTERN_LEN characters - a second line of
#:     defense against a growth shape that is not literal self-recursion (e.g. a 2-function
#:     cycle);
#:   - the pass loop itself is bounded at `len(funcs) + 1` - a fixpoint over N (module,
#:     function) pairs can never legitimately need more than N passes to finish propagating
#:     (each pass that changes anything newly stabilizes at least one pair), so needing one
#:     more is proof of non-convergence, not slowness.
#: Any guard tripping FAILS BY NAME (`HelperFixpointError`) rather than truncating silently -
#: silently capping the pattern set would hide exactly the kind of runaway this exists to catch.
MAX_PATTERN_LEN = 128


class HelperFixpointError(RuntimeError):
    """`_env_read_helpers()` refused to keep iterating - the message names the pass bound and
    function count; see (в)4 above."""


def _module_aliases(pkg: Path = PKG) -> dict[str, dict[str, str]]:
    """For every module in `nevertwice/*.py`, which LOCAL NAME (as used in `name.attr(...)` at
    a call site) refers to which OTHER PACKAGE MODULE - covers exactly the two import shapes
    this package uses to reach another module's functions: `import config as _cfg` / `import
    memory_hook as m` (an `ast.Import`, aliased or not), and `from . import memory_hook as m`
    (the package-relative half of the `try/except ImportError` fallback pairs throughout this
    package) - both resolved by their LAST dotted component, matched against the package's own
    module stems. Anything that does not name a module IN THIS PACKAGE (`os`, `sys`, `json`,
    ...) is not recorded, since it cannot define an env helper this scanner would need to
    chase. `from X import f` (importing a SYMBOL, not a module) is deliberately not handled -
    no call site in this package reaches an env helper that way today (verified directly), and
    guessing at that shape would be exactly the kind of guess (в)5 exists to refuse."""
    stems = {p.stem for p in pkg.glob("*.py")}
    out: dict[str, dict[str, str]] = {}
    for path in sorted(pkg.glob("*.py")):
        table: dict[str, str] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    target = a.name.rsplit(".", 1)[-1]
                    if target in stems:
                        table[a.asname or a.name.split(".")[0]] = target
            elif isinstance(node, ast.ImportFrom) and node.level >= 1 and node.module is None:
                for a in node.names:                    # `from . import memory_hook as m`
                    if a.name in stems:
                        table[a.asname or a.name] = a.name
        out[path.stem] = table
    return out


def _symbol_aliases(pkg: Path = PKG) -> dict[str, dict[str, tuple[str, str]]]:
    """(в)6, item (2). For every module in `nevertwice/*.py`, which LOCAL NAME (used BARE at a
    call site, e.g. `env(...)`) was imported as a SPECIFIC SYMBOL from another package module -
    `from .config import env` (or `as g`) makes a bare `env(...)` mean `config.env` every bit
    as much as `_cfg.env(...)` means it through `_module_aliases`'s MODULE table above. This is
    the counterpart `_module_aliases`'s own docstring said this package did not need - the
    auditor's V4 (`from .config import env` plus a bare call, added to `hosts.py`) is exactly
    the shape that needs it: without this table the call is invisible to `_resolve_call` (no
    module alias named `config` in scope, and `hosts.py` defines no `env` of its own), and
    - (в)6's OTHER half - a truly invisible call used to mean a silently DROPPED literal, never
    even reaching the wall/allowlist check.

    Maps `{importing_module: {local_name: (target_module, original_symbol_name)}}`. Only a
    FROM-import naming an explicit package submodule is recorded (`node.module` resolves to a
    package stem) - `from . import memory_hook as m` (no `node.module`) is a MODULE import,
    `_module_aliases`'s job, not this one."""
    stems = {p.stem for p in pkg.glob("*.py")}
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for path in sorted(pkg.glob("*.py")):
        table: dict[str, tuple[str, str]] = {}
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = node.module.rsplit(".", 1)[-1]
                if target in stems:
                    for a in node.names:                # `from .config import env [as g]`
                        table[a.asname or a.name] = (target, a.name)
        out[path.stem] = table
    return out


def _engine_parts(pkg: Path = PKG) -> tuple[str, ...]:
    """The ordered list of `_engine_*.py` files that `nevertwice/_engine.py` execs into
    `memory_hook`'s own `__dict__` (its `ENGINE_PARTS` tuple, read the same way
    `tools/produced_by.py`'s `PART_LIST_NAMES` already trusts it for import-closure purposes -
    an `ast.literal_eval` of the assignment, not an import, so this stays a static scan)."""
    engine_py = pkg / "_engine.py"
    if not engine_py.is_file():
        return ()
    tree = ast.parse(engine_py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ENGINE_PARTS" for t in node.targets):
            try:
                return tuple(str(n).removesuffix(".py") for n in ast.literal_eval(node.value))
            except (ValueError, TypeError, SyntaxError):
                return ()
    return ()


def _module_members(module: str, engine_parts: tuple[str, ...]) -> set[str]:
    """The modules whose definitions are reachable from `module`'s OWN body WITHOUT any
    attribute access - `module` itself, plus, when `module` is `memory_hook` or one of the
    engine parts, every member of that whole group. `_engine.py` execs all eight parts into
    `memory_hook`'s own `__dict__`, one shared namespace (its own module docstring: "the
    parts... share one namespace by design") - so a BARE call inside `_engine_cards.py` to a
    name `_engine_config.py` defines is not a cross-module call at runtime at all, it is a
    same-namespace call, exactly as if both bodies had been pasted into one file. Parsing each
    part as its own file (this scanner's only way in - nothing here executes the package) would
    otherwise see that call as unresolvable and silently lose every name it reaches -
    `NEVERTWICE_EXTRACT_RETRY` (`_engine_cards.py:810`, a bare `env_int(...)` reaching
    `_engine_config.py`'s `env_int`) is exactly this shape, and is the check this function
    exists to keep passing."""
    if module == "memory_hook" or module in engine_parts:
        return {"memory_hook"} | set(engine_parts)
    return {module}


def _resolve_call(module: str, func_node, helpers: dict[tuple[str, str], dict],
                  aliases: dict[str, dict[str, str]],
                  symbol_aliases: dict[str, dict[str, tuple[str, str]]],
                  engine_parts: tuple[str, ...]
                  ) -> tuple[tuple[str, str] | None, list[tuple[str, str]] | None]:
    """(в)5/(в)6: resolve one call's target to the SINGLE `(module, function)` key in `helpers`
    it refers to, from the caller's own module context. Returns `(resolved, broad)`:
    `resolved` is that key, or `None` when it cannot be pinned to exactly one definition;
    `broad` is populated ONLY when `resolved` is `None` AND the call's own name matches some
    helper's name SOMEWHERE in the package anyway - every `(module, function)` pair that
    matches, so the caller can decide what an UNRESOLVED (as opposed to genuinely unrelated)
    call site is worth. Still fail closed for `resolved` itself - never guessed at:

    * an explicit symbol import (`_symbol_aliases`: `from .config import env`) is tried FIRST
      for a bare call - it is direct evidence of exactly one target, stronger than a same-name
      coincidence in scope, so `env(...)` after that import means `config.env` even though
      `hosts.py` (в)6's V4 shape) defines no `env` of its own and is not `memory_hook` or an
      engine part;
    * otherwise a BARE call (`f(...)`) is resolved within `module`'s own EFFECTIVE group
      (`_module_members`) - covers both an ordinary same-file call (`budget._env_int` calling
      `_env_float`) and an engine part calling a sibling part's function through the shared
      exec namespace (`_engine_cards.py` calling `_engine_config.py`'s `env_int`);
    * an ATTRIBUTE call (`alias.f(...)`) resolves `alias` through `module`'s own import
      statements (`_module_aliases`) to a target module, then searches THAT module's effective
      group - so `m.env_int` (`m` aliasing `memory_hook`, used throughout this package) reaches
      `_engine_config.env_int` the same way a bare call from inside another engine part does,
      and `_cfg.env` (`doctor.py`, `_cfg` aliasing `config`) reaches `config.env`;
    * anything else (a deeper attribute chain, a call result, a subscript) resolves to
      `(None, None)` - not a shape this scanner tries to resolve at all, and not reported as
      unresolved either, since it never looked like a helper call in the first place.

    Two or more group members defining the SAME name, or zero in scope, never becomes
    `resolved` - `hosts._env_float` versus `budget._env_float` (auditor's W6) is exactly the
    shape this refuses to conflate: keying `helpers` by `(module, function)` instead of by bare
    name means the two are different keys from the start, so a call resolved to one is never
    accidentally answered by the other's numeric flag. (в)6's V5 is the SAME refusal reached a
    different way - a second `env_int` appended to `_engine_text.py` (an engine part) makes
    every `m.env_int`/bare-within-the-group call ambiguous (two matches, not one), so `resolved`
    stays `None` for all of them; `broad` is what stops that from being silence."""
    func = func_node
    if isinstance(func, ast.Name):
        sym = symbol_aliases.get(module, {}).get(func.id)
        if sym is not None:
            target_module, target_name = sym
            candidates = [(m2, target_name) for m2 in _module_members(target_module, engine_parts)
                         if (m2, target_name) in helpers]
            if len(candidates) == 1:
                return candidates[0], None
            broad = sorted(k for k in helpers if k[1] == target_name)
            return None, (broad or None)
        scoped = [(m2, func.id) for m2 in _module_members(module, engine_parts)
                 if (m2, func.id) in helpers]
        if len(scoped) == 1:
            return scoped[0], None
        broad = sorted(k for k in helpers if k[1] == func.id)
        return None, (broad or None)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        target_module = aliases.get(module, {}).get(func.value.id)
        if target_module is not None:
            scoped = [(m2, func.attr) for m2 in _module_members(target_module, engine_parts)
                     if (m2, func.attr) in helpers]
            if len(scoped) == 1:
                return scoped[0], None
        broad = sorted(k for k in helpers if k[1] == func.attr)
        return None, (broad or None)
    return None, None


def _helper_call_patterns(module: str, name: str, node, params: set,
                          helpers: dict[tuple[str, str], dict],
                          aliases: dict[str, dict[str, str]],
                          symbol_aliases: dict[str, dict[str, tuple[str, str]]],
                          engine_parts: tuple[str, ...]
                          ) -> set[str]:
    """Patterns from calls INSIDE `node` (the function `(module, name)`) to a helper ALREADY in
    `helpers`, resolved via `_resolve_call` - covers a same-module call, a same-exec-namespace
    call between engine parts, an aliased cross-module call (`m.env_int`), and an explicit
    symbol import (`from .config import env`). Only the STRICT `resolved` half of
    `_resolve_call`'s return is ever used here, never `broad` - (в)6's fail-open-for-discovery
    fix is for LEAF call sites with a literal argument (`_package_env_names`, below); composing
    a NEW helper-of-helper from a merely-plausible, unresolved candidate would let an ambiguous
    guess propagate its numeric-ness onto some THIRD function, which is exactly the unsafe
    direction (в)5 exists to refuse - so an unresolved or ambiguous call composes nothing here,
    full stop. `params` gates which of the CALLER's own parameters may feed the callee's FIRST
    POSITIONAL argument - bare, or as one literal prefix plus the parameter in an f-string
    (`_key_pattern`). The composed pattern is the inner helper's own pattern with `outer_prefix
    + "{}"` substituted for its `"{}"` - `NEVERTWICE_{}` composed with an outer prefix of `""`
    (a bare passthrough) stays `NEVERTWICE_{}`; composed with an outer prefix of `"BUDGET_"`
    becomes `NEVERTWICE_BUDGET_{}`. (в)4: a call that resolves back to `(module, name)` itself -
    the function calling itself, however it spells the call - is skipped outright, never
    composed; a composed pattern longer than `MAX_PATTERN_LEN` is dropped."""
    patterns: set[str] = set()
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and n.args):
            continue
        resolved, _broad = _resolve_call(module, n.func, helpers, aliases, symbol_aliases,
                                         engine_parts)
        if resolved is None or resolved == (module, name):
            continue
        inner = helpers[resolved]
        outer = _key_pattern(n.args[0], params)         # e.g. "{}" or "NEVERTWICE_{}"
        if outer is None:
            continue
        outer_prefix = outer[:-2]                       # strip the trailing "{}"
        for inner_pattern in inner["patterns"]:
            composed = inner_pattern.replace("{}", outer_prefix + "{}")
            if len(composed) <= MAX_PATTERN_LEN:
                patterns.add(composed)
    return patterns


def _env_read_helpers(pkg: Path = PKG) -> dict[tuple[str, str], dict]:
    """Every function in `nevertwice/*.py` whose body reads an environment variable keyed off
    ONE OF ITS OWN PARAMETERS - discovered by AST shape, not a hand-written list, so a NEW
    helper (another `env_int`-shaped wrapper, or a future `config.env("SWEEP_DIR")` call
    through a helper that does not exist yet) is picked up the moment it exists. Helpers OF
    helpers, TO ANY DEPTH: a function that passes one of its own parameters - bare, or as one
    literal prefix plus the parameter in an f-string - as the FIRST argument of an
    ALREADY-KNOWN, UNAMBIGUOUSLY RESOLVED helper (`_resolve_call`) becomes a helper itself,
    with the two patterns COMPOSED (`_helper_call_patterns`, above). `budget._env_int` is
    exactly this shape - it has no `os.environ.get` of its own at all, only `_env_float(name,
    float(default))` - so a single-pass scan finds `_env_float` but not `_env_int`, and its
    four real call sites (`budget.py:97-103`) stay invisible without the fixpoint below.

    Numeric-ness is never inherited: a composed helper is auto-numeric only if ITS OWN return
    annotation says `int`/`float` - `_env_int` declares `-> int` on its own account and would
    be numeric even if `_env_float` (which it calls) did not declare one at all.

    (в)5: keyed by `(module, function)`, NEVER by bare function name - two functions sharing a
    name in two different files (auditor's W6: a second `_env_float` in `hosts.py`, returning a
    plain string) are two different entries, and a call is only ever composed through
    `_resolve_call`'s fail-closed resolution, never by name collision. (в)4: the pass loop is
    bounded (see `MAX_PATTERN_LEN`'s docstring above) - a shape that would otherwise never
    settle fails loudly, by name, instead of hanging.

    NOT covered, by construction, and cannot be without a real data-flow analysis this
    discovery tool does not attempt: a name passed to `os.environ.get`/`os.getenv`/a known
    helper as a KEYWORD argument or in any position OTHER than first; a name built at runtime
    from something that is not a literal at the call site; a call reached through anything
    other than a bare name, a same-exec-namespace sibling call, or one level of `alias.func`
    attribute access (`_resolve_call`'s own docstring names the exact shapes).

    Returns `{(module, function): {"patterns": [...], "numeric": bool}}`.
    """
    engine_parts = _engine_parts(pkg)
    aliases = _module_aliases(pkg)
    symbol_aliases = _symbol_aliases(pkg)

    funcs: list[tuple[str, str, object, set]] = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in node.args.args}
            if params:
                funcs.append((path.stem, node.name, node, params))

    helpers: dict[tuple[str, str], dict] = {}
    for module, name, node, params in funcs:
        patterns = _direct_env_patterns(node, params)
        if patterns:
            numeric = isinstance(node.returns, ast.Name) and node.returns.id in ("int", "float")
            key = (module, name)
            existing = helpers.get(key, {"patterns": set(), "numeric": False})
            helpers[key] = {"patterns": set(existing["patterns"]) | patterns,
                            "numeric": existing["numeric"] or numeric}

    # (в)4: bounded fixpoint - see MAX_PATTERN_LEN's module-level docstring for the full
    # rationale. `passes` counts completed passes; hitting `max_passes` without `changed`
    # having gone False means the set is STILL growing, which fails loudly rather than hangs.
    max_passes = len(funcs) + 1
    changed = True
    passes = 0
    while changed:
        if passes >= max_passes:
            # One more pass than `len(funcs) + 1` still finding growth: name the functions
            # whose call bodies are STILL producing new patterns against the current helper
            # set, so the failure points at the offender rather than just "something, somewhere".
            still_growing = sorted(
                f"{m}:{n}" for m, n, nd, pr in funcs
                if _helper_call_patterns(m, n, nd, pr, helpers, aliases, symbol_aliases,
                                         engine_parts))
            offender = ", ".join(still_growing) if still_growing else "<unknown>"
            raise HelperFixpointError(
                f"helper fixpoint did not converge: {offender} ({max_passes} passes over "
                f"{len(funcs)} functions - the pattern set is still changing; see (в)4)")
        changed = False
        passes += 1
        for module, name, node, params in funcs:
            new_patterns = _helper_call_patterns(module, name, node, params, helpers, aliases,
                                                 symbol_aliases, engine_parts)
            if not new_patterns:
                continue
            numeric = isinstance(node.returns, ast.Name) and node.returns.id in ("int", "float")
            key = (module, name)
            existing = helpers.get(key, {"patterns": set(), "numeric": False})
            merged = set(existing["patterns"]) | new_patterns
            merged_numeric = existing["numeric"] or numeric
            if merged != existing["patterns"] or merged_numeric != existing["numeric"]:
                helpers[key] = {"patterns": merged, "numeric": merged_numeric}
                changed = True

    return {key: {"patterns": sorted(spec["patterns"]), "numeric": spec["numeric"]}
           for key, spec in helpers.items()}


def _package_env_names(pkg: Path = PKG) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Every environment variable name this scanner can discover in `nevertwice/*.py`, split
    into `(discovered, auto_numeric)`.

    Covers, by AST, with a LITERAL string name in every case:
      * `os.environ.get(...)` / `os.getenv(...)` / `os.environ[...]` anywhere in the package;
      * a call to any helper `_env_read_helpers()` finds (`env_int`/`env_float`/`_env_float`
        -> `auto_numeric`, since their own return annotation makes the value numeric by
        construction; `config.env(suffix)` -> BOTH `NEVERTWICE_<suffix>` and
        `CLAUDE_MEMORY_<suffix>`, into `discovered` like any other name, since a suffix can
        resolve to either a path or not (`VAULT` does; `PROFILE` does not));
      * HELPERS OF HELPERS, TO ANY DEPTH - `budget._env_int` reads nothing itself; it calls
        `_env_float(name, ...)`, so it is a helper only because `_env_float` is, and its own
        `-> int` (never `_env_float`'s numeric-ness, which is never inherited) is what makes
        ITS call sites auto-numeric. `_env_read_helpers()`'s fixpoint has no depth limit -
        a helper of a helper of a helper is found the same way a first-order one is;
      * an ALIASED call across a genuine module boundary (`m.env_int`, `m` aliasing
        `memory_hook`; `_cfg.env`, `_cfg` aliasing `config`) or an explicit symbol import
        (`from .config import env`, `_symbol_aliases`), resolved by `_resolve_call` - see its
        own docstring and (в)5/(в)6;
      * (в)6, item (1) - AN UNRESOLVED OR AMBIGUOUS call whose bare/attribute name still
        matches SOME helper's name somewhere in the package (`_resolve_call`'s `broad` return)
        is not silently dropped: its literal is put into `discovered`, NEVER `auto_numeric`
        (fail closed on the number, same as an ordinary collision), with the UNION of every
        matching candidate's patterns applied (so nothing is missed for want of guessing which
        one), and labelled `"unresolved: <file>:<name>"` - which the classification check below
        then forces to be pinned or allowlisted like any other name, and a dedicated check
        lists by name (`_helper_fixpoint's` FAIL-CLOSED discipline was originally "fail closed
        for numeric" only; before this fix an unresolved/ambiguous call was ALSO invisible to
        DISCOVERY - dropped outright, the auditor's V4/V5 - which is the unsafe direction for a
        coverage tool: a real path could vanish from the scan entirely, never even reaching the
        wall/allowlist check that is this whole suite's point).

    Does NOT cover, and cannot by construction: a name built at runtime from something that is
    not a literal at the call site (`os.environ.get(some_variable)`); a name passed as a
    KEYWORD argument, or in any position other than FIRST, to `os.environ.get`/`os.getenv`/a
    known helper; `"X" in os.environ` membership tests, `os.environ.setdefault(...)`/`.pop(...)`,
    or a WRITE via `os.environ[...] = ...` (none of these are "reads" this scanner is asked to
    cover, and an independent enumeration of this package by the auditing session found none
    that would add a name beyond what the shapes above already find - `env_enum_probe.py` /
    `env_enum_probe2.py`, not shipped with this repository); a call reached through anything
    other than a bare name, a same-exec-namespace sibling call, one level of attribute access,
    or an explicit symbol import (`_resolve_call`'s docstring names the exact shapes it tries).
    """
    helpers = _env_read_helpers(pkg)
    engine_parts = _engine_parts(pkg)
    aliases = _module_aliases(pkg)
    symbol_aliases = _symbol_aliases(pkg)
    found: dict[str, list[str]] = {}
    numeric: dict[str, list[str]] = {}
    for path in sorted(pkg.glob("*.py")):
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                is_environ_get = (isinstance(target, ast.Attribute) and target.attr == "get"
                                  and isinstance(target.value, ast.Attribute)
                                  and target.value.attr == "environ"
                                  and isinstance(target.value.value, ast.Name)
                                  and target.value.value.id == "os")
                is_getenv = (isinstance(target, ast.Attribute) and target.attr == "getenv"
                            and isinstance(target.value, ast.Name) and target.value.id == "os")
                if (is_environ_get or is_getenv) and node.args \
                        and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    found.setdefault(node.args[0].value, []).append(path.name)
                    continue
                if not (node.args and isinstance(node.args[0], ast.Constant)
                       and isinstance(node.args[0].value, str)):
                    continue
                literal = node.args[0].value
                resolved, broad = _resolve_call(module, target, helpers, aliases,
                                                symbol_aliases, engine_parts)
                if resolved:
                    spec = helpers[resolved]
                    for pattern in spec["patterns"]:
                        name = pattern.format(literal)
                        label = f"{path.name}:{resolved[0]}.{resolved[1]}"
                        if spec["numeric"] and pattern == "{}":
                            numeric.setdefault(name, []).append(label)
                        else:
                            found.setdefault(name, []).append(label)
                elif broad:
                    # (в)6, item (1): a name that WOULD be a helper call if it resolved, but
                    # does not - never `auto_numeric` (fail closed), never dropped (fail open
                    # for discovery: apply every candidate's patterns, so nothing vanishes).
                    call_name = target.id if isinstance(target, ast.Name) else target.attr
                    label = f"unresolved: {path.name}:{call_name}"
                    for cand in broad:
                        for pattern in helpers[cand]["patterns"]:
                            found.setdefault(pattern.format(literal), []).append(label)
            elif isinstance(node, ast.Subscript):
                val = node.value
                if (isinstance(val, ast.Attribute) and val.attr == "environ"
                        and isinstance(val.value, ast.Name) and val.value.id == "os"):
                    sl = node.slice
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                        found.setdefault(sl.value, []).append(path.name)
    return found, numeric


#: Every name `_package_env_names()` can discover that is NOT a filesystem path this test
#: should require `walled()` to pin - or IS one, but deliberately kept out of the wall (see
#: `tests/_wall.py`'s module docstring for `APPDATA`/`XDG_CONFIG_HOME`). One line each: what
#: the name actually holds, so extending this allowlist means looking, not copying the shape.
#: `NEVERTWICE_CLOUD` is not here - it is a non-path value too, but it is already covered by
#: being in `_wall.PINNED_VARS` (walled() sets it to "none" for an unrelated reason: no real
#: network calls from a sandboxed run), and the check below treats PINNED as satisfying either
#: requirement.
ALLOWLIST: dict[str, str] = {
    # -- credentials --
    "CEREBRAS_API_KEY": "a credential string for the Cerebras extraction backend",
    "DEEPSEEK_API_KEY": "a credential string for the DeepSeek extraction backend",
    "GEMINI_API_KEY": "a credential string for the Gemini extraction/embed backend",
    "GROQ_API_KEY": "a credential string for the Groq extraction backend",
    # -- HTTP endpoints --
    "CEREBRAS_URL": "an HTTP endpoint URL, not a filesystem path",
    "DEEPSEEK_URL": "an HTTP endpoint URL, not a filesystem path",
    "GEMINI_URL": "an HTTP endpoint URL, not a filesystem path",
    "GROQ_URL": "an HTTP endpoint URL, not a filesystem path",
    "NEVERTWICE_EMBED_BASE_URL": "an HTTP endpoint URL for a self-hosted embed backend",
    "OLLAMA_EMBED_URL": "an HTTP endpoint URL, not a filesystem path",
    "OLLAMA_HOST": "an HTTP host:port string `doctor.py` prints, not a path",
    "OLLAMA_TAGS_URL": "an HTTP endpoint URL, not a filesystem path",
    "OLLAMA_URL": "an HTTP endpoint URL, not a filesystem path",
    # -- model / provider / mode / label names --
    "NEVERTWICE_AGENT": "the agent label stamped on captured notes, a string",
    "NEVERTWICE_CEREBRAS_MODEL": "a model name string",
    "NEVERTWICE_DEEPSEEK_MODEL": "a model name string",
    "NEVERTWICE_EMBED_MODEL": "a model name string (sandbox_guard.py already scrubs this for the in-process suites)",
    "NEVERTWICE_EMBED_PROVIDER": "a provider name string (ollama/openai/voyage/cohere/gemini)",
    "NEVERTWICE_EMBED_QUANT": "a quantization mode string",
    "NEVERTWICE_GEMINI_MODEL": "a model name string",
    "NEVERTWICE_GROQ_MODEL": "a model name string",
    "NEVERTWICE_MODEL": "a model name string (local Ollama extraction model)",
    "NEVERTWICE_RANKER": "a ranking-mode name string",
    "NEVERTWICE_USER_MODEL": "a model name string",
    "NEVERTWICE_WRITE_DEDUP_MODE": "a dedup-mode name string",
    "NEVERTWICE_XRERANK_MODEL": "a model name string",
    "NEVERTWICE_TWIN_SPACE": "a calibration SPACE LABEL string, not the calibration file itself (that is NEVERTWICE_TWIN_FILE, which IS pinned)",
    "NEVERTWICE_PROFILE": "a comma-separated profile-name list (coding/research/general), not a path - via config.env('PROFILE')",
    "CLAUDE_MEMORY_PROFILE": "the legacy-prefixed twin of NEVERTWICE_PROFILE above (config.env's own CLAUDE_MEMORY_ expansion) - same non-path value",
    "CLAUDE_MEMORY_EMBED_MODEL": "the legacy-prefixed twin of NEVERTWICE_EMBED_MODEL above (config.env's own CLAUDE_MEMORY_ expansion) - same non-path value",
    # -- flags, thresholds, numbers, text prefixes: none are paths --
    "NEVERTWICE_ADAPTIVE_RECUR": "a boolean flag string",
    "NEVERTWICE_ATTACH_EARLIER_ALWAYS": "a boolean flag string",
    "NEVERTWICE_CLOUD_ONLY": "a boolean flag string",
    "NEVERTWICE_CROSS_PROJECT": "a boolean flag string",
    "NEVERTWICE_EMBED_DOC_PREFIX": "a text prefix string prepended to embedded documents",
    "NEVERTWICE_EMBED_PREFIX": "a text prefix string",
    "NEVERTWICE_EMBED_QUERY_PREFIX": "a text prefix string prepended to embedded queries",
    "NEVERTWICE_EXPLICIT_RETIRE": "a mode-selector string (write/judge)",
    "NEVERTWICE_EXTRACT_TEMP": "an LLM sampling temperature (float), not a path",
    "NEVERTWICE_FUSION": "a boolean/mode flag string for retrieval score fusion",
    "NEVERTWICE_GIT_PUSH": "a boolean flag string",
    "NEVERTWICE_GUARDS_HOTPATH": "a boolean flag string",
    "NEVERTWICE_GUARD_ENFORCE": "a boolean flag string",
    "NEVERTWICE_GUARD_PACK": "a boolean flag string (seed the universal guard pack)",
    "NEVERTWICE_INJECT": "a boolean flag string",
    "NEVERTWICE_INJECT_RECEIPT": "a boolean flag string",
    "NEVERTWICE_LEXICAL_MORPHOLOGY": "a boolean/mode flag string",
    "NEVERTWICE_LOCAL_ONLY": "a boolean/list flag string (which agents stay off cloud)",
    "NEVERTWICE_MAX_DOC_BYTES": "an integer byte cap, not a path",
    "NEVERTWICE_PROJECT_CARD": "a boolean flag string",
    "NEVERTWICE_PROMPT_RECALL": "a boolean flag string",
    "NEVERTWICE_PROMPT_RECALL_MODE": "a mode-selector string",
    "NEVERTWICE_QUARANTINE": "a boolean flag string",
    "NEVERTWICE_RERANK": "a boolean flag string",
    "NEVERTWICE_SERVE_FACTS": "a boolean flag string",
    "NEVERTWICE_STALE_CHECK": "a boolean flag string",
    "NEVERTWICE_START_SWEEP_DETACH": "a boolean flag string",
    "NEVERTWICE_TRACK_ANY_PROJECT": "a boolean flag string",
    "NEVERTWICE_XRERANK": "a boolean flag string (turn the cross-encoder reranker on/off)",
    "NEVERTWICE_XRERANK_MAXLEN": "an integer token-length cap, not a path",
    # -- real Windows system directories used only as text-classification REFERENCE
    # constants (_engine_text.py's _SYS_DIRS, to exclude them from "is this a project"
    # matching) - never opened or listed; pinning them to a fake path would break the
    # exclusion they exist for, not fix a leak --
    "ProgramData": "a Windows system dir NAME used only to exclude it from project detection - never read/listed",
    "ProgramFiles": "a Windows system dir NAME used only to exclude it from project detection - never read/listed",
    "ProgramFiles(x86)": "a Windows system dir NAME used only to exclude it from project detection - never read/listed",
    "SystemRoot": "a Windows system dir NAME used only to exclude it from project detection - never read/listed",
    # -- deliberately-not-pinned PATHS: see tests/_wall.py's module docstring --
    "APPDATA": "a real path, deliberately NOT pinned directly - it also governs Windows user-site package resolution, and pinning it broke `import pytest` in every child process (measured). Mitigated at the adapter level instead: NEVERTWICE_CURSOR_EXPORT and NEVERTWICE_VSCODE_GLOBALSTORAGE_ROOT ARE pinned, and both are checked before this.",
    "XDG_CONFIG_HOME": "unlike APPDATA, NOT measured to break anything - Python's user site on Linux is ~/.local/lib/pythonX.Y/site-packages and on macOS ~/Library/Python/X.Y, never XDG_CONFIG_HOME. Kept unpinned for the CORRECT reason instead: both readers (hosts.py:615 CursorAdapter.roots(), watch.py:112 _vscode_globalstorage_bases()) consult it only in the `else` branch AFTER their own NEVERTWICE_* override is checked, and the wall pins both overrides (NEVERTWICE_CURSOR_EXPORT, NEVERTWICE_VSCODE_GLOBALSTORAGE_ROOT) - so under walled(), that `else` branch is never reached at all. test_every_host_adapter_and_watch_base_resolves_inside_the_wall is the evidence: it drives both readers end to end and both resolve inside tmp.",
}


#: Names that ARE pinned but are not path-shaped at all (their value is a mode string, not a
#: directory), so checking them against tmp would be asking the wrong question. Each is pinned
#: for its own, unrelated reason (`_wall.py`'s `walled()`), not because it is a path.
_NON_PATH_PINNED = {"NEVERTWICE_CLOUD", "CLAUDE_MEMORY_CLOUD"}


def test_walled_covers_or_allowlists_every_env_name_in_the_package() -> None:
    print("\n- walled() covers or allowlists every env name the package reads (F1) -")
    discovered, auto_numeric = _package_env_names()
    check("the scan actually found names (a scan over nothing proves nothing)",
          len(discovered) >= 50, str(len(discovered)))
    check("and the helper-expansion pass found the numeric env_int/env_float/_env_float names "
          "too (auto-classified, never hand-listed)",
          len(auto_numeric) >= 50, str(len(auto_numeric)))

    pinned = set(_wall.WALL_VARS) | set(_wall.PINNED_VARS)
    allowlisted = set(ALLOWLIST)
    numeric_names = set(auto_numeric)

    pinned_and_allowlisted = pinned & allowlisted
    pinned_and_numeric = pinned & numeric_names
    allowlisted_and_numeric = allowlisted & numeric_names
    check("no name is pinned AND allowlisted (one classification per name)",
          not pinned_and_allowlisted, str(pinned_and_allowlisted))
    check("no name is pinned AND auto-numeric", not pinned_and_numeric, str(pinned_and_numeric))
    check("no name is allowlisted AND auto-numeric - a numeric env_int/env_float name does "
          "not ALSO need a hand-written ALLOWLIST reason", not allowlisted_and_numeric,
          str(allowlisted_and_numeric))

    unclassified = sorted(set(discovered) - pinned - allowlisted)
    check(f"every discovered name is pinned by walled() or allowlisted with a reason: "
          f"{len(discovered)} discovered, {len(pinned)} pinned, {len(ALLOWLIST)} allowlisted, "
          f"{len(auto_numeric)} auto-numeric",
          not unclassified,
          f"unclassified: {[(n, discovered[n]) for n in unclassified]}")

    # (в)6, item (3): every call site `_resolve_call` could not pin to exactly one definition,
    # but which still matched a known helper's name somewhere - named here, not just counted.
    # Empty on the unmutated tree; the auditor's V5 (a second `env_int` appended to an engine
    # part, making every `m.env_int`/in-group bare call ambiguous) lists every affected call
    # site by `file:name` when this check goes red.
    unresolved_sites = sorted({label for labels in discovered.values() for label in labels
                              if label.startswith("unresolved: ")})
    check("no call site is unresolved or ambiguous - every bare/attribute call this scanner "
          "recognised as a helper-shaped name resolves to exactly one (module, function)",
          not unresolved_sites, "; ".join(unresolved_sites))

    check("every allowlist entry actually carries a one-line reason (not blank/placeholder)",
          all(isinstance(r, str) and len(r.strip()) >= 8 for r in ALLOWLIST.values()),
          str([n for n, r in ALLOWLIST.items() if len(r.strip()) < 8]))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _wall.walled(tmp)
        tmp_r = str(tmp.resolve())
        discovered_pinned = sorted(set(discovered) & pinned)
        missing = [n for n in discovered_pinned if n not in env]
        path_shaped = [n for n in discovered_pinned if n not in _NON_PATH_PINNED]
        escaping = [n for n in path_shaped if n in env and env[n] and not (
            str(Path(env[n]).resolve()) == tmp_r
            or str(Path(env[n]).resolve()).startswith(tmp_r + os.sep))]
        check(f"every discovered PINNED name is actually set by walled(): {discovered_pinned}",
              not missing, f"missing: {missing}")
        check("and every path-shaped one of them (non-empty) resolves inside the wall",
              not escaping, f"escaping: {escaping}")

    check("APPDATA and XDG_CONFIG_HOME are allowlisted, never pinned directly - the specific "
          "F1 caution this test exists to hold the line on",
          "APPDATA" not in pinned and "XDG_CONFIG_HOME" not in pinned
          and "APPDATA" in ALLOWLIST and "XDG_CONFIG_HOME" in ALLOWLIST)

    # (в)1 acceptance: this scanner's total (discovered + auto-numeric) is 171, not the
    # auditing session's 168 - and every one of the 11 name-level differences (7 gained, 4
    # lost) is accounted for below, none silently absorbed:
    #
    #   LOST (4) - the auditor's own probe records config.env(suffix) call sites by their RAW
    #   SUFFIX ("VAULT", "PROFILE", "CLOUD", "EMBED_MODEL"), never expanded. Requirement (c)
    #   is to expand each to BOTH NEVERTWICE_<suffix> and CLAUDE_MEMORY_<suffix> - which is
    #   what config.env() itself actually reads - so none of the 4 raw suffixes appear here;
    #   they are correctly replaced by their 8 real expansions (below).
    #
    #   GAINED (7) - 6 are exactly those 8 expansions, minus NEVERTWICE_CLOUD and
    #   NEVERTWICE_EMBED_MODEL, which were ALREADY discovered independently (a direct literal
    #   read elsewhere in the package), so expanding them adds no new name; only their
    #   CLAUDE_MEMORY_ twins, plus both of VAULT's and PROFILE's expansions, are new:
    #   NEVERTWICE_VAULT, CLAUDE_MEMORY_VAULT, NEVERTWICE_PROFILE, CLAUDE_MEMORY_PROFILE,
    #   CLAUDE_MEMORY_CLOUD, CLAUDE_MEMORY_EMBED_MODEL.
    #
    #   The 7th, NEVERTWICE_EXTRACT_RETRY (`_engine_cards.py:810`,
    #   `env_int("NEVERTWICE_EXTRACT_RETRY", 0)`), is a genuine finding the auditor's own probe
    #   MISSES - not a difference in what either scanner is asked to cover. The probe builds
    #   its `helpers` set incrementally, file by file, in the SAME single pass it scans call
    #   sites in (`for path in sorted(...): <find helpers in this file> ... <find call sites
    #   in this file>`), so a helper is only recognised at call sites in files sorted AFTER
    #   the file that DEFINES it. `_engine_cards.py` sorts alphabetically BEFORE
    #   `_engine_config.py` (which defines `env_int`), so this one call site is invisible to
    #   the probe's own logic regardless of which scanner it is compared against. This
    #   scanner's `_env_read_helpers()` is a separate, complete pass BEFORE any call site is
    #   examined, so file order cannot hide a call site from it - confirmed directly by
    #   reading the line the probe's own ordering skips.
    #
    # (в)3 adds 4 more on top of that 171: NEVERTWICE_BUDGET_TURN_TOKENS,
    # _SESSION_TOKENS, _TURN_LATENCY_MS and _SESSION_LATENCY_MS (`budget.py:97-103`), read
    # through `budget._env_int`, which is itself a SECOND-ORDER helper - it has no
    # `os.environ.get` of its own, only a call to `_env_float(name, ...)` - invisible to a
    # single, non-fixpoint pass (the auditor's mutation Z2 is exactly this shape, one level
    # deeper: `_sweep_root` -> `_raw` -> `os.environ.get`). 171 + 4 = 175.
    all_names = set(discovered) | set(auto_numeric)
    expected_lost = {"VAULT", "PROFILE", "CLOUD", "EMBED_MODEL"}
    expected_gained = {"NEVERTWICE_VAULT", "CLAUDE_MEMORY_VAULT", "NEVERTWICE_PROFILE",
                       "CLAUDE_MEMORY_PROFILE", "CLAUDE_MEMORY_CLOUD", "CLAUDE_MEMORY_EMBED_MODEL",
                       "NEVERTWICE_EXTRACT_RETRY", "NEVERTWICE_BUDGET_TURN_TOKENS",
                       "NEVERTWICE_BUDGET_SESSION_TOKENS", "NEVERTWICE_BUDGET_TURN_LATENCY_MS",
                       "NEVERTWICE_BUDGET_SESSION_LATENCY_MS"}
    expected_total = 168 - len(expected_lost) + len(expected_gained)
    check(f"this scanner finds {len(all_names)} names - every difference from the auditor's "
          f"168 named above: {len(expected_gained)} gained, {len(expected_lost)} lost "
          f"(expected total {expected_total})",
          len(all_names) == expected_total,
          f"{len(all_names)} names (expected {expected_total})")
    check("all four budget.py names are found and classified auto-numeric (budget._env_int's "
          "OWN -> int, not inherited from _env_float)",
          {"NEVERTWICE_BUDGET_TURN_TOKENS", "NEVERTWICE_BUDGET_SESSION_TOKENS",
           "NEVERTWICE_BUDGET_TURN_LATENCY_MS", "NEVERTWICE_BUDGET_SESSION_LATENCY_MS"}
          <= set(auto_numeric))
    check("NEVERTWICE_EXTRACT_RETRY is found (the probe's own file-ordering miss)",
          "NEVERTWICE_EXTRACT_RETRY" in all_names)


def test_helper_fixpoint_bounded_and_module_qualified() -> None:
    """(в)4, (в)5 and (в)6 - permanent regression coverage for the bugs the auditing session
    found in `_env_read_helpers()`/`_resolve_call()`, reproduced against SYNTHETIC temp
    packages (never the real `nevertwice/` tree, via `_env_read_helpers(pkg=...)`/
    `_package_env_names(pkg=...)`'s own `pkg` parameter) so these run automatically, every
    session, with nothing to mutate or revert by hand.

    (в)4, W5 - a function calling itself. `_loop(name, depth=0)` returns `os.environ.get(name,
    '')` when `depth`, else `_loop(f'X_{name}', 1)` - a real direct `os.environ.get` call AND a
    call to itself, both found by a static AST walk that does not evaluate the ternary. Before
    this fix, composing patterns from every call to an already-known helper (with no check for
    "is this call the function calling itself") grew the prefix by one more `X_` every single
    pass, forever: the scanner AT 4e13d14 did not finish this shape within a 12s bound in this
    session's own reproduction against the real package (subprocess killed by timeout, zero
    output) - the auditor reported 60s+ there. Checked here: convergence well under a second,
    AND `_loop`'s own pattern set stays exactly `{"{}"}`  -  the second assertion is the one
    that actually proves the self-call was REFUSED, since a bug that merely finished fast for
    an unrelated reason would still pass a timing-only check.

    (в)4, a 2-function cycle - the shape self-call refusal alone does NOT stop (`_a` calls
    `_b`, `_b` calls `_a`; neither call is literally "a function calling itself"), included so
    the OTHER two guards (the `len(funcs) + 1` pass bound, `MAX_PATTERN_LEN`) are exercised by
    something, not just present in the source with no test ever tripping them. With only two
    functions in this synthetic package the pass bound (3) is reached long before any pattern
    nears 128 characters, so this specifically proves the bound - `_env_read_helpers()` must
    raise `HelperFixpointError`, by name, rather than loop.

    (в)5, W6 - two functions sharing a bare name in two different modules, one `-> float`
    (numeric) and one `-> str` (not). At 4e13d14, `helpers` was keyed by bare name, so the
    second function's own call site got OR-merged onto the FIRST's numeric flag - a path
    masquerading as a number, the unsafe direction, confirmed against the REAL package earlier
    in this session (`budget._env_float` vs. a synthetic `hosts._env_float`: the auditor's own
    shape, reproduced there and reverted - this permanent version never touches `hosts.py`).
    Checked here: the non-numeric module's own call resolves to ITS OWN definition and is
    `discovered`, never `auto_numeric`.

    (в)6, item (2), V4 - an explicit `from .config import env` (a SYMBOL import, not a module
    one) followed by a bare `env(...)` call. Resolved through `_symbol_aliases` to `config`'s
    own `env`, exactly as `from .config import env` in `hosts.py` would be - checked here with
    a synthetic `config_mod.env`.

    (в)6, item (1), V5 - a SECOND `env_int` appended to the engine group (two `_engine_*.py`
    parts both defining it) makes every `m.env_int(...)` call ambiguous - two matches, not one.
    At 4e13d14's successor (517a373, still keyed by `(module, function)` but with no fallback
    for "resolved to nothing"), such a call's literal was silently DROPPED from `discovered`
    entirely - not merely misclassified, invisible, so the wall/allowlist check downstream had
    nothing to complain about even though a real path could take this shape. Checked here: the
    literal is `discovered` (never `auto_numeric`, fail closed on the number) AND the call site
    is named in a dedicated "unresolved" list (`unresolved: <file>:<name>`), not merely a count
    mismatch - matching the coordinator's own acceptance wording for V5.
    """
    print("\n- (в)4/(в)5/(в)6: the helper fixpoint is bounded, module-qualified, and never "
         "silently drops an unresolved or ambiguous call -")
    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td)
        (pkg / "_engine.py").write_text("ENGINE_PARTS = ()\n", encoding="utf-8", newline="")

        # --- (в)4, W5: a function calling itself -------------------------------------------
        (pkg / "loopy.py").write_text(
            "import os\n\n"
            "def _loop(name: str, depth: int = 0) -> str:\n"
            "    return os.environ.get(name, '') if depth else _loop(f'X_{name}', 1)\n",
            encoding="utf-8", newline="")
        t0 = time.time()
        helpers = _env_read_helpers(pkg)
        dt = time.time() - t0
        check(f"W5 (a helper that calls itself) converges instead of composing forever - "
              f"{dt:.3f}s (4e13d14 did not finish a 12s bound against the real package)",
              dt < 5.0, f"{dt:.3f}s")
        loop_spec = helpers.get(("loopy", "_loop"))
        check("and W5's own pattern set stays exactly {'{}'} - the self-call was refused, not "
              "merely slow to finish for an unrelated reason",
              loop_spec is not None and loop_spec["patterns"] == ["{}"]
              and loop_spec["numeric"] is False,
              str(loop_spec))
        (pkg / "loopy.py").unlink()

        # --- (в)4, a 2-function cycle: proves the PASS-COUNT bound, not just self-refusal ---
        (pkg / "cycle_mod.py").write_text(
            "import os\n\n"
            "def _a(name: str, depth: int = 0) -> str:\n"
            "    return os.environ.get(name, '') if depth else _b(f'A_{name}')\n\n"
            "def _b(name: str) -> str:\n"
            "    return _a(name, 1)\n",
            encoding="utf-8", newline="")
        try:
            _env_read_helpers(pkg)
            cycle_raised, cycle_msg = False, ""
        except HelperFixpointError as exc:
            cycle_raised, cycle_msg = True, str(exc)
        check("a 2-function cycle (neither call is literally self-recursive) trips the "
              "PASS-COUNT bound instead of looping - HelperFixpointError, by name",
              cycle_raised and "cycle_mod:_a" in cycle_msg and "cycle_mod:_b" in cycle_msg,
              cycle_msg or "no exception raised")
        (pkg / "cycle_mod.py").unlink()

        # --- (в)5, W6: two modules, same bare function name, different numeric-ness ---------
        (pkg / "numeric_mod.py").write_text(
            "import os\n\n"
            "def _env_float(name: str) -> float:\n"
            "    raw = os.environ.get(name)\n"
            "    return float(raw) if raw else 0.0\n",
            encoding="utf-8", newline="")
        (pkg / "stringy_mod.py").write_text(
            "import os\n\n"
            "def _env_float(name: str) -> str:\n"
            "    return os.environ.get(name, '')\n\n"
            "_PROBE = _env_float('NEVERTWICE_COLLIDE_DIR')\n",
            encoding="utf-8", newline="")
        found, numeric = _package_env_names(pkg)
        check("W6: stringy_mod's OWN _env_float is not merged with numeric_mod's same-named "
              "function - NEVERTWICE_COLLIDE_DIR is discovered, never silently auto-numeric",
              "NEVERTWICE_COLLIDE_DIR" in found and "NEVERTWICE_COLLIDE_DIR" not in numeric,
              f"found={'NEVERTWICE_COLLIDE_DIR' in found} "
              f"numeric={'NEVERTWICE_COLLIDE_DIR' in numeric}")
        (pkg / "numeric_mod.py").unlink()
        (pkg / "stringy_mod.py").unlink()

        # --- (в)5 addendum: the ENGINE_PARTS re-export chain, m.env_int's own real shape ----
        (pkg / "_engine.py").write_text("ENGINE_PARTS = ('_engine_config.py',)\n",
                                        encoding="utf-8", newline="")
        (pkg / "memory_hook.py").write_text(
            "# a loader: execs _engine.py's parts into this module's own namespace, never\n"
            "# imports them - see nevertwice/_engine.py's own module docstring.\n",
            encoding="utf-8", newline="")
        (pkg / "_engine_config.py").write_text(
            "import os\n\n"
            "def env_int(name: str, default: int) -> int:\n"
            "    raw = os.environ.get(name)\n"
            "    return int(raw) if raw else default\n",
            encoding="utf-8", newline="")
        (pkg / "caller_mod.py").write_text(
            "try:\n"
            "    from . import memory_hook as m\n"
            "except ImportError:\n"
            "    import memory_hook as m\n\n"
            "VALUE = m.env_int('NEVERTWICE_ENGINE_CHAIN_PROBE', 5)\n",
            encoding="utf-8", newline="")
        found2, numeric2 = _package_env_names(pkg)
        check("the ENGINE_PARTS re-export chain resolves m.env_int to _engine_config's own "
              "env_int (auto-numeric) even though memory_hook.py itself never defines or "
              "imports it - the shape ~40 real names (NEVERTWICE_ANTICIPATE_TAU and siblings) "
              "depend on, per the coordinator's addendum",
              "NEVERTWICE_ENGINE_CHAIN_PROBE" in numeric2,
              str(numeric2.get("NEVERTWICE_ENGINE_CHAIN_PROBE")))

        # --- (в)6, item (2): an explicit `from .X import f` symbol import resolves a bare
        # call to its true origin, exactly like V4 (`from .config import env` in hosts.py) --
        (pkg / "config_mod.py").write_text(
            "import os\n\n"
            "def env(name: str, default=None):\n"
            "    return os.environ.get(f'NEVERTWICE_{name}',\n"
            "                          os.environ.get(f'CLAUDE_MEMORY_{name}', default))\n",
            encoding="utf-8", newline="")
        (pkg / "importer_mod.py").write_text(
            "try:\n"
            "    from .config_mod import env\n"
            "except ImportError:\n"
            "    from config_mod import env\n\n"
            "PROBE = env('SYMBOL_IMPORT_PROBE')\n",
            encoding="utf-8", newline="")
        found3, numeric3 = _package_env_names(pkg)
        check("(в)6 item (2): a symbol import resolves a bare call to its true origin - "
              "NEVERTWICE_SYMBOL_IMPORT_PROBE and its CLAUDE_MEMORY_ twin are discovered even "
              "though importer_mod.py defines no env() of its own and is not memory_hook or "
              "an engine part",
              "NEVERTWICE_SYMBOL_IMPORT_PROBE" in found3
              and "CLAUDE_MEMORY_SYMBOL_IMPORT_PROBE" in found3,
              str({k: v for k, v in found3.items() if "SYMBOL_IMPORT_PROBE" in k}))
        (pkg / "config_mod.py").unlink()
        (pkg / "importer_mod.py").unlink()

        # --- (в)6, item (1): an unresolved/ambiguous call is DISCOVERED, never silently
        # dropped and never silently auto-numeric - V5's shape, a second env_int appended to
        # the engine group makes the EXISTING caller_mod.py call (still present from the
        # ENGINE_PARTS scenario above) ambiguous ----------------------------------------------
        (pkg / "_engine_extra.py").write_text(
            "import os\n\n"
            "def env_int(name: str, default: int) -> int:\n"
            "    raw = os.environ.get(name)\n"
            "    return int(raw) if raw else default\n",
            encoding="utf-8", newline="")
        (pkg / "_engine.py").write_text(
            "ENGINE_PARTS = ('_engine_config.py', '_engine_extra.py')\n",
            encoding="utf-8", newline="")
        found4, numeric4 = _package_env_names(pkg)
        check("(в)6 item (1): a SECOND env_int appended to the engine group makes "
              "m.env_int('NEVERTWICE_ENGINE_CHAIN_PROBE', ...) ambiguous (two matches) - the "
              "literal is DISCOVERED, non-numeric, never silently dropped and never silently "
              "auto-numeric",
              "NEVERTWICE_ENGINE_CHAIN_PROBE" in found4
              and "NEVERTWICE_ENGINE_CHAIN_PROBE" not in numeric4,
              f"found={'NEVERTWICE_ENGINE_CHAIN_PROBE' in found4} "
              f"numeric={'NEVERTWICE_ENGINE_CHAIN_PROBE' in numeric4} "
              f"labels={found4.get('NEVERTWICE_ENGINE_CHAIN_PROBE')}")
        unresolved4 = sorted({lbl for labels in found4.values() for lbl in labels
                             if lbl.startswith("unresolved: ")})
        check("and the unresolved call site is named by file:function - V5's acceptance was "
              "LISTING them, not only a count mismatch",
              bool(unresolved4) and all(lbl.endswith(":env_int") for lbl in unresolved4),
              str(unresolved4))


def test_every_host_adapter_and_watch_base_resolves_inside_the_wall() -> None:
    """F1, end to end - not "is the env var pinned" (the scanner test above) but "does each
    adapter's roots() and watch's globalstorage bases actually resolve inside the wall". A
    mis-pinned name, or an adapter reading a DIFFERENT name than the one pinned, would still
    fail this even though the scanner test passed. Mirrors the auditing session's own
    `wall_reach_probe.py` (read for the shape, not modified)."""
    print("\n- every host adapter and watch base resolves inside the wall (F1, end to end) -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _wall.walled(tmp)
        code = (
            "import sys;sys.path[:]=[p for p in sys.path if p not in ('','.')];"
            "sys.path.insert(0,'.');"
            "from nevertwice import hosts, watch;"
            "[print(a.name, *a.roots()) for a in (c() for c in hosts._ADAPTERS)];"
            "print('watch', *watch._vscode_globalstorage_bases()[:2])"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, timeout=60)
        check("the probe process exits 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr[-400:]}")
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        check("the probe printed something for every adapter plus watch",
              len(lines) >= 5, str(lines))
        # A line with no PATHS after the label (e.g. `generic-jsonl` with nothing configured)
        # has nothing to leak - only a line that names a real path is checked.
        with_paths = [ln for ln in lines if len(ln.split()) > 1]
        outside = [ln for ln in with_paths if str(tmp) not in ln]
        check(f"every adapter root and watch base resolves inside the wall: {lines}",
              not outside, f"outside: {outside}")


# ------------------------------------------------------------- pure functions (hookwire)


def test_tokens_and_hook_command() -> None:
    print("\n- tokens() and hook_command() -")
    check("bare tokens split on whitespace",
          hookwire.tokens("python script.py arg") == ["python", "script.py", "arg"])
    check("quoted tokens keep embedded spaces, case preserved",
          hookwire.tokens('"C:/Python314/python.exe" "My Script.py"')
          == ["C:/Python314/python.exe", "My Script.py"])
    check("backslashes normalise to forward slashes",
          hookwire.tokens(r'"C:\Users\Me\python.exe" "C:\Users\Me\nevertwice\memory_hook.py"')
          == ["C:/Users/Me/python.exe", "C:/Users/Me/nevertwice/memory_hook.py"])
    check("empty command has no tokens", hookwire.tokens("") == [])

    cmd = hookwire.hook_command(r"C:\Py\python.exe", r"C:\home\nevertwice\hook_shim.py",
                                r"D:\clone\nevertwice\memory_hook.py")
    toks = hookwire.tokens(cmd)
    check("hook_command wires the interpreter, then -c, then a payload - 3 tokens",
          len(toks) == 3 and toks[0] == "C:/Py/python.exe" and toks[1] == "-c", str(toks))
    payload = toks[2]
    check("the payload is valid, self-contained Python source",
          _payload_parses(payload), payload)
    check("the payload carries the shim and engine paths, forward-slashed",
          "C:/home/nevertwice/hook_shim.py" in payload
          and "D:/clone/nevertwice/memory_hook.py" in payload, payload)
    check("the payload drops '' and '.' from sys.path BEFORE importing anything but sys",
          payload.startswith("import sys;sys.path[:]=[p for p in sys.path if p not in "
                             "('','.')];import"),
          payload)
    check("the payload contains no double quote, backtick or $ (must survive an outer "
          "double-quoted shell token, POSIX and PowerShell alike)",
          not any(c in payload for c in ('"', "`", "$")), payload)
    parsed = hookwire._parse_c_payload(payload)
    check("hookwire's own parser recovers the exact shim/engine pair from the payload",
          parsed == ("C:/home/nevertwice/hook_shim.py", "D:/clone/nevertwice/memory_hook.py"),
          str(parsed))

    for bad, char in (("C:/it's/here", "'"), ('C:/say "hi"/x', '"'),
                      ("C:/$HOME/x", "$"), ("C:/back`tick/x", "`")):
        try:
            hookwire.hook_command(bad, "shim", "engine")
            check(f"a path containing {char!r} is refused at construction time", False, bad)
        except ValueError as exc:
            check(f"a path containing {char!r} is refused at construction time",
                  char in str(exc), str(exc))


def test_is_ours_and_is_foreign_copy() -> None:
    print("\n- is_ours() / is_foreign_copy() -")
    check("an old-style direct memory_hook.py entry is ours",
          hookwire.is_ours({"command": '"python" "C:/x/nevertwice/memory_hook.py"'}))
    check("a shim entry is ours",
          hookwire.is_ours({"command": '"python" "C:/home/nevertwice/hook_shim.py" '
                                       '"C:/x/nevertwice/memory_hook.py"'}))
    flat = {"command": '"python" "C:/Users/me/.claude/scripts/memory_hook.py"'}
    check("a hand-rolled flat copy is a foreign copy, not ours",
          hookwire.is_foreign_copy(flat) and not hookwire.is_ours(flat))
    theirs = {"command": '"python" "/home/me/my_hook.py"'}
    check("someone else's unrelated hook is neither",
          not hookwire.is_ours(theirs) and not hookwire.is_foreign_copy(theirs))


def _legacy_shim_cmd(py, shim, engine) -> str:
    """The PRE-`-c` 3-token shape (`"<py>" "<shim>" "<engine>"`) - `hookwire.hook_command`
    itself no longer builds this (it builds the `-c` form now), but `dead_reason` still has to
    classify it correctly for an install from before this form existed, right up until the
    next `install.py` run repoints it. Built here, by hand, precisely because it must NOT come
    from `hook_command` any more."""
    return " ".join(f'"{str(p).replace(chr(92), "/")}"' for p in (py, shim, engine))


def test_dead_reason() -> None:
    print("\n- dead_reason() - which failures block the agent, which do not -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        engine = tmp / "nevertwice" / "memory_hook.py"
        engine.parent.mkdir(parents=True)
        engine.write_text("x = 1\n", encoding="utf-8")
        shim = tmp / "shimdir" / "nevertwice" / "hook_shim.py"
        shim.parent.mkdir(parents=True)
        shim.write_text("x = 1\n", encoding="utf-8")
        py = tmp / "python.exe"
        py.write_bytes(b"")

        print("  - the CURRENT -c form: nothing blocks any more -")
        live = {"command": hookwire.hook_command(py, shim, engine)}
        check("a fully-live -c entry is not dead", hookwire.dead_reason(live) is None)

        missing_engine = {"command": hookwire.hook_command(py, shim, tmp / "gone.py")}
        reason = hookwire.dead_reason(missing_engine)
        check("-c form, engine missing: dead but does NOT block",
              reason is not None and reason[1] is False, str(reason))
        check("...and names 'engine missing'", reason is not None and "engine missing" in reason[0])

        missing_shim = {"command": hookwire.hook_command(
            py, tmp / "goneshimdir" / "nevertwice" / "hook_shim.py", engine)}
        reason = hookwire.dead_reason(missing_shim)
        check("-c form, shim missing: dead but does NOT block (the -c wrapper degrades itself)",
              reason is not None and reason[1] is False, str(reason))
        check("...and names 'shim missing'", reason is not None and "shim missing" in reason[0])

        missing_interp = {"command": hookwire.hook_command(tmp / "gone_py.exe", shim, engine)}
        reason = hookwire.dead_reason(missing_interp)
        check("-c form, interpreter missing (a path, has a slash): dead but does not block",
              reason is not None and reason[1] is False, str(reason))

        bare_interp = {"command": hookwire.hook_command("python", shim, engine)}
        check("-c form, a bare 'python' interpreter (resolved on PATH) is never judged missing",
              hookwire.dead_reason(bare_interp) is None)

        print("  - the LEGACY 3-token shim form: unchanged, a missing SCRIPT still blocks -")
        legacy_live = {"command": _legacy_shim_cmd(py, shim, engine)}
        check("legacy shim form, fully live: not dead", hookwire.dead_reason(legacy_live) is None)

        legacy_missing_engine = {"command": _legacy_shim_cmd(py, shim, tmp / "gone2.py")}
        reason = hookwire.dead_reason(legacy_missing_engine)
        check("legacy shim form, engine missing: dead but does NOT block (unchanged)",
              reason is not None and reason[1] is False, str(reason))

        legacy_missing_shim = {"command": _legacy_shim_cmd(
            py, tmp / "goneshimdir2" / "nevertwice" / "hook_shim.py", engine)}
        reason = hookwire.dead_reason(legacy_missing_shim)
        check("legacy shim form, a missing SCRIPT (the shim itself) is dead and DOES block "
              "(no -c wrapper in the way to catch it)",
              reason is not None and reason[1] is True, str(reason))

        old_missing = {"command": f'"{py}" "{tmp / "gone3" / "nevertwice" / "memory_hook.py"}"'}
        reason = hookwire.dead_reason(old_missing)
        check("the OLDEST 2-token entry (no shim at all) whose memory_hook.py is gone "
              "is dead and DOES block", reason is not None and reason[1] is True, str(reason))

        check("an unrelated hook is never judged dead",
              hookwire.dead_reason({"command": '"python" "/home/me/my_hook.py"'}) is None)

        # Case preservation matters on a case-sensitive filesystem: this asserts the shape of
        # the check (it must use the case-preserved path for the isfile test), skipped only on
        # a filesystem that cannot even represent the distinction.
        wrong_case = str(engine).replace("memory_hook", "MEMORY_HOOK")
        case_sensitive = not Path(wrong_case).exists()
        if case_sensitive:
            reason = hookwire.dead_reason({"command": hookwire.hook_command(py, shim, wrong_case)})
            check("a wrong-case engine path is not silently treated as present",
                  reason is not None, str(reason))
        else:
            print("  --   (skipped: filesystem is case-insensitive)")


def test_hook_python() -> None:
    print("\n- hook_python() - never wire an interpreter that dies with the clone -")
    with tempfile.TemporaryDirectory() as td:
        clone = Path(td) / "clone"
        clone.mkdir()
        outside = Path(td) / "outside_python.exe"
        outside.write_bytes(b"")
        inside = clone / ".venv" / "Scripts" / "python.exe"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(b"")
        result, warning = hookwire.hook_python(str(outside), clone)
        check("an interpreter outside the clone is returned unchanged, no warning",
              result == str(outside) and warning is None, (result, warning))
        rebased, warning2 = hookwire.hook_python(str(inside), clone)
        check("an interpreter INSIDE the clone is rebased to something else",
              rebased != str(inside), rebased)
        check("...and the rebased interpreter is not itself under the clone",
              not str(Path(rebased).resolve()).startswith(str(clone.resolve()) + os.sep), rebased)
        check("...with no warning - a real base interpreter (this test's own) was found",
              warning2 is None, warning2)


def test_hook_python_symlinked_venv_escapes_the_clone() -> None:
    """(б), auditor finding 3, required test (b): `python -m venv` on POSIX makes the venv's
    own interpreter (`.venv/bin/python3`) a SYMLINK to the true base - `venv.create`'s own
    default there (`symlinks=True` on every POSIX platform). `.resolve()` alone FOLLOWS that
    symlink out of the clone, so the OLD "is this inside the clone" check (resolved-only)
    concluded "no, it doesn't need rebasing" and returned the symlink's OWN path, unchanged - a
    string that still lives inside the clone: delete the clone and that command's interpreter
    is gone (`sh` exit 127), memory goes silently off, with no shim message (the shim never
    even runs - `python3` itself is what's missing this time, not the engine after it).

    Fixed by checking `os.path.abspath(executable)` OR its `.resolve()` for "inside" (the
    symlink's own unresolved path IS inside the clone, even though what it points at is not),
    and by trying the RESOLVED real path as the very FIRST rebase candidate - which for a
    symlink means the true base, escaping in one step.

    Windows-skipped: creating a symlink there needs Developer Mode or an elevated process,
    neither assumed here - this is exactly the CI evidence class the coordinator named
    (ubuntu-3.10 / macos-3.10), so POSIX runners are where this actually proves itself."""
    print("\n- hook_python() - a SYMLINKED venv also escapes the clone (POSIX only) -")
    if os.name == "nt":
        print("  skipped on Windows: os.symlink needs Developer Mode or elevation, not "
             "assumed here - covered by CI's own POSIX runners instead")
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clone = tmp / "clone"
        venv_dir = clone / ".venv"
        venv_dir.mkdir(parents=True)
        venv.create(venv_dir, with_pip=False, symlinks=True)
        py = venv_dir / "bin" / "python3"
        if not py.is_file():
            alt = sorted((venv_dir / "bin").glob("python3.*"))
            py = alt[0] if alt else py
        check("the venv interpreter exists", py.is_file(), str(py))
        check("...and IS a symlink (the shape this test exists to cover)",
              py.is_symlink(), str(py))

        result, warning = hookwire.hook_python(str(py), clone)
        clone_r = str(clone.resolve()).replace("\\", "/").lower()
        check("the wired interpreter's RESOLVED path is outside the clone",
              not str(Path(result).resolve()).replace("\\", "/").lower()
              .startswith(clone_r + "/"), result)
        check("...and the STRING ITSELF is outside the clone too - not just its target "
              "(the actual bug: the OLD code returned the symlink's own path, unresolved)",
              not result.replace("\\", "/").lower().startswith(clone_r + "/"), result)
        check("no warning - a real base interpreter was found", warning is None, warning)


def test_base_prefix_fallback_shape() -> None:
    """(б), auditor finding 2: the LAST-RESORT fallback has to be platform-shaped. The
    original code used `base_prefix/python3` on every platform - right on Windows
    (`base_prefix\\python.exe` was never the bug), wrong on POSIX, where a base install's own
    prefix directory does not carry a bare `python3` at its own root, only under `bin/`."""
    print("\n- _base_prefix_fallback() - platform-shaped, not one guess for both -")
    posix = hookwire._base_prefix_fallback("/usr", "posix")
    check("POSIX: base_prefix/bin/python3", posix == "/usr/bin/python3", posix)
    nt = hookwire._base_prefix_fallback("C:\\Python310", "nt")
    check("Windows: base_prefix\\python.exe", nt == "C:\\Python310\\python.exe", nt)


def test_hook_python_candidate_order() -> None:
    """(б), auditor finding 1 and the required "candidate order" unit test (d): each candidate
    mocked directly through `hook_python`'s own `base_executable`/`base_prefix` parameters,
    each one deliberately made to fail so the NEXT is what actually gets returned - proving
    fallthrough, not just that some candidate eventually works. Three scenarios:

      1. `sys._base_executable` already escapes the clone - the ordinary (3.11+) case, used
         directly, first candidate that is even tried after the resolved-real-path check.
      2. `sys._base_executable` itself still points INSIDE the clone - this is EXACTLY 3.10's
         POSIX bug for a COPIED venv (finding 1: "_base_executable still names the venv's own
         interpreter") - falls through to `pyvenv.cfg`'s own `home`, which `venv` writes
         regardless of symlinks-vs-copies and is what actually rescues 3.10.
      3. Both of the above fail (no `pyvenv.cfg` at all, e.g. a hand-built venv) - falls
         through to `base_prefix`'s own python binary, the last resort.
    """
    print("\n- hook_python() - candidate order: each one skipped in turn, mocked directly -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        def outside_file(name: str) -> Path:
            p = tmp / "outside" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
            return p

        # 1: base_executable already escapes - used directly
        clone1 = tmp / "clone1"
        copy1 = clone1 / ".venv" / "bin" / "python3"
        copy1.parent.mkdir(parents=True)
        copy1.write_bytes(b"")
        base_ii = outside_file("base_ii")
        result1, warn1 = hookwire.hook_python(str(copy1), clone1, base_executable=str(base_ii))
        check("(ii) sys._base_executable is used directly when it already escapes the clone",
              result1 == str(base_ii) and warn1 is None, (result1, warn1))

        # 2: base_executable fails (simulates the 3.10 POSIX bug - still inside the clone) ->
        # pyvenv.cfg's own home rescues it. The file at `home` has to be named the way
        # `hook_python` actually looks for it (`python3` on POSIX, `python.exe` on Windows) -
        # NOT just "some file that exists outside the clone", or this scenario would pass for
        # the wrong reason (falling through past (iii) to (iv)'s base_prefix fallback instead).
        clone2 = tmp / "clone2"
        venv2 = clone2 / ".venv"
        copy2 = venv2 / "bin" / "python3"
        copy2.parent.mkdir(parents=True)
        copy2.write_bytes(b"")
        base_iii_dir = tmp / "base_iii_home"
        base_iii_dir.mkdir()
        base_iii = base_iii_dir / ("python.exe" if os.name == "nt" else "python3")
        base_iii.write_bytes(b"")
        (venv2 / "pyvenv.cfg").write_text(f"home = {base_iii_dir}\n", encoding="utf-8")
        result2, warn2 = hookwire.hook_python(str(copy2), clone2, base_executable=str(copy2))
        check("(ii) failing (still inside the clone, the 3.10 POSIX shape) falls through to "
              "(iii) pyvenv.cfg's own home",
              result2 == str(base_iii) and warn2 is None, (result2, warn2))

        # 3: base_executable fails AND there is no pyvenv.cfg at all -> base_prefix fallback
        clone3 = tmp / "clone3"
        copy3 = clone3 / ".venv" / "bin" / "python3"
        copy3.parent.mkdir(parents=True)
        copy3.write_bytes(b"")
        base_iv_dir = tmp / "base_iv"
        base_iv = (base_iv_dir / "python.exe") if os.name == "nt" \
            else (base_iv_dir / "bin" / "python3")
        base_iv.parent.mkdir(parents=True)
        base_iv.write_bytes(b"")
        result3, warn3 = hookwire.hook_python(
            str(copy3), clone3, base_executable=str(copy3), base_prefix=str(base_iv_dir))
        check("(ii) and (iii) both failing (no pyvenv.cfg at all) falls through to (iv) "
              "base_prefix's own python binary",
              result3 == str(base_iv) and warn3 is None, (result3, warn3))


def test_hook_python_no_candidate_warns_loudly() -> None:
    """(б), required test (e): if NONE of the four candidates can be found at all - here,
    `base_executable` itself points back inside the clone (the 3.10 shape) AND there is no
    `pyvenv.cfg` AND `base_prefix`'s own python binary does not exist either - `hook_python`
    does not refuse to wire anything: the coordinator's own decision is that a hook which fires
    today and could go silent later is better than none. It returns the ORIGINAL `executable`
    UNCHANGED plus a warning `install.py` prints loudly (`wire_hooks()`'s own `if
    HOOK_PYTHON_WARNING: print(...)`) - checked here by name: it names the clone, the
    interpreter actually wired, and says the failure mode is silent."""
    print("\n- hook_python() - no candidate at all: wire it anyway, but warn loudly -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clone = tmp / "clone"
        copy = clone / ".venv" / "bin" / "python3"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(b"")
        missing_base_dir = tmp / "nowhere"          # deliberately never created
        result, warning = hookwire.hook_python(
            str(copy), clone, base_executable=str(copy), base_prefix=str(missing_base_dir))
        check("wired anyway - the original executable, unchanged",
              result == str(copy), result)
        check("...with a warning naming the clone, the interpreter, and the silent failure "
              "mode - by name, not just 'a warning exists'",
              warning is not None and str(clone) in warning and str(copy) in warning
              and "silent" in warning.lower(), warning)


def test_write_shim_and_remove_shim() -> None:
    print("\n- write_shim() / remove_shim() -")
    with tempfile.TemporaryDirectory() as td:
        settings = Path(td) / "settings.json"
        shim = hookwire.shim_path(settings)
        src1 = b"# nevertwice-hook-shim v1 - x\nprint('a')\n"
        src2 = b"# nevertwice-hook-shim v1 - x\nprint('b')\n"

        verb = hookwire.write_shim(settings, src1, dry_run=False)
        check("first write reports 'written'", verb == "written", verb)
        check("the file landed at shim_path with the right bytes",
              shim.is_file() and shim.read_bytes() == src1)
        verb = hookwire.write_shim(settings, src1, dry_run=False)
        check("re-writing identical bytes reports 'current' and touches nothing",
              verb == "current", verb)
        verb = hookwire.write_shim(settings, src2, dry_run=False)
        check("writing different bytes reports 'updated'",
              verb == "updated" and shim.read_bytes() == src2, verb)
        verb = hookwire.write_shim(settings, b"a third body", dry_run=True)
        check("dry_run reports the verb but writes nothing",
              verb == "updated" and shim.read_bytes() == src2, verb)

        removed = hookwire.remove_shim(settings, dry_run=True)
        check("dry-run remove reports the path but leaves the file",
              removed == [str(shim)] and shim.is_file())
        removed = hookwire.remove_shim(settings, dry_run=False)
        check("real remove deletes the marked file",
              removed == [str(shim)] and not shim.is_file())
        check("the now-empty shim dir is cleaned up too", not shim.parent.exists())

        shim.parent.mkdir(parents=True)
        shim.write_text("not ours - no marker\n", encoding="utf-8")
        removed = hookwire.remove_shim(settings, dry_run=False)
        check("a file at the shim path with no marker is left alone",
              removed == [] and shim.is_file())


# ------------------------------------------------------------- hook_shim.py shape


def test_shim_hot_path_shape() -> None:
    print("\n- hook_shim.py - the hot-path shape -")
    src = SHIM_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_imports = {a.name for node in tree.body if isinstance(node, ast.Import)
                  for a in node.names}
    check("top-level imports are exactly {os, sys}", top_imports == {"os", "sys"},
          str(top_imports))
    check("line 1 starts with hookwire.SHIM_MARKER",
          src.splitlines()[0].startswith(hookwire.SHIM_MARKER))
    guards = [n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
    check("exactly one __main__ guard, and it is the LAST statement",
          len(guards) == 1 and guards[0] is tree.body[-1])
    check("the guard calls sys.exit(main())",
          bool(guards) and "sys.exit(main())" in ast.unparse(guards[0]))


# ------------------------------------------------------------- degraded behaviour (direct)


def test_shim_degraded_direct() -> None:
    print("\n- the shim degrades to exit 0, run directly (no engine present at all) -")
    r = subprocess.run([sys.executable, str(SHIM_SRC)],
                       input=json.dumps({"hook_event_name": "PreToolUse"}),
                       capture_output=True, text=True, timeout=60)
    check("no engine arg: exit 0", r.returncode == 0, str(r.returncode))
    check("stderr names the missing engine", "memory is off" in r.stderr, r.stderr)
    check("PreToolUse: nothing on stdout (only SessionStart's is shown to the agent)",
          r.stdout == "", repr(r.stdout))
    check("no Python traceback on the degraded path", "Traceback" not in r.stderr, r.stderr)

    with tempfile.TemporaryDirectory() as td:
        gone = Path(td) / "gone_engine.py"
        r2 = subprocess.run([sys.executable, str(SHIM_SRC), str(gone)],
                            input=json.dumps({"hook_event_name": "SessionStart"}),
                            capture_output=True, text=True, timeout=60)
        check("missing engine path, SessionStart: exit 0", r2.returncode == 0)
        check("SessionStart carries the notice on stdout too (Claude Code folds it into context)",
              "memory is off" in r2.stdout, r2.stdout)


def test_shim_deliberate_exit_propagates() -> None:
    print("\n- a deliberate SystemExit from the engine is never swallowed -")
    with tempfile.TemporaryDirectory() as td:
        stub = Path(td) / "stub_engine.py"
        stub.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SHIM_SRC), str(stub)], input="{}",
                           capture_output=True, text=True, timeout=60)
        check("SystemExit(2) from the engine propagates through the shim unchanged",
              r.returncode == 2, str(r.returncode))


def test_shim_needs_engine_dir_on_syspath() -> None:
    """The predicted failure, reproduced against the REAL engine: `_engine_config.py`'s bare
    `import config` fallback needs the engine's own directory on `sys.path`, which
    `python memory_hook.py` gets for free and `runpy.run_path` does not add on its own. The
    shim must be exercised from a directory that is NOT the engine's own, because in the real
    deployment it never is - the shim lives beside settings.json, the engine inside a clone."""
    print("\n- the shim adds the engine's own directory to sys.path -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clone = tmp / "clone" / "nevertwice"
        shutil.copytree(PKG, clone, ignore=shutil.ignore_patterns("__pycache__"))
        shimdir = tmp / "shimhome"
        shimdir.mkdir()
        shim_copy = shimdir / "hook_shim.py"
        shim_copy.write_bytes(SHIM_SRC.read_bytes())

        env = _engine_env(tmp)
        r = subprocess.run([sys.executable, str(shim_copy), str(clone / "memory_hook.py")],
                           input=json.dumps({"hook_event_name": "SessionEnd", "session_id": "sp",
                                            "cwd": str(tmp)}),
                           capture_output=True, text=True, env=env, timeout=180)
        check("the real engine runs clean when the shim lives OUTSIDE the engine's directory",
              r.returncode == 0 and "ModuleNotFoundError" not in r.stderr
              and "Traceback" not in r.stderr,
              f"exit {r.returncode}: {r.stderr[-400:]}")


# ------------------------------------------------------------- Req 1: delete / rename


def _make_clone(tmp: Path) -> Path:
    clone = tmp / "clone" / "nevertwice"
    shutil.copytree(PKG, clone, ignore=shutil.ignore_patterns("__pycache__"))
    return clone


def _shim_command(tmp: Path, engine: Path) -> list[str]:
    shimdir = tmp / "shimhome"
    shimdir.mkdir(exist_ok=True)
    shim_copy = shimdir / "hook_shim.py"
    if not shim_copy.exists():
        shim_copy.write_bytes(SHIM_SRC.read_bytes())
    return [sys.executable, str(shim_copy), str(engine)]


def _run_event(argv: list[str], event: dict, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    payload = dict(event, cwd=str(cwd))
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                          env=env, timeout=180)


def _req1(tmp: Path, mutate: Path) -> None:
    """Shared body for the delete and the rename case - `mutate` removes the engine."""
    clone = _make_clone(tmp)
    engine = clone / "memory_hook.py"
    argv = _shim_command(tmp, engine)
    env = _engine_env(tmp)

    live_events = {
        "PreToolUse": {"hook_event_name": "PreToolUse", "session_id": "r1",
                       "tool_name": "Read", "tool_input": {"file_path": "a.py"}},
        "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit", "session_id": "r1",
                             "prompt": "what did we decide"},
    }
    for label, event in live_events.items():
        r = _run_event(argv, event, env, tmp)
        check(f"{label} through the shim, engine present: exit 0", r.returncode == 0,
              f"exit {r.returncode}: {r.stderr[-300:]}")

    mutate(clone)

    dead_events = dict(live_events, SessionStart={"hook_event_name": "SessionStart",
                                                  "session_id": "r1", "source": "startup"})
    for label, event in dead_events.items():
        r = _run_event(argv, event, env, tmp)
        check(f"{label} through the shim, engine gone: exit 0", r.returncode == 0,
              f"exit {r.returncode}: {r.stderr[-300:]}")
        check(f"{label}: no Python traceback", "Traceback" not in r.stderr, r.stderr[-300:])
        check(f"{label}: stderr names the missing engine", "memory is off" in r.stderr, r.stderr)
        if label == "SessionStart":
            check(f"{label}: stdout carries the notice", "memory is off" in r.stdout, r.stdout)
        else:
            check(f"{label}: stdout stays empty", r.stdout == "", repr(r.stdout))


def test_req1_delete() -> None:
    print("\n- Req 1: the clone is DELETED while wired -")
    with tempfile.TemporaryDirectory() as td:
        _req1(Path(td), lambda clone: shutil.rmtree(clone))


def test_req1_rename() -> None:
    print("\n- Req 1: the clone is RENAMED (moved) while wired -")
    with tempfile.TemporaryDirectory() as td:
        _req1(Path(td), lambda clone: os.rename(clone, clone.with_name("nevertwice-moved")))


def test_identity_across_all_five_events() -> None:
    """The same real engine, driven direct and through the shim, across every wired event -
    same exit code, same stdout, same stderr (timestamps and store paths masked), same store
    tree. Not just the happy path: this is what proves the shim is a transparent hand-off and
    not merely "usually works"."""
    print("\n- identical behaviour, direct vs through the shim, all five events -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clone = _make_clone(tmp)
        engine = clone / "memory_hook.py"

        #: The payload's `cwd` has to be the SAME literal string for both arms - each still
        #: gets its own isolated store/root directory, but the engine logs the `cwd` it was
        #: given verbatim, and two different real temp paths there is a difference in the
        #: harness, not in the code under test (found running this the first time).
        shared_cwd = str(tmp / "shared-cwd")

        def drive(via_shim: bool, label: str, event: dict):
            root = tmp / label
            root.mkdir()
            env = _engine_env(root)
            argv = (_shim_command(root, engine) if via_shim else [sys.executable, str(engine)])
            store = Path(env["NEVERTWICE_HOME"])
            r = subprocess.run(argv, input=json.dumps(dict(event, cwd=shared_cwd)),
                               capture_output=True, text=True, env=env, timeout=240)

            def mask(s: str) -> str:
                return TS.sub("<TS>", s.replace(str(store), "<STORE>").replace(str(root), "<ROOT>"))
            tree = (sorted(str(p.relative_to(store)) for p in store.rglob("*") if p.is_file())
                    if store.is_dir() else [])
            return r.returncode, mask(r.stdout), mask(r.stderr), tree

        for label, event in FIVE_EVENTS.items():
            a = drive(False, f"{label}_direct", event)
            b = drive(True, f"{label}_shim", event)
            check(f"{label}: identical exit/stdout/stderr/store-tree, direct vs via the shim",
                  a == b,
                  next((f"{w} differs" for w, x, y in zip(("exit", "stdout", "stderr", "files"),
                                                          a, b) if x != y), ""))


def test_migration_repoints_old_style_entries() -> None:
    """`install.py` finds five pre-existing entries in BOTH forms this `-c` form supersedes -
    the oldest (`"<py>" "<engine>"`, no shim at all) and the 3-token shim form this whole
    track wired before the `-c` correction - and repoints every one of them onto the
    identical `-c` command, in place - never appending a second hook beside a stale one. A
    foreign copy and the user's own hook are untouched, byte for byte."""
    print("\n- install.py repoints BOTH legacy forms onto the -c command -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        home = tmp / "home"
        home.mkdir()
        env = _wall.walled(home)
        settings = Path(env["NEVERTWICE_CLAUDE_SETTINGS"])

        clone = tmp / "clone"
        shutil.copytree(PKG, clone / "nevertwice", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "install.py", clone / "install.py")
        old_engine = clone / "nevertwice" / "memory_hook.py"
        oldest_cmd = f'"{sys.executable}" "{old_engine}"'.replace("\\", "/")
        # A shim wired by an install from BEFORE this correction - a real file, at the exact
        # path a fresh install would also compute, so repointing it is indistinguishable from
        # repointing the shim the fresh install itself will write.
        pre_c_shim = hookwire.shim_path(settings)
        pre_c_shim.parent.mkdir(parents=True)
        pre_c_shim.write_bytes((PKG / "hook_shim.py").read_bytes())
        shim_form_cmd = _legacy_shim_cmd(sys.executable, pre_c_shim, old_engine)

        foreign = {"type": "command",
                  "command": "python C:/Users/me/.claude/scripts/memory_hook.py"}
        theirs = {"type": "command", "command": "python /home/me/my_own_hook.py"}
        oldest_events = ("SessionStart", "UserPromptSubmit", "SessionEnd")
        shim_form_events = ("PreCompact", "PreToolUse")
        hooks = {ev: [{"matcher": "", "hooks": [{"type": "command", "command": oldest_cmd}]}]
                for ev in oldest_events}
        hooks.update({ev: [{"matcher": "", "hooks": [{"type": "command",
                                                       "command": shim_form_cmd}]}]
                     for ev in shim_form_events})
        hooks["Extra"] = [{"hooks": [foreign, theirs]}]
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

        r = subprocess.run([sys.executable, str(clone / "install.py")], env=env,
                           capture_output=True, text=True, timeout=120)
        check("install.py exits 0 against five pre-existing entries in two legacy forms",
              r.returncode == 0, f"exit {r.returncode}: {r.stderr[-300:]}")

        data = json.loads(settings.read_text(encoding="utf-8"))
        five = oldest_events + shim_form_events
        ours_commands = [h["command"] for ev in five
                         for g in data["hooks"][ev] for h in g["hooks"]]
        shim = hookwire.shim_path(settings)
        # (б) CI redness (Windows, all Pythons): install.py's own SETTINGS/SHIM come from
        # `hookwire.settings_path()`, which never resolves the NEVERTWICE_CLAUDE_SETTINGS
        # override - but its HOOK (the engine path) comes from `PKG = Path(__file__).resolve()
        # .parent / "nevertwice"`, which DOES resolve. On a GitHub Windows runner (TEMP set to
        # an 8.3 SHORT spelling, e.g. C:/Users/RUNNER~1/AppData/Local/Temp/...) that asymmetry
        # is real, not a test bug on the product's side: the wired command's shim path stays
        # short (from the unresolved settings override) while its engine path comes out long
        # (`.resolve()` canonicalises 8.3 short segments) - reproduced directly on this machine
        # by pointing TEMP/TMP at a short spelling before running this test, confirmed red with
        # exactly this shape (`s=...VERYLO~1.../hook_shim.py`, `e=...verylong.../memory_hook.py`
        # in the same command string). `old_engine` here was built from the test's own `tmp`
        # object, never resolved, so it silently assumed BOTH halves keep whatever spelling the
        # test constructed them with - `.resolve()` on just this half of the EXPECTED string
        # is what makes the test's expectation match what install.py ACTUALLY computes.
        expected = hookwire.hook_command(sys.executable, shim, old_engine.resolve())
        check("all five entries (both legacy forms) repointed onto the identical -c command, "
              "one per event, none duplicated",
              len(ours_commands) == 5 and all(c == expected for c in ours_commands),
              str(ours_commands))
        check("every repointed command is genuinely the current -c form (starts with -c, not "
              "a bare engine or shim path)", all(hookwire.tokens(c)[1] == "-c"
                                                 for c in ours_commands), str(ours_commands))
        check("the shim was actually installed where the repointed command points",
              shim.is_file() and shim.read_bytes() == (PKG / "hook_shim.py").read_bytes())

        extra_commands = [h["command"] for g in data["hooks"]["Extra"] for h in g["hooks"]]
        check("the foreign copy and the user's own hook survive untouched, byte for byte",
              extra_commands == [foreign["command"], theirs["command"]], str(extra_commands))


def _make_shadow_project(tmp: Path) -> tuple[Path, Path]:
    """A `project` directory (the hook's cwd) containing a `json.py` and a `pathlib.py` that
    each append a marker line to `marker.txt` if ever imported - and the marker file's path.
    Neither is ever legitimately importable from a project directory; if either marker
    appears, `sys.path[0]` still held the cwd when something tried to `import json` or
    `import pathlib`."""
    project = tmp / "project"
    project.mkdir()
    marker = tmp / "marker.txt"
    for name in ("json", "pathlib"):
        (project / f"{name}.py").write_text(
            f"with open({str(marker)!r}, 'a', encoding='utf-8') as _f:\n"
            f"    _f.write('{name}.py SHADOWED' + chr(10))\n",
            encoding="utf-8")
    return project, marker


def test_c_payload_never_shadows_a_stdlib_import_from_cwd() -> None:
    """`python -c` puts `''` (the cwd) at `sys.path[0]`, and Claude Code runs a hook command
    with cwd = the user's PROJECT. A project containing its own `json.py`/`pathlib.py` must
    never have either imported instead of the real stdlib module - checked through every shell
    available on this machine, with the engine present (its own `import json`, `import
    pathlib`) and absent (the shim's degraded branch's `import json`)."""
    print("\n- the -c payload never lets the project's cwd shadow a stdlib import -")
    # F2: on Windows, _run_via_powershell tests a TRANSFORMED command ("& ...; exit
    # $LASTEXITCODE"), not necessarily what Claude Code itself passes to PowerShell - see its
    # own docstring. (б) Cause 1: on POSIX this now also covers `sh` (Claude Code's own main
    # path there, per its docs) - see `_available_shells`'s docstring.
    shells = _available_shells()
    check("at least one shell is available to test through", bool(shells), "none found")

    for shell_name, runner in shells:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            project, marker = _make_shadow_project(tmp)
            shimdir = tmp / "shimhome"
            shimdir.mkdir()
            shim = shimdir / "hook_shim.py"
            shim.write_bytes(SHIM_SRC.read_bytes())
            engine = tmp / "clone" / "nevertwice" / "memory_hook.py"
            engine.parent.mkdir(parents=True)
            engine.write_text("import json, pathlib, sys\nsys.exit(0)\n", encoding="utf-8")

            cmd = hookwire.hook_command(sys.executable, shim, engine)
            env = _engine_env(tmp)

            r = runner(cmd, cwd=project, env=env)
            check(f"{shell_name}: engine present - command exits 0",
                  r.returncode == 0, f"exit {r.returncode}: {r.stderr[-300:]}")
            check(f"{shell_name}: engine present - the marker never appears",
                  not marker.exists(),
                  marker.read_text(encoding="utf-8") if marker.exists() else "")

            engine.unlink()          # now the degraded (engine-gone) path, same cwd/shell
            r2 = runner(cmd, cwd=project, env=env)
            check(f"{shell_name}: engine absent - command still exits 0",
                  r2.returncode == 0, f"exit {r2.returncode}: {r2.stderr[-300:]}")
            check(f"{shell_name}: engine absent - the marker never appears",
                  not marker.exists(),
                  marker.read_text(encoding="utf-8") if marker.exists() else "")


def test_c_payload_exit_codes_both_shells() -> None:
    """Five cases, through every shell available on this machine: the engine's own exit code
    propagates unchanged for 0, a deliberate 2 (Claude Code's BLOCK code) and 7 (an arbitrary
    third value, so a mutation that special-cases 0/2 cannot hide behind it); the shim deleted
    degrades to 0; the whole clone deleted (engine gone, shim untouched) degrades to 0 too."""
    print("\n- exit-code propagation through the -c form, every shell available -")
    shells = _available_shells(
        "PowerShell (via the $LASTEXITCODE relay - see _run_via_powershell)")
    check("at least one shell is available to test through", bool(shells), "none found")

    for shell_name, runner in shells:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cwd = tmp / "project"
            cwd.mkdir()
            shimdir = tmp / "shimhome"
            shimdir.mkdir()
            shim = shimdir / "hook_shim.py"
            shim.write_bytes(SHIM_SRC.read_bytes())
            engine = tmp / "clone" / "nevertwice" / "memory_hook.py"
            engine.parent.mkdir(parents=True)
            env = _engine_env(tmp)
            cmd = hookwire.hook_command(sys.executable, shim, engine)

            for code in (0, 2, 7):
                engine.write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
                r = runner(cmd, cwd=cwd, env=env)
                check(f"{shell_name}: engine exit {code} -> {code}", r.returncode == code,
                      f"exit {r.returncode}: {r.stderr[-300:]}")

            shim.unlink()
            r = runner(cmd, cwd=cwd, env=env)
            check(f"{shell_name}: shim deleted -> exit 0", r.returncode == 0,
                  f"exit {r.returncode}: {r.stderr[-300:]}")
            shim.write_bytes(SHIM_SRC.read_bytes())        # restore for the next case

            shutil.rmtree(engine.parent.parent)             # the whole clone gone
            r = runner(cmd, cwd=cwd, env=env)
            check(f"{shell_name}: clone deleted (engine gone) -> exit 0", r.returncode == 0,
                  f"exit {r.returncode}: {r.stderr[-300:]}")


def test_venv_in_clone_is_never_the_wired_interpreter() -> None:
    """O3b: a venv created INSIDE the checkout being installed must not become the wired
    interpreter - it would vanish along with the clone, exactly like the engine does, defeating
    the shim's whole purpose. `hookwire.hook_python` rebases it; this drives that end to end
    through a real `install.py` run and a real venv."""
    print("\n- a venv created inside the clone is never the wired interpreter (O3b) -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        home = tmp / "home"
        home.mkdir()
        env = _wall.walled(home)
        settings = Path(env["NEVERTWICE_CLAUDE_SETTINGS"])

        clone = tmp / "clone"
        shutil.copytree(PKG, clone / "nevertwice", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "install.py", clone / "install.py")

        venv_dir = clone / ".venv"
        venv.create(venv_dir, with_pip=False)
        venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
        check("the venv interpreter exists", venv_python.is_file(), str(venv_python))

        r = subprocess.run([str(venv_python), str(clone / "install.py")], env=env,
                           capture_output=True, text=True, timeout=180)
        check("install.py exits 0 run under a venv interpreter living inside the clone",
              r.returncode == 0, f"exit {r.returncode}: {r.stderr[-400:]}")

        data = json.loads(settings.read_text(encoding="utf-8"))
        wired_cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        wired_interp = hookwire.tokens(wired_cmd)[0]
        clone_norm = str(clone.resolve()).replace("\\", "/").lower()
        check("the wired interpreter is NOT the venv's own, and not under the clone at all",
              not wired_interp.lower().startswith(clone_norm + "/"), wired_cmd)

        shutil.rmtree(clone)                      # the clone AND its venv are both gone now
        argv = shlex.split(wired_cmd)
        r2 = subprocess.run(argv, input=json.dumps({"hook_event_name": "SessionStart"}),
                           capture_output=True, text=True, timeout=60)
        check("after the clone (and its venv) is deleted, the wired command still exits 0",
              r2.returncode == 0, f"exit {r2.returncode}: {r2.stderr[-300:]}")


def test_every_subprocess_call_touching_install_or_hosts_is_walled() -> None:
    """Req 4, statically: every `subprocess.run`/`Popen`/`check_output`/`check_call` anywhere
    under tests/ whose arguments mention install.py, hook_shim.py or hosts has to pass an env
    built through `_wall.walled(...)` - never `dict(os.environ, ...)` alone. One call built the
    wrong way is how a test wrote five hooks into the owner's real settings.json."""
    print("\n- every install.py/hosts/hook_shim subprocess call in tests/ is walled -")
    target_names = ("install.py", "hook_shim.py", "nevertwice.hosts", "nevertwice/hosts")
    call_methods = {"run", "Popen", "check_output", "check_call"}
    offenders = []
    checked = 0
    for path in sorted((ROOT / "tests").glob("_test_*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        funcs = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in funcs:
            fn_src = ast.get_source_segment(src, fn) or ""
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in call_methods):
                    continue
                call_src = ast.get_source_segment(src, node) or ""
                if not any(name in call_src for name in target_names):
                    continue
                checked += 1
                has_env_kw = any(kw.arg == "env" for kw in node.keywords)
                walled_upstream = "walled(" in fn_src or "walled_in_process(" in fn_src
                if not (has_env_kw and walled_upstream):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    check("at least one call was actually checked (a rule over nothing proves nothing)",
          checked >= 5, str(checked))
    check("every install.py/hosts/hook_shim subprocess call passes an env built by the wall",
          not offenders, "; ".join(offenders[:8]))


def test_latency_direct_vs_via_the_shim() -> None:
    """Printed, never asserted - PreToolUse latency is a performance property, not a
    correctness one, and the whole track's other suites already gate the engine's own hot
    path. 25 alternating runs of the SAME trivial stub "engine", direct (`python stub.py`) vs
    through the shim (`python hook_shim.py stub.py`) - isolating exactly what the shim's own
    indirection (argv handling, the sys.path insert, `runpy.run_path`) costs on top of a bare
    `python <script>`, with no real store I/O on either side to drown it out."""
    print("\n- latency: direct vs through the shim (informational only) -")
    with tempfile.TemporaryDirectory() as td:
        stub = Path(td) / "stub_engine.py"
        stub.write_text("import sys\n", encoding="utf-8")
        event = json.dumps({"hook_event_name": "PreToolUse", "session_id": "lat"})

        def once(argv):
            t0 = time.perf_counter()
            subprocess.run(argv, input=event, capture_output=True, text=True, timeout=60)
            return time.perf_counter() - t0

        direct_argv = [sys.executable, str(stub)]
        shim_argv = [sys.executable, str(SHIM_SRC), str(stub)]
        direct_times, shim_times = [], []
        for i in range(25):
            if i % 2 == 0:
                direct_times.append(once(direct_argv))
                shim_times.append(once(shim_argv))
            else:
                shim_times.append(once(shim_argv))
                direct_times.append(once(direct_argv))
        direct_times.sort()
        shim_times.sort()
        n = len(direct_times)
        print(f"       direct (python stub.py)          : min {direct_times[0] * 1000:.2f} ms, "
              f"median {direct_times[n // 2] * 1000:.2f} ms")
        print(f"       via shim (python hook_shim.py ...) : min {shim_times[0] * 1000:.2f} ms, "
              f"median {shim_times[n // 2] * 1000:.2f} ms")
        check("the latency comparison ran to completion", len(direct_times) == len(shim_times) == 25)

        # The CURRENT -c form vs the PRE-correction 3-token shim form - what the cwd-safety
        # fix and the "shim itself can go missing" fix cost on top of the shape this replaces.
        # Both invoked directly (list-form subprocess, no shell layer) so the number is the
        # payload's own overhead, not a shell's. The payload string is pulled straight out of
        # `hook_command` itself, never rebuilt by hand here.
        c_payload = hookwire.tokens(hookwire.hook_command(sys.executable, stub, stub))[2]
        c_argv = [sys.executable, "-c", c_payload]
        token_argv = [sys.executable, str(stub), str(stub)]
        c_times, token_times = [], []
        for i in range(25):
            if i % 2 == 0:
                c_times.append(once(c_argv))
                token_times.append(once(token_argv))
            else:
                token_times.append(once(token_argv))
                c_times.append(once(c_argv))
        c_times.sort()
        token_times.sort()
        print(f"       -c form (python -c \"...\")          : min {c_times[0] * 1000:.2f} ms, "
              f"median {c_times[n // 2] * 1000:.2f} ms")
        print(f"       3-token form (python <shim> <eng>) : min {token_times[0] * 1000:.2f} ms, "
              f"median {token_times[n // 2] * 1000:.2f} ms")
        check("the -c-vs-3-token latency comparison ran to completion",
              len(c_times) == len(token_times) == 25)


def test_non_blocking_even_with_a_missing_interpreter() -> None:
    """The shell's own "command not found" for a missing interpreter must never coincide with
    exit 2 - which is Claude Code's block code - so this asserts inequality, never a specific
    code (the shell's own choice of code differs by platform)."""
    print("\n- a missing INTERPRETER is never mistaken for exit 2 -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shim = tmp / "hook_shim.py"
        shim.write_bytes(SHIM_SRC.read_bytes())
        engine = tmp / "engine.py"
        engine.write_text("x = 1\n", encoding="utf-8")
        cmd = f'"{tmp / "gone" / "python"}" "{shim}" "{engine}"'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        check("a missing interpreter never returns exit code 2", r.returncode != 2,
              f"exit {r.returncode}")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code, AND the real
    ~/.claude/settings.json this process started with must be exactly what it ends with."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"
    assert _wall.settings_unchanged(), (
        "the real ~/.claude/settings.json changed during this suite - see _wall.py")


def main() -> int:
    for fn in (test_walled_covers_or_allowlists_every_env_name_in_the_package,
               test_helper_fixpoint_bounded_and_module_qualified,
               test_every_host_adapter_and_watch_base_resolves_inside_the_wall,
               test_tokens_and_hook_command,
               test_is_ours_and_is_foreign_copy,
               test_dead_reason,
               test_hook_python,
               test_hook_python_symlinked_venv_escapes_the_clone,
               test_base_prefix_fallback_shape,
               test_hook_python_candidate_order,
               test_hook_python_no_candidate_warns_loudly,
               test_write_shim_and_remove_shim,
               test_shim_hot_path_shape,
               test_shim_degraded_direct,
               test_shim_deliberate_exit_propagates,
               test_shim_needs_engine_dir_on_syspath,
               test_req1_delete,
               test_req1_rename,
               test_identity_across_all_five_events,
               test_migration_repoints_old_style_entries,
               test_c_payload_never_shadows_a_stdlib_import_from_cwd,
               test_c_payload_exit_codes_both_shells,
               test_venv_in_clone_is_never_the_wired_interpreter,
               test_every_subprocess_call_touching_install_or_hosts_is_walled,
               test_latency_direct_vs_via_the_shim,
               test_non_blocking_even_with_a_missing_interpreter):
        fn()
    test_zz_every_check_passed()
    print(f"\nhook shim: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
