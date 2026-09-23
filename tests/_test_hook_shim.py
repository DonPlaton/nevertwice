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


def _config_path_env_names() -> set[str]:
    """Every environment variable `nevertwice/config.py` reads whose value flows into a
    filesystem PATH (`Path(...)` or `_expand(...)`) - discovered by reading config.py's own
    AST, not kept as a list by hand here, so a name added there later reddens THIS test
    instead of leaking through `_wall.walled()` unchanged (the exact shape of the
    NEVERTWICE_PROJECTS_ROOT gap this pair of functions exists to close: config.py reads it,
    `hosts.py` separately reads a DIFFERENTLY-NAMED NEVERTWICE_CLAUDE_PROJECTS, and `walled()`
    pinned only the second - a probe of NEVERTWICE_PROJECTS_ROOT="C:/pretend/real/.claude/
    projects" passed straight through a walled() env with nothing catching it).

    Two passes over the whole module (`ast.walk`, which does not care about nesting depth or
    scope): first, every simple `NAME = <expr>` assignment, keyed by name (config.py is small
    and flat enough that one merged namespace across module and function scopes is a safe
    simplification for a discovery tool, not a general-purpose one); second, every call to
    `Path(...)` or `_expand(...)` - the two ways this module turns a string into a path - with
    the env names found either directly nested inside that call, or one simple assignment
    away (`custom = os.environ.get("NEVERTWICE_ENV_FILE")` ... `Path(custom)`, two statements
    apart, is exactly the shape a direct-nesting-only walk would miss).
    """
    src = (PKG / "config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    def literal_env_names(node) -> set[str]:
        names: set[str] = set()
        for n in ast.walk(node):
            if not isinstance(n, ast.Call):
                continue
            target = n.func
            is_os_environ_get = (isinstance(target, ast.Attribute) and target.attr == "get"
                                 and isinstance(target.value, ast.Attribute)
                                 and target.value.attr == "environ")
            is_env_helper = isinstance(target, ast.Name) and target.id == "env"
            if is_os_environ_get and n.args and isinstance(n.args[0], ast.Constant):
                names.add(n.args[0].value)
            elif is_env_helper and n.args and isinstance(n.args[0], ast.Constant):
                suffix = n.args[0].value
                names.add(f"NEVERTWICE_{suffix}")
                names.add(f"CLAUDE_MEMORY_{suffix}")
        return names

    assigns: dict = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            assigns[node.targets[0].id] = node.value

    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("Path", "_expand")):
            continue
        found |= literal_env_names(node)
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id in assigns:
                found |= literal_env_names(assigns[n.id])
    return found


