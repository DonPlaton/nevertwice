"""Preflight the Windows dynamic TCP port budget before a campaign that will open
thousands of short connections to a local Ollama (R-v2-ports, plan by planner,
2026-09-24, A2).

`research/_ollama_pacer.py` (commit 1) paces and retries around the port-exhaustion
signature AFTER it happens. This module asks the question BEFORE a campaign starts: does
Windows have enough of its dynamic port range free right now to sustain the pace the
pacer is about to run at, or should the campaign wait for TIME_WAIT to drain, or refuse
outright and say what pace WOULD fit?

The arithmetic is the plan's own (see PLAN-R-V2-PORTS.md's "Design" section): at ~7
sockets held per Ollama call and Windows' default TIME_WAIT of 120s, a sustained rate of
`r` calls/s holds `840 * r` ports (`PORTS_PER_RATE_UNIT`). The pacer's own ceiling (8/s)
needs 6,720 of those; a reserve of 1,680 (2/s for the memory hook plus one other session
using the same machine) brings the total need to 8,400 (`NEED`).

    free = range - excluded_in_range - in_use_in_range
    free >= NEED                    -> go
    free + time_wait_in_range >= NEED -> wait (poll every 15s, up to 240s)
    else                             -> refuse (exit 3), printing the pace that WOULD fit:
                                        (free - RESERVE) / PORTS_PER_RATE_UNIT

Every number comes from three read-only OS queries, each behind its own overridable seam
(`_run_netsh_dynamicport`, `_run_netsh_excluded`, `_run_connections_csv`) so a hermetic
test can feed canned text - including a LOCALIZED sample: this machine's own `netsh`
answers in Russian, and its output is parsed by POSITION (the Nth `: NUMBER` field, a
bare `NUMBER  NUMBER` line) rather than by matching an English label, which is what makes
the English and Russian samples parse identically. `Get-NetTCPConnection`'s `State`
values are NOT localized (they are the underlying .NET enum's own names - `TimeWait`,
`Established`, ... - confirmed by running it on this machine), so that query is read by
its (ASCII, locale-independent) CSV column names instead.

Non-Windows: nothing here applies (the ephemeral range and TIME_WAIT behaviour differ),
so `measure()` returns `{"platform": "unchecked", ...}` and `preflight()`/`main()` treat
that exactly like "go" - the check does not block a platform it cannot ask.

    python research/_port_budget.py                       # measure, print, exit 0/3
    python research/_port_budget.py --wait 240 -- <cmd>    # preflight, then exec <cmd>
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
import time
from typing import Callable

#: 6,720 (the pacer's own 8/s ceiling) + 1,680 reserve (2/s for the memory hook plus one
#: other session sharing this machine) = 8,400 - the plan's own numbers, not re-derived
#: here.
BASE_NEED = 6_720
RESERVE = 1_680
NEED = BASE_NEED + RESERVE
#: Ports held per 1 call/s of SUSTAINED rate, over one TIME_WAIT window: ~7 sockets/call
#: x 120s (`PLAN-R-V2-PORTS.md`'s own derivation, "ports held = 840 r"). Used only to
#: print the pace that WOULD fit when refusing - never to gate anything itself.
PORTS_PER_RATE_UNIT = 840
POLL_INTERVAL_S = 15.0
MAX_WAIT_S = 240.0                                            # 2x the default TIME_WAIT

# ── overridable seams (a test never spawns netsh/PowerShell, never sleeps for real) ─────
_platform: str = sys.platform
_now: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


class ParseError(ValueError):
    """netsh/PowerShell answered with something this module cannot read."""


def _run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=30)
    return res.stdout or ""


def _run_netsh_dynamicport() -> str:
    return _run(["netsh", "int", "ipv4", "show", "dynamicport", "tcp"])


def _run_netsh_excluded() -> str:
    return _run(["netsh", "int", "ipv4", "show", "excludedportrange", "protocol=tcp"])


def _run_connections_csv() -> str:
    #: `-Property LocalPort,State`: .NET member names, never translated by the OS locale
    #: (verified on this machine's own ru-RU install - `Get-NetTCPConnection`'s `State`
    #: prints `TimeWait`/`Established`/... regardless). `ConvertTo-Csv` gives a parser no
    #: column-width or label-language guessing to do.
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
          "Get-NetTCPConnection -ErrorAction SilentlyContinue | "
          "Select-Object -Property LocalPort,State | ConvertTo-Csv -NoTypeInformation"]
    return _run(cmd)


# ── parsing: position-based, so English and a localized sample read identically ────────

#: Any `: NUMBER` field, in the order the two lines of `netsh ... show dynamicport tcp`
#: print them (Start Port, then Number of Ports) - true under both an English and this
#: machine's own Russian install ("Начальный порт : 49152" / "Число портов : 16384").
_COLON_NUMBER = re.compile(r":\s*(\d+)")


def parse_dynamicport(text: str) -> tuple[int, int]:
    """(start_port, number_of_ports) from `netsh int ipv4 show dynamicport tcp`."""
    nums = _COLON_NUMBER.findall(text)
    if len(nums) < 2:
        raise ParseError(f"expected two ': NUMBER' fields (start port, port count) in "
                         f"dynamicport output, found {len(nums)}: {text!r}")
    return int(nums[0]), int(nums[1])


#: A bare `NUMBER   NUMBER` line, optionally with a trailing `*` (Windows' own marker for
#: a "managed" exclusion) - every exclusion-range row, in EITHER language, is exactly this
#: shape; only the header/footer prose around it differs by locale, and this regex never
#: looks at that prose.
_RANGE_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s*\*?\s*$", re.M)


def parse_excluded(text: str) -> list[tuple[int, int]]:
    """[(start, end), ...] (inclusive) from `netsh int ipv4 show excludedportrange`."""
    return [(int(a), int(b)) for a, b in _RANGE_LINE.findall(text)]


def parse_connections_csv(text: str) -> list[tuple[int, str]]:
    """[(local_port, state), ...] from the `ConvertTo-Csv` output of `_run_connections_csv`."""
    out: list[tuple[int, str]] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            port = int((row.get("LocalPort") or "").strip())
        except (TypeError, ValueError):
            continue
        out.append((port, (row.get("State") or "").strip()))
    return out


def _excluded_ports_in_range(start: int, count: int, ranges: list[tuple[int, int]]) -> int:
    """How many DISTINCT ports inside `[start, start+count)` some exclusion range covers -
    a set, so two overlapping exclusion ranges (Windows does emit these) are never
    double-counted."""
    end = start + count
    covered: set = set()
    for a, b in ranges:
        lo, hi = (a, b) if a <= b else (b, a)
        lo, hi = max(lo, start), min(hi, end - 1)
        if lo <= hi:
            covered.update(range(lo, hi + 1))
    return len(covered)


# ── measure -> decide -> preflight ──────────────────────────────────────────────────────

def measure() -> dict:
    """One snapshot: the configured dynamic range, what Windows excludes from it, and
    what currently occupies it - split into ports that stay busy on the next check
    (anything but TIME_WAIT) and ports draining (TIME_WAIT, freed by Windows' default
    ~120s TcpTimedWaitDelay)."""
    if _platform != "win32":
        return {"platform": "unchecked", "free": None}
    start, count = parse_dynamicport(_run_netsh_dynamicport())
    excluded = parse_excluded(_run_netsh_excluded())
    conns = parse_connections_csv(_run_connections_csv())
    end = start + count
    excluded_in_range = _excluded_ports_in_range(start, count, excluded)
    in_use = time_wait = 0
    for port, state in conns:
        if start <= port < end:
            if state == "TimeWait":
                time_wait += 1
            else:
                in_use += 1
    free = count - excluded_in_range - in_use
    return {"platform": "win32", "start": start, "range": count,
            "excluded_in_range": excluded_in_range, "in_use_in_range": in_use,
            "time_wait_in_range": time_wait, "free": free}


def decide(m: dict) -> dict:
    """`m` (from `measure()`) plus a `decision` in {"unchecked", "go", "wait", "refuse"},
    and, only when refusing, `pace_that_would_fit` (calls/s, the plan's own formula)."""
    if m.get("platform") != "win32":
        return {**m, "decision": "unchecked"}
    free = m["free"]
    if free >= NEED:
        return {**m, "decision": "go"}
    if free + m["time_wait_in_range"] >= NEED:
        return {**m, "decision": "wait"}
    fit = max(0.0, (free - RESERVE) / PORTS_PER_RATE_UNIT)
    return {**m, "decision": "refuse", "pace_that_would_fit": round(fit, 3)}


def preflight(wait_s: float = MAX_WAIT_S) -> dict:
    """`decide(measure())`, and if that reads "wait", re-measure every `POLL_INTERVAL_S`
    (up to `wait_s`) for TIME_WAIT to drain `free` past `NEED`. Returns the FINAL
    decision: "go"/"unchecked" (proceed), or "refuse" (the wait budget ran out, or a
    re-measurement found the shortfall is not draining at all - real exclusions/in-use
    growth, not TIME_WAIT)."""
    m = measure()
    d = decide(m)
    if d["decision"] != "wait":
        return d
    start_t = _now()
    while _now() - start_t < wait_s:
        _sleep(POLL_INTERVAL_S)
        m = measure()
        d = decide(m)
        if d["decision"] in ("go", "refuse"):
            return d
    fit = max(0.0, (m["free"] - RESERVE) / PORTS_PER_RATE_UNIT)
    return {**m, "decision": "refuse", "pace_that_would_fit": round(fit, 3),
            "reason": f"waited up to {wait_s:.0f}s (polling every {POLL_INTERVAL_S:.0f}s) "
                     f"without draining to the {NEED} ports needed"}


def _report(d: dict) -> str:
    if d["decision"] == "unchecked":
        return f"port budget: unchecked ({_platform!r} is not Windows - this check does not apply)"
    if d["decision"] == "go":
        return (f"port budget: OK - {d['free']:,} of {d['range']:,} dynamic ports free "
               f"(need {NEED:,})")
    if d["decision"] == "refuse":
        extra = f" - {d['reason']}" if "reason" in d else ""
        return (f"port budget: REFUSED - only {d['free']:,} of {d['range']:,} dynamic ports "
               f"free (need {NEED:,}){extra}. A pace of "
               f"{d.get('pace_that_would_fit', 0):.2f} calls/s would fit today "
               f"(NEVERTWICE_OLLAMA_PACE_S={1 / max(d.get('pace_that_would_fit', 0), 1e-9):.3f} "
               f"if using it).")
    return f"port budget: {d}"                                    # "wait", surfaced mid-poll only


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wait", type=float, default=MAX_WAIT_S,
                    help=f"seconds to poll while draining (default {MAX_WAIT_S:.0f})")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="optional: -- <command...> to run once the budget allows")
    a = ap.parse_args(argv)
    child = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd

    d = preflight(a.wait)
    print(_report(d))
    if d["decision"] not in ("go", "unchecked"):
        return 3
    if child:
        return subprocess.run(child).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
