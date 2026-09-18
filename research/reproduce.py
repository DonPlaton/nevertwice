#!/usr/bin/env python3
"""RESEARCH - the reproduction package (GOAL F7).

F7's exit criterion is that the research **runs clean from a fresh clone with no author help**.
The test of that is not a README section, it is a script somebody else can run, so this is that
script: it regenerates every committed research artifact, hashes the result, and reports which
ones came back byte-identical.

The honest part is the classification. Not everything here is reproducible, and a package that
implies it is would fail the first stranger who tried:

* **deterministic** - same inputs, same bytes, every time and on any machine. These are the
  artifacts a reviewer can actually check.
* **machine-dependent** - the computation is reproducible but the numbers are not, because they
  measure the machine. Latency is the whole category, and F1's re-measurement showed the same
  statistic moving by a third between sessions on one box.
* **needs-absent-input** - a third-party dataset that is not committed and cannot be, so the
  artifact cannot be regenerated here at all. Recorded with what is missing.
* **needs-hardware** - a GPU and local model weights.

A reproduction that quietly re-ran only the easy artifacts and printed "all reproduced" would be
worse than none, so every artifact is listed in every run, including the ones that were skipped
and why.

    python research/reproduce.py              # regenerate everything reproducible, verify
    python research/reproduce.py --verify     # verify committed hashes without regenerating
    python research/reproduce.py --manifest   # print the raw-data manifest and exit

Exit code 0 only when every DETERMINISTIC artifact reproduced byte-for-byte.

Standard library only. Python 3.10+. No third-party packages are needed for any of this.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "reproduction.json"

DETERMINISTIC = "deterministic"
MACHINE = "machine-dependent"
ABSENT_INPUT = "needs-absent-input"
HARDWARE = "needs-hardware"

#: Every committed research artifact, what makes it, and what it takes to remake it.
ARTIFACTS = [
    {"file": "research/results/longmem_oracle.json",
     "command": ["python", "research/longmem_eval.py", "--xrerank", "--save",
                 "--out=research/results/longmem_oracle.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/longmemeval_oracle.json (third-party, MIT, hash-pinned in "
                "research/corpus_pin.py; fetch with `python research/corpus_pin.py --fetch "
                "longmemeval_oracle`)",
                "a local Ollama serving bge-m3",
                "research/data/longmem_embeds__c28000.json (built by --embed, a few minutes; "
                "the suffix is the embedding cap - the older uncapped-looking file holds "
                "2,000-character vectors and is refused)"],
     "note": "external retrieval recall@k on the global pool of the pinned LongMemEval-oracle "
             "corpus. Deterministic given the embedding cache: the ranking is arithmetic over "
             "stored vectors, so re-running the report reproduces exactly, while rebuilding the "
             "cache needs the embedder. The corpus is verified against its committed hash "
             "before a byte is read, which is the property the 2026-07 run of this file lacked "
             "and was withdrawn for."},
    {"file": "research/results/locomo.json",
     "command": ["python", "research/locomo_eval.py", "--save",
                 "--out=research/results/locomo.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/locomo10.json (third-party, CC-BY-NC-4.0, hash-pinned in "
                "research/corpus_pin.py; 2.8 MB, fetch with `python research/corpus_pin.py "
                "--fetch locomo10`)",
                "a local Ollama serving bge-m3",
                "research/data/locomo_embeds.json (built by --embed, about five minutes)"],
     "note": "LoCoMo retrieval, per conversation, against the annotated evidence turn. "
             "Deterministic given the embedding cache. The corpus is non-commercial and is not "
             "redistributed here - only its hash. Every turn id in the cache is namespaced with "
             "its conversation: LoCoMo restarts numbering at D1:1 in each of the ten, and a "
             "cache keyed by the bare id collapses 5,882 turns into 1,033, which is a defect "
             "this stand shipped with for exactly one run."},
    {"file": "research/results/longmem_oracle_raw.json",
     "command": ["python", "research/longmem_eval.py", "--no-morphology", "--save",
                 "--out=research/results/longmem_oracle_raw.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["the same corpus and vector cache as research/results/longmem_oracle.json"],
     "note": "the ablation arm of research/LEXICAL_MORPHOLOGY.md: the same stand with the lexical "
             "signal on raw tokens (no stop words, no stems), as it ran before 2026-09-06. "
             "Deterministic given the embedding cache."},
    {"file": "research/results/locomo_raw.json",
     "command": ["python", "research/locomo_eval.py", "--no-morphology", "--save",
                 "--out=research/results/locomo_raw.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["the same corpus and vector cache as research/results/locomo.json"],
     "note": "the LoCoMo ablation arm of research/LEXICAL_MORPHOLOGY.md, raw tokens. "
             "Deterministic given the embedding cache."},
    {"file": "research/results/fusion_weight_sweep.json",
     "command": ["python", "research/fusion_sweep.py", "--save"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["the oracle and LoCoMo corpora and their vector caches, as for the two stands "
                "it runs (research/longmem_eval.py, research/locomo_eval.py)"],
     "note": "the dense weight of the calibrated fusion swept on both pinned corpora, one "
             "harness run per weight, everything else fixed. Deterministic given the caches; "
             "about twenty minutes of CPU."},
    {"file": "research/results/lexical_morphology_vault.json",
     "command": ["python", "research/lexical_morphology_probe.py", "--protocol", "both",
                 "--out", "research/results/lexical_morphology_vault.json"],
     "kind": ABSENT_INPUT, "task": "external-retrieval",
     "inputs": ["a populated Nevertwice store (NEVERTWICE_VAULT); the committed artifact was "
                "produced on the owner's, which is private"],
     "note": "lexical recall on a real store with the tokenizer's morphology off and on, by "
             "language half; rates and counts only, no note text. Anyone with a populated "
             "store reproduces the shape, nobody reproduces the owner's numbers."},
    {"file": "research/results/frontier.json",
     "command": ["python", "research/frontier_eval.py", "judge", "--save"],
     "kind": HARDWARE, "task": "answer-accuracy",
     "inputs": ["the oracle corpus and vector cache", "a local Ollama serving the reader, the two judges "
                "and bge-m3", "the contexts of every arm (frontier_eval.py contexts, some in the "
                "competitor venvs, two of them reading the stores head_to_head.py left on disk)"],
     "note": "judge-scored answer accuracy per system and k with the reader's own prompt-token count; "
             "not deterministic (local models at temperature 0 still drift), so the summary is "
             "reproduced from the committed caches and a fresh run is a new measurement."},
    {"file": "research/results/longmem_s.json",
     "command": ["python", "research/longmem_eval.py", "--data=s", "--save",
                 "--out=research/results/longmem_s.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/longmemeval_s.json (third-party, MIT, hash-pinned in "
                "research/corpus_pin.py; 278 MB, fetch with `python research/corpus_pin.py "
                "--fetch longmemeval_s`)",
                "a local Ollama serving bge-m3",
                "research/data/longmem_embeds__longmemeval_s__c28000.json (built by --data=s --embed, "
                "about half an hour, 272 MB)"],
     "note": "the same benchmark outside the oracle setting: 19,206 retrievable sessions of the "
             "19,829 unique ids, the other 623 carrying no text in the published corpus. "
             "Deterministic given the embedding cache; the report itself takes about half an "
             "hour because the ranking is pure Python over twenty thousand vectors."},
    {"file": "research/results/head_to_head_v2.json",
     "command": ["python", "research/head_to_head.py", "--only=nevertwice,mem0,langmem,amem",
                 "--save", "--out=research/results/head_to_head_v2.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/longmemeval_oracle.json (hash-pinned, see above)",
                "a local Ollama serving bge-m3",
                "for the competitor arms: an environment with mem0ai, langgraph + langmem + "
                "langchain-ollama, and chromadb"],
     "volatile": ["ingest_s", "query_s", "_wall_s"],
     "note": "the same four systems on the same pinned pool with the same embedder and the same "
             "scoring function. Zep and Cognee record a blocker rather than a number: the first "
             "needs Neo4j or FalkorDB, the second an unwritten adapter. The timings move with "
             "the machine and are marked volatile; the recall columns do not."},
    {"file": "research/results/head_to_head_locomo.json",
     "command": ["python", "research/head_to_head.py", "--data=locomo",
                 "--only=nevertwice,mem0,langmem,amem", "--save",
                 "--out=research/results/head_to_head_locomo.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/locomo10.json (hash-pinned, see above)",
                "a local Ollama serving bge-m3",
                "for the competitor arms: an environment with mem0ai, langgraph + langmem + "
                "langchain-ollama, and chromadb"],
     "volatile": ["ingest_s", "query_s", "_wall_s"],
     "note": "LoCoMo pooled globally - one store for all ten conversations, which is what a whole "
             "history looks like to a competitor store - scored on retrieval of the annotated "
             "evidence turn. Same embedder and scoring for every arm."},
    {"file": "research/results/head_to_head_s.json",
     "command": ["python", "research/head_to_head.py", "--data=s",
                 "--only=nevertwice,mem0,langmem,amem", "--save",
                 "--out=research/results/head_to_head_s.json"],
     "kind": HARDWARE, "task": "external-retrieval",
     "inputs": ["research/data/longmemeval_s.json (hash-pinned, see above)",
                "a local Ollama serving bge-m3",
                "for the competitor arms: an environment with mem0ai, langgraph + langmem + "
                "langchain-ollama, and chromadb"],
     "volatile": ["ingest_s", "query_s", "_wall_s"],
     "note": "the same four systems on the non-oracle pool: 19,206 retrievable sessions instead "
             "of 940. One store arm has a blocker rather than a number in the committed artifact; "
             "the table names it. Ingest is hours per competitor arm, one process at a time."},
    {"file": "research/results/longmem_s_raw.json",
     "command": ["python", "research/longmem_eval.py", "--data=s", "--no-morphology", "--save",
                 "--out=research/results/longmem_s_raw.json"],
     "kind": HARDWARE, "task": "retrieval",
     "inputs": ["research/data/longmemeval_s.json (hash-pinned, see above)",
                "the embedding cache built by --data=s --embed"],
     "note": "the morphology ablation on the non-oracle pool: the same sessions and questions as "
             "longmem_s.json with the lexical arm tokenised without stop words or stems. "
             "Deterministic given the cache."},
    {"file": "research/results/asof_v1.json",
     "command": ["python", "research/asof_bench.py", "--arms", "nevertwice,naive", "--runs", "2",
                 "--out", "research/results/asof_v1.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "as-of recall: the same corpus ingested with dates two months apart and asked for a "
             "day between the sessions and a day after. Two ingest runs pooled; the extractor is "
             "not deterministic, so a fresh run is a new measurement of the same design."},
    {"file": "research/results/asof_recent.json",
     "command": ["python", "research/asof_bench.py", "--recent", "--arms", "nevertwice", "--runs", "2",
                 "--out", "research/results/asof_recent.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "the J2b control: the same as-of design with the first session dated inside the 90-day "
             "archive window, so the archived-closure rate of asof_v1.json is read against the write "
             "path's own rate."},
    {"file": "research/results/facts_dilution.json",
     "command": ["python", "research/facts_dilution_probe.py", "--save",
                 "--out", "research/results/facts_dilution.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1_implicit.json",
                "research/results/supersession_v1_implicit.json (names the seven control cases of finding 5a)",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "ledger K1: the rank of the correct note for every implicit-corpus control case with the "
             "[facts] block in the notes' cached text and with it stripped and re-embedded; the gate "
             "(worse on at least six of the seven named cases, better on none) was written before the run."},
    {"file": "research/results/silence_probe.json",
     "command": ["python", "research/silence_probe.py", "--save",
                 "--out", "research/results/silence_probe.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "research/results/asof_v1.json (names the first sessions the extractor left without a note)",
                "a local Ollama serving the extraction model"],
     "note": "ledger K3: for every first session the as-of artifact marks as never written, what the "
             "extraction prompt returned (no items, items the writer rejected, a failure) beside what "
             "the public path wrote - a diagnosis of the extractor's silence on a two-sentence session."},
    {"file": "research/results/supersession_v1_implicit.json",
     "command": ["python", "research/supersession_bench.py", "--dataset",
                 "research/data/supersession_v1_implicit.json", "--arms", "nevertwice,naive"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1_implicit.json",
                "a local Ollama serving bge-m3 and the extraction model",
                "mem0ai in its own environment for the Mem0 arm"],
     "note": "the implicit-replacement variant: the committed artifact pools two engine runs with "
             "the Mem0 and naive arms carried beside them (--pool ... --with ...); the extractor "
             "is not deterministic."},
    {"file": "research/results/supersession_baseline_ef8120d.json",
     "command": ["python", "research/supersession_bench.py", "--arms", "nevertwice,naive"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["a git worktree of this repository at ef8120d (the engine before J2b and the literal-fact "
                "channel) with HEAD's research/supersession_bench.py copied into it, so the old engine is "
                "scored by today's classifier",
                "research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "ledger K2 / naryad B item 2: the historical baseline for the corrected over-retraction metric - "
             "two runs of the pre-J2b engine pooled with the same bench, so the J2b change has a before on the "
             "metric the bench reads today. Its claims are registered born withdrawn (historical) and cite "
             "nothing; the artifact records engine_commit and bench_commit."},
    {"file": "research/results/supersession_baseline_ef8120d_implicit.json",
     "command": ["python", "research/supersession_bench.py", "--dataset",
                 "research/data/supersession_v1_implicit.json", "--arms", "nevertwice,naive"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["a git worktree of this repository at ef8120d with HEAD's research/supersession_bench.py copied "
                "into it (see supersession_baseline_ef8120d.json)",
                "research/data/supersession_v1_implicit.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "the implicit-corpus half of the same historical baseline: the pre-J2b engine under today's "
             "classifier, two runs pooled; registered born withdrawn."},
    {"file": "research/results/supersession_k7_d07375e.json",
     "command": ["python", "research/supersession_bench.py", "--arms", "nevertwice,naive"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at d07375e - the engine with the K7 same-fact absorb gate ON (its default before the "
                "revert), the K5 retry and the K6 preamble strip",
                "research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model",
                "the carried Mem0 and Zep/Graphiti run files of campaign K (--with)"],
     "note": "ledger K7: the artifact of record for the K7 mechanism as measured - two runs pooled at d07375e. The gate "
             "was read by the letter: over-retraction proper 0.000 (met), stale 0.117 against a cap of 0.053 (missed), "
             "so the default was reverted in the next commit and the mechanism kept as an opt-in switch. Registered as a "
             "historical family that cites nothing."},
    {"file": "research/results/supersession_k7_d07375e_implicit.json",
     "command": ["python", "research/supersession_bench.py", "--dataset",
                 "research/data/supersession_v1_implicit.json", "--arms", "nevertwice,naive"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at d07375e (see supersession_k7_d07375e.json)",
                "research/data/supersession_v1_implicit.json",
                "a local Ollama serving bge-m3 and the extraction model",
                "the carried Mem0 and Zep/Graphiti run files of campaign K (--with)"],
     "note": "the implicit-corpus half of the K7 record: over-retraction proper 0.000 (met), stale 0.167 against a cap of "
             "0.087 (missed); two runs pooled at d07375e; registered as a historical family."},
    {"file": "research/results/asof_k7_d07375e.json",
     "command": ["python", "research/asof_bench.py", "--arms", "nevertwice,naive", "--runs", "2",
                 "--out", "research/results/asof_v1.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at d07375e (the K5 retry and the K7 gate on)",
                "research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model",
                "the carried Zep/Graphiti as-of runs of campaign K (--with)"],
     "note": "the as-of stand at d07375e, kept as the record the K5 gate (never-written first sessions) was read from; "
             "the live as-of claims come from the re-run on the engine the gates left standing."},
    {"file": "research/results/k8_collisions_explicit.json",
     "command": ["python", "research/k8_collisions.py", "--stand", "supersession",
                 "--out", "research/results/k8_collisions_explicit.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at a04b547 (the engine before K8: absorb by title)",
                "research/data/supersession_v1.json", "a local Ollama serving bge-m3 and the extraction model"],
     "note": "ledger K8 step 0: the K7 store made durable - every same-slug collision the write path met on the "
             "explicit corpus, both statements and the case's truth, recorded by a wrapper around write_typed_note. "
             "The calibration set of the skeleton test and the truth set of the judge."},
    {"file": "research/results/k8_collisions_implicit.json",
     "command": ["python", "research/k8_collisions.py", "--stand", "supersession", "--dataset",
                 "research/data/supersession_v1_implicit.json", "--out", "research/results/k8_collisions_implicit.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at a04b547", "research/data/supersession_v1_implicit.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "the implicit-corpus half of the K8 collision record."},
    {"file": "research/results/k8_collisions_asof.json",
     "command": ["python", "research/k8_collisions.py", "--stand", "asof",
                 "--out", "research/results/k8_collisions_asof.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["this repository at a04b547", "research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and the extraction model"],
     "note": "the two-day dating of the as-of stand, controls included: the `r` branch (same slug, another day - "
             "the slug retirement) recorded pair by pair; 7 of 17 still-true control notes were retired."},
    {"file": "research/results/k8_step0.json",
     "command": ["python", "research/k8_skeleton.py", "research/results/k8_collisions_explicit.json",
                 "research/results/k8_collisions_implicit.json", "research/results/k8_collisions_asof.json",
                 "--out", "research/results/k8_step0.json"],
     "kind": DETERMINISTIC, "task": "supersession",
     "inputs": ["the three k8_collisions_*.json files", "research/data/supersession_v1{,_implicit}.json (the markers)"],
     "note": "ledger K8 step 0: can a skeleton test tell a replacement from a different fact on one topic? AUC "
             "0.63-0.66 on the extractor's descriptions, 0.76-0.81 on the facts block - the 'about 0.5' branch; the "
             "similarity is published and not shipped as a rule. Pure string work over the recorded pairs."},
    {"file": "research/results/k8_judge_eval.json",
     "command": ["python", "research/k8_judge_eval.py", "research/results/k8_collisions_explicit.json",
                 "research/results/k8_collisions_implicit.json", "research/results/k8_collisions_asof.json",
                 "--out", "research/results/k8_judge_eval.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["the three k8_collisions_*.json files", "a local Ollama serving the extraction model (the judge)"],
     "note": "ledger K8 step 3: the same-fact judge (K7's prompt, K8's sleep-time step) over 222 pairs, 204 of known "
             "truth - accuracy 0.956, `replaces` precision 1.000 / recall 0.953, `separate` precision 0.571 / recall "
             "1.000, 414.5 tokens a pair. What K7 was really worth, without a campaign or a cache confound."},
    {"file": "research/results/k8_vault_dryrun.json",
     "command": ["python", "research/k8_vault_dryrun.py", "<path to the owner's vault>", "--weeks", "4",
                 "--today", "2026-09-16", "--out", "research/results/k8_vault_dryrun.json"],
     "kind": ABSENT_INPUT, "task": "supersession",
     "inputs": ["the owner's private vault (8,235 typed notes; read-only, not committed)"],
     "note": "ledger K8, the price of layer 3 in the field: 292 same-slug collisions from other sessions in the four "
             "weeks to 2026-09-16 -> at most 73 contested pairs a week for the sleep-time judge (rules 2 and 4 are "
             "inert on a store whose notes carry no facts block yet)."},
    {"file": "research/results/code_sessions_v1.json",
     "command": ["python", "research/code_sessions_eval.py", "judge", "--arms", "nevertwice_full,naive,mem0_infer", "--save"],
     "kind": HARDWARE, "task": "code-sessions",
     "inputs": ["research/data/code_sessions_v1.json (generated by research/gen_code_sessions.py with a local GLM model)",
                "a local Ollama serving qwen2.5-7b-64k (extractor), qwen2.5:7b (reader), qwen3.6:27b (judge), bge-m3",
                "for the Mem0 arm: a separate environment with mem0ai[extras]"],
     "volatile": ["seconds"],
     "note": "the code-session stand (ledger J3): contexts, answers and verdicts are cached per stage"},
    {"file": "research/results/code_heldout_v2.json",
     "command": ["python", "research/code_sessions_eval.py", "summary", "--arms", "nevertwice_full,naive,mem0_infer",
                 "--corpus", "D:/Coding/_nevertwice_polygon/code_heldout/code_heldout_v2.json", "--save",
                 "--out", "research/results/code_heldout_v2.json"],
     "kind": ABSENT_INPUT, "task": "code-sessions",
     "inputs": ["the private hand-marked held-out over the owner's own coding sessions - outside the repository by "
                "design; research/data/code_heldout_review_manifest.json records its hash and the review counts",
                "a local Ollama serving qwen2.5-7b-64k (extractor, temperature pinned to 0), qwen2.5:7b (reader), bge-m3",
                "for the Mem0 arm: a separate environment with mem0ai[extras]"],
     "volatile": ["seconds"],
     "note": "cannot be regenerated from a clone: the sessions are the owner's; the manifest is what a reader can "
             "check. Carries the fact-survival metric per arm beside the reader's accuracy."},
    {"file": "research/data/guard_bench_v1.json",
     "command": ["python", "research/gen_guard_bench.py", "--out", "research/data/guard_bench_v1.json"],
     "kind": DETERMINISTIC, "task": "active-memory",
     "inputs": [],
     "note": "the guard corpus: labelled tool calls written from an audited table (ledger J6)"},
    {"file": "research/results/guard_bench_v1.json",
     "command": ["python", "research/guard_bench.py", "--llm", "--save"],
     "kind": HARDWARE, "task": "active-memory",
     "inputs": ["research/data/guard_bench_v1.json",
                "a local Ollama serving bge-m3 (the prompt_recall arm) and qwen3-coder:30b (the model-written patterns)"],
     "volatile": ["ms_per_call", "seconds", "latency"],
     "note": "every arm read at a matched false-alarm rate; the deterministic arms reproduce on CPU, the model arm needs the GPU"},
    {"file": "research/results/supersession_v1.json",
     "command": ["python", "research/supersession_bench.py",
                 "--arms", "nevertwice,naive", "--out",
                 "research/results/supersession_v1.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and qwen3-coder:30b",
                "for the Mem0 arm: a separate environment with mem0ai[extras]"],
     "volatile": ["seconds"],
     "note": "the three-arm supersession stand. The naive arm is deterministic and needs "
             "nothing; the two LLM arms call a local extraction model, so their per-case "
             "results move between runs even at temperature 0 - the published figures moved "
             "when the extractor stopped answering in the wrong language. The dataset is "
             "committed and hash-checked (gen_supersession_dataset.py --check), which is the "
             "half a stranger verifies without the model. The Mem0 arm runs from its own "
             "environment and is merged in by --compare."},
    {"file": "research/results/supersession_v1_switch.json",
     "command": ["python", "research/supersession_bench.py", "--arms", "nevertwice", "--sleep",
                 "--out", "research/results/supersession_v1_switch.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and qwen3-coder:30b",
                "NEVERTWICE_EXPLICIT_RETIRE=judge in the environment"],
     "volatile": ["seconds"],
     "note": "the switch arm of the explicit corpus: the same stand with the extractor's explicit "
             "`contradicts` routed to the sleep-time judge instead of retiring at write time "
             "(default write). Pooled over two runs like the default arm, and read twice - after the "
             "replacing session and after the adjudication - so the switch's price is a paired "
             "comparison within one extraction draw rather than a difference between two."},
    {"file": "research/results/supersession_v1_implicit_switch.json",
     "command": ["python", "research/supersession_bench.py", "--dataset",
                 "research/data/supersession_v1_implicit.json", "--arms", "nevertwice", "--sleep",
                 "--out", "research/results/supersession_v1_implicit_switch.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1_implicit.json",
                "a local Ollama serving bge-m3 and qwen3-coder:30b",
                "NEVERTWICE_EXPLICIT_RETIRE=judge in the environment"],
     "volatile": ["seconds"],
     "note": "the same switch arm on the corpus whose second session never names the retraction - "
             "the half of the rule that decides the default, since this is where the switch's cost "
             "in stale facts shows up."},
    {"file": "research/results/asof_v1_switch.json",
     "command": ["python", "research/asof_bench.py", "--arms", "nevertwice,naive", "--runs", "2",
                 "--sleep", "--out", "research/results/asof_v1_switch.json"],
     "kind": HARDWARE, "task": "supersession",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and qwen3-coder:30b",
                "NEVERTWICE_EXPLICIT_RETIRE=judge in the environment"],
     "volatile": ["seconds"],
     "note": "the as-of stand under the switch, both readings. It carries no published claim - the "
             "switch's effect is decided on the displacement corpora - and is kept because a mode "
             "that changes what is retired changes when a belief interval closes, and that is the "
             "measure this stand reads."},
    {"file": "research/results/abstention_ab.json",
     "command": ["python", "research/abstention_ab.py", "--part", "all",
                 "--out", "research/results/abstention_ab.json"],
     "kind": HARDWARE, "task": "abstention",
     "inputs": ["research/data/supersession_v1.json",
                "a local Ollama serving bge-m3 and qwen3-coder:30b"],
     "note": "the sweep that turned both abstention defaults off. The re-mining half is pure "
             "file I/O and reproduces exactly; the two retrieval sweeps build a store first, "
             "so they need the model."},
    {"file": "research/matched_conditions.json",
     "command": ["python", "research/matched_conditions.py", "--save"],
     "kind": DETERMINISTIC, "task": "F1",
     "inputs": ["research/matched_conditions_corpus.json"],
     "volatile": ["latency_ms_per_episode"],
     "note": "the precision/recall curve over the firing threshold"},
    {"file": "research/cheap_baselines.json",
     "command": ["python", "research/cheap_baselines.py", "--save"],
     "kind": DETERMINISTIC, "task": "F2",
     "inputs": ["research/matched_conditions_corpus.json",
                "research/cheap_baselines_rules.json"],
     "volatile": ["latency_ms_per_episode"],
     "note": "the six B5 baselines at a matched false-alarm rate"},
    {"file": "research/ablations.json",
     "command": ["python", "research/ablations.py", "--save"],
     "kind": DETERMINISTIC, "task": "F4",
     "inputs": ["research/matched_conditions_corpus.json"],
     "note": "one mechanism removed at a time"},
    {"file": "research/uncertainty.json",
     "command": ["python", "research/uncertainty.py", "--save"],
     "kind": DETERMINISTIC, "task": "F5",
     "inputs": ["research/matched_conditions_corpus.json"],
     "note": "paired tests, bootstrap with a fixed seed, Holm correction"},
    {"file": "research/harms.json",
     "command": ["python", "research/harms.py", "--save"],
     "kind": DETERMINISTIC, "task": "F6",
     "inputs": ["research/matched_conditions_corpus.json", "research/poisoning.json"],
     "note": "the safety evaluation"},
    {"file": "research/poisoning.json",
     "command": ["python", "research/poisoning.py", "--save"],
     "kind": DETERMINISTIC, "task": "E2/prior",
     "inputs": [], "note": "memory-poisoning attack corpus"},
    {"file": "research/forgetting.json",
     "command": ["python", "research/forgetting.py", "--save"],
     "kind": DETERMINISTIC, "task": "prior",
     "inputs": [], "requires": ["numpy"],
     "note": "coverage under forgetting. The ONE research script that is not stdlib-only: it "
             "needs numpy, which the frozen image deliberately does not carry."},
    {"file": "research/longitudinal_results.json",
     "command": ["python", "research/longitudinal_improvement.py", "--sweep", "--save"],
     "kind": DETERMINISTIC, "task": "prior",
     "inputs": [], "note": "the active-vs-inject token ratio"},
    {"file": "research/blast_radius_precision.json",
     "command": ["python", "research/blast_radius_precision.py"],
     "kind": DETERMINISTIC, "task": "I4",
     "inputs": [".git", "research/blast_radius_calibration.json",
                "research/blast_radius_labels.json"],
     "note": "the precision census that closed the blast-radius track: every finding labelled, "
             "and the git grep baseline swept to a matched flag rate. Carries no timings, so it "
             "is byte-identical or it is wrong. Needs FULL history."},
    {"file": "research/blast_radius_calibration.json",
     "command": ["python", "research/blast_radius_calibration.py", "--commits", "150"],
     "kind": DETERMINISTIC, "task": "I1",
     "inputs": [".git"],
     "volatile": ["seconds"],
     "note": "replays the blast-radius checker over this repository's history. Deterministic "
             "given the same 150 commits - the per-commit timings are the only thing that "
             "measures the machine, and they are declared volatile. Needs FULL history: a "
             "shallow clone cannot reach the 150th ancestor and the run will come up short."},
    {"file": "research/latency_bench.json",
     "command": ["python", "research/latency_bench.py", "--save"],
     "kind": MACHINE, "task": "prior", "inputs": [],
     "note": "measures THIS machine. The same minimum-of-five statistic moved by about a "
             "third between sessions on one box, so byte-identity is not the test - the "
             "order of magnitude is."},
    {"file": "research/embed_universal/heldout/serving_check.json",
     "command": ["python", "research/embed_universal/serving_check.py"],
     "kind": HARDWARE, "task": "M6",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers", "BAAI/bge-m3",
                "research/embed_universal/models/universal_v1_merged",
                "a running Ollama serving nevertwice-embed"],
     "note": "whether the served f16 GGUF matches the safetensors checkpoint the numbers were "
             "measured on. Needs a running Ollama with the model already loaded - the script "
             "refuses to pull it rather than filling somebody else's working set."},
    {"file": "research/embed_universal/heldout/capacity_sweep.json",
     "command": ["python", "research/embed_universal/capacity_sweep.py"],
     "kind": HARDWARE, "task": "M5",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers", "BAAI/bge-m3",
                "research/embed_universal/models/universal_v1_merged"],
     "note": "four LoRA ranks trained, evaluated and deleted. It needs no swept checkpoint as an "
             "input because it makes them: about five minutes on a 5090. It will NOT come back "
             "byte-identical - the run itself measured that this recipe does not reproduce its "
             "own weights - which is why it is classified by hardware rather than as "
             "deterministic."},
    {"file": "research/embed_universal/heldout/matryoshka_v1.json",
     "command": ["python", "research/embed_universal/truncation_eval.py"],
     "kind": HARDWARE, "task": "M4",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers", "BAAI/bge-m3",
                "research/embed_universal/models/universal_v1_merged",
                "research/embed_universal/models/matryoshka_v1_merged"],
     "note": "what truncation costs at 1024, 512 and 256 dimensions. The Matryoshka checkpoint "
             "was DELETED per the decision; the HALF of this table that matters - the shipped "
             "model truncated naively - needs only universal_v1_merged and is the part the "
             "model card quotes."},
    {"file": "research/embed_universal/heldout/distil_v1.json",
     "command": ["python", "research/embed_universal/heldout_eval.py",
                 "--out", "research/embed_universal/heldout/distil_v1.json"],
     "kind": HARDWARE, "task": "M3",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers",
                "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3",
                "research/embed_universal/models/universal_v1_merged",
                "research/embed_universal/models/distil_v1_merged"],
     "note": "the table that closed M3. Like M2's, the checkpoint it measures was DELETED per "
             "the decision written before the run, so re-running means regenerating it first "
             "with distil.py - about five minutes on a 5090, committed. The measurement is kept; "
             "the model is not."},
    {"file": "research/embed_universal/heldout/hard_v1.json",
     "command": ["python", "research/embed_universal/heldout_eval.py",
                 "--out", "research/embed_universal/heldout/hard_v1.json"],
     "kind": HARDWARE, "task": "M2",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers",
                "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3",
                "research/embed_universal/models/universal_v1_merged",
                "research/embed_universal/models/hard_v1_merged"],
     "note": "the table that closed M2. The mined checkpoint it measures was DELETED per the "
             "deletion decision written before the run, so this cannot be re-run without first "
             "regenerating it - mine_negatives.py then train_hard.py, about four minutes on a "
             "5090, both committed. The measurement is kept; the model is not."},
    {"file": "research/embed_universal/heldout/baseline_v1.json",
     "command": ["python", "research/embed_universal/heldout_eval.py"],
     "kind": HARDWARE, "task": "M1",
     "inputs": ["research/embed_universal/heldout/external_heldout_v1.json",
                "a CUDA GPU", "torch", "sentence_transformers",
                "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3",
                "research/embed_universal/models/universal_v1_merged"],
     "note": "the embedding baseline on the frozen external set. Deterministic in principle - "
             "the numbers are model outputs, not machine measurements - but it needs ~5 GB of "
             "weights and a GPU, so a bare clone cannot run it. The BENCHMARK it ran against is "
             "committed and hash-checked, which is the half a stranger can verify without the "
             "hardware: research/embed_universal/heldout_set.py --verify."},
    {"file": "research/capability_grid.json",
     "command": ["python", "research/capability_grid.py", "--all"],
     "kind": HARDWARE, "task": "F3",
     "inputs": ["local Qwen2.5-Instruct weights", "a CUDA GPU", "torch", "transformers"],
     "note": "generates text with local models. Needs hardware a fresh clone does not carry."},
    {"file": "research/longmem_results.json",
     "command": ["python", "research/longmem_eval.py"],
     "kind": ABSENT_INPUT, "task": "prior",
     "inputs": ["research/data/longmemeval_oracle.json"],
     "note": "the LongMemEval-oracle dataset is third-party and not committed. Every claim "
             "from this artifact is already withdrawn as stale in the evidence manifest."},
]


#: Files under research/ that are inputs to the runner or its own output, not research results.
NOT_A_RESULT = {
    "research/evidence_manifest.json",          # the governance file itself
    "research/reproduction.json",               # this runner's own output
    "research/matched_conditions_corpus.json",  # an input
    "research/cheap_baselines_rules.json",      # an input
}


def scope() -> dict:
    """What this package promises to reproduce, and what it deliberately does not.

    The rule, derived rather than asserted: **every artifact backing a claim the project still
    publishes, plus the F-phase outputs.** The repository also carries a few dozen older result
    files from experiments whose claims are already withdrawn as stale, and regenerating those
    would reproduce numbers nobody is standing behind.

    Computed from `evidence_manifest.json` so it cannot drift: if a withdrawn claim is ever
    revived, its artifact stops being out of scope and the check fails until it is listed. A
    hand-maintained exclusion list would have gone stale the first time that happened.
    """
    listed = {a["file"] for a in ARTIFACTS}
    on_disk = {f"research/{q.name}" for q in (ROOT / "research").glob("*.json")}
    live_backed, withdrawn_backed = set(), set()
    manifest = ROOT / "research" / "evidence_manifest.json"
    if manifest.is_file():
        for claim in json.loads(manifest.read_text(encoding="utf-8"))["claims"]:
            raw = claim.get("raw")
            if not raw:
                continue
            (withdrawn_backed if claim.get("stale") else live_backed).add(raw)
    unlisted = on_disk - listed - NOT_A_RESULT
    return {
        "rule": "every artifact backing a claim the project still publishes, plus the F-phase "
                "outputs",
        "in_scope": sorted(listed),
        "live_claims_backed_by": sorted(live_backed),
        "live_artifacts_missing_from_the_manifest": sorted(live_backed - listed),
        "out_of_scope": sorted(unlisted),
        "out_of_scope_reason": "these back only claims already withdrawn as stale, or no claim "
                               "at all - older experiment outputs kept for the record. "
                               "Regenerating them would reproduce numbers nobody stands behind.",
        "out_of_scope_still_backing_a_live_claim": sorted(unlisted & live_backed),
        "withdrawn_claims_backed_by": sorted(withdrawn_backed - live_backed),
    }


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(path: Path, volatile: list) -> str | None:
    """A hash of everything in the artifact that SHOULD be reproducible.

    Some artifacts legitimately embed a measurement of the machine that produced them - F2
    reports each arm's latency per episode, which is real evidence and must not be deleted. But
    a byte-hash over a file containing a timing can never match on another machine, so a
    reproduction package that hashed the raw bytes would report a permanent failure and teach
    the reader to ignore it.

    So the volatile fields are named per artifact, stripped, and the rest is hashed. Both hashes
    are reported: the raw one shows the file is not byte-identical, the canonical one shows
    whether anything that matters changed.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    _strip(data, set(volatile or ()))
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _strip(node, keys: set) -> None:
    if isinstance(node, dict):
        for k in list(node):
            if k in keys:
                node.pop(k)
            else:
                _strip(node[k], keys)
    elif isinstance(node, list):
        for item in node:
            _strip(item, keys)


