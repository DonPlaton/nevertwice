#!/usr/bin/env python3
"""PREREG-V3 A9 (the driver plan's A5; the auditor's Q1-Q12 = (a)): research/v3/run_v3_pilot.py - the S1 pilot end to end
through run_v3._drive_stand: the real scheduler, STATUS writer, hooks, incident gate, stand plan and answerer, a real
recording proxy (in this process) over a fake upstream, and fake arms as children under a temporary launch contract
(this suite spawns children: under the lock). The fake arms seal their store the way the adapters do
(base.write_seal: a manifest of the store's files), so M34 is measured; a rendered V (M33) and the V≡ pointer are read
on a stand result built in this process.

* T-PLT-1: S1-smoke-1 runs clean (exit 0), STATUS START..END, and <runs>/S1-smoke-1/_pilot/<attempt>/ holds reads.json,
  m33.json, m34.json, pilot.json and an artifact per point B, K and V;
* T-PLT-2: each arm is read at its points - ours at B, K and V (its own V read, k 3), the floor at B alone (rev1 §3.2) -
  every read answered;
* T-PLT-3 (M34): every unit's directory store after its reads is its seal's - changed False, no finding;
* T-PLT-4: the artifacts are S1's (the base stand), V's holds ours alone - the floor is named in v_not_read - with V's
  declared axis k, and no row carries a context's text;
* T-PLT-5 (M34): a store written during the reads is a finding by name (the unit, the path), never "unchanged";
* T-PLT-6 (M33): a rendered V's v_cut summed per unit and per stand; an unmeasured one a problem by name;
* T-PLT-7 (T-RV, the auditor): the artifacts are built on the base stand S1 from the stand id S1-smoke-1 - V's too - a
  V≡ arm is its pointer, a refused arm is in v_not_read;
* T-PLT-8 (R7): an artifact target inside research/v3/results is refused;
* T-PLT-9: arms outside the run config, or a declared V≡ that does not hold, refuse the pilot by name before anything
  runs.

    python tests/research/_test_v3_run_pilot.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
import psutil  # noqa: F401,E402  the research extra - the units die by tree (as run_smoke's suite)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RV = _load("v3_run_v3_for_pilot", ROOT / "research" / "v3" / "run_v3.py")    # the pilot's own run_v3 instance
try:
    PI = _load("v3_run_v3_pilot_t", ROOT / "research" / "v3" / "run_v3_pilot.py")
except Exception as e:  # noqa: BLE001 - no pilot module: every row FAILs by name
    PI, PI_ERR = None, f"{type(e).__name__}: {e}"
else:
    PI_ERR = None
L = RV.load("launch.py", smoke=True)
SC = RV.load("scheduler.py", smoke=True)
P = RV.load("run_v3_proxy.py", smoke=True)
TP = RV.load("templates.py", smoke=True)
PL = RV.load("run_v3_plan.py", smoke=True)
A = RV.load("artifact.py", smoke=True)
SCTL = RV.load("sched_ctl.py", smoke=True)
SS = _load("v3_subsample_plt", ROOT / "research" / "v3" / "subsample.py")
PX = _load("v3_llm_proxy_plt", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_plt", ROOT / "research" / "_llm_proxy_selftest.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def val(fn, default=None):
    """The call's value, or ``default`` when it raises (the row then FAILs by its condition, never the suite)."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"       ({type(e).__name__}: {e})")
        return default


def err_of(fn) -> str:
    try:
        return f"returned {fn()!r}"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


FAKE_ARM = r'''
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))


class H:
    def __init__(self):
        self.items, self.reads = [], 0
        if spec["stage"] == "read":
            with open(os.path.join(spec["write_dir"], "store", "items.json"), encoding="utf-8") as f:
                self.items = json.load(f)

    def hello(self):
        return {"protocol": B.PROTOCOL, "arm": spec["arm"], "stage": spec["stage"], "pid": os.getpid()}

    def write(self, item, date=None):
        self.items.append(item.get("text") or "")
        return {"op_id": item.get("item_id")}

    def end_write(self):
        store = os.path.join(os.getcwd(), "store")
        os.makedirs(store, exist_ok=True)
        with open(os.path.join(store, "items.json"), "w", encoding="utf-8") as f:
            json.dump(self.items, f)
        seal = B.write_seal(os.getcwd(), store, arm=spec["arm"], run=spec["run"], unit=spec["unit"])
        return {"footprint": {"retrievable": len(self.items)}, "seal": seal}

    def read(self, qid, query, k=10, point=None):
        self.reads += 1
        if spec.get("mutate") and self.reads == 1:           # a product that writes its store while it reads
            with open(os.path.join(spec["write_dir"], "store", "read-log.txt"), "w", encoding="utf-8") as f:
                f.write(qid)
        return {"qid": qid, "items": [{"text": t, "rank": i + 1} for i, t in enumerate(self.items[:k])],
                "point": point}

    def counters(self):
        return {}


sys.exit(B.main_with(H))
'''

