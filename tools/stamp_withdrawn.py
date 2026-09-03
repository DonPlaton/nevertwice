"""Every page that prints a withdrawn figure has to say so, not just the front ones.

`docs/COMPARISON.md` got a withdrawal banner by hand in 2026-09, and writing it made the
real shape of the problem visible: **forty-four** registered pages print at least one figure
the evidence register marks withdrawn, and none of them tell the reader. The front-page
documents are governed, so their numbers are generated from live claims and cannot drift.
The study archive is `backlog`, which caps how many unregistered numbers a page may print
but says nothing about whether the numbers it prints are still true.

That is the wrong way round. A retracted result is exactly the thing a reader is most likely
to quote back at you, and the study pages are where the detail lives, so they are where
someone goes to check.

This tool stamps the banner, and `tests/_test_withdrawn_pages.py` fails if a page stops
carrying one it needs. Neither deletes anything: a project that removes its retracted results
loses the record of having been wrong, which is the only part of being wrong that is useful.

    python tools/stamp_withdrawn.py            # report, change nothing
    python tools/stamp_withdrawn.py --apply
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: Fenced blocks are commands and sample output - a number there is an illustration of a
#: command, not a claim the page is making.
FENCE = re.compile(r"```.*?```", re.S)

#: Any of these anywhere on the page means the page already qualifies its own numbers.
MARKERS = ("withdrawn", "WITHDRAWN", "retracted", "RETRACTED")

BANNER_ID = "<!-- withdrawn-banner -->"
#: No count in the banner, for two reasons. A count is itself a number on the page, and four
#: `backlog` pages went one over a budget that may only shrink. It is also a number that goes
#: stale the moment the register changes - a correction that needs correcting.
BANNER = (
    BANNER_ID + "\n"
    "> **Withdrawn: figures on this page must not be quoted.** They were retracted in 2026-08\n"
    "> and remain here because deleting a result one was wrong about destroys the record of\n"
    "> having been wrong. The design, the method and the caveats stand; the numbers do not.\n"
    "> Each figure's own reason is in\n"
    "> [`research/evidence_manifest.json`]({link}), and\n"
    "> `python tools/check_freshness.py --list-stale` lists every one.\n"
)


def _link_from(rel: str) -> str:
    """The register, reached from the page being stamped.

    One hard-coded `../research/...` is right for `docs/` and wrong for both of the other
    depths the register covers. A dead link inside a correction is worse than none: it reads
    as a citation and resolves to nothing.
    """
    here = posixpath.dirname(rel.replace("\\", "/")) or "."
    return posixpath.relpath("research/evidence_manifest.json", here)


#: Three or more decimals, or a two-digit percentage. `0.80` and `0.05` are numbers this
#: repository prints about a dozen unrelated things.
_PRECISE = re.compile(r"^\d+\.\d{3,}$|^\d{2,}(\.\d+)?%$")
#: Two decimals: weak on its own, usable in company.
_COARSE = re.compile(r"^\d+\.\d{2}$|^\d+(\.\d+)?%$")
#: A mantissa of nothing but zeros identifies no study.
_ROUND = re.compile(r"^[01]\.0+$")


def _strength(printed: str) -> int:
    """How much evidence one matched form is that the page cited that study.

    A first pass took any form with a dot in it and flagged forty-five pages, several of
    them on `0.000` and `1.000` - values that appear wherever a rate is perfect or a count
    is empty, and identify nothing. Stamping "figures on this page are withdrawn" onto a
    page that never cited the study is its own false statement, so the bar is: one precise
    form is enough, two coarse ones are enough, and one coarse one is not.
    """
    p = printed.strip()
    if _ROUND.match(p):
        return 0
    if _PRECISE.match(p):
        return 2
    if _COARSE.match(p):
        return 1
    return 0


def _distinctive(printed: str) -> bool:
    return _strength(printed) > 0


def withdrawn_claims(manifest: dict) -> list[dict]:
    return [c for c in manifest["claims"] if c.get("withdrawn_on") or c.get("stale")]


def live_forms(manifest: dict) -> set[str]:
    """Every form a still-published claim prints.

    A page printing one of these is printing a live number, whatever else happens to share the
    value. `research/LOCOMO.md` prints 0.428 as its lexical R@3 and a withdrawn head-to-head
    figure is also 0.428; without this the page earns a retraction banner for figures measured
    the day before. The freshness guard has always worked this way and this one did not.
    """
    forms: set[str] = set()
    for c in manifest["claims"]:
        if c.get("withdrawn_on") or c.get("stale"):
            continue
        for printed in c.get("printed", []):
            forms.add(str(printed).strip())
    return forms


def figures_on_page(text: str, claims: list[dict], live: set[str] | None = None
                    ) -> list[tuple[str, str]]:
    body = FENCE.sub(" ", text)
    live = live or set()
    found = []
    for c in claims:
        for printed in c.get("printed", []):
            p = str(printed)
            if p.strip() in live or not _distinctive(p):
                continue
            if re.search(rf"(?<![\w.]){re.escape(p)}(?![\w])", body):
                found.append((c["id"], p))
                break
    return found


#: One high-precision figure is a citation. Any number of coarse ones are not.
#:
#: The rule started as "two points, so two coarse forms count", and `research/ABSTENTION_AB.md`
#: showed why that is wrong: its threshold sweep runs over 0.35, 0.75 and 0.90, three round
#: numbers that happen to equal three withdrawn recall figures from studies it never cites.
#: Two coincidences are not evidence of one citation - they are evidence that round numbers
#: are common. A page whose only overlap is coarse escapes the stamp, and that is the right
#: error to make: a false retraction notice is a false statement, while a missed one leaves
#: the page as it already was.
STAMP_AT = 2


def needs_banner(path: Path, claims: list[dict], live: set[str] | None = None
                 ) -> list[tuple[str, str]]:
    """The figures that oblige this page to carry a banner, or an empty list."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if any(m in text for m in MARKERS):
        return []
    figs = figures_on_page(text, claims, live)
    return figs if any(_strength(pr) >= STAMP_AT for _cid, pr in figs) else []


