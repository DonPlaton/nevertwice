"""F5: does a declared growth axis prevent a scale fault a model would otherwise write?

`COLDSTART_F5.md` is the design, written before this ran. The short version of why this
experiment exists: T3 showed a *portable* invariant cannot help, because portable knowledge
is what models already have. A declared growth axis is the one shape that escapes the
argument -- `records: 1_000 -> 50_000_000` is in no model's weights and is checkable on day
one.

Two stages, and the first can end it:

    python research/invariants_lab/measure_declared_axis.py --probe     # the base rate
    python research/invariants_lab/measure_declared_axis.py --trials N  # the stand
    python research/invariants_lab/measure_declared_axis.py --print

**The judge is `scale.canary`, not `scale.find_problems`.** Scoring a static checker with a
static rule would be `ABSTENTION_F1.md` §1's circularity: the ON arm is optimised against
the judge and wins by construction. The canary runs the generated function at n and 10n and
asks whether cost grew faster than the input. The static verdict is recorded beside it and
is not the gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from statsmodels.stats.proportion import proportion_confint
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scale as SC  # noqa: E402
from corpusio import progress  # noqa: E402

ARTIFACT = Path(__file__).with_name("declared_axis_f5.json")
OLLAMA = "http://localhost:11434/api/generate"

AXIS = {"name": "records", "unit": "records", "start": 1_000, "target": 50_000_000}
DECLARATION = SC.Declaration.parse({"axes": [AXIS]})

PROBE_TRIALS = 5            # COLDSTART_F5.md section 4: 6 tasks x 5 = 30 per model
MAX_TRIALS = 400            # above this the stand is refused, with the arithmetic
ASSUMED_FIX_RATE = 0.60     # declared in section 4, conservative
ALPHA = 0.05

SYSTEM = ("You are a careful Python engineer. Answer with one function in a single "
          "```python code block and nothing else. Use only the standard library.")

#: Six tasks over a parameter literally named `records`, each of a shape that invites a
#: superlinear implementation without requiring one. Declared before the run.
TASKS: tuple[tuple[str, str], ...] = (
    ("duplicate_pairs",
     "Write `duplicate_pairs(records)`. `records` is a list of dicts each having an "
     "'email' key. Return a list of (i, j) index pairs, i < j, where the two records "
     "share the same email."),
    ("count_matches",
     "Write `count_matches(records)`. `records` is a list of dicts each having a "
     "'country' key. Return a list of integers, one per record, giving how many OTHER "
     "records have the same country."),
    ("dedupe",
     "Write `dedupe(records)`. `records` is a list of dicts each having an 'id' key. "
     "Return a new list keeping only the first record for each id, in the original order."),
    ("join_accounts",
     "Write `join_accounts(records, accounts)`. `records` is a list of dicts with an "
     "'account_id' key and `accounts` is a list of dicts with an 'id' key. Return a list "
     "of (record, account) pairs where record['account_id'] == account['id']."),
    ("top_by_score",
     "Write `top_by_score(records, k)`. `records` is a list of dicts each having a "
     "'score' key. Return the k records with the highest score, highest first."),
    ("summarise",
     "Write `summarise(records)`. `records` is a list of dicts each having a 'country' "
     "key. Return a dict mapping each country to the number of records with it."),
)

#: The synthetic input each task's function is run against, as source, so the harness and
#: the subprocess agree on it without pickling.
INPUT_SOURCE = {
    "duplicate_pairs": "([{'email': 'u%d@x' % (i % max(n // 2, 1))} for i in range(n)],)",
    "count_matches": "([{'country': 'c%d' % (i % 7)} for i in range(n)],)",
    "dedupe": "([{'id': i % max(n // 2, 1)} for i in range(n)],)",
    "join_accounts": ("([{'account_id': i} for i in range(n)], "
                      "[{'id': i} for i in range(n)])"),
    "top_by_score": "([{'score': (i * 7919) % 1000} for i in range(n)], 10)",
    "summarise": "([{'country': 'c%d' % (i % 7)} for i in range(n)],)",
}

#: COLDSTART_F5.md section 2. Anything outside this and the code is not executed.
ALLOWED_IMPORTS = {"collections", "itertools", "heapq", "operator", "functools", "math",
                   "typing", "dataclasses", "bisect", "statistics", "re"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "open", "input",
                   "globals", "locals", "vars", "getattr", "setattr", "delattr"}
FORBIDDEN_MODULES = {"os", "sys", "subprocess", "shutil", "socket", "pathlib", "shelve",
                     "pickle", "importlib", "ctypes", "multiprocessing", "threading"}


# ---------------------------------------------------------------------------


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (blocks[0] if blocks else text).strip()


def containment_verdict(code: str) -> str | None:
    """None when the code may be executed; otherwise why it may not."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return f"does not parse: {type(exc).__name__}"
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               for n in ast.walk(tree)):
        return "no function in it"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES or root not in ALLOWED_IMPORTS:
                    return f"imports {alias.name!r}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES or root not in ALLOWED_IMPORTS:
                return f"imports from {node.module!r}"
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"names {node.id!r}"
        elif isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in FORBIDDEN_MODULES:
                return f"touches {base.id}.{node.attr}"
    return None


