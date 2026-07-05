#!/usr/bin/env python3
"""RESEARCH - axis D: the longitudinal improvement-per-token benchmark.

The axis no competitor measures. LongMemEval asks "can you recall a fact"; we showed that is
reader-bound and commoditizing (`QA_ACCURACY.md`). The question that matters for an *agent's*
memory is different:

    Does the agent, because of memory, get measurably better over a SERIES of related tasks -
    and does the memory cost fewer tokens than it saves?

This harness measures exactly that, and it does so by isolating the MEMORY mechanism from the
reader LLM's raw ability (the confound), which is the whole point: we hold the "agent" as a
controlled component with a base error rate and measure what each memory *design* adds on top.

## The model (faithful, not toy)

A **task family** is a seeded sequence of N related tasks. Each task carries a *pitfall* (a
mistake class), and pitfalls **recur** across the series - exactly like real coding, where the
same class of bug reappears. Three arms see the identical stream and the identical knowledge;
they differ only in HOW and WHEN memory delivers it, and at what token cost:

  * **no-mem**        - hits every pitfall independently at the base rate; repeats mistakes.
  * **v1 always-inject** - recalls notes into context EVERY turn (the field's design): learns
                        each pitfall, but pays an injection tax on every task, pitfall or not.
  * **v2 active (guards)** - 0 context tokens until a guard fires; a pitfall becomes a guard
                        after it bites once, and the guard then fires only on the tasks that
                        actually risk it. Pays only when it acts. Guards can misfire
                        (false-positive) and self-retire (Popperian), which we cost honestly.

Both memory arms apply the **same** knowledge effect (`eff`) when the lesson is present - the
comparison is delivery economics, not who knows more. Token accounting is explicit and
overridable; the default costs are conservative and matched to v1's measured injection size.

    python research/longitudinal_improvement.py                 # headline run + curve
    python research/longitudinal_improvement.py --sweep --save   # sensitivity + JSON
    python research/longitudinal_improvement.py --n 400 --seed 7

Deterministic (seeded). Research dep: none for the sim; matplotlib optional for the figure.
A --live mode (a handful of real tasks through an LLM) grounds the sim's one assumption - that
a fired guard actually changes the model's output - and is a separate, opt-in validation.
"""
import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── honest, overridable token costs (all in tokens) ───────────────────
C_TASK = 500        # attempting a task (same for every arm; not "memory overhead")
C_FAIL_REDO = 800   # extra tokens to detect + fix a hit pitfall (the cost of NOT preventing)
C_INJECT_V1 = 400   # v1 recalls ~this many note-tokens into context EVERY task (v1 measured ~644 at start)
C_GUARD_FIRE = 25   # a v2 guard warning is one line, and only when it fires
EFF = 0.75          # knowledge effect: seeing the lesson/guard cuts the pitfall's fail prob by this


def make_family(n, seed, n_pitfalls=6, pitfall_density=0.55):
    """A seeded task series. Each task either carries a pitfall (id 0..n_pitfalls-1) or is
    pitfall-free (None). Pitfalls recur (a small set reused across N), so a memory can learn
    each once and reap it many times."""
    rng = random.Random(seed)
    tasks = []
    for _ in range(n):
        if rng.random() < pitfall_density:
            tasks.append(rng.randrange(n_pitfalls))
        else:
            tasks.append(None)
    return tasks


def _bernoulli(rng, p):
    return 1 if rng.random() < p else 0


