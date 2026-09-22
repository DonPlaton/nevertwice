#!/usr/bin/env python3
"""`tools/remeasure.py` withdraws by import closure and restores only from a real re-run.

A hundred claims went stale on 2026-09-05 when the review changed the engine. The withdrawal has
to be mechanical or it gets skipped, and the restoration has to be strict or it becomes the
thing the freshness check exists to catch: a number restamped with a commit that never produced
it. Mutation-checked where it matters - a stale artifact is refused, a dirty closure is refused.
"""
import _env_guard  # noqa: F401
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                      text=True, check=True).stdout.strip()

print("\n- printed forms keep their shape -")
for old, value, want in [("0.802", 0.8123, "0.812"), ("89", 91.4, "91"), ("89 ms", 91.4, "91 ms"),
                         ("19,206", 19412, "19,412"), ("81%", 0.8333, "83%"), ("26.6%", 0.2749, "27.5%"),
                         ("+0.14", 0.201, "+0.20"), ("31×", 29.6, "30×"), ("1.1 x 10^-16", 3.4e-9, "3.4 x 10^-9"),
                         ("0.8193", 0.5, "0.5000")]:
    check(f"{old!r} -> {want!r}", rm.reformat(old, value) == want, str(rm.reformat(old, value)))
check("an unknown shape is reported, not guessed", rm.reformat("about a third", 0.3) is None)

print("\n- withdrawal follows the import closure -")
manifest = {"claims": [
    {"id": "a.one", "value": 1, "printed": ["1"], "statement": "s", "cited_in": ["README.md"],
     "produced_by": ["nevertwice/memory_hook.py", "research/x.py"], "commit": HEAD, "raw": None, "pointer": None},
    {"id": "b.two", "value": 2, "printed": ["2"], "statement": "s", "cited_in": ["README.md"],
     "produced_by": ["research/embed_universal/y.py"], "commit": HEAD, "raw": None, "pointer": None},
    {"id": "c.old", "value": 3, "printed": ["3"], "statement": "s", "cited_in": [], "stale": "already gone",
     "produced_by": ["nevertwice/memory_hook.py"], "commit": HEAD, "raw": None, "pointer": None},
]}
ids = rm.withdraw(manifest, "the engine moved; the re-run needs the GPU", "2026-09-05",
                  touching={"nevertwice/memory_hook.py"})
check("only the claim whose closure meets the change is withdrawn", ids == ["a.one"], str(ids))
a = manifest["claims"][0]
check("its citations are parked, not lost", a["cited_in"] == [] and a["cited_in_pending"] == ["README.md"])
check("it is stale, dated and marked pending", a["stale"] and a["withdrawn_on"] == "2026-09-05"
      and a["pending_remeasure"] is True)
check("an already-withdrawn claim is left alone", "pending_remeasure" not in manifest["claims"][2])
check("--pending groups by command", rm.pending(manifest) == {"?": ["a.one"]}, str(rm.pending(manifest)))

print("\n- restoration reads the artifact, and only a fresh one -")
raw_rel = "tests/_tmp_remeasure_raw.json"
raw = ROOT / raw_rel
man = {"claims": [{
    "id": "r.rate", "value": 0.802, "printed": ["0.802"], "unit": "recall@5", "n": 500,
    "statement": "Nevertwice reaches RECALL@5 0.802 on the stand",
    "cited_in": [], "cited_in_pending": ["README.md", "docs/BENCHMARKS.md"],
    "stale": "the engine moved; the re-run needs the GPU", "withdrawn_on": "2026-09-05",
    "pending_remeasure": True, "produced_by": ["sandbox_guard.py"],     # tracked and clean
    "commit": "0" * 40, "raw": raw_rel, "pointer": "methods.hybrid.recall@5",
    "ci": {"method": "wilson", "level": 0.95, "low": 0.7648, "high": 0.8346}}]}
