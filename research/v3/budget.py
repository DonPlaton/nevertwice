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
# Provider peak windows exclude Chinese public holidays. Weekend workdays remain off-peak.
# Official 2026 notice, published 2025-11-04; do not extrapolate an unverified year.
HOLIDAY_CALENDAR_YEAR = 2026
HOLIDAY_CALENDAR_URL = "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm"
_HOLIDAYS = frozenset(_dt.date.fromisoformat(day) for day in (
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"))
_MODEL_LITERALS = {"deepseek-flash": ("DEEPSEEK_FLASH_PRICE", "DEEPSEEK_FLASH_LIMITS"),
    "deepseek-v4-pro": ("DEEPSEEK_PRO_PRICE", "DEEPSEEK_PRO_LIMITS")}
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
    if type(value) is not int or value < (1 if positive else 0):
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
        if (price.get("currency") != "USD" or type(price.get("model")) is not str
                or price["model"] not in _MODEL_LITERALS or limits.get("model") != price["model"]):
            raise ValueError("USD supported model price and matching limits required")
        _count(price["per_tokens"], positive=True)
        _count(limits["max_output"], positive=True)
        _usage_count(price["per_tokens"])
        _usage_count(limits["max_output"])
        for key in ("input_cache_hit", "input_cache_miss", "output"):
            rates = price[key]
            off, peak = _money(rates["off_peak"]), _money(rates["peak"])
            if peak < off:
                raise ValueError("peak must bound off-peak")
        for win in ("off_peak", "peak"):
            if _money(price["input_cache_miss"][win]) < _money(price["input_cache_hit"][win]):
                raise ValueError("cache miss must bound cache hit")
        # Validate both one-token resolution and the largest supported accounting sum.
        # A finite rate alone does not ensure division/multiplication fits the ledger.
        for win in ("off_peak", "peak"):
            rates = [_money(_usd(_money(_rate(price, key, win)))) for key in ("input_cache_hit", "input_cache_miss", "output")]
            _money(_usd(_money(MAX_USAGE_COUNT * sum(rates, Decimal(0)))))
        copied = copy.deepcopy((_plain(price), _plain(limits)))
        json.dumps(copied, allow_nan=False)
        return copied
    except (KeyError, TypeError, ValueError, ArithmeticError) as e:
        raise BudgetStop("price-unknown", str(e)) from None


def load_prices(path: Path = RUN_V3, *, model: str = "deepseek-flash") -> tuple[dict, dict]:
    if type(model) is not str or model not in _MODEL_LITERALS:
        raise BudgetStop("price-unknown", "unsupported exact model")
    price_name, limits_name = _MODEL_LITERALS[model]
    price, limits = _validate_prices(_literal(path, price_name), _literal(path, limits_name))
    if price["model"] != model:
        raise BudgetStop("price-unknown", "literal does not match the selected model")
    return price, limits


def price_sha(price: Mapping) -> str:
    return hashlib.sha256(json.dumps(_plain(price), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rate(price: Mapping, key: str, win: str) -> Decimal:
    return _money(price[key][win]) / Decimal(_count(price["per_tokens"], positive=True))


def window(t0: float, t1: float) -> str:
    if not all(isinstance(t, (float, int)) and not isinstance(t, bool) and math.isfinite(t) for t in (t0, t1)) or t1 < t0:
        raise ValueError("invalid request interval")
    utc = _dt.timezone.utc
    try:
        day = _dt.datetime.fromtimestamp(t0, utc).date()
        last = _dt.datetime.fromtimestamp(t1, utc).date()
    except (ValueError, OverflowError, OSError):
        raise BudgetStop("price-window-unverified", "timestamp outside the verified calendar") from None
    if day.year != HOLIDAY_CALENDAR_YEAR or last.year != HOLIDAY_CALENDAR_YEAR:
        raise BudgetStop("price-window-unverified", "holiday calendar year not verified")
    while day <= last:
        if day.weekday() < 5 and day not in _HOLIDAYS:
            for a, b in PEAK_HOURS_UTC:
                start = _dt.datetime(day.year, day.month, day.day, a, tzinfo=utc).timestamp()
                end = _dt.datetime(day.year, day.month, day.day, b, tzinfo=utc).timestamp()
                if t0 < end and t1 >= start:
                    return "peak"
        day += _dt.timedelta(days=1)
    return "off_peak"


class _Pairs(list):
    """JSON object pairs retained without silently collapsing duplicate bounds."""


def _request_integer(raw: str) -> int:
    # Refuse before Python's integer parse cap can turn an oversized bound into model-max.
    if len(raw.lstrip("-")) > 19:
        raise BudgetStop("request-bound-unverified")
    value = int(raw)
    if abs(value) > MAX_USAGE_COUNT:
        raise BudgetStop("request-bound-unverified")
    return value


def request_max_tokens(body: bytes, limits: Mapping) -> tuple[int, str]:
    try:
        req = json.loads(body, object_pairs_hook=_Pairs, parse_int=_request_integer)
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
    if type(reasoning) is not int or not 0 <= reasoning <= completion:
        reasoning = None
    usd = hit * _rate(price, "input_cache_hit", win) + miss * _rate(price, "input_cache_miss", win) + completion * _rate(price, "output", win)
    return usd, reasoning


def bytes_usd(in_bytes: int, out_bytes: int, win: str, price: Mapping) -> Decimal:
    return _count(in_bytes) * _rate(price, "input_cache_miss", win) + _count(out_bytes) * _rate(price, "output", win)


SETTLEMENT_OUTCOMES = frozenset({"complete", "incomplete", "abandoned", "no-response", "io-error",
    "parse-limit", "parse-error", "model-mismatch", "usage-conflict", "usage-shape", "usage-bound"})
ACCOUNTING_ANOMALIES = frozenset({"model-mismatch", "usage-conflict", "usage-shape", "usage-bound"})
UNVERIFIED_ACCOUNTING_REASONS = frozenset({"model-mismatch", "usage-count-unverified",
    "usage-conflict", "usage-shape", "usage-bound", "price-window-unverified"})
# Signed 64-bit counters are the supported accounting evidence range, not a provider limit.
MAX_USAGE_COUNT = (1 << 63) - 1


def _usage_count(value: Any) -> int:
    count = _count(value)
    if count > MAX_USAGE_COUNT:
        raise OverflowError("unsupported usage counter")
    return count


def project_usage(usage: Any) -> tuple[dict | None, str | None]:
    """A closed numeric projection; arbitrary provider fields never become evidence."""
    if usage is None:
        return None, None
    if not isinstance(usage, Mapping):
        return None, "usage-shape"
    try:
        values = {key: _usage_count(usage.get(key)) for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
            "completion_tokens", "prompt_tokens")}
        if values["prompt_cache_hit_tokens"] + values["prompt_cache_miss_tokens"] != values["prompt_tokens"]:
            return None, "usage-shape"
        details = usage.get("completion_tokens_details")
        if details is not None:
            if not isinstance(details, Mapping):
                return None, "usage-shape"
            reasoning = details.get("reasoning_tokens")
            if reasoning is not None:
                reasoning = _usage_count(reasoning)
                if reasoning > values["completion_tokens"]:
                    return None, "usage-shape"
                values["completion_tokens_details"] = {"reasoning_tokens": reasoning}
        return values, None
    except OverflowError:
        return None, "usage-count-unverified"
    except (ValueError, TypeError, KeyError):
        return None, "usage-shape"


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
        self._halt_global: str | None = None
        self._halt_runs: dict[str, str] = {}
        self._cost_unverified: set[str] = set()
        self._run_caps: dict[str, Decimal | None] = {}

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
                cap_raw = rec.get("run_ceiling_usd")
                cap = _money(cap_raw) if cap_raw is not None else None
                if run in self._run_caps and cap != self._run_caps[run]:
                    return "record"
                t0 = _dt.datetime.fromisoformat(rec["t0"])
                if t0.tzinfo is None or not isinstance(rec["price_sha"], str) or len(rec["price_sha"]) != 64:
                    return "record"
                stored = _Stored(_money(rec["worst_usd"]), run, t0.timestamp(), _count(rec["in_bytes"]),
                                 _count(rec["max_tokens"]), rec["price_sha"])
                self._run_caps.setdefault(run, cap)
                self._open[rid] = stored
                self._ids.add(rid)
                self._run_calls[run] = self._run_calls.get(run, 0) + 1
            elif kind == "settle":
                stored = self._open.get(rec["id"])
                if stored is None or rec["id"] in self._cost_unverified:
                    return "record"
                usd = _money(rec["usd"])
                del self._open[rec["id"]]  # fully validate money before releasing a reservation
                self._settled += usd
                self._run_settled[stored.run] = self._run_settled.get(stored.run, Decimal(0)) + usd
                self._anomaly = self._anomaly or rec.get("anomaly") is True
            elif kind == "accounting-unverified":
                rid = rec["id"]
                stored = self._open.get(rid)
                if stored is None or rid in self._cost_unverified:
                    return "record"
                if rec.get("reason") not in UNVERIFIED_ACCOUNTING_REASONS or rec.get("basis") != "unverified":
                    return "record"
                if rec.get("price_sha") != stored.price_sha or _money(rec["worst_usd"]) != stored.worst:
                    return "record"
                self._cost_unverified.add(rid)
                self._anomaly = True
            elif kind == "admission-stop":
                if rec.get("why") != "request-bound-unverified":
                    return "record"
                self._halt_global = self._halt_global or "request-bound-unverified"
            elif kind == "stop":
                _money(rec["total_usd"])
                _money(rec["worst_usd"])
                if rec.get("why") == "total":
                    self._halt_global = self._halt_global or "total"
                elif rec.get("why") == "run-ceiling" and isinstance(rec.get("run"), str):
                    self._halt_runs.setdefault(rec["run"], "run-ceiling")
                else:
                    return "record"
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

    def _require_verified_cost(self) -> None:
        if self._cost_unverified:
            raise BudgetStop("accounting-unverified", "provider cost evidence remains unresolved")

    def total(self) -> Decimal:
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            self._require_verified_cost()
            return self._total()

    def _run_total(self, run: str) -> Decimal:
        return self._run_settled.get(run, Decimal(0)) + sum((r.worst for r in self._open.values() if r.run == run), Decimal(0))

    def run_total(self, run: str) -> tuple[Decimal, int, int]:
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            self._require_verified_cost()
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
        bound_error = None
        # The `request-bound-unverified` branch below refuses before any of these is read, but a
        # static reader cannot see that ordering: inert defaults give py/uninitialized-local-variable
        # nothing to flag, and the refusal path cannot leak a stale name either.
        max_tokens, source, worst = 0, "", Decimal(0)
        try:
            max_tokens, source = request_max_tokens(body, self.limits)
            worst = worst_usd(len(body), max_tokens, self.price)
        except BudgetStop as exc:
            if exc.why != "request-bound-unverified":
                raise
            bound_error = exc.why
        with self._tlock, self._locked():
            self._catch_up()
            self._require_sound()
            if self._halt_global:
                raise BudgetStop(self._halt_global, "the shared ledger has a durable admission halt")
            if run in self._halt_runs:
                raise BudgetStop(self._halt_runs[run], "this trusted run has a durable admission halt")
            if self._anomaly:
                raise BudgetStop("accounting-anomaly", "provider accounting requires review")
            if bound_error:
                # No request was admitted, so existing cost totals remain authoritative.
                # The refusal is global and durable without serializing the unsafe body/count.
                self._append({"kind": "admission-stop", "why": bound_error, "t": _iso(self.clock()),
                              "arm": arm, "role": role, "run": run, "unit": unit})
                raise BudgetStop(bound_error)
            if run in self._run_caps and ceiling != self._run_caps[run]:
                raise BudgetStop("run-ceiling-changed", "the trusted run's first reservation pinned its ceiling")
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
                          "worst_usd": _usd(worst), "price_sha": self.price_sha,
                          "run_ceiling_usd": _usd(ceiling) if ceiling is not None else None})
            return Reservation(rid, t0, run, len(body), worst)

    def settle(self, res: Reservation, *, status: int | None, usage: Any = None, done: bool = False,
               out_bytes: int | None = None, outcome: str | None = None) -> dict:
        # Only typed status/outcome data may reach ordinary or forensic records.
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise BudgetStop("settlement-status-invalid")
        if type(done) is not bool:
            raise BudgetStop("settlement-completion-invalid")
        outcome = ("complete" if done else "incomplete") if outcome is None else outcome
        if not isinstance(outcome, str) or outcome not in SETTLEMENT_OUTCOMES:
            raise BudgetStop("settlement-outcome-invalid")
        if outcome == "complete" and not done:
            raise BudgetStop("settlement-completion-invalid")
        projected, usage_error = project_usage(usage)
        with self._tlock, self._locked():
            self._catch_up()
            if self._broken:
                # Forensic evidence cannot release the still-authoritative reserve.
                rec = {"kind": "settle-unverified", "id": res.id, "status": status, "ledger_error": self._broken,
                       "basis": "unverified", "outcome": outcome, "t1": _iso(self.clock())}
                line = json.dumps(dict(rec, prev=hashlib.sha256(self._last).hexdigest() if self._last else ZERO_SHA)).encode("ascii")
                with open(self.path, "ab") as f:
                    f.write(line + NL)
                    f.flush()
                    os.fsync(f.fileno())
                return rec
            stored = self._open.get(res.id)
            if stored is None:
                raise BudgetStop("reservation-unknown", "unknown or already settled ID")
            if res.id in self._cost_unverified:
                raise BudgetStop("settlement-unverified", "this ID retains unresolved accounting evidence")
            accounting_reason = outcome if outcome in ACCOUNTING_ANOMALIES else usage_error
            if accounting_reason is None and projected is not None and (
                    projected["prompt_tokens"] > stored.in_bytes or projected["completion_tokens"] > stored.max_tokens):
                accounting_reason = "usage-bound"
            output_count_unverified = (usage is None and done and isinstance(out_bytes, int)
                and not isinstance(out_bytes, bool) and (type(out_bytes) is not int or out_bytes > MAX_USAGE_COUNT))
            unverified = "model-mismatch" if outcome == "model-mismatch" else (
                "usage-count-unverified" if usage_error == "usage-count-unverified" or output_count_unverified else (
                    accounting_reason if stored.price_sha != self.price_sha else None))
            if unverified:
                # Neither a changed local tariff nor unpriceable counters may erase the observation.
                # Bind provenance to the persisted reservation without applying the current tariff.
                rec = {"kind": "accounting-unverified", "id": res.id, "t1": _iso(max(self.clock(), stored.t0)),
                       "status": status, "outcome": outcome, "usage": None, "basis": "unverified",
                       "reason": unverified, "price_sha": stored.price_sha,
                       "worst_usd": _usd(stored.worst), "anomaly": True}
                self._append(rec)
                return rec
            if stored.price_sha != self.price_sha:
                raise BudgetStop("price-changed", "reservation retains its original price")
            t1 = max(self.clock(), stored.t0)
            try:
                win = window(stored.t0, t1)
            except BudgetStop as exc:
                if exc.why != "price-window-unverified":
                    raise
                rec = {"kind": "accounting-unverified", "id": res.id, "t1": _iso(t1),
                       "status": status, "outcome": outcome, "usage": None, "basis": "unverified",
                       "reason": exc.why, "price_sha": stored.price_sha,
                       "worst_usd": _usd(stored.worst), "anomaly": True}
                self._append(rec)
                return rec
            rec = {"kind": "settle", "id": res.id, "t1": _iso(t1), "status": status, "window": win,
                   "usage": projected, "reasoning_tokens": None, "price_sha": self.price_sha, "outcome": outcome}
            if usage_error:
                rec["usage_error"] = usage_error
            priced = usage_usd(projected, win, self.price) if projected is not None else None
            reason = None
            if outcome in ACCOUNTING_ANOMALIES:
                reason = outcome
            elif usage_error:
                reason = "usage-shape"
            elif priced is not None and (projected["prompt_tokens"] > stored.in_bytes or projected["completion_tokens"] > stored.max_tokens):
                reason = "usage-bound"
            elif outcome != "complete" or not done:
                reason = outcome
            if reason:
                amount = max(stored.worst, priced[0]) if priced is not None else stored.worst
                rec.update(basis="conservative", usd=_usd(amount), reason=reason)
                if amount == stored.worst:
                    rec["basis"] = "worst"
                if reason in ACCOUNTING_ANOMALIES:
                    rec["anomaly"] = True
                if priced is not None:
                    rec["raw_estimate_usd"] = _usd(priced[0])
            elif priced is not None:
                rec.update(basis="usage", usd=_usd(priced[0]), reasoning_tokens=priced[1])
            elif usage is None and done and type(out_bytes) is int and out_bytes >= 0:
                estimate = bytes_usd(stored.in_bytes, out_bytes, win, self.price)
                rec.update(basis="bytes", usd=_usd(min(stored.worst, estimate)), raw_estimate_usd=_usd(estimate))
            else:
                rec.update(basis="worst", usd=_usd(stored.worst), reason="no-usage")
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
