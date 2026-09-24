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
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

sys.path.insert(0, str(ROOT / "research"))
import abstention_ab as ab  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
from nevertwice import api as nt_api  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as remeasure_mod  # noqa: E402 - K16(2): row_refusal on the RESULT artifact

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


# ── R-v2-ports item 9A: the pacer is wired into this stand's own Ollama traffic ────────

class _JsonResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_failing_embed(*a, **kw):
    """Every `/api/embed` call fails with a genuine (non-port-exhaustion) 500 - the F1
    failure P0(a) exists to catch. Any other endpoint succeeds, so the run itself does not
    crash on something unrelated to embedding."""
    req = a[0] if a else kw.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if pacer._is_embed_path(url):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
    return _JsonResp({"models": []})


def _fake_capture_session(text, project=None, session_id=None, trigger=None):
    """Stands in for the real (LLM-driven) extractor: writes ONE note directly via the
    store's own API. `write_typed_note` itself calls `embed_text` once per note, which is
    the traffic this suite exists to prove gets paced - `build_store` calls this once per
    session (two per case), so two embed attempts happen before `capture_hits`'s own
    `api.recall` (a third) even runs."""
    m.write_typed_note(m.TYPE_FOLDER["pattern"],
                       {"title": f"t-{session_id}", "description": f"note for {session_id}",
                        "principle": "a fake principle, hermetic test only"},
                       project or "abstention_test", "2026-09-24", [], "pattern")


MINI_CASE = {"id": "mini-case", "shape": "value_replaced",
            "sessions": [["Settled it: the timeout is 30 seconds."],
                        ["Came back to it. The timeout is 5 seconds."]],
            "query": "what is the timeout", "current": ["5 second"], "superseded": ["30 second"]}
MINI_DATASET = {"name": "mini", "cases": [MINI_CASE]}


@contextlib.contextmanager
def _isolated_pacer():
    # (в)/MX3: check(), not a bare assert - a real regression here must redden by name, not
    # crash the whole suite with an uncaught Traceback.
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


def _run_mini(out_path: Path) -> dict:
    """Runs `ab.main()` for real (`--part recall --out ...`) against MINI_DATASET - `ab.DATASET`
    is patched directly (this stand has no `--dataset` CLI flag, unlike asof_bench.py)."""
    saved_argv = sys.argv
    saved_dataset = ab.DATASET
    saved_capture = nt_api.capture_session
    nt_api.capture_session = _fake_capture_session
    try:

        class _MiniPath:
            """A drop-in for `ab.DATASET` (a `Path`): only `.read_text()` is ever called on
            it inside `main()`'s `--part recall/inject` branch."""

            def read_text(self, encoding="utf-8"):
                return json.dumps(MINI_DATASET)

        ab.DATASET = _MiniPath()
        sys.argv = ["abstention_ab.py", "--part", "recall", "--k", "3", "--out", str(out_path)]
        rc = ab.main()
        return {"rc": rc, "artifact": (json.loads(out_path.read_text(encoding="utf-8"))
                                       if out_path.exists() else {})}
    finally:
        sys.argv = saved_argv
        ab.DATASET = saved_dataset
        nt_api.capture_session = saved_capture


print("\n- item 9A: the real run wires pacer.install()/attach() around this stand's own "
      "Ollama traffic; a failed embed marks the artifact invalid and "
      "tools/remeasure.row_refusal refuses a REAL claim pointer -")
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.json"
        result = _run_mini(out_path)
    check("main() exits 0 on the mini dataset", result["rc"] == 0, str(result["rc"]))
    art = result["artifact"]
    check("ollama_transport is written on the artifact (install() wrapped the embedder call)",
          "ollama_transport" in art, str(sorted(art)))
    check("the artifact is marked invalid - every /api/embed call failed",
          art.get("valid") is False and "embed" in (art.get("invalid_reason") or ""),
          str(art.get("invalid_reason")))
    reason = remeasure_mod.row_refusal(art, "recall_sweep[0].mean_chars", 0)
    check("tools/remeasure.row_refusal refuses a REAL claim pointer "
          "(abstention.recall.sweep.t0_0.mean_chars) on this invalid result",
          reason is not None and "invalid" in reason, str(reason))

print("\n- item 9A mutations: install()/attach() removed from the real run (in-process, "
      "ab.pacer IS the _ollama_pacer module - reassigning its attribute simulates the call "
      "site being deleted without editing the file) -")
saved_install, saved_attach = ab.pacer.install, ab.pacer.attach
ab.pacer.install = lambda: None                        # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td2:
        out_path2 = Path(td2) / "out.json"
        result_no_install = _run_mini(out_path2)
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in result_no_install["artifact"],
          str(sorted(result_no_install["artifact"])))
ab.pacer.install = saved_install

ab.pacer.attach = lambda *a, **k: None                  # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen_failing_embed
    with tempfile.TemporaryDirectory() as td3:
        out_path3 = Path(td3) / "out.json"
        result_no_attach = _run_mini(out_path3)
    check("mutation 'attach() removed': no ollama_transport is written and the artifact "
          "stays WRONGLY valid (the pacer paced the call but the artifact never learns it - "
          "would FAIL the same checks above)",
          "ollama_transport" not in result_no_attach["artifact"]
          and "valid" not in result_no_attach["artifact"],
          str(sorted(result_no_attach["artifact"])))
ab.pacer.attach = saved_attach

check("ab.pacer.install/attach are restored to the real functions after the mutations",
      ab.pacer.install is saved_install and ab.pacer.attach is saved_attach)

print("\n- G5 (the auditor's surviving mutation): _pool_runs carries a LATER run's valid:false - run 1 clean, run 2 invalid, the pooled artifact is invalid and row_refusal refuses the real pointer -")
import copy as _copy  # noqa: E402
_g5_base = json.loads((ROOT / "research" / "results" / "abstention_ab.json").read_text(encoding="utf-8"))
_g5_clean = _copy.deepcopy(_g5_base)
_g5_bad = _copy.deepcopy(_g5_base)
_g5_bad["valid"] = False
_g5_bad["invalid_reason"] = "ollama_transport.failed_outcomes=2 on /api/embed"
_g5_pooled = ab._pool_runs([_g5_clean, _g5_bad])
check("G5: the pooled artifact is invalid, naming run 2",
      _g5_pooled.get("valid") is False and "run 2" in (_g5_pooled.get("invalid_reason") or ""),
      str(_g5_pooled.get("invalid_reason")))
_g5_reason = remeasure_mod.row_refusal(_g5_pooled, "recall_sweep[0].mean_chars", 0)
check("G5: row_refusal refuses the real pointer recall_sweep[0].mean_chars "
      "(abstention.recall.sweep.t0_0.mean_chars)", _g5_reason is not None, str(_g5_reason))
_g5_clean_pool = ab._pool_runs([_copy.deepcopy(_g5_base), _copy.deepcopy(_g5_base)])
check("G5: two clean runs pool to a restorable artifact",
      _g5_clean_pool.get("valid") is not False, str(_g5_clean_pool.get("valid")))

print(f"\nabstention_ab --runs (stub / --part remine only) + item 9A pacer wiring: {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
