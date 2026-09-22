#!/usr/bin/env python3
"""No registered page prints a retracted figure without saying it is retracted.

`docs/COMPARISON.md` was fixed by hand in 2026-09, and fixing it exposed the general case:
thirty-six registered pages printed at least one withdrawn figure and none of them told the
reader. The front-page documents are governed, so their numbers are generated from live
claims and cannot drift. The study archive is `backlog`, which caps how many unregistered
numbers a page may print and says nothing about whether the ones it prints are still true -
which is the wrong way round, because the studies are where someone goes to check.

This suite pins the invariant `tools/stamp_withdrawn.py` establishes. It is deliberately not
a "the banner text is exactly this" test: what matters is that a reader meets the retraction
on the page carrying the numbers, whatever words it arrives in.
"""
import _env_guard  # noqa: F401
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import stamp_withdrawn as sw  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


manifest = json.loads(sw.MANIFEST.read_text(encoding="utf-8"))
claims = sw.withdrawn_claims(manifest)
live = sw.live_forms(manifest)

print("\n- the register still has something to protect against -")
check("some claims are withdrawn", len(claims) > 0, str(len(claims)))

print("\n- no registered page quotes a withdrawn figure in silence -")
offenders = []
for rel, entry in sorted(manifest["documents"].items()):
    if entry.get("governance") == "exempt":
        continue
    p = ROOT / rel
    if p.exists() and sw.needs_banner(p, claims, live):
        offenders.append(rel)
check("every page that prints one carries a retraction", not offenders,
      ", ".join(offenders[:4]))

print("\n- the banner points somewhere that exists -")
missing = []
for md in list(ROOT.glob("docs/*.md")) + list(ROOT.glob("research/**/*.md")):
    text = md.read_text(encoding="utf-8", errors="replace")
    if sw.BANNER_ID not in text:
        continue
    for line in text.splitlines():
        if "evidence_manifest.json`](" not in line:
            continue
        target = line.split("evidence_manifest.json`](", 1)[1].split(")", 1)[0]
        if not (md.parent / target).resolve().exists():
            missing.append(f"{md.relative_to(ROOT).as_posix()} -> {target}")
check("every banner link resolves", not missing, "; ".join(missing[:3]))

print("\n- a number a live claim prints is not a withdrawn citation -")
_live_probe = ROOT / "tests" / "_tmp_live_probe.md"
try:
    # 0.788 is a withdrawn figure. Told that a live claim prints the same form (as LoCoMo's
    # lexical R@3 once shared 0.428 with a withdrawn head-to-head row), the page must not earn
    # a retraction banner for a number taken yesterday. Synthetic on purpose: which live and
    # withdrawn claims happen to coincide changes with every re-measurement.
    _live_probe.write_text("# Study\n\nlexical recall at three is 0.788.\n", encoding="utf-8")
    check("a live form does not trigger a banner",
          not sw.needs_banner(_live_probe, claims, {"0.788"}))
    check("and it would have without the rule",
          bool(sw.needs_banner(_live_probe, claims, set())))
finally:
    _live_probe.unlink(missing_ok=True)

print("\n- a coincidence is not treated as a citation -")
# 0.000 and 1.000 appear wherever a rate is perfect or a count is empty. Scoring them as
# evidence stamped nine pages that had never cited the study they were matched against.
check("an all-zero mantissa carries no weight", sw._strength("0.000") == 0)
check("an all-one mantissa carries no weight", sw._strength("1.000") == 0)
# 100% is what every held defence prints and 0% what every empty count prints; treating either
# as a citation stamped two embedding studies on 2026-09-05 for a poisoning figure they never cited.
check("an all-or-nothing rate carries no weight", sw._strength("100%") == 0 and sw._strength("0%") == 0)
check("a real percentage still does", sw._strength("81%") >= sw.STAMP_AT)

