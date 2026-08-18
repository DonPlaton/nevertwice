#!/usr/bin/env python3
"""Regression tests for the graph-laws checker (integrity.py).

The checker is what stands between a knowledge graph that LOOKS well-formed note by note and
one that actually holds together: dangling targets, hallucinated relation types, causal
cycles, contradictory converse pairs, dead wikilinks, and producer/consumer vocabulary drift.

Every law is exercised on fixture note metadata - no vault, no embedder, no database - plus
one pin that keeps the mirrored causal vocabulary from drifting away from causal.py, which is
the exact failure the checker exists to report.

    python _test_integrity.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import integrity as ig           # noqa: E402
import causal as cz              # noqa: E402
import api                       # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


def note(stem, entities, relations=None):
    return {"stem": stem, "ntype": "mistake", "project": "p",
            "entities": entities, "relations": relations or []}


def laws(findings):
    return {f["law"] for f in findings}


# ── LAW 1: referential ────────────────────────────────────────────────────────
print("LAW 1 - referential integrity")
notes = [note("n1", ["oom"], [{"rel": "fixed-by", "target": "grad-checkpointing"}]),
         note("n2", ["grad-checkpointing"])]
check("a target some note defines is clean", not ig.check_edges(notes))
dangling = [note("n1", ["oom"], [{"rel": "fixed-by", "target": "nowhere"}])]
f = ig.check_edges(dangling)
check("a target no note defines is reported", laws(f) == {"referential"})
check("it explains the real consequence (relation_expand cannot follow)",
      "relation_expand" in f[0]["detail"])
check("endemic by nature → warn, so --strict stays usable", f[0]["severity"] == "warn")
check("the finding carries its source note", f[0]["stem"] == "n1")

# ── LAW 2: vocabulary ─────────────────────────────────────────────────────────
print("LAW 2 - vocabulary conformance")
bad = [note("n1", ["a"], [{"rel": "blocks", "target": "a2"}]), note("n2", ["a2"])]
f = ig.check_edges(bad)
check("a type nothing defines is an error", any(x["law"] == "vocabulary" and
                                                x["severity"] == "error" for x in f))
ok = [note("n1", ["a"], [{"rel": "depends-on", "target": "a2"}]), note("n2", ["a2"])]
check("a consumed-only type is legal", "vocabulary" not in laws(ig.check_edges(ok)))
prof = [note("n1", ["a"], [{"rel": "builds-on", "target": "a2"}]), note("n2", ["a2"])]
check("a Brain-profile type is legal even when the profile is off",
      "vocabulary" not in laws(ig.check_edges(prof)))
check("known_vocab unions producers, consumers and profiles",
      ig.PRODUCED_VOCAB <= ig.known_vocab() and ig.CONSUMED_VOCAB <= ig.known_vocab())

# ── LAW 3: acyclicity ─────────────────────────────────────────────────────────
print("LAW 3 - acyclicity of the causal orientation")
dag = [note("n1", ["a"], [{"rel": "causes", "target": "b"}]),
       note("n2", ["b"], [{"rel": "causes", "target": "c"}]), note("n3", ["c"])]
check("a DAG has no cycle", not ig.check_acyclicity(dag))
two = [note("n1", ["a"], [{"rel": "causes", "target": "b"}]),
       note("n2", ["b"], [{"rel": "causes", "target": "a"}])]
cyc = ig.check_acyclicity(two)
check("mutual causation is a cycle", len(cyc) == 1 and cyc[0]["law"] == "acyclicity")
check("the cycle is spelled out", "a -> b -> a" in cyc[0]["detail"])
three = [note("n1", ["a"], [{"rel": "causes", "target": "b"}]),
         note("n2", ["b"], [{"rel": "causes", "target": "c"}]),
         note("n3", ["c"], [{"rel": "causes", "target": "a"}])]
check("a 3-cycle is reported exactly once, not once per rotation",
      len(ig.check_acyclicity(three)) == 1)
check("REVERSE relations are flipped before the check (a depends-on b ⇒ b→a)",
      ig.impact_edges([note("n1", ["a"], [{"rel": "depends-on", "target": "b"}])]) ==
      {"b": {"a": {"depends-on"}}})
check("symmetric/non-causal types never enter the impact graph",
      ig.impact_edges([note("n1", ["a"], [{"rel": "related-to", "target": "b"}])]) == {})
check("a self-edge is not a cycle",
      not ig.check_acyclicity([note("n1", ["a"], [{"rel": "causes", "target": "a"}])]))

# ── LAW 3, SCC counting + severity classes (review 2026-08) ──────────────────
print("LAW 3 - SCC counting, severity, and the density bound")
mixed = [note("n1", ["retry-logic"], [{"rel": "caused-by", "target": "flaky-api"},
                                      {"rel": "fixes", "target": "flaky-api"}]),
         note("n2", ["flaky-api"])]
f = ig.check_acyclicity(mixed)
check("a consistent 'exists-because-of AND fixes' pair is a WARN, not an error",
      len(f) == 1 and f[0]["severity"] == "warn" and "mixed cause/fix" in f[0]["detail"])
pure = ig.check_acyclicity(two)
check("a pure-cause cycle stays an error", pure and pure[0]["severity"] == "error")
big = [note("n1", ["a"], [{"rel": "causes", "target": "b"}]),
       note("n2", ["b"], [{"rel": "causes", "target": "c"}]),
       note("n3", ["c"], [{"rel": "causes", "target": "a"},
                          {"rel": "causes", "target": "b"}])]
r3 = ig.check_acyclicity(big)
check("one finding per cyclic REGION (SCC), not per cycle rotation", len(r3) == 1)
check("the region size is named when it exceeds the example cycle",
      "cyclic region of 3 entities" in r3[0]["detail"] or "a -> b -> c -> a" in r3[0]["detail"])
# 40+ regions: totals must report ALL of them (the old enumerator capped at 20)
many_cyc = []
for i in range(40):
    many_cyc.append(note(f"p{i}", [f"x{i}"], [{"rel": "causes", "target": f"y{i}"}]))
    many_cyc.append(note(f"q{i}", [f"y{i}"], [{"rel": "causes", "target": f"x{i}"}]))
rep40 = ig.check(many_cyc)
check("totals report every cyclic region, uncapped (was silently capped at 20)",
      rep40["totals"]["acyclicity"] == 40)
check("nothing is reported as capped under exact SCC counting",
      rep40["stats"]["cycles_capped"] is False)
# The density bound: a dense ACYCLIC diamond lattice must finish instantly (the old
# simple-path DFS took ~12s on 61 nodes and grew 4x every 2 layers).
import time as _t
lattice = []
for layer in range(30):
    for j in range(2):
        lattice.append(note(f"L{layer}_{j}", [f"d{layer}_{j}"],
                            [{"rel": "causes", "target": f"d{layer + 1}_0"},
                             {"rel": "causes", "target": f"d{layer + 1}_1"}]))
_t0 = _t.perf_counter()
lat_f = ig.check_acyclicity(lattice)
_dt = _t.perf_counter() - _t0
check(f"dense acyclic lattice checks in O(V+E) ({_dt * 1000:.0f}ms, was ~12s)", _dt < 1.0)
check("...and reports no cycles", lat_f == [])

print("LAW 1 - entity universe for scoped runs")
scoped = [note("n1", ["oom"], [{"rel": "fixed-by", "target": "other-project-entity"}])]
f_narrow = ig.check_edges(scoped)
f_wide = ig.check_edges(scoped, entity_universe={"oom", "other-project-entity"})
check("without a universe, a cross-project target is flagged",
      any(x["law"] == "referential" for x in f_narrow))
check("with the store-wide universe, it resolves",
      not any(x["law"] == "referential" for x in f_wide))

print("LAW 5 - resolution semantics")
check("resolution is case-insensitive (NTFS/APFS reality)",
      not ig.check_wikilinks({"n1": "[[Live-Note]]"}, {"live-note"}))
check("a path-form link resolves by its basename",
      not ig.check_wikilinks({"n1": "[[Patterns/live-note]]"}, {"live-note"}))

# ── LAW 4: converse contradiction ─────────────────────────────────────────────
print("LAW 4 - converse coherence")
contra = [note("n1", ["x"], [{"rel": "causes", "target": "y"}]),
          note("n2", ["x"], [{"rel": "caused-by", "target": "y"}]), note("n3", ["y"])]
f = [x for x in ig.check_edges(contra) if x["law"] == "converse"]
check("a pair asserted in both directions is a contradiction", len(f) == 1)
check("it is an error, not a nuance", f[0]["severity"] == "error")
check("it names the note that made the first assertion", "n1" in f[0]["detail"])
check("fixes/fixed-by is a converse pair too",
      any(x["law"] == "converse" for x in ig.check_edges(
          [note("n1", ["x"], [{"rel": "fixes", "target": "y"}]),
           note("n2", ["x"], [{"rel": "fixed-by", "target": "y"}]), note("n3", ["y"])])))
check("the same direction twice is not a contradiction",
      not [x for x in ig.check_edges(
          [note("n1", ["x"], [{"rel": "causes", "target": "y"}]),
           note("n2", ["x"], [{"rel": "causes", "target": "y"}]), note("n3", ["y"])])
          if x["law"] == "converse"])

# ── LAW 5: wikilinks ──────────────────────────────────────────────────────────
print("LAW 5 - link resolution")
f = ig.check_wikilinks({"n1": "see [[live-note]] and [[gone-note]]"}, {"live-note", "n1"})
check("an unresolvable link is reported", len(f) == 1 and f[0]["subject"] == "gone-note")
check("links are warnings, not errors", f[0]["severity"] == "warn")
check("an archived target still resolves (Obsidian resolves it, so it is history not rot)",
      not ig.check_wikilinks({"n1": "[[archived-thing]]"}, {"archived-thing"}))
check("alias and heading tails are stripped before resolving",
      not ig.check_wikilinks({"n1": "[[live-note|nice name]] [[live-note#section]]"},
                             {"live-note"}))

# ── vocabulary coherence (system level) ───────────────────────────────────────
print("vocabulary coherence - producer vs consumer drift")
v = ig.vocabulary_report([note("n1", ["a"], [{"rel": "alternative-to", "target": "b"}]),
                          note("n2", ["b"], [{"rel": "causes", "target": "a"}])])
check("counts every type in use", v["in_use"] == {"alternative-to": 1, "causes": 1})
check("a symmetric type is flagged causally inert (written, never traversed)",
      v["causally_inert"] == [{"rel": "alternative-to", "edges": 1}])
check("`causes` is consumed, so it is not inert",
      "causes" not in [x["rel"] for x in v["causally_inert"]])
# This check is what the drift report was BUILT to catch: `fixes`/`fixed-by` were the store's
# most common edges and were invisible to the impact graph until the report measured it
# (causal.py, 2026-08). Pinned so the blind spot cannot silently return.
check("`fixes` / `fixed-by` are consumed by the causal model, not inert",
      {"fixes", "fixed-by"} <= ig.CONSUMED_VOCAB)
check("types the impact graph expects but nothing writes are reported",
      "depends-on" in v["unproduced"] and "causes" not in v["unproduced"])
check("the mirrored causal vocabulary has not drifted from causal.py",
      ig.CAUSAL_FORWARD == frozenset(cz._FORWARD) and
      ig.CAUSAL_REVERSE == frozenset(cz._REVERSE))

# ── whole-store report ────────────────────────────────────────────────────────
print("report assembly")
r = ig.check(dangling)
check("stats count the corpus", r["stats"]["notes"] == 1 and r["stats"]["edges"] == 1)
check("dangling_rate is the headline health number", r["stats"]["dangling_rate"] == 1.0)
check("a clean graph rates 0.0", ig.check(notes)["stats"]["dangling_rate"] == 0.0)
check("no edges → no division by zero", ig.check([note("n", ["a"])])["stats"]["dangling_rate"] == 0.0)
check("a clean graph reports nothing", not ig.check(notes)["findings"])

many = [note(f"n{i}", ["a"], [{"rel": "causes", "target": f"ghost{i}"}]) for i in range(80)]
r = ig.check(many, cap=10)
check("findings are capped so one endemic law cannot bury the sharp ones",
      len(r["findings"]) == 10)
check("but totals report the TRUE count - no silent truncation",
      r["totals"]["referential"] == 80)
check("errors are counted across the whole set, not the capped view",
      ig.check(three)["stats"]["errors"] >= 1)
check("the cycle cap is surfaced, never silent", "cycles_capped" in ig.check(notes)["stats"])

print("surfaces")
check("api.integrity is exported", callable(getattr(api, "integrity", None)))
check("render produces a human report", "graph laws:" in ig.render(ig.check(notes)))
check("render names the unreachable-edge rate", "unreachable edges" in ig.render(ig.check(notes)))
check("a clean store says so", "all laws hold" in ig.render(ig.check(notes)))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