_RUNNER = r'''
import json, sys, gc, time, tracemalloc
src = sys.stdin.read()
payload = json.loads(src)
ns = {}
exec(payload["code"], ns)
fn = None
for name in payload["names"]:
    if name in ns and callable(ns[name]):
        fn = ns[name]
        break
if fn is None:
    for v in ns.values():
        if callable(v) and getattr(v, "__module__", None) is None:
            fn = v
            break
if fn is None:
    print(json.dumps({"error": "no callable"})); sys.exit(0)

def build(n):
    return eval(payload["inputs"], {"n": n})

MEASURABLE = 0.005
def measure(size, repeats=3):
    best, peak = float("inf"), 0
    args = build(size)
    for _ in range(repeats):
        gc.collect(); tracemalloc.start()
        t0 = time.perf_counter()
        fn(*args)
        el = time.perf_counter() - t0
        _c, p = tracemalloc.get_traced_memory(); tracemalloc.stop()
        best = min(best, el); peak = max(peak, p)
    return best, peak

try:
    size = payload["n"]
    st, sm = measure(size)
    while st < MEASURABLE and size * 10 <= payload["max_n"]:
        size *= 2
        st, sm = measure(size)
    if st < MEASURABLE:
        print(json.dumps({"abstained": True, "n": size})); sys.exit(0)
    bt, bm = measure(size * 10)
    tf = bt / st / 10
    mf = (bm / max(sm, 1)) / 10
    print(json.dumps({"abstained": False, "n": size, "time_factor": tf,
                      "memory_factor": mf,
                      "ok": tf <= 3.0 and mf <= 3.0}))
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__ + ": " + str(exc)[:200]}))
'''


#: How far the canary may size up before giving up. The probe's first run used 200,000
#: and abstained on **half** of every generation -- and not at random: `dedupe`,
#: `summarise` and `top_by_score` have answers so fast they never reached the 5 ms floor
#: at the sizes that ceiling allowed, so three of six tasks contributed nothing and the
#: stand would have been silently halved and biased toward the slow half. Raised to
#: `scale.canary`'s own default. Fifth time this project has had to notice that an
#: instrument which cannot answer is not an instrument that answered "fine".
MAX_N = 2_000_000


def dynamic_verdict(code: str, task: str, *, n: int = 200,
                    timeout: int = 240) -> dict:
    """`scale.canary`'s question, asked in a subprocess so a runaway cannot take the run.

    Returns a dict with `ok`, or with `abstained`, or with `error`. None of the three is
    silently a pass: COLDSTART_F5.md section 3 makes an abstention and an error
    **unusable**, and `SCALE_X4` already found 12 of 40 "misses" were abstentions.
    """
    payload = json.dumps({"code": code, "names": [task], "inputs": INPUT_SOURCE[task],
                          "n": n, "max_n": MAX_N})
    try:
        proc = subprocess.run([sys.executable, "-c", _RUNNER],
                              input=payload, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"error": "timed out"}
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return {"error": "no output: " + (proc.stderr or "")[:200]}
    try:
        return json.loads(out[-1])
    except json.JSONDecodeError:
        return {"error": "unparseable runner output"}


