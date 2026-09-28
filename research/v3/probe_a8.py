#!/usr/bin/env python3
"""PREREG-V3 A8 C5 (the auditor's Q-C5-1..8): the probes of the pinned products - what each one's own source says and
what it does against a loopback fake upstream, pinned per field in <runs>/_a8/<run>/<arm>/probe.json, written once.

This part (C5a) holds the verdicts; they read records, never a product:

* ``fact``: a source fact is exactly one match of a pattern declared in code BEFORE the read, in a file read as data
  under a given root - its value, its 1-based line, the sha256 of the file's bytes and "file:line@sha256:<hex>"; no
  match is blocked:source-missing:<field>, two or more are blocked:source-ambiguous:<field> (never the first);
* ``mine``: the proxy's call lines of one arm and one unit ("<run>.<unit>", the /u/ prefix) on the v1 endpoint;
* mem0's fields (m0_*): the pin, the wrapped client, the usage (the adapter's LLMUsage equals the proxy's answered
  lines: calls, prompt and completion tokens, no failed and no unreported call - zero against zero is unmeasured, never
  a pass), the temperature and the thinking field as the pinned source sends them, the response format and no tool
  offered, the OSS timestamp refusal found in the source (else blocked by name, never a silent switch of the route);
* C5a-2a: mem0's source facts are DECLARED in M0_SOURCE before any read of the pinned 2.2.0 (written from 2.0.19, the
  auditor's O-a): a fact is read in a whole file or within one function (``scope``: exactly one class and one def by
  the qualified name, parsed, never imported); the add path's LLM call sites come from the AST (``llm_sites``: each
  one's line, whether it sits in a loop, its response_format as written); one distinct format is the format, several
  are a set each line must belong to (C5A-10); ``m0_calls_per_add`` bounds the answered lines by the sites x the adds
  (a site in a loop: blocked:source-unbounded); a thinking field mentioned in the DeepSeek LLM is
  blocked:source-changed - its meaning goes to the auditor;
* B-NLP NLP-3 (the auditor's PASSIVE check): spacy_models' four module-level state names and the model the product
  checks for are source facts (``nlp_facts``); ``m0_nlp_active`` reads what the adapter saw after the adds - the model
  installed, both models loaded, no failed flag, by exactly those names, no catcher line - never loading anything
  itself; anything else is blocked:nlp-off;
* C6 C1 (the auditor's Q-C6-1..7): the source facts of mem0's writer bound are declared in M0_BOUND_SOURCE (from 2.0.19
  and its openai SDK): the SDK's own retries and that the DeepSeek client keeps them, max_tokens and its send line, the
  system prompt's bytes (literal_bytes), the user prompt's sections (prompt_parts: constant bytes, fields declared in
  M0_PROMPT_FIELDS), the prompt call's keywords, the last-k window and its truncation, the top_k window and a memory's
  shape, the message frames; ``mem0_bound_facts`` reads them, ``bound_blocked`` names every reason they make no bound;
  F-C6-4 closes the chain from the adapter's message to the two prompt strings: the small helpers held whole
  (M0_HELPER_SHAPES), the add path, add() and the builder by their writes, defaults and calls' arguments (M0_WRITES,
  M0_DEFAULTS, M0_CALLS), the config's custom instructions (M0_CONFIG_DEFAULTS) and the adapter's side (M0_ADAPTER);
  the probe record carries them all (bound_facts, bound_blocked);
* C5b (the auditor's Q-C5b-1 = O-a): letta's OpenAPI-generic facts, offline - the document's pin over canonical
  JSON (lt_pin, R-C5-1) and its stability over starts (lt_stable), the adapter's call table read as data
  (letta_calls) and its conformance (lt_conformance), the recall route under a pattern declared first
  (lt_recall_route) and Point V's archival page size from the schema (lt_archival_default); the layer
  facts wait for the previous Letta release's source, fetched in its own window;
* ``verdict``: "pass" only when every field is ok, there is no problem, every boundary check is complete with 0/0 and
  no catcher line belongs to the arm; else the first blocked:<reason> - the fields in their declared order, then the
  problems - else "fail". The proxy's own spawn is unwitnessed by design (launch.spawn_proxy): 0/0 covers the product
  child's tree only (R-C5-9), and probe.json says so.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import secrets as _secrets
import sys
import textwrap
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: The adapter's start answer when mem0's own OpenAI client is wrapped (research/v3/arms/arm_mem0.py, Q-AB-1).
M0_USAGE_SOURCE = "mem0.llm.client.chat.completions.create, response.usage"
M0_PIN = ("mem0ai", "2.2.0")
M0_NO_CLIENT = "mem0's LLM has no OpenAI client"
#: C5a-2a (the auditor's O-a, 07:0x): mem0's source facts, declared in code BEFORE any read of the pinned 2.2.0 - written
#: from mem0 2.0.19 (the polygon's mem0_eval venv, read as data). On 2.2.0 anything but one match is blocked by name and
#: goes to the auditor; a new pattern is an erratum with his ruling and one E5 line, never an edit after seeing 2.2.0.
#: field: (file, pattern) - a whole-file fact; or (file, qualified name, pattern) - a fact within that function.
M0_SOURCE = {
    # the DeepSeek provider config's own default (the adapter sets none: §3, the product's defaults)
    "m0_temperature": ("mem0/configs/llms/deepseek.py", r"^\s+temperature: float = ([0-9.]+),\s*$"),
    # any mention of a thinking field in the DeepSeek LLM: none - the route is none; any - its meaning to the auditor
    "m0_thinking": ("mem0/llms/deepseek.py", r"\bthinking\b"),
    # the OSS refusal of a caller's timestamp in the SYNC Memory.add (the async twin is another class)
    "m0_timestamp": ("mem0/memory/main.py", "Memory.add",
                     r'if timestamp is not None:\s*\n\s*raise ValueError\(get_temporal_feature_error_message\("sync", "add", "timestamp"\)\)'),
    # every LLM call site on the add path, by the AST (called, or handed to a pool)
    "m0_llm_sites": ("mem0/memory/main.py", "Memory._add_to_vector_store", "self.llm.generate_response"),
    # the answer's shape the fake upstream must give (R-C5-4): the list's key, and each item's text key
    "m0_content_key": ("mem0/memory/main.py", "Memory._add_to_vector_store",
                       r'json\.loads\(response, strict=False\)\.get\("(\w+)", \[\]\)'),
    "m0_item_key": ("mem0/memory/main.py", "Memory._add_to_vector_store", r'mem_texts = \[m\.get\("(\w+)", ""\)'),
    # Q-A8-8: the spaCy-backed utilities main.py imports at module level - every one, for the auditor
    "m0_nlp": ("mem0/memory/main.py", r"^from mem0\.utils\.(entity_extraction|lemmatization|spacy_models) import"),
    # B-NLP NLP-3 (the auditor's passive check, 07:3x): the module state the adapter READS after the adds - never a load
    "m0_nlp_full_var": ("mem0/utils/spacy_models.py", r"^(_nlp_full) = None\s*$"),
    "m0_nlp_lemma_var": ("mem0/utils/spacy_models.py", r"^(_nlp_lemma) = None\s*$"),
    "m0_nlp_failed_full_var": ("mem0/utils/spacy_models.py", r"^(_load_failed_full) = False\s*$"),
    "m0_nlp_failed_lemma_var": ("mem0/utils/spacy_models.py", r"^(_load_failed_lemma) = False\s*$"),
    # the model the product itself checks for (and downloads at run time when it is missing) - NLP-2 fetches exactly it
    "m0_nlp_model": ("mem0/utils/spacy_models.py", "_ensure_model_available", r'spacy\.util\.is_package\("([\w.-]+)"\)'),
}
#: C6 (the auditor's Q-C6-1..3): the source facts of mem0's writer bound, declared from 2.0.19 (and its openai SDK)
#: before any read of 2.2.0. Forms: (file, pattern); (file, qualified name, pattern); (file, name) for a module-level
#: string literal's bytes (literal_bytes); the AST ones say so in their comment.
M0_BOUND_SOURCE = {
    # R = the add path's LLM sites x (1 + the SDK's own retries) - the retries from the INSTALLED openai (Q-C6-3)
    "m0_max_retries": ("openai/_constants.py", r"^DEFAULT_MAX_RETRIES = (\d+)\s*$"),
    "m0_client_default": ("openai/_client.py", "OpenAI.__init__", r"^\s+max_retries: int = (DEFAULT_MAX_RETRIES),\s*$"),
    # AST: exactly one OpenAI(...) in DeepSeekLLM.__init__, with no max_retries keyword and no **kwargs
    "m0_client_ctor": ("mem0/llms/deepseek.py", "DeepSeekLLM.__init__", "OpenAI"),
    # and no max_retries or with_options anywhere in the DeepSeek LLM - must be ABSENT
    "m0_client_override": ("mem0/llms/deepseek.py", r"\b(max_retries|with_options)\b"),
    # O = the DeepSeek config's max_tokens default, and the line that sends it
    "m0_max_tokens": ("mem0/configs/llms/deepseek.py", r"^\s+max_tokens: int = (\d+),\s*$"),
    "m0_max_tokens_sent": ("mem0/llms/base.py", r'^\s+params\["max_tokens"\] = self\.config\.max_tokens\s*$'),
    # F: the system prompt (and the agent suffix, counted conservatively), and where the add path takes it
    "m0_system_prompt": ("mem0/configs/prompts.py", "ADDITIVE_EXTRACTION_PROMPT"),
    "m0_agent_suffix": ("mem0/configs/prompts.py", "AGENT_CONTEXT_SUFFIX"),
    "m0_system_prompt_used": ("mem0/memory/main.py", "Memory._add_to_vector_store",
                              r"^\s+system_prompt = (ADDITIVE_EXTRACTION_PROMPT)\s*$"),
    # AST: the user prompt's sections - each append's constant bytes and its fields (prompt_parts), and its separator
    "m0_user_prompt": ("mem0/configs/prompts.py", "generate_additive_extraction_prompt", "sections"),
    # AST: the keywords the add path passes to it
    "m0_prompt_call": ("mem0/memory/main.py", "Memory._add_to_vector_store", "generate_additive_extraction_prompt"),
    # the two windows: the last messages and the existing memories (anchored on the add path's own search call)
    "m0_last_k": ("mem0/memory/main.py", "Memory._add_to_vector_store", r"get_last_messages\(session_scope, limit=(\d+)\)"),
    "m0_top_k": ("mem0/memory/main.py", "Memory._add_to_vector_store",
                 r"existing_results = self\.vector_store\.search\([^)]*?top_k=(\d+)"),
    # the last-k lines are cut to a limit of characters (F-C6-2), in the history formatter
    "m0_trunc_limit": ("mem0/configs/prompts.py", r"^PAST_MESSAGE_TRUNCATION_LIMIT = (\d+)\s*$"),
    "m0_trunc_used": ("mem0/configs/prompts.py", "_format_conversation_history",
                      r'result \+= f"\{role\}: \{_truncate_content\(content\)\}\\n"'),
    # an existing memory's shape in the prompt (F-C6-3: serialized by json.dumps)
    "m0_memory_item": ("mem0/memory/main.py", "Memory._add_to_vector_store",
                       r'existing_memories\.append\(\{"id": str\(idx\), "text": mem\.payload\.get\("data", ""\)\}\)'),
    "m0_memory_dump": ("mem0/configs/prompts.py", "_serialize_memories",
                       r"return json\.dumps\(memories or \[\], ensure_ascii=(False)\)"),
    # the new message's frame in parse_messages: every "<role>: {content}\n" form (a set fact)
    "m0_message_frame": ("mem0/memory/utils.py", "parse_messages", r'f"(\w+): \{content\}\\n"'),
}
#: the fields the user prompt may interpolate - declared before the read; any other is blocked:source-changed
M0_PROMPT_FIELDS = frozenset({"_format_summary(summary)", "_format_conversation_history(last_k_messages)",
                              "_serialize_memories(recently_extracted_memories)", "_serialize_memories(existing_memories)",
                              "_format_new_messages(new_messages)", "observation_date", "current_date",
                              "custom_instructions"})
#: the keywords the add path may pass to the prompt builder (Q-C6-1: custom instructions are the config's, None here)
M0_PROMPT_CALL = frozenset({"existing_memories", "new_messages", "last_k_messages", "custom_instructions"})
#: F-C6-4 (the auditor accepted it, 2026-09-28): the chain from the adapter's message to the two prompt strings, declared
#: from 2.0.19 before any read of 2.2.0 (scratch record c2a_2019.json). What is small is held whole: a helper's shape is
#: the sha256 of its ast.unparse with the docstring dropped - quotes, comments and layout do not count, any other change
#: does. What is long (the add path, add(), the prompt builder) is held by its writes, its defaults and its calls'
#: arguments. ast.unparse's output differs between Python minors: under another minor than M0_SHAPE_PYTHON a shape is
#: blocked:normalizer-changed, never compared.
M0_SHAPE_PYTHON = (3, 14)
M0_HELPER_SHAPES = {
    "m0_fn_summary": ("mem0/configs/prompts.py", "_format_summary",
                      "eca1bb27e66bcf6112f3263398760ce7b84257e93a04356d23bc7f219a9a6b88"),
    "m0_fn_truncate": ("mem0/configs/prompts.py", "_truncate_content",
                       "5a1733903f942c44b858e38a5008b5380abba74334aadd3811ff8cee698d1bf6"),
    "m0_fn_history": ("mem0/configs/prompts.py", "_format_conversation_history",
                      "b13d2cd83d9ab62dead7df6b72ad5adec25d89612594e33ec8b34f0f07964edb"),
    "m0_fn_memories": ("mem0/configs/prompts.py", "_serialize_memories",
                       "d40626a070252d9c84826b6d66e0190bf66320784516ef59eab08a5db8f4d8d4"),
    "m0_fn_new_messages": ("mem0/configs/prompts.py", "_format_new_messages",
                           "59409fdf8f46337d1320e3131fc5707df513129b81d138f061192881d4781d45"),
    "m0_fn_dates": ("mem0/configs/prompts.py", "_resolve_dates",
                    "9b8b45dcccaee8147f13a376f335beeac05c395867f4830808c6a688081fa689"),
    "m0_fn_parse": ("mem0/memory/utils.py", "parse_messages",
                    "0fae0b7c2fe4ac52861739453e12c572b7f2698c8d1a1f813755cd44fad30990"),
    "m0_fn_vision": ("mem0/memory/utils.py", "parse_vision_messages",
                     "145e95c39edaf28f208ff42522fe87f68fcb27b2b8f03dcb4355915602752e33"),
    "m0_fn_generate": ("mem0/llms/deepseek.py", "DeepSeekLLM.generate_response",
                       "c0488b092afc3aa86e05f7be9eba05d3147378ed014e34ab7503bd6bd325ea12")}
_DATES = "current_date, observation_date = _resolve_dates(current_date, timestamp)"
#: the names a scope may write, and every write of them in 2.0.19 (ast.unparse of the statement, its first line; any
#: method called on the name counts as a write) - sorted. One more write, or another one, is blocked:source-changed.
#: A qualified name "class X" is the whole module-level class.
M0_WRITES = {
    "m0_builder_writes": ("mem0/configs/prompts.py", "generate_additive_extraction_prompt",
                          ("summary", "recently_extracted_memories", "existing_memories", "new_messages",
                           "last_k_messages", "current_date", "timestamp", "custom_instructions", "use_input_language",
                           "observation_date"),
                          {"current_date": [_DATES], "observation_date": [_DATES]}),
    "m0_add_path_writes": ("mem0/memory/main.py", "Memory._add_to_vector_store",
                           ("messages", "prompt", "parsed_messages", "last_messages", "existing_results",
                            "existing_memories", "system_prompt", "custom_instr", "user_prompt"),
                           {"last_messages": ["last_messages = self.db.get_last_messages(session_scope, limit=10)"],
                            "parsed_messages": ["parsed_messages = parse_messages(messages)"],
                            "existing_results": ["existing_results = self.vector_store.search(query=parsed_messages, "
                                                 "vectors=query_embedding, top_k=10, filters=search_filters)"],
                            "existing_memories": sorted(["existing_memories = []", "existing_memories.append({'id': "
                                                         "str(idx), 'text': mem.payload.get('data', '')})"]),
                            "system_prompt": sorted(["system_prompt = ADDITIVE_EXTRACTION_PROMPT",
                                                     "system_prompt += AGENT_CONTEXT_SUFFIX"]),
                            "custom_instr": ["custom_instr = prompt or self.custom_instructions"],
                            "user_prompt": ["user_prompt = generate_additive_extraction_prompt(existing_memories="
                                            "existing_memories, new_messages=parsed_messages, last_k_messages="
                                            "last_messages, custom_instructions=custom_instr)"]}),
    "m0_add_writes": ("mem0/memory/main.py", "Memory.add", ("messages", "prompt"),
                      {"messages": sorted(["messages = [{'role': 'user', 'content': messages}]", "messages = [messages]",
                                           "messages = parse_vision_messages(messages)",
                                           "messages = parse_vision_messages(messages, self.llm, "
                                           "self.config.llm.config.get('vision_details'))"])}),
    "m0_custom_writes": ("mem0/memory/main.py", "class Memory", ("self.custom_instructions",),
                         {"self.custom_instructions": ["self.custom_instructions = self.config.custom_instructions"]}),
}
#: the defaults of the parameters nobody passes: the builder's - None names every parameter but the add path's own
#: (M0_PROMPT_CALL) - and add()'s that decide what reaches the prompt (a prompt= replaces the custom instructions, a
#: timestamp is refused, an agent id adds the agent suffix, a memory type takes another path)
M0_DEFAULTS = {
    "m0_prompt_defaults": ("mem0/configs/prompts.py", "generate_additive_extraction_prompt", None,
                           {"summary": "None", "recently_extracted_memories": "None", "current_date": "None",
                            "timestamp": "None", "use_input_language": "False"}),
    "m0_add_defaults": ("mem0/memory/main.py", "Memory.add", ("prompt", "timestamp", "agent_id", "memory_type"),
                        {"prompt": "None", "timestamp": "None", "agent_id": "None", "memory_type": "None"}),
}
#: the one call of each, its positional arguments and its keywords as ast.unparse writes them
M0_CALLS = {
    "m0_prompt_args": ("mem0/memory/main.py", "Memory._add_to_vector_store", "generate_additive_extraction_prompt",
                       {"args": [], "keywords": {"existing_memories": "existing_memories", "new_messages": "parsed_messages",
                                                 "last_k_messages": "last_messages", "custom_instructions": "custom_instr"}}),
    "m0_llm_args": ("mem0/memory/main.py", "Memory._add_to_vector_store", "self.llm.generate_response",
                    {"args": [], "keywords": {"messages": "[{'role': 'system', 'content': system_prompt}, "
                                                          "{'role': 'user', 'content': user_prompt}]",
                                              "response_format": "{'type': 'json_object'}"}}),
    "m0_add_call": ("mem0/memory/main.py", "Memory.add", "self._add_to_vector_store",
                    {"args": ["messages", "processed_metadata", "effective_filters", "infer"],
                     "keywords": {"prompt": "prompt"}}),
}
#: a config field's default: the custom instructions, None unless the adapter sets them (Q-C6-1)
M0_CONFIG_DEFAULTS = {"m0_custom_default": ("mem0/configs/base.py", "MemoryConfig", "custom_instructions", "None")}
#: the adapter's side (Q-C6-1): mem0_config's keys, its DeepSeek llm config's keys, and the keywords every
#: ``self.mem.add(...)`` may pass - custom instructions, vision or a prompt= would put text in the prompt the bound does
#: not count
M0_ADAPTER = {"config_keys": ["embedder", "history_db_path", "llm", "vector_store"],
              "llm_config_keys": ["deepseek_base_url", "model"], "add_keywords": ["infer", "metadata", "user_id"]}
M0_NLP_VARS = ("m0_nlp_full_var", "m0_nlp_lemma_var", "m0_nlp_failed_full_var", "m0_nlp_failed_lemma_var")
#: the adapter's state key -> the source fact naming it
M0_NLP_NAMES = {"full": "m0_nlp_full_var", "lemma": "m0_nlp_lemma_var", "failed_full": "m0_nlp_failed_full_var",
                "failed_lemma": "m0_nlp_failed_lemma_var", "model": "m0_nlp_model"}
#: C5a-2c (the auditor's Q-DRV-5): the probe's writes, declared data fixed before any run - three messages of one dated
#: session, user and assistant, as run_v3_plan.write_ops builds a message unit's ops. Their sha goes into probe.json.
M0_OPS = [
    {"item": {"item_id": "a8m1", "session_id": "a8s1", "speaker": "Alice", "text": "I moved to Lisbon last spring.",
              "role": "user"}, "date": "2026-03-02T10:00:00Z"},
    {"item": {"item_id": "a8m2", "session_id": "a8s1", "speaker": "Bob", "text": "How do you like the city so far?",
              "role": "assistant"}, "date": "2026-03-02T10:00:00Z"},
    {"item": {"item_id": "a8m3", "session_id": "a8s1", "speaker": "Alice", "text": "I work at a bakery near the river.",
              "role": "user"}, "date": "2026-03-02T10:00:00Z"},
]
#: the one text the fake's one answer carries, in the pinned source's own shape (Q-DRV-5)
M0_SCRIPT_TEXT = "Alice lives in Lisbon and works at a bakery"
#: the probe's fields, in their declared order (the verdict takes the first blocked reason in this order)
M0_FIELDS = ("m0_pin", "m0_client", "m0_usage", "m0_calls_per_add", "m0_temperature", "m0_thinking", "m0_format_tools",
             "m0_timestamp", "m0_nlp_active", "m0_nlp")
WITNESS_SCOPE = ("the boundary checks cover the product child's tree only; the probe proxy's own spawn is unwitnessed "
                 "by design (launch.spawn_proxy) - R-C5-9")


class ProbeError(RuntimeError):
    """A probe input this module's rules do not allow - named, never worked around."""


