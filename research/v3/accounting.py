#!/usr/bin/env python3
"""PREREG-V3 TB4.10a (A6): the inputs of an artifact's per-arm blocks, computed from the recording proxy's logs (rev1
§2.3, §4.3, §4.5, §6; the auditor's Q3, Q5, Q-50-1).

The auditor's B-DUP ruling (O-a): the blocks' semantics are research/v3/artifact.py's (TB4.2, gated). This module only
computes what its builders take - it builds no block and judges nothing: the harness passes cloud_counters() to
artifact.cloud_transport(), and proxy_boundary_inputs() with witness_inputs() to artifact.boundary_block(); the
zero-tolerance verdicts are artifact's P0 clauses and m5 v3's. A count this module cannot measure refuses by name
(AccountingError) - never 0 (R9).

* load_proxy: calls.jsonl, flags.jsonl, catcher.jsonl and ollama.jsonl, one record per LF-terminated line; a line that
  is not UTF-8 or does not parse is a named problem, never skipped in silence or decoded with replacements;
* attribute: a call's (arm, run, unit, phase) - the /u/<run>.<unit> prefix split at its FIRST dot (Q3; a server arm's
  second part is its block), the phase from the port role and the scheduler's stage: the reader port is "answer", the
  J3 port "judge", an arm's write port is "write" in the write stage and "read" in the question stage;
* cloud_counters (§4.5): per (unit, request key), sorted by t0 - the §4.5 key is sha256(body || arm) with no unit in it,
  and one session body is written in several units (B-ACC1), so a key is classified within its unit. A key that failed
  (non-2xx, no status, client_abandoned) and then succeeded within 30 minutes of the first failure, to the success's
  t1, is transport_recovered; a write-phase key that never succeeded is transport_lost; a
  read/answer/judge key that never succeeded on a question that was not dropped is a failed outcome; a key that
  succeeded only later than 30 minutes is late_recovered (published, never recovered, never in P1 - Q-50-1), listed
  per unit with the flag order_sensitive on S6/S6L; the zero-tolerance counts and the descriptive ones -
  upstream_errors is a call answered 5xx or not answered at all (the proxy's upstream_error: connect or send failed;
  the proxy's own counter of that name counts only the latter); models_seen; fingerprints per endpoint class, a unit
  straddled when its calls of one class ran under two (§4.3 d; /anthropic by its response model); tokens by phase
  (§6). Only the arm's own calls count: the J3 and scheduler ports are arms of their own in the proxy's records (R4).
  A forwarded record with no request key, t0, or (on a success) t1 refuses by name - never read as 0;
* proxy_boundary_inputs (P0h): the canary, owner-marker and ancestor-canary hits the proxy wrote on each of the
  arm-run's call records, refused ones included, and the catcher's refused egress by host - the ARM's across this log,
  since catcher records carry no run (every run of the arm shares its catcher port); witness_inputs: a launch
  check record (launch.Witnesses.end_check) as the egress and fs witnesses artifact.boundary_block() takes.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

RECOVERY_S = 30 * 60
PHASES = ("write", "read", "answer", "judge")
#: The scheduler's stage names (proxy /stage) and the phase an arm's write-port call has in each.
STAGE_PHASE = {"write": "write", "questions": "read"}
ROLE_PHASE = {"reader": "answer", "j3": "judge"}
#: Q-50-1: stands where the order of writes is what is measured - a write that lands after the unit's later writes
#: (late_recovered) is flagged there, and a non-zero share in the pilot is a rev2 question, never decided on the fly.
ORDER_SENSITIVE_STANDS = ("S6", "S6L")
#: The per-call hit fields the proxy writes on every call record (research/_llm_proxy.py, recording checks).
HIT_FIELDS = ("canary_hits", "owner_marker_hits", "ancestor_canary_hits")


class AccountingError(ValueError):
    """A record the accounting cannot place as the preregistration says."""


@dataclass
class ProxyLog:
    calls: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    catcher: list = field(default_factory=list)
    ollama: list = field(default_factory=list)
    problems: list = field(default_factory=list)


def _jsonl(path: Path, problems: list) -> list[dict]:
    """One record per LF-terminated line; an unfinished last line or a line that does not parse is a named problem."""
    if not path.is_file():
        return []
    lines = path.read_bytes().split(b"\n")
    if lines and lines[-1] != b"":
        problems.append(f"{path.name}: the last line has no LF (a write cut short)")
    out = []
    for i, raw in enumerate(lines[:-1], 1):                  # an unterminated last line is never a record
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.name}:{i}: not UTF-8")
            continue
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            problems.append(f"{path.name}:{i}: not a JSON record")
            continue
        if not isinstance(obj, dict):
            problems.append(f"{path.name}:{i}: not a JSON object")
            continue
        out.append(obj)
    return out


def load_proxy(run_dir: Path) -> ProxyLog:
    log = ProxyLog()
    for name in ("calls", "flags", "catcher", "ollama"):
        setattr(log, name, _jsonl(Path(run_dir) / f"{name}.jsonl", log.problems))
    return log


def split_unit(prefix: str) -> tuple[str, str]:
    """Q3: "<run>.<unit>" split at the FIRST dot (a run id has none; the unit part may)."""
    if not isinstance(prefix, str) or "." not in prefix:
        raise AccountingError(f"a call's unit prefix is not <run>.<unit>: {prefix!r}")
    run, unit = prefix.split(".", 1)
    if not run or not unit:
        raise AccountingError(f"a call's unit prefix is not <run>.<unit>: {prefix!r}")
    return run, unit


def attribute(call: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """(arm, run, unit or block, phase) of one call."""
    run, unit = split_unit(call.get("unit"))
    role = call.get("port_role")
    if role in ROLE_PHASE:
        phase = ROLE_PHASE[role]
    elif role == "write":
        stage = call.get("stage")
        if stage not in STAGE_PHASE:
            raise AccountingError(f"a write-port call in the stage {stage!r} has no phase")
        phase = STAGE_PHASE[stage]
    else:
        raise AccountingError(f"a call on the port role {role!r} has no phase")
    return call.get("arm"), run, unit, phase


def _when(rec: Mapping[str, Any], key: str) -> float:
    """A record's t0 or t1 as a timestamp; a record without it refuses - never read as 0 (R9)."""
    s = rec.get(key)
    if not isinstance(s, str) or not s:
        raise AccountingError(f"a record ({str(rec.get('request_key') or rec.get('op_id'))[:12]}) has no {key}")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise AccountingError(f"{key}={s!r} is not an ISO 8601 time") from None


