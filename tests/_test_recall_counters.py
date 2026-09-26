#!/usr/bin/env python3
"""A caller can tell a degraded recall from an empty store, and count cross-encoder reorders.

PREREG-V3 TB3(e): the v3 adapter reads our arm through `api.recall(..., xrerank=False)` and must
show `xrerank_calls` 0 and `recall_degraded` 0 - a read the vectors did not rank is a transport
incident (the unit re-runs or is dropped for all arms, never an ours-only re-ask). `api.recall()`
drops the mode string, so nothing outside the process could tell. `api.recall_stats()` returns the
running counters; the adapter reads them as deltas around its own calls.

An empty store is not a transport incident. It is what an extractor that wrote nothing leaves, the
outcome the writer-yield figure (K76) exists to show. Counted as `recall_degraded`, it would drop the
unit for every arm - exactly the units where our product stored nothing would leave the comparison
(the auditor's pre-gate finding on the first TB3(e) candidate). So, pinned by literals:

* (a) embedder unreachable, query embed failed, vectors from another embedder, notes stored without
  vectors: `recall_degraded` +1, `recall_empty_store` +0;
* (b) no notes for the project at all: `recall_empty_store` +1, `recall_degraded` +0;
* (c) a read the vectors ranked (hybrid): both +0;
* `xrerank_calls` counts a cross-encoder reorder: 0 with `xrerank=False` even where the cross-encoder
  would switch itself on, 1 with `xrerank=True`.

No vault content, no embedder, no network: the embed cache, the embedder and the cross-encoder are
stubbed.

    python tests/_test_recall_counters.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

make_sandbox(m, offline=True)

import api  # noqa: E402
import memory_search as ms  # noqa: E402

P = F = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global P, F
    if cond:
        P += 1
        print(f"  ok   {name}")
    else:
        F += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


VEC = [1.0, 0.0, 0.0]
NOTES = {f"m-note{i}": {"ntype": "mistake", "project": "p", "title": f"cpu device {i}",
                        "desc": "training silently fell back to the cpu device",
                        "prevention": "assert cuda", "recurrence": 1, "vec": [1.0, 0.1 * i, 0.0]}
         for i in range(5)}
TEXT_ONLY = {s: {k: v for k, v in r.items() if k != "vec"} for s, r in NOTES.items()}

saved = {n: getattr(m, n) for n in ("load_embed_cache", "embedder_available", "embed_text",
                                    "embed_cache_usable", "_scale_index")}
saved_ce = (ms._ce.enabled, ms._ce.reorder)


def delta(fn) -> dict:
    before = api.recall_stats()
    fn()
    after = api.recall_stats()
    return {k: after[k] - before.get(k, 0) for k in after}


try:
    m._scale_index = lambda *a, **kw: None
    m.embed_cache_usable = lambda: True
    m.embed_text = lambda *a, **kw: list(VEC)
    reorders = []
    ms._ce.reorder = lambda q, results, k: reorders.append(q) or results[:k]

    DEGRADED = {"searches": 1, "recall_degraded": 1, "recall_empty_store": 0}
    EMPTY = {"searches": 1, "recall_degraded": 0, "recall_empty_store": 1}
    RANKED = {"searches": 1, "recall_degraded": 0, "recall_empty_store": 0}

    def reads(d: dict) -> dict:
        return {k: d.get(k) for k in ("searches", "recall_degraded", "recall_empty_store")}

    print("\n- (a) a store with notes the vectors did not rank: recall_degraded, not recall_empty_store -")
    m.load_embed_cache = lambda *a, **kw: dict(NOTES)
    m.embedder_available = lambda *a, **kw: False
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("embedder unreachable: searches 1, recall_degraded 1, recall_empty_store 0", reads(d) == DEGRADED, str(d))
    m.embedder_available = lambda *a, **kw: True
    m.embed_text = lambda *a, **kw: None
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("query embed failed: recall_degraded 1, recall_empty_store 0", reads(d) == DEGRADED, str(d))
    m.embed_text = lambda *a, **kw: list(VEC)
    m.embed_cache_usable = lambda: False
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("vectors from another embedder: recall_degraded 1, recall_empty_store 0", reads(d) == DEGRADED, str(d))
    m.embed_cache_usable = lambda: True
    m.load_embed_cache = lambda *a, **kw: dict(TEXT_ONLY)
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("notes stored without vectors (their embed failed at write): recall_degraded 1, recall_empty_store 0",
          reads(d) == DEGRADED, str(d))

    print("\n- (b) a store with no notes for the project: recall_empty_store, not recall_degraded -")
    m.load_embed_cache = lambda *a, **kw: dict(NOTES)          # notes exist, but for project "p" only
    got = []
    d = delta(lambda: got.extend(api.recall("cpu device", project="empty_proj", k=3, xrerank=False)))
    check("the empty project really returns nothing", got == [], str(got))
    check("empty project: searches 1, recall_degraded 0, recall_empty_store 1", reads(d) == EMPTY, str(d))
    m.load_embed_cache = lambda *a, **kw: {}
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("an empty store: recall_degraded 0, recall_empty_store 1", reads(d) == EMPTY, str(d))

    print("\n- (c) a recall the vectors ranked moves neither -")
    m.load_embed_cache = lambda *a, **kw: dict(NOTES)
    hits = []
    d = delta(lambda: hits.extend(api.recall("cpu device", project="p", k=3, xrerank=False)))
    check("the fixture really ranks by vectors (three hits)", len(hits) == 3, str(len(hits)))
    check("hybrid: searches 1, recall_degraded 0, recall_empty_store 0", reads(d) == RANKED, str(d))

    print("\n- cross-encoder reorders are counted -")
    ms._ce.enabled = lambda: True                      # would switch itself on (weights cached)
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=False))
    check("xrerank=False refuses it: xrerank_calls 0", d.get("xrerank_calls") == 0 and not reorders, str(d))
    d = delta(lambda: api.recall("cpu device", project="p", k=3, xrerank=True))
    check("xrerank=True: xrerank_calls 1", d.get("xrerank_calls") == 1 and len(reorders) == 1, str(d))
    d = delta(lambda: api.recall("cpu device", project="p", k=3))
    check("left as None it resolves through enabled(), and is counted", d.get("xrerank_calls") == 1, str(d))

    print("\n- the counters are a copy, not the live dict -")
    snap = api.recall_stats()
    snap["searches"] = -99
    check("editing the returned dict changes nothing", api.recall_stats()["searches"] != -99)
finally:
    for n, v in saved.items():
        setattr(m, n, v)
    ms._ce.enabled, ms._ce.reorder = saved_ce

print(f"\nrecall counters: {P} passed, {F} failed")
sys.exit(1 if F else 0)
