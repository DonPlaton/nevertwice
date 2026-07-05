#!/usr/bin/env python3
"""Entity + typed-relation knowledge graph over the memory store.

Lessons carry optional `entities` (tools / concepts / files) and `relations`
([{rel, target}] typed edges), both LLM-emitted at extraction and normalised into note
frontmatter. This module reads them straight from the files, so the graph works with NO
embedder and NO database, and never touches the per-prompt hot path: it is an explicit,
on-demand facet over the store.

Split out of memory_hook to keep that module lean (2026-06-20 polish). The functions are
re-exported from memory_hook, so `m.entity_index(...)` etc. keep working. memory_hook is
imported LAZILY (via `_m()`, only inside function bodies), so importing graph and
memory_hook in either order is safe despite the re-export cycle, and a test that reassigns
`memory_hook.VAULT` is honoured (the shared iterators run in memory_hook's live namespace).

    entity_index / notes_for_entity / co_occurring / entity_graph   - Phase 1 (entities)
    related_by / relation_graph                                     - Phase 2 (typed edges)
    relation_expand                                                 - Phase 2b (recall expansion)
    graph_export                                                    - visualization (mermaid/dot/json)
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_MH = None


def _m():
    """memory_hook, imported lazily so graph and memory_hook import cleanly in either order
    (graph is re-exported FROM memory_hook, which is otherwise a circular import)."""
    global _MH
    if _MH is None:
        try:
            from . import memory_hook as mh
        except ImportError:
            import memory_hook as mh
        _MH = mh
    return _MH


def _sql():
    """The SQLite scale-index module IF its graph tables are built and authoritative, else None
    (F4). When present, the entity/relation queries below run in SQL instead of an O(all-notes)
    markdown scan; when absent, they fall back to the markdown read - which stays the source of
    truth, so dropping the .sqlite file only costs speed, never correctness."""
    try:
        try:
            from . import index_sqlite as sx
        except ImportError:
            import index_sqlite as sx
        return sx if sx.graph_index_ready() else None
    except Exception:
        return None


def entity_index(project: str | None = None) -> dict:
    """entity -> [note metas tagged with it]. The shared graph index, built in ONE note
    scan. Pass it to the query helpers below (idx=) so a `--entity` / `--entities` command
    scans the vault once, not once per entity (audit 2026-06-20)."""
    mh = _m()
    notes = mh._iter_project_notes(project) if project else mh._iter_all_notes()
    idx: dict[str, list] = {}
    for n in notes:
        for e in n.get("entities") or []:
            idx.setdefault(e, []).append(n)
    return idx


def entity_types_index(project: str | None = None) -> dict:
    """entity -> type (paper/method/dataset/...), read from notes' `entity_types`
    frontmatter (Brain layer, F1). Newest note wins when an entity is typed more than once,
    so a re-classification supersedes the old label. Empty until a brain profile has tagged
    anything - a coding-only store never writes entity_types. Drives the entity cards (F2).
    Uses the SQLite graph index at scale (F4), else a markdown scan."""
    sx = _sql()
    if sx is not None:
        return sx.sql_etype_index(project)
    mh = _m()
    notes = mh._iter_project_notes(project) if project else mh._iter_all_notes()
    typed: dict = {}
    for n in sorted(notes, key=lambda n: n.get("date", "")):
        for name, typ in (n.get("entity_types") or {}).items():
            typed[name] = typ                  # last (newest) write wins
    return typed


def entities_by_type(etype: str, project: str | None = None) -> list:
    """All known entities classified as `etype` (e.g. every 'method' or 'paper'), sorted.
    The enumeration the entity-card generator walks to know what cards to (re)build."""
    sx = _sql()
    if sx is not None:
        return sx.sql_entities_by_type(etype, project)
    et = etype.strip().lower()
    return sorted(e for e, t in entity_types_index(project).items() if t == et)


def entity_timeline(entity: str, project: str | None = None, sup: dict | None = None,
                    idx: dict | None = None) -> dict:
    """The chronological history of an entity across LIVE and SUPERSEDED notes (Brain layer, F3):
    first/last seen, the dated mentions, and the EVOLUTION events - where an earlier note about it
    was later superseded, i.e. the take changed. Reads the Superseded/ folders too, so it shows
    history that live recall hides; `sup` reuses a pre-built superseded index and `idx` the live
    entity index across a card refresh (so the card's own scan is not repeated). Pull-only -
    surfaced in the entity card and via api.entity_timeline, never injected. Returns
    {entity, first_seen, last_seen, count, mentions:[...], evolution:[...]} or {} for an unknown one."""
    mh = _m()
    norm = mh._norm_entities([entity])
    if not norm:
        return {}
    ent = norm[0]
    live = notes_for_entity(ent, project, k=1000, idx=idx)
    dead = (sup if sup is not None else mh._superseded_index(project)).get(ent, [])
    notes = live + dead
    if not notes:
        return {}
    rows = sorted(({"date": n.get("date", ""), "title": n.get("title", ""),
                    "stem": n.get("stem", ""), "ntype": n.get("ntype", ""),
                    "status": n.get("status", "live"),
                    "superseded_by": n.get("superseded_by", "")} for n in notes),
                  key=lambda r: r["date"])
    dates = [r["date"] for r in rows if r["date"]]
    evolution = [r for r in rows if r["status"] == "superseded" and r["superseded_by"]]
    return {"entity": ent, "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "", "count": len(rows),
            "mentions": rows, "evolution": evolution}


def salience_index(project: str | None = None) -> dict:
    """stem -> salience in [0,1]: pure graph CENTRALITY (Brain F5). A note is salient when its
    entities are referenced by the rest of the store - inbound relation edges + co-occurrence
    degree. This is the NEW signal orthogonal to recurrence (the ranker already applies recurrence
    separately, so salience deliberately does NOT re-fold it - double-counting it once compounded
    the keep/rank decision). Max-scaled across the corpus, so an entity-less / flat store → all 0
    (INERT, e.g. on a benchmark). Computed sleep-time, stamped by consolidation, read as a gentle
    ranking nudge. No embedder, no LLM."""
    mh = _m()
    notes = mh._iter_project_notes(project) if project else mh._iter_all_notes()
    if not notes:
        return {}
    ent_notes: dict = {}        # how many notes carry each entity (degree source)
    inbound: dict = {}          # how many relation edges TARGET each entity (inbound references)
    for n in notes:
        for e in n.get("entities") or []:
            ent_notes[e] = ent_notes.get(e, 0) + 1
        for ed in n.get("relations") or []:
            t = ed.get("target")
            if t:
                inbound[t] = inbound.get(t, 0) + 1
    raw: dict = {}
    for n in notes:
        ents = n.get("entities") or []
        deg = sum(max(0, ent_notes.get(e, 1) - 1) for e in ents)   # co-occurrence centrality
        inb = sum(inbound.get(e, 0) for e in ents)                 # inbound reference count
        raw[n["stem"]] = math.log1p(inb) + 0.5 * math.log1p(deg)
    hi = max(raw.values(), default=0.0)
    if hi <= 0:                  # no centrality anywhere → inert
        return {s: 0.0 for s in raw}
    return {s: round(v / hi, 4) for s, v in raw.items()}


def _edge_counts(notes, exclude=None, rel=None) -> dict:
    """{(rel, target): count} over the notes' typed relations, skipping self-edges (target
    == `exclude`) and, when `rel` is given, other relation types. One source for the edge
    aggregation, shared by related_by and relation_graph so the two never drift."""
    counts: dict = {}
    for n in notes:
        for edge in n.get("relations") or []:
            r, t = edge.get("rel"), edge.get("target")
            if r and t and t != exclude and not (rel and r != rel):
                counts[(r, t)] = counts.get((r, t), 0) + 1
    return counts


def _edges_sorted(counts: dict, k: int | None = None) -> list:
    """A {(rel,target): count} map → [{rel, target, notes}] strongest first."""
    out = [{"rel": r, "target": t, "notes": c}
           for (r, t), c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out[:k] if k else out


def notes_for_entity(entity: str, project: str | None = None, k: int = 20,
                     idx: dict | None = None) -> list[dict]:
    """Live notes tagged with `entity` (faceted recall), newest first. The entity is
    normalised, so 'CUDA' and 'cuda' match. `idx` reuses a pre-built index (no rescan); with no
    idx and a built SQLite graph (F4), only the matching stems' files are read, not the vault."""
    mh = _m()
    norm = mh._norm_entities([entity])
    if not norm:
        return []
    if idx is None:
        sx = _sql()
        if sx is not None:
            metas = [mh._note_meta_for_stem(s) for s in sx.sql_stems_for_entity(norm[0], project)]
            metas = [n for n in metas if n]
            return sorted(metas, key=lambda n: n.get("date", ""), reverse=True)[:k]
    idx = idx if idx is not None else entity_index(project)
    return sorted(idx.get(norm[0], []), key=lambda n: n.get("date", ""), reverse=True)[:k]


