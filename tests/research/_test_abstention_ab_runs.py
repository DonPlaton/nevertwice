"""`research/abstention_ab.py --runs N`: pooled at the existing keys, per-run spread beside them.

Reuses `research/supersession_bench.py`'s convention (`_fold_engine_runs`/`pool`), on the
narrower surface this stand needs: no `--pool`-from-files entry point, one subprocess per run
so a store or a `.prompt_recall` state from run 1 is never read by run 2 (the exact bug
`supersession_bench.py` found on 2026-09-22 when its own `--runs N` still looped in-process).

Two levels of proof: `_pool_dict`/`_pool_runs` unit-tested directly on synthetic dicts (no
process, no model, no store), then one real end-to-end pass through `--part remine --runs 2`
- the one part of this stand that touches no model and no store at all, so it is safe to run
for real under the no-Ollama/no-GPU rule.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

sys.path.insert(0, str(ROOT / "research"))
import abstention_ab as ab  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- _pool_leaf / _pool_dict: the pooled mean lands at the EXISTING key -")
run1 = {"threshold": 0.0, "mean_chars": 10.0, "cases": 5, "label": "off"}
run2 = {"threshold": 0.0, "mean_chars": 14.0, "cases": 5, "label": "off"}
pooled_row = ab._pool_dict([run1, run2])
check("mean_chars pooled to the arithmetic mean, at the SAME key",
      pooled_row["mean_chars"] == 12.0, pooled_row)
check("threshold (identical across runs) pools to itself", pooled_row["threshold"] == 0.0)
check("cases (an int) pools the same way", pooled_row["cases"] == 5.0)
check("a non-numeric field (label) is kept from run 1, not averaged",
      pooled_row["label"] == "off")
check("no existing key was renamed: the pooled dict has exactly run 1's key set plus siblings",
      set(pooled_row) == set(run1) | {"mean_chars_per_run", "cases_per_run", "threshold_per_run"},
      sorted(pooled_row))

print("\n- the sibling key carries the per-run values AND the spread, named f'{key}_per_run' -")
sib = pooled_row["mean_chars_per_run"]
check("per-run values, in run order", sib["values"] == [10.0, 14.0], sib)
check("spread: min", sib["min"] == 10.0)
check("spread: max", sib["max"] == 14.0)

print("\n- mutation: pooling the sibling INTO the existing key would move the pointer -")
# What (1) rules out: writing the pooled mean under a NEW name instead of the old one, which
# is exactly the shape change that would break every claim already pointing at `mean_chars`.
check("mutation: a differently-named pooled key ('mean_chars_pooled') is NOT what the real "
      "code writes - the real code keeps the original name, this only shows what breaking it "
      "would look like", "mean_chars_pooled" not in pooled_row and "mean_chars" in pooled_row)


print("\n- list-of-dict rows are pooled by their OWN id field, not by position -")
rows_a = [{"threshold": 0.0, "mean_chars": 10.0}, {"threshold": 0.5, "mean_chars": 20.0}]
rows_b = [{"threshold": 0.5, "mean_chars": 24.0}, {"threshold": 0.0, "mean_chars": 14.0}]  # reversed
pooled_sweep = ab._pool_dict([{"sweep": rows_a}, {"sweep": rows_b}])["sweep"]
by_thr = {r["threshold"]: r for r in pooled_sweep}
check("two rows come back, one per threshold", len(pooled_sweep) == 2, pooled_sweep)
check("threshold 0.0 paired the RIGHT two rows across runs (10.0 and 14.0 -> mean 12.0)",
      by_thr[0.0]["mean_chars"] == 12.0, by_thr)
check("threshold 0.5 paired the RIGHT two rows across runs (20.0 and 24.0 -> mean 22.0)",
      by_thr[0.5]["mean_chars"] == 22.0, by_thr)

print("\n- mutation: pairing rows by POSITION instead of by id gives the WRONG pooled numbers -")
# rows_b is rows_a's own threshold order, reversed - the id-matched code above got it right
# despite that; a naive zip() would pair rows_a[0] (thr 0.0) with rows_b[0] (thr 0.5).
naive = [ab._pool_dict([ra, rb]) for ra, rb in zip(rows_a, rows_b)]
check("mutation: positional pairing pools threshold 0.0's row against threshold 0.5's row "
      "(would silently reintroduce a real bug: a --limit or a reordered THRESHOLDS list "
      "between two runs would then pool the wrong pairs together)",
      naive[0]["threshold"] != 0.0 or naive[0]["mean_chars"] != by_thr[0.0]["mean_chars"],
      naive[0])


print("\n- _pool_runs refuses to pool runs from different source revisions -")
same_sha = [{"code_sha": "aaaa", "x": 1}, {"code_sha": "aaaa", "x": 3}]
check("same code_sha: pools fine", ab._pool_runs(same_sha)["x"] == 2.0)
none_sha = [{"code_sha": None, "x": 1}, {"code_sha": None, "x": 3}]
check("both code_sha None (no git checkout): pools as before, the exemption "
      "supersession_bench.py grants a pre-field artifact", ab._pool_runs(none_sha)["x"] == 2.0)

print("\n- mutation: two runs pooled under DIFFERENT code_sha are refused, not silently folded -")
diff_sha = [{"code_sha": "aaaa", "x": 1}, {"code_sha": "bbbb", "x": 3}]
try:
    ab._pool_runs(diff_sha)
    check("mutation: pooling different revisions raises (would silently reintroduce the "
          "'edit landed mid-loop' bug supersession_bench.py's own guard exists for)", False)
except ValueError as e:
    check("mutation: pooling different revisions raises", True)
    check("and names both revisions, not just that they differ",
          "aaaa" in str(e) and "bbbb" in str(e), str(e))


print("\n- `runs` records how many were folded in -")
check("2 runs pooled -> runs == 2", ab._pool_runs(same_sha)["runs"] == 2)
check("3 runs pooled -> runs == 3",
      ab._pool_runs(same_sha + [{"code_sha": "aaaa", "x": 5}])["runs"] == 3)


print("\n- end to end: `--part remine --runs 2` (no model, no store, hermetic) -")
PY = sys.executable
SCRIPT = str(ROOT / "research" / "abstention_ab.py")
with tempfile.TemporaryDirectory() as tmp:
    out2 = Path(tmp) / "pooled.json"
    r = subprocess.run([PY, SCRIPT, "--part", "remine", "--stages", "2", "--per-stage", "20",
                       "--runs", "2", "--out", str(out2)],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    check("the --runs 2 process exits 0", r.returncode == 0, r.stdout[-800:] + r.stderr[-800:])
    run_files = sorted(Path(tmp).glob("pooled.run*.json"))
    check("each run's own artifact was written beside the pooled one",
          [p.name for p in run_files] == ["pooled.run1.json", "pooled.run2.json"],
          [p.name for p in run_files])
    pooled = json.loads(out2.read_text(encoding="utf-8")) if out2.exists() else {}
    check("the pooled artifact was written", bool(pooled))
    if pooled:
        check("runs == 2 at the top", pooled.get("runs") == 2, pooled.get("runs"))
        rm = pooled.get("remine", {})
        check("remine.events pooled at the SAME key `remine.events`", "events" in rm, sorted(rm))
        check("a deterministic stand (fixed seed, no model): both runs read the identical "
              "number, so the pooled mean equals it and the spread is zero",
              rm.get("events_per_run", {}).get("min") == rm.get("events_per_run", {}).get("max")
              == rm.get("events"), rm.get("events_per_run"))
        check("per_stage rows pooled by `stage`, 2 stages (--stages 2)",
              len(rm.get("per_stage", [])) == 2, rm.get("per_stage"))
        if rm.get("per_stage"):
            st0 = rm["per_stage"][0]
            check("a per_stage row carries its own per-run sibling",
                  "file_bytes_per_run" in st0, sorted(st0))
        check("code_sha travelled from run 1 (a real 40-char git sha on this checkout)",
              pooled.get("code_sha") is None or len(pooled["code_sha"]) == 40,
              pooled.get("code_sha"))

    print("\n- N=1 is untouched: identical to the shape before --runs existed -")
    out_default = Path(tmp) / "n1_default.json"
    out_explicit = Path(tmp) / "n1_explicit.json"
    r1 = subprocess.run([PY, SCRIPT, "--part", "remine", "--stages", "2", "--per-stage", "20",
                        "--out", str(out_default)],
                        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    r2 = subprocess.run([PY, SCRIPT, "--part", "remine", "--stages", "2", "--per-stage", "20",
                        "--runs", "1", "--out", str(out_explicit)],
                        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    check("both processes exit 0", r1.returncode == 0 and r2.returncode == 0,
          (r1.stdout[-400:], r2.stdout[-400:]))
    d1 = json.loads(out_default.read_text(encoding="utf-8")) if out_default.exists() else {}
    d2 = json.loads(out_explicit.read_text(encoding="utf-8")) if out_explicit.exists() else {}
    check("neither N=1 output carries a `runs` key or a pooling wrapper",
          "runs" not in d1 and "runs" not in d2, (sorted(d1), sorted(d2)))
    check("neither N=1 output carries a `_per_run` sibling anywhere",
          not any(k.endswith("_per_run") for k in d1.get("remine", {}))
          and not any(k.endswith("_per_run") for k in d2.get("remine", {})))
    for k in ("store", "measured_at"):
        d1.pop(k, None)
        d2.pop(k, None)
    check("omitting --runs and passing --runs 1 explicitly produce byte-identical shapes "
          "(store/measured_at excluded: both are per-process, not part of the shape)",
          d1 == d2, {"only_in_default": sorted(set(d1) - set(d2)),
                    "only_in_explicit": sorted(set(d2) - set(d1))})
    check("the remine section itself matches exactly (deterministic, seeded)",
          d1.get("remine") == d2.get("remine"))


print(f"\nabstention_ab --runs (stub / --part remine only): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
