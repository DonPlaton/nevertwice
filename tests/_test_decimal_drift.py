#!/usr/bin/env python3
"""A study page generated from a stand may not print a decimal the register no longer carries.

The front-page documents are `governed`: every number on them resolves to a live claim, and their
tables are rendered from the register. The study pages are `backlog`: a ratchet caps how many
unregistered numbers each may print, and nothing asks whether the numbers it does print are still
the ones the stand produced. On 2026-09-11 that gap held four pages at once. `SUPERSESSION.md`
quoted a stale rate of 0.075 under a heading dated one campaign earlier while its own generated
table read 0.033; `ABSTENTION_AB.md` said its sweep table was "read from" an artifact whose every
cell differed from the table; `LOCOMO.md` carried the headline table by hand and had drifted a
whole engine revision behind the register; the as-of caption asserted a miss on a run that had met
its gate. Each page's register entry said its figures were registered. None were checked.

This suite checks them. For every document the register marks as produced by a stand
(`generator`), and for every governed document, each **decimal** number outside fenced blocks and
outside generated regions must resolve to something the register vouches for: a live claim's
printed forms or interval bounds, an external citation, a recorded drift entry, or a declared
non-metric rule. Integers are left to the ratchet - a case count or a year is not what drifts -
and a decimal that matches nothing is exactly a stand's figure that the stand no longer produces.

The rule is about live claims, with one qualified exception. A page that carries the withdrawal
banner `tools/stamp_withdrawn.py` stamps may still print the figures of *withdrawn* claims - that
is the honest state between a code change and the re-run, and the banner says the numbers are not
to be quoted. Without the banner a withdrawn claim's figure is an orphan like any other, because a
page that prints a retracted figure as if it were current is the failure this suite exists for.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402

MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

FENCE = re.compile(r"^```.*?^```", re.S | re.M)
GENERATED_REGION = re.compile(r"<!-- (claims|comparison):([\w-]+) -->.*?<!-- /\1:\2 -->", re.S)
#: A decimal: digits, a dot, digits - not part of a version (`2.0.19`), a path or a word.
DECIMAL = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _mask(match: re.Match) -> str:
    """Blank a span but keep its newlines, so line numbers in the report stay true."""
    return re.sub(r"[^\n]", " ", match.group(0))


def withdrawn_forms(manifest: dict) -> set[str]:
    """The forms of withdrawn claims - admissible only under a withdrawal banner."""
    forms: set[str] = set()
    for c in manifest["claims"]:
        if not c.get("stale"):
            continue
        for p in c.get("printed", []):
            p = str(p).strip()
            forms |= {p, p.lstrip("+−-"), p.rstrip("%")}
        v = c.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            forms |= {f"{v:.2f}", f"{v:.3f}", f"{v:.4f}", f"{v:g}"}
        ci = c.get("ci")
        if isinstance(ci, dict):
            for b in (ci.get("low"), ci.get("high")):
                if isinstance(b, (int, float)):
                    forms |= {f"{b:.2f}", f"{b:.3f}", f"{b:.4f}"}
    return forms


def carries_withdrawal_banner(text: str) -> bool:
    """The banner `tools/stamp_withdrawn.py` stamps, or a hand-written withdrawal notice."""
    return "<!-- withdrawn-banner -->" in text or "**withdrawn" in text[:4000].lower()


def vouched_forms(manifest: dict) -> set[str]:
    """Every decimal form a live claim, an external citation or a drift entry stands behind."""
    forms: set[str] = set()
    for c in manifest["claims"]:
        if c.get("stale"):
            continue
        for p in c.get("printed", []):
            p = str(p).strip()
            forms |= {p, p.lstrip("+−-"), p.rstrip("%")}
        v = c.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            forms |= {f"{v:.2f}", f"{v:.3f}", f"{v:.4f}", f"{v:g}"}
        ci = c.get("ci")
        if isinstance(ci, dict):
            for b in (ci.get("low"), ci.get("high")):
                if isinstance(b, (int, float)):
                    forms |= {f"{b:.2f}", f"{b:.3f}", f"{b:.4f}"}
    for e in manifest.get("external_citations", []):
        forms |= {str(p).strip() for p in e.get("printed", [])}
    for d in manifest.get("drift", []):
        forms |= {str(p).strip() for p in d.get("printed", [])}
    return forms


def non_metric_rules(manifest: dict) -> list[tuple]:
    rules = []
    for n in manifest["non_metrics"]:
        match = ((lambda t, lit=n["literal"]: t == lit) if "literal" in n
                 else (lambda t, rx=re.compile(n["pattern"]): bool(rx.match(t))))
        ctx = re.compile(n["context"]) if n.get("context") else None
        rules.append((match, ctx))
    return rules


def orphan_decimals(doc: str, forms: set[str], rules: list[tuple],
                    withdrawn: set[str] | None = None) -> list[tuple[str, int]]:
    raw = (ROOT / doc).read_text(encoding="utf-8")
    text = GENERATED_REGION.sub(_mask, FENCE.sub(_mask, raw))
    lines = text.splitlines()
    admissible = set(forms)
    if withdrawn and carries_withdrawal_banner(raw):
        admissible |= withdrawn
    out = []
    for m in DECIMAL.finditer(text):
        token = m.group(0)
        lineno = text[: m.start()].count("\n") + 1
        line = lines[lineno - 1] if lineno <= len(lines) else ""
        if token in admissible:
            continue
        if any(match(token) and (ctx is None or ctx.search(line)) for match, ctx in rules):
            continue
        out.append((token, lineno))
    return out


def pages_under_the_rule(manifest: dict) -> list[str]:
    docs = manifest.get("documents", {})
    return sorted(d for d, e in docs.items()
                  if e.get("generator") or e.get("governance") == "governed")


def test_no_stand_page_prints_a_decimal_the_register_lost() -> None:
    print("\n- every decimal on a stand's page is one the register still vouches for -")
    forms = vouched_forms(MANIFEST)
    withdrawn = withdrawn_forms(MANIFEST)
    rules = non_metric_rules(MANIFEST)
    pages = pages_under_the_rule(MANIFEST)
    check("the rule covers the study pages produced by a stand", len(pages) >= 5, str(len(pages)))
    for doc in pages:
        if not (ROOT / doc).is_file():
            check(f"{doc} exists", False)
            continue
        orphans = orphan_decimals(doc, forms, rules, withdrawn)
        detail = "; ".join(f"{t} (line {ln})" for t, ln in orphans[:6])
        check(f"{doc}: no decimal outside a generated region is unvouched", not orphans, detail)


def test_the_rule_bites() -> None:
    """A copy of a live claim's figure with one digit changed is an orphan; the same figure inside a
    generated region or a fence is not this suite's business."""
    print("\n- the rule catches what it is for -")
    man = MANIFEST
    live = next((str(c["printed"][0]) for c in MANIFEST["claims"]
                 if not c.get("stale") and c.get("printed")
                 and re.fullmatch(r"0\.\d{3}", str(c["printed"][0]))), None)
    if live is None:
        #: Stage D left no live claim with a three-decimal figure (only two declarations are live);
        #: the rule is exercised on a copy of the register carrying one synthetic live figure, so it
        #: is never green for want of something to vouch for.
        man = json.loads(json.dumps(MANIFEST))
        man["claims"].append({"id": "probe.live", "printed": ["0.482"], "value": 0.482})
        live = "0.482"
    forms = vouched_forms(man)
    rules = non_metric_rules(man)
    drifted = live[:-1] + ("1" if live[-1] != "1" else "2")
    while drifted in forms:                              # a coincidence with another live form
        drifted = drifted[:-1] + str((int(drifted[-1]) + 1) % 10)
    check("a live claim's own figure passes", live in forms, live)
    check("a one-digit drift of it does not", drifted not in forms, drifted)
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        rel = Path(tmp).relative_to(ROOT).as_posix() + "/page.md"
        page = ROOT / rel
        page.write_text(f"prose {drifted} here\n\n```\n{drifted}\n```\n\n"
                        f"<!-- claims:x -->\n| {drifted} |\n<!-- /claims:x -->\n", encoding="utf-8")
        found = orphan_decimals(rel, forms, rules)
        check("the orphan in prose is reported once, at its line",
              [(t, ln) for t, ln in found] == [(drifted, 1)], str(found))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


if __name__ == "__main__":
    test_no_stand_page_prints_a_decimal_the_register_lost()
    test_the_rule_bites()
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
