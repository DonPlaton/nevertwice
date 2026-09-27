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
  that unit's loss); a forwarded record with no request key, t0 or t1, or a line that is not UTF-8, refuses by name;
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
      [keys[("u1", k)]["class"] for k in ("ok1", "rec", "late", "edge", "lostw", "aband")]
      == ["ok", "recovered", "late_recovered", "recovered", "never", "recovered"], str({k: v["class"] for k, v in keys.items()}))
check("a key whose calls span two phases refuses",
      "spans the phases" in err(lambda: AC.classify_keys([call("x"), call("x", role="reader")])))
check("MA15: recovery is measured to the success's t1 - a failure at 0:00 answered 29:59..30:05 is late_recovered",
      AC.classify_keys([call("m15", status=500, t0=0), call("m15", t0=29 + 59 / 60, t1=30 + 5 / 60)])[("u1", "m15")]["class"]
      == "late_recovered")
check("F-ACC2: a forwarded record without a request key refuses",
      "request_key" in err(lambda: AC.classify_keys([call(None)])))
check("F-ACC2: a record without t0 refuses - never read as 0",
      "t0" in err(lambda: AC.classify_keys([{k: v for k, v in call("n0").items() if k != "t0"}])))
check("F-ACC2: a success without t1 refuses", "t1" in err(lambda: AC.classify_keys(
    [call("n1", status=500, t0=1), {k: v for k, v in call("n1", t0=2).items() if k != "t1"}])))

print("\n- B-ACC1: one request key, two units (the §4.5 key has no unit; a session body repeats across units) -")
dup1 = [call("dup", unit="r1.u1", status=503, t0=1), call("dup", unit="r1.u1", status=503, t0=2),
        call("dup", unit="r1.u2", t0=6)]
c1 = AC.cloud_counters(dup1, arm="mem0", run="r1", stand="S1", cloud_bypass=0)
check("u1 lost it for good, u2 wrote the same body 5 min later: u1's transport_lost, nothing recovered",
      c1["transport_lost"] == 1 and c1["transport_lost_units"] == ["u1"] and c1["transport_recovered"] == 0, str(c1))
dup2 = [call("dup", unit="r1.u1", t0=1), call("dup", unit="r1.u2", status=503, t0=2), call("dup", unit="r1.u2", status=503, t0=3)]
c2 = AC.cloud_counters(dup2, arm="mem0", run="r1", stand="S1", cloud_bypass=0)
check("u1 wrote it, then u2 lost the same body: u2's loss is seen", c2["transport_lost"] == 1
      and c2["transport_lost_units"] == ["u2"], str(c2))
dup3 = [call("dup", unit="r1.u1", status=503, t0=1), call("dup", unit="r1.u2", t0=121)]
c3 = AC.cloud_counters(dup3, arm="mem0", run="r1", stand="S1", cloud_bypass=0)
check("u1 lost it, u2 wrote it two hours later: u1's loss, and no late_recovered pinned on u1",
      c3["transport_lost_units"] == ["u1"] and c3["late_recovered"] == 0 and c3["late_recovered_units"] == [], str(c3))
k3 = AC.classify_keys(dup3)
check("the classes are per (unit, key)", {k: v["class"] for k, v in k3.items()} == {("u1", "dup"): "never",
                                                                                     ("u2", "dup"): "ok"}, str(k3))

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
                       key_question=KQ, dropped={"q9"}, cloud_bypass=0,
                       incident_units=["u3", "u3"])
check("another arm's, another run's and the scheduler port's calls never count; refused calls are not calls",
      ct["calls"] == len(calls) + len(read_calls) + len(misc), str(ct["calls"]))
check("transport_lost: the write-phase key that never succeeded, with its unit",
      ct["transport_lost"] == 1 and ct["transport_lost_units"] == ["u1"])
check("failed_outcomes: the answer key that never succeeded on a question NOT dropped; the dropped one not counted",
      ct["failed_outcomes"] == 1, str(ct["failed_outcomes"]))
check("recovered and late_recovered counted apart", ct["transport_recovered"] == 3 and ct["late_recovered"] == 1,
      f"{ct['transport_recovered']} {ct['late_recovered']}")
check("a never-succeeded read key with no question refuses",
      "has no question" in err(lambda: AC.cloud_counters(allc, arm="mem0", run="r1", stand="S1", cloud_bypass=0,
                                                         key_question={("u1", "r_lost"): "q7"})))
check("Q-50-1 a late success is listed per unit - on S1 without the order flag",
      ct["late_recovered_units"] == [{"stand": "S1", "unit": "u1", "order_sensitive": False}], str(ct["late_recovered_units"]))
ct6 = AC.cloud_counters(allc, arm="mem0", run="r1", stand="S6", ollama=ollama, cloud_bypass=0,
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
                       arm="mem0", run="r1", stand="S1", cloud_bypass=0)
