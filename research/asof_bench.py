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

Since J2 every engine row also records what the store holds for the case's first session
(notes written, live or retired, their `valid_to`) and classifies an old-day failure into one
of five kinds - `never_written` (the extractor wrote nothing for session one), `absorbed` (the
twin gate absorbed session two's different fact into session one's note, which no longer serves
the old fact; K1b), `unranked` (a note exists but nothing came back for the old day),
`paraphrase` (the old note came back but no marker survived its wording), `leak` (the new fact
came back for the old day) - so a miss says which half of the system missed.

`--recent` shifts all four dates inside the 90-day archive window (ledger J2b): the control
that isolates "does the write path close an interval at all" from "does it close one after the
old note has aged into `Archive/`". At the shipped dating session one is 193 days back and
archived before session two arrives; the archive-aware reconcile must still close its interval,
and `s0_retired_rate` is read against the `--recent` rate.

    python research/asof_bench.py --arms nevertwice,naive --out run1.json
    python research/asof_bench.py --recent --arms nevertwice --out recent.json  # J2b control
    python research/asof_bench.py --limit 5            # smoke
"""
from __future__ import annotations

import argparse
import collections
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
# Shipped dating: session one 193 days before 2026-09-10, archived before session two arrives.
DAY_FIRST, DAY_BETWEEN, DAY_SECOND, DAY_AFTER = "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"
# --recent (J2b control): the same span shifted inside the 90-day window so session one is never
# archived, isolating write-path interval closure from archive-aware closure.
RECENT_DAYS = ("2026-07-15", "2026-08-01", "2026-08-20", "2026-09-05")
FAIL_KINDS = ("never_written", "absorbed", "unranked", "paraphrase", "leak")


def _apply_recent() -> None:
    """Move the four stand dates inside the archive window for the J2b control run."""
    global DAY_FIRST, DAY_BETWEEN, DAY_SECOND, DAY_AFTER
    DAY_FIRST, DAY_BETWEEN, DAY_SECOND, DAY_AFTER = RECENT_DAYS


def _items(hits: list[dict]) -> list[str]:
    """One string per returned note: title and description."""
    return [" ".join(p for p in (str(h.get("title") or ""), str(h.get("description") or "")) if p)
            for h in hits]


def _row(case: dict, old_items: list[str], new_items: list[str]) -> dict:
    old_ok = sb._hit(case["superseded"], " ".join(old_items)) and not sb._hit(case["current"], " ".join(old_items))
    new_ok = sb._hit(case["current"], " ".join(new_items))
    return {"id": case["id"], "shape": case["shape"], "old_day_correct": bool(old_ok),
            "new_day_correct": bool(new_ok), "both_correct": bool(old_ok and new_ok),
            "old_items": len(old_items), "new_items": len(new_items)}


def _session_state(project: str, case: dict) -> dict:
    """What the store holds for the case's two sessions, read from disk (J2 instrumentation).

    Each typed note carries its writing session in frontmatter; the session note's stem ends in
    the engine's 8-character identity hash of the ingest id, which is how a note is attributed
    to session one or two here without touching the write path. Per session: notes written,
    live, retired, their `valid_to` values, and whether any note's text carries the superseded
    marker (for session one) or the current marker (for session two)."""
    import sandbox_guard as sg                                  # noqa: PLC0415
    from nevertwice import memory_hook as m                     # noqa: PLC0415
    root = Path(sg.store())
    sid8 = {f"-session-{m._sid8(f'{project}-s{j}')}": j for j in (0, 1)}
    per = {j: {"written": 0, "live": 0, "retired": 0, "absorbed": 0, "valid_to": [], "marker_in_text": False,
               "titles": [], "supersedes": [], "superseded_via": []}
           for j in (0, 1)}
    notes: list[tuple[int, Path, dict, str]] = []
    for folder in ("Patterns", "Mistakes", "Decisions"):
        d = root / folder
        if not d.exists():
            continue
        for md in d.rglob(f"*-{project}-*.md"):
            fm = m._read_frontmatter_file(md)
            sess = str(fm.get("session") or "")
            j = next((jj for tag, jj in sid8.items() if tag in sess), None)
            if j is None:
                continue
            notes.append((j, md, fm, md.read_text(encoding="utf-8", errors="replace")))
    # A note the twin gate absorbed the other session's item into is rewritten in place: its
    # `session` becomes the absorbing session and the earlier one survives only in `sources` (and
    # its statement under `## Previous statement`). Until 2026-09-11 that read here as "session one
    # wrote nothing" (K1b/K3). It is credited as absorbed only when the earlier session has NO
    # note of its own anywhere - a proper same-slug supersession also inherits `sources`, and there
    # the earlier session's note stands in `Superseded/` and is counted as retired, not absorbed.
    own = {j: sum(1 for jj, *_ in notes if jj == j) for j in (0, 1)}
    for j, md, fm, body in notes:
        srcs = " ".join(str(s) for s in (fm.get("sources") or []) if s)
        for tag, jj in sid8.items():
            if jj != j and own[jj] == 0 and tag in srcs:
                per[jj]["written"] += 1
                per[jj]["absorbed"] += 1
                per[jj]["titles"].append(md.stem)
    for j, md, fm, body in notes:
        st = per[j]
        st["written"] += 1
        if md.parent.name == "Superseded" or str(fm.get("status") or "") == "superseded":
            st["retired"] += 1
            if fm.get("superseded_via"):
                st["superseded_via"].append(str(fm["superseded_via"]))   # J2b: slug|explicit|twin
        else:
            st["live"] += 1
        if fm.get("valid_to"):
            st["valid_to"].append(str(fm["valid_to"]))
        st["titles"].append(md.stem)               # the slug is in the stem: did the two sessions agree on it?
        sup = fm.get("supersedes")
        if isinstance(sup, list) and sup:
            st["supersedes"].extend(str(x) for x in sup)
        markers = case["superseded"] if j == 0 else case["current"]
        if markers and sb._hit(markers, body):
            st["marker_in_text"] = True
    return {"s0": per[0], "s1": per[1]}


def old_fail_kind(row: dict, state: dict | None) -> str | None:
    """Why the old day failed, from the row and the store state; None when it did not fail."""
    if row.get("old_day_correct"):
        return None
    s0 = (state or {}).get("s0") or {}
    if state is not None and s0.get("written", 0) == 0:
        return "never_written"
    if state is not None and s0.get("absorbed", 0) and s0.get("written", 0) == s0.get("absorbed", 0):
        # every note session one wrote was absorbed into session two's: the old statement is on
        # disk under "Previous statement" and no note serves it for the old day (K1b/K3)
        return "absorbed"
    if row.get("old_items", 0) == 0:
        return "unranked"
    return "leak" if row.get("leak") else "paraphrase"


def _read_case(api, case: dict, project: str, k: int, run: int) -> dict:
    old_hits = api.as_of(case["query"], DAY_BETWEEN, project, k=k)
    new_hits = api.as_of(case["query"], DAY_AFTER, project, k=k)
    old, new = _items(old_hits), _items(new_hits)
    row = _row(case, old, new)
    row["leak"] = bool(sb._hit(case["current"], " ".join(old)))
    try:
        state = _session_state(project, case)
    except Exception as e:                                        # noqa: BLE001 - diagnostics only
        state = {"error": f"{type(e).__name__}: {e}"}
    row["store"] = state
    row["old_fail_kind"] = old_fail_kind(row, state if "s0" in state else None)
    row["run"] = run
    return row


def run_nevertwice(cases: list[dict], k: int, runs: int = 1, sleep: bool = False) -> dict:
    """`runs` > 1 repeats the whole ingest under fresh project names and pools the case-runs:
    the extraction model is not deterministic at temperature 0 (the supersession bench pools
    two runs for the same reason), so one run of this stand is not a result either. `sleep`
    (K8) adds the second reading: the sleep-time judge over the store's contested pairs, then
    both days asked again (`after_sleep`)."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = sb.LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", sb.EMBED_MODEL)
    # The extractor samples: the engine's default is 0.2, right for the live hook and
    # wrong for a measurement. Pinning the MODEL and leaving the TEMPERATURE loose is
    # what `supersession_bench` did for a year, publishing a run-to-run spread it blamed
    # on the model (fixed 2026-09-22: at 0.2 every one of 80 cases served different text
    # between runs of one commit, at 0 exactly one did).
    os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"
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
        old_hits = api.as_of(case["query"], DAY_BETWEEN, project, k=k)
        new_hits = api.as_of(case["query"], DAY_AFTER, project, k=k)
        old, new = _items(old_hits), _items(new_hits)
        row = _row(case, old, new)
        row["leak"] = bool(sb._hit(case["current"], " ".join(old)))
        try:
            state = _session_state(project, case)
        except Exception as e:                                    # noqa: BLE001 - diagnostics only
            state = {"error": f"{type(e).__name__}: {e}"}
        row["store"] = state
        row["old_fail_kind"] = old_fail_kind(row, state if "s0" in state else None)
        rows.append({**row, "run": run})
        print(f"  [run {run + 1}/{runs} {i + 1}/{len(cases)}] {case['id']}  old {row['old_day_correct']}  "
              f"new {row['new_day_correct']}"
              + (f"  ({row['old_fail_kind']})" if row["old_fail_kind"] else ""), flush=True)
    out = {"rows": rows, **score(rows), "runs": runs, "seconds": round(time.time() - t0, 1),
           "config": f"ollama {sb.LLM} + {sb.EMBED_MODEL}, k={k}, days {DAY_FIRST}/{DAY_SECOND}",
           "store_bytes": sb.store_bytes()}
    if runs > 1:
        out["per_run"] = [score([r for r in rows if r.get("run") == run])["both_correct_rate"] for run in range(runs)]
    if sleep:
        from nevertwice import consolidate_memory as cm            # noqa: PLC0415
        t1 = time.time()
        adj = cm.adjudicate_contested(apply=True, has_llm=True, cap=sb.SLEEP_CAP, budget=sb.SLEEP_BUDGET)
        rows2 = [_read_case(api, cases[i], f"asof{run}{i:03d}", k, run)
                 for run in range(runs) for i in range(len(cases))]
        after = {"rows": rows2, **score(rows2), "runs": runs, "adjudication": adj,
                 "seconds": round(time.time() - t1, 1), "store_bytes": sb.store_bytes(),
                 "config": f"the same store after consolidate_memory.adjudicate_contested(budget={adj['budget']}, cap={adj['cap']})"}
        if runs > 1:
            after["per_run"] = [score([r for r in rows2 if r.get("run") == run])["both_correct_rate"]
                                for run in range(runs)]
        out["after_sleep"] = after
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
    out = {"n_cases": len(sup), "both_correct_rate": round(both / n, 4),
           "both_correct_ci": [round(x, 4) for x in sb.wilson(both, n)],
           "old_day_rate": round(old / n, 4), "new_day_rate": round(new / n, 4),
           "errors": sum(1 for r in sup if r.get("error"))}
    if any("old_fail_kind" in r for r in sup):
        kinds = collections.Counter(r.get("old_fail_kind") for r in sup if r.get("old_fail_kind"))
        out["old_day_failures_by_kind"] = {k: kinds.get(k, 0) for k in FAIL_KINDS}
        out["s0_never_written"] = sum(1 for r in sup
                                      if ((r.get("store") or {}).get("s0") or {}).get("written") == 0)
        # K1b/K3: session one's note exists only as the note session two's item was absorbed into
        out["s0_absorbed"] = sum(1 for r in sup
                                 if ((r.get("store") or {}).get("s0") or {}).get("absorbed"))
        # how often the replacing session actually closed the first fact's interval - the
        # write path's cross-day recognition, which the same-day supersession stand cannot see
        written = [r for r in sup if ((r.get("store") or {}).get("s0") or {}).get("written")]
        out["s0_retired_rate"] = round(sum(1 for r in written
                                           if r["store"]["s0"].get("retired")) / len(written), 4) if written else None
        # J2b: which mechanism closed the interval, for the notes that were retired at all
        via = collections.Counter(v for r in written
                                  for v in (r["store"]["s0"].get("superseded_via") or []))
        out["s0_retired_via"] = dict(via)
        out["s0_written_pairs"] = len(written)
    return out


