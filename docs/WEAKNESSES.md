# Nevertwice — weaknesses & limitations (hostile self-audit, 2026-06-17; launch update 2026-06-20)

*Written in the role of a harsh critic: dogfooded on a real 328-note / 12-project vault,
ran all 10 test suites, probed the new research features for dead code, token bloat, and
clutter. Findings are graded **[FIXED]** (closed this pass), **[OPEN]** (real, unsolved),
**[design]** (a trade-off worth naming), **[info]** (known/by-design). Fairness section first.*

## Launch-state update (2026-06-20)

This audit was written on 2026-06-17. Several items it lists as open were closed in the
rounds that followed (calibrated fusion, the trained cross-encoder, the watch daemon, the
head-to-head, binary quantization), shipped in **v1.0.0**. The corrections, so the body
below is read in context:

- **Default ranker is now calibrated score fusion, not RRF.** Mentions of "hybrid RRF" as
  the default below are stale. Calibrated fusion lifted the default from R@1 0.42 / R@5 0.66
  to **0.55 / 0.80** and now leads Mem0 / LangMem / A-MEM on a shared local stand. RRF is a
  fallback (`NEVERTWICE_FUSION=rrf`). See `research/RETRIEVAL_FUSION.md`.
- **W4 (no reranker) is addressed.** A purpose-trained cross-encoder (bge-reranker-v2-m3)
  ships as an opt-in second stage: recall@1 0.55 to **0.61**, MRR +0.06 on LongMemEval. Off
  by default to keep the stdlib core dependency-free; `backend_report()` now surfaces it when
  torch is present, so it is discoverable rather than hidden.
- **W2 (embedding-compression ceiling) is mitigated, not eliminated.** The lever that closes
  most of it (the cross-encoder above) now exists, and calibrated fusion raised the default
  floor. The pure-stdlib bi-encoder default still has the ceiling, which is a deliberate
  no-dependency trade, not an unsolved bug. SPLADE learned-sparse was measured this round and
  loses to BM25 (and needs torch), so it was not adopted.
- **"Automatic capture is Claude-Code-only" is outdated.** `nevertwice watch` (a stdlib polling
  daemon) plus `ingest` now give always-on auto-capture for Codex / Cline / Roo / Aider /
  Gemini-CLI, and an MCP server covers any MCP client. Hooks remain the zero-latency path for
  Claude Code.
- **W13 (only one external benchmark) is updated.** There is now a full local head-to-head
  against Mem0, LangMem, and A-MEM on one shared embedder (`research/head_to_head.py`), and a
  second external axis: **end-to-end answer-accuracy** on standard LongMemEval-oracle
  (`research/qa_eval.py` → `QA_ACCURACY.md`) — 0.788 with an open reasoning reader, decomposed
  by a reader sweep that localizes the gap to vendor headlines as reader strength, not memory.
- **Scale:** opt-in 1-bit index quantization (`NEVERTWICE_EMBED_QUANT=binary`) plus a popcount
  scan reach six figures of notes with no ANN dependency (`research/QUANTIZATION.md`).

### The four genuinely-remaining items and their disposition

1. **Default-path precision ceiling (W2/W4).** Closed as far as the design allows: the
   cross-encoder lever is shipped and discoverable; the stdlib default sits at its measured
   bi-encoder-fusion ceiling. No stdlib magic fix exists (PRF, structure-aware spreading,
   LLM-rerank, agreement bonuses, robust normalization were all measured and rejected).
2. **Plausible single-source false fact (W7).** Irreducible without external ground truth. A
   non-issue under the single-user threat model (you own every session); the opt-in
   corroboration quarantine covers the multi-tenant case; provenance (`sources`, `confidence`)
   is stored so a note's support is auditable. Documented as an accepted boundary.
