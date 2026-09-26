<!-- PREREG-V3 revision 1, 2026-09-26. Replaces revision 0 (sha256 bb80734873a609fdd6a52dfaab1c6bf6fa70d2d34272c85dc1fa67866327ec4d). Folds in the auditor's premortem verdict PR1-PR11 and its rulings, the auditor's rulings on C1-C14, and the closure audit (G1-G14 and the minor items); closes Q1-Q19; fences the choices revision 2 may still make. Committed as research/v3/PREREG-V3-rev1.md (revision 2 will be research/v3/PREREG-V3-rev2.md); the .loop copy is a witness. -->

# PREREG-V3: the rules of the v3 head-to-head campaign, fixed before its first number

Revision 1, 2026-09-26.

**Inputs:**
- LOCAL-TASK-D §2-§10;
- PREREG-V2 (rev 7), and revision 0 of this file;
- the auditor's traps T1-T30, T12a and T13a (.loop/AUDIT-PREREG-V3-TRAPS.md), including their post-premortem revision
  of T21 and T27;
- the premortem verdict PR1-PR11 and its [RULING] items (.loop/premortem/premortem-transcript-20260926-042912.md);
- the auditor's rulings on C1-C14, and the closure audit G1-G14 with its minor items;
- .loop/STATUS-V3-CONTRACT.md;
- the v3 instruments .loop/m4_check.py and .loop/m5_check.py;
- the executor's decisions on Q1-Q19.

**Status:** no v3 number exists and there is no v3 anchor. This revision closes every Qn of revision 0 (§15.2).

**Fenced slots:**
- The choices that the build, the installs and the pilot may still settle are fenced.
- A slot is a pair of HTML comments: an opening comment `rev2-slot: <id>` and a closing comment `/rev2-slot`. Each
  slot states its pre-declared branches.
- Revision 2 may only replace the text inside a fence with one of those branches, plus the record that decided it.
- Everything outside the fences is fixed by the sha256 of this revision (§1.2).

## 0. Purpose and scope

- **What v3 measures:** Nevertwice at the v3 anchor against every competitor in LOCAL-TASK-D §4, on public benchmarks
  that carry a licence and a pinned sha256. It measures one axis at a time:
  - A: long-memory QA and retrieval;
  - B: fact update and supersession;
  - C: time;
  - D: token efficiency;
  - E: speed;
  - F: coding-agent memory.
- **Where verdicts come from:** a small confirmatory family (§9.2) carries every "ahead", "behind" and "equivalent".
  It covers axes A, B and F, at Point B, with ours against each runnable competitor. Every other number is
  descriptive.
- **Layers are named, not subtracted ([RULING on R1]).**
  - **Why our cell may be empty:**
    - The product's extractor is written for coding-agent sessions.
    - LongMemEval, LoCoMo and BEAM are personal-chat benchmarks.
    - FactConsolidation feeds counterfactual 512-token chunks.
    - AMA-Bench trajectories are about six times longer than our 12,000-character transcript window.
    - Under the pinned LLM, the relevance gate may store nothing retrievable, and our cell may sit on the `none`
      bracket.
  - **How it is handled:** that result is a finding for the owner and for the global task.
    - It is measured (K76).
    - It is named in every E5 sentence (K79).
    - It is never tuned: no prompt edit, no project hint or window chosen after the pilot, no benchmark profile.
  - **Evidence already on file:**
    - capture_session with no project runs in os.getcwd() (api.py:672), and process_session skips an untracked cwd
      (_engine_cards.py:926-933).
    - An off-topic session yields no typed notes (_engine_cards.py:1071-1075), only a Session note (:1128-1131).
      Session notes are never embedded (:1145-1146), so they are never recalled.
    - On the withdrawn v2 frontier (LME-oracle, 150 questions), nevertwice_full scored 0.06/0.067/0.08 at k 1/3/5,
      and `none` scored 0.02.
    - Our ranker over raw sessions scored 0.35/0.45/0.45, about the same as the Mem0 store (0.35/0.43/0.40).
- **No owner data** (LOCAL-TASK-D §3.2).
  - The owner's corpora and labels are in D:\Coding\_nevertwice_owner_data_quarantine\, unopened, and on every deny
    list (§2.6).
  - Our own corpora (supersession_v1, asof) run only as secondary rows labelled "our corpus". They never appear in a
    headline sentence (T15).
- **No aggregate score** across axes. **No comparison** with any vendor-published number (T9, T11).
- **P0-P6** apply to every number produced at the v3 anchor, including the register's re-measurements for restore #3.
- **Every cell names** its benchmark file and the model version that produced it.
- **Out of scope:**
  - the global task (a better memory), and any tuning of our config on v3 data;
  - running the vendor-managed tier; creating cloud accounts or keys;
  - end-to-end agentic coding benchmarks. A pinnable benchmark of coding memory across sessions is the novel follow-up:
    E5 names it, and this file does not plan it;
  - v2 global-pool stands other than S9 (D1), and code_sessions (D3);
  - HaluMem, LongMemEval-V2, LoCoMo-Refined, BEAM 500K/1M/10M as stands, and LME(S*);
  - ablations of our own switches (xrerank on, graph hops, the principle layer), except the one pre-declared ablation
    row (K79);
  - any change to ~/.claude, ~/.codex, the owner's environment variables, Ollama's environment settings, the Task
    Scheduler, the firewall, Docker's networking or any other machine-wide setting.

## 1. Revision discipline, anchor and freeze

### 1.1 Order (PR10)
1. **Revision 1 (this file).** Every Qn is closed. Its sha256 is witnessed in STATE-C, and it is committed as
   research/v3/PREREG-V3-rev1.md so git dates it.
2. **Launch contract and proxy** (TB9, TB1), then their gate (A2). Nothing else touches the network or an arm before
   this gate.
3. **Build** (§14, A3-A7): fetches under the contract (A3), then the idle probe, the engine changes, the harness, the
   lists and the pacer.
4. **Installs** under the launch contract, with two documented attempts per arm (A8). Before the pilot, each system's
   shipped temperature is read from its installed source or vendor docs into FREEZE-V3 (§5.5).
5. **Pilot** on the smoke split with scoring disabled (A9). Then the pre-anchor design check (A9.5) and the
   pre-launch checklist (§14.3).
6. **Revision 2** (research/v3/PREREG-V3-rev2.md). Only fenced slots change, each to one of its declared branches,
   with the record attached. The auditor signs it, and it is committed. There is at most one revision 2.
7. **Certification kit**, gated on a fixture v3 artifact (A10.5).
8. **Anchor** (§1.3).
9. **Scored runs**, in the order of §5.6.

A choice that is neither closed nor fenced here cannot be made later.

### 1.2 Identity of the revisions
- The anchor tree holds both research/v3/PREREG-V3-rev1.md and research/v3/PREREG-V3-rev2.md.
- m2_v3 (auditor) proves that the two files are byte-identical outside the fences.
- After the first scored number, nothing in this file changes. A change means all of the following (§1.4):
  - a revision 3;
  - a new anchor;
  - a new STATUS campaign log;
  - a re-run of every stand.

### 1.3 The anchor
- **One anchor commit holds** (E2):
  - E1 closed;
  - the code of §14;
  - research/v3/FREEZE-V3.json;
  - the corpus pins of §3 and the committed lists;
  - research/v3/register_plan.json and the E5 placeholders (TB6b);
  - both revision files;
  - CI 14/14 at the anchor.
- **FREEZE-V3.json** is the record every instrument reads instead of STATE-C prose. It holds:
  - **prereg:** the sha256 of revision 1 and of revision 2.
  - **datasets:** per stand, the file, the HF repo and revision, the sha256 and the licence.
  - **lists:** path, sha256 and seed.
  - **models:**
    - the pinned LLM name, and the newest DeepSeek change-log date;
    - the embedder tag, digest and options;
    - J1 qwen3.6:27b a50eda8ed977;
    - J2's ordered list with digests (§8.3), and which member passed;
    - J3 deepseek-v4-pro, with its proxy pin;
    - the free-form FA/FR generator, hermes3-8b (§8.5);
    - the S6L and S10 model, qwen3-coder:30b 06c1097efce0;
    - the MDE discordance input and its source sha256 (§9.5).
  - **arms:** an entry for EVERY system, including every baseline, the ablation, the raw-text row and the brackets'
    reader.
    - m5 --freeze checks {version, python, temperature (shipped)}. The temperature is read from the installed source
      or the vendor docs before the pilot (§5.5).
    - Also recorded: config, thinking route or fallback, tools_allowed (§2.6.6), date route, lockfile sha256, image
      digests (Letta, FalkorDB), binary sha256 (Claude Code) and Node version.
  - **instruments:** path, sha256 and selftest count for:
    - m2_v3, m3, m4, m5 and tools/remeasure.py;
    - _pacer_selftest.py and the proxy selftest;
    - the launch-contract module, the statistics module and the idle probe.
    If an instrument changes after the anchor, both versions run on every artifact, and both verdicts are logged.
  - **rules and numbers fixed before the anchor:**
    - `symmetry: {"<dotted arm_decl path>": value | {per_system: {...}}}`: the §5.0 rules. m5 checks them on every
      artifact and FAILs any departure (C9 [RULING]);
    - `sensitivity_rows: [{id, stand, overrides: {temperature: 0}}]`: an artifact with `sensitivity: "<id>"` is checked
      against the listed overrides only, and against the freeze values for everything else (C5 [RULING]);
    - the P1 bands;
    - the reconciliation and A/B tolerances;
    - the idle thresholds and the per-unit ceilings;
    - the budgets, prefixes and floors;
    - the confirmatory family, with its simulated MDE and coverage.
  - **the allow set and the deny list** of §2.6.4.
  - **Not included:** the anchor sha, because a commit cannot contain its own hash. STATUS's `CAMPAIGN V3 START` line
    records anchor= and freeze=<sha256 of FREEZE-V3.json>.
- **STATUS.** The scheduler (TB4) appends to .loop/campaign-v3-log/STATUS. It follows the line formats and the rules
  S1-S8 of .loop/STATUS-V3-CONTRACT.md, which this file adopts by reference:
  - every launched run is logged, including smoke, debug and aborted runs and re-runs;
  - barriers hold;
  - each artifact's measured_at lies inside its START..END;
  - set-asides precede the first scored START;
  - an exogenous re-run covers all arms;
  - the log is append-only.
- **Set-asides (T17, T29).** At the anchor, before the first scored START, every cache, store, answer and verdict file
  older than the anchor is moved to `.stale-<time>`, with one STATUS SET-ASIDE line each:
  - frontier_*_cache.json, including frontier_full_ingest_cache.json;
  - the longmem and locomo vector caches;
  - the H2H_DATA stores;
  - the qdrant and chroma directories;
  - D:\Coding\_nevertwice_polygon\h2h_v2_stores and its two .pre-v2-fe6ddff-* set-asides.