def test_walled_covers_every_path_resolving_name_in_config() -> None:
    print("\n- walled() covers every path-resolving name config.py reads -")
    discovered = _config_path_env_names()
    check("the scan actually found names (a scan over nothing proves nothing)",
          len(discovered) >= 4, str(sorted(discovered)))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _wall.walled(tmp)
        tmp_r = str(tmp.resolve())
        missing = [n for n in discovered if not env.get(n)]
        escaping = [n for n in discovered if env.get(n) and not (
            str(Path(env[n]).resolve()) == tmp_r
            or str(Path(env[n]).resolve()).startswith(tmp_r + os.sep))]
        check(f"every name config.py reads is set by walled(): {sorted(discovered)}",
              not missing, f"missing: {missing}")
        check("and every one of them resolves inside the wall", not escaping,
              f"escaping: {escaping}")



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
    check("hook_command quotes and forward-slashes every token",
          cmd == '"C:/Py/python.exe" "C:/home/nevertwice/hook_shim.py" '
                 '"D:/clone/nevertwice/memory_hook.py"', cmd)


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

        live = {"command": hookwire.hook_command(py, shim, engine)}
        check("a fully-live shim entry is not dead", hookwire.dead_reason(live) is None)

        missing_engine = {"command": hookwire.hook_command(py, shim, tmp / "gone.py")}
        reason = hookwire.dead_reason(missing_engine)
        check("a shim with a missing ENGINE is dead but does not block",
              reason is not None and reason[1] is False, str(reason))
        check("...and names 'engine missing'", reason is not None and "engine missing" in reason[0])

        missing_shim = {"command": hookwire.hook_command(
            py, tmp / "goneshimdir" / "nevertwice" / "hook_shim.py", engine)}
        reason = hookwire.dead_reason(missing_shim)
        check("a missing SCRIPT (the shim itself) is dead and DOES block",
              reason is not None and reason[1] is True, str(reason))

        old_missing = {"command": f'"{py}" "{tmp / "gone3" / "nevertwice" / "memory_hook.py"}"'}
        reason = hookwire.dead_reason(old_missing)
        check("an old-style entry whose memory_hook.py is gone is dead and DOES block",
              reason is not None and reason[1] is True, str(reason))

        missing_interp = {"command": hookwire.hook_command(tmp / "gone_py.exe", shim, engine)}
        reason = hookwire.dead_reason(missing_interp)
        check("a missing interpreter (a path, has a slash) is dead but does not block",
              reason is not None and reason[1] is False, str(reason))

        bare_interp = {"command": f'"python" "{shim}" "{engine}"'}
        check("a bare 'python' interpreter (resolved on PATH) is never judged missing",
              hookwire.dead_reason(bare_interp) is None)

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
        check("an interpreter outside the clone is returned unchanged",
              hookwire.hook_python(str(outside), clone) == str(outside))
        rebased = hookwire.hook_python(str(inside), clone)
        check("an interpreter INSIDE the clone is rebased to something else",
              rebased != str(inside), rebased)
        check("...and the rebased interpreter is not itself under the clone",
              not str(Path(rebased).resolve()).startswith(str(clone.resolve()) + os.sep), rebased)


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
    """`install.py` finds five pre-existing OLD-STYLE (pre-shim) entries and repoints every one
    of them onto the identical shim command, in place - never appending a second hook beside a
    stale one. A foreign copy and the user's own hook are untouched, byte for byte."""
    print("\n- install.py repoints five old-style entries onto the shim -")
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
        old_cmd = f'"{sys.executable}" "{old_engine}"'.replace("\\", "/")

        foreign = {"type": "command",
                  "command": "python C:/Users/me/.claude/scripts/memory_hook.py"}
        theirs = {"type": "command", "command": "python /home/me/my_own_hook.py"}
        five = ("SessionStart", "UserPromptSubmit", "SessionEnd", "PreCompact", "PreToolUse")
        hooks = {ev: [{"matcher": "", "hooks": [{"type": "command", "command": old_cmd}]}]
                for ev in five}
        hooks["Extra"] = [{"hooks": [foreign, theirs]}]
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

        r = subprocess.run([sys.executable, str(clone / "install.py")], env=env,
                           capture_output=True, text=True, timeout=120)
        check("install.py exits 0 against five pre-existing old-style entries",
              r.returncode == 0, f"exit {r.returncode}: {r.stderr[-300:]}")

        data = json.loads(settings.read_text(encoding="utf-8"))
        ours_commands = [h["command"] for ev in five
                         for g in data["hooks"][ev] for h in g["hooks"]]
        shim = hookwire.shim_path(settings)
        expected = hookwire.hook_command(sys.executable, shim, old_engine)
        check("all five entries repointed onto the identical shim command, none duplicated",
              len(ours_commands) == 5 and all(c == expected for c in ours_commands),
              str(ours_commands))
        check("the shim was actually installed where the repointed command points",
              shim.is_file() and shim.read_bytes() == (PKG / "hook_shim.py").read_bytes())

        extra_commands = [h["command"] for g in data["hooks"]["Extra"] for h in g["hooks"]]
        check("the foreign copy and the user's own hook survive untouched, byte for byte",
              extra_commands == [foreign["command"], theirs["command"]], str(extra_commands))


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
    for fn in (test_walled_covers_every_path_resolving_name_in_config,
               test_tokens_and_hook_command,
               test_is_ours_and_is_foreign_copy,
               test_dead_reason,
               test_hook_python,
               test_write_shim_and_remove_shim,
               test_shim_hot_path_shape,
               test_shim_degraded_direct,
               test_shim_deliberate_exit_propagates,
               test_shim_needs_engine_dir_on_syspath,
               test_req1_delete,
               test_req1_rename,
               test_identity_across_all_five_events,
               test_migration_repoints_old_style_entries,
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
