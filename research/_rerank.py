#!/usr/bin/env python3
"""Shared LLM-as-reranker primitive (W2/W4). A cross-encoder substitute: jointly score a
query against candidate texts and return one 0-10 relevance score each, in a single JSON call.

Two backends — `ollama` (local, the local-first answer; Ollama is already a hard dependency) and
`deepseek` (opt-in cloud, runtime-blocked without DEEPSEEK_API_KEY). Used by precision_bench and
longmem_eval; promoted to a core opt-in recall mode only if it wins on an EXTERNAL benchmark.

PRIVACY: pure transport — takes text the caller already holds, returns scores. Persists nothing.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m

CHAR_BUDGET = 320            # per-item text budget into the prompt (bounds tokens)


def _loads_tolerant(raw):
    """Parse a model reply to a dict. Falls back to regex-extracting the `scores` array when the
    JSON is slightly malformed (small models drop commas: `[10 9 8]`), so a competent model is not
    penalised for a formatting wobble. Returns {} when nothing usable is found."""
    try:
        return json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        mt = re.search(r'scores"?\s*:\s*\[([^\]]*)\]', raw or "", re.S)
        if mt:
            nums = re.findall(r"-?\d+(?:\.\d+)?", mt.group(1))
            if nums:
                return {"scores": [float(x) for x in nums]}
        return {}


def _strip_fence(raw):
    return m._strip_json_fence((raw or "").strip())


def parse_scores(obj, n):
    """Coerce a model reply dict into n clamped [0,10] scores, or None if unusable
    (→ caller keeps the first-stage order). A non-numeric element degrades to 0.0, not a crash."""
    if not isinstance(obj, dict):
        return None
    sc = obj.get("scores")
    if not isinstance(sc, list) or not sc:
        return None
    out = []
    for i in range(n):
        try:
            out.append(max(0.0, min(10.0, float(sc[i]))))
        except (IndexError, TypeError, ValueError):
            out.append(0.0)
    return out


def build_prompt(query, items, char_budget=CHAR_BUDGET):
    lines = ["You score how relevant each candidate is to a QUERY.",
             "Relevant = the SAME underlying topic, lesson, bug, or evidence — not mere word overlap.",
             "", "QUERY:", (query or "")[:char_budget], "", "CANDIDATES:"]
    for i, t in enumerate(items):
        lines.append(f"[{i}] {(t or '')[:char_budget]}")
    lines.append("")
    lines.append('Return JSON {"scores":[...]} — one integer 0-10 per candidate, in the SAME order.')
    return "\n".join(lines)


def ollama_rerank(query, items, model, stats, char_budget=CHAR_BUDGET, timeout=120):
    """One local JSON call scoring all items. Returns scores list or None. `stats` accumulates
    {calls, errors, prompt_chars} so callers can report cost."""
    prompt = build_prompt(query, items, char_budget)
    payload = json.dumps({"model": model, "prompt": prompt, "format": "json", "stream": False,
                          "think": False, "keep_alive": "10m",   # pin the model so a co-tenant doesn't evict it mid-run
                          "options": {"temperature": 0.0, "num_ctx": 16384}}
                         ).encode("utf-8")
    stats["calls"] += 1
    stats["prompt_chars"] += len(prompt)
    req = urllib.request.Request(m.OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        scores = parse_scores(_loads_tolerant(data.get("response")), len(items))
        if scores is None:
            stats["errors"] += 1
        return scores
    except Exception as e:
        stats["errors"] += 1
        m.log(f"ollama rerank failed: {type(e).__name__}: {e}")
        return None


def deepseek_rerank(query, items, stats, char_budget=CHAR_BUDGET, timeout=120):
    """Opt-in cloud backend. Returns None (no network) when DEEPSEEK_API_KEY is unset."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    prompt = build_prompt(query, items, char_budget)
    payload = json.dumps({"model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                          "messages": [{"role": "user", "content": prompt}], "temperature": 0.0,
                          "response_format": {"type": "json_object"}}).encode("utf-8")
    stats["calls"] += 1
    stats["prompt_chars"] += len(prompt)
    url = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        content = data["choices"][0]["message"]["content"]
        return parse_scores(_loads_tolerant(content), len(items))
    except Exception as e:
        stats["errors"] += 1
        m.log(f"deepseek rerank failed: {type(e).__name__}: {e}")
        return None