def static_verdict(code: str) -> int:
    return len(SC.find_problems(code, "m.py", DECLARATION))


def ask(model: str, prompt: str, *, timeout: int = 300,
        temperature: float = 0.7) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "system": SYSTEM, "stream": False,
        "think": False,   # T3: a thinking model spent its whole budget in `thinking`
        "options": {"temperature": temperature, "num_predict": 900},
    }).encode()
    request = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "")


def finding_message(code: str) -> str | None:
    problems = SC.find_problems(code, "m.py", DECLARATION)
    if not problems:
        return None
    p = problems[0]
    return (f"{p.detail}. This project declares a growth axis: "
            f"{AXIS['name']}: {AXIS['start']:,} -> {AXIS['target']:,} {AXIS['unit']}.")


def one_trial(model: str, task: str, prompt: str, trial: int) -> dict:
    seed = f"\n\n# attempt {trial}"
    first_raw = ask(model, prompt + seed)
    first = extract_code(first_raw)
    row = {"model": model, "task": task, "trial": trial,
           # The code is kept, truncated, so a reader can audit why the checker was
           # silent on a generation the canary condemned. The probe's two real faults
           # had static=0 and nothing in the artifact said why.
           "off_code": first[:1400],
           "off_len": len(first), "off_static": None, "off": None,
           "on_static": None, "on": None, "fired": False, "turns": 1,
           "off_unusable": None, "on_unusable": None}

    refusal = containment_verdict(first)
    if refusal:
        row["off_unusable"] = refusal
    else:
        row["off_static"] = static_verdict(first)
        got = dynamic_verdict(first, task)
        if got.get("error"):
            row["off_unusable"] = got["error"]
        elif got.get("abstained"):
            row["off_unusable"] = "canary abstained"
        else:
            row["off"] = not got["ok"]          # True == a scale fault
            row["off_time_factor"] = round(got["time_factor"], 3)

    message = finding_message(first) if not refusal else None
    if message is None:
        row["on"], row["on_unusable"] = row["off"], row["off_unusable"]
        row["on_static"] = row["off_static"]
        return row

    row["fired"], row["turns"] = True, 2
    second = extract_code(ask(
        model,
        f"{prompt}{seed}\n\nA project invariant applies here: {message}\n"
        f"Write the function so that it does not violate it."))
    row["on_code"] = second[:1400]
    row["on_len"] = len(second)
    refusal2 = containment_verdict(second)
    if refusal2:
        row["on_unusable"] = refusal2
        return row
    row["on_static"] = static_verdict(second)
    got2 = dynamic_verdict(second, task)
    if got2.get("error"):
        row["on_unusable"] = got2["error"]
    elif got2.get("abstained"):
        row["on_unusable"] = "canary abstained"
    else:
        row["on"] = not got2["ok"]
        row["on_time_factor"] = round(got2["time_factor"], 3)
    return row


def run(model: str, trials: int) -> list[dict]:
    rows = []
    total = len(TASKS) * trials
    for t in range(trials):
        for task, prompt in TASKS:
            progress(f"  {model} {len(rows) + 1}/{total}  {task} trial {t}")
            try:
                rows.append(one_trial(model, task, prompt, t))
            except Exception as exc:  # noqa: BLE001 - a dead model must not lose the run
                rows.append({"model": model, "task": task, "trial": t,
                             "off_unusable": f"harness: {type(exc).__name__}",
                             "on_unusable": f"harness: {type(exc).__name__}",
                             "off": None, "on": None, "fired": False})
    return rows


# ---------------------------------------------------------------------------


def trials_needed(base_rate: float, fix_rate: float = ASSUMED_FIX_RATE) -> float:
    """COLDSTART_F5.md section 4: 6 discordant pairs one way rejects at alpha = 0.05."""
    denom = base_rate * fix_rate
    return float("inf") if denom <= 0 else 6.0 / denom


