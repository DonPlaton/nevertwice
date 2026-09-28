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
