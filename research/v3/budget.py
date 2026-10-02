#!/usr/bin/env python3
"""Stage D DeepSeek reservations, settlements and independent balance witness.

The owner's $20 ceiling uses a $19 admission threshold. Every request reserves its
UTF-8 input bytes at peak cache-miss rates and its output token bound at peak rates
before forwarding. Open reservations survive process death. All readers/writers
share launch.file_lock. A corrupt ledger refuses admission and authoritative sums.

The JSONL chain also has an atomically replaced tail checkpoint (.sha256). A crash
between appending and checkpointing fails closed. This detects ledger-only edits;
it is not authentication against an actor able to rewrite both files. A broken
ledger may receive forensic settlements, but never becomes authoritative again.

Completed streams without usage use the lesser of two conservative bounds: byte
pricing and the persisted reservation. The raw byte estimate remains in the line.
Reported usage outside the request's bounds stops further requests for review.
"""
from __future__ import annotations

import ast
import copy
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import os
import sys
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, NamedTuple

HERE = Path(__file__).resolve().parent
RUN_V3 = HERE / "run_v3.py"
LEDGER = Path(r"D:\Coding\_nevertwice_polygon\runs\v3\_budget\deepseek_ledger.jsonl")
LIMIT_USD = Decimal("20.00")
STOP_USD = Decimal("19.00")
PEAK_HOURS_UTC = ((1, 4), (6, 10))
RECONCILE_LOW = Decimal("0.9")
RECONCILE_HIGH = Decimal("1.1")
RECONCILE_FROM_USD = Decimal("0.20")
BALANCE_EVERY_S = 60.0
BALANCE_MAX_S = 600.0
ZERO_SHA = "0" * 64
NL = b"\n"
_LOAD_LOCK = threading.RLock()


class BudgetStop(RuntimeError):
    """Named refusal; the proxy returns 503 without forwarding a request."""

    def __init__(self, why: str, detail: str = "") -> None:
        super().__init__(f"budget-stop: {why}" + (f" ({detail})" if detail else ""))
        self.why = why


class BalanceRefused(RuntimeError):
    """The balance cannot independently witness the ledger."""


