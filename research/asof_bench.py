#!/usr/bin/env python3
"""As-of recall: does the memory answer for a DATE, not only for now? (ledger I6)

The supersession bench asks the question a coding agent asks most - what is true now - and
scores a memory on not handing back the retracted fact. This bench asks the second question a
memory with a history can answer: what did we believe on that day? The same sixty supersession
cases are ingested with dates - the first session on one date, the replacing session two
months later - and each case is queried twice through the public API: once for a day between
the two sessions, when the first fact held, and once for a day after the second, when the
replacement holds. A case is correct only when BOTH answers are right: the old fact for the
old day and the new fact for the new day.

Arms:
* `nevertwice` - `api.capture_session(date=...)` then `api.as_of(query, date)`, which walks
  live and retired notes by their belief interval and ranks them by the query; no LLM at query
  time, no embedder for a retired note.
* `naive` - the same append-only markdown store the supersession bench uses as its floor; it
  has no notion of a date, so both queries return the same ranking (at most one of the two can
  be right, and which one is the ranker's accident).
* `mem0` - recorded as blocked: Mem0's memories carry the wall-clock time of the `add` call
  and its search has no as-of filter, so a benchmark that places facts in the past cannot be
  run against it without patching the product. A blocker is a record, not a zero.

Gate, written first (ledger I6): both-correct >= 0.80 on the 60 cases; over-retraction of the
supersession bench unchanged (this bench writes no new mechanism into the write path). The
extraction model is the supersession stand's; two runs are pooled the same way.

    python research/asof_bench.py --arms nevertwice,naive --out run1.json
    python research/asof_bench.py --limit 5            # smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - must precede any nevertwice import

sandbox_guard.isolate(prefix="nevertwice_asof_")
sys.path.insert(0, str(HERE))
import supersession_bench as sb  # noqa: E402 - the cases, the markers, the naive floor

DATASET = HERE / "data" / "supersession_v1.json"
DAY_FIRST, DAY_BETWEEN, DAY_SECOND, DAY_AFTER = "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"


def _items(hits: list[dict]) -> list[str]:
    return [" ".join(str(h.get(f) or "") for f in ("title", "description")) for h in hits]


def _row(case: dict, old_items: list[str], new_items: list[str]) -> dict:
    old_ok = sb._hit(case["superseded"], " ".join(old_items)) and not sb._hit(case["current"], " ".join(old_items))
    new_ok = sb._hit(case["current"], " ".join(new_items))
    return {"id": case["id"], "shape": case["shape"], "old_day_correct": bool(old_ok),
            "new_day_correct": bool(new_ok), "both_correct": bool(old_ok and new_ok),
            "old_items": len(old_items), "new_items": len(new_items)}


def run_nevertwice(cases: list[dict], k: int, runs: int = 1) -> dict:
    """`runs` > 1 repeats the whole ingest under fresh project names and pools the case-runs:
    the extraction model is not deterministic at temperature 0 (the supersession bench pools
    two runs for the same reason), so one run of this stand is not a result either."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = sb.LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", sb.EMBED_MODEL)
    from nevertwice import api                                    # noqa: PLC0415
    rows, t0 = [], time.time()
    for run, case_i in ((r, i) for r in range(runs) for i in range(len(cases))):
        case = cases[case_i]
        i = case_i
        project = f"asof{run}{i:03d}"
        try:
            api.capture_session("\n".join(case["sessions"][0]), project=project,
                                session_id=f"{project}-s0", trigger="ingest", date=DAY_FIRST)
            api.capture_session("\n".join(case["sessions"][1]), project=project,
                                session_id=f"{project}-s1", trigger="ingest", date=DAY_SECOND)
        except Exception as e:                                    # noqa: BLE001 - reported per case
            rows.append({"id": case["id"], "shape": case["shape"], "old_day_correct": False,
                         "new_day_correct": False, "both_correct": False,
                         "error": f"{type(e).__name__}: {e}"})
            continue
        old = _items(api.as_of(case["query"], DAY_BETWEEN, project, k=k))
        new = _items(api.as_of(case["query"], DAY_AFTER, project, k=k))
        rows.append({**_row(case, old, new), "run": run})
        print(f"  [run {run + 1}/{runs} {i + 1}/{len(cases)}] {case['id']}  old {rows[-1]['old_day_correct']}  "
              f"new {rows[-1]['new_day_correct']}", flush=True)
    out = {"rows": rows, **score(rows), "runs": runs, "seconds": round(time.time() - t0, 1),
           "config": f"ollama {sb.LLM} + {sb.EMBED_MODEL}, k={k}, days {DAY_FIRST}/{DAY_SECOND}"}
    if runs > 1:
        out["per_run"] = [score([r for r in rows if r.get("run") == run])["both_correct_rate"] for run in range(runs)]
    return out


