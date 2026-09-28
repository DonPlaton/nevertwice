#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A3: research/v3/run_v3_gate.py - the incident gate's driver, on a temporary calls.jsonl with the
real IncidentGate and a real StatusLog (no process, no network; a scripted probe; virtual time):

* GT-two-arms: 5 upstream failures of 2 arms in the window open INCIDENT START with both arms; no new unit starts;
* GT-canary-close: two consecutive good canaries 60 s apart write INCIDENT END with the same arms and kind (STATUS
  refuses anything else), and units start again;
* GT-one-arm-probe-fail / -ok (Q25-INC): 5 failures of ONE arm send the scheduler's probe; a failed probe is the
  second arm and opens the incident; a good one does not, and no second probe goes out within 60 s;
* GT-halt-402: a 402 opens INCIDENT START kind=402 for its arm and halts - no new unit, even with the gate closed;
* GT-kind: the incident's kind is its window's most frequent failure class, a tie going to the earlier of 5xx, 429,
  timeout;
* GT-lf-only: records are split at LF only - a U+2028 inside a string does not split one; a line still being written
  waits for the next poll;
* GT-model-event (Q-12-8): an answer naming an unexpected model opens kind=model-event, the next poll ends it, and the
  same model does not open it again;
* GT-scheduler-once: the scheduler's own records in the log are not fed again (its probes are fed directly);
* GT-problem: a line that is not a JSON object is a named problem.

    python tests/research/_test_v3_run_gate.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load("v3_run_gate_t", ROOT / "research" / "v3" / "run_v3_gate.py")
