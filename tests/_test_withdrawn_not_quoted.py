#!/usr/bin/env python3
"""A page that renders withdrawn numbers must say so, in the reader's line of sight.

docs/COMPARISON.md renders the July head-to-head — Nevertwice ahead of Mem0, LangMem and
A-MEM on every column — while the evidence register marks all sixteen of those figures
withdrawn. The test that forbids citing a withdrawn claim does not look at that page,
because it is registered `backlog` rather than `governed`, so the retraction never reached
the reader. Someone cloning the repository saw retracted results presented as current.

The banner lives OUTSIDE the generated regions on purpose: regenerating the tables must
not silently remove the retraction that qualifies them.
"""
import _env_guard  # noqa: F401
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "COMPARISON.md"

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


text = PAGE.read_text(encoding="utf-8")
# The banner is prose and wraps; compare against a whitespace-normalised copy so a
# line break cannot make a present sentence look absent.
flat = " ".join(text.split())
manifest = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
claims = manifest["claims"]
items = claims.items() if isinstance(claims, dict) else list(enumerate(claims))
withdrawn = [v for _k, v in items if v.get("stale") or v.get("withdrawn_on")]

print("\n- the page carries the retraction -")
check("a withdrawal banner is present", "WITHDRAWN" in text)
check("it says the numbers must not be quoted", "must not be quoted" in flat)
check("it gives the reason", "content hash" in flat or "unhashed" in flat)

print("\n- the banner is outside the generated regions -")
gen_start = text.find("<!-- comparison:verified -->")
banner = text.find("WITHDRAWN")
check("the banner precedes the first generated table",
      banner != -1 and gen_start != -1 and banner < gen_start,
      f"banner at {banner}, region at {gen_start}")

print("\n- and it is warranted: those claims really are withdrawn -")
check("the register holds withdrawn claims", len(withdrawn) > 100, str(len(withdrawn)))
check("the page still shows the numbers rather than deleting them",
      "0.550" in text and "0.478" in text)

print(f"\nwithdrawn not quoted: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
