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

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "nevertwice"

sys.path.insert(0, str(PKG.parent))
import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo
# This bench seeds 150 fabricated notes and 50 fabricated guards. It built the child
# environment from `os.environ` and pinned NEVERTWICE_HOME only, so on a machine where
# NEVERTWICE_VAULT is exported - the supported way to point at a real store - the whole
# seed landed in the live vault. Isolating here scrubs the inherited value out of
# `os.environ` before `main()` copies it, and `main()` now pins both names on the child.
sandbox_guard.isolate(prefix="nevertwice-latency-")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _head_commit() -> str:
    """The commit these timings describe. A latency artifact that cannot name its engine is
    the exact failure task B8 exists to close."""
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _note_count() -> int:
    """How many notes the in-process recall actually sees.

    Hard-coding "150 notes" in the label is how this bench came to publish a floor measured
    over an empty store: the seed lands in the subprocess env, while the in-process half reads
    whatever store `config` resolved at import time. Counting makes the discrepancy visible in
    the output instead of silently mislabelling the row.
    """
    try:
        import memory_hook as m
        return len(m._iter_all_notes())
    except Exception:                  # noqa: BLE001 - a label must never fail the bench
        return -1


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


def _repeats() -> int:
    """How many times to repeat the whole measurement (`--repeat N`, default 5).

    Best-of-3 inside one invocation does not survive a busy host: three consecutive runs of
    this bench on the same commit produced 142, 185 and 112 ms for the same hot path. A single
    invocation therefore reports a sample, not a cost. Repeating the whole thing and keeping
    the minimum - the least-contended observation, which is what `timeit` documents - gives an
    estimate that a loaded machine can only push *up*, and recording the spread beside it makes
    the contention visible rather than hidden in a suspiciously precise figure.
    """
    for arg in sys.argv:
        if arg.startswith("--repeat="):
            return max(1, int(arg.split("=", 1)[1]))
    return 5


def _res(ms: float) -> float:
    """Round to a resolution the measurement can actually support.

    Reporting 102.8 ms from samples that spread over 20 ms is false precision, and false
    precision is how a number stops being falsifiable. Whole milliseconds above 10 ms,
    two decimals below - where the quantity really is sub-millisecond.
    """
    return round(ms) if ms >= 10 else round(ms, 2)


def _summarise(samples: list[float]) -> dict:
    ordered = sorted(samples)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"ms": _res(ordered[0]), "median_ms": _res(median),
            "max_ms": _res(ordered[-1]), "repeats": len(ordered)}


def main() -> None:
    tmp = tempfile.mkdtemp()
    empty_root = tempfile.mkdtemp()
    env = {k: v for k, v in os.environ.items()
           if not any(s in k for s in ("CEREBRAS", "GROQ", "DEEPSEEK", "GEMINI",
                                       "OPENAI", "VOYAGE", "COHERE", "ANTHROPIC"))}
    # BOTH names, always: config resolves `env("VAULT") or NEVERTWICE_HOME`, so pinning HOME
    # alone leaves an inherited NEVERTWICE_VAULT in charge of where the seed below lands.
    env.update({"NEVERTWICE_HOME": tmp, "NEVERTWICE_VAULT": tmp, "NEVERTWICE_GUARD_PACK": "1",
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

    events = {
        "pretooluse": ({"hook_event_name": "PreToolUse", "session_id": "b", "cwd": tmp,
                        "tool_name": "Edit",
                        "tool_input": {"file_path": "a.py", "new_string": "y = eval(s)"}},
                       "PreToolUse end-to-end",
                       "fires on every tool call; includes interpreter start"),
        "userpromptsubmit": ({"hook_event_name": "UserPromptSubmit", "session_id": "b",
                              "cwd": tmp,
                              "prompt": "why does the handler crash with failure mode 3"},
                             "UserPromptSubmit end-to-end", "recall per prompt"),
        "sessionstart": ({"hook_event_name": "SessionStart", "session_id": "b2", "cwd": tmp,
                          "source": "startup"},
                         "SessionStart end-to-end (idle)",
                         "no backlog: the LLM probe is gated off"),
    }
    repeats = _repeats()
    samples: dict[str, list[float]] = {"cold_import": [], **{k: [] for k in events}}
    for _ in range(repeats):
        samples["cold_import"].append(_cold_import_ms())
        for key, (evt, _label, _note) in events.items():
            samples[key].append(_e2e_ms(evt, env))

    summary = {key: _summarise(vals) for key, vals in samples.items()}
    rows = [("cold import (guards + engine)", f"{summary['cold_import']['ms']:.0f} ms",
             "paid once per hook process", "cold_import")]
    for key, (_evt, label, note) in events.items():
        rows.append((label, f"{summary[key]['ms']:.0f} ms", note, key))

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
                 f"{(time.perf_counter()-t)/n*1000:.2f} ms", "in-process, pure regex",
                 "guards_check"))
    import memory_search as ms
    t = time.perf_counter()
    for _ in range(20):
        ms.search_core("failure mode 3 token12 in handler", "perfproj", 5)
    rows.append((f"lexical recall, {_note_count()} notes (no embedder)",
                 f"{(time.perf_counter()-t)/20*1000:.1f} ms", "the weak-PC floor",
                 "lexical_recall_floor"))

    w = max(len(r[0]) for r in rows)
    print(f"\nHot-path latency ({sys.platform}, Python {sys.version.split()[0]})\n")
    for name, val, note, _key in rows:
        print(f"  {name:<{w}}  {val:>9}   {note}")
    print("\nMachine-readable:")
    print(json.dumps({name: val for name, val, _n, _k in rows}, indent=1))

    if "--save" in sys.argv:
        out = ROOT / "research" / "latency_bench.json"
        out.write_text(json.dumps({
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "commit": _head_commit(),
            "repeats": repeats,
            "measurements": {key: {"label": name, "note": note,
                                   **summary.get(key, {"ms": float(val.split()[0])})}
                             for name, val, note, key in rows},
            "load_note": f"Each hot path was measured {repeats} times, and each of those is "
                         "itself the best of 3 invocations; `ms` is the minimum, with "
                         "`median_ms` and `max_ms` beside it. The minimum is the "
                         "least-contended observation, so a busy host can only push these "
                         "numbers up - which is the direction an honest latency claim should "
                         "err in. Nothing here pins CPU affinity or waits for an idle stand.",
        }, indent=1) + "\n", encoding="utf-8")
        print(f"\n  saved -> {out}")


if __name__ == "__main__":
    main()
