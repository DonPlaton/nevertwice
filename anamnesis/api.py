#!/usr/bin/env python3
"""Anamnesis — stable in-process Python API.

The CLIs (`memory_search`, `remember`, `ingest`) and the MCP server are the
universal interface, but a Python caller — a framework adapter, a custom agent, a
notebook — shouldn't have to shell out. This module is that library surface: three
functions, stdlib-only, no argparse, no `sys.exit`. The CLIs and the LangChain /
LlamaIndex integrations and the generic `capture` decorator are all thin shims over
these, so there is exactly one write path and one read path.

    from anamnesis.api import recall, remember, capture_session

    remember("Crash-safe writes", project="myproj", type="pattern",
             prevention="write to a tmp file then os.replace — never partial files")
    hits = recall("how do I persist files safely", project="myproj", k=5)
    capture_session(transcript_text, project="myproj", agent="my-bot")
"""
import os
import uuid
from datetime import datetime
from pathlib import Path

sys_path_root = str(Path(__file__).resolve().parent)
import sys
if sys_path_root not in sys.path:
    sys.path.insert(0, sys_path_root)
import memory_hook as m
import memory_search as _search
import digest as _digest
import dashboard as _dashboard
import guards as _guards
import anticipate as _anticipate
import causal as _causal


def recall(query: str, project: str | None = None, k: int = 5,
           *, rerank: bool = False, expand_relations: bool = False,
           max_expand: int = 5) -> list[dict]:
    """Rank memory notes for `query`. Returns a list of dicts with keys:
    `score, ntype, project, title, stem, description, prevention`. Empty list when
    nothing is embedded or matches. Semantic (embedding cosine) with a GPU-free
    lexical fallback when Ollama is busy; `rerank=True` adds an opt-in cloud rerank.

    `expand_relations=True` is relation-aware retrieval (Phase 2b): after the direct
    hits, it appends up to `max_expand` graph-connected lessons reached by the hits'
    typed relation edges (a query about a bug also surfaces its fixed-by fix), each
    tagged with `via`. Off by default, so plain recall is unchanged."""
    if not query or not query.strip():
        return []
    results, _mode = _search.search_core(query, project, k, rerank=rerank)
    if expand_relations and results:
        results = results + m.relation_expand(results, project, max_add=max_expand)
    return results


def format_note(result: dict) -> str:
    """Render a `recall()` result as a compact block for an LLM context window or a
    human: 'TYPE — title' then the description, then 'Prevention: …' (empties omitted).
    Shared by the LangChain/LlamaIndex adapters and the capture helpers so every
    surface renders a memory the same way."""
    nt = (result.get("ntype") or "").strip()
    head = (result.get("title") or "").strip()
    lines = [f"{nt.upper()} — {head}" if nt and head else (head or nt)]
    desc = (result.get("description") or "").strip()
    prev = (result.get("prevention") or "").strip()
    if desc:
        lines.append(desc)
    if prev:
        lines.append(f"Prevention: {prev}")
    return "\n".join(ln for ln in lines if ln)


def _entity_view(n: dict) -> dict:
    """A note meta → the same shape recall() returns, plus `entities`/`date`."""
    return {"ntype": n.get("ntype"), "project": n.get("project"),
            "title": n.get("title"), "stem": n.get("stem"),
            "description": n.get("desc", ""), "prevention": n.get("prevention", ""),
            "entities": n.get("entities", []), "recurrence": n.get("recurrence", 1),
            "date": n.get("date", "")}


def notes_for_entity(entity: str, project: str | None = None, k: int = 20) -> list[dict]:
    """Every live note tagged with `entity` (faceted recall, no query needed), newest
    first. The entity knowledge graph (Phase 1): the LLM tags each lesson with its key
    entities (tools, concepts, files), so 'show me everything about CUDA' is one call.
    Reads note frontmatter, so it works with NO embedder. Returns recall-shaped dicts."""
    return [_entity_view(n) for n in m.notes_for_entity(entity, project, k)]


def co_occurring(entity: str, project: str | None = None, k: int = 10) -> list[dict]:
    """Entities that share a note with `entity` (implicit relations), strongest first:
    `[{entity, shared}]` where `shared` is the number of notes the two appear in together."""
    return [{"entity": e, "shared": c} for e, c in m.co_occurring(entity, project, k)]


def entity_graph(project: str | None = None, top: int = 30) -> dict:
    """Overview of the entity knowledge graph: the most-connected entities, each with its
    note count and top co-occurring neighbours. `{entity: {notes, links}}`."""
    return m.entity_graph(project, top)