3. **Entity-typed knowledge graph. CLOSED (2026-06-20).** The one real feature gap, now shipped
   in two phases with no database and no embedder (the graph reads straight from note frontmatter).
   **Phase 1:** the extraction LLM tags each lesson with its key entities (tools/concepts/files),
   stored normalised, giving faceted recall and co-occurrence (`notes_for_entity`, `co_occurring`,
   `entity_graph`). **Phase 2:** lessons also carry typed relation edges (`caused-by`, `fixed-by`,
   `depends-on`, ...), so the graph is traversable: `related_by(entity, rel)` returns typed edges
   and each target is itself an entity, so multi-hop works (`cuda --fixed-by--> grad-checkpointing
   --requires--> pytorch`); `relation_graph` is the overview. All of it is on every surface: the
   Python API, the CLI (`--entity` / `--entities` / `--relations`), and the `memory_entities` MCP
   tool. **Phase 2b:** the edges feed retrieval — `recall(expand_relations=True)` (CLI
   `--expand-relations`, MCP flag) appends the lessons the hits' edges reach, so a query about a bug
   surfaces its fix even with no shared words; off by default, each addition tagged with `via`. The
   graph also **exports** to Mermaid / DOT / JSON (`graph_export`, CLI `--graph`) for a visual that
   renders straight in an Obsidian or GitHub markdown block, and can enrich the **SessionStart** card
   itself via the opt-in `NEVERTWICE_RELATION_EXPAND` (SessionStart only, budget-gated, never the
   per-prompt path). The whole feature lives in its own `nevertwice/graph.py`. 60 regression tests
   (`_test_entities.py`); a hostile multi-pass audit fixed an O(E×N) rescan and confirmed the hot-path
   discipline. This matches the entity/relation extraction the leaders use, but over markdown with zero
   new dependency. **Brain layer (2026-06-22):** an opt-in `research`/`general` profile builds on this
   graph — first-class typed entities over a wide research ontology (paper/method/architecture/dataset/
   benchmark/metric/…), per-entity **cards** (a cross-project rollup stored under `Entities/`, pull-only,
   never in the recall pool), an **evolution timeline** across live + superseded notes, and sleep-time
   graph-**centrality salience**. A SQLite graph scale-tier keeps the entity queries single-digit-ms at
   5k notes. Off by default — the hot path is byte-for-byte unchanged — and held by separation / budget /
   privacy / opt-in invariant tests (+107 tests; `docs/BRAIN_LAYER_DESIGN.md`).
4. **No multi-tenant server / horizontal scale.** An explicit non-goal: local-first files are
   the whole thesis. Multi-machine is already supported (the store is git; `sync.py` mirrors
   it), but a hosted multi-tenant service is out of scope by design.

## What actually works (verified by dogfooding)

- **Semantic recall surfaces the right note** when one exists: "cuda out of memory" → the
  GPU-memory-leak mistake (0.48); a sharper "gpu vram leak subprocess windows" → 0.68. The
  hybrid RRF + lexical fallback + bi-temporal `--as-of` all run.
- **All 10 suites pass** (5 core ~4 s + 5 research). The new production paths are off by
  default (`NEVERTWICE_RANKER=hybrid`, `NEVERTWICE_DIVERGENCE=0`), so they add **zero cost to
  the default hot path** — the "new features bloat everything" critique does **not** hold for
  the default ranker; the research lives in isolated `research/` modules.
- **Token economy holds.** SessionStart injects a ~1100-char Context *brief* + a budgeted fact
  list, **not** the 20 KB raw Context file. The card is the bounded representation; the journal
  is the store. So "doesn't save tokens" is rebutted for the injection path.
- **Second dogfood pass (2026-06-17) — the core is healthy.** On the real vault: the injection
  guard `_looks_injected` has **0 false-positives** (0/328 legit notes rejected); **cross-project
  transfer works** (a query in one project surfaces relevant lessons from others); **graph-hop
  expands** (top-5 hits carried 41 wikilinks; `expand_hops=2` → 10 hits vs 5); and the `sources`
  field does **not** leak into injected fact-lines (store-only, no recall-token bloat). The
  "prove it's a toy" framing fails: real retrieval works — the one genuine bug found (recurrence
  carry-forward, W15) is fixed, and the rest are dormant-on-old-data (recurrence/confidence/decay)
  or defensible design (W10).
- **Scale verified (synthetic 12 k-note stress).** Index build is 0.37 s for 12 000 rows; the
  JSON cache is **not** memoised and a cold parse costs ~200 ms at that size — exactly the cost the
  SQLite index removes on the per-event hook (it reads candidates from SQLite, never parsing the
  JSON), and the FTS-prefilter caps per-query cosine work past 600 candidates. The hook loads the
  cache once per event and reuses it, so no redundant parses. The scale path is already covered by
  the P1 prefilter test (bound + no-query-full-set) — confirmed, no gap.

## Weaknesses

