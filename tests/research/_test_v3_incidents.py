#!/usr/bin/env python3
"""PREREG-V3 TB4.11b (A6): research/v3/incidents.py - the incident gate, the halts, the balance rule and the repair
plan (rev1 §4.5, P2; Q24).

* Q24's upstream failure: >= 500, 429, no status, incomplete and not abandoned - on any port role; a 4xx other than
  429, a refused or an abandoned call is not one;
* the gate: 5 failures across 2 arms within 60 s open it (M-INC-threshold); 5 from one arm do not; failures older
  than 60 s fall out; while open no new unit starts (M-INC-new-unit-during); a canary every 60 s; ONE success does not
  close it, two consecutive do (M-INC-close-one-success), a failure resets the count; the failures seen while it
  was open are dropped at INCIDENT END (I11); Q25-INC: 5 failures of ONE arm make the scheduler's probe due (no
  incident open, none sent in 60 s), and a failed probe is the second arm;
* halts 401/402/403 (M-HALT-402-continues); the balance rule 2x;
* the repair plan: never after scoring (M-REPAIR-after-scoring); exogenous units re-run for EVERY arm
  (M-REPAIR-exo-subset) in fresh stores; endogenous for one arm, in fresh stores (M-REPAIR-endo-store-reuse); no
  lone-arm whole-run re-run (M-REPAIR-lone-arm-whole-run); > 10 % exogenous makes the stand invalid.

    python tests/research/_test_v3_incidents.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_incidents", ROOT / "research" / "v3" / "incidents.py")
IN = importlib.util.module_from_spec(_spec)
sys.modules["v3_incidents"] = IN
_spec.loader.exec_module(IN)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except IN.IncidentError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not an IncidentError: {type(e).__name__}: {e}"


print("- Q24: what an upstream failure is -")
for label, c, want in (("a 500", {"status": 500, "complete": True}, True), ("a 429", {"status": 429}, True),
                       ("no status (an upstream error)", {"status": None}, True),
                       ("an incomplete 200 not abandoned", {"status": 200, "complete": False}, True),
                       ("a whole 200", {"status": 200, "complete": True}, False), ("a 400", {"status": 400, "complete": True}, False),
                       ("an incomplete 200 the client abandoned", {"status": 200, "complete": False, "client_abandoned": True}, False),
                       ("a call the proxy refused", {"status": None, "refused": "model_mismatch"}, False),
                       ("a 503 on the reader port", {"status": 503, "port_role": "reader"}, True)):
    check(f"{label}: {want}", IN.is_upstream_failure(c) is want)

print("\n- the gate -")
g1 = IN.IncidentGate()
for i in range(5):
    g1.observe("mem0", 10 + i, {"status": 500})
check("5 failures from ONE arm do not open an incident", not g1.is_open and g1.admits_new_unit())
g = IN.IncidentGate()
for i in range(3):
    g.observe("mem0", 10 + i, {"status": 500})
g.observe("zep", 13, {"status": 502})
check("4 failures across 2 arms do not open it", not g.is_open)
g.observe("zep", 15, {"status": 502})
check("M-INC-threshold: the FIFTH failure within 60 s, across 2 arms, opens it", g.is_open
      and g.events == [("INCIDENT START", 15)], str(g.events))
check("M-INC-new-unit-during: no new unit while it is open", not g.admits_new_unit())
g2 = IN.IncidentGate()
for i, arm in enumerate(["a", "b", "a", "b"]):
    g2.observe(arm, i, {"status": 500})
g2.observe("a", 61.5, {"status": 500})
check("failures older than 60 s fall out of the window", not g2.is_open)
g3 = IN.IncidentGate()
for i, arm in enumerate(["a", "b", "a", "b"]):
    g3.observe(arm, 100 + i, {"status": 500})
g3.observe("a", 104, {"status": 200, "complete": True})
check("a success is not a failure", not g3.is_open)
check("a canary is due at once, then every 60 s", g.canary_due(15) and (g.canary(15, True) or True)
      and not g.canary_due(74) and g.canary_due(75))
check("M-INC-close-one-success: one success does not close it", g.is_open)
g.canary(75, False)
g.canary(135, True)
check("a failure resets the count: success, failure, success still open", g.is_open)
g.canary(195, True)
check("two consecutive successes close it (INCIDENT END), and units may start again",
      not g.is_open and g.admits_new_unit() and g.events[-1] == ("INCIDENT END", 195), str(g.events))
check("a canary with no incident refuses", "only while an incident" in err(lambda: g.canary(300, True)))
gi = IN.IncidentGate()
for i, arm in enumerate(["a", "b", "a", "b", "a"]):
    gi.observe(arm, i, {"status": 503})
for i in range(4):
    gi.observe("a", 50 + i, {"status": 503})                  # units in flight keep failing while it is open
gi.canary(60, True)
gi.canary(61, True)
gi.observe("b", 62, {"status": 503})
check("I11: failures gathered while the incident was open are dropped at INCIDENT END - one new failure does not reopen "
      "it", not gi.is_open and gi.events == [("INCIDENT START", 4), ("INCIDENT END", 61)], str(gi.events))

print("\n- Q25-INC: arms take their stages one at a time (Q25(2)), so the scheduler's probe is the second arm -")
gp = IN.IncidentGate()
for i in range(5):
    gp.observe("mem0", 100 + i, {"status": 503})
check("(1) 5 failures of ONE arm within 60 s: a probe is due, and no incident is open",
      gp.probe_due(104) and not gp.is_open)
gp.probe(104, {"status": 503})
check("(2) the probe failed: INCIDENT START", gp.is_open and gp.events == [("INCIDENT START", 104)], str(gp.events))
go = IN.IncidentGate()
for i, arm in enumerate(["a", "b", "a", "b", "a"]):
    go.observe(arm, i, {"status": 503})
for i in range(5):
    go.observe("a", 70 + i, {"status": 503})                  # the window now holds 5 failures of one arm
check("... and no probe while an incident is open, whatever the window holds", go.is_open and not go.probe_due(74))
gq = IN.IncidentGate()
for i in range(5):
    gq.observe("mem0", 200 + i, {"status": 503})
gq.probe(204, {"status": 200, "complete": True})
check("(3) the probe succeeded: not open, and no second probe within 60 s", not gq.is_open and not gq.probe_due(230))
for i in range(5):
    gq.observe("mem0", 260 + i, {"status": 503})
check("... and due again 60 s after the last one", not gq.probe_due(263) and gq.probe_due(264))
gr = IN.IncidentGate()
for i, arm in enumerate(["a", "a", "a", "b"]):
    gr.observe(arm, 300 + i, {"status": 503})
check("(4) failures of two arms: no probe - the gate opens on its own at the fifth", not gr.probe_due(303))
gr.observe("a", 304, {"status": 503})
check("... and it does", gr.is_open and not gr.probe_due(305))
g4 = IN.IncidentGate()
for i in range(4):
    g4.observe("mem0", 400 + i, {"status": 503})
check("4 failures of one arm: no probe yet", not g4.probe_due(403))
g4.probe(403, {"status": 503})
check("4 of one arm and a failed probe open it (5 across 2 arms)", g4.is_open)
gz = IN.IncidentGate()
for i in range(5):
    gz.observe("mem0", i, {"status": 503})
check("failures older than 60 s do not make a probe due", gz.probe_due(4) and not gz.probe_due(100)
      and not IN.IncidentGate().probe_due(0))
check("the thresholds are rev1's", (IN.WINDOW_S, IN.MIN_FAILURES, IN.MIN_ARMS, IN.CANARY_EVERY_S, IN.CLOSE_AFTER)
      == (60, 5, 2, 60, 2))

print("\n- halts and the balance -")
check("M-HALT-402-continues: 401, 402 and 403 halt; 400, 429 and 500 do not",
      [IN.halt_kind(s) for s in (401, 402, 403, 400, 429, 500, None)] == ["401", "402", "403", None, None, None, None])
check("the balance must be at least 2x the projection", IN.balance_ok(20, 10) and not IN.balance_ok(19.99, 10))

print("\n- the repair plan -")
U = [f"u{i}" for i in range(20)]
ARMS = ["nevertwice", "mem0", "zep"]
p = IN.repair_plan("S4", units=U, arms=ARMS, scored=False, incident_units=["u1"], embed_failure_units=["u2"],
                   endogenous=[("mem0", "u3", "ceiling"), ("zep", "u1", "crash")])
exo = [r for r in p["reruns"] if r.kind == "exogenous"]
endo = [r for r in p["reruns"] if r.kind == "endogenous"]
check("M-REPAIR-exo-subset: an exogenous unit re-runs for EVERY arm", [r.arms for r in exo] == [tuple(ARMS)] * 2
      and [r.unit for r in exo] == ["u1", "u2"], str(exo))
check("M-REPAIR-endo-store-reuse: every re-run is in fresh stores", all(r.fresh_store for r in p["reruns"]))
check("an endogenous unit re-runs for its arm only; one already covered by an exogenous re-run is not doubled",
      [(r.arms, r.unit, r.reason) for r in endo] == [(("mem0",), "u3", "ceiling")], str(endo))
check("the exogenous share and the stand's validity (2/20 = 10 % is valid)", p["exogenous_share"] == 0.1 and not p["stand_invalid"])
p3 = IN.repair_plan("S4", units=U, arms=ARMS, scored=False, incident_units=["u1", "u2", "u5"])
check("above 10 % exogenous the stand is invalid", p3["stand_invalid"])
check("M-REPAIR-after-scoring: the plan is refused once the stand is scored",
      "before any scoring" in err(lambda: IN.repair_plan("S4", units=U, arms=ARMS, scored=True)))
check("M-REPAIR-lone-arm-whole-run: no lone-arm whole-run re-run",
      "whole-run" in err(lambda: IN.repair_plan("S4", units=U + ["*"], arms=ARMS, scored=False, endogenous=[("mem0", "*", "x")])))
check("a unit the stand does not have refuses", "not a unit" in err(lambda: IN.repair_plan("S4", units=U, arms=ARMS, scored=False,
                                                                                            incident_units=["u99"])))
check("an unknown arm refuses", "unknown arm" in err(lambda: IN.repair_plan("S4", units=U, arms=ARMS, scored=False,
                                                                           endogenous=[("letta", "u1", "x")])))
check("a unit failing again after its repair is dropped for every arm",
      IN.dropped_after_repair({"u1", "u2"}, {"u2"}, kind="exogenous") == {"u2"})

print(f"\nv3 incidents: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
