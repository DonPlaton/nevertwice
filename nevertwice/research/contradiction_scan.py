#!/usr/bin/env python3
"""RESEARCH (I-12) — detect cross-title semantic contradictions / redundancies
that same-slug supersession misses (the §6 gap of the temporal-graph work).

Two notes can describe the same thing with DIFFERENT titles — so the structural
supersession (same project+ntype+slug) and the 0.92 dedup never link them, yet
one may contradict or duplicate the other. This scans for that band.

Core is GPU-free and 100% local: CPU cosine over the EXISTING embedding cache.
Candidate band: same project, different slug, both currently-valid, cosine in
[NEAR, DEDUP) — "very related, differently titled".

Optional `--adjudicate` asks the cloud LLM (Cerebras, zero-retention, no GPU) to
classify each pair {redundant|contradict|distinct} + recommend an action. It is
SKIPPED for local-only projects (privacy routing is respected — nothing sensitive
leaves the machine).

    python research/contradiction_scan.py                 # local CPU scan
    python research/contradiction_scan.py --adjudicate     # + cloud classify (cloud-ok projects)
    python research/contradiction_scan.py --near 0.80
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import memory_hook as m
import research.temporal_graph as tg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ICON = {"mistake": "⚠️", "pattern": "✅", "decision": "🎯"}


def _arg(flag, default):
    for a in sys.argv:
        if a.startswith(flag + "="):
            try:
                return float(a.split("=", 1)[1])
            except ValueError:
                pass
    return default


def find_candidates(nodes, near, dedup):
    cache = m.load_embed_cache()
    live = [n for n in nodes
            if n["status"] == "current"
            and isinstance(cache.get(n["id"], {}).get("vec"), list)]
    by_proj = {}
    for n in live:
        by_proj.setdefault(n["project"], []).append(n)
    pairs = []
    for proj, ns in by_proj.items():
        for i, a in enumerate(ns):
            va = cache[a["id"]]["vec"]
            for b in ns[i + 1:]:
                if a["slug"] == b["slug"]:
                    continue  # same-slug = handled by supersession
                s = m.cosine(va, cache[b["id"]]["vec"])
                if near <= s < dedup:
                    pairs.append((s, a, b))
    pairs.sort(key=lambda x: -x[0])
    return pairs


ADJ_PROMPT = """Две заметки памяти агента из одного проекта. Классифицируй связь.
Верни ТОЛЬКО JSON: {{"relation": "redundant|contradict|distinct", "action": "<одна строка: что сделать>"}}.
  redundant — об одном и том же, дублируют (слить);
  contradict — утверждают разное/противоположное (нужна реконсиляция, какая верна);
  distinct — связаны, но это разные валидные факты (оставить оба).

A [{a_type}] {a_title}: {a_desc}
B [{b_type}] {b_title}: {b_desc}
"""


def adjudicate(pair):
    s, a, b = pair
    prompt = ADJ_PROMPT.format(
        a_type=a["ntype"], a_title=a["slug"], a_desc=(a["desc"] or "")[:300],
        b_type=b["ntype"], b_title=b["slug"], b_desc=(b["desc"] or "")[:300])
    # Force Cerebras directly (cloud, zero-retention, no GPU). generate_json would
    # route local-only projects to Ollama (GPU) — we skip those before calling.
    res = m.call_cerebras(prompt)
    rel = (res or {}).get("relation", "?")
    act = (res or {}).get("action", "")
    return rel, act


def main():
    near = _arg("--near", 0.82)
    dedup = _arg("--dedup", 0.92)
    do_adj = "--adjudicate" in sys.argv
    t0 = time.time()
    nodes = tg.load_nodes()
    tg.apply_supersession(nodes)
    pairs = find_candidates(nodes, near, dedup)
    dt = time.time() - t0

    bar = "=" * 76
    print(bar)
    print(f"  SEMANTIC CONTRADICTION / REDUNDANCY SCAN (I-12)  —  band [{near}, {dedup})")
    print(bar)
    print(f"  candidate cross-title pairs: {len(pairs)}   (scan {dt:.2f}s, CPU, local)")
    print()
    shown = pairs[:20]
    adj_counts = {}
    for s, a, b in shown:
        local = m.is_local_only(a["project"])
        print(f"  cos={s:.3f}  [{a['project']}]")
        print(f"     A {ICON.get(a['ntype'],'·')} {a['slug']} — {(a['desc'] or '')[:80]}")
        print(f"     B {ICON.get(b['ntype'],'·')} {b['slug']} — {(b['desc'] or '')[:80]}")
        if do_adj:
            if local:
                print("     adjudication: SKIPPED (local-only project — privacy)")
            elif not (m.CEREBRAS_API_KEY and m.ACTIVE_CLOUD != "none"):
                print("     adjudication: no cloud backend")
            else:
                rel, act = adjudicate((s, a, b))
                adj_counts[rel] = adj_counts.get(rel, 0) + 1
                print(f"     → {rel.upper()}: {act}")
        print()
    if len(pairs) > len(shown):
        print(f"  … +{len(pairs)-len(shown)} more pairs")
    if do_adj and adj_counts:
        print(f"\n  adjudicated (cloud-ok projects): {adj_counts}")
    print(bar)
    print("  Finding: these pairs are invisible to slug-supersession and to the 0.92")
    print("  dedup — exactly the cross-title reconciliation gap (temporal-graph §6).")
    print(bar)

    if "--save" in sys.argv:
        out = m.VAULT / "contradiction_candidates.json"
        m.write_atomic(out, json.dumps(
            [{"cosine": round(s, 4), "project": a["project"],
              "a": a["id"], "b": b["id"],
              "a_desc": a["desc"], "b_desc": b["desc"]} for s, a, b in pairs],
            ensure_ascii=False, indent=1))
        print(f"  saved → {out}")


if __name__ == "__main__":
    main()