### Retrieval & recall
- **W1 [FIXED] Abstention never fired.** The absolute floor (`CONFIDENT_SIM=0.30`) sat *below*
  the bge-m3 background — on the real vault, note↔note median cosine ≈ 0.42 and a *nonsense*
  query ("xyzzy nonsense zzz") scored 0.43, the **same band** as real queries. So recall
  returned arbitrary notes for any input and never said "I don't know." Fixed: a query is
  low-confidence unless its top clears the absolute floor **and** stands a margin (0.15) above
  the per-query **median** similarity — corpus-adaptive. Verified: real → confident, gibberish
  → "⚠ no confident match".
- **W2 [OPEN — now measured] Embedding compression is the deeper problem.** bge-m3 cosines for short,
  multilingual notes bunch tightly; a real match clears the median by ~0.16–0.32, a no-match by
  ~0.13 — a ~0.03 absolute gap. The relative-margin gate mitigates but the boundary is fuzzy: a
  genuinely-relevant-but-weak query (no close note in the vault) abstains, and a borderline
  nonsense could slip through. Real fix needs a stronger embedder, a cross-encoder reranker, or
  query expansion — not a threshold. **Measured the two local fixes (2026-06-17,
  `research/precision_bench.py` + `longmem_eval.py --rerank`, see `research/W2_PRECISION.md`):
  embedding-space PRF (Rocchio) does not beat the bi-encoder even as an upper bound (real store,
  131 cross-session queries); and a local LLM-as-reranker over the hybrid top-10 *degrades*
  external precision on LongMemEval (R@1 0.418→0.31–0.39 across 4b/7b × 700/1600-char snippets, all
  Δ negative or within noise; R@10 unchanged 0.770) at 650–1900 ms/query. Conclusion: the gap is in
  the encoder; closing it needs a TRAINED cross-encoder or a better embedder — a model dependency
  outside the stdlib/local-first design — not a promptable LLM or query-side vector arithmetic.
  The bi-encoder hybrid stays the default; the reranker lives in `research/` (off the hot path).**
- **W3 [FIXED] The HOOK injection path had no confidence gate.** The abstention fix was in the
  CLI only; `retrieve_relevant` injected the semantically-nearest notes regardless of confidence.
  Fixed: the relative gate is now canonical in the core (`_low_confidence`) and `retrieve_relevant`
  drops the semantic ranking when low-confidence; the per-prompt path additionally opts out of the
  recency fallback (`recency_fallback=False`) so an off-topic prompt stays silent rather than
  injecting recent-but-irrelevant notes. Dogfooded: off-topic prompt 5→0 injected hits, confident
  query unaffected. (`memory_search` now delegates to the core gate — one gate, both paths.)
- **W4 [ADDRESSED 2026-06-20 — see launch update] No reranker on the default path.** Retrieval
  is bi-encoder cosine + lexical (now calibrated fusion). A purpose-trained cross-encoder
  (bge-reranker-v2-m3) now ships as an opt-in second stage (recall@1 0.55 to 0.61), off by
  default to keep the core dependency-free, and surfaced by `backend_report()` when torch is
  present. The remaining "not on by default" is the deliberate stdlib trade, not an open bug.

### Scale & performance
- **W5 [VERIFIED — not a bug] The scale-index builds correctly.** `.index.sqlite` was absent on
  the real vault, but `ensure_scale_index()` builds it on demand (verified: → 1.66 MB index,
  retrieval then reads 114 candidates straight from it) and the hook **already** calls it on both
  SessionStart and per-prompt recall. The absence was stale state, not a code path gap. Residual
  (minor, by design): the read-only `memory_search` CLI / MCP parse the JSON cache directly rather
  than reading the index, so a *standalone* recall doesn't get the FTS-prefilter — fine for
  on-demand single queries, a perf note only at very large scale.
- **W6 [FIXED] Double frontmatter read.** The 3B `sources` work made the supersede loop parse
  each old note's YAML twice (`_note_recurrence` + `_note_sources`). Folded into one
  `_note_recur_sources` read — a self-inflicted efficiency regression, now closed.