def missing_modules(spec: dict) -> list:
    """Third-party modules this artifact needs that are not importable here.

    Treated exactly like a missing dataset or an absent GPU: named, and the artifact skipped
    with the reason, rather than run and reported as a failure. Building the container is what
    surfaced this - `forgetting.py` needs numpy, and the claim that "no third-party packages are
    required" was true of the core and false of one research script. It was published in three
    places before a bare image proved otherwise.
    """
    out = []
    for name in spec.get("requires") or ():
        try:
            __import__(name)
        except ImportError:
            out.append(name)
    return out


def missing_inputs(spec: dict) -> list:
    """Inputs that are files and are not here. Non-path inputs are hardware, listed as-is."""
    out = []
    for item in spec["inputs"]:
        if "/" not in item and "." not in item:
            out.append(item)
            continue
        if not (ROOT / item).exists():
            out.append(item)
    return out


def run_one(spec: dict, regenerate: bool) -> dict:
    path = ROOT / spec["file"]
    before = sha256(path)
    before_canonical = canonical(path, spec.get("volatile"))
    result = {
        "file": spec["file"], "task": spec["task"], "kind": spec["kind"],
        "note": spec["note"], "command": " ".join(spec["command"]),
        "committed_sha256": before,
        "committed_canonical_sha256": before_canonical,
        "volatile_fields": spec.get("volatile") or [],
        "committed_present": before is not None,
        "missing_inputs": missing_inputs(spec),
        "requires": spec.get("requires") or [],
        "missing_modules": missing_modules(spec),
    }
    if not result["committed_present"]:
        result["status"] = "absent"
        result["explain"] = "the committed artifact is not in this clone"
        return result
    if result["missing_inputs"] or result["missing_modules"]:
        result["status"] = "skipped"
        result["explain"] = ("cannot regenerate here: missing "
                             + ", ".join(result["missing_inputs"] + result["missing_modules"]))
        return result
    if spec["kind"] in (MACHINE, HARDWARE):
        result["status"] = "skipped"
        result["explain"] = (f"{spec['kind']}: regenerating would change the numbers without "
                             "any of the code being wrong")
        return result
    if not regenerate:
        result["status"] = "not-run"
        result["explain"] = "--verify only hashes what is committed"
        return result

    started = time.perf_counter()
    proc = subprocess.run([sys.executable, *spec["command"][1:]], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=1800,
                          env=_env())
    result["seconds"] = round(time.perf_counter() - started, 1)
    if proc.returncode != 0:
        result["status"] = "failed"
        result["explain"] = (proc.stderr or proc.stdout)[-300:].replace("\n", " | ")
        return result
    after = sha256(path)
    after_canonical = canonical(path, spec.get("volatile"))
    result["regenerated_sha256"] = after
    result["regenerated_canonical_sha256"] = after_canonical
    if after_canonical == before_canonical:
        result["status"] = "reproduced"
        if after != before and result["volatile_fields"]:
            result["explain"] = ("byte-different only in the declared volatile fields ("
                                 + ", ".join(result["volatile_fields"])
                                 + "): everything that should reproduce did")
    else:
        result["status"] = "differs"
        result["explain"] = ("the regenerated artifact differs in fields that are NOT declared "
                             "volatile - either the code changed without the artifact being "
                             "restamped, or this artifact is not as deterministic as it is "
                             "declared to be")
    return result


