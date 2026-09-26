#!/usr/bin/env python3
"""(б) b-b: a derived artifact names the files it was computed from, and goes stale when one moves.

`tools/draw_divergence.py` computes four live figures from four supersession artifacts; its claims'
`produced_by` closure is the tool alone, so re-measuring a supersession artifact left them "fresh"
with a number derived from the old inputs (the auditor's finding at restore #2). The same holds for
a `--pool` over per-run files and a `--with` merge.

A derived artifact now records `inputs: [{path, sha256}]` (`research/_provenance.record_inputs`,
or the tool's own equivalent), `tools/check_freshness` fails a live claim whose artifact's inputs
no longer hash the same, and `tools/remeasure --restore` refuses to restore from it.

    python tests/_test_input_closure.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402
import _provenance as prov  # noqa: E402
import check_freshness as cf  # noqa: E402
import draw_divergence as dd  # noqa: E402
import remeasure as rm  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("\n- the record and the question -")
with tempfile.TemporaryDirectory() as td:
    a, b = Path(td) / "a.json", Path(td) / "b.json"
    a.write_text('{"x": 1}\n', encoding="utf-8")
    b.write_text('{"y": 2}\n', encoding="utf-8")
    doc = prov.record_inputs({}, [a, b])
    check("record_inputs names each input with its sha256", len(doc["inputs"]) == 2
          and all(len(r["sha256"] or "") == 64 for r in doc["inputs"]), str(doc))
    check("nothing moved: inputs_moved is empty", cf.inputs_moved(doc) == [], str(cf.inputs_moved(doc)))
    b.write_text('{"y": 3}\n', encoding="utf-8")
    check("an input re-measured: inputs_moved names it as changed",
          [m.endswith("(changed)") for m in cf.inputs_moved(doc)] == [True], str(cf.inputs_moved(doc)))
    a.unlink()
    check("an input gone: named as missing", any(m.endswith("(missing)") for m in cf.inputs_moved(doc)))
    check("an artifact that records no inputs is not a derived one: nothing to ask",
          cf.inputs_moved({"x": 1}) == [] and cf.inputs_moved(None) == [])

print("\n- draw_divergence records the four artifacts it reads -")
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "research" / "results").mkdir(parents=True)
    for key in dd.DIVERGENCE_SET:
        (root / "research" / "results" / f"{key}.json").write_bytes(
            (ROOT / "research" / "results" / f"{key}.json").read_bytes())
    rec = dd.divergence_all(root)
    check("one input per artifact of the divergence set, by repository path",
          [r["path"] for r in rec["inputs"]] == [f"research/results/{k}.json" for k in dd.DIVERGENCE_SET],
          str([r["path"] for r in rec["inputs"]]))
    check("and nothing has moved against those copies", cf.inputs_moved(rec, root) == [])
    p = root / "research" / "results" / f"{dd.DIVERGENCE_SET[0]}.json"
    p.write_bytes(p.read_bytes() + b"\n")
    check("re-measuring one of them stales the derivation", len(cf.inputs_moved(rec, root)) == 1)
check("the committed draw_divergence.json records its inputs and they match the tree",
      bool(json.loads((ROOT / "research" / "results" / "draw_divergence.json").read_text(encoding="utf-8"))
           .get("inputs")) and cf.inputs_moved(json.loads(
               (ROOT / "research" / "results" / "draw_divergence.json").read_text(encoding="utf-8"))) == [])

print("\n- freshness fails a live claim, restore refuses a withdrawn one, once an input moves -")
art_rel, in_rel = "tests/_tmp_input_closure_art.json", "tests/_tmp_input_closure_in.json"
art, inp = ROOT / art_rel, ROOT / in_rel
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
try:
    inp.write_text('{"rate": 0.5}\n', encoding="utf-8")
    art.write_text(json.dumps(prov.record_inputs({"rate": 0.5}, [inp])), encoding="utf-8")
    claim = {"id": "ic.rate", "value": 0.5, "printed": ["0.5"], "statement": "rate 0.5", "raw": art_rel,
             "pointer": "rate", "produced_by": ["sandbox_guard.py"], "commit": head, "cited_in": []}
    fails, _, _ = cf.check({"claims": [dict(claim)]}, cf.Git())
    check("control: inputs unchanged, the claim is fresh", fails == [], str(fails))
    inp.write_text('{"rate": 0.7}\n', encoding="utf-8")
    fails, _, _ = cf.check({"claims": [dict(claim)]}, cf.Git())
    check("an input changed after the derivation: the live claim is stale, by that reason",
          [f["reason"] for f in fails] == ["an input of this derived artifact changed after it was computed"],
          str(fails))
    refusal = rm.row_refusal(json.loads(art.read_text(encoding="utf-8")), "rate", 0, raw=art_rel)
    check("and restore refuses to put its number back", bool(refusal) and "input" in refusal, str(refusal))
    _saved = cf.inputs_moved
    cf.inputs_moved = lambda doc, root=ROOT: []
    try:
        fails, _, _ = cf.check({"claims": [dict(claim)]}, cf.Git())
        check("mutation 'inputs not asked': the same stale derivation now WRONGLY reads fresh "
              "(would FAIL the check above)", fails == [], str(fails))
    finally:
        cf.inputs_moved = _saved
finally:
    art.unlink(missing_ok=True)
    inp.unlink(missing_ok=True)

print("\n- a --pool over per-run files records those files -")
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "pooled.json"
    runs = ["research/results/v2_runs/sup.run1.json", "research/results/v2_runs/sup.run2.json"]
    others = ["research/results/v2_runs/sup_mem0.json"]
    r = subprocess.run([sys.executable, "research/supersession_bench.py", "--pool", *runs, "--with", *others,
                        "--out", str(out)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    pooled = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    check("the pool ran", r.returncode == 0 and bool(pooled), (r.stderr or r.stdout)[-300:])
    check("and its artifact names the two runs and the --with file it was computed from",
          [x["path"] for x in pooled.get("inputs") or []] == runs + others,
          str([x.get("path") for x in pooled.get("inputs") or []]))
    check("each by the hash the tree has now", cf.inputs_moved(pooled) == [], str(cf.inputs_moved(pooled)))

print(f"\ninput closure: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
