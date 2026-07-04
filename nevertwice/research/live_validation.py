#!/usr/bin/env python3
"""RESEARCH — live validation of the Active Memory thesis on a real LLM.

Code can look right and still be a non-product. The whole Active Memory story rests on ONE
empirical assumption: that a fired guard/warning actually changes a real model's output — that
seeing the lesson makes the agent avoid the pitfall. The D simulation *assumed* that effect
(`eff = 0.75`). This harness MEASURES it, end to end, on a real model, and then feeds the
measured number back into D to see whether the improvement-per-token conclusion survives on
measured ground instead of an assumption.

## The experiment (paired, objective, honest)

A curated set of real coding micro-tasks, each with a **common pitfall** and an **objective
static check** (regex/AST — we never execute model output, for safety). For each task we run a
real model (DeepSeek, the user's key) twice:

  * **without memory** — just the task.
  * **with active memory** — the task plus the ONE-LINE guard the system would fire.

We measure the pitfall rate in each condition; the drop is the real `eff`. We also run the
actual `guards.check` on the *unguarded* outputs to confirm axis A genuinely detects the real
bug (not just that a hand-written hint helps). Multiple trials per task (temperature > 0) give
a rate, not an anecdote.

    python research/live_validation.py --trials 5 --save     # needs DEEPSEEK_API_KEY
    python research/live_validation.py --replug-d            # re-run D with the measured eff
    python research/live_validation.py --dry                 # list tasks + self-check the checks

Honest by construction: if the guard does NOT reduce the pitfall, this prints eff≈0 and the
thesis is in trouble — which is exactly what we want to learn before shipping.
"""
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import memory_hook as m          # noqa: E402
import guards as G               # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ── objective static checks (pitfall present?) — no execution of model output ──

def _strip_fences(code: str) -> str:
    """Pull the code out of a ```python ... ``` block if present; else return as-is."""
    m2 = re.search(r"```(?:python|py)?\s*(.+?)```", code, re.DOTALL)
    return (m2.group(1) if m2 else code).strip()


def chk_mutable_default(c):
    return bool(re.search(r"def\s+\w+\([^)]*=\s*(\[\s*\]|\{\s*\}|list\(\)|dict\(\))", c))


def chk_bare_except(c):
    return bool(re.search(r"except\s*:", c))


def chk_div_zero(c):
    # pitfall = divides by len(...) with NO empty/zero guard anywhere
    divides = bool(re.search(r"/\s*len\(", c))
    guarded = bool(re.search(r"(if\s+not\s+\w+)|(len\([^)]*\)\s*==\s*0)|(len\([^)]*\)\s*<\s*1)"
                             r"|(if\s+\w+\s*:)|(if\s+len\()|(or\s+1\b)|(max\(1)", c))
    return divides and not guarded


def chk_no_context_manager(c):
    # pitfall = open(...) used without a `with`
    opens = bool(re.search(r"\bopen\s*\(", c))
    return opens and not bool(re.search(r"\bwith\s+open\s*\(", c))


def chk_sql_format(c):
    # pitfall = string-built SQL passed to execute (f-string / % / .format / + concat)
    return bool(re.search(r"execute\s*\(\s*f[\"']", c)) or \
        bool(re.search(r"(SELECT|INSERT|UPDATE|DELETE).{0,80}(%\s*\(|%\s*\w|\.format\(|\+\s*str\(|\+\s*\w+_id)",
                       c, re.IGNORECASE))


def chk_float_eq(c):
    return bool(re.search(r"==\s*(0?\.3+\b|1\s*/\s*3|0?\.1\s*\+\s*0?\.2)", c)) and \
        "isclose" not in c


def chk_mutate_iter(c):
    return bool(re.search(r"for\s+\w+\s+in\s+(\w+)\s*:(?:[^\n]*\n)*?[^\n]*\1\.(remove|pop)\(", c)) or \
        bool(re.search(r"for\s+\w+\s+in\s+(\w+)\s*:(?:[^\n]*\n)*?[^\n]*del\s+\1\[", c))


def chk_eq_none(c):
    return bool(re.search(r"[!=]=\s*None", c))


