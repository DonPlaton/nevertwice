#!/usr/bin/env python3
"""RESEARCH (launch round) — fast vector-search R&D harness on the LongMemEval stand.

Loads the cached bge-m3 vectors once, builds the dense cosine matrix (one matmul) and
the BM25 matrix, caches both to .npy, then runs a battery of retrieval hypotheses
INSTANTLY (no GPU, no re-embed). Every method is scored exactly as longmem_eval.py
(calibrated score fusion, deterministic sid tie-break) so a win here is a win in production.

    python research/rnd_launch.py --build      # build cos/bm25 matrices → data/*.npy (once)
    python research/rnd_launch.py              # run the battery (fast)
    python research/rnd_launch.py --only=quant # one experiment group

Faithfulness gate: the `baseline` row must equal longmem_results.json
(hybrid R@1 0.550, R@5 0.802, R@10 0.858, MRR 0.657) or the harness is not trustworthy.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m  # noqa: E402
import longmem_eval as le  # noqa: E402

DATA = HERE / "data"
ORACLE = DATA / "longmemeval_oracle.json"
EMB = DATA / "longmem_embeds.json"
COS_NPY = DATA / "_rnd_cos.npy"
BM_NPY = DATA / "_rnd_bm.npy"
META = DATA / "_rnd_meta.json"
KS = (1, 3, 5, 10)


def build():
    """Build and cache the cosine matrix C (Qn,N) and BM25 matrix B (Qn,N)."""
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
    N = len(pool_ids)
    # dense matrix, unit-normalised (cosine == dot)
    S = np.array([svec[s] for s in pool_ids], dtype=np.float64)
    S /= (np.linalg.norm(S, axis=1, keepdims=True) + 1e-12)
    # BM25 over session token lists
    toks_lists = {s: m._token_list(pool[s]) for s in pool_ids}
    bm_tf, bm_dl, bm_df, bm_avgdl = le.build_bm25(pool_ids, toks_lists)
    idx = {s: i for i, s in enumerate(pool_ids)}
    rows_C, rows_B, qrel, qids = [], [], [], []
    used = [e for e in data if e["question_id"] in qvec
            and (set(e["answer_session_ids"]) & set(pool_ids))]
    for e in used:
        qid = e["question_id"]
        q = np.array(qvec[qid], dtype=np.float64)
        q /= (np.linalg.norm(q) + 1e-12)
        rows_C.append(S @ q)                                  # cosine to every session
        qt = m._tokens(e["question"])
        bm = le.bm25_scores(qt, pool_ids, bm_tf, bm_dl, bm_df, bm_avgdl)
        b = np.zeros(N)
        for s, v in bm.items():
            b[idx[s]] = v
        rows_B.append(b)
        qrel.append([idx[s] for s in e["answer_session_ids"] if s in idx])
        qids.append(qid)
    C = np.array(rows_C)
    B = np.array(rows_B)
    np.save(COS_NPY, C)
    np.save(BM_NPY, B)
    META.write_text(json.dumps({"pool_ids": pool_ids, "qrel": qrel, "qids": qids}))
    # also save the raw (un-normalised pre-truncation) session matrix for matryoshka tests
    np.save(DATA / "_rnd_S.npy", np.array([svec[s] for s in pool_ids], dtype=np.float64))
    np.save(DATA / "_rnd_Q.npy", np.array([qvec[q] for q in qids], dtype=np.float64))
    print(f"built C{C.shape} B{B.shape}  ({N} sessions, {len(qids)} questions)")


def load():
    C = np.load(COS_NPY)
    B = np.load(BM_NPY)
    meta = json.loads(META.read_text())
    return C, B, meta["pool_ids"], [set(r) for r in meta["qrel"]]


def _zrows(M, mask=None):
    """Row-wise z-score. If mask given, mean/std are over masked entries only and
    unmasked entries are set to the -3.0 floor (matches le.calibrated's lex path)."""
    if mask is None:
        mu = M.mean(axis=1, keepdims=True)
        sd = M.std(axis=1, keepdims=True)
        sd[sd == 0] = 1.0
        return (M - mu) / sd
    Z = np.full(M.shape, -3.0)
    for i in range(M.shape[0]):
        nz = mask[i]
        if not nz.any():
            continue
        vals = M[i, nz]
        mu = vals.mean()
        sd = vals.std() or 1.0
        Z[i, nz] = (vals - mu) / sd
    return Z


def _recall(scores, pool_ids, qrel):
    """recall@k + MRR for a (Qn,N) score matrix, deterministic (-score, sid) tie-break."""
    acc = {k: 0.0 for k in KS}
    mrr = 0.0
    n = scores.shape[0]
    pid = pool_ids
    for i in range(n):
        row = scores[i]
        rel = qrel[i]
        order = sorted(range(len(pid)), key=lambda j: (-row[j], pid[j]))
        for k in KS:
            if any(j in rel for j in order[:k]):
                acc[k] += 1.0
        for rank, j in enumerate(order):
            if j in rel:
                mrr += 1.0 / (rank + 1)
                break
    return {f"recall@{k}": acc[k] / n for k in KS} | {"mrr": mrr / n}


def _zrows_robust(M, mask=None):
    """Median/MAD z-score (outlier-robust; score pools are heavy-tailed)."""
    if mask is None:
        med = np.median(M, axis=1, keepdims=True)
        mad = np.median(np.abs(M - med), axis=1, keepdims=True)
        mad[mad == 0] = 1.0
        return (M - med) / (1.4826 * mad)
    Z = np.full(M.shape, -3.0)
    for i in range(M.shape[0]):
        nz = mask[i]
        if not nz.any():
            continue
        vals = M[i, nz]
        med = np.median(vals)
        mad = np.median(np.abs(vals - med)) or 1.0
        Z[i, nz] = (vals - med) / (1.4826 * mad)
    return Z


def fuse(Csig, B, sem_w=0.5, lex_w=1.0, agree=0.0, robust=False, adaptive=False):
    """Calibrated score fusion with optional cross-signal agreement bonus.
    Csig: dense-signal matrix (cosine or a quantized stand-in). agree>0 adds a
    z_sem*z_lex interaction term. robust → median/MAD normalisation.
    adaptive → per-query lexical weight from query specificity (max BM25 z)."""
    zfn = _zrows_robust if robust else _zrows
    ZS = zfn(Csig)
    ZL = zfn(B, mask=(B > 0))
    if adaptive:
        # specific query (one term dominates lexically) → trust lexical more;
        # diffuse query → lean on dense. scale sem_w by inverse lexical peakedness.
        peak = ZL.max(axis=1, keepdims=True)            # how sharply the top lex match stands out
        sw = sem_w * (1.0 / (1.0 + 0.25 * np.clip(peak, 0, None)))
        z = sw * ZS + lex_w * ZL
    else:
        z = sem_w * ZS + lex_w * ZL
    if agree:
        z = z + agree * ZS * ZL
    z = np.clip(z, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-z))


