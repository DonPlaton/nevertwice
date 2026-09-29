#!/usr/bin/env python3
"""PREREG-V3 A8 C5a-2c (the auditor's e2e ruling, 08:2x): probe_a8.run_mem0_probe over the campaign's REAL wiring -
this suite SPAWNS CHILDREN (a venv's creation, the mem0 adapter child), so it runs only under the battery lock. The
real scheduler, the real PlanLauncher (the adapter staged by CodeStager, spawned through launch.spawn under a
temporary contract), the real recording proxy's code (in this process, as _test_v3_run_smoke's), probe_upstream's
fakes as its upstreams; the product is the fake mem0 (tests/fixtures/v3_fake_products) inside a real venv, beside a
synthetic mem0 source tree the probe's facts read:

* E1: probe.json is written, once - a second run of the label is refused;
* E2: the real proxy's records of the unit land in calls.jsonl with the stage the driver set ("_a8/b01", "write"),
  every mem0 line under /u/r1.u1;
* E3: the adapter's counters carry "nlp" (read, never loaded: the fake has no spacy_models, so m0_nlp_active is
  blocked:nlp-off - the outcome, by name) and llm_usage, which equals the proxy's answered lines (m0_usage passes);
* E4: the fakes saw the adapter's traffic only through the /u/ legs - every fake DeepSeek request is a recorded call,
  every fake Ollama request a recorded leg line of the unit, and no trap or unserved path;
* E5: the probe-local STATUS closed START..END, the turn's boundary check is complete with 0/0, no catcher line.

    python tests/research/_test_v3_probe_driver_e2e.py
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


P = _load("v3_probe_a8_e2e_t", ROOT / "research" / "v3" / "probe_a8.py")
L = _load("v3_launch_e2e_t", ROOT / "research" / "v3" / "launch.py")
PX = _load("v3_llm_proxy_e2e_t", ROOT / "research" / "_llm_proxy.py")
M = P.probe_modules()
SCTL = _load("v3_sched_ctl_e2e_t", ROOT / "research" / "v3" / "sched_ctl.py")
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


class Quiet:
    """The native witness's sampler with nothing to see (its own suites test the real one)."""

    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


TMP = Path(tempfile.mkdtemp(prefix="nvt3_probe_driver_e2e_"))
atexit.register(shutil.rmtree, TMP, True)
POLY = TMP / "polygon"
ARM_PY = Path(getattr(sys, "_base_executable", None) or sys.executable)
EXC = {sys.executable: "the test interpreter", str(ARM_PY): "the fake product venv's base interpreter"}
# B-E2E-REALEXC (CI research tier, no venv): a base interpreter reached by a symlink (setup-python's bin/python), a
# POSIX venv's bin/python links to it, and launch compares an exception with the written AND the real path of the
# executable (.../python3.13) - so each exception is named by its real path too; launch itself is not widened
for _p, _why in ((sys.executable, "the test interpreter"), (ARM_PY, "the fake product venv's base interpreter")):
    EXC.setdefault(os.path.realpath(_p), f"{_why} (its real path)")
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)
for d in ("owner_home", "watched"):
    (TMP / d).mkdir()
(TMP / "watched" / "idle.txt").write_bytes(b"idle")
C = L.Contract(polygon_root=POLY, runs_root=POLY / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner_home",
               secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine", conservation_root=TMP / "conservation",
               system_dirs=SYSTEM, binary_exceptions=EXC)
TAG, DIG = "nvt3-bge-m3-d1:latest", "561ce53b" + "0" * 56