print("\n- a page that qualifies its numbers in its own words is left alone, whatever the case -")
_own = ROOT / "tests" / "_tmp_own_notice.md"
try:
    _own.write_text("# Study\n\n> **Withdrawn 2026-09-05, pending a re-run.**\n\nR@5 0.788.\n",
                    encoding="utf-8")
    check("a capitalised notice counts as a marker", not sw.needs_banner(_own, claims, live))
finally:
    _own.unlink(missing_ok=True)
check("three decimals are enough on their own", sw._strength("0.788") >= sw.STAMP_AT)
check("two decimals are not enough on their own", 0 < sw._strength("0.80") < sw.STAMP_AT)

print("\n- a page that already says it is retracted is left alone -")
tmp = ROOT / "tests" / "_tmp_withdrawn_probe.md"
try:
    # The probe value is chosen at run time: a withdrawn claim's three-decimal printed form
    # that no live claim prints. A fixed value stopped working twice - 0.422 the day the
    # pinned re-run reproduced it exactly, 0.788 the day the morphology re-run landed on it -
    # because a value a live claim also prints is, correctly, no longer evidence of citing
    # the withdrawn one.
    probe = next(f for c in claims for f in c.get("printed", [])
                 if f.count(".") == 1 and len(f.split(".")[1]) == 3 and f not in live)
    body = f"# Study\n\noracle answer accuracy reached {probe} on that set.\n"
    tmp.write_text(body, encoding="utf-8")
    check("an unmarked page is flagged", bool(sw.needs_banner(tmp, claims, live)))
    tmp.write_text(body + "\nThese figures were withdrawn in 2026-08.\n", encoding="utf-8")
    check("a marked page is not flagged again", not sw.needs_banner(tmp, claims, live))
finally:
    tmp.unlink(missing_ok=True)

print("\n- stamping is idempotent -")
sample = ROOT / "research" / "QA_ACCURACY.md"
if sample.exists():
    before = sample.read_text(encoding="utf-8")
    check("a stamped page needs no second stamp",
          not sw.needs_banner(sample, claims, live))
    check("the page was not modified by the check",
          sample.read_text(encoding="utf-8") == before)

# A live number whose evidence link lands on a retracted page
#
# The banner is a statement about the page it is on, and the check above pins that. It says
# nothing about the reader who arrives from somewhere else. The register's only cited claim is
# the token ratio in README's evidence table, whose evidence column links to
# `research/ACTIVE_MEMORY.md` - a page that opens with "figures on this page must not be
# quoted". So the one reader who does what the project asks, and follows the link to check the
# project's single published number, is told not to quote it.
#
# Nothing here is false: the claim is live, the page is retracted, and both statements are
# correct on their own. The reader gets the wrong answer from two right ones, which is why this
# needs its own check rather than a stronger banner.
print()
print("- a live number never sends the reader to a page that retracts it -")
import re  # noqa: E402

LINK = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)")
WARNS = ("withdrawn", "retracted", "still registered", "still live")

live_claims = [c for c in manifest["claims"] if not (c.get("stale") or c.get("withdrawn_on"))]
unwarned = []
checked = 0
for c in live_claims:
    forms = [str(f) for f in (c.get("printed") or []) if str(f).strip()]
    for rel in (c.get("cited_in") or []):
        page = ROOT / rel
        if not page.exists():
            continue
        for line in page.read_text(encoding="utf-8").splitlines():
            if not any(f in line for f in forms):
                continue
            for target in LINK.findall(line):
                dest = (page.parent / target).resolve()
                if not dest.is_file() or dest.suffix != ".md":
                    continue
                checked += 1
                head = dest.read_text(encoding="utf-8", errors="replace")[:4000].lower()
                if not any(m in head for m in sw.MARKERS):
                    continue
                if not any(w in line.lower() for w in WARNS):
                    unwarned.append(f"{rel}: {c['id']} -> {target}")

check(f"a live number's evidence link says so when it lands on a retracted page "
      f"({checked} link(s) checked)", not unwarned, "; ".join(unwarned[:4]))
check("and at least one such link exists, so the check above proved something", checked > 0,
      "no live claim's citation carries a link to a markdown page")

print(f"\nwithdrawn pages: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
