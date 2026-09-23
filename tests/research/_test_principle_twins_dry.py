"""`research/principle_twins.py` (A6, Q5): the dataset validator and `--dry`/`--help` paths,
with NO embedder call - the real sweep is deliberately not run (the GPU is busy with a
measurement campaign), so this suite only proves the plumbing that does not need one.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import principle_twins as pt  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the shipped dataset loads and validates clean -")
raw = pt.load_dataset(pt.DATA)
check("40 positive pairs", len(raw.get("positives") or []) == 40, str(len(raw.get("positives") or [])))
check("40 negative pairs", len(raw.get("negatives") or []) == 40, str(len(raw.get("negatives") or [])))
problems = pt.validate_dataset(raw)
check("no structural problems", not problems, "; ".join(problems[:5]))

print("\n- the validator actually catches a malformed dataset -")
raised = False
try:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text(json.dumps({"positives": []}), encoding="utf-8")   # no "negatives" key
        pt.load_dataset(p)
except ValueError:
    raised = True
check("load_dataset raises ValueError on a missing top-level key", raised)

broken_rows = {
    "positives": [{"a": "same text twice", "b": "same text twice"}, {"a": "", "b": "y"}],
    "negatives": [{"a": "x"}],   # missing "b" entirely
}
problems2 = pt.validate_dataset(broken_rows)
check("an identical a/b pair is flagged", any("identical" in p for p in problems2), str(problems2))
check("an empty field is flagged", any("empty" in p for p in problems2), str(problems2))
check("a missing field is flagged", any("missing" in p for p in problems2), str(problems2))

print("\n- --dry validates and exits 0 on the shipped dataset, embeds/writes nothing -")
rc = pt.main(["--dry"])
check("--dry on the real dataset exits 0", rc == 0, str(rc))

print("\n- --dry on a malformed dataset exits nonzero instead of embedding it -")
with tempfile.TemporaryDirectory() as td2:
    bad_path = Path(td2) / "bad_shape.json"
    bad_path.write_text(json.dumps({"positives": [{"a": "x", "b": "x"}], "negatives": []}),
                        encoding="utf-8")
    rc_bad = pt.main(["--dry", "--data", str(bad_path)])
check("--dry on a broken dataset exits nonzero", rc_bad != 0, str(rc_bad))

print("\n- --help exits 0 and does not touch the embedder -")
try:
    pt.main(["--help"])
    help_rc = 0
except SystemExit as e:
    help_rc = e.code
check("--help exits 0 (argparse's own convention)", help_rc == 0, str(help_rc))

print("\n- the Wilson interval behaves at the edges -")
check("0/0 is (0, 0), not a division error", pt.wilson(0, 0) == (0.0, 0.0))
lo, hi = pt.wilson(0, 40)
check("0/40 has a positive upper bound (a zero count is not certainty)", hi > 0.0, str(hi))
check("0/40's upper bound stays well under 1", hi < 0.2, str(hi))
lo2, hi2 = pt.wilson(40, 40)
check("40/40's lower bound is high but not exactly 1", 0.8 < lo2 < 1.0, str(lo2))

print("\n- the sweep range matches the plan (0.75 to 0.95) -")
check("sweep starts at 0.75", pt.T_SWEEP[0] == 0.75, str(pt.T_SWEEP[0]))
check("sweep ends at 0.95", pt.T_SWEEP[-1] == 0.95, str(pt.T_SWEEP[-1]))

print(f"\nprinciple twins (--dry only, no embedder): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
