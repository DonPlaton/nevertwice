# What the golden store does not prove

Measured efb641a, `python tests/_test_golden_store.py --no-canary` under coverage.py.

- statements entered: **44.2%**
- top-level functions entered: **151 of 246** (61.4%)

The gate written in `.loop/GOAL-CLOSE.md` (track L, L3) asks for 90% of top-level definitions
**or** this list. This is the list. It exists because a coverage number on its own invites the
wrong repair - widening the corpus until the percentage looks respectable - while the question
that matters is which *named* piece of behaviour a byte-identical run would fail to notice.

The four functions the project's headline numbers are made of are all entered, and the canary
in `tests/_canary_run.py` proves it: a `raise` injected into `_same_fact_verdict`,
`_replacement_guard`, `as_of` or `_same_replacement` makes this proof fail. A proof those four
can survive would certify a refactor that broke them.

## Not entered, by area

**everything else** (54)

`_accumulated_header` `_age_marker` `_bare_int_has_value_context` `_collapse_restatements` `_context_brief` `_earlier_text` `_ensure_vault_gitignore` `_fact_line` `_find_repo_root` `_fit_context_tail` `_fit_fact_line` `_has_value_literal` `_is_trivial_prompt` `_item_count` `_iter_contested_both` `_iter_superseded_notes` `_json_api_call` `_load_rankers` `_note_links` `_note_snippet` `_note_stale` `_pid_alive` `_project_dir_for_cwd` `_recency_fallback` `_record_recall_saving` `_recur_boost` `_referenced_paths` `_relative_value` `_rrf_scores` `_safe_model_seg` `_scrub_for_log` `_spill_context_entries` `_strip_json_fence` `_superseded_index` `_transcript_grew` `_truncate_utf8_bytes` `_twin_probability` `_twin_words` `_unverified_values` `_user_brief` `argval` `backend_report` `call_ollama` `emit_session_start_context` `llm_available` `llm_backend_desc` `local_routing_desc` `main` `maintain_contexts` `mark_resolved` `ollama_alive` `parse_session_stem` `read_session_meta` `rebuild_index`

**archive, prune and the lock** (10)

`_archive_dest` `_prune_prompt_recall_state` `acquire_lock` `archive_old_sessions` `archive_old_typed` `has_unprocessed` `load_processed` `prune_processed_db` `release_lock` `sweep_unprocessed`

**cloud extractor backends** (7)

`_call_openai_chat` `_embed_cloud` `call_cerebras` `call_cloud` `call_deepseek` `call_gemini` `call_groq`

**cross-project and prompt recall** (7)

`_cross_line` `_load_prompt_recall_state` `_prompt_recall_state_path` `_save_prompt_recall_state` `emit_prompt_recall` `rerank_notes` `retrieve_cross_project`

**CLI, status and maintenance** (6)

`_degraded_status` `_spawn_detached_catchup` `_warn_if_store_relocated` `git_autocommit` `regen_graph_for_project` `write_status`

**entity and project cards** (6)

`_entity_card_stem` `_existing_entity_card` `build_entity_card` `entity_card` `refresh_entity_cards` `write_entity_card`

**embedding cache and the scale index** (5)

`_embed_http` `_embed_key` `embedder_available` `ensure_scale_index` `rebuild_scale_index`

## What that means for a refactor

A pure move certified only by this proof is certified for the write path, the displacement
rules, the sleep-time judge, as-of, recall and the guard hot path. It is **not** certified for
the cloud backends, the scale index, the card builders or the maintenance commands: those need
their own corpus, or the move must leave them alone and say so.

