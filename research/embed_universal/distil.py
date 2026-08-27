#!/usr/bin/env python3
"""M3 - distil the cross-encoder's ranking into the bi-encoder.

M2 changed the negatives and taught the wrong axis. This changes the SUPERVISION: instead of a
hard label saying "this one, not that one", the student is trained to reproduce the teacher's
*margin* between the two, which carries how much better one is than the other.

Three decisions that decide whether this measures anything:

* **The queries are situation-shaped.** Each is a training lesson's *prevention* sentence - the
  same shape the benchmark's hard axis uses and the shape M2's mined negatives never had. Only
  non-held-out lessons are used; the frozen set is never touched.
* **The teacher is read as LOGITS, not probabilities.** `bge-reranker-v2-m3` sigmoids by
  default, and on this data it saturates: true positives sit at 0.9997. Margins between saturated
  probabilities are ~1.0 for nearly every triple, so the target would be almost constant and the
  run would return a null result for a reason that has nothing to do with distillation. With
  `activation_fn=Identity` the same pair reads 0.46 against -11.04. The margin distribution is
  measured and written into the record before training, so a degenerate target is visible rather
  than inferred afterwards.
* **The student starts from v1**, the shipped model, as the threshold document declares.

    python research/embed_universal/distil.py --dry-run   # supervision + VRAM, no training
    python research/embed_universal/distil.py

Needs a CUDA GPU. Writes models/distil_v1{,_merged}/ (gitignored) and
heldout/distillation_v1.json (committed).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LESSONS = HERE / "data" / "lessons.jsonl"
OUT = HERE / "models" / "distil_v1"
MERGED = HERE / "models" / "distil_v1_merged"
RECORD = HERE / "heldout" / "distillation_v1.json"

STUDENT = str(HERE / "models" / "universal_v1_merged")
TEACHER = "BAAI/bge-reranker-v2-m3"

SEED = 11
EPOCHS = 3
BATCH = 24
LR = 1e-4
LORA = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
        "target_modules": ["query", "key", "value", "dense"]}
DEPTH = 8          # candidates retrieved per query before the gold is removed
NEGATIVES = 2      # negatives kept per query
BATCH_INFER = 64
REQUIRED_FREE_GB = 20.0

#: A margin distribution this narrow means the teacher is not discriminating and the run would
#: measure nothing. Checked before training rather than diagnosed after.
MIN_MARGIN_SPREAD = 1.0

#: The teacher's margins are divided by their own interquartile range before they become targets.
#:
#: MarginMSELoss asks the student to reproduce the teacher's margin as a NUMBER. The student's
#: similarity is cosine over normalised embeddings, so its margin cannot leave [-2, 2]; the
#: teacher's logit margins span roughly [-13, +15]. The first run asked for values outside the
#: student's output space and got what that deserves: a model 17 points worse on the axis it was
#: meant to improve AND on the one it was meant to preserve, and a twin axis that finally broke.
#:
#: Dividing by the IQR puts the target's own interquartile range at about 1, inside what a cosine
#: margin can express, and keeps the ORDER and relative sizes the teacher assigned - which is the
#: part being distilled. The divisor is computed from the data rather than chosen, and recorded.
SCALE_BY = "iqr"


def _document(lesson: dict) -> str:
    return "\n".join(p for p in (lesson.get("title", ""), lesson.get("desc", "")) if p).strip()


def _situation(lesson: dict) -> str:
    title = (lesson.get("title") or "").strip()
    prevention = (lesson.get("prevention") or "").strip()
    if prevention and len(prevention) > 20 and prevention.lower() != title.lower():
        return prevention
    return ""


def _vram() -> dict:
    import torch
    free, total = torch.cuda.mem_get_info()
    return {"free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2)}


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    return {"min": round(s[0], 3), "p25": round(s[len(s) // 4], 3),
            "median": round(s[len(s) // 2], 3), "p75": round(s[3 * len(s) // 4], 3),
            "max": round(s[-1], 3)}


def build_supervision() -> tuple[list[dict], dict]:
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    lessons = [json.loads(line) for line in LESSONS.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    train = [r for r in lessons if not r.get("heldout")]
    docs = [_document(r) for r in train]
    pairs = [(i, _situation(r)) for i, r in enumerate(train)]
    pairs = [(i, q) for i, q in pairs if q]
    print(f"training lessons {len(train)}, situation-shaped queries {len(pairs)}", flush=True)

    model = SentenceTransformer(STUDENT, device="cuda")
    model.half()
    with torch.inference_mode():
        d_v = model.encode(docs, batch_size=BATCH_INFER, convert_to_tensor=True,
                           normalize_embeddings=True, show_progress_bar=False)
        q_v = model.encode([q for _, q in pairs], batch_size=BATCH_INFER, convert_to_tensor=True,
                           normalize_embeddings=True, show_progress_bar=False)
    order = (q_v @ d_v.T).float().argsort(dim=1, descending=True)[:, :DEPTH + 1].cpu().tolist()
    del model, d_v, q_v
    torch.cuda.empty_cache()

    triples = []
    for row, (gold, query) in zip(order, pairs):
        negatives = [j for j in row if j != gold][:NEGATIVES]
        for j in negatives:
            triples.append({"query": query, "pos": docs[gold], "neg": docs[j]})
    print(f"triples: {len(triples)}", flush=True)

    teacher = CrossEncoder(TEACHER, device="cuda", max_length=512)
    identity = torch.nn.Identity()          # LOGITS: the sigmoid saturates on this data
    with torch.inference_mode():
        pos_scores = teacher.predict([(t["query"], t["pos"]) for t in triples],
                                     batch_size=BATCH_INFER, activation_fn=identity,
                                     show_progress_bar=False)
        neg_scores = teacher.predict([(t["query"], t["neg"]) for t in triples],
                                     batch_size=BATCH_INFER, activation_fn=identity,
                                     show_progress_bar=False)
    del teacher
    torch.cuda.empty_cache()

    raw = [float(p) - float(n) for p, n in zip(pos_scores, neg_scores)]
    ordered = sorted(raw)
    iqr = ordered[3 * len(ordered) // 4] - ordered[len(ordered) // 4] if ordered else 1.0
    divisor = iqr if iqr > 1e-6 else 1.0
    for t, m in zip(triples, raw):
        t["raw_margin"] = m
        t["margin"] = m / divisor

    margins = [t["margin"] for t in triples]
    stats = {
        "training_lessons": len(train),
        "situation_queries": len(pairs),
        "triples": len(triples),
        "depth": DEPTH, "negatives_per_query": NEGATIVES,
        "teacher": TEACHER,
        "teacher_activation": "identity (raw logits)",
        "teacher_activation_note": (
            "The default sigmoid saturates on this data - true positives sit at 0.9997 - so "
            "margins between probabilities would be ~1.0 for nearly every triple and the target "
            "would be almost constant. The same pair reads 0.46 against -11.04 as logits."),
        "scale_rule": SCALE_BY,
        "margin_divisor": round(divisor, 4),
        "margin_scale_note": (
            "The teacher's raw logit margins span about 28 units; a cosine margin over normalised "
            "embeddings cannot leave [-2, 2]. Targets are divided by the raw margins' own "
            "interquartile range so the bulk lands inside what the student can express, keeping "
            "the order and relative sizes the teacher assigned. The first run omitted this and "
            "produced a model 17 points worse on both retrieval axes."),
        "raw_margin_quantiles": _quantiles(raw),
        "margin_quantiles": _quantiles(margins),
        "margin_spread": round(max(margins) - min(margins), 3) if margins else 0.0,
        "teacher_positive_logits": _quantiles([float(s) for s in pos_scores]),
        "teacher_negative_logits": _quantiles([float(s) for s in neg_scores]),
        "negative_margins": sum(1 for m in margins if m < 0),
    }
    return triples, stats


def _merge(src: Path, dst: Path) -> None:
    """Same routine as train_hard.py, and for the same recorded reason: a bare HF checkpoint
    silently loads MEAN-pooled while bge-m3 is a CLS model."""
    import torch
    from peft import PeftModel
    from sentence_transformers import SentenceTransformer, models as st_models
    from transformers import AutoModel, AutoTokenizer

    base = AutoModel.from_pretrained(STUDENT, torch_dtype=torch.float32)
    merged = PeftModel.from_pretrained(base, str(src)).merge_and_unload()
    merged.save_pretrained(str(dst))
    AutoTokenizer.from_pretrained(STUDENT).save_pretrained(str(dst))
    word = st_models.Transformer(str(dst))
    pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
    SentenceTransformer(modules=[word, pool, st_models.Normalize()], device="cuda").save(str(dst))
    check = SentenceTransformer(str(dst), device="cuda")
    pooling = [m for m in check.modules() if type(m).__name__ == "Pooling"]
    mode = getattr(pooling[0], "pooling_mode", "?") if pooling else "?"
    if mode != "cls":
        raise SystemExit(f"REFUSING: the merged checkpoint loads {mode}-pooled, not cls")
    print(f"merged -> {dst}  (verified {mode}-pooled through the published load path)", flush=True)


def main(argv: list[str] | None = None) -> int:
    import torch
    from peft import LoraConfig, TaskType
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    vram = _vram()
    print(f"VRAM before: {vram}", flush=True)
    if vram["free_gb"] < REQUIRED_FREE_GB:
        print(f"REFUSING: {vram['free_gb']} GB free, {REQUIRED_FREE_GB} required - the margin "
              "protects a concurrently-running Ollama from a mid-run collision.", flush=True)
        return 2

    triples, stats = build_supervision()
    print(json.dumps({k: stats[k] for k in
                      ("triples", "raw_margin_quantiles", "margin_divisor", "margin_quantiles",
                       "negative_margins")}, indent=1), flush=True)
    if stats["raw_margin_quantiles"].get("max", 0) - stats["raw_margin_quantiles"].get("min", 0) \
            < MIN_MARGIN_SPREAD:
        print(f"REFUSING: the teacher's margins span only {stats['margin_spread']}, which is not "
              "a signal to distil. Check the activation before changing anything else.",
              flush=True)
        return 3
    if args.dry_run:
        return 0

    random.seed(SEED)
    torch.manual_seed(SEED)
    examples = [InputExample(texts=[t["query"], t["pos"], t["neg"]], label=t["margin"])
                for t in triples]
    random.shuffle(examples)

    model = SentenceTransformer(STUDENT, device="cuda")
    model.add_adapter(LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, **LORA))
    loader = DataLoader(examples, shuffle=True, batch_size=BATCH, drop_last=True)
    loss = losses.MarginMSELoss(model)
    started = time.perf_counter()
    model.fit(train_objectives=[(loader, loss)], epochs=EPOCHS,
              warmup_steps=max(10, int(len(loader) * EPOCHS * 0.1)),
              optimizer_params={"lr": LR}, use_amp=True, show_progress_bar=False)
    seconds = time.perf_counter() - started
    peak = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    model.save(str(OUT))
    print(f"saved -> {OUT}  ({seconds:.0f}s, peak {peak} GB)", flush=True)
    del model
    torch.cuda.empty_cache()
    _merge(OUT, MERGED)

    RECORD.write_bytes((json.dumps({
        "generated_by": "research/embed_universal/distil.py",
        "student_init": "nevertwice-embed v1 (the shipped model)",
        "loss": "MarginMSELoss",
        "seed": SEED, "epochs": EPOCHS, "batch": BATCH, "lr": LR, "lora": LORA,
        "seconds": round(seconds, 1), "peak_allocated_gb": peak,
        "vram_before": vram, "vram_margin_required_gb": REQUIRED_FREE_GB,
        "changed_from_m2": ("the supervision, not the negatives: the teacher's MARGIN between a "
                            "query's own note and a retrieved neighbour, over situation-shaped "
                            "queries. M2 changed which negatives were shown and kept a hard "
                            "label."),
        **stats,
    }, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
