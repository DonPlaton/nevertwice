#!/usr/bin/env python3
"""`tools/remeasure.py` withdraws by import closure and restores only from a real re-run.

A hundred claims went stale on 2026-09-05 when the review changed the engine. The withdrawal has
to be mechanical or it gets skipped, and the restoration has to be strict or it becomes the
thing the freshness check exists to catch: a number restamped with a commit that never produced
it. Mutation-checked where it matters - a stale artifact is refused, a dirty closure is refused.
"""
import _env_guard  # noqa: F401
import contextlib
import copy
import io
import json
import os
import re
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

    #: The ROW, not only the file (2026-09-24, before the v2 campaign). A merging --save writes a
    #: fresh file around an old row - restore #1 put twelve h2h_pinned.*_full claims back that way,
    #: rows stamped 2026-09-08 at f0ed080 - and a stand marks its own run invalid, which restore
    #: never read. The file below is fresh on disk in every case; only the row differs.
    _head_t = int(subprocess.run(["git", "log", "-1", "--format=%ct", HEAD], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.strip())
    import datetime as _dt
    _utc = lambda t: _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _rows = {
        "old":     {"measured_at": {"commit": "f0ed080", "utc": _utc(_head_t - 86400 * 15)},
                    "recall@5": 0.834},
        "fresh":   {"measured_at": {"commit": HEAD, "utc": _utc(_head_t + 60)}, "recall@5": 0.834},
        "invalid": {"measured_at": {"commit": HEAD, "utc": _utc(_head_t + 60)}, "recall@5": 0.834,
                    "valid": False, "invalid_reason": "bypassed the pacer via requests: 1 request(s)"},
        "nostamp": {"recall@5": 0.834},
    }
    raw.write_text(json.dumps({"methods": _rows}), encoding="utf-8")
    os.utime(raw, None)

    def _one(row):
        m = copy.deepcopy(man)
        m["claims"][0]["pointer"] = f"methods.{row}.recall@5"
        return rm.restore(m, head=HEAD)

    restored, left, _ = _one("old")
    check("a row merged in from an older run is refused although the file is fresh",
          restored == [] and any("before HEAD" in x and "f0ed080" in x for x in left), str(left))
    restored, left, _ = _one("invalid")
    check("a row its own run marked invalid is refused, with the run's reason",
          restored == [] and any("marked invalid" in x and "requests" in x for x in left), str(left))
    restored, left, _ = _one("fresh")
    check("control: a row stamped after HEAD restores", restored == ["r.rate"], str(left))
    restored, left, _ = _one("nostamp")
    check("control: a row with no stamp falls back to the file check and restores",
          restored == ["r.rate"], str(left))
    raw.write_text(json.dumps({"valid": False, "invalid_reason": "embed failed",
                               "methods": {"fresh": _rows["fresh"]}}), encoding="utf-8")
    os.utime(raw, None)
    restored, left, _ = _one("fresh")
    check("an artifact marked invalid at its ROOT refuses every row under it",
          restored == [] and any("embed failed" in x for x in left), str(left))

    #: K15 (the auditor on 3fb1f64). (1) The DEEPEST stamp decides: since f91b7ba twelve stands stamp
    #: the artifact ROOT, so a fresh root over a merged old row must still refuse the row.
    raw.write_text(json.dumps({"measured_at": {"commit": HEAD, "utc": _utc(_head_t + 60)},
                               "methods": {"old": _rows["old"]}}), encoding="utf-8")
    os.utime(raw, None)
    restored, left, _ = _one("old")
    check("a fresh stamp at the ROOT does not vouch for an older merged row under it",
          restored == [] and any("f0ed080" in x for x in left), str(left))
    #: (2) A fresh TIME is not fresh CODE: a run started today from an old worktree. The commit
    #: must be HEAD or leave this claim's closure (produced_by: sandbox_guard.py) unchanged.
    _git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                                      check=True).stdout.strip()
    _last = _git("log", "--format=%H", "-1", "--", "sandbox_guard.py")
    _before = _git("rev-parse", f"{_last}^")            # sandbox_guard.py differs from HEAD here
    _same = _git("rev-parse", "HEAD~1")                 # after its last change: closure identical
    _k15 = {
        "oldcode": {"measured_at": {"commit": _before, "utc": _utc(_head_t + 60)}, "recall@5": 0.834},
        "samecl":  {"measured_at": {"commit": _same, "utc": _utc(_head_t + 60)}, "recall@5": 0.834},
        "nocommit": {"measured_at": {"utc": _utc(_head_t + 60)}, "recall@5": 0.834},
        #: the auditor's MK15c: a commit git has never seen cannot vouch for anything
        "ghost":    {"measured_at": {"commit": "a" * 40, "utc": _utc(_head_t + 60)}, "recall@5": 0.834},
    }
    raw.write_text(json.dumps({"methods": _k15}), encoding="utf-8")
    os.utime(raw, None)
    restored, left, _ = _one("oldcode")
    check("a fresh-dated row measured on code whose closure differs from HEAD is refused",
          restored == [] and any("sandbox_guard.py" in x for x in left), str(left))
    restored, left, _ = _one("samecl")
    check("control: a row at another commit with an identical closure restores",
          restored == ["r.rate"], str(left))
    restored, left, _ = _one("nocommit")
    check("a row with a measurement time but no commit is refused",
          restored == [] and any("no commit" in x for x in left), str(left))
    restored, left, _ = _one("ghost")
    check("a row stamped with a commit git cannot resolve is refused",
          restored == [] and any("not one git can resolve" in x for x in left), str(left))
    _saved_cm = rm._closure_moved
    try:
        rm._closure_moved = lambda *a, **k: None
        restored, _left, _ = _one("oldcode")
        check("mutation: without the commit check, the old-code row WOULD be restored",
              restored == ["r.rate"], str(_left))
    finally:
        rm._closure_moved = _saved_cm

    #: The call site, not only the function: with the guard disabled the same old row comes back.
    raw.write_text(json.dumps({"methods": _rows}), encoding="utf-8")
    os.utime(raw, None)
    _saved = rm.row_refusal
    try:
        rm.row_refusal = lambda *a, **k: None
        restored, _left, _ = _one("old")
        check("mutation: without the row guard, the merged old row WOULD be restored",
              restored == ["r.rate"], str(_left))
    finally:
        rm.row_refusal = _saved
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
        #: The claim names a REAL stand in produced_by, so an uncommitted edit to that stand made
        #: restore refuse it for "commit first" instead of the pair move this block is about - every
        #: staged change to supersession_bench.py turned this red before its commit (found twice on
        #: stands/ports, 2026-09-24). This block tests the pair guard, so the working tree is held
        #: clean for it; the dirty-closure refusal has its own check above.
        _saved_dirty = rm._dirty_files
        rm._dirty_files = lambda: set()
        try:
            _restored, _left, _ = rm.restore(_man, head=HEAD)
        finally:
            rm._dirty_files = _saved_dirty
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


