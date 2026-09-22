#!/usr/bin/env python3
"""The campaign's shape is a measured split, not a wish: which claims a re-run can settle.

The council's third point (2026-09-22) was to sort the restorable claims into "margin larger than
the noise" and "inside the noise" BEFORE the campaign and run only the first, so the campaign does
not reproduce the problem with fresh data and the same green checks. `tools/campaign_triage.py`
does the sorting; this suite pins its numbers so the split is a decision with a count attached
rather than a paragraph.

    A  188   deterministic given committed inputs   (128 our own arms, 60 competitors')
    B    0   noisy, printed no finer than its spread
    C   45   noisy, printed FINER than the stand resolves
    D  329   noisy, no run-to-run spread measured for the field
    E   10   machine-dependent

**B is zero OF THE FORTY-FIVE WHOSE SPREAD IS KNOWN**, and the qualifier is the whole point.
B and D are not independent: a claim cannot reach B without first leaving D, and D is "nobody
measured this field". So `B = 0` is a statement about the 45 claims on the five fields that were
measured - all 45 print finer than their stand resolves - and says nothing about the other 329.
Written flat as "no claim prints coarser than its spread" it would read as a property of the
project when it is a property of our knowledge (the distinction is the auditing session's,
2026-09-22). The five-run study is the authority for the 45: `over_retraction_rate` ranged
.0-.05 across five runs of ONE commit, and it is the gate everyone called absolute at 0.000.

Two wrong signs were caught before this went anywhere, and both are exercised below, because a
gate that refuses on a sign it never checked is the failure this repository spent the night on:

    the engine DEFINES the doors, so `nevertwice/_engine_cards.py` is in every claim's closure -
      asking "does the closure mention a door" made all 572 noisy
    `ollama` appears in `longmem_eval.py:111` as the EMBEDDER's provider and in a docstring of
      `_rerank.py` - matching the word made every retrieval claim noisy, though embedding is
      cached and the ranking over it is arithmetic

    python tests/_test_campaign_triage.py
"""
import _env_guard  # noqa: F401
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import campaign_triage as T  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_c = MANIFEST["claims"]
claims = list(_c.values() if isinstance(_c, dict) else _c)
pending = [c for c in claims if c.get("pending_remeasure")]
groups = T.groups_of(pending)

#: Measured 2026-09-22 at `5dafc5b`. A number here moving is not a failure of the code: it means
#: the campaign's shape changed, and whoever changed it says so in this line.
KNOWN = {"A": 188, "B": 0, "C": 45, "D": 329, "E": 10}

print("\n- the split is the one the campaign was planned against -")
check("the pending set has not moved", len(pending) == sum(KNOWN.values()),
      f"{len(pending)} against {sum(KNOWN.values())}")
for g, n in KNOWN.items():
    check(f"group {g}: {n} claims - {T.NAMES[g]}", len(groups[g]) == n, str(len(groups[g])))

own = [c for c, _ in groups["A"] if not T.COMPETITOR.search(c["id"])]
comp = [c for c, _ in groups["A"] if T.COMPETITOR.search(c["id"])]
check("and A splits 128 our own / 60 competitor arms", (len(own), len(comp)) == (128, 60),
      f"{len(own)} / {len(comp)}")

#: C carries a seam of its own. The spread table was measured on ONE stand, so `SPREAD.get(leaf)`
#: is a rule by field NAME, and for a claim of another stand it transfers someone else's
#: resolution. 34 of the 45 sit on the stand the spread came from; 11 (abstention's `current_rate`,
#: code-sessions' `stale_rate`) inherit it. The transfer may hold - noise of the same kind is often
#: of the same order - but it is inherited rather than measured, and for the campaign those 11 may
#: turn out to belong in D, needing a run rather than a re-print.
home = [c for c, _ in groups["C"] if not T.transferred(c)]
away = [c for c, _ in groups["C"] if T.transferred(c)]
check("C splits 34 measured on their own stand / 11 on a transferred spread",
      (len(home), len(away)) == (34, 11), f"{len(home)} / {len(away)}")
check("and the table says which stand it was measured on, rather than implying every stand",
      T.SPREAD_MEASURED_ON == "research/supersession_bench.py", T.SPREAD_MEASURED_ON)

print("\n- B is empty OF THE MEASURED, which is a smaller claim than B is empty -")
measured = len(groups["B"]) + len(groups["C"])
check(f"the fields with a measured spread carry {measured} claims, and none of them is in B",
      not groups["B"] and measured == 45, f"B {len(groups['B'])}, measured {measured}")
check("the unmeasured majority is counted, not folded into the same sentence",
      len(groups["D"]) == 329, str(len(groups["D"])))
c_over = [c for c, _ in groups["C"]
          if (c.get("pointer") or "").endswith("over_retraction_rate")]
check("the gate called absolute is in C, printed finer than it is resolved", len(c_over) == 6,
      str(len(c_over)))