I = G._incidents()
SL = _load("v3_status_log_for_gate_t", ROOT / "research" / "v3" / "status_log.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


T0 = 1_790_000_000.0


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def rec(arm: str, t: float, status=200, complete=True, model="deepseek-v4-flash", **kw) -> dict:
    return {"arm": arm, "t0": iso(t - 1), "t1": iso(t), "status": status, "complete": complete,
            "response_model": model, **kw}


class World:
    def __init__(self, name: str, probes=(), expected=("deepseek-v4-flash",), poll_s: float = 1.0) -> None:
        self.calls = TMP / f"{name}.calls.jsonl"
        self.status_path = TMP / f"{name}.STATUS"
        self.now = [T0]
        self.lines_written = [0]

        def status_now():                       # STATUS's own clock: 1 ms further on every line it stamps
            self.lines_written[0] += 1
            return dt.datetime.fromtimestamp(T0 + self.lines_written[0] / 1000, dt.timezone.utc)
        self.st = SL.StatusLog(self.status_path, now=status_now, local_tz=dt.timezone.utc)
        self.probes, self.sent, self.errors = list(probes), 0, []

        def send_probe():
            self.sent += 1
            nxt = self.probes.pop(0) if self.probes else {"status": 200, "complete": True}
            if nxt == "raise":
                self.probes.insert(0, "raise")          # it keeps failing
                raise RuntimeError("the probe's transport broke")
            return nxt
        self.recorded: list = []
        self.d = G.GateDriver(I.IncidentGate(), calls_path=self.calls, status=self.st, send_probe=send_probe,
                              id_prefix=f"inc-{name}", expected_models=expected, clock=lambda: self.now[0],
                              record=self.recorded.append, poll_s=poll_s)

    def poll(self) -> None:
        try:
            self.d.poll()
        except Exception as e:  # noqa: BLE001 - a refused STATUS line or a crash is this row's FAIL, by name
            self.errors.append(repr(e))

    def write(self, *recs, raw: bytes = b"") -> None:
        with open(self.calls, "ab") as f:
            for r in recs:
                f.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
            f.write(raw)

    def lines(self, kind: str) -> list[str]:
        if not self.status_path.exists():
            return []
        return [x for x in self.status_path.read_text(encoding="utf-8").splitlines() if f" INCIDENT " in x and kind in x]


def admits(d) -> object:
    """Q2: the driver's answer - True or False while it admits or waits, "halted:<kind>" when it raises GateHalted (a halt
    is no wait), any other exception by name (the row that reads it FAILs)."""
    try:
        return d.admits_new_unit()
    except G.GateHalted as e:
        return f"halted:{e.kind}"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def kv(line: str, key: str) -> str | None:
    for tok in line.split():
        if tok.startswith(key + "="):
            return tok.split("=", 1)[1]
    return None


TMP = Path(tempfile.mkdtemp(prefix="nvt3_gate_"))
try:
    print("- incidents from the log -")
    w = World("two")
    w.write(rec("a3", T0 - 100, status=500),              # a third arm's failure, outside the 60 s window
            *[rec("a1", T0 + i, status=503) for i in range(3)], *[rec("a2", T0 + 3 + i, status=502) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()
    starts = w.lines(" START ")
    check("GT-two-arms: 5 upstream failures of 2 arms open INCIDENT START naming both arms (not an arm whose failure "
          "left the window), kind 5xx", len(starts) == 1 and kv(starts[0], "arms") == "a1,a2"
          and kv(starts[0], "kind") == "5xx" and not w.errors, f"{starts} {w.errors}")
    check("... and no new unit starts while it is open", w.d.admits_new_unit() is False)
    two_win = w.d.incidents[0].get("window") if w.d.incidents else None
    check("GT-two-arms: the record's window counts only the gate's 60 s - the third arm's old failure is not in it",
          two_win == {"by_kind": {"5xx": 5}, "by_arm": {"a1": 3, "a2": 2}}, str(two_win))
    w.now[0] = T0 + 10
    w.poll()
    w.now[0] = T0 + 70
    w.poll()
    ends = w.lines(" END ")
    check("GT-canary-close: two good canaries 60 s apart write INCIDENT END with the same arms and kind",
          len(ends) == 1 and kv(ends[0], "arms") == "a1,a2" and kv(ends[0], "kind") == "5xx" and w.sent == 2
          and not w.errors, f"{ends} sent={w.sent} {w.errors}")
    check("... units start again, and STATUS passes the writer's own replay", w.d.admits_new_unit() is True
          and SL.self_check(w.status_path) == [], str(SL.self_check(w.status_path)))

    w = World("badcanary", probes=[{"status": 503, "complete": True}])
    w.write(*[rec("a1", T0 + i, status=503) for i in range(3)], *[rec("a2", T0 + 3 + i, status=502) for i in range(2)])
    for step in (10, 70):
        w.now[0] = T0 + step
        w.poll()
    mid = w.lines(" END ")
    w.now[0] = T0 + 130
    w.poll()
    check("GT-canary-close: a failed canary resets the count - two CONSECUTIVE good ones are needed",
          mid == [] and len(w.lines(" END ")) == 1 and w.sent == 3 and not w.errors, f"{mid} sent={w.sent} {w.errors}")

    w = World("probefail", probes=[{"status": 503, "complete": True}])
    w.write(*[rec("a1", T0 + i, status=500) for i in range(5)])
    w.now[0] = T0 + 6
    w.poll()
    starts = w.lines(" START ")
    check("GT-one-arm-probe-fail: 5 failures of one arm send the probe; its failure is the second arm and opens the "
          "incident (Q25-INC) - whose first canary is due at once", w.sent == 2 and len(starts) == 1
          and kv(starts[0], "arms") == "a1,scheduler" and w.d.gate.successes == 1, f"sent={w.sent} {starts}")
    pf_win = w.d.incidents[0].get("window") if w.d.incidents else None
    check("GT-one-arm-probe-fail: the failed probe is in the record's window as the scheduler's",
          pf_win == {"by_kind": {"5xx": 6}, "by_arm": {"a1": 5, "scheduler": 1}}, str(pf_win))
    w = World("probeok")
    w.write(*[rec("a1", T0 + i, status=500) for i in range(5)])
    w.now[0] = T0 + 6
    w.poll()
    w.now[0] = T0 + 20
    w.poll()
    check("GT-one-arm-probe-ok: a good probe opens nothing, and no second probe goes out within 60 s",
          w.sent == 1 and not w.lines(" START ") and w.d.admits_new_unit(), f"sent={w.sent} {w.lines(' START ')}")

    w = World("halt")
    w.write(rec("a2", T0, status=402))
    w.poll()
    starts = w.lines(" START ")
    check("GT-halt-402: a 402 opens INCIDENT START kind=402 for its arm and halts - no new unit, the gate itself closed",
          len(starts) == 1 and kv(starts[0], "kind") == "402" and kv(starts[0], "arms") == "a2"
          and w.d.halted == "402" and admits(w.d) == "halted:402" and w.d.gate.admits_new_unit() is True, str(starts))
    try:
        w.d.admits_new_unit()
        q2 = "no raise"
    except Exception as e:  # noqa: BLE001 - read below
        q2 = e
    check("Q2 (F15): a halted driver's admits_new_unit RAISES GateHalted naming its kind - a halt is no wait: returning "
          "False spun the scheduler's _await_gate for good; the raise takes the B-OPEN path",
          isinstance(q2, G.GateHalted) and q2.kind == q2.halt == "402" and "402" in str(q2), repr(q2))

    w = World("mix")
    w.write(*[rec("a1", T0 + i, status=500) for i in range(3)], *[rec("a2", T0 + 3 + i, status=429) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()
    inc = w.d.incidents[0] if w.d.incidents else {}
    check("Q-12-9: 3 x 5xx + 2 x 429 - kind=5xx, and the harness's record keeps the whole mix by class and by arm",
          [kv(x, "kind") for x in w.lines(" START ")] == ["5xx"]
          and inc.get("window") == {"by_kind": {"5xx": 3, "429": 2}, "by_arm": {"a1": 3, "a2": 2}}
          and w.recorded and w.recorded[0].get("window") == inc.get("window"), f"{inc} {w.recorded}")

    w = World("reopen")
    w.write(*[rec("a1", T0 + i, status=503) for i in range(3)], *[rec("a2", T0 + 3 + i, status=502) for i in range(2)])
    w.now[0] = T0 + 5
    w.poll()                                               # START, first canary good
    w.write(*[rec("a1", T0 + 50 + i, status=500) for i in range(3)])
    w.now[0] = T0 + 51
    w.poll()                                               # failures while it is open
    w.now[0] = T0 + 65
    w.poll()                                               # second canary: END
    w.write(*[rec("a2", T0 + 66 + i, status=429) for i in range(3)], *[rec("a3", T0 + 69 + i, status=429) for i in range(2)])
    w.now[0] = T0 + 71
    w.poll()                                               # a second incident
    second = w.d.incidents[1].get("window") if len(w.d.incidents) > 1 else None
    check("GT-reopen: an incident's END drops the failures seen while it was open - the next incident's record counts "
          "only its own", second == {"by_kind": {"429": 5}, "by_arm": {"a2": 3, "a3": 2}} and not w.errors,
          f"{second} {w.errors}")

    w = World("t1")
    #: each call ended within seconds of the others (t1 = T0..T0+4) but they STARTED 100 s apart (t0 = T0-400..T0):
    #: timed at their start, no 60 s window would hold five of them
    w.write(*[dict(rec("a1" if i < 3 else "a2", T0 + i, status=500), t0=iso(T0 - 400 + 100 * i)) for i in range(5)])
    w.now[0] = T0 + 6
    w.poll()
    check("GT-time: a failure is timed at its end (t1) - five calls that ended within seconds open the incident, "
          "however long each took", len(w.lines(" START ")) == 1, str(w.lines(" START ")))

    w = World("kind429")
    w.write(*[rec("a1", T0 + i, status=429) for i in range(3)], *[rec("a2", T0 + 3 + i, status=500) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()
    k1 = [kv(x, "kind") for x in w.lines(" START ")]
    w = World("kindtie")
    w.write(rec("a1", T0, status=429), rec("a1", T0 + 1, status=429), rec("a2", T0 + 2, status=500),
            rec("a2", T0 + 3, status=500), rec("a2", T0 + 4, status=200, complete=False))
    w.now[0] = T0 + 10
    w.poll()
    k2 = [kv(x, "kind") for x in w.lines(" START ")]
    check("GT-kind: the window's most frequent failure class (429 here); a tie goes to the earlier of 5xx, 429, timeout",
          k1 == ["429"] and k2 == ["5xx"], f"{k1} {k2}")

    print("\n- reading the log -")
    w = World("lf")
    w.write(rec("a1", T0, status=500, note="a b"), raw=json.dumps(rec("a1", T0 + 1, status=500)).encode()[:40])
    w.poll()
    n1 = len(w.d.gate.failures)
    w.write(raw=json.dumps(rec("a1", T0 + 1, status=500)).encode()[40:] + b"\n")
    w.poll()
    n2 = len(w.d.gate.failures)
    check("GT-lf-only: a U+2028 inside a string does not split its record, and a line still being written waits",
          n1 == 1 and n2 == 2 and w.d.problems == [], f"{n1} {n2} {w.d.problems}")
    w = World("model")
    w.write(rec("a1", T0, model="deepseek-v4-pro"))
    w.poll()
    s1, e1 = w.lines(" START "), w.lines(" END ")
    w.write(rec("a1", T0 + 1, model="deepseek-v4-pro"))
    w.poll()
    s2, e2 = w.lines(" START "), w.lines(" END ")
    check("GT-model-event: an unexpected model opens kind=model-event for its arm, the next poll ends it, and the same "
          "model does not open it again", len(s1) == 1 and kv(s1[0], "kind") == "model-event" and e1 == []
          and len(s2) == 1 and len(e2) == 1 and kv(e2[0], "kind") == "model-event"
          and SL.self_check(w.status_path) == [], f"{s1} {e1} {s2} {e2}")
    w = World("sched")
    w.write(*[rec("scheduler", T0 + i, status=500) for i in range(5)])
    w.now[0] = T0 + 6
    w.poll()
    check("GT-scheduler-once: the scheduler's own records are not fed again - no failure counted, no probe sent",
          len(w.d.gate.failures) == 0 and w.sent == 0, f"{len(w.d.gate.failures)} sent={w.sent}")
    w = World("junk")
    w.write(raw=b"not json\n[1, 2]\n")
    w.write(rec("a1", T0, status=500))
    w.poll()
    check("GT-problem: a line that is not JSON, and one that is not an object, are named problems; the next record "
          "is still read", len(w.d.problems) == 2 and len(w.d.gate.failures) == 1, str(w.d.problems))
    check("R-GATE-P: a record the gate cannot read leaves it blind - halted harness-error, no new unit",
          w.d.halted == "harness-error" and admits(w.d) == "halted:harness-error", str(w.d.halted))
    w = World("notime")
    w.write({"arm": "a1", "status": 500, "complete": True})
    w.poll()
    check("R-GATE-P: a call without a readable time is named and halts harness-error",
          any("no readable time" in p for p in w.d.problems) and w.d.halted == "harness-error", str(w.d.problems))
    w = World("noarm")
    w.write({"status": 500, "t1": iso(T0)}, {"arm": "", "status": 500, "t1": iso(T0 + 1)})
    w.poll()
    check("GTc: a call without an arm (or with an empty one) is named and halts harness-error - the gate cannot see it",
          sum("without an arm" in p_ for p_ in w.d.problems) == 2 and w.d.halted == "harness-error", str(w.d.problems))
    w = World("blind-then-402")
    w.write(raw=b"not json\n")
    w.poll()
    blind = w.d.halted
    w.write(rec("a2", T0 + 1, status=402))
    w.poll()
    check("GTe: a 402 after the gate went blind still opens INCIDENT START kind=402 and halts 402 - waiting for the "
          "owner is never lost behind harness-error", blind == "harness-error" and w.d.halted == "402"
          and [kv(x, "kind") for x in w.lines(" START ")] == ["402"], f"{blind} {w.d.halted} {w.lines(' START ')}")
    w = World("halt-notime")
    w.write({"arm": "a2", "status": 402, "t1": "yesterday"})
    w.poll()
    check("R-GATE-T: a 402 whose time does not read still halts - INCIDENT START kind=402",
          w.d.halted == "402" and [kv(x, "kind") for x in w.lines(" START ")] == ["402"], f"{w.d.halted} {w.errors}")
    try:
        G.GateDriver(I.IncidentGate(), calls_path=TMP / "x.jsonl", status=None, send_probe=lambda: {},
                     id_prefix="inc-x", expected_models=())
        r_empty = "accepted"
    except G.GateError as e:
        r_empty = str(e)
    check("R-GATE-M: a driver with no expected model is refused - model-event would never fire", "R-GATE-M" in r_empty,
          r_empty)
    w = World("crash", probes=["raise"], poll_s=0.02)
    w.write(*[rec("a1", T0 + i, status=500) for i in range(5)])
    w.now[0] = T0 + 6
    w.d.start()
    time.sleep(0.5)
    in_loop = (w.d.halted, admits(w.d))                     # before stop(): the LOOP's own handling
    try:
        w.d.stop()
        stop_raised = None
    except Exception as e:  # noqa: BLE001 - a raise past stop() is this row's FAIL, by name
        stop_raised = repr(e)
    check("GT-crash: a poll that crashes in the loop (the probe's transport) is named and halts harness-error at once - "
          "no new unit while the stand runs", in_loop == ("harness-error", "halted:harness-error")
          and any("poll failed" in p for p in w.d.problems), f"{in_loop} {w.d.problems}")
    check("GT-crash: ... and stop()'s last poll crashing too is named, never raised past it",
          stop_raised is None and w.d.halted == "harness-error" and any("last poll failed" in p for p in w.d.problems),
          f"{stop_raised} {w.d.problems}")
    w = World("stop")
    w.write(rec("a1", T0, status=500))
    w.d.stop()
    check("stop() reads the last whole lines", len(w.d.gate.failures) == 1, str(len(w.d.gate.failures)))

    print("\n- B-HALT-SCHED, B-CANARY-T, a one-pass expected_models -")
    w = World("canary402", probes=[{"status": 402, "complete": False}])
    w.write(*[rec("a1", T0 + i, status=503) for i in range(3)], *[rec("a2", T0 + 3 + i, status=502) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()                                               # the incident opens; its first canary is answered 402
    kinds = [kv(x, "kind") for x in w.lines(" START ")]
    check("B-HALT-SCHED: a 402 answered to the incident's canary halts (INCIDENT START kind=402) - while the incident "
          "holds every unit the canary is the only call, and as a failed canary the 402 kept the incident open for good",
          w.d.halted == "402" and kinds == ["5xx", "402"] and admits(w.d) == "halted:402" and not w.errors,
          f"{w.d.halted} {kinds} {w.errors}")
    w = World("sched402")
    w.write(rec("scheduler", T0, status=402))
    w.poll()
    check("B-HALT-SCHED: a 402 in the scheduler port's own record (its model probe, a probe) halts too - the halt is "
          "read before the scheduler's records are skipped, and none of them is fed as a failure",
          w.d.halted == "402" and [kv(x, "kind") for x in w.lines(" START ")] == ["402"]
          and len(w.d.gate.failures) == 0, f"{w.d.halted} {w.lines(' START ')}")
    w = World("canaryt")
    w.write(*[rec("a1", T0 + i, status=500) for i in range(5)])
    sent_at: list = []

    def slow_probe():                                      # the probe and each canary take 50 s of the gate's clock
        sent_at.append(w.now[0])
        w.now[0] += 50
        return {"status": 503, "complete": True} if len(sent_at) == 1 else {"status": 200, "complete": True}
    w.d.send_probe = slow_probe
    w.now[0] = T0 + 6
    w.poll()                                               # the probe fails (the incident opens), then canary #1
    w.now[0] = T0 + 6 + 100 + 1                            # 51 s after canary #1 went out (at T0+56 + 50 s of its own)
    w.poll()
    check("B-CANARY-T: a canary is timed when it goes out, never at the poll's start - the one after a 50 s probe is "
          "not taken for one sent 50 s earlier, so the incident does not close two canaries 10 s apart",
          len(sent_at) == 2 and w.d.gate.last_canary == sent_at[1] and not w.lines(" END "),
          f"sent at {sent_at} last_canary {w.d.gate.last_canary} ends {w.lines(' END ')}")
    gen = G.GateDriver(I.IncidentGate(), calls_path=TMP / "gen.calls.jsonl", status=SL.StatusLog(TMP / "gen.STATUS",
                       local_tz=dt.timezone.utc), send_probe=lambda: {}, id_prefix="inc-gen",
                       expected_models=(m for m in ["deepseek-v4-flash"]), clock=lambda: T0)
    with open(TMP / "gen.calls.jsonl", "ab") as f_:
        f_.write((json.dumps(rec("a1", T0, model="deepseek-v4-pro")) + "\n").encode("utf-8"))
    gen.poll()
    gen_lines = (TMP / "gen.STATUS").read_text(encoding="utf-8") if (TMP / "gen.STATUS").exists() else ""
    check("R-GATE-M: expected models given as a one-pass iterable are read once - model-event still fires",
          "deepseek-v4-flash" in gen.models and "kind=model-event" in gen_lines, f"{gen.models} {gen_lines[-120:]}")

    class FlakyStatus:
        """STATUS whose first incident line fails (a lock that timed out) - the next one goes through."""

        def __init__(self, inner):
            self.inner, self.n = inner, 0

        def incident(self, *a, **k):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("the STATUS lock timed out")
            return self.inner.incident(*a, **k)

    w = World("batch")
    w.d.status = FlakyStatus(w.st)
    w.write(rec("a1", T0, status=402), rec("a2", T0 + 1, status=500))
    w.poll()                                               # the 402's START fails half-way through the batch
    w.poll()
    check("B-GATE-BATCH: a poll that fails half-way through its batch leaves the rest for the next poll - the 402 still "
          "halts, and the 500 after it is still fed", w.d.halted == "402" and len(w.d.gate.failures) == 1
          and [kv(x, "kind") for x in w.lines(" START ")] == ["402"], f"{w.d.halted} {len(w.d.gate.failures)} {w.errors}")
    w = World("recfail")

    def _full(_rec):
        raise OSError("the incidents file cannot be written")
    w.d.record = _full
    w.write(*[rec("a1", T0 + i, status=503) for i in range(3)], *[rec("a2", T0 + 3 + i, status=502) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()
    w.now[0] = T0 + 75
    w.poll()
    check("B-GATE-REC: an incident record that cannot be written is named (harness-error) - never a second INCIDENT "
          "START for the same event, and the incident still ENDs", len(w.lines(" START ")) == 1
          and len(w.lines(" END ")) == 1 and w.d.halted == "harness-error"
          and any("could not be written" in p for p in w.d.problems), f"{w.lines(' START ')} {w.d.problems}")

    print("\n- Q2: every halt source after STAND START - its INCIDENT START (a provider's stop) and GateHalted by kind -")
    seen_q2 = {}
    for arm_ in ("a1", "scheduler"):
        for st_ in (401, 402, 403):
            w = World(f"q2{arm_}{st_}")
            w.write(rec(arm_, T0, status=st_))
            w.poll()
            seen_q2[(arm_, str(st_))] = ([kv(x, "kind") for x in w.lines(" START ")], [x for x in w.lines(" END ")],
                                         admits(w.d))
    w = World("q2probe", probes=[{"status": 402, "complete": False}])
    w.write(*[rec("a1", T0 + i, status=500) for i in range(5)])
    w.now[0] = T0 + 6
    w.poll()                                               # one arm's 5 failures: the probe is due - and answered 402
    seen_q2[("probe", "402")] = ([kv(x, "kind") for x in w.lines(" START ")], w.lines(" END "), admits(w.d))
    w = World("q2blind")
    with open(w.calls, "ab") as f_:
        f_.write(b"not json\n")
    w.poll()
    seen_q2[("blind", "harness-error")] = ([kv(x, "kind") for x in w.lines(" START ")], w.lines(" END "), admits(w.d))
    want_q2 = {**{(a_, k_): ([k_], [], f"halted:{k_}") for a_ in ("a1", "scheduler") for k_ in ("401", "402", "403")},
               ("probe", "402"): (["402"], [], "halted:402"), ("blind", "harness-error"): ([], [], "halted:harness-error")}
    check("Q2: every halt source - a 401, 402 or 403 in an arm's record or the scheduler port's, a 402 answered to the "
          "probe - opens INCIDENT START kind=<k> with no END and raises GateHalted(<k>); a gate gone blind raises "
          "GateHalted(harness-error) with no INCIDENT line (STAND END halt= names it)", seen_q2 == want_q2,
          str({k_: v_ for k_, v_ in seen_q2.items() if want_q2.get(k_) != v_}))
    w = World("q2hk")
    hk0 = w.d.halt_kind()
    w.write(rec("a1", T0, status=403))
    w.poll()
    check("Q2: halt_kind() reads the halt without raising - None before it, 403 after (the scheduler's normal STAND END "
          "asks it when no unit is left to ask admits_new_unit)", hk0 is None and w.d.halt_kind() == "403",
          f"{hk0} {w.d.halt_kind()}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run gate: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
