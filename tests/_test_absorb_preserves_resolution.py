#!/usr/bin/env python3
"""An absorb rewrite must not reopen a fix that already shipped.

The rewrite rebuilt frontmatter from scratch and carried only recurrence and sources, so
`status`, `resolved_by`, `resolves`, `relations`, `salience` and `supersedes` were
silently dropped. Measured consequence in the live vault (review 2026-09): a mistake lost
`status: resolved` while the decision that fixed it still carried `resolves:` pointing at
it — so the dead bug was re-injected at SessionStart against a committed, tested fix.

Newer information still wins: a field the new extraction supplies is not overwritten by
the old value.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the carried set is the one the review named -")
src = (Path(__file__).resolve().parents[1] / "nevertwice" / "memory_hook.py").read_text(encoding="utf-8")
for field in ("status", "resolved_by", "resolves", "relations", "salience", "supersedes"):
    check(f"{field} is carried across an absorb", f'"{field}"' in src.split("_carried: dict")[1][:900])

print("\n- newer information wins -")
seg = src.split("_carried.items()")[1][:400]
check("carried fields use setdefault, so a fresh value is not overwritten",
      "setdefault" in seg)
check("the carry reads the absorb target, not an arbitrary note",
      "_read_frontmatter_file(absorb_into)" in src)

print("\n- a corrupt prior cannot lose the new note -")
seg2 = src.split("_read_frontmatter_file(absorb_into)")[1][:300]
check("the prior read is guarded", "except Exception" in seg2)

print(f"\nabsorb preservation: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
