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
import shlex
import shutil
import subprocess
import sys
import tempfile
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

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _engine_env(tmp: Path) -> dict:
    """A walled subprocess env, ALSO isolated for the store and the transcript sweep root -
    every call in this file that might hand off to the real engine needs both walls, or a run
    against a live `memory_hook.py` reads the owner's real ~/.claude/projects (found the hard
    way while first smoke-testing this shim: no NEVERTWICE_PROJECTS_ROOT override swept real,
    unrelated project transcripts through a real extractor)."""
    env = _wall.walled(tmp)
    store = tmp / "store"
    proj = tmp / "proj"
    env["NEVERTWICE_HOME"] = str(store)
    env["NEVERTWICE_VAULT"] = str(store)
    env["NEVERTWICE_CLOUD"] = "none"
    env["NEVERTWICE_PROJECTS_ROOT"] = str(proj)
    return env


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
    for fn in (test_tokens_and_hook_command,
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
               test_non_blocking_even_with_a_missing_interpreter):
        fn()
    test_zz_every_check_passed()
    print(f"\nhook shim: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