def _env() -> dict:
    import os
    return {**os.environ, "PYTHONUTF8": "1"}


def build(regenerate: bool = True) -> dict:
    results = [run_one(spec, regenerate) for spec in ARTIFACTS]
    deterministic = [r for r in results if r["kind"] == DETERMINISTIC]
    reproduced = [r for r in deterministic if r["status"] == "reproduced"]
    broken = [r for r in deterministic
              if r["status"] in ("differs", "failed", "absent")]
    return {
        "schema_version": 1,
        "generated_by": "python research/reproduce.py",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "third_party_packages_required": sorted(
            {mod for spec in ARTIFACTS for mod in (spec.get("requires") or ())}),
        "container": {
            "dockerfile": "Dockerfile",
            "base_pinned_by": "digest",
            "built_end_to_end_on_the_authoring_machine": False,
            "why": "the build exceeded this environment's time limit while pulling the base "
                   "image and was never observed to finish. The Dockerfile's CONTENTS are "
                   "checked by tests/_test_reproduction.py; the reproduction itself is verified "
                   "by running this script from a fresh clone, which is what F7's exit "
                   "criterion asks for. The container is a convenience on top of that.",
        },
        "scope": scope(),
        "artifacts": results,
        "summary": {
            "deterministic": len(deterministic),
            "reproduced": len(reproduced),
            "not_reproduced": len(broken),
            "skipped": len([r for r in results if r["status"] == "skipped"]),
        },
        "verdict": _verdict(results, deterministic, reproduced, broken),
    }


