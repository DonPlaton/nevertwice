#!/usr/bin/env python3
"""PREREG-V3 TB4.1 (A6): the STATUS v3 writer.

.loop/STATUS-V3-CONTRACT.md fixes the line formats and the auditor's m2_v3 rules S1-S8; the A6 rulings add a UNIT-ABORT
line with aborted= on the arm-run's END (Q1), the arm order and its seed on BLOCK START (Q25), and run ids without dots
(Q3: the proxy's unit prefix /u/<run>.<unit> is split at the first dot). This module writes exactly those lines and
refuses, by construction and before a byte is written, every sequence the rules forbid:

* S2: an id STARTs once; END / ABORT / UNIT-ABORT only for a live START; every tag is scored, smoke or debug - a smoke
  or debug run is logged like any other;
* S3: a stand has at most one open block, a block ENDs only when none of its runs is live, a START needs its block
  open - so every END of block b precedes every START of block b+1; utc strictly increases from line to line, so the
  strict barrier holds in the file too. A clock that does not advance is refused, never nudged;
* S9/S10 (A6 Q1, Q25): a UNIT-ABORT names a unit of its block, reason=ceiling, inside its live arm-run, and the END lists
  exactly those units; a START's arm is in its block's seeded arm_order, and a block ENDs only when every arm of that
  order has STARTed in it;
* S6: an exogenous RERUN covers arms=all and names an incident this file opened; cause and kind come from fixed lists;
* S7: no scored START outside CAMPAIGN V3 START .. END (smoke and debug runs of the pilot may precede the campaign);
* S8: before each append the file must still be exactly the bytes this writer has seen - a byte changed elsewhere is
  refused - and every append holds launch.file_lock (A6 ruling B11), so a second process cannot interleave.

A writer opened on an existing file replays it through the same state machine (each line must re-render byte for byte)
and continues; self_check() reports what a replay refuses. m2_v3 stays the judge; this is the writer's own mirror.

Lines: `<local YYYY-MM-DD HH:MM:SS> <event> ... utc=<ISO 8601, microseconds, +00:00>`, UTF-8, LF, one space between
fields, no space inside an id or a value.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

HERE = Path(__file__).resolve().parent
TAGS = ("scored", "smoke", "debug")
CAUSES = ("exogenous", "endogenous")
INCIDENT_KINDS = ("5xx", "429", "timeout", "401", "402", "403", "model-event")
UNIT_ABORT_REASONS = ("ceiling",)          # §5.6: a unit that hits its wall-clock ceiling (A6 Q1)
LOCAL_FMT = "%Y-%m-%d %H:%M:%S"
_SEG = re.compile(r"[A-Za-z0-9._-]+\Z")
_RUN = re.compile(r"[A-Za-z0-9_-]+\Z")
_WORD = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_INT = re.compile(r"-?\d+\Z")
_WALL = re.compile(r"\d+\.\d{3}s\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_TS = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\Z")
#: The keys of each event, in the order they are written.
KEYS = {
    "CAMPAIGN-START": ("anchor", "prereg", "freeze"), "CAMPAIGN-END": (),
    "STAND-START": ("order", "model", "changelog"), "STAND-END": ("model", "changelog"),
    "BLOCK-START": ("units", "arm_order", "seed"), "BLOCK-END": (),
    "START": ("pid", "tag"), "END": ("rc", "wall", "units", "out", "aborted"), "ABORT": ("reason",),
    "UNIT-ABORT": ("reason",), "RERUN": ("arms", "cause", "incident"),
    "INCIDENT-START": ("arms", "kind"), "INCIDENT-END": ("arms", "kind"), "SET-ASIDE": (),
}
OPTIONAL = {("END", "aborted")}


class StatusRefused(RuntimeError):
    """A line the STATUS v3 rules forbid; nothing was written."""


@dataclass
class Ev:
    kind: str
    ident: str = ""
    kv: dict = field(default_factory=dict)
    dst: str = ""


def _file_lock():
    """launch.file_lock (A6 ruling B11), loaded once from this directory."""
    mod = sys.modules.get("v3_launch_for_status")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_launch_for_status", HERE / "launch.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_launch_for_status"] = mod
        spec.loader.exec_module(mod)
    return mod.file_lock


# ── the line form ───────────────────────────────────────────────────────────────────────────────────────────────

def _head(ev: Ev) -> str:
    k = ev.kind
    if k.startswith("CAMPAIGN-"):
        return f"CAMPAIGN V3 {k.split('-', 1)[1]}"
    if k.startswith(("STAND-", "BLOCK-", "INCIDENT-")):
        word, state = k.split("-", 1)
        return f"{word} {ev.ident} {state}"
    if k == "SET-ASIDE":
        return f"SET-ASIDE {ev.ident} -> {ev.dst}"
    return f"{k} {ev.ident}"


def render_body(ev: Ev) -> str:
    parts = [_head(ev)]
    for key in KEYS[ev.kind]:
        if key in ev.kv:
            parts.append(f"{key}={ev.kv[key]}")
    return " ".join(parts)


def render_line(ev: Ev, utc: dt.datetime, ts: str) -> bytes:
    return f"{ts} {render_body(ev)} utc={utc.isoformat(timespec='microseconds')}\n".encode("utf-8")


def parse_line(line: str) -> tuple[str, Ev, dt.datetime]:
    """(ts, event, utc) of one line in the writer's form; ValueError otherwise."""
    ts, rest = line[:19], line[20:]
    if not _TS.match(ts) or line[19:20] != " ":
        raise ValueError("no local timestamp")
    body, sep, u = rest.rpartition(" utc=")
    if not sep:
        raise ValueError("no utc=")
    utc = dt.datetime.fromisoformat(u)
    if utc.tzinfo is None or utc.utcoffset() != dt.timedelta(0):
        raise ValueError("utc= is not UTC")
    tok = body.split(" ")
    if tok[:3] in (["CAMPAIGN", "V3", "START"], ["CAMPAIGN", "V3", "END"]):
        ev, kvs = Ev(f"CAMPAIGN-{tok[2]}"), tok[3:]
    elif tok[0] in ("STAND", "BLOCK", "INCIDENT") and len(tok) >= 3 and tok[2] in ("START", "END"):
        ev, kvs = Ev(f"{tok[0]}-{tok[2]}", tok[1]), tok[3:]
    elif tok[0] == "SET-ASIDE" and len(tok) == 4 and tok[2] == "->":
        ev, kvs = Ev("SET-ASIDE", tok[1], dst=tok[3]), []
    elif tok[0] in ("START", "END", "ABORT", "UNIT-ABORT", "RERUN") and len(tok) >= 2:
        ev, kvs = Ev(tok[0], tok[1]), tok[2:]
    else:
        raise ValueError("matches no STATUS v3 line format")
    for kv in kvs:
        key, eq, val = kv.partition("=")
        if not eq or key not in KEYS[ev.kind] or key in ev.kv:
            raise ValueError(f"unexpected field {kv!r}")
        ev.kv[key] = val
    return ts, ev, utc


