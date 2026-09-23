# nevertwice-hook-shim v1 - written by install.py; `python install.py --uninstall` removes it.
"""Thin, dependency-free shim between a Claude Code hook entry and the Nevertwice engine.

`install.py` writes this exact file to `~/.claude/nevertwice/hook_shim.py`, beside
settings.json rather than inside any one clone, and wires every hook command to run it with
the engine's path (`memory_hook.py` inside the clone `install.py` ran from) as its one
argument. The point of the indirection: `python <path-to-memory_hook.py>` exits 2 when that
file is not there - the clone was deleted, moved, or `pip uninstall`'d while still wired - and
Claude Code treats exit 2 from PreToolUse and UserPromptSubmit as a BLOCK: every edit, command
and prompt refused, with no way left to ask the agent to repair its own settings.json. This
file never does that.

When the engine is where the wired command says, this file hands the process to it exactly as
`python memory_hook.py` would: same stdin, same stdout, same exit code, `SystemExit` included
- the engine's own guard is allowed to deny a tool call, and this file must never be the thing
that quietly turns that denial into something else. When the engine is NOT there, this file
says so on stderr (and on stdout too for `SessionStart`, whose stdout Claude Code folds into
context, so the agent actually sees it) and exits 0: memory turns off, the tool call does not.

Deliberately dependency-free at module scope - only `os` and `sys` are imported here, because
this file has to import instantly and without risk on every tool call, before it knows whether
there is an engine to hand off to at all. `json` and `runpy` are imported inside `main()`,
only on the paths that actually need them. `tests/_test_hook_shim.py` pins the module-level
import set with an AST check, and drives every wired event through this file and directly
through the engine to prove the two are indistinguishable.
"""
import os
import sys


def main() -> int:
    """Run the engine named in `sys.argv[1]`, or degrade to a message that never blocks."""
    engine = sys.argv[1] if len(sys.argv) > 1 else ""
    if engine and os.path.isfile(engine):
        # The engine's `_engine_config.py` falls back to a bare `import config` when it is not
        # running as a package, and that fallback needs the engine's own directory on
        # sys.path - `python memory_hook.py` gets it for free (the interpreter puts the
        # script's directory at sys.path[0]), and runpy does not do the same for a path handed
        # to it explicitly, so this file adds it by hand.
        engine_dir = os.path.dirname(os.path.abspath(engine))
        if engine_dir not in sys.path:
            sys.path.insert(0, engine_dir)
        import runpy
        # SystemExit from inside the engine propagates through this call untouched - it is
        # never caught here, because the one thing worse than a hook that blocks by accident
        # is a shim that silently un-blocks a denial the engine meant.
        runpy.run_path(engine, run_name="__main__")
        return 0

    import json
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    event_name = payload.get("hook_event_name") if isinstance(payload, dict) else None
    message = ("[nevertwice] memory is off: the engine is not at "
              + (engine or "(no engine path given)")
              + ". Re-run install.py from the clone's new location, or run "
                "install.py --uninstall; this hook exits 0 and never blocks the agent.")
    sys.stderr.write(message + "\n")
    if event_name == "SessionStart":
        # SessionStart's stdout is folded into the agent's context by Claude Code; every other
        # event's stdout is not shown, so printing there too would be silent noise at best.
        sys.stdout.write(message + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