def related_by(entity: str, rel: str | None = None, project: str | None = None,
               k: int = 20) -> list:
    """Typed edges declared by lessons about `entity` (Phase 2, relation-aware multi-hop):
    `[{rel, target, notes}]`, optionally filtered to one `rel` (causes / caused-by / fixes /
    fixed-by / depends-on / ...). Each `target` is itself an entity, so chain the call to
    traverse: related_by(related_by('cuda','fixed-by')[0]['target'])."""
    return m.related_by(entity, rel, project, k)


def relation_graph(project: str | None = None, top: int = 30) -> dict:
    """Per-entity typed-edge overview: `{entity: [{rel, target, notes}]}`, entities ranked
    by total edge weight. The Phase-2 graph."""
    return m.relation_graph(project, top)


def graph_export(project: str | None = None, fmt: str = "mermaid", top: int = 40,
                 cooccurrence: bool = False) -> str:
    """Render the knowledge graph to a portable string: `mermaid` (renders in Obsidian /
    a GitHub markdown block, no tool needed), `dot` (Graphviz), or `json` (nodes/edges).
    Typed relation edges are directed and labelled; `cooccurrence=True` adds dashed edges
    for entities that share notes."""
    return m.graph_export(project, fmt, top, cooccurrence)


def entity_card(entity: str) -> str:
    """The Brain-layer card for a first-class entity (paper / method / dataset / ...): a
    distilled, cross-project rollup of everything the memory knows about it — where it is used,
    its typed neighbours, and the lessons grouped by kind. Built on the fly if not cached.
    PULL-ONLY by design: this is how Brain knowledge reaches an agent — by explicit request,
    never by injection. '' when the entity has no notes (or no brain profile is active)."""
    return m.entity_card(entity)


def entities_by_type(etype: str, project: str | None = None) -> list:
    """Every entity classified as `etype` (e.g. all 'method' or 'paper' entities), sorted —
    the enumeration behind the entity cards. Empty until a brain profile has typed something."""
    return m.entities_by_type(etype, project)


def entity_timeline(entity: str, project: str | None = None) -> dict:
    """The chronological history of an entity across LIVE and SUPERSEDED notes (Brain F3):
    `{entity, first_seen, last_seen, count, mentions, evolution}`. `evolution` lists the points
    where an earlier take was superseded — how the understanding of the entity changed over time.
    Pull-only — surfaced in the entity card and here, never injected. `{}` for an unknown entity."""
    return m.entity_timeline(entity, project)


def conflicts(project: str | None = None, limit: int = 50) -> list[dict]:
    """The contradiction / supersession ledger — every fact the memory revised, newest
    first: `[{kind, project, ntype, old_stem, old_title, old_date, new_stem, new_title,
    new_date, resolved}]`. Anamnesis resolves contradictions at write time (M-2: a new
    fact supersedes the old, retired to `Superseded/`), so this IS that audit trail.
    `resolved=False` flags a still-evolving chain. Pure frontmatter scan — no embedder,
    no LLM, no network. The read-side answer to memanto's `conflicts`."""
    return _digest.compute_conflicts(m.slug_project(project) if project else None, limit=limit)


def digest(project: str | None = None, days: int = 7) -> dict:
    """A point-in-time rollup of the store — what was added/revised in the last `days`,
    per project and type, plus the most-connected entities: `{generated, window_days,
    totals, by_project, recent, changed, top_entities}`. Read-only synthesis (the
    daily/weekly "what's new" view); no embedder, no LLM, no network."""
    return _digest.compute_digest(project, days=days)


def dashboard(project: str | None = None, days: int = 30) -> str:
    """Render the whole memory store to one self-contained HTML string (inline CSS, no
    server, no external asset) — stats, per-project breakdown, recent notes, the
    contradiction ledger, and the most-connected entities. Write it to a `.html` file and
    open it in a browser. Read-only frontmatter scan; no embedder, no LLM, no network."""
    return _dashboard.build_html(project, days=days)


def what_breaks(entity: str, project: str | None = None, *, depth: int = 2) -> dict:
    """Active memory, axis C — the counterfactual. What may break if you change / touch
    `entity`: `{entity, impacts:[{effect, via, hops, notes}], failure_modes:[...], evidence}`,
    induced by traversing the causal model (the store's typed relation edges, oriented into a
    cause→effect impact graph) plus the failure modes mistakes attach to the entity. A
    synthesized consequence set, not an episode dump — the whole point is answering the
    question at a fraction of the tokens a recall-everything would spend."""
    return _causal.what_breaks(entity, project, depth=depth)