def run_arm(arm, tasks, base_fail, seed, *, guard_fp_rate=0.03, m_retire=3,
            eff=EFF, costs=None):
    """Simulate one arm over the task series. Returns
    {errors, mem_tokens, redo_tokens, total_tokens, guard_fires}. `arm` ∈
    {'nomem','v1','v2'}. Same RNG seed across arms → identical pitfall *draws*, so the arms
    are compared on the same luck (paired), isolating the memory design."""
    c = costs or {}
    c_task = c.get("task", C_TASK); c_redo = c.get("redo", C_FAIL_REDO)
    c_inj = c.get("inject", C_INJECT_V1); c_fire = c.get("fire", C_GUARD_FIRE)
    rng = random.Random(seed)                       # per-arm draw stream (seeded identically)
    fp_rng = random.Random(seed + 1)                # separate stream for spurious v2 fires
    seen = set()                                    # pitfalls that have bitten once (→ learnable)
    guard_status = {}                               # pitfall -> 'advisory'/'blocking'/'retired'
    guard_fp = {}                                   # pitfall -> false-positive count
    errors = mem_tokens = redo_tokens = guard_fires = 0

    for pit in tasks:
        # spurious v2 false-positive fire on THIS task (a guard firing where it shouldn't).
        if arm == "v2":
            live_guards = [p for p, s in guard_status.items() if s != "retired"]
            if live_guards and fp_rng.random() < guard_fp_rate:
                gp = live_guards[fp_rng.randrange(len(live_guards))]
                mem_tokens += c_fire; guard_fires += 1        # paid a token cost for a wrong fire
                guard_fp[gp] = guard_fp.get(gp, 0) + 1
                if guard_fp[gp] >= m_retire:                  # Popperian self-retirement
                    guard_status[gp] = {"blocking": "advisory", "advisory": "retired"}.get(
                        guard_status[gp], "retired")
                    guard_fp[gp] = 0

        if pit is None:
            continue                                # pitfall-free task: nothing to prevent

        lesson_present = pit in seen                # the knowledge exists only after it bit once
        fail_p = base_fail

        if arm == "nomem":
            pass                                     # never learns
        elif arm == "v1":
            mem_tokens += c_inj                      # v1 injects on pitfall tasks here; the
            if lesson_present:                       # pitfall-free tasks are charged after the loop
                fail_p = base_fail * (1 - eff)       # (v1's tax is unconditional - every task)
        elif arm == "v2":
            st = guard_status.get(pit)
            if st and st != "retired":               # a live guard fires only on a real pitfall task
                mem_tokens += c_fire; guard_fires += 1
                fail_p = base_fail * (1 - eff)

        fail = _bernoulli(rng, fail_p)
        errors += fail
        redo_tokens += fail * c_redo
        if fail and not lesson_present:
            seen.add(pit)                            # first bite creates the lesson…
            if arm == "v2":
                guard_status[pit] = "advisory"       # …and, for v2, an advisory guard

    # v1 injects on EVERY task, not only pitfall tasks - charge the pitfall-free ones too.
    if arm == "v1":
        mem_tokens += c_inj * sum(1 for p in tasks if p is None)

    total = c_task * len(tasks) + mem_tokens + redo_tokens
    return {"errors": errors, "mem_tokens": mem_tokens, "redo_tokens": redo_tokens,
            "total_tokens": total, "guard_fires": guard_fires}


def evaluate(n=200, seed=1, base_fail=0.4, trials=20, **kw):
    """Average the three arms over `trials` seeds. Returns per-arm means plus the headline
    metrics: errors prevented vs no-mem, memory tokens spent, and improvement-per-1k-tokens."""
    arms = ("nomem", "v1", "v2")
    agg = {a: {"errors": [], "mem_tokens": [], "total_tokens": [], "guard_fires": []} for a in arms}
    for t in range(trials):
        tasks = make_family(n, seed + t)
        for a in arms:
            r = run_arm(a, tasks, base_fail, seed + t, **kw)
            for k in agg[a]:
                agg[a][k].append(r[k])
    out = {}
    for a in arms:
        out[a] = {k: statistics.fmean(v) for k, v in agg[a].items()}
    base_err = out["nomem"]["errors"]
    for a in ("v1", "v2"):
        prevented = base_err - out[a]["errors"]
        mem = out[a]["mem_tokens"] or 1e-9
        out[a]["errors_prevented"] = prevented
        out[a]["improvement_per_1k_tok"] = round(prevented / (mem / 1000), 3)
        out[a]["total_tokens_vs_nomem"] = round(out[a]["total_tokens"] - out["nomem"]["total_tokens"], 1)
    return out


