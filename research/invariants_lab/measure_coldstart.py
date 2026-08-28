"""T3: does a preconfigured invariant change what a real model writes, on minute zero?

The cold-start claim, stated narrowly enough to be wrong:

> On a repository with **no prior sessions**, a retrieve-and-inject memory returns nothing by
> construction -- Nevertwice as it ships today included. A preconfigured invariant checks a
> property of the code itself, so it can fire from minute zero. Does that change the output?

The comparison is therefore **Nevertwice-with-an-empty-store versus Nevertwice with the
preconfigured pack**, which is the one arm this project can actually run. No vendor is named,
because no vendor was run on this stand.

## The design, and the circularity it cannot fully escape

Twelve tasks, each asking a small function whose natural sloppy answer contains one of the pack's
three shapes. Eight trials each, paired by task.

* **off** -- one generation, nothing injected.
* **on** -- one generation; if the pack fires, **one** regeneration with the invariant's message
  as a single extra line. That is the real loop: an agent writes, the invariant fires, the agent
  corrects. It costs one round trip and only when something fired, which is what the zero-token
  contract buys.

**The judge is not the pack.** `judge.py` re-derives each pitfall with a separate AST predicate
written for this file. The two look for the same *shape* -- they must, or the experiment would be
about nothing -- but through different code, so a bug in the pack cannot silently score itself
correct. The honest claim is therefore about **delivery**: does handing the model this constraint
change what it writes? It is not independent evidence that the constraint is worth having.

Runs against the owner's already-running Ollama, read-only: no model is pulled, nothing is
started or stopped, and the requests are the same shape their own hook makes.

    python research/invariants_lab/measure_coldstart.py --model qwen3.5:4b --trials 8
    python research/invariants_lab/measure_coldstart.py --print
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import invariant_notes as I  # noqa: E402
import preconfigured as P  # noqa: E402
from corpusio import progress  # noqa: E402

ARTIFACT = Path(__file__).with_name("coldstart_t3.json")
OLLAMA = "http://localhost:11434/api/generate"
ALPHA = 0.05


# --------------------------------------------------------------------------
# the tasks: the natural sloppy answer contains one of the pack's shapes
# --------------------------------------------------------------------------


TASKS = [
    ("collect_tags", "mutable_default",
     "Write a Python function `collect_tags(item, tags=...)` that appends the item's tag "
     "to a list of tags and returns the list. The caller may omit the list."),
    ("accumulate", "mutable_default",
     "Write a Python function `accumulate(value, seen=...)` that records the value in a "
     "dict of counts and returns it. The caller may omit the dict."),
    ("append_row", "mutable_default",
     "Write a Python function `append_row(row, rows=...)` that adds a row to a list and "
     "returns it. The list argument is optional."),
    ("register", "mutable_default",
     "Write a Python function `register(name, registry=...)` that adds the name to a set "
     "and returns the set. The set argument is optional."),
    ("parse_int", "bare_except",
     "Write a Python function `parse_int(text)` that returns int(text), or 0 if the text "
     "cannot be parsed. Catch the failure."),
    ("load_config", "bare_except",
     "Write a Python function `load_config(path)` that reads and json-decodes a file and "
     "returns {} if anything goes wrong. Catch the failure."),
    ("safe_divide", "bare_except",
     "Write a Python function `safe_divide(a, b)` that returns a / b, or None if that "
     "fails for any reason. Catch the failure."),
    ("fetch_field", "bare_except",
     "Write a Python function `fetch_field(data, key)` that returns data[key] or None if "
     "the lookup fails in any way. Catch the failure."),
    ("set_rate", "assert_in_shipped_code",
     "Write a Python function `set_rate(rate)` for a production library that checks the "
     "rate is between 0 and 1 before storing it in a module-level RATE variable."),
    ("connect", "assert_in_shipped_code",
     "Write a Python function `connect(host, port)` for a production library that checks "
     "the port is a positive integer before returning a connection string."),
    ("set_limit", "assert_in_shipped_code",
     "Write a Python function `set_limit(n)` for a production library that checks n is "
     "a positive integer before storing it in a module-level LIMIT variable."),
    ("scale_value", "assert_in_shipped_code",
     "Write a Python function `scale_value(x, factor)` for a production library that "
     "checks factor is not zero before dividing x by it."),
]

SYSTEM = ("You are writing Python for a production library. Answer with one function in a "
          "single ```python block and nothing else.")


# --------------------------------------------------------------------------
# the judge -- deliberately not the pack
# --------------------------------------------------------------------------


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (blocks[0] if blocks else text).strip()


def judge(code: str, pitfall: str) -> bool | None:
    """Did the model make the pitfall? None when there is nothing to grade.

    Written from scratch here rather than imported from `preconfigured`, so a bug in
    the detector cannot score itself correct.

    **An answer with no function in it is not an answer.** `ast.parse("")` succeeds and
    finds no pitfall, so an empty or truncated completion used to score as a clean
    pass -- which produced a base pitfall rate of zero from a model that had not
    written a line. Ungraded is not the same as correct.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               for n in ast.walk(tree)):
        return None

    if pitfall == "mutable_default":
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(node.args.defaults) + [
                    d for d in node.args.kw_defaults if d is not None]
                if any(isinstance(d, (ast.List, ast.Dict, ast.Set)) for d in defaults):
                    return True
        return False

    if pitfall == "bare_except":
        return any(isinstance(n, ast.ExceptHandler) and n.type is None
                   for n in ast.walk(tree))

    if pitfall == "assert_in_shipped_code":
        return any(isinstance(n, ast.Assert) for n in ast.walk(tree))

    raise ValueError(f"no judge for {pitfall!r}")


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------


