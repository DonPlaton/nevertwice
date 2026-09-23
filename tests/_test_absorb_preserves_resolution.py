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
import _engine_source  # noqa: E402  the engine's text, one path for every suite

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- the carried set is the one the review named -")
src = _engine_source.SRC
# A3 (Q5) pulled the inline tuple out into a module-level `_ABSORB_CARRY_FIELDS` constant (so
# its own mutation test can monkeypatch it instead of editing the file), so the six fields this
# review named now live at THAT declaration, not inline after `_carried: dict = {}` any more -
# the loop that reads it (`for _k in _ABSORB_CARRY_FIELDS:`) still runs right after, unchanged.
carry_decl = src.split("_ABSORB_CARRY_FIELDS = (")[1][:400]
for field in ("status", "resolved_by", "resolves", "relations", "salience", "supersedes"):
    check(f"{field} is carried across an absorb", f'"{field}"' in carry_decl)
check("the carried set is actually READ right after _carried: dict = {} is declared",
      "_ABSORB_CARRY_FIELDS" in src.split("_carried: dict")[1][:400])

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