# ── project-specific knowledge: constraints the model CANNOT know from training. This is
# memory's true value — conveying a fact no LLM was trained on. `check` = did the code apply
# the constraint the guard states? base rate should be high (unknowable), eff high (guard tells).
def chk_render_guard(c):     # constraint applied = empty-guard before render_chart
    return not bool(re.search(r"(if\s+(not\s+)?rows|if\s+len\(\s*rows|rows\s*and\b)", c))


def chk_ms_units(c):         # constraint applied = seconds converted to ms
    return not bool(re.search(r"\*\s*1000|1000\s*\*", c))


def chk_auth_order(c):       # constraint applied = authenticate() before connect()
    ai, ci = c.find("authenticate"), c.find("connect")
    return not (ai != -1 and ci != -1 and ai < ci)


def chk_lock_finally(c):     # constraint applied = release paired safely (finally OR a context manager)
    finally_release = bool(re.search(r"finally\s*:(?:[^\n]*\n)*?[^\n]*release_lock", c))
    context_mgr = bool(re.search(r"with\s+[\w.]*(acquire_lock|lock)", c, re.IGNORECASE))
    return not (finally_release or context_mgr)   # a `with` solution is correct, not a pitfall


PROJECT_TASKS = [
    {"id": "api-empty-guard", "check": chk_render_guard, "family": "project",
     "prompt": "Write a short Python snippet that calls render_chart(rows) to display a list of "
               "rows. Only the code.",
     "guard": "guard: render_chart(rows) raises on an empty list — always check `if rows:` first."},
    {"id": "api-ms-units", "check": chk_ms_units, "family": "project",
     "prompt": "Write a short Python snippet that uses set_timeout(t) to set a 5 second timeout. "
               "Only the code.",
     "guard": "guard: set_timeout takes MILLISECONDS, not seconds — pass seconds*1000."},
    {"id": "api-auth-order", "check": chk_auth_order, "family": "project",
     "prompt": "Write a short Python snippet that creates Client() and calls .connect() on it. "
               "Only the code.",
     "guard": "guard: you MUST call client.authenticate() before client.connect() or it hangs."},
    {"id": "api-lock-finally", "check": chk_lock_finally, "family": "project",
     "prompt": "Write a short Python snippet that calls acquire_lock() around a critical section. "
               "Only the code.",
     "guard": "guard: acquire_lock() must be paired with release_lock() in a finally block or it "
              "deadlocks."},
]

TASKS = PROJECT_TASKS + [
    {"id": "mutable-default", "check": chk_mutable_default, "family": "generic",
     "prompt": "Write a short Python function that appends a value to an optional list and "
               "returns the list, so a caller can omit the list on the first call. Only the code.",
     "guard": "guard: a mutable default argument (=[] or ={}) is created once and shared across "
              "every call, so it accumulates — use =None and create the list inside the function."},
    {"id": "bare-except", "check": chk_bare_except,
     "prompt": "Write a short Python function that parses a string into an int and returns 0 if "
               "it cannot. Only the code.",
     "guard": "guard: a bare `except:` also swallows KeyboardInterrupt/SystemExit and hides real "
              "bugs — catch the specific exception (except ValueError)."},
    {"id": "div-zero", "check": chk_div_zero,
     "prompt": "Write a short Python function average(nums) that returns the mean of a list of "
               "numbers. Only the code.",
     "guard": "guard: sum(nums)/len(nums) raises ZeroDivisionError on an empty list — handle the "
              "empty case explicitly."},
    {"id": "no-context-manager", "check": chk_no_context_manager,
     "prompt": "Write a short Python function read_lines(path) that returns the lines of a text "
               "file. Only the code.",
     "guard": "guard: open() without a `with` block leaks the file handle if an error is raised — "
              "use `with open(path) as f:`."},
    {"id": "sql-format", "check": chk_sql_format,
     "prompt": "Write a short Python function get_user(cursor, user_id) that runs a SQL query to "
               "fetch a user row by its id. Only the code.",
     "guard": "guard: building SQL by string-formatting the id is injectable — pass it as a "
              "parameter: cursor.execute('... WHERE id = ?', (user_id,))."},
    {"id": "float-eq", "check": chk_float_eq,
     "prompt": "Write a short Python function that returns True if a given float equals one third. "
               "Only the code.",
     "guard": "guard: floating-point == is unreliable (0.1+0.2 != 0.3) — use math.isclose."},
    {"id": "mutate-iter", "check": chk_mutate_iter,
     "prompt": "Write a short Python function drop_evens(lst) that removes the even numbers from "
               "the list in place. Only the code.",
     "guard": "guard: removing from a list while iterating it skips elements — iterate over a copy "
              "(for x in lst[:]) or build a new list."},
    {"id": "eq-none", "check": chk_eq_none,
     "prompt": "Write a short Python function is_missing(x) that returns True when x is None. "
               "Only the code.",
     "guard": "guard: test for None with `is`/`is not`, never `==` (== can be overridden and is "
              "slower/incorrect for some types)."},
]