def _print(out, title=""):
    if title:
        print(title)
    print(f"  {'arm':6} {'errors':>8} {'mem_tok':>9} {'total_tok':>10} {'prevented':>10} {'impr/1k':>9}")
    for a in ("nomem", "v1", "v2"):
        r = out[a]
        prev = r.get("errors_prevented", 0)
        ipt = r.get("improvement_per_1k_tok", 0)
        print(f"  {a:6} {r['errors']:8.1f} {r['mem_tokens']:9.0f} {r['total_tokens']:10.0f} "
              f"{prev:10.1f} {ipt:9.2f}")


def sensitivity(save=False):
    """Sweep base_fail and guard_fp_rate - the honesty check: where does v2 win, and where
    does it NOT? (A benchmark that only ever flatters the home team is worthless.)"""
    rows = []
    print("\nSENSITIVITY - v2 improvement-per-1k-tok vs v1, across regimes")
    print(f"  {'base_fail':>9} {'guard_fp':>9} {'v1 i/1k':>8} {'v2 i/1k':>8} {'v2 tok<v1?':>10} {'winner':>7}")
    for bf in (0.2, 0.4, 0.6):
        for fp in (0.0, 0.05, 0.15, 0.3):
            out = evaluate(n=200, base_fail=bf, trials=25, guard_fp_rate=fp)
            v1i, v2i = out["v1"]["improvement_per_1k_tok"], out["v2"]["improvement_per_1k_tok"]
            v2_cheaper = out["v2"]["total_tokens"] < out["v1"]["total_tokens"]
            winner = "v2" if v2i >= v1i else "v1"
            rows.append({"base_fail": bf, "guard_fp": fp, "v1_ipt": v1i, "v2_ipt": v2i,
                         "v2_total_lt_v1": v2_cheaper, "winner": winner})
            print(f"  {bf:9.2f} {fp:9.2f} {v1i:8.2f} {v2i:8.2f} {str(v2_cheaper):>10} {winner:>7}")
    return rows


def main():
    argv = sys.argv[1:]
    opt = lambda n, d: type(d)(next((a.split("=", 1)[1] for a in argv
                                     if a.startswith(f"--{n}=")), d))
    n = opt("n", 200); seed = opt("seed", 1); base_fail = opt("base_fail", 0.4)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    out = evaluate(n=n, seed=seed, base_fail=base_fail, trials=25)
    print("=" * 78)
    print(f"  LONGITUDINAL IMPROVEMENT-PER-TOKEN  (N={n} tasks, base_fail={base_fail}, 25 trials)")
    print("=" * 78)
    _print(out)
    v1, v2 = out["v1"], out["v2"]
    print(f"\n  → both memory arms prevent ~{v2['errors_prevented']:.0f} errors vs no-memory.")
    print(f"  → v2 spends {v2['mem_tokens']:.0f} memory-tokens vs v1's {v1['mem_tokens']:.0f} "
          f"({v1['mem_tokens']/max(1,v2['mem_tokens']):.1f}× less) for the same knowledge.")
    print(f"  → improvement-per-1k-tokens: v2 {v2['improvement_per_1k_tok']:.2f}  "
          f"vs v1 {v1['improvement_per_1k_tok']:.2f}  "
          f"({v2['improvement_per_1k_tok']/max(0.01,v1['improvement_per_1k_tok']):.1f}× better).")
    print(f"  → total tokens vs no-mem: v2 {v2['total_tokens_vs_nomem']:+.0f}, "
          f"v1 {v1['total_tokens_vs_nomem']:+.0f}  (negative = net SAVING, incl. prevented redo).")

    rows = sensitivity() if "--sweep" in argv else None

    if "--save" in argv:
        res = {"params": {"n": n, "base_fail": base_fail, "trials": 25,
                          "costs": {"task": C_TASK, "redo": C_FAIL_REDO,
                                    "inject_v1": C_INJECT_V1, "guard_fire": C_GUARD_FIRE},
                          "eff": EFF},
               "arms": out, "sensitivity": rows}
        p = HERE / "longitudinal_results.json"
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
    print("=" * 78)
    return out


if __name__ == "__main__":
    main()
