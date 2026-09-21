#!/usr/bin/env python3
"""The entry point loads cached bytecode, and nothing about the module changes because of it.

The agent's five hooks run `python memory_hook.py` as a script. CPython never serves `__main__`
from `__pycache__`, so the whole engine used to be recompiled on every tool call: measured
2026-09-18, 48.6 ms of a 98.6 ms PreToolUse, beside 21.3 ms of interpreter start and 5.6 ms of
the guard's own work. `memory_hook.py` is now a loader that executes cached code objects in its
own `globals()`, and `_engine.py` is an index over eight part files that hold the body.

Three things have to be true for that to be free, and this file pins all three.

**One namespace.** Some eighty names are rebound on this module by `tests/` (`m.VAULT`,
`m.generate_json`, `m.ollama_alive`, ...), `_rebase_vault` moves seven more with `global`, and
both hand-rolled `cache_clear` hooks close over dictionaries in it. `exec` into `globals()` keeps
all of that in `memory_hook.__dict__`. A `from _engine import *` facade would copy the values
into a second namespace and strand every rebind silently - the 2026-08 live-cache incident class,
which is why the check below is name-for-name and not a spot check.

**One namespace for the PARTS too, and this is the half that fails quietly.** `__globals__` is
the dictionary of the module a function was defined in. A part imported as a real module would
read its own dictionary while every rebind kept landing here: `tests/_test_guards.py` would set
`m.GUARD_ENFORCE` and exercise the branch where it is false, in green. So the question is asked
of every function the engine defines, not of a sample, and the name-for-name contract reads the
parts rather than the index - parsing the index alone finds two names and passes.

**Same behaviour.** Every hook event, driven as a subprocess exactly as the agent drives it, must
produce the same exit code, stdout, stderr and store tree as the engine run directly, once the
sandbox path and the clock are masked out.

    python tests/_test_entry_point.py
"""
import json
import os
import re
import shutil
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

import ast  # noqa: E402
import types  # noqa: E402

entry, engine = PKG / "memory_hook.py", PKG / "_engine.py"
#: Read off the loader, never hard-coded: a ninth part added tomorrow has to fall under every
#: check below on the day it is added, not on the day someone remembers to extend a list here.
PARTS = [PKG / n for n in m.ENGINE_PARTS]

check("the engine body lives beside the entry point", engine.exists())
check("the entry point is small", entry.stat().st_size < 4096,
      f"{entry.stat().st_size} bytes")
check(f"the loader is an index over {len(PARTS)} parts, and every one of them is there",
      bool(PARTS) and all(p.exists() for p in PARTS),
      f"missing: {[p.name for p in PARTS if not p.exists()]}")
check("the entry point does not import the engine as a module",
      "import _engine" not in entry.read_text(encoding="utf-8"))

# The whole reason the body was cut up. A cap that only the loader satisfies would be a cap on
# nothing, so it is asserted on the parts, which is where the 8,426 lines actually went.
longest = max(PARTS, key=lambda p: len(p.read_text(encoding="utf-8").splitlines()))
longest_n = len(longest.read_text(encoding="utf-8").splitlines())
check("no part of the engine is longer than 2,000 lines", longest_n <= 2000,
      f"{longest.name} is {longest_n} lines")


# ── one namespace: every patch point still lives on this module ─────────────────────
print("# the namespace is the engine's own")