def _field(value: Any, *, rule: str, ok: bool, source: str | None = None, failed: str = "",
           blocked: str | None = None) -> dict:
    out = {"value": value, "source": source, "rule": rule, "ok": bool(ok), "rule_failed": failed}
    if blocked:
        out["blocked"] = blocked
    return out


# ── source facts ─────────────────────────────────────────────────────────────────────────────────────────────

def _source_path(root: Path, rel: str, name: str) -> Path:
    if os.path.isabs(rel) or Path(rel).drive or ".." in Path(rel).parts:
        raise ProbeError(f"{name}: the source path {rel!r} is not a relative path under the root")
    path = Path(root) / rel
    try:
        if os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) != os.path.realpath(root):
            raise ProbeError(f"{name}: the source path {rel!r} resolves outside the root")
    except ValueError:
        raise ProbeError(f"{name}: the source path {rel!r} is on another drive than the root") from None
    return path


def fact(root: Path, rel: str, pattern: str, *, name: str, group: int = 1) -> dict:
    """Exactly one match of ``pattern`` (declared before the read) in ``root``/``rel``, read as data. ``rel`` must be
    a relative path that stays under ``root``."""
    path = _source_path(root, rel, name)
    if not path.is_file():
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{name}"}
    data = path.read_bytes()
    text = data.decode("utf-8", "replace")
    found = list(re.finditer(pattern, text, re.MULTILINE))
    lines = [text.count("\n", 0, m.start()) + 1 for m in found]
    if not found:
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{name}"}
    if len(found) > 1:
        return {"value": None, "file": rel, "lines": lines, "blocked": f"blocked:source-ambiguous:{name}"}
    sha = hashlib.sha256(data).hexdigest()
    return {"value": found[0].group(group), "file": rel, "line": lines[0], "sha256": sha,
            "source": f"{rel}:{lines[0]}@sha256:{sha}"}