def run_zep(cases: list[dict], k: int) -> dict:
    """Graphiti's bitemporal edges asked for a day (ledger J4): episodes carry the stand's dates
    as `reference_time`; a fact is believed on a day when it was stated by then and neither
    invalidated nor expired by then - filtered over Graphiti's own fields in
    `research/_graphiti_arm.py`, because the server-side date filter misbehaved in the probe."""
    sys.path.insert(0, str(HERE))
    import _graphiti_arm as ga                                  # noqa: PLC0415
    why = ga.available()
    if why:
        return {"blocked": why}
    try:
        arm = ga.GraphitiArm(sb.LLM, sb.EMBED_MODEL)
    except Exception as e:                                      # noqa: BLE001
        return {"blocked": f"Graphiti init failed ({type(e).__name__}: {e})"}
    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        group = f"asof{i:03d}"
        r = arm.ingest(group, [("\n".join(case["sessions"][0]), DAY_FIRST), ("\n".join(case["sessions"][1]), DAY_SECOND)])
        if r["errors"]:
            rows.append({"id": case["id"], "shape": case["shape"], "old_day_correct": False, "new_day_correct": False,
                         "both_correct": False, "error": getattr(arm, "last_error", "episode error")})
            continue
        old = arm.search_asof(group, case["query"], DAY_BETWEEN, k)
        new = arm.search_asof(group, case["query"], DAY_AFTER, k)
        rows.append(_row(case, old, new))
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  old {rows[-1]['old_day_correct']}  new {rows[-1]['new_day_correct']}", flush=True)
    stats = arm.stats()
    arm.close()
    failed = sum(1 for r in rows if r.get("error"))
    if failed > len(rows) * 0.1:
        return {"blocked": f"{failed} of {len(rows)} cases errored", "rows": rows, "graphiti": stats}
    return {"rows": rows, **score(rows), "seconds": round(time.time() - t0, 1),
            "config": f"graphiti-core via FalkorDB, {sb.LLM} + {sb.EMBED_MODEL}, edges filtered by valid_at/invalid_at/expired_at, k={k}",
            "graphiti": stats}