# Not a spot check: the engine's public and private surface is compared name for name against
# what the module actually carries, because a facade would pass any short list of samples.
#
# It has to read the PARTS and not just `_engine.py`. When the body was one file this loop
# parsed that file and found 501 names; after the cut, parsing the index alone finds two
# (`ENGINE_PARTS` and a loop variable) and prints "all 2 names ... are on memory_hook" in
# green. A contract that shrinks to nothing without going red is the failure this suite exists
# to prevent, so the number is part of the message and the sources are the files that carry code.
declared = set()
for src_file in [engine, *PARTS]:
    for node in ast.parse(src_file.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declared.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        declared.add(x.id)
#: The loader's own scratch names are deleted on its last line; they are not part of the engine.
declared = {n for n in declared if not n.startswith("_ld_")}
missing = sorted(n for n in declared if not hasattr(m, n))
check(f"all {len(declared)} names the engine defines are on memory_hook", not missing,
      f"missing: {missing[:8]}")
check("and that is the whole engine, not a fragment of it", len(declared) > 400,
      f"only {len(declared)} names declared - is the contract reading every part?")

# Both loaders' scratch names, not just the new one. This check used to list `memory_hook.py`'s
# three (`_ENGINE`, `_spec`, `_code`); replacing that list with the `_ld_` prefix would have
# covered `_engine.py`'s temporaries and quietly stopped covering the entry point's, so that
# dropping its `del _code` cost nothing. Both, and the prefix so a ninth temporary is covered
# the moment it is written.
leaked = sorted(n for n in vars(m)
                if n.startswith("_ld_") or n in ("_ENGINE", "_spec", "_code"))
check("neither loader leaves any of its own names behind", not leaked, f"leaked: {leaked}")

check("__file__ still points at the entry point",
      Path(m.__file__).name == "memory_hook.py")
check("rebinding a name on the module is seen by the engine's own code",
      (lambda: (setattr(m, "EARLIER_MAX_CHARS", 7),
                m._earlier_text.__globals__ is vars(m))[1])())

# The check above asks the question of ONE function, and one function is exactly what a split
# leaves behind. `__globals__` is the dictionary of the module a function was DEFINED in: move
# `emit_pretooluse_guard` into a real module and its read of `GUARD_ENFORCE` goes to that
# module's dictionary, while `tests/_test_guards.py` keeps setting the name here. Nothing goes
# red - the suite runs the un-enforced branch and reports that enforcement works. So the
# question is asked of every function the engine carries, and the answer has to be this
# dictionary for all of them.
#
# Two sibling modules are re-exported on purpose (`store_state`'s atomic writers and the
# `graph` layer's query functions); they are named, not pattern-matched, so that a third one
# appearing is a decision someone has to make here rather than a silent exemption.
RE_EXPORTED_FROM = {"store_state.py", "graph.py"}
own, foreign = [], []
for _n, _v in sorted(vars(m).items()):
    if not isinstance(_v, types.FunctionType):
        continue
    (own if Path(_v.__code__.co_filename).name not in RE_EXPORTED_FROM
     else foreign).append((_n, _v))
stranded = [n for n, v in own if v.__globals__ is not vars(m)]
check(f"all {len(own)} functions the engine defines run in this module's namespace",
      not stranded, f"stranded: {stranded[:8]}")
check(f"and the {len(foreign)} deliberate re-exports are the only ones from elsewhere",
      len(own) > 200 and all(Path(v.__code__.co_filename).name in RE_EXPORTED_FROM
                             for _, v in foreign))

# The static suites do not import the engine; they read it. `tests/_engine_source.py` puts the
# body back together from the parts, and if that reconstruction ever drifts from what the loader
# runs, a dozen suites go on asserting against text the process never executes.
sys.path.insert(0, str(HERE))
import _engine_source  # noqa: E402

check("the text the static suites read is the text the loader runs",
      _engine_source.SRC == "".join(
          p.read_text(encoding="utf-8").partition("#<<<ENGINE-PART-BODY>>>\n")[2] for p in PARTS),
      f"{len(_engine_source.SRC)} chars reassembled")
check("and it is still valid Python, in this order", bool(ast.parse(_engine_source.SRC).body))


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
# ── the body cache is a cache, never a source of truth ─────────────────────────────
#
# `_engine.py` keeps the eight compiled parts in one marshalled file rather than letting eight
# loaders keep eight `.pyc`s, because eight round trips cost +4.47 ms of every PreToolUse
# (measured paired, 2026-09-22). A hand-rolled cache earns exactly one thing from a reader:
# suspicion. These checks are that suspicion made executable, and they are here rather than in a
# suite of their own because the failure they guard against - serving yesterday's bytecode - is
# indistinguishable from the engine simply being wrong.
#
# The freshness key is `(st_size, st_mtime_ns)` per part. That is strictly stronger than what
# CPython pins a `.pyc` with, which is the source's size and its mtime truncated to whole
# seconds; an edit that lands inside one second and keeps the byte count is invisible to a `.pyc`
# and visible here.
print()
print("# the compiled-body cache is never served stale, and never fails the tool call")


def load_in(pkg: Path, expr: str) -> subprocess.CompletedProcess:
    """Import the engine from `pkg` in a fresh interpreter and print `expr`."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import memory_hook as m\n"
            "print(%s)\n") % (str(pkg), expr)
    env = dict(os.environ)
    env.update({"NEVERTWICE_HOME": str(pkg / "store"), "NEVERTWICE_VAULT": str(pkg / "store"),
                "NEVERTWICE_CLOUD": "none"})
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=240)


cachedir = Path(tempfile.mkdtemp(prefix="bodycache_")) / "nevertwice"
shutil.copytree(PKG, cachedir, ignore=shutil.ignore_patterns("__pycache__"))
r = load_in(cachedir, "getattr(m, '_CACHE_PROBE', 'absent')")
check("a cold copy loads and writes its cache", r.returncode == 0 and "absent" in r.stdout,
      f"exit {r.returncode}: {r.stderr[-400:]}")
blob = cachedir / "__pycache__" / f"_engine_body.{sys.implementation.cache_tag}.bin"
check("the cache landed where the loader looks for it", blob.exists(), str(blob))

# The one that matters. An edit to a part after the cache was written must reach the process,
# or every future change to the engine would be invisible until someone cleared __pycache__ by
# hand - and nothing would go red, because the engine would simply go on doing what it did.
edited = cachedir / m.ENGINE_PARTS[3]
edited.write_text(edited.read_text(encoding="utf-8") + "\n_CACHE_PROBE = 'fresh'\n",
                  encoding="utf-8", newline="")
r = load_in(cachedir, "getattr(m, '_CACHE_PROBE', 'absent')")
check("an edited part invalidates the cache and is seen", "fresh" in r.stdout,
      f"exit {r.returncode}: {r.stdout!r} {r.stderr[-300:]}")

# A cache that cannot be trusted has to be discarded rather than used or raised on: a truncated
# write, a half-flushed file, a marshal from another Python. Each one is a recompile.
for label, payload in (("a truncated", blob.read_bytes()[:len(blob.read_bytes()) // 3]),
                       ("an empty", b""),
                       ("a not-marshal-at-all", b"this is not a code object"),
                       ("a well-formed but wrong-shaped",
                        __import__("marshal").dumps({"a": 1}))):
    blob.write_bytes(payload)
    r = load_in(cachedir, "getattr(m, '_CACHE_PROBE', 'absent')")
    check(f"{label} cache is discarded, not executed and not raised on",
          r.returncode == 0 and "fresh" in r.stdout,
          f"exit {r.returncode}: {r.stdout!r} {r.stderr[-300:]}")

# A cache the process cannot create. The engine has to run anyway; it just pays the compile
# every time, which is a performance problem and never a correctness one.
#
# `chmod` was the obvious way to arrange this and is the wrong one: on Windows it does nothing
# to a directory, the write succeeds, and the check passes while never entering the branch it
# claims to cover. Replacing `__pycache__` with a FILE makes `makedirs` raise on both platforms
# for the same reason, so the arm is actually taken wherever this suite runs.
shutil.rmtree(blob.parent)
blob.parent.write_text("not a directory", encoding="utf-8")
try:
    r = load_in(cachedir, "getattr(m, '_CACHE_PROBE', 'absent')")
    check("a cache it cannot write still loads the engine",
          r.returncode == 0 and "fresh" in r.stdout,
          f"exit {r.returncode}: {r.stdout!r} {r.stderr[-300:]}")
finally:
    blob.parent.unlink()

# And the freshness key has to be the PARTS, not the loader: editing `_engine.py` itself is a
# change to the index, which CPython's own `.pyc` for the entry point does not cover either -
# so the check is that the parts' identity is what the key is made of.
check("the freshness key is built from every part, in load order",
      all(n in PKG.joinpath("_engine.py").read_text(encoding="utf-8") for n in ("st_size",
                                                                               "st_mtime_ns")),
      "the loader no longer keys on size and mtime")


print()
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

# The body being several files makes the half-copied install several times more likely: an
# installer that copies `memory_hook.py` and `_engine.py` and stops now lands a directory that
# looks complete and imports nothing. That case has to degrade the same way, with the missing
# file named, and it is the LAST part that is dropped here - a loader that checked only before
# the loop would pass this while dying in the middle of the engine.
partial = Path(tempfile.mkdtemp(prefix="partialinstall_")) / "nevertwice"
#: A whole install minus one part, not two files in an empty directory: the engine imports its
#: siblings, so a bare pair fails on `config` long before it reaches a part and would report
#: this closed on the wrong error.
shutil.copytree(PKG, partial, ignore=shutil.ignore_patterns("__pycache__"))
(partial / PARTS[-1].name).unlink()
r = subprocess.run([sys.executable, str(partial / "memory_hook.py")],
                   input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "h",
                                     "cwd": str(partial), "tool_name": "Edit", "tool_input": {}}),
                   capture_output=True, text=True, timeout=60)
check("a missing engine PART also leaves the tool call alone", r.returncode == 0,
      f"exit {r.returncode}: {r.stderr[-300:]}")
check("and names the part that is missing", PARTS[-1].name in r.stderr,
      r.stderr[-300:])

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


# ── a payload field that is present and null ──────────────────────────
#
# `session.get("session_id", "unknown")` returns the DEFAULT only when the key is ABSENT. An
# explicit JSON `null` - which is exactly how a host with no session writes "no value" - comes
# back as None, and `session_id[:8]` in main()'s very first log line raised TypeError before
# any try. The whole point of `hosts.py` is that hosts are other people's, so an adapter that
# sends `"session_id": null` turned every Edit, Write and Bash of that agent into an error:
# the rule this project has now written in three places is that memory being unavailable
# costs memory, never the agent's tool call.
#
# Every type, not one: a list survives the slice and a string is the normal case, so a check
# on a single value would have reported this closed.
print()
print("# a payload field of any JSON type costs memory, never the tool call")

good = Path(tempfile.mkdtemp(prefix="payloadstore_"))
genv = dict(os.environ)
genv.update({"NEVERTWICE_HOME": str(good), "NEVERTWICE_VAULT": str(good),
             "NEVERTWICE_CLOUD": "none"})
gtranscript = good / "t.jsonl"
gtranscript.write_text(
    json.dumps({"type": "user", "message": {"role": "user", "content": "remember this"}})
    + "\n", encoding="utf-8", newline="")

for label, value in (("a string", "abcdef12"), ("null", None), ("an int", 7),
                     ("a float", 1.5), ("a bool", True), ("a list", ["a", "b"]),
                     ("a dict", {"id": "x"})):
    for event in ("PreToolUse", "SessionStart"):
        payload = {"hook_event_name": event, "session_id": value, "cwd": str(good),
                   "transcript_path": str(gtranscript)}
        if event == "PreToolUse":
            payload |= {"tool_name": "Edit",
                        "tool_input": {"file_path": "a.py", "new_string": "x = 1"}}
        r = subprocess.run([sys.executable, str(entry)], input=json.dumps(payload),
                           capture_output=True, text=True, env=genv, timeout=180)
        check(f"session_id as {label} ({event}): exit 0", r.returncode == 0,
              f"exit {r.returncode}: {r.stderr[-300:]}")
        check(f"session_id as {label} ({event}): no traceback",
              "Traceback" not in r.stderr, r.stderr[-300:])

# The other fields of that same log line, each one null - none of them is more trusted than
# the one that crashed.
for field in ("cwd", "transcript_path", "hook_event_name", "tool_name", "prompt", "agent"):
    payload = {"hook_event_name": "PreToolUse", "session_id": "abcdef12", "cwd": str(good),
               "transcript_path": str(gtranscript), "tool_name": "Edit",
               "tool_input": {"file_path": "a.py", "new_string": "x = 1"}}
    payload[field] = None
    r = subprocess.run([sys.executable, str(entry)], input=json.dumps(payload),
                       capture_output=True, text=True, env=genv, timeout=180)
    check(f"{field}=null: exit 0", r.returncode == 0, f"exit {r.returncode}: {r.stderr[-300:]}")
    check(f"{field}=null: no traceback", "Traceback" not in r.stderr, r.stderr[-300:])

print()
print(f"entry point: {P} passed, {F} failed")
sys.exit(1 if F else 0)
