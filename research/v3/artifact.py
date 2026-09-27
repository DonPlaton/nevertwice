#!/usr/bin/env python3
"""PREREG-V3 TB4.2 (A6): the v3 artifact - one per (stand, point, tier), in the §2.3 shape m5 v3 and m2_v3 read.

Builders refuse what the rules forbid instead of writing it, and never default a counter to 0 (R9: a field the
instrument did not measure is refused, not zeroed):

* arm_decl(): m5 REQUIRED plus every §2.3 v3 field, typed; store_persistence ("disk" | "memory", ruling Q25(4));
* blocked(): the m4 vocabulary for the four arm-level kinds, and no numbers - never scored 0 (P3);
* ollama_transport(): Q5 O-a - integer totals, the pacer's dicts kept under `detail`. The pacer records each call
  once, by its FINAL outcome (by_status, by_exception_type and gave_up are exclusive per call), so
  failed_outcomes = their sum over embed calls + our arm's degraded recalls (P0 a), and failed_outcomes_llm = the same
  sum over LLM calls, which the pacer counts after its bounded retry; attempts that later succeeded are only in
  llm_retries. The TB7 fields (embed_at_cap, fallback_local) and embed_models_seen (ruling B2) are required;
* cloud_transport(): every P0(b) counter and every "also written" field of §2.3, required;
* boundary_block(): the four P0h counters from the proxy and the witnesses; an incomplete or absent witness raises;
* p1_block(): P1 bands - <= 2 % no label, 2-10 % "lossy-writer (x%)", > 10 % raises P1Exceeds with the dominant class
  (the arm is then blocked by the repair logic after its documented attempts, never written as a valid row). A lost
  operation carries its LOSS REASON (counted, published) and its evidence; its BLOCK CLASS - always one of rev1 P1's
  three - is derived by block_class() (the auditor's rulings: breaker -> transport; fallback_refused by its cloud slug;
  product-error by whether the proxy saw a completed response), and the dominant class is taken over those only;
* yield_block(): K76 - retrievable_unit_share and coverage over evaluation units; labels only on scored runs;
* run_record(): the per-run file an END line names (out=), stamped inside its START..END (ruling Q2, m2_v3 S4);
* build(): the aggregate - input_sha256 = sha256 of the canonical input_manifest, run_files {status_id: {path,
  sha256}}, measured_at {commit, dirty, utc} taken at assembly, after the last END (m2_v3 S4);
* p0_flags() / p0_root_flags(): the P0 a-j clauses readable from an artifact and its context, one function each;
  p0j holds K87 checks 1-2 inside the row's reconciliation branch (the auditor's B-DUP ruling: accounting.py only
  computes the numbers).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

SCHEMA = "nvt3-artifact-1"
POINTS = ("B", "K", "V")
TIERS = ("product", "retrieval")
EVAL_UNITS = ("haystack", "conversation", "row", "trajectory", "question")
CONFIG_RE = re.compile(r"^(vendor-default|vendor-recommended:\S+|ours:\S+)$")
TRANSPORT_RE = re.compile(r"^(ollama|cloud:[a-z0-9_-]+)$")
DATE_ROUTE_RE = re.compile(r"^(field:[A-Za-z0-9_.-]+|header|none)$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
#: The m4 vocabulary (.loop/m4_check.py VOCAB) for the four kinds an arm can be blocked with (§2.3, P3).
BLOCK_VOCAB = {
    "blocked": re.compile(r"^blocked:(install|import:[\w.]+|structured-output|tool-calling|local-server|"
                          r"unsupported-surface|silent-extraction|thinking-uncontrollable|transport)(\b.*)?$"),
    "needs-other-env": re.compile(r"^needs-other-env:\S.*$"),
    "competitor-lacks-capability": re.compile(r"^competitor-lacks-capability:[a-z][\w-]*(\b.*)?$"),
    "owner-decision": re.compile(r"^owner-decision:\d{4}-\d{2}-\d{2}\b.*$"),
}
CLOUD_ZERO = ("failed_outcomes", "fallback_local", "model_mismatch", "thinking_calls", "cloud_bypass", "tool_violation")
CLOUD_ALSO = ("transport_recovered", "transport_lost", "upstream_errors", "client_abandoned", "product_retries",
              "thinking_injected", "fingerprints_seen", "straddled_units", "empty_content", "json_invalid", "capped",
              "reasoning_tokens", "tokens", "incident_units")
BOUNDARY = ("canary_hits", "owner_marker_hits", "egress_hits", "fs_hits")
#: rev1 P1's block classes - the only values a P1 block can name (P3's vocabulary).
BLOCK_CLASSES = ("structured-output", "tool-calling", "transport")
#: What a lost operation is counted and published as (TB4.10 ruling; M1 ruling).
LOSS_REASONS = ("structured-output", "tool-calling", "transport", "product-error", "breaker", "fallback_refused")
#: The engine's _LLM_LAST["failure"] slugs (_engine_store.py), split by what they mean for a refused fallback.
CONTENT_SLUGS = ("truncated", "empty", "unparsable", "blocked")
TRANSPORT_SLUGS = ("http", "transport", "error")
P1_LABEL_BAND, P1_BLOCK_BAND = 0.02, 0.10
K76_LABEL_BELOW = 0.5
JUDGE_INVALID_MAX = 0.01
DROP_OWN_MAX = 0.05
DECL_FIELDS = {                     # field -> (types, required?)
    "system": (str,), "version": (str,), "python": (str,), "config": (str,), "llm": (str, type(None)),
    "llm_transport": (str, type(None)), "embedder": (str, type(None)), "k": (int,), "context_budget_tokens": (int,),
    "runs": (int,), "deterministic": (bool,), "write_granularity": (str,), "input_sha256": (str,),
    "embeds_via_ollama": (bool,), "tier": (str,), "point": (str,), "llm_params": (dict,), "reader": (dict,),
    "judges": (dict,), "now_rule": (str,), "date_route": (str,), "renderer": (dict,), "threshold": (str, int, float),
    "namespace": (str,), "tools_allowed": (list,), "launch": (dict,), "deviations": (list,), "symmetry": (dict,),
    "store_persistence": (str,),
}


class ArtifactRefused(ValueError):
    """A block or artifact the v3 rules forbid; nothing was built."""


class P1Exceeds(ArtifactRefused):
    """lost_share > 10 %: not a valid row. The arm is blocked by `dominant` after its documented attempts (P1)."""

    def __init__(self, share: float, dominant: str) -> None:
        super().__init__(f"lost_share {share:.3f} > 10 %: P1 blocks the arm by its dominant class {dominant!r}")
        self.share, self.dominant = share, dominant


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _count(name: str, v) -> int:
    if not (_int(v) and v >= 0):
        raise ArtifactRefused(f"{name} must be a measured non-negative integer, got {v!r} (never defaulted to 0)")
    return v


def _share(num: float, den: float) -> float:
    return 0.0 if den == 0 else num / den


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


# ── arm declaration and blocked arms ───────────────────────────────────────────────────────────────────────────

def arm_decl(**f) -> dict:
    """The §2.3 arm_decl, every field present and typed; refused otherwise."""
    missing = [k for k in DECL_FIELDS if k not in f]
    extra = [k for k in f if k not in DECL_FIELDS]
    if missing or extra:
        raise ArtifactRefused(f"arm_decl missing {missing}, unknown {extra}")
    bad = [k for k, types in DECL_FIELDS.items()
           if not isinstance(f[k], types) or (isinstance(f[k], bool) and bool not in types)]
    if bad:
        raise ArtifactRefused(f"arm_decl fields of the wrong type: {bad}")
    if not CONFIG_RE.match(f["config"]):
        raise ArtifactRefused(f"config {f['config']!r} is neither vendor-default, vendor-recommended:<doc> nor ours:<id>")
    if (f["llm"] is None) != (f["llm_transport"] is None):
        raise ArtifactRefused("llm and llm_transport are both set or both null")
    if f["llm_transport"] is not None and not TRANSPORT_RE.match(f["llm_transport"]):
        raise ArtifactRefused(f"llm_transport {f['llm_transport']!r} is not ollama or cloud:<provider>")
    for k in ("k", "context_budget_tokens", "runs"):
        if f[k] <= 0:
            raise ArtifactRefused(f"{k} must be positive, got {f[k]}")
    if not f["deterministic"] and f["runs"] < 2:
        raise ArtifactRefused(f"a non-deterministic arm with runs={f['runs']} - P4 needs at least 2")
    if not HEX64.match(f["input_sha256"]):
        raise ArtifactRefused("input_sha256 is a sha256")
    if f["tier"] not in TIERS or f["point"] not in POINTS:
        raise ArtifactRefused(f"tier {f['tier']!r} / point {f['point']!r} outside {TIERS} / {POINTS}")
    lp = f["llm_params"]
    if f["llm"] is not None and not {"temperature", "max_tokens", "thinking_route"} <= set(lp):
        raise ArtifactRefused("llm_params needs temperature, max_tokens and thinking_route for an LLM arm")
    if not {"tag", "template_sha256"} <= set(f["reader"]):
        raise ArtifactRefused("reader needs tag and template_sha256")
    for j in ("J1", "J2", "J3"):
        if not isinstance(f["judges"].get(j), dict) or not {"tag", "digest"} <= set(f["judges"][j]):
            raise ArtifactRefused(f"judges.{j} needs tag and digest")
    if f["now_rule"] != "wall-clock":
        raise ArtifactRefused("now_rule is 'wall-clock' (§5.4: no benchmark-relative now)")
    if not DATE_ROUTE_RE.match(f["date_route"]):
        raise ArtifactRefused(f"date_route {f['date_route']!r} is field:<name>, header or none")
    if not {"name", "sha256"} <= set(f["renderer"]):
        raise ArtifactRefused("renderer needs name and sha256")
    if not all(isinstance(t, str) for t in f["tools_allowed"]):
        raise ArtifactRefused("tools_allowed is a list of names")
    if not {"env_names", "cwd_rule", "binary_sha256"} <= set(f["launch"]):
        raise ArtifactRefused("launch needs env_names, cwd_rule and binary_sha256")
    if not all(isinstance(n, str) and "=" not in n for n in f["launch"]["env_names"]):
        raise ArtifactRefused("launch.env_names holds names only, never values")
    if not f["symmetry"]:
        raise ArtifactRefused("symmetry holds one entry per §5.0 knob")
    if f["store_persistence"] not in ("disk", "memory"):
        raise ArtifactRefused("store_persistence is disk or memory (ruling Q25(4))")
    return dict(f)


def blocked(reason: str) -> dict:
    kind = reason.split(":", 1)[0]
    rx = BLOCK_VOCAB.get(kind)
    if rx is None or not rx.match(reason):
        raise ArtifactRefused(f"blocked reason {reason!r} is outside the vocabulary {sorted(BLOCK_VOCAB)}")
    return {"blocked": reason}


# ── transport ──────────────────────────────────────────────────────────────────────────────────────────────────

def _final_failures(d: Mapping, name: str) -> int:
    if not isinstance(d, Mapping) or not {"by_status", "by_exception_type", "gave_up"} <= set(d):
        raise ArtifactRefused(f"pacer {name} is not the pacer's failure record")
    return (sum(_count(f"{name}.by_status", v) for v in d["by_status"].values())
            + sum(_count(f"{name}.by_exception_type", v) for v in d["by_exception_type"].values())
            + _count(f"{name}.gave_up", d["gave_up"]))


def ollama_transport(pacer: Mapping, *, embed_at_cap: int, fallback_local: int, embed_models_seen: Sequence[str],
                     degraded_recalls: int) -> dict:
    """Q5 O-a: the integer totals m5 reads, the pacer's own record kept whole under `detail`."""
    calls = _count("calls", pacer.get("calls"))
    bp = pacer.get("bypass_calls")
    if not isinstance(bp, Mapping):
        raise ArtifactRefused("pacer bypass_calls is the pacer's per-library record")
    models = list(embed_models_seen)
    if not all(isinstance(x, str) and x.strip() for x in models):
        raise ArtifactRefused("embed_models_seen names each embed call's model (ruling B2)")
    return {"calls": calls,
            "failed_outcomes": _final_failures(pacer.get("failed_outcomes"), "failed_outcomes")
                               + _count("degraded_recalls", degraded_recalls),
            "failed_outcomes_llm": _final_failures(pacer.get("failed_outcomes_llm"), "failed_outcomes_llm"),
            "bypass_calls": sum(_count("bypass_calls", v) for v in bp.values()),
            "embed_at_cap": _count("embed_at_cap", embed_at_cap),
            "fallback_local": _count("fallback_local", fallback_local),
            "embed_models_seen": sorted(set(models)),
            "detail": dict(pacer)}


