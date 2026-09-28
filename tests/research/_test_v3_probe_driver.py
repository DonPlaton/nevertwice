#!/usr/bin/env python3
"""PREREG-V3 A8 C5a-2c (the auditor's Q-DRV-1..5): research/v3/probe_a8.run_mem0_probe - spawns NO child. The driver
runs on the campaign's own modules (the real StatusLog, GateDriver, IncidentGate, StopGate, build_config, the real
fakes of probe_upstream); only what would spawn or reach a socket outside this process is injected - the proxy's
start and stop, the scheduler (whose write_turn stands in for the adapter child), the launcher, the witnesses:

* before anything is started: the mem0_v3 install record (m0_pin), a clean a8-spacy-model record (else
  blocked:model-not-installed: mem0 would download the model at run time), the venv's site, the source facts, the fake's
  one answer in their shape, the D1 embed pin (<runs>/_d1tag/<run>/record.json, else blocked:no-embed-pin) - a blocked
  one writes its probe.json with nothing started;
* the proxy: build_config with the fakes as both upstreams on 127.0.0.1, a sentinel key file outside the secrets root,
  the stand's canaries; the probe-local STATUS (<runs>/_a8/<run>/STATUS) with its full state machine - STAND, BLOCK
  (arm_order mem0, seed 0: the probe's one arm, no campaign_seed), START ... END; the gate is StopGate AND GateDriver;
  the proxy stage is set for the write and cleared after it, the turn wrapped in a boundary check;
* probe.json: the fields, the outcome, the ops', the script's and STATUS's sha256s, the expected model, the ceiling,
  the test-upstream note, the embed pin, the installed-set pair (R-NLP-SET), written once; a failure anywhere is named
  and the proxy and the fakes are stopped all the same.

    python tests/research/_test_v3_probe_driver.py
"""
from __future__ import annotations

import atexit
import hashlib
import http.client
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_probe_a8_drv_t", ROOT / "research" / "v3" / "probe_a8.py")
L = _load("v3_launch_drv_t", ROOT / "research" / "v3" / "launch.py")
M = P.probe_modules()
PASSED = FAILED = 0
RAISED: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def ok(fn) -> bool:
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_probe_driver_"))
atexit.register(shutil.rmtree, TMP, True)
TAG, DIG = "nvt3-bge-m3-d1:latest", "561ce53b" + "0" * 56
MAIN = (b"class Memory:\n    def add(self, messages, timestamp=None):\n        if timestamp is not None:\n"
        b"            raise ValueError(get_temporal_feature_error_message(\"sync\", \"add\", \"timestamp\"))\n\n"
        b"    def _add_to_vector_store(self, messages):\n        response = self.llm.generate_response(\n"
        b"            messages=messages,\n            response_format={\"type\": \"json_object\"},\n        )\n"
        b"        items = json.loads(response, strict=False).get(\"memory\", [])\n"
        b"        mem_texts = [m.get(\"text\", \"\") for m in items if m.get(\"text\")]\n")
SPM = (b"_nlp_full = None\n_nlp_lemma = None\n_load_failed_full = False\n_load_failed_lemma = False\n\n\n"
       b"def _ensure_model_available():\n    import spacy\n    if not spacy.util.is_package(\"en_core_web_sm\"):\n"
       b"        download(\"en_core_web_sm\")\n")
NLP_ON = {"names": {"full": "_nlp_full", "lemma": "_nlp_lemma", "failed_full": "_load_failed_full",
                    "failed_lemma": "_load_failed_lemma", "model": "en_core_web_sm"}, "module": True, "nlp_full": True,
          "nlp_lemma": True, "failed_full": False, "failed_lemma": False, "is_package": True}


