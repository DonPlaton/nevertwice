#!/usr/bin/env python3
"""Active memory, axis C — causal / counterfactual memory.

Retrieval answers "what do I know about X" by dumping every note that mentions X. A memory that
*understands* X answers a different, harder question in one line:

    "What breaks if I change / touch X?"

This module induces a compact **causal model** from the episodes already in the store — the
typed relation edges lessons carry (`causes`, `caused-by`, `depends-on`, `requires`, `enables`,
`prevents`, `part-of`, `uses`, …) plus the failure modes that mistakes attach to an entity —
orients every edge into a single *impact* direction (cause → effect), and traverses it to answer
counterfactuals. The output is a short synthesized consequence list with evidence stems, NOT the
underlying notes: the token win is answering the question instead of returning the library. See
`research/ACTIVE_MEMORY.md`.

    python -m nevertwice.causal breaks prism-orchestrator
    python -m nevertwice.causal why nan-gradients --project p

Built on the existing relation graph (`memory_hook.relation_graph`) and entity index, so it adds
no store and no dependency; stdlib-only, no network on the query path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Orient each relation into a single impact direction (cause → effect), so "what does changing
# X impact" is one downstream traversal. FORWARD: edge (src --rel--> tgt) means src impacts tgt.
# REVERSE: it means tgt impacts src (e.g. "A depends-on B" ⇒ changing B breaks A ⇒ B→A). The
# rest (fixes/alternative-to/…) are not breakage-causal and are excluded from the impact graph.
_FORWARD = {"causes", "enables", "prevents", "implements", "improves", "enforces",
            "standardizes", "exposes"}
_REVERSE = {"caused-by", "depends-on", "requires", "uses", "part-of"}
_IMPACT_RELS = _FORWARD | _REVERSE


def build_impact_graph(project=None) -> dict:
    """Induce the causal impact graph: `{cause: [{effect, via, notes}]}`, where an edge means
    'changing `cause` may impact `effect`'. Oriented from the store's typed relation edges. A
    compact artifact built from `relation_graph` — no note bodies, just the causal skeleton."""
    rg = m.relation_graph(m.slug_project(project) if project else None, top=10_000)
    impact: dict = {}
    for src, edges in rg.items():
        for e in edges:
            rel, tgt, notes = e.get("rel"), e.get("target"), e.get("notes", 1)
            if rel in _FORWARD:
                cause, effect = src, tgt
            elif rel in _REVERSE:
                cause, effect = tgt, src
            else:
                continue
            if not cause or not effect or cause == effect:
                continue
            impact.setdefault(cause, []).append({"effect": effect, "via": rel, "notes": notes})
    return impact


def _failure_modes(entity, project=None, k=6) -> list[dict]:
    """The mistakes that tag `entity` — the known ways touching it has gone wrong. `{title,
    prevention, stem}`, most-recurrent first. This is the other half of the causal picture:
    not just what depends on X, but how X itself tends to fail."""
    try:
        notes = m.notes_for_entity(entity, project, k * 3)
    except Exception:
        return []
    mis = [n for n in notes if n.get("ntype") == "mistake"]
    mis.sort(key=lambda n: -n.get("recurrence", 1))
    return [{"title": n.get("title", ""), "prevention": n.get("prevention", ""),
             "stem": n.get("stem", "")} for n in mis[:k]]


def what_breaks(entity, project=None, *, depth=2, max_effects=8, impact=None) -> dict:
    """Counterfactual: what may break if you change / touch `entity`. Traverses the impact graph
    downstream (depth-limited, cycle-safe) and collects `entity`'s own failure modes. Returns
    `{entity, impacts:[{effect, via, hops, notes}], failure_modes:[...], evidence:[stems]}` —
    a synthesized consequence set, ranked by proximity×weight, deduped. One answer, not a dump."""
    impact = build_impact_graph(project) if impact is None else impact
    seen = {entity}
    frontier = [(entity, 0)]
    found: dict = {}
    while frontier:
        node, d = frontier.pop(0)
        if d >= depth:
            continue
        for e in impact.get(node, []):
            eff = e["effect"]
            if eff == entity:                                 # the thing being changed is not its own impact
                continue
            score = e["notes"] / (d + 1)                      # closer + better-attested = higher
            if eff not in found or score > found[eff]["score"]:
                found[eff] = {"effect": eff, "via": e["via"], "hops": d + 1,
                              "notes": e["notes"], "score": score}
            if eff not in seen:
                seen.add(eff)
                frontier.append((eff, d + 1))
    impacts = sorted(found.values(), key=lambda x: -x["score"])[:max_effects]
    for it in impacts:
        it.pop("score", None)
    fails = _failure_modes(entity, project)
    evidence = [f["stem"] for f in fails if f["stem"]]
    return {"entity": entity, "impacts": impacts, "failure_modes": fails, "evidence": evidence}


def why(entity, project=None, *, depth=2, max_causes=8) -> dict:
    """The upstream counterfactual: what causes / underlies `entity` (traverse the impact graph
    in reverse). `{entity, causes:[{cause, via, hops}]}`."""
    impact = build_impact_graph(project)
    reverse: dict = {}
    for cause, edges in impact.items():
        for e in edges:
            reverse.setdefault(e["effect"], []).append({"effect": cause, "via": e["via"],
                                                         "notes": e["notes"]})
    r = what_breaks(entity, project, depth=depth, max_effects=max_causes,
                    impact=reverse)
    return {"entity": entity, "causes": r["impacts"]}


def counterfactual(entity, project=None) -> str:
    """A one-paragraph synthesized answer to 'what happens if I change `entity`' — the
    token-economical output: top consequences + failure modes in a few lines, with evidence
    stems, instead of the underlying notes. '' if the entity has no causal footprint."""
    wb = what_breaks(entity, project)
    if not wb["impacts"] and not wb["failure_modes"]:
        return ""
    lines = [f"Changing `{entity}` may impact:"]
    if wb["impacts"]:
        for it in wb["impacts"]:
            hop = "" if it["hops"] == 1 else f", {it['hops']} hops"
            lines.append(f"  → {it['effect']}  (via {it['via']}{hop})")
    else:
        lines.append("  → (no downstream dependents recorded)")
    if wb["failure_modes"]:
        lines.append(f"Known failure modes when touching `{entity}`:")
        for f in wb["failure_modes"]:
            tail = f" — {f['prevention'][:100]}" if f["prevention"] else ""
            lines.append(f"  • {f['title']}{tail}")
    if wb["evidence"]:
        lines.append(f"  [evidence: {', '.join(wb['evidence'][:4])}]")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: causal breaks <entity> [--project P]  |  why <entity> [--project P]")
        return
    cmd = argv[0]
    ent = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""
    project = m.argval(argv, "project")
    if not ent:
        print("provide an entity")
        return
    if cmd == "breaks":
        out = counterfactual(ent, project)
        print(out or f"no causal footprint recorded for `{ent}`.")
    elif cmd == "why":
        r = why(ent, project)
        if not r["causes"]:
            print(f"no upstream causes recorded for `{ent}`.")
        else:
            print(f"`{ent}` is caused / underpinned by:")
            for c in r["causes"]:
                print(f"  ← {c['effect']}  (via {c['via']})")
    else:
        print(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
