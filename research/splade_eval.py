#!/usr/bin/env python3
"""RESEARCH (launch round, A1) — does a learned-sparse signal (SPLADE) beat BM25
as the lexical arm of the calibrated fusion?

Encodes the 940 sessions + 500 questions with a SPLADE checkpoint (max-pooled
log(1+relu(MLM logits)) over the vocab), builds the query·doc sparse-dot matrix,
and compares calibrated(dense, SPLADE) against the shipped calibrated(dense, BM25)
on the same stand. Reuses the cached dense cosine matrix from rnd_launch.

    python research/splade_eval.py            # encode (GPU) + score + report

Honest scope: SPLADE truncates each session to MAXLEN tokens (passage model); BM25
sees the full session. Decide by the number.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import rnd_launch as R  # noqa: E402

DATA = HERE / "data"
ORACLE = DATA / "longmemeval_oracle.json"
EMB = DATA / "longmem_embeds.json"
SP_CACHE = DATA / "_splade_sparse.json"
MODEL = "naver/splade-cocondenser-ensembledistil"
MAXLEN = 512
TOPK = 256          # keep the top-K terms per vector (sparse)
KS = (1, 3, 5, 10)


def _texts():
    data = json.loads(ORACLE.read_text(encoding="utf-8"))
    cache = json.loads(EMB.read_text(encoding="utf-8"))
    svec, qvec = cache["sessions"], cache["questions"]
    pool = {}
    for e in data:
        for sid, turns in zip(e["haystack_session_ids"], e["haystack_sessions"]):
            if sid not in pool:
                pool[sid] = "\n".join(f"{t.get('role','')}: {t.get('content','')}"
                                      for t in turns)[:28000]
    pool_ids = [s for s in pool if s in svec]
    used = [e for e in data if e["question_id"] in qvec
            and (set(e["answer_session_ids"]) & set(pool_ids))]
    return pool, pool_ids, used


def encode(texts, batch=16):
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForMaskedLM.from_pretrained(MODEL).to(dev).eval()
    out = []
    t0 = time.time()
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            enc = tok(chunk, padding=True, truncation=True, max_length=MAXLEN,
                      return_tensors="pt").to(dev)
            logits = model(**enc).logits                       # (B, L, V)
            relu = torch.log1p(torch.relu(logits))
            mask = enc["attention_mask"].unsqueeze(-1)
            vec = (relu * mask).max(dim=1).values               # (B, V) SPLADE max-pool
            for row in vec:
                nz = torch.nonzero(row, as_tuple=True)[0]
                if len(nz) > TOPK:
                    top = torch.topk(row[nz], TOPK).indices
                    nz = nz[top]
                out.append({int(t): round(float(row[t]), 4) for t in nz})
            if (i // batch) % 10 == 0:
                print(f"  encoded {i+len(chunk)}/{len(texts)} ({time.time()-t0:.0f}s)",
                      file=sys.stderr)
    return out


def sparse_dot(qv, dv):
    if len(qv) > len(dv):
        qv, dv = dv, qv
    return sum(w * dv.get(t, 0.0) for t, w in qv.items())


def main():
    pool, pool_ids, used = _texts()
    if SP_CACHE.exists():
        sp = json.loads(SP_CACHE.read_text())
        s_sparse = {k: {int(t): w for t, w in v.items()} for k, v in sp["sessions"].items()}
        q_sparse = {k: {int(t): w for t, w in v.items()} for k, v in sp["questions"].items()}
        print(f"[splade] loaded cache ({len(s_sparse)} sessions, {len(q_sparse)} questions)",
              file=sys.stderr)
    else:
        print(f"[splade] encoding {len(pool_ids)} sessions + {len(used)} questions with {MODEL}",
              file=sys.stderr)
        sv = encode([pool[s] for s in pool_ids])
        qv = encode([e["question"] for e in used])
        s_sparse = {pool_ids[i]: sv[i] for i in range(len(pool_ids))}
        q_sparse = {used[i]["question_id"]: qv[i] for i in range(len(used))}
        SP_CACHE.write_text(json.dumps({
            "sessions": s_sparse, "questions": q_sparse, "model": MODEL}))
        print(f"[splade] cached → {SP_CACHE.name}", file=sys.stderr)

    # SPLADE score matrix aligned to rnd_launch's pool order
    C, B, rl_pool, qrel = R.load()
    assert rl_pool == pool_ids, "pool mismatch — rebuild rnd_launch first"
    Sp = np.zeros((len(used), len(pool_ids)))
    for i, e in enumerate(used):
        qsp = q_sparse[e["question_id"]]
        for j, sid in enumerate(pool_ids):
            Sp[i, j] = sparse_dot(qsp, s_sparse[sid])

    print("=" * 74)
    print(f"  SPLADE vs BM25 as the lexical arm — {len(used)} q / {len(pool_ids)} sessions")
    print("=" * 74)
    print(f"  {'method':28} " + " ".join(f"{'R@'+str(k):>7}" for k in KS) + f" {'MRR':>7}")
    rows = {}
    rows["calibrated(dense,BM25)"] = R._recall(R.fuse(C, B), pool_ids, qrel)
    rows["SPLADE alone"] = R._recall(Sp, pool_ids, qrel)
    rows["calibrated(dense,SPLADE)"] = R._recall(R.fuse(C, Sp), pool_ids, qrel)
    rows["calibrated(dense,BM25+SPLADE)"] = R._recall(
        R.fuse(C, B + Sp / (Sp.max() or 1) * B.max()), pool_ids, qrel)
    for name, r in rows.items():
        print(R.fmt(name, r))
    base = rows["calibrated(dense,BM25)"]["recall@5"]
    spl = rows["calibrated(dense,SPLADE)"]["recall@5"]
    verdict = ("WIN — SPLADE beats BM25, ship as opt-in lexical signal"
               if spl > base + 0.005 else
               "NO WIN — BM25 matches/beats SPLADE here; keep stdlib BM25 (honest negative)")
    print(f"\n  → R@5 SPLADE {spl:.3f} vs BM25 {base:.3f} ({spl-base:+.3f}) — {verdict}")
    (HERE / "splade_eval.json").write_text(json.dumps(
        {"model": MODEL, "maxlen": MAXLEN, "rows": rows, "verdict": verdict}, indent=1))
    print("=" * 74)


if __name__ == "__main__":
    main()