print("")
print("- `--pending` warns where the command it prints cannot write its own artifact -")
#: The package declares eleven entries whose recorded command produces a SMALLER file than the
#: committed one, and the register carries those same commands for 281 claims. Measured
#: 2026-09-22: ZERO of the 281 mentioned it - 171 had an empty `note`, the other 110 talked about
#: something else - so the surface a person acts on printed a command that silently overwrites a
#: merged artifact. The caveat is READ from the package at print time rather than stamped into
#: the manifest: a copy would be a second source of truth, agreeing on the day it is written.
_asm = rm._assembled_artifacts()
check("the package declares artifacts their command cannot write", len(_asm) == 11, str(len(_asm)))
check("and the one the campaign turns on is among them",
      "research/results/supersession_v1_implicit.json" in _asm)

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    rm.main(["--pending"])
_out = _buf.getvalue()
check("the listing warns rather than printing the command bare",
      "cannot write that file as it stands" in _out)
#: The queue is not the register. 188 withdrawn claims sit outside it with their reason in
#: `stale`, and this listing used to print the queue and its size and never say they existed -
#: so a campaign planned from "572 pending" would end with the register whole minus those.
check("the listing names the withdrawn claims that are NOT in the queue",
      "are NOT in this queue" in _out)
_third = re.search(r"and (\d+) withdrawn claim\(s\) are NOT in this queue", _out)
check(f"and counts them, so the number the campaign plans against is visible "
      f"({_third.group(1) if _third else '?'})",
      bool(_third) and int(_third.group(1)) >= 150, _out[-200:] if not _third else "")

_tail = [ln for ln in _out.splitlines() if "declares incomplete" in ln]
check("and it says how many claims stand behind such a command", bool(_tail),
      _out.splitlines()[-1] if _out else "(no output)")
_n = int(re.search(r"(\d+) of the (\d+) stand behind", _out).group(1)) if _tail else 0
check(f"which is a population, not a zero ({_n})", _n >= 200, str(_n))

#: The rule bites: with the package's declaration removed, the listing goes back to printing the
#: command bare - so a green line above means the caveat travelled, not that nothing was wrong.
_real = rm._assembled_artifacts
rm._assembled_artifacts = lambda: {}
try:
    _buf2 = io.StringIO()
    with contextlib.redirect_stdout(_buf2):
        rm.main(["--pending"])
    check("without the declaration the warning disappears",
          "cannot write that file as it stands" not in _buf2.getvalue())
finally:
    rm._assembled_artifacts = _real