def binary_then_float_rerank(S, Q, B, sem_w=0.5, topm=50):
    """A2 lossless-ish path: rank by 32x-smaller binary codes, then re-score the
    top-M with the exact float cosine. Cache stays binary; recall ≈ float."""
    Sb = np.sign(S); Sb[Sb == 0] = 1.0
    Qb = np.sign(Q); Qb[Qb == 0] = 1.0
    d = S.shape[1]
    Cbin = (Qb @ Sb.T) / d
    Sn = S / (np.linalg.norm(S, axis=1, keepdims=True) + 1e-12)
    Qn = Q / (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-12)
    Crr = Cbin.copy()
    for i in range(Cbin.shape[0]):
        top = np.argpartition(-Cbin[i], topm)[:topm]
        Crr[i, top] = (Qn[i] @ Sn[top].T)               # exact cosine on the shortlist
    return fuse(Crr, B, sem_w=sem_w)


def fmt(name, r):
    return (f"  {name:28} " + " ".join(f"{r['recall@'+str(k)]:7.3f}" for k in KS)
            + f" {r['mrr']:7.3f}")


def main():
    if "--build" in sys.argv:
        build()
        return
    if not COS_NPY.exists():
        build()
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), None)
    C, B, pool_ids, qrel = load()
    print("=" * 74)
    print(f"  RND launch battery — {C.shape[0]} questions / {len(pool_ids)} sessions (bge-m3 cached)")
    print("=" * 74)
    print(f"  {'method':28} " + " ".join(f"{'R@'+str(k):>7}" for k in KS) + f" {'MRR':>7}")

    # ── faithfulness gate ────────────────────────────────────────────────────
    base = _recall(fuse(C, B), pool_ids, qrel)
    print(fmt("baseline (calibrated 0.5)", base))
    assert abs(base["recall@1"] - 0.550) < 1e-6 and abs(base["recall@5"] - 0.802) < 1e-6, \
        f"FAITHFULNESS FAIL: {base}"
    print("  " + "-" * 70)
    results = {"baseline": base}

    # ── A4: fusion-weight sweep ──────────────────────────────────────────────
    if only in (None, "weights"):
        for w in (0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0, 1.5):
            r = _recall(fuse(C, B, sem_w=w), pool_ids, qrel)
            results[f"sem_w={w}"] = r
            print(fmt(f"sem_w={w}", r))
        print("  " + "-" * 70)

    # ── A5: cross-signal agreement bonus ─────────────────────────────────────
    if only in (None, "agree"):
        for g in (0.05, 0.1, 0.2, 0.3, 0.5):
            r = _recall(fuse(C, B, agree=g), pool_ids, qrel)
            results[f"agree={g}"] = r
            print(fmt(f"agree={g}", r))
        print("  " + "-" * 70)

    # ── A2: matryoshka truncation (dense signal from truncated dims) ─────────
    if only in (None, "matryoshka"):
        S = np.load(DATA / "_rnd_S.npy")
        Q = np.load(DATA / "_rnd_Q.npy")
        for d in (128, 256, 384, 512, 768, 1024):
            St = S[:, :d].copy()
            Qt = Q[:, :d].copy()
            St /= (np.linalg.norm(St, axis=1, keepdims=True) + 1e-12)
            Qt /= (np.linalg.norm(Qt, axis=1, keepdims=True) + 1e-12)
            Ct = Qt @ St.T
            r = _recall(fuse(Ct, B), pool_ids, qrel)
            results[f"matry-{d}"] = r
            print(fmt(f"matryoshka-{d}d ({d*4}B)", r))
        print("  " + "-" * 70)

    # ── A2: binary quantization (sign codes) ─────────────────────────────────
    if only in (None, "quant"):
        S = np.load(DATA / "_rnd_S.npy")
        Q = np.load(DATA / "_rnd_Q.npy")
        Sb = np.sign(S); Sb[Sb == 0] = 1.0
        Qb = np.sign(Q); Qb[Qb == 0] = 1.0
        d = S.shape[1]
        # symmetric binary cosine ≈ (Qb·Sb)/d
        Cbin = (Qb @ Sb.T) / d
        r = _recall(fuse(Cbin, B), pool_ids, qrel)
        results["binary-sym"] = r
        print(fmt(f"binary symmetric ({d//8}B)", r))
        # asymmetric: float query · binary doc
        Qn = Q / (np.linalg.norm(Q, axis=1, keepdims=True) + 1e-12)
        Casym = (Qn @ Sb.T) / math.sqrt(d)
        r = _recall(fuse(Casym, B), pool_ids, qrel)
        results["binary-asym"] = r
        print(fmt(f"binary asymmetric ({d//8}B)", r))
        print("  " + "-" * 70)

    # ── A2: binary codes + float rerank of the shortlist (recover the loss) ──
    if only in (None, "binrerank"):
        S = np.load(DATA / "_rnd_S.npy")
        Q = np.load(DATA / "_rnd_Q.npy")
        for tm in (20, 50, 100):
            r = _recall(binary_then_float_rerank(S, Q, B, topm=tm), pool_ids, qrel)
            results[f"bin+rerank{tm}"] = r
            print(fmt(f"binary + float-rerank top{tm}", r))
        # binary on truncated dims (combo): 512d binary = 64B = 64x smaller
        Sb = np.sign(S[:, :512]); Sb[Sb == 0] = 1.0
        Qb = np.sign(Q[:, :512]); Qb[Qb == 0] = 1.0
        Ccombo = (Qb @ Sb.T) / 512
        r = _recall(fuse(Ccombo, B), pool_ids, qrel)
        results["bin+matry512"] = r
        print(fmt("binary+matryoshka-512 (64B)", r))
        print("  " + "-" * 70)

    # ── A5: robust (median/MAD) calibration ──────────────────────────────────
    if only in (None, "robust"):
        for w in (0.4, 0.5, 0.6):
            r = _recall(fuse(C, B, sem_w=w, robust=True), pool_ids, qrel)
            results[f"robust sem_w={w}"] = r
            print(fmt(f"robust(MAD) sem_w={w}", r))
        print("  " + "-" * 70)

    # ── A5: per-query specificity-adaptive weight ───────────────────────────
    if only in (None, "adaptive"):
        r = _recall(fuse(C, B, adaptive=True), pool_ids, qrel)
        results["adaptive"] = r
        print(fmt("specificity-adaptive weight", r))
        r = _recall(fuse(C, B, sem_w=0.6, adaptive=True), pool_ids, qrel)
        results["adaptive0.6"] = r
        print(fmt("specificity-adaptive (0.6)", r))
        print("  " + "-" * 70)

    # save
    out = {"stand": "longmemeval-oracle", "n_q": C.shape[0], "n_sess": len(pool_ids),
           "results": results}
    (HERE / "rnd_launch.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  saved → research/rnd_launch.json")
    print("=" * 74)


if __name__ == "__main__":
    t = time.time()
    main()
    print(f"  ({time.time()-t:.1f}s)", file=sys.stderr)