def all_in(root: Path, rel: str, pattern: str, *, name: str) -> dict:
    """Every match of ``pattern`` in the file (a set fact - it may be empty), each with its line."""
    path = _source_path(root, rel, name)
    if not path.is_file():
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{name}"}
    data = path.read_bytes()
    text = data.decode("utf-8", "replace")
    sha = hashlib.sha256(data).hexdigest()
    found = [{"value": m.group(1) if m.re.groups else m.group(0), "line": text.count("\n", 0, m.start()) + 1}
             for m in re.finditer(pattern, text, re.MULTILINE)]
    return {"value": found, "file": rel, "sha256": sha, "source": f"{rel}@sha256:{sha}"}


def scope(root: Path, rel: str, qualname: str, *, name: str) -> dict:
    """The source of exactly one function, by its qualified name ("Class.method" or "function"), read as data and
    parsed, never imported: {text, first_line, file, sha256, source}."""
    path = _source_path(root, rel, name)
    if not path.is_file():
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{name}"}
    data = path.read_bytes()
    text = data.decode("utf-8", "replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"value": None, "file": rel, "blocked": f"blocked:source-unparsable:{name}"}
    *outer, fn = qualname.split(".")
    body = tree.body
    for cls in outer:
        classes = [n for n in body if isinstance(n, ast.ClassDef) and n.name == cls]
        if len(classes) != 1:
            return {"value": None, "file": rel, "blocked": f"blocked:source-{'missing' if not classes else 'ambiguous'}:{name}"}
        body = classes[0].body
    defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn]
    if len(defs) != 1:
        return {"value": None, "file": rel, "blocked": f"blocked:source-{'missing' if not defs else 'ambiguous'}:{name}"}
    node = defs[0]
    lines = text.splitlines(keepends=True)
    sha = hashlib.sha256(data).hexdigest()
    return {"value": qualname, "file": rel, "first_line": node.lineno, "text": "".join(lines[node.lineno - 1:node.end_lineno]),
            "sha256": sha, "source": f"{rel}:{node.lineno}@sha256:{sha}"}


def fact_in(sc: Mapping, pattern: str, *, name: str, group: int = 1) -> dict:
    """Exactly one match of ``pattern`` within a function's scope, its line counted in the file."""
    if sc.get("blocked"):
        return {"value": None, "file": sc.get("file"), "blocked": sc["blocked"]}
    found = list(re.finditer(pattern, sc["text"], re.MULTILINE))
    lines = [sc["first_line"] + sc["text"].count("\n", 0, m.start()) for m in found]
    if not found:
        return {"value": None, "file": sc["file"], "blocked": f"blocked:source-missing:{name}"}
    if len(found) > 1:
        return {"value": None, "file": sc["file"], "lines": lines, "blocked": f"blocked:source-ambiguous:{name}"}
    m = found[0]
    return {"value": m.group(group) if m.re.groups >= group else m.group(0), "file": sc["file"], "line": lines[0],
            "sha256": sc["sha256"], "source": f"{sc['file']}:{lines[0]}@sha256:{sc['sha256']}"}


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


_LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def llm_sites(sc: Mapping, dotted: str) -> dict:
    """Every call site of ``dotted`` in the scope, by the AST: the call itself, or a call that hands it on (a pool, a
    thread); each with its line in the file, whether it sits in a loop, and its response_format as written (a literal,
    None when absent, "unparsable" otherwise)."""
    if sc.get("blocked"):
        return {"value": None, "file": sc.get("file"), "blocked": sc["blocked"]}
    tree = ast.parse(textwrap.dedent(sc["text"]))
    out = []

    def walk(node: ast.AST, in_loop: bool) -> None:
        if isinstance(node, ast.Call) and (_dotted(node.func) == dotted or any(_dotted(a) == dotted for a in node.args)):
            kw = next((k.value for k in node.keywords if k.arg == "response_format"), None)
            if kw is None:
                fmt = None
            else:
                try:
                    fmt = ast.literal_eval(kw)
                except ValueError:
                    fmt = "unparsable"
            out.append({"line": sc["first_line"] + node.lineno - 1, "in_loop": in_loop, "response_format": fmt})
        for child in ast.iter_child_nodes(node):
            walk(child, in_loop or isinstance(node, _LOOPS))
    walk(tree, False)
    if not out:
        return {"value": None, "file": sc["file"], "blocked": "blocked:source-missing:m0_llm_sites"}
    return {"value": out, "file": sc["file"], "sha256": sc["sha256"], "source": sc["source"]}


def literal_bytes(root: Path, rel: str, name: str, *, field: str) -> dict:
    """Exactly one module-level ``name = "<a str literal>"`` in the file, read and parsed as data: its UTF-8 bytes."""
    path = _source_path(root, rel, field)
    if not path.is_file():
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{field}"}
    data = path.read_bytes()
    try:
        tree = ast.parse(data.decode("utf-8", "replace"))
    except SyntaxError:
        return {"value": None, "file": rel, "blocked": f"blocked:source-unparsable:{field}"}
    hits = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name]
    if len(hits) != 1:
        return {"value": None, "file": rel, "blocked": f"blocked:source-{'missing' if not hits else 'ambiguous'}:{field}"}
    v = hits[0].value
    if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
        return {"value": None, "file": rel, "line": hits[0].lineno, "blocked": f"blocked:source-changed:{field}"}
    sha = hashlib.sha256(data).hexdigest()
    return {"value": len(v.value.encode("utf-8")), "file": rel, "line": hits[0].lineno, "sha256": sha,
            "source": f"{rel}:{hits[0].lineno}@sha256:{sha}"}


def ctor_calls(sc: Mapping, name: str, *, field: str) -> dict:
    """Every call of the bare name ``name`` in the scope, by the AST: its line, its keyword names, and whether it passes
    **kwargs. The value is the list; none is blocked:source-missing."""
    if sc.get("blocked"):
        return {"value": None, "blocked": sc["blocked"]}
    tree = ast.parse(textwrap.dedent(sc["text"]))
    calls = [{"line": sc["first_line"] + n.lineno - 1, "keywords": sorted(k.arg for k in n.keywords if k.arg),
              "starstar": any(k.arg is None for k in n.keywords)}
             for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]
    if not calls:
        return {"value": None, "blocked": f"blocked:source-missing:{field}"}
    return {"value": calls, "source": sc["source"]}


def call_keywords(sc: Mapping, dotted: str, *, field: str) -> dict:
    """The keyword names of exactly one call of ``dotted`` (a bare or a dotted name) in the scope."""
    if sc.get("blocked"):
        return {"value": None, "blocked": sc["blocked"]}
    tree = ast.parse(textwrap.dedent(sc["text"]))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Name) and n.func.id == dotted) or _dotted(n.func) == dotted)]
    if len(calls) != 1:
        return {"value": None, "blocked": f"blocked:source-{'missing' if not calls else 'ambiguous'}:{field}"}
    kw = calls[0].keywords
    if any(k.arg is None for k in kw) or calls[0].args:
        return {"value": None, "blocked": f"blocked:source-changed:{field}"}
    return {"value": sorted(k.arg for k in kw), "line": sc["first_line"] + calls[0].lineno - 1, "source": sc["source"]}


def prompt_parts(sc: Mapping, *, field: str) -> dict:
    """The user prompt's sections, by the AST: every ``sections.append(<str or f-string>)`` - its constant UTF-8 bytes,
    the expressions it interpolates, whether it sits under an if - and the separator of the one
    ``"<sep>".join(sections)`` it returns. An interpolated field outside M0_PROMPT_FIELDS is blocked:source-changed."""
    if sc.get("blocked"):
        return {"value": None, "blocked": sc["blocked"]}
    tree = ast.parse(textwrap.dedent(sc["text"]))
    parts: list = []

    def is_append(n: ast.AST) -> bool:
        return isinstance(n, ast.Call) and _dotted(n.func) == "sections.append" and len(n.args) == 1

    def is_join(n: ast.AST) -> bool:
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join"
                and isinstance(n.func.value, ast.Constant) and bool(n.args) and isinstance(n.args[0], ast.Name)
                and n.args[0].id == "sections")

    def walk(node: ast.AST, under_if: bool) -> None:
        if is_append(node):
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                parts.append({"const_bytes": len(a.value.encode("utf-8")), "fields": [], "conditional": under_if})
            elif isinstance(a, ast.JoinedStr):
                const = sum(len(v.value.encode("utf-8")) for v in a.values if isinstance(v, ast.Constant))
                fields = [ast.unparse(v.value) for v in a.values if isinstance(v, ast.FormattedValue)]
                parts.append({"const_bytes": const, "fields": fields, "conditional": under_if})
            else:
                parts.append({"const_bytes": None, "fields": [ast.unparse(a)], "conditional": under_if})
        for child in ast.iter_child_nodes(node):
            walk(child, under_if or isinstance(node, ast.If))
    walk(tree, False)
    joins = [n for n in ast.walk(tree) if is_join(n)]
    # C6C1-1 (the auditor): a section added any other way than append would be uncounted. Held as the list of the
    # allowed USES of the name, not of the forbidden writes: ``sections`` may appear only as the target of
    # ``sections = []``, the receiver of an append the walk above counts, and the argument of a join the joins above
    # count - the same predicates, so nothing is allowed that is not counted. Any other use - another method, an
    # augmented or annotated assignment, a non-empty value, an index, an alias, a bound method, an argument that may
    # write it - is blocked:source-changed
    allowed = {id(n.func.value) for n in ast.walk(tree) if is_append(n)} | {id(n.args[0]) for n in joins}
    allowed |= {id(n.targets[0]) for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.List) and not n.value.elts}
    parent = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    other = sorted({f"line {sc['first_line'] + n.lineno - 1}: {ast.unparse(parent[id(n)])[:80]}"
                    for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "sections" and id(n) not in allowed})
    if other:
        return {"value": None, "uses": other, "blocked": f"blocked:source-changed:{field}"}
    if not parts or len(joins) != 1:
        return {"value": None, "blocked": f"blocked:source-{'missing' if not parts or not joins else 'ambiguous'}:{field}"}
    unknown = sorted({f for x in parts for f in x["fields"]} - M0_PROMPT_FIELDS)
    if unknown or any(x["const_bytes"] is None for x in parts):
        return {"value": None, "fields": unknown, "blocked": f"blocked:source-changed:{field}"}
    return {"value": {"parts": parts, "separator": joins[0].func.value.value}, "source": sc["source"]}


