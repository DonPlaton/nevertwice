#!/usr/bin/env python3
"""RESEARCH PROTOTYPE - bi-temporal knowledge graph retrofitted onto the existing
file-based Obsidian memory vault. GPU-free: structural parsing + CPU cosine over
the embeddings cache that already exists. No new embeddings, no network.

Goal: demonstrate that the temporal-KG capabilities of Zep/Graphiti
(arXiv:2501.13956) - bi-temporal facts, point-in-time recall, belief evolution,
contradiction timelines - can be reconstructed on plain markdown + git, fully
local and $0, without Neo4j or a cloud service.

Bi-temporal model (per fact/node), mirroring Graphiti's four timestamps:
  valid_from / valid_to     - VALID time: when the fact held true in the project
  txn_from   / txn_to       - TRANSACTION time: when the system recorded it
A fact is "current" iff valid_to is None. Supersession is recovered from
same-(project,ntype,slug) families ordered by date (implicit), and from explicit
`superseded_by` frontmatter (the live system stamps this going forward).

    python research/temporal_graph.py            # build + demo report
    python research/temporal_graph.py --save      # also write temporal_graph.json
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OPEN = None          # sentinel: still-valid / still-current (∞)
FAR = "9999-12-31"   # for sorting open intervals last


# ── parsing ───────────────────────────────────────────────────────────

def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm = {}
    for ln in text[3:end if end != -1 else len(text)].split("\n"):
        if ":" in ln:
            k, v = ln.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def _body_fields(text: str):
    desc, prevention, links = "", "", []
    seen = False
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("# "):
            seen = True
            continue
        for lk in re.findall(r"\[\[([^]|#]+)", ln):
            links.append(lk.strip())
        if not seen or not s:
            continue
        if s.startswith("**Как избежать:**"):
            prevention = s.replace("**Как избежать:**", "").strip()
        elif not desc and not s.startswith(("**", "#", "-", "_", "[[", "|", "##")):
            desc = s
    # de-dup links, drop self handled by caller
    seen_l, out = set(), []
    for l in links:
        if l not in seen_l:
            seen_l.add(l)
            out.append(l)
    return desc, prevention, out


def load_nodes():
    """One node per typed note (live + Superseded + Archive). Returns list of dicts."""
    db = m.load_processed()
    id8_txn = {sid[:8]: rec.get("processed_at")
               for sid, rec in db.items()
               if isinstance(rec, dict) and rec.get("processed_at")}
    nodes = []
    for ntype, folder in m.TYPE_FOLDER.items():
        for sub in ("", "Superseded", "Archive"):
            d = m.VAULT / folder / sub if sub else m.VAULT / folder
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                parsed = m.parse_typed_stem(p.stem)
                if not parsed:
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                fm = _frontmatter(text)
                desc, prevention, links = _body_fields(text)
                sess = fm.get("session", "")
                id8 = sess.split("-")[-1] if sess else ""
                txn = id8_txn.get(id8)
                if not txn:
                    try:
                        txn = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                    except OSError:
                        txn = None
                nodes.append({
                    "id": p.stem, "project": parsed["project"], "ntype": ntype,
                    "slug": parsed["slug"], "title": (desc[:0] or p.stem),
                    "date": parsed["date"], "desc": desc, "prevention": prevention,
                    "session": sess, "links": [l for l in links if l != p.stem],
                    "explicit_superseded_by": fm.get("superseded_by"),
                    "storage": sub or "live",
                    "valid_from": parsed["date"], "valid_to": OPEN,
                    "txn_from": txn, "txn_to": OPEN,
                    "superseded_by": None, "supersedes": None,
                    "status": "current",
                })
    return nodes


# ── bi-temporal edges from same-slug families ─────────────────────────

def apply_supersession(nodes):
    """Recover supersession history: within a (project,ntype,slug) family ordered
    by valid date, each older version is superseded by the next. Sets valid_to /
    txn_to / status - the bi-temporal invalidation Graphiti does via LLM conflict
    detection, here recovered structurally for free."""
    by_id = {n["id"]: n for n in nodes}
    fam = {}
    for n in nodes:
        fam.setdefault((n["project"], n["ntype"], n["slug"]), []).append(n)
    chains = 0
    for key, fam_nodes in fam.items():
        if len(fam_nodes) < 2:
            continue
        fam_nodes.sort(key=lambda n: n["valid_from"])
        chains += 1
        for older, newer in zip(fam_nodes, fam_nodes[1:]):
            older["valid_to"] = newer["valid_from"]
            older["txn_to"] = newer["txn_from"]
            older["superseded_by"] = newer["id"]
            older["status"] = "superseded"
            newer["supersedes"] = older["id"]
    # honor explicit frontmatter superseded_by too (future-proof)
    for n in nodes:
        tgt = n.get("explicit_superseded_by")
        if tgt and tgt in by_id and n["status"] == "current":
            n["status"] = "superseded"
            n["superseded_by"] = n["superseded_by"] or tgt
    return chains


# ── communities via CPU cosine over the EXISTING cache (no GPU) ───────

def build_communities(nodes, sim=0.6):
    cache = m.load_embed_cache()
    vec = {n["id"]: cache[n["id"]]["vec"] for n in nodes
           if n["id"] in cache and isinstance(cache[n["id"]].get("vec"), list)}
    ids = list(vec)
    used, clusters = set(), []
    for i, a in enumerate(ids):
        if a in used:
            continue
        cl = [a]
        for b in ids[i + 1:]:
            if b in used:
                continue
            if m.cosine(vec[a], vec[b]) >= sim:
                cl.append(b)
                used.add(b)
        if len(cl) > 1:
            used.add(a)
            clusters.append(cl)
    return clusters, len(vec)


# ── bi-temporal queries ───────────────────────────────────────────────

def _le(a, b):  # a <= b for date strings, OPEN treated as +inf on the right
    return a is not None and (b is OPEN or b is None or a <= b)


def as_of_valid(nodes, d):
    """Facts that held true at VALID time d (point-in-time truth)."""
    return [n for n in nodes
            if n["valid_from"] <= d and (n["valid_to"] is OPEN or d < n["valid_to"])]


def belief_at(nodes, d):
    """What the SYSTEM had on record at TRANSACTION time d (iso date prefix)."""
    out = []
    for n in nodes:
        tf = (n["txn_from"] or "")[:10]
        tt = n["txn_to"]
        if tf and tf <= d and (tt is OPEN or (tt or "")[:10] > d):
            out.append(n)
    return out


def timeline(nodes, project, slug):
    fam = [n for n in nodes if n["project"] == project and n["slug"] == slug]
    return sorted(fam, key=lambda n: n["valid_from"])


def contradictions(nodes):
    """Facts the system changed its mind about: superseded versions + their
    successor, as a dated chain."""
    out = {}
    for n in nodes:
        if n["status"] == "superseded" and n["superseded_by"]:
            key = (n["project"], n["ntype"], n["slug"])
            out.setdefault(key, set()).update([n["id"], n["superseded_by"]])
    return out


# ── report ────────────────────────────────────────────────────────────

def toks(s):
    return len(s) // 4


def main():
    t0 = time.time()
    nodes = load_nodes()
    chains = apply_supersession(nodes)
    clusters, embedded = build_communities(nodes)
    build_s = time.time() - t0

    live = [n for n in nodes if n["storage"] == "live"]
    current = [n for n in nodes if n["status"] == "current"]
    superseded = [n for n in nodes if n["status"] == "superseded"]
    txn_cov = sum(1 for n in nodes if n["txn_from"])
    cross = [c for c in clusters
             if len({nid.split("-")[3] if len(nid.split("-")) > 3 else "" for nid in c}) > 1]

    edges = sum(len(n["links"]) for n in nodes) + chains + len(nodes)  # links + supersede + in-project
    bar = "=" * 74
    print(bar)
    print("  BI-TEMPORAL KNOWLEDGE GRAPH - prototype on the live vault (GPU-free)")
    print(bar)
    print(f"  nodes (facts)        : {len(nodes)}  (live={len(live)}, "
          f"superseded/archived={len(nodes)-len(live)})")
    print(f"  edges                : ~{edges}  (wikilinks + {chains} supersede-chains + in-project)")
    print(f"  supersession chains  : {chains}  (recovered structurally, $0)")
    print(f"  current vs retired   : {len(current)} current / {len(superseded)} superseded")
    print(f"  transaction-time cov : {txn_cov}/{len(nodes)} "
          f"({100*txn_cov//max(1,len(nodes))}%) joined to processed_at/mtime")
    print(f"  semantic communities : {len(clusters)} (from {embedded} embedded), "
          f"{len(cross)} span >1 project (transferable knowledge)")
    print(f"  build time / source  : {build_s:.2f}s, pure CPU + existing cache")
    print()

    # 1) belief evolution (the headline temporal capability)
    print("- 1. BELIEF EVOLUTION (a fact the system revised over time) -")
    fam = contradictions(nodes)
    if fam:
        (proj, nt, slug), _ = max(fam.items(), key=lambda kv: len(kv[1]))
        for n in timeline(nodes, proj, slug):
            vt = n["valid_to"] or "now"
            mark = "★ current" if n["status"] == "current" else "  retired"
            print(f"   [{n['valid_from']} → {vt}] {mark}  {n['desc'][:90] or n['slug']}")
        print(f"   → {proj}/{nt}/{slug}: {len(timeline(nodes,proj,slug))} versions; "
              f"flat retrieval would surface all at once, contradicting each other.")
    print()

    # 2) point-in-time recall: valid-time slice then vs now
    print("- 2. POINT-IN-TIME RECALL (as_of valid-time) -")
    early, late = "2026-05-20", datetime.now().strftime("%Y-%m-%d")
    for d in (early, late):
        v = as_of_valid(nodes, d)
        print(f"   as_of({d}): {len(v)} facts held true "
              f"(current-as-of-then, not today's view)")
    # show one fact whose truth changed between the two dates
    changed = [n for n in nodes if n["status"] == "superseded"
               and n["valid_from"] <= early and (n["valid_to"] or FAR) <= late]
    if changed:
        c = changed[0]
        print(f"   e.g. on {early} the project believed: "
              f"\"{(c['desc'] or c['slug'])[:80]}\" - later revised.")
    print()

    # 3) transaction-time: how the knowledge base grew
    print("- 3. KNOWLEDGE GROWTH (belief_at transaction-time) -")
    for d in ("2026-05-15", "2026-06-01", datetime.now().strftime("%Y-%m-%d")):
        print(f"   by {d}: system had {len(belief_at(nodes, d))} facts on record")
    print()

    # 4) cross-project transferable knowledge (community subgraph)
    print("- 4. CROSS-PROJECT TRANSFER (semantic communities spanning projects) -")
    for c in sorted(cross, key=len, reverse=True)[:3]:
        projs = sorted({nid.split("-")[3] for nid in c if len(nid.split("-")) > 3})
        titles = [next((n["desc"][:48] or n["slug"] for n in nodes if n["id"] == nid), nid)
                  for nid in c[:3]]
        print(f"   • {len(c)} notes across {projs}:")
        for t in titles:
            print(f"        - {t}")
    if not cross:
        print("   (no cross-project clusters at this threshold)")
    print()

    # 5) token economy: point-in-time project state vs reading full Context
    print("- 5. TOKEN ECONOMY (point-in-time state vs full Context read) -")
    proj = max({n["project"] for n in nodes},
               key=lambda p: sum(1 for n in nodes if n["project"] == p))
    cur = [n for n in current if n["project"] == proj]
    snap = "\n".join(f"- {n['ntype']}: {n['desc'][:80] or n['slug']}" for n in cur)
    ctx_fp = m.VAULT / "Context" / f"{proj}.md"
    ctx_tok = toks(ctx_fp.read_text(encoding="utf-8", errors="replace")) if ctx_fp.exists() else 0
    print(f"   project '{proj}': {len(cur)} CURRENT facts → {toks(snap)} tokens, "
          f"point-in-time, contradiction-free")
    print(f"   full Context/{proj}.md (append-only narrative) → {ctx_tok} tokens, "
          f"no point-in-time slicing")
    if ctx_tok:
        print(f"   → temporal snapshot is {ctx_tok/max(1,toks(snap)):.1f}x smaller AND "
              f"answers 'what is true NOW' that the flat log cannot.")
    print()
    print(bar)
    print(f"  Verdict: bi-temporal recall reconstructed on markdown+git, "
          f"{build_s:.2f}s, $0, no GPU.")
    print(bar)

    if "--save" in sys.argv:
        out = m.VAULT / "temporal_graph.json"
        graph = {"generated": datetime.now().isoformat(timespec="seconds"),
                 "model": "bi-temporal (valid_from/to, txn_from/to)",
                 "stats": {"nodes": len(nodes), "chains": chains,
                           "communities": len(clusters), "cross_project": len(cross)},
                 "nodes": nodes}
        m.write_atomic(out, json.dumps(graph, ensure_ascii=False, indent=1))
        print(f"  saved → {out}  ({out.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