def co_occurring(entity: str, project: str | None = None, k: int = 10,
                 idx: dict | None = None) -> list[tuple]:
    """Entities that share a note with `entity` (implicit relations), by shared-note count."""
    norm = _m()._norm_entities([entity])
    if not norm:
        return []
    e = norm[0]
    if idx is None:
        sx = _sql()
        if sx is not None:
            return sx.sql_co_occurring(e, project, k)
    idx = idx if idx is not None else entity_index(project)
    counts: dict[str, int] = {}
    for n in idx.get(e, []):
        for other in n.get("entities") or []:
            if other != e:
                counts[other] = counts.get(other, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def entity_graph(project: str | None = None, top: int = 30) -> dict:
    """Overview of the graph: the most-connected entities with their note count and top
    co-occurring neighbours. Builds the index ONCE and reuses it for every entity (was a
    rescan per entity → 31 scans for one call; audit 2026-06-20)."""
    idx = entity_index(project)
    ranked = sorted(idx.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]
    return {e: {"notes": len(notes), "links": co_occurring(e, project, k=5, idx=idx)}
            for e, notes in ranked}


def related_by(entity: str, rel: str | None = None, project: str | None = None,
               k: int = 20, idx: dict | None = None) -> list:
    """Typed edges declared by lessons tagged with `entity` (Phase 2, relation-aware
    multi-hop): [{rel, target, notes}] by how many of those lessons declare the edge,
    optionally filtered to one `rel`. Targets are entities, so a result's `target` is a
    valid `entity` for the next related_by/notes_for_entity call."""
    norm = _m()._norm_entities([entity])
    if not norm:
        return []
    rfilter = _m()._norm_entities([rel])
    rel_one = rfilter[0] if rfilter else None
    if idx is None:
        sx = _sql()
        if sx is not None:
            return sx.sql_related_by(norm[0], rel_one, project, k)
    idx = idx if idx is not None else entity_index(project)
    return _edges_sorted(_edge_counts(idx.get(norm[0], []), exclude=norm[0], rel=rel_one), k)


def relation_graph(project: str | None = None, top: int = 30) -> dict:
    """Per-entity typed-edge overview: {entity: [{rel, target, notes}]}, entities ranked by
    total edge weight. One index build; shares _edge_counts with related_by."""
    idx = entity_index(project)
    out = {e: _edges_sorted(_edge_counts(notes, exclude=e), 8) for e, notes in idx.items()}
    out = {e: edges for e, edges in out.items() if edges}
    ranked = sorted(out.items(), key=lambda kv: -sum(x["notes"] for x in kv[1]))[:top]
    return dict(ranked)


def relation_expand(hits, project: str | None = None, max_add: int = 5, rels=None) -> list:
    """Relation-aware retrieval (Phase 2b): given first-stage recall hits (dicts with a
    `stem`), pull in lessons the query did NOT surface directly but that the graph connects
    to them. Follows each hit's typed relation edges to their target entities and returns
    notes about those targets, so a query that hits an OOM mistake also surfaces its
    `fixed-by` fix. Reads note frontmatter (no embedder), bounded by `max_add`; `rels`
    optionally limits which relation types expand (e.g. {'fixed-by','fixes'}). Returns
    recall-shaped dicts tagged with `via` (the edge that pulled them in)."""
    if not hits:
        return []
    mh = _m()
    rfilter = {x for r in (rels or ()) for x in mh._norm_entities([r])} or None
    notes = mh._iter_project_notes(project) if project else mh._iter_all_notes()
    by_stem = {n["stem"]: n for n in notes}
    idx: dict = {}
    for n in notes:
        for e in n.get("entities") or []:
            idx.setdefault(e, []).append(n)
    present = {h.get("stem") for h in hits}
    added, seen_targets = [], set()
    for h in hits:
        meta = by_stem.get(h.get("stem"))
        if not meta:
            continue
        for edge in meta.get("relations") or []:
            t, rel = edge.get("target"), edge.get("rel")
            if not t or t in seen_targets or (rfilter and rel not in rfilter):
                continue
            seen_targets.add(t)
            for n in idx.get(t, []):
                if n["stem"] in present or len(added) >= max_add:
                    continue
                present.add(n["stem"])
                added.append({"score": 0.0, "ntype": n["ntype"], "project": n.get("project"),
                              "title": n["title"], "stem": n["stem"],
                              "description": n.get("desc", ""),
                              "prevention": n.get("prevention", ""),
                              "recurrence": n.get("recurrence"),
                              "via": f"{rel} -> {t}", "low_confidence": True})
            if len(added) >= max_add:
                return added
    return added


def graph_export(project: str | None = None, fmt: str = "mermaid", top: int = 40,
                 cooccurrence: bool = False) -> str:
    """Render the knowledge graph to a portable format: 'mermaid' (renders directly in
    Obsidian / a GitHub markdown block, no tool to install), 'dot' (Graphviz), or 'json'
    (nodes/edges for D3 or any custom view). Typed relation edges are directed and
    labelled; with cooccurrence=True, entities sharing >=2 notes get a dashed undirected
    edge too. Nodes are capped at `top` by note count, so a large vault stays legible."""
    mh = _m()
    notes = mh._iter_project_notes(project) if project else mh._iter_all_notes()
    node_notes: dict = {}                      # entity -> note count
    rel_edges: dict = {}                       # (src, rel, tgt) -> count
    cooc: dict = {}                            # (a, b) sorted -> shared-note count
    for n in notes:
        ents = n.get("entities") or []
        for e in ents:
            node_notes[e] = node_notes.get(e, 0) + 1
        for edge in n.get("relations") or []:
            r, t = edge.get("rel"), edge.get("target")
            if not r or not t:
                continue
            node_notes.setdefault(t, 0)        # a target is a node even if untagged
            for src in ents:
                if src != t:
                    rel_edges[(src, r, t)] = rel_edges.get((src, r, t), 0) + 1
        if cooccurrence:
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    if a != b:
                        cooc[tuple(sorted((a, b)))] = cooc.get(tuple(sorted((a, b))), 0) + 1
    keep = {e for e, _ in sorted(node_notes.items(), key=lambda kv: (-kv[1], kv[0]))[:top]}
    redges = sorted(((s, r, t, c) for (s, r, t), c in rel_edges.items() if s in keep and t in keep),
                    key=lambda x: (-x[3], x[0], x[2]))
    cedges = sorted(((a, b, c) for (a, b), c in cooc.items() if c >= 2 and a in keep and b in keep),
                    key=lambda x: -x[2])

    if fmt == "json":
        return json.dumps({"nodes": [{"id": e, "notes": node_notes[e]}
                                     for e in sorted(keep, key=lambda e: (-node_notes[e], e))],
                           "edges": [{"source": s, "rel": r, "target": t, "notes": c}
                                     for s, r, t, c in redges],
                           "cooccurrence": [{"a": a, "b": b, "notes": c} for a, b, c in cedges]},
                          ensure_ascii=False, indent=1)
    if fmt == "dot":
        out = ["digraph nevertwice {", '  rankdir=LR; node [shape=box];']
        for e in sorted(keep):
            out.append(f'  "{e}" [label="{e}\\n({node_notes[e]})"];')
        for s, r, t, c in redges:
            out.append(f'  "{s}" -> "{t}" [label="{r}"];')
        for a, b, c in cedges:
            out.append(f'  "{a}" -> "{b}" [dir=none, style=dashed];')
        out.append("}")
        return "\n".join(out)
    # mermaid (default)
    ids = {e: f"n{i}" for i, e in enumerate(sorted(keep))}
    out = ["graph LR"]
    for e in sorted(keep):
        out.append(f'  {ids[e]}["{e}"]')
    for s, r, t, c in redges:
        out.append(f'  {ids[s]} -->|{r}| {ids[t]}')
    for a, b, c in cedges:
        out.append(f'  {ids[a]} -.- {ids[b]}')
    return "\n".join(out)
