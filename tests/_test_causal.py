#!/usr/bin/env python3
"""Self-check for causal.py (active memory, axis C). Verifies edge orientation into impact
direction (forward vs reverse relations), downstream multi-hop traversal, cycle safety, that
failure modes are pulled from mistakes tagging the entity, and that the counterfactual output
is a short synthesized answer (bounded - not an episode dump). Mocks the relation graph."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m          # noqa: E402
import causal as C               # noqa: E402

# orchestrator --causes--> god-object --causes--> hard-to-test   (forward chain)
# training --depends-on--> device-config   ⇒ impact device-config → training (reverse)
# throughput --caused-by--> cpu-fallback   ⇒ impact cpu-fallback → throughput (reverse)
# a cycle a<->b to prove cycle-safety
_RG = {
    "orchestrator": [{"rel": "causes", "target": "god-object", "notes": 3}],
    "god-object": [{"rel": "causes", "target": "hard-to-test", "notes": 2}],
    "training": [{"rel": "depends-on", "target": "device-config", "notes": 4}],
    "throughput": [{"rel": "caused-by", "target": "cpu-fallback", "notes": 5}],
    "a": [{"rel": "causes", "target": "b", "notes": 1}],
    "b": [{"rel": "causes", "target": "a", "notes": 1}],
    "theme": [{"rel": "alternative-to", "target": "hardcoded", "notes": 9}],   # non-causal → excluded
}
_MIS = {"orchestrator": [{"ntype": "mistake", "title": "god-object-hard-to-test",
                          "prevention": "split into modules", "stem": "s-orch", "recurrence": 3}]}


def _install():
    m.relation_graph = lambda project=None, top=0: dict(_RG)
    m.notes_for_entity = lambda e, p=None, k=0: list(_MIS.get(e, []))
    m.slug_project = lambda s: (s or "").lower()


def test_edge_orientation():
    _install()
    g = C.build_impact_graph()
    assert {x["effect"] for x in g["orchestrator"]} == {"god-object"}        # forward
    assert {x["effect"] for x in g["device-config"]} == {"training"}         # depends-on reversed
    assert {x["effect"] for x in g["cpu-fallback"]} == {"throughput"}        # caused-by reversed
    assert "theme" not in g                                                  # alternative-to excluded
    print("ok test_edge_orientation")


def test_downstream_multihop_and_cycle_safe():
    _install()
    wb = C.what_breaks("orchestrator", depth=3)
    effects = {i["effect"] for i in wb["impacts"]}
    assert "god-object" in effects and "hard-to-test" in effects            # 2-hop reached
    hop = {i["effect"]: i["hops"] for i in wb["impacts"]}
    assert hop["god-object"] == 1 and hop["hard-to-test"] == 2
    # cycle must terminate and not list the origin as its own impact
    wc = C.what_breaks("a", depth=5)
    assert {i["effect"] for i in wc["impacts"]} == {"b"}
    print("ok test_downstream_multihop_and_cycle_safe")


def test_failure_modes_from_mistakes():
    _install()
    wb = C.what_breaks("orchestrator")
    assert wb["failure_modes"] and wb["failure_modes"][0]["title"] == "god-object-hard-to-test"
    assert wb["evidence"] == ["s-orch"]
    print("ok test_failure_modes_from_mistakes")


def test_counterfactual_is_short_synthesis_not_dump():
    _install()
    s = C.counterfactual("orchestrator")
    assert "Changing `orchestrator`" in s
    assert "god-object" in s and "split into modules" in s
    assert len(s.splitlines()) <= 12          # a synthesized answer, not an episode dump
    # an entity with no causal footprint returns empty (silent)
    assert C.counterfactual("nonexistent-xyz") == ""
    print("ok test_counterfactual_is_short_synthesis_not_dump")


def test_why_reverses():
    _install()
    r = C.why("training")
    assert {c["effect"] for c in r["causes"]} == {"device-config"}   # training is underpinned by device-config
    print("ok test_why_reverses")


if __name__ == "__main__":
    test_edge_orientation()
    test_downstream_multihop_and_cycle_safe()
    test_failure_modes_from_mistakes()
    test_counterfactual_is_short_synthesis_not_dump()
    test_why_reverses()
    print("\nall causal self-checks passed")
