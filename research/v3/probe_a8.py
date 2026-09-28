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
* ``verdict``: "pass" only when every field is ok, there is no problem, every boundary check is complete with 0/0 and
  no catcher line belongs to the arm; else the first blocked:<reason> - the fields in their declared order, then the
  problems - else "fail". The proxy's own spawn is unwitnessed by design (launch.spawn_proxy): 0/0 covers the product
  child's tree only (R-C5-9), and probe.json says so.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: The adapter's start answer when mem0's own OpenAI client is wrapped (research/v3/arms/arm_mem0.py, Q-AB-1).
M0_USAGE_SOURCE = "mem0.llm.client.chat.completions.create, response.usage"
M0_PIN = ("mem0ai", "2.2.0")
M0_NO_CLIENT = "mem0's LLM has no OpenAI client"
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

def fact(root: Path, rel: str, pattern: str, *, name: str, group: int = 1) -> dict:
    """Exactly one match of ``pattern`` (declared before the read) in ``root``/``rel``, read as data. ``rel`` must be
    a relative path that stays under ``root``."""
    if os.path.isabs(rel) or Path(rel).drive or ".." in Path(rel).parts:
        raise ProbeError(f"{name}: the source path {rel!r} is not a relative path under the root")
    path = Path(root) / rel
    try:
        if os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) != os.path.realpath(root):
            raise ProbeError(f"{name}: the source path {rel!r} resolves outside the root")
    except ValueError:
        raise ProbeError(f"{name}: the source path {rel!r} is on another drive than the root") from None
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
    value = float(raw)
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
    """Every line asks the response format the pinned source sets and offers no tool."""
    rule = "every v1 line of the unit asks the pinned source's response_format and offers no tool"
    want, blocked = _source_value(src, "m0_format_tools", rule)
    if blocked:
        return blocked
    lines = mine(calls, arm="mem0", run=run, unit=unit)
    if not lines:
        return _field(want, rule=rule, ok=False, source=src.get("source"), failed="unmeasured: no line")
    bad = sorted({f"response_format {c.get('response_format')!r}" for c in lines if c.get("response_format") != want})
    bad += sorted({f"tools_offered {t}" for c in lines for t in (c.get("tools_offered") or [])})
    return _field(want, rule=rule, ok=not bad, source=src.get("source"), failed="; ".join(bad))


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