def world(tag, *, install=True, model=True, d1=True, spm=SPM, main=MAIN, d1_record=None):
    base = TMP / tag
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "q",
                   conservation_root=base / "cv")
    venv = c.polygon_root / "mem0_v3"
    site = venv / "Lib" / "site-packages"
    for rel, data in {"mem0/memory/main.py": main, "mem0/configs/llms/deepseek.py": b"class C:\n    def __init__(\n"
                      b"        self,\n        temperature: float = 0.1,\n    ):\n        pass\n",
                      "mem0/llms/deepseek.py": b"class DeepSeekLLM:\n    pass\n",
                      "mem0/utils/spacy_models.py": spm}.items():
        if data is None:
            continue
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)
    runs = c.runs_root
    if install:
        d = runs / "_install" / "a8-pypi-mem0_v3" / "i1"
        d.mkdir(parents=True)
        (d / "install_record.json").write_text(json.dumps({"problems": [], "venv": str(venv), "installed_set_sha256": "a" * 64,
                                                           "import_versions": {"dists": {"mem0ai": "2.2.0"}}}), encoding="utf-8")
    if model:
        d = runs / "_install" / "a8-spacy-model" / "m1"
        d.mkdir(parents=True)
        (d / "model_record.json").write_text(json.dumps({"problems": [], "model_installed_set_sha256": "b" * 64}),
                                             encoding="utf-8")
    if d1:
        d = runs / "_d1tag" / "d1"
        d.mkdir(parents=True)
        (d / "record.json").write_text(json.dumps(d1_record if d1_record is not None else
                                                  {"problems": [], "tag": {"name": TAG, "digest": DIG}}), encoding="utf-8")
    return c, venv


def iso(t: float) -> str:
    """The proxy's own record time form (_llm_proxy._iso)."""
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


class Control:
    """The proxy's control port: the stage calls, and its counters - which, with ``stops``, show the upstream's stop
    statuses only AFTER the first read (StopGate's base), as a 401 arriving mid-run would."""

    def __init__(self, stops=None):
        self.log: list = []
        self.stops = stops or {}
        self.reads = 0

    def stage(self, block, stage):
        self.log.append((block, stage))

    def counters(self):
        self.reads += 1
        return {} if self.reads == 1 else self.stops


