#!/usr/bin/env python3
"""The compacted Context block counts and spans CUMULATIVELY.

Both used to describe only the current pass. A second compaction wrote "compacted from 2"
over "compacted from 8", and because the next pass then believed only 2 had ever been
folded, the hole was undetectable. The span collapsed identically:
(2026-05-04 → 2026-08-28) became (2026-05-04 → 2026-05-04). Measured in the 2026-09 vault
review in projects that had never been re-extracted — the Current Status line of one of
them, naming the arXiv endorsement blocker, was deleted outright.
"""
import _env_guard  # noqa: F401
import sys, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def count(block):
    mm = re.search(r"ompacted from (\d+)", block)
    return int(mm.group(1)) if mm else None


def span(block):
    mm = re.search(r"\((\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2})\)", block)
    return mm.groups() if mm else None


print("\n- a first compaction counts what it folded -")
first = m._accumulated_header(["## 2026-05-04 a", "## 2026-06-01 b", "## 2026-07-02 c"], "state one")
check("counts the three entries", count(first) == 3, str(count(first)))
check("spans first to last", span(first) == ("2026-05-04", "2026-07-02"), str(span(first)))

print("\n- a second compaction ADDS, and never shrinks -")
second = m._accumulated_header([first, "## 2026-08-28 d", "## 2026-08-29 e"], "state two")
check("the counter grows rather than resetting", count(second) == 5, str(count(second)))
check("the span keeps the ORIGINAL start", span(second) == ("2026-05-04", "2026-08-29"),
      str(span(second)))

print("\n- and again, so it cannot erode over many passes -")
third = m._accumulated_header([second, "## 2026-09-01 f"], "state three")
check("the counter keeps growing", count(third) == 6, str(count(third)))
check("the span still starts where it always did", span(third) == ("2026-05-04", "2026-09-01"),
      str(span(third)))

print("\n- the block is never counted as one of the entries it stands for -")
check("a lone prior block folds nothing new",
      count(m._accumulated_header([first], "s")) == 3)

print("\n- the Russian heading is recognised too -")
ru = "## Накопленное состояние (compacted) (2026-01-01 → 2026-02-02)\n\nx\n\n_Compacted from 9 earlier entries._"
check("a Russian prior block is not double-counted",
      count(m._accumulated_header([ru, "## 2026-03-03 z"], "s")) == 10,
      str(count(m._accumulated_header([ru, "## 2026-03-03 z"], "s"))))

print(f"\ncontext compaction: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
