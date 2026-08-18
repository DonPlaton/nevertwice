#!/usr/bin/env python3
"""Regression tests for Refraction - the lens algebra (lenses.py) and its emitters (emit.py).

Two claims are under test:

  1. The algebra holds: primitives are pure View -> View and compose, so a Bases-style table
     is the degenerate case of the same machinery that expresses causal and confidence views.
  2. The emitters are HONEST: the `.base` target renders what Obsidian Bases can actually do
     and declares, in the file it writes, which columns did not survive the translation.
     An emitter that silently dropped the ranking would make the format look equivalent when
     it is not - the exact confusion this design exists to avoid.

Fixture notes only - no vault, no embedder, no network.

    python _test_lenses.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
import lenses as L               # noqa: E402
import emit as em                # noqa: E402
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


def note(stem, *, conf=None, rec=1, resolved=False, title=None, ntype="mistake"):
    return {"stem": stem, "title": title or stem, "ntype": ntype, "project": "p",
            "confidence": conf, "recurrence": rec, "resolved": resolved,
            "entities": [], "relations": []}


# ── the algebra ───────────────────────────────────────────────────────────────
print("View + relational primitives (the degenerate case)")
v = L.view("table", "T", [{"a": 3, "b": "x"}, {"a": 1, "b": "y"}, {"a": 2, "b": "z"}])
check("columns are inferred from the rows", v.columns == ["a", "b"])
check("where filters", len(L.where(lambda r: r["a"] > 1)(v).rows) == 2)
check("order_by sorts descending by default", [r["a"] for r in L.order_by("a")(v).rows] == [3, 2, 1])
check("order_by ascending", [r["a"] for r in L.order_by("a", desc=False)(v).rows] == [1, 2, 3])
vm = L.view("table", "T", [{"b": "shaky"}, {"b": None}, {"b": "mild"}])
check("order_by survives a missing value in a text column (was TypeError)",
      [r["b"] for r in L.order_by("b", desc=False)(vm).rows] == ["mild", "shaky", None])
check("missing values sort LAST regardless of direction",
      L.order_by("b")(vm).rows[-1]["b"] is None)
vmix = L.view("table", "T", [{"b": 2}, {"b": "x"}, {"b": 1}])
check("a mixed-type column sorts without raising", len(L.order_by("b")(vmix).rows) == 3)
check("top truncates", len(L.top(2)(v).rows) == 2)
check("top of a negative count is empty, not a tail slice", L.top(-2)(v).rows == [])
check("select projects columns", L.select("b")(v).columns == ["b"] and
      set(L.select("b")(v).rows[0]) == {"b"})
check("pipe composes left to right",
      [r["a"] for r in L.pipe(v, L.where(lambda r: r["a"] > 1), L.order_by("a"), L.top(1)).rows]
      == [3])
check("primitives are pure - the input view is untouched", [r["a"] for r in v.rows] == [3, 1, 2])
check("a View is immutable (NamedTuple)", isinstance(v, tuple))

# ── the falsification lens ────────────────────────────────────────────────────
print("falsification risk")
check("a fully-confident, never-revised belief has nothing to test",
      L.falsification_risk(note("n", conf=1.0)) == 0.0)
check("an unstamped confidence is treated as confident (ranker's convention)",
      L.falsification_risk(note("n", conf=None)) == 0.0)
check("lower confidence means higher risk",
      L.falsification_risk(note("n", conf=0.5)) > L.falsification_risk(note("n", conf=0.9)))
check("recurrence raises the stakes",
      L.falsification_risk(note("n", conf=0.5, rec=4)) >
      L.falsification_risk(note("n", conf=0.5, rec=1)))
check("a revised belief outranks a never-revised twin",
      L.falsification_risk(note("n", conf=0.5), revisions=2) >
      L.falsification_risk(note("n", conf=0.5), revisions=0))
check("a resolved belief is discounted, not excluded",
      0 < L.falsification_risk(note("n", conf=0.5, resolved=True)) <
      L.falsification_risk(note("n", conf=0.5)))
check("a poisoned confidence cannot invert the ranking",
      L.falsification_risk(note("n", conf=5.0)) == 0.0 and
      L.falsification_risk(note("n", conf=-3.0)) > 0)

print("falsification frontier")
notes = [note("sure", conf=1.0), note("shaky", conf=0.5, rec=3),
         note("mild", conf=0.9), note("settled", conf=0.4, resolved=True)]
fr = L.falsification_frontier(notes=notes, k=10)
check("fully-confident beliefs are off the frontier",
      "sure" not in [r["belief"] for r in fr.rows])
check("the shakiest, most load-bearing belief leads", fr.rows[0]["belief"] == "shaky")
check("rows carry the inputs, so the ranking argues for itself",
      {"confidence", "recurrence", "revisions", "risk"} <= set(fr.columns))
check("k caps the frontier", len(L.falsification_frontier(notes=notes, k=1).rows) == 1)
check("it reports how much it scanned", fr.meta.get("scanned") == 4)
check("an empty store yields an empty frontier",
      L.falsification_frontier(notes=[], k=5).rows == [])

# ── emitters ──────────────────────────────────────────────────────────────────
print("emitters")
md = em.to_markdown(fr)
check("markdown renders a table with a header rule", "| belief |" in md and "|---" in md)
check("markdown carries the view's title", "Falsification Frontier" in md)
check("an empty view says so, rather than emitting a headless table",
      "(nothing to show)" in em.to_markdown(L.view("table", "T", [])))
g = L.view("graph", "G", [{"effect": "b"}], ["effect"],
           [{"source": "a", "rel": "fixes", "target": "b"}])
mer = em.to_mermaid(g)
check("mermaid emits a fenced graph", mer.startswith("```mermaid") and "graph LR" in mer)
check("mermaid labels the edge with its relation type", "-->|fixes|" in mer)
check("an edgeless graph still emits valid mermaid",
      "```mermaid" in em.to_mermaid(L.view("graph", "G", [])))
check("json round-trips the view",
      '"title"' in em.to_json(fr) and '"rows"' in em.to_json(fr))
check("an unknown format falls back to markdown, never raises",
      em.emit(fr, "nope") == em.to_markdown(fr))
check("mermaid on a TABLE view renders markdown, never the empty-graph placeholder",
      em.emit(fr, "mermaid") == em.to_markdown(fr))
check("mermaid on a GRAPH view stays mermaid", em.emit(g, "mermaid") == em.to_mermaid(g))
evil = L.view("table", "T", [{"belief": "use a | not b", "risk": 1.0}])
check("a '|' in a cell is escaped, not a phantom column",
      "a \\| not b" in em.to_markdown(evil))
gq = L.view("graph", "G", [], [], [{"source": 'cfg "prod"', "rel": "fixes", "target": "x"}])
check("a double quote in a mermaid label cannot break the diagram",
      '"prod"' not in em.to_mermaid(gq).split("\n", 2)[2])

print("the .base target is honest")
b = em.to_base(fr)
check("it is a Base, not a table dump", "views:" in b and "type: table" in b)
check("stored fields survive as Base columns", "confidence:" in b)
check("computed columns are declared lost, not silently dropped",
      "Not expressible as a Base column" in b and "risk" in b.split("Not expressible")[1][:80])
check("it states what it is approximating", "cannot compute this ranking" in b)
check("it carries the closest expressible filter", "confidence < 0.8" in b)
check("it sorts on the field standing in for the lens's ordering",
      "property: confidence" in b)
fr_scoped = fr._replace(meta={**fr.meta, "project": "prism"})
check("a project-scoped lens emits a project-scoped Base (scope was silently dropped)",
      'project == "prism"' in em.to_base(fr_scoped))
check("an unscoped lens adds no project filter", 'project ==' not in b)

print("surfaces")
check("api.frontier is exported", callable(getattr(api, "frontier", None)))
check("api.lens_causal is exported", callable(getattr(api, "lens_causal", None)))
check("every registered lens is callable", all(callable(f) for f in L.LENSES.values()))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