# the venv: a real one (no pip), the fake mem0 inside it, the synthetic mem0 source the probe's facts read beside it
VENV = POLY / "mem0_v3"
subprocess.run([str(ARM_PY), "-m", "venv", "--without-pip", str(VENV)], check=True, capture_output=True, timeout=300)
SITE = next(s for s in VENV.rglob("site-packages") if s.is_dir())
shutil.copytree(ROOT / "tests" / "fixtures" / "v3_fake_products" / "mem0", SITE / "mem0")
for rel, data in {
        "mem0/memory/main.py": (b"class Memory:\n    def add(self, messages, timestamp=None):\n        if timestamp is not None:\n"
                                b"            raise ValueError(get_temporal_feature_error_message(\"sync\", \"add\", \"timestamp\"))\n\n"
                                b"    def _add_to_vector_store(self, messages):\n        response = self.llm.generate_response(\n"
                                b"            messages=messages,\n            response_format={\"type\": \"json_object\"},\n        )\n"
                                b"        items = json.loads(response, strict=False).get(\"memory\", [])\n"
                                b"        mem_texts = [m.get(\"text\", \"\") for m in items if m.get(\"text\")]\n"),
        "mem0/configs/llms/deepseek.py": b"class C:\n    def __init__(\n        self,\n        temperature: float = 0.1,\n    ):\n        pass\n",
        "mem0/llms/deepseek.py": b"class DeepSeekLLM:\n    pass\n",
        "mem0/utils/spacy_models.py": (b"_nlp_full = None\n_nlp_lemma = None\n_load_failed_full = False\n_load_failed_lemma = False\n\n\n"
                                       b"def _ensure_model_available():\n    import spacy\n    if not spacy.util.is_package(\"en_core_web_sm\"):\n"
                                       b"        download(\"en_core_web_sm\")\n")}.items():
    (SITE / rel).parent.mkdir(parents=True, exist_ok=True)
    (SITE / rel).write_bytes(data)
RUNS = C.runs_root
for rel, rec in {"_install/a8-pypi-mem0_v3/i1/install_record.json":
                 {"problems": [], "venv": str(VENV), "installed_set_sha256": "a" * 64,
                  "import_versions": {"dists": {"mem0ai": "2.2.0"}}},
                 "_install/a8-spacy-model/m1/model_record.json": {"problems": [], "model_installed_set_sha256": "b" * 64},
                 "_d1tag/d1/record.json": {"problems": [], "tag": {"name": TAG, "digest": DIG}}}.items():
    (RUNS / rel).parent.mkdir(parents=True, exist_ok=True)
    (RUNS / rel).write_text(json.dumps(rec), encoding="utf-8")

PROXIES: list = []


def start_proxy(c, *, python, key_file, config, secrets, unit, parent_env, witnesses):
    """The real recording proxy's code, in this process (as _test_v3_run_smoke's): the config the driver built, loaded
    in test-upstream mode because the key file is the driver's sentinel, never a secret."""
    run_dir = Path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    cfgp = run_dir / "proxy_config.json"
    cfgp.write_bytes(json.dumps(dict(config)).encode("utf-8"))
    pc = PX.ProxyConfig.load(cfgp, dict(secrets), test_upstream_ok=True)
    px = PX.Proxy(pc, PX.read_key(key_file), canaries=secrets.get("canaries") or {}, log=lambda m: None)
    ports = px.start()
    PROXIES.append(px)
    return M.RP.ProxyHandle(child=None, ports=ports, control=SCTL.ProxyControl(ports["control"], secrets["control_token"]),
                            tokens=dict(secrets["tokens"]), run_dir=run_dir)


def stop_proxy(h):
    left = PROXIES[-1].stop()
    return {"rc": 0 if not left else 1, "killed": False, "shutdown_error": None}


SEEN: dict = {}
_orig_ds, _orig_ol = M.U.FakeDeepSeek, M.U.FakeOllama


class DS(_orig_ds):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        SEEN["ds"] = self


class OL(_orig_ol):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        SEEN["ol"] = self


M.U.FakeDeepSeek, M.U.FakeOllama = DS, OL          # the same fakes, kept so the rows can read what they saw
real = P.real_deps(C, L, environ={k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
                                                                                         "COMSPEC", "PATHEXT", "PATH")},
                   proxy_python=Path(sys.executable))
