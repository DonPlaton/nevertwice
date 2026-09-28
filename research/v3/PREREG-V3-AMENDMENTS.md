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