def insert(text: str, link: str) -> str:
    """Put the banner under the page title, where the reader is already looking.

    Under, not above: a banner before the H1 makes the page look like a notice rather than a
    study, and the studies are still worth reading.
    """
    banner = BANNER.format(link=link)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            rest = lines[i + 1:]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "".join(lines[:i + 1]) + "\n" + banner + "\n" + "".join(rest)
    return banner + "\n" + text


def remove(text: str) -> str:
    """Strip a banner this page no longer earns.

    Tightening the rule left eleven pages carrying a retraction notice justified only by
    round numbers they never cited. A false retraction is as wrong as a missing one, and
    worse in one way: it invites a reader to distrust results that are fine.
    """
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(BANNER_ID):
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the banners (default: report)")
    ap.add_argument("--unstamp", action="store_true",
                    help="also remove banners from pages the current rule would not stamp")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = withdrawn_claims(manifest)
    live = live_forms(manifest)
    todo = []
    for rel, entry in sorted(manifest["documents"].items()):
        if entry.get("governance") == "exempt":
            continue
        p = ROOT / rel
        if not p.exists():
            continue
        figs = needs_banner(p, claims, live)
        if figs:
            todo.append((rel, p, figs))

    print(f"{len(claims)} withdrawn claims; {len(todo)} page(s) print one without saying so")
    for rel, _p, figs in todo:
        ids = sorted({cid for cid, _ in figs})
        print(f"  {rel:52s} {len(figs):>3} figures  e.g. {ids[0]}")

    if not args.apply:
        if todo:
            print("\nnothing written - rerun with --apply")
        return 1 if todo else 0

    for rel, p, figs in todo:
        p.write_text(insert(p.read_text(encoding="utf-8"), _link_from(rel)),
                     encoding="utf-8")
    print(f"\nstamped {len(todo)} page(s)")

    if args.unstamp:
        removed = 0
        for rel in sorted(manifest["documents"]):
            f = ROOT / rel
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if BANNER_ID not in text:
                continue
            figs = figures_on_page(text, claims, live)
            if not any(_strength(pr) >= STAMP_AT for _cid, pr in figs):
                f.write_text(remove(text), encoding="utf-8")
                print(f"  unstamped {rel} - its only overlap was round numbers")
                removed += 1
        print(f"unstamped {removed} page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