- **Tree check (T16),** at the anchor and at every STAND START and STAND END:
  - HEAD equals the anchor, and no tracked file is modified;
  - `git status --porcelain research/` lists nothing outside .gitignore, apart from research/v3/results/ (P0e);
  - `git status --porcelain --ignored research/ .claude/` is recorded, because the owner's global ignore hides
    .claude/settings.local.json from the porcelain check. No child process ever runs with the repository as its cwd
    (§2.6), so that file stays where it is;
  - a name-only check (`git ls-files --others --ignored --exclude-standard` and `--others --exclude-standard`) finds no
    path matching codesess_code_heldout_* and no untracked or ignored path matching *heldout* (case-insensitive),
    except:
    - .loop/HELDOUT-LABELING.md and .loop/heldout_clean.sh (the auditor's process documents);
    - .loop/campaign-log/d1_heldout.log and .loop/campaign-v2-log/{b4_heldout.log, .cm_b4_heldout, .mark_b4_heldout}
      (runs of research/embed_universal/heldout_eval.py on the public external heldout set, not owner data);
    - __pycache__ bytecode of tracked modules.
    The 18 codesess_code_heldout_* caches and the campaign-v2 b7/b10 heldout logs were quarantined unopened on
    2026-09-26 (the auditor's rulings); the campaign-v2 STATUS lines naming those steps remain as history.

### 1.4 No commit between the anchor and restore #3
- STATUS-V3-CONTRACT S1 requires the anchor to be the parent of the restore commit.
- Artifacts stay untracked under research/v3/results/ until restore #3 commits them.
- remeasure refuses any row older than HEAD's commit (tools/remeasure.py:348-351, :623), and it cannot scope a re-run to
  one stand. So a code fix after the anchor means a new anchor, a new STATUS campaign file (the old one is kept as a
  witness), and a re-run of every stand.
- Restore #3 is built on a candidate tree and gated by m2_v3, m3, m4 and m5 before update-ref.

## 2. Arms

### 2.1 Tiers
- **product** (headline): the system as a user runs it, with its own writer (an LLM), its own store and its own
  retrieval call.
- **ablation:** `nevertwice-ablation` is nevertwice with the relevance gate off and the window set to the unit (§2.2).
  - It is labelled "ablation, not the shipped default".
  - It is never a headline, and never in the confirmatory family.
- **retrieval** (component): a system's store and search over the same raw items, with no LLM on write.
  - It is labelled as a component and never called the product.
  - On S2, S4, S8 and S9 it is scored by R@k.
  - On S5, S6 and S7 it is also read by the stand reader at Point B: these are the "ranker-only" rows.
- **bracket**, stored under `brackets`, not `arms`:
  - `none`: the question only;
  - `oracle`: the gold evidence;
  - `full-context`: the whole haystack in the reader's window, where it fits.
  - The full-context bracket fits DeepSeek's 1M window on S1 and S4-S7, and it may beat every memory arm. That is a
    result, not a validity problem.
- **sensitivity:** the all-0 temperature row on S6 FC-SH (§5.5). It is its own artifact.
- **vendor-managed writer** (T25): hosted products whose extractor cannot be pinned. This tier sits outside the main
  table.

### 2.2 Arm table
Arm ids are lowercase `[a-z0-9-]`. The ablation and the raw-text row are separate systems, so m5's cross-artifact
constancy of (version, python, config) holds.

| Arm (system) | Tier | Version / source (pinned in FREEZE-V3) | Runtime | config | Status 2026-09-26 |
|---|---|---|---|---|---|
| nevertwice | product | anchor commit | repo venv, recorded | ours:v3-<anchor> | to adapt (TB3, TB4) |
| nevertwice-rawtext | product (S7 secondary) | anchor commit | repo venv | ours:v3-<anchor> | to adapt |
| nevertwice-ablation | ablation | anchor commit | repo venv | ours:v3-<anchor>+gate-off+window-unit | to build (TB3) |
| mem0 | product | mem0ai, latest stable at freeze, with `[nlp]` (T22); 2.2.0 on 2026-09-23 | 3.12, fresh venv mem0_v3 | vendor-recommended:https://github.com/mem0ai/memory-benchmarks | to install |
| zep-graphiti | product | graphiti-core, latest stable at freeze (0.30.2 read 2026-09-26); FalkorDB image by digest | 3.12, fresh venv graphiti_v3 | vendor-recommended:https://github.com/getzep/graphiti | to install, to adapt |
| langmem | product | langmem, latest stable (0.0.30); langgraph pinned | 3.12, fresh venv langmem_v3 | vendor-recommended:https://langchain-ai.github.io/langmem/guides/delayed_processing/ | to install, to adapt |
| a-mem | product | agiresearch/A-mem at a pinned commit. Not PyPI a-mem 0.2.6, which is DiaaAj/a-mem-mcp (T21) | 3.12, fresh venv amem_v3 | vendor-recommended:https://github.com/WujiangXu/AgenticMemory | to install; expected blocked:structured-output on the DeepSeek stands |
| cognee | product | cognee, latest stable (1.6.1) | 3.12, fresh venv cognee_v3 | vendor-recommended:https://docs.cognee.ai/setup-configuration/llm-providers | to install |
| letta | product | Letta server in Docker by image digest, base memory tools only (Q15) | container | vendor-default | to install after the A2 gate |
| supermemory-local | product | supermemory-server (0.0.8) via npm under §2.6; Node pinned. A separate arm from hosted Supermemory (T8) | Node, recorded | vendor-recommended:https://github.com/supermemoryai/memorybench | to install after the A2 gate |
| claude-code-memory | product | Claude Code, latest stable at freeze, as a polygon-pinned binary. Never the owner's installed CLI; auto-update off | Node or native, recorded | vendor-default | needs-other-env:owner-yes (§2.6.8) |
| nevertwice-ranker | retrieval | anchor commit | repo venv | ours:v3-<anchor> | to adapt |
| mem0-store | retrieval | as mem0, infer=False | as mem0 | vendor-default | to adapt |
| langmem-store | retrieval | as langmem, InMemoryStore | as langmem | vendor-default | to adapt |
| chroma-store | retrieval | chromadb of amem_v3 (A-MEM's store) | 3.12 | vendor-default | to adapt |
| bm25-floor | retrieval | anchor commit | repo venv | ours:v3-<anchor> | to build |
| mem0-platform, zep-cloud, supermemory-hosted, letta-cloud | vendor-managed | hosted | - | vendor-default | needs-other-env:cloud-account |

The v2 venvs mem0_eval (3.14.4), amem_eval and graphiti_eval predate the launch contract. No v3 arm runs from them
(Q14).

The per-arm specifications below are the adapters' contract. Adapters are committed under research/v3/arms/ at the
anchor and published with the results (K71). Every product arm writes through its own proxy port with its per-arm token
(§4.4), and embeds with the v3 tag (§5.1).

**nevertwice** (product)
- **Writer:** deepseek-flash, through the native DeepSeek backend.
  - The stand sets NEVERTWICE_CLOUD=deepseek, NEVERTWICE_DEEPSEEK_MODEL=deepseek-flash, DEEPSEEK_URL=<its proxy
    port>/u/<unit> and DEEPSEEK_API_KEY=<its per-arm token>.
  - It sets them after sandbox_guard.isolate() and before the engine import (§4.2).
  - PYTHONPYCACHEPREFIX points into the runs tree (§2.6.2).
- **Thinking:** `"thinking": {"type": "disabled"}`, shipped on the DeepSeek JSON path (TB3).
- **Temperature:** 0.2, shipped (_engine_store.py:960). **Output cap:** 4,096 (B1).
- **Write unit:** one session per call, `api.capture_session(text, project=<stand id>, session_id=<unit>-<session>,
  date=<ISO date>)`.
  - All three arguments are keyword-only (api.py:653-656).
  - project is always passed. With no project, capture_session uses os.getcwd() (api.py:672), and an untracked cwd is
    skipped (_engine_cards.py:926-933).
  - The date reaches the note's stem and frontmatter, but not the extraction prompt (declared in §5.4).
- **Store:** a fresh vault and a fresh process per unit. Project = stand id.
- **Read:** `api.recall(query, project=<stand id>, k=<point k>, xrerank=False)` (api.py:62-64).
  - The context is `api.format_note(hit)` verbatim for each hit, in rank order (api.py:102-116).
  - No date is rendered, because recall returns none (api.py:65-66).
  - Counter `xrerank_calls` must be 0.
  - Counter `recall_degraded` counts reads the vectors did not rank although the store holds notes for the unit: the
    embedder did not answer, the query embed failed, the vectors come from another embedder, or the notes were stored
    without vectors. A degraded recall is an embed transport failure of our arm. P2 handles it like any arm's embed
    failure: the unit is re-run for every arm, or dropped for every arm. There is no ours-only re-ask.
  - Counter `recall_empty_store` counts reads of a store that holds no notes for the unit: what an extractor that
    wrote nothing leaves. It is the product's own outcome, not a failure. The read returns what it returns, the empty
    store shows in K76's yield, and it never enters P0a, P2 or PR7 (the auditor's TB3(e) ruling).
  - Only recall_degraded feeds P2.
- **S7 headline, through the hook's own reader.**
  - research/v3/render_ama_jsonl.py is committed before the pilot. It writes each trajectory as a Claude Code JSONL,
    mapping fields by their documented meaning:
    - the task statement → the first user message;
    - each step's reasoning text → assistant text;
    - each action → an assistant tool_use block named "Bash", with {"command": <action>};
    - each observation → a user tool_result block.
    A role that the dataset's schema lacks is omitted. Every event carries the ingestion wall-clock time and the unit
    directory as cwd.
  - The adapter calls process_session(session_id, cwd=<unit dir>, transcript_path=<jsonl>, trigger="SessionEnd",
    processed_db, project_override=<stand id>).
  - It then runs the post-steps that capture_session runs: rebuild_index, archive_old_sessions, archive_old_typed,
    prune_processed_db, git_autocommit (api.py:685-690).
  - There is one extraction per trajectory, and no stand-side multi-window replay.
  - The reader keeps user and assistant text and tool_use inputs, each line capped at MAX_MESSAGE_CHARS (2,000), and
    drops tool_result blocks (_engine_store.py:633-693). So observations never reach our extractor. That is a named
    reader and schema layer.

**nevertwice-rawtext** (S7 secondary): as nevertwice, with capture_session run on the rendered trajectory text.

**nevertwice-ablation:** as nevertwice, with two changes.
- **The relevance gate is off** (TB3).
  - The code gate is bypassed.
  - ONE pre-declared ablation prompt variant is used (C1 [RULING]): the shipped prompt minus exactly its
    project_relevant section and its trivial-session rule. It is a mechanical removal, and its diff is published.
  - Both are pinned by sha256, committed before the pilot and listed in §2.4. They are used only in this labelled row,
    never in a headline.
  - There is no second variant, and no edit after any pilot output.
  - The shipped prompt is untouched. "Prompt edits forbidden" protects the shipped config; this one diagnostic variant
    is layer decomposition, not tuning.
- **The window is set to the unit:** NEVERTWICE_MAX_TRANSCRIPT = the unit's length in characters.

**mem0** (product)
- **Writer:** provider deepseek, model deepseek-flash, base URL = its proxy port. Thinking: §2.2.1.
- **Temperature:** read from source before the pilot (§5.5). The vendor harness uses 0.1.
- **Embedder:** provider ollama, the v3 tag, 1024 dims.
- **Write unit:** one message per add() (CHUNK_SIZE=1).
  - speaker_a is the user and speaker_b the assistant.
  - Content: "{speaker}: {text}".
- **Dates:** mem0ai OSS refuses timestamp= with ValueError "Platform-only temporal parameter"
  (mem0/memory/main.py:817-818 in 2.0.19). The date therefore travels in content, as the uniform header (§5.3).
- **Store:** MEM0_DIR and a fresh vector store per unit; user_id = unit id.
- **Read:** `Memory.search(query, filters={"user_id": <unit>}, top_k=<point k>, threshold=0.1)`.
  - Both values are passed explicitly. The OSS defaults are top_k 20 and threshold 0.1.
  - The context is the memory text lines.
  - Mem0's add-time is never rendered as a date.

**zep-graphiti** (product)
- **Writer:** OpenAIGenericClient with structured_output_mode="json_object", base URL = its proxy port. Thinking:
  §2.2.1.
- **Temperature:** read from source before the pilot (§5.5).
- **Embedder:** the v3 tag, through Ollama /v1 (OpenAIEmbedder).
- **Write unit:** one EpisodeType.message episode per message ("speaker: text").
  - reference_time = the session date.
  - Episodes are sequential per group.
  - Telemetry is off, and SEMAPHORE_LIMIT is recorded.
- **Store:** a fresh FalkorDB graph per unit; group_id = unit id.
- **Read at Points K and B:** edges from the hybrid RRF search with valid_at and invalid_at, then node summaries, in rank
  order.
- **Read at V:** 20 edges + 20 nodes, in the Zep LongMemEval harness template.
- **Dates** come from valid_at and invalid_at.

**langmem** (product)
- **Writer:** a LangChain chat model at its proxy port. Thinking is turned off through the model's documented extra_body
  argument (§2.2.1).
- **Temperature:** read from source before the pilot (§5.5).
- **Embedder:** the v3 tag (OllamaEmbeddings).
- **Write unit:** one session thread: the role-preserving message list in one manager call.
  - Manager defaults (query_limit 5).
  - The uniform date header.
- **Store:** InMemoryStore per unit; namespace = unit id.
- **Read:** `store.search(namespace, query=q, limit=<point k>)`, rendered as "- item" lines.

**a-mem** (product)
- **Writer:** the upstream OpenAIController, with OPENAI_BASE_URL = its proxy port.
- **Temperature:** 0.7 in the upstream source, confirmed from the installed source (§5.5).
- **Embedder:** the v3 tag, through a chroma embedding function that replaces all-MiniLM-L6-v2.
- **Write unit:** one note per turn, `add_note("Speaker X says : text", time=<date>)`.
- **Store:** a fresh chroma directory per unit.
- **Read:** `search_agentic(q, k)`. The rendered date is the note's timestamp.
- **Expected block:** both upstream controllers send response_format json_schema, which DeepSeek does not accept. So
  blocked:structured-output is expected on the DeepSeek stands, named as a consequence of the pinned LLM. S6L gives
  a-mem a row.

**cognee** (product)
- **Writer:** LiteLLM with LLM_PROVIDER=custom, model deepseek/deepseek-flash, endpoint = its proxy port. Thinking:
  §2.2.1.
- **Temperature:** read from source before the pilot (§5.5).
- **Embedder:** EMBEDDING_PROVIDER=ollama, the v3 tag, 1024 dims, HUGGINGFACE_TOKENIZER=BAAI/bge-m3 (loaded offline
  from the polygon cache).
- **Write unit:** one document per session, one chunk per turn (the vendor BEAM harness, cited); the uniform date header.
- **Store:** data and system roots per unit.
- **Read:** the product's default search type at the pinned version, with only_context=True and top_k per point.
- **Read at V:** chunks 20 + entities 20, per the vendor harness (cited).

**letta** (product)
- **Server:** server only, in Docker by image digest (Q15), on Docker's default bridge network.
  - It reaches the host's proxy ports through host.docker.internal. Embeddings go through the proxy's Ollama leg.
  - If the container cannot reach the proxy on the host's loopback that way, Letta is blocked:local-server. The proxy
    never binds another interface.
  - Nothing blocks the container's egress: there is no firewall or Docker-network change. The declared control is the
    container egress witness (§2.6.9).
  - Thinking: §2.2.1.
- **Temperature:** read from source before the pilot (§5.5).
- **Write unit:** one message per call, to one agent per unit.
  - The agent edits its own core and archival memory, with the §2.6.6 tools only.
  - The uniform date header. Letta's message API is not given a date.
- **Servers:** one server per (arm, run, block), restarted between blocks; one agent per unit.
- **Attribution:** each agent's model endpoint carries the unit prefix /u/<unit>. If the pinned version ignores
  per-agent endpoints, the block rule of §4.4 applies.
- **Read, by the stand reader:**
  - the core memory blocks first;
  - then archival and recall (conversation) search hits, interleaved by rank (archival 1, recall 1, archival 2, ...),
    up to the point's budget;
  - at Point K: the first 10 interleaved hits.

**supermemory-local** (product)
- **Server:** via npm under §2.6.
  - OPENAI_BASE_URL = its proxy port, and OPENAI_MODEL=deepseek-flash.
  - SUPERMEMORY_EMBEDDING_* point at the proxy's Ollama leg, with the v3 tag at 1024 dims, locked at first boot.
  - Thinking: §2.2.1.
- **Temperature:** read from source before the pilot (§5.5).
- **Write unit:** one document per session, with the uniform date header. The adapter waits until the document and
  memory status are "done", bounded by the per-unit ceiling (§5.6).
- **Servers:** one per (arm, run, block); one container_tag per unit.
- **Attribution:** the block rule of §4.4.
- **Read:** `search.memories(q, container_tag, hybrid, limit=<point k>)`, with the threshold at its shipped default,
  passed explicitly.

**claude-code-memory** (product; §2.6.8)
- **Writer:** the polygon-pinned binary against DeepSeek's Anthropic endpoint, through its proxy port.
  - ANTHROPIC_MODEL, ANTHROPIC_SMALL_FAST_MODEL and every tier and subagent model variable are set to deepseek-flash.
  - DeepSeek maps claude-opus* names to deepseek-v4-pro, so the proxy refuses any other name as model_mismatch.
  - Thinking: §2.2.1.
- **Temperature:** read from source or docs before the pilot (§5.5).
- **Write unit:** one fresh `claude -p` per session, with the prompt "Conversation from <date>:\n<transcript>".
  - cwd = the unit directory.
  - The unit's memory directory carries state across sessions. There is no --resume chain.
  - Tools are locked (§2.6.6).
- **Config:** a fresh CLAUDE_CONFIG_DIR per unit.
- **Read, by the stand reader:**
  - Point B "R-all": MEMORY.md, then the topic files in file order, up to the budget;
  - Point K: competitor-lacks-capability:k;
  - V "R-index": MEMORY.md, the first 200 lines or 25 KB.

**Retrieval tier** (nevertwice-ranker, mem0-store, langmem-store, chroma-store, bm25-floor)
- **Writer:** no LLM. **Embedder:** the v3 tag (bm25-floor embeds nothing).
- **Items:** one item per benchmark item: a session (S2, S9), a turn (S4, S5), the benchmark's 512-token chunk (S6) or
  a step (S7). Each item is truncated per §5.1 and rendered with its position "#<index>".
- **Store and read:** a fresh store per unit, and the store's own top-k search.

<!-- rev2-slot: arm-status -->
Written from A8/A9: two documented attempts per arm, the capability probes, and the §2.2.1 route checks.
- **Per arm, exactly one of:** runnable; blocked:<P3 reason>; needs-other-env:<env>.
- **Per probe, as it applies:** competitor-lacks-capability:<probe>.
- **Also recorded per arm:**
  - the verified thinking route, or the declared fallback (thinking-default slot);
  - the allowed tool set, where A8 shrank it (§2.6.6; it can only shrink).

No other part of §2.2 changes.
<!-- /rev2-slot -->

### 2.2.1 Thinking routes (Q2, K58)
- **The provider default:** DeepSeek's documented default is thinking on (§4.1).
- **The one rule:**
  - Each arm turns thinking off through its own documented route, if it has one.
  - An arm with no documented route gets the one declared per-arm proxy fallback (§4.2), and only under branch (b) of
    the thinking-default slot.
  - Beyond that one field for those arms, the proxy never changes a request.
- **The check:** every response is checked, for reasoning tokens on /v1 and for thinking blocks on /anthropic.

| Arm | Documented route (verified inside the thinking-default slot) |
|---|---|
| nevertwice | shipped: `"thinking": {"type": "disabled"}` in the DeepSeek JSON request (TB3). The Ollama path already sends think:false (_engine_store.py:874) |
| mem0 | [U] none known in the DeepSeek LLM config at 2.0.19; checked at the pinned version |
| zep-graphiti | [U] none known in LLMConfig or OpenAIGenericClient; checked at the pinned version |
| langmem | the LangChain chat model's documented extra_body argument |
| a-mem | not reached on the DeepSeek stands (expected block). On S6L, qwen3-coder:30b is a non-thinking model |
| cognee | [U] LiteLLM accepts a thinking parameter; whether Cognee's LLM config passes it is checked |
| letta | [U] the model's LLM config reasoning fields |
| supermemory-local | [U] none known (configured by environment only) |
| claude-code-memory | [U] Claude Code's own thinking setting, if DeepSeek's Anthropic endpoint honours it |
| stand reader | the stand's client sends `"thinking": {"type": "disabled"}` |

### 2.3 arm_decl and artifact shape (m5 v3)
- **One artifact per (stand, point, tier).** This keeps m5's parity of k and budget inside each file.
- **Root fields:**
  - `arms` (a mapping), and `brackets`, kept separate from `arms`;
  - `declared_axes`, only as §5.2 lists it;
  - `input_manifest` {dataset sha256, list sha256, split}, with input_sha256 = its sha256;
  - `stand`, `point`, `tier`;
  - `model_version` {response model, newest change-log date};
  - `measured_at` {commit, utc, dirty}, where dirty is defined in P0e;
  - `status_ids`: the STATUS START ids the artifact covers;
  - `sensitivity`, on the all-0 row only: "temperature-0".
- **arm_decl, the m5 v3 REQUIRED fields:**
  - system, version, python;
  - config: "vendor-default", "vendor-recommended:<doc url>" or "ours:<frozen id>";
  - llm: an exact tag, or null;
  - llm_transport: "ollama" or "cloud:deepseek", and null exactly when llm is null;
  - embedder: a tag, or null for an arm that embeds nothing. Null arms are outside embedder parity;
  - k, context_budget_tokens, runs, deterministic, write_granularity, input_sha256, embeds_via_ollama.
- **arm_decl, v3 fields this file requires.** The harness asserts them, and m5 checks each against FREEZE-V3 where
  noted:
  - tier and point;
  - llm_params {temperature (a value, or the set of per-call-site values), max_tokens, thinking_route}. m5 --freeze
    checks temperature;
  - reader {tag, template_sha256};
  - judges {J1, J2, J3: tag and digest};
  - now_rule: "wall-clock";
  - date_route: "field:<name>", "header" or "none";
  - renderer {name, sha256};
  - threshold: the value sent, or "n/a";
  - namespace: the per-unit store or namespace rule;
  - tools_allowed [names], as fixed in §2.6.6;
  - launch {env_names [names only], cwd_rule, binary sha256};
  - deviations [list];
  - symmetry {knob: value}, one entry for every §5.0 knob. m5 checks these against FREEZE-V3 `symmetry`.
- **Per-arm row blocks:**
  - **cloud_transport (m5 v3):**
    - calls > 0;
    - failed_outcomes, fallback_local, model_mismatch, thinking_calls, cloud_bypass and tool_violation are all present
      and all 0;
    - models_seen names exactly one model;
    - also written: transport_recovered, transport_lost, upstream_errors, client_abandoned, product_retries,
      thinking_injected, fingerprints_seen (per endpoint class), straddled_units, empty_content, json_invalid, capped,
      reasoning_tokens, tokens by phase {write, read, answer}, and incident_units.
  - **boundary (m5, P0h):** {canary_hits, owner_marker_hits, egress_hits, fs_hits}, each 0. Also published:
    ancestor_canary_hits, and egress_attempts (the catcher's refused requests, by host).
  - **p1 (m5, P1):** {lost_share, transport_lost_share, label}. label is empty, "lossy-writer (x%)" or the blocked
    reason. Also written: lost, logical_writes and classes.
  - **yield (m5, K76):** {unit, retrievable_unit_share, coverage, labels}.
    - unit is the stand's evaluation unit: haystack, conversation, row or trajectory.
    - labels is "writer-gated (x%)", "window (y%)", both, or empty.
    - Also written: items_per_1k_read and empty_context_share. For ours: relevant_false_share, proposed, refused,
      quarantined, skipped, off_topic and truncation_share.
  - **ollama_transport**, in the pacer's shape: calls, failed_outcomes, failed_outcomes_llm, bypass_calls, embed_at_cap,
    and fallback_local (Ollama generation-path calls).
  - **caches[] (m5 v3, K60/K61):** {path, sha256, built: {commit, utc, ollama_transport}, hits, misses}.
  - **reconciliation** (K87): {proxy_calls, adapter_calls, product_logical_calls, tokens_delta_pct, serverlog_delta,
    branch}.
  - **Our arm only:** llm_stats, the api.m._LLM_STATS deltas, plus xrerank_calls, recall_degraded and
    recall_empty_store.
  - **blocked:** "<vocabulary value>" and no numbers. The m5 BLOCK_RE and the m4 VOCAB govern the values.

### 2.4 Our frozen config, and where each default was chosen (T4)
Provenance was traced with `git log -L` and pickaxe across the renames, before this revision's sha. "Before this
repository" means the value is present, unchanged, at the repository's first commit: 482a7bf, 2026-06-20, the import of
Anamnesis v1.0.0. What data chose those values is unknown.

| Default | Value | Chosen on | Source |
|---|---|---|---|
| RETRIEVAL_FUSION | calibrated | LongMemEval oracle pool (R@5 0.66 -> 0.80) | _engine_config.py:620-625 |
| FUSION_SEM_WEIGHT | 1.0 | fusion_sweep 2026-09-06, LME oracle pool + LoCoMo | :626-633 |
| RETRIEVAL_SEM_WEIGHT (RRF path) | 2.0 | before this repository (482a7bf); data unknown | :616-619 |
| RETRIEVAL_SIM_FLOOR / CONFIDENT_MARGIN | 0.40 / 0.15 | dogfood W1/W3 (owner store) | :559, :566-570 |
| RECUR boosts | 0.03 / 0.0003 / 0.02 | longitudinal_bench (synthetic) | :571-581 |
| DECAY half-life / floor | 365 d / 0.5 | design (M-3), not fitted | :588-592 |
| ARCHIVE days (typed / session) | 90 / 30 | design | :509-510 |
| MAX_TRANSCRIPT_CHARS | 12,000 | design (B1) | :478 |
| TRUNCATE_HEAD_FRAC | 0.4 (head 40 %, tail 60 %) | design | :498; truncate_smart _engine_store.py:543 |
| MAX_MESSAGE_CHARS (hook reader, per line) | 2,000 | design | :500; _engine_store.py:633-693 |
| EXTRACT_NUM_PREDICT | 4,096 | B1 | :486 |
| Relevance gate | project_relevant false: no typed notes, no context update, Session note only | audit C1 | _engine_cards.py:1048-1075; prompt _engine_config.py:833-841 |
| Extraction prompt | EXTRACTION_PROMPT; template sha256 in FREEZE-V3 | first form before this repository (482a7bf), data unknown. Edited 8652be5 (2026-07-11, English everywhere), df0554e (2026-09-02, supersession bench fixes) and d07375e (2026-09-12, K5/K6/K7); none of these edits used benchmark data | _engine_config.py:775 |
| Ablation prompt variant (nevertwice-ablation only) | the shipped prompt minus :836-841 (project_relevant) and :833-834 (trivial-session rule), removed mechanically, diff published; sha256 in FREEZE-V3 | C1 [RULING]: layer decomposition, not a shipped default | TB3(d) |
| Project rule | the caller's project, else a tracked cwd, else skip | audit C2 | _engine_cards.py:923-933; api.py:653-656, :672 |
| EXTRACT_RETRY | 0 (K5 missed its gate) | K5 | _engine_cards.py:809-810 |
| Cloud write temperature | 0.2 | before this repository (482a7bf); data unknown | _engine_store.py:960 |
| DeepSeek thinking | disabled (TB3) | DeepSeek's documented default (thinking on), and our Ollama path's think:false | _engine_store.py:874, :956-962 |
| Cloud timeout / retries / backoff | 60 s / 2 / 2.0 | before this repository (482a7bf); data unknown | _engine_config.py:385-387 |
| DEEPSEEK_MODEL default | deepseek-v4-flash (a legacy name, served by V4.1-Flash); v3 sets deepseek-flash | - | :375 |
| PROMPT_RECALL_K / INJECT_BUDGET_CHARS | 3 / 2,200 | before this repository (482a7bf); data unknown | :743, :530 |
| XRERANK | off: api.recall(xrerank=False), counted | reranker evaluated on LME-S | api.py:62-76; memory_search.py:148-149 |
| cloud rerank | off | opt-in | :724 |

**Labelling rule:** a stand whose questions or sessions took part in choosing any default is labelled **tuned-on**.
- LME-S, LME-M and LME-oracle are tuned-on, because they share the 500 questions and the evidence sessions. So M is not
  the untouched part that T4 suggested; this is a declared deviation.
- LoCoMo is tuned-on.
- BEAM, MemoryAgentBench (FactConsolidation) and AMA-Bench are **untouched**. In the history they appear only as prose
  (df0554e, 7fb0b2a), never as data, and no default was chosen on them. No stand is relabelled.

### 2.5 Deviations from vendor defaults
Declared per arm, in arm_decl.deviations.
- **LLM:** deepseek-flash, non-thinking, where vendors default to OpenAI models.
- **Embedder:** one bge-m3 tag. Mem0 and Graphiti default to text-embedding-3-small, and A-MEM to all-MiniLM-L6-v2.
- **Retrieval depth:** set per point (§5.2).
- **Reader:** the stand reader for every arm. Letta and Claude Code lose their own agentic readers.
- **Graphiti:** json_object mode.
- **Dates:** arms without a write-time date field get the uniform content header (§5.3).
- **Temperature:** each product's shipped default (§5.5).
- **Thinking:** off, through each product's documented route. An arm without one gets the declared per-arm fallback,
  under branch (b) only, listed as thinking_injected (§4.2).
- **Tools:** each arm's fixed memory-tool set (§2.6.6).
- **Claude Code:** a fresh `claude -p` per session.
- **Server arms:** one server per (arm, run, block), and one namespace per unit. The same rule applies to every server
  arm.

### 2.6 Launch contract and boundary (PR1)
[RULING] No out-of-process arm, no npm install, no Letta run and no Claude Code run happens before this section is built
(TB9, TB1) and passes the auditor's gate (A2). Fetches (A3) come after that gate too, and run under the contract.

**2.6.1 Scope.** The contract covers every child process that the scheduler, the harness, the proxy or an install
script starts:
- arm runners, our own included;
- fetch commands (A3);
- install commands (pip, npm, docker);
- servers (the Letta container, supermemory-local);
- the reader and judge clients;
- the processes these spawn, such as our engine's git snapshot.

**The proxy is the single instrument exception.** Its contract lets it read exactly three things: D:\Coding\_secrets\deepseek.env,
~/.claude/CLAUDE.md, and ~/.claude/rules/*.md. It reads them read-only, at start, into memory. Nothing derived from
them is persisted (§2.6.9). Everything else in §2.6 applies to the proxy too.

**2.6.2 Environment from an allowlist, never {**os.environ}.**
- **Set by the contract:**
  - Windows essentials: SystemRoot, SystemDrive, windir, ComSpec, PATHEXT, NUMBER_OF_PROCESSORS,
    PROCESSOR_ARCHITECTURE, OS.
  - A polygon-first PATH: the arm's venv or Node directory, then System32, then the system Git (for our engine's own
    git snapshot).
  - TEMP, TMP, HOME, USERPROFILE, APPDATA and LOCALAPPDATA, pointing into a fake home under the runs tree.
  - PYTHONPYCACHEPREFIX, pointing into the runs tree for every Python child. Importing the repository then writes no
    bytecode into it.
  - NO_PROXY=127.0.0.1,localhost.
  - HTTP_PROXY and HTTPS_PROXY, pointing at the proxy's egress catcher. This is the only proxy value allowed.
  - HF_HOME and the other HF_*, TRANSFORMERS_* and SENTENCE_TRANSFORMERS_* cache variables, inside the polygon. After
    A3 they are set with HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1.
  - MEM0_DIR, per unit.
  - GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM, pointing at empty files in the fake home.
  - Telemetry and auto-update off. This fixed list is set for every child; an unused variable is harmless:
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, DISABLE_TELEMETRY=1, DISABLE_ERROR_REPORTING=1, DISABLE_AUTOUPDATER=1,
    MEM0_TELEMETRY=False, GRAPHITI_TELEMETRY_ENABLED=false, TELEMETRY_DISABLED=1, ANONYMIZED_TELEMETRY=False,
    HF_HUB_DISABLE_TELEMETRY=1, LITELLM_LOCAL_MODEL_COST_MAP=True, LANGCHAIN_TRACING_V2=false,
    LANGSMITH_TRACING=false, NEXT_TELEMETRY_DISABLED=1, DO_NOT_TRACK=1. The egress witness and the catcher cover any
    undocumented path.
  - The arm's declared variables: base URLs at its proxy port, its per-arm token, and its NEVERTWICE_* or product
    settings.
- **Never present:**
  - CLAUDECODE, and every inherited CLAUDE_* or CLAUDE_CODE_* variable. For the Claude Code arm, the contract itself sets
    only CLAUDE_CONFIG_DIR, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC and the model variables, each by name.
  - Every inherited *_TOKEN, *_KEY or *_SECRET variable. CLOUDFLARE_API_TOKEN was verified set in the session on
    2026-09-26.
  - Inherited HTTP_PROXY and HTTPS_PROXY (verified set).
  - CLAUDE_CODE_MESSAGING_SOCKET (verified set).
  - Any path on the deny list (§2.6.4).
- **Dummy tokens (C11 [RULING]):**
  - OPENAI_API_KEY, DEEPSEEK_API_KEY and ANTHROPIC_AUTH_TOKEN, when the contract sets them, hold per-arm proxy tokens,
    not provider keys. They are recorded by name and never start with "sk-".
  - The forbidden set is every *_TOKEN, *_KEY or *_SECRET INHERITED from the parent. The real key lives only in the
    proxy.
- **Assertion before every spawn:**
  - the harness builds the environment, and checks it against the allowlist and the forbidden patterns;
  - on any violation, it refuses to spawn;
  - it records the variable NAMES, never the values, in arm_decl.launch.env_names.

**2.6.3 Working directory.**
- Each unit runs in a fresh, empty, non-git directory: <polygon>\runs\v3\<stand>\<run>\<arm>\<unit>\.
- The harness asserts that it is outside every git work tree, is never the repository, and is never under
  C:\Users\Platon.
- Our arm's project comes from project=<stand id> (§2.2), not from the cwd.

**2.6.4 Allow set and deny list (the fixed baseline, 2026-09-26).**
- **Allowed under D:\Coding\_nevertwice_polygon\:**
  - the venvs graphiti_eval, mem0_eval and amem_eval (v2 artefacts; no v3 arm runs from them), and the new v3 venvs;
  - backups: read-only, never used by arms;
  - core_bare and bare314;
  - the runs tree;
  - llama.cpp, a public clone of ggml-org/llama.cpp at 169e4a7, only if a stand uses it. No v3 stand does;
  - h2h_v2_stores with its two .pre-v2-fe6ddff-* set-asides (v2 competitor stores built from public LongMemEval), only
    until the anchor set-aside (§1.3).
- **Denied to every child, on every contract:**
  - **The quarantine:** D:\Coding\_nevertwice_owner_data_quarantine\, in full, unopened and default-deny.
    - It holds code_heldout, heldout_marks.json, quarantine_live, stores, embed_specialize and corpus_heldout,
      polygon_unknown\{corpus, twin_gate, empty_projects, results, loose files}, research_data_code_heldout (the 18
      untracked research/data/codesess_code_heldout_* caches) and loop_campaign_v2_heldout (the 36 campaign-v2 b7/b10
      heldout logs and markers), both moved 2026-09-26.
    - If a classification is ever needed, it is by provenance only (the generating script, git history, manifest source
      fields), never by content.
  - **The worktrees** `before` (dd9ea5e) and `engine_ef8120d` (ef8120d): git worktrees of this repository under the
    polygon, denied until `git worktree remove` removes them at cleanup.
  - **The repository** D:\Coding\nevertwice, including .claude\settings.local.json.
    - It is never a cwd and never writable by a child.
    - It is on no child's import path, except one named exception: the harness and our arm's runner import its code
      read-only, with PYTHONPYCACHEPREFIX in the runs tree.
  - **The owner's home,** C:\Users\Platon, in full: ~/.claude, ~/.claude.json, ~/.codex, ~/.mem0, ~/.config,
    ~/.local\share\claude, and the npm and pip caches. The proxy's single read exception is in §2.6.1.
  - **Other owner locations:**
    - D:\Obsidian\Claude_Memory and D:\Nevertwice_Conservation;
    - D:\Local_AI_Models, which only the Ollama service reads;
    - D:\Coding\_secrets, which only the proxy reads, by path.
- **What "denied" means:**
  - The path never appears in an environment value, cwd, PATH, import path (except the named exception), Docker mount or
    allowed-tools path.
  - The filesystem witness watches it, to the extent §2.6.9 allows.
  - Declared limit: reads by a native child cannot be witnessed without OS auditing, and OS auditing would change
    machine settings. The controls against reads are tool denial and the fake home.

**2.6.5 Binaries, fetches and installs.**
- **Binaries:** every child's executable is resolved to an absolute path under the polygon and asserted before spawn:
  venv python, polygon Node, and the polygon-pinned Claude Code binary. The exceptions, recorded by path and version,
  are the system docker CLI and the system Git.
- **Fetches (A3):** they run under the contract, inside a declared fetch window. The egress witness records every host
  contacted. No token is used: all data is public.
- **Python arms:** fresh 3.12 venvs; pip with PIP_CACHE_DIR in the polygon and no user config.
- **Node arms:**
  - Attempt 1: `npm install --ignore-scripts --prefix <polygon dir>`, with npm_config_cache in the polygon and
    npm_config_userconfig pointing at an empty file, so ~/.npmrc is never read.
  - Attempt 2: only where the vendor documents a required install script, run under the same contract.
- **Containers:** by image digest, with mounts from the runs tree only.
- **Records:** lockfiles and resolved versions are hashed into FREEZE-V3. The egress witness records the install hosts.

**2.6.6 Tool lockdown.**
- **The allowed sets** below were fixed on 2026-09-26 from vendor docs and source. They are memory tools only. A8 may
  only remove names from them.

| Arm | Allowed tool names |
|---|---|
| nevertwice | none (json_object) |
| mem0 | none (json_object; graph memory off) |
| zep-graphiti | none (json_object structured output) |
| langmem | Memory (the default schema); PatchDoc and RemoveDoc (trustcall's patch and delete tools); PatchFunctionErrors and PatchFunctionName (trustcall's validation-repair tools) |
| a-mem | none (json_schema; expected blocked) |
| cognee | none, or, if its LLM client runs in tool mode, only the names of the Pydantic response models its installed package source defines for the default cognify pipeline (structured output), listed from that source at A8 before the pilot; never an action tool |
| letta | send_message, conversation_search, archival_memory_insert, archival_memory_search, core_memory_append, core_memory_replace, memory_insert, memory_replace, memory_rethink, memory_finish_edits |
| supermemory-local | none, or response-model names from its installed source, by the same rule as cognee |
| claude-code-memory | Read, Write, Edit, MultiEdit and memory, each restricted to the unit's memory directory |
| stand reader, judges | none |

- **Forbidden regardless of any list:** a tool name that contains, case-insensitively, any of bash, shell, powershell,
  cmd, terminal, exec, run_code, code_interpreter, python, computer, browser, web, fetch, http, url, download, task,
  agent, notebook or kill, or that starts with mcp__.
- **Violation:** a tool name offered in a request's tools list, or called in a response, that is outside the arm's
  allowed set or matches a forbidden pattern is a tool_violation. Zero tolerance (m5).
- **Claude Code, fixed now:**
  - the polygon-pinned binary, with its resolved path asserted;
  - `claude -p --output-format json --permission-mode default --setting-sources user --settings <unit settings.json>
    --strict-mcp-config --mcp-config <empty.json>`;
  - `--allowedTools "Read(<memdir>/**)" "Write(<memdir>/**)" "Edit(<memdir>/**)" "MultiEdit(<memdir>/**)"`;
  - `--disallowedTools` set to every other tool the binary offers;
  - the unit's settings contain `"disableAllHooks": true`, and no permissive defaultMode;
  - a fresh CLAUDE_CONFIG_DIR per unit;
  - the harness refuses to spawn if argv or settings name bypassPermissions, acceptEdits or
    --dangerously-skip-permissions;
  - if the pinned version rejects any of these flags or rule forms, the arm is blocked:unsupported-surface. Nothing is
    substituted;
  - the defence against commands inside S7 trajectories is tool denial, not prompt wording.
- **Letta:**
  - server only, in Docker by digest;
  - agents are created with exactly the tools listed above, with no custom tools and no tool-sandbox execution;
  - Letta Code and local mode are not used (Q15);
  - its network is governed by §2.2 and §2.6.9.
- **supermemory-local and the in-process Python arms:** no shell tools; the lists above apply.

**2.6.7 Proxy boundary** (§4.4):
- it binds 127.0.0.1 only, with per-arm ports and tokens;
- it strips the client's Authorization and x-api-key, and forwards only allowlisted headers;
- it reads the real key by path, and never prints, logs or writes it.

**2.6.8 The Claude Code route and the owner's yes (Q19).**
- The request on file asked for "claude -p under Max".
- The route this file would run is different: the polygon-pinned `claude -p` against DeepSeek's Anthropic endpoint,
  through the proxy, with tools locked per §2.6.6.
- The owner is not pinged again (LOCAL-TASK-D §9).

<!-- rev2-slot: claude-code-route -->
Decided at revision 2, from the record on file.
- (a) A yes that names this exact route is on file before the anchor: claude-code-memory runs under §2.6.
- (b) Otherwise, claude-code-memory is needs-other-env:owner-yes for v3, and E5 states the route the yes would have to
  name.
<!-- /rev2-slot -->

**2.6.9 Canaries and witnesses.** They run in A9, then in block 1 of every stand. The auditor signs off the A9 results
before the anchor (CL1).
- **Leak canaries.** Each is a random string, recorded by hash. The proxy scans every outbound body in memory, and
  canary_hits must be 0.
  - a decoy secret variable in the scheduler's own environment, which must never reach a child;
  - a decoy CLAUDE.md in the fake home's .claude\, which is read only if CLAUDE_CONFIG_DIR is not honoured;
  - a decoy credentials file in the fake home.
- **Owner markers.**
  - **The markers:**
    - the owner's name, in Latin and Cyrillic forms;
    - the owner's email and git identity, from the repository's `git config user.name` and `user.email`;
    - the home path (the scheduler's USERPROFILE);
    - hashed shingles of ~/.claude/CLAUDE.md and ~/.claude/rules/*.md.
  - **Handling:** the proxy, the single instrument exception (§2.6.1), reads those files at start and computes salted
    hashes of normalised n-grams in memory. They are derived owner data, so they are never persisted anywhere ([RULING]).
    Nothing is written under D:\Coding\_secrets\, which holds secrets only, or anywhere else. The hashes are recomputed
    on restart.
  - owner_marker_hits must be 0. Only counts are written.
- **Ancestor check for Claude Code.**
  - Claude Code's documented project-memory discovery reads CLAUDE.md files in the cwd's ancestors.
  - Before any Claude Code spawn, the harness checks by name only that no CLAUDE.md, CLAUDE.local.md or .claude\ exists
    in any ancestor of the runs tree (D:\Coding\_nevertwice_polygon, D:\Coding, D:\).
  - A decoy at the runs-tree root shows whether discovery is active (ancestor_canary_hits). It is published, and it is
    not a leak by itself.
- **Egress witness.**
  - **Native processes:** psutil samples, every second, the connections of every process in each arm's tree. Any
    non-loopback connection outside the fetch and install windows is a hit (egress_hits).
  - **Containers (Letta, FalkorDB):** every 5 s, `docker exec <container> cat /proc/net/tcp /proc/net/tcp6` is parsed.
    Any connection to a remote address other than the host gateway's proxy and Ollama ports is a hit. This is Letta's
    only network control, and it is declared.
  - **The catcher:** the proxy's egress catcher records (host only) every request a child sends to HTTP(S)_PROXY, and
    refuses it.
- **Filesystem witness ([RULING] G1).**
  - **Method:** before and after each check, it takes names, sizes and mtimes. No content is read.
  - **Persisted:** only per-directory digests of the sorted (name, size, mtime) tuples. Names stay in memory, for the
    counts.
  - **Watched set.** Legitimate writers are excluded by this list, not by judgement at the time:
    - **the repository's working tree,** except .git/**, .loop/**, research/v3/results/** and every __pycache__/
      directory. HEAD and the tracked files are checked separately (§1.3);
    - **a fixed list of owner config paths** that no legitimate process writes during a stand: ~/.claude/settings.json,
      ~/.claude/CLAUDE.md, ~/.claude/rules/**, ~/.claude/hooks/**, ~/.claude/scripts/**, ~/.codex/config.toml,
      ~/.mem0/**, ~/.gitconfig, ~/.npmrc, ~/.config/git/**;
    - **the polygon's idle entries:** backups, core_bare, bare314, the v2 venvs, llama.cpp, and h2h_v2_stores until its
      set-aside;
    - **D:\Coding\_secrets;**
    - **the worktrees** `before` and `engine_ef8120d`, until they are removed;
    - **the quarantine,** by the size and mtime of its root and of its already-known top-level entries only.
      D:\Nevertwice_Conservation is checked the same way: root and top level only.
  - **The quarantine is on every agent's and tool's search deny list:** no glob, no grep, no recursive walk
    ([RULING], after the drafting agent's name-only glob listed names inside it).
  - **Declared limits, not watched:**
    - ~/.claude/projects, ~/.claude.json, ~/.claude/.credentials.json (credential refreshes), ~/.local/share/claude,
      and the npm and HF caches in the owner's home. Live Claude Code sessions and other owner processes write these
      continuously;
    - D:\Obsidian\Claude_Memory, which is never walked (the owner's instruction not to read it);
    - D:\Local_AI_Models, the Ollama service's own store.
  - **A hit:** any difference in the watched set between before and after (fs_hits).
- **Any hit** invalidates the stand's rows (P0h). P2 handles the arm-run: a repair is possible only after the cause is
  removed without a code change. A fix that is code means a new anchor.

## 3. Stands and benchmarks

### 3.1 Stands

| Stand | Data (pinned at fetch: HF revision + sha256) | Licence | Protocol | n; runs | Scoring | Label; role |
|---|---|---|---|---|---|---|
| S1 LME-S | xiaowu0162/longmemeval-cleaned `longmemeval_s_cleaned.json` (Q4 a) | MIT | per-question haystack (official), fresh store per question | n_S 200, floor 120 (§3.4); 2 runs | official per-type judge prompts (J1; J2 on 25 %); EM/F1 twin; R@k where attributable | tuned-on; descriptive |
| S2 LME-M | `longmemeval_m_cleaned.json` | MIT | per-question haystack; also the same questions' S haystacks, for a paired scale drop | n_M 50 = S1's first 50, floor 30; retrieval tier only (Q7 a) | R@1/3/5/10, MRR@10 | tuned-on; descriptive |
| S3 LME-oracle | `longmemeval_oracle.json` of the cleaned repo (one pin if byte-identical to the current pin) | MIT | reader-ceiling bracket on S1's questions | S1 list | as S1 | bracket |
| S4 LoCoMo | snap-research/locomo `data/locomo10.json` (current pin 79fa87e9...) | CC BY-NC 4.0, research statements only | per conversation (10 stores) | cats 1-4: 1,540; cat 5: 446; 3 runs | token F1 (official) is the stand's primary metric, descriptive; J (Mem0-paper prompt) descriptive (Q18); cat 5: official string check + false-abstention rate on 1-4; R@k by evidence dia_id | tuned-on; descriptive |
| S5 BEAM | Mohammadta/BEAM, the 128K split (the README says 128K, the HF card 100K; 20 conversations) | data CC BY-SA 4.0, code MIT | per conversation (20 stores) | 400 (20 per conversation, 2 per ability); 2 runs | the pinned official scoring code: nugget judge (J1, J2 on all) on nine abilities, Kendall tau-b on event ordering; the ability classes fixed by name in §8.4 | untouched; overall confirmatory (axis A), abilities descriptive |
| S6 FactConsolidation | ai-hyz/MemoryAgentBench Conflict_Resolution: FC-SH and FC-MH × 6K/32K/64K/262K | MIT | 512-token chunks fed in order, one store per row | FC-SH 4 rows (60-100 questions each), 5 runs; FC-MH 6K and 32K, 5 runs; FC-MH 64K and 262K secondary, 2 runs | exact match, no judge | untouched; FC-SH confirmatory (axis B), FC-MH descriptive |
| S6L | as S6 | MIT | as S6, with qwen3-coder:30b (06c1097efce0) as writer and reader | FC-SH + FC-MH 6K and 32K; 2 runs | exact match | untouched; secondary (gives A-MEM a row) |
| S7 AMA-Bench SWE | AMA-bench/AMA-bench, SWE domain | MIT | each trajectory ingested once; questions answered offline | all 432 QA (34 or 36 trajectories, confirmed at fetch); 3 runs | official AMA judge prompt (J1, J2 on all); EM/F1 twin | untouched; confirmatory (axis F) |
| S8 speed | S4 run-1 stores; the first 10 S2 haystacks | - | §7 | 500 queries per arm | p50, p95, mean | descriptive |
| S9 h2h_s (owner D1) | original `longmemeval_s` pin 08d8dad4... (Q4 a) | MIT | global pool, 19,829 sessions (19,206 non-empty), retrieval tier | 500 | R@k, MRR@10 | tuned-on; secondary |
| S10 our corpora | supersession_v1 (explicit, implicit), asof | ours | as in v2, on its v2 LLM (qwen3-coder:30b, digest-pinned) | 80 / 60 cases | stand metrics | "our corpus" |
| S11 product-internal (reserved) | only the blocks register_plan (ii) names | - | local; no cloud call | per register_plan | per stand | never in a headline |

**Stand notes:**
- **S1 and S9** use different LongMemEval files (Q4 a). Every cell names its file.
- **S6:** the four FC-SH rows are fixed strata (§9.2). On FC-MH, every published system scores ≤ 5 %.
- **S7:** questions about observation state cannot be answered through our shipped reader, which drops tool_result
  blocks. This is named as a reader and schema layer.
- **Retrieval tier on S5-S7:** nevertwice-ranker, mem0-store, langmem-store, chroma-store and bm25-floor. They run over
  per-turn items (S5), the benchmark's 512-token chunks (S6) and per-step items (S7), and the stand reader reads them at
  Point B.
- **No smoke, pilot or probe unit comes from a list that a judged or confirmatory cell scores** (§9.4, §8.5).
- **Licences (T30):**
  - data is never committed; it is downloaded and pinned by sha256;
  - subsamples are published as id lists with the seed, never as data files;
  - no ND-licensed set is used;
  - every stand card carries its licence line;
  - LoCoMo results are research statements only;
  - BEAM's share-alike clause applies to derived data, which v3 does not publish.

**Why each benchmark:**
- **LongMemEval:** a member of the field's standard pair, and named by the owner. The cleaned files are the maintained
  version; the original repo is marked deprecated.
- **LoCoMo:** the other standard, with the lowest ingestion cost. Its known answer-key errors (6.4 %) and the lenient J
  judge are declared.
- **BEAM:** the only long-memory QA set with published competitor numbers that this repository never touched (T4). Its
  10 abilities cover update and time.
- **FactConsolidation:** a public, deterministically scored (exact match) supersession test (T15), with published
  numbers for Mem0, Zep, Cognee and MemGPT.
- **AMA-Bench SWE:** the only public, pinnable benchmark found that swaps the memory layer while holding a coding agent's
  trajectories fixed (T18).

<!-- rev2-slot: dataset-facts -->
Recorded at A3 (fetch), with the file shas. Revision 2 writes one branch per item. Every rule is fixed here.
- **Counts:**
  - AMA-Bench SWE: trajectories (34 or 36) and questions (432);
  - BEAM 128K split: conversations (20) and questions (400);
  - the question count of each FC row;
  - LoCoMo: the category counts must equal 841/282/321/96/446. Otherwise, the stand is re-pinned before the anchor.
- **BEAM split label:** the HF config name the pin uses.
- **BEAM ability names, as found** in the pinned data. The slot records them and never re-classifies an ability away
  from the §8.4 name rule. An ability whose name is on none of the §8.4 lists is judged on the full answer (the
  long-form rule), and is listed here.
- **BEAM dates:**
  - BEAM carries per-session timestamps → dates travel per §5.3;
  - it carries none → no arm receives a date on S5.
- **Gold evidence ids, per stand:**
  - present → the oracle bracket, R@k and the loss masking of §9.3 use them;
  - absent → the oracle bracket is "not available (no gold evidence)", and loss sensitivity uses worst-case imputation.
- **Smoke and probe units:** the identities of the units that the fixed rules of §9.4 and §8.5 select.
<!-- /rev2-slot -->

### 3.2 Arms × stands

| | S1 | S2 | S4 | S5 | S6 | S6L | S7 | S8 | S9 | S10 |
|---|---|---|---|---|---|---|---|---|---|---|
| product arms | x (B, K, V) | - | x | x | x | x (local LLM) | x | x (search) | - | nevertwice, mem0, zep-graphiti, naive |
| nevertwice-ablation | x | - | x | x | x | - | x | - | - | - |
| nevertwice-rawtext | - | - | - | - | - | - | x | - | - | - |
| retrieval arms | - | x | x | x (ranker-only) | x (ranker-only) | - | x (ranker-only) | x | x | - |
| brackets | x | - | x | x | x | x | x | - | - | - |
| all-0 temperature row | - | - | - | - | x (FC-SH) | - | - | - | - | - |

### 3.3 Axis map and headline rule

| Axis | Confirmatory (Point B, §9.2) | Descriptive, labelled |
|---|---|---|
| A long-memory QA/retrieval | S5 overall | S1 and S4 (tuned-on); S2 (scale, retrieval tier); S9; S5 ablation and ranker-only rows |
| B update/supersession | S6 FC-SH | S5 knowledge-update and contradiction resolution; S1 knowledge-update (its judge accepts old + new values: lenient); S6 FC-MH; the S6 all-0 row; S6L; S10 (our corpus) |
| C time | none (per-ability MDE about 25 pp) | S5 temporal + event ordering (tau-b); S1 temporal-reasoning; S4 temporal |
| D tokens | none | §6, per stand |
| E speed | none | S8; the network row is never ordered |
| F coding-agent memory | S7 | the S7 raw-text, ablation and ranker-only rows; the construct gap (§12 N5) |

- Verdict words come only from confirmatory cells. The headline sentence of an axis comes only from them.
- Every headline sentence names its layer: writer-domain, window, schema, ranking, supersession or time (K79).
- Tuned-on cells carry the label. An advantage that holds on one benchmark only is called that (LOCAL-TASK-D §3.3).

### 3.4 Subsample, orders and sizes (T10, Q6)
- **S1/S2 construction:**
  - research/v3/subsample.py is committed before the anchor, with seed 20260926.
  - Strata are question_type × abstention flag. The published counts are SSU 70, SSA 56, SSP 30, MS 133, KU 78 and
    TR 133, with 30 abstention items among them.
  - Each cell is permuted with the seed. The cells are then interleaved into one nested order in which every prefix is
    proportionally stratified (largest remainder).
- **Unit orders for S4-S7:** a seeded permutation of the units (conversations; rows by length tier; trajectories) is
  committed under research/v3/lists/. Every prefix is therefore declared in advance.
- **Splits:**
  - S1 = the first n_S of the order;
  - S2 = the first n_M;
  - the smoke split = the 20 questions at the tail (positions 481-500). It is disjoint from S1, S2 and S3 by
    construction;
  - the list shas are in FREEZE-V3 before any arm runs.
- **Sizes:** n_S 200 (floor 120), and n_M 50 (floor 30).
- **Floors for the other stands:**
  - S4: 5 conversations;
  - S5: 10 conversations;
  - S6: the FC-SH 6K, 32K and 64K rows;
  - S7: 24 trajectories.
- **Lowering a size:** only by the budget rule (§5.6).
  - Each arm runs the largest committed prefix whose projection fits the stand's budget.
  - Below the floor, the arm is needs-other-env:compute-budget(<projected h>) on that stand.
  - Pairs are compared on their common prefix. This holds for confirmatory and descriptive cells alike, and a
    descriptive CI uses the common prefix too.
  - A confirmatory member's MDE is re-simulated at that prefix (K82).
  - Because the orders are nested prefixes, lowering n cannot select questions.
- **Power table:** revision 0's table assumed independent paired questions, and it is withdrawn.
  - The auditor's simulation of the rules as written gave P(claim) ≤ 0.06 at a true 5 pp lead, on every headline stand.
  - The four FC-SH rows made "CI excludes 0" equivalent to four agreeing signs, with null p 0.125.
  - K82 replaces the table.

<!-- rev2-slot: sizes-and-budgets -->
From A9's projection formula (§5.6), per stand and arm:
- the projected hours for the full list;
- the largest committed prefix inside the stand's budget;
- the floor check;
- the common prefix of each pair.

Branches, per stand:
- (a) every runnable arm fits the full list → n as declared;
- (b) some arm does not → that arm runs its prefix, and its pairs use the common prefix;
- (c) an arm cannot reach the floor → needs-other-env:compute-budget(<projected h>) for that arm on that stand.

Budgets, floors and the prefix orders do not change.
<!-- /rev2-slot -->

## 4. The LLM

### 4.1 Decision and argument (T23, Q1)
- **One pinned LLM** for every writer and for the reader on S1-S7: DeepSeek `deepseek-flash`, in non-thinking mode,
  through the recording proxy (§4.4).
- **Facts from DeepSeek's docs, read on 2026-09-26:**
  - deepseek-flash serves DeepSeek-V4.1-Flash since 2026-09-10, the newest change-log entry. The legacy name
    deepseek-v4-flash is routed to the same model. deepseek-chat was scheduled for discontinuation on 2026-07-24.
  - Context is 1M tokens.
  - JSON output is json_object only. Tool calls are supported.
  - **Thinking is the default mode (on, effort high).** On the OpenAI-format endpoint it is turned off with a top-level
    `"thinking": {"type": "disabled"}` (extra_body in the SDK). The Anthropic-format endpoint supports the `thinking`
    field and ignores budget_tokens. The value that turns thinking off there is [U]; the thinking-default slot settles
    it.
  - Temperature has no effect in thinking mode.
  - The Anthropic-format endpoint maps claude-opus* model names to deepseek-v4-pro, and claude-haiku* and claude-sonnet*
    names to deepseek-flash.
- **Other stands:** S10 keeps its v2 LLM. S6L runs qwen3-coder:30b locally, as both writer and reader. The judges are
  separate (§8).
- **What each vendor's docs say:**
  - **Mem0:** documents a deepseek provider. json_object is enough for its fact list.
  - **Graphiti:** OpenAIGenericClient serves OpenAI-compatible providers. Its README says to use json_object for
    providers that do not honour json_schema, and DeepSeek offers json_object only. Graphiti "works best with"
    OpenAI/Anthropic/Gemini-class models. Locally, json_object failed 3 of 5 episodes in v2 (_graphiti_arm.py:9-14).
  - **LangMem:** extracts through tool calls (trustcall). DeepSeek supports tool calls; ChatOllama tool calls have known
    failures.
  - **Cognee:** routes through LiteLLM and reaches DeepSeek via the custom provider. Its model matrix marks qwen2.5:7b
    "Problematic" and recommends at least 14B locally.
  - **Letta:** lists DeepSeek as a provider. It warns that weaker models behave unexpectedly, and it needs tool calls.
  - **Supermemory-local:** takes an OpenAI-compatible LLM through environment variables.
  - **Claude Code:** DeepSeek documents an Anthropic-compatible endpoint for it, so the product's own writer runs on the
    pinned LLM (T7).
  - **A-MEM:** both upstream controllers send json_schema, so blocked:structured-output is expected.
- **Why not a local model of 14B or more:**
  - Throughput. v2 measured 221 min for amem_full (head_to_head_v2.json ingest_s 13,271.3), and 62 min for mem0_infer +
    langmem_full, over 940 sessions on a local 7B. Those were per-session writes, so no v2 timing measures a
    per-message writer. S1 at n = 200 is about 98.8k message writes per run, since LME-S averages 493.9 messages per
    haystack.
  - One GPU would be shared by the writer, the embedder and the judge.
  - Graphiti's json_object mode and Letta/LangMem tool calls are weaker on local models.
  - What local buys (a digest pin) is replaced by §4.3.
- **Why flash, not v4-pro:** at list price, v4-pro costs about 4.4× per cache-miss token, with the same drift exposure.

### 4.2 Consequences
- **T28 (our arm's fallback):**
  - generate_json falls back to Ollama on any cloud failure (_engine_store.py:1017-1040). v3 runs switch the fallback
    off (TB3), and a failed cloud call counts as fail.
  - Every artifact with our arm carries api.m._LLM_STATS.
  - fallback_local = its "ollama" count, plus any Ollama generation-path call the pacer or the proxy saw for the arm.
    It must be 0 (K56).
  - The stand sets NEVERTWICE_CLOUD, NEVERTWICE_DEEPSEEK_MODEL, DEEPSEEK_URL and DEEPSEEK_API_KEY (§2.2).
    - It does so after sandbox_guard.isolate(), which sets the cloud to none and XRERANK to 0
      (sandbox_guard.py:373-374).
    - It does so before the engine import, because the engine binds its model at import.
  - The label is running_llm(), and it must equal the proxy's model record for the arm.
- **Temperature:** every product at its shipped default (§5.5). Ours is 0.2. Revision 0's "ours at 0" is withdrawn.
- **Thinking: one rule (Q2, C3 [RULING (b2)]).**
  - Each product turns thinking off through its documented route (§2.2.1).
  - If the provider default is thinking on (branch (b) of the slot below, the expected branch), an arm with no
    documented route gets exactly one declared per-arm proxy fallback:
    - the proxy sets the single thinking field of that endpoint class in that arm's requests, and nothing else;
    - it is recorded per call as thinking_injected;
    - it is listed in the arm's deviations.
  - In branch (a), no fallback is applied.
  - Every response is checked either way: reasoning tokens must be 0 on every response (K58).
- **Our DeepSeek backend (TB3(b)):** it sends `"thinking": {"type": "disabled"}` itself.
  - This is a product bug fix: with thinking on, B1's 4,096-token cap truncates the JSON of every DeepSeek-backend user.
  - It lands as its own gated product commit, with a regression test, before the anchor. It is not a stand-side setting.
- **Output cap:** our calls keep B1's 4,096-token cap. finish_reason=length is counted per arm. The count is
  descriptive; P1 counts only unrecovered losses.
- **Empty JSON:** DeepSeek's JSON mode "may occasionally return empty content". empty_content and json_invalid are
  counted per arm (descriptive). P1 prices losses by logical outcome (K75).

<!-- rev2-slot: thinking-default -->
**What the pilot (A9) records:**
- per endpoint class, whether a request with no thinking field returns reasoning tokens;
- for each arm, whether its documented route yields zero reasoning tokens.

DeepSeek's docs, read on 2026-09-26, say thinking is the default, so branch (b) is the expected one.

**Branches:**
- (a) The provider default is non-thinking: nothing changes. Every arm runs as shipped; no fallback is applied; and
  thinking_calls stays at zero tolerance.
- (b) The provider default is thinking on:
  - every arm turns thinking off through its documented route;
  - an arm with no documented route runs under the declared per-arm proxy fallback (thinking_injected, listed in its
    deviations; C3 [RULING (b2)]);
  - an arm whose responses still carry reasoning tokens, under its route or under the fallback, is
    blocked:thinking-uncontrollable. E5 names that as a consequence of the pinned LLM. Such an arm is never kept with
    reasoning on, and never silently dropped.

**The /anthropic off switch** ([U]: the two DeepSeek pages disagree). The pilot probe sends each candidate field on the
/anthropic endpoint, and records which one yields zero thinking blocks:
- (b-i) `"thinking": {"type": "disabled"}` works: it is the field for /anthropic too;
- (b-ii) only `"reasoning": {"effort": "none"}` works: that field is the fallback for /anthropic arms;
- (b-iii) neither works: every /anthropic arm is blocked:thinking-uncontrollable.

Beyond the declared per-arm fallback, the proxy changes no request in either branch.
<!-- /rev2-slot -->

### 4.3 Model identity and fingerprints (T1; [RULING])
- **a) Pinned names.**
  - deepseek-flash on every arm port and every reader port; deepseek-v4-pro only on the J3 port.
  - The proxy refuses any other name and counts it as model_mismatch (zero tolerance).
- **b) Records.** The response `model` and `system_fingerprint` are recorded for every call. Only /v1 returns
  fingerprints; /anthropic returns none.
- **c) Model identity, at zero tolerance.** Either of these is a model event:
  - a new value of the response `model`;
  - a change-log entry that changes what deepseek-flash serves.
  The change log is read at every STAND START and STAND END (STATUS), and at every BLOCK START (the proxy JSONL). It
  gives dates only [U times], so the event time is taken conservatively: the start of the entry's date in UTC+8, minus
  12 h. Scoping follows P2.
- **d) Fingerprints never invalidate a stand alone.**
  - Transitions are recorded per endpoint class.
  - A unit is "straddled" if any arm's calls for it ran under more than one fingerprint.
  - A pre-declared sensitivity analysis recomputes every verdict without the straddled units. A verdict that changes
    becomes "unresolved (fingerprint-sensitive)".
  - /v1 is compared within /v1; /anthropic is compared by model only.
  - Revision 0's 10 pp exposure rule and the Q3(b) ratchet are deleted. As written, rule (e) would have tripped from
    sampling noise alone, with P about 0.53-0.57 per run on S4 and S6.
- **e) Pilot record.** The pilot records fingerprint rotation over at least 12 h, and publishes it. No rule depends on
  it.

### 4.4 Recording proxy (T2, K84)
- **Process and ports:**
  - One local process, research/_llm_proxy.py, binding 127.0.0.1 only.
  - One port per arm, one reader port per arm, the J3 port and an egress-catcher port.
  - Each arm has a per-arm token, and the dummy key doubles as that token. A request without it is refused locally and
    counted, and never forwarded.
  - Its per-call JSONL lives in the runs tree.
- **Forwarding:**
  - to https://api.deepseek.com (/v1 and /anthropic), with the real key read by path from
    D:\Coding\_secrets\deepseek.env. The key is never printed, logged or written;
  - the client's Authorization and x-api-key are stripped, and only allowlisted headers are forwarded.
- **Byte for byte:**
  - The upstream response is streamed to the client as it arrives, SSE included, with keep-alive comments and blank
    lines passed through. A copy is teed to an in-memory parser after forwarding.
  - The proxy never buffers before forwarding, never retries on the cloud leg, and never caps or queues.
  - It never changes a request: not temperature, max_tokens, model, response_format or stream options. A request it
    would have to change is refused and counted instead (model_mismatch).
  - The one exception is the declared per-arm thinking fallback (§4.2):
    - only under branch (b) of the thinking-default slot;
    - only for an arm listed with no documented route;
    - only the one thinking field;
    - recorded per call as thinking_injected.
  - On a client disconnect, it cancels upstream and records client_abandoned.
- **Raw-forward mode**, used for the A/B (§4.6) and for the DeepSeek captures (A2):
  - a byte pipe that injects the key header, plus, for an arm under the declared thinking fallback, the same one body
    field;
  - nothing else: no buffering, no parsing, no taxonomy, no retries;
  - it is the same process, with the same key handling.
- **Ollama leg:** a counting pass-through to the local Ollama.
  - Who uses it: the out-of-process arms (Letta, supermemory-local), every arm during S8 (§7), and every arm under the
    embed-ceiling slot (§5.1).
  - It applies the pacer's local policy, with the pacer's own code and constants (K33):
    - the same pace floor, 0.125 s per arm;
    - in pace mode, the same bounded retries: port exhaustion (WinError 10048/10055) 16 × 15 s; generation-path 5xx 2
      times, 15 s then 30 s; 4xx never;
    - in observe mode, none.
  - The leg is local, not cloud, so the cloud leg's no-retry rule does not apply to it (G8).
- **Attribution:**
  - **arm:** by port;
  - **unit:** by a URL path prefix `/u/<unit>`, where the product takes a base URL per process, or per agent (Letta);
  - **phase:** by the block's stage windows (write stage, then question stage; §5.6), and by port (reader, J3);
  - **the block rule**, for server arms without per-unit URLs (supermemory-local; Letta if per-agent endpoints are
    ignored):
    - a call attributable only to a block is charged to every unit of that block whose write stage overlapped it, for
      P1 masking and for P2's re-run decisions;
    - token totals are exact per arm-run, and per unit only as block shares (declared).
- **Per-call record**, with no bodies, in an append-only JSONL:
  - arm, run, unit (or block), phase, utc start and end, endpoint;
  - requested model, response model, system_fingerprint, status, time to first byte, latency, stream flag;
  - request-key hash (§4.5);
  - usage {prompt, completion, cache_hit, cache_miss, reasoning}, finish_reason, response_format type;
  - temperature, max_tokens and thinking as sent, and thinking_injected;
  - whether a json_object response parsed, and whether content was empty;
  - the tool names offered and called;
  - canary, owner-marker and ancestor-canary counts.
  Summaries are derived from the JSONL. An invalid flag survives proxy restarts (K35).
- **In memory only:**
  - the outbound body scan (§2.6.9);
  - the owner-marker hashes;
  - the response parse (usage, finish_reason, tool names, reasoning).
  Only counts are written.
- **Thinking check:** reasoning tokens > 0 on /v1, or a thinking block on /anthropic, counts as thinking_calls.
- **Selftest:**
  - fake OpenAI, Anthropic-SSE and Ollama upstreams, plus real DeepSeek captures made through raw-forward (A2) and pinned
    by sha256. The captures cover non-stream and SSE, both endpoint classes, and keep-alive comments;
  - mutation tests that turn red by name:
    - drop a count;
    - buffer before forwarding;
    - retry on the cloud leg;
    - change a field other than the declared fallback, or apply the fallback outside branch (b);
    - leak the key into a log;
    - miss a canary;
    - skip the Ollama-leg retry policy;
  - a logged pre-step in every arm environment, beside _pacer_selftest.py.

### 4.5 Transport taxonomy, incidents and halts (K85)
- **Request key:** sha256(canonical request body ‖ arm). Only the hash is kept, never the body.
- **transport_recovered:** a key that failed (non-2xx, reset, timeout, client_abandoned) and then succeeded within 30
  min, by the product's own retry or by the stand's re-ask. It is published, and never invalidates.
- **transport_lost:** a write-phase key that never succeeded. It is counted once, in P1, per unit. P1 precedes P0b for
  write-phase transport.
- **The order for a read-, answer- or judge-phase failure (W3):**
  1. the client's own retries;
  2. the stand's re-asks: at most 2, at least 5 min apart;
  3. if the call is still unrecovered, the question is dropped for every arm (P2), and the drop counts toward the 5 %
     limit.
  failed_outcomes (m5, must be 0) counts a key that never succeeded on a question that was not dropped.
- **upstream_errors and client_abandoned** are descriptive. client_abandoned must be 0 in the A/B.
- **Incident gate** (in the scheduler):
  - ≥ 5 upstream failures across ≥ 2 arms within 60 s opens an incident (a STATUS INCIDENT START line).
  - The scheduler then starts no new unit. Units in flight continue under their products' own semantics.
  - A canary request (scheduler port, phase canary) is sent every 60 s. Two consecutive successes close the incident
    (INCIDENT END).
  - Units with a transport_lost key or a failed outcome inside the window, for any arm, are re-run for every arm (P2,
    exogenous).
- **Halts:**
  - A 401, 402 or 403 from upstream halts the campaign (INCIDENT kind=401, 402 or 403).
  - The units in flight become exogenous re-runs once the cause is removed.
  - A 402 or a balance shortfall waits for the owner. That is the LOCAL-TASK-D §9 case "all remaining items wait only
    for the owner".
- **Balance:**
  - A stand starts only if the DeepSeek balance (GET /user/balance, through the proxy) is ≥ 2× the stand's projected
    cost from A9's formula. The balance is recorded at STAND START.
  - If the pilot finds the balance endpoint unavailable, the 402 halt alone applies, and E5 declares it.
- **Zero tolerance stays for:** model_mismatch, thinking_calls, fallback_local, cloud_bypass, tool_violation and
  failed_outcomes.

### 4.6 Independent reconciliation and the recording-vs-raw-forward A/B (K87)
**Per arm-run.** Each check holds within the branch that the fence below fixes. Otherwise the arm-run is invalid (P0j).
1. **Proxy vs adapter (HTTP level):** in-process arms count requests with a counting-only httpx or requests event hook.
2. **Proxy vs product (logical level):**
   - Sources, the product's own report: our api.m._LLM_STATS deltas; a counting LLM-client wrapper for Graphiti (the v2
     pattern, _graphiti_arm.py:76-82); Letta's per-step usage; Claude Code's `--output-format json` usage; LiteLLM
     callbacks for Cognee; and a per-call line in supermemory-local's server log, if it writes one.
   - Logical calls ≤ HTTP calls; the difference is published as product retries.
   - Tokens agree within ±0.5 % where both sides count them.
3. **Ollama:** the Ollama server.log per stage, against the pacer plus the proxy's Ollama leg.
4. **Provider (C4 [RULING]):** ONE DeepSeek key.
   - DeepSeek's usage export is compared with the proxy's campaign-wide totals.
   - A 402, or a balance below 2× the next stand's projection, is the "everything waits for the owner" condition. It
     means one summary request, not a new class of ping.

<!-- rev2-slot: reconciliation-granularity -->
Decided at A9 from what each source exposes. No tolerance applies other than these.
- **Checks 1-2, per arm:**
  - (a) an in-process adapter counter exists → exact;
  - (b) only a product-side counter exists (out-of-process arms) → ±1 % of calls and tokens against it;
  - (c) no second counter exists → the arm-run is valid and labelled "single-witness" iff both hold:
    - the recording-vs-raw-forward A/B (below) passed for the arm's surface class, the out-of-process SSE leg: the
      ab-arm slot's out-of-process arm ran and was in tolerance;
    - its store footprint is > 0 on every unit: every evaluation unit's store holds ≥ 1 item at the end of the write
      stage (K76).
    E5 names the arm and the label. If either condition fails, every arm-run of that arm is invalid (P0j), and E5
    names the arm.
- **Check 3:**
  - (a) server.log has one line per request → exact, per stage;
  - (b) only per-minute totals → ±1 % per stage;
  - (c) no usable server.log → the Ollama witness is declared absent, and the pacer and the proxy's Ollama leg stand
    alone. E5 says so.
- **Check 4:**
  - (a) usage per UTC day and model → ±1 % of tokens per day;
  - (b) only coarser totals → ±1 % at that granularity;
  - (c) no export → declared absent.
<!-- /rev2-slot -->

**A/B, recording vs raw-forward (C12 [RULING]).** It runs before the anchor, and it blocks the anchor.
- **No key in any child.** No child environment ever holds the real key, not even once. Both legs go through the same
  proxy process, which injects the key.
- **Arms:**
  - mem0 (in-process, json_object), or nevertwice if mem0 is blocked;
  - one out-of-process arm (the ab-arm slot). It is omitted only if none is runnable, and that is declared.
- **Runs:** two raw-forward runs and two recording runs, on the first 3 smoke haystacks.
  - Raw-forward injects the key header and, for an arm under the declared thinking fallback, the same one body field
    (§4.4).
  - So the two legs differ only by what the recorder adds.
- **Hop latency:** a localhost microbenchmark of the proxy hop (raw-forward and recording, against a local echo
  upstream) is recorded beside the A/B.
- **In tolerance** iff each recording-run mean lies inside the range of the two raw-forward runs, widened by:
  - logical calls per unit: ±5 %;
  - input and output tokens per unit: ±10 %;
  - P1 lost_share: ±1 pp;
  - items stored per unit: ±10 %;
  - wall time per unit: +10 %, plus 20 ms per call.
  In addition, client_abandoned must be 0, and the time to first byte the proxy adds must be p50 ≤ 20 ms and p95 ≤
  100 ms.
- **Out of tolerance:** the proxy is fixed, and the A/B is repeated, before the anchor.

<!-- rev2-slot: ab-arm -->
- **The out-of-process arm:** the first runnable of letta and supermemory-local, preferring one whose A8 probe shows
  streaming requests. If neither is runnable: none, declared in E5, and the SSE path rests on the selftests with real
  captures.
- **The in-process arm:** mem0; nevertwice if mem0 is blocked.
- **The record:** every metric, both legs, and the hop microbenchmark, attached. Revision 2 can only record "in
  tolerance". An A/B out of tolerance blocks the anchor.
<!-- /rev2-slot -->

## 5. Fairness parameters

### 5.0 Symmetry table (PR3, K80)
- **One value or rule per knob, for every arm.**
  - Each arm's value goes in arm_decl.symmetry. The rules go in FREEZE-V3's `symmetry` key.
  - m5 v3 checks every arm_decl.symmetry entry against FREEZE-V3 `symmetry`, on every artifact, and FAILs any departure
    (C9 [RULING]; built).
  - m5 also checks llm, llm_transport, embedder, k, budget and input_sha256; the transport, boundary, p1 and yield
    blocks; the caches; constancy; and the shipped temperature through --freeze.
  - Knobs outside arm_decl are covered by harness assertions with fixture mutations.
- **A row where our arm departs from a rule fails.**

| Knob | The one rule | nevertwice | Others |
|---|---|---|---|
| Writer LLM | deepseek-flash through the proxy | same | same |
| Thinking | off through the product's documented route; for an arm without one, the declared per-arm fallback under branch (b) only | shipped disabled (TB3) | §2.2.1 |
| Write temperature | the product's shipped default, read from source or docs before the pilot; the proxy never changes it | 0.2 | FREEZE-V3 |
| Reader and judge temperature | 0, one reader for every arm | 0 | 0 |
| Write max_tokens | shipped value | 4,096 (B1) | shipped, recorded |
| Query-time now | wall clock; no benchmark-relative now | none passed | none |
| Write-date carriage | the product's own write-time field, else the uniform header | capture_session(date=) | §5.3 |
| Archive clock at capture | the product's own clock | datetime.now() at every capture (declared, §5.4) | products' own |
| Context renderer | the product's documented renderer, else "- <item>" lines | api.format_note verbatim | §2.2 |
| Date rendering | only from a per-item event-time field; add-time never | none (recall returns no date) | Graphiti valid/invalid; A-MEM timestamp |
| Point, k, budget | B: k 200, 7,000 cl100k (headline); K: k 10, 7,000; V: S1 only | same | same |
| Similarity threshold | shipped default, passed explicitly | the shipped floor and abstention gate | Mem0 0.1; others recorded |
| Write unit | the vendor harness's or recommended unit (T20) | session; S7 via the hook reader | §2.2 |
| Read call | the product's own search call | api.recall(..., xrerank=False) | §2.2 |
| Reader | the stand reader, one template per benchmark | same | same (Letta and Claude Code lose their own readers) |
| Tools | memory tools only, fixed per arm; shell/web/task/code names forbidden (§2.6.6) | none | §2.6.6 |
| Client timeout and retries | shipped; the proxy never retries on the cloud leg | 60 s, 2 retries | shipped, recorded |
| Local embed retries | the pacer's policy, in-process or on the proxy's Ollama leg | pacer | pacer or the Ollama leg, same constants |
| Provider prefix cache | on for all (not controllable); tokens counted regardless | same | same |
| Cross-encoder rerank | off, passed explicitly, counted | xrerank_calls 0 | not applicable |
| Embedder | one bge-m3 tag; stand truncation ≤ 2,048 tokens | same | same |
| Product-internal concurrency | shipped default, recorded | not applicable | e.g. SEMAPHORE_LIMIT |
| Unit isolation | fresh store and process per (arm, run, unit); servers per (arm, run, block) | same | same |
| Launch | §2.6 for every child | same | same |
| Local fallback | none; counted | fallback off (TB3) | off where documented, counted |
| Sensitivity temperature | all-0 row on S6 FC-SH only | NEVERTWICE_EXTRACT_TEMP=0 | documented parameter, or absent |

### 5.1 Embedder (T3, D1, K62)
- **One tag:** a single bge-m3 tag with the options D1 names (num_batch 2048), pinned by digest.
  - Every embedding arm calls it.
  - The options live in the tag, not in requests, so /api/embed and /v1/embeddings behave alike.
  - Creating the tag writes one new manifest into the owner's Ollama store. That is covered by D1, the owner's explicit
    decision (C13 [RULING]): it is a new tag, nothing is overwritten, its digest is recorded in FREEZE-V3, and no model
    is pulled.
  - Container images (FalkorDB, Letta) follow the v2 FalkorDB precedent: pinned digests, no Docker setting changed,
    listed in FREEZE-V3, and removed at cleanup unless the owner keeps them. LOCAL-TASK-D §10 names only polygon venvs,
    so E5 carries one line stating this as an interpretation.
- **Stand-side truncation:** every text the stand embeds, or hands a product to embed as one item, is cut by the stand
  to ≤ 2,048 bge-m3 tokens.
  - The tokenizer is BAAI/bge-m3, pinned.
  - The cut is explicit, counted per item, and identical for every arm.
- **Product-internal embeddings** go to the same tag. Inputs that hit the cap are counted per arm (embed_at_cap, from
  prompt_eval_count).
- **No shrink-retry:** an embed 400 is a failed outcome. It goes through P2 like any embed failure.
- **No vendor-default embedders:** there is no OpenAI key, and DeepSeek has no embedding API. The deviation is listed
  per arm. There is no embedder axis.

<!-- rev2-slot: embed-ceiling -->
A9 measures the sustained embed rate per process and in total, the pacer's port-exhaustion retries, and each arm's
embed calls per write unit. It projects the embed time per stand.
- (a) Every stand's projected embed time fits its budget at the measured ceiling → nothing changes.
- (b) Otherwise, every arm's Ollama traffic goes through the proxy's Ollama leg.
  - That leg keeps a keep-alive upstream pool and one global ceiling, shared round-robin per arm, on top of the pacer
    policy of §4.4.
  - This is a local-resource scheduler, not a cloud-leg intervention.
  - It is applied to every arm and re-measured. If it fits, it is adopted.
- (c) Otherwise, the stand's n falls under the budget rule (§3.4), for every arm alike.

Never by caching one arm's vectors.
<!-- /rev2-slot -->

### 5.2 Points: k and budget (T5, T24, K65; [RULING])
Budgets are counted with tiktoken cl100k_base, version pinned. The reader's usage.prompt_tokens is reported beside them.
- **Point B (primary, headline):**
  - every arm is asked for up to 200 ranked items;
  - the stand fills the context in rank order up to 7,000 tokens, and the last item is cut at the cap;
  - k = 200 and budget = 7,000 for all;
  - Claude Code uses "R-all". Letta uses its core blocks, then interleaved archival and recall hits;
  - per-arm median context tokens are printed.
- **Point K (secondary):**
  - k = 10 and a budget of 7,000 tokens for every arm;
  - per-arm median context tokens are printed. Our items fold "| earlier:" siblings (memory_search.py:254), so ten of
    our items can carry more than ten notes;
  - claude-code-memory is competitor-lacks-capability:k.
- **Row V (vendor default, S1 only, secondary):** each arm's documented retrieval, capped at 7,000 tokens;
  declared_axes ["k"].
  - Mem0: harness top_k 200.
  - Zep: 20 edges + 20 nodes.
  - Supermemory: limit 30, threshold 0.3.
  - Cognee: 20 + 20.
  - LangMem: limit 10.
  - A-MEM: retrieve_k 10.
  - Letta: its archival default.
  - Claude Code: "R-index".
  - Ours: k = PROMPT_RECALL_K = 3, the shipped depth (Q12).
- **Retrieval tier:** R@1/3/5/10 and MRR@10 where gold ids exist; Point B QA for the ranker-only rows.

### 5.3 Write unit and dates (T20, PR11)
- **Write units** are as in §2.2. Roles and times go in each API's own fields.
- **Date carriage, one rule, fixed now:**
  - An arm whose write API has a write-time date field gets the date there: ours (capture_session date=), Graphiti
    (reference_time) and A-MEM (time=).
  - Every other arm gets the uniform header "Conversation from <YYYY-MM-DD>:" as the first line of every write unit's
    content: Mem0 OSS (timestamp= raises ValueError), LangMem, Cognee, Letta, supermemory-local and Claude Code.
  - Stands without dates (FC, AMA) carry none for any arm.
- **Add-time:** a product's add-time is never presented as the event date.

### 5.4 "Now" (T19, T27 as revised; [RULING], Q11)
- **No arm gets a benchmark-relative query-time "now" in any row.** Every arm runs with wall-clock now. Mem0 OSS refuses
  reference_date= (mem0/memory/main.py:1432-1433), so a query-time now would be ours alone.
- **N2 (api.recall(now=)) is not built for v3.** It is not a shipped feature that any surface uses, and there is no N2
  row (§12 N13).
- **Declared effects on our arm under wall-clock now** (the "time" layer in E5):
  - **Write side.** capture_session runs archive_old_sessions and archive_old_typed after every stored session
    (api.py:685-690), with a cutoff from datetime.now() (_engine_store.py:298).
    - Every typed note dated more than 90 days before the run moves to Archive/ at write time. That is every note from
      LME, LoCoMo and BEAM.
    - It stays recallable (B2). But it leaves the live set that the same-session absorb and the near-duplicate
      reconcile read (_engine_notes.py:136-150, :260-270), so our write-side dedup and supersession see fewer notes.
  - **Read side (T27).** The salience decay (half-life 365 d, floor 0.5; _engine_recall.py:132-139, :153) puts every
    2023 note at the floor, so recency ranks nothing inside a benchmark haystack.
  - **Extractor.** Our date field reaches the note's stem and frontmatter, but not the extraction prompt, so the
    extractor cannot resolve relative dates.

### 5.5 Temperature (T21 as revised; [RULING], Q16, K70)
- **Every product writes at its shipped default**, ours included (0.2, _engine_store.py:960).
  - "Shipped" is read, before the pilot, from the installed package source (file and line) or the vendor's
    documentation at the pinned version, for each call site.
  - It is the value the arm's declared config (vendor-default or vendor-recommended) sets.
  - If the config sets none, it is the library default.
  - If the product sends no temperature, it is "provider default" (DeepSeek's 1.0), and the arm is listed.
  - A product with different values at different call sites records the set.
  - These values are written into FREEZE-V3's arms section at A8. That draft's sha256 is witnessed in STATE-C before
    A9. The values are never taken from the proxy's observations.
- **Records and checks:**
  - The proxy records temperature per call, and never changes it.
  - Every pilot call's value must equal FREEZE-V3's (CL7). A difference blocks the anchor until our configuration of
    that arm is corrected; the FREEZE value is never edited to fit.
  - m5 --freeze fails a row whose arm_decl.llm_params.temperature differs from FREEZE-V3.
- **Reader and judges:** 0 for every arm (symmetric).
- **Sensitivity row** (pre-declared, descriptive): S6 FC-SH only, 2 runs, in its own artifact with
  `sensitivity: "temperature-0"`, checked against FREEZE-V3 `sensitivity_rows`.
  - Every arm with a documented temperature parameter writes at 0. Ours does so through NEVERTWICE_EXTRACT_TEMP (TB3).
  - An arm without one is listed as competitor-lacks-capability:temperature in that artifact.
- **Values known from upstream,** each still read from the installed source at A8: ours 0.2; A-MEM 0.7 (upstream
  OpenAIController); Mem0's harness 0.1.

### 5.6 Schedule, budgets and order (PR8, K89)
- **Blocks:**
  - S1: 10 questions;
  - S4: 5 conversations;
  - S5: 10 conversations;
  - S6: length tiers {6K, 32K}, {64K}, {262K};
  - S7: 12 trajectories.
- **Runs in parallel:** all runs of a stand run as parallel units in the same block. Each has a separate process, a
  fresh store and a distinct run_id.
- **Stages within a block:** first a write stage (all units ingest; asynchronous writers wait until their status is
  "done"), then a question stage (reads and answers).
- **Barrier:** every arm finishes block b (END or ABORT) before any arm starts block b+1 (STATUS S3).
- **Time clause [RULING]:** M2's v2 clause "own and competitor arms never overlap" is replaced by: "every arm of a block
  runs in the same block window; no arm runs a block in a window the other arms do not share". It is declared, and
  flagged in E5.
- **Repair blocks** are appended after a stand's regular blocks (STATUS: BLOCK <stand>/repair-<n>, with RERUN lines).
  - **Exogenous:** a repair block runs the affected units for every arm and bracket, barriered like any block.
  - **Endogenous:** a repair block runs one arm's affected units (P2), with the same model identity. It is declared
    outside the time clause, because the other arms do not share its window.
- **Concurrency:**
  - per-arm unit concurrency = block size × runs, identical for every arm;
  - product-internal concurrency stays at its shipped default and is recorded. Graphiti's semaphore_gather creates a
    Semaphore(20) per call (graphiti-core helpers.py:127), so it has no process-wide limit;
  - the proxy never caps. A per-arm quota exists only through the concurrency-quota slot.
- **Per-unit wall-clock ceiling:**
  - 3 × the arm's pilot median seconds per input token × the unit's input tokens, and at least 10 min;
  - hitting it aborts the unit (STATUS ABORT). P2 treats that as endogenous.
- **Budgets (symmetric).** They cover all runs and the write and question stages; judge stages are excluded:
  - S6-SH: 24 h;
  - S6-MH: 24 h;
  - S5: 48 h;
  - S7: 24 h;
  - S4: 24 h;
  - S1: 72 h.
  §3.4 applies them to nested prefixes. A9 publishes, per arm and stand: the LLM and embed calls per write unit, tokens
  per call, p50 and p95 per sequential hop, the embed and port ceiling, and the projection formula.
- **Order [RULING]:**
  - S6-SH → S5 → S7 → S4 → S1;
  - then S6-MH (the 6K and 32K product rows; 64K and 262K secondary);
  - then the S6 all-0 row;
  - then the retrieval tier (S2, S9, and the ranker-only rows on S5-S7);
  - then S8, in an idle window (it needs the S4 run-1 stores);
  - then S6L, S10, and S11 if register_plan schedules it;
  - J1 and then J2 run per judged stand, after its question stage. J3 runs on its sample.
  - With PR7, a model event between stands no longer resets finished stands.
- **GPU:**
  - one resident Ollama model per stage: the embedder tag during write and question stages; J1 or J2 during judging;
    qwen3-coder:30b during S6L and S10;
  - each is unloaded with keep_alive 0 between stages;
  - the owner's Ollama settings are unchanged.

<!-- rev2-slot: per-unit-ceilings -->
Filled from the pilot: each arm's median seconds per input token, per stand, and the resulting ceiling values. The
factor 3 and the 10-minute floor do not change.
<!-- /rev2-slot -->

<!-- rev2-slot: concurrency-quota -->
- (a) At the declared unit concurrency, the pilot sees 429s or held connections on < 1 % of calls → no quota.
- (b) Otherwise: one per-arm unit concurrency, identical for every arm, halved until the pilot rate is < 1 %. The
  scheduler enforces it, never the proxy.
<!-- /rev2-slot -->

### 5.7 Caches (T29, T17, K60, K61)
- **Built in-campaign, alike or not at all:**
  - every cache an arm reads is built inside the campaign, at the anchor, per run, and for every arm alike. Otherwise
    no arm uses one;
  - this covers vector caches, contexts, answers, verdicts, ingest caches and product stores;
  - caches live in the runs tree or under research/v3/results/.
- **Build record:** every cache carries {stand, run_id, commit, utc, the build's ollama_transport and cloud_transport,
  items, dropped}.
- **Read record:** every row that reads a cache records caches[] = {path, sha256, built: {commit, utc, ollama_transport},
  hits, misses}. m5 --anchor checks it.
- **Invalid reads:** a row served from a cache with no build record, a commit other than the anchor, or a utc before the
  anchor is invalid (K60).
- **require_traffic:**
  - calls > 0 stays in code for the head_to_head competitor arms, supersession and asof;
  - a cache-reading arm declares embeds_via_ollama. It is valid iff misses ≤ ollama_transport.calls, and the build
    record shows calls > 0 with no failed outcome (K61).
- **Cost of cached work:** the time and tokens in a build record are charged to the arm that reads the cache.
- **DeepSeek's prefix cache:** a provider behaviour, on for every arm. Tokens count as input whether they hit or miss
  (§6).

## 6. Token accounting (T13, T13a, K69)
Per arm and run:
- **Write path:** every LLM call of the product's own pipeline (phase write): calls; input tokens, split into cache hit
  and miss; output tokens; reasoning tokens. Also embedding calls and their prompt_eval_count tokens.
- **Read path:** the product's LLM calls during retrieval (phase read), and the stand reader's call (phase answer).
- **Context per query:** the cl100k_base count of the context handed to the reader; and the reader's prompt_tokens minus
  the fixed overhead measured on the `none` bracket (token_floor.py:21-25). Per-arm medians at Points B and K are
  printed.
- **Footprint:** items stored, with their characters and tokens, at the end of ingestion.
- **Axis-D denominator:** the tokens each product actually read.
  - For ours: the characters that reached the extractor after the reader and truncate_smart.
  - For per-message writers: the message tokens sent.
  - Each arm's truncated share is printed beside it.
  - The reported figure is write-path tokens per 1,000 tokens read.
- **Our arm (T13a):** read api.m._LLM_STATS, never the bare memory_hook object. It is reconciled with the proxy like
  every arm (K87).
- **Cached work:** charged from the cache's build record (§5.7).
- **Cost:** list-price dollars, with the cache hit/miss split. Descriptive.

## 7. Timings and machine_idle (T12, T12a, K74) — S8
- **Speed axis, local operations only:**
  - (a) the latency of each arm's search call (query embedding + index lookup, no LLM), on the S4 run-1 stores, at Point
    B (headline) and Point K (secondary). 20 warm-up queries are excluded, then 500 LoCoMo questions run in a seeded
    order at concurrency 1. Reported as p50, p95 and mean;
  - (b) for the retrieval tier, ingest time without an LLM, over the first 10 S2 haystacks.
- **Identical hop:** during S8, every arm's Ollama traffic goes through the proxy's Ollama leg, in observe mode,
  in-process and out-of-process alike. So every arm pays the same localhost hop. The Ollama server.log counts the calls
  independently.
- **An arm whose search calls an LLM** is reported in the network row only.
- **Network row:** end-to-end write time per unit and answer latency with the cloud LLM, as median and p95, with the
  time of day and DeepSeek peak/off-peak recorded. It is never ordered.
- **Every timing artifact** runs the pacer and the proxy in observe mode, and P0 applies. It carries a measured
  machine_idle record, computed by research/_idle_probe.py and never written by hand:

```json
"machine_idle": {
  "idle": true,
  "rule": "v3-idle-1",
  "probe": {"tool": "research/_idle_probe.py", "commit": "<sha>"},
  "pre":  {"start_utc": "...", "seconds": 60, "interval_s": 1,
           "gpu_util_pct": {"p50": 0, "p95": 0, "max": 0}, "gpu_mem_used_mib": {"p50": 0, "max": 0},
           "cpu_total_pct": {"p50": 0, "p95": 0, "max": 0}, "ram_available_gib": {"min": 0}},
  "post": {"...": "the same shape, 60 s after the timed loop"},
  "during": {"interval_s": 5, "samples": 0,
             "foreign_processes": [{"name": "", "pid": 0, "cpu_pct_of_core_mean": 0, "gpu_mem_mib": 0, "rss_mib": 0}],
             "gpu_compute_apps": [{"name": "", "pid": 0, "used_mib": 0}],
             "ollama_resident": ["<embedder tag>@<digest>"]},
  "allowlist": ["<the stand's process tree>", "ollama*", "<the recording proxy>", "<the arm's declared service, e.g. its FalkorDB container>"],
  "thresholds": {"gpu_util_p95": 5, "gpu_util_max": 25, "cpu_total_p95": 10, "cpu_total_max": 35,
                 "ram_available_gib_min": 8, "foreign_cpu_pct_of_core_mean": 10,
                 "foreign_gpu_mem_mib": 256, "ollama_resident_only": ["<embedder tag>"]},
  "violations": []
}
```

- **How idle is computed:** idle is true iff violations is empty. The pre and post windows are checked against every
  threshold. The during window is checked for foreign processes and resident models only.
- **Sources:** NVML or `nvidia-smi --query-gpu=utilization.gpu,memory.used`; psutil for CPU, RAM and processes; Ollama
  /api/ps for resident models.
- **Busy machine:** the runner waits up to 30 minutes, in 5-minute steps, for an idle window. Otherwise it records
  idle:false, and the claims stay machine-not-idle.
- **Shape check:** remeasure refuses a record without rule, probe, pre, post and thresholds (TB6b).
- **Calibration:** before the anchor, the probe runs `--calibrate` for 10 minutes on the idle machine.

<!-- rev2-slot: idle-thresholds -->
- (a) The calibration violates no threshold → the thresholds are unchanged.
- (b) The calibration violates a threshold → that threshold becomes the calibration's observed maximum plus 20 %,
  rounded up. This happens once, with the calibration record attached.
<!-- /rev2-slot -->

## 8. Reader and judge contract (T11, T9, PR5, K83)

### 8.1 Reader
- **Model and mode:** deepseek-flash, non-thinking. The stand's client sends `"thinking": {"type": "disabled"}`.
  Temperature 0; max_tokens 1,024; its own proxy port per arm (phase answer).
- **One template per benchmark,** identical for every arm and bracket: the benchmark's own answer prompt, with its
  history block replaced by the arm's context block.
  - It ends with an instruction to finish with one line, "SHORT ANSWER: <at most 15 words>". The exceptions are BEAM's
    long-form and ordering abilities (§8.4).
  - The LME template includes question_date and allows "unanswerable".
  - The LoCoMo template includes the "No information available" instruction for cat 5.
  - Templates are frozen, and pinned by file sha256.
- **Missing SHORT ANSWER line:** it is re-asked once. If it is still missing, the answer is scored as empty (wrong).
  This is counted per arm as reader_format_failures, and published.

### 8.2 Context blocks
- Each arm's block follows §5.0:
  - ours: api.format_note verbatim;
  - Zep: facts with valid and invalid dates, plus entity summaries (its harness);
  - Mem0: memory text;
  - the others: the product's documented renderer, else "- item" lines.
- Dates appear only from a per-item event-time field.

### 8.3 Judges
Only models already on disk are used, with no pulls. Digests are recorded in FREEZE-V3.
- **J1:** qwen3.6:27b, Ollama digest a50eda8ed977 (Qwen family).
  - Temperature 0, with think:false.
  - A verdict that carries thinking output, or is cut at its cap, is re-asked once. If it is still so, it counts as
    unparsable (§8.4, the 1 % rule).
  - **Output caps:**
    - LME: 10 tokens;
    - LoCoMo J: 64;
    - BEAM and AMA: the smallest power of two ≥ 2× the longest output that the official format admits on the official
      examples. The values are recorded in the judges slot.
  - **J1's contract (CL5), pass/fail before the anchor, on the pilot judge set:**
    - ≤ 1 % invalid verdicts after the re-ask;
    - thinking off on every accepted verdict;
    - a test-retest flip rate ≤ 10 % on 200 re-judged verdicts.
- **J2:** the first model in this ordered list that passes the same contract. The one used is declared.
  1. glm-4.7-flash:q4_K_M, d1a8a26252f1 (GLM family);
  2. gemma3n:e4b, 15cb39fd9394 (Gemma family; small, 6.9B; declared weaker);
  3. hermes3-8b (Llama family, 8B; declared weaker);
  4. llama3 (Llama family, 8B; declared weaker).
  - The digests of 3 and 4 are recorded from `ollama list` at A3.
  - If none passes, every judged verdict carries "judge-dependent (no J2)" (§9.2).
  - **A declared-weaker J2 (items 2-4; C9 [RULING]):** J2 agreement (§9.2 robustness 1) does not rest on it alone. A
    judged confirmatory verdict stands only if its ordering also holds under J1 and under the EM/F1 twin (on S5 the
    short-answer subset, with tau-b; C2). Otherwise, and on any judged cell with no twin, it reads "judge-dependent
    (weak J2)". J2's own Δ is still printed.
  - J2 uses the same prompts, caps, re-ask rule and parse as J1.
  - It judges every verdict of S5 and S7 (the confirmatory judged stands).
  - It judges a 25 % paired sample of questions on S1 and S4: the same questions for every arm.
- **J3:** deepseek-v4-pro, non-thinking, on its own proxy port and pin.
  - It gives a labelled row on a 10 % paired sample of S5 and S7.
  - It is never used in a verdict.
- **Official prompts:**
  - LongMemEval: the per-type prompts from evaluate_qa.py, at a pinned commit;
  - LoCoMo: J with the Mem0-paper prompt, declared lenient;
  - BEAM: its nugget rubric (0, 0.5 or 1 per nugget, averaged);
  - AMA-Bench: its judge prompt.
  - FC exact match, LoCoMo F1 and BEAM event ordering need no judge.
- **Declared deviation:** LongMemEval's official judge is GPT-4o-2024-08-06. There is no OpenAI key, and owner labelling
  is forbidden, so there is no human calibration.

### 8.4 Parsing and the deterministic twin
- **Strict parse, per prompt:**
  - LME: the output, stripped and lowercased, matches `^(yes|no)\b`;
  - J: the official label field;
  - BEAM and AMA: their official formats.
  An unparsable output is re-asked once; if it is still unparsable, the verdict is invalid. The official "yes in
  response" parse is reported beside the strict parse. P0i governs the invalid share.
- **BEAM ability classes, fixed by name** (F1). They come from the BEAM paper, README and dataset card, never from
  outputs:
  - **short answer** (the SHORT ANSWER line is judged; EM/F1 twin): Abstention, Contradiction Resolution, Information
    Extraction, Knowledge Update, Multi-Session Reasoning (the paper's Multi-Hop Reasoning), Temporal Reasoning;
  - **ordering:** Event Ordering. The full answer's list is scored by Kendall tau-b: deterministic, with no judge;
  - **long form** (the full answer is judged; no twin): Instruction Following, Preference Following, Summarization.
    One cap applies for every arm: the official cap if there is one, else the reader's max_tokens.
  - **S5 overall** = the official BEAM overall score, as the pinned scoring code computes it from these per-question
    scores.
  - **An ability not on these lists** is judged on the full answer (the long-form rule) and listed in the dataset-facts
    slot, which records facts and never re-classifies (C7 [RULING]).
- **The twin:** SQuAD EM and token F1 of the SHORT ANSWER against the gold answer, beside every judged cell. On S5 it
  covers the short-answer abilities, with tau-b for event ordering beside it.
- **J1 test-retest:** on a 10 % paired sample per judged stand. The flip rate is published.

### 8.5 Per-arm, per-style FA/FR probe
It runs before the anchor, and it feeds §9.2.
- **Source questions, all from outside every list a judged or confirmatory cell scores:**
  - LME prompts, and the LoCoMo J prompt (a generic question/gold/answer judge): LME questions at positions 201-480 of
    the nested order. These are never in S1, S2, S3 or the smoke split. They are in S9's list, which is retrieval-only
    (no reader, no judge): the one declared exception;
  - BEAM's rubric: the questions of the S5 smoke unit (a 500K-split conversation, §9.4);
  - AMA's prompt: the questions of the S7 smoke trajectory (a non-SWE domain, §9.4).
- **Items:**
  - 200 per judged prompt and arm style, built with the arm's own renderer. Half contain the gold evidence, and half a
    distractor.
  - The reader answers, and each judge judges.
  - Error classes are templated and deterministic: enumerations that contain the gold, old + new values, hedges,
    relative dates, near-miss entities. Several variants per source question reach 200.
- **Rates, per arm and judge:** FA (the judge accepts a wrong answer), and FR (the judge rejects a right one).
  - The templated classes are primary (C8 [RULING]). J2's FA/FR, and the rule below, use templated items only.
- **Free-form items:** hermes3-8b generates one wrong-but-on-topic answer per source question. J1 alone judges them;
  they are published and never used in a rule. If hermes3-8b does not load, there are none.
- **The items are frozen** before the anchor, and recorded by sha256.
- **Rule:** a judge prompt whose pooled templated FA is > 30 % cannot give verdicts; its judged metric is descriptive.
  LoCoMo J is descriptive by decision (Q18).

<!-- rev2-slot: judges -->
This slot records the outcomes of the rules fixed in §8.3-§8.5, and nothing else.
- **J1's contract (CL5):**
  - pass → as declared;
  - fail → every judged confirmatory member becomes descriptive, and E5 says so.
- **J2:** the first model of the §8.3 ordered list that passes. If none passes, every judged verdict carries
  "judge-dependent (no J2)". If it is a declared-weaker fallback (items 2-4), the §8.3 weak-J2 rule applies.
- **Output caps for BEAM and AMA:** the values the §8.3 formula gives on the official examples.
- **Per judged prompt:**
  - pooled templated FA ≤ 30 % → the judged metric may give verdicts on S5 and S7;
  - pooled templated FA > 30 % → the metric is descriptive, with the EM/F1 twin shown where one exists.
  - Judged metrics on S1 and S4 are descriptive in every branch. LoCoMo J is descriptive by decision (Q18).
<!-- /rev2-slot -->

### 8.6 Leakage (T9)
LME and LoCoMo predate the reader's training data. The `none` bracket measures what the reader answers without memory.
Absolute numbers are never compared with vendor-published ones.

## 9. Runs, smoke split, statistics (T14, PR4)

### 9.1 Runs
- **Runs per stand,** for product arms, the ablation and cloud-calling brackets:
  - S1: 2 runs;
  - S4: 3;
  - S5: 2;
  - S6 FC-SH and FC-MH 6K/32K: 5; FC-MH 64K/262K: 2;
  - the S6 all-0 row: 2;
  - S6L: 2;
  - S7: 3;
  - S10: as in v2.
- **Independence:** every run has a fresh store, a fresh process, and run_id in every cache key. Nothing is read across
  runs.
- **Retrieval tier:** 1 run if a byte-identical rerun on the smoke split proves determinism; otherwise 2.
- **Per cell:** mean over runs, range (min-max), n, runs and status.

<!-- rev2-slot: retrieval-determinism -->
Per retrieval arm:
- (a) a byte-identical rerun on the smoke split → deterministic: true, 1 run;
- (b) otherwise → 2 runs.
<!-- /rev2-slot -->

### 9.2 The confirmatory family and its decision rule (K81)
- **The family:** Point B only, one metric per axis.
  - A: S5 overall, the official BEAM overall score (J1, with J2 on every verdict; §8.4);
  - B: S6 FC-SH, exact match at question level, with the four rows as fixed strata;
  - F: S7, the official AMA judge (J1, with J2 on every verdict).
- **Members:** nevertwice against each runnable competitor, on each of the three stands (the confirmatory-family slot).
  - **Membership is decided only from pre-scoring counters** (W22). A member is removed before any scored comparison
    only if its arm is blocked, or if an arm-run of it is still invalid after P2's repair (P0a-h, P0j, P1 > 10 %).
  - **Equal valid runs (G7):** both arms of a member must have all their runs valid. An arm with a run still invalid has
    an invalid cell: the member is removed, and its cell is never a mean over fewer runs.
  - **A judge failure (P0i) never removes a member.** It causes re-judging. A judged cell still invalid after the
    re-judge keeps its place in the Holm family with p = 1, and reads "unresolved (judge-invalid)".
- **Estimate:** the per-question score is averaged over runs. Δ = ours minus the competitor, over the questions valid
  for both, on their common prefix.
- **Tests:**
  - **S5 and S7:** a wild cluster bootstrap-t with the null imposed.
    - Clusters are conversations (S5) or trajectories (S7).
    - Rademacher weights for G ≥ 12; Webb six-point weights for G < 12.
    - 9,999 draws, seed 20260926, two-sided.
  - **S6 FC-SH:** a stratified sign-flip permutation test of per-question Δ, within rows.
    - The statistic is the row-size-weighted mean Δ.
    - 99,999 draws (exact if there are fewer patterns), seed 20260926, two-sided.
  - **Common to both tests** (TB10, research/v3/stats.py):
    - the bootstrap-t also enumerates every weight pattern when there are fewer patterns than draws (Rademacher
      G ≤ 13, Webb G ≤ 5), as the sign-flip does;
    - an exact p is the share of patterns at least as extreme as the observed statistic, the identity pattern
      included; a sampled p is (1 + hits)/(1 + draws); ties count as extreme (relative tolerance 1e-9);
    - the bootstrap-t studentizes with the CR1 cluster-robust standard error;
    - a displayed interval inverts the member's own test by bisection, with the same seed at every null. An end the
      test cannot reach stays at the range bound and is marked open;
    - branch (b)'s sign-flip on cluster means weights each cluster by its size, so the statistic stays the
      question-level mean Δ.
- **Multiplicity: two Holm families.**
  - The superiority family runs at family-wise error 0.05; displayed intervals are at the Bonferroni level
    (1 − 0.05/F).
  - The equivalence family (TOST) runs at family-wise error 0.05.
  - The probability of at least one false verdict across both families is at most 0.10.
- **TOST:** margin ±5 pp. The one-sided p-values come from the same method, with Δ shifted by ±5 pp. The larger of the
  two is the TOST p.
- **Verdict:**
  - Holm rejects in the superiority family → ahead (Δ > 0) or behind (Δ < 0);
  - else Holm rejects in the equivalence family → equivalent;
  - else → unresolved.
- **Robustness.** All of these must hold; otherwise the verdict becomes "unresolved (<qualifier>)". When several fail,
  every qualifier is printed, in the order of this list, each once:
  1. **J2 agreement (judged stands):** J2's Δ has the same sign, with an unadjusted two-sided p < 0.05 for ahead or
     behind, or a TOST p < 0.05 for equivalent. Otherwise: judge-dependent. If no J2 passed its contract: "judge-dependent
     (no J2)". If J2 is a declared-weaker fallback, the §8.3 weak-J2 rule decides this check.
  2. **Twin agreement (judged stands with a twin):** the twin's Δ has the same sign. Otherwise: judge-dependent.
     - On S5 the twin covers the short-answer abilities only, with tau-b for event ordering.
     - The S5 verdict therefore rests on the judge-robustness test (C2 [RULING]): J2 double-judges the full stand, the
       ordering must hold under J1 and under J2 separately (item 1), and twin sign agreement is required on the
       short-answer subset.
  3. **Judge style:** |Δ| > |FA_ours − FA_c| + |FR_ours − FR_c|, using the templated §8.5 rates. Otherwise:
     judge-dependent.
  4. **Fingerprints:** the verdict holds without straddled units (§4.3). Otherwise: fingerprint-sensitive.
  5. **Losses:** the verdict holds under the P1 loss sensitivity analysis (§9.3). Otherwise: loss-sensitive.
  6. **Labels:** neither arm carries a K76 label. Otherwise the cell reads "labelled (<label>)", and it is placed only
     against none and oracle.
- **Per-run estimates** are printed. A sign flip between runs is reported, never a veto.
- **S6 FC-SH also prints** each row's Δ and the length trend (the slope of per-row Δ on log2 of the row length), both
  descriptive.

<!-- rev2-slot: confirmatory-family -->
Written at A9.5 (K82).
- **Starting family:** {S5 overall, S6 FC-SH, S7} × {runnable competitors, per the arm-status slot}. F0 is its size
  before any member is dropped.
- **Per member:** G (clusters or strata), m, runs, the simulated MDE at α = 0.05/F0 and power 0.8, and the synthetic
  coverage. The inputs are those of §9.5.
- **Branches, per member:**
  - (a) MDE ≤ 15 pp and coverage ≥ 93 % → confirmatory, as declared;
  - (b) coverage < 93 % → the exact sign-flip test on cluster means replaces the bootstrap-t (S5, S7), and the member is
    re-simulated;
  - (c) MDE > 15 pp, or coverage still < 93 % → descriptive.
- **Holm** runs over the members that remain. A member dropped here stays dropped.
<!-- /rev2-slot -->

### 9.3 Descriptive cells and sensitivity analyses
- **Descriptive cells** show mean over runs, range, n, runs, status and a 95 % CI. They use the same cluster-aware
  method as the stand, on the common prefix:
  - S1 and S2: questions;
  - S4: conversations, G = 10, Webb weights;
  - S5 and S7: as in §9.2;
  - S6: rows as strata.
  They carry no verdict words.
- **Pre-declared sensitivity analyses:**
  - without straddled units;
  - loss masking: drop the questions whose evidence went through a lost write, where the benchmark names evidence
    units; otherwise, worst-case imputation against the lossy arm;
  - J2 only;
  - the twin only;
  - the S6 all-0 row.

### 9.4 Smoke and pilot
- **Smoke units come from outside every scored list** (F2). They are fixed here by rule, and the dataset-facts slot
  records which units the rules select:
  - **S1:** the 20 questions at the tail of the nested order (positions 481-500). They are outside S1, S2 and S3, and
    in S9's retrieval-only list: the declared exception.
  - **S4:** a LoCoMo-format fixture, built mechanically from the S1 smoke haystacks' sessions: speakers mapped to
    speaker_a/speaker_b, dates from the haystack dates, and the LME smoke questions.
  - **S5:** the first conversation of BEAM's 500K split in the dataset's own order, cut after the last session that ends
    within its first 128K tokens, with its own questions.
  - **S6:** the MemoryAgentBench Accurate_Retrieval row whose context length is closest to 32K tokens (ties broken by
    dataset order), fed as 512-token chunks, with its own questions.
  - **S7:** the AMA-Bench trajectory from a non-SWE domain whose character length is closest to the SWE median (ties
    broken by dataset order), with its own questions.
- **`--smoke` has no scorer.** It prints plumbing counters only: calls, failures, items, tokens, seconds, yield and
  coverage.
- **What runs here:** installs, capability probes, the boundary checks, the A/B, the judge-contract checks and the FA/FR
  probe.
- **What pilot numbers feed:** projections and the pre-declared slots only. Yield is printed and informational; it is
  never a label (labels come from the scored runs only) and never a config input (CL2).

### 9.5 Pre-anchor design check (K82)
- **The simulation,** one per confirmatory member:
  - at the stand's real G (clusters or strata), m (questions per cluster) and runs;
  - run correlation r 0.8;
  - **paired discordance 0.32, pinned now (F5):**
    - it is the median paired discordance over the v2 product-arm pairs {nevertwice_full, mem0_infer, amem_full}, at
      k=5, under J1 (qwen3.6:27b), on the v2 frontier (LME-oracle, 150 questions);
    - the pairs gave 0.320, 0.093 and 0.333;
    - the source is research/data/frontier_verdicts_cache.pre-v2-fe6ddff-111931.json, sha256
      4d1a4f7b843c91afefb47f33f0f73cc9be5751dfa4c34a3fbc31b75d576e7349;
    - the decision uses 0.32. The simulation also reports the MDE at 0.093 and 0.333, as sensitivity;
    - the 0.093 pair is two arms near the floor, which rarely disagree because both are rarely right. A higher
      discordance gives a larger, more conservative MDE, so 0.32 errs toward declaring a member descriptive;
  - ICC seeded from v2 (LoCoMo paired-hit ICC 0.000-0.003), plus a store allowance of 0.05;
  - the member's own §9.2 test on every simulated dataset; the CR1 t against t_{G−1} is reported beside it as the
    analytic reference;
  - α = 0.05/F0 (Holm's strictest step in the superiority family);
  - graded BEAM scores simulated as binary (every nugget of a question moves together): the most variable case, so the
    most conservative;
  - 2,000 simulated datasets per Δ, on a 1 pp grid;
  - **the data model** (research/v3/stats.py, `Design`):
    - each question's paired outcome per run is +1, −1 or 0, with P(+1) = (d + Δ_g)/2 and P(−1) = (d − Δ_g)/2, where
      d is the discordance;
    - the cluster effect Δ_g is a Beta variable on [−d, d] with mean Δ and variance ICC·(d − Δ²). The ICC is then
      exact and nothing is clipped; an ICC that cannot be reached at (d, Δ) is refused, not clipped;
    - a run keeps the question's first draw with probability √r and redraws otherwise, so two runs correlate at r;
    - S6 rows are fixed strata with no random effect.
- **Outputs:**
  - (i) the MDE at power 0.8;
  - (ii) the coverage of the nominal 95 % interval at Δ = 0.
- **Applying the result:** the branches of the confirmatory-family slot.
- **Records:** seed 20260926. The code and its output go into FREEZE-V3.

## 10. Rules P0-P6

### P0. What counts as a valid run
A row, arm-run or stand is INVALID if any of the following holds. P0 is read on the final units, after P2's re-runs.
- **a) Ollama transport** (the pacer for in-process arms; the proxy's Ollama leg for out-of-process arms and during S8):
  - bypass_calls > 0;
  - failed_outcomes on an embed endpoint > 0, including any 400, and including our arm's degraded recalls (§2.2);
  - failed_outcomes_llm > 0 after the pacer's bounded retry, on local LLM paths only (judges, S6L, S10). The retry is:
    pace mode, 5xx at most twice, 15 s then 30 s; 4xx never; observe mode never;
  - an arm with llm_transport "ollama", or with embeds_via_ollama, whose record shows calls == 0, unless K61 holds.
- **b) Cloud transport (m5 v3):**
  - cloud_transport is missing, or calls == 0;
  - any of failed_outcomes, fallback_local, model_mismatch, thinking_calls, cloud_bypass or tool_violation is > 0;
  - models_seen is not exactly one model;
  - a model event falls inside the artifact's blocks (§4.3).
  Write-phase transport losses are not P0b: they are transport_lost, counted in P1.
- **c) Corpus and population:**
  - a pinned file with a different sha256;
  - a pool size that differs from the pinned non-empty count (empty items are a corpus property: empty_skipped);
  - a list whose sha256 differs from FREEZE-V3;
  - questions scored that differ from the recorded list minus the units dropped for all arms under P2;
  - a unit dropped for one arm but not for all;
  - an arm-run whose own failures dropped more than 5 % of the stand's units.
- **d) Declaration:**
  - m5 v3 (with --anchor and --freeze) is not PASS;
  - a §5.0 knob is outside its rule (K80);
  - a blocked value is outside the vocabulary;
  - a blocked arm carries numbers.
- **e) Provenance:**
  - measured_at is not the anchor, or dirty is true. Dirty means that, at measurement time, a tracked file is modified,
    or an untracked file exists outside research/v3/results/ and the gitignored paths. The campaign's own untracked
    artifacts under research/v3/results/ are not dirty (X10);
  - HEAD differs from the anchor;
  - the produced_by closure is not identical to the anchor's;
  - utc is before the anchor, or outside the artifact's STATUS START..END (STATUS S4);
  - a cache read breaks K60;
  - a derived artifact's recorded inputs have moved.
- **f) Completeness:**
  - a requested arm with no contexts;
  - a scored point whose n differs from the number of questions asked for it;
  - a partial cache (K23, K34);
  - an answer or verdict not stamped at the anchor (K30).
- **g) Timing rows:** not in observe mode, or without a measured machine_idle whose idle is true (P5).
- **h) Boundary (K77, K78):**
  - a spawn without its environment-assertion record;
  - any boundary counter > 0: canary_hits, owner_marker_hits, egress_hits or fs_hits (in A9, or in block 1 of the
    stand).
  P2 handles the arm-run.
- **i) Judge contract (K83):**
  - Invalid verdicts are more than 1 % of an arm's verdicts on a stand. A verdict with thinking output or cut at its cap
    is re-asked once; if it is still so, it counts as invalid, and so does an unparsable one after its re-ask.
  - Consequence: that arm's verdicts on that stand are re-judged once, with the same judge.
  - Still > 1 %: the judged cell is invalid, "unresolved (judge-invalid)". P0i never removes a confirmatory member (W22).
- **j) Reconciliation (K87):** a check outside its branch of the reconciliation-granularity slot.

The flag lands on the row or root that the claim pointer reads.

### P1. Write-path losses (K75)
- **Lost operation.** A logical write operation is lost if:
  - the product reported an error for it; or
  - it ended with a transport_lost key; or
  - its output was empty, unparsable or cut at the cap, and the product did not recover it with its own retry or
    re-ask.
- **Symmetric checks:**
  - json_object content, for arms that request it;
  - tool-call arguments, for tool-calling arms;
  - the product's own status, for server and agent arms: Supermemory document status, Letta step errors, Claude Code
    error results.
- **Shares:** lost_share = lost / logical write operations, and transport_lost_share = transport_lost / logical write
  operations, per arm-run.
  - Both are written in the p1 block (§2.3), with its label.
  - The proxy's per-response counts are published beside them, and never used alone.
- **Bands:**
  - ≤ 2 %: valid, published beside the row.
  - 2-10 %: valid, labelled "lossy-writer (x%)", and subject to the loss sensitivity analysis (§9.3). A verdict that
    changes becomes "unresolved (loss-sensitive)". This replaces revision 0's margin.
  - > 10 %: after the two documented attempts, the arm is blocked by the dominant class:
    - structured-output (empty, unparsable or cut);
    - tool-calling (tool arguments);
    - transport, only after PR7's unit re-runs are exhausted and transport_lost still exceeds 10 % (C6 [RULING]).
    The block is named as a consequence of the pinned LLM where DeepSeek's documented behaviour caused it.
- **Empty extractions:** a valid empty extraction is not a loss (B4's slug separates it), but it counts in writer-yield
  (K76).
- **Our arm:** the same rule applies.

### P2. Invalidation and re-runs (PR7, K88)
- **The re-run unit** is (arm, run, unit). The decision comes from transport, boundary and reconciliation counters and
  from STATUS, before any scoring of the affected stand.
- **The order for a failed call:**
  1. the product's own retries;
  2. for answer, read and judge calls, the stand's re-asks (§4.5);
  3. then this rule.
- **Exogenous:** an incident window, a 401/402/403 halt, or any embed transport failure (the embedder is shared, so a
  degraded recall of ours counts too; G9).
  - Every affected unit is re-run for every arm and bracket, in fresh stores, in an appended repair block that all arms
    share, barriered (7.2).
  - The originals are discarded for all arms alike.
  - A unit that fails again is dropped for every arm.
  - Re-run units above 10 % of a stand's units make the stand invalid.
- **Endogenous:** one arm's unit crashed or hit its ceiling, or its arm-run hit a first P0b, P0h or P0j invalidity (G7).
  - The affected units are those whose counters show it. Where attribution is per block, that means every unit of the
    affected blocks.
  - They are re-run once for that arm, with fresh stores and processes, in an appended repair block with the same model
    identity. That block is declared outside the time clause (§5.6).
  - For P0h, the repair runs only after the cause is removed without a code change.
  - A unit that fails twice is dropped for every arm. The drop counts against the arm whose failures caused it (P0c).
- **Equal valid runs:**
  - A cell compares arms at equal valid run counts.
  - An arm-run still invalid after its repair makes that arm's cell on that stand invalid; it is never a mean over
    fewer runs.
  - There are no lone-arm whole-run re-runs.
- **Model events (§4.3):**
  - A model event invalidates its own block and every later block of the stand, for every arm.
  - The completed prefix stands if it meets the stand's floor (§3.4). Otherwise the stand is re-run once, under the new
    model.
  - An event between stands leaves finished stands valid, and every E5 cell names its model version.
- **Repeated invalidity:**
  - An arm-run invalid again after its repair becomes owner-decision:<date>, with the log.
  - A stand-level invalidity re-runs every arm of that stand once.
- **Code fixes:** a fix that needs code is a new anchor, and every stand is re-run (§1.4).

### P3. Blocked, lacking, asymmetric
- **Two attempts per vendor docs,** in a separate venv under §2.6, each logged: first the documented config, then the
  vendor's documented alternative. After that, the arm is blocked with one of these reasons, and never scored 0:
  - install;
  - import:<module>;
  - structured-output;
  - tool-calling;
  - local-server;
  - unsupported-surface;
  - silent-extraction;
  - thinking-uncontrollable;
  - transport: only after PR7's unit re-runs are exhausted and transport_lost still exceeds 10 % (C6 [RULING]). It is a
    named outcome, never 0.
- **competitor-lacks-capability:<probe>:**
  - k;
  - source-attribution;
  - as-of;
  - temperature (the S6 sensitivity row only).
- **needs-other-env:** cloud-account, owner-yes, compute-budget(<projected h>).
- **historical:N<k>:** claims of stands this file does not measure (§12).
- **Rankings:** no verdict between arms whose declarations differ outside the declared axes, or outside §5.0. Blocked
  pairs stay pending, and are not re-pointed.

### P4. Resolution and verdicts (K81, K82)
- **Runs and determinism** follow §9.1. Byte-identical reruns are one draw. A spread of 0 over identical draws is
  "unresolved", not zero.
- **Verdict words** ("ahead", "behind", "equivalent") are used only for confirmatory cells, by §9.2. Every other cell is
  "descriptive".
- **Qualifiers and labels:** the §9.2 qualifiers downgrade a verdict to "unresolved (<reason>)". A K76 label replaces
  the verdict.
- **Resolution for restore** (carried from v2): the Wilson half-width at n, the measured spread, or 0 for a
  deterministic claim. A claim that moves beyond it is restored, but listed and read by hand.

### P5. Timings
- §7 applies: local operations only, observe mode, a measured idle record, the identical Ollama hop, and a network row
  that is never ordered.
- Warm-up is excluded, concurrency is 1, and each arm has at least 200 timed calls.

### P6. Freeze, anchor, reporting, restore
- **Shas:** revision 1's sha is in STATE-C before the pilot, and revision 2's before the anchor. Both are committed
  under research/v3/. FREEZE-V3 is in the anchor tree.
- **Frozen before any scored data is seen:** this file, FREEZE-V3, the lists, the seeds, and the prompt and template
  shas.
- **Smokes:** only on units outside every scored list (§9.4), with scoring disabled.
- **No tuning, no picking:**
  - no tuning of our config on v3 data;
  - no choice of hint, window, prompt or profile after the pilot;
  - no best-run picking: every valid run is in the run average, every launched run is in STATUS (S2), and every invalid
    run is listed with its cause.
- **Reporting (E5):**
  - a TL;DR of three lines per LOCAL-TASK-D §6, each sentence naming its layer (writer-domain, window, schema, ranking,
    supersession, time);
  - per untouched stand, the rows shipped / ablation / ranker-only / none / oracle;
  - the tuned-on and untouched labels;
  - axis headlines only from the confirmatory family;
  - "our corpus" rows out of every headline;
  - no aggregate, and no vendor-number comparison;
  - every cell names its benchmark file and its model version;
  - one line to the owner on each of these:
    - Q5: the v2 frontier is now historical:N7;
    - Q19: the exact route the owner's yes would have to name;
    - the domain finding;
    - the M2 time-clause change, and any endogenous repair block;
    - any blocked:thinking-uncontrollable arm, and any arm under the thinking fallback;
    - Letta's declared network limit;
    - the container-image interpretation (§5.1).
- **E5 table:** it is rendered from the register. Every E5 cell was registered as a pending placeholder at the anchor
  (K91).
- **Restore #3:**
  - register_plan is applied;
  - m2_v3, m3, m4 and m5 PASS at the restore commit, whose parent is the anchor;
  - CI 14/14.

## 11. K-codes (continuing after K54)
- **K55 (T2):** every cloud call goes through the recording proxy; cloud_transport per arm-run (see K84).
- **K56 (T28):** no local fallback for a cloud-LLM arm; api.m._LLM_STATS in every artifact of our arm; fallback_local 0.
- **K57 (T1):** pinned names enforced; response model and fingerprint recorded per call; barrier blocks; model identity
  at zero tolerance (see K86).
- **K58 (T1, T23):** non-thinking for every arm, through its documented route, or, only for an arm without one and only
  under branch (b) of the thinking-default slot, the declared per-arm proxy fallback (thinking_injected, one field).
  thinking_calls must be 0, checked on every response.
- **K59 (m5):** arm_decl complete, embeds_via_ollama included; m5 v3 PASS on every v3 artifact, with --anchor and
  --freeze.
- **K60 (T29, T17):** caches built in-campaign, at the anchor, per run, with build records; a cache built before the
  anchor invalidates.
- **K61 (T29):** require_traffic for cache readers: misses ≤ calls, and the build record shows calls > 0.
- **K62 (T3, D1):** one embedder tag; stand truncation ≤ 2,048 tokens; no shrink-retry; embed failures through P2;
  embed_at_cap counted.
- **K63 (T10):** benchmark protocols (per question, per conversation, per chunk, per trajectory); a global pool only in
  S9.
- **K64 (T10):** nested, seeded, stratified lists and committed unit orders; shas in FREEZE-V3; smoke units outside every
  scored list.
- **K65 (T5, T24):** Point B primary and headline; Point K secondary, with per-arm median context tokens; the V row on S1
  only.
- **K66 (T11, T9):** the judge policy is K83.
- **K67 (T4, T15):** the defaults table, including the gate, the prompt sha, the ablation variant, the project rule, the
  window and the filled provenance; tuned-on/untouched labels; the headline rule.
- **K68 (T19, T27):** wall-clock now for every arm; dates through the product's own field or the uniform header; the
  declared write-side archive and decay-floor effects.
- **K69 (T13, T13a):** token accounting (§6); the axis-D denominator is the tokens the product read.
- **K70 (T21):** each product writes at its shipped temperature. The value is read from source or docs before the pilot,
  recorded per call and checked by m5 --freeze. The all-0 row runs on S6 FC-SH only.
- **K71 (T20):** the write unit per vendor harness; adapters committed.
- **K72 (T25, T8):** tiers (§2.1).
- **K73 (T6, T7):** capability probes recorded as competitor-lacks-capability.
- **K74 (T12, T12a):** measured machine_idle.
- **K75 (T23):** P1 write-path losses by logical outcome; a sensitivity analysis instead of a margin.
- **K76 (PR2):** writer-yield, per product arm and ablation, per stand and run. The unit is the stand's evaluation unit
  (G6): haystack (S1), conversation (S4, S5), row (S6), trajectory (S7).
  - **retrievable_unit_share:** the share of evaluation units whose store holds ≥ 1 retrievable item at the end of the
    write stage.
    - For ours, typed notes count; Session notes never do.
    - For the others, the product's own stored items: Mem0 ADD/UPDATE events, Graphiti edges or nodes, LangMem
      memories, Cognee chunks or nodes, Letta archival inserts or core edits, Supermemory memories with status done,
      Claude Code memory files, A-MEM notes.
  - **coverage:** characters reaching the writer's LLM ÷ the evaluation unit's characters, averaged over units.
  - **Also written:** retrievable items per 1K tokens read; the empty-context share at Point B; for ours, the
    relevant=false share, the proposed/refused/quarantined/skipped/off_topic counts (api.py:692-703), and the
    truncation-marker share.
  - **Labels, fixed now:** "writer-gated (x%)" if retrievable_unit_share < 0.5; "window (y%)" if coverage < 0.5.
  - A labelled cell is placed only against none and oracle.
  - Labels come from the scored runs only. Pilot yields are informational.
- **K77 (PR1):** the launch contract for every child, fetches included: the environment allowlist, the fixed telemetry
  list, the cwd rule, binary pinning, installs, the allow set and deny list (quarantine included), and the proxy as the
  single instrument exception (§2.6).
- **K78 (PR1):** the fixed memory-tool allowlists and forbidden patterns, and tool_violation on offered or called names;
  leak canaries, owner markers and the Claude Code ancestor check; egress witnesses, native and container; the
  filesystem witness on its fixed watched set; the auditor's sign-off of the A9 results.
- **K79 (PR2):** layers named:
  - project = stand id;
  - S7 through the hook's reader, with the fixed AMA field mapping, and the raw-text row secondary;
  - one pre-declared ablation row;
  - ranker-only rows on S5-S7;
  - E5 prints shipped / ablation / ranker-only / none / oracle, and every TL;DR sentence names its layer.
- **K80 (PR3):** the §5.0 symmetry table, one rule per knob, checked by m5 against FREEZE-V3 `symmetry`, and by harness
  assertions.
- **K81 (PR4):** the confirmatory family; two Holm families (superiority, equivalence) on bootstrap-t and sign-flip
  p-values; TOST ±5 pp; run-averaged scores at equal valid runs; membership from pre-scoring counters only; the
  robustness qualifiers.
- **K82 (PR4):** before the anchor, a simulated cluster-aware MDE (> 15 pp → descriptive) and ≥ 93 % synthetic coverage
  for every confirmatory member, with discordance 0.32 pinned.
- **K83 (PR5):** the judge contract:
  - J1 think off, with the one re-ask rule; official-size caps; strict parse; the 1 % rule with re-judging;
  - J1's pass/fail contract;
  - J2 from the pinned ordered list; J3 labelled only;
  - one reader template with a SHORT ANSWER line;
  - the BEAM classes fixed by name; the EM/F1 twin;
  - the per-arm FA/FR probe on questions outside every scored list.
- **K84 (PR6):** the recording proxy: byte-for-byte streaming, keep-alives passed, client_abandoned recorded; on the
  cloud leg, no retries, no caps, and no change to a request except the one declared per-arm thinking fallback;
  raw-forward mode with the same one field; the Ollama leg under the pacer's policy (G8).
- **K85 (PR6):** the transport taxonomy (request-key hash; transport_recovered, transport_lost, failed_outcomes) and the
  re-ask-then-drop order; the incident gate; halts on 401/402/403; balance ≥ 2× the projection, or the 402 rule alone.
- **K86 (PR6, ruling):** fingerprints recorded, straddled units, the sensitivity analysis, endpoint classes; model
  identity at zero tolerance; the Q3(b) ratchet deleted.
- **K87 (PR6, C12):** independent reconciliation per arm-run, within the pre-declared granularity branches; the
  recording-vs-raw-forward A/B, both legs through the proxy with no key in any child, within tolerance before the
  anchor.
- **K88 (PR7):** scoped invalidation (P2), with exogenous and endogenous repair blocks and equal valid runs.
- **K89 (PR8):** parallel runs, multi-unit blocks and stages, per-unit ceilings, symmetric budgets on nested prefixes,
  the headline-first order, the M2 time clause and its declared repair-block exception.
- **K90 (PR8):** the embed ceiling resolved symmetrically before the anchor, never by one arm's cache.
- **K91 (PR9):**
  - A10.5, the certification kit gated on a fixture v3 artifact with named mutations red;
  - FREEZE-V3 and STATUS v3;
  - register_plan;
  - the E5 placeholders, with E5 rendered from the register;
  - no commit between the anchor and restore #3.
- **K92 (PR10):** revision 1 closes every Qn before the pilot; revision 2 changes only fenced slots; byte identity
  outside the fences.
- **K93 (G14):** the pre-launch checklist CL1-CL9 (§14.3) passes before the anchor. The auditor signs CL1.

## 12. Not measured, and why
The item numbers are stable. m4 accepts `historical:N<k>` for them.
- **N1:** the vendor-managed tier (mem0-platform, zep-cloud, supermemory-hosted, letta-cloud):
  needs-other-env:cloud-account. The keys were requested on 2026-09-25 at 16:53 and not given. They are not asked for
  again.
- **N2:** claude-code-memory, unless a yes naming the exact route of §2.6.8 is on file before the anchor:
  needs-other-env:owner-yes. Even with the yes, the product's own reader (Claude choosing which topic files to open) is
  replaced by the stand reader.
- **N3:** a-mem on the DeepSeek stands: expected blocked:structured-output (it sends json_schema). S6L gives it a row.
- **N4:** the LME-M product tier, for every arm alike (Q7 a). At n = 50 it resolves about 21 pp, and it would be the
  largest cost item. The full-context bracket on M exceeds the 1M window.
- **N5:** coding conventions carried across sessions, the product's core use. There is no public, pinnable benchmark:
  - DreamBench-SWE withholds its oracles;
  - MemGym-CodeQA's dataset returned HTTP 401;
  - SWE-ContextBench, MemoryArena and Evo-Memory need an agent loop per arm that cannot be run equally here (T18), and
    MemoryArena has no coding domain.
- **N6:** absolute comparability with vendor-published or GPT-4o-judged numbers (T9, T11).
- **N7:** v2 stands not re-measured. Their claims become historical:N7:
  - global-pool h2h on oracle and LoCoMo;
  - the v2 frontier (Q5: S1 at Points B and K, plus the V row and the brackets, is the v3 frontier);
  - token_floor (its code_sessions half was retired by D3, and §6 replaces its per-query half);
  - code_sessions (D3).
- **N8:** HaluMem (CC BY-NC-ND, written by a vendor); LongMemEval-V2 (multimodal, about 25M tokens); LoCoMo-Refined (no
  paper); BEAM 500K/1M/10M as stands (cost; a 500K conversation serves only as the S5 smoke unit); LME(S*) (S1 uses the
  official protocol).
- **N9:** disk footprint in bytes, because the storage engines differ. Stored items and tokens are reported instead.
- **N10:** multi-user isolation, concurrency, and scale beyond LME-M.
- **N11:** hosted-only features (Mem0's graph memory and decay, Zep Cloud's context API).
- **N12:** v2 product-internal stands that v3 does not schedule (guard_bench, abstention_ab, fusion_weight_sweep,
  longmem_*, as register_plan maps them). historical:N12, unless register_plan schedules one as S11.
- **N13:** a benchmark-relative query-time "now" for any arm ([RULING], T27 revised). N2 of revision 0 is not built.
- **N14:** the owner's corpora and labels (code_heldout_v1/v2, heldout_marks, corpus_heldout and the rest of the
  quarantine): owner-decision under the owner-data ban. The quarantine is never opened.
- **N15:** the products' own agentic readers (Letta, Claude Code), replaced by the stand reader.

## 13. Trap closure

| Trap | Closed by | How |
|---|---|---|
| T1 alias drift | §4.3, K57, K86, P0b, P2 | model identity at zero tolerance; fingerprints recorded with a sensitivity analysis; scoped model-event invalidation |
| T2 cloud transport | §4.4-4.6, K55, K84, K85, K87, P0b | recording proxy; taxonomy; independent reconciliation |
| T3 embedder parity | §5.1, K62, K90 | one tag; stand truncation; symmetric embed ceiling; the same local retry policy for every arm |
| T4 tuned on the same data | §2.4, §3.3, K67, P6 | defaults table with filled provenance, gate, prompt, ablation variant, project rule and window; labels confirmed by history |
| T5 same k and budget | §5.2, K65 | Point B (equal budget) primary; K secondary |
| T6 metrics an arm lacks | P3, K73 | vocabulary |
| T7 Claude Code memory | §2.2, §2.6, §12 N2 | locked route with fixed flags; exact-route yes |
| T8 Supermemory and clouds | §2.1-2.2, K72 | separate arm; vocabulary |
| T9 benchmark leakage | §8.6, P6 | declared; `none` bracket |
| T10 LME-M cost | §3.1 S2, §3.4, K63, K64, K89 | retrieval tier only; nested lists; budgets |
| T11 official judge | §8, K83 | judge contract; twin; J2 from a pinned ordered list; FA/FR probe |
| T12 timings | §7, P5, K74 | rule |
| T12a measured idle | §7, K74 | schema and calibration |
| T13 competitors' internal tokens | §6, K69 | rule |
| T13a api.m tokens | §6, K69, K87 | rule; the C4 fix (748581b) re-verified at the anchor |
| T14 runs | §9.1, P4, K81 | 2/3/5 runs; run-averaged at equal valid runs |
| T15 win only on our corpus | §0, §3.3, P6 | rule |
| T16 untracked files | §1.3 | porcelain check, the recorded ignored listing, HEAD check, codesess check |
| T17 old caches | §1.3, §5.7, K60 | set-asides logged in STATUS |
| T18 coding axis | S7, §12 N5 | AMA through the hook reader |
| T19 "now" | §5.4, K68 | wall clock for every arm ([RULING]) |
| T20 write granularity | §2.2, §5.3, K71 | rule |
| T21 A-MEM identity; temperature | §2.2, §5.5, K70 | upstream pin; shipped temperatures read from source ([RULING]) |
| T22 Mem0 without spaCy | §2.2 | `[nlp]`; top_k and threshold explicit per point |
| T23 LLM choice | §4.1-4.2, P1, K75, S6L | argued; thinking slot and the one fallback rule; blocks named as the LLM's |
| T24 equal budget | §5.2, K65 | Point B headline |
| T25 vendor-managed writer | §2.1, K72 | separate tier |
| T26 Python versions | §2.2, FREEZE-V3, m5 constancy | recorded and checked |
| T27 decay floor (revised) | §5.4, K68 | wall clock for all; effects declared |
| T28 silent LLM mix | §4.2, K56, P0b | rule |
| T29 caches alike or none | §5.7, K60, K61, K69 | rule |
| T30 licences | §3.1 | licence per stand; data not committed; id lists; no ND set |

## 14. Build before the anchor, and the campaign steps

### 14.1 Code (TB)
Each item is verified by a regression test that turns red under a named mutation, plus the auditor's gate.
- **TB1 (executor, A2): research/_llm_proxy.py and its selftest** (§4.4-4.6).
  - Raw-forward mode comes first, with a fake-upstream selftest. The DeepSeek captures are then made through it.
  - Then the recording mode, including:
    - the egress catcher, per-arm tokens and the J3 port;
    - the declared per-arm thinking fallback (branch (b) only);
    - the canary and owner-marker scan (markers computed in memory from the §2.6.9 sources, never persisted);
    - offered and called tool-name counts;
    - the Ollama leg under the pacer's code and constants;
    - unit prefixes and the block rule;
    - the balance query, and the append-only JSONL.
  - Its full selftest runs on the real captures.
- **TB2 (A4): research/_idle_probe.py,** its selftest, and `--calibrate` (§7).
- **TB3 (A5): engine changes.**
  - (a) the cloud-fallback switch, off and counted in v3 runs;
  - (b) `"thinking": {"type": "disabled"}` on the DeepSeek JSON path, shipped to every user;
  - (c) NEVERTWICE_EXTRACT_TEMP honoured on the cloud paths (_engine_store.py:914, :960), with the default unchanged at
    0.2. It is used only by the S6 all-0 row;
  - (d) the gate-off ablation switch: the code-gate bypass plus the pre-declared ablation prompt variant (§2.4), both
    pinned by sha256. The default path is proven unchanged;
  - (e) the xrerank_calls, recall_degraded and recall_empty_store counters, readable by the adapter.
  - Withdrawn from revision 0: api.recall(now=), and "ours at 0".
- **TB4 (A6): the research/v3/ harness.**
  - loaders for S1-S7, and the smoke-unit rules (§9.4); per-unit stores and processes;
  - the §2.2 adapters, including render_ama_jsonl.py with the fixed field mapping, and the hook-reader path;
  - Points B, K and V; the brackets;
  - the reader template; the judge contract, with the J2 ordered list;
  - K76 yield on evaluation units; the ablation row; the retrieval tier with ranker-only QA;
  - D1 truncation; token accounting by phase;
  - the scheduler: blocks, stages, barriers, parallel runs, ceilings, the incident gate, exogenous and endogenous repair
    blocks, budgets and prefixes, and the STATUS writer per STATUS-V3-CONTRACT;
  - `--smoke` with no scorer; the FA/FR probe; the A/B harness.
  - **Verify:** fixture tests with no network; m5 v3 PASS on a fixture artifact, and FAIL on each asymmetry. Mutations of
    P0, P1, K61, K76, the boundary counters and the STATUS writer turn red by name.
- **TB5 (A3, A7):**
  - subsample.py, the lists and the unit orders;
  - corpus_pin entries (LME cleaned S/M/oracle, BEAM 128K and the S5 smoke conversation, MemoryAgentBench CR and the S6
    smoke row, AMA-Bench SWE and the S7 smoke trajectory), each with its HF revision and licence;
  - the pinned prompt and scoring files; the tokenizers.
- **TB6a (auditor, .loop):**
  - m2_v3: its SPEC is keyed to research/v3 artifacts; it enforces STATUS rules S1-S8, and checks the fence byte
    identity between the two revision files;
  - m3 v3: the §2.2 arm names, and an M3-LINES v3 mapping;
  - m4 v3 vocabulary, including blocked:transport and blocked:thinking-uncontrollable: done;
  - m5 v3, including `sensitivity_rows` and `symmetry` against FREEZE-V3: done. It will also check, from the artifacts,
    the P1 bands, the K76 labels and the P0h counters (the boundary, p1 and yield blocks, §2.3).
- **TB6b (executor, repo, through the auditor's gate):**
  - remeasure's idle-record shape check;
  - restore refuses claims that carry excluded_because, unless they are on register_plan's lift list;
  - the FREEZE-V3.json writer, including the pre-pilot arms draft (§5.5);
  - research/v3/register_plan.json. It maps each of the 650 pending and 209 third-state claims to exactly one of:
    - (i) a v3 artifact and pointer measuring the same statistic, or a new id with the old one superseded-by;
    - (ii) a scheduled, budgeted, STATUS-logged S11 block;
    - (iii) historical:N<k>, applied at the anchor;
    - (iv) an excluded_because from the extended vocabulary. code_heldout_v2 → owner-decision, under the owner-data ban.
    The choice per claim uses only the claim's stand and pointer, never v3 numbers;
  - the E5 placeholders (raw pointer, n pointer, CI pointers, printed form), and research/v3/render_e5.py.
- **TB7 (A7): pacer.**
  - a per-arm count of Ollama generation-path calls (fallback_local);
  - prompt_eval_count per embed call (embed_at_cap);
  - the provider-host tripwire (cloud_bypass);
  - its policy constants, importable by the proxy's Ollama leg.
- **TB8 (A8): installs under TB9.**
  - per-arm install scripts, with two documented attempts;
  - lockfiles hashed into FREEZE-V3;
  - a logged _pacer_selftest and proxy selftest per environment;
  - the shipped temperatures read from the installed sources (§5.5).
- **TB9 (executor, A2): research/v3/launch.py.**
  - the environment allowlist, with the fixed telemetry list and PYTHONPYCACHEPREFIX, and its assertions;
  - the cwd and deny-list checks; the proxy's single read exception; binary pinning;
  - the declared fetch window;
  - the Claude Code lockdown with the fixed flags;
  - fake homes and canaries;
  - the native and container egress witnesses;
  - the filesystem witness on its fixed watched set, persisting only digests, with no walk of the quarantine.
  - **Mutations that must turn red:** inherit one forbidden variable; run in the repo cwd; allow a bypass mode; skip a
    witness; walk the quarantine; persist a name.
- **TB10 (A6): research/v3/stats.py.** Wild cluster bootstrap-t (Rademacher, Webb), stratified sign-flip, two Holm
  families, TOST, and the MDE and coverage simulator with the pinned discordance. Each is checked against fixture
  values.

### 14.2 Steps (A)
- **A1:** revision 1 fixed by sha, and committed as research/v3/PREREG-V3-rev1.md.
- **A2:** TB9 and TB1, in this order:
  1. the launch contract;
  2. raw-forward mode and its fake-upstream selftest;
  3. the DeepSeek captures, made only through raw-forward, so the key never leaves the proxy;
  4. the recording mode and its selftest on those captures;
  5. the auditor's gate.
  [RULING] No out-of-process arm, no npm install, no Letta and no Claude Code before this gate passes.
- **A3:** fetch and pin (TB5), under the contract, in a declared fetch window:
  - datasets, the official prompt and scoring files, tokenizers, and A-MEM's upstream commit;
  - `ollama list` recorded (names and digests; no pulls);
  - the D1 embedder tag created;
  - the dataset-facts slot filled.
- **A4:** TB2, and the calibration.
- **A5:** TB3.
- **A6:** TB4 and TB10.
- **A7:** the lists (TB5), and TB7.
- **A8:** installs (TB8), capability probes and thinking-route checks.
  - The shipped temperatures go into the FREEZE-V3 arms draft. Its sha is witnessed in STATE-C before A9.
  - Tool sets may only shrink.
  - The arm-status slot is filled.
- **A9:** the pilot on the smoke units, scoring disabled:
  - boundary checks, submitted for the auditor's sign-off;
  - DeepSeek probes: the thinking default and the /anthropic field, empty responses, fingerprints over ≥ 12 h, the
    incident rate, and the balance endpoint;
  - yield, printed only;
  - projections and the embed ceiling;
  - the A/B and the reconciliation branches;
  - the judge-contract checks and the FA/FR probe;
  - retrieval determinism;
  - the ceilings.
- **A9.5:** the design check (K82).
- **A10:** revision 2 (research/v3/PREREG-V3-rev2.md; fenced slots only), signed and committed.
- **A10.5:** the certification kit on a fixture v3 artifact: m2_v3, m3, m4, m5 and the TB6b parts, with named mutations
  red.
- **A11:** the anchor: the checklist of §14.3 passed; set-asides; the tree check; commit; CI 14/14; FREEZE-V3; CAMPAIGN
  V3 START.
- **A12:** the scored stands, in the order of §5.6.
- **A13:** the validity sweep and re-runs (P2).
- **A14:** restore #3.
- **A15:** the E5 report.

**Owner-dependent steps:**
- the exact-route yes (no ping);
- the vendor-managed keys (asked once, not again);
- a 402 or a balance shortfall;
- the E5 report.

The PC must be idle for S8; the probe decides whether it is.

### 14.3 Pre-launch checklist (G14, K93). Every item PASSes before the anchor.
- **CL1 (boundary, M5):**
  - in A9: canary_hits = 0, owner_marker_hits = 0, egress_hits = 0, fs_hits = 0 and tool_violation = 0;
  - an environment-assertion record (names only) exists for every spawn;
  - a name-only check finds no path under the polygon matching heldout_marks*;
  - none of the quarantined top-level names (code_heldout, heldout_marks.json, quarantine_live, stores,
    embed_specialize, corpus_heldout, corpus, twin_gate, empty_projects, results) exists at the polygon's top level;
  - the auditor signs off these results.
- **CL2 (yield, M3):** per-arm, per-stand writer-yield is printed from the pilot, and is informational only. Labels come
  from the scored runs only. No config changes.
- **CL3 (cost, M4):** calls per write unit, tokens per call, p50/p95 per hop, and the embed and port ceiling are
  published. Each stand's projection fits its budget, or the prefix rule is named (sizes-and-budgets slot).
- **CL4 (proxy, M7):** the A/B is in tolerance; the reconciliation branches hold on the pilot; client_abandoned = 0.
- **CL5 (judges, M8):** J1 passes its contract (§8.3); J2 is resolved from the ordered list; per-arm FA/FR is computed on
  the templated items.
- **CL6 (statistics, M2):** simulated MDE and coverage exist for every confirmatory member. Members above 15 pp, or below
  93 % coverage, are declared descriptive (confirmatory-family slot).
- **CL7 (symmetry, M6):**
  - m5 PASS on a fixture carrying FREEZE-V3 `symmetry` and `sensitivity_rows`;
  - every pilot call's proxy-recorded temperature equals the FREEZE-V3 value written before the pilot.
- **CL8 (cloud, M1):**
  - the pilot's fingerprint rotation and incident rate are recorded, with the expected incidents per stand;
  - the DeepSeek balance is ≥ 2× the first stand's projection, or, if the endpoint is unavailable, the 402 rule applies
    alone.
- **CL9 (certification, M9):** A10.5 PASS: m2_v3, m3, m4 and m5 on a fixture v3 artifact, with named mutations red;
  register_plan complete; FREEZE-V3 written; both revision files committed.

## 15. Closure

### 15.1 PR1-PR11

| PR | Mode | Closed in |
|---|---|---|
| PR1 | M5 | §2.6 (the [RULING] gate; contract with the fixed telemetry list; allow set and deny list with the quarantine; the proxy's single exception; fixed tool sets; canaries; witnesses; route); §4.4 (tokens, catcher, scans, tool names); P0h; K77, K78; TB9; A2; CL1 |
| PR2 | M3 | §0 ([RULING on R1]); §2.2 (project = stand id, keyword-only args, S7 hook reader with the fixed field mapping); §2.4 (gate, prompt, ablation variant, project rule, window, provenance); §2.1 and TB3 (the ablation); §3.1 (ranker-only rows on S5-S7); K76 (evaluation units), K79; P6 layers |
| PR3 | M6 | §5.0; §5.2 ([RULING] Point B); §5.4 ([RULING] now); §5.5 ([RULING] temperature, read from source before the pilot); §8.2; §2.2 (Letta recall memory, Cognee default retrieval, xrerank=False counted); §6 (axis-D denominator) |
| PR4 | M2 | §9.2-9.5; §3.4; P4; K81, K82; CL6 |
| PR5 | M8 | §8; P0i; K83; CL5 |
| PR6 | M1, M7 | §4.3 ([RULING] fingerprints); §4.4-4.6; P0b, P0j; K84-K87; CL4, CL8 |
| PR7 | M1, M4 | P2; §5.6 repair blocks; K88; STATUS RERUN and INCIDENT lines |
| PR8 | M4 | §5.6 ([RULING] order); §5.1 embed-ceiling slot; §3.4 budgets; K89, K90; CL3 |
| PR9 | M9 | §1.2-1.4; §2.3 (m5 v3 fields, boundary, p1, yield); §5.6 ([RULING] time clause); TB6a, TB6b; A10.5; K91; CL9 |
| PR10 | M6 | §1.1, §1.2; the fences; K92 |
| PR11 | adjacent | §2.2 (Mem0 dates in content, top_k and threshold explicit, capture_session arguments and project); §4.1 (221 min; per-session v2 timings; 98.8k messages) |

### 15.2 Q1-Q19

| Q | Closed as | Where |
|---|---|---|
| Q1 LLM | deepseek-flash, non-thinking | §4.1 |
| Q2 thinking | each product's documented route; else, under branch (b) only, the one declared per-arm proxy fallback (C3 b2); pre-declared branches | §2.2.1, §4.2 and its slot |
| Q3 drift | [RULING]: model identity at zero tolerance; fingerprints never invalidate alone; ratchet deleted | §4.3 |
| Q4 LME files | (a): cleaned for S1-S3; original for S9; every cell names its file | §3.1 |
| Q5 frontier | (a): S1 at B and K, the V row and the brackets are the v3 frontier; v2 frontier historical:N7; one line in E5 | §12 N7, P6 |
| Q6 sizes | n_S 200 (120), n_M 50 (30), under K82 and the budgets | §3.4, §5.6 |
| Q7 LME-M | (a): retrieval tier only | §3.1 S2 |
| Q8 coding | (a): AMA-Bench SWE through the hook reader | §2.2, §3.1 S7 |
| Q9 A-MEM | (b): S6L under qwen3-coder:30b, secondary | §3.1 |
| Q10 judges | J1 qwen3.6:27b; J2 from the pinned ordered list (glm-4.7-flash first); J3 v4-pro labelled; digests in FREEZE-V3 | §8.3 |
| Q11 now | [RULING]: wall clock for all; N2 not built; effects declared | §5.4 |
| Q12 V row | ours k = 3 | §5.2 |
| Q13 xrerank | off: xrerank=False, counted | §2.2, §2.4 |
| Q14 versions | latest stable at freeze, fresh 3.12 venvs, through the contract; lockfiles in FREEZE-V3 | §2.2, TB8 |
| Q15 Letta | server only, in Docker by digest, fixed memory tools, declared network limit | §2.2, §2.6.6 |
| Q16 temperature | [RULING]: shipped defaults, read from source before the pilot; ours 0.2; all-0 row on S6 only | §5.5 |
| Q17 m5/remeasure | m5 v3 (auditor, built, with symmetry and sensitivity_rows) plus TB6b | §2.3, §14 |
| Q18 LoCoMo judge | J descriptive; F1 the primary descriptive metric of S4 | §3.1, §8.5 |
| Q19 Claude Code | needs-other-env:owner-yes; the yes must name the exact route; no new ping | §2.6.8 |

### 15.3 Closure audit (G1-G14)

| Item | Closed in |
|---|---|
| G1 filesystem witness hits by construction | §2.6.9 (fixed watched set; legitimate writers excluded; declared limits); §2.6.2 and §2.6.4 (PYTHONPYCACHEPREFIX; our runner as the named import exception) |
| G2 A/B raw-forward and the thinking fallback | §4.4 (raw-forward mode), §4.6 |
| G3 one rewrite rule everywhere | §2.2.1, §2.5, §4.2, §4.4, §5.0, K58, K84, §15.2 Q2; the thinking-default slot |
| G4 fences that could move a headline | §8.4 (BEAM classes by name); §9.4 (smoke units by rule, outside every scored list); §8.3 (J2 ordered list); §9.5 (discordance pinned); §2.4 (provenance filled, slot removed) |
| G5 tool allowlist fixed now | §2.6.6 |
| G6 K76 unit | K76, §2.3 yield |
| G7 re-runs after endogenous invalidity | P2, §5.6 repair blocks, §9.2 equal valid runs |
| G8 Ollama-leg retry parity | §4.4 Ollama leg, §5.0, TB7 |
| G9 degraded recall | §2.2 nevertwice, P0a, P2 |
| G10 shipped temperatures from source | §5.5, §1.3, A8, CL7 |
| G11 fetches and captures under the contract | §2.6.1, §2.6.5, A2-A3, TB1 |
| G12 Letta network | §2.2 letta, §2.6.9 container egress witness (declared limit) |
| G13 later decisions outside fences | §2.6.2 (telemetry fixed), §2.6.6 (Claude Code flags fixed), §5.3 (Letta header fixed), §4.6 (reconciliation-granularity slot), §4.5 (balance fallback), C4 (one key) |
| G14 CL criteria | §14.3, K93 |