def counterfactual(entity: str, project: str | None = None) -> str:
    """The one-paragraph synthesized answer to 'what happens if I change `entity`' (axis C):
    top downstream impacts + known failure modes + evidence stems, in a few lines. '' when the
    entity has no causal footprint. This is the token-economical face of causal memory."""
    return _causal.counterfactual(entity, project)


def why(entity: str, project: str | None = None, *, depth: int = 2) -> dict:
    """The upstream counterfactual (axis C): what causes / underpins `entity`, by traversing the
    causal impact graph in reverse. `{entity, causes:[{cause, via, hops}]}`."""
    return _causal.why(entity, project, depth=depth)


def guards_check(action_text: str, *, project: str | None = None,
                 path: str | None = None, tool: str | None = None) -> list[dict]:
    """Active memory, axis A — the 0-token hot path. Return the guards that fire for a
    proposed action (a diff, command, or code the agent is about to write), or `[]` — and
    NOTHING reaches context unless one matches. Each hit is `{id, status, message, scope}`;
    a `blocking` status means stop and comply or override-with-reason. Pure regex+scope
    match: no embedder, no LLM, no network. See `research/ACTIVE_MEMORY.md`."""
    return _guards.check(action_text, project=project, path=path, tool=tool)


def guard_feedback(guard_id: str, outcome: str, *, session_id: str | None = None,
                   reason: str | None = None) -> dict | None:
    """The Popperian loop: record what happened after a guard fired. `outcome` ∈
    {'helped', 'false_positive', 'corroborated'}. K distinct-session 'helped' promote an
    advisory guard to blocking; M 'false_positive' demote/retire it; a `reason` on an
    override is stored as a learned exception that narrows the guard. Returns the updated
    guard. This is how reality falsifies a wrong guard — memory proposes, reality disposes."""
    return _guards.feedback(guard_id, outcome, session_id=session_id, reason=reason)


def anticipate(trajectory: str, project: str | None = None, *, k: int = 1,
               use_embeddings: bool = False) -> list[dict]:
    """Active memory, axis B — predict the failure the current `trajectory` (the plan / files /
    recent steps the agent is about to act on) is heading toward, by resemblance to past
    mistakes. Returns up to `k` `{stem, risk, title, message}` above an adaptive threshold, or
    `[]` (SILENT, 0 tokens) — one precise warning, never a dump; token spend ∝ predicted risk.
    Lexical by default; `use_embeddings=True` adds a semantic blend when the embedder is free.
    Pair with `guard_feedback`-style `anticipate` feedback to keep a cry-wolf predictor quiet."""
    return _anticipate.anticipate(trajectory, project, k=k, use_embeddings=use_embeddings)


def anticipate_feedback(mistake_stem: str, outcome: str) -> dict:
    """Adapt axis B: `outcome` ∈ {'helped','false_alarm'}. A false alarm raises that failure
    mode's firing bar (Popperian — a cry-wolf predictor goes quiet); 'helped' keeps it
    sensitive. Returns the updated per-failure state."""
    return _anticipate.feedback(mistake_stem, outcome)


def guards_generate(project: str | None = None, *, limit: int | None = None) -> int:
    """Distill guards from the vault's mistake notes (sleep-time, off the hot path); returns
    how many new guards were added. Idempotent. Uses the cloud/Ollama router for precise
    patterns with a deterministic fallback."""
    return _guards.generate_from_vault(project, limit=limit)


def remember(title: str, *, project: str, type: str = "pattern",
             description: str = "", prevention: str = "", tags=(),
             supersedes: str = "", entities=(), relations=None,
             embed: bool = True) -> str | None:
    """Write one typed memory note and return its stem, or None if it was rejected
    as a prompt-injection payload. `type` ∈ {pattern, mistake, decision}. Embeds
    immediately when the embedder is free (so it is recallable at once); otherwise
    the note is written and folded into semantic recall on the next embed run.
    `entities` (tools/concepts/files) feed the entity knowledge graph.

    Raises ValueError on bad arguments and RuntimeError if the vault lock is busy."""
    if type not in m.TYPED_TYPES:
        raise ValueError(f"type must be one of {sorted(m.TYPED_TYPES)}")
    if not project or not title:
        raise ValueError("project and title are required")
    proj = m.slug_project(project)
    tag_list = m._norm_tags(tags.split(",") if isinstance(tags, str) else list(tags))
    ent = entities.split(",") if isinstance(entities, str) else list(entities)
    item = {"title": title, "description": description or "",
            "prevention": prevention or "", "supersedes": supersedes or "",
            "entities": ent, "relations": list(relations or [])}
    if not m.acquire_lock(timeout_s=60):
        raise RuntimeError("vault busy (lock held by another process) — try again")
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        stem = m.write_typed_note(m.TYPE_FOLDER[type], item, proj, date, tag_list, type)
        if not stem:
            return None
        if embed and m.embedder_available(2):
            m.update_embeddings([(stem, type, proj, title,
                                  description or "", prevention or "")])
        m.rebuild_index()
        m.git_autocommit()
        return stem
    finally:
        m.release_lock()


