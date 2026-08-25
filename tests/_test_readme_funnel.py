#!/usr/bin/env python3
"""The README is a funnel, not an encyclopedia.

At 468 lines it was a reference manual with a banner on top: the acquisition story - what this
is, proof that it works, how to install it - was interleaved with per-agent setup, the full
benchmark commentary, the import recipes and the feature inventory, all of which already had
homes under `docs/`. A stranger had to read a third of it before reaching a reason to care.

Two rules keep it that way, and one keeps the shortening honest:

* **≤ 180 lines**, with the whole acquisition story inside the first 120 - banner, one sentence,
  the install command, the demo transcript, the differentiator, the evidence, the architecture;
* **every link resolves**, in the README and in the docs map it points at;
* **nothing was deleted, only moved** - each topic that left the README is asserted to exist in
  the document the README now sends the reader to. Shortening a page by dropping what it
  documented is not a funnel, it is a regression, and it is the failure this file exists to
  catch.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

README = ROOT / "README.md"
TEXT = README.read_text(encoding="utf-8")
LINES = TEXT.splitlines()
FIRST_SCREEN = "\n".join(LINES[:120])

MAX_LINES = 180
ACQUISITION_LINES = 120

# `]( ... )` rather than a full `[text](target)`: badges nest an image inside the link,
# and a regex that requires balanced brackets silently skips every one of them.
LINK = re.compile(r"\]\(([^)\s]+)")
HEADING = re.compile(r"^#+\s+(.*)$", re.M)

# What the first 120 lines have to carry, as a phrase that must literally appear there. Each is
# one beat of the funnel: what it is, how to get it, that it works, and what it costs.
ACQUISITION = {
    "the install command": "pip install nevertwice",
    "the demo entry point": "examples/guard_demo.py",
    "the guard firing (the product)": "repeat flagged",
    "the corrected action staying clean": "correction clean",
    "the zero-token claim": "zero context tokens until it does",
    "the measured headline": "86%",
    "the head-to-head table": "<!-- claims:head-to-head -->",
    "the architecture visual": "the agent recollects, and does not repeat itself",
}

# Topic that left the README -> (the document it moved to, a phrase proving it arrived).
# A shortened README that simply dropped these would pass a line-count check and fail here.
RELOCATED = {
    "the digest / conflicts commands": ("docs/FEATURES.md", "nevertwice.digest --conflicts"),
    "the offline HTML dashboard": ("docs/FEATURES.md", "nevertwice.dashboard"),
    "bi-temporal queries": ("docs/FEATURES.md", "what did we believe on March 3"),
    "the AGENTS.md / OKF export": ("docs/FEATURES.md", "Open Knowledge Format"),
    "the project bootstrapper": ("docs/FEATURES.md", "bootstrap_contexts"),
    "what we measured and cut": ("docs/FEATURES.md", "abstractive consolidation"),
    "the Brain layer": ("docs/FEATURES.md", "NEVERTWICE_PROFILE=research"),
    "importing another tool's memory": ("docs/INTEGRATIONS.md", "nevertwice-import"),
    "the per-agent setup recipes": ("docs/INTEGRATIONS.md", "nevertwice-watch"),
    "the vendor comparison table": ("docs/COMPARISON.md", "Differentiators"),
    "the retrieval-fusion commentary": ("docs/BENCHMARKS.md", "calibrated score fusion"),
}

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s+", "-", text.strip())


def test_the_readme_fits_the_funnel_budget() -> None:
    print("\n- the README is short enough to be read -")
    check(f"the README is at most {MAX_LINES} lines", len(LINES) <= MAX_LINES,
          f"{len(LINES)} lines")
    for name, phrase in ACQUISITION.items():
        check(f"the first {ACQUISITION_LINES} lines carry {name}", phrase in FIRST_SCREEN,
              "missing or below the fold"
              if phrase not in TEXT else "present, but past line 120")


def test_depth_moved_rather_than_disappeared() -> None:
    """The check that makes the line budget safe to enforce."""
    print("\n- everything that left the README landed somewhere -")
    for topic, (doc, phrase) in RELOCATED.items():
        path = ROOT / doc
        if not path.exists():
            check(f"{topic} -> {doc}", False, "the destination does not exist")
            continue
        body = path.read_text(encoding="utf-8")
        check(f"{topic} is in {doc}", phrase.lower() in body.lower(),
              f"{phrase!r} not found")
    linked = {m for m in LINK.findall(TEXT)}
    for doc in sorted({d for d, _ in RELOCATED.values()}):
        reachable = doc in linked or any(doc.split("/")[-1] in link for link in linked)
        check(f"the README still points at {doc}", reachable)


def test_every_link_resolves() -> None:
    print("\n- no link in the funnel is broken -")
    for source in (README, ROOT / "docs" / "README.md"):
        base = source.parent
        broken = []
        for target in LINK.findall(source.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue                      # a pure in-page anchor, checked below
            if not (base / path).exists():
                broken.append(target)
        check(f"{source.relative_to(ROOT).as_posix()}: every relative link resolves",
              not broken, ", ".join(broken[:5]))


def test_every_in_page_anchor_resolves() -> None:
    """The badges link into the page. A renamed section silently breaks them."""
    print("\n- the badges point at sections that exist -")
    slugs = {slug(h) for h in HEADING.findall(TEXT)}
    anchors = sorted({t[1:] for t in LINK.findall(TEXT) if t.startswith("#")})
    check("badge anchors were found", len(anchors) >= 3, str(anchors))
    missing = [a for a in anchors if a not in slugs]
    check("every in-page anchor names a real heading", not missing,
          f"{missing} not in {sorted(slugs)}")


def test_the_new_page_is_not_an_orphan() -> None:
    print("\n- the relocated depth is reachable from the map -")
    map_text = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    check("docs/README.md lists FEATURES.md", "FEATURES.md" in map_text)
    check("the README links FEATURES.md directly", "docs/FEATURES.md" in TEXT)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nreadme funnel: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