print("\n- the statement is rewritten in ONE anchored pass: a replaced number is never re-matched -")
#: Restore #1 (2026-09-23) turned "and 0.0167 ms per check" into "and 0.0372 ms per check". The
#: printed forms were ['0.017', '0.0167', '0.02'] -> ['0.027', '0.0272', '0.03'], and the rewrite
#: replaced them one after another without anchors: "0.0167" became "0.0272", then the short form
#: "0.02" matched INSIDE the new "0.0272" and made it "0.0372". The review line did not fire,
#: because "0.03" - a new printed form - was then "in" the statement. Found by the auditing session.
_sraw_rel = "tests/_tmp_remeasure_stmt.json"
_sraw = ROOT / _sraw_rel


def _restore_one(printed, statement, value):
    _sraw.write_text(json.dumps({"v": value}), encoding="utf-8")
    m = {"claims": [{"id": "s.ms", "value": 0.0, "printed": list(printed), "unit": "ms",
                     "statement": statement, "cited_in": [], "cited_in_pending": [],
                     "stale": "timed on a loaded machine; the re-run needs the GPU box idle",
                     "withdrawn_on": "2026-09-23", "pending_remeasure": True,
                     "produced_by": ["sandbox_guard.py"], "commit": "0" * 40,
                     "raw": _sraw_rel, "pointer": "v"}]}
    restored, left, review = rm.restore(m, head=HEAD)
    return m["claims"][0], restored, left, review


try:
    c, restored, left, review = _restore_one(["0.017", "0.0167", "0.02"],
                                              "and 0.0167 ms per check", 0.0272)
    check("the auditor's case: 0.0167 -> 0.0272, not 0.0372",
          c["statement"] == "and 0.0272 ms per check", repr(c["statement"]))
    #: a short OLD form that is a prefix of a longer NEW form, which chaining would re-enter twice
    #: ['0.2', '0.25'] -> ['0.3', '0.31']: in order, "0.2" hits inside "0.25" first and leaves
    #: "0.35", after which "0.25" no longer occurs
    c, *_ = _restore_one(["0.2", "0.25"], "reaches 0.25 on the stand", 0.31)
    check("a short old form that is a prefix of a longer one does not eat it",
          c["statement"] == "reaches 0.31 on the stand", repr(c["statement"]))
    #: a digit on either side is not a boundary: 10.02 is not 0.02
    c, *_ = _restore_one(["0.02"], "a budget of 10.02 ms and 0.02 ms per check", 0.03)
    check("a number is matched whole, never inside a larger one",
          c["statement"] == "a budget of 10.02 ms and 0.03 ms per check", repr(c["statement"]))
    #: the anchor after a number must also refuse a decimal point or thousands comma that
    #: continues it (the auditing session's (в)1): "45" is not the start of "45.6%", "19" is not
    #: the start of "19,206", "2.0" is not the start of the version "2.0.19"
    c, *_ = _restore_one(["45"], "45 notes, 45.6% of them with facts", 47)
    check("an integer form is not matched at the front of a decimal",
          c["statement"] == "47 notes, 45.6% of them with facts", repr(c["statement"]))
    c, *_ = _restore_one(["19"], "19 of 19,206 sessions", 21)
    check("an integer form is not matched at the front of a thousands-separated number",
          c["statement"] == "21 of 19,206 sessions", repr(c["statement"]))
    c, *_ = _restore_one(["2.0"], "Mem0 2.0.19 at a ratio of 2.0", 2.5)
    check("a decimal form is not matched at the front of a version string",
          c["statement"] == "Mem0 2.0.19 at a ratio of 2.5", repr(c["statement"]))
    #: a printed form that is several numbers ("1.1 x 10^-16") is one form: the post-check must
    #: vouch for the numbers inside it, or it refuses every p-value rewrite it exists to allow
    c, restored, left, review = _restore_one(["1.1 x 10^-16"], "p = 1.1 x 10^-16 on 60 cases", 3.4e-9)
    check("a multi-number printed form is rewritten, not refused by the post-check",
          c["statement"] == "p = 3.4 x 10^-9 on 60 cases", repr(c["statement"]) + " " + str(review))
    #: the post-check is the second line: with the anchors taken away (the mutation that let
    #: 0.0167 become 0.0372), it must refuse the corrupted rewrite and say so, not write it
    _anchors = (rm._BEFORE, rm._AFTER)
    rm._BEFORE, rm._AFTER = "", ""
    try:
        c, restored, left, review = _restore_one(["0.02"], "a budget of 10.02 ms and 0.02 ms per check",
                                                  0.03)
    finally:
        rm._BEFORE, rm._AFTER = _anchors
    check("without the anchors the post-check keeps the old statement rather than print 10.03",
          c["statement"] == "a budget of 10.02 ms and 0.02 ms per check", repr(c["statement"]))
    check("and sends the claim to review, naming what it refused to print",
          any("NOT rewritten" in r and "10.03" in r for r in review), str(review))
finally:
    if _sraw.exists():
        _sraw.unlink()


print(f"\nremeasure: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
