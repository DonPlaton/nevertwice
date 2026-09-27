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
    def __init__(self, name: str, probes=(), expected=("deepseek-v4-flash",)) -> None:
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
            return self.probes.pop(0) if self.probes else {"status": 200, "complete": True}
        self.recorded: list = []
        self.d = G.GateDriver(I.IncidentGate(), calls_path=self.calls, status=self.st, send_probe=send_probe,
                              id_prefix=f"inc-{name}", expected_models=expected, clock=lambda: self.now[0],
                              record=self.recorded.append)

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
          and w.d.halted == "402" and w.d.admits_new_unit() is False and w.d.gate.admits_new_unit() is True, str(starts))

    w = World("mix")
    w.write(*[rec("a1", T0 + i, status=500) for i in range(3)], *[rec("a2", T0 + 3 + i, status=429) for i in range(2)])
    w.now[0] = T0 + 10
    w.poll()
    inc = w.d.incidents[0] if w.d.incidents else {}
    check("Q-12-9: 3 x 5xx + 2 x 429 - kind=5xx, and the harness's record keeps the whole mix by class and by arm",
          [kv(x, "kind") for x in w.lines(" START ")] == ["5xx"]
          and inc.get("window") == {"by_kind": {"5xx": 3, "429": 2}, "by_arm": {"a1": 3, "a2": 2}}
          and w.recorded and w.recorded[0].get("window") == inc.get("window"), f"{inc} {w.recorded}")

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
    w = World("stop")
    w.write(rec("a1", T0, status=500))
    w.d.stop()
    check("stop() reads the last whole lines", len(w.d.gate.failures) == 1, str(len(w.d.gate.failures)))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run gate: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
