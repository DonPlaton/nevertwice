"""Phase E: does an agent fix a stale caller better with the mechanism than without?

`ENDTOEND_E.md` is the design, written before this ran. **E4 is G-C**, the gate that decides
the push.

Every number here is **in sample**: `HELDOUT_H3_BLOCKED.md` records that DNS resolves
nothing on this machine, so the held-out corpus does not exist and the tasks come from the
eight repositories the mechanisms were tuned on.

Written after the H1 freeze and deliberately outside it -- this file is a stand, not a
mechanism, and its hash is not among the 29 in `heldout_seal.json`.

    python research/invariants_lab/measure_endtoend.py --probe        # E2, the base rate
    python research/invariants_lab/measure_endtoend.py --trials N     # E4
    python research/invariants_lab/measure_endtoend.py --print
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abstain  # noqa: E402
import binding  # noqa: E402
import sigscan  # noqa: E402
import blast_radius_deleted as br  # noqa: E402
from corpora import repos_for  # noqa: E402
from corpusio import BlobReader, progress  # noqa: E402

MUTANTS = Path(__file__).with_name("mutants.json")
ARTIFACT = Path(__file__).with_name("endtoend_e.json")

SEED = 20260828
POLICY = "decidable-only"     # the policy F1's declared rule named
MAX_LINES = 400               # ENDTOEND_E.md section 2
MIN_SITES = 2
BASE_RATE_FLOOR = 0.10        # section 4
ASSUMED_FIX_RATE = 0.60
MAX_TRIALS = 600
HARM_CEILING = 0.05           # section 6
ALPHA = 0.05

SYSTEM = ("You are a careful Python engineer maintaining a large codebase. Return the "
          "complete corrected file in one ```python code block and nothing else. Do not "
          "delete code you were not asked to change.")

OLLAMA = "http://localhost:11434/api/generate"


# ---------------------------------------------------------------------------
# the task pool
# ---------------------------------------------------------------------------


def _signature_of(source: str, qualname: str) -> str | None:
    """`name(a, b, *, c=1)` as a caller sees it, rebuilt from the extractor's params."""
    d = sigscan.scan_defs(source).get(qualname)
    if d is None:
        return None
    parts = []
    for prm in d.params:
        kind = getattr(prm, "kind", "pos")
        name = getattr(prm, "name", "?")
        if kind == "vararg":
            parts.append("*" + name)
        elif kind == "kwarg":
            parts.append("**" + name)
        elif getattr(prm, "has_default", False):
            parts.append(name + "=...")
        else:
            parts.append(name)
    return d.short + "(" + ", ".join(parts) + ")"


def build_pool() -> list[dict]:
    """Every task satisfying ENDTOEND_E.md section 2, one per source commit, seeded."""
    mutants = [m for m in json.loads(MUTANTS.read_text(encoding="utf-8"))["mutants"]
               if m["confirmed"] and m["breakage"] == "arity"]
    repos = {r.name: r for r in repos_for("dev")}
    readers: dict[str, BlobReader] = {}
    by_commit: dict[tuple[str, str], dict] = {}
    try:
        for m in mutants:
            repo = repos.get(m["repo"])
            if repo is None:
                continue
            blobs = readers.setdefault(m["repo"], BlobReader(repo))
            stale = blobs.read(m["parent"], m["caller_path"])     # the reverted caller
            fixed = blobs.read(m["sha"], m["caller_path"])        # what the maintainer did
            new_def_src = blobs.read(m["sha"], m["defining_path"])
            old_def_src = blobs.read(m["parent"], m["defining_path"])
            if stale is None or fixed is None or new_def_src is None or old_def_src is None:
                continue
            if stale.count("\n") + 1 > MAX_LINES:
                continue
            sites = [s for s in sigscan.scan_refs(stale, {m["symbol"]}) if s.is_call]
            if len(sites) < MIN_SITES:
                continue
            key = (m["repo"], m["sha"])
            if key in by_commit:
                continue
            by_commit[key] = {
                "mid": m["mid"], "repo": m["repo"], "sha": m["sha"],
                "qualname": m["qualname"], "symbol": m["symbol"],
                "caller_path": m["caller_path"], "defining_path": m["defining_path"],
                "stale": stale, "fixed": fixed,
                "old_signature": _signature_of(old_def_src, m["qualname"]) or "",
                "new_signature": _signature_of(new_def_src, m["qualname"]) or "",
                "new_def_source": new_def_src,
                "old_def_source": old_def_src,
                "n_sites": len(sites),
            }
    finally:
        for r in readers.values():
            r.close()
    pool = [by_commit[k] for k in sorted(by_commit)]
    rng = random.Random(SEED)
    rng.shuffle(pool)
    return pool