V1 = json.dumps({"id": "c1", "model": "deepseek-v4-flash", "system_fingerprint": "fp_pilot",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "It was blue.\nSHORT ANSWER: blue\n"},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 40, "completion_tokens": 6, "prompt_cache_hit_tokens": 0,
                           "prompt_cache_miss_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 0}}}).encode()
BAL = json.dumps({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "100.00"}]}).encode()


class Upstream(ST.FakeUpstream):
    def _serve(self, c, path):
        if path.split(b"?", 1)[0].endswith(b"/chat/completions"):
            body = V1
        elif path.startswith(b"/user/balance"):
            body = BAL
        else:
            return super()._serve(c, path)
        self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                   + b"\r\n\r\n" + body)
        return True


class Quiet:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


TMP = Path(tempfile.mkdtemp(prefix="nvt3_run_pilot_"))
POLY = TMP / "polygon"
ARM_PY = Path(getattr(sys, "_base_executable", None) or sys.executable)
GIT = shutil.which("git")
EXC = {sys.executable: "the test interpreter", str(ARM_PY): "the fake arms' base interpreter"}
if GIT:
    EXC[GIT] = "git, for the tree check"
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)
(TMP / "owner_home").mkdir()
(TMP / "watched").mkdir()
(TMP / "watched" / "idle.txt").write_bytes(b"idle")
C = L.Contract(polygon_root=POLY, runs_root=POLY / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner_home",
               secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine", conservation_root=TMP / "conservation",
               system_dirs=SYSTEM, binary_exceptions=EXC)
FAKE_DIR = POLY / "_fake"
FAKE_DIR.mkdir(parents=True)
(FAKE_DIR / "base.py").write_bytes((ROOT / "research" / "v3" / "arms" / "base.py").read_bytes())
(FAKE_DIR / "fake_arm.py").write_text(FAKE_ARM, encoding="utf-8")
KEYFILE = TMP / "keys" / "deepseek.env"
KEYFILE.parent.mkdir()
KEYFILE.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())
ORDER = [f"q{i:03d}" for i in range(500)]
LISTS = TMP / "lists"
LISTS.mkdir()
S1 = SS.list_record("S1", ORDER, seed=SS.SEED, rule=SS.RULE)
(LISTS / "S1.json").write_text(json.dumps(S1), encoding="utf-8")


def lme_rec(i: int) -> dict:
    return {"question_id": f"q{i:03d}", "question_type": "multi-session", "question": f"what colour was thing {i}?",
            "question_date": "2023/05/22 (Mon) 10:00", "haystack_session_ids": [f"s{i}a", f"s{i}b"],
            "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"],
            "haystack_sessions": [[{"role": "user", "content": f"thing {i} is blue"},
                                   {"role": "assistant", "content": "noted"}],
                                  [{"role": "user", "content": f"more about {i}"}]]}


T1 = TP.Template(stand="S1", text="Today is {question_date}.\nContext:\n{context}\nQuestion: {question}\n"
                 "End with SHORT ANSWER: <answer>", sha256="2" * 64, source_pin="test", source_sha256="2" * 64,
                 slots=("question_date", "context", "question"))
TEMPLATE = TP.Template(stand="S4", text="Context:\n{context}\nQuestion: {question}", sha256="0" * 64, source_pin="test",
                       source_sha256="0" * 64, slots=("context", "question"))
PROXIES: list = []