def cloud_transport(counters: Mapping) -> dict:
    """Every P0(b) counter and every field §2.3 says is also written; none is defaulted."""
    missing = [k for k in ("calls", "models_seen", *CLOUD_ZERO, *CLOUD_ALSO) if k not in counters]
    if missing:
        raise ArtifactRefused(f"cloud_transport lacks {missing} - the proxy writes them, always")
    for k in ("calls", *CLOUD_ZERO):
        _count(k, counters[k])
    if not isinstance(counters["models_seen"], list):
        raise ArtifactRefused("models_seen is a list")
    if not {"write", "read", "answer"} <= set(counters["tokens"]):
        raise ArtifactRefused("tokens are written by phase: write, read, answer")
    return dict(counters)


def boundary_block(*, proxy: Mapping, witnesses: Sequence[Mapping]) -> dict:
    """P0h: canary and owner-marker hits from the proxy's scan, egress and fs hits from the witnesses. An incomplete
    witness, or a kind with no witness at all, raises - an unmeasured boundary is never 0."""
    for k in ("canary_hits", "owner_marker_hits", "ancestor_canary_hits", "egress_attempts"):
        if k not in proxy:
            raise ArtifactRefused(f"the proxy record lacks {k}")
    hits = {"egress": 0, "fs": 0}
    seen = set()
    for w in witnesses:
        kind = w.get("kind")
        if kind not in hits:
            raise ArtifactRefused(f"witness kind {kind!r} is egress or fs")
        if w.get("complete") is not True:
            raise ArtifactRefused(f"the {kind} witness is incomplete - an unmeasured boundary is not 0 (P0h)")
        hits[kind] += _count(f"{kind} witness hits", w.get("hits"))
        seen.add(kind)
    if seen != set(hits):
        raise ArtifactRefused(f"no {sorted(set(hits) - seen)} witness - an unmeasured boundary is not 0 (P0h)")
    return {"canary_hits": _count("canary_hits", proxy["canary_hits"]),
            "owner_marker_hits": _count("owner_marker_hits", proxy["owner_marker_hits"]),
            "egress_hits": hits["egress"], "fs_hits": hits["fs"],
            "ancestor_canary_hits": _count("ancestor_canary_hits", proxy["ancestor_canary_hits"]),
            "egress_attempts": dict(proxy["egress_attempts"])}


