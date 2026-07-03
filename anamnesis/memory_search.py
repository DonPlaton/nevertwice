#!/usr/bin/env python3
"""On-demand search over the memory vault — the recall tool ANY agent can shell
out to mid-task (audit C3). Read-only; never touches the write pipeline.

    python memory_search.py "gpu memory leak"               # all projects, with facts
    python memory_search.py "beam search" myproject         # one project
    python memory_search.py "race condition" --k=15         # more hits
    python memory_search.py "cuda oom" --expand             # + linked sibling notes
    python memory_search.py "cuda oom" --json               # machine-readable (for agents)
    python memory_search.py "cuda oom" --brief              # titles only

Works even with the GPU fully busy: if Ollama can't embed the query it falls
back to lexical token overlap over the cached note text (audit H5).
"""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m
import reranker_ce as _ce          # opt-in trained cross-encoder (lazy heavy deps)

ICON = {"mistake": "⚠️", "pattern": "✅", "decision": "🎯"}
# Adaptive abstention lives in the core (memory_hook._low_confidence) so the CLI and the
# SessionStart/per-prompt hook gate identically (DRY). CONFIDENT_SIM kept as the absolute-floor
# alias for back-compat; the relative margin is ANAMNESIS_CONFIDENT_MARGIN.
CONFIDENT_SIM = m.RETRIEVAL_SIM_FLOOR
_low_confidence = m._low_confidence


def _linked(stem: str, ntype: str, limit: int = 6) -> list[str]:
    """Sibling/linked notes of a hit — makes the wikilink graph usable from
    recall, not just Obsidian's graph view (audit H4)."""
    fp = m.VAULT / m.TYPE_FOLDER.get(ntype, "") / f"{stem}.md"
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen, out = set(), []
    for l in re.findall(r"\[\[([^]|#]+)", text):
        l = l.strip()
        if l and l != stem and l not in seen:
            seen.add(l)
            out.append(l)
    return out[:limit]


def _as_result(h: dict, low: bool) -> dict:
    """An index_sqlite hit → the search_core result shape (adds low_confidence)."""
    return {"score": h.get("score", 0.0), "ntype": h.get("ntype"),
            "project": h.get("project"), "title": h.get("title"),
            "stem": h.get("stem"), "description": h.get("description", ""),
            "prevention": h.get("prevention", ""), "low_confidence": low}


def _lex_overlap(qtok: set, r: dict, s: str) -> float:
    """Token-overlap lexical score (title+desc+prevention+stem) plus the recurrence
    prior, or 0.0 when no query term matches. One source for the lexical-fallback
    formula that three recall paths share (launch-round dedup)."""
    toks = m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')} {s}")
    ov = len(qtok & toks)
    return (ov / ((len(qtok) ** 0.5) or 1.0) + m._recur_boost(r)) if ov else 0.0


def _lexical_only(query: str, project: str | None, k: int, cache: dict) -> list[dict]:
    """Pure lexical recall for a store with no usable embeddings (#32) — so a user
    who never ran an embedder (no Ollama, no cloud key) still gets recall instead of
    nothing. Prefers the SQLite FTS5 index (bm25, scales, indexes text-only notes);
    falls back to token overlap over the cached note text when FTS is unavailable.
    Always flagged low_confidence — lexical is a weaker signal than confident semantic."""
    try:
        idx = m._scale_index()
        if idx:
            hits, mode = idx.search(query, project or None, k)
            if hits and "lexical" in mode:
                return [_as_result(h, True) for h in hits]
    except Exception:
        pass
    qtok = m._tokens(query)
    if not qtok:
        return []
    scored = []
    for s, r in cache.items():
        if not isinstance(r, dict) or (project and r.get("project") != project):
            continue
        sc = _lex_overlap(qtok, r, s)
        if sc:
            scored.append((sc, s, r))
    scored.sort(key=lambda x: -x[0])
    return [{"score": round(sc, 3), "ntype": r.get("ntype"), "project": r.get("project"),
             "title": r.get("title"), "stem": s, "description": r.get("desc", ""),
             "prevention": r.get("prevention", ""), "low_confidence": True}
            for sc, s, r in scored[:k]]