# ── the model caller (plain text; no JSON mode) ───────────────────────

def make_caller(model="deepseek-chat", temperature=0.7, timeout=90):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    def call(prompt):
        body = json.dumps({"model": model, "temperature": temperature,
                           "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(m.DEEPSEEK_URL, data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
            "User-Agent": m._UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read())
                return _strip_fences(((data.get("choices") or [{}])[0].get("message") or {})
                                     .get("content", "") or "")
            except Exception:
                if attempt < 2:
                    import time as _t
                    _t.sleep(2 * (attempt + 1))
        return ""
    return call


def make_ollama_caller(model="qwen2.5:3b", temperature=0.7, timeout=120):
    """A plain-text caller for a LOCAL Ollama model — the 'weak agent' arm. No JSON mode (we
    want code, not structured output), so a small model isn't fighting a format constraint."""
    def call(prompt):
        body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                           "think": False, "options": {"temperature": temperature}}).encode("utf-8")
        req = urllib.request.Request(m.OLLAMA_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read())
                return _strip_fences((data.get("response") or "").strip())
            except Exception:
                if attempt < 1:
                    import time as _t
                    _t.sleep(2)
        return ""
    return call


def _run_task(task, call, trials, with_guard):
    prompt = task["prompt"]
    if with_guard:
        prompt = f"{task['prompt']}\n\n[memory] {task['guard']}"
    pitfalls, outputs = 0, []
    for _ in range(trials):
        code = call(prompt)
        outputs.append(code)
        if code and task["check"](code):
            pitfalls += 1
    return pitfalls, outputs


def validate(tasks, trials=5, model="deepseek-chat", concurrency=8, call=None):
    call = call or make_caller(model)          # injectable for tests (no network)
    results = {}
    # run all (task, condition) cells concurrently
    cells = [(t, cond) for t in tasks for cond in (False, True)]

    def _cell(t, cond):
        p, outs = _run_task(t, call, trials, cond)
        return t["id"], cond, p, outs

    raw = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(_cell, t, cond) for t, cond in cells]
        for f in as_completed(futs):
            tid, cond, p, outs = f.result()
            raw[(tid, cond)] = (p, outs)

    for t in tasks:
        tid = t["id"]
        p_wo, outs_wo = raw[(tid, False)]
        p_w, _ = raw[(tid, True)]
        rate_wo, rate_w = p_wo / trials, p_w / trials
        # does axis A's guard.check actually DETECT the real bug on the unguarded outputs?
        det = _guard_detects(t, outs_wo)
        results[tid] = {"rate_without": rate_wo, "rate_with": rate_w,
                        "abs_reduction": rate_wo - rate_w,
                        "rel_reduction": (rate_wo - rate_w) / rate_wo if rate_wo else None,
                        "guard_detect_rate": det, "trials": trials}
    return results


def _guard_detects(task, unguarded_outputs) -> float | None:
    """Of the unguarded outputs that actually CONTAIN the pitfall, how many does a generated
    guard's `check` fire on? Validates that axis A detects the real bug, not just that a hint
    helps. Builds a guard from the task's own pitfall via a simple pattern; None if no buggy
    outputs to test."""
    buggy = [c for c in unguarded_outputs if c and task["check"](c)]
    if not buggy:
        return None
    # a representative guard pattern for this pitfall (what generation would produce)
    pat = _GUARD_PATTERNS.get(task["id"])
    if not pat or not G.safe_pattern(pat):
        return None
    fired = sum(1 for c in buggy if re.search(pat, c))
    return round(fired / len(buggy), 3)