def cache_record(*, path: str, sha256: str, built_commit: str, built_utc: str, built_ollama_transport: Mapping,
                 hits: int, misses: int) -> dict:
    """K60/K61 (§5.7): a cache a row reads, with its in-campaign build record. The build's ollama_transport is an
    ollama_transport() block, so its embed_models_seen names the model the cached vectors came from - what m5 reads
    for a fully cache-served arm (ruling B2)."""
    if not HEX64.match(sha256) or not HEX40.match(built_commit):
        raise ArtifactRefused("a cache record names the cache's sha256 and the 40-hex commit it was built at")
    try:
        dt.datetime.fromisoformat(built_utc)
    except (TypeError, ValueError):
        raise ArtifactRefused(f"built utc {built_utc!r} is not ISO 8601") from None
    missing = [k for k in ("calls", "failed_outcomes", "failed_outcomes_llm", "embed_models_seen")
               if k not in built_ollama_transport]
    if missing:
        raise ArtifactRefused(f"the build's ollama_transport lacks {missing} - it is an ollama_transport() block")
    if built_ollama_transport["calls"] > 0 and not built_ollama_transport["embed_models_seen"]:
        raise ArtifactRefused("a build that paid embed calls names their model (B2)")
    return {"path": path, "sha256": sha256, "hits": _count("hits", hits), "misses": _count("misses", misses),
            "built": {"commit": built_commit, "utc": built_utc, "ollama_transport": dict(built_ollama_transport)}}