def class_scope(root: Path, rel: str, cls: str, *, name: str) -> dict:
    """The source of exactly one module-level class, read as data and parsed, never imported - scope()'s shape."""
    path = _source_path(root, rel, name)
    if not path.is_file():
        return {"value": None, "file": rel, "blocked": f"blocked:source-missing:{name}"}
    data = path.read_bytes()
    text = data.decode("utf-8", "replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"value": None, "file": rel, "blocked": f"blocked:source-unparsable:{name}"}
    hits = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls]
    if len(hits) != 1:
        return {"value": None, "file": rel, "blocked": f"blocked:source-{'missing' if not hits else 'ambiguous'}:{name}"}
    node = hits[0]
    sha = hashlib.sha256(data).hexdigest()
    return {"value": cls, "file": rel, "first_line": node.lineno,
            "text": "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno]), "sha256": sha,
            "source": f"{rel}:{node.lineno}@sha256:{sha}"}


def _scope_tree(sc: Mapping, field: str) -> tuple[ast.AST | None, dict | None]:
    """A scope's parsed text, or the record that says why there is none."""
    if sc.get("blocked"):
        return None, {"value": None, "blocked": sc["blocked"]}
    try:
        return ast.parse(textwrap.dedent(sc["text"])), None
    except SyntaxError:
        return None, {"value": None, "blocked": f"blocked:source-unparsable:{field}"}


def fn_shape(sc: Mapping, declared: str, *, field: str) -> dict:
    """F-C6-4: a helper held whole - the sha256 of its ast.unparse with the docstring dropped, against the declared one.
    Any change but quotes, comments and layout is blocked:source-changed, with the normalized text so it can be read;
    under another Python minor than M0_SHAPE_PYTHON it is blocked:normalizer-changed, never compared."""
    tree, why = _scope_tree(sc, field)
    if why:
        return why
    if tuple(sys.version_info[:2]) != tuple(M0_SHAPE_PYTHON):
        return {"value": None, "python": list(sys.version_info[:2]), "blocked": f"blocked:normalizer-changed:{field}"}
    node = tree.body[0]
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        node.body = body[1:] or [ast.Pass()]
    text = ast.unparse(node)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if sha != declared:
        return {"value": None, "sha256": sha, "text": text, "source": sc["source"], "blocked": f"blocked:source-changed:{field}"}
    return {"value": {"sha256": sha, "chars": len(text)}, "source": sc["source"]}


def writes_in(sc: Mapping, names: Iterable[str], declared: Mapping[str, list], *, field: str) -> dict:
    """F-C6-4: every write of the watched names in the scope - an assignment, an augmented or annotated one, a loop, with
    or walrus target, a del, an index or attribute under the name, or any method called on it - as the first line of its
    ast.unparse, sorted by name. Anything but the declared writes is blocked:source-changed, with the writes read."""
    tree, why = _scope_tree(sc, field)
    if why:
        return why
    watched = set(names)
    out: dict[str, list[str]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and ast.unparse(n.func.value) in watched:
            out.setdefault(ast.unparse(n.func.value), []).append(ast.unparse(n).split("\n")[0])
            continue
        if isinstance(n, ast.Assign):
            tg = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.NamedExpr, ast.comprehension)):
            tg = [n.target]
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            tg = [x.optional_vars for x in n.items if x.optional_vars is not None]
        elif isinstance(n, ast.Delete):
            tg = n.targets
        else:
            continue
        hit = {ast.unparse(x) for t in tg for x in ast.walk(t) if isinstance(x, (ast.Name, ast.Attribute))
               and ast.unparse(x) in watched}
        for h in hit:
            out.setdefault(h, []).append(ast.unparse(n).split("\n")[0])
    value = {k: sorted(v) for k, v in sorted(out.items())}
    if value != {k: sorted(v) for k, v in declared.items()}:
        return {"value": None, "writes": value, "source": sc.get("source"), "blocked": f"blocked:source-changed:{field}"}
    return {"value": value, "source": sc["source"]}


def param_defaults(sc: Mapping, names: Sequence[str] | None, declared: Mapping[str, Any], *, field: str) -> dict:
    """F-C6-4: the defaults of the parameters nobody passes, as ast.unparse writes them (None: no default; "<absent>":
    no such parameter). ``names`` None is every parameter but self and the add path's own keywords (M0_PROMPT_CALL), so
    a new one shows. *args or **kwargs, or anything but the declared defaults, is blocked:source-changed."""
    tree, why = _scope_tree(sc, field)
    if why:
        return why
    a = tree.body[0].args
    if a.vararg is not None or a.kwarg is not None:
        return {"value": None, "star": [x.arg for x in (a.vararg, a.kwarg) if x is not None], "source": sc.get("source"),
                "blocked": f"blocked:source-changed:{field}"}
    pos = a.posonlyargs + a.args
    got: dict[str, Any] = {x.arg: None for x in pos + a.kwonlyargs}
    for x, v in zip(pos[len(pos) - len(a.defaults):], a.defaults):
        got[x.arg] = ast.unparse(v)
    for x, v in zip(a.kwonlyargs, a.kw_defaults):
        got[x.arg] = ast.unparse(v) if v is not None else None
    keep = [k for k in got if k != "self" and k not in M0_PROMPT_CALL] if names is None else list(names)
    value = {k: got.get(k, "<absent>") for k in keep}
    if value != dict(declared):
        return {"value": None, "defaults": value, "source": sc.get("source"), "blocked": f"blocked:source-changed:{field}"}
    return {"value": value, "source": sc["source"]}


def call_args(sc: Mapping, dotted: str, declared: Mapping[str, Any], *, field: str) -> dict:
    """F-C6-4: the one call of ``dotted`` in the scope - its positional arguments and its keywords (** for a double-star
    one), as ast.unparse writes them. None is blocked:source-missing, two are ambiguous, anything but the declared
    arguments is blocked:source-changed."""
    tree, why = _scope_tree(sc, field)
    if why:
        return why
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == dotted]
    if len(calls) != 1:
        return {"value": None, "blocked": f"blocked:source-{'missing' if not calls else 'ambiguous'}:{field}"}
    c = calls[0]
    value = {"args": [ast.unparse(x) for x in c.args],
             "keywords": {(k.arg if k.arg is not None else "**"): ast.unparse(k.value) for k in c.keywords}}
    if value != dict(declared):
        return {"value": None, "call": value, "source": sc.get("source"), "blocked": f"blocked:source-changed:{field}"}
    return {"value": value, "line": sc["first_line"] + c.lineno - 1, "source": sc["source"]}


