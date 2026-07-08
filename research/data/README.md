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

## Reproducibility of the head-to-head

`research/head_to_head.py` installs each competitor's own package and runs it on this same pool
with the same local `bge-m3` embedder. Because results move with a competitor's version, the
runner **records the exact installed version** of every system it compared against (via
`importlib.metadata`) into `head_to_head.json` and the printed report, alongside the Python and
OS it ran on. So a published table is always traceable to the versions that produced it rather than
to whatever happened to be on the machine. Pin those versions when you cite a number.

## Generated caches (also ignored)

`--embed` writes per-embedder vector caches here (`longmem_embeds*.json`) so re-ranking is instant.
Delete them to force a re-embed. None of these are committed.

## Derived caches

Everything else that used to sit here (embedding caches `longmem_embeds*.json`, the
`_rnd_*.npy` matrices, SPLADE sparse vectors, LLM answer caches) is derived: the harnesses
rebuild each artifact on first run (`rnd_launch.py --build`, the eval scripts' cache paths).
They are gitignored so the repo stays a few megabytes instead of a hundred.
