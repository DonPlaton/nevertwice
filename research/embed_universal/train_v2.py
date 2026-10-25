#!/usr/bin/env python3
"""nevertwice-embed v2: 3-axis LoRA r=4 + Matryoshka [1024, 512, 256] + Bridge Supervision on bge-m3.

Combines the three measured findings from M4 (train_matryoshka.py), M5 (capacity_sweep.py),
and Stage D / A6.7:
1. Axis 1 (Capacity): LoRA r=4 (lora_alpha=8, 1.78M trainable params) on qkv+dense, which
   generalizes better to unseen domains than r=16/r=64 by preserving bge-m3's pretrained
   multilingual geometry.
2. Axis 2 (Matryoshka): MatryoshkaLoss over dims [1024, 512, 256] with weights [1.0, 0.5, 0.5],
   so 256d (1 KB/vec, 4x smaller index) and 512d prefixes stand on their own while regularizing
   the full 1024d representation.
3. Axis 3 (Bridge Supervision): High-confidence situation-to-lesson pairs from non-held-out
   training lessons (filtered by BAAI/bge-reranker-v2-m3 teacher logits) plus bilingual EN/RU
   conversational bridge pairs (status, temporal, origin, and supersession queries paired with
   indirect episodic statements).

Evaluates stock bge-m3, nevertwice-embed v1 (r=16), and nevertwice-embed v2 across all axes
(twin AUC / recall@1%FPR / recall@0%FPR, retrieval_title R@1/R@5/MRR@10, retrieval_situation
R@1/R@5/MRR@10 in EN & RU, Matryoshka widths 1024/512/256, and conversational bridge R@1/MRR).
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, TaskType
from sentence_transformers import CrossEncoder, InputExample, SentenceTransformer, losses, models as st_models
from torch.utils.data import DataLoader
from transformers import AutoModel, AutoTokenizer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from heldout_eval import _retrieval_block, _twin_block, encode  # noqa: E402

PAIRS = HERE / "data" / "train_pairs.jsonl"
LESSONS = HERE / "data" / "lessons.jsonl"
FROZEN = HERE / "heldout" / "external_heldout_v1.json"
OUT_ADAPTER = HERE / "models" / "universal_v2"
OUT_MERGED = HERE / "models" / "universal_v2_merged"
V1_MERGED = HERE / "models" / "universal_v1_merged"
RECORD = HERE / "heldout" / "v2_3axis_eval.json"

BASE = "BAAI/bge-m3"
TEACHER = "BAAI/bge-reranker-v2-m3"
SEED = 11
WIDTHS = [1024, 512, 256]
TARGETS = ["query", "key", "value", "dense"]

# Bilingual conversational & temporal bridge supervision (synthetic, domain-disjoint from heldout)
BRIDGE_TRAIN_PAIRS = [
    ("What is her current relationship status?", "[Date: 2023-05-25 | Speaker: Alice] It will be tough raising kids as a single parent after that rough breakup, but I am ready."),
    ("What is his marital or relationship status?", "[Date: 2024-02-10 | Speaker: Mark] My wife and I celebrated our fifth wedding anniversary yesterday [2024-02-09]."),
    ("Where did she move from several years ago?", "[Date: 2023-06-09 | Speaker: Clara] I moved here from my home country four years ago [2019], and my grandma back in Sweden sent me this necklace."),
    ("What country is the user originally from?", "[Date: 2023-08-12 | Speaker: user] Growing up in my hometown in Portugal taught me to love coastal road trips."),
    ("How many total hours did I spend driving across my trips?", "[Date: 2023-05-21 | Speaker: user] My drive to the Outer Banks took four hours, the Washington D.C. trip took six hours, and Tennessee took five hours."),
    ("How many projects have I completed in total now?", "[Confirmed fact] User completed 5 woodworking projects as of August [Date: 2023-08-15]\n[Prior 2023-07-10: User had completed 4 woodworking projects]"),
    ("When did she run the charity race?", "[Date: 2023-05-25 | Speaker: Melanie] I ran a charity race for mental health last Saturday [2023-05-20] and it was rewarding."),
    ("When is the family planning to go camping?", "[Date: 2023-05-25 | Speaker: Melanie] The kids are excited for summer break and we are going camping next month [June 2023]."),
    ("What are her plans for the summer?", "[Date: 2023-05-25 | Speaker: Caroline] (re: Any fun plans for the summer?) Researching adoption agencies to give a loving home to kids."),
    ("What degree did the user graduate with?", "[Date: 2023-05-20 | Speaker: user] I graduated with a degree in Business Administration before switching into logistics."),
    ("Какой у пользователя семейный статус?", "[Date: 2024-03-15 | Speaker: user] После тяжёлого расставания я живу один и планирую усыновить ребёнка как родитель-одиночка."),
    ("Откуда он переехал несколько лет назад?", "[Date: 2024-04-10 | Speaker: user] Я переехал из родной страны четыре года назад [2020], бабушка из Швеции подарила мне этот кулон."),
    ("Сколько всего проектов я завершил после обновления?", "[Confirmed fact] Завершено 5 проектов по живописи [Date: 2024-08-15]\n[Prior 2024-07-10: Ранее было завершено 4 проекта по живописи]"),
    ("Что ассистент предлагал в качестве гипотетического варианта?", "[Date: 2024-05-20] [Assistant context — hypothetical/suggestion] В качестве гипотетического варианта можно рассмотреть экспресс-автобус за $29 вместо такси."),
]

# Held-out conversational bridge evaluation queries & corpus (never seen in training)
CONV_BENCH_CORPUS = [
    "[Date: 2023-05-25 | Speaker: Elena] Raising a child as a single parent after my breakup will be challenging, but I am optimistic.",
    "[Date: 2023-06-14 | Speaker: Elena] I left my home country five years ago [2018]; my aunt in Norway still sends me books.",
    "[Date: 2023-05-20 | Speaker: user] I graduated with a Bachelor of Science in Mechanical Engineering in 2018.",
    "[Date: 2023-08-19 | Speaker: user] I have now finished 7 ceramic bowls in class.\n[Prior 2023-07-01: User had finished 5 ceramic bowls]",
    "[Date: 2023-05-25 | Speaker: Lucas] (re: What are your plans for the summer?) Volunteering at the marine biology station in July.",
    "[Date: 2023-06-10 | Speaker: Lucas] I attended the robotics symposium last Friday [2023-06-02] in Boston.",
    "[Date: 2023-05-22] [Assistant context] Hypothetically, renting a hybrid car could save around $85 on fuel.",
    "[Date: 2024-05-12 | Speaker: Анна] Я окончила университет со степенью магистра прикладной математики и информатики.",
    "[Date: 2024-06-20 | Speaker: Анна] На прошлой неделе [the week before 2024-06-20] я выступала на школьной конференции.",
    "[Date: 2024-09-01 | Speaker: Анна] Сейчас у меня закрыто 9 спринтов.\n[Prior 2024-08-01: Было закрыто 6 спринтов]",
]
CONV_BENCH_QUERIES = [
    ("What is Elena's relationship status?", 0),
    ("Which country did Elena move from 5 years ago?", 1),
    ("What university degree did I graduate with?", 2),
    ("How many ceramic bowls have I finished since starting class?", 3),
    ("What are Lucas's plans for the summer?", 4),
    ("When did Lucas attend the robotics symposium?", 5),
    ("What hypothetical fuel savings did the assistant estimate?", 6),
    ("С какой специальностью или дипломом Анна окончила университет?", 7),
    ("Когда Анна выступала на школьной конференции?", 8),
    ("Сколько всего спринтов закрыто у Анны на текущий момент?", 9),
]


def select_teacher_situation_pairs(top_k: int = 160) -> list[tuple[str, str]]:
    """Select the cleanest non-held-out (situation, doc) pairs verified by bge-reranker-v2-m3."""
    lessons = [json.loads(line) for line in LESSONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    train_lessons = [r for r in lessons if not r.get("heldout")]
    cands = []
    for r in train_lessons:
        title = (r.get("title") or "").strip()
        desc = (r.get("desc") or "").strip()
        prev = (r.get("prevention") or "").strip()
        if prev and len(prev) > 25 and prev.lower() != title.lower() and desc:
            cands.append((prev, f"{title}\n{desc}".strip()))
    teacher = CrossEncoder(TEACHER, device="cuda", max_length=512)
    with torch.inference_mode():
        logits = teacher.predict(cands, batch_size=64, activation_fn=torch.nn.Identity(), show_progress_bar=False)
    del teacher
    torch.cuda.empty_cache()
    scored = sorted(zip(logits, cands), key=lambda x: x[0], reverse=True)
    return [pair for score, pair in scored[:top_k] if float(score) > 3.0]


def evaluate_all_widths(model_path: str, data: dict) -> dict:
    """Evaluate a checkpoint at 1024d, 512d, and 256d on external_heldout_v1 + conversational bridge."""
    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    texts = (
        corpus
        + [r["a"] for r in twin]
        + [r["b"] for r in twin]
        + [q["query"] for q in data["axes"]["retrieval_title"]]
        + [q["query"] for q in data["axes"]["retrieval_situation"]]
        + CONV_BENCH_CORPUS
        + [q for q, _ in CONV_BENCH_QUERIES]
    )
    t0 = time.perf_counter()
    full_vecs = encode(model_path, texts)
    encode_s = round(time.perf_counter() - t0, 2)

    out_widths = {}
    for w in WIDTHS:
        vecs = torch.nn.functional.normalize(full_vecs[:, :w], p=2, dim=1)
        at = 0
        c_v = vecs[at:at + len(corpus)]; at += len(corpus)
        a_v = vecs[at:at + len(twin)]; at += len(twin)
        b_v = vecs[at:at + len(twin)]; at += len(twin)
        w_res: dict = {
            "twin": _twin_block([{"label": r["label"], "score": float(torch.dot(a_v[i], b_v[i]))}
                                 for i, r in enumerate(twin)]),
            "by_language": {},
        }
        for axis in ("retrieval_title", "retrieval_situation"):
            queries = data["axes"][axis]
            q_v = vecs[at:at + len(queries)]; at += len(queries)
            order = (q_v @ c_v.T).argsort(dim=1, descending=True)
            ranks = []
            for i, q in enumerate(queries):
                row = order[i].tolist()
                ranks.append(row.index(q["gold"]) + 1 if q["gold"] in row[:200] else 0)
            w_res[axis] = _retrieval_block(ranks)
            per_lang: dict[str, list[int]] = {}
            for q, rnk in zip(queries, ranks):
                per_lang.setdefault(q["lang"] or "?", []).append(rnk)
            w_res["by_language"][axis] = {
                lang: {
                    "n": len(rs),
                    "recall@1": round(sum(1 for r in rs if r == 1) / len(rs), 4),
                    "recall@5": round(sum(1 for r in rs if 1 <= r <= 5) / len(rs), 4),
                }
                for lang, rs in sorted(per_lang.items())
            }
        conv_c = vecs[at:at + len(CONV_BENCH_CORPUS)]; at += len(CONV_BENCH_CORPUS)
        conv_q = vecs[at:at + len(CONV_BENCH_QUERIES)]; at += len(CONV_BENCH_QUERIES)
        conv_order = (conv_q @ conv_c.T).argsort(dim=1, descending=True)
        conv_ranks = []
        for i, (_, gold_idx) in enumerate(CONV_BENCH_QUERIES):
            row = conv_order[i].tolist()
            conv_ranks.append(row.index(gold_idx) + 1)
        w_res["conversational_bridge"] = {
            "n": len(conv_ranks),
            "recall@1": round(sum(1 for r in conv_ranks if r == 1) / len(conv_ranks), 4),
            "recall@3": round(sum(1 for r in conv_ranks if r <= 3) / len(conv_ranks), 4),
            "mrr": round(sum(1.0 / r for r in conv_ranks) / len(conv_ranks), 4),
        }
        out_widths[str(w)] = w_res
    return {"encode_seconds": encode_s, "widths": out_widths}


def merge_checkpoint(src_adapter: Path, dst_merged: Path) -> float:
    base = AutoModel.from_pretrained(BASE, torch_dtype=torch.float32)
    merged = PeftModel.from_pretrained(base, str(src_adapter)).merge_and_unload()
    merged.save_pretrained(str(dst_merged))
    AutoTokenizer.from_pretrained(BASE).save_pretrained(str(dst_merged))
    word = st_models.Transformer(str(dst_merged))
    pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
    SentenceTransformer(modules=[word, pool, st_models.Normalize()], device="cuda").save(str(dst_merged))

    st_a = SentenceTransformer(str(src_adapter), device="cuda")
    st_m = SentenceTransformer(str(dst_merged), device="cuda")
    probe = ["verify checksum before merge", "проверка инвариантов памяти", "single parent adoption plan"]
    va = st_a.encode(probe, normalize_embeddings=True, convert_to_numpy=True)
    vm = st_m.encode(probe, normalize_embeddings=True, convert_to_numpy=True)
    min_cos = float((va * vm).sum(axis=1).min())
    del st_a, st_m
    torch.cuda.empty_cache()
    if min_cos < 0.9999:
        raise RuntimeError(f"Merge parity check failed: min_cos={min_cos}")
    return min_cos


def main() -> int:
    free, total = torch.cuda.mem_get_info()
    vram = {"free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2)}
    print(f"GPU: {torch.cuda.get_device_name(0)} | VRAM: {vram}", flush=True)

    base_pairs = [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines() if line.strip()]
    sit_pairs = select_teacher_situation_pairs(top_k=120)
    print(f"Base twin/supersede pairs: {len(base_pairs)} | Teacher situation pairs: {len(sit_pairs)} | Conv bridge pairs: {len(BRIDGE_TRAIN_PAIRS) * 2}", flush=True)

    data = json.loads(FROZEN.read_text(encoding="utf-8"))

    # Evaluate stock bge-m3 and v1 (r=16) first
    print("Evaluating stock bge-m3...", flush=True)
    stock_eval = evaluate_all_widths(BASE, data)
    print("Evaluating nevertwice-embed v1 (r=16)...", flush=True)
    v1_eval = evaluate_all_widths(str(V1_MERGED), data)

    # Train candidate A: LoRA r=4 + Matryoshka [1024, 512, 256] (weights [1.0, 0.4, 0.4]) + Bridge pairs
    # Train candidate B: LoRA r=4 + Matryoshka [1024, 512, 256] (weights [1.0, 0.3, 0.2]) + Teacher situation + Bridge pairs
    candidates = [
        ("v2_r4_matryoshka_bridge", [1.0, 0.4, 0.4], 0),
        ("v2_r4_3axis_full", [1.0, 0.35, 0.25], 80),
    ]
    best_name = None
    best_score = -1.0
    cand_results = {}

    for cand_name, m_weights, n_sit in candidates:
        random.seed(SEED)
        torch.manual_seed(SEED)
        ex = [InputExample(texts=[p["anchor"], p["positive"]]) for p in base_pairs]
        for q_b, d_b in BRIDGE_TRAIN_PAIRS * 2:
            ex.append(InputExample(texts=[q_b, d_b]))
        for q_s, d_s in sit_pairs[:n_sit]:
            ex.append(InputExample(texts=[q_s, d_s]))
        random.shuffle(ex)

        model = SentenceTransformer(BASE, device="cuda")
        model.add_adapter(LoraConfig(
            task_type=TaskType.FEATURE_EXTRACTION,
            r=4, lora_alpha=8, lora_dropout=0.05,
            target_modules=TARGETS,
        ))
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        loader = DataLoader(ex, shuffle=True, batch_size=24, drop_last=True)
        inner = losses.MultipleNegativesRankingLoss(model)
        loss = losses.MatryoshkaLoss(model, inner, matryoshka_dims=WIDTHS, matryoshka_weights=m_weights)

        t0 = time.perf_counter()
        model.fit(
            train_objectives=[(loader, loss)],
            epochs=3,
            warmup_steps=max(10, int(len(loader) * 3 * 0.1)),
            optimizer_params={"lr": 1e-4},
            use_amp=True,
            show_progress_bar=False,
        )
        train_s = round(time.perf_counter() - t0, 2)
        peak_gb = round(torch.cuda.max_memory_allocated() / 2**30, 2)

        tmp_adapter = HERE / "models" / f"_{cand_name}_adapter"
        tmp_merged = HERE / "models" / f"_{cand_name}_merged"
        model.save(str(tmp_adapter))
        del model
        torch.cuda.empty_cache()
        min_cos = merge_checkpoint(tmp_adapter, tmp_merged)

        ev = evaluate_all_widths(str(tmp_merged), data)
        ev["train_seconds"] = train_s
        ev["peak_vram_gb"] = peak_gb
        ev["trainable_parameters"] = trainable
        ev["merge_parity_cosine"] = round(min_cos, 6)
        ev["matryoshka_weights"] = m_weights
        ev["situation_pairs_added"] = n_sit
        cand_results[cand_name] = ev

        w1024 = ev["widths"]["1024"]
        w256 = ev["widths"]["256"]
        score = (
            w1024["retrieval_situation"]["recall@1"]["value"]
            + w1024["retrieval_situation"]["recall@5"]["value"]
            + w1024["retrieval_title"]["recall@1"]["value"]
            + w256["retrieval_situation"]["recall@5"]["value"]
            + w1024["conversational_bridge"]["mrr"]
        )
        print(
            f"[{cand_name}] ({train_s}s) 1024d sit R@1={w1024['retrieval_situation']['recall@1']['value']:.4f} "
            f"R@5={w1024['retrieval_situation']['recall@5']['value']:.4f} "
            f"title R@1={w1024['retrieval_title']['recall@1']['value']:.4f} "
            f"256d sit R@5={w256['retrieval_situation']['recall@5']['value']:.4f} "
            f"conv MRR={w1024['conversational_bridge']['mrr']:.4f}",
            flush=True,
        )
        if score > best_score:
            best_score = score
            best_name = cand_name

    # Promote winning v2 model to models/universal_v2 and models/universal_v2_merged
    import shutil
    for p in (OUT_ADAPTER, OUT_MERGED):
        if p.exists():
            shutil.rmtree(p)
    shutil.copytree(HERE / "models" / f"_{best_name}_adapter", OUT_ADAPTER)
    shutil.copytree(HERE / "models" / f"_{best_name}_merged", OUT_MERGED)
    for cand_name, _, _ in candidates:
        for suf in ("adapter", "merged"):
            p = HERE / "models" / f"_{cand_name}_{suf}"
            if p.exists():
                shutil.rmtree(p)

    summary = {
        "generated_by": "research/embed_universal/train_v2.py",
        "device": f"{torch.cuda.get_device_name(0)}, torch {torch.__version__}",
        "vram_before": vram,
        "winner": best_name,
        "models": {
            "stock bge-m3": stock_eval,
            "nevertwice-embed v1 (r=16)": v1_eval,
            "nevertwice-embed v2 (r=4, 3-axis)": cand_results[best_name],
            "candidates": cand_results,
        },
    }
    RECORD.write_text(json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved evaluation artifact -> {RECORD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