# ---------------------------------------------------------------------------
# the judge -- binding simulation on the agent's OUTPUT
# ---------------------------------------------------------------------------


def judge(returned: str, task: dict) -> tuple[bool | None, str]:
    """(passes, why). None when the answer cannot be graded at all.

    ENDTOEND_E.md section 3: a file that does not parse, or that lost its callables, is
    **unusable** rather than a failure -- deleting the call site is not a fix.
    """
    try:
        tree = ast.parse(returned)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return None, "does not parse: " + type(exc).__name__
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               for n in ast.walk(tree)):
        return None, "no definitions left"
    was = set(sigscan.scan_defs(task["stale"]))
    now = set(sigscan.scan_defs(returned))
    if was - now:
        return None, "lost " + str(len(was - now)) + " definition(s)"

    new_def = sigscan.scan_defs(task["new_def_source"]).get(task["qualname"])
    if new_def is None or new_def.kind != "func":
        return None, "the changed symbol is not resolvable in the new tree"
    bad = []
    for site in sigscan.scan_refs(returned, {task["symbol"]}):
        if not site.is_call or site.has_star:
            continue
        why = binding.call_fails(site, new_def)
        if why is not None:
            bad.append(f"line {site.lineno}: {why}")
    return (not bad), ("; ".join(bad[:3]) if bad else "every call binds")


# ---------------------------------------------------------------------------
# the mechanism, and the two prompts
# ---------------------------------------------------------------------------


def mechanism_findings(task: dict, source: str) -> list[str]:
    """What `blast_radius` under `decidable-only` says about this tree."""
    before = {task["defining_path"]: source_of_parent(task),
              task["caller_path"]: source}
    after = {task["defining_path"]: task["new_def_source"],
             task["caller_path"]: source}
    verdict = br.check_sources(before, after, scan=after)
    kept = abstain.decide(verdict, after, POLICY)
    return [f"{f.path}:{f.lineno}  {f.qualname} -- its contract changed and this call "
            f"does not fit the new one" for f in kept]


def source_of_parent(task: dict) -> str:
    """The defining file as it was *before* the contract change."""
    return task["old_def_source"]


def build_prompt(task: dict, source: str, findings: list[str]) -> str:
    head = (
        f"A contract in `{task['defining_path']}` changed in this commit:\n\n"
        f"    before:  {task['old_signature']}\n"
        f"    after:   {task['new_signature']}\n\n"
        f"Here is `{task['caller_path']}` as it stands. Some call sites in it were "
        f"written against the OLD contract and will now raise TypeError. Return the "
        f"complete corrected file.\n"
    )
    if findings:
        head += ("\nA project invariant checked this diff and reports:\n"
                 + "\n".join("  " + f for f in findings) + "\n")
    return head + "\n```python\n" + source + "\n```\n"


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (blocks[0] if blocks else text).strip()


def ask(model: str, prompt: str, *, timeout: int = 600) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "system": SYSTEM, "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 6000},
    }).encode()
    request = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "")


# ---------------------------------------------------------------------------


def one_task(model: str, task: dict, *, harm_arm: bool) -> dict:
    """One trial. `harm_arm` runs the *correct* tree, where nothing needs fixing."""
    source = task["fixed"] if harm_arm else task["stale"]
    findings = mechanism_findings(task, source)
    row = {"mid": task["mid"], "repo": task["repo"], "symbol": task["symbol"],
           "harm_arm": harm_arm, "n_sites": task["n_sites"],
           "fired": bool(findings), "n_findings": len(findings)}

    if harm_arm:
        # Nothing is wrong. The only question is what the mechanism's firing costs.
        row["off_changed"] = False
        row["off"] = True
        if not findings:
            row["on_changed"], row["on"] = False, True
            return row
        answer = extract_code(ask(model, build_prompt(task, source, findings)))
        ok, why = judge(answer, {**task, "stale": source})
        row["on_unusable"] = None if ok is not None else why
        row["on_changed"] = answer.strip() != source.strip()
        row["on"] = ok
        row["on_why"] = why
        return row

    for arm, given in (("off", []), ("on", findings)):
        answer = extract_code(ask(model, build_prompt(task, source, given)))
        ok, why = judge(answer, task)
        row[arm] = ok
        row[arm + "_why"] = why
        row[arm + "_unusable"] = None if ok is not None else why
        row[arm + "_changed"] = answer.strip() != source.strip()
    return row


