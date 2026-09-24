"""LoCoMo, the benchmark this project decided not to run, run anyway.

The roadmap has said for months that **LoCoMo is not a candidate**, and the reason has never
changed: plain BM25 is reported to score about 94% on it, so it no longer separates memory
systems, and a vendor headline there says little about one. That is a claim about a benchmark,
and this project's own rule is that a claim needs a measurement. Refusing to run something
because you believe it is saturated, without showing that it is, is an argument from authority
with the authority being us.

So: run it, on the same stand as everything else, and let the floor speak. If a term-overlap
baseline with no embedder and no model lands near the ceiling, the exclusion is demonstrated
rather than asserted, and the number is published either way.

**What this measures, and what it does not.** LoCoMo ships human-annotated evidence turns
(`dia_id`, e.g. `D1:3`) for every question, so it supports a retrieval metric with external
ground truth, exactly like LongMemEval. That is what runs here. It is **not** the setting the
published LoCoMo headlines use: those are end-to-end question answering scored by an LLM judge,
which measures the reader as much as the memory. A number from this file must never be compared
against a vendor's LoCoMo accuracy figure; they are different quantities.

Retrieval is per conversation, which is LoCoMo's own setting: each question is about one long
dialogue, and the haystack is that dialogue's turns.

    python research/locomo_eval.py --embed
    python research/locomo_eval.py --save --out=research/results/locomo.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard  # noqa: E402

sandbox_guard.isolate(prefix="nevertwice_locomo_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import corpus_pin  # noqa: E402
import longmem_eval as le  # noqa: E402
import memory_hook as m  # noqa: E402
import _ollama_pacer as pacer  # noqa: E402 - R-v2-ports: pace/retry/count --embed's traffic

KS = (1, 3, 5, 10)
CORPUS = "locomo10"
EMB = HERE / "data" / "locomo_embeds.json"
MAXCHARS = 4000        # a dialogue turn; the cap only catches pathological outliers


def load() -> list[dict]:
    """One record per conversation: the turn pool and the questions asked about it."""
    corpus_pin.verify(CORPUS)
    raw = json.loads(corpus_pin.path_of(CORPUS).read_text(encoding="utf-8"))
    out = []
    for conv in raw:
        # Namespace every turn id with its conversation. `dia_id` restarts at D1:1 in each of
        # the ten, so a bare id names ten different lines; keying anything by it merges them.
        sid = conv.get("sample_id")
        pool: dict[str, str] = {}
        #: K21 (.loop/HANDOFF-PORTS.md, PREREG-V2 rev 4 P0(b)) - the SAME shape longmem_eval's
        #: own `load()` excludes: a turn with no text is a corpus property, not a failure,
        #: excluded from `pool` before embedding is ever attempted. Empirically zero for the
        #: real locomo10.json (checked read-only, on the RAW speaker/text fields - the
        #: templated "speaker: text" string is never "" even when both are blank, since the
        #: ": " itself survives `.strip()`; checking the templated form would silently never
        #: catch anything), but the mechanism exists for the same reason the gate does - a
        #: corpus edit or a future one could introduce it silently.
        empty_skipped: list[str] = []
        for key, val in conv["conversation"].items():
            if not key.startswith("session_") or key.endswith("_date_time"):
                continue
            if not isinstance(val, list):
                continue
            for turn in val:
                did = turn.get("dia_id")
                if not did:
                    continue
                # Emptiness is checked on the RAW fields, before the "speaker: text"
                # template - the template's own ": " survives `.strip()` even when both
                # fields are blank (a turn with speaker="" and text="" joins to ":", not
                # "", so checking the TEMPLATED string never catches it - the bug this
                # comment exists to prevent a regression back into).
                speaker = (turn.get("speaker") or "").strip()
                content = (turn.get("text") or "").strip()
                if not speaker and not content and not turn.get("blip_caption"):
                    empty_skipped.append(f"{sid}:{did}")
                    continue
                text = f"{turn.get('speaker', '')}: {turn.get('text', '')}".strip()
                if turn.get("blip_caption"):
                    text += f" [image: {turn['blip_caption']}]"
                pool[f"{sid}:{did}"] = text[:MAXCHARS]
        qa = []
        for q in conv["qa"]:
            ev = q.get("evidence") or []
            if isinstance(ev, str):
                ev = [ev]
            ev = [f"{sid}:{e}" for e in ev]
            ev = [e for e in ev if e in pool]
            if not ev or not q.get("question"):
                continue                        # unanswerable / adversarial: no retrieval target
            qa.append({"question": q["question"], "evidence": ev,
                       "category": q.get("category"), "sample_id": sid})
        out.append({"sample_id": sid, "pool": pool, "qa": qa,
                    "empty_skipped": sorted(empty_skipped)})
    return out


def _cache_identity() -> dict:
    """What the vectors in `locomo_embeds.json` are, so a re-measure on stale ones is visible."""
    if not EMB.exists():
        return {"path": str(EMB.relative_to(ROOT)).replace("\\", "/"), "present": False}
    h, size = hashlib.sha256(), 0
    with open(EMB, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return {"path": str(EMB.relative_to(ROOT)).replace("\\", "/"), "present": True,
            "bytes": size, "sha256": h.hexdigest(),
            "mtime": int(EMB.stat().st_mtime),
            "note": "rebuilt by `python research/locomo_eval.py --embed`; gitignored input"}


def embed_all(convs: list[dict]) -> dict:
    cache = json.loads(EMB.read_text(encoding="utf-8")) if EMB.exists() else {}
    cache.setdefault("turns", {})
    cache.setdefault("questions", {})
    cache.setdefault("dropped_turns", [])
    cache.setdefault("dropped_questions", [])
    #: K21(1): `c["pool"]` already excludes `empty_skipped` turns (load()'s own job) - an
    #: empty turn is never even attempted here.
    todo_t = [(d, t) for c in convs for d, t in c["pool"].items() if d not in cache["turns"]]
    todo_q = [q["question"] for c in convs for q in c["qa"] if q["question"] not in cache["questions"]]
    pacer.install()          # R-v2-ports: pace/retry/count this --embed run's own traffic
    snap = pacer.snapshot()
    #: B1 (.loop/HANDOFF-PORTS.md, item 6, "same for locomo if the same pattern exists" -
    #: it does): a turn whose embed FAILS never lands in `cache["turns"]` - `main()`'s own
    #: `ids = [d for d in c["pool"] if d in tvec]` then drops it from that conversation's
    #: pool, silently. A retry that later succeeds clears it here.
    dropped = set(cache["dropped_turns"])
    #: K21(4): the SAME shape for a question - its own embed failing leaves it out of
    #: `cache["questions"]` and `main()`'s scoring loop skips it (`q["question"] not in
    #: qvec`) with no trace either.
    dropped_q = set(cache["dropped_questions"])
    t0 = time.time()
    for i, (did, text) in enumerate(todo_t, 1):
        # `le.embed_full`: the engine's endpoint, model and prefix without the 2,000-char
        # hot-path cap. No LoCoMo turn is longer than 462 characters, so the vectors are
        # the same either way; the kinds are the engine's own ("doc" was not one of them).
        v = le.embed_full(text, kind=m.doc_embed_kind())
        if v:
            cache["turns"][did] = v
            dropped.discard(did)
        else:
            dropped.add(did)
        if i % 500 == 0:
            print(f"  turns {i}/{len(todo_t)}  ({time.time() - t0:.0f}s)", flush=True)
            cache["dropped_turns"] = sorted(dropped)
            pacer.attach(cache, since=snap)
            EMB.write_text(json.dumps(cache), encoding="utf-8", newline="\n")
    for i, q in enumerate(dict.fromkeys(todo_q), 1):
        v = le.embed_full(q, kind=m.query_embed_kind())
        if v:
            cache["questions"][q] = v
            dropped_q.discard(q)
        else:
            dropped_q.add(q)
        if i % 500 == 0:
            print(f"  questions {i}  ({time.time() - t0:.0f}s)", flush=True)
    cache["dropped_turns"] = sorted(dropped)
    cache["dropped_questions"] = sorted(dropped_q)
    pacer.attach(cache, since=snap)
    EMB.write_text(json.dumps(cache), encoding="utf-8", newline="\n")
    print(f"[embed] done in {time.time() - t0:.0f}s -> {EMB.name}")
    return cache


def _recall_mrr(ranked: list[str], rel: set[str]) -> tuple[dict, float]:
    rec = {k: (1.0 if rel & set(ranked[:k]) else 0.0) for k in KS}
    mrr = 0.0
    for i, s in enumerate(ranked, 1):
        if s in rel:
            mrr = 1.0 / i
            break
    return rec, mrr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=None, help="first N conversations (smoke)")
    ap.add_argument("--no-morphology", action="store_true",
                    help="ablation: raw tokens, no stop words, no stems (NEVERTWICE_LEXICAL_MORPHOLOGY=0)")
    args = ap.parse_args()
    if args.no_morphology:
        m.LEXICAL_MORPHOLOGY = False

    convs = load()
    if args.limit:
        convs = convs[:args.limit]
    n_turns = sum(len(c["pool"]) for c in convs)
    n_qa = sum(len(c["qa"]) for c in convs)
    print(f"LoCoMo: {len(convs)} conversations, {n_turns} turns, {n_qa} scorable questions")

    if args.embed:
        embed_all(convs)
        return 0
    if not EMB.exists():
        print("No embeddings - run: python research/locomo_eval.py --embed", file=sys.stderr)
        return 2
    cache = json.loads(EMB.read_text(encoding="utf-8"))
    tvec, qvec = cache["turns"], cache["questions"]

    methods = ("semantic", "lexical", "hybrid")
    agg = {mth: ({k: 0.0 for k in KS}, [0.0]) for mth in methods}
    by_cat: dict = {}
    n = 0
    pool_used = 0            # B1: turns actually in a conversation's pool (embed succeeded)
    for c in convs:
        ids = [d for d in c["pool"] if d in tvec]
        pool_used += len(ids)
        toks = {d: m._token_list(c["pool"][d]) for d in ids}
        bm_tf, bm_dl, bm_df, bm_avgdl = le.build_bm25(ids, toks)
        for q in c["qa"]:
            if q["question"] not in qvec:
                continue
            rel = set(q["evidence"])
            if not rel & set(ids):
                continue
            qv = qvec[q["question"]]
            qt = m._tokens(q["question"])
            cos = {d: m.cosine(qv, tvec[d]) for d in ids}
            bm = le.bm25_scores(qt, ids, bm_tf, bm_dl, bm_df, bm_avgdl)
            cal = le.calibrated(cos, bm)
            # `bm25_scores` returns only the turns a query term actually hit, so the lexical
            # arm ranks over its own keys and the fused arm over the union - the same shape
            # `longmem_eval` uses. Sorting `ids` by `bm[d]` would raise on every turn the
            # query never touched, which is most of them.
            ranked = {"semantic": sorted(ids, key=lambda d: (-cos[d], d)),
                      "lexical": sorted(bm, key=lambda d: (-bm[d], d)),
                      "hybrid": sorted(cal, key=lambda d: (-cal[d], d))}
            n += 1
            cat = str(q.get("category"))
            slot = by_cat.setdefault(cat, {mth: [0.0, 0] for mth in methods})
            for mth in methods:
                rec, mr = _recall_mrr(ranked[mth], rel)
                for k in KS:
                    agg[mth][0][k] += rec[k]
                agg[mth][1][0] += mr
                slot[mth][0] += rec[5]
                slot[mth][1] += 1

    print("=" * 74)
    print(f"  LoCoMo per-conversation retrieval, external evidence turns as ground truth")
    print(f"  {n} scorable questions over {n_turns} turns in {len(convs)} conversations")
    print("=" * 74)
    print(f"  {'method':12} " + " ".join(f"{'R@' + str(k):>7}" for k in KS) + f" {'MRR':>7}")
    out = {}
    for mth in methods:
        rec, mr = agg[mth]
        row = {f"recall@{k}": rec[k] / n if n else 0 for k in KS}
        row["mrr"] = mr[0] / n if n else 0
        out[mth] = row
        print(f"  {mth:12} " + " ".join(f"{row['recall@' + str(k)]:7.3f}" for k in KS)
              + f" {row['mrr']:7.3f}")

    lex5, hyb5 = out["lexical"]["recall@5"], out["hybrid"]["recall@5"]
    print(f"\n  → lexical-only R@5 = {lex5:.3f}. The roadmap excluded this benchmark on the")
    print(f"    grounds that a term-overlap floor lands near the ceiling and it therefore stops")
    print(f"    separating systems. Fusion adds {hyb5 - lex5:+.3f} over that floor here.")
    print(f"  → NB: this is RETRIEVAL recall against annotated evidence turns. It is NOT the")
    print(f"    LLM-judged answer accuracy that published LoCoMo headlines report; the two")
    print(f"    numbers measure different things and must never be compared.")

    if args.save:
        res = {"conversations": len(convs), "turns": n_turns, "questions": n,
               "morphology": bool(m.LEXICAL_MORPHOLOGY),
               "embedder": m.EMBED_MODEL, "methods": out,
               "by_category_recall_at_5": {c: {k: (v[0] / v[1] if v[1] else 0.0)
                                               for k, v in s.items()}
                                           for c, s in sorted(by_cat.items())},
               #: K21(1)/PREREG-V2 rev 4 P0(b): a turn with no text is a corpus property
               #: (empirically 0 on the real locomo10.json, checked read-only), excluded
               #: from `pool`/`n_turns` at `load()` time - never against `n_turns` and
               #: never as a drop.
               "empty_skipped": sum(len(c.get("empty_skipped") or []) for c in convs),
               "provenance": corpus_pin.record(CORPUS),
               #: The corpus is pinned by hash and the VECTORS were pinned by nothing. This
               #: stand's claims close over thirty files including all nine engine parts, so any
               #: engine edit withdraws them - and the re-measure then reads whatever vectors
               #: `locomo_embeds.json` happens to hold, leaving the semantic half of the
               #: measurement un-redone. On a cached cache "every figure reproduced exactly" is
               #: then true by construction: evidence that nothing was re-embedded rather than
               #: that the numbers survive a revised engine (audit 2026-09-22). The cache is a
               #: declared rebuildable input, not a committed artifact - `research/data/
               #: .gitignore:11` - so recording its identity is the only way a reader can tell
               #: which vectors a number came from.
               "embed_cache": _cache_identity()}
        # K21(4): the question axis, pinned to `n_qa` - the SCORABLE count `load()` itself
        # already computed (its own filter drops LoCoMo's 9 unanswerable/adversarial
        # questions as a CORPUS property, never a failure; the auditor's rev-4 note: this
        # must be the load()-filtered count, 1,977, never the raw 1,986, or K21 recurs here).
        le.copy_cache_provenance(res, cache, [
            ("dropped_turns", sorted(cache.get("dropped_turns") or []),
             pool_used, n_turns, "turns"),
            ("dropped_questions", sorted(cache.get("dropped_questions") or []),
             n, n_qa, "questions"),
        ])
        target = Path(args.out) if args.out else (HERE / "results" / "locomo.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print(f"\n  saved -> {target}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