try:
    raw.write_text(json.dumps({"methods": {"hybrid": {"recall@5": 0.834}}}), encoding="utf-8")
    # an artifact older than the code commit is the OLD measurement wearing a new hash
    old_time = int(subprocess.run(["git", "log", "-1", "--format=%ct", HEAD], cwd=ROOT,
                                  capture_output=True, text=True, check=True).stdout.strip()) - 3600
    os.utime(raw, (old_time, old_time))
    m1 = copy.deepcopy(man)
    restored, left, _review = rm.restore(m1, head=HEAD)
    check("an artifact that predates HEAD is refused", restored == [] and any("predates" in x for x in left),
          str(left))
    check("and the claim stays withdrawn", m1["claims"][0].get("stale"))

    os.utime(raw, None)                                     # now: re-run after the commit
    m2 = copy.deepcopy(man)
    restored, left, review = rm.restore(m2, head=HEAD)
    c = m2["claims"][0]
    check("a fresh artifact restores the claim", restored == ["r.rate"] and not left, str(left))
    check("the value and printed form follow the artifact", c["value"] == 0.834 and c["printed"] == ["0.834"])
    check("the statement quotes the new form", "0.834" in c["statement"] and "0.802" not in c["statement"])
    check("the Wilson interval is recomputed", c["ci"]["low"] < 0.834 < c["ci"]["high"]
          and abs(c["ci"]["low"] - 0.7987) < 0.002, str(c["ci"]))
    check("the commit is HEAD", c["commit"] == HEAD)
    check("the citations come back", c["cited_in"] == ["README.md", "docs/BENCHMARKS.md"])
    check("no withdrawal fields remain", not any(k in c for k in ("stale", "withdrawn_on", "pending_remeasure",
                                                                   "cited_in_pending")))
    check("nothing to review by hand when the statement carried the form", review == [], str(review))

    m3 = copy.deepcopy(man)
    m3["claims"][0]["pointer"] = "methods.missing.recall@5"
    restored, left, _ = rm.restore(m3, head=HEAD)
    check("a missing pointer leaves the claim withdrawn", restored == [] and any("missing" in x for x in left))

    m4 = copy.deepcopy(man)
    m4["claims"][0]["produced_by"] = [raw_rel]              # the artifact itself is untracked = dirty
    restored, left, _ = rm.restore(m4, head=HEAD)
    check("a dirty closure is refused", restored == [] and any("commit first" in x for x in left), str(left))
finally:
    raw.unlink(missing_ok=True)

print("\n- a positional pointer must still address the pair the claim is about -")
#: Measured, not supposed. Running the two supersession commands exactly as the register records
#: them produced three arms instead of seven - `--arms nevertwice,naive` cannot make the mem0 and
#: zep arms, which had come from separate runs pooled with `--with`. `pairs[0]` stopped being
#: `(mem0, naive)` and became `(naive, nevertwice)`; the restore rewrote
#: `supersession.mem0_vs_naive.p_mcnemar` from 1.0 to 2.78e-17 while its sentence still read
#: "mem0 against naive". Pointer resolved, value matched what had just been written there,
#: freshness passed, battery green (2026-09-22).
_art = {"pairs": [{"a": "mem0", "b": "naive", "p_mcnemar": 1.0},
                  {"a": "mem0", "b": "nevertwice", "p_mcnemar": 2.2e-14}]}
check("a pointer addressing the claim's own pair is accepted",
      rm.pair_mismatch({"id": "supersession.mem0_vs_naive.p_mcnemar",
                       "pointer": "pairs[0].p_mcnemar"}, _art) is None)
_moved = rm.pair_mismatch({"id": "supersession.mem0_vs_nevertwice.p_mcnemar",
                          "pointer": "pairs[0].p_mcnemar"}, _art)
check("a pointer that moved to another pair is refused", bool(_moved))
check("and the refusal names both pairs, so the reader sees the swap",
      _moved and "mem0" in _moved and "naive" in _moved and "nevertwice" in _moved, str(_moved))
check("a pointer that is not positional into a pair list is left alone",
      rm.pair_mismatch({"id": "supersession.nevertwice.stale_rate",
                       "pointer": "pooled_nevertwice.stale.rate"}, _art) is None)
check("an id that names no pair is left alone",
      rm.pair_mismatch({"id": "locomo.semantic.recall_at_1",
                       "pointer": "pairs[0].p_mcnemar"}, _art) is None)
check("a list whose rows carry no arm names is left alone - nothing to compare",
      rm.pair_mismatch({"id": "supersession.mem0_vs_naive.p_mcnemar",
                       "pointer": "pairs[0].p_mcnemar"}, {"pairs": [{"p_mcnemar": 1.0}]}) is None)


print("\n- and `restore` itself refuses it, not merely the helper -")
#: The first version checked `pair_mismatch` and never the call site: disabling the call in
#: `restore` reddened nothing. That is the same shape as the helper's own defect - a property
#: verified where it is defined and not where it is used (2026-09-22).
import tempfile as _tf  # noqa: E402

with _tf.TemporaryDirectory() as _td:
    _raw = ROOT / "research" / "results" / "_pairmove_probe.json"
    _raw.parent.mkdir(parents=True, exist_ok=True)
    _raw.write_text(json.dumps({"pairs": [{"a": "naive", "b": "nevertwice",
                                           "p_mcnemar": 2.78e-17}]}), encoding="utf-8")
    try:
        os.utime(_raw, None)
        _man = {"claims": [{
            "id": "supersession.mem0_vs_naive.p_mcnemar", "value": 1.0, "printed": ["1.0"],
            "statement": "mem0 against naive on the same 60 supersession cases",
            "unit": "p", "n": 60, "ci": None, "dataset": "d", "environment": "e",
            "command": "python research/supersession_bench.py --runs 2",
            "produced_by": ["research/supersession_bench.py"], "cited_in": [],
            "stale": "withdrawn for the re-measure", "pending_remeasure": True,
            "commit": "0" * 40, "raw": "research/results/_pairmove_probe.json",
            "pointer": "pairs[0].p_mcnemar"}]}
        _restored, _left, _ = rm.restore(_man, head=HEAD)
        check("restore refuses a claim whose index moved to another pair",
              _restored == [] and any("another pair" in x for x in _left), str(_left))
        check("and the claim keeps its own value rather than taking the neighbour's",
              _man["claims"][0]["value"] == 1.0, str(_man["claims"][0]["value"]))
    finally:
        _raw.unlink(missing_ok=True)