def run_naive(cases: list[dict], k: int) -> dict:
    """The floor has no dates: one ranking answers both days."""
    rows = []
    for case in cases:
        items = _naive_items(case, k)
        rows.append(_row(case, items, items))
    return {"rows": rows, **score(rows), "seconds": 0.0,
            "config": "append-only markdown + BM25-style IDF overlap, no dates, k=%d" % k}


def _naive_items(case: dict, k: int) -> list[str]:
    docs = [ln for sess in case["sessions"] for ln in sess]
    q = set(sb._norm(case["query"]).split())
    scored = sorted(docs, key=lambda d: -len(q & set(sb._norm(d).split())))
    return scored[:k]


def score(rows: list[dict]) -> dict:
    sup = [r for r in rows if r["shape"] != "control"]
    n = len(sup) or 1
    both = sum(1 for r in sup if r["both_correct"])
    old = sum(1 for r in sup if r["old_day_correct"])
    new = sum(1 for r in sup if r["new_day_correct"])
    return {"n_cases": len(sup), "both_correct_rate": round(both / n, 4),
            "both_correct_ci": [round(x, 4) for x in sb.wilson(both, n)],
            "old_day_rate": round(old / n, 4), "new_day_rate": round(new / n, 4),
            "errors": sum(1 for r in sup if r.get("error"))}


ARMS = {"nevertwice": run_nevertwice, "naive": run_naive}
RUNS = 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--arms", default="nevertwice,naive")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", type=int, default=2, help="engine runs to pool (extraction is not deterministic)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    raw = Path(args.dataset).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    cases = [c for c in data["cases"] if c["shape"] != "control"]
    if args.limit:
        cases = cases[:args.limit]
    print(f"dataset {data['name']}  supersession cases {len(cases)}  sha256={hashlib.sha256(raw).hexdigest()[:16]}")
    print(f"store   {sandbox_guard.store()}\n")
    out = {"dataset": {"name": data["name"], "sha256": hashlib.sha256(raw).hexdigest(),
                       "path": str(Path(args.dataset).resolve().relative_to(ROOT)) if Path(args.dataset).resolve().is_relative_to(ROOT) else args.dataset,
                       "supersession_cases": len(cases)},
           "days": {"first": DAY_FIRST, "between": DAY_BETWEEN, "second": DAY_SECOND, "after": DAY_AFTER},
           "k": args.k, "llm": sb.LLM, "embedder": sb.EMBED_MODEL, "arms": {}}
    for name in [a.strip() for a in args.arms.split(",") if a.strip()]:
        fn = ARMS.get(name)
        if fn is None:
            print(f"- {name}: unknown arm (have: {', '.join(ARMS)})")
            continue
        print(f"- {name}")
        res = fn(cases, args.k, args.runs) if name == "nevertwice" else fn(cases, args.k)
        out["arms"][name] = res
        print(f"  both-correct {res['both_correct_rate']} {res['both_correct_ci']} | old day {res['old_day_rate']} "
              f"| new day {res['new_day_rate']} | errors {res['errors']} | {res['seconds']}s\n")
    out["arms"]["mem0"] = {"blocked": "Mem0 stamps a memory with the wall-clock time of the add() call and its "
                                      "search has no as-of filter; facts cannot be placed in the past without "
                                      "patching the product"}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
