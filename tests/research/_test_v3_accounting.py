#!/usr/bin/env python3
"""PREREG-V3 TB4.10a' (A6; the auditor's B-DUP ruling O-a): research/v3/accounting.py computes, from the recording
proxy's logs, the INPUTS of the gated research/v3/artifact.py builders - it builds and judges nothing itself (rev1 §2.3,
§4.3, §4.5, §6; Q3). Synthetic records in the proxy's own shape:

* load_proxy: LF records only; a line that does not parse, or a last line cut short, is a named problem;
* attribute: run and unit split at the FIRST dot; the phase from the port role and the stage (reader - answer, j3 -
  judge, write port - write in the write stage, read in the question stage); anything else refuses;
* the taxonomy: ok, recovered within 30 min - to the success's t1 (MA15) - late_recovered after it (the
  M-TAX-recovered-31min row), never; per (unit, request key) - the §4.5 key is sha256(body || arm) with no unit in it,
  and one session body is written in several units (B-ACC1: the same body lost in one unit and written in another is
  that unit's loss); within a (unit, key) each success closes an episode, and failures after the last success are an
  episode of their own - never (B-ACC1b: a body written twice in a unit, lost the second time); recovery counts from
  the episode's FIRST failure (MB3), in t0 order whatever the log's order (MB2); duplicate_body_groups and
  ambiguous_recoveries are published; key_question is keyed by (unit, key) - a map by the bare key refuses (MB7); a
  forwarded record with no request key, t0 or t1, or a line that is not UTF-8, refuses by name;
* cloud_counters: transport_lost is a write-phase key that never succeeded, failed_outcomes a read/answer key that never
  succeeded on a question not dropped (M-TAX-dropped-in-failed); a never-succeeded key with no question refuses; the
  counts (refused model_mismatch, tool violations, thinking only on /v1 and /anthropic, models_seen, fingerprints per
  endpoint class, straddled units, empty content, json_invalid only where JSON was asked, capped, upstream errors,
  client_abandoned, fallback_local from the Ollama leg, tokens by phase with the cache split - M-TOKENS-phase-swap,
  M-TOKENS-cache-hit-excluded); upstream_errors = a 5xx or no answer at all (upstream_error, F-ACC3); straddled units
  per endpoint class, /anthropic by its model (F-ACC4); another arm's or run's calls, and the scheduler port's, never
  count; cloud_bypass is measured, never defaulted;
* the counters pass artifact.cloud_transport, and artifact's P0b names each zero-tolerance count by the name
  artifact.CLOUD_ZERO gives it; Q-50-1's late_recovered and late_recovered_units (order_sensitive on S6/S6L) and
  transport_lost_units reach the built artifact, and m5 v3 does not reject them (a named SKIP without .loop);
* proxy_boundary_inputs: canary, owner-marker and ancestor-canary hits summed over the arm-run's call records, refused
  ones included (M-BOUNDARY-canary-dropped); a record without the field, or a bool in it (MA12), refuses - never 0;
  the catcher's refused egress by host - the arm's, across the log (F-ACC5: catcher records carry no run); witness_inputs: the launch check record as the egress and fs witnesses artifact.boundary_block takes - an
  incomplete check stays incomplete, so the builder refuses it;
* TB4.10b' lost_operations (Q12, M1): each lost logical write in artifact's LOSS_REASONS with its evidence (a product
  error names response_seen and tool_call; the engine's no-call operations are breaker or fallback_refused with its
  slug); a transport loss is a failed attempt of its
  (unit, key)'s trailing never episode (B-ACC1b); artifact.p1_block bands them and raises P1Exceeds by the derived dominant class; yield_inputs (K76): the
  stand's evaluation unit only (M-K76-unit-question), artifact.yield_block caps coverage per unit and labels only
  scored runs; cache_inputs (K60, K61): artifact.cache_record's arguments, the verdicts m5 --anchor's;
  reconciliation_inputs (K87): the product port's HTTP calls and tokens, the deltas and product retries - the verdict
  is artifact's P0j;
* end to end: logs on disk -> load_proxy -> accounting -> artifact blocks -> build -> m5 v3 check_file and P0 clean;
* M-DUP: no copy of an artifact builder or of an m5 check lives here.

    python tests/research/_test_v3_accounting.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import inspect
import shutil
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


AC = _load("v3_accounting", ROOT / "research" / "v3" / "accounting.py")
A = _load("v3_artifact_for_accounting", ROOT / "research" / "v3" / "artifact.py")
M5P = ROOT / ".loop" / "m5_check.py"
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


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except AC.AccountingError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not an AccountingError: {type(e).__name__}: {e}"


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except A.ArtifactRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def t(minutes: float) -> str:
    stamp = dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=round(minutes * 60))
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def call(key, *, arm="mem0", unit="r1.u1", role="write", stage="write", status=200, t0=0.0, t1=None, usage=None,
         endpoint="v1", fp="fp_a", model="deepseek-v4-flash", **kw):
    c = {"v": 1, "arm": arm, "unit": unit, "port_role": role, "stage": stage, "block": "b1", "endpoint": endpoint,
         "request_key": key, "status": status, "complete": status is not None, "client_abandoned": False,
         "refused": None, "t0": t(t0), "t1": t(t1 if t1 is not None else t0 + 0.1), "response_model": model,
         "system_fingerprint": fp if endpoint == "v1" else None, "response_format": None, "json_ok": None,
         "content_empty": False, "finish_reason": "stop", "thinking": False, "thinking_injected": 0,
         "tool_violation": False, "canary_hits": 0, "ancestor_canary_hits": 0, "owner_marker_hits": 0,
         "usage": usage or {"prompt": 100, "completion": 10, "cache_hit": 60, "cache_miss": 40, "reasoning": 0}}
    c.update(kw)
    return c


print("- load_proxy: LF records, named problems -")
TMP = Path(tempfile.mkdtemp(prefix="v3acct_"))
try:
    (TMP / "calls.jsonl").write_bytes(b'{"a": 1}\n\nnot json\n[1]\n{"b": 2}')
    (TMP / "flags.jsonl").write_bytes(b'{"kind": "canary"}\n')
    (TMP / "catcher.jsonl").write_bytes(b'{"host": "x\xff"}\n{"host": "ok"}\n')
    log = AC.load_proxy(TMP)
    check("records read, blank lines skipped, an unterminated last line never a record", log.calls == [{"a": 1}] and log.flags == [{"kind": "canary"}], str(log.calls))
    check("a line that is not JSON, a line that is not an object and a last line without LF are named problems",
          any("calls.jsonl:3" in p and "not a JSON record" in p for p in log.problems)
          and any("calls.jsonl:4" in p and "not a JSON object" in p for p in log.problems)
          and any("no LF" in p for p in log.problems), str(log.problems))
    check("F-ACC2: a line that is not UTF-8 is a named problem, never decoded with replacement characters",
          log.catcher == [{"host": "ok"}] and any("catcher.jsonl:1" in p and "not UTF-8" in p for p in log.problems),
          str(log.problems))
    check("an absent log is empty, not a problem", log.ollama == [])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n- attribute: the first dot, the phase from role and stage -")
check("Q3: run and unit split at the FIRST dot", AC.split_unit("r1.u.with.dots") == ("r1", "u.with.dots"))
check("a prefix without a dot refuses", "not <run>.<unit>" in err(lambda: AC.split_unit("r1u1")))
for role, stage, phase in (("reader", "questions", "answer"), ("j3", "judges", "judge"), ("write", "write", "write"),
                           ("write", "questions", "read")):
    check(f"role {role} in stage {stage} is the {phase} phase", AC.attribute(call("k", role=role, stage=stage))[3] == phase)
check("a write-port call in an unknown stage refuses", "has no phase" in err(lambda: AC.attribute(call("k", stage="lunch"))))
check("an unknown port role refuses", "has no phase" in err(lambda: AC.attribute(call("k", role="catcher"))))

print("\n- the taxonomy: ok, recovered within 30 min, late after it, never -")
calls = [call("ok1", t0=1),
         call("rec", status=500, t0=2), call("rec", t0=20),                        # failed, then ok 18 min later
         call("late", status=None, t0=3), call("late", t0=34.5),                   # ok after 31.5 min
         call("edge", status=502, t0=4), call("edge", t0=33.9, t1=34.0),           # ok exactly 30 min after
         call("lostw", status=500, t0=5), call("lostw", status=500, t0=6),         # write, never
         call("aband", t0=7, client_abandoned=True), call("aband", t0=8)]          # abandoned, then ok
keys = AC.classify_keys(calls)
check("classes: ok, recovered, late_recovered, recovered at exactly 30 min, never, and an abandoned attempt recovered",
      [keys.get(("u1", k), {}).get("classes") for k in ("ok1", "rec", "late", "edge", "lostw", "aband")]
      == [["ok"], ["recovered"], ["late_recovered"], ["recovered"], ["never"], ["recovered"]],
      str({k: v.get("classes") for k, v in keys.items()}))
check("a key whose calls span two port roles refuses - the same body on the write and the reader port is a record "
      "inconsistency (Q1 keeps AccountingError for those)",
      "spans the phases" in err(lambda: AC.classify_keys([call("x"), call("x", role="reader")])))
sp_calls = [call("sp", status=500, t0=1), call("sp", stage="questions", t0=5)]
try:
    ksp = AC.classify_keys(sp_calls).get(("u1", "sp"), {})
    ct_sp = AC.cloud_counters(sp_calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
except Exception as e:  # noqa: BLE001 - the rows FAIL by name
    ksp = ct_sp = {"error": f"{type(e).__name__}: {e}"}
check("Q1 (F6): a write-stage body retried after stage('questions') belongs to the write phase by its key's first "
      "occurrence - flagged spans_phases, one key, never a refusal", ksp.get("phase") == "write"
      and ksp.get("spans_phases") == ["read", "write"] and ksp.get("attempts") == 2
      and ksp.get("classes") == ["recovered"], str(ksp))
check("Q1 (F6): ... and in the counters it is one recovered key whose tokens are the write phase's, spans_phases 1",
      ct_sp.get("spans_phases") == 1 and ct_sp.get("transport_recovered") == 1 and ct_sp.get("transport_lost") == 0
      and (ct_sp.get("tokens") or {}).get("write", {}).get("prompt") == 200
      and (ct_sp.get("tokens") or {}).get("read", {}).get("prompt") == 0, str(ct_sp)[:300])
check("MA15: recovery is measured to the success's t1 - a failure at 0:00 answered 29:59..30:05 is late_recovered",
      AC.classify_keys([call("m15", status=500, t0=0), call("m15", t0=29 + 59 / 60, t1=30 + 5 / 60)]).get(
          ("u1", "m15"), {}).get("classes") == ["late_recovered"])
check("MB3: recovery counts from the episode's FIRST failure - failures at 0:00 and 0:20, success ending 0:40, is late",
      AC.classify_keys([call("m3", status=500, t0=0), call("m3", status=500, t0=20), call("m3", t0=39, t1=40)]).get(
          ("u1", "m3"), {}).get("classes") == ["late_recovered"])
check("MB2: attempts are taken in t0 order whatever the log's order - a success logged before its earlier failure",
      AC.classify_keys([call("m2", t0=10), call("m2", status=500, t0=5)]).get(("u1", "m2"), {}).get("classes")
      == ["recovered"])
check("F-ACC2: a forwarded record without a request key refuses",
      "request_key" in err(lambda: AC.classify_keys([call(None)])))
check("F-ACC2: a record without t0 refuses - never read as 0",
      "t0" in err(lambda: AC.classify_keys([{k: v for k, v in call("n0").items() if k != "t0"}])))
check("F-ACC2: a success without t1 refuses", "t1" in err(lambda: AC.classify_keys(
    [call("n1", status=500, t0=1), {k: v for k, v in call("n1", t0=2).items() if k != "t1"}])))

print("\n- B-ACC1: one request key, two units (the §4.5 key has no unit; a session body repeats across units) -")
dup1 = [call("dup", unit="r1.u1", status=503, t0=1), call("dup", unit="r1.u1", status=503, t0=2),
        call("dup", unit="r1.u2", t0=6)]
c1 = AC.cloud_counters(dup1, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("u1 lost it for good, u2 wrote the same body 5 min later: u1's transport_lost, nothing recovered",
      c1["transport_lost"] == 1 and c1["transport_lost_units"] == ["u1"] and c1["transport_recovered"] == 0, str(c1))
dup2 = [call("dup", unit="r1.u1", t0=1), call("dup", unit="r1.u2", status=503, t0=2), call("dup", unit="r1.u2", status=503, t0=3)]
c2 = AC.cloud_counters(dup2, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("u1 wrote it, then u2 lost the same body: u2's loss is seen", c2["transport_lost"] == 1
      and c2["transport_lost_units"] == ["u2"], str(c2))
dup3 = [call("dup", unit="r1.u1", status=503, t0=1), call("dup", unit="r1.u2", t0=121)]
c3 = AC.cloud_counters(dup3, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("u1 lost it, u2 wrote it two hours later: u1's loss, and no late_recovered pinned on u1",
      c3["transport_lost_units"] == ["u1"] and c3["late_recovered"] == 0 and c3["late_recovered_units"] == [], str(c3))
k3 = AC.classify_keys(dup3)
check("the classes are per (unit, key)", {k: v.get("classes") for k, v in k3.items()} == {("u1", "dup"): ["never"],
                                                                                         ("u2", "dup"): ["ok"]}, str(k3))

print("\n- B-ACC1b: one request key twice in ONE unit (a turn repeated in a session, a session repeated in a haystack) -")
e1 = AC.cloud_counters([call("rep", t0=1), call("rep", status=503, t0=2), call("rep", status=503, t0=3)],
                       arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("[success, failure for good] in one unit: the second write is a lost operation (transport_lost 1)",
      e1.get("transport_lost") == 1 and e1.get("transport_lost_units") == ["u1"] and e1.get("duplicate_body_groups") == 0, str(e1))
e2 = AC.cloud_counters([call("rep", t0=1), call("rep", t0=5)], arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("[success, success]: two episodes, both ok - duplicate_body_groups 1, nothing lost or ambiguous",
      e2.get("duplicate_body_groups") == 1 and e2.get("ambiguous_recoveries") == 0 and e2.get("transport_lost") == 0, str(e2))
e3 = AC.cloud_counters([call("rep", t0=1), call("rep", status=500, t0=5), call("rep", t0=6)], arm="mem0", run="r1",
                       stand="S1", cloud_bypass=0, background_writes=0)
check("[success, failure, success]: the failure series before the second success is recovered AND ambiguous "
      "(it may have been either write)", e3.get("transport_recovered") == 1 and e3.get("ambiguous_recoveries") == 1
      and e3.get("duplicate_body_groups") == 1 and e3.get("transport_lost") == 0, str(e3))
e4 = AC.cloud_counters([call("rq", role="reader", stage="questions", t0=40),
                        call("rq", role="reader", stage="questions", status=502, t0=41)],
                       arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0, key_question={("u1", "rq"): "q3"})
check("a reader key answered once, then failed for good in the same unit: a failed outcome of its own",
      e4.get("failed_outcomes") == 1, str(e4.get("failed_outcomes")))
e5 = AC.cloud_counters([call("rr", role="reader", stage="questions", t0=40), call("rr", role="reader", stage="questions", t0=45)],
                       arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("duplicate_body_groups counts write groups only - a reader key answered twice is not one", e5.get("duplicate_body_groups")
      == 0, str(e5.get("duplicate_body_groups")))
check("the episodes of a group, in t0 order", AC.classify_keys([call("rep", t0=1), call("rep", status=503, t0=2)]).get(
    ("u1", "rep"), {}).get("classes") == ["ok", "never"])

print("\n- cloud_counters -")
read_calls = [call("r_ok", role="reader", stage="questions", t0=40),
              call("r_lost", role="reader", stage="questions", status=500, t0=41),
              call("r_drop", role="reader", stage="questions", status=500, t0=42),
              call("q_read", stage="questions", t0=43, usage={"prompt": 7, "completion": 3, "cache_hit": 5, "cache_miss": 2,
                                                              "reasoning": 0})]
others = [call("o1", arm="zep", t0=1), call("o2", unit="r2.u1", t0=1),
          call("canary1", arm="scheduler", unit=None, role="scheduler", stage=None, t0=2)]   # R4: the scheduler port
refused_calls = [dict(call("mm", t0=44), refused="model_mismatch", status=None, complete=False)]
misc = [call("th", t0=45, thinking=True), call("th2", t0=45.5, thinking=True, endpoint="balance"),
        call("fp", t0=46, unit="r1.u2", fp="fp_b"), call("fp2", t0=46.5, unit="r1.u2", fp="fp_a"),
        call("cap", t0=47, finish_reason="length"), call("empty", t0=48, content_empty=True),
        call("jbad", t0=49, response_format="json_object", json_ok=False),
        call("jfree", t0=49.5, response_format=None, json_ok=False), call("tv", t0=50, tool_violation=True),
        call("ant", t0=51, endpoint="anthropic", model="deepseek-v4-flash")]
ollama = [{"arm": "mem0", "unit": "r1.u1", "fallback_local": True}, {"arm": "mem0", "unit": "r2.u1", "fallback_local": True},
          {"arm": "zep", "unit": "r1.u1", "fallback_local": True}, {"arm": "mem0", "unit": "r1.u1", "fallback_local": False}]
allc = calls + read_calls + others + refused_calls + misc
KQ = {("u1", "r_lost"): "q7", ("u1", "r_drop"): "q9"}
ct = AC.cloud_counters(allc, arm="mem0", run="r1", stand="S1", ollama=ollama,
                       key_question=KQ, dropped={"q9"}, cloud_bypass=0, background_writes=0,
                       incident_units=["u3", "u3"])
check("another arm's, another run's and the scheduler port's calls never count; refused calls are not calls",
      ct["calls"] == len(calls) + len(read_calls) + len(misc), str(ct["calls"]))
check("transport_lost: the write-phase key that never succeeded, with its unit",
      ct["transport_lost"] == 1 and ct["transport_lost_units"] == ["u1"])
check("failed_outcomes: the answer key that never succeeded on a question NOT dropped; the dropped one not counted",
      ct["failed_outcomes"] == 1, str(ct["failed_outcomes"]))
check("recovered and late_recovered counted apart", ct["transport_recovered"] == 3 and ct["late_recovered"] == 1,
      f"{ct['transport_recovered']} {ct['late_recovered']}")
check("MB7: a question map by the bare request key refuses - it would join questions across units",
      "has no question" in err(lambda: AC.cloud_counters(allc, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0,
                                                         key_question={"r_lost": "q7", "r_drop": "q9"})))
check("a never-succeeded read key with no question refuses",
      "has no question" in err(lambda: AC.cloud_counters(allc, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0,
                                                         key_question={("u1", "r_lost"): "q7"})))
check("Q-50-1 a late success is listed per unit - on S1 without the order flag",
      ct["late_recovered_units"] == [{"stand": "S1", "unit": "u1", "order_sensitive": False}], str(ct["late_recovered_units"]))
ct6 = AC.cloud_counters(allc, arm="mem0", run="r1", stand="S6", ollama=ollama, cloud_bypass=0, background_writes=0,
                        key_question=KQ, dropped={"q9"})
check("Q-50-1 ... on S6 (FactConsolidation: the order is what is measured) with order_sensitive, and still not lost",
      ct6["late_recovered_units"] == [{"stand": "S6", "unit": "u1", "order_sensitive": True}] and ct6["transport_lost"] == 1)
check("the order-sensitive stands are S6 and S6L", AC.ORDER_SENSITIVE_STANDS == ("S6", "S6L"))
check("model_mismatch from the proxy's refusals, tool violations, thinking only on /v1 and /anthropic",
      ct.get("model_mismatch") == 1 and ct.get("tool_violation") == 1 and ct.get("thinking_calls") == 1)
check("fallback_local from the arm's own run on the Ollama leg", ct["fallback_local"] == 1)
check("models_seen, fingerprints per endpoint class (/anthropic by model), straddled units",
      ct["models_seen"] == ["deepseek-v4-flash"] and ct["fingerprints_seen"] == {"anthropic": ["deepseek-v4-flash"],
                                                                                "v1": ["fp_a", "fp_b"]}
      and ct["straddled_units"] == ["u2"], str(ct["fingerprints_seen"]))
check("capped, empty content, json_invalid only where JSON was asked, upstream errors, client_abandoned",
      ct["capped"] == 1 and ct["empty_content"] == 1 and ct["json_invalid"] == 1 and ct["upstream_errors"] == 6
      and ct["client_abandoned"] == 1, str({k: ct[k] for k in ("capped", "empty_content", "json_invalid",
                                                              "upstream_errors", "client_abandoned")}))
n_write = len(calls) + len(misc)
check("tokens by phase, each with its cache split (write / read / answer never swapped)",
      ct["tokens"]["write"] == {"prompt": 100 * n_write, "completion": 10 * n_write, "cache_hit": 60 * n_write,
                                "cache_miss": 40 * n_write}
      and ct["tokens"]["read"] == {"prompt": 7, "completion": 3, "cache_hit": 5, "cache_miss": 2}
      and ct["tokens"]["answer"]["prompt"] == 300, str(ct["tokens"]))
check("incident units as given, once each", ct["incident_units"] == ["u3"])
ue = AC.cloud_counters([call("ue", status=None, complete=False, upstream_error="ConnectRefused", t0=1), call("ue", t0=2),
                        call("u5", status=503, t0=3), call("u5", t0=4), call("u6", status=429, t0=5), call("u6", t0=6)],
                       arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("F-ACC3: upstream_errors = a 5xx answer or no answer at all (the proxy's upstream_error); a 429 is neither",
      ue["upstream_errors"] == 2, str(ue["upstream_errors"]))
an = AC.cloud_counters([call("a1", endpoint="anthropic", model="m-1", unit="r1.u7", t0=1),
                        call("a2", endpoint="anthropic", model="m-2", unit="r1.u7", t0=2),
                        call("a3", endpoint="anthropic", model="m-1", unit="r1.u8", t0=3),
                        call("a4", fp="fp_z", unit="r1.u8", t0=4)], arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)
check("F-ACC4: a unit whose /anthropic calls ran under two models is straddled; one model per class is not",
      an["straddled_units"] == ["u7"], str(an["straddled_units"]))
sig = inspect.signature(AC.cloud_counters).parameters
check("cloud_bypass is a measured input with no default (R9: never defaulted to 0)",
      "cloud_bypass" in sig and sig["cloud_bypass"].default is inspect.Parameter.empty)
check("background_writes is a measured input with no default either (the auditor's R9 ruling)",
      "background_writes" in sig and sig["background_writes"].default is inspect.Parameter.empty
      and ct.get("background_writes") == 0)

print("\n- R9: a product's write after its adapter's end_write, outside every question operation of the unit -")
bw_calls = [call("w1", t0=1), call("w2", t0=2), call("bg1", t0=5), call("rd", stage="questions", t0=10),
            call("bg2", stage="questions", t0=12), call("ans", role="reader", stage="questions", t0=13),
            call("o1", unit="r1.u2", t0=6), call("x1", arm="zep", t0=6), call("r2", unit="r2.u1", t0=6)]
bw = AC.background_writes(bw_calls, arm="mem0", run="r1", end_write_at={"u1": t(3)},
                          read_windows={"u1": [(t(9.5), t(10.5))]})
check("a write-port call after end_write and outside every read window is background - before end_write, inside a "
      "read window, on the reader's port, another arm's or run's, or a unit with no end_write stamp is not",
      bw == {"count": 2, "units": ["u1"], "statuses": {"200": 2}}, str(bw))
bgf = [call("w1", t0=1), call("bgf", stage="questions", status=500, t0=10),
       call("bgf", stage="questions", status=None, upstream_error="ProtocolError", t0=10.2)]
try:
    ct_bgf = AC.cloud_counters(bgf, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=1)
    bw_f = AC.background_writes(bgf, arm="mem0", run="r1", end_write_at={"u1": t(3)},
                                read_windows={"u1": [(t(9.5), t(10.5))]})
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    ct_bgf = bw_f = {"error": f"{type(e).__name__}: {e}"}
check("Q1 (C21): a questions-stage write-port call that never succeeds is R9 background activity - never a 'has no "
      "question' refusal, never a failed outcome; background_writes counts its key once, inside a read window or not, "
      "with its last status", ct_bgf.get("failed_outcomes") == 0
      and bw_f == {"count": 1, "units": ["u1"], "statuses": {"ProtocolError": 1}}, f"{ct_bgf.get('error')} {bw_f}")
try:
    bw_o = AC.background_writes([call("w1", t0=1), call("bgo", stage="questions", status=503, t0=12)], arm="mem0",
                                run="r1", end_write_at={"u1": t(3)}, read_windows={"u1": [(t(9.5), t(10.5))]})
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    bw_o = {"error": f"{type(e).__name__}: {e}"}
check("Q1 (C21): a questions-stage write-port call after end_write, outside every read window, that never succeeded is "
      "counted ONCE - by the after-end_write rule, never again as a never-succeeded key",
      bw_o == {"count": 1, "units": ["u1"], "statuses": {"503": 1}}, str(bw_o))
check("the bound: a call exactly at the end_write stamp is not after it; one exactly at either edge of a read window is "
      "inside", AC.background_writes([call("e", t0=3), call("f", stage="questions", t0=10.5),
                                     call("g", stage="questions", t0=9.5)], arm="mem0", run="r1",
                           end_write_at={"u1": t(3)}, read_windows={"u1": [(t(9.5), t(10.5))]})["count"] == 0)
halt_calls = [call("w1", t0=1), call("qh", unit="r1.u9", role="reader", stage="questions", status=402, t0=10),
              call("qf", role="reader", stage="questions", status=503, t0=11)]
halt_kq = {("u9", "qh"): "u9:q1", ("u1", "qf"): "u1:q1"}
try:
    ct_h = AC.cloud_counters(halt_calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0,
                             key_question=halt_kq, unrecorded_units=["u9"])
    ct_h0 = AC.cloud_counters(halt_calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0,
                              key_question=halt_kq)
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    ct_h = ct_h0 = {"error": f"{type(e).__name__}: {e}"}
check("B-REASK-HALT (the auditor's condition 2): a question key of a unit the stand never recorded - its block ABORTed "
      "by the halt, the re-ask never made - that never succeeded is no failed outcome; unrecorded_keys counts it, and a "
      "recorded unit's never key is still one; unrecorded_units names the unit", ct_h.get("failed_outcomes") == 1
      and ct_h.get("unrecorded_keys") == 1 and ct_h.get("unrecorded_units") == ["u9"]
      and ct_h0.get("failed_outcomes") == 2 and ct_h0.get("unrecorded_keys") == 0 and ct_h0.get("unrecorded_units") == [],
      f"{ct_h} | {ct_h0}"[:400])
ct_bw = AC.cloud_counters(bw_calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=bw["count"])
check("the count reaches the artifact, and artifact's P0b names it",
      "P0b: background_writes > 0" in A.p0b({"arm_decl": {"llm_transport": "cloud:deepseek"},
                                            "cloud_transport": A.cloud_transport(ct_bw)}, A.P0Context(), "mem0"))
check("product_retries is left to the reconciliation unless given", ct["product_retries"] is None
      and AC.cloud_counters(calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0, product_retries=2)["product_retries"] == 2)

print("\n- the counters through artifact.cloud_transport, judged by artifact's P0b -")
try:
    blk, why = A.cloud_transport(ct), ""
except A.ArtifactRefused as e:
    blk, why = None, str(e)
check("artifact.cloud_transport takes the counters: every P0(b) and 'also written' field is there", blk is not None, why)
cloud_decl = {"llm_transport": "cloud:deepseek"}
p0b = A.p0b({"arm_decl": cloud_decl, "cloud_transport": blk or {}}, A.P0Context(), "mem0") if blk else []
check("artifact's P0b names each zero-tolerance count accounting measured (the names are artifact.CLOUD_ZERO's)",
      all(f"P0b: {k} > 0" in p0b for k in ("failed_outcomes", "fallback_local", "model_mismatch", "thinking_calls",
                                          "tool_violation")), str(p0b))
clean = [call("c1", t0=1), call("c2", t0=2, unit="r1.u2"), call("c3", role="reader", stage="questions", t0=40)]
cblk = A.cloud_transport(AC.cloud_counters(clean, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0))
check("a clean arm-run's counters raise no P0b", A.p0b({"arm_decl": cloud_decl, "cloud_transport": cblk}, A.P0Context(),
                                                       "mem0") == [])
check("a measured cloud bypass reaches P0b", "P0b: cloud_bypass > 0" in A.p0b(
    {"arm_decl": cloud_decl, "cloud_transport": A.cloud_transport(
        AC.cloud_counters(clean, arm="mem0", run="r1", stand="S1", cloud_bypass=1, background_writes=0))}, A.P0Context(), "mem0"))

print("\n- proxy_boundary_inputs and witness_inputs, built by artifact.boundary_block -")
bcalls = [call("b1", t0=1), call("b2", t0=2, ancestor_canary_hits=2),
          dict(call("b3", t0=3, canary_hits=1), refused="canary", status=None, complete=False),   # refused, still a hit
          call("b4", t0=4, arm="zep", canary_hits=5), call("b5", t0=5, unit="r2.u1", owner_marker_hits=1),
          call("b6", t0=6, arm="scheduler", unit=None, role="scheduler", stage=None)]
catcher = [{"arm": "mem0", "host": "api.openai.com", "tunnelled": False}, {"arm": "mem0", "host": "api.openai.com", "tunnelled": False},
           {"arm": "mem0", "host": "x.org", "tunnelled": True}, {"arm": "zep", "host": "y.org", "tunnelled": False}]
pb = AC.proxy_boundary_inputs(bcalls, catcher, arm="mem0", run="r1", ollama=[])
check("the arm-run's hits summed over its call records, a refused call's included; another arm's and run's never",
      pb.get("canary_hits") == 1 and pb.get("owner_marker_hits") == 0 and pb.get("ancestor_canary_hits") == 2, str(pb))
check("the catcher's refused egress by host, this arm's only", pb["egress_attempts"] == {"api.openai.com": 2})
check("MA12: a bool in a hit field refuses - True is not a count",
      "canary_hits" in err(lambda: AC.proxy_boundary_inputs([call("b", canary_hits=True)], [], arm="mem0", run="r1",
                                                            ollama=[])))
check("a call record without a hit field refuses - never 0",
      "canary_hits" in err(lambda: AC.proxy_boundary_inputs([{k: v for k, v in call("b").items() if k != "canary_hits"}],
                                                            [], arm="mem0", run="r1", ollama=[])))
oll = [{"arm": "mem0", "unit": "r1.u1", "path": "/api/pull", "error": "refused:path", "status": None},
       {"arm": "mem0", "unit": "r1.u1", "path": "/api/embed", "error": None, "status": 200, "is_embed": True},
       {"arm": "mem0", "unit": "r1.u2", "path": "/api/%70ull", "error": "refused:encoded-target", "status": None},
       {"arm": "mem0", "unit": "r2.u1", "path": "/api/pull", "error": "refused:path", "status": None},
       {"arm": "zep", "unit": "r1.u1", "path": "/api/pull", "error": "refused:path", "status": None},
       {"arm": "mem0", "unit": None, "path": "/api/delete", "error": "refused:model-store", "status": None},
       {"arm": "mem0", "unit": "r1.u3", "path": "/api/embed", "error": "ProtocolError", "status": 200}]
try:
    pbo = AC.proxy_boundary_inputs([], [], arm="mem0", run="r1", ollama=oll)
    pb0 = AC.proxy_boundary_inputs([], [], arm="mem0", run="r1", ollama=[])
except Exception as e:  # noqa: BLE001 - the rows FAIL by name
    pbo = pb0 = {"error": f"{type(e).__name__}: {e}"}
check("B-OLM-VIS: the Ollama leg's refusals of the arm-run are counted - refused:path, refused:encoded-target, and one "
      "with no unit prefix (the arm's, as a catcher record is) - never another arm's or run's, an answered call or a "
      "failed one", pbo.get("ollama_refused") == 3, str(pbo))
check("B-OLM-VIS: an arm-run the leg refused nothing is 0 - never 'not measured'", pb0.get("ollama_refused") == 0, str(pb0))
oll_q4 = [{"arm": "mem0", "unit": "r1.u1", "error": "refused:canary", "status": None, "canary_hits": 1,
           "ancestor_canary_hits": 0, "owner_marker_hits": 0},
          {"arm": "mem0", "unit": "r1.u2", "error": None, "status": 200, "canary_hits": 0, "ancestor_canary_hits": 1,
           "owner_marker_hits": 0},
          {"arm": "mem0", "unit": "r1.u3", "error": "refused:owner_marker", "status": None, "canary_hits": 0,
           "ancestor_canary_hits": 0, "owner_marker_hits": 1},
          {"arm": "zep", "unit": "r1.u1", "error": "refused:canary", "status": None, "canary_hits": 1}]
try:
    pb4 = AC.proxy_boundary_inputs([], [], arm="mem0", run="r1", ollama=oll_q4)
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    pb4 = {"error": f"{type(e).__name__}: {e}"}
check("Q4: the leg's scan hits are the arm-run's P0h counts too - a refused canary, an owner marker, an ancestor hit on "
      "a call it forwarded (another arm's never) - and a refused:canary or refused:owner_marker is the product's leak, "
      "never ollama_refused: one event, counted once",
      (pb4.get("canary_hits"), pb4.get("owner_marker_hits"), pb4.get("ancestor_canary_hits"), pb4.get("ollama_refused"))
      == (1, 1, 1, 0), str(pb4))
CHECK_OK = {"check_id": "S1.b01", "complete": True, "native": {"hits": 0, "complete": True},
            "containers": [None, {"hits": 1, "complete": True}], "fs": {"fs_hits": 0, "changed_labels": []}}
w = AC.witness_inputs(CHECK_OK)
check("witness_inputs: the native and each container egress witness, and the fs witness",
      w == [{"kind": "egress", "complete": True, "hits": 0}, {"kind": "egress", "complete": True, "hits": 1},
            {"kind": "fs", "complete": True, "hits": 0}], str(w))
bb = A.boundary_block(proxy=pb, witnesses=w)
check("artifact.boundary_block builds from them: container egress counted, the ancestor hits and egress published",
      bb["egress_hits"] == 1 and bb["canary_hits"] == 1 and bb["ancestor_canary_hits"] == 2
      and bb["egress_attempts"] == {"api.openai.com": 2}, str(bb))
check("... and artifact's P0h names the canary hit and the egress hit", {"P0h: canary_hits > 0", "P0h: egress_hits > 0"}
      <= set(A.p0h({"boundary": bb}, A.P0Context(), "mem0")))
check("an incomplete check stays incomplete, so the builder refuses it (never an unmeasured 0)",
      refused(lambda: A.boundary_block(proxy=pb, witnesses=AC.witness_inputs({**CHECK_OK, "complete": False})), "incomplete"))
check("an fs witness that measured nothing is refused by the builder",
      refused(lambda: A.boundary_block(proxy=pb, witnesses=AC.witness_inputs(
          {**CHECK_OK, "fs": {"fs_hits": None, "complete": False}})), "incomplete"))
check("no native witness: no egress witness at all, and the builder refuses", refused(
    lambda: A.boundary_block(proxy=pb, witnesses=AC.witness_inputs({**CHECK_OK, "native": None, "containers": []})),
    "no ['egress'] witness"))

print("\n- Q-50-1's fields reach the built artifact; m5 v3 does not reject them -")
ANCHOR = "a1" * 20
D1 = "nvt3-bge-m3-d1:latest"
MANIFEST = {"dataset_sha256": "d" * 64, "list_sha256": "e" * 64, "split": "scored", "prefix": 10}
PACER = {"calls": 40, "bypass_calls": {"requests": 0, "aiohttp": 0},
         "failed_outcomes": {"by_status": {}, "by_exception_type": {}, "gave_up": 0},
         "failed_outcomes_llm": {"by_status": {}, "by_exception_type": {}, "gave_up": 0}, "llm_retries": 0,
         "mode": "observe"}                      # the real attach() names its mode (B-PACER-REC)


def decl(system: str) -> dict:
    return A.arm_decl(
        system=system, version="1.0", python="3.12.10", config="vendor-default", llm="deepseek-flash",
        llm_transport="cloud:deepseek", embedder=D1, k=200, context_budget_tokens=7000, runs=2, deterministic=False,
        write_granularity="per-session", input_sha256=A.sha256_of(MANIFEST), embeds_via_ollama=True, tier="product",
        point="B", llm_params={"temperature": 0.2, "max_tokens": 4096, "thinking_route": "documented"},
        reader={"tag": "deepseek-flash", "template_sha256": "f" * 64},
        judges={j: {"tag": f"{j.lower()}-tag", "digest": "0" * 64} for j in ("J1", "J2", "J3")}, now_rule="wall-clock",
        date_route="field:date", renderer={"name": "api.format_note", "sha256": "1" * 64}, threshold="n/a",
        namespace="vault per unit", tools_allowed=[], launch={"env_names": ["PATH"], "cwd_rule": "unit dir",
                                                           "binary_sha256": "2" * 64},
        deviations=[], symmetry={"write_date_carriage": "own-field"}, store_persistence="disk")


late_log = [call("w1", t0=1), call("w2", t0=2, unit="r1.u2"),
            call("wl", t0=3, status=500), call("wl", t0=40),                      # a write that landed 37 min late
            call("wx", t0=4, unit="r1.u2", status=500), call("wx", t0=5, unit="r1.u2", status=502),   # a lost write
            call("rd", role="reader", stage="questions", t0=45)]


def arm_row(system: str, stand: str) -> dict:
    lg = [dict(c, arm=system) for c in late_log]
    return {"arm_decl": decl(system), "runs": [{"status_id": f"{stand}/b01/r1/{system}", "units_dropped_own": 0}],
            "cloud_transport": A.cloud_transport(AC.cloud_counters(lg, arm=system, run="r1", stand=stand, cloud_bypass=0, background_writes=0)),
            "boundary": A.boundary_block(proxy=AC.proxy_boundary_inputs(lg, [], arm=system, run="r1", ollama=[]),
                                         witnesses=AC.witness_inputs({**CHECK_OK, "containers": []})),
            "ollama_transport": A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1],
                                                   degraded_recalls=0, direct_calls=0),
            "p1": A.p1_block(lost_ops=[{"reason": "transport"}], transport_lost=1, logical_writes=100),
            "yield": A.yield_block(unit="row", scored=True, units=[{"retrievable": 2, "chars_in": 900, "chars": 1000}] * 2),
            "questions": [{"qid": "q0", "invalid": None}], "units_dropped": []}


sids = ["S6/b01/r1/mem0", "S6/b01/r1/zep"]
doc = A.build(stand="S6", point="B", tier="product", arms={"mem0": arm_row("mem0", "S6"), "zep": arm_row("zep", "S6")},
              brackets=None, input_manifest=MANIFEST, model_version={"response_model": "deepseek-v4-flash",
                                                                      "changelog_newest": "2026-09-10"},
              commit=ANCHOR, dirty=False, status_ids=sids,
              run_files={s: {"path": f"runs/{s}.json", "sha256": "3" * 64} for s in sids})
mct = doc["arms"]["mem0"]["cloud_transport"]
check("late_recovered, late_recovered_units (order_sensitive on S6) and transport_lost_units are in the built artifact",
      mct["late_recovered"] == 1 and mct["late_recovered_units"] == [{"stand": "S6", "unit": "u1", "order_sensitive": True}]
      and mct["transport_lost"] == 1 and mct["transport_lost_units"] == ["u2"], str({k: mct.get(k) for k in (
          "late_recovered", "late_recovered_units", "transport_lost_units")}))
if not M5P.exists():
    skip("m5 v3 check_file PASSes the artifact carrying them", "the auditor's m5 lives in .loop, outside the repository (R12)")
else:
    M5 = _load("v3_m5_for_accounting", M5P)
    with tempfile.TemporaryDirectory(prefix="v3acct_m5_") as td:
        p = Path(td) / "S6_B_product.json"
        p.write_bytes(A.render(doc))
        v = M5.check_file(p, {})
        check("m5 v3 check_file PASSes the artifact carrying them", v == [], str(v[:3]))

print("\n- TB4.10b' lost_operations (Q12, M1): the join of logical writes and calls, in artifact's reasons -")
J = {"response_format": "json_object", "json_ok": True}
pc = [call("k1", unit="r1.u1", t0=1, **J),                                          # op1: fine
      call("k2", unit="r1.u1", t0=5.5, tools_offered=["core_memory_append"], **J),   # op2: the product threw after a reply
      call("k3", unit="r1.u1", status=500, t0=11), call("k3", unit="r1.u1", status=500, t0=11.5),   # op3: lost key
      call("k4", unit="r1.u1", t0=21, content_empty=True, response_format="json_object", json_ok=False),  # op4
      call("k5a", unit="r1.u1", t0=31, finish_reason="length", **J), call("k5b", unit="r1.u1", t0=32, **J),  # op5
      call("k6", unit="r1.u1", t0=41, tools_offered=["core_memory_append"], parse_ok=False),     # op6: tool args
      call("k7", unit="r1.u1", t0=51, **J),                                         # op7: a valid empty extraction
      call("k8x", unit="r1.u2", status=500, t0=61), call("k8", unit="r1.u1", t0=61.5, **J),   # op8: other unit's loss
      call("k9", unit="r1.u1", status=503, t0=71)]                                   # op9: the product threw, no reply
ops = [{"op_id": "op1", "unit": "u1", "t0": t(0.5), "t1": t(2)},
       {"op_id": "op2", "unit": "u1", "t0": t(5), "t1": t(6), "error": "RuntimeError: letta step failed"},
       {"op_id": "op3", "unit": "u1", "t0": t(10.5), "t1": t(12)}, {"op_id": "op4", "unit": "u1", "t0": t(20.5), "t1": t(22)},
       {"op_id": "op5", "unit": "u1", "t0": t(30.5), "t1": t(33)}, {"op_id": "op6", "unit": "u1", "t0": t(40.5), "t1": t(42)},
       {"op_id": "op7", "unit": "u1", "t0": t(50.5), "t1": t(52)}, {"op_id": "op8", "unit": "u1", "t0": t(60.5), "t1": t(62)},
       {"op_id": "op9", "unit": "u1", "t0": t(70.5), "t1": t(72), "error": "ConnectError"}]
NOCALL = [{"op_id": "n1", "unit": "u1", "reason": "breaker"},
          {"op_id": "n2", "unit": "u1", "reason": "fallback_refused", "slug": "truncated"}]
lo = AC.lost_operations(ops, pc, arm="mem0", run="r1", no_call_ops=NOCALL)
by_id = {x["op_id"]: x for x in lo["lost_ops"]}
check("lost: a product error, a transport_lost key in the window, an empty last JSON call, unparsable tool arguments, "
      "and the engine's no-call operations - each by artifact's LOSS_REASONS",
      {k: v["reason"] for k, v in by_id.items()} == {"op2": "product-error", "op3": "transport", "op4": "structured-output",
                                                      "op6": "tool-calling", "op9": "product-error", "n1": "breaker",
                                                      "n2": "fallback_refused"}, str({k: v["reason"] for k, v in by_id.items()}))
check("a cut JSON call with a later success in the window is recovered; a valid empty extraction is not lost; another "
      "unit's lost key in the same minutes is not this operation's", lo["logical_writes"] == 11 and len(lo["lost_ops"]) == 7,
      str(lo["logical_writes"]))
check("M1 evidence: a product error names whether the proxy saw a completed response, and whether it was a tool call",
      by_id["op2"]["response_seen"] is True and by_id["op2"]["tool_call"] is True
      and by_id["op9"]["response_seen"] is False and by_id["op9"]["tool_call"] is False, str(by_id["op2"]))
check("transport_lost counts the operations that ended with a transport_lost key (rev1 P1)", lo["transport_lost"] == 1)
check("every lost operation's block class derives in artifact (no reason outside LOSS_REASONS, no missing evidence)",
      [A.block_class(x) for x in lo["lost_ops"]] and all(x["reason"] in A.LOSS_REASONS for x in lo["lost_ops"]))
try:
    A.p1_block(**lo)
    exc = None
except A.P1Exceeds as e:
    exc = e
check("artifact.p1_block takes them: 7 of 11 is over 10 %, P1Exceeds by the dominant class transport (op3, op9 with no "
      "reply, the breaker)", exc is not None and exc.dominant == "transport", repr(exc))
many = [{"op_id": f"o{i}", "unit": "u9", "t0": t(80), "t1": t(81)} for i in range(95)]
band = A.p1_block(**AC.lost_operations(many, [], arm="mem0", run="r1", no_call_ops=[
    {"op_id": f"b{i}", "unit": "u9", "reason": "breaker"} for i in range(5)]))
check("... and 5 of 100 is labelled by artifact's band", band["label"] == "lossy-writer (5.0%)" and band["lost"] == 5,
      str(band))
check("M-P1-proxy-alone: no logical operations refuses - never the proxy's counts alone",
      "never the proxy" in err(lambda: AC.lost_operations([], pc, arm="mem0", run="r1")))
for why, kw in (("empty", {"content_empty": True}), ("cut", {"finish_reason": "length"}),
                ("unparsable", {"json_ok": False}), ("failed", {"status": 500})):
    one = AC.lost_operations([{"op_id": "w", "unit": "u5", "t0": t(90), "t1": t(91)}],
                             [call("kw", unit="r1.u5", t0=90.5, **{**J, **kw}), call("kw", unit="r1.u5", t0=95, **J)],
                             arm="mem0", run="r1")
    check(f"the last JSON call {why}: structured-output, why={why}",
          [(x["reason"], x.get("why")) for x in one["lost_ops"]] == [("structured-output", why)], str(one["lost_ops"]))
ep = [call("g", unit="r1.u6", t0=100, **J), call("g", unit="r1.u6", status=503, t0=105, **J),
      call("g", unit="r1.u6", status=503, t0=106, **J),
      call("h", unit="r1.u6", status=503, t0=110, **J), call("h", unit="r1.u6", t0=111, **J),
      call("h", unit="r1.u6", status=503, t0=120, **J),
      call("m", unit="r1.u6", t0=130, **J), call("m", unit="r1.u6", status=503, t0=131, **J),
      call("m", unit="r1.u6", t0=132, **J), call("m", unit="r1.u6", status=503, t0=140, **J)]
eo = [{"op_id": "A", "unit": "u6", "t0": t(99.5), "t1": t(100.5)}, {"op_id": "B", "unit": "u6", "t0": t(104.5), "t1": t(107)},
      {"op_id": "C", "unit": "u6", "t0": t(109.5), "t1": t(111.5)}, {"op_id": "D", "unit": "u6", "t0": t(119.5), "t1": t(121)},
      {"op_id": "E", "unit": "u6", "t0": t(130.5), "t1": t(132.5)}, {"op_id": "F", "unit": "u6", "t0": t(139.5), "t1": t(141)}]
el = AC.lost_operations(eo, ep, arm="mem0", run="r1")
check("B-ACC1b in P1: the second write of a body that failed for good is lost by transport, the first is not; a "
      "failure that a later success of the same key closed is not a transport loss, the failure after it is",
      [(x["op_id"], x["reason"]) for x in el["lost_ops"]] == [("B", "transport"), ("D", "transport"), ("F", "transport")]
      and el["transport_lost"] == 3, str([(x["op_id"], x["reason"]) for x in el["lost_ops"]]))
check("a no-call operation with a reason outside the engine's two refuses",
      "no-call" in err(lambda: AC.lost_operations(ops, pc, arm="mem0", run="r1",
                                                  no_call_ops=[{"op_id": "x", "reason": "slow"}])))
print("\n- B-LO: lost_operations reads only the arm-run's own write port, and its classes are its own -")
lo1 = [call("K", unit="r1.u1", status=503, t0=200, **J), call("K", unit="r1.u1", status=503, t0=201, **J),
       call("K", unit="r2.u1", t0=202, **J)]
lop = [{"op_id": "w", "unit": "u1", "t0": t(199.5), "t1": t(201.5)}]
l1 = AC.lost_operations(lop, lo1, arm="mem0", run="r1")
check("B-LO1: r1 lost K for good while r2 wrote the same body - r1's operation is lost by transport, as cloud_counters "
      "says", [x["reason"] for x in l1["lost_ops"]] == ["transport"]
      and AC.cloud_counters(lo1, arm="mem0", run="r1", stand="S1", cloud_bypass=0, background_writes=0)["transport_lost"] == 1,
      str(l1["lost_ops"]))
lo2 = [call("M", unit="r1.u1", t0=300, **J), call("Z", arm="zep", unit="r1.u1", t0=300.5, content_empty=True,
                                                   response_format="json_object", json_ok=False),
       call("Z2", arm="zep", unit="r1.u1", t0=310.5, **J)]
l2 = AC.lost_operations([{"op_id": "a", "unit": "u1", "t0": t(299.5), "t1": t(301)},
                         {"op_id": "b", "unit": "u1", "t0": t(310), "t1": t(311), "error": "ValueError"}],
                        lo2, arm="mem0", run="r1")
check("B-LO2: another arm's empty JSON reply in the window is not this arm's loss, and another arm's success is not "
      "this arm's response_seen", [(x["op_id"], x["reason"], x.get("response_seen")) for x in l2["lost_ops"]]
      == [("b", "product-error", False)], str(l2["lost_ops"]))
lo3 = [call("E0", unit="r1.u1", status=503, t0=400, **J), call("E1", unit="r1.u1", status=503, t0=421, **J)]
l3 = AC.lost_operations([{"op_id": "x0", "unit": "u1", "t0": t(400), "t1": t(401)},
                         {"op_id": "x1", "unit": "u1", "t0": t(420), "t1": t(421)}], lo3, arm="mem0", run="r1")
check("L1: a call exactly at the operation's t0, and one exactly at its t1, are in its window",
      [(x["op_id"], x["reason"]) for x in l3["lost_ops"]] == [("x0", "transport"), ("x1", "transport")],
      str(l3["lost_ops"]))
check("a reader call is never a write operation's call",
      AC.lost_operations([{"op_id": "r", "unit": "u1", "t0": t(500), "t1": t(501)}],
                         [call("R", unit="r1.u1", role="reader", stage="questions", t0=500.5, content_empty=True,
                               response_format="json_object", json_ok=False)], arm="mem0", run="r1")["lost_ops"] == [])

print("\n- TB4.10b' yield_inputs (K76) through artifact.yield_block -")
U = [{"unit": "h1", "retrievable_items": 3, "chars_to_writer": 1500, "unit_chars": 1000, "stored_chars": 10},
     {"unit": "h2", "retrievable_items": 0, "chars_to_writer": 100, "unit_chars": 1000, "stored_chars": 10},
     {"unit": "h3", "retrievable_items": 0, "chars_to_writer": 300, "unit_chars": 1000, "stored_chars": 10}]
yi = AC.yield_inputs("S1", U, unit_kind="haystack", tokens_read=2000, contexts_b=["x", " ", ""])
y = A.yield_block(**yi, scored=True)
check("retrievable_unit_share over the stand's units; coverage = characters reaching the writer / the unit's, capped at "
      "1 per unit by artifact (1500 of 1000 is 1)", abs(y["retrievable_unit_share"] - 1 / 3) < 1e-12
      and abs(y["coverage"] - (1.0 + 0.1 + 0.3) / 3) < 1e-12, str(y))
check("labels on a scored run, none on an unscored one (artifact's rule)",
      y["labels"] == ["writer-gated (33.3%)", "window (46.7%)"] and A.yield_block(**yi, scored=False)["labels"] == [],
      str(y["labels"]))
check("items per 1K read and the empty-context share at Point B ride along as extra fields",
      y["items_per_1k_read"] == 1.5 and abs(y["empty_context_share"] - 2 / 3) < 1e-12)
check("M-K76-unit-question: a unit kind other than the stand's refuses",
      "evaluation unit" in err(lambda: AC.yield_inputs("S1", U, unit_kind="question")))
check("the stands' evaluation units are rev1's (haystack, conversation, row, trajectory)",
      AC.UNIT_OF_STAND == {"S1": "haystack", "S4": "conversation", "S5": "conversation", "S6": "row", "S6L": "row",
                           "S7": "trajectory"})
check("M-K76-session-notes: for ours, typed notes count and Session notes never do",
      AC.ours_retrievable([{"type": "fact"}, {"type": "Session"}, {"type": "decision"}, {"type": "session"}]) == 2)
check("a note without a type refuses - K76 counts typed notes, never an untyped one as typed (R9)",
      "without a type" in err(lambda: AC.ours_retrievable([{"type": "fact"}, {"title": "x"}])))
yb = AC.yield_inputs("S1", [{"unit": "h1", "retrievable_items": True, "chars_to_writer": 10, "unit_chars": 10}],
                     unit_kind="haystack", tokens_read=1000)
check("a bool is not a retrievable count - items_per_1k_read ignores it, and artifact refuses the unit",
      yb["extra"]["items_per_1k_read"] == 0.0 and refused(lambda: A.yield_block(**yb, scored=True), "retrievable"),
      str(yb["extra"]))
check("a unit with no characters is refused by artifact, not zeroed here",
      refused(lambda: A.yield_block(**AC.yield_inputs("S6", [{"unit": "r", "retrievable_items": 1, "chars_to_writer": 0,
                                                              "unit_chars": 0}], unit_kind="row"), scored=True),
              "no characters"))

print("\n- TB4.10b' cache_inputs (K60, K61) through artifact.cache_record; the verdicts are m5 --anchor's -")
BUILT_OT = A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1], degraded_recalls=0, direct_calls=0)
B = {("c/vec.json", "5" * 64): {"commit": ANCHOR, "utc": "2026-10-01T10:00:00+00:00", "ollama_transport": BUILT_OT}}
ci = AC.cache_inputs([{"path": "c/vec.json", "sha256": "5" * 64, "hits": 9, "misses": 3}], B)
try:
    cr = A.cache_record(**ci[0])
except A.ArtifactRefused as e:
    cr = {"built": {}, "refused": str(e)}
check("a read and its build record become artifact.cache_record's arguments",
      cr["built"].get("commit") == ANCHOR and cr.get("hits") == 9 and cr.get("misses") == 3
      and cr["built"].get("ollama_transport") == BUILT_OT and cr["built"].get("utc") == "2026-10-01T10:00:00+00:00",
      str(cr))
check("a read with no build record refuses by its path (K60 needs one)",
      "c/other.json" in err(lambda: AC.cache_inputs([{"path": "c/other.json", "sha256": "6" * 64, "hits": 1, "misses": 0}], B)))

print("\n- TB4.10b' reconciliation_inputs (K87): the numbers only; the verdict is artifact's P0j -")
rl = [call("h1", t0=1, status=500), call("h1", t0=1.5), call("h2", t0=2, unit="r1.u2"), call("h3", stage="questions", t0=40),
      call("ans", role="reader", stage="questions", t0=41), call("zz", arm="zep", t0=1),
      dict(call("mm2", t0=3), refused="model_mismatch", status=None, complete=False)]
ri = AC.reconciliation_inputs(rl, arm="mem0", run="r1", branch="a", adapter_calls=4, product_calls=3, product_tokens=363)
check("HTTP calls and tokens of the product's own port (the write port, both stages); the reader's, another arm's and "
      "refused calls are not the product's", ri["proxy_calls"] == 4 and ri["adapter_calls"] == 4, str(ri))
check("tokens_delta_pct = the product's tokens against the proxy's; product_retries = HTTP - logical",
      abs(ri["tokens_delta_pct"] - 10.0) < 1e-12 and ri["proxy_tokens"] == 330 and ri["product_retries"] == 1, str(ri))
check("B-REC1: the proxy's tokens are its completed 2xx calls' only - a failed attempt's usage is not the product's",
      ri["proxy_tokens"] == 3 * 110)
z5 = AC.reconciliation_inputs([dict(call("z", t0=1), usage={})], arm="mem0", run="r1", branch="a", adapter_calls=1,
                              product_tokens=500)
z0 = AC.reconciliation_inputs([dict(call("z", t0=1), usage={})], arm="mem0", run="r1", branch="b", product_calls=1,
                              product_tokens=0)
check("B-REC1: the product counted 500 tokens the proxy never saw - 100 %, and P0j in branch (a) (a bypass's signature)",
      z5["tokens_delta_pct"] == 100.0 and A.p0j({"reconciliation": z5}, A.P0Context(reconciliation_branches=frozenset({"a"})),
                                                "mem0") != [], str(z5))
check("B-REC1: 0 against 0 is a delta of 0, not a missing counter - not P0j in branch (b)",
      z0["tokens_delta_pct"] == 0.0 and A.p0j({"reconciliation": z0}, A.P0Context(reconciliation_branches=frozenset({"b"})),
                                              "mem0") == [], str(A.p0j({"reconciliation": z0}, A.P0Context(
          reconciliation_branches=frozenset({"b"})), "mem0")))
check("R-K87-1: the proxy's logical calls are the episodes of the product port's (unit, key) groups - a retried "
      "body is one, a body written twice is two", ri.get("proxy_logical_calls") == 3
      and AC.reconciliation_inputs([call("d", t0=1), call("d", t0=2)], arm="mem0", run="r1",
                                   branch="b").get("proxy_logical_calls") == 2, str(ri.get("proxy_logical_calls")))
check("the row carries rev1 §2.3's reconciliation fields", {"proxy_calls", "adapter_calls", "product_logical_calls",
                                                            "tokens_delta_pct", "serverlog_delta", "branch"} <= set(ri))
k87 = A.P0Context(reconciliation_branches=frozenset({"a", "b"}))
ri0 = AC.reconciliation_inputs(rl, arm="mem0", run="r1", branch="a", adapter_calls=4, product_calls=3, product_tokens=330)
check("artifact's P0j accepts the exact adapter with equal tokens, and flags one call off and a 10 % tokens delta (Q-K87-1)",
      A.p0j({"reconciliation": ri0}, k87, "mem0") == []
      and A.p0j({"reconciliation": AC.reconciliation_inputs(rl, arm="mem0", run="r1", branch="a", adapter_calls=3)},
                k87, "mem0") != []
      and any("tokens" in x for x in A.p0j({"reconciliation": ri}, k87, "mem0")), str(A.p0j({"reconciliation": ri0}, k87, "mem0")))
check("no product counter: no delta and no retries, never 0", AC.reconciliation_inputs(
    rl, arm="mem0", run="r1", branch="c")["tokens_delta_pct"] is None
      and AC.reconciliation_inputs(rl, arm="mem0", run="r1", branch="c")["product_retries"] is None)

print("\n- end to end: logs -> accounting -> artifact blocks -> build -> m5 v3 and P0, clean -")
with tempfile.TemporaryDirectory(prefix="v3acct_e2e_") as td:
    rd = Path(td)
    import json as _json
    e2e = []
    for arm in ("mem0", "zep"):
        e2e += [call(f"{arm}-w{i}", arm=arm, unit=f"r1.u{i % 2}", t0=i, **J) for i in range(1, 5)]
        e2e += [call(f"{arm}-q{i}", arm=arm, unit=f"r1.u{i % 2}", role="reader", stage="questions", t0=40 + i) for i in range(2)]
    (rd / "calls.jsonl").write_bytes("".join(_json.dumps(c) + "\n" for c in e2e).encode())
    (rd / "catcher.jsonl").write_bytes(b"")
    lg = AC.load_proxy(rd)
    arms = {}
    for arm in ("mem0", "zep"):
        wops = [{"op_id": f"o{i}", "unit": f"u{i % 2}", "t0": t(i - 0.2), "t1": t(i + 0.2)} for i in range(1, 5)]
        rc = AC.reconciliation_inputs(lg.calls, arm=arm, run="r1", branch="a", adapter_calls=4, product_calls=4,
                                      product_tokens=440)
        arms[arm] = {
            "arm_decl": decl(arm), "runs": [{"status_id": f"S6/b01/r1/{arm}", "units_dropped_own": 0}],
            "cloud_transport": A.cloud_transport(AC.cloud_counters(lg.calls, arm=arm, run="r1", stand="S6", cloud_bypass=0, background_writes=0,
                                                                   ollama=lg.ollama,
                                                                   product_retries=rc["product_retries"])),
            "boundary": A.boundary_block(proxy=AC.proxy_boundary_inputs(lg.calls, lg.catcher, arm=arm, run="r1",
                                                                        ollama=lg.ollama),
                                         witnesses=AC.witness_inputs({**CHECK_OK, "containers": []})),
            "ollama_transport": BUILT_OT,
            "p1": A.p1_block(**AC.lost_operations(wops, lg.calls, arm=arm, run="r1")),
            "yield": A.yield_block(**AC.yield_inputs("S6", [{"unit": "u0", "retrievable_items": 2, "chars_to_writer": 800,
                                                              "unit_chars": 1000},
                                                             {"unit": "u1", "retrievable_items": 1, "chars_to_writer": 900,
                                                              "unit_chars": 1000}], unit_kind="row"), scored=True),
            "reconciliation": rc, "questions": [{"qid": "q0", "invalid": None}], "units_dropped": []}
    sids2 = ["S6/b01/r1/mem0", "S6/b01/r1/zep"]
    doc2 = A.build(stand="S6", point="B", tier="product", arms=arms, brackets=None, input_manifest=MANIFEST,
                   model_version={"response_model": "deepseek-v4-flash", "changelog_newest": "2026-09-10"}, commit=ANCHOR,
                   dirty=False, status_ids=sids2, run_files={s: {"path": f"runs/{s}.json", "sha256": "3" * 64} for s in sids2})
    ctx = A.P0Context(anchor=ANCHOR, reconciliation_branches=frozenset({"a"}), stand_units=2)
    fl = [x for n, r in doc2["arms"].items() for x in A.p0_flags(r, ctx, n)]
    check("the row-level P0 clauses raise nothing on the clean logs", fl == [] and lg.problems == [], str(fl[:3]))
    check("p1 from the logs: 0 of 4 lost", doc2["arms"]["mem0"]["p1"]["lost_share"] == 0
          and doc2["arms"]["mem0"]["p1"]["logical_writes"] == 4)
    if not M5P.exists():
        skip("m5 v3 check_file PASSes the artifact built from the logs", "the auditor's m5 lives in .loop (R12)")
    else:
        p2 = rd / "S6_B_product.json"
        p2.write_bytes(A.render(doc2))
        v2 = M5.check_file(p2, {"anchor": ANCHOR})
        check("m5 v3 check_file PASSes the artifact built from the logs", v2 == [], str(v2[:3]))
        bad = A.render({**doc2, "arms": {**doc2["arms"], "mem0": {**doc2["arms"]["mem0"], "caches": [
            A.cache_record(**AC.cache_inputs([{"path": "c/v.json", "sha256": "5" * 64, "hits": 1, "misses": 0}],
                                             {("c/v.json", "5" * 64): {**B[("c/vec.json", "5" * 64)], "commit": "b" * 40}})[0])]}}})
        p2.write_bytes(bad)
        v3 = M5.check_file(p2, {"anchor": ANCHOR})
        check("K60 is m5 --anchor's verdict: a cache built at another commit is named there, not here",
              any("not the anchor" in x for x in v3), str(v3[:3]))

print("\n- Q-A5-1: K76's coverage by 32-character windows -")
import json  # noqa: E402
END = "2026-09-28T10:00:00+00:00"


def body(*strings, t0="2026-09-28T09:00:00+00:00", status=200):
    return {"t0": t0, "strings": list(strings), "via": "write", "status": status}


ITEM = ("The quarterly report was filed late because the upload kept failing on the flaky network "
        "until a bounded retry was added to the client, and then the whole backlog went through at once.")
N = len(AC.normalize_ws(ITEM))
cut = AC.normalize_ws(ITEM)
mid_cut = cut[:40] + " [...] " + cut[-60:]
cv = AC.unit_coverage([ITEM], [body("Extract the facts:\n" + mid_cut)], end_write_at=END)
check("Q-A5-1 (a): an item whose middle the product cut (truncate_smart: a head and a tail kept) counts the head AND "
      "the tail - never the head alone (40 + 60 characters, and at most the space beside each cut, which the body "
      "carries too)", 100 <= cv["covered"] <= 102 and cv["chars"] == N, str(cv))
framed = [body("user: " + ITEM.replace(" ", "  ").replace(", ", ",\n\t")),
          body('{"role":"user","content":"x"}', "Session 3\n" + ITEM)]
cvf = AC.unit_coverage([ITEM], framed[:1], end_write_at=END)
check("Q-A5-1 (b): the product's framing - a role label, doubled spaces, new lines and tabs - lowers nothing",
      cvf["covered"] == N and cvf["coverage"] == 1.0, str(cvf))
cv0 = AC.unit_coverage([ITEM, "a short one"], [], end_write_at=END)
check("Q-A5-1 (c): a unit that never reached the writer's LLM covers 0 of all its characters",
      cv0["covered"] == 0 and cv0["chars"] == N + len("a short one") and cv0["coverage"] == 0.0 and cv0["calls"] == 0,
      str(cv0))
cvd = AC.unit_coverage([ITEM], [body(AC.normalize_ws(ITEM)[10:41])], end_write_at=END)
check("Q-A5-1 (d): a match of 31 characters counts nothing - windows are 32", cvd["covered"] == 0, str(cvd))
cve = AC.unit_coverage([ITEM], [body(ITEM), body("again: " + ITEM), body(ITEM + " " + ITEM)], end_write_at=END)
check("Q-A5-1 (e): an item carried by several calls, or twice in one, counts once - at most its own length",
      cve["covered"] == N and cve["calls"] == 3, str(cve))
cvs = AC.unit_coverage(["short item", "not there at all", "short but absent here"], [body("xx short item yy")],
                       end_write_at=END)
check("Q-A5-1: an item shorter than 32 counts whole as a substring, else not at all - its start alone is nothing",
      cvs["covered"] == len("short item")
      and cvs["chars"] == len("short item") + len("not there at all") + len("short but absent here"), str(cvs))
cvt = AC.unit_coverage([ITEM], [body(ITEM, t0="2026-09-28T10:00:01+00:00")], end_write_at=END)
check("Q-A5-1: a request sent after the unit's end_write is not its write phase", cvt["covered"] == 0
      and cvt["calls"] == 0, str(cvt))
cvf5 = AC.unit_coverage([ITEM], [body(ITEM, status=500), body(ITEM, status=None)], end_write_at=END)
check("Q-A5-1 (C-1): a request record whose status is not 200 counts nothing - the LLM made no memory from it",
      cvf5["covered"] == 0 and cvf5["calls"] == 0, str(cvf5))
try:
    AC.unit_coverage([ITEM], [], end_write_at=None)
    no_end = "accepted"
except AC.AccountingError as e:
    no_end = str(e)
check("Q-A5-1: a unit without an end_write stamp (aborted) has no coverage - refused, never 0", "end_write" in no_end,
      no_end)


def reference_coverage(item_texts, bodies, *, end_write_at):
    """The first implementation (a127869), windows of ALL request strings - the reference C-2 must equal."""
    end = AC._when({"t": end_write_at}, "t")
    used = [b for b in bodies if b.get("status") == 200 and AC._when(b, "t0") <= end]
    strs = [AC.normalize_ws(s) for b in used for s in (b.get("strings") or []) if isinstance(s, str)]
    windows = {s[i:i + 32] for s in strs for i in range(len(s) - 31)}
    covered = total = 0
    for text in item_texts:
        n = AC.normalize_ws(text)
        total += len(n)
        if len(n) < 32:
            covered += len(n) if n and any(n in s for s in strs) else 0
            continue
        mark = bytearray(len(n))
        for i in range(len(n) - 31):
            if n[i:i + 32] in windows:
                mark[i:i + 32] = b"\x01" * 32
        covered += mark.count(1)
    return {"covered": covered, "chars": total, "coverage": covered / total if total else None, "calls": len(used)}


CASES = {"head and tail": ([ITEM], [body("Extract the facts:\n" + mid_cut)]),
         "framing": ([ITEM], framed[:1]),
         "nothing": ([ITEM, "a short one"], []),
         "31 chars": ([ITEM], [body(AC.normalize_ws(ITEM)[10:41])]),
         "repeats": ([ITEM, ITEM, "short item"], [body(ITEM), body("again: " + ITEM), body(ITEM + " " + ITEM, "short item")]),
         "shorts": (["short item", "not there at all", "short but absent here"], [body("xx short item yy")]),
         "mixed": ([ITEM, "short item", mid_cut[:50]], [body(mid_cut, "yy short item"), body(ITEM, status=500),
                                                        body(ITEM, t0="2026-09-28T11:00:00+00:00")])}
diff = {k: (AC.unit_coverage(i, b, end_write_at=END), reference_coverage(i, b, end_write_at=END))
        for k, (i, b) in CASES.items()}
check("Q-A5-1 (C-2): the unit-bounded pass gives exactly the reference's numbers - a cut middle, framing, nothing, 31 "
      "characters, repeats, short items, a failed and a late request", all(a == b for a, b in diff.values()),
      str({k: v for k, v in diff.items() if v[0] != v[1]}))
import random  # noqa: E402
import tracemalloc  # noqa: E402
rng = random.Random(7)
VOCAB = ["memory", "ledger", "upload", "retry", "session", "episode", "graph", "edge", "node", "fact", "report",
         "quarterly", "network", "client", "server", "adoption", "interview", "canvas", "lake", "sunrise"]
DISTINCT = [" ".join(rng.choice(VOCAB) for _ in range(3000)) for _ in range(100)]     # 100 strings of ~20 KB
BIG = [body(*DISTINCT[j * 10:(j + 1) * 10]) for _r in range(10) for j in range(10)]   # 20 MB: each string sent 10x
BIG_ITEMS = [DISTINCT[3][500:800], "a sentence that no request ever carried, long enough", "short"]
tracemalloc.start()
cvb = AC.unit_coverage(BIG_ITEMS, BIG, end_write_at=END)
_cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
sent = sum(len(s) for b in BIG for s in b["strings"])
check("Q-A5-1 (C-2): 20 MB of request strings (graphiti-like repeats) - the peak memory is bounded by the unit's items "
      "and one string at a time, not by the bodies (< 8 MB), and the result is right",
      sent > 19_000_000 and peak < 8_000_000 and cvb["covered"] == len(AC.normalize_ws(DISTINCT[3][500:800])) + 0 + 0
      and cvb["calls"] == 100, f"sent={sent} peak={peak} {cvb}")
BD = Path(tempfile.mkdtemp(prefix="nvt3_bodies_"))
(BD / "bodies" / "mem0").mkdir(parents=True)
(BD / "bodies" / "mem0" / "r1.u1.jsonl").write_bytes(
    (json.dumps(body(ITEM)) + "\n" + json.dumps(body("x")) + "\n{bad").encode("utf-8"))
recs_b, probs_b = AC.load_bodies(BD, arm="mem0", run="r1", unit="u1")
check("Q-A5-1: load_bodies reads bodies/<arm>/<run>.<unit>.jsonl, one record per LF line - a cut last line is a "
      "named problem", len(recs_b) == 2 and probs_b and "no LF" in probs_b[0], f"{len(recs_b)} {probs_b}")
shutil.rmtree(BD, ignore_errors=True)

print("\n- TB7 cloud_bypass (Q-12-6, Q-A7-8) -")
cat_ = [{"arm": "mem0", "host": "api.deepseek.com", "tunnelled": False}, {"arm": "mem0", "host": "API.OpenAI.com"},
        {"arm": "mem0", "host": "api.anthropic.com"}, {"arm": "mem0", "host": "huggingface.co"},
        {"arm": "mem0", "host": "deepseek.com"}, {"arm": "letta", "host": "api.deepseek.com"}]
check("TB7: cloud_bypass - the arm's catcher records whose host is a provider host (DeepSeek, and the OpenAI and "
      "Anthropic SDK defaults, any case); another host, a near name or another arm's record is not one",
      AC.cloud_bypass(cat_, arm="mem0") == 3 and AC.cloud_bypass(cat_, arm="letta") == 1
      and AC.cloud_bypass(cat_, arm="nevertwice") == 0
      and AC.PROVIDER_HOSTS == ("api.deepseek.com", "api.openai.com", "api.anthropic.com"),
      f"{AC.cloud_bypass(cat_, arm='mem0')} {AC.cloud_bypass(cat_, arm='letta')}")

print("\n- TB7 embed_inputs (Q-A7-7), at accounting's own level -")
emb = [{"arm": "mem0", "is_embed": True, "status": 200, "embed_inputs": 2, "embed_tokens": 11, "embed_at_cap": 1,
        "prompt_eval_count": 11},
       {"arm": "mem0", "is_embed": True, "status": 200, "embed_inputs": 1, "embed_tokens": 5, "embed_at_cap": 0,
        "prompt_eval_count": 7},
       {"arm": "mem0", "is_embed": True, "status": 400, "embed_inputs": 9, "embed_tokens": 900, "embed_at_cap": 9,
        "prompt_eval_count": None},
       {"arm": "mem0", "is_embed": False, "is_llm": True, "status": 200},
       {"arm": "letta", "is_embed": True, "status": 200, "embed_inputs": 1, "embed_tokens": 3, "embed_at_cap": 0,
        "prompt_eval_count": 3}]
try:
    e_mem0 = AC.embed_inputs(emb, arm="mem0")
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    e_mem0 = f"{type(e).__name__}: {e}"
check("TBd/TBe: embed_inputs counts only the arm's answered (200) embed calls - a 400 embed, a generation call and "
      "another arm's call are not in it - and counts the call whose token sum (5) is not Ollama's prompt_eval_count "
      "(7) as mismatched, never adjusting it", e_mem0 == {"embed_at_cap": 1, "calls": 2, "inputs": 3, "tokens": 16,
                                                         "prompt_eval_count": 18, "mismatched_calls": 1}, str(e_mem0))

try:
    AC.embed_inputs(emb, arm="chroma-store", expected=True)
    exp_none = "accepted"
except AC.AccountingError as e:
    exp_none = str(e)
except Exception as e:  # noqa: BLE001
    exp_none = f"not refused by name: {type(e).__name__}: {e}"
try:
    plain = (AC.embed_inputs(emb, arm="chroma-store")["calls"], AC.embed_inputs(emb, arm="letta", expected=True)["calls"])
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    plain = f"{type(e).__name__}: {e}"
check("R-EMBED-PATH: an arm that embeds through Ollama (expected) with no answered embed call on its leg refuses - "
      "unmeasured, never 0; without the expectation it is simply 0 calls; an arm with calls passes either way",
      "unmeasured" in exp_none and plain == (0, 1), f"{exp_none} | {plain}")

print("\n- M-DUP: nothing here builds or judges -")
gone = [n for n in ("cloud_transport", "m5_cloud_problems", "boundary", "boundary_problems", "ollama_transport",
                    "P1_BANDS", "ZERO_TOLERANCE", "p1", "yield_", "caches", "reconciliation") if hasattr(AC, n)]
check("no copy of an artifact builder, an m5 check or a zero-tolerance list", gone == [], str(gone))

print(f"\nv3 accounting: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
sys.exit(1 if FAILED else 0)
