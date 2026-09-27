#!/usr/bin/env python3
"""PREREG-V3 TB4.10 (A6): the inputs of an artifact's per-arm blocks, computed from the recording proxy's logs (rev1
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
* cloud_counters (§4.5): per (unit, request key), in t0 order - the §4.5 key is sha256(body || arm) with no unit in it,
  and one session body is written in several units (B-ACC1), so a key is classified within its unit - and per EPISODE
  within it (B-ACC1b: one body written twice in a unit): each success closes an episode; an episode whose success
  came after failed attempts (non-2xx, no status, client_abandoned) and ended (t1) within 30 minutes of the episode's
  first failure is transport_recovered, later than that late_recovered (published, never recovered, never in P1 -
  Q-50-1, listed per unit with the flag order_sensitive on S6/S6L); failed attempts after the last success are an
  episode of their own that never succeeded - on a write-phase key a transport_lost operation, on a read/answer/judge
  key a failed outcome unless its question (key_question, by (unit, key)) was dropped. duplicate_body_groups (write
  groups with two or more successes) and ambiguous_recoveries (recovered episodes inside them - either write may be the
  one that failed) are published, for the P1 sensitivity row. The zero-tolerance counts and the descriptive ones -
  upstream_errors is a call answered 5xx or recorded with no answer (upstream_error: connect or send failed); the
  proxy's own counter of that name is another quantity (connect, send, read and framing failures, partial responses
  included, never a 5xx); models_seen; fingerprints per endpoint class, a unit
  straddled when its calls of one class ran under two (§4.3 d; /anthropic by its response model); tokens by phase
  (§6). Only the arm's own calls count: the J3 and scheduler ports are arms of their own in the proxy's records (R4).
  A forwarded record with no request key, t0, or (on a success) t1 refuses by name - never read as 0;
* proxy_boundary_inputs (P0h): the canary, owner-marker and ancestor-canary hits the proxy wrote on each of the
  arm-run's call records, refused ones included, and the catcher's refused egress by host - the ARM's across this log,
  since catcher records carry no run (every run of the arm shares its catcher port); witness_inputs: a launch
  check record (launch.Witnesses.end_check) as the egress and fs witnesses artifact.boundary_block() takes.

TB4.10b (Q12, Q13, K60, K61, K76, K87; the auditor's M1):
* lost_operations -> artifact.p1_block(): each lost logical write in artifact's LOSS_REASONS with its evidence - a
  transport loss is a failed attempt of the trailing never episode of its (unit, key) inside the operation's window;
  the bands, the block classes and P1Exceeds are artifact's; never from the proxy alone;
* yield_inputs -> artifact.yield_block(): the stand's evaluation unit (never the question) and each unit's retrievable
  items and characters; the per-unit cap and the scored-only labels are artifact's;
* cache_inputs -> artifact.cache_record(): a read with its in-campaign build record; K60/K61 are m5 --anchor's;
* reconciliation_inputs: the §2.3 reconciliation numbers; the branch's predicate is artifact's P0j (K87).
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
    """Per (unit, request key) of the forwarded calls, in t0 order: its phase and its episodes. Each success closes an
    episode - ok with no failed attempt before it; recovered if it ended (t1) within 30 minutes of the episode's first
    failure; else late_recovered. Failed attempts after the last success are one more episode, never (B-ACC1b: the same
    body written twice in a unit, the second time lost). The §4.5 key is sha256(body || arm), with no unit in it, and
    one session body is written in several units: classified across units, one unit's loss would hide behind another
    unit's success (B-ACC1). "classes" lists the episodes' classes in order; "last_ok" is the t0 of the group's last
    success (None without one) - a failed attempt after it belongs to the trailing never episode."""
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
        phases = {attribute(c)[3] for c in cs}
        if len(phases) != 1:
            raise AccountingError(f"the request key {key[:12]} spans the phases {sorted(phases)}")
        classes, fails = [], []
        for c in cs:
            if not succeeded(c):
                fails.append(c)
                continue
            if not fails:
                cls = "ok"
            elif _when(c, "t1") - _when(fails[0], "t0") <= RECOVERY_S:
                cls = "recovered"
            else:
                cls = "late_recovered"
            classes.append(cls)
            fails = []
        if fails:
            classes.append("never")
        last_ok = max((_when(c, "t0") for c in cs if succeeded(c)), default=None)
        out[(unit, key)] = {"phase": phases.pop(), "unit": unit, "attempts": len(cs), "classes": classes,
                            "last_ok": last_ok}
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
    eps = [(k, v, e) for k, v in sorted(keys.items()) for e in v["classes"]]
    lost = [k for k, v, e in eps if v["phase"] == "write" and e == "never"]
    dup = [v for v in keys.values() if v["phase"] == "write" and sum(e != "never" for e in v["classes"]) >= 2]
    failed = []
    for k, v, e in eps:
        if v["phase"] != "write" and e == "never":
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
        "transport_recovered": sum(1 for _k, _v, e in eps if e == "recovered"),
        "late_recovered": sum(1 for _k, _v, e in eps if e == "late_recovered"),
        "late_recovered_units": [{"stand": stand, "unit": u, "order_sensitive": stand in ORDER_SENSITIVE_STANDS}
                                 for u in sorted({v["unit"] for _k, v, e in eps if e == "late_recovered"})],
        "transport_lost": len(lost),
        "transport_lost_units": sorted({keys[k]["unit"] for k in lost}),
        "duplicate_body_groups": len(dup),
        "ambiguous_recoveries": sum(1 for v in dup for e in v["classes"] if e in ("recovered", "late_recovered")),
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


# -- TB4.10b --

UNIT_OF_STAND = {"S1": "haystack", "S4": "conversation", "S5": "conversation", "S6": "row", "S6L": "row",
                 "S7": "trajectory"}
#: Q12: the engine's losses of an operation that made no call at all - the breaker was open, or a fallback was refused
#: (its slug is the cloud call's own failure; artifact.block_class() derives the class from it).
NO_CALL_REASONS = ("breaker", "fallback_refused")
JSON_FORMATS = ("json_object", "json_schema")


def _in_lost_episode(c: Mapping[str, Any], unit: str, key_classes: Mapping[tuple[str, str], Mapping[str, Any]]) -> bool:
    """A failed attempt of its (unit, key)'s trailing never episode: an attempt after the group's last success, or in a
    group with none (every attempt before the last success belongs to an episode that success closed)."""
    g = key_classes.get((unit, c.get("request_key")))
    if not g:
        return False
    return g.get("last_ok") is None or _when(c, "t0") > g["last_ok"]


def lost_operations(ops: list, calls: list, *, arm: str, run: str,
                    no_call_ops: Iterable[Mapping[str, Any]] = ()) -> dict:
    """artifact.p1_block()'s arguments for one arm-run (Q12's join, rev1 P1), from the arm-run's own write-port calls
    only - another arm's, another run's and the reader's calls are never an operation's (B-LO1, B-LO2); their episodes
    are classified here, so a caller cannot join runs or arms. An operation {op_id, unit, t0, t1, and error when the
    product reported one} is lost if the product reported an error - product-error, with the M1 evidence: response_seen
    (the proxy saw a completed response in the operation's window) and tool_call (that response's request offered
    tools); if a failed attempt of its (unit, key)'s trailing never episode falls in its window [t0, t1] - transport (an
    attempt a later success of the same key closed is not one); or if the last JSON or tool call in its window was
    empty, cut, unparsable or failed, with no later success in it - structured-output or tool-calling. The engine's
    operations that made no call come as they are (breaker, or fallback_refused with its slug). A valid empty
    extraction is not a loss. transport_lost counts the operations lost by transport."""
    no_call_ops = list(no_call_ops)
    if not ops and not no_call_ops:
        raise AccountingError("p1 needs the arm's logical write operations - never the proxy's counts alone")
    mine = [c for c in _arm_run(calls, arm, run) if c.get("port_role") == "write"]
    key_classes = classify_keys(mine)
    by_unit: dict[str, list] = defaultdict(list)
    for c in mine:
        if not c.get("refused"):
            by_unit[split_unit(c.get("unit"))[1]].append(c)
    lost = []
    for op in ops:
        t0, t1 = _when(op, "t0"), _when(op, "t1")
        window = sorted((c for c in by_unit.get(op["unit"], ()) if t0 <= _when(c, "t0") <= t1),
                        key=lambda c: _when(c, "t0"))
        base = {"op_id": op.get("op_id"), "unit": op.get("unit")}
        if op.get("error"):
            done = [c for c in window if succeeded(c)]
            lost.append({**base, "reason": "product-error", "error": op["error"], "response_seen": bool(done),
                         "tool_call": bool(done and done[-1].get("tools_offered"))})
            continue
        never = sorted({c.get("request_key") for c in window if _in_lost_episode(c, op["unit"], key_classes)})
        if never:
            lost.append({**base, "reason": "transport", "keys": never})
            continue
        structured = [c for c in window if c.get("response_format") in JSON_FORMATS or c.get("tools_offered")]
        if structured:
            last = structured[-1]
            why = ("empty" if last.get("content_empty") else "cut" if last.get("finish_reason") == "length"
                   else "unparsable" if last.get("json_ok") is False or last.get("parse_ok") is False
                   else "failed" if not succeeded(last) else None)
            if why:
                lost.append({**base, "reason": "tool-calling" if last.get("tools_offered") else "structured-output",
                             "request_key": last.get("request_key"), "why": why})
    for op in no_call_ops:
        if op.get("reason") not in NO_CALL_REASONS:
            raise AccountingError(f"a no-call operation's reason is one of {NO_CALL_REASONS}, not {op.get('reason')!r}")
        lost.append(dict(op))
    return {"lost_ops": lost, "transport_lost": sum(1 for x in lost if x["reason"] == "transport"),
            "logical_writes": len(ops) + len(no_call_ops)}


def yield_inputs(stand: str, units: Iterable[Mapping[str, Any]], *, unit_kind: str, tokens_read: int | None = None,
                 contexts_b: Iterable[str] | None = None) -> dict:
    """artifact.yield_block()'s arguments but `scored`, which the caller knows (K76): the stand's evaluation unit (G6 -
    never the question); each unit {unit, retrievable_items, chars_to_writer, unit_chars} as {retrievable, chars_in,
    chars}; and the extra fields items_per_1k_read and empty_context_share (Point B contexts)."""
    if UNIT_OF_STAND.get(stand) != unit_kind:
        raise AccountingError(f"{stand}'s evaluation unit is {UNIT_OF_STAND.get(stand)!r}, not {unit_kind!r}")
    rows = [{"retrievable": u.get("retrievable_items"), "chars_in": u.get("chars_to_writer"), "chars": u.get("unit_chars")}
            for u in units]
    items = sum(r["retrievable"] for r in rows if isinstance(r["retrievable"], int) and not isinstance(r["retrievable"], bool))
    contexts = None if contexts_b is None else list(contexts_b)
    return {"unit": unit_kind, "units": rows,
            "extra": {"items_per_1k_read": 1000.0 * items / tokens_read if tokens_read else None,
                      "empty_context_share": sum(1 for c in contexts if not c.strip()) / len(contexts) if contexts else None}}


def ours_retrievable(notes: Iterable[Mapping[str, Any]]) -> int:
    """K76 for our arm: typed notes count, Session notes never do; a note without a type refuses (R9)."""
    notes = list(notes)
    untyped = [n for n in notes if not isinstance(n.get("type"), str) or not n["type"]]
    if untyped:
        raise AccountingError(f"{len(untyped)} note(s) without a type - K76 counts typed notes only")
    return sum(1 for n in notes if n["type"].lower() != "session")


def cache_inputs(reads: Iterable[Mapping[str, Any]], builds: Mapping[tuple, Mapping[str, Any]]) -> list[dict]:
    """artifact.cache_record()'s arguments for each cache an arm-run read (K60, K61): the read's path, sha256, hits and
    misses with its in-campaign build record {commit, utc, ollama_transport - an artifact.ollama_transport() block},
    keyed by (path, sha256). The K60/K61 verdicts are m5 --anchor's (check_caches); a read with no build record
    cannot be placed."""
    out = []
    for r in reads:
        b = builds.get((r.get("path"), r.get("sha256")))
        if b is None:
            raise AccountingError(f"the cache {r.get('path')} ({str(r.get('sha256'))[:12]}) has no build record - K60 "
                                  f"needs one")
        out.append({"path": r.get("path"), "sha256": r.get("sha256"), "built_commit": b.get("commit"),
                    "built_utc": b.get("utc"), "built_ollama_transport": b.get("ollama_transport"),
                    "hits": r.get("hits"), "misses": r.get("misses")})
    return out


def _tokens_delta(product: int | None, proxy: int) -> float | None:
    """The product's tokens against the proxy's, in percent (B-REC1): None only without a product counter; 0 against 0
    is 0.0; tokens the product counted and the proxy never saw (proxy 0) are 100.0 - a bypass's signature, never
    dropped as a missing delta."""
    if product is None:
        return None
    if proxy == 0:
        return 0.0 if product == 0 else 100.0
    return 100.0 * (product - proxy) / proxy


def reconciliation_inputs(calls: Iterable[Mapping[str, Any]], *, arm: str, run: str, branch: str,
                          adapter_calls: int | None = None, product_calls: int | None = None,
                          product_tokens: int | None = None, serverlog_delta: int | None = None) -> dict:
    """The §2.3 reconciliation numbers of one arm-run (K87 checks 1-2): the HTTP calls and tokens the proxy saw on the
    product's own port (the arm's write port, both stages; the reader's port is the harness's; tokens of its completed
    2xx calls only - Q-K87-1), the adapter's and the
    product's counters as given, tokens_delta_pct = the product's tokens against the proxy's in percent, and
    product_retries = HTTP - logical calls. proxy_logical_calls (R-K87-1) counts the episodes of the port's (unit, key)
    groups - a body retried after transport failures is one logical call, a body written twice is two - which branch
    (b) compares with the product's logical calls. No verdict here: the branch's predicate is artifact's P0j."""
    own = [c for c in _arm_run(calls, arm, run) if not c.get("refused") and c.get("port_role") == "write"]
    http = len(own)
    logical = sum(len(v["classes"]) for v in classify_keys(own).values())
    tokens = sum(int((c.get("usage") or {}).get(k) or 0) for c in own if succeeded(c) for k in ("prompt", "completion"))
    return {"proxy_calls": http, "proxy_logical_calls": logical, "proxy_tokens": tokens, "adapter_calls": adapter_calls,
            "product_logical_calls": product_calls,
            "tokens_delta_pct": _tokens_delta(product_tokens, tokens),
            "serverlog_delta": serverlog_delta, "branch": branch,
            "product_retries": None if product_calls is None else http - product_calls}