# ── the rules ───────────────────────────────────────────────────────────────────────────────────────────────────

def _id(ident: str, n: int, what: str) -> list[str]:
    parts = ident.split("/")
    if len(parts) != n or not all(_SEG.match(p) for p in parts):
        raise StatusRefused(f"{ident!r} is not an id of the form {what} ([A-Za-z0-9._-] segments, no space)")
    return parts


def _value(key: str, val: str) -> str:
    if not val or any(c.isspace() for c in val):
        raise StatusRefused(f"{key}={val!r}: a value holds no space and is not empty")
    return val


def _list(key: str, items: Sequence[str]) -> str:
    items = list(items)
    if not items or len(set(items)) != len(items) or not all(isinstance(x, str) and _SEG.match(x) for x in items):
        raise StatusRefused(f"{key}: a non-empty list of distinct ids without spaces or commas, got {items!r}")
    return ",".join(items)


class _State:
    """What the file has said so far; check() validates one event and returns the commit that records it."""

    def __init__(self) -> None:
        self.campaign: str | None = None          # None | "open" | "closed"
        self.stands: dict[str, str] = {}          # stand -> open | closed
        self.open_block: dict[str, str] = {}      # stand -> its open block id
        self.blocks: dict[str, list[str]] = {}    # stand/block -> units
        self.arm_order: dict[str, list[str]] = {}  # stand/block -> its seeded arm order
        self.started_arms: dict[str, set] = {}    # stand/block -> arms that STARTed in it
        self.live: dict[str, list[str]] = {}      # live arm-run id -> its unit aborts
        self.done: set[str] = set()
        self.incidents: dict[str, dict] = {}      # id -> {"open", "arms", "kind"}
        self.last_utc: dt.datetime | None = None

    def check(self, ev: Ev, utc: dt.datetime) -> Callable[[], None]:  # noqa: C901 - one branch per line form
        if self.last_utc is not None and utc <= self.last_utc:
            raise StatusRefused(f"utc {utc.isoformat()} does not follow the last line's {self.last_utc.isoformat()} "
                                f"- the clock must advance")
        k, kv = ev.kind, ev.kv
        missing = [x for x in KEYS[k] if x not in kv and (k, x) not in OPTIONAL]
        if missing:
            raise StatusRefused(f"{k} lacks {missing}")
        for key, val in kv.items():
            _value(key, val)
        commits: list[Callable[[], None]] = []

        if k == "CAMPAIGN-START":
            if self.campaign is not None:
                raise StatusRefused("CAMPAIGN V3 START was already written (S1: exactly one)")
            if not _HEX40.match(kv["anchor"]):
                raise StatusRefused(f"anchor={kv['anchor']!r} is not a 40-hex commit")
            if not (_HEX64.match(kv["prereg"]) and _HEX64.match(kv["freeze"])):
                raise StatusRefused("prereg= and freeze= are sha256 (64 hex)")
            commits.append(lambda: setattr(self, "campaign", "open"))
        elif k == "CAMPAIGN-END":
            if self.campaign != "open":
                raise StatusRefused("CAMPAIGN V3 END without an open campaign")
            if any(s == "open" for s in self.stands.values()):
                raise StatusRefused("CAMPAIGN V3 END while a stand is open")
            commits.append(lambda: setattr(self, "campaign", "closed"))
        elif k == "STAND-START":
            _id(ev.ident, 1, "<stand>")
            if ev.ident in self.stands:
                raise StatusRefused(f"stand {ev.ident} was already started in this file")
            if not (_INT.match(kv["order"]) and int(kv["order"]) >= 1):
                raise StatusRefused(f"order={kv['order']!r} is not a positive integer")
            commits.append(lambda: self.stands.__setitem__(ev.ident, "open"))
        elif k == "STAND-END":
            if self.stands.get(ev.ident) != "open":
                raise StatusRefused(f"stand {ev.ident} is not open")
            if ev.ident in self.open_block:
                raise StatusRefused(f"STAND END of {ev.ident} with an open block {self.open_block[ev.ident]}")
            commits.append(lambda: self.stands.__setitem__(ev.ident, "closed"))
        elif k == "BLOCK-START":
            stand, _b = _id(ev.ident, 2, "<stand>/<block>")
            if self.stands.get(stand) != "open":
                raise StatusRefused(f"stand {stand} is not open")
            if ev.ident in self.blocks:
                raise StatusRefused(f"block {ev.ident} was already started in this file")
            if stand in self.open_block:
                raise StatusRefused(f"block {self.open_block[stand]} of {stand} is still open (the S3 barrier)")
            units = kv["units"].split(",")
            _list("units", units)
            order = kv["arm_order"].split(",")
            _list("arm_order", order)
            if not (_INT.match(kv["seed"]) and int(kv["seed"]) >= 0):
                raise StatusRefused(f"seed={kv['seed']!r} is not a non-negative integer")

            def c_block() -> None:
                self.blocks[ev.ident] = units
                self.arm_order[ev.ident] = order
                self.started_arms[ev.ident] = set()
                self.open_block[stand] = ev.ident
            commits.append(c_block)
        elif k == "BLOCK-END":
            stand, _b = _id(ev.ident, 2, "<stand>/<block>")
            if self.open_block.get(stand) != ev.ident:
                raise StatusRefused(f"block {ev.ident} is not open")
            live = [i for i in self.live if i.startswith(ev.ident + "/")]
            if live:
                raise StatusRefused(f"BLOCK END of {ev.ident} while runs are live: {live} (the S3 barrier)")
            never = [a for a in self.arm_order[ev.ident] if a not in self.started_arms[ev.ident]]
            if never:
                raise StatusRefused(f"BLOCK END of {ev.ident}: arms {never} of its arm_order never started (S10)")
            commits.append(lambda: self.open_block.pop(stand))
        elif k == "START":
            stand, block, run, arm = _id(ev.ident, 4, "<stand>/<block>/<run>/<arm>")
            if not _RUN.match(run):
                raise StatusRefused(f"run id {run!r} holds a dot (Q3: the unit prefix is split at the first dot)")
            if ev.ident in self.live or ev.ident in self.done:
                raise StatusRefused(f"START id {ev.ident} was already used (S2)")
            if self.open_block.get(stand) != f"{stand}/{block}":
                raise StatusRefused(f"block {stand}/{block} is not open")
            if kv["tag"] not in TAGS:
                raise StatusRefused(f"tag={kv['tag']!r} is not one of {TAGS}")
            if not (_INT.match(kv["pid"]) and int(kv["pid"]) > 0):
                raise StatusRefused(f"pid={kv['pid']!r} is not a positive integer")
            if arm not in self.arm_order[f"{stand}/{block}"]:
                raise StatusRefused(f"arm {arm} is not in block {stand}/{block}'s arm_order (S10)")
            if kv["tag"] == "scored" and self.campaign != "open":
                raise StatusRefused("a scored START outside the campaign (CAMPAIGN V3 START .. END, S7)")

            def c_start() -> None:
                self.live[ev.ident] = []
                self.started_arms[f"{stand}/{block}"].add(arm)
            commits.append(c_start)
        elif k == "UNIT-ABORT":
            stand, block, _run, _arm, unit = _id(ev.ident, 5, "<stand>/<block>/<run>/<arm>/<unit>")
            arm_run = ev.ident.rsplit("/", 1)[0]
            if arm_run not in self.live:
                raise StatusRefused(f"UNIT-ABORT {ev.ident}: no live START {arm_run}")
            if unit not in self.blocks.get(f"{stand}/{block}", []):
                raise StatusRefused(f"unit {unit} is not in block {stand}/{block}")
            if unit in self.live[arm_run]:
                raise StatusRefused(f"unit {unit} of {arm_run} was already aborted")
            if kv["reason"] not in UNIT_ABORT_REASONS:
                raise StatusRefused(f"UNIT-ABORT reason={kv['reason']!r} is not one of {UNIT_ABORT_REASONS} (S9)")
            commits.append(lambda: self.live[arm_run].append(unit))
        elif k in ("END", "ABORT"):
            if ev.ident not in self.live:
                raise StatusRefused(f"{k} {ev.ident}: no live START with this id (S2)")
            if k == "END":
                if not _INT.match(kv["rc"]):
                    raise StatusRefused(f"rc={kv['rc']!r} is not an integer")
                if not _WALL.match(kv["wall"]):
                    raise StatusRefused(f"wall={kv['wall']!r} is not <seconds>.<3 digits>s")
                if not (_INT.match(kv["units"]) and int(kv["units"]) >= 0):
                    raise StatusRefused(f"units={kv['units']!r} is not a count")
                want = ",".join(self.live[ev.ident])
                if kv.get("aborted", "") != want:
                    raise StatusRefused(f"END {ev.ident} aborted={kv.get('aborted')!r} is not its UNIT-ABORT units "
                                        f"{want!r} (Q1)")
            elif not _WORD.match(kv["reason"]):
                raise StatusRefused(f"reason={kv['reason']!r} is not one word")

            def c_close() -> None:
                self.live.pop(ev.ident)
                self.done.add(ev.ident)
            commits.append(c_close)
        elif k == "RERUN":
            stand, block, _run, unit = _id(ev.ident, 4, "<stand>/<block>/<run>/<unit>")
            if unit not in self.blocks.get(f"{stand}/{block}", []):
                raise StatusRefused(f"RERUN {ev.ident}: unit {unit} is not in block {stand}/{block}")
            if kv["cause"] not in CAUSES:
                raise StatusRefused(f"cause={kv['cause']!r} is not one of {CAUSES}")
            if kv["arms"] != "all":
                _list("arms", kv["arms"].split(","))
            iid = kv["incident"]
            if iid != "-" and iid not in self.incidents:
                raise StatusRefused(f"RERUN names incident {iid}, which this file never opened")
            if kv["cause"] == "exogenous":
                if kv["arms"] != "all":
                    raise StatusRefused("an exogenous RERUN covers all arms (S6/PR7): arms=all")
                if iid == "-":
                    raise StatusRefused("an exogenous RERUN names its incident")
        elif k == "INCIDENT-START":
            _id(ev.ident, 1, "<incident>")
            if ev.ident in self.incidents:
                raise StatusRefused(f"incident {ev.ident} was already opened")
            if kv["kind"] not in INCIDENT_KINDS:
                raise StatusRefused(f"kind={kv['kind']!r} is not one of {INCIDENT_KINDS}")
            _list("arms", kv["arms"].split(","))
            commits.append(lambda: self.incidents.__setitem__(ev.ident, {"open": True, "arms": kv["arms"],
                                                                          "kind": kv["kind"]}))
        elif k == "INCIDENT-END":
            inc = self.incidents.get(ev.ident)
            if not inc or not inc["open"]:
                raise StatusRefused(f"incident {ev.ident} is not open")
            if (kv["arms"], kv["kind"]) != (inc["arms"], inc["kind"]):
                raise StatusRefused(f"INCIDENT END {ev.ident} names other arms or kind than its START")
            commits.append(lambda: inc.__setitem__("open", False))
        elif k == "SET-ASIDE":
            _value("src", ev.ident)
            _value("dst", ev.dst)
        else:
            raise StatusRefused(f"unknown event {k}")

        def commit() -> None:
            for c in commits:
                c()
            self.last_utc = utc
        return commit


