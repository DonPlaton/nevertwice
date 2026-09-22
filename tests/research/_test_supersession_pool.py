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
    check("a blocked arm is left out rather than carried as a number", "mem0" not in res2["arms"])
    check("without mem0 there is no per-run pairing", res2["pairs_per_engine_run"] == [])

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

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
