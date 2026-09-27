#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A5: research/v3/run_v3_smoke.py - the smoke's summary, plumbing counters only (rev1 §9.4), on a
synthetic stand result (the scheduler's UnitRecords), a synthetic proxy log and bodies files in a temporary run dir:

* SM-fields: each row is exactly arm, run and the seven fields; failures exactly its five counters;
* SM-counts-from-log: calls, transport_lost, upstream_errors and tokens come from the proxy's log (accounting), unit
  aborts and reader format failures from the scheduler's records - never from an adapter's own counts;
* SM-items, SM-seconds: the end_write footprints' retrievable items (aborted units left out), the units' active time;
* SM-yield (Q-A5-1): retrievable_unit_share and coverage by accounting.unit_coverage over the bodies files, no label
  even where a scored run would carry one; an arm with no writer LLM prints "n/a: no writer LLM";
* SM-refuses-score-keys: a key naming a score, verdict, gold, twin, judge, label, accuracy, EM or F1 - at any depth -
  refuses the row; SM-no-results-dir: never under research/v3/results, never over a file; SM-render: every printed
  line is a smoke row; a log or a bodies file with a problem refuses the summary.

    python tests/research/_test_v3_smoke.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SM = _load("v3_run_smoke_t", ROOT / "research" / "v3" / "run_v3_smoke.py")
SC = _load("v3_scheduler_for_smoke_t", ROOT / "research" / "v3" / "scheduler.py")
AC = _load("v3_accounting_for_smoke", ROOT / "research" / "v3" / "accounting.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except SM.SmokeError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not the smoke's named refusal: the row FAILs by name
        return f"not refused by the smoke: {type(e).__name__}: {e}"


END = "2026-09-28T10:00:00+00:00"
T = lambda m: f"2026-09-28T09:{m:02d}:00+00:00"  # noqa: E731
TEXT1 = "Caroline said the adoption agency called back on Tuesday about the second interview slot."
TEXT3 = "Melanie painted the lake at sunrise and plans to show the canvas at the fall exhibition."
ITEMS = {"u1": [TEXT1], "u2": ["a unit the writer never saw at all, long enough"], "u3": [TEXT3]}


def wrec(arm, unit, retrievable, active, *, aborted=None):
    return SC.UnitRecord(arm=arm, run="r1", unit=unit, footprint={"retrievable": retrievable}, active_s=active,
                         end_write_utc=None if aborted else END, aborted=aborted)


def call(unit, key, *, role="write", stage="write", status=200, prompt=0, arm="mem0", t=5):
    return {"arm": arm, "unit": f"r1.{unit}", "port_role": role, "stage": stage, "request_key": key, "t0": T(t),
            "t1": T(t + 1), "status": status, "complete": status == 200, "endpoint": "v1",
            "usage": {"prompt": prompt, "completion": 1 if status == 200 else 0, "cache_hit": 0, "cache_miss": prompt}}


RESULT = {"stand": "S4-smoke-1", "blocks": [{
    "write": {"mem0": {("r1", "u1"): wrec("mem0", "u1", 4, 2.0), ("r1", "u2"): wrec("mem0", "u2", 0, 1.0),
                       ("r1", "u3"): wrec("mem0", "u3", 0, 0.5)},
              "bm25-floor": {("r1", "u1"): wrec("bm25-floor", "u1", 5, 0.25), ("r1", "u2"): wrec("bm25-floor", "u2", 5, 0.25),
                             ("r1", "u3"): wrec("bm25-floor", "u3", 2, 0.1, aborted="ceiling")}},
    "questions": {"mem0": {("r1", "u1"): {"reads": [{"qid": "u1:q1", "point": "B", "t0": T(20), "t1": T(21),
                                                     "answer": {"format_failure": True, "sha256": "0" * 64}}],
                                          "active_s": 1.5, "aborted": None},
                           ("r1", "u2"): {"reads": [], "active_s": 0.25, "aborted": "ceiling"},
                           ("r1", "u3"): {"reads": [], "active_s": 0.0, "aborted": None}},
                  "bm25-floor": {("r1", "u1"): {"reads": [], "active_s": 0.25}, ("r1", "u2"): {"reads": [], "active_s": 0.25}}},
}]}
LOG = AC.ProxyLog(calls=[call("u1", "k1", prompt=100), call("u2", "k2", status=502, t=6),
                         call("u1", "k3", role="reader", stage="questions", prompt=50, t=20)])
KQ = {("u1", "k3"): "u1:q1"}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_smoke_"))
try:
    RUN = TMP / "px-run"
    (RUN / "bodies" / "mem0").mkdir(parents=True)
    (RUN / "bodies" / "mem0" / "r1.u1.jsonl").write_bytes(
        (json.dumps({"t0": T(5), "strings": ["user: " + TEXT1, "Extract the facts."], "via": "write", "status": 200}) + "\n").encode())

    def summary(log=LOG, **kw):
        args = dict(stand="S4-smoke-1", key_question=KQ, item_texts=ITEMS, run_dir=RUN, no_writer={"bm25-floor"})
        args.update(kw)
        return SM.summarize(RESULT, log, **args)

    print("- the fields -")
    try:
        s, s_err = summary(), None
    except Exception as e:  # noqa: BLE001 - a summary that raises FAILs the rows below by name
        s, s_err = {"stand": None, "arm_runs": []}, f"{type(e).__name__}: {e}"
    rows = {r["arm"]: r for r in s["arm_runs"]}
    for _arm in ("mem0", "bm25-floor"):                    # placeholders, so every row below FAILs by name
        rows.setdefault(_arm, {"arm": _arm, "run": "r1", **{f: None for f in SM.SMOKE_FIELDS},
                               "failures": {k: None for k in SM.FAILURE_FIELDS},
                               "tokens": {"write": {"prompt": None}, "answer": {"prompt": None}}})
    check("SM-fields: one row per arm-run, exactly arm, run and calls, failures, items, tokens, seconds, yield, coverage",
          s["stand"] == "S4-smoke-1" and sorted(rows) == ["bm25-floor", "mem0"]
          and all(set(r) == {"arm", "run", *SM.SMOKE_FIELDS} for r in rows.values())
          and all(set(r["failures"]) == set(SM.FAILURE_FIELDS) for r in rows.values()), f"{s_err} {str(s)[:300]}")
    m = rows["mem0"]
    check("SM-counts-from-log: calls, transport_lost, upstream_errors and tokens by phase are the proxy log's",
          m["calls"] == 3 and m["failures"]["transport_lost"] == 1 and m["failures"]["upstream_errors"] == 1
          and m["failures"]["failed_outcomes"] == 0 and m["tokens"]["write"]["prompt"] == 100
          and m["tokens"]["answer"]["prompt"] == 50, str(m))
    check("SM-counts: unit aborts and reader format failures are the scheduler's records",
          m["failures"]["unit_aborts"] == 1 and m["failures"]["reader_format_failures"] == 1
          and rows["bm25-floor"]["failures"]["unit_aborts"] == 1, str(m["failures"]))
    check("SM-items, SM-seconds: the footprints' retrievable items (an aborted unit left out) and the units' active time",
          m["items"] == 4 and rows["bm25-floor"]["items"] == 10 and m["seconds"] == 5.25
          and rows["bm25-floor"]["seconds"] == 1.1, f"{m['items']} {m['seconds']} {rows['bm25-floor']}")
    check("SM-yield (Q-A5-1): the writer's share of units with a retrievable item and its coverage over the bodies - "
          "1 of 3 units, u1 fully covered, u2 and u3 never reached the writer", m["yield"] == round(1 / 3, 4)
          and m["coverage"] == round(1 / 3, 4), f"{m['yield']} {m['coverage']}")
    check("SM-yield: no label on a smoke, even below the 0.5 a scored run would label (labels come from scored runs "
          "only)", isinstance(m["yield"], float) and m["yield"] < 0.5 and "label" not in json.dumps(s), json.dumps(m))
    check("SM (Q-A5-1 O-d): an arm with no writer LLM prints 'n/a: no writer LLM' for yield and coverage",
          rows["bm25-floor"]["yield"] == SM.NO_WRITER and rows["bm25-floor"]["coverage"] == SM.NO_WRITER,
          str(rows["bm25-floor"]))

    print("\n- the smoke has no scorer -")
    base = dict(rows["mem0"])
    bad = {"twin in tokens": refused(lambda: SM.check_row({**base, "tokens": {**base["tokens"], "twin": 0.5}})),
           "nested judge": refused(lambda: SM.check_row({**base, "failures": {**base["failures"], "judge_invalid": 0}})),
           "f1 in tokens": refused(lambda: SM.check_row({**base, "tokens": {**base["tokens"], "f1": 1}})),
           "a score instead of calls": refused(lambda: SM.check_row({**{k: v for k, v in base.items() if k != "calls"},
                                                                     "score": 1}))}
    check("SM-refuses-score-keys: a twin or an F1 among the tokens is refused for naming a score; a nested judge "
          "counter and a score in place of a field are refused too", "names a score" in bad["twin in tokens"]
          and "names a score" in bad["f1 in tokens"]
          and all(v != "accepted" and not v.startswith("not refused") for v in bad.values()), str(bad))
    check("... and 'coverage', 'failures' and the token phases are no score", SM.score_keys(base) == [],
          str(SM.score_keys(base)))

    print("\n- the printed lines and the file -")
    text = SM.render(s)
    back = SM.rows_from_lines(text)
    check("SM-render: one JSON line per arm-run, each a smoke row with its stand", len(text.strip().split("\n")) == 2
          and back == s["arm_runs"], text[:200])
    extra = json.dumps({"stand": "S4-smoke-1", **base, "gold": 1})
    check("SM-render: a printed line carrying a gold key is refused", refused(lambda: SM.rows_from_lines(extra))
          != "accepted", extra[:100])
    res_dir = TMP / "research" / "v3" / "results"
    under = refused(lambda: SM.write(s, res_dir / "S4" / "smoke.json", results_dir=res_dir))
    out = SM.write(s, TMP / "runs" / "S4-smoke-1" / "_smoke" / "summary.json", results_dir=res_dir)
    again = refused(lambda: SM.write(s, out, results_dir=res_dir))
    check("SM-no-results-dir: never under research/v3/results; in the runs tree once, never over a file",
          "writes nothing under" in under and json.loads(out.read_text(encoding="utf-8")) == s
          and "already exists" in again and not res_dir.exists(), f"{under} | {again}")
    check("SM: the module's own results dir is research/v3/results", SM.RESULTS_DIR == ROOT / "research" / "v3" / "results")

    print("\n- refusals -")
    plog = refused(lambda: summary(log=AC.ProxyLog(calls=LOG.calls, problems=["calls.jsonl:3: not a JSON record"])))
    check("a proxy log with a problem refuses the summary by name", "has problems" in plog, plog)
    (RUN / "bodies" / "mem0" / "r1.u3.jsonl").write_bytes(b'{"t0": "x", "strings": []}')      # no LF: a cut write
    pb = refused(lambda: summary())
    check("a bodies file with a problem refuses the summary by name", "bodies file has problems" in pb, pb)
    (RUN / "bodies" / "mem0" / "r1.u3.jsonl").unlink()
    empty = refused(lambda: SM.summarize({"blocks": []}, LOG, stand="S4-smoke-1", key_question=KQ, item_texts=ITEMS,
                                         run_dir=RUN))
    check("a stand result with no arm-run is refused - never an empty summary", "no arm-run" in empty, empty)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 smoke: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
