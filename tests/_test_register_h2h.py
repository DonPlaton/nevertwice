"""`tools/register_h2h.py`: a head-to-head family is registered from its artifact, not by hand.

Pins the claim shape the renderers and the freshness tools rely on: ids, statement forms,
printed forms, Wilson intervals on recall and none on MRR, the artifact's `n`, the package
version in the label, blocked arms skipped and named, existing claims never rewritten.
"""
import _env_guard  # noqa: F401
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import register_h2h as rh  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def arm(r1, r5, r10, mrr, version, n=1977, **extra):
    return {"recall@1": r1, "recall@3": (r1 + r5) / 2, "recall@5": r5, "recall@10": r10,
            "mrr@10": mrr, "n": n, "version": version, **extra}


ART = {
    "_provenance": {"corpus": "locomo10"}, "_questions": 1977,
    "nevertwice": arm(0.271, 0.494, 0.582, 0.3661, "nevertwice==2.4.0"),
    "mem0": arm(0.30, 0.575, 0.66, 0.42, "mem0ai==2.0.19"),
    "langmem": arm(0.20, 0.40, 0.50, 0.30, "langmem==0.0.30"),
    "amem": arm(0.21, 0.41, 0.51, 0.31, "chromadb==1.5.9"),
    "amem_full": {"blocked": "a-mem not installed", "version": "a-mem (not installed)"},
    "cognee": {"blocked": "no adapter"},
}

print("\n- claims from an artifact -")
new, skipped, blocked = rh.build_claims("h2h_locomo", ART, systems=None, head="deadbeef",
                                        produced_by=["research/head_to_head.py"],
                                        existing=set())
ids = [c["id"] for c in new]
#: Five metrics, not four. The registrar used to carry its own `KS = (1, 5, 10)` against the
#: stand's `(1, 3, 5, 10)`, so recall@3 was measured on every run and registered on none - the
#: register holds 19 recall@1, 19 recall@5, 19 recall@10 from this family and ZERO recall@3.
#: The depths now come off the artifact, and this count is the assertion that they do: an arm
#: carrying four depths yields four recall claims, and a copy of the constant anywhere in the
#: registrar would show up here as sixteen again.
check("four unblocked arms x (four depths + the reciprocal rank) = twenty claims",
      len(new) == 20, str(len(new)))
check("the depth the registrar used to drop is registered",
      "h2h_locomo.mem0.recall_at_3" in ids, str(sorted(i for i in ids if "recall_at" in i)))
check("ids follow the family.system.metric shape",
      "h2h_locomo.mem0.recall_at_5" in ids and "h2h_locomo.amem.mrr" in ids)
check("blocked arms are skipped and named", len(blocked) == 2 and blocked[0].startswith("amem_full:"),
      str(blocked))
by = {c["id"]: c for c in new}
m5 = by["h2h_locomo.mem0.recall_at_5"]
check("statement carries the package version from the artifact row",
      m5["statement"].startswith("Mem0 2.0.19 reaches RECALL@5 0.575 on the LoCoMo global pool"),
      m5["statement"])
check("printed form is the three-decimal figure", m5["printed"] == ["0.575"])
check("value is the artifact's number, untouched", m5["value"] == 0.575)
check("pointer resolves into the artifact", m5["pointer"] == "mem0.recall@5")
check("n is the artifact's, not a constant", m5["n"] == 1977)
ci = m5["ci"]
check("recall claims carry a Wilson interval around the value",
      ci and ci["method"] == "wilson" and ci["low"] < 0.575 < ci["high"], str(ci))
check("MRR claims carry no interval", by["h2h_locomo.mem0.mrr"]["ci"] is None)
#: The statement quotes the field's own name, so it says MRR@10 as soon as the stand starts
#: truncating at max(KS) - the depth is a condition of the number and belongs in the sentence a
#: reader sees, not only in the unit. Spelled from the fixture's key rather than written out.
check("MRR statement form", by["h2h_locomo.mem0.mrr"]["statement"]
      == "Mem0 2.0.19 reaches MRR@10 0.420 on the LoCoMo global pool "
         "(all ten conversations in one store)")
check("and the unit is that same name, not a bare 'mrr'",
      by["h2h_locomo.mem0.mrr"]["unit"] == "mrr@10", by["h2h_locomo.mem0.mrr"]["unit"])
check("our label needs no version", by["h2h_locomo.nevertwice.recall_at_1"]["statement"]
      .startswith("Nevertwice (calibrated fusion) reaches RECALL@1 0.271"))
check("A-MEM label names its store and version", by["h2h_locomo.amem.mrr"]["statement"]
      .startswith("A-MEM (chromadb 1.5.9) reaches MRR@10 0.310"))
check("every claim names dataset, environment, raw, command, commit and closure",
      all(c["dataset"] == "locomo10_pinned" and c["environment"] == rh.ENVIRONMENT
          and c["raw"].endswith("head_to_head_locomo.json") and c["commit"] == "deadbeef"
          and c["produced_by"] == ["research/head_to_head.py"] and c["command"] for c in new))
check("cited where the family's table is printed",
      all(c["cited_in"] == ["docs/BENCHMARKS.md"] for c in new))

print("\n- existing claims are never rewritten -")
new2, skipped2, _ = rh.build_claims("h2h_locomo", ART, systems=None, head="cafebabe",
                                    produced_by=[], existing=set(ids))
check("a second pass adds nothing", new2 == [])
check("...and names every claim it left alone", sorted(skipped2) == sorted(ids))

print("\n- selecting arms and overriding the command -")
new3, _, blocked3 = rh.build_claims("h2h_pinned", {**ART, "mem0_infer": arm(0.5, 0.7, 0.8, 0.6, "mem0ai==2.0.19", n=500)},
                                    systems=["mem0_infer", "amem_full"], head="h", produced_by=[],
                                    existing=set(), command="python research/head_to_head.py --only=mem0_infer",
                                    note="pipeline note")
check("only the selected arms are registered", {c["id"].split(".")[1] for c in new3} == {"mem0_infer"})
check("the pipeline label says what ran", new3[0]["statement"]
      .startswith("Mem0 2.0.19 full pipeline (its LLM extraction on) reaches RECALL@1 0.500 on the pinned stand"))
check("command and note overrides land on every claim",
      all(c["command"].endswith("--only=mem0_infer") and c["note"] == "pipeline note" for c in new3))
check("a selected arm that blocked is reported", blocked3 == ["amem_full: a-mem not installed"], str(blocked3))

print("\n- the label helper -")
check("version parsed from pkg==ver", rh.label_for("langmem", {"version": "langmem==0.0.30"}) == "LangMem 0.0.30")
check("no version, no dangling space", rh.label_for("mem0", {"version": "?"}) == "Mem0")
check("unknown arm gets a plain label", rh.label_for("zep", {"version": "graphiti-core==0.3"}) == "zep 0.3")

print("\n- the CLI refuses an artifact older than HEAD and a dirty closure -")
with tempfile.TemporaryDirectory() as tmp:
    man = Path(tmp) / "m.json"
    man.write_text(json.dumps({"claims": []}), encoding="utf-8")
    rc = rh.main(["--family", "h2h_locomo", "--manifest", str(man), "--dry-run"])
    check("dry-run against the real artifact exits 0 or names why it cannot (2), never crashes",
          rc in (0, 2), str(rc))
    check("dry-run wrote nothing", json.loads(man.read_text(encoding="utf-8")) == {"claims": []})

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