def search_core(query: str, project: str | None = None, k: int = 10,
                rerank: bool | None = None, xrerank: bool | None = None):
    """Rank memory notes for a query — calibrated score fusion of semantic (embedding
    cosine) and lexical (BM25), recurrence-boosted, with a pure-lexical fallback when the
    GPU/Ollama is busy. Returns (results, mode); results is a list of dicts. Shared by the
    CLI and the MCP server (I-8) so both rank identically and stay DRY. The same fusion the
    hook injection path uses; ANAMNESIS_FUSION=rrf restores the legacy semantic-primary path.

    Two opt-in rerankers over an over-fetched candidate pool, both off by default:
    - xrerank (ANAMNESIS_XRERANK=1): a trained cross-encoder (bge-reranker-v2-m3) —
      the measured precision win (LongMemEval R@1 0.55→0.61, MRR +0.06). Local GPU,
      needs the [reranker] extra. Takes precedence when both are set.
    - rerank (I-3, ANAMNESIS_RERANK=1): a free cloud model reorders the top-k —
      higher precision at the cost of cloud latency.
    Never used on the hot hook paths."""
    if rerank is None:
        rerank = m.RERANK_ENABLED
    if xrerank is None:
        xrerank = _ce.ENABLED
    cache = m.load_embed_cache()
    has_any = any(isinstance(r, dict) and isinstance(r.get("vec"), list)
                  for r in cache.values())
    cands = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)
             and (not project or r.get("project") == project)]
    if not cands:
        # No embedded vectors for this filter. Before giving up, try a pure lexical
        # answer — the FTS5 index covers text-only notes (#32), so a no-embedder user
        # gets recall, not silence. Only fall through to the embed-rebuild hint when
        # lexical also finds nothing.
        lex = _lexical_only(query, project, k, cache)
        if lex:
            return lex, "lexical (no embedder)"
        # "nothing embedded at all" ≠ "this project has no notes" — don't tell the
        # user to rebuild a perfectly good index when a filter just missed (audit A14)
        return [], ("empty" if not has_any and not cache else "empty-project")

    scored, mode = [], "semantic"
    qvec = (m.embed_text(query, kind=m.query_embed_kind(), project=project)
            if m.embed_cache_usable() and m.embedder_available(2) else None)
    if qvec:
        sims = [(m.cosine(qvec, r.get("vec") or []), s, r) for s, r in cands]
        raw = sorted((c for c, _, _ in sims), reverse=True)
        amb = m._ambiguity(raw)                                   # adaptive recurrence
        rec_of = {s: r for _, s, r in sims}
        if m.RETRIEVAL_FUSION == "rrf":                           # legacy: semantic-primary
            for sim, s, r in sims:
                if sim > m.RETRIEVAL_NEAR_FLOOR:      # named floor, not a magic 0.15 (audit)
                    scored.append((sim + m._recur_boost(r) * amb, s, r))
        else:                                                     # calibrated fusion (shipped default)
            sem_scores = {s: sim for sim, s, r in sims if sim > m.RETRIEVAL_NEAR_FLOOR}
            fused = m._calibrated_fusion(sem_scores, m._bm25_scores(m._tokens(query), cands))
            for s, fsc in fused.items():
                scored.append((fsc + m._recur_boost(rec_of.get(s, {})) * amb, s, rec_of.get(s, {})))
            mode = "hybrid"
        if _low_confidence(raw):
            mode = mode + " (low-confidence)"
    else:
        # embedder pinged OK but the query embed failed → weak token-overlap ranking. Mark it
        # low-confidence like every other lexical path — `low_conf` below is derived from this
        # string, so omitting it silently bypassed the abstention gate for programmatic callers
        # (code-review 2026-07, HIGH).
        mode = "lexical (Ollama/GPU busy) (low-confidence)"
        qtok = m._tokens(query)
        for s, r in cands:
            sc = _lex_overlap(qtok, r, s)
            if sc:
                scored.append((sc, s, r))
    scored.sort(key=lambda x: -x[0])
    # Mixed store: text-only notes (no vector — written while no embedder was up, #32)
    # never enter `cands`, so without this they'd be INVISIBLE to recall as soon as the
    # store holds ANY embedded note (audit 2026-06-18 CRIT — the all-text-only path above
    # only fires when `cands` is empty). Supplement the ranking with their lexical
    # (token-overlap) matches so they stay reachable; they rank after vector hits and
    # carry low_confidence (lexical is a weaker signal than a confident semantic match).
    seen = {s for _, s, _ in scored}
    qtok = m._tokens(query)
    text_extra = []
    if qtok:
        for s, r in cache.items():
            if (not isinstance(r, dict) or r.get("vec") is not None or s in seen
                    or (project and r.get("project") != project)):
                continue
            sc = _lex_overlap(qtok, r, s)
            if sc:
                text_extra.append((sc, s, r))
    text_extra.sort(key=lambda x: -x[0])
    pool = max(k, m.RERANK_POOL) if (rerank or xrerank) else k
    # carry the abstention signal ON the results too — api.recall() drops `mode`, so a
    # programmatic caller (integrations, custom agents) otherwise can't tell these are
    # nearest-but-weak matches the hook would not auto-inject (audit 2026-06-18).
    low_conf = "low-confidence" in mode

    def _mk(sc, s, r, lc):
        return {"score": round(sc, 3), "ntype": r.get("ntype"),
                "project": r.get("project"), "title": r.get("title"), "stem": s,
                "description": r.get("desc", ""), "prevention": r.get("prevention", ""),
                "low_confidence": lc}
    results = ([_mk(sc, s, r, low_conf) for sc, s, r in scored]
               + [_mk(sc, s, r, True) for sc, s, r in text_extra])[:pool]
    if xrerank and len(results) > 1:                 # trained cross-encoder wins if both set
        results = _ce.reorder(query, results, k)
        mode += " + xrerank"
    elif rerank and len(results) > 1:
        results = m.rerank_notes(query, results, k=k, project=project)
        mode += " + rerank"
    return results, mode


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    rest = [a for a in argv if not a.startswith("--")]
    k = 10
    as_of_date = None
    for a in flags:
        if a.startswith("--k="):
            try:
                k = int(a.split("=", 1)[1])
            except ValueError:
                pass
        elif a.startswith("--as-of="):
            as_of_date = a.split("=", 1)[1].strip()

    # Entity knowledge graph (no query needed): facet by one entity, the entity graph,
    # the typed-relation graph, or a portable export of the whole graph.
    entity = next((a.split("=", 1)[1] for a in flags if a.startswith("--entity=")), None)
    graph_fmt = next(("mermaid" if a == "--graph" else a.split("=", 1)[1]
                      for a in flags if a == "--graph" or a.startswith("--graph=")), None)
    if entity or "--entities" in flags or "--relations" in flags or graph_fmt:
        proj = rest[0] if rest else None     # the positional is the project in entity mode
        if graph_fmt:
            print(m.graph_export(proj, fmt=graph_fmt, cooccurrence="--cooccurrence" in flags))
            return
        if entity:
            idx = m.entity_index(proj)        # one scan, shared across the three facets
            notes = m.notes_for_entity(entity, proj, k, idx=idx)
            co = m.co_occurring(entity, proj, idx=idx)
            edges = m.related_by(entity, project=proj, idx=idx)
            if "--json" in flags:
                print(json.dumps({"entity": entity, "notes": [
                    {"ntype": n["ntype"], "title": n["title"], "stem": n["stem"],
                     "project": n.get("project")} for n in notes],
                    "co_occurring": [{"entity": e, "shared": c} for e, c in co],
                    "relations": edges}, ensure_ascii=False, indent=2))
            else:
                print(f"{len(notes)} note(s) tagged {entity!r}" + (f" [{proj}]" if proj else "") + ":")
                for n in notes:
                    print(f"  {ICON.get(n['ntype'], '·')} {n['title']}  ({n['stem']})")
                if co:
                    print("  co-occurs: " + ", ".join(f"{e} x{c}" for e, c in co))
                if edges:
                    print("  edges: " + ", ".join(f"--{e['rel']}--> {e['target']}" for e in edges))
        elif "--relations" in flags:
            rg = m.relation_graph(proj)
            if "--json" in flags:
                print(json.dumps(rg, ensure_ascii=False, indent=2))
            else:
                print(f"Relation graph{(' [' + proj + ']') if proj else ''} — {len(rg)} source(s):")
                for src, edges in rg.items():
                    for e in edges:
                        print(f"  {src} --{e['rel']}--> {e['target']}  (x{e['notes']})")
        else:
            g = m.entity_graph(proj)
            if "--json" in flags:
                print(json.dumps(g, ensure_ascii=False, indent=2))
            else:
                print(f"Entity graph{(' [' + proj + ']') if proj else ''} — {len(g)} entit(y/ies):")
                for e, info in g.items():
                    links = ", ".join(f"{le} x{lc}" for le, lc in info["links"])
                    print(f"  {e}  ({info['notes']} notes)" + (f"  -> {links}" if links else ""))
        return

    if not rest:
        print("usage: memory_search.py <query> [project] [--k=N] [--expand] [--expand-relations] "
              "[--json] [--brief] [--rerank] [--xrerank] [--as-of=YYYY-MM-DD] | --entity=X [project] | "
              "--entities [project] | --relations [project] | --graph[=mermaid|dot|json] [project]",
              file=sys.stderr)
        sys.exit(1)
    query = rest[0]
    project = rest[1] if len(rest) > 1 else None

    # M-5: point-in-time recall — what we believed about <project> on a given day
    if as_of_date:
        if not project:
            print("--as-of needs a project: memory_search.py <ignored> <project> --as-of=DATE",
                  file=sys.stderr)
            sys.exit(1)
        snap = m.as_of(project, as_of_date)
        if "--json" in flags:
            print(json.dumps(snap, ensure_ascii=False, indent=2))
            return
        print(f"As of {as_of_date}, project {project} held {len(snap)} belief(s):")
        for r in snap:
            span = f"{r['valid_from']}…{r['valid_to'] or 'now'}"
            print(f"  {ICON.get(r['ntype'], '·')} {r['title']}  ({span})  {r['stem']}")
        return

    top, mode = search_core(query, project, k, rerank=("--rerank" in flags) or None,
                            xrerank=("--xrerank" in flags) or None)
    if "--expand-relations" in flags and top:        # Phase 2b: relation-aware expansion
        top = top + m.relation_expand(top, project, max_add=k)
    if mode == "empty":
        print("(no memory stored yet — see it work with `python examples/demo.py`, or capture "
              "a session, then `python anamnesis/embed_index.py`)", file=sys.stderr)
        sys.exit(1)
    if mode == "empty-project":
        print(f"(no memory for project {project!r})")
        return

    if "--json" in flags:
        print(json.dumps(top, ensure_ascii=False, indent=2))
        return

    if not top:
        print("(no results)")
        return
    if "low-confidence" in mode:
        print("⚠ no confident match — nearest notes below may be off-topic:")
    print(f"Top {len(top)} for {query!r}" + (f" [{project}]" if project else "")
          + f"  — {mode}")
    for r in top:
        nt = r.get("ntype", "")
        title = m._strip_lead_icon(r.get("title") or r.get("stem", ""))
        via = f"  (via {r['via']})" if r.get("via") else ""    # Phase 2b graph-expansion marker
        print(f"  {r['score']:5.2f} {ICON.get(nt, '·')} [{r.get('project')}] {title}{via}")
        if "--brief" not in flags:
            snip = m._note_snippet(r["stem"], nt, max_chars=300) or r.get("description", "")
            if snip:
                print(f"        {snip}")
        print(f"        {r['stem']}")
        if "--expand" in flags:
            for sib in _linked(r["stem"], nt):
                print(f"          ↳ [[{sib}]]")


if __name__ == "__main__":
    main()
