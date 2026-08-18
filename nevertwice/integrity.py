#!/usr/bin/env python3
"""Graph laws - the memory checks that what it believes is internally possible.

Referential integrity ("does this link resolve?") is the weakest property a knowledge graph
can have. Nevertwice's graph is TYPED: lessons carry `relations: [{rel, target}]` whose types
(`causes`, `caused-by`, `depends-on`, `requires`, `part-of`, `fixes`, ...) are not decoration
- `causal.py` orients them into an impact graph and traverses it to answer "what breaks if I
touch X". That machinery presumes an algebra the store was never checked against:

    LAW 1  referential   every relation target names an entity the store actually knows
    LAW 2  vocabulary    every `rel` is a type some part of the system defines
    LAW 3  acyclicity    the causal orientation is a strict partial order - no cycles
    LAW 4  converse      a pair is never asserted in both directions at once
                         (A causes B AND A caused-by B is a contradiction, not a nuance)
    LAW 5  linkage       [[wikilinks]] in note bodies resolve to a note in the store

A violation is not cosmetic. A causal CYCLE makes `what_breaks` answer differently depending
on traversal order - the memory confidently reports consequences that its own model
contradicts. A dangling target makes `relation_expand` silently return nothing, so a bug
query never surfaces its fix and the failure looks like "no lesson recorded". Both are
invisible today: nothing reads the graph as a whole and asks whether it holds together.

Beyond per-edge laws it reports VOCABULARY COHERENCE - the drift between the relation types
the store PRODUCES and the ones the code CONSUMES:

    causally-inert    a type the store writes that the impact graph ignores  (write-only)
    unproduced        a type the impact graph expects that nothing writes    (dead branch)

This is the system-level check that catches a class of bug no per-note validation can:
extraction and reasoning drifting apart while every individual note stays "valid".

Read-only by design: it reports and exits, never edits. Findings are suggestions for a human
or an agent, in the same spirit as `guards` (warn, don't block) and `consolidate` (dry-run by
default). Pure and stdlib-only - the check functions take note metadata and return findings,
so they run in a test with no vault, no embedder, no database.

    python -m nevertwice.integrity                  # human report over the whole store
    python -m nevertwice.integrity --project prism  # one project
    python -m nevertwice.integrity --json           # machine-readable
    python -m nevertwice.integrity --strict         # exit 1 on any error-severity finding (CI gate)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_MH = None


def _m():
    """memory_hook, imported lazily so this module and memory_hook import cleanly in either
    order (same contract graph.py keeps)."""
    global _MH
    if _MH is None:
        try:
            from . import memory_hook as mh
        except ImportError:
            import memory_hook as mh
        _MH = mh
    return _MH


# ── The vocabulary, gathered from every place that defines one ────────────────
# Producers: the extraction prompt asks for these types (memory_hook._extract_prompt).
PRODUCED_VOCAB = frozenset({
    "causes", "caused-by", "fixes", "fixed-by", "depends-on", "requires",
    "part-of", "alternative-to", "related-to",
})

# Consumers: causal.py orients exactly these into the impact graph. Read FROM causal.py -
# the single source - so the two can never drift (review 2026-08: the first version mirrored
# the sets by hand, i.e. reintroduced in miniature the producer/consumer drift this module
# exists to catch). Literal fallback only for a pathological deployment missing causal.py.
def _causal_orientation():
    try:
        try:
            from . import causal as cz
        except ImportError:
            import causal as cz
        return frozenset(cz._FORWARD), frozenset(cz._REVERSE)
    except Exception:
        return (frozenset({"causes", "enables", "prevents", "implements", "improves",
                           "enforces", "standardizes", "exposes", "fixes"}),
                frozenset({"caused-by", "depends-on", "requires", "uses", "part-of",
                           "fixed-by"}))


CAUSAL_FORWARD, CAUSAL_REVERSE = _causal_orientation()
CONSUMED_VOCAB = CAUSAL_FORWARD | CAUSAL_REVERSE

# Brain-layer profiles contribute their own hint vocabularies (config.RELATION_HINTS). Read
# through config so enabling a profile cannot make its own relations look "unknown".
def _profile_vocab() -> frozenset:
    try:
        try:
            from . import config as cfg
        except ImportError:
            import config as cfg
        return frozenset(r for rels in cfg.RELATION_HINTS.values() for r in rels)
    except Exception:
        return frozenset()


def known_vocab() -> frozenset:
    """Every relation type any part of the system defines - the union a note's `rel` is
    checked against. Anything outside it is a typo or a hallucinated type: `_norm_relations`
    normalises the token but validates nothing, so 'fixed_by' and 'fixesd' reach the store
    intact and fragment the graph (`related_by(rel='fixed-by')` silently misses them)."""
    return PRODUCED_VOCAB | CONSUMED_VOCAB | _profile_vocab()


# Converse pairs - asserting both about the SAME ordered pair is a contradiction (LAW 4).
CONVERSE = {"causes": "caused-by", "caused-by": "causes",
            "fixes": "fixed-by", "fixed-by": "fixes"}

# Relations that impose a strict partial order; a cycle among them is a logical impossibility
# (LAW 3). `related-to` / `alternative-to` are symmetric by nature and excluded.
ORDERING = CONSUMED_VOCAB


def _finding(law, severity, subject, detail, stem="") -> dict:
    return {"law": law, "severity": severity, "subject": subject, "detail": detail, "stem": stem}


# ── LAW 1-2-4: per-edge checks ────────────────────────────────────────────────

def check_edges(notes, entity_universe: set | None = None) -> list[dict]:
    """Referential integrity, vocabulary conformance and converse contradictions, in one pass
    over the notes' typed edges.

    `entity_universe` is the resolution set for LAW 1 - pass the STORE-WIDE entity set when
    `notes` is a project slice, or cross-project targets get flagged as dangling and inflate
    dangling_rate: the same store would yield different truths per invocation (review
    2026-08). Defaults to the notes' own entities, which is right for whole-store runs and
    self-contained fixtures."""
    known = known_vocab()
    entities = entity_universe if entity_universe is not None else {
        e for n in notes for e in (n.get("entities") or [])}
    out, seen_pairs = [], {}
    for n in notes:
        stem = n.get("stem", "")
        for edge in n.get("relations") or []:
            rel, tgt = edge.get("rel"), edge.get("target")
            if not rel or not tgt:
                continue
            if rel not in known:
                out.append(_finding(
                    "vocabulary", "error", rel,
                    f"unknown relation type `{rel}` (target `{tgt}`) - not defined by extraction, "
                    f"the causal model, or any profile; it will never be traversed", stem))
            if tgt not in entities:
                # `warn`, not `error`: measured on a real 3.5k-note store this is ENDEMIC
                # (see `dangling_rate` in stats) because extraction asks for a target "in the
                # same style" as an entity but never requires the target to also BE tagged as
                # one. That makes it a design finding to fix once - in the prompt, or by
                # letting relation_expand fall back to note titles - not per-note negligence,
                # and it must not jam --strict permanently red.
                out.append(_finding(
                    "referential", "warn", tgt,
                    f"`{rel} -> {tgt}` points at an entity no note defines - relation_expand "
                    f"cannot follow it, so this edge is unreachable", stem))
            # LAW 4: the same ordered pair asserted with a relation AND its converse.
            for src in (n.get("entities") or []):
                if src == tgt:
                    continue
                key = (src, tgt)
                prior = seen_pairs.setdefault(key, {})
                prior.setdefault(rel, stem)
                conv = CONVERSE.get(rel)
                if conv and conv in prior:
                    out.append(_finding(
                        "converse", "error", f"{src} <-> {tgt}",
                        f"`{src} {rel} {tgt}` contradicts `{src} {conv} {tgt}` "
                        f"(asserted in {prior[conv]}) - both directions cannot hold", stem))
    return out


# ── LAW 3: acyclicity of the causal orientation ───────────────────────────────

def impact_edges(notes) -> dict:
    """`{cause: {effect: {rels}}}` - the same orientation causal.build_impact_graph applies
    (FORWARD keeps src->tgt, REVERSE flips it), rebuilt from note metadata so the law can be
    checked on a fixture with no vault behind it. Edge rels are kept so cycle findings can
    be classified by relation family."""
    g: dict = {}
    for n in notes:
        ents = n.get("entities") or []
        for edge in n.get("relations") or []:
            rel, tgt = edge.get("rel"), edge.get("target")
            if not rel or not tgt or rel not in ORDERING:
                continue
            for src in ents:
                if src == tgt:
                    continue
                cause, effect = (src, tgt) if rel in CAUSAL_FORWARD else (tgt, src)
                g.setdefault(cause, {}).setdefault(effect, set()).add(rel)
    return g


CYCLE_CAP = 20


def _sccs(graph: dict) -> list[list]:
    """Nontrivial (len>1) strongly-connected components of `{u: {v: rels}}` - iterative
    Tarjan, O(V+E) always. Each nontrivial SCC contains at least one cycle, and every cycle
    lives inside exactly one SCC, so counting SCCs counts the store's independent cyclic
    regions EXACTLY, at linear cost.

    This replaced simple-path DFS enumeration (review 2026-08, measured): a dense but
    ACYCLIC 61-node diamond lattice took ~12s and quadrupled every 2 layers - the cycle cap
    never fired because there was nothing to find, so `--strict` hung CI on precisely the
    healthy stores it certifies. Self-loops cannot occur (impact_edges drops src==tgt)."""
    index: dict = {}
    low: dict = {}
    onstack: set = set()
    stack: list = []
    out: list = []
    counter = [0]
    for root in sorted(graph):
        if root in index:
            continue
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        onstack.add(root)
        work = [(root, iter(sorted(graph.get(root, ()))))]
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    onstack.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in onstack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=lambda c: c[0])


def _example_cycle(graph: dict, comp: list) -> list:
    """One concrete shortest cycle through comp[0] - BFS inside the component. The
    human-readable evidence attached to each cyclic-region finding."""
    from collections import deque
    comp_set = set(comp)
    start = comp[0]
    parent: dict = {start: None}
    q = deque([start])
    while q:
        u = q.popleft()
        for v in sorted(graph.get(u, ())):
            if v == start:
                path = [u]
                while parent[path[-1]] is not None:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            if v in comp_set and v not in parent:
                parent[v] = u
                q.append(v)
    return comp   # unreachable for a true SCC; degrade to listing the region


def find_cycles(graph: dict, cap: int = CYCLE_CAP) -> list[list]:
    """One example cycle per cyclic region (SCC), capped. O(V+E) regardless of density."""
    return [_example_cycle(graph, c) for c in _sccs(graph)[:cap]]


# Relation family split for cycle severity: a cycle mixing a fix-edge with a cause-edge can
# encode a CONSISTENT note pair ("retry-logic exists because of flaky-api AND fixes it":
# caused-by orients flaky-api -> retry-logic, fixes orients retry-logic -> flaky-api), so it
# is a warn, not an error (review 2026-08). A single-family cycle stays an error: pure-cause
# cycles are genuine contradictions, and a pure fixes/fixed-by 2-cycle is the converse
# contradiction LAW 4 describes.
FIX_FAMILY = frozenset({"fixes", "fixed-by"})


def check_acyclicity(notes) -> list[dict]:
    """LAW 3, per cyclic REGION: one finding per nontrivial SCC, so the totals entry is the
    exact number of independent cyclic regions - never capped, never inflated by rotations
    (review 2026-08: the old per-cycle enumeration silently capped totals at 20, the exact
    silent-truncation failure this module documents itself as existing to catch).

    Scope note: this audits the FULL edge set. causal.what_breaks traverses a top-8-edges-
    per-entity view (graph.relation_graph's cap), so a flagged region can lie beyond what
    the deployed traversal reaches - it is still a contradiction in the data."""
    g = impact_edges(notes)
    out = []
    for comp in _sccs(g):
        cyc = _example_cycle(g, comp)
        arrow = " -> ".join(cyc + [cyc[0]])
        comp_set = set(comp)
        rels = {r for u in comp for v, rr in g.get(u, {}).items()
                if v in comp_set for r in rr}
        mixed = bool(rels & FIX_FAMILY) and bool(rels - FIX_FAMILY)
        region = f" (cyclic region of {len(comp)} entities)" if len(comp) > len(cyc) else ""
        detail = f"causal cycle {arrow}{region} - the causal orientation contradicts itself here"
        if mixed:
            detail += (" [mixed cause/fix cycle - may encode a consistent 'exists-because-of"
                       " and fixes' pair; review the edges rather than deleting on sight]")
        out.append(_finding("acyclicity", "warn" if mixed else "error", cyc[0], detail))
    return out


# ── LAW 5: wikilink resolution ────────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|#]+)")


def check_wikilinks(bodies: dict, resolvable: set) -> list[dict]:
    """LAW 5. `bodies` maps stem -> note text, `resolvable` is every stem the store can
    resolve. Archived and superseded notes stay in the vault and Obsidian still resolves
    them, so they belong in `resolvable`: a link into the archive is history, not rot."""
    # Case-insensitive, path-tolerant resolution (review 2026-08): Obsidian resolves
    # [[Note]] to note.md on NTFS/APFS, and a [[dir/note]] path link by its basename -
    # exact-match comparison reported both as broken.
    res = {s.strip().lower() for s in resolvable}
    out = []
    for stem, text in bodies.items():
        for raw in _WIKILINK_RE.findall(text or ""):
            target = raw.strip()
            key = target.split("/")[-1].strip().lower()
            if target and key and key not in res:
                out.append(_finding(
                    "linkage", "warn", target,
                    f"[[{target}]] resolves to nothing in the store", stem))
    return out


# ── Vocabulary coherence (system level) ───────────────────────────────────────

def vocabulary_report(notes) -> dict:
    """Producer/consumer drift. Per-note validation cannot see this: every note is individually
    well-formed while extraction and the causal model quietly stop agreeing on what an edge
    MEANS. `causally_inert` types are written and then ignored by the impact graph;
    `unproduced` types the impact graph is built to traverse but nothing ever writes."""
    used: dict = {}
    for n in notes:
        for edge in n.get("relations") or []:
            rel = edge.get("rel")
            if rel:
                used[rel] = used.get(rel, 0) + 1
    inert = sorted((r, c) for r, c in used.items() if r not in CONSUMED_VOCAB)
    unproduced = sorted(CONSUMED_VOCAB - set(used))
    return {"in_use": dict(sorted(used.items(), key=lambda kv: (-kv[1], kv[0]))),
            "causally_inert": [{"rel": r, "edges": c} for r, c in inert],
            "unproduced": unproduced}


# ── Whole-store check ─────────────────────────────────────────────────────────

FINDING_CAP = 50


def check(notes, bodies: dict | None = None, resolvable: set | None = None,
          cap: int = FINDING_CAP, entity_universe: set | None = None) -> dict:
    """Run every law over `notes` (a list of note metadata dicts). `bodies`/`resolvable` are
    optional - omit them and LAW 5 is skipped, which is what a graph-only fixture wants.
    `entity_universe` widens LAW 1 resolution beyond the notes' own entities (check_edges).

    Findings are capped per law so one endemic violation cannot bury the rare, sharp ones -
    but `totals` always reports the TRUE count, because a checker that silently truncates its
    own output is the exact failure mode it exists to catch."""
    findings = check_edges(notes, entity_universe) + check_acyclicity(notes)
    if bodies:
        findings += check_wikilinks(bodies, resolvable or set())
    totals, shown, kept = {}, {}, []
    for f in findings:
        law = f["law"]
        totals[law] = totals.get(law, 0) + 1
        if shown.get(law, 0) < cap:
            shown[law] = shown.get(law, 0) + 1
            kept.append(f)
    edges = sum(len(n.get("relations") or []) for n in notes)
    entities = {e for n in notes for e in (n.get("entities") or [])}
    dangling = totals.get("referential", 0)
    return {
        "stats": {"notes": len(notes), "entities": len(entities), "edges": edges,
                  "findings": len(findings),
                  "errors": sum(1 for f in findings if f["severity"] == "error"),
                  # The headline health number: what share of the typed edges the graph
                  # cannot actually traverse. A high rate means the knowledge graph is
                  # mostly decorative, however well-formed each note looks on its own.
                  "dangling_rate": round(dangling / edges, 3) if edges else 0.0,
                  # SCC-based counting is exact, so nothing is ever silently capped; the key
                  # stays for report-shape compatibility (display capping shows via totals).
                  "cycles_capped": False},
        "totals": totals,
        "findings": kept,
        "vocabulary": vocabulary_report(notes),
    }


def check_store(project: str | None = None, links: bool = True) -> dict:
    """`check` against the real store. Reads note metadata through memory_hook's iterators
    (the same source graph.py uses) and, for LAW 5, the note bodies.

    A --project run still resolves LAW 1 targets against the WHOLE store's entities: a
    cross-project edge is reachable in reality, so scoping the resolution set would make the
    same store yield different truths per invocation (review 2026-08)."""
    mh = _m()
    notes = mh._iter_project_notes(mh.slug_project(project)) if project else mh._iter_all_notes()
    universe = None
    if project:
        universe = {e for n in mh._iter_all_notes() for e in (n.get("entities") or [])}
    bodies, resolvable = {}, set()
    if links:
        # EVERY markdown file in the store is a resolvable target, not just the typed-note
        # folders: notes legitimately link to `[[<project>]]` Context cards and to
        # `[[...-session-...]]` transcripts, and Archive/ and Superseded/ notes stay resolvable
        # in Obsidian even though live recall skips them. Scoping this to TYPE_FOLDER reported
        # 7103 "broken" links on the live store, every one of them a false positive.
        for p in mh.VAULT.rglob("*.md"):
            resolvable.add(p.stem)
        for n in notes:
            p = mh.VAULT / mh.TYPE_FOLDER.get(n.get("ntype"), "") / f"{n.get('stem')}.md"
            try:
                bodies[n.get("stem", "")] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return check(notes, bodies, resolvable, entity_universe=universe)


# ── Rendering + CLI ───────────────────────────────────────────────────────────

_ICON = {"error": "[!]", "warn": "[~]", "info": "[i]"}


def render(report: dict) -> str:
    s, v = report["stats"], report["vocabulary"]
    totals = report.get("totals", {})
    lines = [f"graph laws: {s['notes']} notes, {s['entities']} entities, {s['edges']} edges",
             f"  unreachable edges: {s['dangling_rate']:.0%} "
             f"({totals.get('referential', 0)}/{s['edges']} point at an entity no note defines)",
             ""]
    if not report["findings"]:
        lines.append("  all laws hold - no dangling targets, unknown types, cycles or "
                     "contradictions.")
    else:
        by_law: dict = {}
        for f in report["findings"]:
            by_law.setdefault(f["law"], []).append(f)
        for law in sorted(by_law):
            items = by_law[law]
            total = totals.get(law, len(items))
            # ("cycle search capped" note removed: cycles_capped is hardcoded False -
            # SCC counting is exact - so the branch could never fire; review 2026-08 G8)
            lines.append(f"  {law} - {total}:")
            for f in items[:12]:
                where = f" [{f['stem']}]" if f["stem"] else ""
                lines.append(f"    {_ICON.get(f['severity'], '[?]')} {f['detail']}{where}")
            if total > 12:
                lines.append(f"    ... {total - 12} more")
            lines.append("")
    if v["causally_inert"] or v["unproduced"]:
        lines.append("  vocabulary drift (producer vs consumer):")
        for it in v["causally_inert"]:
            lines.append(f"    [~] `{it['rel']}` x{it['edges']} written, but the causal model "
                         f"ignores it - these edges never reach what_breaks/why")
        if v["unproduced"]:
            lines.append(f"    [~] the causal model traverses {', '.join(v['unproduced'])} - "
                         f"no note in the store declares any of them")
    return "\n".join(lines)


def main():
    # UTF-8 stdout for BOTH branches, before any print: the --json path on a cp1251 pipe
    # (this codebase documents the hazard for hook stdout) otherwise dies with
    # UnicodeEncodeError on the first non-ASCII finding detail (review 2026-08).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("usage: integrity [--project P] [--json] [--strict] [--no-links]")
        return
    mh = _m()
    project = mh.argval(argv, "project")
    report = check_store(project, links="--no-links" not in argv)
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render(report))
    if "--strict" in argv and report["stats"]["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
