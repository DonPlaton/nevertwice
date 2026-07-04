#!/usr/bin/env python3
"""RESEARCH — downstream consolidation eval (roadmap Phase 2): does REAL LLM principle-synthesis
close the 4A gap? (the decisive check before shipping consolidation).

4A (ABSTRACTIVE.md) showed, on synthetic latent vectors, that averaging K episodic instances of a
lesson recovers the latent rule better than any single instance — but flagged the load-bearing gap:
its "principle" is the *vector mean* of the cluster, an IDEALISATION. A production consolidation
instead has an LLM summarise the cluster's *text* into a principle and embeds THAT — a different
operator. This module measures the real operator on the live store, so the decision to ship
abstractive consolidation (Phase 3) rests on a measured downstream number, not the synthetic upper
bound.

METHOD (leave-one-out on real cross-session clusters). Within a project, notes whose cosine >= 0.55
cluster; a cluster of K>=3 spanning >1 date is a lesson re-encountered across sessions. For each
member held out as a SIMULATED NEW OCCURRENCE, synthesise a principle from the OTHER K-1 members'
text (the LLM operator Phase 3 would ship), embed it, and ask two questions:
  1. Mechanism: does cosine(held-out, principle) beat cosine(held-out, best single episode), and does
     it match the vector-mean idealisation (4A)? — i.e. is text-synthesis as good as the ideal mean?
  2. Downstream: in the FULL store, replacing the K-1 episodes with the 1 principle, is the held-out
     occurrence's right topic still retrieved at top-3 — at K-1:1 compression? (vs the episodic store,
     the production status quo, ~recall@3 0.71.)

PRIVACY. Aggregate only: cosines, recall@k, win-rates, compression, cost. Reads the LOCAL cache
(vectors + the title/desc/prevention text the embedder stored) in-process and synthesises text via a
LOCAL model; never prints, saves, or commits any note or principle text.

    NEVERTWICE_VAULT=/path python research/consolidation_eval.py --save
    NEVERTWICE_VAULT=/path NEVERTWICE_CONSOLIDATE_MODEL=qwen2.5:7b python research/consolidation_eval.py

Research dep: stdlib + memory_hook; calls Ollama (synthesis + embedding).
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
CLUSTER_THR = 0.55
MIN_K = 3                    # only clusters with >=3 members are consolidation candidates
K = 3                        # recall@K
TEXT_CHARS = 400            # per-note text budget into the synthesis prompt
MODEL = os.environ.get("NEVERTWICE_CONSOLIDATE_MODEL", m.OLLAMA_MODEL)
LIMIT = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--limit=")), None)

VEC: dict = {}
TEXT: dict = {}


def _date(stem):
    p = m.parse_typed_stem(stem)
    return p["date"] if p else "?"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _mean(vecs):
    d = len(vecs[0])
    out = [0.0] * d
    for v in vecs:
        for i in range(d):
            out[i] += v[i]
    return [x / len(vecs) for x in out]


def _clusters(notes, thr):
    used, groups = set(), []
    for i, (s1, r1) in enumerate(notes):
        if s1 in used:
            continue
        grp = [s1]
        for s2, r2 in notes[i + 1:]:
            if s2 not in used and m.cosine(r1["vec"], r2["vec"]) >= thr:
                grp.append(s2)
                used.add(s2)
        if len(grp) > 1:
            used.add(s1)
            groups.append(grp)
    return groups


def synthesise_principle(texts, stats):
    """The real consolidation operator (prototype for Phase 3): summarise K notes that recur across
    sessions into the single general PRINCIPLE they teach. Returns principle text, or "" on failure."""
    lines = ["These memory notes recurred across different sessions and concern the SAME underlying",
             "lesson. Write the single general PRINCIPLE they teach — the reusable takeaway, not the",
             "specifics of any one instance.", "", "NOTES:"]
    for i, t in enumerate(texts):
        lines.append(f"[{i}] {(t or '')[:TEXT_CHARS]}")
    lines.append("")
    lines.append('Return JSON {"title": "<short title>", "principle": "<1-3 sentences>"}.')
    prompt = "\n".join(lines)
    payload = json.dumps({"model": MODEL, "prompt": prompt, "format": "json", "stream": False,
                          "think": False, "keep_alive": "10m",
                          "options": {"temperature": 0.2, "num_ctx": 8192}}).encode("utf-8")
    import urllib.request
    stats["calls"] += 1
    stats["prompt_chars"] += len(prompt)
    req = urllib.request.Request(m.OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        obj = json.loads(m._strip_json_fence((data.get("response") or "").strip()))
        title, principle = obj.get("title", ""), obj.get("principle", "")
        return f"{title}\n{principle}".strip()
    except Exception as e:
        stats["errors"] += 1
        m.log(f"synthesis failed: {type(e).__name__}: {e}")
        return ""


def main():
    cache = m.load_embed_cache()
    notes = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)]
    bar = "=" * 78
    print(bar)
    print("  CONSOLIDATION DOWNSTREAM EVAL — does real LLM principle-synthesis preserve/beat raw")
    print("  episodes for retrieving a new occurrence? (closes the 4A vector-mean gap; aggregate-only)")
    print(bar)
    if len(notes) < 20:
        print(f"  only {len(notes)} embedded notes — set NEVERTWICE_VAULT to a real, populated store.")
        return
    by_proj = defaultdict(list)
    for s, r in notes:
        VEC[s] = r["vec"]
        TEXT[s] = f"{r.get('title','')}\n{r.get('desc','')}\n{r.get('prevention','')}".strip()
        by_proj[r.get("project")].append((s, r))
    all_stems = [s for s, _ in notes]

    # cross-session clusters with K>=3 members (the consolidation candidates)
    clusters = []
    for ns in by_proj.values():
        for g in _clusters(ns, CLUSTER_THR):
            if len(g) >= MIN_K and len({_date(x) for x in g}) > 1:
                clusters.append(g)
    if LIMIT:
        clusters = clusters[:LIMIT]
    n_notes_in = sum(len(g) for g in clusters)
    print(f"  {len(notes)} notes / {len(by_proj)} projects")
    print(f"  {len(clusters)} cross-session clusters (K>=3, >1 date), covering {n_notes_in} notes\n")
    if not clusters:
        print("  no K>=3 cross-session clusters — store too young/sparse for a consolidation eval.")
        return
    if not m.ollama_alive():
        print("  Ollama not reachable — needed for synthesis + embedding. Start it and retry.")
        return

    print(f"  synthesising principles (leave-one-out, model={MODEL}) ...")
    stats = {"calls": 0, "errors": 0, "prompt_chars": 0}
    t0 = time.time()
    # mechanism (cosine to the held-out new occurrence) and downstream (full-store recall@K)
    best_ep, mean_ep, prin = [], [], []          # cosine(held-out, best episode / vector-mean / principle)
    epi_hit = con_hit = q = 0
    for g in clusters:
        members = list(g)
        for qi in members:
            episodes = [s for s in members if s != qi]
            if not episodes:
                continue
            ep_vecs = [VEC[s] for s in episodes]
            ptext = synthesise_principle([TEXT[s] for s in episodes], stats)
            if not ptext:
                continue
            pvec = m.embed_text(ptext, kind=m.doc_embed_kind())
            if not pvec:
                continue
            q += 1
            qv = VEC[qi]
            best_ep.append(max(m.cosine(qv, ev) for ev in ep_vecs))
            mean_ep.append(m.cosine(qv, _unit(_mean(ep_vecs))))
            prin.append(m.cosine(qv, pvec))
            # full-store recall@K: relevant = the same topic. Episodic store: the K-1 episodes
            # are candidates (right = any of them). Consolidated store: episodes removed, principle
            # added (right = the principle). Distractors = every other note in the store.
            others = [s for s in all_stems if s != qi and s not in set(episodes)]
            epi_cands = {s: m.cosine(qv, VEC[s]) for s in (others + episodes)}
            epi_top = sorted(epi_cands, key=lambda s: -epi_cands[s])[:K]
            if set(epi_top) & set(episodes):
                epi_hit += 1
            con_scores = {s: m.cosine(qv, VEC[s]) for s in others}
            con_scores["<principle>"] = m.cosine(qv, pvec)
            con_top = sorted(con_scores, key=lambda s: -con_scores[s])[:K]
            if "<principle>" in con_top:
                con_hit += 1
    dt = time.time() - t0
    if not q:
        print("  no principles synthesised (model failures) — check Ollama/model.")
        return

    avg = lambda xs: sum(xs) / len(xs)
    win = sum(1 for p, b in zip(prin, best_ep) if p >= b) / q          # principle >= best episode
    ideal = sum(1 for p, me in zip(prin, mean_ep) if p >= me) / q      # synthesis >= vector-mean
    epi_r, con_r = epi_hit / q, con_hit / q
    print(f"\n— mechanism: cosine of the held-out new occurrence to ... ({q} leave-one-out queries) —")
    print(f"  best single episode (status quo):  {avg(best_ep):.3f}")
    print(f"  vector-mean of episodes (4A ideal): {avg(mean_ep):.3f}")
    print(f"  LLM-synthesised principle (real):   {avg(prin):.3f}")
    print(f"  principle >= best episode: {win:.0%} of queries   |   principle >= vector-mean: {ideal:.0%}")
    print(f"\n— downstream: full-store recall@{K} for the right topic (compression {n_notes_in}->{len(clusters)}) —")
    print(f"  episodic store (status quo):   {epi_r:.3f}")
    print(f"  consolidated store (principle):{con_r:.3f}   (Δ {con_r-epi_r:+.3f})")
    print(f"  cost: {stats['calls']} synthesis calls, {stats['errors']} errors, "
          f"~{stats['prompt_chars']//4} prompt tok, {dt:.0f}s")

    verdict_ship = con_r >= epi_r - 0.02 and avg(prin) >= avg(best_ep) - 0.02
    print()
    if verdict_ship:
        print(f"  → SHIP-SUPPORTED: real LLM synthesis retrieves the new occurrence about as well as the")
        print(f"    best raw episode AND preserves full-store recall, while compressing {n_notes_in}->{len(clusters)}.")
        print(f"    The 4A vector-mean result transfers to the real text operator — Phase 3 can ship it,")
        print(f"    archiving (not deleting) the episodes so instance detail stays one hop away.")
    else:
        print(f"  → SHIP-CAUTION: real synthesis underperforms raw episodes here (principle cosine")
        print(f"    {avg(prin):.3f} vs best-episode {avg(best_ep):.3f}; recall {con_r:.3f} vs {epi_r:.3f}).")
        print(f"    The vector-mean idealisation does NOT fully transfer to text synthesis on this store;")
        print(f"    Phase 3 should keep episodes retrievable (link principle->episodes, don't replace).")

    if SAVE:
        out = {"notes": len(notes), "clusters": len(clusters), "notes_in_clusters": n_notes_in,
               "loo_queries": q, "model": MODEL, "k": K,
               "cos_best_episode": avg(best_ep), "cos_vector_mean": avg(mean_ep),
               "cos_principle": avg(prin), "principle_ge_best_episode": win,
               "principle_ge_vector_mean": ideal,
               "recall_episodic": epi_r, "recall_consolidated": con_r,
               "synthesis_calls": stats["calls"], "errors": stats["errors"],
               "wall_s": round(dt, 1), "ship_supported": bool(verdict_ship)}
        p = HERE / "consolidation_eval.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved aggregate metrics -> {p}")
    print(bar)


if __name__ == "__main__":
    main()