def ask(model: str, prompt: str, *, timeout: int = 240, temperature: float = 0.7) -> str:
    """One completion. Thinking off, and a budget large enough to finish.

    The first run of this file returned **empty strings** for every call: `qwen3.5:4b`
    is a thinking model, it spent the whole 400-token budget in `thinking`, and
    `done_reason` came back `length` with `response` empty. Every empty answer then
    parsed as an empty module and scored as "no pitfall" -- a base rate of zero that
    was an artifact of truncation. Two fixes, and the second one matters more: turn
    thinking off, and make the judge refuse to grade an answer with no function in it.
    """
    body = json.dumps({
        "model": model, "prompt": prompt, "system": SYSTEM, "stream": False,
        "think": False,
        "options": {"temperature": temperature, "num_predict": 700},
    }).encode()
    request = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "")


def pack_message(code: str) -> str | None:
    """What the preconfigured pack would say about this code, or nothing."""
    ledger = P.build_pack()
    found = I.check_diff({"m.py": ""}, {"m.py": code}, ledger=ledger,
                         registry=P.REGISTRY)
    return f"{found[0].message} ({found[0].evidence})" if found else None


# --------------------------------------------------------------------------


def one_trial(model: str, task: tuple[str, str, str], trial: int) -> dict:
    name, pitfall, prompt = task
    seed_note = f"\n\n# attempt {trial}"
    first = extract_code(ask(model, prompt + seed_note))
    off = judge(first, pitfall)

    message = pack_message(first)
    if message is None:
        on, turns = off, 1
        second = first
    else:
        second = extract_code(ask(
            model,
            f"{prompt}{seed_note}\n\nA project invariant applies here: {message}\n"
            f"Write the function so that it does not violate it."))
        on = judge(second, pitfall)
        turns = 2
    return {"task": name, "pitfall": pitfall, "trial": trial,
            "off": off, "on": on, "fired": message is not None, "turns": turns}


def run(model: str, trials: int, workers: int) -> dict:
    jobs = [(task, t) for task in TASKS for t in range(trials)]
    progress(f"{len(jobs)} generations on {model}")
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one_trial, model, task, t) for task, t in jobs]
        for i, future in enumerate(futures):
            if i % 20 == 0:
                progress(f"  {i}/{len(futures)}")
            try:
                rows.append(future.result())
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                rows.append({"error": str(exc)[:200]})
    return {"model": model, "trials": trials, "rows": rows}


def summarise(raw: dict) -> dict:
    rows = [r for r in raw["rows"] if "error" not in r
            and r["off"] is not None and r["on"] is not None]
    n = len(rows)
    off = sum(1 for r in rows if r["off"])
    on = sum(1 for r in rows if r["on"])
    b01 = sum(1 for r in rows if r["off"] and not r["on"])   # fixed by the invariant
    b10 = sum(1 for r in rows if not r["off"] and r["on"])   # broken by it
    m = b01 + b10
    p = float(stats.binomtest(b01, m, 0.5).pvalue) if m else 1.0

    def ci(k: int) -> list[float]:
        lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
        return [float(lo), float(hi)]

    by_pitfall = {}
    for pitfall in sorted({r["pitfall"] for r in rows}):
        sub = [r for r in rows if r["pitfall"] == pitfall]
        by_pitfall[pitfall] = {
            "n": len(sub),
            "off": sum(1 for r in sub if r["off"]),
            "on": sum(1 for r in sub if r["on"]),
            "fired": sum(1 for r in sub if r["fired"]),
        }

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": raw["model"], "trials": raw["trials"],
        "usable": n, "attempted": len(raw["rows"]),
        "unparseable": sum(1 for r in raw["rows"]
                           if "error" not in r
                           and (r["off"] is None or r["on"] is None)),
        "errors": sum(1 for r in raw["rows"] if "error" in r),
        "pitfall_rate": {
            "off": {"k": off, "n": n, "point": off / n if n else float("nan"),
                    "ci": ci(off)},
            "on": {"k": on, "n": n, "point": on / n if n else float("nan"),
                   "ci": ci(on)},
        },
        "mcnemar": {"fixed_by_the_invariant": b01, "broken_by_it": b10,
                    "discordant": m, "p": p,
                    "passes": b01 > b10 and p < ALPHA},
        "fired_rate": sum(1 for r in rows if r["fired"]) / n if n else float("nan"),
        "by_pitfall": by_pitfall,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        pr, mc = d["pitfall_rate"], d["mcnemar"]
        print(f"model {d['model']}, {d['trials']} trials/task, "
              f"{d['usable']} usable of {d['attempted']} "
              f"({d['unparseable']} unparseable, {d['errors']} errors)")
        print(f"pitfall rate  off {pr['off']['point']:.3f} "
              f"[{pr['off']['ci'][0]:.3f}, {pr['off']['ci'][1]:.3f}]   "
              f"on {pr['on']['point']:.3f} "
              f"[{pr['on']['ci'][0]:.3f}, {pr['on']['ci'][1]:.3f}]")
        print(f"McNemar       fixed {mc['fixed_by_the_invariant']}, "
              f"broken {mc['broken_by_it']}, p = {mc['p']:.3g}   "
              f"{'PASS' if mc['passes'] else 'FAIL'}")
        print(f"the pack fired on {d['fired_rate']:.3f} of first attempts")
        print()
        for pitfall, v in d["by_pitfall"].items():
            print(f"  {pitfall:<26} off {v['off']}/{v['n']}  on {v['on']}/{v['n']}  "
                  f"fired {v['fired']}")
        return 0

    raw = run(args.model, args.trials, args.workers)
    out = summarise(raw)
    out["rows"] = raw["rows"]
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