# ── write losses and yield ─────────────────────────────────────────────────────────────────────────────────────

def block_class(op: Mapping) -> str:
    """The P1 block class of one lost operation, derived from its evidence - never a new block value:
    a reason that is already a class stays; product-error -> structured-output (tool-calling for a tool call) when the
    proxy saw a completed response for the operation, else transport; breaker -> transport (the breaker trips only on
    exhausted transport); fallback_refused -> by the cloud call's own slug: content slugs -> structured-output,
    transport slugs -> transport. Missing evidence is refused, never defaulted."""
    reason = op.get("reason")
    if reason in BLOCK_CLASSES:
        return reason
    if reason == "product-error":
        if not isinstance(op.get("response_seen"), bool):
            raise ArtifactRefused("a product-error loss names whether the proxy saw a completed response (response_seen)")
        if op["response_seen"]:
            return "tool-calling" if op.get("tool_call") is True else "structured-output"
        return "transport"
    if reason == "breaker":
        return "transport"
    if reason == "fallback_refused":
        slug = op.get("slug")
        if slug in CONTENT_SLUGS:
            return "structured-output"
        if slug in TRANSPORT_SLUGS:
            return "transport"
        raise ArtifactRefused(f"a fallback_refused loss with slug {slug!r}: its block class cannot be derived")
    raise ArtifactRefused(f"loss reason {reason!r} is outside {LOSS_REASONS}")


