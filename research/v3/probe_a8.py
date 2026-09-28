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
import json
import os
import re
import textwrap
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
