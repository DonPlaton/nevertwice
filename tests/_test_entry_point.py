#!/usr/bin/env python3
"""The entry point loads cached bytecode, and nothing about the module changes because of it.

The agent's five hooks run `python memory_hook.py` as a script. CPython never serves `__main__`
from `__pycache__`, so the whole engine used to be recompiled on every tool call: measured
2026-09-18, 48.6 ms of a 98.6 ms PreToolUse, beside 21.3 ms of interpreter start and 5.6 ms of
the guard's own work. `memory_hook.py` is now a loader that executes `_engine.py`'s cached code
object in its own `globals()`.

Two things have to be true for that to be free, and this file pins both.

**One namespace.** Some eighty names are rebound on this module by `tests/` (`m.VAULT`,
`m.generate_json`, `m.ollama_alive`, ...), `_rebase_vault` moves seven more with `global`, and
both hand-rolled `cache_clear` hooks close over dictionaries in it. `exec` into `globals()` keeps
all of that in `memory_hook.__dict__`. A `from _engine import *` facade would copy the values
into a second namespace and strand every rebind silently - the 2026-08 live-cache incident class,
which is why the check below is name-for-name and not a spot check.

**Same behaviour.** Every hook event, driven as a subprocess exactly as the agent drives it, must
produce the same exit code, stdout, stderr and store tree as the engine run directly, once the
sandbox path and the clock are masked out.

    python tests/_test_entry_point.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PKG))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

P = F = 0
TS = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


# ── the shape of the entry point ────────────────────────────────────────────────────
print("# the entry point is a loader, not the engine")

entry, engine = PKG / "memory_hook.py", PKG / "_engine.py"
check("the engine body lives in _engine.py", engine.exists())
check("the entry point is small", entry.stat().st_size < 4096,
      f"{entry.stat().st_size} bytes")
check("the engine is the big one", engine.stat().st_size > 100_000)
check("the entry point does not import the engine as a module",
      "import _engine" not in entry.read_text(encoding="utf-8"))


# ── one namespace: every patch point still lives on this module ─────────────────────
print("# the namespace is the engine's own")

# Not a spot check: the engine's public and private surface is compared name for name against
# what the module actually carries, because a facade would pass any short list of samples.
import ast  # noqa: E402

tree = ast.parse(engine.read_text(encoding="utf-8"))
declared = set()
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        declared.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for x in ast.walk(t):
                if isinstance(x, ast.Name):
                    declared.add(x.id)
missing = sorted(n for n in declared if not hasattr(m, n))
check(f"all {len(declared)} names the engine defines are on memory_hook", not missing,
      f"missing: {missing[:8]}")

leaked = [n for n in ("_ENGINE", "_spec", "_code") if hasattr(m, n)]
check("the loader leaves none of its own names behind", not leaked, f"leaked: {leaked}")

check("__file__ still points at the entry point",
      Path(m.__file__).name == "memory_hook.py")
check("rebinding a name on the module is seen by the engine's own code",
      (lambda: (setattr(m, "EARLIER_MAX_CHARS", 7),
                m._earlier_text.__globals__ is vars(m))[1])())


# ── same behaviour, event for event ─────────────────────────────────────────────────
print("# every hook event behaves identically through either file")


def drive(script: Path, evt: dict):
    """Run one hook event as the agent runs it and return its masked observable result."""
    store = tempfile.mkdtemp(prefix="entry_")
    proj = tempfile.mkdtemp(prefix="entryp_")
    env = {k: v for k, v in os.environ.items()
           if not any(s in k for s in ("CEREBRAS", "GROQ", "DEEPSEEK", "GEMINI", "OPENAI",
                                       "VOYAGE", "COHERE"))}
    env.update({"NEVERTWICE_HOME": store, "NEVERTWICE_VAULT": store,
                "NEVERTWICE_CLOUD": "none", "NEVERTWICE_PROJECTS_ROOT": proj})
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(dict(evt, cwd=store)),
                       capture_output=True, text=True, env=env, timeout=240)

    def mask(s: str) -> str:
        return TS.sub("<TS>", s.replace(store, "<STORE>").replace(proj, "<PROJ>"))

    tree_ = sorted(str(p.relative_to(store)) for p in Path(store).rglob("*") if p.is_file())
    return r.returncode, mask(r.stdout), mask(r.stderr), tree_


EVENTS = {
    "PreToolUse": {"hook_event_name": "PreToolUse", "session_id": "e1", "tool_name": "Edit",
                   "tool_input": {"file_path": "a.py", "new_string": "y = eval(s)"}},
    "SessionStart": {"hook_event_name": "SessionStart", "session_id": "e2", "source": "startup"},
    "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit", "session_id": "e2",
                         "prompt": "why does the handler crash with failure mode 3"},
    "SessionEnd": {"hook_event_name": "SessionEnd", "session_id": "e2", "reason": "clear"},
    "PreCompact": {"hook_event_name": "PreCompact", "session_id": "e2", "trigger": "manual"},
}
for label, evt in EVENTS.items():
    a, b = drive(engine, evt), drive(entry, evt)
    same = a == b
    check(f"{label} identical through the loader", same,
          next((f"{w} differs" for w, x, y in zip(("exit", "stdout", "stderr", "files"), a, b)
                if x != y), ""))


# ── a half-copied install degrades loudly, and never fails the agent's tool call ────
print("# a missing engine is reported, not crashed")

half = Path(tempfile.mkdtemp(prefix="halfinstall_"))
(half / "memory_hook.py").write_bytes(entry.read_bytes())
r = subprocess.run([sys.executable, str(half / "memory_hook.py")],
                   input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "h",
                                     "cwd": str(half), "tool_name": "Edit", "tool_input": {}}),
                   capture_output=True, text=True, timeout=60)
check("exit code stays 0 so the agent's tool call is not blocked", r.returncode == 0,
      f"exit {r.returncode}")
check("the reason is printed", "_engine.py" in r.stderr and "missing" in r.stderr.lower())

# ── a store that cannot be created is the same kind of failure ────────────────────
# `main()` calls `VAULT.mkdir(parents=True, exist_ok=True)` on EVERY event, and so does
# `acquire_lock` one layer down. An unreachable store - an unplugged drive, a synced folder
# mid-repair, a permission change - raises OSError out of the hook, and on PreToolUse that is an
# error before every Edit, Write and Bash the agent tries. Memory being unavailable must cost
# memory, never the agent's tool call.
#
# The first version of this check ran PreToolUse only and passed while SessionEnd and PreCompact
# still exited 1 with a traceback: guarding `main()`'s own mkdir left the one inside
# `acquire_lock` - and the comment on the guard named `acquire_lock` among the callers that
# "already create what they need", which is exactly the line that throws. PreToolUse never
# reaches it, because on an unreachable store no guard fires and nothing is recorded. Every
# event the hook is wired to is exercised here, for that reason.
print("# a store that cannot be created is reported, not crashed - on every wired event")

blocked = Path(tempfile.mkdtemp(prefix="blockedstore_"))
(blocked / "afile").write_text("not a directory", encoding="utf-8")
env = dict(os.environ)
env.update({"NEVERTWICE_HOME": str(blocked / "afile" / "store"),
            "NEVERTWICE_VAULT": str(blocked / "afile" / "store"),
            "NEVERTWICE_CLOUD": "none"})
transcript = blocked / "t.jsonl"
transcript.write_text(
    json.dumps({"type": "user", "message": {"role": "user", "content": "remember this"}})
    + "\n", encoding="utf-8")
for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd", "PreCompact"):
    payload = {"hook_event_name": event, "session_id": "b", "cwd": str(blocked),
               "transcript_path": str(transcript)}
    if event == "PreToolUse":
        payload |= {"tool_name": "Edit", "tool_input": {"file_path": "a.py",
                                                        "new_string": "x = 1"}}
    if event == "UserPromptSubmit":
        payload["prompt"] = "what did we decide"
    r = subprocess.run([sys.executable, str(entry)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=180)
    check(event + ": exit code stays 0 so the agent is not blocked", r.returncode == 0,
          f"exit {r.returncode}: {r.stderr[-400:]}")
    check(event + ": and nothing raises out of the hook", "Traceback" not in r.stderr,
          r.stderr[-400:])

print()
print(f"entry point: {P} passed, {F} failed")
sys.exit(1 if F else 0)
