#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A3: the incident gate's driver - it feeds incidents.IncidentGate from the proxy's calls.jsonl,
sends the probe and the canary, writes the INCIDENT lines, and halts (rev1 §4.5; the auditor's Q24, Q25-INC, Q-12-8).

* reading: calls.jsonl by byte offset, whole LF-terminated lines only - a line still being written waits for the next
  poll, and a U+2028 inside a JSON string never splits a record (bytes are split at b"\\n", nothing else). A line that
  is not a JSON object is a named problem, never skipped silently.
* feeding: every record of an arm is observed at its t1 (t0 when it has none); the scheduler's own records are not -
  the driver feeds its probe and canary outcomes itself (IncidentGate.probe/canary), so reading them back would count
  them twice.
* halts: a 401, 402 or 403 from upstream opens INCIDENT START kind=<status> for its arm and halts - no new unit starts
  again in this process (a 402 waits for the owner, §4.5); a halt has no END.
* an incident (Q-12-9 O-a): when the gate opens one, INCIDENT START names the arms in its window and a kind - the most
  frequent failure class there (5xx, 429, timeout for no status or an incomplete answer), a tie going to the earlier
  class in status_log.INCIDENT_KINDS; its END repeats them (STATUS refuses anything else). The mix never hides: the
  harness's incident record beside the STATUS line holds the window's full count by class and by arm.
* records: every incident START and END is also handed to ``record`` (run_v3 appends them, chained, to
  <runs>/_launch/incidents.jsonl) and kept in ``incidents``.
* the probe (Q25-INC) and the canary: the injected send_probe() - the scheduler's 1-token call on its own port - whose
  outcome goes to the gate; a canary succeeds when the probe is no upstream failure.
* model-event (Q-12-8): the first answer naming a model outside the expected set opens INCIDENT START
  kind=model-event for its arm, and the next poll ends it - a point event; m2 S6 asks for the re-runs after it.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

HERE = Path(__file__).resolve().parent
KIND_ORDER = ("5xx", "429", "timeout")          # status_log.INCIDENT_KINDS' upstream classes, in its order


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _incidents():
    return _load("v3_incidents_for_gate", HERE / "incidents.py")


def failure_kind(call: Mapping[str, Any]) -> str:
    """The INCIDENT kind of one upstream failure (Q24): 5xx, 429, or timeout (no status, or an incomplete answer)."""
    st = call.get("status")
    if isinstance(st, int) and not isinstance(st, bool):
        if st == 429:
            return "429"
        if st >= 500:
            return "5xx"
    return "timeout"


def _epoch(rec: Mapping[str, Any]) -> float | None:
    for k in ("t1", "t0"):
        v = rec.get(k)
        if isinstance(v, str) and v:
            try:
                return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


class GateDriver:
    """See the module docstring. ``status``: the StatusLog; ``send_probe``: () -> the probe's call record (at least
    status and complete); ``expected_models``: the models the stand's probe named (Q-12-8's baseline)."""

    def __init__(self, gate: Any, *, calls_path: str | Path, status: Any, send_probe: Callable[[], Mapping[str, Any]],
                 id_prefix: str, expected_models: Iterable[str] = (), clock: Callable[[], float] = time.time,
                 poll_s: float = 1.0, record: Callable[[dict], None] | None = None) -> None:
        self.gate, self.calls_path, self.status, self.record = gate, Path(calls_path), status, record
        self.send_probe, self.id_prefix, self.clock, self.poll_s = send_probe, id_prefix, clock, poll_s
        self.models = set(expected_models)
        self.offset = 0
        self.halted: str | None = None
        self.problems: list[str] = []
        self.incidents: list[dict] = []
        self._n = 0
        self._fails: list[tuple[float, str, str]] = []     # (t, arm, kind) of the upstream failures fed to the gate
        self._open: dict | None = None
        self._model_event: dict | None = None
        self._seen_events = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── the gate the scheduler reads ───────────────────────────────────────────────────────────────────────────
    def admits_new_unit(self) -> bool:
        return self.halted is None and self.gate.admits_new_unit()

    # ── one poll ───────────────────────────────────────────────────────────────────────────────────────────────
    def _next_id(self) -> str:
        self._n += 1
        return f"{self.id_prefix}-{self._n}"

    def _start(self, arms: Iterable[str], kind: str, window: Mapping[str, Any] | None = None) -> dict:
        inc = {"id": self._next_id(), "arms": sorted(set(arms)), "kind": kind}
        self.status.incident(inc["id"], "START", arms=inc["arms"], kind=kind)
        self.incidents.append(dict(inc, state="open", window=window))
        if self.record is not None:
            self.record({"event": "INCIDENT START", **inc, "window": window, "t": self.clock()})
        return inc

    def _end(self, inc: Mapping[str, Any]) -> None:
        self.status.incident(inc["id"], "END", arms=inc["arms"], kind=inc["kind"])
        for x in self.incidents:
            if x["id"] == inc["id"]:
                x["state"] = "closed"
        if self.record is not None:
            self.record({"event": "INCIDENT END", "id": inc["id"], "arms": inc["arms"], "kind": inc["kind"],
                         "t": self.clock()})

    def _read(self) -> list[dict]:
        try:
            with open(self.calls_path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []                                    # no whole line yet
        self.offset += end + 1
        out = []
        for raw in data[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self.problems.append(f"calls.jsonl: a line at or before byte {self.offset} is not JSON")
                continue
            if not isinstance(rec, dict):
                self.problems.append(f"calls.jsonl: a line at or before byte {self.offset} is not an object")
                continue
            out.append(rec)
        return out

    def poll(self) -> None:
        I = _incidents()
        with self._lock:
            if self._model_event is not None:            # Q-12-8: a point event ends at the next poll
                self._end(self._model_event)
                self._model_event = None
            for rec in self._read():
                arm = rec.get("arm")
                if not isinstance(arm, str) or arm == I.PROBE_ARM:
                    continue
                t = _epoch(rec)
                if t is None:
                    self.problems.append(f"a call of {arm} has no readable time")
                    continue
                halt = I.halt_kind(rec.get("status"))
                if halt is not None and self.halted is None:
                    self._start([arm], halt)
                    self.halted = halt
                model = rec.get("response_model")
                if isinstance(model, str) and model and self.models and model not in self.models \
                        and self._model_event is None:
                    self._model_event = self._start([arm], "model-event")
                    self.models.add(model)
                if I.is_upstream_failure(rec):
                    self._fails.append((t, arm, failure_kind(rec)))
                self.gate.observe(arm, t, rec)
                self._events()
            now = self.clock()
            if self.gate.probe_due(now):
                call = dict(self.send_probe())
                if I.is_upstream_failure(call):
                    self._fails.append((now, I.PROBE_ARM, failure_kind(call)))
                self.gate.probe(now, call)
                self._events()
            if self.gate.canary_due(now):
                call = dict(self.send_probe())
                self.gate.canary(now, not I.is_upstream_failure(call))
                self._events()

    def _events(self) -> None:
        I = _incidents()
        for name, t in self.gate.events[self._seen_events:]:
            if name == "INCIDENT START":
                arms = sorted({a for _t, a in self.gate.failures})       # the gate's own window: its arms
                win = [(a, k) for tt, a, k in self._fails if tt >= t - I.WINDOW_S]
                kinds = [k for _a, k in win]
                kind = max(KIND_ORDER, key=lambda k: (kinds.count(k), -KIND_ORDER.index(k)))
                window = {"by_kind": {k: kinds.count(k) for k in KIND_ORDER if kinds.count(k)},
                          "by_arm": {a: sum(1 for x, _k in win if x == a) for a in sorted({x for x, _k in win})}}
                self._open = self._start(arms, kind, window)
            elif name == "INCIDENT END" and self._open is not None:
                self._end(self._open)
                self._open = None
                self._fails.clear()                      # the gate dropped the failures seen while it was open
        self._seen_events = len(self.gate.events)

    # ── the background loop ────────────────────────────────────────────────────────────────────────────────────
    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.poll_s):
                try:
                    self.poll()
                except Exception as e:  # noqa: BLE001 - named, and the gate then refuses new units
                    self.problems.append(f"the gate's poll failed: {type(e).__name__}: {e}")
                    self.halted = self.halted or "harness-error"
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5 * self.poll_s + 5)
        self.poll()                                      # the last whole lines, and a pending point event's END
