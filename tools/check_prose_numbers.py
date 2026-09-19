#!/usr/bin/env python3
"""Every number in hand-written research prose, against the evidence its own page names.

Part 4.3 of `.loop/GOAL-FINISH-B.md` (track T3): the K8-C campaign left three sentences standing
directly above generated tables that refuted them, and the register's own statements were found on
2026-09-19 quoting percentages from a third draw. Generated `<!-- claims:... -->` blocks cannot lag -
they are rewritten from the register on every run. What lags is the prose around them, and nothing
was checking it.

Two obvious checks are wrong, in opposite directions, and are recorded here so they are not tried
again. Searching every artifact for the literal matches an embedding coordinate: `2.1686` appears
inside `locomo_embeds.json` and proves nothing about a page on commit-scan latency. Searching for the
literal in the page's own artifact fails the moment the page rounds, because prose "0.896" never
matches a stored `0.89634`. This walks the numeric leaves of the artifacts the page itself names and
asks whether any of them, rounded to the precision the prose used, equals what the prose printed -
which is what a reader does when they check a page against its evidence.

An unmatched line is not automatically wrong. A page may state a run condition (free VRAM, wall
clock), a constant read from code, a count taken from a corpus, or a competitor's published figure.
What it may not do is state one silently: the residue this prints is the list a human has to read,
and `tests/_test_prose_numbers.py` keeps it from growing.

    python tools/check_prose_numbers.py            # the residue, page by page
    python tools/check_prose_numbers.py --json     # machine-readable, for the suite
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
REF = re.compile(r"[\w/\.-]+\.json")

NUM = re.compile(r"(?<![\w/.-])\d+(?:[.,]\d+)?%?(?![\w/-])")
YEARISH = re.compile(r"^(19|20)\d\d$")


def _live_printed() -> dict[str, list[str]]:
    """Every form a live claim would render, so a prose number that matches one is sourced."""
    man = json.loads((ROOT / "research/evidence_manifest.json").read_text(encoding="utf-8"))
    printed: dict[str, list[str]] = {}
    for c in man["claims"]:
        if c.get("stale") or c.get("pending_remeasure"):
            continue
        for form in c.get("printed") or []:
            printed.setdefault(str(form).lstrip("0") or "0", []).append(c["id"])
        v = c.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            for form in (f"{v}", f"{v:.3f}", f"{v:.2f}", f"{v:.1f}", f"{v * 100:.1f}", f"{v * 100:.0f}"):
                printed.setdefault(form.lstrip("0") or "0", []).append(c["id"])
    return printed


def _inventory() -> list[dict]:
    """One row per line of hand-written prose that states a number a live claim does not print.

    Generated blocks, fenced commands and table rows are out: the first cannot lag, the second is a
    command rather than a claim, and the third is checked as a table elsewhere.
    """
    printed = _live_printed()
    rows = []
    for page in sorted((ROOT / "research").glob("*.md")):
        in_claims = in_fence = False
        for i, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if "<!-- claims:" in s:
                in_claims = True
                continue
            if "<!-- /claims:" in s:
                in_claims = False
                continue
            if in_claims or in_fence or s.startswith("|") or not s:
                continue
            nums = [n for n in NUM.findall(s) if not YEARISH.match(n.rstrip("%"))]
            if not nums:
                continue
            unsourced = [n for n in nums
                         if n.rstrip("%").lstrip("0") not in printed and n.rstrip("%") not in printed]
            if unsourced:
                rows.append({"page": page.name, "line": i, "nums": nums,
                             "unsourced": unsourced, "text": s[:200]})
    return rows


rows = _inventory()
leaves: dict[str, set[float]] = {}


def numbers_in(obj, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            try:
                out.add(float(k))
            except (TypeError, ValueError):
                pass
            numbers_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            numbers_in(v, out)
    elif isinstance(obj, str):
        for tok in re.findall(r"-?\d+(?:\.\d+)?", obj):
            try:
                out.add(float(tok))
            except ValueError:
                pass


def artifact_numbers(rel: str) -> set[float]:
    if rel not in leaves:
        got: set[float] = set()
        for base in (ROOT, ROOT / "research"):
            p = base / rel
            if p.is_file():
                try:
                    numbers_in(json.loads(p.read_text(encoding="utf-8")), got)
                except (OSError, ValueError):
                    pass
                break
        leaves[rel] = got
    return leaves[rel]


def matches(tok: str, pool: set[float]) -> bool:
    t = tok.rstrip("%").replace(",", "")
    try:
        want = float(t)
    except ValueError:
        return False
    dp = len(t.split(".")[1]) if "." in t else 0
    cands = [want, want / 100] if tok.endswith("%") else [want, want / 100, want * 100]
    for c in cands:
        for v in pool:
            if round(v, dp) == round(c, dp):
                return True
            if dp == 0 and abs(v - c) < 0.5:
                return True
    return False


own: dict[str, list[str]] = {}
for page in sorted((ROOT / "research").glob("*.md")):
    own[page.name] = sorted(set(REF.findall(page.read_text(encoding="utf-8"))))

orphans = []
for r in rows:
    if not r["unsourced"]:
        continue
    pool: set[float] = set()
    for rel in own.get(r["page"]) or []:
        pool |= artifact_numbers(rel)
    still = [t for t in r["unsourced"] if not matches(t, pool)]
    if still:
        orphans.append({**r, "unsourced": still, "page_artifacts": (own.get(r["page"]) or [])[:5]})

if "--json" in sys.argv:
    print(json.dumps(orphans, indent=1, ensure_ascii=False))
else:
    print(f"{len(orphans)} line(s) state a number that no numeric leaf of the page's own artifacts "
          f"rounds to")
    by_page: dict[str, int] = {}
    for r in orphans:
        by_page[r["page"]] = by_page.get(r["page"], 0) + 1
    for page, n in sorted(by_page.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {page}  (names {len(own.get(page) or [])} artifact(s))")
    if "--list" in sys.argv:
        print()
        for r in orphans:
            print(f"{r['page']}:{r['line']} -> {r['unsourced']}")
            print(f"    {r['text'][:160]}")
