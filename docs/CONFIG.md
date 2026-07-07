# Configuration reference

**You do not need this file to run Nevertwice.** Everything auto-detects (see
[QUICKSTART.md](../QUICKSTART.md)); `install.py` prints the backend it chose. This page
is the full list of knobs for when you want to tune something. Every one is optional and
has a sensible default.

Set any of these in your shell environment or in a `.env` file next to the package /
at the repo root (see [`.env.example`](../.env.example)). `NEVERTWICE_*` is the canonical
prefix; the legacy `CLAUDE_MEMORY_*` names are still read for back-compat.

> Privacy note: `NEVERTWICE_LOCAL_ONLY` / `NEVERTWICE_CLOUD_ONLY` decide which projects may
> ever touch a cloud backend. They gate **both** extraction and the cloud embedder. See
> [Privacy & data routing](#privacy--data-routing).

---

## The ten you might actually touch

These are the only vars in [`.env.example`](../.env.example). Most people set zero of them.

| Variable | Default | What it does |
|---|---|---|
| `NEVERTWICE_CLOUD` | `auto` | Cloud extraction backend: `cerebras` / `groq` / `deepseek` / `gemini` / `none` / `auto` (picks whichever key is present, else local Ollama). |
| `CEREBRAS_API_KEY` · `GROQ_API_KEY` · `DEEPSEEK_API_KEY` · `GEMINI_API_KEY` | n/a | One key enables fast off-GPU extraction. None → local Ollama. |
| `NEVERTWICE_HOME` | `~/.nevertwice` | Where the Markdown + Git store lives. |
| `NEVERTWICE_PROJECTS_ROOT` | `~/.claude/projects` | Host-agent transcript dir for the catch-up sweep. |
| `NEVERTWICE_PROJECT_ROOTS` | n/a | Extra roots whose git repos are tracked as projects (`os.pathsep`-separated). |
| `NEVERTWICE_EMBED_PROVIDER` | `ollama` | Embedder for semantic recall: `ollama` (local) / `openai` / `voyage` / `cohere` / `gemini`. |
| `NEVERTWICE_EMBED_MODEL` | per-provider | Override the embedding model (e.g. `text-embedding-3-small`). |
| `OPENAI_API_KEY` · `VOYAGE_API_KEY` · `COHERE_API_KEY` | n/a | Key for the matching cloud embedder (Gemini reuses `GEMINI_API_KEY`). |

Everything below is **advanced**: rarely needed, safe to ignore.

---

## Extraction backends (cloud LLM, with local Ollama fallback)

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_CEREBRAS_MODEL` | `gpt-oss-120b` | Cerebras model. |
| `NEVERTWICE_GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model. |
| `NEVERTWICE_DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek model. |
| `NEVERTWICE_GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model. |
| `CEREBRAS_URL` · `GROQ_URL` · `DEEPSEEK_URL` · `GEMINI_URL` | provider default | Override the API endpoint (self-host / proxy). |
| `NEVERTWICE_GEMINI_TIMEOUT` / `_RETRIES` / `_BACKOFF` | `60` / `2` / `2.0` | Gemini HTTP retry policy. |

## Local models (Ollama)

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_MODEL` | `qwen3:8b` | Local extraction model (fallback, or primary if no cloud key). Bigger public tags (`qwen3:14b`, `qwen3:30b-a3b`, `qwen3:32b`) extract better with more VRAM. |
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | Generation endpoint. |
| `OLLAMA_EMBED_URL` | `http://127.0.0.1:11434/api/embed` | Embedding endpoint. |
| `OLLAMA_TAGS_URL` | `http://127.0.0.1:11434/api/tags` | Liveness/model-list endpoint. |
| `NEVERTWICE_TIMEOUT` / `_RETRIES` / `_RETRY_BACKOFF` | `120` / `2` / `1.5` | Ollama call retry policy. |

## Embedding / semantic recall

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_EMBED_BASE_URL` | n/a | Any OpenAI-compatible `/v1/embeddings` host (together / deepinfra / localai). |
| `NEVERTWICE_EMBED_TIMEOUT` | `20` | Per-embed HTTP timeout (s). |
| `NEVERTWICE_EMBED_PREFIX` | `0` | Enable nomic-style task prefixes (asymmetric embedders). |
| `NEVERTWICE_EMBED_DOC_PREFIX` | `search_document: ` | Doc-side prefix when enabled. |
| `NEVERTWICE_EMBED_QUERY_PREFIX` | `search_query: ` | Query-side prefix when enabled. |
| `NEVERTWICE_EMBED_QUANT` | n/a | `binary` packs the scale-index as 1-bit sign codes: 16x smaller than the float16 default, ~lossless recall (R@5 0.802 to 0.796 on LongMemEval), and a popcount scan that stays instant into six figures of notes. For very large vaults. The float32 cache is unchanged; switching just rebuilds the index. See `research/QUANTIZATION.md`. |

After switching provider/model, re-embed once: `python nevertwice/embed_index.py --rebuild`.
The cache self-invalidates: recall stays on lexical until the rebuild, never wrong.
After setting `NEVERTWICE_EMBED_QUANT`, rebuild the index once the same way.

## Retrieval & ranking

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_FUSION` | `calibrated` | The shipped ranker: calibrated score fusion (z-normalise each signal, combine magnitudes). `rrf` falls back to reciprocal rank fusion. See `research/RETRIEVAL_FUSION.md`. |
| `NEVERTWICE_FUSION_SEM_WEIGHT` | `0.5` | Dense (semantic) weight in calibrated fusion. Measured Pareto-optimal on LongMemEval. |
| `NEVERTWICE_RECUR_FUSION_BOOST` | `0.02` | Recurrence tiebreak scaled to the calibrated (0,1) score range (inert on a no-recurrence corpus). |
| `NEVERTWICE_RANKER` | `hybrid` | Legacy RRF path / signal selector: `hybrid` (RRF) / `semantic` / `lexical`. Only active when `NEVERTWICE_FUSION=rrf` or `posterior`. |
| `NEVERTWICE_SEM_WEIGHT` | `2.0` | Semantic weight in the **RRF** fusion (distinct from `NEVERTWICE_FUSION_SEM_WEIGHT`). |
| `NEVERTWICE_SIM_FLOOR` | `0.40` | Min cosine to consider a semantic hit. |
| `NEVERTWICE_RETRIEVAL_K` | `5` | Candidates returned for on-demand search. |
| `NEVERTWICE_RETRIEVAL_EMBED_TIMEOUT` | `5` | Embed timeout during retrieval (s). |
| `NEVERTWICE_CONF_FLOOR` | `0.6` | Abstention floor: below this, "no confident match". |
| `NEVERTWICE_CONFIDENT_MARGIN` | `0.15` | Margin between top-1 and top-2 to call a result confident. |
| `NEVERTWICE_NEAR_FLOOR` | `0.15` | Near-duplicate / proximity floor. |
| `NEVERTWICE_AMBIGUITY_K` | `15` | Pool size used to judge query ambiguity. |
| `NEVERTWICE_RERANK` | `0` | Cloud-judge rerank for on-demand search (opt-in). |
| `NEVERTWICE_RERANK_POOL` | `15` | First-stage pool size fed to the reranker. |
| `NEVERTWICE_XRERANK` | `0` | Trained cross-encoder rerank (opt-in; needs `[reranker]` extra). |
| `NEVERTWICE_XRERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder model. |
| `NEVERTWICE_XRERANK_MAXLEN` | `512` | Cross-encoder max sequence length. |

## Recurrence / salience prior

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_ADAPTIVE_RECUR` | `1` | Adaptive recurrence scaling (inert on a no-recurrence corpus). |
| `NEVERTWICE_RECUR_BOOST` | `0.03` | Score boost per recurrence. |
| `NEVERTWICE_RECUR_RRF_BOOST` | `0.0003` | Recurrence boost inside RRF. |
| `NEVERTWICE_RESOLVED_WEIGHT` | `0.6` | Down-weight for already-resolved (superseded) notes. |

## Recall on each prompt (task-aware)

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_PROMPT_RECALL` | `1` | Recall relevant lessons on every prompt. |
| `NEVERTWICE_PROMPT_RECALL_MODE` | `smart` | `smart` / `once` / `every`. |
| `NEVERTWICE_PROMPT_RECALL_K` | `3` | Lessons injected per prompt. |
| `NEVERTWICE_PROMPT_RECALL_MAX` | `6` | Hard ceiling per session. |
| `NEVERTWICE_PROMPT_RECALL_MIN_CHARS` | `16` | Skip recall for trivially short prompts. |
| `NEVERTWICE_PROMPT_RECALL_ALIVE_TIMEOUT` | `1` | Embedder liveness ping budget (s). |
| `NEVERTWICE_PROMPT_RECALL_EMBED_TIMEOUT` | `2` | Embed budget per prompt (s). |

## Injection / context budget

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_INJECT` | `1` | Inject the project card + lessons at SessionStart. |
| `NEVERTWICE_INJECT_BUDGET_CHARS` | `2200` | Char budget for injected memory. |
| `NEVERTWICE_PROJECT_CARD` | `1` | Maintain & inject the per-project card. |
| `NEVERTWICE_CARD_MAX_ITEMS` | `5` | Max items per card section. |
| `NEVERTWICE_CONTEXT_MAX_BYTES` | `12000` | Byte cap for `Context/<project>.md`. |
| `NEVERTWICE_CONTEXT_KEEP_RECENT` | `12` | Recent entries kept verbatim before compaction. |
| `NEVERTWICE_CONTEXT_KEEP_MIN` | `3` | Minimum entries always kept. |
| `NEVERTWICE_CONTEXT_LINKS_MAX` | `60` | Max `[[wikilinks]]` tracked per context. |
| `NEVERTWICE_USER_MODEL` | `1` | Maintain the cross-project user profile. |

## Forgetting / retention

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_ARCHIVE_DAYS` | `30` | Move Sessions/ notes to Archive after N days. |
| `NEVERTWICE_TYPED_ARCHIVE_DAYS` | `90` | Archive typed notes (patterns/mistakes/decisions) after N days. |
| `NEVERTWICE_PRUNE_DAYS` | `90` | Prune horizon for stale candidates. |
| `NEVERTWICE_DECAY_HALFLIFE` | `365` | Salience half-life (days). |
| `NEVERTWICE_DECAY_FLOOR` | `0.5` | Minimum decayed salience. |
| `NEVERTWICE_MAX_LIVE_PER_PROJECT` | `0` | `0`=off; >0 archives lowest-salience excess (submodular cap). |

## Cross-project transfer

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_CROSS_PROJECT` | `1` | Surface lessons from other projects. |
| `NEVERTWICE_CROSS_K` | `2` | Max cross-project lessons. |
| `NEVERTWICE_CROSS_SIM_FLOOR` | `0.5` | Min similarity for a cross-project hit. |

## Scaling (large stores)

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_PREFILTER_LIMIT` | `600` | Past this many candidates, FTS-prefilter then cosine-rerank the top; bounds per-prompt cost. |
| `NEVERTWICE_GRAPH_HOPS` | `0` | Multi-hop graph expansion over `[[wikilinks]]` (0 = off). |
| `NEVERTWICE_RELATION_EXPAND` | `0` | Append up to N graph-connected lessons (reached by the top hits' typed relation edges) to the **SessionStart** card, so a bug also carries its fix. 0 = off (keeps injection precise + token-lean); never runs on the per-prompt path. See `docs/INTEGRATIONS.md` (entity graph). |

## Capture / sweep / ingest

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_AGENT` | `claude-code` | Agent label stamped on captured notes. |
| `NEVERTWICE_TRACK_ANY_PROJECT` | `1` | Track any git repo you work in, beyond the configured roots. |
| `NEVERTWICE_MAX_TRANSCRIPT` | `12000` | Max transcript chars sent to extraction. |
| `NEVERTWICE_MAX_SWEEP_BYTES` | `10485760` | Per-file cap for the `--dir` sweep / `watch` (DoS guard). |
| `NEVERTWICE_SWEEP_DAYS` | `30` | Only sweep transcripts modified in the last N days. |
| `NEVERTWICE_SWEEP_CAP` | `8` | Max transcripts processed per SessionStart catch-up. |
| `NEVERTWICE_SWEEP_CAP_END` | `25` | Max transcripts processed per SessionEnd catch-up. |
| `NEVERTWICE_WATCH_MAX_PER_CYCLE` | `40` | Max transcripts the `watch` daemon mines per poll cycle. |
| `NEVERTWICE_TRUNCATE_HEAD_FRAC` | `0.4` | Fraction of a truncated transcript kept from the head (rest from the tail). |
| `NEVERTWICE_TRUNCATE_HEAD_CHARS` | n/a | Absolute head-char override for `truncate_smart` (wins over the fraction). |
| `NEVERTWICE_ENV_FILE` | n/a | Custom `.env` location (otherwise package/repo-root only). |

## Graph (`graph.json` for code navigation)

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_PROJECT_ROOT` | (cwd) | Root the graph generator scans. |
| `NEVERTWICE_GRAPH_MAX_FILES` | `800` | File cap for graph generation. |
| `NEVERTWICE_GRAPH_MAX_BYTES` | `120000` | Byte cap for `graph.json`. |

## Active memory (guards / anticipation)

The active layer is stdlib-only and needs no model on the hot path, so it works fully on a weak
machine and against a cloud coding agent.

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_GUARD_PACK` | `0` | `1` installs the **universal guard pack** at consolidation: high-precision, almost-always-a-smell pitfalls (eval, `shell=True`, `verify=False`, `pickle.loads`, `== None`, bare `except`, `yaml.load`, weak hashes, …) that fire from the first session with no history and no model. Advisory-only and never promotes to blocking. Add anytime with `python -m nevertwice.guards pack`. |
| `NEVERTWICE_GUARD_PROMOTE` | `3` | Distinct-session corroborations before an advisory guard earns `blocking` (pack guards never promote). |
| `NEVERTWICE_GUARD_RETIRE` | `3` | False positives before a guard demotes / self-retires. |
| `NEVERTWICE_GUARD_ENFORCE` | `0` | `1` lets a `blocking` guard actually deny the PreToolUse edit; default only warns (advisory). |
| `NEVERTWICE_ANTICIPATE_TAU` | `0.22` | Min trajectory-resemblance risk before anticipation surfaces one warning (silent below). |

## Privacy & data routing

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_LOCAL_ONLY` | n/a | Comma-list of projects that must NEVER use a cloud backend (extraction **and** embedder). |
| `NEVERTWICE_CLOUD_ONLY` | n/a | Fail-safe allowlist: if set, only these projects may use the cloud; every other (incl. unknown) stays local. Takes precedence over `LOCAL_ONLY`. |
| `NEVERTWICE_QUARANTINE` | `0` | Opt-in corroboration quarantine for multi-tenant stores. |
| `NEVERTWICE_QUARANTINE_CONF` | `0.95` | Confidence required to auto-release from quarantine. |

## Sync

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_GIT_PUSH` | `0` | Push the store to a remote after each commit (cross-machine sync). |

## Research / experimental

| Variable | Default | Notes |
|---|---|---|
| `NEVERTWICE_DIVERGENCE` | `0` | Divergent-retrieval experiment (off). |
| `NEVERTWICE_STALE_CHECK` | `0` | Periodic stale-note check (off by default). |
| `NEVERTWICE_DEDUP_SIM` | `0.92` | Cosine threshold for the weekly consolidation merge (sleep-time dedup). |
| `NEVERTWICE_POST_W_REL` / `_FREQ` / `_SAL` | `1.0` / `0.3` / `0.2` | Weights for the opt-in posterior ranker (`NEVERTWICE_RANKER=posterior`). |

---

*Generated against the codebase on 2026-06-20. If you find a knob that isn't here, it's
experimental and unsupported; open an issue.*