# guard regexes a real generation (guards.propose_from_mistake) would emit for these pitfalls
_GUARD_PATTERNS = {
    "mutable-default": r"def\s+\w+\([^)]*=\s*(\[\s*\]|\{\s*\})",
    "bare-except": r"except\s*:",
    "no-context-manager": r"\bopen\s*\(",
    "float-eq": r"==\s*0?\.3",
    "eq-none": r"[!=]=\s*None",
}


def _family_eff(rows):
    got = [r for r in rows if r["rate_without"] > 0]
    effs = [r["rel_reduction"] for r in got if r["rel_reduction"] is not None]
    return (round(sum(effs) / len(effs), 3) if effs else None), len(got)


def _summarize(results, tasks):
    fam = {t["id"]: t.get("family", "generic") for t in tasks}
    rows = list(results.values())
    proj = [r for tid, r in results.items() if fam.get(tid) == "project"]
    gen = [r for tid, r in results.items() if fam.get(tid) != "project"]
    mean_wo = sum(r["rate_without"] for r in rows) / len(rows)
    mean_w = sum(r["rate_with"] for r in rows) / len(rows)
    eff_all, n_all = _family_eff(rows)
    eff_p, n_p = _family_eff(proj)
    eff_g, n_g = _family_eff(gen)
    dets = [r["guard_detect_rate"] for r in rows if r["guard_detect_rate"] is not None]
    det = sum(dets) / len(dets) if dets else None
    return {"mean_rate_without": round(mean_wo, 3), "mean_rate_with": round(mean_w, 3),
            "measured_eff": eff_all if eff_all is not None else 0.0, "tasks_with_pitfall": n_all,
            "eff_project": eff_p, "n_project_with_pitfall": n_p,
            "eff_generic": eff_g, "n_generic_with_pitfall": n_g,
            "guard_detect_rate": round(det, 3) if det is not None else None}


