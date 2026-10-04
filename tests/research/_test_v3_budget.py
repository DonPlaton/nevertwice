#!/usr/bin/env python3
"""R-BUDGET-20 (the owner, 2026-10-01; the auditor's BUD plan with BUD-a..f): research/v3/budget.py - the DeepSeek
ledger, the prices by window, the reservation before forwarding, the settlement, the balance as a second witness.

* prices: run_v3.py's DEEPSEEK_FLASH_PRICE and DEEPSEEK_FLASH_LIMITS read as literals (max_output 393216); two
  assignments or another currency refuse by name;
* windows: UTC weekdays [01, 04) and [06, 10) are peak; a request that touches a peak hour anywhere in [t0, t1] is
  priced at the peak (BUD-a);
* the worst price: bytes as peak cache misses plus max_tokens at the peak output price; no max_tokens is the model's
  maximum, a larger one is reserved as asked;
* reservations: the total (settled + every open reservation, a dead process's too) plus the worst may not reach
  $19.00, the run's spend plus the worst may not reach its ceiling - refused by name with a stop line; two ledgers
  on one file (threads, instances) never reserve past the line together;
* settlement: the usage at the window's price, reasoning never added twice (BUD-c); a finished stream with no usage
  by the lesser of byte pricing and the persisted token bound (BUD-b), raw estimate retained; anything else the worst;
* the ledger: chained lines and a durable tail checkpoint; corruption refuses admission and authoritative
  totals, while a broken-ledger settlement is retained only as unverified forensic evidence;
* the balance: the one USD entry's total_balance, read until two equal reads within 10 minutes; reconciliation
  within [0.9, 1.1] of the ledger's spend, only recorded below $0.20.

Rows use temporary files, threads and two bounded local Python ledger writers; no network or model calls.

    python tests/research/_test_v3_budget.py
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import subprocess
from unittest import mock
import sys
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))


def _load():
    spec = importlib.util.spec_from_file_location("v3_budget_under_test", ROOT / "research" / "v3" / "budget.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = ("BUD-PRICE-1", "BUD-WIN-1a", "BUD-WIN-1b", "BUD-MAX-1", "BUD-WORST-1", "BUD-USAGE-1", "BUD-SETTLE-1",
        "BUD-RES-1", "BUD-RES-2", "BUD-OPEN-1", "BUD-SEQ-1", "BUD-PAR-1", "BUD-BROKEN-1", "BUD-BROKEN-2",
        "BUD-BAL-1", "BUD-BAL-2", "BUD-REC-1")
try:
    B = _load()
    B.load_prices()
except Exception as e:                  # no module or no limits yet: every row fails by its name
    for row in ROWS:
        check(row, False, f"research/v3/budget.py does not load: {type(e).__name__}: {e}")
    print(f"\nv3 budget: {PASSED} passed, {FAILED} failed")
    sys.exit(1)
UTC = dt.timezone.utc


def ts(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> float:
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp()


THU = (2026, 10, 1)                     # a Thursday
SAT, SUN, FRI, MON = (2026, 10, 3), (2026, 10, 4), (2026, 10, 2), (2026, 10, 5)
NOON = ts(*THU, 12)                     # off peak


class Clock:
    def __init__(self, t: float = NOON) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def body(max_tokens: int | None = None, length: int = 1000, key: str = "max_tokens") -> bytes:
    req: dict = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "hi"}]}
    if max_tokens is not None:
        req[key] = max_tokens
    req["pad"] = ""
    raw = json.dumps(req).encode()
    if length:
        req["pad"] = "x" * (length - len(raw))
        raw = json.dumps(req).encode()
        assert len(raw) == length, (len(raw), length)
    return raw


def forge(path: Path, recs: list[dict]) -> None:
    """Chained lines written the ledger's way - a history some earlier run left."""
    last = path.read_bytes().rstrip(b"\n").split(b"\n")[-1] if path.exists() and path.stat().st_size else b""
    with open(path, "ab") as f:
        for r in recs:
            line = json.dumps(dict(r, prev=hashlib.sha256(last).hexdigest() if last else "0" * 64),
                              sort_keys=True, ensure_ascii=True).encode()
            f.write(line + b"\n")
            last = line
    raw = path.read_bytes()
    path.with_suffix(path.suffix + ".sha256").write_text(
        json.dumps({"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}), encoding="ascii")


def spent(path: Path, run: str, usd: str, rid: str = "r0", *, ceiling_usd: Decimal | None = None) -> None:
    forge(path, [{"kind": "reserve", "id": rid, "t0": B._iso(NOON), "arm": "a", "role": "write", "run": run, "unit": None,
                  "in_bytes": 1, "max_tokens": 1, "max_tokens_source": "request", "worst_usd": usd,
                  "price_sha": B.price_sha(B.load_prices()[0]),
                  "run_ceiling_usd": str(ceiling_usd) if ceiling_usd is not None else None},
                 {"kind": "settle", "id": rid, "t1": B._iso(NOON), "status": 200, "window": "off_peak", "usage": None,
                  "reasoning_tokens": None, "basis": "usage", "usd": usd, "price_sha": B.price_sha(B.load_prices()[0])}])


def stopped(fn) -> tuple[str | None, str]:
    try:
        fn()
    except B.BudgetStop as e:
        return e.why, str(e)
    return None, ""


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_bytes().split(b"\n") if x]


TMP = Path(tempfile.mkdtemp(prefix="nvt_budget_"))
N = [0]


def fresh() -> Path:
    N[0] += 1
    d = TMP / f"l{N[0]}"
    d.mkdir()
    return d / "deepseek_ledger.jsonl"


try:
    PRICE, LIMITS = B.load_prices()

    print("- prices -")
    src = (ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8")
    two = TMP / "two.py"
    two.write_text(src + "\nDEEPSEEK_FLASH_LIMITS = {'model': 'deepseek-flash', 'max_output': 1}\n", encoding="utf-8")
    cny = TMP / "cny.py"
    cny.write_text(src.replace('"currency": "USD"', '"currency": "CNY"', 1), encoding="utf-8")
    check("BUD-PRICE-1: the price and the limits are run_v3.py's literals - max_output 393216 (384 x 1024), peak miss "
          "0.3 and peak output 1.2 USD per 1M; two assignments or another currency refuse by name",
          LIMITS["max_output"] == 393216 and LIMITS["read"] == "2026-10-01" and PRICE["currency"] == "USD"
          and PRICE["input_cache_miss"]["peak"] == 0.3 and PRICE["output"]["peak"] == 1.2
          and stopped(lambda: B.load_prices(two))[0] == "price-unknown"
          and stopped(lambda: B.load_prices(cny))[0] == "price-unknown",
          f"{LIMITS} {stopped(lambda: B.load_prices(two))} {stopped(lambda: B.load_prices(cny))}")

    print("\n- windows (BUD-a) -")
    points = {"Thu 00:59:59": (ts(*THU, 0, 59, 59), "off_peak"), "Thu 01:00": (ts(*THU, 1), "peak"),
              "Thu 03:59:59": (ts(*THU, 3, 59, 59), "peak"), "Thu 04:00": (ts(*THU, 4), "off_peak"),
              "Thu 05:59:59": (ts(*THU, 5, 59, 59), "off_peak"), "Thu 06:00": (ts(*THU, 6), "peak"),
              "Thu 09:59:59": (ts(*THU, 9, 59, 59), "peak"), "Thu 10:00": (ts(*THU, 10), "off_peak"),
              "Sat 02:00": (ts(*SAT, 2), "off_peak"), "Sun 07:00": (ts(*SUN, 7), "off_peak")}
    got = {k: B.window(t, t) for k, (t, _) in points.items()}
    check("BUD-WIN-1a: a point in time - peak exactly on weekdays' [01:00, 04:00) and [06:00, 10:00) UTC",
          all(got[k] == w for k, (_, w) in points.items()), str(got))
    spans = {"00:30 -> 01:00 (touches the start)": (ts(*THU, 0, 30), ts(*THU, 1), "peak"),
             "00:30 -> 00:59:59": (ts(*THU, 0, 30), ts(*THU, 0, 59, 59), "off_peak"),
             "03:30 -> 04:30 (starts in peak)": (ts(*THU, 3, 30), ts(*THU, 4, 30), "peak"),
             "04:10 -> 05:50": (ts(*THU, 4, 10), ts(*THU, 5, 50), "off_peak"),
             "Fri 23:00 -> Mon 00:30": (ts(*FRI, 23), ts(*MON, 0, 30), "off_peak"),
             "Fri 23:00 -> Mon 01:00": (ts(*FRI, 23), ts(*MON, 1), "peak")}
    got = {k: B.window(a, b) for k, (a, b, _) in spans.items()}
    check("BUD-WIN-1b (BUD-a): a request is priced at the peak when any instant of [t0, t1] is a peak hour - started "
          "off peak and finished in it, or started in it; a weekend never is",
          all(got[k] == w for k, (_, _, w) in spans.items()), str(got))

    print("\n- the worst price -")
    cases = {"none": (body(), (393216, "model-max")), "1000": (body(1000), (1000, "request")),
             "500000 (past the model's maximum)": (body(500000), (500000, "request")),
             "max_completion_tokens 7000 with max_tokens 10":
                 (body(10)[:-1] + b', "max_completion_tokens": 7000}', (7000, "request")),
             "true": (body(True), (393216, "model-max")), "-1": (body(-1), (393216, "model-max")),
             '"100"': (body("100"), (393216, "model-max")), "no JSON": (b"not json", (393216, "model-max")),
             "a list": (b"[1, 2]", (393216, "model-max")), "not UTF-8": (b"\xff\xfe", (393216, "model-max"))}
    got = {k: B.request_max_tokens(b, LIMITS) for k, (b, _) in cases.items()}
    check("BUD-MAX-1 (Q-BUD-2 a): max_tokens is the request's own (the larger of max_tokens and "
          "max_completion_tokens, even past the model's maximum); none, a non-integer, or a body that is no JSON "
          "object is the model's documented maximum 393216",
          all(got[k] == w for k, (_, w) in cases.items()), str(got))
    led = fresh()
    L = B.Ledger(led, clock=Clock())
    r = L.reserve(arm="mem0", role="write", run="w1", unit="u1", body=body(2000))
    rec = lines(led)[-1]
    check("BUD-WORST-1: the worst price is every byte a peak cache miss and max_tokens at the peak output price - "
          "1000 bytes with max_tokens 2000 reserve exactly $0.0027; the reserve line says so",
          B.worst_usd(1000, 2000, PRICE) == Decimal("0.0027") and r.worst == Decimal("0.0027")
          and B.worst_usd(0, 393216, PRICE) == Decimal("0.4718592")
          and rec["kind"] == "reserve" and Decimal(rec["worst_usd"]) == Decimal("0.0027")
          and rec["max_tokens"] == 2000 and rec["max_tokens_source"] == "request" and rec["in_bytes"] == 1000
          and rec["price_sha"] == B.price_sha(PRICE) and rec["prev"] == "0" * 64,
          f"{B.worst_usd(1000, 2000, PRICE)} {rec}")

    print("\n- usage (BUD-c) -")
    use = {"prompt_cache_hit_tokens": 1000, "prompt_cache_miss_tokens": 2000, "completion_tokens": 3000,
           "prompt_tokens": 3000, "completion_tokens_details": {"reasoning_tokens": 500}}
    off, peak = B.usage_usd(use, "off_peak", PRICE), B.usage_usd(use, "peak", PRICE)
    bad = {"hit + miss != prompt": dict(use, prompt_tokens=3001), "a bool": dict(use, completion_tokens=True),
           "negative": dict(use, prompt_cache_hit_tokens=-1), "no completion": {k: v for k, v in use.items()
                                                                               if k != "completion_tokens"},
           "a list": [1, 2]}
    check("BUD-USAGE-1: usage is priced hit x hit + miss x miss + completion x output at the window's price "
          "($0.002103 off peak, $0.004206 at the peak); reasoning (500) is completion's, never added again; a usage "
          "whose hit + miss is not the prompt's count, or any count not a non-negative integer, is not priced",
          off == (Decimal("0.002103"), 500) and peak == (Decimal("0.004206"), 500)
          and all(B.usage_usd(u, "off_peak", PRICE) is None for u in bad.values())
          and B.usage_usd({k: v for k, v in use.items() if k != "completion_tokens_details"}, "off_peak", PRICE)
          == (Decimal("0.002103"), None),
          f"{off} {peak} {[B.usage_usd(u, 'off_peak', PRICE) for u in bad.values()]}")

    print("\n- settlement (BUD-b) -")
    led = fresh()
    clk = Clock()
    L = B.Ledger(led, clock=clk)
    settle_use = dict(use, prompt_cache_hit_tokens=100, prompt_cache_miss_tokens=200, prompt_tokens=300,
                      completion_tokens=300, completion_tokens_details={"reasoning_tokens": 50})
    # Reserve all six requests before the shape anomaly halts new admission.
    ordinary = [L.reserve(arm="a", role="write", run="s1", unit=None, body=body(2000)) for _ in range(5)]
    clk.t = ts(*THU, 0, 59, 30)
    rp = L.reserve(arm="a", role="write", run="s1", unit=None, body=body(2000))
    clk.t = NOON
    s_use = L.settle(ordinary[0], status=200, done=True, usage=settle_use)
    s_bytes = L.settle(ordinary[1], status=200, done=True, out_bytes=100)
    s_torn = L.settle(ordinary[2], status=200, done=False, out_bytes=100)
    s_shape = L.settle(ordinary[3], status=200, done=True, usage=dict(use, prompt_tokens=1))
    s_none = L.settle(ordinary[4], status=None)
    clk.t = ts(*THU, 1, 0, 30)
    s_peak = L.settle(rp, status=200, done=True, usage=settle_use)
    tot, calls, open_ = L.run_total("s1")
    want = [("usage", "0.0002103", "off_peak"), ("bytes", "0.00021", "off_peak"), ("worst", "0.0027", "off_peak"),
            ("worst", "0.0027", "off_peak"), ("worst", "0.0027", "off_peak"), ("usage", "0.0004206", "peak")]
    have = [(s["basis"], s["usd"], s["window"]) for s in (s_use, s_bytes, s_torn, s_shape, s_none, s_peak)]
    check("BUD-SETTLE-1: the fact replaces the reservation - usage at the window's price (reasoning 50 written "
          "apart), a stream that ended with [DONE] and no usage by its bytes (1000 in x miss + 100 out x output), a "
          "broken stream, a usage in another shape or no answer at the worst; a request from 00:59:30 to 01:00:30 at "
          "the peak",
          all(h[0] == w[0] and Decimal(h[1]) == Decimal(w[1]) and h[2] == w[2] for h, w in zip(have, want))
          and s_use["reasoning_tokens"] == 50 and s_torn["reason"] == "incomplete"
          and s_shape["reason"] == "usage-shape" and s_none["reason"] == "incomplete"
          and tot == sum(Decimal(w[1]) for w in want) and calls == 6 and open_ == 0,
          f"{have} total {tot} calls {calls} open {open_}")

    print("\n- reservations -")
    led = fresh()
    spent(led, "old", "18.9973")
    L = B.Ledger(led, clock=Clock())
    why, msg = stopped(lambda: L.reserve(arm="a", role="write", run="new", unit="u", body=body(2000)))
    stop = lines(led)[-1]
    led2 = fresh()
    spent(led2, "old", "18.9972")
    L2 = B.Ledger(led2, clock=Clock())
    ok = L2.reserve(arm="a", role="write", run="new", unit="u", body=body(2000))
    check("BUD-RES-1 (R-BUDGET-20 (2)): a reservation whose worst brings the total to $19.00 exactly is refused by "
          "name with a stop line (18.9973 + 0.0027); at 18.9972 it is reserved (total 18.9999) - the line is $19.00, "
          "not $20",
          B.STOP_USD == Decimal("19.00") and why == "total" and stop["kind"] == "stop" and stop["why"] == "total"
          and Decimal(stop["total_usd"]) == Decimal("18.9973") and Decimal(stop["worst_usd"]) == Decimal("0.0027")
          and ok.worst == Decimal("0.0027") and L2.total() == Decimal("18.9999"),
          f"{why} {msg} {stop} {L2.total()}")
    led = fresh()
    spent(led, "p1", "1.4973", ceiling_usd=Decimal("1.5"))
    L = B.Ledger(led, clock=Clock())
    ceil = Decimal("1.5")
    w1 = stopped(lambda: L.reserve(arm="a", role="write", run="p1", unit="u", body=body(2000), ceiling_usd=ceil))
    w2 = stopped(lambda: L.reserve(arm="a", role="write", run="p2", unit="u", body=body(2000), ceiling_usd=ceil))
    w3 = stopped(lambda: L.reserve(arm="a", role="write", run="p1", unit="u", body=body(2000)))
    restarted = B.Ledger(led, clock=Clock())
    w4 = stopped(lambda: restarted.reserve(arm="other", role="read", run="p1", unit="other",
                                          body=body(2000), ceiling_usd=ceil))
    check("BUD-RES-2: a run's pinned $1.5 ceiling counts only that run; p1 at 1.4973 + 0.0027 refuses, p2 reserves; "
          "dropping p1's cap or restarting cannot reopen admission",
          w1[0] == "run-ceiling" and w2[0] is None and w3[0] == "run-ceiling" and w4[0] == "run-ceiling"
          and restarted.run_total("p1") == (Decimal("1.4973"), 1, 0)
          and restarted.run_total("p2") == (Decimal("0.0027"), 1, 1)
          and sum(row["kind"] == "stop" for row in lines(led)) == 1,
          f"{w1} {w2} {w3} {w4}")

    led = fresh()
    A = B.Ledger(led, clock=Clock())
    ra = A.reserve(arm="a", role="write", run="dead", unit="u", body=body())
    del A
    Bl = B.Ledger(led, clock=Clock())
    check("BUD-OPEN-1: a reservation a dead process never settled stays in the total and in its run's spend - another "
          "ledger on the file counts its worst ($0.4718592 + its bytes)",
          Bl.total() == ra.worst and Bl.run_total("dead") == (ra.worst, 1, 1)
          and ra.worst == B.worst_usd(len(body()), 393216, PRICE),
          f"{Bl.total()} {Bl.run_total('dead')} {ra.worst}")

    W = B.worst_usd(len(body()), 393216, PRICE)
    n = int(B.STOP_USD / W)
    n -= n * W >= B.STOP_USD
    led = fresh()
    A, C = B.Ledger(led, clock=Clock()), B.Ledger(led, clock=Clock())
    took, done = 0, [False, False]
    while not all(done) and took <= 3 * n:           # a bound: a ledger that never stops fails here, never hangs
        for i, x in enumerate((A, C)):
            if not done[i]:
                if stopped(lambda: x.reserve(arm="a", role="write", run="q", unit=None, body=body()))[0]:
                    done[i] = True
                else:
                    took += 1
    check(f"BUD-SEQ-1: two ledgers on one file taking turns read each other's lines before deciding - together they "
          f"reserve exactly {n} model-maximum requests (n x worst < $19), never one each past the line",
          took == n and n * W < B.STOP_USD <= (n + 1) * W and B.Ledger(led).broken() is None,
          f"took {took}, n {n}, W {W}")

    led = fresh()
    ledgers = [B.Ledger(led, clock=Clock()), B.Ledger(led, clock=Clock())]
    counts: list[int] = []
    gate = threading.Barrier(8)

    def worker(x) -> None:
        k = 0
        gate.wait()
        while k <= 3 * n and not stopped(lambda: x.reserve(arm="a", role="write", run="t", unit=None,
                                                             body=body()))[0]:
            k += 1
        counts.append(k)

    ths = [threading.Thread(target=worker, args=(ledgers[i % 2],)) for i in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(120)
    reader = B.Ledger(led)
    recs = lines(led)
    check(f"BUD-PAR-1: eight threads on two ledgers of one file reserve exactly {n} in all, the chain intact "
          f"(the check and the append are one step under the file's lock)",
          sum(counts) == n and len(counts) == 8 and reader.broken() is None
          and reader.run_total("t") == (n * W, n, n) and sum(r["kind"] == "reserve" for r in recs) == n
          and sum(r["kind"] == "stop" for r in recs) == 1
          and stopped(lambda: B.Ledger(led, clock=Clock()).reserve(arm="a", role="write", run="t",
              unit=None, body=body()))[0] == "total",
          f"{counts} {reader.broken()} {reader.run_total('t')}")

    print("\n- the ledger's integrity -")
    res = {}
    led = fresh()                                                           # torn
    spent(led, "r", "0.1")
    with open(led, "ab") as f:
        f.write(b'{"kind": "reserve"')
    res["torn"] = stopped(lambda: B.Ledger(led, clock=Clock()).reserve(arm="a", role="w", run="r", unit=None,
                                                                         body=body(10)))
    led = fresh()                                                           # an edited line
    spent(led, "r1", "0.1")
    raw = led.read_bytes()
    led.write_bytes(raw.replace(b'"run": "r1"', b'"run": "r2"', 1))
    res["chain"] = stopped(lambda: B.Ledger(led, clock=Clock()).reserve(arm="a", role="w", run="r", unit=None,
                                                                          body=body(10)))
    led = fresh()                                                           # shrunk after it was read
    spent(led, "r", "0.1")
    Ls = B.Ledger(led, clock=Clock())
    Ls.total()
    led.write_bytes(led.read_bytes().split(b"\n")[0] + b"\n")
    res["shrunk"] = stopped(lambda: Ls.reserve(arm="a", role="w", run="r", unit=None, body=body(10)))
    led = fresh()                                                           # a settle of no reservation
    forge(led, [{"kind": "settle", "id": "nobody", "usd": "0"}])
    res["record"] = stopped(lambda: B.Ledger(led, clock=Clock()).reserve(arm="a", role="w", run="r", unit=None,
                                                                           body=body(10)))
    led = fresh()                                                           # not JSON
    led.write_bytes(b"not json\n")
    Lu = B.Ledger(led, clock=Clock())
    res["unreadable"] = stopped(lambda: Lu.reserve(arm="a", role="w", run="r", unit=None, body=body(10)))
    before = len(led.read_bytes().split(b"\n"))
    late = B.Reservation("late", NOON, "r", 10, Decimal("0.5"))
    Lu.settle(late, status=200)
    after = len(led.read_bytes().split(b"\n"))
    want = {"torn": "ledger-torn: line 3", "chain": "ledger-chain: line 2", "shrunk": "ledger-shrunk",
            "record": "ledger-record: line 1", "unreadable": "ledger-unreadable: line 1"}
    check("BUD-BROKEN-1: a torn last line, an edited line (the chain), a file shorter than already read, a settle of "
          "no reservation and a line that is no JSON each refuse every reservation as ledger-broken, by name",
          all(res[k][0] == "ledger-broken" and want[k] in res[k][1] for k in want), str(res))
    check("BUD-BROKEN-2: on a broken ledger the refusal stays, and a settlement is still written - a spend is never "
          "dropped",
          stopped(lambda: Lu.reserve(arm="a", role="w", run="r", unit=None, body=body(10)))[0] == "ledger-broken"
          and after == before + 1, f"{before} -> {after}")

    print("\n- the balance (Q-BUD-3, Q-BUD-CUR) -")

    def bal(infos) -> bytes:
        return json.dumps({"is_available": True, "balance_infos": infos}).encode()

    usd = {"currency": "USD", "total_balance": "110.25", "granted_balance": "0.00", "topped_up_balance": "110.25"}
    cases = {"CNY only": bal([dict(usd, currency="CNY")]), "USD twice": bal([usd, usd]),
             "no infos": json.dumps({"is_available": True}).encode(), "no number": bal([dict(usd, total_balance="x")]),
             "no JSON": b"<html>"}
    def parsed(v: bytes) -> str:
        try:
            return f"parsed {B.parse_balance(v)}"
        except B.BalanceRefused as e:
            return str(e)

    refused = {k: parsed(v) for k, v in cases.items()}
    mixed = parsed(bal([usd, dict(usd, currency="CNY", total_balance="5")]))
    check("BUD-BAL-1: the balance is total_balance of the one USD entry, beside any other currency; an account in CNY "
          "(currency named), two USD entries, no balance_infos, no number or no JSON is refused",
          mixed == "parsed 110.25" and "currency CNY" in refused["CNY only"]
          and all(not v.startswith("parsed") for v in refused.values()), f"{mixed} {refused}")

    class Fake:
        def __init__(self, reads: list[str]) -> None:
            self.reads, self.t, self.slept = [Decimal(x) for x in reads], 0.0, []

        def read(self) -> Decimal:
            return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0] + Decimal(self.t)

        def sleep(self, s: float) -> None:
            self.slept.append(s)
            self.t += s

    f1 = Fake(["10.00", "9.50", "9.50", "9.40"])
    got1 = B.stable_balance(f1.read, sleep=f1.sleep, clock=lambda: f1.t)
    f2 = Fake(["1"])
    try:
        got2 = B.stable_balance(f2.read, sleep=f2.sleep, clock=lambda: f2.t)
    except B.BalanceRefused as e:
        got2 = str(e)
    check("BUD-BAL-2: the balance after a run is read every 60 s until two equal reads (10.00, 9.50, 9.50 -> 9.50 in "
          "3 reads); one that never settles is refused after 10 minutes (11 reads)",
          got1 == (Decimal("9.50"), 3) and f1.slept == [60.0, 60.0]
          and isinstance(got2, str) and "11 reads" in got2 and f2.t == 600.0, f"{got1} {got2} {f2.t}")
    D = Decimal
    rec_cases = {("1.00", "0.90"): "ok", ("1.00", "0.8999"): "stop:ledger-over", ("1.00", "1.10"): "ok",
                 ("1.00", "1.1001"): "stop:balance-over", ("0.1999", "5"): "record-only",
                 ("0.20", "0"): "stop:ledger-over"}
    got = {k: B.reconcile(D(k[0]), D("10"), D("10") - D(k[1]))["verdict"] for k in rec_cases}
    check("BUD-REC-1 (Q-BUD-3 a): the balance's move within [0.9, 1.1] of the ledger's spend is ok; below is "
          "stop:ledger-over, above stop:balance-over (the owner is asked); under $0.20 of spend only recorded",
          got == rec_cases, str(got))
    print("\n- reviewed budget safety regressions -")

    def row(name, fn):
        try:
            result = fn()
            check(name, result is True, repr(result))
        except Exception as e:  # noqa: BLE001 - a malformed input must fail by the declared refusal
            check(name, False, f"{type(e).__name__}: {e}")

    def reserve_on(ledger):
        return ledger.reserve(arm="a", role="write", run="r1", unit=None, body=body(2000))

    bad_money = ("-1", "NaN", "sNaN", "Infinity", "-Infinity", "1e1000000000", "1e-1000000000",
                 True, None, [], {})

    def ledger_numbers():
        for kind in ("reserve", "settle"):
            for value in bad_money:
                p = fresh()
                l = B.Ledger(p, clock=Clock())
                r = reserve_on(l)
                rec = lines(p)[0]
                if kind == "reserve":
                    p.unlink()
                    rec["worst_usd"] = value
                    forge(p, [rec])
                else:
                    forge(p, [{"kind": "settle", "id": r.id, "usd": value}])
                if stopped(lambda: reserve_on(B.Ledger(p, clock=Clock())))[0] != "ledger-broken":
                    return False
        return True

    row("BUD-LEDGER-NUM-1: negative, nonfinite and malformed ledger money refuses admission", ledger_numbers)

    def authoritative_reserve():
        p = fresh()
        a = B.Ledger(p, clock=Clock(ts(*THU, 2)))
        r = reserve_on(a)
        original = r.worst
        r.worst, r.in_bytes, r.t0, r.run = Decimal(0), 0, NOON, "borrowed"
        other = B.Ledger(p, clock=Clock(ts(*THU, 2)))
        rec = other.settle(r, status=None)
        if Decimal(rec["usd"]) != original or other.run_total("r1") != (original, 1, 0):
            return False
        r2 = reserve_on(other)
        forged = B.Reservation(r2.id, NOON, "borrowed", 0, Decimal(0))
        rec2 = B.Ledger(p, clock=Clock(ts(*THU, 2))).settle(forged, status=200, done=True, out_bytes=100)
        return rec2["window"] == "peak" and Decimal(rec2["usd"]) == Decimal("0.00042")

    row("BUD-RES-AUTH-1: persisted reservation, not mutable or fabricated caller fields, determines settlement", authoritative_reserve)

    def duplicate_settle():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        l.settle(r, status=None)
        raw = p.read_bytes()
        unknown = B.Reservation("unknown", NOON, "r", 0, Decimal(0))
        outcomes = [stopped(lambda: l.settle(handle, status=None))[0] for handle in (r, unknown)]
        return outcomes == ["reservation-unknown"] * 2 and p.read_bytes() == raw and l.broken() is None

    row("BUD-ID-1: duplicate and unknown settlement refuse before writing, preserving the ledger", duplicate_settle)

    def concurrent_settle():
        p = fresh()
        a, c = B.Ledger(p, clock=Clock()), B.Ledger(p, clock=Clock())
        r = reserve_on(a)
        gate = threading.Barrier(8)
        results = []
        def one(l):
            gate.wait()
            try:
                results.append(stopped(lambda: l.settle(r, status=None))[0])
            except Exception as e:
                results.append(type(e).__name__)
        threads = [threading.Thread(target=one, args=(a if i % 2 else c,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        reader = B.Ledger(p)
        return results.count(None) == 1 and results.count("reservation-unknown") == 7 and reader.broken() is None \
            and sum(x["kind"] == "settle" for x in lines(p)) == 1 and reader.total() == r.worst

    row("BUD-ID-PAR-1: concurrent settlement applies exactly once across ledger instances", concurrent_settle)

    def read_edit():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        reserve_on(l)
        l.total()
        p.write_bytes(p.read_bytes().replace(b'"run": "r1"', b'"run": "r2"'))
        return stopped(lambda: reserve_on(l))[0] == "ledger-broken"

    row("BUD-READ-EDIT-1: editing a consumed same-size prefix refuses current reader", read_edit)

    def read_missing():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        reserve_on(l)
        l.total()
        p.unlink()
        return stopped(lambda: reserve_on(l))[0] == "ledger-broken" and not p.exists()

    row("BUD-READ-MISSING-1: disappearance of a consumed ledger refuses without recreating it", read_missing)

    def tail_edit():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        settled = l.settle(r, status=None)
        old = ('"usd": "' + settled["usd"] + '"').encode()
        raw = p.read_bytes()
        assert old in raw
        p.write_bytes(raw.replace(old, ('"usd": "' + "0" * len(settled["usd"]) + '"').encode()))
        return stopped(lambda: reserve_on(B.Ledger(p, clock=Clock())))[0] == "ledger-broken"

    row("BUD-TAIL-EDIT-1: a fresh reader detects settlement tail edits through its durable checkpoint", tail_edit)

    def settlement_bound():
        p = fresh()
        spent(p, "old", "18.9972")
        l = B.Ledger(p, clock=Clock(ts(*THU, 2)))
        r = reserve_on(l)
        rec = l.settle(r, status=200, done=True, out_bytes=8000)
        return Decimal(rec["usd"]) == r.worst and Decimal(rec.get("raw_estimate_usd", 0)) == Decimal("0.0099") \
            and l.total() == Decimal("18.9999") and stopped(lambda: reserve_on(l))[0] == "total"

    row("BUD-SETTLE-BOUND-1: byte settlement cannot exceed the persisted token upper bound; raw estimate retained", settlement_bound)

    def usage_bound():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        rec = l.settle(r, status=200, done=True, usage=use)
        return rec.get("anomaly") is True and rec.get("reason") == "usage-bound" and Decimal(rec["usd"]) >= max(r.worst, B.usage_usd(use, "off_peak", PRICE)[0]) \
            and stopped(lambda: reserve_on(B.Ledger(p, clock=Clock())))[0] == "accounting-anomaly"

    row("BUD-USAGE-BOUND-1: usage outside request bounds records the anomaly and stops further admission", usage_bound)

    def price_numbers():
        bads = []
        for value in bad_money:
            p = copy.deepcopy(PRICE)
            p["output"]["peak"] = value
            bads.append((p, copy.deepcopy(LIMITS)))
        for field in ("per_tokens", "max_output"):
            for value in (0, -1, True, "100", float("nan"), float("inf")):
                p, lim = copy.deepcopy(PRICE), copy.deepcopy(LIMITS)
                (p if field == "per_tokens" else lim)[field] = value
                bads.append((p, lim))
        p = copy.deepcopy(PRICE)
        p["output"]["off_peak"] = 5
        bads.append((p, copy.deepcopy(LIMITS)))
        p = copy.deepcopy(PRICE)
        p["input_cache_hit"]["peak"] = 5
        bads.append((p, copy.deepcopy(LIMITS)))
        return all(stopped(lambda pair=pair: B.Ledger(fresh(), prices=pair))[0] == "price-unknown" for pair in bads)

    row("BUD-PRICE-NUM-1: invalid divisors, model bounds and rates refuse; peak/miss must bound alternatives", price_numbers)

    def price_alias():
        p, lim = copy.deepcopy(PRICE), copy.deepcopy(LIMITS)
        l = B.Ledger(fresh(), prices=(p, lim), clock=Clock())
        p["output"]["peak"], lim["max_output"] = 0, 1
        r = reserve_on(l)
        try:
            l.price["output"]["peak"] = 0
            return False
        except TypeError:
            return r.worst == Decimal("0.0027")

    row("BUD-PRICE-ALIAS-1: caller and nested public price mutation cannot change admission rates", price_alias)

    def ceiling_numbers():
        l = B.Ledger(fresh(), clock=Clock())
        return all(stopped(lambda v=v: l.reserve(arm="a", role="w", run="r", unit=None, body=body(2000),
                                                ceiling_usd=v))[0] == "run-ceiling-invalid" for v in bad_money if v is not None)

    row("BUD-CEILING-NUM-1: invalid run ceilings refuse by name before reserving", ceiling_numbers)

    def byte_numbers():
        for value in (-1, True, "100", 0.5, float("nan"), float("inf")):
            l = B.Ledger(fresh(), clock=Clock())
            r = reserve_on(l)
            rec = l.settle(r, status=200, done=True, out_bytes=value)
            if rec["basis"] != "worst" or Decimal(rec["usd"]) != r.worst:
                return False
        return True

    row("BUD-BYTES-NUM-1: invalid output byte counters retain the original worst reservation", byte_numbers)

    def balance_numbers():
        for value in bad_money:
            raw = json.dumps({"balance_infos": [{"currency": "USD", "total_balance": value}]}).encode()
            try:
                B.parse_balance(raw)
                return False
            except B.BalanceRefused:
                pass
        for value in (Decimal("Infinity"), Decimal("NaN"), Decimal(-1)):
            try:
                B.stable_balance(lambda: value, sleep=lambda _: None, clock=lambda: 0)
                return False
            except B.BalanceRefused:
                pass
        return True

    row("BUD-BAL-NUM-1: negative/nonfinite balances never become stable witnesses", balance_numbers)

    def reconcile_numbers():
        for idx in range(3):
            for value in bad_money:
                args = [Decimal(1), Decimal(10), Decimal(9)]
                args[idx] = value
                try:
                    B.reconcile(*args)
                    return False
                except B.BalanceRefused:
                    pass
        return True

    row("BUD-REC-NUM-1: malformed reconciliation inputs never yield a successful verdict", reconcile_numbers)

    def empty_anchor():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        l.settle(r, status=None)
        checkpoint = p.with_suffix(p.suffix + ".sha256")
        saved = checkpoint.read_bytes()
        p.write_bytes(b"")
        why = stopped(lambda: reserve_on(B.Ledger(p, clock=Clock())))[0]
        return why == "ledger-broken" and p.read_bytes() == b"" and checkpoint.read_bytes() == saved

    row("BUD-EMPTY-ANCHOR-1: a truncated empty ledger cannot reset spend on a fresh reader", empty_anchor)

    def checkpoint_crash():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        l.settle(r, status=None)
        with mock.patch.object(B.os, "replace", side_effect=PermissionError("checkpoint crash")):
            refused = stopped(lambda: reserve_on(l))[0]
        reader = B.Ledger(p, clock=Clock())
        return refused == "ledger-broken" and stopped(reader.total)[0] == "ledger-broken" \
            and stopped(lambda: reserve_on(reader))[0] == "ledger-broken"

    row("BUD-CHECKPOINT-CRASH-1: interrupted checkpoint write refuses current and restarted admission", checkpoint_crash)

    def process_reserve():
        p = fresh()
        start = p.parent / "start"
        code = "\n".join([
            "import importlib.util,json,sys,time",
            "from pathlib import Path",
            "root,p,gate,ready=map(Path,sys.argv[1:])",
            "spec=importlib.util.spec_from_file_location('budget_process_test',root/'research/v3/budget.py')",
            "b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)",
            f"l=b.Ledger(p,clock=lambda:{NOON!r})",
            f"body={body()!r}",
            "ready.write_bytes(b'ready')",
            "deadline=time.monotonic()+20",
            "while not gate.exists():",
            " if time.monotonic()>deadline: raise RuntimeError('start barrier timed out')",
            " time.sleep(.01)",
            "count=0",
            "while count<120:",
            " try: l.reserve(arm='a',role='write',run='proc',unit=None,body=body)",
            " except b.BudgetStop as e:",
            "  if e.why!='total': raise",
            "  break",
            " count+=1",
            "print(json.dumps({'count':count}))",
        ])
        children = []
        outputs = []
        try:
            ready_paths = [p.parent / f"ready-{i}" for i in range(2)]
            for ready in ready_paths:
                children.append(subprocess.Popen([sys.executable, "-B", "-c", code, str(ROOT), str(p), str(start), str(ready)],
                                                 cwd=TMP, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            deadline = time.monotonic() + 15
            while not all(ready.exists() for ready in ready_paths):
                if time.monotonic() > deadline or any(child.poll() is not None for child in children):
                    raise RuntimeError("ledger writers did not reach the start barrier")
                time.sleep(0.01)
            start.write_bytes(b"go")
            for child in children:
                out, err = child.communicate(timeout=30)
                if child.returncode:
                    raise RuntimeError(err.decode("utf-8", errors="replace")[:500])
                outputs.append(json.loads(out))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)
        reader = B.Ledger(p)
        return sum(x["count"] for x in outputs) == n and reader.broken() is None \
            and reader.run_total("proc") == (n * W, n, n)

    row("BUD-PROC-1: two independent processes share the reservation limit and a valid ledger", process_reserve)

    def unsafe_usage():
        sentinel = "BUD_USAGE_SENTINEL_OWNED_DO_NOT_PERSIST"
        known_bad = [dict(settle_use, completion_tokens=number)
                     for number in (float("nan"), float("inf"), Decimal("NaN"), True, -1)]
        unknown_extra = [dict(settle_use, extra={"text": sentinel, "unsafe": item})
                         for item in (float("nan"), Decimal(1), object())]
        unknown_extra.append(dict(settle_use, completion_tokens_details={"reasoning_tokens": 50, "text": sentinel}))
        for usage, malformed in [(item, True) for item in known_bad] + [(item, False) for item in unknown_extra]:
            path = fresh()
            ledger = B.Ledger(path, clock=Clock())
            held = reserve_on(ledger)
            rec = ledger.settle(held, status=200, done=True, usage=usage)
            if malformed:
                if (rec["basis"] != "worst" or Decimal(rec["usd"]) != held.worst
                        or rec.get("usage_error") != "usage-shape" or rec.get("reason") != "usage-shape"
                        or rec.get("anomaly") is not True or ledger.run_total("r1") != (held.worst, 1, 0)):
                    return False
                if stopped(lambda: reserve_on(B.Ledger(path, clock=Clock())))[0] != "accounting-anomaly":
                    return False
            else:
                expected, reasoning = B.usage_usd(settle_use, "off_peak", PRICE)
                if (rec["basis"] != "usage" or Decimal(rec["usd"]) != expected or rec["usage"] != settle_use
                        or rec["reasoning_tokens"] != reasoning or ledger.run_total("r1") != (expected, 1, 0)):
                    return False
                reserve_on(B.Ledger(path, clock=Clock()))
            if any(sentinel.encode() in file.read_bytes() for file in path.parent.iterdir() if file.is_file()):
                return False
        return True

    row("BUD-USAGE-NUM-1: invalid known numeric usage retains worst and halts; arbitrary extras are discarded without losing valid usage", unsafe_usage)

    def body_bytes():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        raw = json.dumps({"messages": [{"role": "user", "content": "Привет 🌍"}], "max_tokens": 1}, ensure_ascii=False)
        why = stopped(lambda: l.reserve(arm="a", role="w", run="r", unit=None, body=raw))[0]
        r = l.reserve(arm="a", role="w", run="r", unit=None, body=raw.encode("utf-8"))
        return why == "body-not-bytes" and r.in_bytes == len(raw.encode("utf-8")) > len(raw)

    row("BUD-BODY-BYTES-1: reserve requires the exact UTF-8 wire bytes, never Unicode character counts", body_bytes)

    def duplicate_max():
        cases = [(b'{"max_tokens":500000,"max_tokens":1}', 500000),
                 (b'{"max_tokens":1,"max_tokens":500000}', 500000),
                 (b'{"max_tokens":null,"max_completion_tokens":1}', 393216)]
        return all(B.request_max_tokens(raw, LIMITS)[0] == bound for raw, bound in cases)

    row("BUD-MAX-DUP-1: duplicate output bounds use their largest value; invalid fields retain model maximum", duplicate_max)

    def atomic_malformed():
        p = fresh()
        l = B.Ledger(p, clock=Clock())
        r = reserve_on(l)
        forge(p, [{"kind": "settle", "id": r.id, "usd": "x"}])
        return stopped(l.total)[0] == "ledger-broken" and stopped(lambda: l.run_total("r1"))[0] == "ledger-broken"

    row("BUD-LEDGER-ATOMIC-1: malformed settlement cannot expose a partial authoritative total", atomic_malformed)

# Deferred controls to insert into _test_v3_budget.py's existing owned-temp scope.
# NOT applied or executed. Existing names B, Clock, body, fresh, stopped, spent,
# row, Decimal are supplied by that suite. Landing follows C6b, before BUD2.

    def ceiling_is_pinned_before_any_halt():
        for first_cap in (None, Decimal("0.01")):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit="u1", body=body(1), ceiling_usd=first_cap)
            ledger.settle(held, status=200, done=True, usage={"prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0})
            retries = []
            alternatives = (Decimal("0.01"),) if first_cap is None else (None, Decimal("0.02"), Decimal("0.005"))
            for instance in (ledger, B.Ledger(p, clock=Clock())):
                for cap in alternatives:
                    retries.append(stopped(lambda: instance.reserve(arm="a", role="read", run="probe/r1",
                        unit="FORGED_DIFFERENT_UNIT", body=body(1), ceiling_usd=cap))[0])
            if retries != ["run-ceiling-changed"] * (len(alternatives) * 2):
                return False
            same = B.Ledger(p, clock=Clock()).reserve(arm="a", role="read", run="probe/r1", unit=None,
                body=body(1), ceiling_usd=first_cap)
            other = B.Ledger(p, clock=Clock()).reserve(arm="a", role="write", run="another-stand/r1", unit=None,
                body=body(1), ceiling_usd=Decimal("0.02"))
            if same.id == held.id or other.id in {held.id, same.id}:
                return False
        return True

    row("BUD-CAP-1: the first reservation pins optional ceiling across settlement/restart/role/unit changes, before any halt", ceiling_is_pinned_before_any_halt)

    def changed_cap_chained_record_refuses_replay():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1), ceiling_usd=Decimal("0.01"))
        import uuid
        forge(p, [{"kind": "reserve", "id": uuid.uuid4().hex, "t0": B._iso(NOON), "arm": "a", "role": "read",
                   "run": "probe/r1", "unit": "u2", "in_bytes": 1000, "max_tokens": 1, "max_tokens_source": "max_tokens",
                   "worst_usd": str(held.worst), "price_sha": B.price_sha(PRICE), "run_ceiling_usd": "0.02"}])
        restarted = B.Ledger(p, clock=Clock())
        return "ledger-record" in (restarted.broken() or "") and held.id in restarted._open \
            and stopped(restarted.total)[0] == "ledger-broken"

    row("BUD-CAP-REPLAY-1: replay rejects a later validly chained reserve changing the pinned cap", changed_cap_chained_record_refuses_replay)

    def halt_run_survives_restart():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        ceiling = Decimal("0.002")
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit="u1", body=body(1000), ceiling_usd=ceiling)
        first = stopped(lambda: ledger.reserve(arm="a", role="read", run="probe/r1", unit="u2", body=body(2000), ceiling_usd=ceiling))[0]
        ledger.settle(held, status=200, done=True, usage={"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
            "prompt_tokens": 0, "completion_tokens": 0})
        retries = []
        for instance in (ledger, B.Ledger(p, clock=Clock())):
            for retry_ceiling in (None, ceiling, Decimal("2")):
                retries.append(stopped(lambda: instance.reserve(arm="a", role="write", run="probe/r1", unit=None,
                    body=body(1), ceiling_usd=retry_ceiling))[0])
        other = stopped(lambda: B.Ledger(p, clock=Clock()).reserve(arm="a", role="write", run="other-probe/r1", unit=None,
            body=body(1), ceiling_usd=Decimal("1.5")))[0]
        return first == "run-ceiling" and retries == ["run-ceiling"] * 6 and other is None

    row("BUD-HALT-RUN-1: stopped run stays stopped after settlement/restart/omitted-or-raised ceiling; another stand scope is independent", halt_run_survives_restart)

    def halt_total_survives_discount():
        p = fresh()
        spent(p, "historical", "18.5")
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit="u1", body=body(300000))
        first = stopped(lambda: ledger.reserve(arm="a", role="write", run="probe/r2", unit="u2", body=body()))[0]
        ledger.settle(held, status=200, done=True, usage={"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
            "prompt_tokens": 0, "completion_tokens": 0})
        retries = [stopped(lambda: instance.reserve(arm="a", role="read", run="another-stand/r1", unit=None,
            body=body(1)))[0] for instance in (ledger, B.Ledger(p, clock=Clock()))]
        return first == "total" and ledger.total() == Decimal("18.5") and retries == ["total", "total"]

    row("BUD-HALT-TOTAL-1: first global refusal remains durable even after outstanding reserves settle below the limit", halt_total_survives_discount)

    def provisional_usage_retains_worst():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
        usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10,
            "prompt_tokens": 10, "completion_tokens": 1}
        rec = ledger.settle(held, status=200, usage=usage, done=False)
        return rec["basis"] == "worst" and Decimal(rec["usd"]) == held.worst and rec["reason"] == "incomplete" \
            and ledger.run_total("probe/r1") == (held.worst, 1, 0)

    row("BUD-PROVISIONAL-1: valid early usage followed by truncation cannot discount the persisted reserve", provisional_usage_retains_worst)

    def force_worst_outcomes_override_done_and_usage():
        usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": 1}
        for outcome in ("incomplete", "abandoned", "no-response", "io-error", "parse-limit", "parse-error"):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
            rec = ledger.settle(held, status=200, done=True, usage=usage, outcome=outcome)
            if rec["basis"] != "worst" or Decimal(rec["usd"]) != held.worst or rec["reason"] != outcome \
                    or B.Ledger(p, clock=Clock()).run_total("probe/r1") != (held.worst, 1, 0):
                return False
        return True

    row("BUD-OUTCOME-WORST-1: every accepted incomplete/error outcome overrides DONE plus cheap valid usage", force_worst_outcomes_override_done_and_usage)

    def usage_has_closed_numeric_projection():
        sentinel = "CREDENTIAL_SENTINEL_OWNED_DO_NOT_PERSIST"
        for malformed in (False, True):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
            usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10,
                "prompt_tokens": 10, "completion_tokens": sentinel if malformed else 1,
                "completion_tokens_details": {"reasoning_tokens": 0, "extra": sentinel},
                "extra": {"nested": sentinel}, "credentials": sentinel}
            rec = ledger.settle(held, status=200, done=True, usage=usage)
            if any(sentinel.encode() in f.read_bytes() for f in p.parent.iterdir() if f.is_file()):
                return False
            if malformed:
                if rec["basis"] != "worst" or Decimal(rec["usd"]) != held.worst or rec.get("usage_error") != "usage-shape":
                    return False
            else:
                expected = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10,
                    "completion_tokens": 1, "completion_tokens_details": {"reasoning_tokens": 0}}
                if rec["basis"] != "usage" or rec["usage"] != expected:
                    return False
        return True

    row("BUD-PRIVACY-1: valid and malformed usage retain only closed numeric fields; nested/text sentinels reach no durable file", usage_has_closed_numeric_projection)

    def accounting_anomaly_is_typed_and_durable():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
        usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": 1}
        rec = ledger.settle(held, status=200, done=True, usage=usage, outcome="model-mismatch")
        restarted = B.Ledger(p, clock=Clock())
        retry = stopped(lambda: restarted.reserve(arm="a", role="write", run="different/r1", unit=None,
            body=body(1)))[0]
        totals = [stopped(instance.total)[0] for instance in (ledger, restarted)]
        run_totals = [stopped(lambda: instance.run_total("probe/r1"))[0] for instance in (ledger, restarted)]
        duplicate = stopped(lambda: restarted.settle(held, status=200, done=True, usage=usage))[0]
        return rec["kind"] == "accounting-unverified" and rec["basis"] == "unverified" and "usd" not in rec \
            and Decimal(rec["worst_usd"]) == held.worst and "raw_estimate_usd" not in rec \
            and rec["reason"] == "model-mismatch" and rec["anomaly"] is True and retry == "accounting-anomaly" \
            and totals == ["accounting-unverified"] * 2 and run_totals == ["accounting-unverified"] * 2 \
            and duplicate == "settlement-unverified" and held.id in restarted._open

    row("BUD-ANOMALY-1: model mismatch retains the reserve, halts admission and refuses authoritative cost across restart", accounting_anomaly_is_typed_and_durable)

    def unresolved_cost_cannot_release_from_later_chained_settle():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
        ledger.settle(held, status=200, done=True, outcome="model-mismatch")
        forge(p, [{"kind": "settle", "id": held.id, "t1": B._iso(NOON), "status": 200, "window": "off_peak",
                   "usage": None, "reasoning_tokens": None, "basis": "worst", "usd": str(held.worst),
                   "price_sha": B.price_sha(PRICE)}])
        restarted = B.Ledger(p, clock=Clock())
        return "ledger-record" in (restarted.broken() or "") and held.id in restarted._open \
            and stopped(restarted.total)[0] == "ledger-broken"

    row("BUD-UNVERIFIED-REPLAY-1: replay refuses a later chained settlement releasing an unresolved model reservation", unresolved_cost_cannot_release_from_later_chained_settle)

    def shape_and_conflict_anomalies_globally_halt():
        good_usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": 1}
        cases = [(dict(usage={"completion_tokens": "OWNED_SHAPE_SENTINEL"}), "usage-shape"),
                 (dict(usage=good_usage, outcome="usage-shape"), "usage-shape"),
                 (dict(usage=good_usage, outcome="usage-conflict"), "usage-conflict")]
        for options, reason in cases:
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
            rec = ledger.settle(held, status=200, done=True, **options)
            restarted = B.Ledger(p, clock=Clock())
            why = stopped(lambda: restarted.reserve(arm="a", role="write", run="unrelated/r1", unit=None, body=body(1)))[0]
            if rec["reason"] != reason or rec["basis"] != "worst" or Decimal(rec["usd"]) != held.worst \
                    or rec["anomaly"] is not True or why != "accounting-anomaly":
                return False
        return True

    row("BUD-ANOMALY-TYPES-1: malformed or typed shape/conflict accounting halts unrelated admission across restart", shape_and_conflict_anomalies_globally_halt)

    def known_tariff_anomaly_cannot_understate_usage():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
        usage = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": 2000}
        rec = ledger.settle(held, status=200, done=True, usage=usage)
        expected = Decimal("0.0012015")  # 10 * 0.15/1M + 2000 * 0.60/1M, independent of production helper.
        restarted = B.Ledger(p, clock=Clock())
        retry = stopped(lambda: restarted.reserve(arm="a", role="read", run="other/r1", unit=None, body=body(1)))[0]
        return held.worst == Decimal("0.0003012") and rec["basis"] == "conservative" and Decimal(rec["usd"]) == expected \
            and Decimal(rec["raw_estimate_usd"]) == expected and rec["reason"] == "usage-bound" and rec["anomaly"] is True \
            and rec["price_sha"] == B.price_sha(PRICE) and restarted.total() == expected and retry == "accounting-anomaly"

    row("BUD-ANOMALY-COST-1: known-tariff bound anomaly records at least the reported priced usage, even above the reserve", known_tariff_anomaly_cannot_understate_usage)

    def explicit_bound_without_valid_usage_still_halts():
        for usage in (None, {"completion_tokens": "OWNED_MALFORMED_USAGE_SENTINEL"}):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            rec = ledger.settle(held, status=200, done=True, usage=usage, outcome="usage-bound")
            restarted = B.Ledger(p, clock=Clock())
            retry = stopped(lambda: restarted.reserve(arm="a", role="write", run="other/r1", unit=None, body=body(1)))[0]
            if rec["basis"] != "worst" or Decimal(rec["usd"]) != held.worst or rec["reason"] != "usage-bound" \
                    or rec["anomaly"] is not True or "raw_estimate_usd" in rec or restarted.total() != held.worst \
                    or retry != "accounting-anomaly":
                return False
            if any(b"OWNED_MALFORMED_USAGE_SENTINEL" in f.read_bytes() for f in p.parent.iterdir() if f.is_file()):
                return False
        return True

    row("BUD-ANOMALY-NONE-1: explicit usage-bound with absent/malformed usage settles conservatively without crashing and durably halts", explicit_bound_without_valid_usage_still_halts)

    def status_cannot_persist_arbitrary_text():
        sentinel = "STATUS_SENTINEL_OWNED_DO_NOT_PERSIST"
        for value in (sentinel, {"nested": sentinel}, True, 99, 600):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(2000))
            before = p.read_bytes()
            why = stopped(lambda: ledger.settle(held, status=value))[0]
            if why != "settlement-status-invalid" or p.read_bytes() != before or ledger.run_total("probe/r1") != (held.worst, 1, 1):
                return False
            if any(sentinel.encode() in f.read_bytes() for f in p.parent.iterdir() if f.is_file()):
                return False
        return True

    row("BUD-STATUS-1: invalid or textual status refuses before writing and cannot release a reservation", status_cannot_persist_arbitrary_text)

    def completion_and_outcome_are_closed_types():
        sentinel = "OUTCOME_SENTINEL_OWNED_DO_NOT_PERSIST"
        cases = [(dict(outcome=sentinel), "settlement-outcome-invalid"),
                 (dict(outcome={"text": sentinel}), "settlement-outcome-invalid"),
                 (dict(done=sentinel), "settlement-completion-invalid"),
                 (dict(done=1), "settlement-completion-invalid"),
                 (dict(done=False, outcome="complete"), "settlement-completion-invalid")]
        for options, expected in cases:
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            before = p.read_bytes()
            why = stopped(lambda: ledger.settle(held, status=200, **options))[0]
            if why != expected or p.read_bytes() != before or ledger.run_total("probe/r1") != (held.worst, 1, 1):
                return False
            if any(sentinel.encode() in f.read_bytes() for f in p.parent.iterdir() if f.is_file()):
                return False
        return True

    row("BUD-OUTCOME-TYPES-1: unknown outcomes, invalid completion flags and complete-without-DONE refuse before persistence", completion_and_outcome_are_closed_types)

    def invalid_forensic_status_cannot_write_or_release():
        sentinel = "FORENSIC_STATUS_SENTINEL_OWNED_DO_NOT_PERSIST"
        for value in (sentinel, {"text": sentinel}, True, 99, 600):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            with p.open("ab") as stream:
                stream.write(b"owned-torn-row")
            if ledger.broken() is None:
                return False
            before = p.read_bytes()
            why = stopped(lambda: ledger.settle(held, status=value))[0]
            if why != "settlement-status-invalid" or p.read_bytes() != before or held.id not in ledger._open:
                return False
            if any(sentinel.encode() in f.read_bytes() for f in p.parent.iterdir() if f.is_file()):
                return False
        return True

    row("BUD-FORENSIC-STATUS-1: broken-ledger status validation refuses text and invalid numbers before forensic append", invalid_forensic_status_cannot_write_or_release)

    def mismatch_survives_a_changed_tariff():
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
        changed = copy.deepcopy(PRICE)
        changed["output"]["peak"] *= 2
        other = B.Ledger(p, prices=(changed, copy.deepcopy(LIMITS)), clock=Clock())
        if other.price_sha == ledger.price_sha:
            return False
        rec = other.settle(held, status=200, done=True, usage=None, outcome="model-mismatch")
        if rec["kind"] != "accounting-unverified" or rec["basis"] != "unverified" \
                or rec["reason"] != "model-mismatch" or rec["price_sha"] != ledger.price_sha \
                or Decimal(rec["worst_usd"]) != held.worst or "usd" in rec:
            return False
        after = p.read_bytes()
        for prices in ((PRICE, LIMITS), (changed, LIMITS)):
            restarted = B.Ledger(p, prices=prices, clock=Clock())
            if restarted.broken() is not None or held.id not in restarted._open \
                    or stopped(restarted.total)[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.run_total("unrelated/r2"))[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit="u2", body=body(1)))[0] != "accounting-anomaly" \
                    or stopped(lambda: restarted.settle(held, status=200, done=True, out_bytes=0))[0] != "settlement-unverified" \
                    or p.read_bytes() != after:
                return False
        return len(lines(p)) == 2

    row("BUD-MISMATCH-PRICE-1: absent-usage model mismatch is durable before tariff comparison and blocks both tariff readers", mismatch_survives_a_changed_tariff)

    def oversized_usage_has_durable_unverified_cost():
        limit = (1 << 63) - 1  # Supported evidence bound, independent of the production constant.
        accepted = {"prompt_cache_hit_tokens": limit, "prompt_cache_miss_tokens": 0,
                    "prompt_tokens": limit, "completion_tokens": limit,
                    "completion_tokens_details": {"reasoning_tokens": limit}}
        if B.project_usage(accepted) != (accepted, None):
            return False
        huge = 10 ** 1500  # Valid JSON integer, below Python's parsing digit bound.
        examples = [
            {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": huge},
            {"prompt_cache_hit_tokens": huge, "prompt_cache_miss_tokens": 0, "prompt_tokens": huge, "completion_tokens": 0},
            {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": huge, "prompt_tokens": huge, "completion_tokens": 0},
            {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0, "prompt_tokens": huge, "completion_tokens": 0},
            {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0, "prompt_tokens": 0, "completion_tokens": 1,
             "completion_tokens_details": {"reasoning_tokens": huge}},
            dict(accepted, completion_tokens=limit + 1),
        ]
        for usage in examples:
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            rec = ledger.settle(held, status=200, done=True, usage=json.loads(json.dumps(usage)))
            if rec["kind"] != "accounting-unverified" or rec["basis"] != "unverified" \
                    or rec["reason"] != "usage-count-unverified" or rec["usage"] is not None \
                    or rec["price_sha"] != ledger.price_sha or Decimal(rec["worst_usd"]) != held.worst \
                    or "usd" in rec or "raw_estimate_usd" in rec:
                return False
            restarted = B.Ledger(p, clock=Clock())
            if restarted.broken() is not None or held.id not in restarted._open \
                    or stopped(restarted.total)[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.run_total("unrelated/r2"))[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit="u2", body=body(1)))[0] != "accounting-anomaly":
                return False
            after = p.read_bytes()
            if stopped(lambda: restarted.settle(held, status=200, done=True, out_bytes=0))[0] != "settlement-unverified" \
                    or p.read_bytes() != after or len(lines(p)) != 2 or str(huge).encode() in after:
                return False
            forge(p, [{"kind": "settle", "id": held.id, "usd": "0"}])
            if stopped(B.Ledger(p, clock=Clock()).total)[0] != "ledger-broken":
                return False
        return True

    row("BUD-USAGE-RANGE-1: numeric bound is inclusive; oversized parsed counts retain reserve and halt across restart without pricing/serialization", oversized_usage_has_durable_unverified_cost)


    def every_accounting_anomaly_survives_a_changed_tariff():
        valid = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 10, "prompt_tokens": 10, "completion_tokens": 0}
        cases = [(outcome, None, outcome) for outcome in ("usage-conflict", "usage-shape", "usage-bound")]
        cases += [("complete", {"completion_tokens": "OWNED_INVALID_USAGE_TEXT"}, "usage-shape"),
                  ("complete", dict(valid, completion_tokens=2), "usage-bound"),
                  ("complete", dict(valid, prompt_tokens=2000, prompt_cache_miss_tokens=2000), "usage-bound")]
        for outcome, usage, reason in cases:
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            changed = copy.deepcopy(PRICE)
            changed["output"]["peak"] *= 2
            rec = B.Ledger(p, prices=(changed, LIMITS), clock=Clock()).settle(
                held, status=200, done=True, usage=usage, outcome=outcome)
            if rec["kind"] != "accounting-unverified" or rec["basis"] != "unverified" \
                    or rec["reason"] != reason or rec["price_sha"] != ledger.price_sha \
                    or Decimal(rec["worst_usd"]) != held.worst or rec["usage"] is not None \
                    or "usd" in rec or b"OWNED_INVALID_USAGE_TEXT" in p.read_bytes():
                return False
            after = p.read_bytes()
            for prices in ((PRICE, LIMITS), (changed, LIMITS)):
                restarted = B.Ledger(p, prices=prices, clock=Clock())
                if restarted.broken() is not None or held.id not in restarted._open \
                        or stopped(restarted.total)[0] != "accounting-unverified" \
                        or stopped(lambda: restarted.run_total("unrelated/r2"))[0] != "accounting-unverified" \
                        or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit="u2", body=body(1)))[0] != "accounting-anomaly" \
                        or stopped(lambda: restarted.settle(held, status=200, done=True, out_bytes=0))[0] != "settlement-unverified" \
                        or p.read_bytes() != after:
                    return False
            forge(p, [{"kind": "settle", "id": held.id, "usd": "0"}])
            if stopped(B.Ledger(p, clock=Clock()).total)[0] != "ledger-broken":
                return False
        return True

    row("BUD-ANOMALY-PRICE-1: every explicit or inferred accounting anomaly halts both readers before changed-tariff pricing", every_accounting_anomaly_survives_a_changed_tariff)


    def oversized_request_bounds_have_a_durable_admission_stop():
        for request in (body(1 << 63, length=0), body(10 ** 1500, length=0),
                        b'{"model":"deepseek-flash","max_tokens":1' + b'0' * 5000 + b'}'):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            why = stopped(lambda: ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=request))[0]
            if why != "request-bound-unverified":
                return False
            recs = lines(p)
            if len(recs) != 1 or recs[0]["kind"] != "admission-stop" \
                    or recs[0]["why"] != "request-bound-unverified" or "worst_usd" in recs[0] \
                    or len(p.read_bytes()) > 1000:
                return False
            restarted = B.Ledger(p, clock=Clock())
            if restarted.broken() is not None or restarted.total() != Decimal(0) \
                    or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit=None, body=body(1)))[0] != "request-bound-unverified":
                return False
        limit = (1 << 63) - 1
        return B.request_max_tokens(body(limit, length=0), LIMITS) == (limit, "request")

    row("BUD-REQUEST-RANGE-1: oversized parsed and parse-limit JSON request integers persist a typed global admission stop without formatting an unknown worst", oversized_request_bounds_have_a_durable_admission_stop)

    def tariffs_and_divisors_fit_the_accounting_evidence_range():
        bads = []
        high = copy.deepcopy(PRICE)
        for field in ("input_cache_hit", "input_cache_miss"):
            high[field] = {"off_peak": 0, "peak": 0}
        high["output"] = {"off_peak": "1e1000", "peak": "1e1000"}
        bads.append((high, LIMITS))
        tiny = copy.deepcopy(PRICE)
        tiny["output"]["off_peak"] = "1e-1000"
        bads.append((tiny, LIMITS))
        huge_divisor = copy.deepcopy(PRICE)
        huge_divisor["per_tokens"] = 10 ** 1500
        bads.append((huge_divisor, LIMITS))
        bads.append((PRICE, dict(LIMITS, max_output=10 ** 1500)))
        bads.append((PRICE, dict(LIMITS, max_output=1 << 63)))
        zero = copy.deepcopy(PRICE)
        for field in ("input_cache_hit", "input_cache_miss", "output"):
            zero[field] = {"off_peak": 0, "peak": 0}
        zero["per_tokens"] = 1 << 63
        bads.append((zero, LIMITS))
        if any(stopped(lambda pair=pair: B.Ledger(fresh(), prices=pair, clock=Clock()))[0] != "price-unknown" for pair in bads):
            return False
        positive = B.Ledger(fresh(), prices=(PRICE, dict(LIMITS, max_output=(1 << 63) - 1)), clock=Clock())
        return positive.price_sha == B.price_sha(PRICE) and positive.broken() is None

    row("BUD-TARIFF-RANGE-1: reject tariffs whose unit or largest supported accounting amount is unrepresentable, plus oversized divisors/limits", tariffs_and_divisors_fit_the_accounting_evidence_range)

    def oversized_output_bytes_have_durable_unverified_cost():
        for count in (10 ** 1000010, (1 << 63)):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            rec = ledger.settle(held, status=200, done=True, usage=None, out_bytes=count)
            if rec["kind"] != "accounting-unverified" or rec["reason"] != "usage-count-unverified" \
                    or rec["basis"] != "unverified" or rec["price_sha"] != ledger.price_sha \
                    or Decimal(rec["worst_usd"]) != held.worst or "usd" in rec or "raw_estimate_usd" in rec \
                    or len(p.read_bytes()) > 2000:
                return False
            restarted = B.Ledger(p, clock=Clock())
            if restarted.broken() is not None or held.id not in restarted._open \
                    or stopped(restarted.total)[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit=None, body=body(1)))[0] != "accounting-anomaly":
                return False
        p = fresh()
        ledger = B.Ledger(p, clock=Clock())
        held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
        rec = ledger.settle(held, status=200, done=True, out_bytes=(1 << 63) - 1)
        return rec["basis"] == "bytes" and Decimal(rec["usd"]) == held.worst \
            and Decimal(rec["raw_estimate_usd"]) == Decimal("5534023222112.8656342")

    row("BUD-OUTPUT-RANGE-1: output byte evidence is bounded before Decimal arithmetic; overflow halts durably and the inclusive boundary remains conservative", oversized_output_bytes_have_durable_unverified_cost)


    def integer_subtypes_cannot_bypass_accounting_guards():
        class Counter(int):
            pass

        class DeceptiveCounter(int):
            def __gt__(self, other):
                return False

            def __lt__(self, other):
                return False

        for count in (Counter(10 ** 1000010), Counter(1), DeceptiveCounter(10 ** 1500)):
            p = fresh()
            ledger = B.Ledger(p, clock=Clock())
            held = ledger.reserve(arm="a", role="write", run="probe/r1", unit=None, body=body(1))
            rec = ledger.settle(held, status=200, done=True, out_bytes=count)
            restarted = B.Ledger(p, clock=Clock())
            if rec["kind"] != "accounting-unverified" or rec["reason"] != "usage-count-unverified" \
                    or Decimal(rec["worst_usd"]) != held.worst or "usd" in rec or "raw_estimate_usd" in rec \
                    or restarted.broken() is not None or held.id not in restarted._open \
                    or stopped(restarted.total)[0] != "accounting-unverified" \
                    or stopped(lambda: restarted.reserve(arm="other", role="read", run="unrelated/r2", unit=None, body=body(1)))[0] != "accounting-anomaly" \
                    or len(p.read_bytes()) > 2000:
                return False
        ordinary = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1, "prompt_tokens": 1, "completion_tokens": 1}
        for subtype in (Counter, DeceptiveCounter):
            for key in ordinary:
                if B.project_usage(dict(ordinary, **{key: subtype(1)})) != (None, "usage-shape"):
                    return False
            nested = dict(ordinary, completion_tokens_details={"reasoning_tokens": subtype(1)})
            if B.project_usage(nested) != (None, "usage-shape"):
                return False
            price = copy.deepcopy(PRICE)
            price["per_tokens"] = subtype(1000000)
            if stopped(lambda: B.Ledger(fresh(), prices=(price, LIMITS), clock=Clock()))[0] != "price-unknown":
                return False
        return True

    row("BUD-COUNTER-TYPES-1: plain and deceptive integer subclasses cannot enter pricing or evade numeric evidence guards", integer_subtypes_cannot_bypass_accounting_guards)


finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 budget: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