class World:
    """The injected edges: the proxy's start and stop, the scheduler, the launcher, the witnesses, the post."""

    def __init__(self, *, model="deepseek-flash", aborted=None, failed_op=False, raise_in_turn=False, pstop_rc=0,
                 trap=False, temperature=0.1, counters=None, stops=None, interrupt=False, unserved=False,
                 timeless=False, catcher=False, dirty_check=False, bad_record=False):
        self.interrupt, self.unserved, self.timeless, self.catcher = interrupt, unserved, timeless, catcher
        self.dirty_check, self.bad_record = dirty_check, bad_record
        self.model, self.aborted, self.failed_op, self.raise_in_turn = model, aborted, failed_op, raise_in_turn
        self.pstop_rc, self.trap, self.temperature, self.stops = pstop_rc, trap, temperature, stops
        self.counters = counters
        self.started = self.stopped = None
        self.w_log: list = []
        self.turn_seen: dict = {}
        self.launcher_args = None

    def start_proxy(self, c, **kw):
        self.started = kw
        run_dir = Path(kw["config"]["run_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)
        self.h = SimpleNamespace(run_dir=run_dir, control=Control(self.stops),
                                 ports={"scheduler": 1, "arms": {"mem0": {"write": 2, "ollama": 3},
                                                                 "harness-catcher": {"catcher": 4}}},
                                 tokens={"scheduler": "t-s", "mem0": "nvt3-mem0"})
        return self.h

    def stop_proxy(self, h):
        self.stopped = True
        return {"rc": self.pstop_rc, "killed": False}

    def post(self, port, path, body, token, timeout=60.0):
        return 200, {"choices": [{"message": {"content": "x"}}], "model": self.model}

    def make_witnesses(self, c, native, fs, canaries):
        log = self.w_log

        class W:
            def begin_check(self, cid):
                log.append(("begin", cid))

            def end_check(self, cid, world_=self):
                log.append(("end", cid))
                return {"complete": True, "native": {"hits": 0, "loopback_hits": 0},
                        "fs": {"fs_hits": 1 if world_.dirty_check else 0}}
        return W()

    def make_scheduler(self, *a, **k):
        world_ = self
        world_.sched_args = (a, k)

        class Sched:
            pid = 4242

            def write_turn(self, launcher, **kw):
                world_.turn_seen = {"stage": list(world_.h.control.log), "kw": kw,
                                    "admits": k["hooks"].gate.admits_new_unit(), "gate": type(k["hooks"].gate).__name__,
                                    "w_log": list(world_.w_log)}
                if world_.raise_in_turn:
                    raise RuntimeError("the child crashed on its way up")
                if world_.interrupt:
                    raise KeyboardInterrupt
                home = Path(world_.h.run_dir).parent / "u1.home"
                home.mkdir(parents=True, exist_ok=True)
                (home / "start.write.json").write_text(json.dumps({"ok": True, "llm_usage": P.M0_USAGE_SOURCE}),
                                                       encoding="utf-8")
                import time as _t
                lines = [{"arm": "mem0", "unit": f"{kw['runs'][0]}.u1", "endpoint": "v1", "status": 200, "complete": True,
                          "t0": iso(_t.time()), "t1": iso(_t.time()), "port_role": "write",
                          "refused": None, "usage": {"prompt": 1000 + i, "completion": 10 + i},
                          "temperature": world_.temperature, "thinking_sent": None, "response_format": "json_object",
                          "tools_offered": []} for i in range(3)]
                if world_.timeless:
                    for ln in lines:
                        ln.pop("t0"), ln.pop("t1")
                with open(Path(world_.h.run_dir) / "calls.jsonl", "a", encoding="utf-8") as f:
                    for ln in lines:
                        f.write(json.dumps(ln) + "\n")
                    if world_.bad_record:
                        f.write("{not json\n")
                if world_.catcher:
                    with open(Path(world_.h.run_dir) / "catcher.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"arm": "harness-catcher", "host": "github.com", "port": 443}) + "\n")
                if world_.unserved:
                    port = world_.started["config"]["ollama"]["upstream"][1]
                    cn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    cn.request("GET", "/api/version", body=b"", headers={"Content-Type": "application/json"})
                    cn.getresponse().read()
                    cn.close()
                if world_.trap:
                    port = world_.started["config"]["ollama"]["upstream"][1]
                    cn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    cn.request("POST", "/api/pull", body=b"{}", headers={"Content-Type": "application/json"})
                    cn.getresponse().read()
                    cn.close()
                counters = world_.counters or {"llm_usage": {"calls": 3, "failed": 0, "no_usage": 0, "prompt_tokens": 3003,
                                                             "completion_tokens": 33}, "nlp": NLP_ON}
                ops = [{"ok": True}] * 2 + [{"ok": not world_.failed_op, "error": "boom" if world_.failed_op else None}]
                return {(kw["runs"][0], "u1"): SimpleNamespace(aborted=world_.aborted, error=None, ops=ops, counters=counters,
                                                               dirs=SimpleNamespace(home=home))}
        return Sched()

    def make_launcher(self, c, h, python, tag, units):
        self.launcher_args = {"python": python, "tag": tag, "units": units}
        return object()

    def deps(self):
        return P.ProbeDeps(modules=M, environ={"SystemRoot": "C:\\Windows"}, proxy_python=Path("py.exe"),
                           start_proxy=self.start_proxy, stop_proxy=self.stop_proxy, post=self.post,
                           make_witnesses=self.make_witnesses, make_scheduler=self.make_scheduler,
                           make_launcher=self.make_launcher, clock=M.SC.SystemClock())


def run(tag, wd=None, **kw):
    c, venv = world(tag, **kw)
    wd = wd or World()
    try:
        rec = P.run_mem0_probe(c, L, run="r1", install_run="i1", model_run="m1", deps=wd.deps())
        err = None
    except Exception as e:  # noqa: BLE001 - a refusal is the row's to read
        rec, err = {}, e
    return rec, c, venv, wd, err


print("- a whole mem0 probe, the driver's own sequence -")
rec, C, VENV, WD, err = run("ok")
check("the probe passes, and probe.json is written with it", ok(lambda: err is None and rec["outcome"] == "pass"
      and json.loads((C.runs_root / "_a8" / "r1" / "mem0" / "probe.json").read_text(encoding="utf-8"))["outcome"] == "pass"),
      f"{err!r} {rec.get('outcome')} {rec.get('reasons')}")
check("its fields in M0_FIELDS order, each from the run's own inputs", ok(lambda: list(rec["fields"]) == list(P.M0_FIELDS)),
      str(list(rec.get("fields") or {})))
check("probe.json names the ops', the script's and STATUS's sha256s, the expected model, the ceiling, the test-upstream "
      "note, the embed pin's file, the installed-set pair (R-NLP-SET)",
      ok(lambda: rec["ops_sha256"] == P._canon_sha(P.M0_OPS) and len(rec["script_sha256"]) == 64
         and rec["status_sha256"] == hashlib.sha256((C.runs_root / "_a8" / "r1" / "STATUS").read_bytes()).hexdigest()
         and rec["expected_model"] == "deepseek-flash" and rec["ceiling_s"] == M.SC.DEBUG_CEILING_S
         and rec["test_upstream"] == P.TEST_UPSTREAM_NOTE
         and rec["embed_pin"]["path"] == str(C.runs_root / "_d1tag" / "d1" / "record.json") and rec["embed_pin"]["tag"] == TAG
         and rec["installed_sets"] == {"install": "a" * 64, "model": "b" * 64} and rec["witness_scope"] == P.WITNESS_SCOPE),
      str({k: rec.get(k) for k in ("expected_model", "ceiling_s", "installed_sets")}))
_bf = json.loads((C.runs_root / "_a8" / "r1" / "mem0" / "probe.json").read_text(encoding="utf-8")) if not err else {}
check("Q-C6-5 / F-C6-4: probe.json carries the bound facts read from the probed venv and the reasons against a bound - "
      "this world's venv has no prompts.py, so there are reasons, and the probe still passes: a bound that cannot be made "
      "is writer_bound's refusal, never the probe's verdict",
      ok(lambda: set(_bf["bound_facts"]) >= set(P.M0_BOUND_SOURCE) | set(P.M0_HELPER_SHAPES) | {"m0_adapter"}
         and rec["bound_blocked"] == P.bound_blocked(rec["bound_facts"])
         and sorted(_bf["bound_blocked"]) == sorted(rec["bound_blocked"])
         and "m0_system_prompt: blocked:source-missing:m0_system_prompt" in _bf["bound_blocked"]
         and _bf["bound_facts"]["m0_adapter"].get("blocked") is None and _bf["outcome"] == "pass"),
      str({"keys missing": sorted((set(P.M0_BOUND_SOURCE) | set(P.M0_HELPER_SHAPES) | {"m0_adapter"})
                                  - set(_bf.get("bound_facts") or {})),
           "recomputed": rec.get("bound_blocked") == P.bound_blocked(rec.get("bound_facts") or {}),
           "outcome": _bf.get("outcome")})[:600])
_st = C.runs_root / "_a8" / "r1" / "STATUS"
lines = _st.read_text(encoding="utf-8").splitlines() if _st.is_file() else []


def kinds_of(lines_):
    """(event, state) per STATUS line: "STAND _a8 START ..." -> (STAND, START); "START <id> ..." -> (START, None)."""
    out = []
    for ln in lines_:
        p = ln.split()
        if len(p) > 4 and p[2] in ("STAND", "BLOCK"):
            out.append((p[2], p[4]))
        elif len(p) > 2:
            out.append((p[2], None))
    return out


check("Q-DRV-1: the probe-local STATUS runs its full state machine - STAND START, BLOCK START (arm_order mem0, seed 0), "
      "START, END rc 0, BLOCK END, STAND END - and the campaign's own STATUS is never touched",
      ok(lambda: kinds_of(lines) == [("STAND", "START"), ("BLOCK", "START"), ("START", None), ("END", None),
                                     ("BLOCK", "END"), ("STAND", "END")]
         and any("arm_order=mem0" in ln and "seed=0" in ln for ln in lines)
         and any(ln.split()[2] == "END" and "rc=0" in ln for ln in lines)
         and not (C.runs_root / "STATUS").exists()), "\n".join(lines)[:600])
check("Q-DRV-4: the proxy stage is set for the write and cleared after it, as _run_block does",
      ok(lambda: WD.turn_seen["stage"] == [("_a8/b01", "write")] and WD.h.control.log == [("_a8/b01", "write"), (None, None)]),
      str(WD.h.control.log))
check("the turn runs inside its boundary check, which the verdict reads", ok(lambda: WD.turn_seen["w_log"] == [("begin", "a8-mem0-r1")]
      and WD.w_log == [("begin", "a8-mem0-r1"), ("end", "a8-mem0-r1")]), str(WD.w_log))
check("Q-DRV-2: the gate is ProbeGate - StopGate AND GateDriver - and it admitted the unit",
      ok(lambda: WD.turn_seen["gate"] == "ProbeGate" and WD.turn_seen["admits"] is True), str(WD.turn_seen.get("gate")))
check("the write turn: stand _a8, the one run and unit, the declared ops, DEBUG_CEILING_S, the START's id",
      ok(lambda: WD.turn_seen["kw"]["stand"] == "_a8" and WD.turn_seen["kw"]["runs"] == ["r1"]
         and WD.turn_seen["kw"]["units"] == ["u1"] and WD.turn_seen["kw"]["ops_for"]("r1", "u1") == P.M0_OPS
         and WD.turn_seen["kw"]["ceilings"] == {"u1": M.SC.DEBUG_CEILING_S}
         and WD.turn_seen["kw"]["status_ids"] == {"r1": "_a8/b01/r1/mem0"}), str(WD.turn_seen.get("kw", {}).get("status_ids")))
cfg = WD.started["config"]
kf = Path(WD.started["key_file"])
check("Q-DRV-4: the proxy's config - the fakes as both upstreams on 127.0.0.1, test_ollama_upstream (never an edited "
      "dict), mem0 in record mode; a sentinel key file under the probe's own dir, never the secrets root",
      ok(lambda: cfg["upstream"]["host"] == "127.0.0.1" and cfg["upstream"]["tls"] is False
         and cfg["ollama"]["upstream"][0] == "127.0.0.1" and any(a["arm"] == "mem0" and a["mode"] == "record" for a in cfg["arms"])
         and kf.read_text(encoding="utf-8").startswith("DEEPSEEK_API_KEY=nvt3-a8probe-KEYSENTINEL-")
         and str(C.secrets_dir) not in str(kf) and str(C.runs_root / "_a8" / "r1" / "mem0") in str(kf)), str(cfg)[:300])
check("the launcher is the campaign's, on the install record's venv python and the D1 tag, for the one unit",
      ok(lambda: WD.launcher_args == {"python": M.IV.venv_python(VENV), "tag": TAG, "units": ["u1"]}), str(WD.launcher_args))
check("the scheduler is the debug-tagged one with the stand's canaries planted in every home",
      ok(lambda: WD.sched_args[1]["tag"] == "debug" and WD.sched_args[1]["home_canaries"] is not None
         and WD.sched_args[1]["canaries"]), str(WD.sched_args[1].get("tag")))
check("the proxy was stopped", ok(lambda: WD.stopped is True))

print("\n- nothing is started when a precondition is missing -")
for tag, kw, want in (("no_install", {"install": False}, "blocked:not-installed"),
                      ("no_model", {"model": False}, "blocked:model-not-installed"),
                      ("no_d1", {"d1": False}, "blocked:no-embed-pin"),
                      ("no_shape", {"main": MAIN.replace(b'm.get("text", "")', b'm["text"]')}, "blocked:source-missing:m0_item_key")):
    rec, C, VENV, WD, err = run(tag, **kw)
    check(f"{tag}: {want}, probe.json written, no proxy started", ok(lambda: rec["outcome"] == want and WD.started is None
          and (C.runs_root / "_a8" / "r1" / "mem0" / "probe.json").is_file()), f"{err!r} {rec.get('outcome')}")

print("\n- failures are named, and everything still stops -")
rec, C, VENV, WD, err = run("aborted", World(aborted="ceiling"))
check("an aborted unit is a problem by name, and END carries rc 1", ok(lambda: any("aborted ceiling" in p for p in rec["problems"])
      and rec["outcome"] != "pass" and any(ln.split()[2] == "END" and "rc=1" in ln for ln in
                                           (C.runs_root / "_a8" / "r1" / "STATUS").read_text(encoding="utf-8").splitlines())),
      str(rec.get("problems")))
rec, C, VENV, WD, err = run("failed_op", World(failed_op=True))
check("a failed write is a problem by name", ok(lambda: any("a write failed" in p for p in rec["problems"])), str(rec.get("problems")))
rec, C, VENV, WD, err = run("crash", World(raise_in_turn=True))
check("a raise inside the turn is named, the stage is cleared, the check closed, the proxy stopped",
      ok(lambda: any("the probe did not complete: RuntimeError" in p for p in rec["problems"])
         and WD.h.control.log[-1] == (None, None) and WD.w_log[-1][0] == "end" and WD.stopped is True
         and rec["outcome"] == "fail"), str(rec.get("problems")))
c3, _ = world("interrupt")
wd3 = World(interrupt=True)
try:
    P.run_mem0_probe(c3, L, run="r1", install_run="i1", model_run="m1", deps=wd3.deps())
    how = "returned"
except KeyboardInterrupt:
    how = "interrupted"
check("an interrupt (not an Exception) still stops the proxy on its way out - the stop is in a finally",
      how == "interrupted" and wd3.stopped is True, f"{how} stopped={wd3.stopped}")
rec, C, VENV, WD, err = run("pstop", World(pstop_rc=3))
check("a proxy that did not stop by itself is a problem", ok(lambda: any("did not stop by itself" in p for p in rec["problems"])))
rec, C, VENV, WD, err = run("trap", World(trap=True))
check("a trap the fake Ollama saw (a pull) is a problem by name", ok(lambda: any("saw a trap" in p for p in rec["problems"])
      and rec["fake_traps"] == [{"method": "POST", "path": "/api/pull"}]), str(rec.get("problems")))
rec, C, VENV, WD, err = run("nomodel", World(model=None))
check("a scheduler probe that names no model stops before any unit - no gate without an expected model",
      ok(lambda: any("named no model" in p for p in rec["problems"]) and WD.turn_seen == {}), str(rec.get("problems")))
rec, C, VENV, WD, err = run("temp", World(temperature=0.9))
check("the fields read the run's own calls (a temperature the source does not set fails m0_temperature)",
      ok(lambda: rec["fields"]["m0_temperature"]["ok"] is False and rec["outcome"] == "fail"), str(rec.get("reasons")))
rec, C, VENV, WD, err = run("stopgate", World(stops={"mem0": {"upstream_statuses": {"401": 1}}}))
_sl = (C.runs_root / "_a8" / "r1" / "STATUS").read_text(encoding="utf-8").splitlines()
check("Q-DRV-2: a StopGate stop (a 401 seen) keeps the unit from starting, named", ok(lambda: WD.turn_seen == {}
      and any("did not complete" in p for p in rec["problems"])), str(rec.get("problems")))
c2, _ = world("used")
(c2.runs_root / "_a8" / "r1" / "mem0").mkdir(parents=True)
(c2.runs_root / "_a8" / "r1" / "mem0" / "probe.json").write_text("{}", encoding="utf-8")
wd2 = World()
try:
    P.run_mem0_probe(c2, L, run="r1", install_run="i1", model_run="m1", deps=wd2.deps())
    used = "accepted"
except P.ProbeError as e:
    used = str(e)
check("a probe record is written once - a second run of the label is refused before anything starts",
      "written once" in used and wd2.started is None, f"{used} started={wd2.started is not None}")

print("\n- the auditor's D2, D8, D13, D24, D26, D27 -")
for tag, d1rec in (("d1_problem", {"problems": ["e3: a model removed"], "tag": {"name": TAG, "digest": DIG}}),
                   ("d1_nodigest", {"problems": [], "tag": {"name": TAG}}), ("d1_noname", {"problems": [], "tag": {"digest": DIG}})):
    rec, C, VENV, WD, err = run(tag, d1_record=d1rec)
    check(f"D2: a D1 record {tag.split('_', 1)[1]} is blocked:no-embed-pin, with nothing started",
          ok(lambda: rec["outcome"] == "blocked:no-embed-pin" and WD.started is None), f"{err!r} {rec.get('outcome')}")
rec, C, VENV, WD, err = run("unserved", World(unserved=True))
check("D8: a path the fake Ollama does not serve, when asked, is a named problem",
      ok(lambda: any("saw an unserved path" in p and "/api/version" in p for p in rec["problems"])), str(rec.get("problems")))
rec, C, VENV, WD, err = run("timeless", World(timeless=True))
check("D13: the gate's own problem (a call it cannot time) comes into the probe's problems as gate: ...",
      ok(lambda: any(p.startswith("gate: ") and "no readable time" in p for p in rec["problems"])), str(rec.get("problems")))
rec, C, VENV, WD, err = run("catcher", World(catcher=True))
check("D24: a catcher line of the probe's proxy reaches m0_nlp_active (a run-time download) and is the outcome",
      ok(lambda: rec["fields"]["m0_nlp_active"].get("blocked") == "blocked:nlp-off" and rec["outcome"] == "blocked:nlp-off"),
      f"{rec.get('outcome')} {rec.get('fields', {}).get('m0_nlp_active')}")
rec, C, VENV, WD, err = run("dirty_check", World(dirty_check=True))
check("D26: what the turn's boundary check says reaches the verdict - a watched-set change is a problem naming the check",
      ok(lambda: rec["outcome"] == "fail" and any("the probe check counted 1 change" in r for r in rec["reasons"])),
      str(rec.get("reasons")))
rec, C, VENV, WD, err = run("bad_record", World(bad_record=True))
check("D27: a proxy record that cannot be read is a named problem (proxy record: ...)",
      ok(lambda: any(p.startswith("proxy record: ") for p in rec["problems"])), str(rec.get("problems")))

print("\n- ProbeGate alone -")


class G1:
    def __init__(self, v):
        self.v = v

    def admits_new_unit(self):
        if isinstance(self.v, Exception):
            raise self.v
        return self.v


check("both admitting admits", P.ProbeGate(G1(True), G1(True)).admits_new_unit() is True)
check("the driver refusing refuses", P.ProbeGate(G1(True), G1(False)).admits_new_unit() is False)
try:
    P.ProbeGate(G1(RuntimeError("D-AB-8: 401")), G1(True)).admits_new_unit()
    stopped = "admitted"
except RuntimeError as e:
    stopped = str(e)
check("StopGate refusing (it raises) stops, whatever the driver says", "D-AB-8" in stopped, stopped)
check("no row's condition raised", RAISED == [], str(RAISED))
print(f"\nv3 probe driver: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