def succeeded(call: Mapping[str, Any]) -> bool:
    st = call.get("status")
    return (isinstance(st, int) and 200 <= st < 300 and bool(call.get("complete"))
            and not call.get("client_abandoned") and not call.get("refused"))


def classify_keys(calls: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict]:
    """Per (unit, request key) of the forwarded calls: its phase and its class - ok, recovered, late_recovered or
    never (a key with no failed attempt and a success is ok). The §4.5 key is sha256(body || arm), with no unit in it,
    and one session body is written in several units: classified across units, one unit's loss would hide behind
    another unit's success (B-ACC1)."""
    by_key: dict[tuple[str, str], list] = defaultdict(list)
    for c in calls:
        if c.get("refused"):
            continue
        if not c.get("request_key"):
            raise AccountingError(f"a forwarded call of {c.get('arm')} has no request_key - the proxy writes one on "
                                  f"every call")
        by_key[(split_unit(c.get("unit"))[1], c["request_key"])].append(c)
    out = {}
    for (unit, key), cs in by_key.items():
        cs = sorted(cs, key=lambda c: _when(c, "t0"))
        fail = next((c for c in cs if not succeeded(c)), None)
        ok = next((c for c in cs if succeeded(c)), None)
        phases = {attribute(c)[3] for c in cs}
        if len(phases) != 1:
            raise AccountingError(f"the request key {key[:12]} spans the phases {sorted(phases)}")
        if ok is None:
            cls = "never"
        elif fail is None or _when(fail, "t0") > _when(ok, "t0"):
            cls = "ok"
        elif _when(ok, "t1") - _when(fail, "t0") <= RECOVERY_S:
            cls = "recovered"
        else:
            cls = "late_recovered"
        out[(unit, key)] = {"phase": phases.pop(), "class": cls, "unit": unit, "attempts": len(cs)}
    return out


def _arm_run(calls: Iterable[Mapping[str, Any]], arm: str, run: str) -> list:
    """The arm-run's own call records; another arm's (the J3 and scheduler ports included) are never split."""
    return [c for c in calls if c.get("arm") == arm and split_unit(c.get("unit"))[0] == run]


