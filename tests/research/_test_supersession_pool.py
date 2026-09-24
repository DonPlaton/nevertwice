"""`supersession_bench.py --pool`: the committed artifact can be rebuilt from the run files.

The artifact behind `supersession.*` pools two engine runs (the extraction model is not
deterministic at temperature 0) and carries the Mem0 and naive arms beside them. The code that
built it lived in a scratchpad until 2026-09-06, so nobody but the author could rebuild the
file the claims point at. These checks pin the rebuild: counts, per-run spread, the arm names a
pointer such as `pairs[1].p_mcnemar` relies on, and the refusals.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

import json  # noqa: E402
import tempfile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import supersession_bench as sb  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def _row(cid, shape, stale, current, **extra):
    return {"id": cid, "shape": shape, "stale_returned": stale,
            "stale_rank": 1 if stale else None, "current_returned": current,
            "chars_returned": 100, **extra}


def _arm(rows, chars=250.0):
    return {"rows": rows, **sb.score(rows), "mean_chars_returned": chars, "seconds": 1.0}


def _blob(arms, sha="abc"):
    return {"dataset": {"name": "synthetic", "sha256": sha, "path": "x.json"},
            "n_cases": 5, "k": 5, "llm": "llm", "embedder": "emb", "arms": arms}


def _write(tmp, name, blob):
    p = Path(tmp) / name
    p.write_text(json.dumps(blob), encoding="utf-8")
    return p


# four supersession cases + two controls; the controls carry the store-state flags
def engine_rows(stale_ids, retired_ids=()):
    rows = [_row(f"s{i}", "value_replaced", f"s{i}" in stale_ids, True) for i in range(4)]
    for j in range(2):
        cid = f"c{j}"
        retired = cid in retired_ids
        rows.append(_row(cid, "control", False, not retired, current_live=not retired,
                         current_retired=retired, current_demoted=False, current_absent=False))
    return rows


def mem0_rows():
    return ([_row(f"s{i}", "value_replaced", True, True) for i in range(4)]
            + [_row(f"c{j}", "control", False, True) for j in range(2)])


def naive_rows():
    return ([_row(f"s{i}", "value_replaced", i != 0, True) for i in range(4)]
            + [_row(f"c{j}", "control", False, True) for j in range(2)])


with tempfile.TemporaryDirectory() as tmp:
    run1 = _write(tmp, "run1.json", _blob({"nevertwice": _arm(engine_rows({"s0"}), 260.0),
                                           "naive": _arm(naive_rows(), 200.0)}))
    run2 = _write(tmp, "run2.json", _blob({"nevertwice": _arm(engine_rows({"s0", "s1"},
                                                                          {"c1"}), 240.0)}))
    mem0 = _write(tmp, "mem0.json", _blob({"mem0": _arm(mem0_rows(), 450.0)}))

    print("\n- the pooled artifact -")
    res = sb.pool([run1, run2], [mem0])
    P = res["pooled_nevertwice"]
    check("two runs pooled", P["runs"] == 2)
    check("stale pooled over case-runs: 3 of 8", (P["stale"]["k"], P["stale"]["n"]) == (3, 8),
          str(P["stale"]))
    check("per-run stale spread kept beside the pooled rate",
          P["stale"]["per_run"] == [0.25, 0.5], str(P["stale"]["per_run"]))
    check("pooled stale rate", P["stale"]["rate"] == 0.375)
    check("Wilson interval on the pooled count", 0 < P["stale"]["ci"][0] < 0.375 < P["stale"]["ci"][1] < 1)
    check("current pooled: 8 of 8", (P["current"]["k"], P["current"]["n"]) == (8, 8))
    check("control miss counts every control that did not come back: 1 of 4, per run 0 and 0.5",
          (P["control_miss"]["k"], P["control_miss"]["n"]) == (1, 4) and P["control_miss"]["per_run"] == [0.0, 0.5],
          str(P["control_miss"]))
    check("the miss is split by cause: the one miss was a retirement",
          P["control_causes"] == {"retired": 1, "demoted": 0, "never_written": 0, "unranked": 0},
          str(P["control_causes"]))
    check("an arm whose store was not read has no over-retraction figure, only a control miss",
          res["arms"]["mem0"]["over_retraction_rate"] is None and res["arms"]["mem0"]["control_miss_rate"] == 0.0)
    check("over-retraction counts only what the memory retired: 1 of 4 controls",
          (P["over_retraction"]["k"], P["over_retraction"]["n"], P["over_retraction"]["rate"])
          == (1, 4, 0.25), str(P["over_retraction"]))
    check("chars per query averaged over runs", P["mean_chars_returned"] == 250.0)

    print("\n- the arms and the pointers -")
    check("first run is `nevertwice`, second is `nevertwice_run2`",
          list(res["arms"])[:2] == ["nevertwice", "nevertwice_run2"], str(list(res["arms"])))
    check("other arms carried from every file", {"naive", "mem0"} <= set(res["arms"]))
    check("dataset block counts the cases",
          (res["dataset"]["cases"], res["dataset"]["supersession_cases"],
           res["dataset"]["control_cases"]) == (6, 4, 2), str(res["dataset"]))
    pairs = res["pairs"]
    check("pairs in name order: mem0/naive, mem0/nevertwice, naive/nevertwice",
          [(p["a"], p["b"]) for p in pairs]
          == [("mem0", "naive"), ("mem0", "nevertwice"), ("naive", "nevertwice")],
          str([(p["a"], p["b"]) for p in pairs]))
    m0nt = pairs[1]
    check("pairs are computed on the FIRST engine run (3 mem0-only, 0 nevertwice-only)",
          (m0nt["stale_only_mem0"], m0nt["stale_only_nevertwice"], m0nt["n"]) == (3, 0, 4),
          str(m0nt))
    check("every pair carries an exact McNemar p in (0, 1]",
          all(0 < p["p_mcnemar"] <= 1 for p in pairs))
    check("per-run discordance against mem0 recorded for both runs",
          res["pairs_per_engine_run"] == [{"nevertwice_only": 0, "mem0_only": 3},
                                          {"nevertwice_only": 0, "mem0_only": 2}],
          str(res["pairs_per_engine_run"]))
    check("the note names the per-run spread", "0.2500 and 0.5000" in res["pooled_note"])
    check("k, llm and embedder travel from the first run",
          (res["k"], res["llm"], res["embedder"]) == (5, "llm", "emb"))

    print("\n- K20 (the auditor's correction of K19): pool()'s 'pairs' is computed from "
          "the RECORDED rows (an errored mem0 case = its own blank row, what it actually "
          "returned), NOT from pairs_errors_as_failure -")
    with tempfile.TemporaryDirectory() as tmp_k20:
        # nevertwice reads stale on s0 only; mem0's s0 case ERRORED and is recorded as
        # the blank row `run_mem0` actually writes for an error (stale_returned=False,
        # current_returned=False - see `_blank`), never the rescored worst case
        # (`_mem0_errors_as_failure` would flip it to stale_returned=True).
        k20_engine_rows = engine_rows({"s0"})
        k20_mem0_rows = [_row("s0", "value_replaced", False, False, error="TimeoutError: x"),
                         _row("s1", "value_replaced", False, True),
                         _row("s2", "value_replaced", False, True),
                         _row("s3", "value_replaced", False, True),
                         _row("c0", "control", False, True),
                         _row("c1", "control", False, True)]
        k20_engine_file = _write(tmp_k20, "engine.json",
                                 _blob({"nevertwice": _arm(k20_engine_rows, 260.0)}))
        k20_mem0_file = _write(tmp_k20, "mem0.json",
                               _blob({"mem0": _arm(k20_mem0_rows, 450.0)}))
        k20_res = sb.pool([k20_engine_file], [k20_mem0_file])
        k20_first = {"nevertwice": {r["id"]: r for r in k20_engine_rows},
                    "mem0": {r["id"]: r for r in k20_mem0_rows}}
        k20_expected_pairs = sb.compare_arms(k20_first)
        check("K20: pairs == compare_arms(recorded rows) exactly",
              k20_res["pairs"] == k20_expected_pairs, str(k20_res["pairs"]))
        k20_mem0_nv = next(p for p in k20_res["pairs"] if {p["a"], p["b"]} == {"mem0", "nevertwice"})
        k20_failure_nv = next(p for p in k20_res["pairs_errors_as_failure"]
                              if {p["a"], p["b"]} == {"mem0", "nevertwice"})
        check("K20: pairs genuinely differs from pairs_errors_as_failure on the errored "
              "case (setup check: this scenario is real, not vacuous)",
              k20_mem0_nv != k20_failure_nv, str((k20_mem0_nv, k20_failure_nv)))

        print("\n- K20 mutation: pairs <- the failure reading (K19's own bug pattern, "
              "reintroduced) -")
        # no exception risk here (plain dict/list comparison) - no _crash_guard needed,
        # and it is defined later in this file anyway.
        mutated_k20_pairs = k20_res["pairs_errors_as_failure"]
        check("mutation 'pairs <- failure reading': pairs no longer equals "
              "compare_arms(recorded rows) (would FAIL the K20 exact-equality check "
              "above)",
              mutated_k20_pairs != k20_expected_pairs, str(mutated_k20_pairs))

    print("\n- refusals -")
    other_ds = _write(tmp, "other.json", _blob({"mem0": _arm(mem0_rows())}, sha="zzz"))
    try:
        sb.pool([run1], [other_ds])
        check("a file from another dataset is refused", False)
    except ValueError as e:
        check("a file from another dataset is refused", "different datasets" in str(e))
    # a second file carrying an arm the first already has is that arm's second run (K2 parity):
    # pooled over case-runs with the per-run values kept, the pairs computed on its first run
    dup = _write(tmp, "dup.json", _blob({"naive": _arm(naive_rows())}))
    pooled = sb.pool([run1], [dup])
    nv = pooled["arms"]["naive"]
    check("an arm present in two files is pooled over its runs, not silently chosen",
          nv["runs"] == 2 and len(nv["rows"]) == 12 and nv["n_supersession"] == 8
          and nv["per_run_stale"] == [0.75, 0.75] and {r["run"] for r in nv["rows"]} == {0, 1},
          str({k: nv[k] for k in ("runs", "n_supersession", "per_run_stale")}))
    check("the pairs still stand on the arm's first run",
          next(p for p in pooled["pairs"] if {p["a"], p["b"]} == {"naive", "nevertwice"})["n"] == 4)
    try:
        sb.pool([mem0], [])
        check("a file without the engine arm cannot be pooled", False)
    except ValueError as e:
        check("a file without the engine arm cannot be pooled", "no nevertwice arm" in str(e))
    blocked = _write(tmp, "blocked.json", _blob({"mem0": {"blocked": "not installed"}}))
    res2 = sb.pool([run1], [blocked])
    # K29 (the auditor, item 9C): a blocked arm used to be left out entirely - P3 says "a
    # blocked arm is printed 'blocked (reason)', never 0" (and, by the same logic, never
    # absent either). It now stays present, as a declared absence rather than a silent one,
    # and - being wholly blocked, no live constituent at all - does not invalidate the pool.
    check("a blocked arm stays PRESENT, not left out (K29: never silently absent)",
          "mem0" in res2["arms"], str(sorted(res2["arms"])))
    check("...as a blocked-shaped dict naming the file",
          res2["arms"]["mem0"] == {"blocked": "not installed (blocked.json)"},
          str(res2["arms"]["mem0"]))
    check("a wholly-blocked arm does not invalidate the pool (P3: a declared absence)",
          "valid" not in res2, str(res2.get("valid")))
    check("without mem0 there is no per-run pairing", res2["pairs_per_engine_run"] == [])

    print("\n- K29: pool_other_arm() - a single LIVE result always writes 'runs' -")
    single_mem0 = sb.pool_other_arm([_arm(mem0_rows(), 450.0)])
    check("pool_other_arm() on a single live result sets runs=1", single_mem0.get("runs") == 1,
          str(single_mem0))

    print("\n- K29: pool_other_arm() - one live + one blocked constituent - the live one is "
          "still pooled, but the arm is marked invalid, naming the file and the reason -")
    mem0_live_run = {"rows": mem0_rows(), **sb.score(mem0_rows()), "seconds": 1.0, "config": "mem0 x"}
    mem0_blocked_run = {"blocked": "qdrant lock timeout"}
    mixed = sb.pool_other_arm([mem0_live_run, mem0_blocked_run], ["live.json", "bad.json"])
    check("K29 mixed: the live constituent is still pooled (rows present, runs=1)",
          "rows" in mixed and mixed.get("runs") == 1, str(mixed))
    check("K29 mixed: the arm is marked invalid",
          mixed.get("valid") is False, str(mixed))
    check("K29 mixed: the reason names the file and the block reason",
          "bad.json" in (mixed.get("invalid_reason") or "")
          and "qdrant lock timeout" in (mixed.get("invalid_reason") or ""),
          mixed.get("invalid_reason"))

    print("\n- K29: pool_other_arm() - every constituent blocked -> stays present as blocked -")
    all_blocked = sb.pool_other_arm([{"blocked": "not installed"}], ["only.json"])
    check("K29: pool_other_arm() with every constituent blocked returns a blocked dict",
          all_blocked == {"blocked": "not installed (only.json)"}, str(all_blocked))

    print("\n- K29 mutation 'a mixed blocked constituent not checked in pool_other_arm': the "
          "blocked sibling silently ignored, the pooled arm stays wrongly valid -")

    def _pool_other_arm_ignore_blocked(results, files=None):
        live = [r for r in results if not r.get("blocked")]
        if not live:
            return results[0]
        if len(results) == 1:
            out = dict(results[0]); out.setdefault("runs", 1)
            return out
        rows = [dict(r, run=i) for i, res in enumerate(live) for r in res["rows"]]
        out = {"rows": rows, **sb.score(rows), "runs": len(live),
              "seconds": round(sum(float(res.get("seconds") or 0) for res in live), 1),
              "config": live[0].get("config", "")}
        invalid = next((res for res in live if res.get("valid") is False), None)
        if invalid is not None:
            out["valid"] = False
            out["invalid_reason"] = invalid.get("invalid_reason")
        # mutation: `blocked` constituents are computed (live/not-live split) but never
        # folded into out["valid"]/out["invalid_reason"]
        return out

    mutated_mixed = _pool_other_arm_ignore_blocked([mem0_live_run, mem0_blocked_run],
                                                    ["live.json", "bad.json"])
    check("mutation 'mixed blocked constituent not checked': the pooled arm stays WRONGLY "
          "valid (would FAIL the K29 mixed 'marked invalid' check above)",
          "valid" not in mutated_mixed, str(mutated_mixed))

    print("\n- compare() still reads files and pairs in name order -")
    cmp_ = sb.compare([run1, mem0])
    check("compare pairs mem0/naive, mem0/nevertwice, naive/nevertwice",
          [(p["a"], p["b"]) for p in cmp_["pairs"]]
          == [("mem0", "naive"), ("mem0", "nevertwice"), ("naive", "nevertwice")])
    check("compare reports the dataset hash", cmp_["dataset_sha256"] == "abc")

    print("\n- the committed artifact has the pooled shape the register points into -")
    art = json.loads((ROOT / "research/results/supersession_v1.json").read_text(encoding="utf-8"))
    for key in ("arms", "k", "llm", "embedder", "dataset", "pooled_nevertwice", "pooled_note",
                "pairs", "pairs_per_engine_run"):
        check(f"artifact carries `{key}`", key in art)
    got = [(p["a"], p["b"]) for p in art["pairs"]]
    names = sorted({n for pr in got for n in pr})
    check("artifact pairs are in name order, every pair of arms once",
          got == [(a, b) for i, a in enumerate(names) for b in names[i + 1:]], str(got))
    check("the three original pairs are among them (a fourth arm, zep, joined on 2026-09-10)",
          {("mem0", "naive"), ("mem0", "nevertwice"), ("naive", "nevertwice")} <= set(got))

print("\n- the eager OR-count: a control row both retired AND demoted is one loss, not two -")
# a row can carry BOTH current_retired and current_demoted only as a synthetic edge case (the
# real _store_state sets exactly one), but score()/pool() must not double-count it regardless.
both_row = _row("c9", "control", False, False, current_live=False,
               current_retired=True, current_demoted=True, current_absent=False)
retired_only = _row("c8", "control", False, False, current_live=False,
                    current_retired=True, current_demoted=False, current_absent=False)
neither = _row("c7", "control", False, True, current_live=True,
              current_retired=False, current_demoted=False, current_absent=False)
sc = sb.score([both_row, retired_only, neither])
check("two losses (one retired, one retired-AND-demoted), not three: the OR-count",
      sc["over_retraction_rate"] == round(2 / 3, 4), sc.get("over_retraction_rate"))
with tempfile.TemporaryDirectory() as tmp2:
    # one supersession row beside the controls: pool() folds per-run stale over the supersession
    # rows and a run has at least one in every real artifact
    sup_row = _row("s9", "explicit", False, True)
    run_a = _write(tmp2, "a.json", _blob({"nevertwice": _arm([sup_row, both_row, retired_only, neither])}))
    pooled_eager = sb.pool([run_a])
    Pe = pooled_eager["pooled_nevertwice"]
    check("the pooled over_retraction count is also 2 of 3, not 3 (no addition of the two flags)",
          (Pe["over_retraction"]["k"], Pe["over_retraction"]["n"]) == (2, 3), str(Pe["over_retraction"]))
    check("the cause split still counts each flag on its own row (retired:2, demoted:1) - that sum "
          "may exceed the loss count by design (one row names two causes), unlike over_retraction",
          pooled_eager["pooled_nevertwice"]["control_causes"] == {"retired": 2, "demoted": 1,
                                                                   "never_written": 0, "unranked": 0},
          str(pooled_eager["pooled_nevertwice"]["control_causes"]))

print("\n- _statement_text: a fact demoted into '## Previous statement' is found there, not served -")
served_body = ("---\ndate: 2026-06-01\n---\n\n# the upload limit\n\nThe upload limit is 100 MB.\n\n"
              "**Project:** [[p]]\n")
demoted_body = ("---\ndate: 2026-06-01\n---\n\n# the upload limit\n\nThe upload limit is 100 MB.\n\n"
               "## Previous statement\n- The upload limit is 25 MB.\n\n**Project:** [[p]]\n")
check("the currently-served text does not carry a fact only the old note stated",
      not sb._hit(["25 MB"], sb._served_text(demoted_body)))
check("_statement_text finds it in the Previous-statement block",
      sb._hit(["25 MB"], sb._statement_text(demoted_body)))
check("a note with no Previous-statement block: _statement_text is just the served text",
      sb._statement_text(served_body).strip() == sb._served_text(served_body).strip())
check("mutation check: served_text ALONE (the pre-K1b matcher) misses the demoted fact - "
      "this is exactly the false 'never written' _statement_text exists to prevent",
      not sb._hit(["25 MB"], sb._served_text(demoted_body)) and sb._hit(["25 MB"], sb._statement_text(demoted_body)))

print("\n- the corpus is addressable the way reproduce.py prints it -")
rel = "research/data/supersession_v1.json"
by_rel = sb.load_dataset(Path(rel))
by_abs = sb.load_dataset(ROOT / rel)
check("a repository-relative path loads (a run from any cwd, and every printed command)",
      by_rel["sha256"] == by_abs["sha256"])
check("the recorded path is repository-relative with forward slashes",
      by_rel["path"] == rel and by_abs["path"] == rel, f"{by_rel['path']!r} {by_abs['path']!r}")

print("\n- two engine runs that shared one store are not two runs -")
#: `sandbox_guard.isolate` makes one store per PROCESS, and the engine skips a session it has
#: already processed - so a second pass inside one interpreter reads what the first wrote and
#: judged. Measured on the explicit corpus with `--sleep` while `--runs 2` still looped in
#: process: run 1 served 440.0 chars/query and run 2 served 387.5, which is run 1's own
#: AFTER-SLEEP figure (2026-09-22). `--runs` now spawns a process per run; this is the check
#: that notices if it ever stops.
with tempfile.TemporaryDirectory() as tmp3:
    same = "/tmp/nevertwice_supersession_same"
    a = _write(tmp3, "a.json", dict(_blob({"nevertwice": _arm(engine_rows({"s0"}), 260.0)}),
                                    store=same))
    b = _write(tmp3, "b.json", dict(_blob({"nevertwice": _arm(engine_rows({"s1"}), 240.0)}),
                                    store=same))
    c = _write(tmp3, "c.json", dict(_blob({"nevertwice": _arm(engine_rows({"s1"}), 240.0)}),
                                    store=same + "_other"))
    try:
        sb.pool([a, b])
        check("pooling two runs from one store is refused", False, "pool returned")
    except ValueError as e:
        check("pooling two runs from one store is refused", True)
        check("and the refusal says why, not just that", "not a repeat" in str(e), str(e)[:90])
    try:
        sb.pool([a, c])
        check("two runs from different stores still pool", True)
    except ValueError as e:
        check("two runs from different stores still pool", False, str(e))
    # Every committed artifact predates the field. `None` must not read as "the same store".
    d = _write(tmp3, "d.json", _blob({"nevertwice": _arm(engine_rows({"s0"}), 260.0)}))
    e2 = _write(tmp3, "e.json", _blob({"nevertwice": _arm(engine_rows({"s1"}), 240.0)}))
    try:
        sb.pool([d, e2])
        check("files written before the field carry no store and pool as before", True)
    except ValueError as e:
        check("files written before the field carry no store and pool as before", False, str(e))

print("\n- a repeat that ingested nothing is not a repeat -")
#: Distinct stores and the work still not done - the shape the store check cannot see. Counted
#: as the ACTION, because the obvious state-shaped proxy fails: in the auditing session's copy
#: of the one-process bug BOTH runs reported `notes_written` 52, the second having inherited the
#: first's notes and counted them honestly, while taking 1.4 s against 124.6 (2026-09-22).
with tempfile.TemporaryDirectory() as tmp4:
    def _run(store, ingested, not_stored, stale_ids):
        arm = _arm(engine_rows(stale_ids), 250.0)
        arm["sessions_ingested"], arm["sessions_not_stored"] = ingested, not_stored
        arm["notes_written"] = 52          # identical in both, as it was in the real case
        return dict(_blob({"nevertwice": arm}), store=store)

    busy = _write(tmp4, "busy.json", _run("/tmp/one", 160, 0, {"s0"}))
    idle = _write(tmp4, "idle.json", _run("/tmp/two", 0, 160, {"s1"}))
    also = _write(tmp4, "also.json", _run("/tmp/three", 160, 0, {"s1"}))
    mute1 = _write(tmp4, "mute1.json", _run("/tmp/four", 0, 0, {"s0"}))
    mute2 = _write(tmp4, "mute2.json", _run("/tmp/five", 0, 0, {"s1"}))
    try:
        sb.pool([busy, idle])
        check("a run that ingested nothing is refused even with its own store", False,
              "pool returned")
    except ValueError as e:
        check("a run that ingested nothing is refused even with its own store", True)
        check("the refusal names what was OBSERVED - offered and not accepted",
              "offered [160]" in str(e) and "accepted none" in str(e), str(e)[:150])
        #: And does NOT name a cause. `stored: False` is one bit for four endings of
        #: `process_session` (already processed / cwd untracked / empty transcript / extraction
        #: failed), and the reason never reaches the artifact. A message that says "already
        #: processed" would point at the wrong thing exactly when the extractor is broken.
        check("and attributes no cause, because the artifact does not carry one",
              "already processed" not in str(e).split("one bit")[0], str(e)[:150])
    check("the state-shaped proxy would NOT have caught it (this is why the count is the action)",
          json.loads(busy.read_text(encoding="utf-8"))["arms"]["nevertwice"]["notes_written"]
          == json.loads(idle.read_text(encoding="utf-8"))["arms"]["nevertwice"]["notes_written"])
    try:
        sb.pool([busy, also])
        check("two runs that both ingested still pool", True)
    except ValueError as e:
        check("two runs that both ingested still pool", False, str(e))
    try:
        sb.pool([mute1, mute2])
        check("every run ingesting nothing stays legal - that is extractor silence, not a bug",
              True)
    except ValueError as e:
        check("every run ingesting nothing stays legal - that is extractor silence, not a bug",
              False, str(e))

print("\n- two runs of one commit must be one program -")
#: `--runs N` re-executes the stand per run, so the sources on disk are live state between
#: runs. The dangerous edit is the one that lands CLEANLY: a torn write fails loudly on its own,
#: a clean one silently makes run 2 a different program from run 1 (2026-09-22: a comment edited
#: in the stand while runs were in flight, judged safe only afterwards).
with tempfile.TemporaryDirectory() as tmp5:
    def _rev(store, sha):
        arm = _arm(engine_rows({"s0"}), 250.0)
        arm["sessions_ingested"], arm["sessions_not_stored"] = 160, 0
        b = dict(_blob({"nevertwice": arm}), store=store)
        if sha is not None:
            b["code_sha"] = sha
        return b

    old = _write(tmp5, "old.json", _rev("/tmp/a", "aaaaaaaaaaaa"))
    new = _write(tmp5, "new.json", _rev("/tmp/b", "bbbbbbbbbbbb"))
    same = _write(tmp5, "same.json", _rev("/tmp/c", "aaaaaaaaaaaa"))
    none1 = _write(tmp5, "n1.json", _rev("/tmp/d", None))
    none2 = _write(tmp5, "n2.json", _rev("/tmp/e", None))
    try:
        sb.pool([old, new])
        check("two runs from different source revisions are refused", False, "pool returned")
    except ValueError as e:
        check("two runs from different source revisions are refused", True)
        check("and the refusal prints both revisions",
              "aaaaaaaaaaaa" in str(e) and "bbbbbbbbbbbb" in str(e), str(e)[:140])
    try:
        sb.pool([old, same])
        check("the same revision twice still pools", True)
    except ValueError as e:
        check("the same revision twice still pools", False, str(e))
    try:
        sb.pool([none1, none2])
        check("artifacts written before the field carry none and pool as before", True)
    except ValueError as e:
        check("artifacts written before the field carry none and pool as before", False, str(e))

    #: The hash is over the package, not a hand-written list. The list it replaced was chosen as
    #: "what that session happened to edit" and missed `api.py`, through which this stand does
    #: both of its jobs. And the sort key is the repo-relative POSIX path, which is also the
    #: token, so the identity does not depend on the platform: `sorted(Path...)` compares a
    #: lowercased string with `\\` on Windows and the raw one with `/` on POSIX.
    before = sb._code_sha()
    api = ROOT / "nevertwice" / "api.py"
    body = api.read_bytes()
    try:
        api.write_bytes(body + b"\n# touched by the suite\n")
        check("touching api.py moves the hash (the hand-written list would not have)",
              sb._code_sha() != before)
    finally:
        api.write_bytes(body)
    check("and restoring it moves the hash back", sb._code_sha() == before)
    rels = sorted(q.relative_to(ROOT).as_posix()
                  for q in (ROOT / "nevertwice").rglob("*.py"))
    check("the walk reaches the subpackages, not just the top level",
          any("/" in r.split("nevertwice/", 1)[1] for r in rels), len(rels))
    check("the sort key is the string that is hashed, so the order is platform-free",
          rels == sorted(rels))

print("\n- B2: _mem0_errors_by_shape() splits run_mem0's own error rows by case shape -")
# `mem0` is not installed in this environment (checked: ModuleNotFoundError), so run_mem0()
# itself always short-circuits to the "not installed" blocker here - the split logic is
# extracted into its own function specifically so it is testable without the package.
b2_rows = [sb._blank({"id": "s1", "shape": "explicit"}) | {"error": "TimeoutError: x"},
          sb._blank({"id": "s2", "shape": "implicit"}) | {"error": "ValueError: y"},
          sb._blank({"id": "s3", "shape": "explicit"}),                     # no error
          sb._blank({"id": "c1", "shape": "control"}) | {"error": "KeyError: z"},
          sb._blank({"id": "c2", "shape": "control"})]                      # no error
split = sb._mem0_errors_by_shape(b2_rows)
check("2 supersession-shaped rows (explicit/implicit, both non-'control') errored",
      split["supersession"] == 2, str(split))
check("1 control-shaped row errored", split["control"] == 1, str(split))
check("rows with no 'error' key are never counted either way",
      split["supersession"] + split["control"] == sum(1 for r in b2_rows if r.get("error")),
      str(split))

print("\n- B2/K16(2): pool_other_arm sums mem0_errors and propagates a constituent run's "
      "'valid: false' onto the pooled artifact -")
mem0_run1 = {"rows": [_row("c1", "control", False, True)], **sb.score([_row("c1", "control", False, True)]),
            "errors": 2, "mem0_errors": {"supersession": 1, "control": 1},
            "seconds": 1.0, "config": "mem0 x"}
mem0_run2 = {"rows": [_row("c2", "control", False, True)], **sb.score([_row("c2", "control", False, True)]),
            "errors": 0, "mem0_errors": {"supersession": 0, "control": 0},
            "seconds": 1.0, "config": "mem0 x",
            "valid": False, "invalid_reason": "bypassed the pacer via requests: 1 request(s)"}
pooled_mem0 = sb.pool_other_arm([mem0_run1, mem0_run2])
check("mem0_errors sums across the pooled runs the same way 'errors' does",
      pooled_mem0["mem0_errors"] == {"supersession": 1, "control": 1}, str(pooled_mem0))
check("K16(2): a constituent run's own 'valid: false' survives pooling into the SAME "
      "container a claim's pointer resolves through (arms.mem0.X, never an untouched copy)",
      pooled_mem0.get("valid") is False and
      pooled_mem0.get("invalid_reason") == mem0_run2["invalid_reason"], str(pooled_mem0))
pooled_clean = sb.pool_other_arm([mem0_run1, {k: v for k, v in mem0_run2.items()
                                              if k not in ("valid", "invalid_reason")}])
check("no constituent run invalid -> 'valid' is never added (a caller checking "
      "'valid' not in pooled still means nothing to report)",
      "valid" not in pooled_clean, str(pooled_clean))

print("\n- item 4/K16(2): _one_run wires pacer.install()/attach() around EVERY arm, per "
      "arm, on the dict a claim's own pointer (arms.<name>.X) resolves through -")
import contextlib
import json as _json
import urllib.request

sys.path.insert(0, str(ROOT / "research"))
import _ollama_pacer as pacer  # noqa: E402


class _JsonResp:
    def __init__(self, payload):
        self._p = _json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **kw):
    return _JsonResp({"models": []})


def _fake_arm(cases, k):
    """Stands in for a real arm (nevertwice/mem0/naive/zep all need a model, a store or a
    package this environment may not have) - makes exactly ONE call through the paced
    urllib.request.urlopen, the same transport every real arm's own client eventually
    resolves through (F2), and returns a `blocked` row so `_one_run`'s detailed printing
    (which reads keys like `stale_rate` a fake arm has no reason to fabricate) is skipped -
    `pacer.attach` runs BEFORE that check either way."""
    import urllib.request as ur
    with ur.urlopen("http://127.0.0.1:11434/api/tags") as r:
        r.read()
    return {"blocked": "fake arm - pacer wiring test only, not a real measurement"}


@contextlib.contextmanager
def _isolated_pacer():
    # (в)/MX3: a bare `assert` here means any regression that leaves the pacer installed
    # across a block boundary reddens this suite with an uncaught Traceback, not a named
    # FAIL line. check() first (non-fatal), then self-heal so the block that follows still
    # runs on its own merits instead of cascading into more crashes.
    check("pacer starts uninstalled entering this block", not pacer.installed())
    if pacer.installed():
        pacer.uninstall()
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer._reset_for_tests()


class _FakeArgs:
    k = 5
    sleep = False
    third_session = False


FAKE_CASE = [{"id": "f1", "shape": "control", "sessions": [["x"]], "query": "x",
             "current": []}]
FAKE_DATA = {"name": "fake", "sha256": "0" * 64, "path": "fake.json"}

saved_arms = dict(sb.ARMS)
sb.ARMS["fakearm"] = _fake_arm
try:
    with _isolated_pacer():
        urllib.request.urlopen = _fake_urlopen
        out = sb._one_run(FAKE_DATA, FAKE_CASE, _FakeArgs(), ["fakearm"])
        arm_out = out["arms"]["fakearm"]
        check("ollama_transport is written on THIS arm's own dict (install() actually "
              "wrapped the call, attach() actually recorded it)",
              "ollama_transport" in arm_out, str(sorted(arm_out)))
        check("calls == 1 (the fake arm's one urlopen call)",
              arm_out.get("ollama_transport", {}).get("calls") == 1, str(arm_out))

    print("\n- item 4 mutations: install()/attach() removed from _one_run (in-process, "
          "sb.pacer IS the _ollama_pacer module) -")
    saved_install, saved_attach = sb.pacer.install, sb.pacer.attach
    sb.pacer.install = lambda: None                    # mutation: install() removed
    with _isolated_pacer():
        urllib.request.urlopen = _fake_urlopen
        out_no_install = sb._one_run(FAKE_DATA, FAKE_CASE, _FakeArgs(), ["fakearm"])
        check("mutation 'install() removed': no ollama_transport at all on the arm's own "
              "dict (nothing ever got paced - would FAIL the presence check above)",
              "ollama_transport" not in out_no_install["arms"]["fakearm"],
              str(sorted(out_no_install["arms"]["fakearm"])))
    sb.pacer.install = saved_install

    sb.pacer.attach = lambda *a, **k: None              # mutation: attach() removed
    with _isolated_pacer():
        urllib.request.urlopen = _fake_urlopen
        out_no_attach = sb._one_run(FAKE_DATA, FAKE_CASE, _FakeArgs(), ["fakearm"])
        check("mutation 'attach() removed': no ollama_transport on the arm's own dict "
              "(the pacer paced the call but the artifact never learns it)",
              "ollama_transport" not in out_no_attach["arms"]["fakearm"],
              str(sorted(out_no_attach["arms"]["fakearm"])))
    sb.pacer.attach = saved_attach
    check("sb.pacer.install/attach are restored to the real functions after the mutations",
          sb.pacer.install is saved_install and sb.pacer.attach is saved_attach)
finally:
    sb.ARMS.clear()
    sb.ARMS.update(saved_arms)
check("ARMS is restored to exactly its original registered arms",
      set(sb.ARMS) == set(saved_arms), str(sorted(sb.ARMS)))


@contextlib.contextmanager
def _crash_guard(*names: str):
    """K19 (в)/MK18a (the auditor's finding): a mutation block that raises - a plausible
    regression, not just the deliberate ones below - must redden every check it would
    otherwise have made, by name, and let the REST of the suite keep running; without
    this an uncaught exception here crashes the whole process and silently skips every
    check after it, the worst failure mode a test harness can have."""
    try:
        yield
    except Exception as exc:                                          # noqa: BLE001
        for n in names:
            check(n, False, repr(exc))


print("\n- K18/P1(a): _mem0_cap_verdict() - exactly at the cap vs one over, each axis -")
check("exactly at the cap (2 supersession, 1 control) is valid",
      sb._mem0_cap_verdict({"supersession": 2, "control": 1}) is None)
check("one supersession error OVER the cap (3) is invalid, named by count",
      sb._mem0_cap_verdict({"supersession": 3, "control": 0}) is not None)
check("one control error OVER the cap (2) is invalid, named by count",
      sb._mem0_cap_verdict({"supersession": 0, "control": 2}) is not None)
reason_sup = sb._mem0_cap_verdict({"supersession": 3, "control": 0})
check("the reason names both counts and both caps",
      # MK18a (the auditor's finding): `reason_sup` can be None under a plausible
      # regression (e.g. the cap check itself broken) - "3" in None raises TypeError
      # OUTSIDE check(), which would crash the whole suite process and silently skip
      # every check after it. `reason_sup is not None` short-circuits before that.
      reason_sup is not None and "3" in reason_sup and
      str(sb.MEM0_ERR_CAP_SUPERSESSION) in reason_sup and
      "0" in reason_sup and str(sb.MEM0_ERR_CAP_CONTROL) in reason_sup, reason_sup)

print("\n- K18 mutation: cap +1 - the SAME 3-supersession-error case now WRONGLY reads valid -")
saved_cap_sup = sb.MEM0_ERR_CAP_SUPERSESSION
try:
    sb.MEM0_ERR_CAP_SUPERSESSION = 3                        # mutation: cap raised by 1
    with _crash_guard("mutation 'cap +1': 3 supersession errors now WRONGLY verdicts "
                      "valid (would FAIL the 'one supersession error OVER the cap' "
                      "check above)"):
        check("mutation 'cap +1': 3 supersession errors now WRONGLY verdicts valid "
              "(would FAIL the 'one supersession error OVER the cap' check above)",
              sb._mem0_cap_verdict({"supersession": 3, "control": 0}) is None)
finally:
    sb.MEM0_ERR_CAP_SUPERSESSION = saved_cap_sup
check("MEM0_ERR_CAP_SUPERSESSION is restored", sb.MEM0_ERR_CAP_SUPERSESSION == saved_cap_sup)

print("\n- K18/P1(a)+(b): the cap is per RUN, never re-applied to pool_other_arm's SUM -")
# two constituent mem0 runs, EACH exactly at the cap (2 supersession errors, 0 control) so
# NEITHER run's own result carries valid=False - but their SUM (4) is over the cap.
mem0_run_a = {"rows": [_row("s1", "explicit", False, True)], **sb.score([_row("s1", "explicit", False, True)]),
             "errors": 2, "mem0_errors": {"supersession": 2, "control": 0},
             "seconds": 1.0, "config": "mem0 x"}
mem0_run_b = {"rows": [_row("s2", "explicit", False, True)], **sb.score([_row("s2", "explicit", False, True)]),
             "errors": 2, "mem0_errors": {"supersession": 2, "control": 0},
             "seconds": 1.0, "config": "mem0 x"}
check("setup: neither constituent run is itself over cap",
      sb._mem0_cap_verdict(mem0_run_a["mem0_errors"]) is None and
      sb._mem0_cap_verdict(mem0_run_b["mem0_errors"]) is None)
pooled_sum = sb.pool_other_arm([mem0_run_a, mem0_run_b])
check("the pooled SUM is 4, over the per-run cap of 2 (setup: the scenario is real)",
      pooled_sum["mem0_errors"]["supersession"] == 4, str(pooled_sum["mem0_errors"]))
check("per_run_mem0_errors carries each constituent's own split beside the sum",
      pooled_sum["per_run_mem0_errors"] == [mem0_run_a["mem0_errors"], mem0_run_b["mem0_errors"]],
      str(pooled_sum.get("per_run_mem0_errors")))
check("the pool STAYS VALID: the cap is per-run, not re-applied to the sum",
      "valid" not in pooled_sum, str(pooled_sum))

print("\n- K18 mutation: the cap applied to the SUM - the same pool now WRONGLY invalid -")
def _pool_other_arm_cap_on_sum(results):
    """Mutation: as `pool_other_arm`, but the P1 cap is (wrongly) re-applied to the
    pooled SUM, not left per-run only."""
    out = sb.pool_other_arm(results)
    reason = sb._mem0_cap_verdict(out["mem0_errors"])
    if reason is not None:
        out = dict(out)
        out["valid"] = False
        out["invalid_reason"] = reason
    return out
with _crash_guard("mutation 'cap applied to the sum': the SAME pool now WRONGLY reads "
                  "invalid (would FAIL the 'the pool STAYS VALID' check above)"):
    mutated_pool = _pool_other_arm_cap_on_sum([mem0_run_a, mem0_run_b])
    check("mutation 'cap applied to the sum': the SAME pool now WRONGLY reads invalid "
          "(would FAIL the 'the pool STAYS VALID' check above)",
          mutated_pool.get("valid") is False, str(mutated_pool))

print("\n- K19: _double_reading() - a pair where the CORRECTLY-defined failure reading "
      "flips the sign (the auditor's correction of K18: the failure reading must be the "
      "WORST case, stale_returned=True for a supersession-shaped error - not the blank/"
      "as-recorded row K18 used, which reads as a SUCCESS on stale_rate) -")
# nevertwice reads stale on 1/3 cases, current on none. mem0 has 2 supersession-shaped
# errors + 1 real clean case (stale_returned=False, current_returned=True - chosen so
# mem0's OWN current_rate_diff does not ALSO flip: 0.333 (failure) vs 1.0 (success) are
# both above nevertwice's 0.0, same sign either way - this test isolates stale_rate_diff).
# Failure reading (K19-correct): the 2 errors become stale_returned=True (worst case) ->
# mem0 stale_rate 2/3=0.667, diff = 0.667-0.333 = +0.333 (mem0 WORSE). Success reading:
# the 2 errors become stale_returned=False (best case) -> mem0 stale_rate 0/3=0.0, diff =
# 0.0-0.333 = -0.333 (mem0 BETTER) - the sign flips. Under K18's OWN (buggy) code this
# pair could never move at all: the blank row it used AS the failure reading already had
# stale_returned=False, identical to the success reading, so stale_rate_diff was always 0
# either way (the bug this test pins).
flip_first = {
    "nevertwice": {"c1": _row("c1", "explicit", True, False),
                  "c2": _row("c2", "explicit", False, False),
                  "c3": _row("c3", "explicit", False, False)},
    "mem0": {"c1": _row("c1", "explicit", False, False, error="E1"),
            "c2": _row("c2", "explicit", False, False, error="E2"),
            "c3": _row("c3", "explicit", False, True)},
}
pf_flip, ps_flip, reason_flip = sb._double_reading(flip_first)
check("errors-as-failure reading: mem0's stale_rate_diff is positive (worse than "
      "nevertwice) - 0.6667 vs nevertwice's 0.3333", pf_flip[0]["stale_rate_diff"] > 0,
      str(pf_flip))
check("errors-as-success reading: the SAME pair's stale_rate_diff is now negative "
      "(better than nevertwice) - the sign flipped", ps_flip[0]["stale_rate_diff"] < 0,
      str(ps_flip))
check("the pool is marked invalid, naming stale_rate_diff by name",
      reason_flip is not None and "sign" in reason_flip and "stale_rate_diff" in reason_flip,
      str(reason_flip))

print("\n- K19: widened_ci for stale actually WIDENS (lo < hi) - under K18's bug lo==hi "
      "always, since both readings agreed on the blank row's stale_returned=False -")
widened_rows = [_row("c1", "explicit", False, False, error="E1"),
                _row("c2", "explicit", False, True), _row("c3", "explicit", False, True)]
wci = sb._mem0_widened_ci(widened_rows)
check("stale's widened interval genuinely widens (lo strictly < hi)",
      wci["stale"][0] < wci["stale"][1], str(wci.get("stale")))
check("current's widened interval also widens", wci["current"][0] < wci["current"][1],
      str(wci.get("current")))

print("\n- K19b (the auditor's finding): widened_ci is EXACT per direction, not just "
      "lo<hi - stale (lower-is-better) takes its lo from the SUCCESS reading and its hi "
      "from the FAILURE reading; current (higher-is-better) the other way round -")
k19b_failure_score = sb.score(sb._mem0_errors_as_failure(widened_rows))
k19b_success_score = sb.score(sb._mem0_errors_as_success(widened_rows))
check("stale: lo == Wilson_lo(success reading), hi == Wilson_hi(failure reading)",
      wci["stale"] == [k19b_success_score["stale_ci"][0], k19b_failure_score["stale_ci"][1]],
      str((wci["stale"], k19b_success_score["stale_ci"], k19b_failure_score["stale_ci"])))
check("current (opposite direction): lo == Wilson_lo(failure reading), "
      "hi == Wilson_hi(success reading)",
      wci["current"] == [k19b_failure_score["current_ci"][0], k19b_success_score["current_ci"][1]],
      str((wci["current"], k19b_failure_score["current_ci"], k19b_success_score["current_ci"])))

print("\n- K19b mutation: direction ignored (stale wrongly read as higher-is-better, "
      "like current) -")
saved_direction = dict(sb._WIDENED_CI_DIRECTION)
sb._WIDENED_CI_DIRECTION["stale"] = "higher"            # mutation: direction ignored/flipped
with _crash_guard("mutation 'stale direction flipped': widened_ci['stale'] no longer "
                  "matches the exact per-direction formula (would FAIL the K19b "
                  "exact-match check above)"):
    wci_dir_mut = sb._mem0_widened_ci(widened_rows)
    check("mutation 'stale direction flipped': widened_ci['stale'] no longer matches "
          "the exact per-direction formula (would FAIL the K19b exact-match check above)",
          wci_dir_mut["stale"] != [k19b_success_score["stale_ci"][0], k19b_failure_score["stale_ci"][1]],
          str(wci_dir_mut["stale"]))
sb._WIDENED_CI_DIRECTION.clear()
sb._WIDENED_CI_DIRECTION.update(saved_direction)
check("_WIDENED_CI_DIRECTION is restored", sb._WIDENED_CI_DIRECTION == saved_direction)

print("\n- _double_reading() - a pair that does NOT flip stays valid (nevertwice reads "
      "stale on EVERY case, so mem0's own error handling never moves it out ahead) -")
noflip_first = {
    "nevertwice": {"c1": _row("c1", "explicit", True, False),
                  "c2": _row("c2", "explicit", True, False),
                  "c3": _row("c3", "explicit", True, False)},
    "mem0": {"c1": _row("c1", "explicit", False, False, error="E1"),
            "c2": _row("c2", "explicit", False, True),
            "c3": _row("c3", "explicit", False, True)},
}
pf_no, ps_no, reason_no = sb._double_reading(noflip_first)
check("stale_rate_diff agrees in sign both readings (mem0 always ahead of nevertwice's "
      "1.0)", pf_no[0]["stale_rate_diff"] < 0 and ps_no[0]["stale_rate_diff"] < 0,
      str((pf_no[0]["stale_rate_diff"], ps_no[0]["stale_rate_diff"])))
check("current_rate_diff agrees in sign both readings too",
      pf_no[0]["current_rate_diff"] > 0 and ps_no[0]["current_rate_diff"] > 0,
      str((pf_no[0]["current_rate_diff"], ps_no[0]["current_rate_diff"])))
check("both readings agree on which side of 0.05 McNemar falls",
      (pf_no[0]["p_mcnemar"] < 0.05) == (ps_no[0]["p_mcnemar"] < 0.05),
      str((pf_no[0]["p_mcnemar"], ps_no[0]["p_mcnemar"])))
check("no disagreement -> the pool stays valid (reason is None)", reason_no is None,
      str(reason_no))

print("\n- K19 mutation: failure reading = the blank row again (K18's own bug, "
      "reintroduced) - the SAME flip-scenario no longer caught -")
saved_errors_as_failure = sb._mem0_errors_as_failure
sb._mem0_errors_as_failure = lambda rows: rows          # mutation: back to the blank row
with _crash_guard("mutation 'failure reading = the blank row again': the SAME flip "
                  "that FAILED above now reads valid (reason is None) - would FAIL the "
                  "'the pool is marked invalid' check above"):
    _, _, mutated_reason = sb._double_reading(flip_first)
    check("mutation 'failure reading = the blank row again': the SAME flip that FAILED "
          "above now reads valid (reason is None) - would FAIL the 'the pool is marked "
          "invalid' check above", mutated_reason is None, str(mutated_reason))
sb._mem0_errors_as_failure = saved_errors_as_failure
check("_mem0_errors_as_failure is restored to the real function",
      sb._mem0_errors_as_failure is saved_errors_as_failure)

print("\n- K18 (в) MS4: nevertwice_after_sleep inherits its own row's valid/invalid_reason -")
def _fake_nevertwice_with_after_sleep(cases, k, sleep=False, third_session=False):
    res = _arm([_row("c1", "explicit", True, False)])
    res["valid"] = False
    res["invalid_reason"] = "synthetic MS4 fixture: simulated bypass on the main reading"
    after = _arm([_row("c1", "explicit", False, True)])
    after["adjudication"] = {"pairs": 1, "judged": 1, "tokens_spent": 10, "budget": 100,
                             "replaces": 0, "separate": 1, "left": 0,
                             "prompt_tokens": 5, "eval_tokens": 5}
    res["after_sleep"] = after
    return res

class _FakeArgsSleep:
    k = 5
    sleep = True
    third_session = False

saved_arms2 = dict(sb.ARMS)
sb.ARMS["nevertwice"] = _fake_nevertwice_with_after_sleep
try:
    with _isolated_pacer():
        out_sleep = sb._one_run(FAKE_DATA, FAKE_CASE, _FakeArgsSleep(), ["nevertwice"])
        main_entry = out_sleep["arms"]["nevertwice"]
        after_entry = out_sleep["arms"]["nevertwice_after_sleep"]
        check("the main reading's own valid=False survives (unchanged by this fix)",
              main_entry.get("valid") is False, str(main_entry))
        check("MS4: nevertwice_after_sleep is a SEPARATE out['arms'] key - a pointer into "
              "it resolves through THIS container, never the sibling 'nevertwice' one, so "
              "it must carry its own copy of valid/invalid_reason",
              after_entry.get("valid") is False and
              after_entry.get("invalid_reason") == main_entry["invalid_reason"],
              str(after_entry))
finally:
    sb.ARMS.clear()
    sb.ARMS.update(saved_arms2)
check("ARMS is restored after the MS4 fixture too", set(sb.ARMS) == set(saved_arms))

print("\n- K25 (the auditor, item 9C): a constituent's own 'valid: false' reaches the ROOT, "
      "not only its own arm - pooled_nevertwice(_after_sleep).* and pairs[*] sit OUTSIDE "
      "any single arm's dict and used to stay restorable no matter which constituent was "
      "invalid (P2: 'one invalid run inside a --pool invalidates the whole pool') -")
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402

# real pointers from research/evidence_manifest.json (raw: research/results/supersession_v1.json)
PTR_STALE = "pooled_nevertwice.stale.rate"                    # supersession.nevertwice.stale_rate
PTR_PAIR_MEM0_NV = "pairs[1].p_mcnemar"                        # supersession.mem0_vs_nevertwice.p_mcnemar
PTR_AFTER_SLEEP_STALE = "pooled_nevertwice_after_sleep.stale.rate"  # supersession.nevertwice_after_sleep.stale_rate


def _refused(res, ptr):
    return rm.row_refusal(res, ptr, code_time=0, head=None, produced_by=[])


with tempfile.TemporaryDirectory() as tmp_k25:
    # (d) a clean pool - baseline, no constituent invalid anywhere
    k25_run1 = _write(tmp_k25, "run1.json",
                      _blob({"nevertwice": _arm(engine_rows({"s0"}), 260.0),
                            "naive": _arm(naive_rows(), 200.0)}))
    k25_run2_clean = _write(tmp_k25, "run2.json",
                            _blob({"nevertwice": _arm(engine_rows({"s0", "s1"}), 240.0)}))
    k25_mem0_clean = _write(tmp_k25, "mem0.json", _blob({"mem0": _arm(mem0_rows(), 450.0)}))
    res_d = sb.pool([k25_run1, k25_run2_clean], [k25_mem0_clean])
    check("(d) clean pool: root carries no 'valid'", "valid" not in res_d, str(res_d.get("valid")))
    check("(d) clean pool: pooled_nevertwice.stale.rate restorable",
          _refused(res_d, PTR_STALE) is None, str(_refused(res_d, PTR_STALE)))
    check("(d) clean pool: pairs[1].p_mcnemar (mem0 vs nevertwice) restorable",
          _refused(res_d, PTR_PAIR_MEM0_NV) is None, str(_refused(res_d, PTR_PAIR_MEM0_NV)))
    check("(d) setup: pairs[1] really is mem0 vs nevertwice",
          {res_d["pairs"][1]["a"], res_d["pairs"][1]["b"]} == {"mem0", "nevertwice"},
          str(res_d["pairs"][1]))

    # (a) an invalid engine run2
    k25_run2_bad = _write(
        tmp_k25, "run2_bad.json",
        _blob({"nevertwice": dict(_arm(engine_rows({"s0", "s1"}), 240.0), valid=False,
                                  invalid_reason="ollama_transport.bypass_calls=1")}))
    res_a = sb.pool([k25_run1, k25_run2_bad], [k25_mem0_clean])
    check("(a) engine run2 invalid: root is marked invalid",
          res_a.get("valid") is False, str(res_a.get("valid")))
    check("(a) engine run2 invalid: pooled_nevertwice.stale.rate is refused",
          _refused(res_a, PTR_STALE) is not None, str(_refused(res_a, PTR_STALE)))
    check("(a) engine run2 invalid: pairs[1].p_mcnemar is refused too",
          _refused(res_a, PTR_PAIR_MEM0_NV) is not None, str(_refused(res_a, PTR_PAIR_MEM0_NV)))

    # (b) an invalid mem0 --with file
    k25_mem0_bad = _write(
        tmp_k25, "mem0_bad.json",
        _blob({"mem0": dict(_arm(mem0_rows(), 450.0), valid=False,
                            invalid_reason="P1 (.loop/PREREG-V2-2026-09-24.md): 3 mem0 error(s) "
                                          "among supersession cases (cap 2)")}))
    res_b = sb.pool([k25_run1, k25_run2_clean], [k25_mem0_bad])
    check("(b) mem0 invalid: root is marked invalid", res_b.get("valid") is False, str(res_b.get("valid")))
    check("(b) mem0 invalid: pooled_nevertwice.stale.rate is refused",
          _refused(res_b, PTR_STALE) is not None, str(_refused(res_b, PTR_STALE)))
    check("(b) mem0 invalid: pairs[1].p_mcnemar is refused too",
          _refused(res_b, PTR_PAIR_MEM0_NV) is not None, str(_refused(res_b, PTR_PAIR_MEM0_NV)))

    # (c) an invalid after-sleep run
    k25_run1_after = _write(
        tmp_k25, "run1_after.json",
        _blob({"nevertwice": _arm(engine_rows({"s0"}), 260.0),
              "nevertwice_after_sleep": dict(_arm(engine_rows({"s0", "s1"}), 220.0), valid=False,
                                             invalid_reason="ollama_transport.failed_outcomes=2")}))
    res_c = sb.pool([k25_run1_after, k25_run2_clean])
    check("(c) after-sleep invalid: root is marked invalid", res_c.get("valid") is False, str(res_c.get("valid")))
    check("(c) after-sleep invalid: pooled_nevertwice_after_sleep.stale.rate is refused",
          _refused(res_c, PTR_AFTER_SLEEP_STALE) is not None,
          str(_refused(res_c, PTR_AFTER_SLEEP_STALE)))
    check("(c) setup: the main-reading pointer is untouched by the after-sleep invalidity "
          "(the row itself, not root-independent) - pooled_nevertwice.stale.rate is refused "
          "too, but via ROOT propagation, since root propagation does not distinguish readings",
          _refused(res_c, PTR_STALE) is not None)

    print("\n- K25 mutation 'root propagation removed': the constituent-invalidity block "
          "(everything computed into constituent_invalid) skipped entirely -")
    # Simpler and more faithful than re-deriving pool()'s whole body: call the REAL pool() on
    # the (a) scenario, then strip exactly what K25's root-propagation block added (root
    # 'valid'/'invalid_reason' - nothing else on the artifact changes), and show the SAME
    # checks that passed above now fail. This is exactly the artifact a reverted
    # `constituent_invalid`/root-write block would have produced, since every OTHER field is
    # untouched by that block.
    res_a_mut = json.loads(json.dumps(res_a))
    del res_a_mut["valid"]
    del res_a_mut["invalid_reason"]
    check("mutation 'root propagation removed': pooled_nevertwice.stale.rate is now WRONGLY "
          "restorable (would FAIL the (a) 'pooled_nevertwice.stale.rate is refused' check "
          "above)", _refused(res_a_mut, PTR_STALE) is None, str(_refused(res_a_mut, PTR_STALE)))
    check("mutation 'root propagation removed': pairs[1].p_mcnemar is now WRONGLY "
          "restorable (would FAIL the (a) 'pairs[1].p_mcnemar is refused too' check above)",
          _refused(res_a_mut, PTR_PAIR_MEM0_NV) is None, str(_refused(res_a_mut, PTR_PAIR_MEM0_NV)))

    print("\n- K25 mutation 'the --with constituents not checked': pool() built with mem0's "
          "own constituent invalidity ignored -")
    # Same idea, isolated to the --with axis: strip the root flag from the (b) scenario (mem0
    # invalid via --with) specifically, showing (b)'s own checks would go red without it.
    res_b_mut = json.loads(json.dumps(res_b))
    del res_b_mut["valid"]
    del res_b_mut["invalid_reason"]
    check("mutation 'the --with constituents not checked': pooled_nevertwice.stale.rate is "
          "now WRONGLY restorable (would FAIL the (b) checks above)",
          _refused(res_b_mut, PTR_STALE) is None, str(_refused(res_b_mut, PTR_STALE)))

    print("\n- K25 mutation 'the run-file root not checked': a file's OWN root valid:false "
          "(not nested in any arm) never propagated -")
    k25_root_bad = _write(
        tmp_k25, "root_bad.json",
        dict(_blob({"nevertwice": _arm(engine_rows({"s0", "s1"}), 240.0)}),
            valid=False, invalid_reason="synthetic: this FILE's own root is invalid, not any arm"))
    res_root = sb.pool([k25_run1, k25_root_bad], [k25_mem0_clean])
    check("K25 catches a file-root-level valid:false too: the pool's root is invalid",
          res_root.get("valid") is False, str(res_root.get("valid")))
    check("K25 catches a file-root-level valid:false too: pooled_nevertwice.stale.rate refused",
          _refused(res_root, PTR_STALE) is not None, str(_refused(res_root, PTR_STALE)))
    res_root_mut = json.loads(json.dumps(res_root))
    del res_root_mut["valid"]
    del res_root_mut["invalid_reason"]
    check("mutation 'the run-file root not checked': pooled_nevertwice.stale.rate is now "
          "WRONGLY restorable (would FAIL the file-root check above)",
          _refused(res_root_mut, PTR_STALE) is None, str(_refused(res_root_mut, PTR_STALE)))

    print("\n- K25/P5.2 (the auditor): the file-root check at the --WITH-file site "
          "specifically - `k25_root_bad` above went through engine_files (the FIRST "
          "`if blob.get('valid') is False` block in pool()); a --with file's own root is a "
          "SEPARATE block, and a mutation removing only THAT one was not caught by any "
          "existing check -")
    # a --with file whose ROOT is invalid but whose ARM (zep - not used by k25_run1/run2, so
    # this is its only constituent) is perfectly clean - the ONLY possible source of root
    # invalidity in this scenario is the --with-file-root check.
    k25_with_root_bad = _write(
        tmp_k25, "with_root_bad.json",
        dict(_blob({"zep": _arm(naive_rows(), 200.0)}),
            valid=False, invalid_reason="synthetic: this --WITH FILE's own root is invalid"))
    res_p52 = sb.pool([k25_run1, k25_run2_clean], [k25_with_root_bad])
    check("P5.2 setup: the zep arm carried by the --with file is itself clean",
          res_p52["arms"]["zep"].get("valid") is not False, str(res_p52["arms"]["zep"]))
    check("P5.2: the pool's root is invalid anyway, from the --with file's own root",
          res_p52.get("valid") is False, str(res_p52.get("valid")))
    check("P5.2: the reason names the --with file",
          "with_root_bad.json" in (res_p52.get("invalid_reason") or ""),
          res_p52.get("invalid_reason"))
    check("P5.2: pooled_nevertwice.stale.rate is refused via the root",
          _refused(res_p52, PTR_STALE) is not None, str(_refused(res_p52, PTR_STALE)))
    res_p52_mut = json.loads(json.dumps(res_p52))
    del res_p52_mut["valid"]
    del res_p52_mut["invalid_reason"]
    check("P5.2 mutation 'the --with-file-root check removed': pooled_nevertwice.stale.rate "
          "is now WRONGLY restorable (would FAIL the P5.2 checks above) - this is the "
          "mutation the auditor found surviving, on the --with-file site specifically, not "
          "the engine-file one the earlier 'run-file root not checked' mutation covers",
          _refused(res_p52_mut, PTR_STALE) is None, str(_refused(res_p52_mut, PTR_STALE)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
