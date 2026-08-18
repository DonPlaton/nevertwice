#!/usr/bin/env python3
"""Refraction - lenses over the memory: pure projections from the store to a View.

The competitor answer to "where is the UI?" is a folder of hand-written database views
(Obsidian Bases): filter, group, sort, columns. That is a RELATIONAL projection - it sees a
note as a row of frontmatter. It is the right shape for flat notes and structurally cannot
express the three dimensions this store actually has: CAUSALITY (typed impact edges), TIME
(supersession lineage), and CONFIDENCE (how sure the memory is of what it knows).

So this module does not reimplement Bases. It defines a small algebra where a Bases-style
table is the DEGENERATE case:

    relational primitives   where / order_by / top / select     (what Bases can do)
    semantic lenses         falsification_frontier, causal_closure
                                                                 (what it cannot)

A lens is a pure function from the store to a `View`; the primitives are pure View -> View,
so they compose with `pipe`. Rendering lives in emit.py - one View becomes markdown, mermaid,
JSON, or an Obsidian `.base` file - which means the competitor's format is one output target
of this algebra rather than a thing to copy.

The headline lens is FALSIFICATION_FRONTIER: the beliefs nearest to being wrong, ranked. It
unites three signals the store already keeps and never combined - stated confidence (M-10),
recurrence, and the supersession ledger - into one observable answer to a question no memory
system asks itself: *what do I believe that I should test first?* Memory that reports the
edge of its own reliability is memory you can audit rather than trust.

    python -m nevertwice.lenses frontier --project prism --format markdown
    python -m nevertwice.lenses causal cuda --format mermaid
    python -m nevertwice.lenses frontier --format base > "Falsification Frontier.base"

Stdlib only, read-only, off every hot path.
"""
import math
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
_MH = None


def _m():
    global _MH
    if _MH is None:
        try:
            from . import memory_hook as mh
        except ImportError:
            import memory_hook as mh
        _MH = mh
    return _MH


class View(NamedTuple):
    """The one thing every lens returns and every emitter consumes.

    `rows` carries the tabular payload (a list of flat dicts), `columns` fixes their order,
    `edges` carries graph structure when the view has any, and `meta` holds whatever a
    renderer needs to be honest about the view (titles, notes, the closest Bases filter)."""
    kind: str            # "table" | "graph"
    title: str
    rows: list
    columns: list
    edges: list
    meta: dict


def view(kind="table", title="", rows=None, columns=None, edges=None, **meta) -> View:
    rows = list(rows or [])
    return View(kind, title, rows, list(columns or (rows[0].keys() if rows else [])),
                list(edges or []), meta)


# ── relational primitives: the degenerate case, closed under composition ──────

def where(pred):
    return lambda v: v._replace(rows=[r for r in v.rows if pred(r)])


def order_by(key, desc=True):
    """Sort rows by `key`, missing values LAST regardless of direction. Type-stable:
    numbers sort numerically, everything else as strings, so a mixed or half-empty column
    can never raise (review 2026-08: the old `r.get(key) or 0` coerced None to int 0 and
    crashed with TypeError on any text column containing one missing value)."""
    def _key(r):
        v = r.get(key)
        if isinstance(v, bool):
            v = int(v)
        return (0, v) if isinstance(v, (int, float)) else (1, str(v))

    def _apply(view_):
        present = [r for r in view_.rows if r.get(key) not in (None, "")]
        missing = [r for r in view_.rows if r.get(key) in (None, "")]
        return view_._replace(rows=sorted(present, key=_key, reverse=desc) + missing)
    return _apply


def top(n):
    return lambda v: v._replace(rows=v.rows[:max(0, n)])


def select(*columns):
    return lambda v: v._replace(rows=[{c: r.get(c) for c in columns} for r in v.rows],
                                columns=list(columns))


def pipe(v: View, *ops) -> View:
    """Apply View -> View operations left to right. The algebra's composition rule; the whole
    reason primitives and lenses share one type."""
    for op in ops:
        v = op(v)
    return v


# ── the semantic lenses ──────────────────────────────────────────────────────

def _revision_counts(project=None, notes=None) -> dict:
    """stem -> how many times this belief's lineage was revised, from the supersession
    ledger. A fact the memory kept changing its mind about is a fact worth re-testing.

    The project is SLUGGED before it reaches compute_conflicts - every other caller slugs
    (api.conflicts, compute_digest) and the frontier itself slugs for the note scan, so a
    raw name here silently returned {} and zeroed the revision factor of the headline
    ranking (review 2026-08). `notes` shares the already-scanned live list so the ledger
    does not rescan the vault the caller just scanned."""
    try:
        try:
            from . import digest as dg
        except ImportError:
            import digest as dg
        proj = _m().slug_project(project) if project else None
        counts: dict = {}
        for rec in dg.compute_conflicts(proj, limit=100_000, live=notes):
            for key in ("new_stem", "old_stem"):
                s = rec.get(key)
                if s:
                    counts[s] = counts.get(s, 0) + 1
        return counts
    except Exception:
        return {}