def dominant_class(classes: Mapping[str, int]) -> str:
    """The block class with the most lost operations; a tie goes to the class listed first in P1."""
    bad = [c for c in classes if c not in BLOCK_CLASSES]
    if bad:
        raise ArtifactRefused(f"block classes {bad} outside {BLOCK_CLASSES}")
    if not classes or not any(classes.values()):
        raise ArtifactRefused("no lost operation to name a class for")
    return max(BLOCK_CLASSES, key=lambda c: (classes.get(c, 0), -BLOCK_CLASSES.index(c)))


def p1_block(*, lost_ops: Sequence[Mapping], transport_lost: int, logical_writes: int) -> dict:
    """P1 (K75): the shares per arm-run, labelled by band; > 10 % is not a valid row (P1Exceeds). Each lost operation
    is {reason, and its evidence}; reasons are counted as given, block classes derived by block_class()."""
    lost = len(lost_ops)
    for k, v in (("transport_lost", transport_lost), ("logical_writes", logical_writes)):
        _count(k, v)
    reasons: dict[str, int] = {}
    classes: dict[str, int] = {}
    for op in lost_ops:
        c = block_class(op)                              # refuses a reason outside LOSS_REASONS, and missing evidence
        r = op["reason"]
        reasons[r] = reasons.get(r, 0) + 1
        classes[c] = classes.get(c, 0) + 1
    if transport_lost > lost:
        raise ArtifactRefused("transport_lost operations are lost operations: transport_lost <= lost")
    if logical_writes == 0 and lost:
        raise ArtifactRefused("lost operations without logical writes")
    ls, ts = _share(lost, logical_writes), _share(transport_lost, logical_writes)
    if ls > P1_BLOCK_BAND:
        raise P1Exceeds(ls, dominant_class(classes))
    label = f"lossy-writer ({100 * ls:.1f}%)" if ls > P1_LABEL_BAND else ""
    return {"lost_share": ls, "transport_lost_share": ts, "label": label, "lost": lost,
            "transport_lost": transport_lost, "logical_writes": logical_writes, "reasons": reasons, "classes": classes}


def yield_block(*, unit: str, units: Sequence[Mapping], scored: bool, extra: Mapping | None = None) -> dict:
    """K76 over evaluation units: each {retrievable: items in the store at the end of the write stage, chars_in:
    characters that reached the writer's LLM, chars: the unit's characters}. Labels only on scored runs."""
    if unit not in EVAL_UNITS:
        raise ArtifactRefused(f"yield unit {unit!r} is not an evaluation unit {EVAL_UNITS}")
    if not units:
        raise ArtifactRefused("yield over no units")
    covs = []
    for u in units:
        _count("retrievable", u.get("retrievable"))
        c, ci = _count("chars", u.get("chars")), _count("chars_in", u.get("chars_in"))
        if c == 0:
            raise ArtifactRefused("an evaluation unit with no characters")
        covs.append(min(ci, c) / c)          # the unit's own text reaching the LLM; product scaffolding is not coverage
    rus = sum(1 for u in units if u["retrievable"] > 0) / len(units)
    cov = sum(covs) / len(covs)
    labels = []
    if scored:
        if rus < K76_LABEL_BELOW:
            labels.append(f"writer-gated ({100 * rus:.1f}%)")
        if cov < K76_LABEL_BELOW:
            labels.append(f"window ({100 * cov:.1f}%)")
    out = {"unit": unit, "retrievable_unit_share": rus, "coverage": cov, "labels": labels, "scored": scored}
    for k, v in (extra or {}).items():
        if k in out:
            raise ArtifactRefused(f"extra yield field {k} would overwrite a measured one")
        out[k] = v
    return out


# ── files ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(commit: str, dirty: bool, now: Callable[[], dt.datetime]) -> dict:
    if not HEX40.match(commit):
        raise ArtifactRefused(f"commit {commit!r} is a 40-hex sha")
    if not isinstance(dirty, bool):
        raise ArtifactRefused("dirty is a measured bool")
    return {"commit": commit, "dirty": dirty, "utc": now().astimezone(dt.timezone.utc).isoformat(timespec="microseconds")}