deps = P.ProbeDeps(modules=M, environ=real.environ, proxy_python=Path(sys.executable),
                   native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
                   fs=L.FsWitness([L.WatchSpec("watched", TMP / "watched")]), start_proxy=start_proxy, stop_proxy=stop_proxy,
                   post=M.RP.post, make_witnesses=real.make_witnesses, make_scheduler=real.make_scheduler,
                   make_launcher=real.make_launcher, clock=real.clock)

print("- the mem0 probe over the real wiring (children) -")
try:
    rec = P.run_mem0_probe(C, L, run="r1", install_run="i1", model_run="m1", deps=deps)
    err = None
except Exception as e:  # noqa: BLE001 - the rows read it
    rec, err = {}, e
dest = RUNS / "_a8" / "r1" / "mem0" / "probe.json"
check("E1: probe.json is written", ok(lambda: err is None and json.loads(dest.read_text(encoding="utf-8"))["arm"] == "mem0"),
      f"{err!r} {rec.get('problems')}")
proxy_dir = RUNS / "_a8" / "r1" / "mem0" / "_proxy" / "run"
calls = [json.loads(x) for x in (proxy_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()] \
    if (proxy_dir / "calls.jsonl").is_file() else []
mine = [c for c in calls if c.get("arm") == "mem0"]
check("E2: the real proxy recorded the unit's calls with the stage the driver set, every one under /u/r1.u1",
      ok(lambda: len(mine) >= 1 and all(c.get("block") == "_a8/b01" and c.get("stage") == "write" and c.get("unit") == "r1.u1"
                                        for c in mine)), str([(c.get("block"), c.get("stage"), c.get("unit")) for c in mine])[:300])
F = rec.get("fields") or {}
check("E3: the adapter's llm_usage equals the proxy's answered lines (m0_usage passes) and its client is the wrapped one",
      ok(lambda: F["m0_usage"]["ok"] is True and F["m0_client"]["ok"] is True), str({k: (F.get(k) or {}).get("rule_failed")
                                                                                   for k in ("m0_usage", "m0_client")}))
check("E3: the adapter's counters carry spaCy's state, read: the fake has no spacy_models, so m0_nlp_active is "
      "blocked:nlp-off - and that is the outcome, by name", ok(lambda: F["m0_nlp_active"].get("blocked") == "blocked:nlp-off"
                                                                and "module" in F["m0_nlp_active"]["rule_failed"]
                                                                and rec["outcome"] == "blocked:nlp-off"),
      f"{rec.get('outcome')} {(F.get('m0_nlp_active') or {}).get('rule_failed')}")
ds, ol = SEEN.get("ds"), SEEN.get("ol")
oll = [json.loads(x) for x in (proxy_dir / "ollama.jsonl").read_text(encoding="utf-8").splitlines()] \
    if (proxy_dir / "ollama.jsonl").is_file() else []
check("E4: the fakes saw the traffic only through the proxy - every fake DeepSeek request a recorded call, every fake "
      "Ollama request a leg line of the unit - and no trap, no unserved path",
      ok(lambda: len(ds.requests) == len(calls) and len(ol.requests) == len([o for o in oll if o.get("unit") == "r1.u1"])
         and ol.traps == [] and ol.unknown == []),
      f"ds {len(ds.requests) if ds else None} calls {len(calls)}; ol {len(ol.requests) if ol else None} leg {len(oll)}")
check("E5: the probe-local STATUS closed START..END, the boundary check is complete with 0/0, no catcher line",
      ok(lambda: "END _a8/b01/r1/mem0" in (RUNS / "_a8" / "r1" / "STATUS").read_text(encoding="utf-8")
         and not any("the probe check" in r for r in rec["reasons"])
         and not any("catcher line(s) of the arm" in r for r in rec["reasons"])),
      str(rec.get("reasons"))[:400])
try:
    P.run_mem0_probe(C, L, run="r1", install_run="i1", model_run="m1", deps=deps)
    again = "accepted"
except P.ProbeError as e:
    again = str(e)
check("E1: probe.json is written once - a second run of the label is refused", "written once" in again, again)
check("no row's condition raised", RAISED == [], str(RAISED))
print(f"\nv3 probe driver e2e: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