def run(model: str, trials: int, harm_trials: int) -> dict:
    pool = build_pool()
    progress(f"task pool: {len(pool)} tasks satisfying the declared filter")
    rows: list[dict] = []
    for i, task in enumerate(pool[:trials]):
        progress(f"  benefit {i + 1}/{min(trials, len(pool))}  "
                 f"{task['repo']}:{task['symbol']}")
        try:
            rows.append(one_task(model, task, harm_arm=False))
        except Exception as exc:  # noqa: BLE001 - a dead model must not lose the run
            rows.append({"mid": task["mid"], "repo": task["repo"], "harm_arm": False,
                         "off": None, "on": None,
                         "off_unusable": "harness: " + type(exc).__name__,
                         "on_unusable": "harness: " + type(exc).__name__})
    harm: list[dict] = []
    for i, task in enumerate(pool[:harm_trials]):
        progress(f"  harm {i + 1}/{min(harm_trials, len(pool))}  "
                 f"{task['repo']}:{task['symbol']}")
        try:
            harm.append(one_task(model, task, harm_arm=True))
        except Exception as exc:  # noqa: BLE001
            harm.append({"mid": task["mid"], "repo": task["repo"], "harm_arm": True,
                         "on": None, "on_unusable": "harness: " + type(exc).__name__})
    return {"model": model, "pool": len(pool), "rows": rows, "harm": harm}


def trials_needed(base: float, fix: float = ASSUMED_FIX_RATE) -> float:
    denom = base * fix
    return float("inf") if denom <= 0 else 6.0 / denom


def summarise(raw: dict, stage: str) -> dict:
    rows, harm = raw["rows"], raw["harm"]
    usable = [r for r in rows if r.get("off") is not None and r.get("on") is not None]
    off_fail = sum(1 for r in usable if not r["off"])
    n = len(usable)
    base = off_fail / n if n else float("nan")
    lo, hi = (proportion_confint(off_fail, n, alpha=ALPHA, method="wilson")
              if n else (float("nan"), float("nan")))

    fixed = sum(1 for r in usable if not r["off"] and r["on"])
    broken = sum(1 for r in usable if r["off"] and not r["on"])
    ignored = sum(1 for r in usable if r.get("fired") and r["off"] == r["on"])
    m = fixed + broken
    mcnemar = float(stats.binomtest(fixed, m, 0.5).pvalue) if m else float("nan")

    harm_usable = [h for h in harm if h.get("on") is not None]
    false_flag_repair = sum(1 for h in harm_usable
                            if h.get("fired") and h.get("on_changed"))
    false_flag_broke = sum(1 for h in harm_usable
                           if h.get("fired") and h.get("on_changed") and not h["on"])
    harm_n = len(harm_usable) + n
    harm_count = broken + false_flag_repair
    harm_rate = harm_count / harm_n if harm_n else float("nan")
    harm_p = (float(stats.binomtest(harm_count, harm_n, HARM_CEILING,
                                    alternative="greater").pvalue)
              if harm_n else float("nan"))

    need = trials_needed(base)
    reasons: dict[str, int] = {}
    for r in rows:
        for key in ("off_unusable", "on_unusable"):
            if r.get(key):
                reasons[str(r[key])[:44]] = reasons.get(str(r[key])[:44], 0) + 1

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "E", "stage": stage, "model": raw["model"],
        "corpus": "corpus_dev", "in_sample": True,
        "policy": POLICY,
        "pool": raw["pool"],
        "benefit": {
            "trials": len(rows), "usable": n, "unusable": len(rows) - n,
            "unusable_reasons": reasons,
            "base_rate": {"off_failures": off_fail, "n": n, "point": base,
                          "ci": [float(lo), float(hi)]},
            "base_rate_floor": BASE_RATE_FLOOR,
            "stand_can_resolve": base >= BASE_RATE_FLOOR,
            "trials_needed": None if need == float("inf") else int(need + 0.999),
            "trial_ceiling": MAX_TRIALS,
            "four_way": {"fixed": fixed, "broken": broken, "ignored": ignored,
                         "discordant": m},
            "mcnemar_p": mcnemar,
            "on_failures": sum(1 for r in usable if not r["on"]),
            "fired_on": sum(1 for r in rows if r.get("fired")),
        },
        "harm": {
            "trials": len(harm), "usable": len(harm_usable),
            "fired_on_correct_code": sum(1 for h in harm_usable if h.get("fired")),
            "false_flag_repair": false_flag_repair,
            "false_flag_repair_that_broke_it": false_flag_broke,
            "broken_on_benefit_arm": broken,
            "harm_count": harm_count, "harm_n": harm_n, "harm_rate": harm_rate,
            "ceiling": HARM_CEILING, "p_vs_ceiling": harm_p,
        },
        "G_C": {
            "net_benefit_passes": bool(m and fixed > broken and mcnemar < ALPHA),
            "harm_bound_passes": bool(harm_n and harm_rate <= HARM_CEILING),
        },
        "rows": rows, "harm_rows": harm,
    }