ARMS = {"nevertwice": run_nevertwice, "naive": run_naive, "zep": run_zep}
RUNS = 1


def merge_arm(results: list[dict]) -> dict:
    """One arm's result from several files: rows concatenated and tagged with their run, the rates
    re-scored over the case-runs, the per-run values kept beside them - the shape `run_nevertwice`
    gives our own arm when `--runs` is more than one. Ledger K2: the competitor arm is pooled and
    published with its spread exactly as ours is, instead of standing on one run."""
    live = [r for r in results if not r.get("blocked")]
    if not live:
        return results[0]
    if len(live) == 1:
        return live[0]
    rows = [dict(r, run=i) for i, res in enumerate(live) for r in res["rows"]]
    out = {"rows": rows, **score(rows), "runs": len(live),
           "per_run": [res["both_correct_rate"] for res in live],
           "seconds": round(sum(float(res.get("seconds") or 0) for res in live), 1),
           "config": live[0].get("config", "")}
    if any("graphiti" in res for res in live):
        out["graphiti"] = live[0].get("graphiti")
        out["graphiti_per_run"] = [res.get("graphiti") for res in live]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--arms", default="nevertwice,naive")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", type=int, default=2, help="engine runs to pool (extraction is not deterministic)")
    ap.add_argument("--sleep", action="store_true",
                    help="K8: ask both days again after the sleep-time judge (arm `nevertwice_after_sleep`)")
    ap.add_argument("--recent", action="store_true",
                    help="J2b control: date session one inside the 90-day archive window (never archived)")
    ap.add_argument("--with", dest="others", nargs="*", default=[], metavar="FILE",
                    help="result files whose other arms (zep) are merged into the output; an arm present "
                         "in several files is pooled over its runs, per-run values kept")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.recent:
        _apply_recent()
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
           "recent": bool(args.recent),
           "k": args.k, "llm": sb.LLM, "embedder": sb.EMBED_MODEL, "arms": {}}
    for name in [a.strip() for a in args.arms.split(",") if a.strip()]:
        fn = ARMS.get(name)
        if fn is None:
            print(f"- {name}: unknown arm (have: {', '.join(ARMS)})")
            continue
        print(f"- {name}")
        res = fn(cases, args.k, args.runs, sleep=args.sleep) if name == "nevertwice" else fn(cases, args.k)
        out["arms"][name] = res
        if res.get("after_sleep"):
            after = res.pop("after_sleep")
            out["arms"]["nevertwice_after_sleep"] = after
            adj = after["adjudication"]
            print(f"  after sleep: both {after['both_correct_rate']} old {after['old_day_rate']} new {after['new_day_rate']} "
                  f"s0_retired {after.get('s0_retired_rate')} via {after.get('s0_retired_via')} | judge: {adj['pairs']} pairs, "
                  f"judged {adj['judged']}, replaces {adj['replaces']}, separate {adj['separate']}, left {adj['left']}")
        print(f"  both-correct {res['both_correct_rate']} {res['both_correct_ci']} | old day {res['old_day_rate']} "
              f"| new day {res['new_day_rate']} | errors {res['errors']} | {res['seconds']}s")
        if res.get("old_day_failures_by_kind"):
            print(f"  old-day failures by kind: {res['old_day_failures_by_kind']}  "
                  f"(session one never written: {res.get('s0_never_written')})")
        print()
    # other arms measured in their own environments (the Zep arm runs under the graphiti venv):
    # an arm present in several files is pooled over its runs, like ours
    others: dict[str, list[dict]] = {}
    for f in args.others:
        blob = json.loads(Path(f).read_text(encoding="utf-8"))
        if blob.get("dataset", {}).get("sha256") != out["dataset"]["sha256"]:
            print(f"  {f}: measured on another dataset - not merged")
            continue
        for name, res in blob.get("arms", {}).items():
            if name in out["arms"] or name == "mem0" or res.get("blocked"):
                continue
            others.setdefault(name, []).append(res)
    for name, results in others.items():
        out["arms"][name] = merge_arm(results)
        res = out["arms"][name]
        print(f"- {name} (merged from {len(results)} file(s), {res.get('runs', 1)} run(s)): "
              f"both-correct {res['both_correct_rate']} {res['both_correct_ci']} | old day {res['old_day_rate']} "
              f"| new day {res['new_day_rate']}" + (f" | per run {res['per_run']}" if res.get("per_run") else ""))
    out["arms"]["mem0"] = {"blocked": "Mem0 stamps a memory with the wall-clock time of the add() call and its "
                                      "search has no as-of filter; facts cannot be placed in the past without "
                                      "patching the product"}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
