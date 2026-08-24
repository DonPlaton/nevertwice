#!/usr/bin/env python3
"""The evidence contract: no public number without traceable evidence.

`research/evidence_manifest.json` is the register. This suite enforces three things:

1. **Schema** - every claim carries the fields that make it checkable, and every
   dataset/environment reference resolves.
2. **Agreement with the raw results** - a claim that points into a committed result
   file must equal what that file actually says. Transcription drift dies here.
3. **Coverage** - every number printed in the documents the manifest claims to cover
   resolves to a claim, a quoted third-party citation, a declared non-metric, or a
   recorded drift entry. An unaccounted number is a claim with no evidence.

Task B2 renders the documents from the manifest; until then the drift register is
how a stale published figure stays visible instead of quietly standing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

REQUIRED = ("id", "statement", "value", "printed", "unit", "dataset", "environment",
            "n", "command", "raw", "pointer", "commit", "ci", "cited_in")

# Fenced blocks are shell commands and illustrative terminal output, not measurements.
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def resolve(data, pointer: str):
    """Walk a dotted pointer with [index] segments, e.g. `a.b[2].c`."""
    node = data
    for part in pointer.split("."):
        while part.endswith("]"):
            part, _, idx = part[:-1].rpartition("[")
            if part:
                node = node[part]
            node = node[int(idx)]
            part = ""
        if part:
            node = node[part]
    return node


def test_schema_is_complete() -> None:
    print("\n- manifest schema -")
    check("schema_version is present", MANIFEST.get("schema_version") == 1)
    claims = MANIFEST["claims"]
    check("the manifest carries claims", len(claims) > 50, str(len(claims)))

    missing = [c.get("id", "?") for c in claims
               if any(f not in c for f in REQUIRED)]
    check("every claim carries the required fields", not missing,
          ", ".join(missing[:5]))

    ids = [c["id"] for c in claims]
    check("claim ids are unique", len(ids) == len(set(ids)),
          ", ".join(sorted({i for i in ids if ids.count(i) > 1})))

    bad_printed = [c["id"] for c in claims
                   if not c["printed"] or not all(isinstance(p, str) and p.strip()
                                                  for p in c["printed"])]
    check("every claim declares how it is printed", not bad_printed,
          ", ".join(bad_printed[:5]))

    unknown_ds = sorted({c["dataset"] for c in claims
                         if c["dataset"] and c["dataset"] not in MANIFEST["datasets"]})
    check("every dataset reference resolves", not unknown_ds, ", ".join(unknown_ds))

    unknown_env = sorted({c["environment"] for c in claims
                          if c["environment"]
                          and c["environment"] not in MANIFEST["environments"]})
    check("every environment reference resolves", not unknown_env,
          ", ".join(unknown_env))

    unscoped = sorted({d for c in claims for d in c["cited_in"]
                       if d not in MANIFEST["scope"]["docs"]})
    check("claims are only cited in documents the manifest covers", not unscoped,
          ", ".join(unscoped))


def test_untraceable_claims_say_so() -> None:
    print("\n- a claim with no raw file must admit it -")
    silent = [c["id"] for c in MANIFEST["claims"]
              if not c["raw"] and not c.get("raw_gap")]
    check("no claim lacks both a raw file and a stated reason", not silent,
          ", ".join(silent[:5]))

    derived = [c["id"] for c in MANIFEST["claims"]
               if c["raw"] and not c["pointer"] and not c.get("derivation")]
    check("a claim with a raw file but no pointer explains its derivation",
          not derived, ", ".join(derived[:5]))

    gaps = [c["id"] for c in MANIFEST["claims"] if c.get("raw_gap")]
    print(f"       ({len(gaps)} of {len(MANIFEST['claims'])} claims have no committed "
          f"raw artifact - each states why)")


def test_pointers_match_the_raw_results() -> None:
    print("\n- every pointer equals what the raw file says -")
    cache: dict[str, object] = {}
    mismatched: list[str] = []
    unreadable: list[str] = []
    checked = 0

    for c in MANIFEST["claims"]:
        if not c["raw"] or not c["pointer"]:
            continue
        path = ROOT / c["raw"]
        if c["raw"] not in cache:
            try:
                cache[c["raw"]] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                cache[c["raw"]] = exc
        data = cache[c["raw"]]
        if isinstance(data, Exception):
            unreadable.append(f"{c['id']} -> {c['raw']}")
            continue
        try:
            actual = resolve(data, c["pointer"])
        except (KeyError, IndexError, TypeError):
            unreadable.append(f"{c['id']} -> {c['raw']}:{c['pointer']}")
            continue
        checked += 1
        if isinstance(actual, float) or isinstance(c["value"], float):
            same = abs(float(actual) - float(c["value"])) < 1e-9
        else:
            same = actual == c["value"]
        if not same:
            mismatched.append(f"{c['id']}: manifest {c['value']!r} vs raw {actual!r}")

    check("every raw pointer resolves", not unreadable, "; ".join(unreadable[:4]))
    check(f"every one of the {checked} pointed claims matches its raw result",
          not mismatched, "; ".join(mismatched[:4]))


def test_confidence_intervals_are_sane() -> None:
    print("\n- declared intervals bracket their value -")
    broken = []
    for c in MANIFEST["claims"]:
        ci = c.get("ci")
        if not ci:
            continue
        if not (ci["low"] <= float(c["value"]) <= ci["high"]):
            broken.append(f"{c['id']}: {ci['low']}..{ci['high']} excludes {c['value']}")
    check("every interval contains its point estimate", not broken,
          "; ".join(broken[:4]))

    silent = [c["id"] for c in MANIFEST["claims"]
              if not c.get("ci") and not c.get("ci_note") and c["n"]
              and isinstance(c["n"], int) and c["unit"].startswith(("recall", "accuracy"))]
    check("a proportion with a sample size has an interval or a stated reason",
          not silent, ", ".join(silent[:5]))


def _accounted_forms() -> tuple[set[str], list[tuple]]:
    forms: set[str] = set()
    for entry in MANIFEST["claims"] + MANIFEST["external_citations"]:
        for p in entry["printed"]:
            p = p.strip()
            forms |= {p, p.lstrip("+−-"), p.replace(",", ""),
                      p.rstrip("%x×").rstrip(" ms").strip()}
    for d in MANIFEST["drift"]:
        for p in d["printed"]:
            forms |= {p.strip(), p.replace(",", "")}
    # A non-metric may narrow itself to the lines it applies to. Ignoring that
    # `context` would let a broad pattern silently swallow real claims - which is
    # exactly what a mutation caught when this check first ran.
    rules = []
    for n in MANIFEST["non_metrics"]:
        match = ((lambda t, lit=n["literal"]: t == lit) if "literal" in n
                 else (lambda t, rx=re.compile(n["pattern"]): bool(rx.match(t))))
        ctx = re.compile(n["context"]) if n.get("context") else None
        rules.append((match, ctx))
    return forms, rules


def test_every_printed_number_is_accounted_for() -> None:
    print("\n- 100% of printed numbers resolve to evidence -")
    forms, rules = _accounted_forms()
    total = accounted = 0
    for doc in MANIFEST["scope"]["docs"]:
        text = FENCE.sub("", (ROOT / doc).read_text(encoding="utf-8"))
        lines = text.splitlines()
        orphans: dict[str, list[int]] = {}
        for m in NUMBER.finditer(text):
            token = m.group(0)
            total += 1
            lineno = text[:m.start()].count("\n") + 1
            line = lines[lineno - 1] if lineno <= len(lines) else ""
            if (token in forms or token.replace(",", "") in forms
                    or any(match(token) and (ctx is None or ctx.search(line))
                           for match, ctx in rules)):
                accounted += 1
                continue
            orphans.setdefault(token, []).append(lineno)
        detail = "; ".join(f"{t} (line {ls[0]})" for t, ls in
                           sorted(orphans.items())[:6])
        check(f"{doc}: every printed number is accounted for", not orphans, detail)
    check(f"overall coverage is 100% ({accounted}/{total})", accounted == total,
          f"{accounted}/{total}")


def test_drift_entries_carry_the_correct_value() -> None:
    print("\n- the drift register is usable -")
    incomplete = [d.get("doc", "?") for d in MANIFEST["drift"]
                  if not (d.get("printed") and d.get("claims")
                          and d.get("correct") and d.get("note"))]
    check("every drift entry names the doc, the claims and the correct value",
          not incomplete, ", ".join(incomplete[:4]))

    scoped = [d["doc"] for d in MANIFEST["drift"]
              if d["doc"] not in MANIFEST["scope"]["docs"]]
    check("drift is only recorded for documents in scope", not scoped,
          ", ".join(scoped))

    still_printed = []
    for d in MANIFEST["drift"]:
        text = FENCE.sub("", (ROOT / d["doc"]).read_text(encoding="utf-8"))
        if not any(p in text for p in d["printed"]):
            still_printed.append(d["doc"])
    check("every drift entry still describes what the document prints",
          not still_printed,
          ", ".join(still_printed) + " (entry is stale - the doc was fixed; "
          "delete the entry)")
    print(f"       ({len(MANIFEST['drift'])} drift entries open - task B2 regenerates "
          f"these tables from the manifest)")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nevidence manifest: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