### Security (from 3B)
- **W7 [PARTIAL — addressed 2026-06-17] Plausible-false-fact poisoning.** A wrong-but-plausible
  lesson with ordinary confidence is indistinguishable *by form* from a real one — the irreducible
  open core. **Shipped two things that shrink it:** (a) the W8 `_looks_dangerous` guard now hard-
  rejects the *dangerous-advice* subset (disable TLS, chmod 777, exfiltration) → false-fact
  acceptance 1.00→0.50; (b) the **corroboration-gated quarantine** is now wired into
  `write_typed_note`, **opt-in** (`NEVERTWICE_QUARANTINE=1`): a single-source suspicious note
  (near-max confidence, or superseding a corroborated note) is diverted to `Quarantine/` retiring
  nothing — fully blocking supersession-abuse + confidence-spoof (→0.00) for multi-tenant/untrusted
  deployments. OFF by default (single-user owns every session; W9 reasoning). Residual: a *plausible,
  benign-shaped, single-source* lie still passes — only corroboration / external verification can
  catch that, and gating all single-source notes would quarantine most legitimate memory.
- **W8 [ADDRESSED 2026-06-17] The injection guard was phrase-based** (75% of injection shapes; missed
  no-shape dangerous content like "exfiltrate the .env…"). **Added `_looks_dangerous`** — a
  negation-gated guard for dangerous *actions* (secret exfiltration, destructive commands,
  security-control bypass) folded into the write-time `_looks_unsafe` reject. Injection acceptance
  0.25→**0.00**; **0/328 false-positive** on the live vault (cautionary lessons survive via the
  negation gate), so it ships on by default. Overall poisoning block 88% (precision 0.91/recall 0.83).
- **W9 [DEFERRED — measured, deliberately not shipped] Corroboration-quarantine.** Measured on the
  real vault: a conservative rule (single-source AND confidence ≥ 0.95, or superseding a
  recurrence ≥ 3 note) has **0 false-quarantine** (no legit note carries confidence; none supersede
  a high-recurrence note). So it is *safe* — but for a **single-user local** vault the threat it
  defends (adversarial sessions planting lessons) doesn't apply (the user owns every session), and
  `_looks_injected` already **rejects** injection-shaped writes (verified **0 false-positives** on
  328 real notes) while distinct-session counting defeats gaming. Shipping a quarantine that
  ~never fires here would be code-for-a-non-applicable-threat (graveyard) — it belongs in a
  **multi-tenant / shared-store** deployment, documented for that context, not the single-user
  default. (Recurrence-gaming *is* shipped: distinct-session counting.)

### Growth, code & honesty
- **W10 [DESIGN — not a bug; now measured] The per-project cap is OFF by default.** A store grows
  unbounded, but this is a deliberate choice ("never silently shed memory") and **retrieval cost is
  already bounded** regardless of size: past `NEVERTWICE_PREFILTER_LIMIT=600` the FTS-prefilter caps
  the per-query cosine work, so growth is a *storage* concern, not a latency one. A default cap would
  silently drop the user's memory — against the design. The opt-in submodular cap (1C) is there for
  anyone who wants bounded storage. Left as-is. **`retention_bench.py` (3A.3) now quantifies the
  cap on a real store:** it preserves **86.5% / 97.3%** of durable (cross-session) topics at 50% /
  70% budget and **archives** (never deletes) the excess — so it is *safe* if anyone enables it.
  Two measured corollaries: (a) the cap's `recurrence·resolved` utility is **inert on real data**
  (recurrence ≡ 1 — see W15/3A.2), so today's cap already behaves as pure coverage; (b) wiring
  **semantic** recurrence into that utility is **NOT worth it** — it hoards (3.7 vs 2.3 members per
  topic) and hurts at tight budgets. So the cap should keep the coverage objective; no change made.
  **POLICY DECISION (2026-06-17, resolves the P2 sign-off): keep the cap OPT-IN (default 0).** For a
  *memory* system "never silently shed memory" is the load-bearing trust property; retrieval cost is
  already bounded by the FTS-prefilter regardless of store size (so there is no performance reason to
  cap by default); storage is cheap and the store grows slowly (328 notes). A default-on cap would
  therefore trade a real trust cost (surprise archival) for a negligible benefit — the wrong trade.
  The cap stays one env var away, archive-not-delete, coverage-objective, and is now hardened with
  idempotency + conservation tests (a second pass archives 0; live+archived == original, nothing
  deleted). Verdict: the cap policy is correct as designed — nothing to implement beyond the test
  hardening (honest "nothing to improve" rather than speculative recency/utility churn).
