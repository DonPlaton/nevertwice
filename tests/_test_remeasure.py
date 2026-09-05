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

print(f"\nremeasure: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
