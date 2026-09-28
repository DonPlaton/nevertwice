#!/usr/bin/env python3
"""PREREG-V3 TB4.2 (A6): research/v3/artifact.py - the §2.3 artifact m5 v3 and m2_v3 read.

* builders: arm_decl refuses a missing, extra or ill-typed field; blocked() takes only the vocabulary; ollama_transport
  derives Q5's integer totals from the pacer's final-outcome record (+ degraded recalls) and keeps the dicts under
  detail, and cannot be called without the TB7 fields (R9); cloud_transport needs every P0(b) and "also written"
  field; boundary_block refuses an incomplete or absent witness; p1_block bands (<= 2 %, 2-10 % labelled, > 10 %
  P1Exceeds with its dominant class); yield_block's shares, coverage capped per unit, labels on scored runs only;
* files: run_record stamps measured_at now and never overwrites; build() hashes the canonical input_manifest, names
  exactly its status_ids in run_files, keeps brackets to Point B product (Q7), refuses a blocked arm with numbers;
* P0: each clause a-j fires on its own trigger and stays silent on the clean fixture; P0j reads K87 checks 1-2 inside
  the row's branch (B-DUP): (a) exact against the adapter, and a tokens delta, when there is one, exactly 0
  (Q-K87-1), (b) logical calls and tokens within 1 % of the proxy's (logical against logical: the proxy's HTTP
  calls hold transport retries, R-K87-1), the bound
  inclusive, logical <= HTTP calls in (a) and (b), (c) single-witness only with the context's single_witness_ok;
* the auditor's tools, where present (.loop - outside the repository by design, R12; a named SKIP in CI): m5 v3 --freeze
  PASSes F-A1 (product B), F-A2 (retrieval) and F-A3 (the all-0 sensitivity row) and FAILs each one-field asymmetry
  by name; m2_v3.check_artifacts accepts F-A1 against a STATUS written by status_log with its run records.

    python tests/research/_test_v3_artifact.py
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("v3_artifact", ROOT / "research" / "v3" / "artifact.py")
SL = _load("v3_status_log_for_artifact", ROOT / "research" / "v3" / "status_log.py")
PASSED = FAILED = SKIPPED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  SKIP {name} - {why}")


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except A.ArtifactRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


class Clock:
    def __init__(self):
        self.t = dt.datetime(2026, 10, 1, 9, 0, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        self.t += dt.timedelta(seconds=2)
        return self.t


ANCHOR = "a1" * 20
D1 = "nvt3-bge-m3-d1:latest"
MANIFEST = {"dataset_sha256": "d" * 64, "list_sha256": "e" * 64, "split": "scored", "prefix": 10}
ISHA = A.sha256_of(MANIFEST)
PACER = {"calls": 40, "bypass_calls": {"requests": 0, "aiohttp": 0},
         "failed_outcomes": {"by_status": {}, "by_exception_type": {}, "gave_up": 0},
         "failed_outcomes_llm": {"by_status": {}, "by_exception_type": {}, "gave_up": 0}, "llm_retries": 0}
CLOUD = {"calls": 12, "models_seen": ["deepseek-v4-flash"], **{k: 0 for k in A.CLOUD_ZERO},
         "transport_recovered": 0, "transport_lost": 0, "upstream_errors": 0, "client_abandoned": 0,
         "product_retries": 0, "thinking_injected": 0, "fingerprints_seen": {"v1": ["fp1"], "anthropic": []},
         "straddled_units": [], "empty_content": 0, "json_invalid": 0, "capped": 0, "reasoning_tokens": 0,
         "tokens": {"write": {"in": 10, "out": 5}, "read": {}, "answer": {}}, "incident_units": []}
PROXY = {"canary_hits": 0, "owner_marker_hits": 0, "ancestor_canary_hits": 0, "egress_attempts": {}}
WITNESSES = [{"kind": "egress", "complete": True, "hits": 0}, {"kind": "fs", "complete": True, "hits": 0}]


def decl(system: str, **over) -> dict:
    base = dict(system=system, version="1.0", python="3.12.10", config="vendor-default", llm="deepseek-flash",
                llm_transport="cloud:deepseek", embedder=D1, k=200, context_budget_tokens=7000, runs=2,
                deterministic=False, write_granularity="per-session", input_sha256=ISHA, embeds_via_ollama=True,
                tier="product", point="B", llm_params={"temperature": 0.2, "max_tokens": 4096, "thinking_route": "documented"},
                reader={"tag": "deepseek-flash", "template_sha256": "f" * 64},
                judges={j: {"tag": f"{j.lower()}-tag", "digest": "0" * 64} for j in ("J1", "J2", "J3")},
                now_rule="wall-clock", date_route="field:date", renderer={"name": "api.format_note", "sha256": "1" * 64},
                threshold="n/a", namespace="vault per unit", tools_allowed=[],
                launch={"env_names": ["PATH"], "cwd_rule": "unit dir", "binary_sha256": "2" * 64}, deviations=[],
                symmetry={"write_date_carriage": "own-field"}, store_persistence="disk")
    base.update(over)
    return A.arm_decl(**base)


def row(system: str, *, sid: list[str], **over) -> dict:
    d = decl(system, **over.pop("decl", {}))
    r = {"arm_decl": d, "runs": [{"status_id": s, "units_dropped_own": 0} for s in sid],
         "boundary": A.boundary_block(proxy=PROXY, witnesses=WITNESSES),
         "reconciliation": {"proxy_calls": 12, "adapter_calls": 12, "product_logical_calls": 12, "tokens_delta_pct": 0.0,
                            "serverlog_delta": 0, "branch": "a"},
         "questions": [{"qid": f"q{i}", "invalid": None} for i in range(3)], "units_dropped": []}
    if d["llm_transport"] and d["llm_transport"].startswith("cloud:"):
        r["cloud_transport"] = A.cloud_transport(CLOUD)
    if d["embeds_via_ollama"] or d["llm_transport"] == "ollama":
        r["ollama_transport"] = A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1],
                                                   degraded_recalls=0, direct_calls=0)
    if d["llm"] is not None:
        r["p1"] = A.p1_block(lost_ops=[], transport_lost=0, logical_writes=20)
        r["yield"] = A.yield_block(unit="conversation", scored=True,
                                   units=[{"retrievable": 3, "chars_in": 900, "chars": 1000}] * 2)
    r.update(over)
    return r


print("\n- arm_decl and blocked -")
check("a complete arm_decl builds", decl("mem0")["system"] == "mem0")
check("a missing field is refused", refused(lambda: A.arm_decl(**{k: v for k, v in decl("m").items() if k != "k"}),
                                            "missing ['k']"))
check("an unknown field is refused", refused(lambda: A.arm_decl(**decl("m"), extra=1), "unknown ['extra']"))
check("a bool where an integer belongs is refused", refused(lambda: decl("m", k=True), "wrong type"))
check("a config outside vendor-default / vendor-recommended / ours is refused", refused(lambda: decl("m", config="tuned"),
                                                                                         "config"))
check("llm without llm_transport is refused", refused(lambda: decl("m", llm_transport=None), "both set or both null"))
check("a non-deterministic arm with one run is refused (P4)", refused(lambda: decl("m", runs=1), "P4"))
check("now_rule other than wall-clock is refused (§5.4)", refused(lambda: decl("m", now_rule="benchmark"), "wall-clock"))
check("launch.env_names with a value is refused", refused(
    lambda: decl("m", launch={"env_names": ["KEY=x"], "cwd_rule": "u", "binary_sha256": "2" * 64}), "names only"))
check("store_persistence other than disk / memory is refused (Q25)", refused(lambda: decl("m", store_persistence="ram"),
                                                                             "Q25"))
check("blocked() takes a vocabulary value", A.blocked("needs-other-env:cloud-account") ==
      {"blocked": "needs-other-env:cloud-account"})
check("blocked() refuses a reason outside the vocabulary", refused(lambda: A.blocked("blocked:slow"), "vocabulary"))
check("owner-decision needs its date", refused(lambda: A.blocked("owner-decision:later"), "vocabulary")
      and A.blocked("owner-decision:2026-09-27 cloud") == {"blocked": "owner-decision:2026-09-27 cloud"})

print("\n- Q5: ollama_transport from the pacer's final-outcome record -")
pac = copy.deepcopy(PACER)
pac["failed_outcomes"] = {"by_status": {400: 2, 500: 1}, "by_exception_type": {"ReadTimeout": 1}, "gave_up": 1}
pac["failed_outcomes_llm"] = {"by_status": {503: 1}, "by_exception_type": {}, "gave_up": 2}
pac["bypass_calls"] = {"requests": 1, "aiohttp": 2}
ot = A.ollama_transport(pac, embed_at_cap=3, fallback_local=1, embed_models_seen=[D1, D1], degraded_recalls=2,
                        direct_calls=4)
check("failed_outcomes = by_status + by_exception_type + gave_up over embed calls + degraded recalls (2+1+1+1+2)",
      ot["failed_outcomes"] == 7, str(ot["failed_outcomes"]))
check("failed_outcomes_llm = the same sum over LLM calls (1+2)", ot["failed_outcomes_llm"] == 3)
check("bypass_calls sums the pacer's per-library counts; embed_at_cap, fallback_local and direct_calls as given",
      (ot["bypass_calls"], ot["embed_at_cap"], ot["fallback_local"], ot["direct_calls"]) == (3, 3, 1, 4))
check("embed_models_seen is the set of models the embed calls named (B2)", ot["embed_models_seen"] == [D1])
check("the pacer's own record is kept whole under detail", ot.get("detail") == pac)
for kw in ("embed_at_cap", "fallback_local", "embed_models_seen", "degraded_recalls", "direct_calls"):
    args = dict(embed_at_cap=0, fallback_local=0, embed_models_seen=[D1], degraded_recalls=0, direct_calls=0)
    args.pop(kw)
    try:
        A.ollama_transport(PACER, **args)
        ok = False
    except TypeError:
        ok = True
    check(f"ollama_transport cannot be built without {kw} - an unmeasured TB7 field is never 0 (R9)", ok)
for badm in ([""], ["  "], [None], [3]):
    check(f"M3: an embed model name {badm[0]!r} is refused (B2 names each call's model)",
          refused(lambda badm=badm: A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=badm,
                                                       degraded_recalls=0, direct_calls=0), "embed_models_seen"))
check("R-EMBED-PATH: direct_calls must be a measured count - None is refused, never 0",
      refused(lambda: A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1],
                                         degraded_recalls=0, direct_calls=None), "direct_calls"))
check("a pacer record without its failure dicts is refused",
      refused(lambda: A.ollama_transport({"calls": 1, "bypass_calls": {}}, embed_at_cap=0, fallback_local=0,
                                         embed_models_seen=[D1], degraded_recalls=0, direct_calls=0), "failure record"))

print("\n- cloud_transport and boundary -")
for k in ("thinking_calls", "fingerprints_seen", "tokens"):
    c = {x: v for x, v in CLOUD.items() if x != k}
    check(f"cloud_transport without {k} is refused", refused(lambda c=c: A.cloud_transport(c), "lacks"))
check("an incomplete witness is refused, never read as 0 (P0h)", refused(
    lambda: A.boundary_block(proxy=PROXY, witnesses=[{"kind": "egress", "complete": False, "hits": 0}, WITNESSES[1]]),
    "incomplete"))
check("a boundary with no fs witness is refused", refused(lambda: A.boundary_block(proxy=PROXY, witnesses=WITNESSES[:1]),
                                                          "no ['fs'] witness"))
check("a proxy record without owner_marker_hits is refused", refused(
    lambda: A.boundary_block(proxy={k: v for k, v in PROXY.items() if k != "owner_marker_hits"}, witnesses=WITNESSES),
    "owner_marker_hits"))
b = A.boundary_block(proxy={**PROXY, "canary_hits": 1}, witnesses=[{**WITNESSES[0], "hits": 2}, WITNESSES[1]])
check("boundary counts: canary from the proxy, egress from the witnesses", (b["canary_hits"], b["egress_hits"]) == (1, 2))

print("\n- K60/K61 cache records (B2) -")
BUILT = A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1], degraded_recalls=0, direct_calls=0)
cr = A.cache_record(path="stores/S4/r1/vec.json", sha256="4" * 64, built_commit=ANCHOR,
                    built_utc="2026-10-01T09:30:00+00:00", built_ollama_transport=BUILT, hits=50, misses=0)
check("cache_record carries path, sha256, hits, misses and the build's whole ollama_transport (embed_models_seen)",
      cr["built"]["ollama_transport"]["embed_models_seen"] == [D1] and (cr["hits"], cr["misses"]) == (50, 0))
check("a build record without embed_models_seen is refused", refused(lambda: A.cache_record(
    path="x", sha256="4" * 64, built_commit=ANCHOR, built_utc="2026-10-01T09:30:00+00:00",
    built_ollama_transport={k: v for k, v in BUILT.items() if k != "embed_models_seen"}, hits=1, misses=0), "embed_models_seen"))
check("a build that paid embed calls but names no model is refused", refused(lambda: A.cache_record(
    path="x", sha256="4" * 64, built_commit=ANCHOR, built_utc="2026-10-01T09:30:00+00:00",
    built_ollama_transport={**BUILT, "embed_models_seen": []}, hits=1, misses=0), "B2"))
check("a cache record at a commit that is not 40 hex is refused", refused(lambda: A.cache_record(
    path="x", sha256="4" * 64, built_commit="HEAD", built_utc="2026-10-01T09:30:00+00:00", built_ollama_transport=BUILT,
    hits=1, misses=0), "40-hex"))

print("\n- P1 bands -")
SO, TC, TR = ({"reason": "structured-output"}, {"reason": "tool-calling"}, {"reason": "transport"})
p = A.p1_block(lost_ops=[], transport_lost=0, logical_writes=100)
check("0 lost: share 0, no label", (p["lost_share"], p["label"]) == (0.0, ""))
check("2 of 100 (<= 2 %): no label", A.p1_block(lost_ops=[SO, SO], transport_lost=0, logical_writes=100)["label"] == "")
p5 = A.p1_block(lost_ops=[SO] * 3 + [TR] * 2, transport_lost=2, logical_writes=100)
check("5 of 100: labelled 'lossy-writer (5.0%)', transport_lost_share 0.02",
      (p5["label"], p5["transport_lost_share"]) == ("lossy-writer (5.0%)", 0.02), str(p5))
try:
    A.p1_block(lost_ops=[TC] * 9 + [SO] * 6, transport_lost=0, logical_writes=100)
    ex = None
except A.P1Exceeds as e:
    ex = e
check("15 of 100 (> 10 %): P1Exceeds names the dominant class, never a valid row",
      ex is not None and ex.dominant == "tool-calling", repr(ex))
check("a loss reason outside P1's list is refused",
      refused(lambda: A.p1_block(lost_ops=[{"reason": "other"}], transport_lost=0, logical_writes=100), "outside"))
check("M2: transport_lost greater than the lost operations is refused",
      refused(lambda: A.p1_block(lost_ops=[TR], transport_lost=2, logical_writes=100), "transport_lost"))

print("\n- P1 loss reasons and derived block classes (TB4.10 and M1 rulings) -")
PE_RESP = {"reason": "product-error", "response_seen": True}
PE_TOOL = {"reason": "product-error", "response_seen": True, "tool_call": True}
PE_NONE = {"reason": "product-error", "response_seen": False}
check("block_class: a product error on a completed response -> structured-output; on a tool call -> tool-calling; "
      "with no response -> transport",
      (A.block_class(PE_RESP), A.block_class(PE_TOOL), A.block_class(PE_NONE))
      == ("structured-output", "tool-calling", "transport"))
check("block_class: breaker -> transport; fallback_refused by its slug (content -> structured-output, transport -> "
      "transport)",
      A.block_class({"reason": "breaker"}) == "transport"
      and [A.block_class({"reason": "fallback_refused", "slug": s}) for s in ("truncated", "empty", "unparsable", "blocked")]
      == ["structured-output"] * 4
      and [A.block_class({"reason": "fallback_refused", "slug": s}) for s in ("http", "transport", "error")] == ["transport"] * 3)
check("block_class refuses missing evidence: a product error without response_seen, a fallback with an unknown slug",
      refused(lambda: A.block_class({"reason": "product-error"}), "response_seen")
      and refused(lambda: A.block_class({"reason": "fallback_refused", "slug": "cloud_dead"}), "cannot be derived"))
mix = [PE_RESP] * 5 + [PE_NONE] * 4 + [{"reason": "breaker"}] * 2 + [TC] * 1
pm = A.p1_block(lost_ops=mix, transport_lost=0, logical_writes=1000)
check("the p1 block publishes the loss reasons as counted and the block classes as derived",
      pm.get("reasons") == {"product-error": 9, "breaker": 2, "tool-calling": 1}
      and pm["classes"] == {"structured-output": 5, "transport": 6, "tool-calling": 1}, str((pm.get("reasons"), pm.get("classes"))))
try:
    A.p1_block(lost_ops=[PE_RESP] * 9 + [PE_NONE] * 5 + [TC] * 2, transport_lost=0, logical_writes=100)
    exm = None
except Exception as e:  # noqa: BLE001 - any other refusal is a named FAIL of this row, not a crash
    exm = e
check("M1: product errors dominate the reasons, and the block class is still one of the three (structured-output)",
      isinstance(exm, A.P1Exceeds) and exm.dominant == "structured-output" and exm.dominant in A.BLOCK_CLASSES, repr(exm))
check("dominant_class refuses a class outside the three", refused(lambda: A.dominant_class({"product-error": 3}),
                                                                  "outside"))

print("\n- K76 yield -")
y = A.yield_block(unit="conversation", scored=True,
                  units=[{"retrievable": 0, "chars_in": 100, "chars": 1000}, {"retrievable": 2, "chars_in": 5000,
                                                                              "chars": 1000},
                         {"retrievable": 1, "chars_in": 1000, "chars": 1000}])
check("retrievable_unit_share counts units with >= 1 retrievable item (2 of 3)",
      abs(y["retrievable_unit_share"] - 2 / 3) < 1e-12, str(y["retrievable_unit_share"]))
check("coverage is capped per unit at the unit's own characters: mean(0.1, 1.0, 1.0)", abs(y["coverage"] - 0.7) < 1e-12,
      str(y["coverage"]))
check("no label above 0.5", y["labels"] == [])
y05 = A.yield_block(unit="conversation", scored=True, units=[{"retrievable": 0, "chars_in": 500, "chars": 1000},
                                                             {"retrievable": 1, "chars_in": 500, "chars": 1000}])
check("no label at exactly 0.5 (the label is for < 0.5)", y05["labels"] == [], str(y05["labels"]))
y2 = A.yield_block(unit="row", scored=True, units=[{"retrievable": 0, "chars_in": 100, "chars": 1000}] * 3
                   + [{"retrievable": 1, "chars_in": 100, "chars": 1000}])
check("scored: 'writer-gated (25.0%)' and 'window (10.0%)'",
      y2["labels"] == ["writer-gated (25.0%)", "window (10.0%)"], str(y2["labels"]))
y3 = A.yield_block(unit="row", scored=False, units=[{"retrievable": 0, "chars_in": 1, "chars": 1000}])
check("a pilot (unscored) yield carries no label (K76: labels from scored runs only)", y3["labels"] == []
      and y3["scored"] is False)
check("an evaluation unit outside the list is refused", refused(lambda: A.yield_block(unit="session", scored=True,
                                                                                      units=y2 and [{}]), "unit"))

print("\n- files: run_record and build -")
with tempfile.TemporaryDirectory(prefix="v3art_") as td:
    d = Path(td)
    clock = Clock()
    s1 = A.run_record(d / "r1.json", "SX/b01/r1/mem0", {"arm": "mem0"}, commit=ANCHOR, dirty=False, now=clock)
    rec = json.loads((d / "r1.json").read_bytes())
    check("run_record stamps measured_at {commit, dirty, utc} and returns the file's sha256",
          rec["measured_at"] == {"commit": ANCHOR, "dirty": False, "utc": "2026-10-01T09:00:02.000000+00:00"}
          and s1 == hashlib.sha256((d / "r1.json").read_bytes()).hexdigest(), str(rec.get("measured_at")))
    check("run_record never overwrites an existing run file",
          _raises(lambda: A.run_record(d / "r1.json", "x", {}, commit=ANCHOR, dirty=False, now=clock), FileExistsError))
    check("a run record cannot carry its own measured_at", refused(lambda: A.run_record(
        d / "r2.json", "x", {"measured_at": {}}, commit=ANCHOR, dirty=False, now=clock), "writer's"))
    check("a run record refuses a commit that is not 40 hex", refused(lambda: A.run_record(
        d / "r3.json", "x", {}, commit="HEAD", dirty=False, now=clock), "40-hex"))

    sids = ["SX/b01/r1/mem0", "SX/b01/r2/mem0", "SX/b01/r1/nevertwice", "SX/b01/r2/nevertwice"]
    arms = {"mem0": row("mem0", sid=sids[:2]),
            "nevertwice": row("nevertwice", sid=sids[2:], decl={"config": "ours:v3-anchor"}),
            "letta": A.blocked("blocked:local-server"), "mem0-platform": A.blocked("needs-other-env:cloud-account")}
    rf = {s: {"path": f"runs/{s}.json", "sha256": "3" * 64} for s in sids}
    kw = dict(stand="SX", point="B", tier="product", arms=arms, brackets={"none": {}, "oracle": {}},
              input_manifest=MANIFEST, model_version={"response_model": "deepseek-v4-flash", "changelog_newest": "2026-09-10"},
              commit=ANCHOR, dirty=False, status_ids=sids, run_files=rf, now=clock)
    try:
        doc, err = A.build(**kw), None
    except Exception as e:  # noqa: BLE001  - a refused fixture is a named FAIL
        doc, err = None, repr(e)
    check("build: input_sha256 is the sha256 of the canonical input_manifest",
          doc is not None and doc["input_sha256"] == hashlib.sha256(json.dumps(
              MANIFEST, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(), str(err))
    check("build: measured_at {commit, dirty, utc} at assembly; run_files and status_ids kept",
          set(doc["measured_at"]) == {"commit", "dirty", "utc"} and doc["run_files"] == rf and doc["status_ids"] == sids)
    check("build refuses run_files that do not name exactly its status_ids",
          refused(lambda: A.build(**{**kw, "run_files": dict(list(rf.items())[:3])}), "exactly the status_ids"))
    check("build refuses brackets outside Point B product (Q7)", refused(lambda: A.build(
        **{**kw, "point": "K", "arms": {}}), "Point B"))
    check("build refuses a blocked arm that carries a number (P3: no numbers, never 0)",
          refused(lambda: A.build(**{**kw, "arms": {**arms, "letta": {"blocked": "blocked:local-server", "calls": 0}}}),
                  "no numbers"))
    bad_in = row("mem0", sid=sids[:2], decl={"input_sha256": "9" * 64})
    check("build refuses an arm fed another input than the artifact's",
          refused(lambda: A.build(**{**kw, "arms": {**arms, "mem0": bad_in}}), "was fed"))
    check("build refuses a sensitivity id other than the pre-declared all-0 row",
          refused(lambda: A.build(**{**kw, "sensitivity": "temperature-1"}), "sensitivity"))
    out = A.write(doc, d / "results")
    raw = out.read_bytes()
    check("write: <stand>_<point>_<tier>.json, LF, sorted keys, and never overwritten",
          out.name == "SX_B_product.json" and b"\r" not in raw and json.loads(raw) == doc
          and _raises(lambda: A.write(doc, d / "results"), FileExistsError))

print("\n- P0: each clause fires on its own trigger -")
CTX = A.P0Context(anchor=ANCHOR, anchor_utc=dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.timezone.utc), stand_units=20,
                  reconciliation_branches=frozenset({"a"}), list_sha256="e" * 64, dataset_sha256="d" * 64, expected_n=3,
                  m5_pass=True)
clean = row("mem0", sid=sids[:2])
check("the clean row raises no P0 flag", A.p0_flags(clean, CTX, "mem0") == [], str(A.p0_flags(clean, CTX, "mem0")))


def flags(mut, ctx=CTX, arm="mem0"):
    r = copy.deepcopy(clean)
    mut(r)
    try:
        return A.p0_flags(r, ctx, arm)
    except Exception as e:  # noqa: BLE001  - a crashing clause is a named FAIL of the row that reached it
        return [f"CRASH {e!r}"]


def has(fl: list[str], letter: str) -> bool:
    return any(x.startswith(f"P0{letter}:") for x in fl)


check("P0a: bypass_calls > 0", has(flags(lambda r: r["ollama_transport"].__setitem__("bypass_calls", 1)), "a"))
check("P0a (R-EMBED-PATH): direct_calls > 0 - an Ollama reached past the arm's proxy leg",
      has(flags(lambda r: r["ollama_transport"].__setitem__("direct_calls", 1)), "a"))
check("P0a: a failed embed outcome", has(flags(lambda r: r["ollama_transport"].__setitem__("failed_outcomes", 1)), "a"))
check("P0a: failed_outcomes_llm", has(flags(lambda r: r["ollama_transport"].__setitem__("failed_outcomes_llm", 1)), "a"))
check("P0a (R-TOOLS): an LLM call on the Ollama leg of an arm that declares NO LLM (fallback_local > 0) is flagged - p0b "
      "judges fallback_local for cloud arms only, so it was silent",
      any("declares no LLM" in x for x in flags(lambda r: (r["arm_decl"].__setitem__("llm", None),
                                                          r["ollama_transport"].__setitem__("fallback_local", 1)))))
check("... and a cloud arm's leg fallback stays p0b's (no second P0a flag)",
      not any("declares no LLM" in x for x in flags(lambda r: r["ollama_transport"].__setitem__("fallback_local", 1))))
check("P0a: needs Ollama, 0 paced calls, K61 not holding",
      has(flags(lambda r: r["ollama_transport"].__setitem__("calls", 0)), "a"))
check("... but 0 paced calls with K61 holding is not P0a",
      not has(flags(lambda r: r["ollama_transport"].__setitem__("calls", 0),
                    ctx=A.P0Context(**{**CTX.__dict__, "k61": {"mem0": True}})), "a"))
check("P0b: cloud_transport missing", has(flags(lambda r: r.pop("cloud_transport")), "b"))
check("P0b: a P0(b) counter > 0", has(flags(lambda r: r["cloud_transport"].__setitem__("thinking_calls", 1)), "b"))
check("P0b: two models seen", has(flags(lambda r: r["cloud_transport"].__setitem__("models_seen", ["a", "b"])), "b"))
check("P0b (R9): a product's background write after end_write", any(
    x == "P0b: background_writes > 0" for x in flags(lambda r: r["cloud_transport"].__setitem__("background_writes", 1))))
check("R9: background_writes is a P0(b) zero-tolerance counter the builder requires",
      "background_writes" in A.CLOUD_ZERO and refused(lambda: A.cloud_transport(
          {k: v for k, v in CLOUD.items() if k != "background_writes"}), "background_writes"))
check("P0b: a model event inside the artifact's blocks",
      has(A.p0_flags(clean, A.P0Context(**{**CTX.__dict__, "model_event_ids": frozenset({sids[0]})}), "mem0"), "b"))
check("P0c: a run whose own failures dropped 2 of 20 units (10 %)",
      has(flags(lambda r: r["runs"][0].__setitem__("units_dropped_own", 2)), "c"))
check("... 1 of 20 (5 %) is not P0c", not has(flags(lambda r: r["runs"][0].__setitem__("units_dropped_own", 1)), "c"))
check("P0d: a blocked arm carrying a number", has(A.p0_flags({"blocked": "blocked:local-server", "calls": 1}, CTX), "d"))
check("P0d: a blocked value outside the vocabulary", has(A.p0_flags({"blocked": "blocked:slow"}, CTX), "d"))
check("P0f: a requested arm with no contexts", has(flags(lambda r: r.__setitem__("questions", [])), "f"))
tctx = A.P0Context(**{**CTX.__dict__, "timing": True})
check("P0g: a timing row without a measured idle:true", has(A.p0_flags(clean, tctx, "mem0"), "g"))
check("... and not with {mode: observe, idle: true}",
      not has(flags(lambda r: r.__setitem__("machine_idle", {"mode": "observe", "idle": True}), ctx=tctx), "g"))
check("P0h: a boundary counter > 0", has(flags(lambda r: r["boundary"].__setitem__("owner_marker_hits", 1)), "h"))
check("P0h: a spawn without its environment-assertion record",
      has(A.p0_flags(clean, A.P0Context(**{**CTX.__dict__, "unasserted_spawns": frozenset({sids[1]})}), "mem0"), "h"))
qs = [{"qid": f"q{i}", "invalid": None} for i in range(100)]
check("P0i: 2 of 100 verdicts invalid (> 1 %)",
      has(flags(lambda r: r.__setitem__("questions", [{**q, "invalid": "cap" if i < 2 else None}
                                                       for i, q in enumerate(qs)])), "i"))
check("... 1 of 100 is not P0i", not has(flags(lambda r: r.__setitem__(
    "questions", [{**q, "invalid": "cap" if i < 1 else None} for i, q in enumerate(qs)])), "i"))
check("P0j: a reconciliation branch outside the slot", has(flags(lambda r: r["reconciliation"].__setitem__("branch", "c")),
                                                            "j"))
# K87 checks 1-2 inside the branch (the auditor's B-DUP ruling: each branch its own predicate, a row per predicate)
ABC = A.P0Context(**{**CTX.__dict__, "reconciliation_branches": frozenset({"a", "b", "c"})})
REC_B = {"proxy_calls": 1000, "proxy_logical_calls": 1000, "adapter_calls": None, "product_logical_calls": 990,
         "tokens_delta_pct": -1.0, "serverlog_delta": 0, "branch": "b"}


def rec(over: dict, ctx=ABC, base=None) -> list[str]:
    return flags(lambda r: r.__setitem__("reconciliation", {**(base or r["reconciliation"]), **over}), ctx=ctx)


check("K87 (a): the clean row - adapter exact, logical <= HTTP - is not P0j", not has(rec({}), "j"), str(rec({})))
check("P0j (a): the adapter counts one call fewer than the proxy (exact, no tolerance)",
      any("adapter" in x for x in rec({"adapter_calls": 11})), str(rec({"adapter_calls": 11})))
check("P0j (a): no adapter counter on an adapter branch", any("adapter" in x for x in rec({"adapter_calls": None})))
check("P0j (a): logical calls above HTTP calls", any("logical" in x for x in rec({"product_logical_calls": 13})))
check("P0j (a), (b): no HTTP call count from the proxy", any("HTTP call count" in x for x in rec({"proxy_calls": None}))
      and any("HTTP call count" in x for x in rec({"proxy_calls": None}, base=REC_B)))
check("P0j (a), (b): a negative HTTP call count is not a count (K6)", any("HTTP call count" in x for x in rec({"proxy_calls": -1}))
      and any("HTTP call count" in x for x in rec({"proxy_calls": -1}, base=REC_B)))
check("P0j (b): a bool is not a tokens delta - True is not 1 % (K7)",
      any("product-side counter" in x for x in rec({"tokens_delta_pct": True}, base=REC_B)))
check("P0j (b): NaN is not a tokens delta (K9)",
      any("product-side counter" in x for x in rec({"tokens_delta_pct": float("nan")}, base=REC_B)))
check("Q-K87-1 (a) is exact in tokens too: a delta of 0.3 % against the adapter branch is P0j",
      any("tokens" in x and "branch a" in x for x in rec({"tokens_delta_pct": 0.3})), str(rec({"tokens_delta_pct": 0.3})))
check("Q-K87-1 ... no tokens delta on an adapter branch is not P0j (the adapter counts calls, not tokens)",
      not has(rec({"tokens_delta_pct": None}), "j"), str(rec({"tokens_delta_pct": None})))
check("Q-K87-1 ... a bool or NaN delta on an adapter branch is P0j, never read as 0",
      has(rec({"tokens_delta_pct": False}), "j") and has(rec({"tokens_delta_pct": float("nan")}), "j"))
check("K87 (b): calls and tokens at exactly -1 % are inside the tolerance (the boundary is inclusive)",
      not has(rec({}, base=REC_B), "j"), str(rec({}, base=REC_B)))
check("K87 (b): ... and tokens at exactly +1 % too", not has(rec({"tokens_delta_pct": 1.0}, base=REC_B), "j"))
check("R-K87-1 (b): 3 % transport retries with equal logical calls are not P0j (the product's calls are logical)",
      not has(rec({"proxy_calls": 1030, "proxy_logical_calls": 1000, "product_logical_calls": 1000}, base=REC_B), "j"),
      str(rec({"proxy_calls": 1030, "proxy_logical_calls": 1000, "product_logical_calls": 1000}, base=REC_B)))
check("R-K87-1 (b): logical calls 2 % apart are P0j, whatever the HTTP count",
      any("logical calls" in x and "beyond 1 %" in x for x in rec({"proxy_calls": 1030, "proxy_logical_calls": 1000,
                                                                    "product_logical_calls": 980}, base=REC_B)))
check("R-K87-1 (b): no logical call count from the proxy on a product branch",
      any("proxy's logical call count" in x for x in rec({"proxy_logical_calls": None}, base=REC_B)))
check("P0j (b): calls beyond 1 % of the proxy's (989 of 1000)",
      any("calls" in x for x in rec({"product_logical_calls": 989}, base=REC_B)))
check("P0j (b): tokens beyond 1 % (-1.01 %)", any("tokens" in x for x in rec({"tokens_delta_pct": -1.01}, base=REC_B)))
check("P0j (b): tokens beyond 1 % (+1.01 %)", any("tokens" in x for x in rec({"tokens_delta_pct": 1.01}, base=REC_B)))
check("P0j (b): logical calls above HTTP calls, even within 1 %",
      any("logical" in x for x in rec({"product_logical_calls": 1005}, base=REC_B)))
check("P0j (b): no product-side counter on a product branch",
      any("product" in x for x in rec({"product_logical_calls": None}, base=REC_B))
      and any("product" in x for x in rec({"tokens_delta_pct": None}, base=REC_B)))
REC_C = {"proxy_calls": 12, "adapter_calls": None, "product_logical_calls": None, "tokens_delta_pct": None,
         "serverlog_delta": 0, "branch": "c"}
check("K87 (c): single-witness stands when the context says its A/B passed and every footprint is > 0",
      not has(rec({}, ctx=A.P0Context(**{**ABC.__dict__, "single_witness_ok": {"mem0": True}}), base=REC_C), "j"))
check("P0j (c): single-witness without single_witness_ok for the arm",
      any("single-witness" in x for x in rec({}, base=REC_C))
      and any("single-witness" in x for x in rec({}, ctx=A.P0Context(**{**ABC.__dict__, "single_witness_ok": {"mem0": False}}),
                                                  base=REC_C)))
check("P0j (c): another arm's single_witness_ok does not stand for this one",
      has(rec({}, ctx=A.P0Context(**{**ABC.__dict__, "single_witness_ok": {"zep": True}}), base=REC_C), "j"))

rctx = A.P0Context(**{**CTX.__dict__, "windows": {s: (dt.datetime(2026, 10, 1, 8, 30, tzinfo=dt.timezone.utc),
                                                     dt.datetime(2026, 10, 1, 9, 0, 1, tzinfo=dt.timezone.utc))
                                                 for s in sids}})
check("the clean artifact raises no root flag", A.p0_root_flags(doc, rctx) == [], str(A.p0_root_flags(doc, rctx)))


def root(mut, ctx=rctx):
    dd = copy.deepcopy(doc)
    mut(dd)
    return A.p0_root_flags(dd, ctx)


check("P0c: a list sha256 that is not FREEZE-V3's", has(root(lambda x: x["input_manifest"].__setitem__("list_sha256", "0")),
                                                         "c"))
check("P0c: a pinned file with another sha256", has(root(lambda x: x["input_manifest"].__setitem__("dataset_sha256", "0")),
                                                     "c"))
check("P0c: a unit dropped for one arm but not for all",
      has(root(lambda x: x["arms"]["mem0"].__setitem__("units_dropped", ["u3"])), "c"))
check("P0d: m5 is not PASS", has(A.p0_root_flags(doc, A.P0Context(**{**rctx.__dict__, "m5_pass": False})), "d"))
check("P0e: measured_at is not the anchor", has(root(lambda x: x["measured_at"].__setitem__("commit", "b" * 40)), "e"))
check("P0e: dirty", has(root(lambda x: x["measured_at"].__setitem__("dirty", True)), "e"))
check("P0e: utc before the anchor (no run window in play)", has(root(lambda x: x["measured_at"].__setitem__(
    "utc", "2026-10-01T07:00:00+00:00"), ctx=CTX), "e"))
check("P0e: assembled before the last END of its runs", has(root(lambda x: x["measured_at"].__setitem__(
    "utc", "2026-10-01T08:59:00+00:00")), "e"))
check("P0f: a scored n that differs from the questions asked",
      has(root(lambda x: x["arms"]["mem0"].__setitem__("questions", [{"qid": "q0"}])), "f"))

print("\n- the auditor's m5 v3 and m2_v3, where present -")
M5P, M2P = ROOT / ".loop" / "m5_check.py", ROOT / ".loop" / "m2_v3.py"
FREEZE = {"arms": {s: {"version": "1.0", "python": "3.12.10", "temperature": 0.2} for s in ("mem0", "nevertwice")}
          | {s: {"version": "1.0", "python": "3.12.10"} for s in ("nevertwice-ranker", "bm25-floor")},
          "symmetry": {"symmetry.write_date_carriage": "own-field", "reader.template_sha256": "f" * 64},
          "sensitivity_rows": [{"id": "temperature-0", "overrides": {"llm_params.temperature": 0}}],
          "datasets": {"SX": {"sha256": "d" * 64}, "S6": {"sha256": "d" * 64}}, "lists": {"SX": {"sha256": "e" * 64}},
          "d1_tag": {"tag": {"name": D1}}}
if not M5P.exists():
    skip("m5 v3 PASSes F-A1..F-A3 and FAILs each asymmetry by name",
         "the auditor's m5 lives in .loop, outside the repository, by design (R12)")
else:
    M5 = _load("v3_m5_for_artifact", M5P)
    ctx5 = {"freeze": FREEZE}
    ret_arms = {
        "nevertwice-ranker": row("nevertwice-ranker", sid=["SX/b01/r1/nevertwice-ranker"], decl=dict(
            llm=None, llm_transport=None, tier="retrieval", llm_params={}, config="ours:v3-anchor", deterministic=True,
            runs=1)),
        "bm25-floor": row("bm25-floor", sid=["SX/b01/r1/bm25-floor"], decl=dict(
            llm=None, llm_transport=None, embedder=None, embeds_via_ollama=False, tier="retrieval", llm_params={},
            deterministic=True, runs=1))}
    with tempfile.TemporaryDirectory(prefix="v3art_m5_") as td:
        d = Path(td)
        clock = Clock()
        f_a1 = A.build(**{**kw, "now": clock})
        f_a2 = A.build(stand="SX", point="B", tier="retrieval", arms=ret_arms, brackets=None, input_manifest=MANIFEST,
                       model_version=kw["model_version"], commit=ANCHOR, dirty=False,
                       status_ids=["SX/b01/r1/nevertwice-ranker", "SX/b01/r1/bm25-floor"],
                       run_files={s: {"path": f"runs/{s}.json", "sha256": "3" * 64}
                                  for s in ["SX/b01/r1/nevertwice-ranker", "SX/b01/r1/bm25-floor"]}, now=clock)
        t0 = {"temperature": 0, "max_tokens": 4096, "thinking_route": "documented"}
        f_a3 = A.build(stand="S6", point="B", tier="product", sensitivity="temperature-0",
                       arms={"mem0": row("mem0", sid=["S6/b01/r1/mem0"], decl={"llm_params": t0}),
                             "nevertwice": row("nevertwice", sid=["S6/b01/r1/nevertwice"],
                                               decl={"llm_params": t0, "config": "ours:v3-anchor"}),
                             "cognee": A.blocked("competitor-lacks-capability:temperature")},
                       brackets=None, input_manifest=MANIFEST, model_version=kw["model_version"], commit=ANCHOR,
                       dirty=False, status_ids=["S6/b01/r1/mem0", "S6/b01/r1/nevertwice"],
                       run_files={s: {"path": f"runs/{s}.json", "sha256": "3" * 64}
                                  for s in ["S6/b01/r1/mem0", "S6/b01/r1/nevertwice"]}, now=clock)

        def m5(docx, name="x.json") -> list[str]:
            p = d / name
            p.write_bytes(A.render(docx))
            return M5.check_file(p, ctx5)

        for nm, fx in (("F-A1 product B", f_a1), ("F-A2 retrieval", f_a2), ("F-A3 temperature-0", f_a3)):
            v = m5(fx)
            check(f"m5 v3 --freeze PASSes {nm}", v == [], str(v[:3]))
        pf = d / "F-A1.json"
        pf.write_bytes(A.render(f_a1))
        fz = d / "freeze.json"
        fz.write_text(json.dumps(FREEZE), encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(M5P), "--freeze", str(fz), str(pf)], capture_output=True,
                           text=True, cwd=td)
        check("the m5 CLI exits 0 and prints M5: PASS on F-A1", r.returncode == 0 and "M5: PASS" in r.stdout, r.stdout[-300:])

        def asym(label: str, mut, want: str, base=f_a1) -> None:
            dd = copy.deepcopy(base)
            mut(dd)
            v = m5(dd)
            check(f"m5 FAILs by name: {label}", any(want in x for x in v), str(v[:3]))

        mem = lambda dd: dd["arms"]["mem0"]  # noqa: E731
        asym("llm differs", lambda dd: mem(dd)["arm_decl"].__setitem__("llm", "other"), "parity: llm differs")
        asym("embedder differs", lambda dd: mem(dd)["arm_decl"].__setitem__("embedder", "bge-m3:latest"),
             "parity: embedder differs")
        asym("k differs", lambda dd: mem(dd)["arm_decl"].__setitem__("k", 10), "parity: k differs")
        asym("budget differs", lambda dd: mem(dd)["arm_decl"].__setitem__("context_budget_tokens", 9000),
             "parity: context_budget_tokens differs")
        asym("input_sha256 differs", lambda dd: mem(dd)["arm_decl"].__setitem__("input_sha256", "9" * 64),
             "parity: input_sha256 differs")
        asym("a blocked arm carries numbers", lambda dd: dd["arms"]["letta"].__setitem__("calls", 3),
             "a blocked arm carries numbers")
        asym("a cloud P0(b) counter is absent", lambda dd: mem(dd)["cloud_transport"].pop("thinking_calls"),
             "cloud_transport lacks thinking_calls")
        asym("a cloud P0(b) counter is non-zero", lambda dd: mem(dd)["cloud_transport"].__setitem__("fallback_local", 1),
             "cloud_transport.fallback_local=1")
        asym("models_seen names 2", lambda dd: mem(dd)["cloud_transport"].__setitem__("models_seen", ["a", "b"]),
             "models_seen must name exactly one model")
        asym("the temperature differs from the freeze",
             lambda dd: mem(dd)["arm_decl"]["llm_params"].__setitem__("temperature", 0.7), "write temperature")
        asym("a symmetry knob departs", lambda dd: mem(dd)["arm_decl"]["reader"].__setitem__("template_sha256", "9" * 64),
             "symmetry knob reader.template_sha256")
        asym("the sensitivity row is not pre-declared", lambda dd: dd.__setitem__("sensitivity", "temperature-9"),
             "is not pre-declared", base=f_a3)
        asym("a boundary counter is 1", lambda dd: mem(dd)["boundary"].__setitem__("fs_hits", 1), "boundary.fs_hits=1")
        asym("p1 at 5 % with no label", lambda dd: mem(dd)["p1"].update(lost_share=0.05, label=""),
             "needs the label 'lossy-writer")
        asym("a yield label is missing", lambda dd: mem(dd)["yield"].update(retrievable_unit_share=0.3, labels=[]),
             "needs the label 'writer-gated'")
        asym("a yield label is extra", lambda dd: mem(dd)["yield"].update(labels=["window (40.0%)"]),
             "must not carry the label 'window'")
        asym("an arm that embedded with another model than the freeze's D1 tag (B2)",
             lambda dd: mem(dd)["ollama_transport"].__setitem__("embed_models_seen", ["bge-m3:latest"]), "not the D1 tag")
        asym("an arm that names two embed models (B2)",
             lambda dd: mem(dd)["ollama_transport"].__setitem__("embed_models_seen", [D1, "bge-m3:latest"]),
             "must name exactly one model")
        cached = copy.deepcopy(f_a1)
        mem(cached)["ollama_transport"] = A.ollama_transport({**PACER, "calls": 0}, embed_at_cap=0, fallback_local=0,
                                                             embed_models_seen=[], degraded_recalls=0, direct_calls=0)
        mem(cached)["caches"] = [cr]
        v = m5(cached)
        check("m5 PASSes a fully cache-served arm (K61) whose cache build names the D1 tag", v == [], str(v[:3]))
        asym("a cache-served arm whose cache build names no embed model (B2)",
             lambda dd: mem(dd)["caches"][0]["built"]["ollama_transport"].__setitem__("embed_models_seen", []),
             "must name exactly one model", base=cached)
        asym("a pacer dict where the integer total belongs (Q5, B1)",
             lambda dd: mem(dd)["ollama_transport"].__setitem__("failed_outcomes_llm", {"by_status": {}}),
             "is a pacer detail dict")

if not M2P.exists():
    skip("m2_v3.check_artifacts accepts F-A1 against its STATUS and run records",
         "the auditor's m2_v3 lives in .loop, outside the repository, by design (R12)")
else:
    M2 = _load("v3_m2_for_artifact", M2P)
    with tempfile.TemporaryDirectory(prefix="v3art_m2_") as td:
        d = Path(td)
        clock = Clock()
        log = SL.StatusLog(d / "STATUS", now=clock, local_tz=dt.timezone.utc)
        log.campaign_start(anchor=ANCHOR, prereg="b" * 64, freeze="c" * 64)
        log.stand("SX", "START", model="deepseek-v4-flash", changelog="2026-09-10", order=1)
        log.block_start("SX", "b01", units=["u1"], arm_order=["mem0", "nevertwice"], seed=1)   # D4: seed 1 gives this order
        files, rfs = {}, {}
        for s in sids:
            _stand, block, run, arm = s.split("/")
            log.start("SX", block, run, arm, pid=10, tag="scored")
            rel = f"runs/{s}.json"
            sha = A.run_record(d / rel, s, {"arm": arm}, commit=ANCHOR, dirty=False, now=clock)
            log.end(s, rc=0, wall_s=1, units=1, out=rel)
            files[rel] = (d / rel).read_bytes()
            rfs[s] = {"path": rel, "sha256": sha}
        log.block_end("SX", "b01")
        log.stand("SX", "END", model="deepseek-v4-flash", changelog="2026-09-10")
        art = A.build(**{**kw, "run_files": rfs, "now": clock})
        log.campaign_end()
        evs, bad = M2.parse((d / "STATUS").read_text(encoding="utf-8"))
        probs = M2.check_artifacts({"F-A1": art}, evs, anchor=ANCHOR, freeze=FREEZE, run_files=files)
        check("m2_v3.check_artifacts accepts F-A1: run records inside START..END, named by out=, sha-matched; the "
              "aggregate after the last END; F1 dataset and list", not bad and probs == [], str((bad[:2], probs[:3])))
        late = A.build(**{**kw, "run_files": rfs, "now": lambda: dt.datetime(2026, 10, 1, 9, 0, 1,
                                                                            tzinfo=dt.timezone.utc)})
        probs2 = M2.check_artifacts({"early": late}, evs, anchor=ANCHOR, freeze=FREEZE, run_files=files)
        check("... and names an aggregate stamped before the last END of its runs",
              any("precedes the last END" in x for x in probs2), str(probs2[:2]))

print(f"\nv3 artifact: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
sys.exit(1 if FAILED else 0)
