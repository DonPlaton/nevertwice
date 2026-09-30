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