def summarise(rows: list[dict], stage: str) -> dict:
    by_model: dict[str, dict] = {}
    for model in sorted({r["model"] for r in rows}):
        mine = [r for r in rows if r["model"] == model]
        usable = [r for r in mine if r.get("off") is not None]
        faults = sum(1 for r in usable if r["off"])
        n = len(usable)
        lo, hi = (proportion_confint(faults, n, alpha=ALPHA, method="wilson")
                  if n else (float("nan"), float("nan")))
        base = faults / n if n else float("nan")
        need = trials_needed(base)
        paired = [r for r in mine
                  if r.get("off") is not None and r.get("on") is not None]
        b01 = sum(1 for r in paired if r["off"] and not r["on"])   # fixed
        b10 = sum(1 for r in paired if not r["off"] and r["on"])   # broken
        m = b01 + b10
        p = float(stats.binomtest(b01, m, 0.5).pvalue) if m else float("nan")
        reasons: dict[str, int] = {}
        for r in mine:
            if r.get("off_unusable"):
                key = str(r["off_unusable"])[:40]
                reasons[key] = reasons.get(key, 0) + 1
        by_model[model] = {
            "generations": len(mine),
            "usable": n,
            "unusable": len(mine) - n,
            "unusable_reasons": reasons,
            "base_rate": {"faults": faults, "n": n, "point": base, "ci": [float(lo), float(hi)]},
            "checker_fired_on": sum(1 for r in mine if r.get("fired")),
            "static_flagged_off": sum(1 for r in mine if (r.get("off_static") or 0) > 0),
            "paired": {"n": len(paired), "fixed": b01, "broken": b10,
                       "discordant": m, "mcnemar_p": p},
            "trials_needed_at_this_base_rate": (None if need == float("inf")
                                                else int(need + 0.999)),
            "resolvable_within_budget": need <= MAX_TRIALS,
        }
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "F5", "stage": stage,
        "axis": AXIS,
        "judge": "scale.canary (dynamic); find_problems recorded but not the gate",
        "max_trials_budget": MAX_TRIALS,
        "assumed_fix_rate": ASSUMED_FIX_RATE,
        "by_model": by_model,
        "rows": rows,
    }


def _print(data: dict) -> None:
    print(f"F5 -- a declared growth axis, stage: {data['stage']}")
    print(f"  axis {data['axis']['name']}: {data['axis']['start']:,} -> "
          f"{data['axis']['target']:,}   judge: {data['judge']}")
    print()
    for model, m in data["by_model"].items():
        b = m["base_rate"]
        print(f"  {model}")
        print(f"    generations {m['generations']}, usable {m['usable']}, "
              f"unusable {m['unusable']}")
        if m["unusable_reasons"]:
            for why, k in sorted(m["unusable_reasons"].items(), key=lambda x: -x[1])[:5]:
                print(f"       {k:3d}  {why}")
        if m["usable"]:
            print(f"    BASE RATE {b['point']:.3f} [{b['ci'][0]:.2f}, {b['ci'][1]:.2f}]  "
                  f"({b['faults']}/{b['n']} generations are superlinear)")
        print(f"    the checker fired on {m['checker_fired_on']} of {m['generations']}; "
              f"static flagged {m['static_flagged_off']}")
        need = m["trials_needed_at_this_base_rate"]
        if need is None:
            print("    trials needed: INFINITE -- the base rate is zero and the stand "
                  "cannot resolve anything")
        else:
            print(f"    trials needed at fix rate {data['assumed_fix_rate']}: {need} "
                  f"(budget {data['max_trials_budget']}) -- "
                  f"{'RESOLVABLE' if m['resolvable_within_budget'] else 'REFUSED'}")
        p = m["paired"]
        if p["discordant"]:
            print(f"    paired so far: {p['fixed']} fixed, {p['broken']} broken, "
                  f"exact McNemar p = {p['mcnemar_p']:.3g}")
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="qwen3-coder:30b,qwen2.5:7b")
    ap.add_argument("--probe", action="store_true",
                    help="the base-rate probe: 6 tasks x 5 trials per model")
    ap.add_argument("--trials", type=int, default=0)
    ap.add_argument("--print", action="store_true", dest="show")
    args = ap.parse_args(argv)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    trials = PROBE_TRIALS if args.probe else args.trials
    if not trials:
        ap.error("pass --probe or --trials N")
    rows: list[dict] = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        rows.extend(run(model, trials))
    data = summarise(rows, "probe" if args.probe else f"stand ({trials} trials)")
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
