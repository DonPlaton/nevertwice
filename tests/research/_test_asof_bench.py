"""`research/asof_bench.py`: the scoring behind the as-of stand, without an extractor.

A case is correct only when the old day returns the old fact without the new one and the new
day returns the new fact; the dateless floor answers both days with one ranking; the pooled
score keeps per-run rates; the Mem0 blocker is recorded, never a zero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import asof_bench as ab  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


case = {"id": "val-http-timeout", "shape": "value_replaced", "domain": "the API client",
        "sessions": [["Settled it: the HTTP client timeout is 30 seconds.", "Tests stayed green."],
                     ["Came back to it. The HTTP client timeout is 5 seconds.", "Pairing session."]],
        "query": "what is the HTTP client timeout", "current": ["5 second", "5s"], "superseded": ["30 second", "30s"]}

print("\n- one case, two days -")
r = ab._row(case, ["http client timeout: the timeout is 30 seconds"], ["timeout: the HTTP client timeout is 5 seconds"])
check("old day right, new day right, both right", r["old_day_correct"] and r["new_day_correct"] and r["both_correct"])
r = ab._row(case, ["the timeout is 5 seconds now (was 30 seconds)"], ["the HTTP client timeout is 5 seconds"])
check("the old day is wrong when the new fact leaks into it", not r["old_day_correct"] and r["new_day_correct"] and not r["both_correct"])
r = ab._row(case, [], ["the timeout is 5 seconds"])
check("nothing on the old day is wrong on the old day", not r["old_day_correct"])
r = ab._row(case, ["the timeout is 30 seconds"], ["nothing relevant"])
check("the new day needs the replacement", r["old_day_correct"] and not r["new_day_correct"])

print("\n- the dateless floor -")
items = ab._naive_items(case, 5)
check("the floor ranks the case's own sentences by overlap with the query, timeout sentences first",
      len(items) == 4 and all("timeout" in s for s in items[:2]), str(items[:2]))
res = ab.run_naive([case], 5)
row = res["rows"][0]
check("with both facts in the same ranking the old day cannot be right", not row["old_day_correct"] and row["new_day_correct"])
check("the floor's score is over supersession cases", res["n_cases"] == 1 and res["both_correct_rate"] == 0.0)

print("\n- the score -")
rows = [{"shape": "value_replaced", "both_correct": True, "old_day_correct": True, "new_day_correct": True},
        {"shape": "value_replaced", "both_correct": False, "old_day_correct": True, "new_day_correct": False},
        {"shape": "control", "both_correct": False, "old_day_correct": False, "new_day_correct": False}]
sc = ab.score(rows)
check("controls are not counted", sc["n_cases"] == 2 and sc["both_correct_rate"] == 0.5 and sc["old_day_rate"] == 1.0)
check("a Wilson interval around the both-correct rate", sc["both_correct_ci"][0] < 0.5 < sc["both_correct_ci"][1])
check("errors counted", ab.score(rows + [{"shape": "value_replaced", "both_correct": False, "old_day_correct": False,
                                           "new_day_correct": False, "error": "x"}])["errors"] == 1)

print("\n- the days are fixed and ordered -")
check("first < between < second < after", ab.DAY_FIRST < ab.DAY_BETWEEN < ab.DAY_SECOND < ab.DAY_AFTER)

print("\n- J2: the item text is title + description, and why an old day failed -")
hits = [{"title": "beta dashboard flag gates new ui", "description": "The flag gates the new UI."}]
items = ab._items(hits)
check("the item text is title and description joined", items == ["beta dashboard flag gates new ui The flag gates the new UI."], str(items))
check("a hit with only a title still renders", ab._items([{"title": "t"}]) == ["t"])
ret_case = {"id": "ret-beta-flag", "shape": "retracted_no_replacement", "sessions": [[], []],
            "query": "what does the beta_dashboard flag control",
            "current": ["delet", "remov", "unconditional"], "superseded": ["gates the new ui"]}
r_hit = ab._row(ret_case, ["beta dashboard flag gates new ui The flag gates the new UI for beta users."], ["the flag was deleted"])
check("the marker is found in the description on the old day", r_hit["old_day_correct"] and r_hit["both_correct"])
r_para = ab._row(ret_case, ["a paraphrase that never says the marker phrase"], ["the flag was deleted"])
check("a paraphrase that drops the marker misses the old day", not r_para["old_day_correct"])
ok_row = {"old_day_correct": True}
check("no failure, no kind", ab.old_fail_kind(ok_row, {"s0": {"written": 1}}) is None)
check("nothing written for session one -> never_written",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, {"s0": {"written": 0}}) == "never_written")
check("written but nothing returned -> unranked",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, {"s0": {"written": 2}}) == "unranked")
check("returned, marker missed -> paraphrase",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False}, {"s0": {"written": 1}}) == "paraphrase")
check("returned with the new fact -> leak",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": True}, {"s0": {"written": 1}}) == "leak")
check("no store state: the zero-item case cannot be told apart and reads as unranked",
      ab.old_fail_kind({"old_day_correct": False, "old_items": 0}, None) == "unranked")
sc = ab.score([{"shape": "narrowed", "both_correct": False, "old_day_correct": False, "new_day_correct": True,
                "old_fail_kind": "paraphrase", "store": {"s0": {"written": 1}}},
               {"shape": "narrowed", "both_correct": False, "old_day_correct": False, "new_day_correct": False,
                "old_fail_kind": "never_written", "store": {"s0": {"written": 0}}},
               {"shape": "narrowed", "both_correct": True, "old_day_correct": True, "new_day_correct": True,
                "old_fail_kind": None, "store": {"s0": {"written": 1}}}])
check("the score folds the kinds and counts session-one silence",
      sc["old_day_failures_by_kind"] == {"never_written": 1, "absorbed": 0, "unranked": 0, "paraphrase": 1, "leak": 0}
      and sc["s0_never_written"] == 1 and sc["s0_absorbed"] == 0, str(sc))

# K1b/K3: a first-session note the twin gate absorbed into session two's is credited to session
# one through `sources`, and an old-day miss on it is its own kind, not silence
sc2 = ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False},
                       {"s0": {"written": 1, "absorbed": 1}})
check("every first-session note absorbed -> the old-day miss is `absorbed`, not never_written", sc2 == "absorbed", str(sc2))
sc3 = ab.old_fail_kind({"old_day_correct": False, "old_items": 1, "leak": False},
                       {"s0": {"written": 2, "absorbed": 1}})
check("one absorbed note beside one served -> the miss is read from the answer, not the absorb", sc3 == "paraphrase", str(sc3))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
