"""`tools/register_retrieval.py`: a retrieval family is registered from its artifact.

Pins the claim shape for the `longmem_eval` / `locomo_eval` artifacts (methods.<key>.recall@k,
methods.<key>.mrr, questions = n): ids, statements, printed forms, Wilson on recall and none on
MRR, the label suffix an ablation family carries, existing claims left alone, and the refusals.
"""
import _env_guard  # noqa: F401
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import register_retrieval as rr  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def row(r1, r5, r10, mrr):
    return {"recall@1": r1, "recall@3": (r1 + r5) / 2, "recall@5": r5, "recall@10": r10, "mrr": mrr}


ART = {"questions": 1977, "turns": 5882, "morphology": False,
       "methods": {"semantic": row(0.182, 0.432, 0.560, 0.301), "lexical": row(0.271, 0.499, 0.576, 0.377),
                   "hybrid": row(0.293, 0.549, 0.634, 0.411), "semantic+recur": row(0.182, 0.432, 0.560, 0.301)}}

print("\n- claims from a locomo_eval-shaped artifact -")
new, skipped = rr.build_claims("locomo_raw", ART, dataset="locomo10_pinned", stand="LoCoMo, per conversation, on raw tokens",
                               command="python research/locomo_eval.py --no-morphology --save --out=x.json",
                               raw="research/results/locomo_raw.json", head="deadbeef",
                               produced_by=["research/locomo_eval.py"], existing=set(),
                               label_suffix=" (raw tokens)")
ids = [c["id"] for c in new]
check("three known methods x four metrics = twelve claims; the recur arm is not a claim",
      len(new) == 12 and not any("recur" in i for i in ids), str(ids))
by = {c["id"]: c for c in new}
lex5 = by["locomo_raw.lexical.recall_at_5"]
check("statement carries the label, the suffix and the stand",
      lex5["statement"] == "lexical recall (BM25, no embedder) (raw tokens) reaches RECALL@5 0.499 on LoCoMo, per conversation, on raw tokens",
      lex5["statement"])
check("value, printed form, pointer, n", lex5["value"] == 0.499 and lex5["printed"] == ["0.499"]
      and lex5["pointer"] == "methods.lexical.recall@5" and lex5["n"] == 1977)
check("Wilson interval on recall", lex5["ci"]["low"] < 0.499 < lex5["ci"]["high"])
check("MRR: statement form, no interval", by["locomo_raw.hybrid.mrr"]["ci"] is None
      and by["locomo_raw.hybrid.mrr"]["statement"].startswith("the shipped ranker (calibrated score fusion) (raw tokens) reaches MRR 0.411"))
check("every claim names dataset, environment, raw, command, commit and closure",
      all(c["dataset"] == "locomo10_pinned" and c["environment"] == rr.ENVIRONMENT
          and c["raw"] == "research/results/locomo_raw.json" and c["commit"] == "deadbeef"
          and c["produced_by"] == ["research/locomo_eval.py"] and c["cited_in"] == [] for c in new))

print("\n- the cross-encoder arm and a method filter -")
art2 = {**ART, "methods": {**ART["methods"], "hybrid+xrerank": row(0.614, 0.826, 0.858, 0.712)}}
new2, _ = rr.build_claims("fam", art2, dataset="d", stand="s", command="c", raw="r", head="h",
                          produced_by=[], existing=set())
check("hybrid+xrerank maps to the hybrid_xrerank slug", "fam.hybrid_xrerank.recall_at_1" in {c["id"] for c in new2})
new3, _ = rr.build_claims("fam", art2, dataset="d", stand="s", command="c", raw="r", head="h",
                          produced_by=[], existing=set(), methods=["lexical"])
check("--methods restricts the family", {c["id"].split(".")[1] for c in new3} == {"lexical"})

print("\n- existing claims are never rewritten -")
new4, skipped4 = rr.build_claims("locomo_raw", ART, dataset="d", stand="s", command="c", raw="r", head="h2",
                                 produced_by=[], existing=set(ids))
check("a second pass adds nothing and names what it left", new4 == [] and sorted(skipped4) == sorted(ids))

print("\n- refusals -")
try:
    rr.build_claims("x", {"methods": ART["methods"]}, dataset="d", stand="s", command="c", raw="r", head="h",
                    produced_by=[], existing=set())
    check("an artifact without a questions count is refused", False)
except ValueError as e:
    check("an artifact without a questions count is refused", "questions" in str(e))
with tempfile.TemporaryDirectory() as tmp:
    man = Path(tmp) / "m.json"
    man.write_text(json.dumps({"claims": [], "datasets": {}}), encoding="utf-8")
    rc = rr.main(["--family", "f", "--artifact", "research/results/locomo.json", "--dataset", "nope",
                  "--command", "python research/locomo_eval.py", "--stand", "s", "--manifest", str(man), "--dry-run"])
    check("an unregistered dataset is refused (exit 2)", rc == 2, str(rc))
    rc = rr.main(["--family", "f", "--artifact", "research/results/does_not_exist.json", "--dataset", "d",
                  "--command", "python research/locomo_eval.py", "--stand", "s", "--manifest", str(man), "--dry-run"])
    check("a missing artifact is refused (exit 2)", rc == 2, str(rc))
    check("nothing was written", json.loads(man.read_text(encoding="utf-8"))["claims"] == [])

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
