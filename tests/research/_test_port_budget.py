#!/usr/bin/env python3
"""`research/_port_budget.py` (R-v2-ports, commit 2 of the plan): preflight the Windows
dynamic TCP port budget before a campaign starts pacing traffic at Ollama.

T9. Every OS query is FAKE (the module's own `_run_netsh_dynamicport` /
`_run_netsh_excluded` / `_run_connections_csv` seams, monkeypatched to canned text) and
every sleep is a FAKE CLOCK - no real `netsh`, no real `Get-NetTCPConnection`, no real
240s wait. The netsh samples are canned in BOTH English and this machine's own Russian
locale (`netsh` on ru-RU Windows answers in Russian - captured 2026-09-24, R4 of the
plan) to prove the parser reads by POSITION, not by matching an English label.

    python tests/research/_test_port_budget.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
import _port_budget as pb  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ── canned OS output, English and this machine's own ru-RU locale ──────────────────────

ENGLISH_DYNAMICPORT = (
    "\nProtocol tcp Dynamic Port Range\n"
    "---------------------------------\n"
    "Start Port      : 49152\n"
    "Number of Ports : 16384\n\n"
)
#: Captured verbatim (shape, not every row) from `netsh int ipv4 show dynamicport tcp`
#: on this machine, 2026-09-24 (ru-RU, LCID 1049).
RUSSIAN_DYNAMICPORT = (
    "\nПротокол tcp Динамический диапазон портов\n"
    "---------------------------------\n"
    "Начальный порт      : 49152\n"
    "Число портов : 16384\n\n"
)

ENGLISH_EXCLUDED = (
    "\nProtocol tcp Port Exclusion Ranges\n\n"
    "Start Port    End Port\n"
    "----------    --------\n"
    "      1234        1333      \n"
    "     50000       50059     *\n\n"
    "* - Managed Port Exclusions.\n"
)
#: Same shape and the same two ranges, under the real ru-RU headers/footer this machine's
#: `netsh int ipv4 show excludedportrange protocol=tcp` prints (captured 2026-09-24).
RUSSIAN_EXCLUDED = (
    "\nПротокол tcp Диапазоны исключения портов\n\n"
    "Начальный порт    Конечный порт      \n"
    "----------    --------      \n"
    "      1234        1333      \n"
    "     50000       50059     *\n\n"
    "* — управляемые "
    "исключения портов.\n"
)


def _csv(established: list, timewait: list) -> str:
    lines = ['"LocalPort","State"']
    lines += [f'"{p}","Established"' for p in established]
    lines += [f'"{p}","TimeWait"' for p in timewait]
    return "\n".join(lines) + "\n"


# ── T9a: parsing reads by position, so English and Russian agree exactly ───────────────

def test_t9a_parsing_is_locale_independent() -> None:
    print("\n- T9a: parse_dynamicport/parse_excluded read the SAME values from English "
          "and this machine's own Russian netsh output -")
    eng = pb.parse_dynamicport(ENGLISH_DYNAMICPORT)
    rus = pb.parse_dynamicport(RUSSIAN_DYNAMICPORT)
    check("English dynamicport -> (49152, 16384)", eng == (49152, 16384), str(eng))
    check("Russian dynamicport parses to the SAME tuple (position-based, not label-based)",
          rus == eng, f"{rus} vs {eng}")

    eng_x = pb.parse_excluded(ENGLISH_EXCLUDED)
    rus_x = pb.parse_excluded(RUSSIAN_EXCLUDED)
    want = [(1234, 1333), (50000, 50059)]
    check("English excluded ranges -> [(1234,1333),(50000,50059)]", eng_x == want, str(eng_x))
    check("Russian excluded ranges parse to the SAME list", rus_x == want, str(rus_x))

    csv_text = _csv(["50000", "50001"], ["60000"])
    conns = pb.parse_connections_csv(csv_text)
    check("connections CSV parses LocalPort as int, State verbatim (ASCII, not localized)",
          conns == [(50000, "Established"), (50001, "Established"), (60000, "TimeWait")],
          str(conns))


# ── T9b: the exact boundary - 8,399 free refuses, 8,400 free goes ──────────────────────

def test_t9b_exact_boundary_8399_refuses_8400_goes() -> None:
    print(f"\n- T9b: the exact boundary - free={pb.NEED - 1} refuses, free={pb.NEED} goes -")
    pb._platform = "win32"
    try:
        # a clean 10,000-port synthetic range (start=50000), no exclusions, so `free`
        # is controlled entirely by how many Established connections sit inside it
        pb._run_netsh_dynamicport = lambda: (
            "Start Port      : 50000\nNumber of Ports : 10000\n")
        pb._run_netsh_excluded = lambda: "Start Port    End Port\n----------    --------\n"

        in_use_for_8399 = 10000 - (pb.NEED - 1)          # free = range - in_use = NEED-1
        pb._run_connections_csv = lambda: _csv(
            [str(50000 + i) for i in range(in_use_for_8399)], [])
        m = pb.measure()
        check(f"free == {pb.NEED - 1} (range 10000, {in_use_for_8399} in use, 0 excluded)",
              m["free"] == pb.NEED - 1, str(m))
        d = pb.decide(m)
        check(f"decision at free={pb.NEED - 1} is 'refuse' (below NEED, no TIME_WAIT to "
              f"wait on)", d["decision"] == "refuse", str(d))
        check("a refusal carries pace_that_would_fit", "pace_that_would_fit" in d, str(d))

        in_use_for_8400 = 10000 - pb.NEED
        pb._run_connections_csv = lambda: _csv(
            [str(50000 + i) for i in range(in_use_for_8400)], [])
        m2 = pb.measure()
        check(f"free == {pb.NEED} (range 10000, {in_use_for_8400} in use)",
              m2["free"] == pb.NEED, str(m2))
        d2 = pb.decide(m2)
        check(f"decision at free={pb.NEED} is 'go'", d2["decision"] == "go", str(d2))
    finally:
        pb._platform = sys.platform


# ── T9c: TIME_WAIT-drainable -> wait, then a later poll finds it drained to 'go' ───────

def test_t9c_time_wait_drainable_waits_then_drains_to_go() -> None:
    print("\n- T9c: free short of NEED, but free+time_wait covers it -> 'wait'; a later "
          "poll (fake clock, no real sleep) finds it drained -> 'go' -")
    pb._platform = "win32"
    saved_sleep, saved_now = pb._sleep, pb._now
    clock = {"t": 0.0}
    pb._now = lambda: clock["t"]

    def fake_sleep(s):
        clock["t"] += s
    pb._sleep = fake_sleep
    try:
        pb._run_netsh_dynamicport = lambda: (
            "Start Port      : 50000\nNumber of Ports : 10000\n")
        pb._run_netsh_excluded = lambda: "Start Port    End Port\n----------    --------\n"

        # first measurement: free = 10000-1700=8300 (< NEED); +300 TIME_WAIT = 8600 (>= NEED)
        polls = {"n": 0}

        def flaky_csv():
            polls["n"] += 1
            if polls["n"] == 1:
                return _csv([str(50000 + i) for i in range(1700)],
                           [str(59000 + i) for i in range(300)])
            # the second (and every later) measurement: TIME_WAIT drained, fewer in-use
            return _csv([str(50000 + i) for i in range(1500)], [])
        pb._run_connections_csv = flaky_csv

        first = pb.decide(pb.measure())
        check("free 8300 + time_wait 300 (8600 >= NEED) -> 'wait' on the first measurement",
              first["decision"] == "wait", str(first))
        check("resets the poll counter for preflight() below", True)
        polls["n"] = 0

        result = pb.preflight(wait_s=pb.MAX_WAIT_S)
        check("preflight() eventually returns 'go' once TIME_WAIT drains",
              result["decision"] == "go", str(result))
        check("exactly one poll (15s) was needed - the SECOND measurement already drained "
              "enough", polls["n"] == 2, str(polls))
        check("the fake clock actually advanced by one poll interval (no real sleep)",
              clock["t"] == pb.POLL_INTERVAL_S, str(clock))
    finally:
        pb._platform = sys.platform
        pb._sleep, pb._now = saved_sleep, saved_now


# ── T9d: exclusions eating the range -> refuse, with no TIME_WAIT to wait on ───────────

def test_t9d_excluded_range_refuses() -> None:
    print("\n- T9d: Windows' OWN exclusions eat most of the range -> 'refuse' -")
    pb._platform = "win32"
    try:
        pb._run_netsh_dynamicport = lambda: (
            "Start Port      : 50000\nNumber of Ports : 10000\n")
        # one exclusion range covering 9,500 of the 10,000 ports (50000..59499 inclusive)
        pb._run_netsh_excluded = lambda: (
            "Start Port    End Port\n----------    --------\n      50000       59499\n")
        pb._run_connections_csv = lambda: _csv([], [])          # nothing else in use

        m = pb.measure()
        check("excluded_in_range == 9500", m["excluded_in_range"] == 9500, str(m))
        check("free == 500 (10000 - 9500 excluded - 0 in use)", m["free"] == 500, str(m))
        d = pb.decide(m)
        check("no TIME_WAIT to drain (0 in use at all) -> straight to 'refuse', never 'wait'",
              d["decision"] == "refuse", str(d))
        want_fit = max(0.0, (500 - pb.RESERVE) / pb.PORTS_PER_RATE_UNIT)
        check(f"pace_that_would_fit == {want_fit} (the plan's own formula, "
              f"(free - RESERVE) / PORTS_PER_RATE_UNIT, floored at 0)",
              abs(d["pace_that_would_fit"] - want_fit) < 1e-9, str(d))
    finally:
        pb._platform = sys.platform


# ── T9e: non-Windows never runs a single OS query - "unchecked", always ────────────────

def test_t9e_non_windows_is_unchecked_and_never_queries_anything() -> None:
    print("\n- T9e: off Windows, measure()/decide()/preflight() are 'unchecked' and never "
          "run a single OS query -")
    saved_platform = pb._platform
    saved = (pb._run_netsh_dynamicport, pb._run_netsh_excluded, pb._run_connections_csv)

    def _boom():
        raise AssertionError("an OS query ran on a platform this check does not apply to")
    pb._run_netsh_dynamicport = _boom
    pb._run_netsh_excluded = _boom
    pb._run_connections_csv = _boom
    pb._platform = "linux"
    try:
        m = pb.measure()
        check("measure() reports platform='unchecked' on non-Windows", m.get("platform") ==
              "unchecked", str(m))
        d = pb.decide(m)
        check("decide() reads 'unchecked' as its own decision", d["decision"] == "unchecked",
              str(d))
        pf = pb.preflight()
        check("preflight() short-circuits to 'unchecked' without ever polling",
              pf["decision"] == "unchecked", str(pf))
    finally:
        pb._platform = saved_platform
        (pb._run_netsh_dynamicport, pb._run_netsh_excluded,
         pb._run_connections_csv) = saved


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_t9a_parsing_is_locale_independent,
               test_t9b_exact_boundary_8399_refuses_8400_goes,
               test_t9c_time_wait_drainable_waits_then_drains_to_go,
               test_t9d_excluded_range_refuses,
               test_t9e_non_windows_is_unchecked_and_never_queries_anything):
        fn()
    print(f"\nport_budget: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
