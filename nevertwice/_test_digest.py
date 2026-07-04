#!/usr/bin/env python3
"""Self-check for digest.py — conflicts pairing + digest windowing/aggregation.
Mocks the note iterators (no vault, no files, no network), the same way the research
tests mock the LLM, so it asserts the pure logic deterministically."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402
import digest as dg              # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")
OLD = "2020-01-01"

SUP = [
    {"stem": "old1", "superseded_by": "new1", "project": "proj", "ntype": "decision",
     "title": "Old A", "date": "2026-06-01", "status": "superseded"},
    {"stem": "old2", "superseded_by": "new2", "project": "proj", "ntype": "mistake",
     "title": "Old B", "date": OLD, "status": "superseded"},
]
LIVE = [
    {"stem": "new1", "project": "proj", "ntype": "decision", "title": "New A", "date": TODAY},
    {"stem": "new2", "project": "proj", "ntype": "mistake", "title": "New B", "date": OLD},
    {"stem": "x", "project": "proj", "ntype": "pattern", "title": "Pat", "date": TODAY},
]


def _install():
    m._iter_superseded_notes = lambda project=None: [s for s in SUP
                                                     if not project or s["project"] == project]
    m._iter_all_notes = lambda: list(LIVE)
    m._iter_project_notes = lambda project: [n for n in LIVE if n["project"] == project]
    m.entity_graph = lambda project=None, top=8: {"cuda": {"notes": 5, "links": []},
                                                  "torch": {"notes": 2, "links": []}}
    m.slug_project = lambda s: (s or "").lower()


def test_conflicts_pairs_and_orders():
    _install()
    rows = dg.compute_conflicts(None)
    assert len(rows) == 2, rows
    assert rows[0]["old_stem"] == "old1" and rows[0]["new_title"] == "New A"   # newest revision first
    assert rows[0]["new_date"] == TODAY and rows[0]["resolved"] is True
    assert rows[1]["old_stem"] == "old2"
    print("ok test_conflicts_pairs_and_orders")


def test_conflicts_unresolved_when_successor_missing():
    _install()
    m._iter_all_notes = lambda: [LIVE[2]]               # new1/new2 absent → successor unknown
    rows = dg.compute_conflicts(None)
    assert all(r["resolved"] is False for r in rows), rows
    print("ok test_conflicts_unresolved_when_successor_missing")


def test_digest_window_and_aggregation():
    _install()
    d = dg.compute_digest(None, days=7)
    t = d["totals"]
    assert t["live_notes"] == 3 and t["superseded_notes"] == 2 and t["projects"] == 1
    assert t["added_in_window"] == 2, t                 # new1 + x are dated today
    assert t["revised_in_window"] == 1, t               # only old1→new1 lands in the window
    bp = d["by_project"]["proj"]
    assert bp["total"] == 3 and bp["added"] == 2 and bp["superseded"] == 1
    assert bp["by_type"] == {"decision": 1, "mistake": 1, "pattern": 1}, bp["by_type"]
    assert d["top_entities"][0] == {"entity": "cuda", "notes": 5}
    assert len(d["recent"]) == 2
    print("ok test_digest_window_and_aggregation")


def test_digest_project_filter_normalizes():
    _install()
    d = dg.compute_digest("PROJ", days=3650)            # slug_project lowercases → "proj"
    assert d["project"] == "proj"
    assert d["totals"]["live_notes"] == 3
    print("ok test_digest_project_filter_normalizes")


if __name__ == "__main__":
    test_conflicts_pairs_and_orders()
    test_conflicts_unresolved_when_successor_missing()
    test_digest_window_and_aggregation()
    test_digest_project_filter_normalizes()
    print("\nall digest self-checks passed")
