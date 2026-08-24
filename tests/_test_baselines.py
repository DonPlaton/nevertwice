#!/usr/bin/env python3
"""The baseline gate: a headline declares what it was compared against, or it is not one.

`research/BASELINES.md` states the rule; this suite makes it enforceable. Its job is to
stop the cheapest form of self-deception in a project like this - publishing a number
without saying which obvious alternative explanation was ruled out.

The gate has teeth in three places:

* every headline claim carries a verdict for **every** registered baseline, so a baseline
  cannot be quietly dropped from a claim it would embarrass;
* `not_compared` and `not_applicable` both require a reason, because "it does not apply"
  is itself a claim about the metric that a reader may dispute;
* a `loses_to` verdict has to be visible in the document, not buried in a data file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

MANIFEST = json.loads(
    (ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
POLICY_DOC = ROOT / "research" / "BASELINES.md"
DOC = POLICY_DOC.read_text(encoding="utf-8")

# The six the project's goal fixes. `curated_haystack` is an addition, not a substitute.
REQUIRED_BASELINES = ("no_memory", "full_history", "lexical_recall",
                      "curated_agents_md", "llm_session_summary", "linter_or_test")
VERDICTS = ("beats", "ties", "loses_to", "not_compared", "not_applicable")
NEEDS_REASON = ("not_compared", "not_applicable", "loses_to")
MEASURED = ("beats", "ties", "loses_to")

# The claims the project leads with - banner, badges, first screen of the README.
# Naming them here rather than in the manifest means a claim cannot escape the gate by
# quietly dropping its `headline` flag; removing one from this list is a visible edit.
REQUIRED_HEADLINES = (
    "live_validation.relative_reduction",
    "longmem.hybrid.recall_at_5",
    "qa.oracle.answer_accuracy",
    "longitudinal.active_vs_inject_token_ratio",
    "token_ab.distill.ratio",
    "token_ab.live_two_arm.input_token_reduction",
)

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def headline_claims() -> list[dict]:
    return [c for c in MANIFEST["claims"] if c.get("headline")]


def test_the_policy_is_registered() -> None:
    print("\n- the policy exists and is machine-readable -")
    policy = MANIFEST.get("baseline_policy", {})
    check("the rule is stated", bool(policy.get("rule")))
    check("the matched conditions are listed",
          len(policy.get("matched_conditions", [])) >= 4,
          str(len(policy.get("matched_conditions", []))))
    check("every verdict name is defined",
          all(v in policy.get("verdicts", {}) for v in VERDICTS),
          ", ".join(v for v in VERDICTS if v not in policy.get("verdicts", {})))

    # The four conditions are what stop a comparison being won by spending more.
    joined = " ".join(policy.get("matched_conditions", [])).lower()
    for needle in ("model", "token", "latency", "false-positive"):
        check(f"the conditions pin {needle}", needle in joined)


def test_every_required_baseline_is_registered() -> None:
    print("\n- the six required baselines are registered identifiers -")
    registered = MANIFEST.get("baselines", {})
    missing = [b for b in REQUIRED_BASELINES if b not in registered]
    check("all six baselines the policy fixes are present", not missing,
          ", ".join(missing))

    incomplete = [b for b, v in registered.items()
                  if not all(v.get(f) for f in ("name", "definition", "why", "how"))]
    check("every baseline says what it is, why it is the test, and how to run it",
          not incomplete, ", ".join(incomplete))

    undocumented = [b for b in registered if f"`{b}`" not in DOC]
    check("every baseline appears in research/BASELINES.md", not undocumented,
          ", ".join(undocumented))


def test_every_headline_declares_a_verdict_for_every_baseline() -> None:
    """The gap this closes: dropping the one baseline a claim would fail against."""
    print("\n- no headline may skip a baseline -")
    claims = headline_claims()
    marked = {c["id"] for c in claims}
    demoted = [c for c in REQUIRED_HEADLINES if c not in marked]
    check("every claim the project leads with is still marked a headline", not demoted,
          ", ".join(demoted))

    registered = list(MANIFEST["baselines"])
    holes = []
    for claim in claims:
        verdicts = claim.get("baseline_verdicts") or {}
        for baseline in registered:
            if baseline not in verdicts:
                holes.append(f"{claim['id']} x {baseline}")
    check(f"all {len(claims)} x {len(registered)} pairs are declared", not holes,
          "; ".join(holes[:5]))

    unknown = [f"{c['id']} x {b}" for c in claims
               for b, v in (c.get("baseline_verdicts") or {}).items()
               if v.get("verdict") not in VERDICTS]
    check("every verdict is one of the defined values", not unknown,
          "; ".join(unknown[:5]))

    stray = [f"{c['id']} x {b}" for c in claims
             for b in (c.get("baseline_verdicts") or {})
             if b not in registered]
    check("no verdict names an unregistered baseline", not stray, "; ".join(stray[:5]))


def test_measured_verdicts_name_their_evidence() -> None:
    """Otherwise flipping `not_compared` to `beats` is free, which would make the whole
    matrix decorative."""
    print("\n- a measured verdict says where the comparison lives -")
    unevidenced = []
    for claim in headline_claims():
        for baseline, entry in (claim.get("baseline_verdicts") or {}).items():
            if entry.get("verdict") in MEASURED and not (entry.get("evidence") or "").strip():
                unevidenced.append(f"{claim['id']} x {baseline}")
    check("every beats / ties / loses_to names a raw file or the arms it compared",
          not unevidenced, "; ".join(unevidenced[:5]))

    unrooted = []
    for claim in headline_claims():
        for baseline, entry in (claim.get("baseline_verdicts") or {}).items():
            ev = entry.get("evidence") or ""
            if entry.get("verdict") in MEASURED and "research/" not in ev:
                unrooted.append(f"{claim['id']} x {baseline}")
    check("that evidence points at a committed result file", not unrooted,
          "; ".join(unrooted[:5]))

    check("the policy states the evidence rule",
          bool(MANIFEST["baseline_policy"].get("evidence_rule")))


def test_soft_verdicts_carry_a_reason() -> None:
    """`not_applicable` is the one an author reaches for when a baseline is
    inconvenient, so it is exactly the one that must be argued rather than asserted."""
    print("\n- an unmeasured or lost baseline has to say why -")
    silent = []
    for claim in headline_claims():
        for baseline, entry in (claim.get("baseline_verdicts") or {}).items():
            if entry.get("verdict") in NEEDS_REASON and not (entry.get("note") or "").strip():
                silent.append(f"{claim['id']} x {baseline}")
    check("every not_compared / not_applicable / loses_to carries a reason", not silent,
          "; ".join(silent[:5]))

    thin = []
    for claim in headline_claims():
        for baseline, entry in (claim.get("baseline_verdicts") or {}).items():
            note = (entry.get("note") or "").strip()
            if entry.get("verdict") in NEEDS_REASON and len(note) < 25:
                thin.append(f"{claim['id']} x {baseline}: {note!r}")
    check("the reasons are arguments, not placeholders", not thin, "; ".join(thin[:4]))


def test_a_lost_baseline_is_visible_in_the_document() -> None:
    """A claim that fails its gate must be readable as such by a human opening the file,
    not only by a tool reading the manifest."""
    print("\n- a claim that fails the gate says so where people read -")
    losers = [c["id"] for c in headline_claims()
              if any(v.get("verdict") == "loses_to"
                     for v in (c.get("baseline_verdicts") or {}).values())]
    for claim_id in losers:
        check(f"{claim_id} is named in the document", claim_id in DOC)
    check("the document explains what a failed gate means for the claim",
          "does not pass" in DOC or "narrowed" in DOC)
    if not losers:
        print("       (no headline currently loses a baseline)")
    else:
        print(f"       ({len(losers)} headline(s) currently fail a baseline: "
              f"{', '.join(losers)})")


def test_the_unbuilt_arms_are_named_with_an_owner() -> None:
    """An unbuilt baseline is a task, not a footnote. If nothing owns it, it never
    gets built and the matrix stays yellow forever."""
    print("\n- the unbuilt baselines name the task that builds them -")
    unbuilt = [b for b, v in MANIFEST["baselines"].items()
               if v["how"].lower().startswith("not yet built")]
    check("at least one baseline is honestly marked unbuilt", bool(unbuilt),
          "none - if that is true, every arm exists and this check can go")
    check("the document names the task that builds the unbuilt arms",
          "F2" in DOC, "no task id in research/BASELINES.md")
    for baseline in unbuilt:
        check(f"{baseline} is named in the document", f"`{baseline}`" in DOC)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nbaseline gates: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