def cloud_counters(calls: list, *, arm: str, run: str, stand: str, cloud_bypass: int, ollama: Iterable = (),
                   key_question: Mapping[tuple[str, str], str] | None = None, dropped: Iterable[str] = (),
                   incident_units: Iterable[str] = (), product_retries: int | None = None) -> dict:
    """The counters artifact.cloud_transport() takes, for one arm-run on a stand. key_question maps (unit, request key)
    to its question; cloud_bypass is measured by the boundary instruments, never defaulted; product_retries is the
    reconciliation's (K87 check 2) unless given."""
    mine = _arm_run(calls, arm, run)
    forwarded = [c for c in mine if not c.get("refused")]
    keys = classify_keys(mine)
    dropped = set(dropped)
    kq = dict(key_question or {})
    lost = sorted(k for k, v in keys.items() if v["phase"] == "write" and v["class"] == "never")
    failed = []
    for k, v in keys.items():
        if v["phase"] != "write" and v["class"] == "never":
            q = kq.get(k)
            if q is None:
                raise AccountingError(f"a {v['phase']}-phase key {k[1][:12]} of {k[0]} that never succeeded has no question")
            if q not in dropped:
                failed.append(k)
    fps: dict[str, set] = defaultdict(set)
    per_unit_fp: dict[tuple[str, str], set] = defaultdict(set)           # (endpoint class, unit) -> fingerprints
    models = set()
    for c in forwarded:
        if c.get("response_model"):
            models.add(c["response_model"])
        fp = {"v1": c.get("system_fingerprint"), "anthropic": c.get("response_model")}.get(c.get("endpoint"))
        if fp:
            fps[c["endpoint"]].add(fp)
            per_unit_fp[(c["endpoint"], split_unit(c["unit"])[1])].add(fp)
    tokens = {p: {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0} for p in ("write", "read", "answer")}
    reasoning = 0
    for c in forwarded:
        u = c.get("usage") or {}
        ph = attribute(c)[3]
        if ph in tokens:
            for k in tokens[ph]:
                tokens[ph][k] += int(u.get(k) or 0)
        reasoning += int(u.get("reasoning") or 0)
    return {
        "calls": len(forwarded),
        "failed_outcomes": len(failed),
        "fallback_local": sum(1 for o in ollama if o.get("arm") == arm and o.get("fallback_local")
                              and split_unit(o.get("unit"))[0] == run),
        "model_mismatch": sum(1 for c in mine if c.get("refused") == "model_mismatch"),
        "thinking_calls": sum(1 for c in forwarded if c.get("thinking") and c.get("endpoint") in ("v1", "anthropic")),
        "cloud_bypass": cloud_bypass,
        "tool_violation": sum(1 for c in mine if c.get("tool_violation")),
        "models_seen": sorted(models),
        "transport_recovered": sum(1 for v in keys.values() if v["class"] == "recovered"),
        "late_recovered": sum(1 for v in keys.values() if v["class"] == "late_recovered"),
        "late_recovered_units": [{"stand": stand, "unit": u, "order_sensitive": stand in ORDER_SENSITIVE_STANDS}
                                 for u in sorted({v["unit"] for v in keys.values() if v["class"] == "late_recovered"})],
        "transport_lost": len(lost),
        "transport_lost_units": sorted({keys[k]["unit"] for k in lost}),
        "upstream_errors": sum(1 for c in forwarded if (isinstance(c.get("status"), int) and c["status"] >= 500)
                               or c.get("upstream_error")),
        "client_abandoned": sum(1 for c in forwarded if c.get("client_abandoned")),
        "product_retries": product_retries,
        "thinking_injected": sum(int(c.get("thinking_injected") or 0) for c in forwarded),
        "fingerprints_seen": {k: sorted(v) for k, v in sorted(fps.items())},
        "straddled_units": sorted({u for (_cls, u), s in per_unit_fp.items() if len(s) > 1}),
        "empty_content": sum(1 for c in forwarded if c.get("content_empty")),
        "json_invalid": sum(1 for c in forwarded if c.get("response_format") in ("json_object", "json_schema")
                            and c.get("json_ok") is False),
        "capped": sum(1 for c in forwarded if c.get("finish_reason") == "length"),
        "reasoning_tokens": reasoning,
        "tokens": tokens,
        "incident_units": sorted(set(incident_units)),
    }


def proxy_boundary_inputs(calls: Iterable[Mapping[str, Any]], catcher: Iterable[Mapping[str, Any]], *, arm: str,
                          run: str) -> dict:
    """The proxy's half of artifact.boundary_block(): the hit fields summed over the arm-run's call records (a refused
    call's included - the proxy refuses a canary hit and still writes it), and the catcher's refused egress by host:
    the arm's across this log, not the run's - a catcher record carries no run, and the arm's runs share its port."""
    sums = dict.fromkeys(HIT_FIELDS, 0)
    for c in _arm_run(calls, arm, run):
        for f in HIT_FIELDS:
            v = c.get(f)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise AccountingError(f"a call record of {arm}/{run} has no measured {f} ({v!r}) - never read as 0")
            sums[f] += v
    refused: dict[str, int] = defaultdict(int)
    for c in catcher:
        if c.get("arm") == arm and not c.get("tunnelled"):
            refused[str(c.get("host"))] += 1
    return {**sums, "egress_attempts": dict(sorted(refused.items()))}


def witness_inputs(check: Mapping[str, Any]) -> list[dict]:
    """A launch check record (Witnesses.end_check) as artifact.boundary_block()'s witnesses: one egress witness per
    egress record (the native one, then each container's), one fs witness. Each is complete only if the check is; the
    builder refuses an incomplete witness, or a kind with none."""
    whole = check.get("complete") is True
    out = []
    for rec in (check.get("native"), *(check.get("containers") or ())):
        if rec is None:
            continue
        out.append({"kind": "egress", "complete": whole and rec.get("complete") is True, "hits": rec.get("hits")})
    fs = check.get("fs") or {}
    out.append({"kind": "fs", "complete": whole and fs.get("complete") is not False, "hits": fs.get("fs_hits")})
    return out