- **W11 [FIXED 2026-06-17] Two opt-in research rankers moved to a plugin boundary.** `posterior` +
  MMR `divergence` (~45 lines) left `memory_hook` for `nevertwice/rankers.py`, lazy-loaded by
  `retrieve_relevant` (`_load_rankers()`) ONLY when `NEVERTWICE_RANKER=posterior` or
  `NEVERTWICE_DIVERGENCE>0` — a one-way import mirroring `index_sqlite`, so the default hot path never
  imports the code and the core carries no maintenance surface for it. Behaviour bit-identical
  (E3/E5 tests + all 5 core suites green; the default ranker never touches the plugin).
- **W12 [FIXED 2026-06-17] Shared `research/` helper extracted.** The mean±95%CI `_ci` duplicated
  across 5 benches now lives in `research/_common.py` (alongside `_rerank.py`); the benches import it
  (all research tests green). `gen_world` stays per-module by design — each experiment's synthetic
  world is a distinct generator, not duplicated boilerplate, so sharing it would be wrong, not DRY.
- **W15 [PARTIALLY FIXED — the biggest practical finding] The recurrence/confidence machinery is
  nearly inert on real data.** Measured on the live 328-note vault: **328/328 notes have
  recurrence = 1** and **0/328 carry a confidence field**. So the recurrence prior (the through-line
  of 1A/3A/1B/2C), the distinct-session anti-gaming, and confidence-aware ranking **never fire in
  practice** — the elegant research is theoretically sound but dormant on actual usage. Root cause:
  recurrence only grew on an *exact-slug* re-statement, which is rare because the extractor phrases
  each lesson differently; and explicit `supersedes`/`contradicts` (including the M-2 *semantic*
  supersession path) **dropped** the recurrence instead of carrying it. **Fixed the second half**:
  explicit/semantic supersession now carries recurrence + sources forward (a superseded lesson is a
  re-encounter), so recurrence grows whenever the contradiction engine fires, not only on a slug
  collision. **Confidence resolved as NOT a bug**: it is correctly plumbed end-to-end — the JSON
  schema asks for `confidence` in *every* item, `process_session` passes the item dict whole,
  `write_typed_note` stamps it — and 0/328 is purely **temporal**: the confidence prompt landed
  2026-06-16 while every note was written 2026-05-04…06-15, i.e. *before* the field existed. New
  notes populate it; the term is kept (removing correct-but-dormant code is the opposite mistake).
  Same shape as time-decay sitting ~1.0 on a ≤44-day vault — dormant on old data, correct on new.
- **W13 [info] The headline research wins are mechanism-level**, on synthetic worlds (3A/1A/1B/
  1C/2B/2C) or a curated corpus (2A); only LongMemEval (semantic R@5 = 0.65) is external. The
  posterior/bandit gains are in-distribution generalisation, disclosed in each doc — not external
  SOTA claims.
- **W14 [info] `sources` frontmatter** adds bounded store bloat (≤25 session stems) on recurring
  notes; not injected into context, so no recall-token cost.

## Less-traveled-path audit (2026-06-17)

Audited the modules the main passes hadn't covered, looking for data loss / corruption:

**A systematic recurrence-loss problem (exposed by W15) — three bugs, all fixed.** Once W15
showed recurrence *should* grow, an audit found it was being silently **dropped** in three
separate paths — every one a quiet data-loss of the recall-boosting count:
  1. supersession didn't carry it forward (`5116718`);
  2. near-dup merge kept only the keeper's, not the cluster max (`8d67f76`);
  3. `embed_index --rebuild` read it from the empty cache → reset all to 1 (`f8fe122`).
The remaining read points (`update_embeddings`/`_embed_recurrence`, `_note_meta`, the SQLite
index) correctly read from the note frontmatter (the source of truth); recurrence is now
preserved end-to-end.

- **`consolidate_memory` — bug 2 above, fixed.** The near-duplicate merge inherited only the
  *keeper's* recurrence; now carries the cluster's MAX (`_cluster_recurrence`, `8d67f76`).
- **`embed_index` — bug 3 above, fixed + first test.** `--rebuild` reset recurrence to 1 (read
  from `cache={}` instead of the note); now reads `fm.get('recurrence')` like resolved/confidence.
- **`process_now.py` — clean.** Idempotent: skips sessions already in `.processed_sessions.json`,
  and `write_typed_note` is per-session idempotent (C5) even if a session were re-processed.
- **`mcp_server.py` — clean.** Tools declare `required` fields; `_tool_memory_search` validates
  `query`, coerces `k` with try/except, and `isError` is structured (H8), not substring-guessed.