def render(doc: Mapping) -> bytes:
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def run_record(path: Path, status_id: str, payload: Mapping, *, commit: str, dirty: bool,
               now: Callable[[], dt.datetime] = _utc_now) -> str:
    """The per-run file an END line names (out=), stamped now - inside its START..END (ruling Q2, m2_v3 S4).
    Returns its sha256. An existing file is never overwritten."""
    if "measured_at" in payload or "status_id" in payload:
        raise ArtifactRefused("the run record's measured_at and status_id are the writer's")
    data = render({**payload, "status_id": status_id, "measured_at": _stamp(commit, dirty, now)})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def build(*, stand: str, point: str, tier: str, arms: Mapping[str, Mapping], brackets: Mapping | None,
          input_manifest: Mapping, model_version: Mapping, commit: str, dirty: bool, status_ids: Sequence[str],
          run_files: Mapping[str, Mapping], declared_axes: Sequence[str] = (), sensitivity: str | None = None,
          write_record: str | None = None, now: Callable[[], dt.datetime] = _utc_now) -> dict:
    """The aggregate artifact; measured_at is taken now, at assembly (after the last END of its runs)."""
    if point not in POINTS or tier not in TIERS:
        raise ArtifactRefused(f"point {point!r} / tier {tier!r} outside {POINTS} / {TIERS}")
    if not {"dataset_sha256", "list_sha256", "split"} <= set(input_manifest):
        raise ArtifactRefused("input_manifest needs dataset_sha256, list_sha256 and split")
    if not {"response_model", "changelog_newest"} <= set(model_version):
        raise ArtifactRefused("model_version needs response_model and changelog_newest")
    bad_axes = [a for a in declared_axes if a not in ("k", "budget", "embedder")]
    if bad_axes:
        raise ArtifactRefused(f"declared_axes {bad_axes} are not §5.2 axes")
    if sensitivity not in (None, "temperature-0"):
        raise ArtifactRefused(f"sensitivity {sensitivity!r}: only the all-0 row is pre-declared")
    if brackets is not None and (point != "B" or tier != "product"):
        raise ArtifactRefused("brackets live only in the product artifact at Point B (ruling Q7)")
    if set(run_files) != set(status_ids) or not status_ids:
        raise ArtifactRefused("run_files names exactly the status_ids the artifact covers")
    isha = sha256_of(dict(input_manifest))
    for name, row in arms.items():
        if row.get("blocked"):
            if set(row) - {"blocked", "note"}:
                raise ArtifactRefused(f"blocked arm {name} carries more than its reason - no numbers (P3)")
            continue
        d = row.get("arm_decl") or {}
        if d.get("input_sha256") != isha:
            raise ArtifactRefused(f"arm {name} was fed {d.get('input_sha256')}, not this artifact's input {isha}")
        if (d.get("point"), d.get("tier")) != (point, tier):
            raise ArtifactRefused(f"arm {name} declares point/tier {d.get('point')}/{d.get('tier')}")
    doc = {"schema": SCHEMA, "stand": stand, "point": point, "tier": tier, "sensitivity": sensitivity,
           "declared_axes": list(declared_axes), "input_manifest": dict(input_manifest), "input_sha256": isha,
           "model_version": dict(model_version), "status_ids": list(status_ids),
           "run_files": {k: dict(v) for k, v in run_files.items()}, "arms": {k: dict(v) for k, v in arms.items()},
           "measured_at": _stamp(commit, dirty, now)}
    if brackets is not None:
        doc["brackets"] = dict(brackets)
    if write_record is not None:
        doc["write_record"] = write_record
    return doc


def write(doc: Mapping, results_dir: Path) -> Path:
    """research/v3/results/<stand>_<point>_<tier>[_<sensitivity>].json, LF, sorted keys, never overwritten."""
    name = "_".join(x for x in (doc["stand"], doc["point"], doc["tier"], doc.get("sensitivity")) if x) + ".json"
    p = Path(results_dir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "xb") as f:
        f.write(render(doc))
    return p


# ── P0 ─────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class P0Context:
    anchor: str = ""
    anchor_utc: dt.datetime | None = None
    windows: Mapping[str, tuple] = field(default_factory=dict)    # status_id -> (START utc, END utc)
    model_event_ids: frozenset = frozenset()                       # status_ids a model event fell inside (§4.3)
    unasserted_spawns: frozenset = frozenset()                     # status_ids with a spawn lacking its env assertion
    k61: Mapping[str, bool] = field(default_factory=dict)          # arm -> K61 holds (every read cache-served)
    stand_units: int = 0
    reconciliation_branches: frozenset = frozenset()               # the branches the slot allows
    single_witness_ok: Mapping[str, bool] = field(default_factory=dict)   # arm -> K87 (c): A/B passed, footprints > 0
    timing: bool = False
    list_sha256: str = ""
    dataset_sha256: str = ""
    expected_n: int | None = None
    m5_pass: bool | None = None