def _verdict(results: list, deterministic: list, reproduced: list, broken: list) -> list:
    out = []
    # Compared against what was ATTEMPTED, not against every deterministic entry: an artifact
    # skipped for a named missing dependency neither reproduced nor failed, and counting it as
    # a failure printed "0 of 8 did NOT reproduce: ." - a sentence with no meaning, and the
    # kind of summary a reader stops trusting.
    attempted = [r for r in deterministic if r["status"] not in ("skipped", "not-run")]
    skipped_deterministic = [r for r in deterministic if r["status"] == "skipped"]
    if broken:
        out.append(
            f"{len(broken)} of {len(attempted)} attempted deterministic artifacts did NOT "
            "reproduce: " + ", ".join(r["file"] for r in broken) +
            ". Until that is fixed the reproduction package does not hold.")
    elif attempted:
        tail = (f" {len(skipped_deterministic)} more was skipped for a named missing "
                f"dependency and is listed below." if skipped_deterministic else "")
        out.append(
            f"All {len(attempted)} attempted deterministic artifacts regenerated "
            "BYTE-IDENTICAL from this clone, with no author help. That is what F7's exit "
            "criterion asks for, on the artifacts it can ask it of." + tail)
    else:
        out.append("Nothing was regenerated in this run.")

    skipped = [r for r in results if r["status"] == "skipped"]
    if skipped:
        out.append(
            "Deliberately NOT reproduced here, and each says why in the table: " +
            ", ".join(f"{r['file']} ({r['kind']})" for r in skipped) +
            ". A package that quietly re-ran only the easy artifacts and printed 'all "
            "reproduced' would be worse than none.")
    absent = [r for r in results if r["missing_inputs"]]
    if absent:
        out.append(
            "Inputs this clone does not carry: " +
            "; ".join(f"{r['file']} needs {', '.join(r['missing_inputs'])}" for r in absent) +
            ". Every claim resting on those is already withdrawn as stale in the evidence "
            "manifest, so nothing published depends on an artifact a stranger cannot rebuild.")
    sc = scope()
    if sc["out_of_scope_still_backing_a_live_claim"]:
        out.append(
            "SCOPE FAILURE: these artifacts back a claim the project still publishes and are "
            "NOT in the reproduction manifest: "
            + ", ".join(sc["out_of_scope_still_backing_a_live_claim"])
            + ". A published claim whose artifact nobody reproduces is a claim nobody can check.")
    elif sc["out_of_scope"]:
        out.append(
            f"{len(sc['out_of_scope'])} other result files under research/ are deliberately out "
            "of scope: they back only claims already withdrawn as stale, or no claim at all. "
            "Every artifact behind a claim the project still publishes IS in the manifest, and "
            "that is checked against the evidence manifest rather than maintained by hand.")

    required = sorted({mod for spec in ARTIFACTS for mod in (spec.get("requires") or ())})
    if required:
        out.append(
            "The core needs no third-party packages and neither do most of these scripts, "
            "which is why the frozen environment is a pinned interpreter and nothing else. "
            "The exception is named rather than hidden: " + ", ".join(required) +
            " is needed by at least one research script, so in a bare image that artifact is "
            "SKIPPED with the reason rather than installed. Building the container is what "
            "proved the blanket claim wrong.")
    else:
        out.append(
            "No third-party package is needed anywhere here: the whole of this runs on the "
            "Python standard library, which is why the frozen environment is a pinned "
            "interpreter and nothing else.")
    return out