print("\n- and the same at the call site for a list that grew a row -")
#: `pair_mismatch` covers the claims whose id names the two arms. The other seventy-two index a
#: list with no such signature, and `shape_mismatch` compares the list's recorded shape instead.
#: Checked HERE rather than only on the helper, for the reason written above.
_raw = ROOT / "research" / "results" / "_shapemove_probe.json"
_raw.parent.mkdir(parents=True, exist_ok=True)
_raw.write_text(json.dumps({"recall_sweep": [{"threshold": t, "recall": 0.9} for t in
                                             (0.1, 0.2, 0.25, 0.3, 0.33, 0.35)]}),
                encoding="utf-8")
try:
    os.utime(_raw, None)
    _man = {"claims": [{
        "id": "abstention.recall.shipped_threshold", "value": 0.35, "printed": ["0.35"],
        "statement": "the shipped abstention threshold", "unit": "threshold", "n": 40,
        "ci": None, "dataset": "d", "environment": "e",
        "command": "python research/abstention_ab.py --part all",
        "produced_by": ["research/abstention_ab.py"], "cited_in": [],
        "stale": "withdrawn for the re-measure", "pending_remeasure": True,
        "commit": "0" * 40, "raw": "research/results/_shapemove_probe.json",
        "pointer": "recall_sweep[4].threshold",
        #: five rows when it was registered, six on disk now: one threshold inserted
        "shape": [{"at": "recall_sweep", "len": 5, "keys": ["recall", "threshold"]}]}]}
    _restored, _left, _ = rm.restore(_man, head=HEAD)
    check("restore refuses a claim whose list grew a row under it",
          _restored == [] and any("held 5 rows" in x for x in _left), str(_left))
    check("and the claim keeps 0.35 rather than taking the neighbouring threshold",
          _man["claims"][0]["value"] == 0.35, str(_man["claims"][0]["value"]))
    #: The hole, exercised rather than described: an equal-length reorder is invisible to shape.
    _reordered = [{"at": "recall_sweep", "len": 6, "keys": ["recall", "threshold"]}]
    _man["claims"][0]["shape"] = _reordered
    check("an equal-length reorder passes, which is this layer's stated limit",
          rm.shape_mismatch(_man["claims"][0],
                            json.loads(_raw.read_text(encoding="utf-8"))) is None)
finally:
    _raw.unlink(missing_ok=True)


print("")
print("- a guard that cannot do its job refuses, rather than falling silent -")
#: Both guards used to return None when the pointer could not be walked - "I see no mismatch",
#: which is what they also return on healthy data. That was safe only because `restore` resolves
#: the pointer BEFORE calling them and drops the claim when that fails: a dependency on the ORDER
#: of two calls, written down nowhere, and no test would have noticed the guards being moved
#: above it. Rather than record the dependency in a comment, the branch was measured - 807
#: pointers in the register walked, ZERO exceptions in either guard - and made to refuse, which
#: costs nothing today and makes the order irrelevant. Found by the auditing session, 2026-09-22.
_pair_claim = {"id": "supersession.mem0_vs_naive.p_mcnemar", "pointer": "pairs[0].p_mcnemar"}
_said = rm.pair_mismatch(_pair_claim, {"pairs": {"not": "a list"}})
check("`pair_mismatch` on a pointer it cannot walk says so instead of nothing",
      _said is not None and "cannot be walked" in _said, repr(_said))

_shape_claim = {"id": "abstention.recall", "pointer": "recall_sweep[0].threshold",
                "shape": [{"at": "recall_sweep", "len": 6, "keys": ["recall", "threshold"]}]}
_said = rm.shape_mismatch(_shape_claim, {"recall_sweep": 7})
check("`shape_mismatch` on a pointer it cannot walk says so instead of nothing",
      _said is not None and "cannot be walked" in _said, repr(_said))

#: The other half of the property: refusing an unwalkable pointer must not make the guards
#: trigger-happy on the claims they exist for. Healthy data still passes both.
check("and neither guard has started refusing healthy data",
      rm.pair_mismatch({"id": "supersession.mem0_vs_naive.p", "pointer": "pairs[0].p"},
                       {"pairs": [{"a": "mem0", "b": "naive", "p": 0.1}]}) is None
      and rm.shape_mismatch(_shape_claim,
                            {"recall_sweep": [{"threshold": t, "recall": 0.5} for t in range(6)]})
      is None)


print(f"\nremeasure: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