def start_proxy(c, *, python, key_file, config, secrets, unit, parent_env, witnesses):
    run_dir = Path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    cfgp = run_dir / "proxy_config.json"
    cfgp.write_bytes(json.dumps(dict(config)).encode("utf-8"))
    pc = PX.ProxyConfig.load(cfgp, dict(secrets), test_upstream_ok=True)
    px = PX.Proxy(pc, PX.read_key(key_file), canaries=secrets.get("canaries") or {}, log=lambda m: None)
    ports = px.start()
    PROXIES.append({"px": px})
    return P.ProxyHandle(child=None, ports=ports, control=SCTL.ProxyControl(ports["control"], secrets["control_token"]),
                         tokens=dict(secrets["tokens"]), run_dir=run_dir)


def stop_proxy(h):
    left = PROXIES[-1]["px"].stop()
    return {"rc": 0 if not left else 1, "killed": False, "shutdown_error": None}


def plan_factory(mutate: set = frozenset()):
    """make_plan_launcher: an unbound plan launcher over a fake child (points checked, bound once, then built)."""
    class Fake:
        def __init__(self, arm, ar, *, stand_id, unit_block, unit_chars, v_template=None):
            self.arm, self.proxy = arm, None
            self.spec = SimpleNamespace(store_persistence="disk")

        def check_points(self, points):
            return None

        def bind_proxy(self, h):
            self.proxy = h

        def launcher(self, _cls):
            arm = self.arm

            def spec_for(stage, *, stand, run, unit, dirs, write_dirs):
                return {"arm": arm, "stage": stage, "run": run, "unit": unit, "mutate": arm in mutate,
                        "write_dir": str(write_dirs.cwd) if write_dirs is not None else None}
            return SC.ChildArmLauncher(arm, argv_for=lambda p, **_: [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py"),
                                                                       str(p)],
                                       spec_for=spec_for, store_persistence="disk", path_dirs=(str(ARM_PY.parent),),
                                       declared={}, token_names=(), declared_for=lambda *a, **k: {}, reads_point=True)

        def record(self, **_kw):
            return {"arm": self.arm, "fake": True}
    return Fake


