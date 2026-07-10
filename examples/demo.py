#!/usr/bin/env python3
"""Nevertwice 25-second demo - the "it remembered" moment, on any OS.

Pure standard library, no bash. Seeds a throwaway temp store (your real vault is
untouched), then recalls from it. Best with Ollama running for semantic recall; it
falls back to lexical full-text search without it, so it always produces a real hit.

    python examples/demo.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "nevertwice"
PY = sys.executable


def say(msg):
    # flush: the parent is block-buffered when piped, the children are not - without
    # this the narration prints after all results in `demo.py > log` / CI capture
    print(f"\n\033[1;36m{msg}\033[0m", flush=True)


FAILED = 0


def run(args):
    global FAILED
    print(f"\033[2m$ python {' '.join(args)}\033[0m", flush=True)
    r = subprocess.run([PY, str(PKG / args[0]), *args[1:]], cwd=str(ROOT), env=ENV)
    FAILED |= r.returncode                 # a demo that silently swallows failures can't be trusted


# throwaway store so the real vault is never touched
_tmp = tempfile.mkdtemp(prefix="nevertwice-demo-")
ENV = {**os.environ, "NEVERTWICE_VAULT": str(Path(_tmp) / "store")}
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    say("(1) Session one. Your agent hits a bug and learns three lessons:")
    run(["remember.py", "--project", "demo", "--type", "mistake",
         "--title", "CUDA OOM at batch=64 on the GPU",
         "--prevention", "lower batch size or enable gradient checkpointing"])
    run(["remember.py", "--project", "demo", "--type", "pattern",
         "--title", "Crash-safe writes",
         "--prevention", "write to a tmp file then os.replace, never partial files"])
    run(["remember.py", "--project", "demo", "--type", "decision",
         "--title", "Chose Postgres over Mongo",
         "--prevention", "relational integrity mattered more than schema flexibility"])

    say("(2) Days later. A fresh agent, a new prompt. Does it remember?")
    run(["memory_search.py", "training keeps crashing out of gpu memory", "demo"])

    say("(3) Different topic, still the right lesson (not keyword soup):")
    run(["memory_search.py", "how should I persist files safely", "demo"])

    say("(4) And it knows when it does not know (calibrated abstention):")
    run(["memory_search.py", "xyzzy nonsense unrelated gibberish", "demo"])

    say("That is it. Plain Markdown + Git. No DB, no server, no cloud.")
    print("    github.com/DonPlaton/nevertwice")
finally:
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)
sys.exit(1 if FAILED else 0)
