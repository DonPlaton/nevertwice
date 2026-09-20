#!/usr/bin/env python3
"""Three per-item LLM loops that hold the vault lock and never touch it.

F6 gave `adjudicate_contested`'s judge loop a `refresh_lock()` after every call, because a long
hold past `LOCK_STALE_S * 10` (100 minutes) lets a concurrent hook reclaim a lock whose live
holder is still working. The same `--apply` run makes three more unbounded per-item model calls
under that same lock and none of them touched it:

* `guards.generate_from_vault` calls `propose_from_mistake` once per undistilled mistake. On the
  measured store that is 185 of them; at the ~33 s an Ollama fallback costs, the loop alone runs
  6105 s against a 6000 s ceiling.
* `memory_hook.maintain_contexts` compacts one Context file per project through the LLM.
* `consolidate_memory.distill_patterns` distils up to `max_distill` mistakes.

The property is per ITEM, not per run: a loop that refreshes once at the top is a loop whose
second hour is unprotected. So the check samples the lock's age INSIDE each unit of work.

    python tests/_test_long_holds_refresh_the_lock.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0
STALE = 5000.0          # older than any ceiling, so "fresh" is unambiguous


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print("  ok   " + label)
    else:
        F += 1
        print("  FAIL " + label + (" - " + detail if detail else ""))


def _age_lock() -> None:
    """Put the lock's mtime far enough in the past that any touch is visible."""
    import os
    t = time.time() - STALE
    os.utime(m._lock_file(), (t, t))


def _sampler(ages: list):
    """A stand-in unit of work: record how old the lock was when it started, then age it again
    so the NEXT unit can only see a fresh lock if the loop refreshed in between."""
    def unit(*a, **k):
        ages.append(time.time() - m._lock_file().stat().st_mtime)
        _age_lock()
        return None
    return unit


def measured(label: str, ages: list) -> None:
    check(label + ": more than one unit of work ran", len(ages) >= 2, "ran " + str(len(ages)))
    if len(ages) >= 2:
        check(label + ": every unit after the first found a lock the loop had refreshed",
              all(a < 60 for a in ages[1:]),
              "ages " + str([round(a) for a in ages]))


make_sandbox(m, offline=True)
m.acquire_lock(timeout_s=10)

print("# guards.generate_from_vault - one model call per undistilled mistake")
import guards as G  # noqa: E402

for i in range(3):
    m.write_typed_note("Mistakes", {"title": "flaky step " + str(i),
                                    "description": "It failed again.",
                                    "prevention": "Pin the version " + str(i) + "."},
                       "demo", "2026-02-0" + str(i + 1), [], "mistake")
ages_g: list = []
real_propose, G.propose_from_mistake = G.propose_from_mistake, _sampler(ages_g)
_age_lock()
try:
    G.generate_from_vault(min_recurrence=1, use_llm=True)
finally:
    G.propose_from_mistake = real_propose
measured("generate_from_vault", ages_g)

print("# maintain_contexts - one compaction per project Context file")
ctx = m.VAULT / "Context"
ctx.mkdir(parents=True, exist_ok=True)
for name in ("alpha", "beta", "gamma"):
    (ctx / (name + ".md")).write_text("---\ntype: context\n---\n\n# " + name + "\n",
                                      encoding="utf-8")
ages_c: list = []
real_compact, m.compact_context_if_needed = m.compact_context_if_needed, _sampler(ages_c)
real_card, m.refresh_project_card = m.refresh_project_card, lambda *a, **k: None
_age_lock()
try:
    m.maintain_contexts(allow_llm=True)
finally:
    m.compact_context_if_needed = real_compact
    m.refresh_project_card = real_card
measured("maintain_contexts", ages_c)

print("# distill_patterns - one model call per recurring mistake")
import consolidate_memory as C  # noqa: E402

cache = {("2026-02-0" + str(i) + "-demo-mistake-x"): {"ntype": "mistake", "recurrence": 5,
                                                      "title": "t" + str(i), "desc": "d",
                                                      "prevention": "p", "project": "demo"}
         for i in range(1, 4)}
ages_d: list = []
real_json, m.generate_json = m.generate_json, _sampler(ages_d)
_age_lock()
try:
    C.distill_patterns(cache, apply=True, max_distill=3)
finally:
    m.generate_json = real_json
measured("distill_patterns", ages_d)

m.release_lock()
print()
print("long holds refresh the lock: " + str(P) + " passed, " + str(F) + " failed")
sys.exit(1 if F else 0)
