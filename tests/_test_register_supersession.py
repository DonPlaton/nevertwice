"""`tools/register_supersession.py`: a pooled supersession artifact becomes one claim family.

Pins the seventeen-claim shape the v1 family has: pooled engine rates with Wilson intervals over
case-runs, the other arms' rates with intervals over cases, the paired McNemar p-values in the
artifact's pair order, characters per query, the dataset counts; blocked arms skipped; existing
claims left alone; the refusals of the CLI.
"""
import _env_guard  # noqa: F401
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import register_supersession as rs  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


ART = {
    "arms": {
        "nevertwice": {"stale_rate": 0.05, "current_rate": 0.9, "over_retraction_rate": 0.1,
                       "mean_chars_returned": 250.0, "rows": []},
        "nevertwice_run2": {"stale_rate": 0.1, "current_rate": 0.9, "rows": []},
        "mem0": {"stale_rate": 0.9, "current_rate": 0.95, "over_retraction_rate": 0.0,
                 "mean_chars_returned": 450.0, "rows": []},
        "naive": {"stale_rate": 0.95, "current_rate": 0.95, "over_retraction_rate": 0.05,
                  "mean_chars_returned": 220.0, "rows": []},
        "zep": {"blocked": "needs a graph database"},
    },
    "k": 5, "llm": "llm", "embedder": "emb",
    "dataset": {"name": "supersession_v1_implicit", "sha256": "abc", "path": "x", "cases": 80,
                "supersession_cases": 60, "control_cases": 20},
    "pooled_nevertwice": {"runs": 2,
                          "stale": {"k": 9, "n": 120, "rate": 0.075, "ci": [0.04, 0.137], "per_run": [0.05, 0.1]},
                          "current": {"k": 108, "n": 120, "rate": 0.9, "ci": [0.833, 0.942], "per_run": [0.9, 0.9]},
                          "over_retraction": {"k": 4, "n": 40, "rate": 0.1, "ci": [0.04, 0.231]},
                          "mean_chars_returned": 250.0},
    "pooled_note": "two runs",
    "pairs": [{"a": "mem0", "b": "naive", "n": 60, "stale_only_mem0": 2, "stale_only_naive": 4, "discordant": 6, "p_mcnemar": 0.6875},
              {"a": "mem0", "b": "nevertwice", "n": 60, "stale_only_mem0": 52, "stale_only_nevertwice": 1, "discordant": 53, "p_mcnemar": 2.2e-14},
              {"a": "naive", "b": "nevertwice", "n": 60, "stale_only_naive": 55, "stale_only_nevertwice": 1, "discordant": 56, "p_mcnemar": 1.1e-15}],
    "pairs_per_engine_run": [{"nevertwice_only": 1, "mem0_only": 52}, {"nevertwice_only": 2, "mem0_only": 50}],
}

print("\n- the family from a pooled artifact -")
new, skipped = rs.build_claims("supersession_implicit", ART, dataset="supersession_v1_implicit",
                               command="python research/supersession_bench.py --dataset x --pool a b --with c",
                               raw="research/results/supersession_v1_implicit.json", head="deadbeef",
                               produced_by=["research/supersession_bench.py"], existing=set(),
                               stand="the implicit-replacement variant")
by = {c["id"]: c for c in new}
check("thirty claims: 5 pooled engine, 4 per other arm x 2, 3 causes per other arm x 2, 3 pairs with 2 "
      "discordant counts each, 2 dataset", len(new) == 30, str(len(new)))
check("the engine's control-miss rate is skipped when the artifact carries no rows to derive it from",
      "supersession_implicit.nevertwice.control_miss_rate" not in by)
check("another arm's broad rate is registered as a control miss, never as over-retraction",
      "supersession_implicit.mem0.control_miss_rate" in by and "supersession_implicit.mem0.over_retraction_rate" not in by
      and by["supersession_implicit.mem0.control_miss_rate"]["pointer"] == "arms.mem0.over_retraction_rate")