check("F-ACC3: upstream_errors = a 5xx answer or no answer at all (the proxy's upstream_error); a 429 is neither",
      ue["upstream_errors"] == 2, str(ue["upstream_errors"]))
an = AC.cloud_counters([call("a1", endpoint="anthropic", model="m-1", unit="r1.u7", t0=1),
                        call("a2", endpoint="anthropic", model="m-2", unit="r1.u7", t0=2),
                        call("a3", endpoint="anthropic", model="m-1", unit="r1.u8", t0=3),
                        call("a4", fp="fp_z", unit="r1.u8", t0=4)], arm="mem0", run="r1", stand="S1", cloud_bypass=0)
check("F-ACC4: a unit whose /anthropic calls ran under two models is straddled; one model per class is not",
      an["straddled_units"] == ["u7"], str(an["straddled_units"]))
sig = inspect.signature(AC.cloud_counters).parameters
check("cloud_bypass is a measured input with no default (R9: never defaulted to 0)",
      "cloud_bypass" in sig and sig["cloud_bypass"].default is inspect.Parameter.empty)
check("product_retries is left to the reconciliation unless given", ct["product_retries"] is None
      and AC.cloud_counters(calls, arm="mem0", run="r1", stand="S1", cloud_bypass=0, product_retries=2)["product_retries"] == 2)

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
cblk = A.cloud_transport(AC.cloud_counters(clean, arm="mem0", run="r1", stand="S1", cloud_bypass=0))
check("a clean arm-run's counters raise no P0b", A.p0b({"arm_decl": cloud_decl, "cloud_transport": cblk}, A.P0Context(),
                                                       "mem0") == [])
check("a measured cloud bypass reaches P0b", "P0b: cloud_bypass > 0" in A.p0b(
    {"arm_decl": cloud_decl, "cloud_transport": A.cloud_transport(
        AC.cloud_counters(clean, arm="mem0", run="r1", stand="S1", cloud_bypass=1))}, A.P0Context(), "mem0"))

print("\n- proxy_boundary_inputs and witness_inputs, built by artifact.boundary_block -")
bcalls = [call("b1", t0=1), call("b2", t0=2, ancestor_canary_hits=2),
          dict(call("b3", t0=3, canary_hits=1), refused="canary", status=None, complete=False),   # refused, still a hit
          call("b4", t0=4, arm="zep", canary_hits=5), call("b5", t0=5, unit="r2.u1", owner_marker_hits=1),
          call("b6", t0=6, arm="scheduler", unit=None, role="scheduler", stage=None)]
catcher = [{"arm": "mem0", "host": "api.openai.com", "tunnelled": False}, {"arm": "mem0", "host": "api.openai.com", "tunnelled": False},
           {"arm": "mem0", "host": "x.org", "tunnelled": True}, {"arm": "zep", "host": "y.org", "tunnelled": False}]
pb = AC.proxy_boundary_inputs(bcalls, catcher, arm="mem0", run="r1")
check("the arm-run's hits summed over its call records, a refused call's included; another arm's and run's never",
      pb.get("canary_hits") == 1 and pb.get("owner_marker_hits") == 0 and pb.get("ancestor_canary_hits") == 2, str(pb))
check("the catcher's refused egress by host, this arm's only", pb["egress_attempts"] == {"api.openai.com": 2})
check("MA12: a bool in a hit field refuses - True is not a count",
      "canary_hits" in err(lambda: AC.proxy_boundary_inputs([call("b", canary_hits=True)], [], arm="mem0", run="r1")))
check("a call record without a hit field refuses - never 0",
      "canary_hits" in err(lambda: AC.proxy_boundary_inputs([{k: v for k, v in call("b").items() if k != "canary_hits"}],
                                                            [], arm="mem0", run="r1")))
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
         "failed_outcomes_llm": {"by_status": {}, "by_exception_type": {}, "gave_up": 0}, "llm_retries": 0}


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
            "cloud_transport": A.cloud_transport(AC.cloud_counters(lg, arm=system, run="r1", stand=stand, cloud_bypass=0)),
            "boundary": A.boundary_block(proxy=AC.proxy_boundary_inputs(lg, [], arm=system, run="r1"),
                                         witnesses=AC.witness_inputs({**CHECK_OK, "containers": []})),
            "ollama_transport": A.ollama_transport(PACER, embed_at_cap=0, fallback_local=0, embed_models_seen=[D1],
                                                   degraded_recalls=0),
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

print("\n- M-DUP: nothing here builds or judges -")
gone = [n for n in ("cloud_transport", "m5_cloud_problems", "boundary", "boundary_problems", "ollama_transport",
                    "P1_BANDS", "ZERO_TOLERANCE") if hasattr(AC, n)]
check("no copy of an artifact builder, an m5 check or a zero-tolerance list", gone == [], str(gone))

print(f"\nv3 accounting: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
sys.exit(1 if FAILED else 0)