def deps_for(up, *, out: list, err: list, mutate: set = frozenset()):
    return RV.SmokeDeps(
        contract=C, L=L, native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", TMP / "watched")]), clock=SC.SystemClock(), ollama_ctl=None,
        monotonic=__import__("time").monotonic, environ=dict(os.environ), status_path=TMP / "STATUS",
        lme_records=lambda: [lme_rec(i) for i in range(500)], cl100k=(lambda s: max(1, len(s) // 4),
                                                                      lambda s, n: s[:max(0, 4 * n)]),
        cl100k_source="t" * 64, max_token_bytes=128,
        truncate=lambda text: SimpleNamespace(text=text, truncated=False), templates=(TEMPLATE, TEMPLATE),
        locomo_question=lambda q, cat: q, decl=lambda py, *, arm: {"python": str(py), "arm": arm, "version": "test"},
        make_launcher=None, start_proxy=start_proxy, stop_proxy=stop_proxy, post=P.post,
        proxy_route={"test_upstream": {"host": "127.0.0.1", "port": up.port, "tls": False}},
        now_utc=lambda: "2026-09-30T00:00:00Z", lists_dir=LISTS, s1_sha256=S1["ids_sha256"],
        out=out.append, err=err.append, make_plan_launcher=plan_factory(mutate),
        stand_templates=lambda s: T1, arm_template=None, lme_sha256="e" * 64)


CFG = RV.RunConfig(proxy_python=Path(sys.executable), key_file=KEYFILE, git=Path(GIT or "git"), campaign_seed=20260930,
                   embed_tag="nvt3-bge-m3-d1:latest", ollama_url="http://127.0.0.1:11434",
                   arms={"bm25-floor": RV.ArmRun(python=ARM_PY, llm=None, llm_transport=None, embeds_via_ollama=False),
                         "nevertwice": RV.ArmRun(python=ARM_PY, llm="deepseek-flash", llm_transport="cloud:deepseek",
                                                 embeds_via_ollama=False)},
                   sha256="c" * 64)


def attempt_dir(stand_id: str) -> Path:
    ds = sorted((C.runs_root / stand_id / "_pilot").glob("attempt-*"))
    return ds[-1] if ds else C.runs_root / stand_id / "_pilot" / "none"


def jload(p: Path, default=None):
    try:
        return json.loads(p.read_bytes())
    except (OSError, ValueError):
        return default


up = Upstream()
try:
    if PI is None:
        check("the pilot module loads", False, PI_ERR)
    print("- T-PLT-9: refusals before anything runs -")
    st0 = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    r9a = err_of(lambda: PI.run_pilot(CFG, arm_names=["nevertwice", "letta"], runs=["r1"],
                                       deps=deps_for(up, out=[], err=[]))) if PI else PI_ERR
    PT9 = PL._mod("v3_points_for_plan", "points.py")
    saved9 = copy.deepcopy(PT9.V_DEFAULTS)
    PT9.V_DEFAULTS["nevertwice"] = {**PT9.V_DEFAULTS["nevertwice"], "equivalent_to": "B"}     # k 3 is not B's 200
    try:
        r9b = err_of(lambda: PI.run_pilot(CFG, arm_names=["nevertwice"], runs=["r1"],
                                           deps=deps_for(up, out=[], err=[]))) if PI else PI_ERR
    finally:
        PT9.V_DEFAULTS.clear()
        PT9.V_DEFAULTS.update(saved9)
    check("T-PLT-9: arms outside the run config, and a declared V≡ that does not hold, refuse the pilot by name "
          "(CLIError) - no STATUS line, no proxy",
          r9a.startswith("CLIError") and "letta" in r9a and r9b.startswith("CLIError") and "not equivalent" in r9b
          and ((TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else "") == st0
          and PROXIES == [], f"{r9a} | {r9b}")

    print("\n- T-PLT-10, T-PLT-11 (A6): the command line -")
    none_cfg = str(TMP / "none.json")

    def cli(argv):
        return err_of(lambda: PI.main(argv)) if PI and hasattr(PI, "main") else "no main"
    cl = {"S4": cli(["--stand", "S4", "--arms", "nevertwice", "--runs", "r1", "--config", none_cfg]),
          "mem0 without": cli(["--stand", "S1", "--arms", "nevertwice,mem0", "--runs", "r1", "--config", none_cfg]),
          "probe without mem0": cli(["--stand", "S1", "--arms", "nevertwice", "--runs", "r1", "--config", none_cfg,
                                     "--mem0-probe-run", "p1"])}
    check("T-PLT-10 (A6): the pilot's command line refuses a stand other than S1, mem0 without --mem0-probe-run and a "
          "probe run without mem0 - each by the CLI's own refusal (CLIError), before the run config is read",
          cl["S4"].startswith("CLIError") and "S1 alone" in cl["S4"]
          and all(cl[k].startswith("CLIError: --mem0-probe-run") for k in ("mem0 without", "probe without mem0"))
          and all("none.json" not in v for v in cl.values()), str(cl)[:500])
    import ast  # noqa: E402
    mfn = next((n for n in ast.parse((ROOT / "research" / "v3" / "run_v3_pilot.py").read_text(encoding="utf-8")).body
                if isinstance(n, ast.FunctionDef) and n.name == "main"), None) if PI else None
    calls = [ast.unparse(n.func).split(".")[-1] for n in ast.walk(mfn) if isinstance(n, ast.Call)] if mfn else []
    want = ["check_mem0_probe_run", "read_run_config", "default", "load_run_config", "run_pilot"]
    pos = [calls.index(w) if w in calls else -1 for w in want]
    check("T-PLT-11 (A6, F4, M30): main's order - --mem0-probe-run checked, the run config read, then the machine's "
          "contract, the config parsed, and the pilot run on cli_deps (writer bounds from the probe record)",
          all(x >= 0 for x in pos) and pos == sorted(pos) and "cli_deps" in calls, str((pos, calls))[:300])

    print("\n- T-PLT-1..4: a clean S1 pilot of ours and the floor -")
    out1: list = []
    err1: list = []
    rc1 = err_of(lambda: PI.run_pilot(CFG, arm_names=["nevertwice", "bm25-floor"], runs=["r1"],
                                       deps=deps_for(up, out=out1, err=err1))) if PI else PI_ERR
    ad = attempt_dir("S1-smoke-1")
    status1 = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    files1 = sorted(p.relative_to(ad).as_posix() for p in ad.rglob("*") if p.is_file()) if ad.exists() else []
    check("T-PLT-1: S1-smoke-1 runs clean (exit 0), STATUS START..END, and its _pilot attempt holds reads.json, "
          "m33.json, m34.json, pilot.json and an artifact per point",
          rc1 == "returned 0" and "S1-smoke-1" in status1 and all(f in files1 for f in (
              "reads.json", "m33.json", "m34.json", "pilot.json", "artifacts/S1_B_product.json",
              "artifacts/S1_K_product.json", "artifacts/S1_V_product.json")), f"{rc1} {err1[:4]} {files1}")
    reads = jload(ad / "reads.json", {})
    check("T-PLT-2: ours is read at B, K and V (its own V read), the floor at B alone (rev1 §3.2) - 20 reads a point, "
          "every one answered",
          set(reads.get("nevertwice") or {}) == {"B", "K", "V"} and set(reads.get("bm25-floor") or {}) == {"B"}
          and all(c.get("reads") == 20 and c.get("answered") == 20 for a in reads.values() for c in a.values()),
          json.dumps(reads)[:400])
    m34 = jload(ad / "m34.json", {})
    pj = jload(ad / "pilot.json", {})
    cells = [c for a in m34.values() for r in a.values() for c in r.values()]
    check("T-PLT-3 (M34): every unit's directory store after its reads is its seal's - 40 cells, changed False, no "
          "finding and no problem", len(cells) == 40 and all(isinstance(c, dict) and c.get("kind") == "directory"
                                                              and c.get("changed") is False for c in cells)
          and pj.get("findings") == [] and pj.get("problems") == [], f"{len(cells)} {str(cells[:2])[:300]}")
    arts = {p_: jload(ad / "artifacts" / f"S1_{p_}_product.json", {}) for p_ in ("B", "K", "V")}
    texts = json.dumps(arts)
    check("T-PLT-4: the artifacts are S1's (the base stand) - B holds both arms, K and V ours alone, V's declared axis "
          "k - the floor is named in v_not_read, and no artifact carries a context's text",
          all(a_.get("stand") == "S1" for a_ in arts.values()) and set(arts["B"].get("arms") or {}) == {
              "nevertwice", "bm25-floor"} and set(arts["K"].get("arms") or {}) == {"nevertwice"}
          and set(arts["V"].get("arms") or {}) == {"nevertwice"} and arts["V"].get("declared_axes") == ["k"]
          and "bm25-floor" in (pj.get("v_not_read") or {}) and "thing 481 is blue" not in texts,
          str({p_: (a_.get("stand"), sorted(a_.get("arms") or {})) for p_, a_ in arts.items()})[:300])

    print("\n- T-PLT-5 (M34): a store written during the reads -")
    out5: list = []
    err5: list = []
    rc5 = err_of(lambda: PI.run_pilot(CFG, arm_names=["bm25-floor"], runs=["r1"],
                                       deps=deps_for(up, out=out5, err=err5, mutate={"bm25-floor"}))) if PI else PI_ERR
    pj5 = jload(attempt_dir("S1-smoke-2") / "pilot.json", {})
    m345 = jload(attempt_dir("S1-smoke-2") / "m34.json", {})
    changed5 = [c for r in (m345.get("bm25-floor") or {}).values() for c in r.values()
                if isinstance(c, dict) and c.get("changed")]
    check("T-PLT-5 (M34): a product that writes its store while it reads is a finding by name - the unit and the path "
          "read-log.txt - every cell changed, never folded into unchanged; a finding is no failure",
          rc5 == "returned 0" and len(changed5) == 20 and all("read-log.txt" in c.get("changed_paths", []) for c in changed5)
          and len(pj5.get("findings") or []) == 20 and all("read-log.txt" in f for f in pj5.get("findings") or [])
          and any(o.startswith("finding: M34:") for o in out5), f"{rc5} {err5[:3]} {(pj5.get('findings') or [])[:2]}")

    print("\n- T-PLT-6..8: M33, the artifacts on the base stand, R7 - in this process -")

    def ans(point, *, shape="items", v_cut=None, qid="q1", last_cut=False):
        a_ = {"qid": qid, "point": point, "shape": shape, "last_cut": last_cut, "context_sha256": "c" * 64}
        if v_cut is not None:
            a_["v_cut"] = v_cut
        return a_
    VC = {"tokens_full": 9000, "kept_chars": 100, "facts": 2, "facts_whole": 1, "facts_partial": 1, "facts_lost": 0,
          "entities": 1, "entities_whole": 0, "entities_partial": 0, "entities_lost": 1}
    res6 = {"blocks": [{"write": {"zep-graphiti": {("r1", "u1"): SimpleNamespace(seal=None),
                                                   ("r1", "u2"): SimpleNamespace(seal=None)}},
                        "questions": {"zep-graphiti": {
                            ("r1", "u1"): {"reads": [{"qid": "q1", "point": "V",
                                                      "answer": ans("V", shape="rendered", v_cut=VC, last_cut=True)}]},
                            ("r1", "u2"): {"reads": [{"qid": "q2", "point": "V",
                                                      "answer": ans("V", shape="rendered", qid="q2",
                                                                    v_cut="unmeasured: no v_spans")}]}}}}]}
    m33, p33 = val(lambda: PI.m33_table(res6), ({}, [])) if PI else ({}, [])
    zr = (m33.get("zep-graphiti") or {}).get("r1") or {}
    check("T-PLT-6 (M33): a rendered V's v_cut is summed per unit and on the stand line - 1 read, cut, 1 fact partial, "
          "1 entity lost - and an unmeasured v_cut is a problem by name, never counted",
          (zr.get("u1") or {}).get("facts_partial") == 1 and (zr.get("u1") or {}).get("entities_lost") == 1
          and (zr.get("u1") or {}).get("last_cut_share") == 1.0 and (zr.get("u2") or {}).get("unmeasured") == 1
          and (zr.get("_stand") or {}).get("v_reads") == 2 and (zr.get("_stand") or {}).get("entities_lost") == 1
          and any("u2" in p_ and "no v_spans" in p_ for p_ in p33), str((zr, p33))[:400])
    RR = TMP / "art_runs"
    (RR / "_launch").mkdir(parents=True)
    (RR / "_launch" / "records.jsonl").write_text(
        "".join(json.dumps({"status_id": f"S1-smoke-9/b01/r1/{a_}", "path": f"S1-smoke-9/_records/b01/r1.{a_}.json",
                            "sha256": "5" * 64}) + "\n" for a_ in ("nevertwice", "mem0", "letta")), encoding="utf-8")
    res7 = {"tree_start": {"anchor": "a" * 40, "clean": True},
            "blocks": [{"write": {a_: {("r1", "u1"): SimpleNamespace(seal=None)} for a_ in ("nevertwice", "mem0", "letta")},
                        "questions": {a_: {("r1", "u1"): {"reads": [{"qid": "q1", "point": p_, "answer": ans(p_)}
                                                                    for p_ in pts]}}
                                      for a_, pts in (("nevertwice", "BKV"), ("mem0", "BK"), ("letta", "BK"))}}]}
    PT7 = PL._mod("v3_points_for_plan", "points.py")
    inp7 = SimpleNamespace(stand_id="S1-smoke-9", points=lambda a_: PT7.read_points("S1-smoke-9", a_))
    got7 = val(lambda: PI.pilot_artifacts(res7, inp=inp7, arms=["nevertwice", "mem0", "letta"], out_dir=TMP / "art7",
                                           runs_root=RR, lme_sha256="e" * 64, list_sha256="f" * 64, A=A, PT=PT7),
               ({}, {}, ["raised"])) if PI else ({}, {}, ["no module"])
    v7 = jload(Path((got7[0].get("V") or {}).get("path", TMP / "none")), {})
    check("T-PLT-7 (T-RV, the auditor): from the stand id S1-smoke-9 the artifacts are built on the base stand S1 - V's "
          "too, which artifact.py reads on base stands - mem0 at V is its pointer {equivalent_to: B}, letta (V refused) "
          "is left out and named in v_not_read, and nothing was refused",
          set(got7[0]) == {"B", "K", "V"} and v7.get("stand") == "S1" and v7.get("arms", {}).get("mem0") == {
              "equivalent_to": "B"} and "letta" not in v7.get("arms", {}) and "refused" in (got7[1].get("letta") or {})
          and got7[2] == [], str((sorted(got7[0]), got7[1], got7[2]))[:400])
    r8 = err_of(lambda: PI.pilot_artifacts(res7, inp=inp7, arms=["nevertwice"], out_dir=PI.RESULTS_DIR / "pilot-x",
                                            runs_root=RR, lme_sha256="e" * 64, list_sha256="f" * 64, A=A,
                                            PT=PT7)) if PI else PI_ERR
    check("T-PLT-8 (R7): an artifact target inside research/v3/results is refused by name - a pilot is never a result",
          r8.startswith("ValueError") and "research/v3/results" in r8 and not (PI and (PI.RESULTS_DIR / "pilot-x").exists()),
          r8)
finally:
    up.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run pilot: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
