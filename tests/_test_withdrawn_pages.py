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
    # 0.428 is LoCoMo's lexical R@3, measured and live, and also a withdrawn head-to-head
    # figure. Without the live-form rule this page earns a retraction banner for a number
    # taken yesterday.
    _live_probe.write_text("# Study\n\nlexical recall at three is 0.428.\n", encoding="utf-8")
    check("a live form does not trigger a banner",
          not sw.needs_banner(_live_probe, claims, live))
    check("and it would have without the rule",
          bool(sw.needs_banner(_live_probe, claims, set())))
finally:
    _live_probe.unlink(missing_ok=True)

print("\n- a coincidence is not treated as a citation -")
# 0.000 and 1.000 appear wherever a rate is perfect or a count is empty. Scoring them as
# evidence stamped nine pages that had never cited the study they were matched against.
check("an all-zero mantissa carries no weight", sw._strength("0.000") == 0)
check("an all-one mantissa carries no weight", sw._strength("1.000") == 0)
check("three decimals are enough on their own", sw._strength("0.788") >= sw.STAMP_AT)
check("two decimals are not enough on their own", 0 < sw._strength("0.80") < sw.STAMP_AT)

print("\n- a page that already says it is retracted is left alone -")
tmp = ROOT / "tests" / "_tmp_withdrawn_probe.md"
try:
    # 0.788 is qa.oracle.answer_accuracy: withdrawn, with no live twin. 0.422 used
    # to sit here and stopped working the day the pinned re-run reproduced it
    # exactly - a value a live claim also prints is, correctly, no longer evidence
    # of citing the withdrawn one.
    body = "# Study\n\noracle answer accuracy reached 0.788 on that set.\n"
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

print(f"\nwithdrawn pages: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