def p0a(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    ot, d = row.get("ollama_transport") or {}, row.get("arm_decl") or {}
    out = []
    if ot.get("bypass_calls", 0) > 0:
        out.append("P0a: bypass_calls > 0")
    if ot.get("failed_outcomes", 0) > 0:
        out.append("P0a: failed embed outcomes (400s and degraded recalls included)")
    if ot.get("failed_outcomes_llm", 0) > 0:
        out.append("P0a: failed_outcomes_llm after the pacer's bounded retry")
    needs = d.get("llm_transport") == "ollama" or d.get("embeds_via_ollama")
    if needs and ot.get("calls", 0) == 0 and not ctx.k61.get(arm):
        out.append("P0a: needs Ollama but counts no paced call, and K61 does not hold")
    return out


def p0b(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    d = row.get("arm_decl") or {}
    if not str(d.get("llm_transport") or "").startswith("cloud:"):
        return []
    ct = row.get("cloud_transport")
    if not isinstance(ct, Mapping) or ct.get("calls", 0) == 0:
        return ["P0b: cloud_transport missing or calls == 0"]
    out = [f"P0b: {k} > 0" for k in CLOUD_ZERO if ct.get(k, 0) > 0]
    if len(ct.get("models_seen") or []) != 1:
        out.append("P0b: models_seen is not exactly one model")
    if any(s in ctx.model_event_ids for s in _row_ids(row)):
        out.append("P0b: a model event falls inside the artifact's blocks")
    return out


def p0c(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    """An arm-run whose own failures dropped more than 5 % of the stand's units (each run on its own)."""
    if not ctx.stand_units:
        return []
    return [f"P0c: run {r.get('status_id')} dropped {r.get('units_dropped_own')} of {ctx.stand_units} units by its "
            f"own failures (> 5 %)" for r in row.get("runs") or []
            if isinstance(r, Mapping) and (r.get("units_dropped_own") or 0) / ctx.stand_units > DROP_OWN_MAX]


def p0d(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    if row.get("blocked"):
        out = []
        kind = str(row["blocked"]).split(":", 1)[0]
        if kind not in BLOCK_VOCAB or not BLOCK_VOCAB[kind].match(str(row["blocked"])):
            out.append("P0d: a blocked value outside the vocabulary")
        if set(row) - {"blocked", "note"}:
            out.append("P0d: a blocked arm carries numbers")
        return out
    return []


def p0f(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    if row.get("blocked"):
        return []
    qs = row.get("questions")
    if not qs:
        return ["P0f: a requested arm with no contexts"]
    return []


def p0g(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    if not ctx.timing:
        return []
    mi = row.get("machine_idle") or {}
    if mi.get("mode") != "observe" or mi.get("idle") is not True:
        return ["P0g: a timing row not in observe mode, or without a measured idle:true"]
    return []


def p0h(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    if row.get("blocked"):
        return []
    b = row.get("boundary")
    if not isinstance(b, Mapping) or any(k not in b for k in BOUNDARY):
        return ["P0h: no complete boundary record"]
    out = [f"P0h: {k} > 0" for k in BOUNDARY if b[k] > 0]
    if any(s in ctx.unasserted_spawns for s in _row_ids(row)):
        out.append("P0h: a spawn without its environment-assertion record")
    return out


def p0i(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    qs = [q for q in row.get("questions") or [] if isinstance(q, Mapping)]
    if not qs:
        return []
    bad = sum(1 for q in qs if q.get("invalid"))
    if bad / len(qs) > JUDGE_INVALID_MAX:
        return [f"P0i: {bad} of {len(qs)} verdicts invalid (> 1 %)"]
    return []


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def p0j(row: Mapping, ctx: P0Context, arm: str) -> list[str]:
    """K87 checks 1-2 inside the row's branch of the reconciliation-granularity slot, one predicate per branch:
    (a) the adapter's HTTP calls equal the proxy's, exactly, and a tokens delta, when the row has one, is exactly 0
    (Q-K87-1); (b) the product's logical calls and the tokens within 1 %
    of the proxy's, the bound inclusive (tokens_delta_pct is the product's delta against the proxy, in percent);
    in (a) and (b) logical calls never exceed HTTP calls; (c) single-witness only when single_witness_ok holds for this
    arm (the recording-vs-raw-forward A/B passed for its surface class and every unit's footprint is > 0)."""
    rec = row.get("reconciliation")
    if row.get("blocked") or rec is None:
        return []
    branch = rec.get("branch")
    if branch not in ctx.reconciliation_branches:
        return [f"P0j: reconciliation branch {branch!r} is outside the slot's {sorted(ctx.reconciliation_branches)}"]
    out = []
    http, logical = rec.get("proxy_calls"), rec.get("product_logical_calls")
    if branch in ("a", "b") and not (_int(http) and http >= 0):
        return [f"P0j: branch {branch} without the proxy's HTTP call count"]
    if branch == "a":
        ad = rec.get("adapter_calls")
        if not _int(ad):
            out.append("P0j: branch a without the adapter counter")
        elif ad != http:
            out.append(f"P0j: the adapter counted {ad} HTTP calls, the proxy {http} - branch a is exact")
        td = rec.get("tokens_delta_pct")
        if td is not None and not (_num(td) and td == 0.0):
            out.append(f"P0j: tokens {td!r} % against the proxy - branch a is exact (Q-K87-1)")
    if branch == "b":
        td = rec.get("tokens_delta_pct")
        if not _int(logical) or not _num(td):
            out.append("P0j: branch b without the product-side counter (logical calls and tokens)")
        else:
            if abs(logical - http) > 0.01 * http:
                out.append(f"P0j: the product counted {logical} calls, the proxy {http} - beyond 1 % (branch b)")
            if abs(td) > 1.0:
                out.append(f"P0j: tokens {td:+.2f} % against the proxy - beyond 1 % (branch b)")
    if branch in ("a", "b") and _int(logical) and logical > http:
        out.append(f"P0j: {logical} logical calls above {http} HTTP calls")
    if branch == "c" and ctx.single_witness_ok.get(arm) is not True:
        out.append("P0j: single-witness without its A/B passed and a footprint > 0 on every unit (single_witness_ok)")
    return out


ROW_CLAUSES = (p0a, p0b, p0c, p0d, p0f, p0g, p0h, p0i, p0j)


def _row_ids(row: Mapping) -> list[str]:
    return [r.get("status_id") for r in row.get("runs") or [] if isinstance(r, Mapping)]


def p0_flags(row: Mapping, ctx: P0Context, arm: str = "") -> list[str]:
    """The row-level P0 clauses (a, b, c-own-drops, d, f-contexts, g, h, i, j); each named by its letter."""
    return [x for clause in ROW_CLAUSES for x in clause(row, ctx, arm)]


def p0_root_flags(doc: Mapping, ctx: P0Context) -> list[str]:
    """The artifact-level clauses: c (lists, datasets, symmetric drops), d (m5), e (provenance), f (n per point)."""
    out = []
    man = doc.get("input_manifest") or {}
    if ctx.list_sha256 and man.get("list_sha256") != ctx.list_sha256:
        out.append("P0c: a list whose sha256 differs from FREEZE-V3")
    if ctx.dataset_sha256 and man.get("dataset_sha256") != ctx.dataset_sha256:
        out.append("P0c: a pinned file with a different sha256")
    drops = {n: frozenset(r.get("units_dropped") or ()) for n, r in (doc.get("arms") or {}).items()
             if not r.get("blocked")}
    if len(set(drops.values())) > 1:
        out.append("P0c: a unit dropped for one arm but not for all")
    if ctx.m5_pass is False:
        out.append("P0d: m5 v3 is not PASS")
    ma = doc.get("measured_at") or {}
    if ctx.anchor and ma.get("commit") != ctx.anchor:
        out.append("P0e: measured_at is not the anchor")
    if ma.get("dirty") is not False:
        out.append("P0e: dirty is not false")
    t = dt.datetime.fromisoformat(ma["utc"]) if ma.get("utc") else None
    if t is None or (ctx.anchor_utc and t < ctx.anchor_utc):
        out.append("P0e: utc is before the anchor")
    last_end = max((w[1] for s, w in ctx.windows.items() if s in (doc.get("status_ids") or [])), default=None)
    if t is not None and last_end is not None and t < last_end:
        out.append("P0e: the artifact was assembled before the last END of its runs")
    if ctx.expected_n is not None:
        for n, r in (doc.get("arms") or {}).items():
            if not r.get("blocked") and len({q.get("qid") for q in r.get("questions") or []}) != ctx.expected_n:
                out.append(f"P0f: arm {n} scored n differs from the {ctx.expected_n} questions asked")
    return out
