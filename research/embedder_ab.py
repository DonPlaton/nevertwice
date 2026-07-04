#!/usr/bin/env python3
"""RESEARCH — embedder A/B on LongMemEval-oracle (the W2 "stronger embedder" question).

Nevertwice ships bge-m3 as the drop-in embedder (NEVERTWICE_EMBED_MODEL). This sweeps
candidate local embedders through the SAME production retrieval path — no prefixes, the
same 2000-char truncation, the same hybrid RRF — and reports external recall@k against
LongMemEval ground truth. It is the honest "would swapping the embedder, and nothing else,
improve recall?" test. A candidate ships as the recommended default ONLY if it beats bge-m3.

Each model is embedded + evaluated in its OWN subprocess (fresh import → its own per-model
embed cache via longmem_eval._emb_path), so vectors from different models never mix.

    python research/embedder_ab.py                       # default candidate set
    python research/embedder_ab.py bge-m3 mxbai-embed-large:latest   # explicit set

Needs each model pulled in Ollama and data/longmemeval_oracle.json present.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

try:                                            # Windows consoles default to cp1251
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PY = sys.executable

# Multilingual-first candidate set (the store is bilingual; bge-m3 was chosen for that).
# mxbai/bge-large are English-only — included as an English-ceiling reference, not as a
# default recommendation for a multilingual store.
DEFAULT_MODELS = [
    "bge-m3",                                # baseline (already cached)
    "snowflake-arctic-embed2:latest",        # multilingual, larger
    "dengcao/Qwen3-Embedding-0.6B:Q8_0",     # Qwen3-Embedding (2025 MTEB-multilingual SOTA family)
    "mxbai-embed-large:latest",              # English-only ceiling reference
]


def _slug(model):
    return "".join(c if (c.isalnum() or c in "-.") else "_" for c in model)


def run_one(model):
    env = dict(os.environ, NEVERTWICE_EMBED_MODEL=model)
    out = DATA / f"ab_{_slug(model)}.json"
    print(f"\n=== {model} — embedding (cached models skip) ===", flush=True)
    r = subprocess.run([PY, str(HERE / "longmem_eval.py"), "--embed"], env=env)
    if r.returncode != 0:
        print(f"  !! embed failed for {model}", flush=True)
        return None
    print(f"=== {model} — evaluating ===", flush=True)
    r = subprocess.run([PY, str(HERE / "longmem_eval.py"), "--save", f"--out={out}"], env=env)
    if r.returncode != 0 or not out.exists():
        print(f"  !! eval failed for {model}", flush=True)
        return None
    return json.loads(out.read_text(encoding="utf-8"))


def main():
    models = sys.argv[1:] or DEFAULT_MODELS
    rows = []
    for mdl in models:
        res = run_one(mdl)
        if res:
            rows.append((mdl, res))
    if not rows:
        print("no results", file=sys.stderr)
        sys.exit(1)
    base = next((r for mdl, r in rows if mdl == "bge-m3"), rows[0][1])
    print("\n" + "=" * 92)
    print("  Embedder A/B — LongMemEval-oracle, production drop-in (no prefix, hybrid RRF)")
    print("=" * 92)
    hdr = (f"  {'embedder':34} {'sem@1':>6} {'sem@5':>6} {'hyb@5':>6} "
           f"{'hyb@10':>7} {'mrr':>6} {'dHyb@10':>8}")
    print(hdr)
    print("  " + "-" * 96)
    summary = {}
    for mdl, res in rows:
        sem = res["methods"]["semantic"]
        hyb = res["methods"]["hybrid"]
        d10 = hyb["recall@10"] - base["methods"]["hybrid"]["recall@10"]
        flag = "  <- baseline" if mdl == "bge-m3" else (
            "  WIN" if d10 > 0.005 else ("  ~tie" if abs(d10) <= 0.005 else "  loss"))
        print(f"  {mdl:34} {sem['recall@1']:6.3f} {sem['recall@5']:6.3f} "
              f"{hyb['recall@5']:6.3f} {hyb['recall@10']:7.3f} {hyb['mrr']:6.3f} "
              f"{d10:+8.3f}{flag}")
        summary[mdl] = {"semantic": sem, "hybrid": hyb, "delta_hybrid@10": d10}
    # the committed aggregate lives next to longmem_results.json (research/, in git);
    # only aggregate metrics, never transcripts — DATA/ holds the gitignored per-model caches
    out_json = HERE / "embedder_ab.json"
    out_json.write_text(
        json.dumps({"baseline": "bge-m3", "models": summary}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("=" * 92)
    print(f"  saved → {out_json}")


if __name__ == "__main__":
    main()