def remember_lessons(lessons, *, project: str, embed: bool = True) -> list[str]:
    """Write a BATCH of agent-extracted lessons — the turnkey "the agent is the
    extractor" path (#34). No separate extraction model runs: the agent (Claude Code
    or any LLM) decides what it learned, emits structured lessons, and this persists
    them through the same write path as `remember()`. Each lesson is a dict with keys
    `type` (pattern|mistake|decision), `title`, and optional `description`,
    `prevention`, `tags`, `supersedes`. Returns the written stems (lessons that are
    malformed, untitled, or rejected as injection-shaped are skipped, not raised).

    One vault lock / one index rebuild / one git commit for the whole batch — so an
    agent recording five lessons at end-of-task doesn't produce five commits. Empty
    list → no-op. Raises RuntimeError only if the vault lock can't be acquired."""
    if not project:
        raise ValueError("project is required")
    proj = m.slug_project(project)
    valid = []
    for ln in lessons or []:
        if not isinstance(ln, dict):
            continue
        title = (ln.get("title") or "").strip()
        typ = (ln.get("type") or "pattern").strip()
        if not title or typ not in m.TYPED_TYPES:
            continue
        valid.append((typ, title, ln))
    if not valid:
        return []
    if not m.acquire_lock(timeout_s=60):
        raise RuntimeError("vault busy (lock held by another process) — try again")
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        written, embed_recs = [], []
        for typ, title, ln in valid:
            raw_tags = ln.get("tags") or ()
            tag_list = m._norm_tags(raw_tags.split(",") if isinstance(raw_tags, str)
                                    else list(raw_tags))
            item = {"title": title, "description": ln.get("description") or "",
                    "prevention": ln.get("prevention") or "",
                    "supersedes": ln.get("supersedes") or "",
                    "entities": ln.get("entities") or [],
                    "relations": ln.get("relations") or []}
            stem = m.write_typed_note(m.TYPE_FOLDER[typ], item, proj, date, tag_list, typ)
            if stem:                       # None == rejected (injection-shaped) — skip it
                written.append(stem)
                embed_recs.append((stem, typ, proj, title,
                                   item["description"], item["prevention"]))
        if embed and embed_recs:
            # vectors when an embedder is up, else text-only (still FTS-recallable, #32)
            m.update_embeddings(embed_recs)
        if written:
            m.rebuild_index()
            m.git_autocommit()
        return written
    finally:
        m.release_lock()


def capture_session(text: str, *, project: str | None = None,
                    agent: str | None = None, session_id: str | None = None,
                    cwd: str | None = None, trigger: str = "ingest") -> dict:
    """Extract memory from a finished agent session: the same extraction →
    Patterns/Mistakes/Decisions → Context → embeddings pipeline the live hook and
    `ingest.py` run, tagged with `agent`. Returns a summary dict:
    `{stored, project, agent, patterns, mistakes, decisions, session_id}`.

    A stable `session_id` makes re-ingestion idempotent. Needs an LLM backend
    (cloud key or local Ollama); raises RuntimeError if none is reachable or the
    vault lock is busy, ValueError on empty text."""
    if not text or not text.strip():
        raise ValueError("empty transcript text")
    agent = (agent or m.DEFAULT_AGENT).strip() or m.DEFAULT_AGENT
    cwd = cwd or os.getcwd()
    sid = session_id or f"ingest-{uuid.uuid4().hex[:16]}"
    if not m.llm_available():
        raise RuntimeError("no LLM backend (cloud key unset + Ollama down)")
    if not m.acquire_lock(timeout_s=120):
        raise RuntimeError("could not acquire vault lock — another process is busy")
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        db = m.load_processed()
        run_log: list[dict] = []
        ok = m.process_session(sid, cwd, "", trigger, db, run_log=run_log,
                               agent=agent, transcript_text=text,
                               project_override=project)
        if ok:
            m.rebuild_index()
            m.archive_old_sessions()
            m.archive_old_typed()
            m.prune_processed_db(db)
            m.git_autocommit()
        r = run_log[-1] if run_log else {}
        return {"stored": bool(ok), "project": r.get("project", project),
                "agent": agent, "patterns": r.get("patterns", 0),
                "mistakes": r.get("mistakes", 0), "decisions": r.get("decisions", 0),
                "session_id": sid}
    finally:
        m.release_lock()
