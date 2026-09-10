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
                         current_retired=retired, current_absent=False))
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
    dup = _write(tmp, "dup.json", _blob({"naive": _arm(naive_rows())}))
    try:
        sb.pool([run1], [dup])
        check("an arm present in two files is refused, not silently chosen", False)
    except ValueError as e:
        check("an arm present in two files is refused, not silently chosen", "two result files" in str(e))
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

print("\n- the corpus is addressable the way reproduce.py prints it -")
rel = "research/data/supersession_v1.json"
by_rel = sb.load_dataset(Path(rel))
by_abs = sb.load_dataset(ROOT / rel)
check("a repository-relative path loads (a run from any cwd, and every printed command)",
      by_rel["sha256"] == by_abs["sha256"])
check("the recorded path is repository-relative with forward slashes",
      by_rel["path"] == rel and by_abs["path"] == rel, f"{by_rel['path']!r} {by_abs['path']!r}")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