check("a floor that missed one control has one ranking miss by construction",
      by["supersession_implicit.naive.control_miss.unranked"]["value"] == 1
      and by["supersession_implicit.naive.control_miss.retired"]["value"] == 0
      and "construction" in by["supersession_implicit.naive.control_miss.unranked"]["derivation"])
check("an arm that missed nothing has three zero causes", all(
      by[f"supersession_implicit.mem0.control_miss.{k}"]["value"] == 0 for k in ("retired", "never_written", "unranked")))
check("discordant counts point into the pair, one per side",
      by["supersession_implicit.mem0_vs_nevertwice.discordant.mem0"]["value"] == 52
      and by["supersession_implicit.mem0_vs_nevertwice.discordant.nevertwice"]["pointer"] == "pairs[1].stale_only_nevertwice")
check("the blocked arm has no claims", not any(".zep." in i for i in by))
st = by["supersession_implicit.nevertwice.stale_rate"]
check("pooled stale rate over case-runs with its interval and the per-run note",
      st["value"] == 0.075 and st["n"] == 120 and st["ci"]["low"] == 0.04 and "0.05 and 0.1" in st["note"], st["statement"])
check("statement names the variant and the pooling", "implicit-replacement variant, pooled over 2 runs" in st["statement"])
check("over-retraction counts control case-runs", by["supersession_implicit.nevertwice.over_retraction_rate"]["n"] == 40)
m0 = by["supersession_implicit.mem0.stale_rate"]
check("another arm: rate over cases with a Wilson interval", m0["n"] == 60 and m0["ci"]["low"] < 0.9 < m0["ci"]["high"]
      and m0["statement"].startswith("Mem0 2.0.19 returns a retracted fact"))
check("characters per query, no interval", by["supersession_implicit.mem0.chars_per_query"]["value"] == 450.0
      and by["supersession_implicit.mem0.chars_per_query"]["ci"] is None)
p1 = by["supersession_implicit.mem0_vs_nevertwice.p_mcnemar"]
check("pairs keep the artifact's order in their pointers", p1["pointer"] == "pairs[1].p_mcnemar")
check("a tiny p prints in the register's power-of-ten form", p1["printed"] == ["2.2 x 10^-14"], str(p1["printed"]))
check("a plain p prints with two decimals", by["supersession_implicit.mem0_vs_naive.p_mcnemar"]["printed"] == ["0.69"])
check("dataset counts", by["supersession_implicit.dataset.supersession_cases"]["value"] == 60
      and by["supersession_implicit.dataset.control_cases"]["value"] == 20)
check("every claim carries dataset, environment, raw, command, commit and closure",
      all(c["dataset"] == "supersession_v1_implicit" and c["environment"] == rs.ENVIRONMENT
          and c["commit"] == "deadbeef" and c["produced_by"] == ["research/supersession_bench.py"] for c in new))

print("\n- existing claims are never rewritten -")
new2, skipped2 = rs.build_claims("supersession_implicit", ART, dataset="d", command="c", raw="r", head="h",
                                 produced_by=[], existing=set(by))
check("a second pass adds nothing and names every claim it left", new2 == [] and len(skipped2) == 30)

print("\n- refusals -")
with tempfile.TemporaryDirectory() as tmp:
    man = Path(tmp) / "m.json"
    man.write_text(json.dumps({"claims": [], "datasets": {}}), encoding="utf-8")
    rc = rs.main(["--family", "f", "--artifact", "research/results/supersession_v1.json", "--dataset", "nope",
                  "--command", "python research/supersession_bench.py", "--manifest", str(man), "--dry-run"])
    check("an unregistered dataset is refused", rc == 2)
    rc = rs.main(["--family", "f", "--artifact", "research/results/nope.json", "--dataset", "d",
                  "--command", "python research/supersession_bench.py", "--manifest", str(man), "--dry-run"])
    check("a missing artifact is refused", rc == 2)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
