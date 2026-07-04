# research/data: external datasets (not committed)

The retrieval benchmarks read one external file from this directory. It is **not** committed
(it's large and third-party, see `.gitignore`); download it once and drop it here.

## LongMemEval-oracle

`research/longmem_eval.py` and `embedder_ab.py` expect:

```
research/data/longmemeval_oracle.json
```

**LongMemEval** (Wu et al., *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive
Memory*, ICLR 2025) is released by the authors. Get the dataset from the official repository,
which links the current download (Google Drive / HuggingFace mirror):

- Official repo + download instructions: <https://github.com/xiaowu0162/LongMemEval>

Use the **oracle** variant and save/rename it to `longmemeval_oracle.json` in this directory.
The harness reads each entry's `haystack_session_ids` / `haystack_sessions` (the shared pool)
and `answer_session_ids` (the human-annotated evidence, the external ground truth).

Then run:

```bash
python research/longmem_eval.py --embed     # embed pool+questions once (writes the caches below)
python research/longmem_eval.py             # report recall@k (fast)
python research/longmem_eval.py --xrerank   # + the trained cross-encoder ([reranker] extra, GPU)
python research/embedder_ab.py              # A/B local embedders (each pulled in Ollama)
```

## Generated caches (also ignored)

`--embed` writes per-embedder vector caches here (`longmem_embeds*.json`) so re-ranking is instant.
Delete them to force a re-embed. None of these are committed.