print("\n- the two wrong signs are still refused, not merely remembered -")
door, model = T._asked_of_source("research/longmem_eval.py")
check("longmem_eval reaches no engine door", not door)
check("and naming the embedder's provider is not a model call", not model)
door, model = T._asked_of_source("research/k8_judge_eval.py")
check("k8_judge_eval is caught by the endpoint it posts to, not by a stand list", model)
door, _ = T._asked_of_source("research/supersession_bench.py")
check("and a stand that writes notes is caught by the door it calls", door)
#: The engine implements the doors; if it counted, every claim would be noisy - which is exactly
#: what the first version of this rule reported.
check("the engine's own modules are excluded, or the answer is 572",
      T.triage({"pointer": "recall@5", "produced_by": ["nevertwice/_engine_cards.py"],
                "command": "python research/longmem_eval.py --save"})[0] == "A")

print("\n- and the spreads are the measured ones, with a source -")
check("five fields carry a measured run-to-run spread", len(T.SPREAD) == 5, str(len(T.SPREAD)))
check("over-retraction's spread is the .05 that one run in five produced",
      T.SPREAD["over_retraction_rate"] == 0.05, str(T.SPREAD["over_retraction_rate"]))

print("")
print("- the endpoint is recognised however the URL was built -")
#: The rule used to demand a quote right before the path, which means the endpoint had to be the
#: WHOLE string literal. Every caller in this repository builds it by interpolation instead -
#: `f"{OLLAMA}/api/chat"` - so the rule saw ONE file in the tree while thirteen contain the
#: endpoint, and the check above it passed because it was tried on that one file. Nothing in the
#: register moved (the group sizes pinned at the top of this suite are the same under both
#: rules), but `research/gen_code_sessions.py` generates a corpus with a local model and read as
#: pure arithmetic. Found by a reviewing agent over the shift diff, 2026-09-22.
_tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=T.ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout.split()
_src = [f for f in _tracked if T.MODEL_CALL.search(
        (T.ROOT / f).read_text(encoding="utf-8", errors="replace"))]
#: Fourteen, of which eleven CALL the endpoint and three only quote it: this suite, the tool
#: itself, and `_test_audit_fixes.py`. That is the declared false-positive direction - a file
#: wrongly read as touching a model is kept out of the deterministic group, which is the safe
#: side for a campaign plan. The count is pinned so that narrowing the rule again reddens here
#: instead of quietly returning it to the one file it used to see.
check("fourteen tracked sources name a generation endpoint, so the rule has a population",
      len(_src) == 14, str(len(_src)))
check("and the generators it could not see before are among them",
      {"research/gen_code_sessions.py", "research/frontier_eval.py",
       "research/token_ab.py"} <= set(_src))
_door, _model = T._asked_of_source("research/gen_code_sessions.py")
check("a corpus generator that interpolates the URL is seen as calling a model", _model)
check("and it is not mistaken for a stand that writes notes", not _door)
check("a path that merely starts the same way is not an endpoint",
      not T.MODEL_CALL.search("/api/chatter"))


print("")
print("- the spread table names the MODE it was measured in, and the mode is checked -")
#: `SPREAD_MEASURED_ON` was added when a property of a named stand had quietly become a property
#: of a field. One level down, a property of a stand IN A MODE had become a property of the stand:
#: every number in the table was sampled at extractor temperature 0.2, `f405891` pinned the stand
#: to 0, and the same field then ranged 1.30 over three runs instead of 26.4 over five. The table
#: plans a campaign, so the mode is a VALUE here and the staleness is derived from the stand's own
#: source rather than remembered. Found 2026-09-22 by using the table on a gate margin.
_stand = (T.ROOT / T.SPREAD_MEASURED_ON).read_text(encoding="utf-8", errors="replace")
_pin = re.search(r'NEVERTWICE_EXTRACT_TEMP"\]\s*=\s*"([^"]+)"', _stand)
check("the stand pins an extraction temperature in its own source", bool(_pin),
      T.SPREAD_MEASURED_ON)
check("the table says which temperature and how many runs it was measured over",
      T.SPREAD_MEASURED_AT.get("extract_temp") and T.SPREAD_MEASURED_AT.get("runs"),
      str(T.SPREAD_MEASURED_AT))
_stale = bool(_pin) and _pin.group(1) != T.SPREAD_MEASURED_AT["extract_temp"]
check(f"and the upper-bound flag agrees with the comparison "
      f"(stand pins {_pin.group(1) if _pin else '?'}, table measured at "
      f"{T.SPREAD_MEASURED_AT['extract_temp']})",
      T.SPREAD_IS_UPPER_BOUND == _stale,
      f"flag={T.SPREAD_IS_UPPER_BOUND} but stale={_stale}")
#: The rule bites: were the table re-measured at the stand's current pin, the flag would have to
#: come down with it, and claiming either half alone is refused.
check("re-measuring at the stand's pin without lowering the flag would be refused",
      not (_pin and _pin.group(1) == T.SPREAD_MEASURED_AT["extract_temp"]
           and T.SPREAD_IS_UPPER_BOUND))


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
