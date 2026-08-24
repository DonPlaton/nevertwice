#!/usr/bin/env python3
"""The roadmap does not promise things that already shipped.

A roadmap rots in one direction: an item gets built, the entry stays, and the document
keeps advertising work that is finished while a reader uses it to judge what the project
can do. It is a small dishonesty with a long half-life, because nothing fails when it
happens.

Two rules make it fail here:

* every open item states **Today:** what already exists, so the current state has to be
  written down next to the promise rather than left to the reader;
* a feature the CHANGELOG records as shipped may appear in the *Today* half of an item,
  where it describes reality, but never in the promise half.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

ROADMAP = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

# Sections whose bullets are promises. "Not planned" is the opposite of a promise, and
# "Blocked on the maintainer" is a promise too - it just names who it is waiting on.
PROMISE_SECTIONS = ("Blocked on the maintainer", "Near term", "Exploring")
TODAY = "**Today:**"

# Things that exist now. Each is a phrase that would be a lie as an open promise. The
# CHANGELOG entry is named so this list can be audited against it rather than trusted.
SHIPPED: list[tuple[str, str]] = [
    ("tagged-release workflow", "Release automation."),
    ("Trusted Publishing plus", "Release automation."),
    ("evidence manifest", "Every published number now resolves to its evidence."),
    ("baseline gates", "Baseline gates are written policy, and machine-checked."),
    ("comparison snapshot", "The comparison document is generated and dated."),
    ("generated from the manifest", "The published tables are generated, not typed."),
    ("version contract", "The version contract is enforced, not documented."),
]

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def bullets() -> list[tuple[str, str]]:
    """(section, bullet text) for every top-level bullet, sections included."""
    out: list[tuple[str, str]] = []
    section = ""
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append((section, " ".join(x.strip() for x in current)))
            current.clear()

    for line in ROADMAP.splitlines():
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
        elif line.startswith("- "):
            flush()
            current.append(line[2:])
        elif current and line.startswith("  "):
            current.append(line)
        elif not line.strip():
            flush()
    flush()
    return out


BULLETS = bullets()


def test_the_roadmap_has_the_sections_it_claims() -> None:
    print("\n- the roadmap is structured as expected -")
    for section in PROMISE_SECTIONS:
        check(f"the {section!r} section exists", f"## {section}" in ROADMAP)
    check("a 'Not planned' section exists", "## Not planned" in ROADMAP)
    check("bullets were parsed", len(BULLETS) >= 6, str(len(BULLETS)))


def test_every_promise_states_what_exists_today() -> None:
    """Without this, an item can stay accurate-sounding for a year after it shipped."""
    print("\n- every open item states its current state -")
    silent = [text[:60] for section, text in BULLETS
              if section in PROMISE_SECTIONS and TODAY not in text]
    check("every promise carries a 'Today:' clause", not silent,
          "; ".join(silent[:3]))

    thin = [text.split(TODAY, 1)[1][:40] for section, text in BULLETS
            if section in PROMISE_SECTIONS and TODAY in text
            and len(text.split(TODAY, 1)[1].strip()) < 40]
    check("each 'Today:' clause actually describes something", not thin,
          "; ".join(thin[:3]))


def test_no_shipped_feature_is_still_promised() -> None:
    print("\n- nothing already shipped is listed as future work -")
    offenders = []
    for section, text in BULLETS:
        if section not in PROMISE_SECTIONS:
            continue
        promise = text.split(TODAY, 1)[0]
        for phrase, changelog_hint in SHIPPED:
            if phrase.lower() in promise.lower():
                offenders.append(f"{section}: {phrase!r} (shipped: {changelog_hint})")
    check("no promise names a shipped feature", not offenders,
          "; ".join(offenders[:4]))


def test_the_shipped_list_is_honest() -> None:
    """A shipped marker that the CHANGELOG does not corroborate would let this suite
    forbid something that was never actually built."""
    print("\n- the shipped list is corroborated by the changelog -")
    uncorroborated = [hint for _, hint in SHIPPED
                      if hint.lower() not in CHANGELOG.lower()]
    check("every shipped marker points at a changelog entry", not uncorroborated,
          "; ".join(sorted(set(uncorroborated))[:4]))


def test_the_roadmap_does_not_contradict_the_project() -> None:
    """The one contradiction this document actually had: it listed LoCoMo as a candidate
    protocol while the research pages call it discredited."""
    print("\n- the roadmap agrees with the research it links to -")
    if "LoCoMo" in ROADMAP:
        window = ROADMAP[max(0, ROADMAP.find("LoCoMo") - 400):
                         ROADMAP.find("LoCoMo") + 400]
        check("LoCoMo is named as excluded, not as a candidate",
              "not** a candidate" in window or "not a candidate" in window,
              "LoCoMo is mentioned without saying it is excluded")

    check("the blocked item names what unblocks it",
          "Trusted Publishing" in ROADMAP and "maintainer" in ROADMAP.lower())


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nroadmap: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
