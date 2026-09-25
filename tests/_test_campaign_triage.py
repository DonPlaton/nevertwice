#!/usr/bin/env python3
"""The campaign's shape is a measured split, not a wish: which claims a re-run can settle.

The council's third point (2026-09-22) was to sort the restorable claims into "margin larger than
the noise" and "inside the noise" BEFORE the campaign and run only the first, so the campaign does
not reproduce the problem with fresh data and the same green checks. `tools/campaign_triage.py`
does the sorting; this suite pins its numbers so the split is a decision with a count attached
rather than a paragraph.

    A  271   deterministic given committed inputs   (211 our own arms, 60 competitors')
    B    0   noisy, printed no finer than its spread
    C   45   noisy, printed FINER than the stand resolves
    D  335   noisy, no run-to-run spread measured for the field
    E   10   machine-dependent

**B is zero OF THE FORTY-FIVE WHOSE SPREAD IS KNOWN**, and the qualifier is the whole point.
B and D are not independent: a claim cannot reach B without first leaving D, and D is "nobody
measured this field". So `B = 0` is a statement about the 45 claims on the five fields that were
measured - all 45 print finer than their stand resolves - and says nothing about the other 335.
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


#: K53 (stage D, 2026-09-25): the pins below are campaign v2's queue, so they are read off the
#: register AS campaign v2 left it - restore #2, commit 93e8052 - not off the working register. The
#: first stage-D engine fix withdrew 422 claims (219 -> 641 pending); a pin that follows the working
#: register has to be retyped after every engine commit, and a retyped count is exactly the
#: hand-kept number this suite exists to replace. Campaign v3 gets its own split, pinned against its
#: own frozen register when its plan exists. Full git history is required, as for check_freshness.
V2_REGISTER = "93e8052"
_show = subprocess.run(["git", "-C", str(ROOT), "show", f"{V2_REGISTER}:research/evidence_manifest.json"],
                       capture_output=True, text=True, encoding="utf-8")
MANIFEST = json.loads(_show.stdout) if _show.returncode == 0 else {"claims": []}
_c = MANIFEST["claims"]
claims = list(_c.values() if isinstance(_c, dict) else _c)
pending = [c for c in claims if c.get("pending_remeasure")]
check(f"campaign v2's register is readable at {V2_REGISTER} (needs full history)", bool(claims),
      _show.stderr.strip()[:200])
groups = T.groups_of(pending)

#: Measured 2026-09-22 at `5dafc5b`. A number here moving is not a failure of the code: it means
#: the campaign's shape changed, and whoever changed it says so in this line.
#: 2026-09-23, restore #1 of the campaign at 358fa75: 295 pending claims came back live (A 263 -> 35,
#: D 335 -> 268), and 47 went back to pending on purpose (seven timings taken on a loaded machine,
#: forty abstention claims from one draw with no spread). C and E did not move: nothing the campaign
#: ran settles a field printed finer than its spread, or a machine-dependent one.
#: 2026-09-23, the K3/K6 merge: the embedding stands changed, so their eight live claims went back
#: to pending (A 35 -> 43, all eight our own arms; the ninth, Matryoshka, is historical).
#: 2026-09-23, the owner allowed the store-reading stands: the 19 live-vault claims were measured on a
#: read-only copy of the store and came back live (A 43 -> 24, all nineteen our own arms).
#: 2026-09-24, the step-4 engine merge (Q5 principle layer, note_snippet word boundary, defaults set
#: by the Q5 gates): the engine moved under 314 live claims, withdrawn in one pass (A 24 -> 271,
#: D 268 -> 335; the A split moves to 211 our own / 60 competitors'). C and E did not move.
#: 2026-09-25, restore #2 of campaign v2 at fe6ddff: 426 pending claims came back live and the 16
#: bare head_to_head claims became historical, so the queue went 661 -> 219: A 271 -> 82 (own 211 ->
#: 68, competitors 60 -> 14), C 45 -> 15 (on their own stand 34 -> 10, transferred 11 -> 5; the
#: over-retraction gate 6 -> 2), D 335 -> 112. E did not move. Every value is what T.groups_of()
#: returns on the restored register; none was typed from memory.
KNOWN = {"A": 82, "B": 0, "C": 15, "D": 112, "E": 10}

print("\n- the split is the one the campaign was planned against -")
check("the pending set has not moved", len(pending) == sum(KNOWN.values()),
      f"{len(pending)} against {sum(KNOWN.values())}")
for g, n in KNOWN.items():
    check(f"group {g}: {n} claims - {T.NAMES[g]}", len(groups[g]) == n, str(len(groups[g])))

own = [c for c, _ in groups["A"] if not T.COMPETITOR.search(c["id"])]
comp = [c for c, _ in groups["A"] if T.COMPETITOR.search(c["id"])]
check("and A splits 68 our own / 14 competitor arms", (len(own), len(comp)) == (68, 14),
      f"{len(own)} / {len(comp)}")

#: C carries a seam of its own. The spread table was measured on ONE stand, so `SPREAD.get(leaf)`
#: is a rule by field NAME, and for a claim of another stand it transfers someone else's
#: resolution. 34 of the 45 sit on the stand the spread came from; 11 (abstention's `current_rate`,
#: code-sessions' `stale_rate`) inherit it. The transfer may hold - noise of the same kind is often
#: of the same order - but it is inherited rather than measured, and for the campaign those 11 may
#: turn out to belong in D, needing a run rather than a re-print.
home = [c for c, _ in groups["C"] if not T.transferred(c)]
away = [c for c, _ in groups["C"] if T.transferred(c)]
check("C splits 10 measured on their own stand / 5 on a transferred spread",
      (len(home), len(away)) == (10, 5), f"{len(home)} / {len(away)}")
check("and the table says which stand it was measured on, rather than implying every stand",
      T.SPREAD_MEASURED_ON == "research/supersession_bench.py", T.SPREAD_MEASURED_ON)

print("\n- B is empty OF THE MEASURED, which is a smaller claim than B is empty -")
measured = len(groups["B"]) + len(groups["C"])
check(f"the fields with a measured spread carry {measured} claims, and none of them is in B",
      not groups["B"] and measured == KNOWN["B"] + KNOWN["C"], f"B {len(groups['B'])}, measured {measured}")
check("the unmeasured majority is counted, not folded into the same sentence",
      len(groups["D"]) == KNOWN["D"], str(len(groups["D"])))
c_over = [c for c, _ in groups["C"]
          if (c.get("pointer") or "").endswith("over_retraction_rate")]
check("the gate called absolute is in C, printed finer than it is resolved", len(c_over) == 2,
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
#: sorted(set(...)): mid-merge `git ls-files` lists an unmerged file once per stage (1-3), which
#: read this pin as 19 during the 9b merge (2026-09-24) - the count must not depend on index state.
_tracked = sorted(set(subprocess.run(["git", "ls-files", "*.py"], cwd=T.ROOT, capture_output=True,
                                     text=True, encoding="utf-8").stdout.split()))
_src = [f for f in _tracked if f not in T.CLOSURE_EXCLUDED_FILES and T.MODEL_CALL.search(
        (T.ROOT / f).read_text(encoding="utf-8", errors="replace"))]
#: Nineteen, of which eleven CALL the endpoint and eight only quote it: this suite, the tool
#: itself, `_test_audit_fixes.py`, `tests/research/_test_ollama_pacer.py` (R-v2-ports' T7
#: names `/api/generate` as a `httpx.MockTransport` path for its streaming-response case),
#: and `research/_ollama_symmetry_probe.py` (its fake Ollama SERVES `/api/chat` and
#: `/api/generate` - it answers those paths, it never calls them),
#: `tests/research/_test_frontier.py` (item 9B's K35 check sends one request to `/api/chat`
#: through a FAKED `urllib.request.urlopen`, so the pacer counts one clean paced call - it never
#: reaches a real endpoint), and
#: `tests/research/_test_k8_judge_eval.py` (R-v2-ports item 9A's own pacer-wiring suite: its
#: fake `urllib.request.urlopen` returns a canned `/api/generate`-shaped response so
#: `k8_judge_eval.judge()`'s real parsing path runs, never an actual call to the string), and
#: `tests/research/_test_guard_bench.py` (the auditor's G3 check: its FAKED `urlopen` answers
#: `/api/generate` and `/api/chat` with a 500 so guards_llm blocks - never a real endpoint).
#: That is the declared false-positive direction - a file wrongly read as touching a model is
#: kept out of the deterministic group, which is the safe side for a campaign plan. The count
#: is pinned so that narrowing the rule again reddens here instead of quietly returning it to
#: the one file it used to see.
#: K33 (item 9D, 2026-09-24): `research/_ollama_pacer.py` NOW also matches MODEL_CALL (its own
#: bounded LLM retry names /api/generate and /api/chat), one MORE match by raw text - kept
#: out of this listing via `T.CLOSURE_EXCLUDED_FILES`, the same exact-path skip `triage()`'s own
#: closure walk uses, so the population this check pins stays the files whose mention is
#: evidence a CALLER (not shared transport) touches a model. Integration of 9A-rest (2026-09-24):
#: 16 + the _test_frontier.py, _test_k8_judge_eval.py and _test_guard_bench.py quotes = 19; the pacer
#: is not counted.
check("nineteen tracked sources name a generation endpoint, so the rule has a population",
      len(_src) == 19, str(len(_src)))
check("and the generators it could not see before are among them",
      {"research/gen_code_sessions.py", "research/frontier_eval.py",
       "research/token_ab.py"} <= set(_src))
_door, _model = T._asked_of_source("research/gen_code_sessions.py")
check("a corpus generator that interpolates the URL is seen as calling a model", _model)
check("and it is not mistaken for a stand that writes notes", not _door)
check("a path that merely starts the same way is not an endpoint",
      not T.MODEL_CALL.search("/api/chatter"))

print("")
print("- K33 (item 9D, 2026-09-24): the pacer is shared transport, not a door or a caller - "
      "excluded from the closure walk the same reasoning already excludes nevertwice/ -")
check("research/_ollama_pacer.py and sandbox_guard.py are excluded, by exact path (never a prefix or pattern)",
      T.CLOSURE_EXCLUDED_FILES == frozenset({"research/_ollama_pacer.py", "sandbox_guard.py"}),
      str(T.CLOSURE_EXCLUDED_FILES))
_pacer_only_claim = {"pointer": "arms.nevertwice.both_correct_rate",
                     "produced_by": ["research/_ollama_pacer.py"],
                     "command": "python research/asof_bench.py --save"}
check("a claim whose closure holds the pacer (and nothing else that calls a model or a door) "
      "stays deterministic", T.triage(_pacer_only_claim)[0] == "A",
      str(T.triage(_pacer_only_claim)))

# mutation: the exclusion removed - the SAME claim now WRONGLY reads noisy, the shape of the
# 226-claim A->D regression this skip exists to prevent (measured 2026-09-24, before the skip:
# A 271->45, D 335->561, own/comp split 211/60->45/0).
_saved_excluded = T.CLOSURE_EXCLUDED_FILES
T.CLOSURE_EXCLUDED_FILES = frozenset()
try:
    check("mutation 'pacer exclusion removed': the SAME claim now WRONGLY reads noisy "
          "(would FAIL the deterministic check above)",
          T.triage(_pacer_only_claim)[0] != "A", str(T.triage(_pacer_only_claim)))
finally:
    T.CLOSURE_EXCLUDED_FILES = _saved_excluded
check("CLOSURE_EXCLUDED_FILES is restored to the real set",
      T.CLOSURE_EXCLUDED_FILES == _saved_excluded)

#: (б) C5, stage D: sandbox_guard.py names the CLOSED `/api/generate` a test process is pointed at
#: and never calls it. It is in nearly every claim's closure; unexcluded it moved A 82 -> 4.
_guard_only_claim = {"pointer": "arms.nevertwice.both_correct_rate",
                     "produced_by": ["sandbox_guard.py"],
                     "command": "python research/asof_bench.py --save"}
check("sandbox_guard.py really does name a generation endpoint (so the exclusion is doing work)",
      bool(T.MODEL_CALL.search((T.ROOT / "sandbox_guard.py").read_text(encoding="utf-8"))))
check("a claim whose closure holds sandbox_guard.py (and nothing that calls a model) stays deterministic",
      T.triage(_guard_only_claim)[0] == "A", str(T.triage(_guard_only_claim)))
T.CLOSURE_EXCLUDED_FILES = frozenset({"research/_ollama_pacer.py"})
try:
    check("mutation 'sandbox_guard exclusion removed': the SAME claim now WRONGLY reads noisy "
          "(would FAIL the deterministic check above)",
          T.triage(_guard_only_claim)[0] != "A", str(T.triage(_guard_only_claim)))
finally:
    T.CLOSURE_EXCLUDED_FILES = _saved_excluded


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


print("")
print("- a spread names the corpus it was measured on, or says it was not recorded -")
#: The pin was supposed to make this stand reproducible. It did, on ONE corpus: five runs on the
#: explicit corpus at temperature 0 span 35.4 characters while the implicit corpus reproduces to
#: 1.30, a factor of twenty-three at the same pin. The legacy table cannot say which corpus it
#: describes, so comparing it against either is comparing two numbers that each carry an
#: unrecorded variable. Found 2026-09-22 by running the stand instead of trusting the table; the
#: missing field was named by the auditing session.
check("the legacy record admits it does not know its corpus",
      "corpus" in T.SPREAD_MEASURED_AT and T.SPREAD_MEASURED_AT["corpus"] is None,
      str(T.SPREAD_MEASURED_AT))
_pp = T.SPREAD_POST_PIN.get(T.SPREAD_MEASURED_ON) or {}
check("and the post-pin measurements name theirs", set(_pp) == {"explicit", "implicit"},
      str(sorted(_pp)))
check("each of them says the field, the range and how many runs",
      all({"field", "range", "runs"} <= set(v) for v in _pp.values()), str(_pp)[:100])
#: The finding itself, pinned as a number rather than a sentence: the two corpora disagree by
#: more than an order of magnitude, so a run count taken from one does not transfer to the other.
_ratio = _pp["explicit"]["range"] / _pp["implicit"]["range"] if _pp else 0
check(f"the two corpora disagree by more than tenfold ({_ratio:.0f}x), so a run count does not "
      f"transfer between them", _ratio >= 10, f"{_ratio:.1f}")


print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