def falsification_risk(n: dict, revisions: int = 0) -> float:
    """How close a belief sits to being falsified, from signals the store already keeps.

        risk = (1 - confidence) x recurrence-weight x revision-weight x settled-discount

    Read it as: a belief is worth testing when the memory is UNSURE of it (stated confidence
    below 1.0), it is LOAD-BEARING (it keeps recurring, so being wrong costs a lot), and it
    has proven UNSTABLE (its lineage was already revised). A note the memory marked resolved
    is discounted, not excluded - settled is not the same as certain.

    Confidence absent = treated as fully confident, matching the ranker's own convention
    (memory_hook: "a note without confidence is treated as fully confident"), so an
    unstamped note scores 0 and stays off the frontier rather than flooding it."""
    conf = n.get("confidence")
    conf = 1.0 if conf is None else max(0.0, min(1.0, conf))
    rec = max(1, int(n.get("recurrence") or 1))
    risk = (1.0 - conf) * (1.0 + math.log1p(rec - 1)) * (1.0 + revisions)
    if n.get("resolved"):
        risk *= 0.4
    return round(risk, 4)


def falsification_frontier(project=None, k: int = 20, notes=None) -> View:
    """The beliefs nearest to being wrong, most-testable first.

    This is the lens a relational view cannot express: the ranking is a function OVER note
    state (confidence x recurrence x revision history), not a filter on any single stored
    field. Rows carry the inputs alongside the score, so the ranking argues for itself."""
    mh = _m()
    slug = mh.slug_project(project) if project else None
    notes = notes if notes is not None else (
        mh._iter_project_notes(slug) if slug else mh._iter_all_notes())
    revisions = _revision_counts(slug, notes)
    rows = []
    for n in notes:
        rev = revisions.get(n.get("stem", ""), 0)
        risk = falsification_risk(n, rev)
        if risk <= 0:
            continue                      # fully-confident, never-revised: nothing to test
        rows.append({"belief": n.get("title", ""), "type": n.get("ntype", ""),
                     "project": n.get("project", ""),
                     "confidence": n.get("confidence"), "recurrence": n.get("recurrence", 1),
                     "revisions": rev, "resolved": bool(n.get("resolved")),
                     "risk": risk, "stem": n.get("stem", "")})
    v = view("table", "Falsification Frontier", rows,
             ["belief", "type", "project", "confidence", "recurrence", "revisions", "risk"],
             scanned=len(notes), project=slug,
             note="ranked by (1-confidence) x recurrence x revisions; test the top rows first",
             # The closest a relational view can come - deliberately recorded so the emitted
             # .base can say what it is approximating instead of pretending to be the lens.
             base_filter="confidence < 0.8", base_sort="confidence", base_sort_dir="ASC",
             base_note="Bases can filter and sort stored fields; it cannot compute this "
                       "ranking. This view approximates the frontier with its strongest "
                       "single input.")
    return pipe(v, order_by("risk"), top(k))


def causal_closure(entity: str, project=None, depth: int = 2, max_effects: int = 12) -> View:
    """Everything a change to `entity` can reach, as a graph view - the transitive closure of
    the impact edges, which is a traversal no table can hold.

    Edges are the store's ACTUAL impact edges induced on the shown nodes - never a synthetic
    root->effect shortcut. The first version drew every impact as a direct edge from the
    root labeled with the LAST hop's relation, asserting store relations that do not exist
    (review 2026-08: a-causes->b, b-fixes->c rendered as `a -->|fixes| c`). A multi-hop
    effect whose intermediate was cut by max_effects may now appear with no inbound edge -
    an honest gap, shown as a floating node rather than papered over with a false edge."""
    try:
        try:
            from . import causal as cz
        except ImportError:
            import causal as cz
        impact = cz.build_impact_graph(project)
        r = cz.what_breaks(entity, project, depth=depth, max_effects=max_effects,
                           impact=impact)
    except Exception:
        impact, r = {}, {"impacts": [], "failure_modes": []}
    rows = [{"effect": i["effect"], "via": i["via"], "hops": i["hops"], "notes": i["notes"]}
            for i in r.get("impacts", [])]
    shown = {entity} | {i["effect"] for i in r.get("impacts", [])}
    edges = [{"source": u, "rel": e["via"], "target": e["effect"]}
             for u in sorted(shown) for e in impact.get(u, [])
             if e["effect"] in shown and e["effect"] != u]
    return view("graph", f"Causal closure of `{entity}`", rows,
                ["effect", "via", "hops", "notes"], edges,
                entity=entity,
                failure_modes=[f["title"] for f in r.get("failure_modes", [])],
                note="what a change to this entity can reach; edges are the store's real "
                     "typed impact edges among the shown nodes")


LENSES = {"frontier": falsification_frontier, "causal": causal_closure}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    argv = sys.argv[1:]
    if not argv or "--help" in argv or "-h" in argv:
        print("usage: lenses frontier [--project P] [--k N] [--format markdown|base|json]\n"
              "       lenses causal <entity> [--project P] [--format mermaid|markdown|json]")
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        from . import emit as em
    except ImportError:
        import emit as em
    mh = _m()
    name = argv[0]
    project = mh.argval(argv, "project")
    fmt = mh.argval(argv, "format") or ("mermaid" if name == "causal" else "markdown")
    if name == "frontier":
        try:
            k = int(mh.argval(argv, "k") or 20)
        except ValueError:
            k = 20
        v = falsification_frontier(project, k)
    elif name == "causal":
        ent = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""
        if not ent:
            print("provide an entity")
            return
        v = causal_closure(ent, project)
    else:
        print(f"unknown lens: {name} (have: {', '.join(sorted(LENSES))})")
        return
    print(em.emit(v, fmt))


if __name__ == "__main__":
    main()