def config_default(root: Path, rel: str, cls: str, attr: str, declared: str, *, field: str) -> dict:
    """F-C6-4: a config field's default - exactly one ``attr`` in the module-level class, its value or its Field(...)'s
    default= as ast.unparse writes it. A default_factory, no default or another one is blocked:source-changed."""
    sc = class_scope(root, rel, cls, name=field)
    tree, why = _scope_tree(sc, field)
    if why:
        return why
    hits = [n for n in tree.body[0].body if isinstance(n, (ast.AnnAssign, ast.Assign))
            and any(isinstance(t, ast.Name) and t.id == attr for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    if len(hits) != 1:
        return {"value": None, "blocked": f"blocked:source-{'missing' if not hits else 'ambiguous'}:{field}"}
    v = hits[0].value
    if isinstance(v, ast.Call):
        # the declared form's keywords only (2.0.19: description=, default=) - a default_factory, an alias or a
        # positional value beside them could decide the value; any of them is <the call>, never its default=
        kw = [k for k in v.keywords if k.arg == "default"]
        others = {k.arg for k in v.keywords} - {"default", "description"}
        found = ast.unparse(kw[0].value) if len(kw) == 1 and not v.args and not others else f"<{ast.unparse(v)}>"
    else:
        found = ast.unparse(v) if v is not None else "<no value>"
    line = sc["first_line"] + hits[0].lineno - 1
    if found != declared:
        return {"value": None, "found": found, "line": line, "source": sc["source"], "blocked": f"blocked:source-changed:{field}"}
    return {"value": found, "line": line, "source": sc["source"]}


def adapter_facts(path: Path, *, field: str = "m0_adapter") -> dict:
    """Q-C6-1 / F-C6-4: the adapter's side of the bound, from its source by the AST - mem0_config's returned keys, its
    DeepSeek llm config's keys and every ``self.mem.add(...)`` call's keywords (source order). Anything but M0_ADAPTER's
    keys, or a keyword outside its list (** included), is blocked:source-changed."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return {"value": None, "blocked": f"blocked:source-missing:{field}"}
    try:
        tree = ast.parse(data.decode("utf-8", "replace"))
    except SyntaxError:
        return {"value": None, "blocked": f"blocked:source-unparsable:{field}"}
    sha = hashlib.sha256(data).hexdigest()
    source = f"{path.name}@sha256:{sha}"
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "mem0_config"]
    if len(fns) != 1:
        return {"value": None, "source": source, "blocked": f"blocked:source-{'missing' if not fns else 'ambiguous'}:{field}"}

    def keys(d: ast.Dict) -> list:
        return sorted(k.value if isinstance(k, ast.Constant) else f"<{ast.unparse(k) if k else '**'}>" for k in d.keys)

    rets = [n.value for n in ast.walk(fns[0]) if isinstance(n, ast.Return)]
    llm = [d for d in ast.walk(fns[0]) if isinstance(d, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == "provider" and isinstance(v, ast.Constant) and v.value == "deepseek"
        for k, v in zip(d.keys, d.values))]
    cfg = [v for d in llm for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant) and k.value == "config"]
    adds = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.mem.add"),
                  key=lambda n: (n.lineno, n.col_offset))
    found = {"config_keys": keys(rets[0]) if len(rets) == 1 and isinstance(rets[0], ast.Dict) else None,
             "llm_config_keys": keys(cfg[0]) if len(cfg) == 1 and isinstance(cfg[0], ast.Dict) else None,
             "add_keywords": [sorted(k.arg if k.arg is not None else "**" for k in c.keywords) for c in adds]}
    ok = (found["config_keys"] == M0_ADAPTER["config_keys"] and found["llm_config_keys"] == M0_ADAPTER["llm_config_keys"]
          and bool(adds) and all(set(kw) <= set(M0_ADAPTER["add_keywords"]) for kw in found["add_keywords"]))
    if not ok:
        return {"value": None, "found": found, "source": source, "blocked": f"blocked:source-changed:{field}"}
    return {"value": found, "source": source}


def mem0_bound_facts(site: Path, *, adapter: Path | None = None) -> dict:
    """C6: every declared M0_BOUND_SOURCE fact, and F-C6-4's - the helper shapes, the writes, the defaults, the calls'
    arguments, the config default - read from the installed mem0 and openai SDK as data; and the adapter's side
    (``adapter``: arms/arm_mem0.py beside this file)."""
    B = M0_BOUND_SOURCE
    out: dict = {}
    for k in ("m0_max_retries", "m0_max_tokens", "m0_max_tokens_sent", "m0_trunc_limit"):
        out[k] = fact(site, B[k][0], B[k][1], name=k, group=1 if k != "m0_max_tokens_sent" else 0)
    for k in ("m0_client_default", "m0_system_prompt_used", "m0_last_k", "m0_top_k", "m0_trunc_used", "m0_memory_item",
              "m0_memory_dump"):
        rel, qual, pattern = B[k]
        out[k] = fact_in(scope(site, rel, qual, name=k), pattern, name=k, group=1 if k not in ("m0_trunc_used",
                                                                                              "m0_memory_item") else 0)
    for k in ("m0_system_prompt", "m0_agent_suffix"):
        out[k] = literal_bytes(site, B[k][0], B[k][1], field=k)
    rel, qual, name = B["m0_client_ctor"]
    out["m0_client_ctor"] = ctor_calls(scope(site, rel, qual, name="m0_client_ctor"), name, field="m0_client_ctor")
    rel, pattern = B["m0_client_override"]
    out["m0_client_override"] = all_in(site, rel, pattern, name="m0_client_override")
    rel, qual, _ = B["m0_user_prompt"]
    out["m0_user_prompt"] = prompt_parts(scope(site, rel, qual, name="m0_user_prompt"), field="m0_user_prompt")
    rel, qual, dotted = B["m0_prompt_call"]
    out["m0_prompt_call"] = call_keywords(scope(site, rel, qual, name="m0_prompt_call"), dotted, field="m0_prompt_call")
    rel, qual, pattern = B["m0_message_frame"]
    sc = scope(site, rel, qual, name="m0_message_frame")
    out["m0_message_frame"] = ({"value": None, "blocked": sc["blocked"]} if sc.get("blocked") else
                               {"value": sorted(set(re.findall(pattern, sc["text"]))), "source": sc["source"]})
    for k, (rel, qual, sha) in M0_HELPER_SHAPES.items():
        out[k] = fn_shape(scope(site, rel, qual, name=k), sha, field=k)
    for k, (rel, qual, names, declared) in M0_WRITES.items():
        sc = (class_scope(site, rel, qual[len("class "):], name=k) if qual.startswith("class ")
              else scope(site, rel, qual, name=k))
        out[k] = writes_in(sc, names, declared, field=k)
    for k, (rel, qual, names, declared) in M0_DEFAULTS.items():
        out[k] = param_defaults(scope(site, rel, qual, name=k), names, declared, field=k)
    for k, (rel, qual, dotted, declared) in M0_CALLS.items():
        out[k] = call_args(scope(site, rel, qual, name=k), dotted, declared, field=k)
    for k, (rel, cls, attr, declared) in M0_CONFIG_DEFAULTS.items():
        out[k] = config_default(site, rel, cls, attr, declared, field=k)
    out["m0_adapter"] = adapter_facts(Path(adapter) if adapter is not None else HERE / "arms" / "arm_mem0.py")
    return out


def bound_blocked(bf: Mapping) -> list[str]:
    """The reasons mem0's bound facts do not make a bound: a blocked fact, a client that overrides the retries, a prompt
    call with keywords other than M0_PROMPT_CALL, a frame with no role - each by name (C6)."""
    out = [f"{k}: {v['blocked']}" for k, v in bf.items() if isinstance(v, Mapping) and v.get("blocked")]
    ctor = (bf.get("m0_client_ctor") or {}).get("value")
    if ctor is not None and (len(ctor) != 1 or "max_retries" in ctor[0]["keywords"] or ctor[0]["starstar"]):
        out.append(f"m0_client_ctor: the DeepSeek LLM's client is not built once with the SDK's own retries: {ctor}")
    ov = (bf.get("m0_client_override") or {}).get("value")
    if ov:
        out.append(f"m0_client_override: the DeepSeek LLM names {sorted({x['value'] for x in ov})} - the retries may be "
                   "overridden (Q-C6-3)")
    kw = (bf.get("m0_prompt_call") or {}).get("value")
    if kw is not None and set(kw) != M0_PROMPT_CALL:
        out.append(f"m0_prompt_call: the add path passes {kw}, not {sorted(M0_PROMPT_CALL)}")
    fr = (bf.get("m0_message_frame") or {}).get("value")
    if fr is not None and not fr:
        out.append("m0_message_frame: parse_messages frames no role")
    return out


# ── C5b (the auditor's Q-C5b-1 = O-a): letta's OpenAPI-generic facts, offline - no container, no child, no source ──

#: Q-47-8b: the recall (conversation) search route - one path under an agent's messages, recall memory or conversations
#: that ends in /search - declared before any real document is read (Q-C5b-1). None: the reader is core + archival, a
#: declared deviation (an E5 row); two or more: blocked:ambiguous-route.
LT_RECALL_ROUTE = r"^/v1/agents/(\{agent_id\}/)?(messages|recall[-_]memory|conversations?)/search/?$"
#: the names the route's search mode goes by - a query parameter or a JSON body property; its schema default is the mode
LT_RECALL_MODE = ("search_mode", "mode")
#: Point V's page size: the archival listing (arm_letta's list_passages), its method and its page-size query parameter
LT_ARCHIVAL = ("/v1/agents/{agent_id}/archival-memory", "get", "limit")
_OA_METHODS = ("get", "put", "post", "delete", "patch")


def _rest_module() -> Any:
    return _mod("v3_rest_for_a8", "arms/_rest.py")


def _oa_deref(obj: Any, doc: Mapping) -> Any:
    """A local $ref followed to its target (a chain of them, at most 32)."""
    for _ in range(32):
        if not (isinstance(obj, Mapping) and isinstance(obj.get("$ref"), str) and obj["$ref"].startswith("#/")):
            return obj
        node: Any = doc
        for part in obj["$ref"][2:].split("/"):
            node = node.get(part) if isinstance(node, Mapping) else None
        obj = node
    return None


def _oa_source(doc: Mapping) -> str:
    return f"openapi.json@sha256:{_canon_sha(doc)}"


def lt_pin(raw: bytes) -> dict:
    """R-C5-1: the OpenAPI document's pin - _rest.document_sha256, the sha256 over canonical JSON that the adapter checks
    at every start - with the raw bytes' sha256 and the path count beside it."""
    rule = "the document's sha256 over canonical JSON (R-C5-1)"
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return _field(None, rule=rule, ok=False, failed="the document is not a JSON object",
                      blocked="blocked:unparsable-openapi")
    return _field({"sha256": _rest_module().document_sha256(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                   "paths": len(doc.get("paths") or {})}, rule=rule, ok=True)


def lt_stable(raws: Sequence[bytes]) -> dict:
    """R-C5-1, L1: the documents of two or more starts - one canonical sha is stable; otherwise the paths whose items
    differ, blocked:unstable-openapi. One start compares nothing: blocked:one-start."""
    rule = "one canonical sha256 over every start's document (R-C5-1)"
    if len(raws) < 2:
        return _field(None, rule=rule, ok=False, failed="one start is not a comparison", blocked="blocked:one-start")
    pins = [lt_pin(r) for r in raws]
    bad = [p for p in pins if p.get("blocked")]
    if bad:
        return _field(None, rule=rule, ok=False, failed=bad[0]["rule_failed"], blocked=bad[0]["blocked"])
    shas = sorted({p["value"]["sha256"] for p in pins})
    if len(shas) == 1:
        return _field(shas[0], rule=rule, ok=True)
    docs = [json.loads(r) for r in raws]
    names = sorted(set().union(*(set(d.get("paths") or {}) for d in docs)))
    paths = [n for n in names if len({_canon_sha((d.get("paths") or {}).get(n)) for d in docs}) > 1]
    if not paths:
        paths = ["(outside paths)"]
    out = _field(None, rule=rule, ok=False, failed=f"{len(shas)} documents; they differ at {paths}",
                 blocked="blocked:unstable-openapi")
    out["paths"] = paths
    return out


def letta_calls(path: str | os.PathLike | None = None) -> dict:
    """C5b: the letta adapter's declared calls (arms/arm_letta.py's CALLS), read from its source by the AST as data and
    built as _rest.Call - the adapter is never imported here. An entry that is not C(<literals>) is
    blocked:source-changed:lt_calls, the entries named, never evaluated."""
    R = _rest_module()
    p = Path(path) if path is not None else HERE / "arms" / "arm_letta.py"
    try:
        tree = ast.parse(p.read_bytes().decode("utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {"value": None, "blocked": "blocked:source-missing:lt_calls"}
    hits = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "CALLS" and isinstance(n.value, ast.Dict)]
    if len(hits) != 1:
        return {"value": None, "blocked": f"blocked:source-{'missing' if not hits else 'ambiguous'}:lt_calls"}
    out: dict = {}
    bad: list[str] = []
    for k, v in zip(hits[0].value.keys, hits[0].value.values):
        name = k.value if isinstance(k, ast.Constant) and isinstance(k.value, str) else ast.unparse(k) if k else "**"
        try:
            if not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "C"
                    and all(kw.arg is not None for kw in v.keywords)):
                raise ValueError(name)
            out[name] = R.Call(*[ast.literal_eval(a) for a in v.args],
                               **{kw.arg: ast.literal_eval(kw.value) for kw in v.keywords})
        except (ValueError, TypeError, SyntaxError):
            bad.append(name)
    if bad or not out:
        return {"value": None, "entries": bad, "blocked": "blocked:source-changed:lt_calls"}
    return out


def lt_conformance(doc: Mapping, calls: Mapping) -> dict:
    """Q-47-2: the adapter's declared calls against the document (_rest.conformance) - every problem is the value, and
    any is blocked:openapi-mismatch."""
    rule = "every declared call is carried by the document as declared (_rest.conformance, Q-47-2)"
    if calls.get("blocked"):
        return _field(None, rule=rule, ok=False, failed=calls["blocked"], blocked=calls["blocked"])
    problems = _rest_module().conformance(calls, doc)
    if problems:
        return _field(problems, rule=rule, ok=False, source=_oa_source(doc), failed="; ".join(problems),
                      blocked="blocked:openapi-mismatch")
    return _field([], rule=rule, ok=True, source=_oa_source(doc))


def lt_recall_route(doc: Mapping) -> dict:
    """Q-47-8b: the recall search route under LT_RECALL_ROUTE - its path, its methods and its default mode (a query
    parameter's or a JSON body property's schema default, named in LT_RECALL_MODE; None without one). No route is a
    declared deviation, never a failure; two or more are blocked:ambiguous-route, all named."""
    rule = "exactly one path under the declared recall-route pattern, or none (a declared deviation)"
    paths = sorted(p for p in (doc.get("paths") or {}) if re.fullmatch(LT_RECALL_ROUTE, p))
    if not paths:
        out = _field(None, rule=rule, ok=True, source=_oa_source(doc))
        out["deviation"] = ("no recall search route in the document - the reader is core + archival (Q-47-8b), a "
                            "declared deviation that may understate Letta (an E5 row)")
        return out
    if len(paths) > 1:
        out = _field(None, rule=rule, ok=False, source=_oa_source(doc), failed=f"{len(paths)} routes: {paths}",
                     blocked="blocked:ambiguous-route")
        out["paths"] = paths
        return out
    item = doc["paths"][paths[0]]
    methods = sorted(m for m in item if m in _OA_METHODS)
    mode = None
    for m in methods:
        op = item[m]
        params = [_oa_deref(p, doc) for p in list(item.get("parameters") or []) + list(op.get("parameters") or [])]
        props: dict = {}
        body = _oa_deref(op.get("requestBody"), doc) if op.get("requestBody") else None
        if isinstance(body, Mapping):
            schema = _oa_deref(((body.get("content") or {}).get("application/json") or {}).get("schema"), doc)
            props = dict((schema or {}).get("properties") or {}) if isinstance(schema, Mapping) else {}
        cands = [_oa_deref(p.get("schema"), doc) for p in params if isinstance(p, Mapping) and p.get("in") == "query"
                 and p.get("name") in LT_RECALL_MODE] + [_oa_deref(props[n], doc) for n in LT_RECALL_MODE if n in props]
        found = [c["default"] for c in cands if isinstance(c, Mapping) and "default" in c]
        if found:
            mode = found[0]
            break
    return _field({"path": paths[0], "methods": methods, "mode": mode}, rule=rule, ok=True, source=_oa_source(doc))


def lt_archival_default(doc: Mapping) -> dict:
    """Point V's page size: the archival listing's page-size query parameter's schema default (through $ref) - a
    positive int, else blocked:source-changed. None in the schema leaves Point V refusing: its source basis is deferred
    (Q-C5b-1). No listing, or no such parameter, is blocked:source-missing."""
    path, method, name = LT_ARCHIVAL
    rule = "the archival listing's page-size default, from the schema (Point V)"
    field = "lt_archival_default"
    item = (doc.get("paths") or {}).get(path) or {}
    op = item.get(method)
    params = [_oa_deref(p, doc) for p in list(item.get("parameters") or []) + list((op or {}).get("parameters") or [])]
    hit = [p for p in params if isinstance(p, Mapping) and p.get("in") == "query" and p.get("name") == name]
    if op is None or len(hit) != 1:
        return _field(None, rule=rule, ok=False, source=_oa_source(doc), failed=f"{method.upper()} {path} ?{name}",
                      blocked=f"blocked:source-{'ambiguous' if len(hit) > 1 else 'missing'}:{field}")
    schema = _oa_deref(hit[0].get("schema"), doc) or {}
    if "default" not in schema:
        out = _field(None, rule=rule, ok=True, source=_oa_source(doc))
        out["deviation"] = ("no page-size default in the schema - Point V keeps refusing (its source basis is deferred, "
                            "Q-C5b-1)")
        return out
    d = schema["default"]
    if not isinstance(d, int) or isinstance(d, bool) or d < 1:
        return _field(None, rule=rule, ok=False, source=_oa_source(doc), failed=f"the default is {d!r}",
                      blocked=f"blocked:source-changed:{field}")
    return _field(d, rule=rule, ok=True, source=_oa_source(doc))


def formats(sites: Mapping) -> dict:
    """C5A-10 (the auditor, declared before any line): one distinct response_format across the sites is the single
    format; several are a set, each line's format must be one of them, with the sites listed."""
    if sites.get("blocked"):
        return {"value": None, "blocked": sites["blocked"]}
    fmts = [x["response_format"] for x in sites["value"]]
    if "unparsable" in fmts:
        return {"value": None, "source": sites.get("source"), "blocked": "blocked:source-unparsable:m0_format_tools"}
    kinds = sorted({f.get("type") if isinstance(f, dict) else f for f in fmts}, key=str)
    at = [x["line"] for x in sites["value"]]
    if len(kinds) == 1:
        return {"value": kinds[0], "source": sites.get("source"), "sites": at}
    return {"value": None, "formats": kinds, "source": sites.get("source"), "sites": at}


def thinking_fact(root: Path) -> dict | None:
    """The pinned DeepSeek LLM's thinking field: None when it mentions none (the route is none); a mention is
    blocked:source-changed:m0_thinking - its meaning goes to the auditor, never guessed; no file is blocked too."""
    rel, pattern = M0_SOURCE["m0_thinking"]
    found = all_in(root, rel, pattern, name="m0_thinking")
    if found.get("blocked"):
        return found
    if not found["value"]:
        return None
    return {"value": None, "file": rel, "lines": [x["line"] for x in found["value"]], "source": found["source"],
            "blocked": "blocked:source-changed:m0_thinking"}


# ── the proxy's lines ────────────────────────────────────────────────────────────────────────────────────────

def mine(calls: Iterable[Mapping], *, arm: str, run: str, unit: str) -> list[dict]:
    """The proxy's call lines of ``arm`` and the unit "<run>.<unit>" on the v1 endpoint."""
    key = f"{run}.{unit}"
    return [dict(c) for c in calls if c.get("arm") == arm and c.get("unit") == key and c.get("endpoint") == "v1"]


def _answered(lines: Sequence[Mapping]) -> list[Mapping]:
    return [c for c in lines if c.get("status") == 200 and c.get("complete") and not c.get("refused")]


# ── mem0's fields ────────────────────────────────────────────────────────────────────────────────────────────

def m0_pin(install_record: Mapping | None) -> dict:
    """The a8-pypi-mem0_v3 install record: no problem, and the venv's import answer says mem0ai 2.2.0."""
    rule = f"the install record has no problem and {M0_PIN[0]} is {M0_PIN[1]}"
    if not install_record or install_record.get("problems"):
        why = "no install record" if not install_record else f"install problems: {install_record['problems'][:2]}"
        return _field(None, rule=rule, ok=False, failed=why, blocked="blocked:not-installed")
    got = ((install_record.get("import_versions") or {}).get("dists") or {}).get(M0_PIN[0])
    if got != M0_PIN[1]:
        return _field(got, rule=rule, ok=False, failed=f"{M0_PIN[0]} is {got}", blocked="blocked:not-the-pin")
    return _field(got, rule=rule, ok=True)


def m0_client(start: Mapping | None) -> dict:
    """The adapter's start answer: mem0's own OpenAI client wrapped (Q-AB-1), else blocked:no-llm-client."""
    rule = f"the adapter's start answer names {M0_USAGE_SOURCE!r}"
    start = start or {}
    if not start.get("ok"):
        err = str(start.get("error") or "")
        if M0_NO_CLIENT in err:
            return _field(None, rule=rule, ok=False, failed=err[:200], blocked="blocked:no-llm-client")
        return _field(None, rule=rule, ok=False, failed=f"the start failed: {err[:200]}")
    got = start.get("llm_usage")
    return _field(got, rule=rule, ok=got == M0_USAGE_SOURCE, failed="" if got == M0_USAGE_SOURCE else f"llm_usage is {got!r}")


def m0_usage(snapshot: Mapping | None, calls: Iterable[Mapping], *, run: str, unit: str) -> dict:
    """The adapter's LLMUsage snapshot against the proxy's answered lines of (mem0, <run>.<unit>)."""
    rule = ("the adapter's calls, prompt and completion tokens equal the proxy's answered v1 lines of the unit; "
            "failed 0, no_usage 0; at least one call")
    lines = mine(calls, arm="mem0", run=run, unit=unit)
    ans = _answered(lines)
    proxy = {"calls": len(ans), "prompt_tokens": sum(int((c.get("usage") or {}).get("prompt") or 0) for c in ans),
             "completion_tokens": sum(int((c.get("usage") or {}).get("completion") or 0) for c in ans),
             "unanswered": len(lines) - len(ans)}     # the SDK's own retries (a 500, then the same create's 200)
    if snapshot is None:
        return _field({"adapter": None, "proxy": proxy}, rule=rule, ok=False, source="proxy",
                      failed="no llm_usage snapshot from the adapter")
    bad = [k for k in ("calls", "prompt_tokens", "completion_tokens") if snapshot.get(k) != proxy[k]]
    bad += [k for k in ("failed", "no_usage") if snapshot.get(k) != 0]
    if not bad and proxy["calls"] == 0:
        bad = ["unmeasured: no call on either side"]
    return _field({"adapter": dict(snapshot), "proxy": proxy}, rule=rule, ok=not bad, source="proxy",
                  failed="; ".join(bad))


def _source_value(src: Mapping, name: str, rule: str) -> tuple[Any, dict | None]:
    if src.get("blocked"):
        return None, _field(None, rule=rule, ok=False, source=src.get("source"), failed=src["blocked"],
                            blocked=src["blocked"])
    return src.get("value"), None


def m0_temperature(src: Mapping, calls: Iterable[Mapping], *, run: str, unit: str) -> dict:
    """§5.5: the temperature mem0's pinned source sends; every answered line must carry exactly it. The recorded
    value is always the source's."""
    rule = "every v1 line of the unit carries the pinned source's temperature"
    raw, blocked = _source_value(src, "m0_temperature", rule)
    if blocked:
        return blocked
    try:
        value = float(raw)
    except (TypeError, ValueError):                  # C5A-9: a source value that is not a number is named, never raised
        return _field(None, rule=rule, ok=False, source=src.get("source"), failed=f"the source's value {raw!r}",
                      blocked="blocked:source-unparsable:m0_temperature")
    lines = mine(calls, arm="mem0", run=run, unit=unit)
    if not lines:
        return _field(value, rule=rule, ok=False, source=src.get("source"), failed="unmeasured: no line")
    other = sorted({repr(c.get("temperature")) for c in lines if c.get("temperature") != value})
    return _field(value, rule=rule, ok=not other, source=src.get("source"),
                  failed="" if not other else f"lines sent {', '.join(other)}, the source {value}")


def m0_thinking(src: Mapping | None, calls: Iterable[Mapping], *, run: str, unit: str) -> dict:
    """§2.2.1: the thinking field mem0's pinned source sets (None: it sets none - the route is "none"); every line
    must send exactly that."""
    rule = "every v1 line of the unit sends the thinking field the pinned source sets (none: none)"
    want = None
    source = None
    if src is not None:
        want, blocked = _source_value(src, "m0_thinking", rule)
        if blocked:
            return blocked
        source = src.get("source")
    lines = mine(calls, arm="mem0", run=run, unit=unit)
    if not lines:
        return _field(None, rule=rule, ok=False, source=source, failed="unmeasured: no line")
    other = sorted({str(c.get("thinking_sent")) for c in lines if c.get("thinking_sent") != want})
    return _field("none" if want is None else want, rule=rule, ok=not other, source=source,
                  failed="" if not other else f"lines sent thinking {', '.join(other)}")


def m0_format_tools(src: Mapping, calls: Iterable[Mapping], *, run: str, unit: str) -> dict:
    """Every line asks the response format the pinned source sets (C5A-10: one of the sites' formats when they
    differ) and offers no tool."""
    rule = "every v1 line of the unit asks the pinned source's response_format (one of its sites') and offers no tool"
    want, blocked = _source_value(src, "m0_format_tools", rule)
    if blocked:
        return blocked
    allowed = list(src["formats"]) if "formats" in src else [want]
    want = want if "formats" not in src else list(src["formats"])
    lines = mine(calls, arm="mem0", run=run, unit=unit)
    if not lines:
        return _field(want, rule=rule, ok=False, source=src.get("source"), failed="unmeasured: no line")
    bad = sorted({f"response_format {c.get('response_format')!r}" for c in lines if c.get("response_format") not in allowed})
    bad += sorted({f"tools_offered {t}" for c in lines for t in (c.get("tools_offered") or [])})
    return _field(want, rule=rule, ok=not bad, source=src.get("source"), failed="; ".join(bad))


def m0_calls_per_add(sites: Mapping, calls: Iterable[Mapping], *, run: str, unit: str, adds: int) -> dict:
    """The answered lines of the unit are at most the add path's LLM call sites x the adds (C6's requests_per_op); a
    site in a loop has no static bound - blocked:source-unbounded, never a guess."""
    rule = "answered v1 lines of the unit <= the add path's LLM call sites x the adds; at least one"
    if sites.get("blocked"):
        return _field(None, rule=rule, ok=False, source=sites.get("source"), failed=sites["blocked"], blocked=sites["blocked"])
    if any(x["in_loop"] for x in sites["value"]):
        why = "blocked:source-unbounded:m0_calls_per_add"
        return _field(None, rule=rule, ok=False, source=sites.get("source"), failed=why, blocked=why)
    bound = len(sites["value"])
    answered = len(_answered(mine(calls, arm="mem0", run=run, unit=unit)))
    value = {"bound_per_add": bound, "adds": adds, "answered": answered}
    if not answered:
        return _field(value, rule=rule, ok=False, source=sites.get("source"), failed="unmeasured: no answered line")
    over = answered > bound * adds
    return _field(value, rule=rule, ok=not over, source=sites.get("source"),
                  failed=f"{answered} answered lines over the bound {bound} x {adds} adds" if over else "")


def nlp_facts(root: Path) -> dict:
    """NLP-3's source facts: the four module-level state names of mem0.utils.spacy_models and the model the product
    checks for, each exactly one match."""
    out = {k: fact(root, M0_SOURCE[k][0], M0_SOURCE[k][1], name=k) for k in M0_NLP_VARS}
    rel, qual, pattern = M0_SOURCE["m0_nlp_model"]
    out["m0_nlp_model"] = fact_in(scope(root, rel, qual, name="m0_nlp_model"), pattern, name="m0_nlp_model")
    return out


def m0_nlp_active(state: Mapping | None, facts: Mapping[str, Mapping], *, catcher_lines: int) -> dict:
    """B-NLP NLP-3 (the auditor's PASSIVE check): what the adapter read of spaCy's state after the adds, never loading
    anything - the model installed (metadata only), both models loaded, neither failed flag set, by exactly the names
    the pinned source defines; and no catcher line (no run-time download). Anything else is blocked:nlp-off."""
    rule = ("after the adds: the model installed, both models loaded, no failed flag, read by the pinned source's names; "
            "no catcher line")
    for k in (*M0_NLP_VARS, "m0_nlp_model"):
        if (facts.get(k) or {}).get("blocked"):
            b = facts[k]["blocked"]
            return _field(None, rule=rule, ok=False, failed=b, blocked=b)
    if not state:
        return _field(None, rule=rule, ok=False, failed="no nlp state from the adapter", blocked="blocked:nlp-off")
    model = facts["m0_nlp_model"]["value"]
    bad = []
    names = state.get("names") or {}
    for key, fk in M0_NLP_NAMES.items():
        if names.get(key) != facts[fk]["value"]:
            bad.append(f"the adapter read {names.get(key)!r} for {key}, the source defines {facts[fk]['value']!r}")
    if state.get("module") is not True:
        bad.append("module: mem0.utils.spacy_models was never imported")
    if state.get("is_package") is not True:
        bad.append(f"is_package: {model} is not installed ({state.get('is_package')!r})")
    for f in ("failed_full", "failed_lemma"):
        if state.get(f) is not False:
            bad.append(f"{f} is {state.get(f)!r}")
    for f in ("nlp_full", "nlp_lemma"):
        if state.get(f) is not True:
            bad.append(f"{f}: no model loaded after the adds")
    if catcher_lines:
        bad.append(f"{catcher_lines} catcher line(s) of the unit - a run-time download")
    value = {"model": model, "nlp_full": state.get("nlp_full"), "nlp_lemma": state.get("nlp_lemma")}
    return _field(value, rule=rule, ok=not bad, failed="; ".join(bad), blocked="blocked:nlp-off" if bad else None)


def mem0_source_facts(site: Path) -> dict:
    """Every declared M0_SOURCE fact, read from the installed mem0's site-packages as data (C5a-2c)."""
    out: dict = {}
    rel, pattern = M0_SOURCE["m0_temperature"]
    out["m0_temperature"] = fact(site, rel, pattern, name="m0_temperature")
    out["m0_thinking"] = thinking_fact(site)
    for k in ("m0_timestamp", "m0_content_key", "m0_item_key"):
        rel, qual, pattern = M0_SOURCE[k]
        out[k] = fact_in(scope(site, rel, qual, name=k), pattern, name=k)
    rel, qual, dotted = M0_SOURCE["m0_llm_sites"]
    out["m0_llm_sites"] = llm_sites(scope(site, rel, qual, name="m0_llm_sites"), dotted)
    out["m0_format"] = formats(out["m0_llm_sites"])
    rel, pattern = M0_SOURCE["m0_nlp"]
    out["m0_nlp"] = all_in(site, rel, pattern, name="m0_nlp")
    out["nlp"] = nlp_facts(site)
    return out


def mem0_script(facts: Mapping) -> list[dict] | dict:
    """Q-DRV-5: the fake DeepSeek's ONE answer, in the pinned source's own shape {content_key: [{item_key: text}]} - every
    call, the scheduler's and the gate's probes included, gets a valid mem0 answer; a blocked shape fact is blocked."""
    for k in ("m0_content_key", "m0_item_key"):
        if (facts.get(k) or {}).get("blocked"):
            return {"blocked": facts[k]["blocked"]}
    body = {facts["m0_content_key"]["value"]: [{facts["m0_item_key"]["value"]: M0_SCRIPT_TEXT}]}
    return [{"content": json.dumps(body)}]


def mem0_fields(*, start: Mapping | None, counters: Mapping, calls: Iterable[Mapping], catcher_lines: int,
                facts: Mapping, install_record: Mapping | None, run: str, unit: str, adds: int) -> dict:
    """The mem0 probe's fields in M0_FIELDS order, each from its own inputs (C5a-2c)."""
    calls = list(calls)
    nlp = facts.get("m0_nlp") or {}
    nlp_info = (_field(nlp.get("value"), rule="information (Q-A8-8): the spaCy-backed utilities main.py imports", ok=True,
                       source=nlp.get("source")) if not nlp.get("blocked") else
                _field(None, rule="information (Q-A8-8)", ok=False, failed=nlp["blocked"], blocked=nlp["blocked"]))
    out = {
        "m0_pin": m0_pin(install_record),
        "m0_client": m0_client(start),
        "m0_usage": m0_usage(counters.get("llm_usage"), calls, run=run, unit=unit),
        "m0_calls_per_add": m0_calls_per_add(facts["m0_llm_sites"], calls, run=run, unit=unit, adds=adds),
        "m0_temperature": m0_temperature(facts["m0_temperature"], calls, run=run, unit=unit),
        "m0_thinking": m0_thinking(facts["m0_thinking"], calls, run=run, unit=unit),
        "m0_format_tools": m0_format_tools(facts["m0_format"], calls, run=run, unit=unit),
        "m0_timestamp": m0_timestamp(facts["m0_timestamp"]),
        "m0_nlp_active": m0_nlp_active(counters.get("nlp"), facts["nlp"], catcher_lines=catcher_lines),
        "m0_nlp": nlp_info,
    }
    return {k: out[k] for k in M0_FIELDS}


# ── C5a-2c: the driver (the auditor's Q-DRV-1..5) ──────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
PROBE_UNIT, PROBE_BLOCK, PROBE_STAND = "u1", "b01", "_a8"
TEST_UPSTREAM_NOTE = ("the probe's proxy ran in test-upstream mode: its DeepSeek upstream and its Ollama leg's upstream "
                      "are the loopback fakes (probe_upstream) on 127.0.0.1, plain text, with a sentinel key file "
                      "outside the secrets root - that changes only the upstream socket and its binding; every arm "
                      "port, token, recording and refusal is the campaign's own (Q-DRV-4)")


def _canon_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _json_file(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return None


def embed_pin(runs_root: Path, run: str = "d1") -> dict:
    """Q-DRV-5: the campaign's own embed tag and digest, read from the D1 tag record's JSON
    (<runs>/_d1tag/<run>/record.json, written by d1_tag.py) - a missing, unreadable or failed record, or one with no tag,
    is blocked:no-embed-pin."""
    path = Path(runs_root) / "_d1tag" / run / "record.json"
    rec = _json_file(path)
    tag = (rec or {}).get("tag") or {}
    if rec is None or rec.get("problems") or not tag.get("name") or not tag.get("digest"):
        return {"blocked": "blocked:no-embed-pin", "path": str(path)}
    return {"tag": tag["name"], "digest": tag["digest"], "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class ProbeGate:
    """Q-DRV-2: the probe's gate - StopGate AND GateDriver. A 401/402/403 makes StopGate raise (the unit never starts);
    an open incident makes GateDriver refuse; only both admitting admits the unit."""

    def __init__(self, stop: Any, driver: Any) -> None:
        self.stop, self.driver = stop, driver

    def admits_new_unit(self) -> bool:
        return bool(self.stop.admits_new_unit()) and bool(self.driver.admits_new_unit())


def _mod(name: str, rel: str) -> Any:
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, HERE / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def probe_modules() -> SimpleNamespace:
    """The campaign's own modules the driver runs on (never copies of their logic)."""
    return SimpleNamespace(SC=_mod("v3_scheduler_for_a8", "scheduler.py"), PL=_mod("v3_run_v3_plan_for_a8", "run_v3_plan.py"),
                           RP=_mod("v3_run_v3_proxy_for_a8", "run_v3_proxy.py"), SL=_mod("v3_status_log_for_a8", "status_log.py"),
                           G=_mod("v3_run_v3_gate_for_a8", "run_v3_gate.py"), IN=_mod("v3_incidents_for_a8", "incidents.py"),
                           AB=_mod("v3_ab_harness_for_a8", "ab_harness.py"), AC=_mod("v3_accounting_for_a8", "accounting.py"),
                           IV=_mod("v3_install_v3_data_for_a8", "install_v3_data.py"), RV=_mod("v3_run_v3_for_a8", "run_v3.py"),
                           U=_mod("v3_probe_upstream_for_a8", "probe_upstream.py"))


@dataclass
class ProbeDeps:
    """What the driver touches, injectable (as run_v3.run_smoke's deps): a real run takes real_deps()."""
    modules: Any
    environ: dict
    proxy_python: Path
    native: Any = None
    fs: Any = None
    start_proxy: Any = None
    stop_proxy: Any = None
    post: Any = None
    make_witnesses: Any = None
    make_scheduler: Any = None
    make_launcher: Any = None
    clock: Any = None
    extra: dict = field(default_factory=dict)


def real_deps(c: Any, L: Any, *, environ: Mapping[str, str], proxy_python: Path) -> ProbeDeps:
    M = probe_modules()
    return ProbeDeps(
        modules=M, environ=dict(environ), proxy_python=Path(proxy_python), native=L.NativeEgressWitness(),
        fs=L.FsWitness(L.watched_set(c)), start_proxy=lambda c_, **kw: M.RP.start(c_, spawn=L.spawn_proxy, **kw),
        stop_proxy=M.RP.stop, post=M.RP.post,
        make_witnesses=lambda c_, native, fs, canaries: L.Witnesses(c_, native=native, fs=fs, canaries=canaries),
        make_scheduler=lambda *a, **k: M.SC.Scheduler(*a, **k),
        make_launcher=lambda c_, h, python, tag, units: M.PL.PlanLauncher(
            "mem0", stand=PROBE_STAND, python=python, proxy=h, stager=M.PL.CodeStager(c_.runs_root),
            unit_block={u: PROBE_BLOCK for u in units}, embed_tag=tag, dated=True,
            unit_chars={u: sum(len(o["item"]["text"]) for o in M0_OPS) for u in units}).launcher(M.SC.ChildArmLauncher),
        clock=M.SC.SystemClock())


def run_mem0_probe(c: Any, L: Any, *, run: str, install_run: str, model_run: str, deps: ProbeDeps,
                   d1_run: str = "d1") -> dict:
    """C5a-2c: the mem0 probe, after its windows (see the module docstring and the auditor's Q-DRV-1..5): the adapter
    from a CodeStager copy through the campaign's ChildArmLauncher on stand _a8, against the loopback fakes behind the
    real proxy; one unit's write stage (M0_OPS), its counters, the proxy's records; the fields, the verdict,
    probe.json - written once."""
    M = deps.modules
    arm_dir = Path(c.runs_root) / PROBE_STAND / run / "mem0"
    dest = arm_dir / "probe.json"
    if dest.exists():
        raise ProbeError(f"{dest} exists - a probe record is written once (Q-C5-1)")
    record: dict = {"arm": "mem0", "run": run, "unit": PROBE_UNIT, "install_run": install_run, "model_run": model_run,
                    "witness_scope": WITNESS_SCOPE, "ops_sha256": _canon_sha(M0_OPS),
                    "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def done(fields: dict, problems: list[str], checks: list, catcher_lines: int) -> dict:
        record["fields"], record["problems"] = fields, problems
        record["outcome"], record["reasons"] = verdict(fields, problems=problems, checks=checks, catcher_lines=catcher_lines)
        record["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_probe(dest, record)
        return record

    runs = Path(c.runs_root)
    irec = _json_file(runs / "_install" / "a8-pypi-mem0_v3" / install_run / "install_record.json")
    pin_field = m0_pin(irec)
    if pin_field.get("blocked"):
        return done({"m0_pin": pin_field}, [], [], 0)
    mrec = _json_file(runs / "_install" / "a8-spacy-model" / model_run / "model_record.json")
    if mrec is None or mrec.get("problems"):
        return done({"m0_pin": pin_field}, ["blocked:model-not-installed - no clean a8-spacy-model record: mem0 would "
                                            "download it at run time (B-NLP)"], [], 0)
    record["installed_sets"] = {"install": irec.get("installed_set_sha256"),          # R-NLP-SET: the pair
                                "model": mrec.get("model_installed_set_sha256")}
    venv = Path(irec["venv"])
    site = next((x for x in venv.rglob("site-packages") if x.is_dir()), None)
    if site is None:
        return done({"m0_pin": pin_field}, [f"blocked:not-installed - {venv} has no site-packages"], [], 0)
    facts = mem0_source_facts(site)
    bound = mem0_bound_facts(site)                         # Q-C6-5: writer_bound reads them from this record
    record["bound_facts"], record["bound_blocked"] = bound, bound_blocked(bound)
    script = mem0_script(facts)
    if isinstance(script, dict):
        return done({"m0_pin": pin_field}, [f"{script['blocked']} - the fake's answer has no source shape"], [], 0)
    pin = embed_pin(runs, d1_run)
    record["embed_pin"] = pin
    if pin.get("blocked"):
        return done({"m0_pin": pin_field}, [f"{pin['blocked']} - {pin['path']}"], [], 0)
    fd = M.U.FakeDeepSeek(script)
    fo = M.U.FakeOllama(tag=pin["tag"], digest=pin["digest"])
    record.update(script_sha256=fd.script_sha256, test_upstream=TEST_UPSTREAM_NOTE,
                  ceiling_s=M.SC.DEBUG_CEILING_S)
    env = dict(deps.environ)
    wiring = M.RV.boundary_canaries(L, c, env)
    W = deps.make_witnesses(c, deps.native, deps.fs, wiring["canaries"])
    proxy_dir = arm_dir / "_proxy"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    key_file = proxy_dir / "sentinel.env"                  # outside the secrets root: test_upstream_ok, never the key
    key_file.write_bytes(f"DEEPSEEK_API_KEY=nvt3-a8probe-KEYSENTINEL-{_secrets.token_hex(8)}\n".encode())
    cfg = M.RP.build_config({"mem0": {"llm": M.RP.PINNED_MODEL, "llm_transport": M.RP.PROVIDER_TRANSPORT,
                                      "embeds_via_ollama": True}},
                            run_dir=proxy_dir / "run", test_upstream={"host": "127.0.0.1", "port": fd.port, "tls": False},
                            test_ollama_upstream=("127.0.0.1", fo.port))
    problems: list[str] = []
    checks: list = []
    w = None
    gd = None
    h = deps.start_proxy(c, python=deps.proxy_python, key_file=key_file, config=cfg,
                         secrets=M.RP.build_secrets(["mem0"], roles=("scheduler",), canaries=wiring["proxy"]),
                         unit=L.make_unit_dirs(c, PROBE_STAND, "_harness", "proxy", run), parent_env=env, witnesses=W)
    status_path = runs / PROBE_STAND / run / "STATUS"
    try:
        status = M.SL.StatusLog(status_path)
        hooks = SimpleNamespace(gate=None)
        sched = deps.make_scheduler(c, h.control, status, L, deps.clock, None, tag="debug", witnesses=W, parent_env=env,
                                    catcher_url=M.RP.catcher_url(h), hooks=hooks,
                                    canaries=wiring["scheduler"]["canaries"],
                                    home_canaries=wiring["scheduler"]["home_canaries"])
        send_probe = M.RV.probe(deps.post, h.ports["scheduler"], h.tokens["scheduler"])
        model = send_probe().get("model")
        record["expected_model"] = model
        if not model:
            raise ProbeError("the scheduler port's probe named no model - the gate would have no expected model")
        gd = M.G.GateDriver(M.IN.IncidentGate(), calls_path=Path(h.run_dir) / "calls.jsonl", status=status,
                            send_probe=send_probe, id_prefix=f"{PROBE_STAND}-{run}-inc", expected_models=[model])
        hooks.gate = ProbeGate(M.AB.StopGate(h.control, h.control.counters()), gd)
        gd.start()
        venv_py = M.IV.venv_python(venv)
        launcher = deps.make_launcher(c, h, venv_py, pin["tag"], [PROBE_UNIT])
        status.stand(PROBE_STAND, "START", model=model, changelog="a8-probe", order=1)
        status.block_start(PROBE_STAND, PROBE_BLOCK, units=[PROBE_UNIT], arm_order=["mem0"], seed=0)   # probe, one arm
        sid = status.start(PROBE_STAND, PROBE_BLOCK, run, "mem0", pid=sched.pid, tag="debug")
        cid = f"a8-mem0-{run}"
        t0 = time.monotonic()
        W.begin_check(cid)
        h.control.stage(f"{PROBE_STAND}/{PROBE_BLOCK}", "write")
        try:
            recs = sched.write_turn(launcher, stand=PROBE_STAND, runs=[run], units=[PROBE_UNIT],
                                    ops_for=lambda r, u: M0_OPS, ceilings={PROBE_UNIT: M.SC.DEBUG_CEILING_S},
                                    status_ids={run: sid})
        finally:
            h.control.stage(None, None)
            checks.append(("probe", M.IV.check_summary(W.end_check(cid))))
        w = recs[(run, PROBE_UNIT)]
        rc = 0 if (w.aborted is None and w.error is None) else 1
        status.end(sid, rc=rc, wall_s=time.monotonic() - t0, units=1, out=str(dest))
        status.block_end(PROBE_STAND, PROBE_BLOCK)
        status.stand(PROBE_STAND, "END", model=model, changelog="a8-probe")
    except Exception as e:  # noqa: BLE001 - the probe's failure is named; the gate, the proxy, the fakes stop below
        problems.append(f"the probe did not complete: {type(e).__name__}: {e}")
    finally:
        if gd is not None:
            gd.stop()
            problems += [f"gate: {x}" for x in gd.problems]
        pstop = dict(deps.stop_proxy(h))
        fd.close()
        fo.close()
    record["proxy_stop"] = pstop
    if pstop.get("killed") or pstop.get("rc") not in (0,):
        problems.append(f"the proxy did not stop by itself: {pstop}")
    record["status_sha256"] = hashlib.sha256(status_path.read_bytes()).hexdigest() if status_path.exists() else None
    record["fake_traps"], record["fake_unknown"] = list(fo.traps), list(fo.unknown)
    problems += [f"the fake Ollama saw a trap: {t}" for t in fo.traps]
    problems += [f"the fake Ollama saw an unserved path: {t}" for t in fo.unknown]
    log = M.AC.load_proxy(Path(h.run_dir))
    problems += [f"proxy record: {x}" for x in log.problems]
    if w is None:
        return done({"m0_pin": pin_field}, problems or ["the probe did not complete"], checks, len(log.catcher))
    if w.aborted is not None or w.error is not None:
        problems.append(f"the unit did not finish: aborted {w.aborted}, error {w.error}")
    problems += [f"a write failed: {o}" for o in (w.ops or []) if isinstance(o, Mapping) and o.get("ok") is False]
    start = _json_file(Path(w.dirs.home) / "start.write.json")
    fields = mem0_fields(start=start, counters=w.counters or {}, calls=log.calls, catcher_lines=len(log.catcher),
                         facts=facts, install_record=irec, run=run, unit=PROBE_UNIT, adds=len(M0_OPS))
    return done(fields, problems, checks, len(log.catcher))


def m0_timestamp(src: Mapping) -> dict:
    """The OSS timestamp refusal in mem0's pinned source keeps date_route "header"; missing, it is blocked by name -
    the route is never switched silently."""
    rule = "the OSS refusal of a caller's timestamp is found exactly once in the pinned source"
    if src.get("blocked"):
        return _field(None, rule=rule, ok=False, source=src.get("source"), failed=src["blocked"], blocked=src["blocked"])
    return _field("header", rule=rule, ok=True, source=src.get("source"))


# ── the verdict and the record ───────────────────────────────────────────────────────────────────────────────

def _check_problems(step: str, summary: Mapping) -> list[str]:
    out = []
    if not summary.get("complete"):
        out.append(f"the {step} check is not complete")
    if summary.get("native_hits") != 0:
        out.append(f"the {step} check counted {summary.get('native_hits')} egress hit(s)")
    if summary.get("fs_hits") != 0:
        out.append(f"the {step} check counted {summary.get('fs_hits')} change(s) in the watched set")
    return out


def verdict(fields: Mapping[str, Mapping], *, problems: Sequence[str], checks: Sequence[tuple[str, Mapping]],
            catcher_lines: int) -> tuple[str, list[str]]:
    """(outcome, reasons): "pass" only with every field ok, no problem, every check complete with 0/0 and no catcher
    line; else the first blocked reason (fields in their order, then the problems), else "fail"."""
    reasons = [f"{name}: {f.get('rule_failed') or 'not ok'}" for name, f in fields.items() if not f.get("ok")]
    reasons += list(problems)
    for step, summary in checks:
        reasons += _check_problems(step, summary)
    if catcher_lines:
        reasons.append(f"{catcher_lines} catcher line(s) of the arm")
    if not reasons:
        return "pass", []
    blocked = [f["blocked"] for f in fields.values() if f.get("blocked")]
    blocked += [p.split(" ", 1)[0] for p in problems if str(p).startswith("blocked:")]
    return (blocked[0] if blocked else "fail"), reasons


def write_probe(dest: Path, record: Mapping) -> None:
    """probe.json, written once (Q-C5-1): an existing file is refused, never replaced."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8")
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    except FileExistsError:
        raise ProbeError(f"{dest} exists - a probe record is written once (Q-C5-1)") from None
    with os.fdopen(fd, "wb") as f:
        f.write(data)