def _money(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("money must be a finite nonnegative number")
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        raise ValueError("invalid money") from None
    if not number.is_finite() or number < 0:
        raise ValueError("money must be finite and nonnegative")
    # Reject hostile exponents before arithmetic or fixed-point formatting can overflow/allocate without bound.
    if abs(number.as_tuple().exponent) > 1000 or len(number.as_tuple().digits) > 1000:
        raise ValueError("unsupported money precision")
    return number


def _count(value: Any, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < (1 if positive else 0):
        raise ValueError("invalid nonnegative integer")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _literal(path: Path, name: str) -> Any:
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
        found = [n.value for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        if len(found) != 1:
            raise ValueError(f"{len(found)} assignments of {name}")
        return ast.literal_eval(found[0])
    except (OSError, ValueError, SyntaxError, TypeError) as e:
        raise BudgetStop("price-unknown", str(e)) from None


def _validate_prices(price: Any, limits: Any) -> tuple[dict, dict]:
    try:
        if not isinstance(price, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("price and limits must be objects")
        if price.get("currency") != "USD" or price.get("model") != "deepseek-flash" or limits.get("model") != price["model"]:
            raise ValueError("USD deepseek-flash price and matching limits required")
        _count(price["per_tokens"], positive=True)
        _count(limits["max_output"], positive=True)
        for key in ("input_cache_hit", "input_cache_miss", "output"):
            rates = price[key]
            off, peak = _money(rates["off_peak"]), _money(rates["peak"])
            if peak < off:
                raise ValueError("peak must bound off-peak")
        for win in ("off_peak", "peak"):
            if _money(price["input_cache_miss"][win]) < _money(price["input_cache_hit"][win]):
                raise ValueError("cache miss must bound cache hit")
        copied = copy.deepcopy((_plain(price), _plain(limits)))
        json.dumps(copied, allow_nan=False)
        return copied
    except (KeyError, TypeError, ValueError, ArithmeticError) as e:
        raise BudgetStop("price-unknown", str(e)) from None


def load_prices(path: Path = RUN_V3) -> tuple[dict, dict]:
    return _validate_prices(_literal(path, "DEEPSEEK_FLASH_PRICE"), _literal(path, "DEEPSEEK_FLASH_LIMITS"))


def price_sha(price: Mapping) -> str:
    return hashlib.sha256(json.dumps(_plain(price), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rate(price: Mapping, key: str, win: str) -> Decimal:
    return _money(price[key][win]) / Decimal(_count(price["per_tokens"], positive=True))


def window(t0: float, t1: float) -> str:
    if not all(isinstance(t, (float, int)) and not isinstance(t, bool) and math.isfinite(t) for t in (t0, t1)) or t1 < t0:
        raise ValueError("invalid request interval")
    utc = _dt.timezone.utc
    day = _dt.datetime.fromtimestamp(t0, utc).date()
    last = _dt.datetime.fromtimestamp(t1, utc).date()
    while day <= last:
        if day.weekday() < 5:
            for a, b in PEAK_HOURS_UTC:
                start = _dt.datetime(day.year, day.month, day.day, a, tzinfo=utc).timestamp()
                end = _dt.datetime(day.year, day.month, day.day, b, tzinfo=utc).timestamp()
                if t0 < end and t1 >= start:
                    return "peak"
        day += _dt.timedelta(days=1)
    return "off_peak"


class _Pairs(list):
    """JSON object pairs retained without silently collapsing duplicate bounds."""


def request_max_tokens(body: bytes, limits: Mapping) -> tuple[int, str]:
    try:
        req = json.loads(body, object_pairs_hook=_Pairs)
    except ValueError:
        req = None
    values = [v for k, v in req if k in ("max_tokens", "max_completion_tokens")] if isinstance(req, _Pairs) else []
    asked = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
    model_max = _count(limits["max_output"], positive=True)
    if values and len(asked) != len(values):
        return max([model_max, *asked]), "model-max"
    return (max(asked), "request") if asked else (model_max, "model-max")


def worst_usd(in_bytes: int, max_tokens: int, price: Mapping) -> Decimal:
    return _count(in_bytes) * _rate(price, "input_cache_miss", "peak") + _count(max_tokens) * _rate(price, "output", "peak")


def usage_usd(usage: Any, win: str, price: Mapping) -> tuple[Decimal, int | None] | None:
    if not isinstance(usage, Mapping):
        return None
    try:
        hit, miss, completion, prompt = [_count(usage.get(k)) for k in
                                        ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "prompt_tokens")]
    except ValueError:
        return None
    if hit + miss != prompt:
        return None
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, Mapping) else None
    if not isinstance(reasoning, int) or isinstance(reasoning, bool) or not 0 <= reasoning <= completion:
        reasoning = None
    usd = hit * _rate(price, "input_cache_hit", win) + miss * _rate(price, "input_cache_miss", win) + completion * _rate(price, "output", win)
    return usd, reasoning


def bytes_usd(in_bytes: int, out_bytes: int, win: str, price: Mapping) -> Decimal:
    return _count(in_bytes) * _rate(price, "input_cache_miss", win) + _count(out_bytes) * _rate(price, "output", win)


def _launch():
    with _LOAD_LOCK:
        mod = sys.modules.get("v3_launch_for_budget")
        if mod is None:
            spec = importlib.util.spec_from_file_location("v3_launch_for_budget", HERE / "launch.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["v3_launch_for_budget"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop("v3_launch_for_budget", None)
                raise
        return mod


def _usd(value: Decimal) -> str:
    return format(value, "f")


def _iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat(timespec="microseconds")


class Reservation:
    """Opaque caller handle. Only its ID is used to look up the authoritative reserve."""

    __slots__ = ("id", "t0", "run", "in_bytes", "worst")

    def __init__(self, rid: str, t0: float, run: str, in_bytes: int, worst: Decimal) -> None:
        self.id, self.t0, self.run, self.in_bytes, self.worst = rid, t0, run, in_bytes, worst


class _Stored(NamedTuple):
    worst: Decimal
    run: str
    t0: float
    in_bytes: int
    max_tokens: int
    price_sha: str


class Ledger:
    """A shared append-only ledger; checks and writes hold the same interprocess lock."""

    def __init__(self, path: Path = LEDGER, *, prices: tuple[dict, dict] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.anchor = self.path.with_suffix(self.path.suffix + ".sha256")
        price, limits = _validate_prices(*(prices if prices is not None else load_prices()))
        self.price, self.limits = _freeze(price), _freeze(limits)
        self.price_sha = price_sha(self.price)
        self.clock = clock
        self._tlock = threading.Lock()
        self._offset, self._lineno = 0, 0
        self._last = b""
        self._prefix_sha = hashlib.sha256(b"").hexdigest()
        self._settled = Decimal(0)
        self._open: dict[str, _Stored] = {}
        self._ids: set[str] = set()
        self._run_settled: dict[str, Decimal] = {}
        self._run_calls: dict[str, int] = {}
        self._broken: str | None = None
        self._anomaly = False

    def _catch_up(self) -> None:
        if self._broken:
            return
        try:
            if not self.path.exists():
                if self._offset or self.anchor.exists():
                    self._broken = "ledger-missing"
                return
            raw = self.path.read_bytes()
            if len(raw) < self._offset:
                self._broken = "ledger-shrunk"
                return
            if hashlib.sha256(raw[:self._offset]).hexdigest() != self._prefix_sha:
                self._broken = "ledger-edited-prefix"
                return
            data = raw[self._offset:]
            if data and not data.endswith(NL):
                self._broken = f"ledger-torn: line {self._lineno + data.count(NL) + 1} has no end"
                return
            for line in data[:-1].split(NL) if data else ():
                self._lineno += 1
                why = self._apply(line)
                if why:
                    self._broken = f"ledger-{why}: line {self._lineno}"
                    return
                self._last = line
            if raw or self.anchor.exists():
                anchor = json.loads(self.anchor.read_bytes())
                if anchor != {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}:
                    self._broken = "ledger-anchor"
                    return
            self._offset = len(raw)
            self._prefix_sha = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, TypeError) as e:
            self._broken = f"ledger-unreadable-anchor: {type(e).__name__}"

    def _apply(self, line: bytes) -> str | None:
        try:
            rec = json.loads(line)
        except ValueError:
            return "unreadable"
        if not isinstance(rec, dict):
            return "unreadable"
        if rec.get("prev") != (hashlib.sha256(self._last).hexdigest() if self._last else ZERO_SHA):
            return "chain"
        try:
            kind = rec["kind"]
            if kind == "reserve":
                rid, run = rec["id"], rec["run"]
                if not isinstance(rid, str) or not rid or rid in self._ids or not isinstance(run, str):
                    return "record"
                t0 = _dt.datetime.fromisoformat(rec["t0"])
                if t0.tzinfo is None or not isinstance(rec["price_sha"], str) or len(rec["price_sha"]) != 64:
                    return "record"
                stored = _Stored(_money(rec["worst_usd"]), run, t0.timestamp(), _count(rec["in_bytes"]),
                                 _count(rec["max_tokens"]), rec["price_sha"])
                self._open[rid] = stored
                self._ids.add(rid)
                self._run_calls[run] = self._run_calls.get(run, 0) + 1
            elif kind == "settle":
                stored = self._open.get(rec["id"])
                if stored is None:
                    return "record"
                usd = _money(rec["usd"])
                del self._open[rec["id"]]  # fully validate money before releasing a reservation
                self._settled += usd
                self._run_settled[stored.run] = self._run_settled.get(stored.run, Decimal(0)) + usd
                self._anomaly = self._anomaly or rec.get("anomaly") is True
            elif kind == "stop":
                _money(rec["total_usd"])
                _money(rec["worst_usd"])
            else:
                return "record"
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            return "record"
        return None

    def _append(self, rec: dict) -> None:
        rec = dict(rec, prev=hashlib.sha256(self._last).hexdigest() if self._last else ZERO_SHA)
        line = json.dumps(rec, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
        why = self._apply(line)
        if why:
            self._broken = f"ledger-{why}: append"
            raise BudgetStop("ledger-broken", self._broken)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "ab") as f:
                f.write(line + NL)
                f.flush()
                os.fsync(f.fileno())
            raw = self.path.read_bytes()
            payload = json.dumps({"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}).encode("ascii")
            tmp = self.anchor.with_name(self.anchor.name + "." + uuid.uuid4().hex + ".tmp")
            with open(tmp, "xb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.anchor)
        except OSError as e:
            self._broken = f"ledger-write: {type(e).__name__}"
            raise BudgetStop("ledger-broken", self._broken) from None
        self._offset, self._last = len(raw), line
        self._prefix_sha = hashlib.sha256(raw).hexdigest()
        self._lineno += 1

    def _locked(self):
        return _launch().file_lock(self.path)

    def _require_sound(self) -> None:
        if self._broken:
            raise BudgetStop("ledger-broken", self._broken)

    def _total(self) -> Decimal:
        return self._settled + sum((r.worst for r in self._open.values()), Decimal(0))

    def total(self) -> Decimal:
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            return self._total()

    def _run_total(self, run: str) -> Decimal:
        return self._run_settled.get(run, Decimal(0)) + sum((r.worst for r in self._open.values() if r.run == run), Decimal(0))

    def run_total(self, run: str) -> tuple[Decimal, int, int]:
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            return self._run_total(run), self._run_calls.get(run, 0), sum(r.run == run for r in self._open.values())

    def broken(self) -> str | None:
        with self._tlock, self._locked():
            self._catch_up()
            return self._broken

    def reserve(self, *, arm: str, role: str, run: str, unit: str | None, body: bytes,
                ceiling_usd: Decimal | None = None) -> Reservation:
        try:
            ceiling = _money(ceiling_usd) if ceiling_usd is not None else None
        except ValueError as e:
            raise BudgetStop("run-ceiling-invalid", str(e)) from None
        if not isinstance(body, bytes):
            raise BudgetStop("body-not-bytes", "reserve the exact bytes forwarded by the proxy")
        max_tokens, source = request_max_tokens(body, self.limits)
        worst = worst_usd(len(body), max_tokens, self.price)
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            if self._anomaly:
                raise BudgetStop("usage-bound", "provider usage exceeded the persisted request bound")
            t0, total = self.clock(), self._total()
            why = "total" if total + worst >= STOP_USD else (
                "run-ceiling" if ceiling is not None and self._run_total(run) + worst >= ceiling else None)
            if why:
                self._append({"kind": "stop", "t": _iso(t0), "arm": arm, "role": role, "run": run, "unit": unit,
                              "why": why, "total_usd": _usd(total), "worst_usd": _usd(worst)})
                raise BudgetStop(why, f"total {total} + worst {worst}")
            rid = uuid.uuid4().hex
            self._append({"kind": "reserve", "id": rid, "t0": _iso(t0), "arm": arm, "role": role, "run": run,
                          "unit": unit, "in_bytes": len(body), "max_tokens": max_tokens, "max_tokens_source": source,
                          "worst_usd": _usd(worst), "price_sha": self.price_sha})
            return Reservation(rid, t0, run, len(body), worst)

    def settle(self, res: Reservation, *, status: int | None, usage: Any = None, done: bool = False,
               out_bytes: int | None = None) -> dict:
        with self._tlock, self._locked():
            self._catch_up()
            if self._broken:
                # Forensic evidence only. No release or authoritative total on a broken ledger.
                rec = {"kind": "settle-unverified", "id": res.id, "status": status, "ledger_error": self._broken,
                       "basis": "unverified", "t1": _iso(self.clock())}
                line = json.dumps(dict(rec, prev=hashlib.sha256(self._last).hexdigest() if self._last else ZERO_SHA)).encode("ascii")
                with open(self.path, "ab") as f:
                    f.write(line + NL)
                    f.flush()
                    os.fsync(f.fileno())
                return rec
            stored = self._open.get(res.id)
            if stored is None:
                raise BudgetStop("reservation-unknown", "unknown or already settled ID")
            if stored.price_sha != self.price_sha:
                raise BudgetStop("price-changed", "reservation retains its original price")
            t1 = max(self.clock(), stored.t0)
            win = window(stored.t0, t1)
            raw_usage, unsafe_usage = None, False
            if isinstance(usage, Mapping):
                try:
                    raw_usage = _plain(usage)
                    json.dumps(raw_usage, sort_keys=True, allow_nan=False)
                except (TypeError, ValueError, RecursionError):
                    raw_usage, unsafe_usage = None, True
            rec = {"kind": "settle", "id": res.id, "t1": _iso(t1), "status": status, "window": win,
                   "usage": raw_usage, "reasoning_tokens": None,
                   "price_sha": self.price_sha}
            if unsafe_usage:
                rec["usage_error"] = "not-json"
            priced = usage_usd(usage, win, self.price) if usage is not None and not unsafe_usage else None
            if priced is not None and (usage["prompt_tokens"] > stored.in_bytes or usage["completion_tokens"] > stored.max_tokens):
                rec.update(basis="worst", usd=_usd(stored.worst), reason="usage-bound", anomaly=True,
                           raw_estimate_usd=_usd(priced[0]))
            elif priced is not None:
                rec.update(basis="usage", usd=_usd(priced[0]), reasoning_tokens=priced[1])
            elif usage is None and done is True and isinstance(out_bytes, int) and not isinstance(out_bytes, bool) and out_bytes >= 0:
                estimate = bytes_usd(stored.in_bytes, out_bytes, win, self.price)
                rec.update(basis="bytes", usd=_usd(min(stored.worst, estimate)), raw_estimate_usd=_usd(estimate))
            else:
                rec.update(basis="worst", usd=_usd(stored.worst), reason="usage-shape" if usage is not None else "no-usage")
            self._append(rec)
            return rec


def _balance_money(value: Any) -> Decimal:
    try:
        return _money(value)
    except ValueError as e:
        raise BalanceRefused(str(e)) from None


def parse_balance(body: bytes) -> Decimal:
    try:
        doc = json.loads(body)
    except ValueError:
        raise BalanceRefused("balance is no JSON") from None
    infos = doc.get("balance_infos") if isinstance(doc, dict) else None
    if not isinstance(infos, list):
        raise BalanceRefused("balance has no balance_infos")
    usd = [i for i in infos if isinstance(i, dict) and i.get("currency") == "USD"]
    if len(usd) != 1:
        other = sorted({str(i.get("currency")) for i in infos if isinstance(i, dict)} - {"USD"})
        raise BalanceRefused(f"{len(usd)} USD entries in balance_infos; currency {', '.join(other)}")
    return _balance_money(usd[0].get("total_balance"))


def stable_balance(read: Callable[[], Decimal], *, sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic, every_s: float = BALANCE_EVERY_S,
                   max_s: float = BALANCE_MAX_S) -> tuple[Decimal, int]:
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in (every_s, max_s)):
        raise BalanceRefused("invalid balance polling interval")
    start = clock()
    prev, reads = _balance_money(read()), 1
    while True:
        if clock() - start + every_s > max_s:
            raise BalanceRefused(f"balance not stable after {reads} reads in {max_s:.0f} s")
        sleep(every_s)
        cur = _balance_money(read())
        reads += 1
        if cur == prev:
            return cur, reads
        prev = cur


def reconcile(ledger_usd: Decimal, before: Decimal, after: Decimal) -> dict:
    ledger_usd, before, after = map(_balance_money, (ledger_usd, before, after))
    spent = before - after
    if spent < 0:
        raise BalanceRefused("balance increased during the run")
    if ledger_usd < RECONCILE_FROM_USD:
        verdict = "record-only"
    elif spent < RECONCILE_LOW * ledger_usd:
        verdict = "stop:ledger-over"
    elif spent > RECONCILE_HIGH * ledger_usd:
        verdict = "stop:balance-over"
    else:
        verdict = "ok"
    return {"verdict": verdict, "ledger_usd": str(ledger_usd), "balance_before": str(before),
            "balance_after": str(after), "balance_spent": str(spent)}