- **`sync.py` — clean.** A thin `add → commit → pull --rebase --autostash → push` wrapper; on a
  rebase conflict it stops cleanly (returns 1, manual resolution) — git holds everything, no data
  loss. Derived/machine-local files are gitignored so sync merges only real memory.
- **`graphify.py` — clean.** Hard byte cap (`MAX_GRAPH_BYTES`=120 KB) + `MAX_FILES`=800 + skip
  >5 MB files; output is always bounded (the real project's `graph.json` is ~20 KB).
- **`interop.py` — clean.** The AGENTS.md managed-block merge uses `re.S` (so the multi-line block
  is replaced, not duplicated) and `write_atomic`; hand-written content outside the markers is
  preserved.
- **`install.py` — clean.** Idempotent (`_has_our_hook` skips a re-add, no duplicate hooks),
  aborts on an unparseable `settings.json` (no clobber), backs it up before writing, preserves
  other tools' hooks.
- **`bootstrap_contexts.py` — two real bugs, fixed (`db19ecb`).** (a) DATA LOSS: `write_context`
  overwrote `Context/<project>.md` unconditionally, so re-running the seeder destroyed the
  accumulated session history the hook had rolled in — now it SKIPS an existing card (`--force`
  to re-seed). (b) it shared the crash below.
- **`health_check.py` — clean.** Diagnostic writer (`write_atomic` to `health.txt`); no
  destructive ops, no non-ASCII output.
- **Cross-cutting CRASH, fixed (`db19ecb`).** Four CLI tools (`bootstrap_contexts`,
  `consolidate_memory`, `embed_index`, `graphify`) printed `→`/Cyrillic to a non-reconfigured
  stdout and so died with `UnicodeEncodeError` on a default **cp1251 Windows console** (the user's
  OS) — a functional failure, not cosmetics. Added the `sys.stdout.reconfigure(utf-8,
  errors='replace')` guard the other scripts already had.

**Deep audit complete (2026-06-17).** Across the full sweep: **8 real bugs found and fixed**
(abstention floor, double frontmatter read, recurrence-on-supersession, hook confidence-gate,
recurrence-on-merge, recurrence-on-rebuild, bootstrap Context clobber, cp1251 CLI crash) plus
the confidence-emission e2e now tested. Every other audited path (sync/graphify-core/interop/
install/process_now/mcp/health) is clean. Remaining open items are fundamental (W2 embedding
compression) or low-value-for-single-user (W7/W8 security) — not bugs.

## Competitive gaps vs the leaders

- **No LLM entity/relation extraction into a typed knowledge graph** (Zep/Graphiti, Mem0-graph,
  Cognee). Nevertwice has `[[wikilinks]]` + graph-hop, but links are structural, not
  entity-typed — so multi-hop "which intervention affects which pathway" style queries are weaker.
- **No production server / multi-tenant / horizontal scale.** Letta and Zep are services;
  Nevertwice is single-machine files (a deliberate local-first choice, but a real ceiling).
- **Automatic capture** [UPDATED 2026-06-20]: hooks are Claude-Code-only, but `nevertwice watch`
  (a stdlib polling daemon) now gives always-on auto-capture for Codex / Cline / Roo / Aider /
  Gemini-CLI, and an MCP server covers any MCP client. SQLite-only editors (Cursor / Windsurf)
  still need an export-then-`--dir` step.
- **Recall confidence** [UPDATED 2026-06-20]: materially stronger since this was written, via
  calibrated fusion (default) and the opt-in trained cross-encoder; the bi-encoder ceiling (W2)
  remains for the pure-stdlib default.

## Prioritized improvement list

| P | improvement | closes |
|---|---|---|
| ~~P0~~ ✅ | ~~Move the relative-confidence gate into `retrieve_relevant`~~ — **done** (`3736f92`, `e8a63f1`) | W3 |
| ~~P0~~ ✅ | ~~Build the scale-index on first recall~~ — **verified working** (not a bug; hook builds it) | W5 |
| ~~P1~~ ⏸ | ~~Ship the corroboration-quarantine~~ — **deferred** (0-FP but defends a non-single-user threat; multi-tenant only) | W7/W9 |
| **P1** | A *local* reranker needs a cross-encoder model (vs the stdlib/local-first design); the opt-in **cloud** rerank already exists. Honest: re-weighting the existing bi-encoder + lexical signals adds little — a real gain needs a model dep or a better embedder | W4/W2 |
| **P2** | Lightweight entity extraction → typed `[[links]]` (closes the knowledge-graph gap) | graph gap |
| **P2** | Default-on cap measured **safe** (`retention_bench.py`: keeps 86.5%/97.3% of durable topics, archives not deletes) — flipping the default contradicts the "never silently shed memory" principle. **DECIDED 2026-06-17: kept opt-in** (reasoned, see W10); cap hardened with idempotency + conservation tests. Adding **semantic** recurrence to its utility is **not** worth it (hoards) | W10 ✅ |
| ~~P3~~ ✅ | ~~A plugin boundary for the opt-in research rankers; extract shared `research/` helpers~~ — **done** (`rankers.py` lazy-plugin; `research/_common.py` shared `_ci`) | W11/W12 |
| **P3** | Stronger/rerank embedder or query expansion for the compression problem | W2 |

## Capstone adversarial audit (2026-06-17) — 4 hostile passes, 5 real fixes

Ran four parallel hostile audits (bug-hunt, token-economy dogfood, dead-code/scale, competitive-gaps),
each tasked to prove the system is a broken toy. It is not — but they found **5 real issues, 3 of them
in the just-shipped W7/W8 security code** (exactly what a capstone is for). All fixed + tested:

1. **[W8, mine] negation gate too strict.** `_NEGATION_RE` anchored with `\W*$`, so an intervening word
   defeated it: "do not blindly curl secrets" was flagged as an attack (false-positive on a cautionary
   lesson). Fixed: match a negation marker ANYWHERE in the preceding window; widened the window to 36.
2. **[W7, mine] quarantined note inherited trust it never earned.** A quarantined note was stamped with
   the `recurrence`/`sources` of notes it did not retire — so if later promoted it arrived with fake
   corroboration. Fixed: no recurrence/sources carry-forward when quarantining.
3. **[W7, mine] quarantine not idempotent.** A crash-retry after a quarantine write didn't scan
   `Quarantine/`, so it could duplicate the note. Fixed: the idempotency check now also scans
   `Quarantine/` (gated on the opt-in mode → zero hot-path cost when off).
4. **[injection, pre-existing] SessionStart budget breached by ~3–17%.** The cross-project section had
   no per-line budget check and the footer was appended unconditionally past the cap. Fixed: reserve the
   (fixed, essential) footer up front and make the cross-project section budget-aware — measured payload
   now within the 40-char margin (was +377/+17%; now −134..+47 across real projects).
5. **[recall, pre-existing] the absolute confidence floor (0.30) was inert** (below the bge-m3 ~0.42
   background). Raised to **0.40** — measured: real note↔note top-1 minimum is 0.418 (p1 0.46), gibberish
   tops ~0.43, so 0.40 is the highest floor with **0 false-negatives** on the real vault while catching the
   lowest-scoring noise as defense-in-depth. NOT raised to ~0.45 (the auditor's suggestion): real/noise
   OVERLAP at the boundary (the W2 ceiling), so a higher floor would abstain on weak-but-real queries; the
   corpus-adaptive margin gate stays the PRIMARY abstention mechanism (it caught 6/6 gibberish).

**What the audits CONFIRMED clean:** retrieval never leaks a quarantined note into recall; deferred
retirement never fires on a quarantined note; the embed cache excludes quarantined/archived stems;
index_sqlite / ingest / select_coreset / mcp_server / `_looks_injected` / mmr are correct; no dead code in
the new modules (rankers, _rerank, _common — every function is reached). Token economy is real (~3.2×
Context compression). Competitive review: the system has *already* measured-and-rejected the tempting
additions (cross-encoder rerank, abstractive consolidation, recurrence boost); the only design-compatible
open lever is a **stronger local embedder** via the existing `NEVERTWICE_EMBED_MODEL` knob (attacks the W2
ceiling with zero new dependency) plus optional minimal LLM-emitted **typed edges** (P2) — both documented,
neither shipped speculatively.

## Verdict

Not a toy: recall works, the suites are green, the token economy holds, and the new research is
isolated and off-by-default. But the dogfood found a **real, shipped-for-months recall bug**
(abstention never fired — W1, now fixed) and a **live gap** (the hook injects without a
confidence gate — W3). The honest ceiling is **retrieval precision under embedding compression**
(W2/W4) and **plausible-false-fact security** (W7) — neither solved by the work so far, both with
clear next steps above.