def _replay(raw: bytes, state: _State) -> list[str]:
    """Feed a file through the state machine; the problems, first per line (a refused line is not applied)."""
    out: list[str] = []
    if not raw:
        return out
    if b"\r" in raw:
        out.append("the file holds a CR (LF only)")
    if not raw.endswith(b"\n"):
        out.append("the file does not end with a newline")
    for n, line in enumerate(raw.decode("utf-8", "replace").rstrip("\n").split("\n"), 1):
        try:
            ts, ev, utc = parse_line(line)
            if render_line(ev, utc, ts) != (line + "\n").encode("utf-8"):
                raise ValueError("does not re-render byte for byte")
            state.check(ev, utc)()
        except (ValueError, StatusRefused) as e:
            out.append(f"line {n}: {e}")
    return out


def self_check(path: Path) -> list[str]:
    """What a replay of the file refuses - the writer's mirror of m2_v3 S2/S3/S6/S7 (m2_v3 stays the judge)."""
    p = Path(path)
    return _replay(p.read_bytes() if p.exists() else b"", _State())


class StatusLog:
    """The one writer of a STATUS v3 file. Every public method writes one line or raises StatusRefused."""

    def __init__(self, path: Path, *, now: Callable[[], dt.datetime] | None = None,
                 local_tz: dt.tzinfo | None = None, clock_wait_s: float = 2.0) -> None:
        self.path = Path(path)
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._tz = local_tz
        self._wait = clock_wait_s
        self._mutex = threading.Lock()
        self._file_lock = _file_lock()
        self._state = _State()
        raw = self.path.read_bytes() if self.path.exists() else b""
        problems = _replay(raw, self._state)
        if problems:
            raise StatusRefused(f"replay of {self.path} failed, refusing to continue it: {problems[0]}")
        self._seen, self._size = hashlib.sha256(raw).hexdigest(), len(raw)

    def _tick(self) -> dt.datetime:
        deadline = time.monotonic() + self._wait
        while True:
            t = self._now().astimezone(dt.timezone.utc)
            if self._state.last_utc is None or t > self._state.last_utc:
                return t
            if time.monotonic() > deadline:
                raise StatusRefused("the clock did not advance past the last line's utc - refused, never nudged")
            time.sleep(0.001)

    def _emit(self, ev: Ev) -> None:
        with self._mutex, self._file_lock(self.path):
            raw = self.path.read_bytes() if self.path.exists() else b""
            if len(raw) != self._size or hashlib.sha256(raw).hexdigest() != self._seen:
                raise StatusRefused(f"{self.path} was changed outside this writer (S8: append-only) - refused")
            utc = self._tick()
            commit = self._state.check(ev, utc)
            local = utc.astimezone(self._tz) if self._tz is not None else utc.astimezone()
            line = render_line(ev, utc, local.strftime(LOCAL_FMT))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            commit()
            self._seen = hashlib.sha256(raw + line).hexdigest()
            self._size += len(line)

    # ── the events ─────────────────────────────────────────────────────────────────────────────────────────────
    def campaign_start(self, *, anchor: str, prereg: str, freeze: str) -> None:
        self._emit(Ev("CAMPAIGN-START", kv={"anchor": anchor, "prereg": prereg, "freeze": freeze}))

    def campaign_end(self) -> None:
        self._emit(Ev("CAMPAIGN-END"))

    def stand(self, stand: str, state: str, *, model: str, changelog: str, order: int | None = None) -> None:
        if state == "START":
            self._emit(Ev("STAND-START", stand, {"order": str(order), "model": model, "changelog": changelog}))
        elif state == "END":
            self._emit(Ev("STAND-END", stand, {"model": model, "changelog": changelog}))
        else:
            raise StatusRefused(f"STAND state {state!r} is START or END")

    def block_start(self, stand: str, block: str, *, units: Sequence[str], arm_order: Sequence[str], seed: int) -> None:
        self._emit(Ev("BLOCK-START", f"{stand}/{block}", {"units": _list("units", units),
                                                          "arm_order": _list("arm_order", arm_order),
                                                          "seed": str(seed)}))

    def block_end(self, stand: str, block: str) -> None:
        self._emit(Ev("BLOCK-END", f"{stand}/{block}"))

    def start(self, stand: str, block: str, run: str, arm: str, *, pid: int, tag: str) -> str:
        ident = f"{stand}/{block}/{run}/{arm}"
        self._emit(Ev("START", ident, {"pid": str(pid), "tag": tag}))
        return ident

    def unit_abort(self, ident: str, unit: str, *, reason: str = "ceiling") -> None:
        self._emit(Ev("UNIT-ABORT", f"{ident}/{unit}", {"reason": reason}))

    def end(self, ident: str, *, rc: int, wall_s: float, units: int, out: str) -> None:
        if wall_s < 0:
            raise StatusRefused(f"wall {wall_s} s is negative")
        kv = {"rc": str(int(rc)), "wall": f"{float(wall_s):.3f}s", "units": str(int(units)), "out": out}
        aborted = self._state.live.get(ident)
        if aborted:
            kv["aborted"] = ",".join(aborted)
        self._emit(Ev("END", ident, kv))

    def abort(self, ident: str, *, reason: str) -> None:
        self._emit(Ev("ABORT", ident, {"reason": reason}))

    def rerun(self, stand: str, block: str, run: str, unit: str, *, arms: str | Sequence[str], cause: str,
              incident: str) -> None:
        a = arms if arms == "all" else _list("arms", arms)
        self._emit(Ev("RERUN", f"{stand}/{block}/{run}/{unit}", {"arms": a, "cause": cause, "incident": incident}))

    def incident(self, iid: str, state: str, *, arms: Sequence[str], kind: str) -> None:
        if state not in ("START", "END"):
            raise StatusRefused(f"INCIDENT state {state!r} is START or END")
        self._emit(Ev(f"INCIDENT-{state}", iid, {"arms": _list("arms", arms), "kind": kind}))

    def set_aside(self, src: str, dst: str) -> None:
        self._emit(Ev("SET-ASIDE", src, dst=dst))
