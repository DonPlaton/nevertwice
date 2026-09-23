#!/usr/bin/env python3
"""`_looks_like_identifier` (2026-09-24): `write_typed_note`'s call to `principle_scan` used to
forbid every declared `entities` string VERBATIM, so a principle that merely USED one of its own
note's ordinary technical-word entities ("database", "retry", "timeout") was silently rejected -
found on the first real `--extract` run of `research/cross_project_bench.py`: 5 of 10 written
notes' principles were dropped this way, and `write_rejections_by_class` read 0 everywhere,
because the rejection never correlated with a PLANTED identifier, only with the model's own
vocabulary list.

The fix LOOSENS a de-identification gate, so both directions get their own test (the auditor's
binding guard): (a) a principle carrying a REAL identifier is still rejected, by name, per
identifier class; (b) a principle whose only overlap with `entities` is a generic technical word
now survives. A mutation proves the classifier itself is load-bearing (forbid nothing -> the
real-identifier case turns red), and a final pair proves it stays linear-time on a hostile
input, the house style after `_DANGER_RE`.

    python tests/_test_principle_entity_forbidding.py
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
from _sandbox import make_sandbox  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


PROJECT = "cpv1_ib_test"

# (a) a REAL identifier of each of the bench's four classes (research/cross_project_bench.py),
# planted where its class is actually caught. ip/host/path are regex-caught unconditionally by
# `PRINCIPLE_IDENTIFIER_GROUPS` - this change never touches them, kept here only as a
# completeness/regression check. "entity" is the ONLY class this fix touches: it has no
# standalone regex (W17), so it is caught SOLELY through the forbidden-entities path, which is
# exactly what `_looks_like_identifier` now gates.
REAL_IDENTIFIER_PRINCIPLES = {
    "ip": ("Confirmed the fix from a session against 10.0.0.5 before shipping it.", []),
    "host": ("svc-a000.internal.example must be drained before every deploy.", []),
    "path": ("Never write under /var/lib/app-a000/state.db while the migrator runs.", []),
    "entity": ("JIRA-1234 must be resolved before this deploy can proceed safely.",
              ["JIRA-1234"]),
}

# (b) a principle whose only overlap with its own note's `entities` is a plain technical word -
# no digit, no dot, no slash, nothing that identifies a specific deployment.
GENERIC_ENTITY_PRINCIPLE = "Never mock the database directly in an integration test."
GENERIC_ENTITIES = ["database", "integration test"]


def _write_and_read_principle(title: str, principle: str, entities: list) -> str:
    item = {"title": title, "description": "diagnostic fixture note for the write-time gate",
           "principle": principle, "entities": entities}
    stem = m.write_typed_note("Patterns", item, PROJECT, "2026-09-24", [], "pattern")
    if not stem:
        return "__NOT_WRITTEN__"
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    return fm.get("principle") if isinstance(fm.get("principle"), str) else ""


def test_real_identifiers_still_rejected_per_class() -> None:
    print("\n- (a) a REAL identifier of every class is still rejected, by name -")
    for cls, (principle, entities) in REAL_IDENTIFIER_PRINCIPLES.items():
        make_sandbox(m, f"principle_entity_a_{cls}_", offline=True)
        written = _write_and_read_principle(f"real-{cls}-identifier", principle, entities)
        check(f"{cls}: a real identifier-carrying principle is rejected", written == "",
              repr(written))


def test_generic_entity_word_now_survives() -> None:
    print("\n- (b) a principle overlapping ONLY a generic technical-word entity now survives -")
    make_sandbox(m, "principle_entity_b_", offline=True)
    written = _write_and_read_principle("generic-entity-overlap", GENERIC_ENTITY_PRINCIPLE,
                                        GENERIC_ENTITIES)
    check("the principle is kept (not rejected for merely using the word 'database')",
          written == GENERIC_ENTITY_PRINCIPLE, repr(written))


def test_looks_like_identifier_classifies_examples_correctly() -> None:
    print("\n- _looks_like_identifier: the classifier itself, on realistic tokens -")
    identifier_shaped = ["svc-a000", "JIRA-1234", "v2", "app-a000.state", "/var/lib/app",
                         "queue-shard-a000"]
    generic_words = ["database", "retry", "timeout", "integration test", "client-side",
                     "fixture-isolation"]
    for tok in identifier_shaped:
        check(f"{tok!r} looks identifier-shaped", m._looks_like_identifier(tok, PROJECT), tok)
    for tok in generic_words:
        check(f"{tok!r} does NOT look identifier-shaped",
              not m._looks_like_identifier(tok, PROJECT), tok)
    check("the project's own slug is always identifier-shaped, whatever it looks like",
          m._looks_like_identifier(PROJECT, PROJECT))
    check("matching is case-insensitive for the project slug",
          m._looks_like_identifier(PROJECT.upper(), PROJECT))
    check("empty token is never identifier-shaped", not m._looks_like_identifier("", PROJECT))


def test_mutation_forbid_nothing_turns_the_real_identifier_case_red() -> None:
    """A gate that forbids nothing is not a gate - the auditor's binding guard. Bypass
    `_looks_like_identifier` entirely (always False) and prove the REAL entity-class identifier
    (JIRA-1234) now survives when it must not - proving the CLASSIFIER, not `principle_scan`'s
    regex groups (untouched by this change), is what is catching it."""
    print("\n- mutation: a forbid-nothing classifier turns the entity-class real-identifier "
         "case red -")
    saved = m._looks_like_identifier
    m._looks_like_identifier = lambda token, project: False
    try:
        make_sandbox(m, "principle_entity_mut_", offline=True)
        principle, entities = REAL_IDENTIFIER_PRINCIPLES["entity"]
        written = _write_and_read_principle("mutated-entity-identifier", principle, entities)
    finally:
        m._looks_like_identifier = saved
    check("mutation: WITHOUT the classifier, the entity-class identifier now survives (would "
         "FAIL 'entity: a real identifier-carrying principle is rejected' above)",
         written == principle, repr(written))


def _calls_for(fn) -> int:
    """How many calls one timed batch needs to clear the timer - the house pattern
    (`tests/_test_principle_scan.py::_calls_for`, itself from `tests/_test_fact_survival.py`)."""
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


def test_hostile_input_at_n_and_4n_finishes_in_comparable_time() -> None:
    """The repo's linearity-check pattern: a doubling of the input costs a quadratic scan four
    times as much and a linear one twice, so the gate sits at eight times for a FOURFOLD input.
    `_looks_like_identifier` is one bounded character-class scan with no quantifier over a
    repeated group, so this should not even be close - the test exists so a future edit that
    adds one cannot land unnoticed. The hostile string has NO digit, dot or slash anywhere, so
    `re.search` cannot short-circuit on an early match - the actual worst case for this pattern,
    a full unmatched scan of the whole input."""
    print("\n- _looks_like_identifier stays linear on a hostile (no-match) input -")
    small = "clientsidefixtureisolation" * 400          # ~10.4 kB, no digit/dot/slash anywhere
    big = "clientsidefixtureisolation" * 1600            # ~41.6 kB (4x)

    def _small():
        m._looks_like_identifier(small, PROJECT)

    def _big():
        m._looks_like_identifier(big, PROJECT)

    t_small, t_big = _cost(_small), _cost(_big)
    check(f"4x the text costs less than 8x the work ({t_small*1000:.3f}ms -> {t_big*1000:.3f}ms)",
          t_big < 8 * max(t_small, 1e-6))
    check(f"~42 kB of hostile input stays well under a second ({t_big:.3f}s)", t_big < 1.0)


# ── (A) widening, 2026-09-24: the auditor's probe found the first cut too narrow ───────────

#: The auditor's own probe against project "myproj" (quoted verbatim in the finding). Ten of
#: twelve wrongly-kept tokens are caught by the five widened rules; the other two
#: ("phoenix", "acme-corp") are a DELIBERATE residual - see RESIDUAL_STAYS_KEPT and
#: docs/WEAKNESSES.md's W17 "Second residual" paragraph.
WRONGLY_KEPT_NOW_FORBIDDEN = (
    "payments-api", "billing_service", "UserRepository", "STRIPE_SECRET_KEY", "useAuthStore",
    "db-primary", "orders_table", "kafka-consumer-group", "OrderService", "prod-cluster",
)
RESIDUAL_STAYS_KEPT = ("phoenix", "acme-corp")
#: The auditor's nine correctly-kept generic words, unchanged by the widening.
GENERIC_WORDS = ("database", "retry", "timeout", "client-side", "fixture-isolation", "cache",
                 "migration", "API", "HTTP")
PROBE_PROJECT = "myproj"


def test_a_widened_classifier_forbids_common_code_identifier_shapes() -> None:
    """(a) The auditor's ten wrongly-kept, shape-classifiable tokens are forbidden after the
    widening - red before it (the classifier this suite already had, digit/dot/slash/project-
    slug only, read every one of these as generic)."""
    print("\n- (a, widened) the ten wrongly-kept, shape-classifiable tokens are now forbidden -")
    for tok in WRONGLY_KEPT_NOW_FORBIDDEN:
        check(f"{tok!r} now looks identifier-shaped",
              m._looks_like_identifier(tok, PROBE_PROJECT), tok)


def test_b_generic_words_still_kept() -> None:
    """(b) The widening must not cost anything the first cut already got right - the auditor's
    nine correctly-kept generic words, including the two hyphenated ones (their parts are not
    infra nouns) and the two bare acronyms (no lowercase letter to transition from)."""
    print("\n- (b) the nine generic words are still kept, unaffected by the widening -")
    for tok in GENERIC_WORDS:
        check(f"{tok!r} still reads generic", not m._looks_like_identifier(tok, PROBE_PROJECT),
              tok)


def test_c_residual_shapes_stay_kept_by_design() -> None:
    """(c) "phoenix" (a lone lowercase product word) and "acme-corp" (a company kebab whose
    parts are not infra nouns) are EXPLICITLY decided, not an oversight: shape alone cannot
    separate either from vocabulary, so both stay kept here, defended instead by
    `principles.py::_token_provenance` at promotion time - documented as a residual in
    `docs/WEAKNESSES.md` (W17, "Second residual", 2026-09-24)."""
    print("\n- (c) phoenix / acme-corp: explicitly decided to stay kept, defended at promotion -")
    for tok in RESIDUAL_STAYS_KEPT:
        check(f"{tok!r} stays kept (the residual decision, not a bug)",
              not m._looks_like_identifier(tok, PROBE_PROJECT), tok)
    weaknesses = (ROOT / "docs" / "WEAKNESSES.md").read_text(encoding="utf-8")
    check("docs/WEAKNESSES.md documents the residual under W17",
          "Second residual" in weaknesses and "phoenix" in weaknesses
          and "acme-corp" in weaknesses)


def test_e_mutation_removing_underscore_rule_reddens_a_by_name() -> None:
    """(e) `_has_underscore` disabled turns test (a)'s underscore-ONLY tokens red, by name -
    `billing_service` and `orders_table` have no digit/dot/slash, no camelCase transition (all
    lowercase) and no hyphen, so `_has_underscore` is the ONLY rule that can catch them.
    `STRIPE_SECRET_KEY` is deliberately NOT part of this check: `_is_screaming_snake` is a
    second, independent read of the same underscore shape (by design, see its own docstring),
    so it stays caught even with `_has_underscore` disabled - proving the two rules are
    independently redundant, not that the mutation failed to bite."""
    print("\n- mutation: disabling _has_underscore reddens (a)'s underscore-only tokens -")
    saved = m._has_underscore
    m._has_underscore = lambda t: False
    try:
        for tok in ("billing_service", "orders_table"):
            check(f"mutation: {tok!r} now survives without _has_underscore (would FAIL "
                 f"'{tok!r} now looks identifier-shaped' in test (a) above)",
                 not m._looks_like_identifier(tok, PROBE_PROJECT), tok)
        check("mutation: STRIPE_SECRET_KEY is STILL caught - _is_screaming_snake is an "
             "independent rule, not routed through _has_underscore",
             m._looks_like_identifier("STRIPE_SECRET_KEY", PROBE_PROJECT))
    finally:
        m._has_underscore = saved
    check("sanity: with the mutation restored, (a)'s tokens are caught again",
          m._looks_like_identifier("billing_service", PROBE_PROJECT)
          and m._looks_like_identifier("orders_table", PROBE_PROJECT))


def test_d_hyphenated_hostile_input_stays_linear() -> None:
    """(d) `_hyphen_part_is_infra` adds a NEW code path (`_has_camel_transition`'s regex scan
    was already covered by the suite's existing no-match linearity test above, since that
    hostile input has no uppercase letters at all to transition into). The hostile input here
    has MANY hyphens (so the split does real work) and no digit/underscore/uppercase/infra
    match anywhere, so every one of the five rules runs to completion without an early exit -
    the actual worst case for the now-widened function as a whole."""
    print("\n- _looks_like_identifier stays linear on a hostile (many-hyphen) input -")
    small = "-".join(["clientside"] * 500)            # ~5.5 kB, ~500 hyphens, no infra match
    big = "-".join(["clientside"] * 2000)              # ~22 kB (4x), ~2000 hyphens

    def _small():
        m._looks_like_identifier(small, PROBE_PROJECT)

    def _big():
        m._looks_like_identifier(big, PROBE_PROJECT)

    t_small, t_big = _cost(_small), _cost(_big)
    check(f"4x the text costs less than 8x the work ({t_small*1000:.3f}ms -> {t_big*1000:.3f}ms)",
          t_big < 8 * max(t_small, 1e-6))
    check(f"~22 kB / ~2000 hyphens stays well under a second ({t_big:.3f}s)", t_big < 1.0)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code - enforced for every
    counting suite by `tests/_test_the_harness_agrees_with_itself.py`."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_real_identifiers_still_rejected_per_class,
              test_generic_entity_word_now_survives,
              test_looks_like_identifier_classifies_examples_correctly,
              test_mutation_forbid_nothing_turns_the_real_identifier_case_red,
              test_hostile_input_at_n_and_4n_finishes_in_comparable_time,
              test_a_widened_classifier_forbids_common_code_identifier_shapes,
              test_b_generic_words_still_kept,
              test_c_residual_shapes_stay_kept_by_design,
              test_e_mutation_removing_underscore_rule_reddens_a_by_name,
              test_d_hyphenated_hostile_input_stays_linear):
        fn()
    print(f"\nprinciple entity forbidding: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