def _print(d: dict) -> None:
    b, h = d["benefit"], d["harm"]
    print(f"Phase E -- {d['stage']} -- {d['model']} -- {d['corpus']}, IN SAMPLE")
    print(f"  policy {d['policy']}   task pool {d['pool']}")
    print()
    print(f"  benefit arm: {b['trials']} trials, {b['usable']} usable, "
          f"{b['unusable']} unusable")
    for why, k in sorted(b["unusable_reasons"].items(), key=lambda x: -x[1])[:6]:
        print(f"     {k:3d}  {why}")
    r = b["base_rate"]
    if b["usable"]:
        print(f"  BASE RATE (off arm fails) {r['point']:.3f} "
              f"[{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]  ({r['off_failures']}/{r['n']})")
        print(f"     floor {b['base_rate_floor']} -- "
              f"{'the stand can resolve something' if b['stand_can_resolve'] else 'TOO LOW, rebuild with harder tasks'}")
        need = b["trials_needed"]
        print(f"     trials needed: {need if need is not None else 'INFINITE'} "
              f"(ceiling {b['trial_ceiling']})")
    f = b["four_way"]
    print(f"  four-way: fixed {f['fixed']}, broken {f['broken']}, "
          f"ignored {f['ignored']}, discordant {f['discordant']}")
    if f["discordant"]:
        print(f"     exact McNemar p = {b['mcnemar_p']:.4g}")
    print(f"  the mechanism fired on {b['fired_on']} of {b['trials']}")
    print()
    print(f"  harm arm (correct trees): {h['trials']} trials, {h['usable']} usable")
    print(f"     fired on correct code        {h['fired_on_correct_code']}")
    print(f"     false-flag repair            {h['false_flag_repair']}")
    print(f"     of which broke it            {h['false_flag_repair_that_broke_it']}")
    if h["harm_n"]:
        print(f"     HARM {h['harm_count']}/{h['harm_n']} = {h['harm_rate']:.3f} "
              f"(ceiling {h['ceiling']})")
    print()
    g = d["G_C"]
    print(f"  G-C net benefit: {'PASS' if g['net_benefit_passes'] else 'not met'}   "
          f"harm bound: {'PASS' if g['harm_bound_passes'] else 'not met'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="qwen3-coder:30b")
    ap.add_argument("--probe", action="store_true",
                    help="E2: the base rate, on a small number of tasks")
    ap.add_argument("--trials", type=int, default=0)
    ap.add_argument("--harm-trials", type=int, default=0)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    trials = 12 if args.probe else args.trials
    harm = 6 if args.probe else (args.harm_trials or trials)
    if not trials:
        ap.error("pass --probe or --trials N")
    data = summarise(run(args.model, trials, harm),
                     "probe" if args.probe else f"stand ({trials} trials)")
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
