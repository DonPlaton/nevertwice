"""`research/fusion_sweep.py`: does the inner harness's own `valid: false` survive?

`run_point()` runs `longmem_eval.py` / `locomo_eval.py` as a subprocess into a
`TemporaryDirectory` and folds one method's row into `points.<stand>.<weight>`; the temp file
is then deleted. K21/B1 already made those harnesses write `valid`/`invalid_reason`/
`dropped_questions`/`ollama_transport` onto their OWN artifact root when a run is invalid
(PREREG-V2 P0(a)/(b)) - but `run_point()` used to copy only `methods.hybrid` plus `questions`
as `n`, so that flag vanished with the temp file and every point read clean with n pinned at
the harness's own count. K24 (the auditor, item 9C) is the fix: carry the flag onto the point,
and propagate any invalid point to the artifact's root, since `points.<stand>.<weight>` is
exactly what a claim's pointer (`points.oracle["0.5"].recall@5`, ...) reads through.

Never touches Ollama or a real corpus: `subprocess.run` is replaced with a writer of a
synthetic inner blob, the same shape `longmem_eval.py --save` writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

import json  # noqa: E402
import tempfile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import fusion_sweep as fs  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


HYBRID = {"recall@1": 0.5, "recall@5": 0.8, "recall@10": 0.9, "mrr": 0.6}


_real_subprocess_run = fs.subprocess.run


def _fake_subprocess_run(blobs: dict):
    """Stands in for `fs.subprocess.run`: for a command that is one of THIS stand's own
    `longmem_eval.py`/`locomo_eval.py` invocations, writes the blob registered for that
    stand (from `STANDS`) instead of running it - never touches a real process or Ollama.
    Every other command (git, through `_provenance.is_dirty()` - `fs.subprocess` IS the
    `subprocess` module, so patching it patches every caller) falls through to the real
    `subprocess.run`, exactly as the auditor's own `fusion_valid_probe.py` does."""
    def fake_run(cmd, **kw):
        if not any(str(a).startswith("--out=") for a in cmd):
            return _real_subprocess_run(cmd, **kw)
        out_arg = next(a for a in cmd if str(a).startswith("--out="))
        target = Path(out_arg.split("=", 1)[1])
        stand = next(s for s, spec in fs.STANDS.items() if spec[1] in cmd)
        target.write_text(json.dumps(blobs[stand]), encoding="utf-8")
        return fs.subprocess.CompletedProcess(cmd, 0, "", "")
    return fake_run


def _run_main(blobs: dict, out: Path, weights="0.5"):
    saved = fs.subprocess.run
    fs.subprocess.run = _fake_subprocess_run(blobs)
    saved_argv = sys.argv
    try:
        sys.argv = ["fusion_sweep.py", "--weights", weights, "--save", f"--out={out}"]
        rc = fs.main()
    finally:
        fs.subprocess.run = saved
        sys.argv = saved_argv
    return rc, json.loads(out.read_text(encoding="utf-8"))


# a real claim's pointer (research/evidence_manifest.json:
# fusion_sweep.oracle.w0_5.recall_at_5 -> points.oracle["0.5"].recall@5)
POINTER = 'points.oracle["0.5"].recall@5'

