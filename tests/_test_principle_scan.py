#!/usr/bin/env python3
"""`principle_scan` is the only gate between a note's `principle` field and another project's
memory (A4's universal recall pool reads it verbatim - `_cross_line`). A principle that still
names an IP, a host, a path, an email or a version string defeats the whole point of the
Q5 principle layer: a lesson that is supposed to generalise instead re-identifies where it
came from the moment it is read back in a different project.

This suite checks three things: a planted identifier of every class the plan names is
rejected; at least ten realistic, already-de-identified rules pass through unchanged; and each
class's pattern is load-bearing - ablating it (a test-only monkeypatch of
`m.PRINCIPLE_IDENTIFIER_GROUPS`, never a file edit) turns exactly that class's check red and
no other. A final pair of checks proves every pattern stays linear-time on a hostile input,
the house style this repository uses after being bitten once by an exponential regex
(`grep -n "4n\\|linear" tests/_test_fact_survival.py`).

    python tests/_test_principle_scan.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# One planted identifier per class the plan names, each embedded in an otherwise clean
# sentence so a rejection can only be attributed to the planted token.
#: Each text trips EXACTLY one class - chosen so the mutation test below can attribute a
#: rejection to a single pattern. Two classes that legitimately overlap on realistic input
#: (an IPv4 literal also looks like a 4-part version; a filename with an extension also looks
#: like a bare FQDN) are deliberately avoided here so ablating one pattern cannot be masked by
#: another still catching the same planted text (see the `_PRINCIPLE_VERSION_RE` comment).
PLANTED = {
    "ip": "Confirmed the fix from a session against 10.0.0.5 before shipping it.",
    "url_fqdn": "See https://example.com/docs for the reference before changing this.",
    "path": "The private key at /home/user/.ssh/id_rsa must never be copied out.",
    "email": "Contact ops-team@localhost before rotating any shared key.",
    "host_port": "The service listens on db-primary:5432 for every connection.",
    "version": "Upgraded to Python 3.12.1 before re-running the whole suite.",
}

# Realistic, already de-identified lessons - at least ten, per the plan. None of these should
# ever be rejected; if one is, the scanner is too aggressive, not merely cautious.
CLEAN_RULES = [
    "Pin the sampler temperature before comparing two runs of a stochastic system.",
    "Read the whole file before editing it, even when asked not to.",
    "A test that only counts failures in a list can look green under a different runner.",
    "Prefer a bounded regex to an unbounded one when the input is attacker-controlled.",
    "Cap a text field at write time so a malformed value cannot grow without limit.",
    "A retry policy should retry on server errors and never on client errors.",
    "Redact secrets before any text reaches persistent storage.",
    "Measure the ratio of costs, not the absolute time, to tell linear from quadratic.",
    "A negation before a dangerous imperative turns it into a warning, not a command.",
    "Keep the static part of a prompt before the part that changes on every call.",
    "A guard that counts a second copy of its own loop is not really independent.",
    "Never trust a configuration value without a safe fallback for a bad type.",
]

FORBIDDEN_TOKEN_TEXT = "The fix in quantum_prism was to cap the batch before retrying."


def test_planted_identifiers_are_rejected() -> None:
    print("\n- a planted identifier of every class is rejected -")
    for group, text in PLANTED.items():
        check(f"planted {group} identifier is rejected",
              m.principle_scan(text, set()) == "", text)


def test_clean_deidentified_rules_pass() -> None:
    print(f"\n- {len(CLEAN_RULES)} realistic de-identified rules pass through -")
    check("at least ten clean rules are exercised", len(CLEAN_RULES) >= 10, str(len(CLEAN_RULES)))
    for rule in CLEAN_RULES:
        out = m.principle_scan(rule, set())
        check(f"clean rule survives: {rule[:44]!r}", out == rule, repr(out))


def test_forbidden_tokens_are_rejected() -> None:
    print("\n- a forbidden token (project slug / entities) is rejected, case-insensitive -")
    check("with the project named forbidden, the sentence is rejected",
          m.principle_scan(FORBIDDEN_TOKEN_TEXT, {"quantum_prism"}) == "")
    check("the same sentence with nothing forbidden survives",
          m.principle_scan(FORBIDDEN_TOKEN_TEXT, set()) == FORBIDDEN_TOKEN_TEXT)
    check("matching is case-insensitive",
          m.principle_scan(FORBIDDEN_TOKEN_TEXT, {"QUANTUM_PRISM"}) == "")
    check("matching is on a word boundary, not a bare substring",
          m.principle_scan("Quantumprismatic lenses split light cleanly.",
                           {"quantum_prism"}) == "Quantumprismatic lenses split light cleanly.")
    check("an empty forbidden set never rejects on its own",
          m.principle_scan("An ordinary clean sentence with no identifiers at all.", set())
          != "")


def test_mutation_each_identifier_class_is_load_bearing() -> None:
    """Ablating one entry of `PRINCIPLE_IDENTIFIER_GROUPS` must turn exactly that class's own
    planted-identifier check red, and leave every other class's check green - the same "by
    name" bar `tests/_test_the_harness_agrees_with_itself.py` holds every guard in this
    repository to. The ablation is a monkeypatch of the dict itself (restored in `finally`),
    never a file edit - this suite is what proves the pattern is load-bearing, not a one-off
    manual demonstration that leaves no trace once the session ends."""
    print("\n- mutation: dropping one class's pattern turns its own check red, none other -")
    for victim in PLANTED:
        saved = dict(m.PRINCIPLE_IDENTIFIER_GROUPS)
        m.PRINCIPLE_IDENTIFIER_GROUPS[victim] = ()
        try:
            still_rejects = {g: m.principle_scan(t, set()) == "" for g, t in PLANTED.items()}
        finally:
            m.PRINCIPLE_IDENTIFIER_GROUPS.clear()
            m.PRINCIPLE_IDENTIFIER_GROUPS.update(saved)
        check(f"mutation drops '{victim}': its own planted identifier now survives (would FAIL "
              f"'planted {victim} identifier is rejected')", not still_rejects[victim])
        others_still_ok = all(still_rejects[g] for g in PLANTED if g != victim)
        check(f"mutation drops '{victim}': every OTHER class is still caught", others_still_ok,
              str({g: v for g, v in still_rejects.items() if g != victim and not v}))


def _calls_for(fn) -> int:
    """How many calls one timed batch of `fn` needs to clear the timer - the house pattern
    (`tests/_test_fact_survival.py::_calls_for`), reused at a smaller scale here."""
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        span = time.perf_counter() - t0
        if (span >= 0.005 and (n >= 8 or span / n >= 0.05)) or n >= 256:
            return n
        n *= 8


def _cost(fn, runs: int = 8) -> float:
    n = _calls_for(fn)
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        best = min(best, (time.perf_counter() - t0) / n)
    return best


def test_hostile_inputs_at_n_and_4n_finish_in_comparable_time() -> None:
    """The repo's linearity-check pattern (`tests/_test_fact_survival.py`): a doubling of the
    input costs a quadratic scan four times as much and a linear one twice, so the gate sits at
    eight times for a FOURFOLD input. Path-like and colon/dot-separated text is what tripped
    this repository's regex before (`_DANGER_RE`'s docstring), so the hostile input here is
    exactly that shape: long runs of path characters with no terminator, no digit, no protocol -
    the worst case for every one of the six pattern groups at once."""
    print("\n- every identifier pattern stays linear on a hostile input -")
    small = "a/b.c-d:e" * 400          # ~3.6 kB
    big = "a/b.c-d:e" * 1600           # ~14.4 kB (4x)

    def _scan_small():
        m.principle_scan(small, set())

    def _scan_big():
        m.principle_scan(big, set())

    t_small, t_big = _cost(_scan_small), _cost(_scan_big)
    check(f"4x the text costs less than 8x the work ({t_small*1000:.2f}ms -> {t_big*1000:.2f}ms)",
          t_big < 8 * max(t_small, 1e-6))
    check(f"14 kB of hostile input stays well under a second ({t_big:.3f}s)", t_big < 1.0)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_planted_identifiers_are_rejected,
               test_clean_deidentified_rules_pass,
               test_forbidden_tokens_are_rejected,
               test_mutation_each_identifier_class_is_load_bearing,
               test_hostile_inputs_at_n_and_4n_finish_in_comparable_time):
        fn()
    print(f"\nprinciple scan: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
