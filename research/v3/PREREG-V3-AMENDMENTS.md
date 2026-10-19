# PREREG-V3 amendments

Revision 1 of PREREG-V3 (research/v3/PREREG-V3-rev1.md) is frozen: the A3 freeze fragment records its sha256, and it
is never rewritten. An amendment found after it lives here, beside it (the auditor's Q-C5e-3, 2026-09-28). Each one
carries its id, its trap number, its date, the ruling that made it, its reason in one line, and the amended text word
for word. FREEZE-V3 (A10) pins revision 1 and this file together, by the sha256 of each, and E5 lists every amendment.

## A1 - T31: graphiti with its `[falkordb]` extra

- **Trap:** T31 graphiti's FalkorDB client.
- **Date:** 2026-09-28.
- **Ruling:** the auditor's Q-C5e-2 (2026-09-28, 11:13), found as B-GRAPHITI-EXTRA before any window.
- **Reason:** graphiti with its [falkordb] extra: the client its FalkorDB driver imports (arm_graphiti.py:162),
  vendor-documented; without it the arm cannot start.
- **§2.2, the zep-graphiti row, amended** (revision 1's row plus exactly the text `with `[falkordb]` (T31); `):

| zep-graphiti | product | graphiti-core, latest stable at freeze (0.30.2 read 2026-09-26); with `[falkordb]` (T31); FalkorDB image by digest | 3.12, fresh venv graphiti_v3 | vendor-recommended:https://github.com/getzep/graphiti | to install, to adapt |

- **§13, trap closure, the new line:**

| T31 graphiti's FalkorDB client | §2.2 | `[falkordb]`; the client and its driver imported and version-checked at install |

## A2 - B-OLM-VIS: the Ollama leg's refusals are a P0h counter

- **Trap:** none new; the boundary rule P0h (§2.3, §2.6.7, §10 P0 h).
- **Date:** 2026-09-28.
- **Ruling:** the auditor's Q-OLMENC-1 and B-OLM-VIS (2026-09-28, 15:37) and Q-VIS-1 O-a (2026-09-28, 16:05).
- **Reason:** the proxy's Ollama leg refuses a product's call only where the call is unsafe; a refused call changes what
  the product does, so it is counted and the row is not clean - before, the refusal was a line in ollama.jsonl that no
  check read.
- **§2.3, the boundary line, amended** (revision 1's line with `ollama_refused` added to the set, and one sentence
  after it):

  - **boundary (m5, P0h):** {canary_hits, owner_marker_hits, egress_hits, fs_hits, ollama_refused}, each 0. Also
    published: ancestor_canary_hits, and egress_attempts (the catcher's refused requests, by host). ollama_refused
    counts the arm-run's Ollama-leg records whose error is a refusal (refused:path, refused:encoded-target); a record
    with no <run>.<unit> prefix counts in each of the arm's runs; absent is not measured, never 0.

- **§2.6.7 Proxy boundary, the new bullet:**

  - the Ollama leg forwards only Ollama's generation, embed and read-only listing paths - the generation and embed
    paths are the pacer's own lists - compared exactly after /u/<run>.<unit>. A target with "%" or a query is refused
    (400) before routing, any other path (403), the model store among them; each refusal is a line in ollama.jsonl,
    written before the answer, and counted as ollama_refused.

- **§10 P0 h), the boundary counter line, amended:**

  - any boundary counter > 0: canary_hits, owner_marker_hits, egress_hits or fs_hits (in A9, or in block 1 of the
    stand); ollama_refused > 0 in any arm-run.

## A3 - T32: supermemory-local is the vendor's release binary, not an npm package

- **Trap:** T32 supermemory-local's distribution channel.
- **Date:** 2026-09-29.
- **Ruling:** the auditor's Q-A8-9 O-c and Q-A8-10 (2026-09-29), from windows a7-npm-d d1 (the npm registry answers 404
  for supermemory-server), a7-discovery d1 (supermemoryai/supermemory: GitHub releases "supermemory-server X.Y.Z", the
  newest stable server-v0.0.8 of 2026-08-17, tag commit 5d2b5855fe492a3682a1cde4a255e2db0c4db595) and a7-docs d1 (the
  vendor's self-hosting documentation read at that tag commit).
- **Reason:** revision 1 named the right product and version, supermemory-server 0.0.8, on the wrong channel: it is not
  published to npm; the vendor ships it as a single self-contained binary per platform on its GitHub release, with an
  installer script, and documents its configuration by environment variables. No Node runtime is involved.
- **Sources read (a7-docs d1, each checked against its git blob at the tag commit):**
  apps/docs/self-hosting/overview.mdx sha256 f0682e3b2254f94e3029bef8112809ba56e4e1211cab1162228f39c05b57e800,
  quickstart.mdx 5fea333cc119bd85792c17dcfda54cf83ec007e96b12bfe0aad50e1cf106d988,
  configuration.mdx a357a6b74160cb048ce70430a84415712294e2b3f020b6e8aee61cc1d5a51b65,
  embeddings.mdx 577898f689522ae28cb75ac891ea01c0011ae454d327f2a1fde4279f2d496a04,
  providers.mdx c59b25fde70882ba6fb0707a4b13063f15265b16b4ae8185c891da363812b29a;
  the release server-v0.0.8's asset list with digests (d4_report.json sha256
  492f92b77098b80eeca7494bb3550a3988aa596873850ca1d1f269bae6a9fcd8).
- **§2.2, the supermemory-local row, amended** (the channel and the runtime; the rest of revision 1's row unchanged):

| supermemory-local | product | supermemory-server 0.0.8 (T32): the vendor's binary supermemory-server-windows-x64.exe from the GitHub release server-v0.0.8 (tag commit 5d2b5855fe492a3682a1cde4a255e2db0c4db595), its sha256 checked against the release's asset digest and its .sha256 asset, under §2.6; no Node. A separate arm from hosted Supermemory (T8) | a native binary; its version and sha256 recorded | vendor-recommended:https://github.com/supermemoryai/memorybench | to install after the A2 gate |

- **§2.2, the supermemory-local block (revision 1, line 392), the Server bullet amended:**

  - **Server:** the release binary of §2.2 (T32) under §2.6, started without a TTY (the vendor's documentation: no setup
    wizard without a TTY; configuration by environment only).
    - OPENAI_BASE_URL = its proxy port's OpenAI-compatible base (/v1), OPENAI_API_KEY = the arm's proxy token, and
      OPENAI_MODEL=deepseek-flash; OPENAI_FAST_MODEL and OPENAI_TEXT_MODEL unset (the vendor's default: OPENAI_MODEL).
    - SUPERMEMORY_EMBEDDING_PROVIDER=openai with SUPERMEMORY_EMBEDDING_BASE_URL at the proxy's Ollama leg (/v1),
      SUPERMEMORY_EMBEDDING_MODEL = the v3 tag and SUPERMEMORY_EMBEDDING_DIMENSIONS=1024 - locked at first boot (the
      vendor: a dimension mismatch with stored vectors refuses to boot).
    - SUPERMEMORY_DATA_DIR = the server's own directory under the run, PORT = its own port; every other variable at the
      vendor's default (SUPERMEMORY_EMBEDDING_RAM_LIMIT 1gb, SUPERMEMORY_INGEST_CONCURRENCY 2, telemetry unset).
    - Thinking: §2.2.1.

- **§13, trap closure, the new line:**

| T32 supermemory-local's channel | §2.2 | the release binary by its digest; no Node; env-only configuration, no TTY |

- **Open, measured in the A8 probe before the pilot (not facts of this amendment):** (a) whether the binary dials out at
  start - the vendor's quickstart says it "may also print an update available notification on startup", and names no
  switch; §2.6.8 holds: the catcher refuses, egress_attempts records it, and a ruling follows before the pilot;
  (b) that the Windows binary runs here - the tag's quickstart lists macOS and Linux for its installer, while the release
  carries supermemory-server-windows-x64.exe (server-v0.0.6: "add Windows self-hosted server support"); a binary that
  does not run is attempt 1 of §8, not a silent switch of platform; (c) temperature, read from the source at the tag
  commit (§5.5).

## A4 - T33: the scorer's own venv, scorer_v3

- **Trap:** T33 the scorer's packages.
- **Date:** 2026-09-30.
- **Ruling:** the auditor's Q-SCR-1..5 (2026-09-30), from the window a8-pypi-d p1 (d9_report.json
  49c3d7a042ee1576826a75d1ffb6f97be54ae8fb503330d561f1ad45e7266bd5, record.json
  942f82a1ea9942fbf3297bacef237f6e76396a06806cd3b012c7d44f2cadfac9).
- **Reason:** the pinned scorers need packages no arm venv carries - LoCoMo F1's nltk (Q-51-1), BEAM event ordering's
  alignment on all-MiniLM-L6-v2 (Q-49-3 O-b), scipy for the differential probes and tau_b - so they run in a venv of
  their own, never an arm's and never the runner's.
- **§2.2, after the arm table, the new paragraph:**

The scorer is not an arm. Its venv scorer_v3 (3.12, a fresh venv on the base py-base-312 p1) holds exactly
sentence-transformers==6.1.0, nltk==3.10.3, scipy==1.18.1 and torch==2.14.0 (the PyPI CPU wheel; no CUDA index is
declared), and the requirements sentence-transformers 6.1.0 declares without an extra: transformers==5.17.0,
huggingface-hub==1.33.0, tokenizers==0.23.2, numpy==2.5.3, scikit-learn==1.9.1, typing-extensions==4.16.0 and
tqdm==4.70.1 - each the newest stable release its specifier allows (huggingface-hub: 2.0.0 is newer, the specifier
<2.0.0,>=1.3.0 takes 1.33.0), read by the window a8-pypi-d p1. Every further package the lock resolves is recorded
with its wheel's sha256 by the lock window. The alignment runs on the CPU with one thread and the model's pinned files
offline (HF_HUB_OFFLINE=1); a unit scored twice gives the same bytes.

- **§13, trap closure, the new line:**

| T33 the scorer's packages | §2.2 | scorer_v3: the 11 versions of window a8-pypi-d p1; the lock's wheels by sha256; CPU, one thread, offline |

## A5 - T34: mem0_v3 carries what its selected paths import - ollama and fastembed

- **Trap:** T34 Mem0 without its Ollama client and without BM25.
- **Date:** 2026-09-30.
- **Ruling:** the auditor's Q-M0-OLLAMA = (a) and Q-M0-FE = (a) (2026-09-30), from M31 of the download a8-pypi-mem0_v3 d1
  (download_record.json e082e3952561adf70fa53204647825a2584d73b945a88b56a5a350d560ffc86c, m31.json
  ff00d5e8546ba888efea5bb0be7eb7cf9c91393386eff7993a9dfab43de38bc0: unprovided fastembed and ollama, both blocking).
- **Reason:** mem0ai[nlp]==2.2.0 provides neither. `ollama` is imported at module level by mem0/embeddings/ollama.py:7
  (the embedder of both arms) and mem0/llms/ollama.py:5 (mem0-store's LLM): without it the arm does not start. The
  vendor declares it as `ollama>=0.3.0; extra == 'llms'`. `fastembed` is imported by mem0/vector_stores/qdrant.py:97 for
  the `Qdrant/bm25` sparse encoder; without it mem0 2.2.0 logs a warning and drops BM25 from its hybrid score
  (mem0/utils/scoring.py: semantic + BM25 + entity, at most 2.5; without BM25, 1.5) - the product would run degraded
  and nothing would fail. The vendor declares it as `fastembed>=0.3.1; extra == 'extras'`. T22 gave Mem0 its spaCy for
  the same reason: the product runs as designed on the paths our config selects.
- **Rejected:** the vendor's whole extras `[llms]` and `[extras]` - they add litellm, groq, google-genai,
  google-generativeai, vertexai, together, boto3, elasticsearch, opensearch-py, langchain, langchain-core,
  langchain-community, sentence-transformers and transformers, integrations our config never selects, and they would
  turn the lazy langchain_core import at mem0/memory/main.py:3695 from unprovided into a live path no probe has run.
  Running without fastembed - a silent bias against the competitor (§3.4).
- **§2.2, the mem0 row becomes:**

| mem0 | product | mem0ai, latest stable at freeze, with `[nlp]` (T22); 2.2.0 on 2026-09-23; with ollama==0.6.3 and fastembed==0.8.1 (T34) | 3.12, fresh venv mem0_v3 | vendor-recommended:https://github.com/mem0ai/memory-benchmarks | to install |

- **§2.2, after the arm table, the new paragraph:**

Mem0's venv mem0_v3 holds, beside mem0ai[nlp]==2.2.0, exactly the two distributions the code its config selects imports
and the extra `[nlp]` does not bring: ollama==0.6.3 (the Ollama client of its embedder and of mem0-store's LLM; the
vendor's floor ollama>=0.3.0) and fastembed==0.8.1 (the BM25 encoder of its Qdrant store; the vendor's floor
fastembed>=0.3.1) - each the newest stable release, read by the auditor at pypi.org on 2026-09-30 (ollama 0.6.3 uploaded
2026-09-29, fastembed 0.8.1 uploaded 2026-09-22, neither yanked). Every further package the lock resolves is recorded
with its wheel's sha256 by the lock window. The files of the Hugging Face model Qdrant/bm25 that fastembed loads are
pinned by a window at revision 22b8d2af71a76161e18dd432d2cee0eefa66e412 and read offline (HF_HUB_OFFLINE=1) from a
unit-local copy. A mem0 unit counts its BM25: the encoder loaded, the collection has its `bm25` sparse slot,
keyword_search returned a result (not None) at least once, and none of the lines "fastembed not installed", "Failed to
load BM25 encoder" or "predates v3 hybrid search" in its log; a unit without all four is a failed unit, never a scored
one. The unit also counts its results with a BM25 score above zero; that count is published, never a reason to fail a
unit (a query can share no term with any memory). The vendor's own harness (mem0ai/memory-benchmarks at 4b61c5d)
installs mem0ai from a branch, feat/v3-pipeline, that no longer exists, and no fastembed; whether the vendor's published
numbers had BM25 is not known. Ours run release 2.2.0 as its code is built: a fresh collection with the `bm25` slot and
the encoder its warning asks for.

- **§13, trap closure, the new line:**

| T34 Mem0 without its Ollama client and without BM25 | §2.2 | mem0_v3 += ollama==0.6.3, fastembed==0.8.1; Qdrant/bm25 at 22b8d2af by windows, offline; a unit without BM25 fails |

## A6 - T35: the order of an arm's reads within an S1 unit

- **Trap:** T35 read order within a unit.
- **Date:** 2026-09-30.
- **Ruling:** the auditor's Q3 = (a) on the S1 pilot driver plan (2026-09-30); rev1 §5.2 does not state the order.
- **Reason:** S1 is read at three points (B, K and, where Row V gives it, V) from one store that the write stage built
  once. The reads of one unit run in one read child after the write stage, in one declared order, the same for every
  arm, so no arm's point sees a store another arm's point would not.
- **§5.2, after the paragraph on the read stage, the new paragraph:**

Within an S1 unit an arm's reads run in one read child, after its write stage, in the order B, K, V (V only where Row V
reads it), the same for every arm. B, the headline point, reads the store exactly as the write stage left it; K reads
it after B, and V after B and K. The pilot measures, for every arm, run and S1 unit, the store's digest before the
unit's first read and after its last (M34); a product whose store changes under its own reads is named, and before the
campaign either its points are read from a restored copy of the written store, one per point, or the change is a
declared line of the report beside its K and V numbers - never discovered afterwards.

- **§13, trap closure, the new line:**

| T35 read order within a unit | §5.2 | B, K, V in one read child, the same for every arm; M34 digest before/after; a store changed by reads is decided before the campaign |

## A7 - T36: supermemory-local's telemetry, update check and browser are off, as §2.6 sets for every child

- **Trap:** T36 supermemory-local reports and calls out.
- **Date:** 2026-09-30.
- **Ruling:** the auditor's Q-C1-AMD = (a) (2026-09-30), from the facts the reader sm_bundle takes from the pinned binary's
  bundle (a8-supermemory-bin b2, sha256 d8fb2ac0...; Q-A8S-SRC = (c)).
- **Reason:** A3 left the server's telemetry at the vendor's default ("telemetry unset"). The pinned binary shows what that
  default does: a PostHog heartbeat at start and every six hours, and, while its telemetry is on, the memory agent's
  model input and output - the ingested documents' text - sent to us.i.posthog.com; an update check against GitHub at
  start and daily; and a browser opened at start. §2.6 already sets telemetry and auto-update off for every child; the
  vendor's own switches do it for this server, and none of them changes what the server stores, retrieves or asks its
  model.
- **§2.6, the fixed list of "Telemetry and auto-update off" gains:** SUPERMEMORY_DISABLE_TELEMETRY=1,
  SUPERMEMORY_NO_UPDATE_CHECK=1, SUPERMEMORY_NO_OPEN=1 - set for every child like the rest of the list, each the value the
  server's own comparison accepts (the bundle's facts telemetry_switch, update_switch, browser_switch).
- **A3, the supermemory-local defaults:** "telemetry unset" is replaced by "telemetry, the update check and the browser off
  through §2.6's fixed list (A7)"; SUPERMEMORY_EMBEDDING_RAM_LIMIT and SUPERMEMORY_INGEST_CONCURRENCY stay the vendor's.

- **§13, trap closure, the new line:**

| T36 supermemory-local reports and calls out | §2.6 | SUPERMEMORY_DISABLE_TELEMETRY=1, SUPERMEMORY_NO_UPDATE_CHECK=1, SUPERMEMORY_NO_OPEN=1 in the fixed list for every child; the values the binary's own comparisons accept |

## A8 - T37: graphiti_v3 and langmem_v3 carry what their selected paths import - httpx and langchain-ollama

- **Trap:** T37 graphiti_v3 without httpx and langmem_v3 without langchain-ollama.
- **Date:** 2026-10-18.
- **Ruling:** M31 of the downloads a8-pypi-graphiti_v3 d1 and a8-pypi-langmem_v3 d1 (same pattern as A5/T34 for mem0_v3).
- **Reason:** graphiti-core[falkordb]==0.30.2 imports `httpx` at module level in `graphiti_core/llm_client/client.py:23`, while
  `openai` 2.x depends on `httpx2` and no longer pulls `httpx`; without `httpx==0.28.1` `graphiti_core` fails to import.
  `langmem==0.0.30` does not pull `langchain-ollama`, which `arm_langmem.py:130` imports for the shared Ollama embedder
  (`OllamaEmbeddings`) and for `langmem-store`'s Ollama LLM (`ChatOllama`); without `langchain-ollama==1.1.0` neither arm starts.
- **§2.2, the zep-graphiti and langmem rows become:**

| zep-graphiti | product | graphiti-core, latest stable at freeze (0.30.2 read 2026-09-26); with `[falkordb]` (T31) and httpx==0.28.1 (T37); FalkorDB image by digest | 3.12, fresh venv graphiti_v3 | vendor-recommended:https://github.com/getzep/graphiti | to install, to adapt |
| langmem | product | langmem, latest stable (0.0.30); with langchain-ollama==1.1.0 (T37); langgraph pinned | 3.12, fresh venv langmem_v3 | vendor-recommended:https://langchain-ai.github.io/langmem/guides/delayed_processing/ | to install, to adapt |

- **§13, trap closure, the new line:**

| T37 graphiti_v3 without httpx and langmem_v3 without langchain-ollama | §2.2 | graphiti_v3 += httpx==0.28.1; langmem_v3 += langchain-ollama==1.1.0; M31 verified before install |

## A9 - T38: claude-code-memory pinned to @anthropic-ai/claude-code-win32-x64 2.1.304 over the DeepSeek /anthropic proxy route

- **Trap:** T38 claude-code-memory binary, memory path override and tool-search beta.
- **Date:** 2026-10-20.
- **Ruling:** owner decision Q7 = (b) (2026-10-20) and A8 probe `runs/v3/_a8/cc_p2/claude-code-memory/probe.json`.
- **Reason:** `@anthropic-ai/claude-code` 2.1.304 ships its Windows binary in `@anthropic-ai/claude-code-win32-x64@2.1.304`
  (tarball `sha256: 422c20c6b5df262c53ca92e694278d8b4804be860313a9253673822e1d1d4152`, `package/claude.exe`
  `sha256: 019e5174d5d954585edb1a946fe280e44a973050b4897034adf0e16306f85fdd`). In `claude.exe` 2.1.304, the user-settings
  `autoMemoryDirectory` key is gated behind an online GrowthBook flag (`tengu_linked_cake`), whereas the environment variable
  `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` deterministically binds the auto-memory directory offline; and setting
  `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` disables the `DeferredToolPlaceholder` beta stub so the request offers only the
  allowed file tools (`Edit`, `Read`, `Write`). Routing `ANTHROPIC_BASE_URL` to the arm's proxy port
  (`/u/<run>.<unit>/anthropic`) with `MAX_THINKING_TOKENS=0` and the proxy's branch (b) fallback (`{"thinking": {"type": "disabled"}}`)
  runs `claude-code-memory` on the shared `deepseek-flash` writer model with `R-CC-WIT` (`x-nvt3-home-canary`) and `L3`
  verified.
- **§2.2, the claude-code-memory row becomes:**

| claude-code-memory | product | `@anthropic-ai/claude-code-win32-x64==2.1.304` (`claude.exe` `sha256: 019e5174d5...`) | polygon `claude_code_v3`, driven by `arm_claude_code.py` on `arms314` | https://code.claude.com/docs/en/memory | installed, adapted (`cc_p2` pass) |

- **§13, trap closure, the new line:**

| T38 claude-code-memory binary and offline memory path | §2.2, §2.6.6 | `@anthropic-ai/claude-code-win32-x64==2.1.304` in `claude_code_v3`; `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE=<unit>/store`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`, `MAX_THINKING_TOKENS=0`; `cc_p2` probe and `L3` passed |

## A10 - J1 judge model on cloud deepseek-flash via the recording proxy

- **Date:** 2026-10-20.
- **Ruling:** owner decision Q8 (2026-10-20): use cloud `deepseek-flash` (`deepseek-v4-1-flash-1017` via `api.deepseek.com`
  through the recording proxy, temperature 0, thinking disabled) for judge `J1` instead of the removed local Ollama tag
  `qwen3.6:27b`, keeping full proxy auditability and exact token/cost accounting.
- **§8.3, the J1 judge model:** `J1` runs on `deepseek-flash` via the recording proxy (`temperature: 0`,
  `thinking: {"type": "disabled"}`), with the same strict/lenient parsers and one identical re-ask rule (`Q-49-6`,
  `reader_judge.cloud_judge_request`).