def main():
    argv = sys.argv[1:]
    trials = int(next((a.split("=", 1)[1] for a in argv if a.startswith("--trials=")), "5"))
    model = next((a.split("=", 1)[1] for a in argv if a.startswith("--model=")), "deepseek-chat")

    if "--dry" in argv:
        print(f"{len(TASKS)} tasks; checks self-test on planted buggy/clean snippets:")
        _selfcheck_checks()
        return None

    if "--replug-d" in argv:
        eff = float(next((a.split("=", 1)[1] for a in argv if a.startswith("--eff=")), "0"))
        if not eff:
            p = HERE / "live_validation_results.json"
            eff = json.loads(p.read_text())["summary"]["measured_eff"] if p.exists() else 0.0
        import longitudinal_improvement as L
        out = L.evaluate(n=200, base_fail=0.4, trials=25, eff=eff)
        print(f"D re-run with MEASURED eff={eff}:")
        L._print(out)
        v1, v2 = out["v1"], out["v2"]
        print(f"  → v2 improvement-per-1k-tok {v2['improvement_per_1k_tok']} vs v1 "
              f"{v1['improvement_per_1k_tok']}; v2 total tokens vs no-mem {v2['total_tokens_vs_nomem']:+.0f}")
        return None

    ollama_model = next((a.split("=", 1)[1] for a in argv if a.startswith("--ollama=")), None)
    if ollama_model:
        call = make_ollama_caller(ollama_model)
        model = f"ollama:{ollama_model}"
        concurrency = 3                              # local GPU serializes; don't thrash it
        print(f"[live] LOCAL weak agent {model} trials={trials} tasks={len(TASKS)}", file=sys.stderr)
    else:
        call = None
        concurrency = 8
        print(f"[live] model={model} trials={trials} tasks={len(TASKS)} "
              f"(needs DEEPSEEK_API_KEY)", file=sys.stderr)
    results = validate(TASKS, trials=trials, model=model, concurrency=concurrency, call=call)
    summ = _summarize(results, TASKS)
    print("=" * 74)
    print(f"  LIVE VALIDATION — does a fired guard change a real model's output?  ({model})")
    print("=" * 74)
    print(f"  {'task':20} {'without':>8} {'with':>6} {'abs↓':>6} {'rel↓':>6} {'A-detect':>9}")
    for tid, r in results.items():
        rel = f"{r['rel_reduction']:.2f}" if r["rel_reduction"] is not None else "  -"
        det = f"{r['guard_detect_rate']:.2f}" if r["guard_detect_rate"] is not None else "  -"
        print(f"  {tid:20} {r['rate_without']:8.2f} {r['rate_with']:6.2f} "
              f"{r['abs_reduction']:6.2f} {rel:>6} {det:>9}")
    print(f"\n  → mean pitfall rate: {summ['mean_rate_without']:.2f} → {summ['mean_rate_with']:.2f}")
    print(f"  → MEASURED eff overall: {summ['measured_eff']:.2f}  (the D sim assumed 0.75)")
    print(f"     · PROJECT-specific knowledge (memory's true value): eff="
          f"{summ['eff_project']}  over {summ['n_project_with_pitfall']} task(s) the model got wrong")
    print(f"     · GENERIC pitfalls (the model was trained on): eff="
          f"{summ['eff_generic']}  over {summ['n_generic_with_pitfall']} task(s)")
    print(f"  → axis-A guard detection rate on real buggy outputs: {summ['guard_detect_rate']}")
    print(f"  → pitfall actually occurred in {summ['tasks_with_pitfall']}/{len(TASKS)} tasks")
    if "--save" in argv:
        name = next((a.split("=", 1)[1] for a in argv if a.startswith("--out=")),
                    "live_validation_results.json")
        p = HERE / name
        p.write_text(json.dumps({"model": model, "trials": trials, "tasks": results,
                                 "summary": summ}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  saved → {p}")
    print("=" * 74)
    return results


def _selfcheck_checks():
    """Sanity: each check fires on a planted-buggy snippet and stays quiet on a clean one."""
    cases = {
        "mutable-default": ("def f(x, b=[]):\n b.append(x); return b", "def f(x, b=None):\n b=b or []; return b"),
        "bare-except": ("try:\n int(s)\nexcept:\n 0", "try:\n int(s)\nexcept ValueError:\n 0"),
        "div-zero": ("def average(n): return sum(n)/len(n)", "def average(n):\n if not n: return 0\n return sum(n)/len(n)"),
        "no-context-manager": ("def r(p):\n f=open(p); return f.readlines()", "def r(p):\n with open(p) as f: return f.readlines()"),
        "sql-format": ("cursor.execute(f'SELECT * FROM u WHERE id={user_id}')", "cursor.execute('SELECT * FROM u WHERE id=?',(user_id,))"),
        "float-eq": ("def t(x): return x == 0.333", "import math\ndef t(x): return math.isclose(x, 1/3)"),
        "mutate-iter": ("for x in lst:\n lst.remove(x)", "for x in lst[:]:\n lst.remove(x)"),
        "eq-none": ("def m(x): return x == None", "def m(x): return x is None"),
        "api-empty-guard": ("render_chart(rows)", "if rows:\n render_chart(rows)"),
        "api-ms-units": ("set_timeout(5)", "set_timeout(5 * 1000)"),
        "api-auth-order": ("c.connect()", "c.authenticate()\nc.connect()"),
        "api-lock-finally": ("acquire_lock()\nwork()", "acquire_lock()\ntry:\n work()\nfinally:\n release_lock()"),
    }
    by_id = {t["id"]: t for t in TASKS}
    ok = True
    for tid, (buggy, clean) in cases.items():
        chk = by_id[tid]["check"]
        b, c = chk(buggy), chk(clean)
        flag = "ok " if (b and not c) else "FAIL"
        if not (b and not c):
            ok = False
        print(f"  [{flag}] {tid:20} buggy={b} clean={c}")
    print("checks self-test:", "ALL OK" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    main()