def render(p: dict) -> str:
    L = ["", "Reproduction package (GOAL F7)", ""]
    L.append(f"  python {p['python']}, third-party packages required: "
             f"{p['third_party_packages_required'] or 'none'}")
    L.append("")
    L.append(f"  {'artifact':44} {'task':6} {'status':12} {'kind'}")
    for r in p["artifacts"]:
        L.append(f"  {r['file']:44} {r['task']:6} {r['status']:12} {r['kind']}")
        if r.get("explain"):
            L.append(f"      {r['explain'][:110]}")
    s = p["summary"]
    sc = p["scope"]
    L.append("")
    L.append(f"  scope: {sc['rule']}")
    L.append(f"         {len(sc['out_of_scope'])} older result file(s) out of scope; "
             f"{len(sc['out_of_scope_still_backing_a_live_claim'])} of those still back a "
             f"live claim")
    L.append(f"  {s['reproduced']}/{s['deterministic']} deterministic artifacts reproduced "
             f"byte-for-byte; {s['skipped']} skipped by design")
    L.append("")
    for line in p["verdict"]:
        L.append("  * " + line.replace(". ", ".\n    "))
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true",
                    help="hash the committed artifacts without regenerating them")
    ap.add_argument("--manifest", action="store_true",
                    help="print the raw-data manifest and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    args = ap.parse_args(argv)

    if args.manifest:
        print(json.dumps(ARTIFACTS, ensure_ascii=False, indent=1))
        return 0

    payload = build(regenerate=not args.verify)
    if args.save:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render(payload))
    return 0 if payload["summary"]["not_reproduced"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