with tempfile.TemporaryDirectory() as tmp:
    print("\n- a clean inner run: nothing invalid, n is the scored count -")
    clean_blobs = {
        "oracle": {"questions": 500, "methods": {"hybrid": dict(HYBRID)}},
        "locomo": {"questions": 480, "methods": {"hybrid": dict(HYBRID)}},
    }
    out_clean = Path(tmp, "clean.json")
    rc, res_clean = _run_main(clean_blobs, out_clean)
    check("exit 0", rc == 0)
    point_clean = res_clean["points"]["oracle"]["0.5"]
    check("n is the harness's own scored count (500)", point_clean["n"] == 500, str(point_clean))
    check("no 'valid' key on a clean point", "valid" not in point_clean, str(point_clean))
    check("no 'valid' key at the root", "valid" not in res_clean, str(res_clean))
    ref_clean = rm.row_refusal(res_clean, POINTER, code_time=0, head=None, produced_by=[])
    check("a clean artifact restores the real pointer", ref_clean is None, str(ref_clean))

    print("\n- an invalid inner run (K21/B1's own shape: valid, invalid_reason, "
          "dropped_questions, ollama_transport at the harness's root) -")
    invalid_blobs = {
        "oracle": {"questions": 499, "valid": False,
                  "invalid_reason": "ollama_transport.failed_outcomes=3 on /api/embed",
                  "dropped_questions": ["q1"],
                  "ollama_transport": {"failed_outcomes": 3, "bypass_calls": 0},
                  "methods": {"hybrid": dict(HYBRID)}},
        "locomo": {"questions": 480, "methods": {"hybrid": dict(HYBRID)}},
    }
    out_bad = Path(tmp, "bad.json")
    rc, res_bad = _run_main(invalid_blobs, out_bad)
    point_bad = res_bad["points"]["oracle"]["0.5"]
    check("point-level carry: valid False lands on the point",
          point_bad.get("valid") is False, str(point_bad))
    check("point-level carry: invalid_reason lands on the point",
          point_bad.get("invalid_reason") == invalid_blobs["oracle"]["invalid_reason"],
          str(point_bad))
    check("point-level carry: dropped_questions lands on the point",
          point_bad.get("dropped_questions") == ["q1"], str(point_bad))
    check("point-level carry: ollama_transport lands on the point",
          point_bad.get("ollama_transport") == invalid_blobs["oracle"]["ollama_transport"],
          str(point_bad))
    check("n is the SCORED count from the inner blob (499), not a pinned 500",
          point_bad["n"] == 499, str(point_bad))
    check("root propagation: the artifact root is marked invalid",
          res_bad.get("valid") is False, str(res_bad.get("valid")))
    check("root propagation: the reason names the point (oracle w=0.5)",
          "oracle w=0.5" in (res_bad.get("invalid_reason") or ""), res_bad.get("invalid_reason"))
    check("the clean locomo point is untouched (point-level carry only touches the bad point)",
          "valid" not in res_bad["points"]["locomo"]["0.5"])
    ref_bad = rm.row_refusal(res_bad, POINTER, code_time=0, head=None, produced_by=[])
    check("row_refusal refuses the real pointer points.oracle[\"0.5\"].recall@5",
          ref_bad is not None and "ollama_transport" in ref_bad, str(ref_bad))
    # the pointer that actually PROVES root propagation matters: locomo w=0.5 is itself
    # clean (no 'valid' anywhere on ITS own path) and is refused only because the ROOT -
    # a container on every pointer's path - was invalidated by the OTHER point (oracle
    # w=0.5). A pointer that resolves through the bad point directly would be refused by
    # point-level carry alone and would never distinguish the two mechanisms.
    LOCOMO_POINTER = 'points.locomo["0.5"].recall@5'
    ref_bad_locomo = rm.row_refusal(res_bad, LOCOMO_POINTER, code_time=0, head=None, produced_by=[])
    check("root propagation: a CLEAN point in the same artifact is refused too, via the root",
          ref_bad_locomo is not None, str(ref_bad_locomo))

    print("\n- mutation 'point-level carry removed': run_point back to its pre-K24 body "
          "(only `n` copied) -")

    def _pre_k24_run_point(stand, weight, tmp_dir):
        out = tmp_dir / f"{stand}_w{weight}.json"
        cmd = ["python", *fs.STANDS[stand][1:], "--save", f"--out={out}"]
        r = fs.subprocess.run(cmd, cwd=fs.ROOT, env={}, capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError("mutation fixture: subprocess failed")
        blob = json.loads(out.read_text(encoding="utf-8"))
        row = dict(blob["methods"]["hybrid"])
        row["n"] = blob["questions"]
        return row

    saved_run_point = fs.run_point
    fs.run_point = _pre_k24_run_point
    try:
        out_mut1 = Path(tmp, "mut1.json")
        rc_mut1, res_mut1 = _run_main(invalid_blobs, out_mut1)
        point_mut1 = res_mut1["points"]["oracle"]["0.5"]
        check("mutation 'point-level carry removed': the point no longer carries 'valid' "
              "(would FAIL the point-level carry check above)",
              "valid" not in point_mut1, str(point_mut1))
        check("mutation 'point-level carry removed': the root stays clean too, since "
              "root_invalid_reasons() finds nothing to propagate (would FAIL the root "
              "propagation check above)",
              "valid" not in res_mut1, str(res_mut1))
        ref_mut1 = rm.row_refusal(res_mut1, POINTER, code_time=0, head=None, produced_by=[])
        check("mutation 'point-level carry removed': the real pointer is now WRONGLY "
              "restorable (would FAIL the row_refusal check above)",
              ref_mut1 is None, str(ref_mut1))
    finally:
        fs.run_point = saved_run_point
    check("fs.run_point restored to the real function", fs.run_point is saved_run_point)

    print("\n- mutation 'root propagation removed': root_invalid_reasons() stubbed to [] -")
    saved_root_fn = fs.root_invalid_reasons
    fs.root_invalid_reasons = lambda points: []
    try:
        out_mut2 = Path(tmp, "mut2.json")
        rc_mut2, res_mut2 = _run_main(invalid_blobs, out_mut2)
        point_mut2 = res_mut2["points"]["oracle"]["0.5"]
        check("mutation 'root propagation removed': the point ITSELF still carries valid "
              "False (point-level carry is untouched by this mutation)",
              point_mut2.get("valid") is False, str(point_mut2))
        check("mutation 'root propagation removed': the root is WRONGLY left clean (would "
              "FAIL the root propagation check above)",
              "valid" not in res_mut2, str(res_mut2))
        # the pointer that actually catches this mutation: locomo w=0.5 is clean on its OWN
        # path (point-level carry never touched it) and depends entirely on root
        # propagation to be refused - unlike POINTER (oracle w=0.5), which stays refused by
        # point-level carry alone and would not move under this mutation.
        ref_mut2 = rm.row_refusal(res_mut2, LOCOMO_POINTER, code_time=0, head=None, produced_by=[])
        check("mutation 'root propagation removed': the clean locomo pointer is now "
              "WRONGLY restorable, because row_refusal never sees a container on ITS OWN "
              "path carrying valid:false, and the root - the one container that DID before "
              "this mutation - was never set (would FAIL the 'a CLEAN point ... is refused "
              "too' check above)",
              ref_mut2 is None, str(ref_mut2))
    finally:
        fs.root_invalid_reasons = saved_root_fn
    check("fs.root_invalid_reasons restored to the real function",
          fs.root_invalid_reasons is saved_root_fn)

    print("\n- mutation 'n back to the pinned count': n hardcoded to 500 regardless of the "
          "inner blob's own (scored) count -")

    def _pinned_n_run_point(stand, weight, tmp_dir):
        row = saved_run_point(stand, weight, tmp_dir)
        row["n"] = 500                    # mutation: ignore the inner blob's scored count
        return row

    fs.run_point = _pinned_n_run_point
    try:
        out_mut3 = Path(tmp, "mut3.json")
        rc_mut3, res_mut3 = _run_main(invalid_blobs, out_mut3)
        point_mut3 = res_mut3["points"]["oracle"]["0.5"]
        check("mutation 'n back to the pinned count': n reads 500, not the inner blob's "
              "own scored 499 (would FAIL the 'n is the SCORED count' check above)",
              point_mut3["n"] == 500, str(point_mut3))
        check("mutation 'n back to the pinned count': the mutated n disagrees with the "
              "correctly-computed n on the SAME inner blob",
              point_mut3["n"] != invalid_blobs["oracle"]["questions"],
              (point_mut3["n"], invalid_blobs["oracle"]["questions"]))
    finally:
        fs.run_point = saved_run_point
    check("fs.run_point restored to the real function (again)", fs.run_point is saved_run_point)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
