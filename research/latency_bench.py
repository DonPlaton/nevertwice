#!/usr/bin/env python3
"""Hot-path latency bench - the speed numbers behind docs/BENCHMARKS.md "Speed".

Measures what a user actually pays, end to end, on THIS machine:
  * cold import of the hook engine (every hook process pays this once)
  * PreToolUse e2e (a real subprocess, stdin event -> exit; fires on every tool call)
  * UserPromptSubmit e2e (recall per prompt)
  * idle SessionStart e2e (the no-backlog case the A1 gate keeps instant)
  * guards.check() per call, in-process, on a realistic ledger
  * lexical recall (no embedder) on a seeded store

Stdlib only, throwaway vault, no model and no network (NEVERTWICE_CLOUD=none and the
probe gate never fires on an empty backlog), so it runs anywhere - including the weak
machines the numbers are about. Run: python research/latency_bench.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "nevertwice"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _cold_import_ms() -> float:
    r = subprocess.run(
        [sys.executable, "-c",
         "import time;t=time.perf_counter();import sys;sys.path.insert(0,r'%s');"
         "import guards;print(f'{(time.perf_counter()-t)*1000:.1f}')" % PKG],
        capture_output=True, text=True)
    return float(r.stdout.strip() or "nan")


def _e2e_ms(evt: dict, env: dict, n: int = 3) -> float:
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        subprocess.run([sys.executable, str(PKG / "memory_hook.py")],
                       input=json.dumps(evt), capture_output=True, text=True,
                       env=env, timeout=120)
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def main() -> None:
    tmp = tempfile.mkdtemp()
    empty_root = tempfile.mkdtemp()
    env = {k: v for k, v in os.environ.items()
           if not any(s in k for s in ("CEREBRAS", "GROQ", "DEEPSEEK", "GEMINI",
                                       "OPENAI", "VOYAGE", "COHERE", "ANTHROPIC"))}
    env.update({"NEVERTWICE_HOME": tmp, "NEVERTWICE_GUARD_PACK": "1",
                "NEVERTWICE_CLOUD": "none", "NEVERTWICE_PROJECTS_ROOT": empty_root})

    # seed: universal pack + 50 project guards + 150 notes (no embedder)
    subprocess.run([sys.executable, str(PKG / "guards.py"), "pack"],
                   capture_output=True, env=env)
    seed = ("import sys; sys.path.insert(0, r'%s'); import api, guards as G\n"
            "ls=[{'type':'mistake','title':f'm{i}','description':f'failure mode {i%%7} token{i} in handler','prevention':'do X'} for i in range(150)]\n"
            "api.remember_lessons(ls, project='perfproj', embed=False)\n"
            "gs=G.load_guards()\n"
            "for i in range(50): G.register(gs, G.make_guard(rf'tok_{i}\\(', f'm {i}', project='perfproj'))\n"
            "G.save_guards(gs)") % PKG
    subprocess.run([sys.executable, "-c", seed], capture_output=True, env=env)

    rows = [("cold import (guards + engine)", f"{_cold_import_ms():.0f} ms",
             "paid once per hook process")]
    rows.append(("PreToolUse end-to-end", f"{_e2e_ms({'hook_event_name': 'PreToolUse', 'session_id': 'b', 'cwd': tmp, 'tool_name': 'Edit', 'tool_input': {'file_path': 'a.py', 'new_string': 'y = eval(s)'}}, env):.0f} ms",
                 "fires on every tool call; includes interpreter start"))
    rows.append(("UserPromptSubmit end-to-end", f"{_e2e_ms({'hook_event_name': 'UserPromptSubmit', 'session_id': 'b', 'cwd': tmp, 'prompt': 'why does the handler crash with failure mode 3'}, env):.0f} ms",
                 "recall per prompt"))
    rows.append(("SessionStart end-to-end (idle)", f"{_e2e_ms({'hook_event_name': 'SessionStart', 'session_id': 'b2', 'cwd': tmp, 'source': 'startup'}, env):.0f} ms",
                 "no backlog: the LLM probe is gated off"))

    # in-process: check() and lexical recall
    for k, v in env.items():
        os.environ[k] = v
    sys.path.insert(0, str(PKG))
    import guards as G
    gs = G.load_guards()
    text = ("def handler(x):\n    q = db.query(User).filter(User.name == x)\n" * 30)[:2000]
    n = 300
    t = time.perf_counter()
    for _ in range(n):
        G.check(text, project="perfproj", guards=gs)
    rows.append((f"guards.check(), {len(gs)} guards, 2 KB text",
                 f"{(time.perf_counter()-t)/n*1000:.2f} ms", "in-process, pure regex"))
    import memory_search as ms
    t = time.perf_counter()
    for _ in range(20):
        ms.search_core("failure mode 3 token12 in handler", "perfproj", 5)
    rows.append(("lexical recall, 150 notes (no embedder)",
                 f"{(time.perf_counter()-t)/20*1000:.1f} ms", "the weak-PC floor"))

    w = max(len(r[0]) for r in rows)
    print(f"\nHot-path latency ({sys.platform}, Python {sys.version.split()[0]})\n")
    for name, val, note in rows:
        print(f"  {name:<{w}}  {val:>9}   {note}")
    print("\nMachine-readable:")
    print(json.dumps({name: val for name, val, _ in rows}, indent=1))


if __name__ == "__main__":
    main()
