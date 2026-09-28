#!/usr/bin/env python3
"""PREREG-V3 §4.6 (K87), TB4.13: research/v3/ab_harness.py - the recording-vs-raw-forward A/B end to end: the real
scheduler, STATUS writer, hooks, stand plan and answerer, ONE real proxy (in this process) over a fake upstream, and
fake arm children under a temporary launch contract (this suite spawns children):

* T1: four debug stands raw1, rec1, rec2, raw2 in that order, one proxy; the record names each leg's stand, times and
  mapping; STATUS passes its own replay; the verdict is made on all six metrics;
* T2 (M-AB-key-in-child): the sentinel key is in no file under the run's tree but its own key file - no child's
  environment or argv dump holds it;
* T3 (M-AB-different-haystacks): every leg ran the S1 order's positions 481-483, the same input sha256s;
* T4 (M-AB-metric-source-asym): the upstream reports 1000/100 tokens for a writer call, the fake product counts 40/4 -
  every leg's tokens are the product's and the reader's (7/3), never the proxy's, which recorded 1000 on the
  recording legs;
* T5 (M-AB-fallback-one-leg): a local generation call on raw2 alone refuses the verdict by name; with the thinking
  fallback declared (branch b), the field is injected on all four legs alike; the twin differs from its arm only in
  name, mode, token and ports (loaded back from the proxy's own config); Q-AB-7: a raw leg's child speaks to the
  twin's port with the twin's token, a recording leg's to the arm's;
* T6: the raw twin leaves no calls.jsonl line, no bodies/ and no flags.jsonl line; its Ollama lines are named;
* in process: unit_metrics over mem0's LLMUsage and our engine's llm_stats, a memory-store arm's cumulative question
  counters, an aborted unit or a stage without counters refused, an arm without a source refused; next_ab_id.

    python tests/research/_test_v3_ab_harness.py
"""
from __future__ import annotations

import hashlib
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RV = _load("v3_run_v3_for_ab", ROOT / "research" / "v3" / "run_v3.py")    # the name ab_harness loads it under
AB = _load("v3_ab_harness_t", ROOT / "research" / "v3" / "ab_harness.py")
L = RV.load("launch.py", smoke=True)
SC = RV.load("scheduler.py", smoke=True)
P = RV.load("run_v3_proxy.py", smoke=True)
TP = RV.load("templates.py", smoke=True)
SL = RV.load("status_log.py", smoke=True)
SCTL = RV.load("sched_ctl.py", smoke=True)
SS = _load("v3_subsample_ab", ROOT / "research" / "v3" / "subsample.py")
PX = _load("v3_llm_proxy_ab", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_ab", ROOT / "research" / "_llm_proxy_selftest.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def raises(fn, exc, text: str) -> bool:
    try:
        fn()
    except exc as e:
        return text in str(e)
    except Exception:  # noqa: BLE001 - another failure is not the named refusal
        return False
    return False


print("- in process: unit_metrics, the sources, next_ab_id -")
W1 = SimpleNamespace(aborted=None, ops=[{"op_id": "a", "t0": "2026-09-28T00:00:00+00:00", "t1": "2026-09-28T00:00:01+00:00",
                                         "ok": True, "error": None}],
                     footprint={"retrievable": 5}, active_s=2.0,
                     counters={"llm_usage": {"calls": 3, "failed": 1, "no_usage": 0, "prompt_tokens": 120,
                                             "completion_tokens": 12},
                               "http": {"counts": {"ollama:generate": {"attempts": 0}}}})
Q1 = {"active_s": 1.0, "counters_include_write": False,
      "counters": {"llm_usage": {"calls": 0, "failed": 0, "no_usage": 0, "prompt_tokens": 0, "completion_tokens": 0}},
      "reads": [{"answer": {"request_keys": ["k1", "k2"], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}},
                {"unrecovered": True}]}
m = AB.unit_metrics("mem0", "rec1", "u1", W1, Q1)
check("unit_metrics (mem0): the product's calls and tokens from LLMUsage in both stages plus the reader's calls and "
      "usage; items from the footprint; wall = write + questions; lost_share by the adapter's view",
      (m["calls"], m["tokens_in"], m["tokens_out"], m["items"], m["wall_s"], m["lost_share"], m["writer_calls"],
       m["reader_calls"], m["reads_unrecovered"], m["llm_failed"]) == (5, 127, 15, 5, 3.0, 0.0, 3, 2, 1, 1), str(m))
Q1c = {**Q1, "counters_include_write": True,
       "counters": {"llm_usage": {"calls": 4, "failed": 1, "no_usage": 0, "prompt_tokens": 160, "completion_tokens": 16}}}
mc = AB.unit_metrics("mem0", "rec1", "u1", W1, Q1c)
check("a memory-store arm's question counters include the write snapshot - the stage sum is not double-counted",
      (mc["writer_calls"], mc["tokens_in"]) == (4, 160 + 7), str(mc))
W_err = SimpleNamespace(**{**vars(W1), "ops": W1.ops + [{"op_id": "b", "t0": "2026-09-28T00:00:02+00:00",
                                                        "t1": "2026-09-28T00:00:03+00:00", "ok": False,
                                                        "error": "product said no"}]})
check("lost_share: an operation the product reported failed is lost - the adapter's view (Q-AB-4)",
      AB.unit_metrics("mem0", "rec1", "u1", W_err, Q1)["lost_share"] == 0.5)
we = SimpleNamespace(aborted=None, ops=[], footprint={"retrievable": 1}, active_s=1.0,
                     counters={"llm_stats": {"cloud": 2, "ollama": 1, "fail": 0, "prompt_tokens": 80, "eval_tokens": 8}})
qe = {"active_s": 1.0, "counters_include_write": False, "counters": {"llm_stats": {"cloud": 0, "ollama": 0, "fail": 0}},
      "reads": []}
me = AB.unit_metrics("nevertwice", "raw1", "u1", we, qe)
check("unit_metrics (our engine): llm_stats cloud+ollama and prompt/eval tokens; ollama calls are local generation",
      (me["calls"], me["tokens_in"], me["tokens_out"], me["local_generation"]) == (3, 80, 8, 1), str(me))
check("an aborted unit gives no A/B metric - refused by name",
      raises(lambda: AB.unit_metrics("mem0", "rec1", "u1", SimpleNamespace(**{**vars(W1), "aborted": "crash"}), Q1),
             AB.ABError, "was aborted"))
check("B-WCTR: a stage without its counters is refused - never a 0 that looks measured",
      raises(lambda: AB.unit_metrics("mem0", "rec1", "u1", SimpleNamespace(**{**vars(W1), "counters": None}), Q1),
             AB.ABError, "without its counters"))
check("mem0 counters without llm_usage are refused (Q-AB-1)",
      raises(lambda: AB.unit_metrics("mem0", "rec1", "u1", SimpleNamespace(**{**vars(W1), "counters": {}}), Q1),
             AB.ABError, "no llm_usage"))
check("our engine's calls without its token counts are refused",
      raises(lambda: AB.unit_metrics("nevertwice", "raw1", "u1", SimpleNamespace(
          **{**vars(we), "counters": {"llm_stats": {"cloud": 2, "ollama": 0}}}), qe), AB.ABError, "counted no tokens"))
check("an arm with no product-side source is no A/B arm (the ab-arm's comes with A8)",
      raises(lambda: AB.unit_metrics("letta", "rec1", "u1", W1, Q1), AB.ABError, "ARM_SOURCES"))
cfg0 = {"arms": [{"arm": "mem0", "mode": "record", "thinking_route": "fallback", "write_port": True, "pinned_model": "p"},
                 {"arm": "bm25-floor", "mode": "record", "write_port": False}, {"arm": "harness-catcher", "mode": "catch"}]}
c2, tw = AB.ab_proxy_config(cfg0, ["mem0"])
check("ab_proxy_config: a raw twin <arm>-abraw per A/B arm - its arm's entry with only the name and mode changed",
      tw == {"mem0": "mem0-abraw"} and AB.twin_diff(c2, "mem0") == ["arm", "mode"]
      and [a["arm"] for a in c2["arms"]] == ["mem0", "bm25-floor", "harness-catcher", "mem0-abraw"], str(c2))
check("... an A/B arm without a recording write port is refused (nothing to raw-forward)",
      raises(lambda: AB.ab_proxy_config(cfg0, ["bm25-floor"]), AB.ABError, "no recording write port"))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_ab_"))
st0 = TMP / "STATUS0"
SL.StatusLog(st0, local_tz=__import__("datetime").timezone.utc).stand("S4-ab-2-rec1", "START", model="m",
                                                                         changelog="2026-09-10", order=2)
check("next_ab_id: one past the highest <stand>-ab-<n>-<leg> STAND START; 1 on a new file",
      AB.next_ab_id(st0, "S4") == ("S4-ab-3", 3) and AB.next_ab_id(TMP / "none", "S4") == ("S4-ab-1", 1),
      str(AB.next_ab_id(st0, "S4")))

FAKE_ARM = r'''
import hashlib, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))
STATS = {"cloud": 0, "ollama": 0, "fail": 0, "prompt_tokens": 0, "eval_tokens": 0}
with open(os.path.join(os.getcwd(), "env_dump.json"), "w", encoding="utf-8") as f:     # T2: what the child holds
    json.dump({"env": dict(os.environ), "argv": sys.argv}, f)
if spec["stage"] == "write":                                                             # Q-AB-7: where it speaks
    with open(os.path.join(spec["seen_dir"], spec["run"] + "." + spec["unit"] + ".json"), "w", encoding="utf-8") as f:
        json.dump({"url": os.environ.get("NVT3_WRITE_URL"),
                   "token_sha256": hashlib.sha256(os.environ.get("NVT3_WRITE_TOKEN", "").encode()).hexdigest()}, f)


def send(body):
    req = urllib.request.Request(os.environ["NVT3_WRITE_URL"], data=body, method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["NVT3_WRITE_TOKEN"],
                                          "Content-Type": "application/json"})
    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20).read()


class H:
    def __init__(self):
        self.items = []
        if spec["stage"] == "read":
            with open(os.path.join(spec["write_dir"], "store", "items.json"), encoding="utf-8") as f:
                self.items = json.load(f)

    def hello(self):
        return {"protocol": B.PROTOCOL, "arm": spec["arm"], "stage": spec["stage"], "pid": os.getpid()}

    def write(self, item, date=None):
        send(json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": "extract facts"}]}).encode())
        STATS["cloud"] += 1                     # the product's own count: 40 in, 4 out per call (T4)
        STATS["prompt_tokens"] += 40
        STATS["eval_tokens"] += 4
        time.sleep(0.2)
        self.items.append(item.get("text") or "")
        return {"op_id": item.get("item_id")}

    def end_write(self):
        if spec["run"] in spec.get("generate_on", []):
            STATS["ollama"] += 1                # T5: a local generation call on this leg only
        os.makedirs(os.path.join(os.getcwd(), "store"), exist_ok=True)
        with open(os.path.join(os.getcwd(), "store", "items.json"), "w", encoding="utf-8") as f:
            json.dump(self.items, f)
        return {"footprint": {"retrievable": len(self.items)}, "seal": {"sha256": "0" * 64}}

    def read(self, qid, query, k=10):
        return {"qid": qid, "items": [{"text": t, "rank": i + 1} for i, t in enumerate(self.items[:k])]}

    def counters(self):
        return {"llm_stats": dict(STATS)}


sys.exit(B.main_with(H))
'''

READER = json.dumps({"id": "c1", "model": "deepseek-v4-flash", "system_fingerprint": "fp_ab",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "It was blue.\nSHORT ANSWER: blue\n"},
                                  "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 7, "completion_tokens": 3, "prompt_cache_hit_tokens": 0,
                               "prompt_cache_miss_tokens": 7, "completion_tokens_details": {"reasoning_tokens": 0}}}).encode()
WRITER = json.dumps({"id": "w1", "model": "deepseek-v4-flash", "system_fingerprint": "fp_ab",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 0,
                               "prompt_cache_miss_tokens": 1000, "completion_tokens_details": {"reasoning_tokens": 0}}}).encode()
BAL = json.dumps({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "100.00"}]}).encode()


class Upstream(ST.FakeUpstream):
    def _serve(self, c, path):
        p = path.split(b"?", 1)[0]
        if p.endswith(b"/nvt3-writer"):
            body = WRITER
        elif p.endswith(b"/chat/completions"):
            body = READER
        elif p.startswith(b"/user/balance"):
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
SEEN = TMP / "seen"
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
            "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"],
            "haystack_sessions": [[{"role": "user", "content": f"thing {i} is blue"},
                                   {"role": "assistant", "content": "noted"}],
                                  [{"role": "user", "content": f"more about {i}"}]]}


TEMPLATE = TP.Template(stand="S4", text="Context:\n{context}\nQuestion: {question}\nEnd with SHORT ANSWER: <answer>",
                       sha256="0" * 64, source_pin="test", source_sha256="0" * 64, slots=("context", "question"))
TEMPLATE5 = TP.Template(stand="S4-cat5", text="Context:\n{context}\nQuestion: {question}\nMay be unanswerable.",
                        sha256="1" * 64, source_pin="test", source_sha256="1" * 64, slots=("context", "question"))
PROXIES: list = []
BAD_TWIN = {"on": False}      # a proxy that misbehaves on the raw twin alone: records it, and never injects for it


def start_proxy(c, *, python, key_file, config, secrets, unit, parent_env, witnesses):
    run_dir = Path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    cfgp = run_dir / "proxy_config.json"
    cfgp.write_bytes(json.dumps(dict(config)).encode("utf-8"))
    pc = PX.ProxyConfig.load(cfgp, dict(secrets), test_upstream_ok=True)
    px = PX.Proxy(pc, PX.read_key(key_file), canaries=secrets.get("canaries") or {}, log=lambda m: None)
    if BAD_TWIN["on"]:
        for name, a in px.arms.items():
            if name.endswith(AB.AB_RAW_SUFFIX):
                a.mode = "record"
        inner = px._fallback
        px._fallback = lambda arm, *a, **k: ((a[3], 0) if arm.arm.endswith(AB.AB_RAW_SUFFIX) else inner(arm, *a, **k))
    ports = px.start()
    PROXIES.append({"px": px, "ports": ports, "tokens": dict(secrets["tokens"]), "config": cfgp,
                    "secrets": dict(secrets)})
    return P.ProxyHandle(child=None, ports=ports, control=SCTL.ProxyControl(ports["control"], secrets["control_token"]),
                         tokens=dict(secrets["tokens"]), run_dir=run_dir)


def stop_proxy(h):
    left = PROXIES[-1]["px"].stop()
    return {"rc": 0 if not left else 1, "killed": False, "shutdown_error": None}


def make_launcher_factory(generate_on=()):
    def make(arm, ar, *, stand_id, proxy, unit_block, unit_chars):
        def spec_for(stage, *, stand, run, unit, dirs, write_dirs):
            return {"arm": arm, "stage": stage, "run": run, "unit": unit, "generate_on": list(generate_on),
                    "seen_dir": str(SEEN), "write_dir": str(write_dirs.cwd) if write_dirs is not None else None}

        def declared_for(stage, *, stand, run, unit, dirs, write_dirs):
            return {"NVT3_WRITE_URL": P.url(proxy, arm, run, unit, suffix="/v1/chat/completions/nvt3-writer")}
        return SC.ChildArmLauncher(arm, argv_for=lambda p, **_: [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py"), str(p)],
                                   spec_for=spec_for, store_persistence="disk", path_dirs=(str(ARM_PY.parent),),
                                   declared={"NVT3_WRITE_TOKEN": proxy.tokens[arm]}, token_names=("NVT3_WRITE_TOKEN",),
                                   declared_for=declared_for)
    return make


def deps_for(up, *, out: list, err: list, generate_on=()):
    return RV.SmokeDeps(
        contract=C, L=L, native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", TMP / "watched")]), clock=SC.SystemClock(), ollama_ctl=None,
        monotonic=__import__("time").monotonic, environ=dict(os.environ), status_path=TMP / "STATUS",
        lme_records=lambda: [lme_rec(i) for i in range(500)], cl100k=(lambda s: max(1, len(s) // 4),
                                                                      lambda s, n: s[:max(0, 4 * n)]),
        cl100k_source="t" * 64, max_token_bytes=128,
        truncate=lambda text: SimpleNamespace(text=text, truncated=False), templates=(TEMPLATE, TEMPLATE5),
        locomo_question=lambda q, cat: q, decl=lambda py, *, arm: {"python": str(py), "arm": arm, "version": "test"},
        make_launcher=make_launcher_factory(generate_on), start_proxy=start_proxy, stop_proxy=stop_proxy, post=P.post,
        proxy_route={"test_upstream": {"host": "127.0.0.1", "port": up.port, "tls": False}},
        now_utc=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        lists_dir=LISTS, s1_sha256=S1["ids_sha256"], out=out.append, err=err.append)


CFG = RV.RunConfig(proxy_python=Path(sys.executable), key_file=KEYFILE, git=Path(GIT or "git"), campaign_seed=20260928,
                   embed_tag="nvt3-bge-m3-d1:latest", ollama_url="http://127.0.0.1:11434",
                   arms={"nevertwice": RV.ArmRun(python=ARM_PY, llm="deepseek-flash", llm_transport="cloud:deepseek",
                                                 embeds_via_ollama=False)},
                   sha256="c" * 64)
WALL_ONLY = "out of tolerance - recording run"


def run(**kw) -> tuple:
    out, err = [], []
    SEEN.mkdir(exist_ok=True)
    for f in SEEN.iterdir():
        f.unlink()
    try:
        res = AB.run_ab(CFG, stand="S4", arm_names=["nevertwice"], deps=deps_for(up, out=out, err=err,
                                                                              generate_on=kw.pop("generate_on", ())),
                        hop_n=20, **kw)
        crash = None
    except Exception as e:  # noqa: BLE001 - a crash FAILs the rows by name
        res, crash = None, f"{type(e).__name__}: {e}"
    rec = json.loads(res.record_path.read_text(encoding="utf-8")) if res and res.record_path else {}
    return res, crash, rec, out, err


up = Upstream()
try:
    if GIT is None:
        check("git is on PATH (the stand's tree check runs it)", False)
        raise SystemExit
    print("\n- T1, T3, T4, T6, Q-AB-7: a clean A/B with the declared thinking fallback -")
    res1, crash1, rec1, out1, err1 = run(thinking_branch="b", thinking_routes={"nevertwice": "fallback"})
    legs = rec1.get("legs") or []
    probs = rec1.get("problems") or []
    wall_noise = bool(probs) and all(WALL_ONLY in p and "wall_s" in p for p in probs)
    status_text = (TMP / "STATUS").read_text(encoding="utf-8") if (TMP / "STATUS").exists() else ""
    check("T1: four debug stands raw1, rec1, rec2, raw2 in that order (ABBA), one proxy, each leg timed; the verdict made "
          "on all six metrics; exit 0 - or 1 naming only a wall-time excess of a recording run (timing noise on a "
          "loaded machine, said below)", crash1 is None and [lg.get("leg") for lg in legs] == ["raw1", "rec1", "rec2", "raw2"]
          and [lg.get("stand") for lg in legs] == [f"S4-ab-1-{x}" for x in ("raw1", "rec1", "rec2", "raw2")]
          and all(lg["t0"] <= lg["t1"] for lg in legs) and all(a["t1"] <= b["t0"] for a, b in zip(legs, legs[1:]))
          and len(PROXIES) == 1 and set((rec1.get("verdicts") or {}).get("nevertwice", {}).get("ranges", {}))
          == {"calls", "tokens_in", "tokens_out", "lost_share", "items", "wall_s"}
          and ((res1.rc == 0 and not probs) or (res1.rc == 1 and wall_noise)),
          f"{crash1} {[lg.get('leg') for lg in legs]} {probs[:3]} {err1[:3]}")
    if wall_noise:
        print(f"       note: the wall-time tolerance was exceeded by timing noise: {probs}")
    starts = [x for x in status_text.splitlines() if " START " in x and "/nevertwice " in x and "S4-ab-1-" in x]
    check("T1: every arm-run START is tag=debug, and STATUS passes the writer's own replay",
          len(starts) == 4 and all(" tag=debug " in x + " " for x in starts) and SL.self_check(TMP / "STATUS") == [],
          f"{starts[:2]} {SL.self_check(TMP / 'STATUS')}")
    want_units = ["smoke-q480", "smoke-q481", "smoke-q482"]
    shas = rec1.get("units", {}).get("input_sha256")
    check("T3: every leg ran the S1 order's positions 481-483 - the same units, the same input sha256s",
          rec1.get("units", {}).get("ids") == want_units and rec1["units"].get("order_positions") == [481, 482, 483]
          and len(legs) == 4 and all((lg.get("units") or {}).get("nevertwice") == want_units
                                     and lg.get("unit_input_sha256") == shas for lg in legs)
          and shas and len(set(shas)) == 3, str([(lg.get("leg"), lg.get("units")) for lg in legs]))
    per = [(lg["leg"], u, x) for lg in legs for u, x in (lg.get("metrics", {}).get("nevertwice") or {}).items()]
    check("T4: every leg's tokens are the product's own count (40/4 per writer call) plus the reader's usage (7/3) - "
          "never the upstream's 1000/100 the proxy saw",
          len(per) == 12 and all(x["writer_calls"] == 3 and x["reader_calls"] == 1 and x["tokens_in"] == 3 * 40 + 7
                                 and x["tokens_out"] == 3 * 4 + 3 and x["calls"] == 4 for _l, _u, x in per),
          str([(lg_, u_, x_["tokens_in"], x_["writer_calls"]) for lg_, u_, x_ in per][:4]))
    run_dir = C.runs_root / "_ab" / "S4-ab-1" / "_proxy"
    calls = [json.loads(x) for f in run_dir.rglob("calls.jsonl") for x in f.read_text(encoding="utf-8").splitlines()]
    wcalls = [x for x in calls if x.get("arm") == "nevertwice" and x.get("port_role") == "write"]
    check("T4: ... while the proxy recorded 1000 prompt tokens per writer call on the recording legs only",
          len(wcalls) == 2 * 3 * 3 and all((x.get("usage") or {}).get("prompt") == 1000 for x in wcalls)
          and {x.get("unit", "").split(".")[0] for x in wcalls} == {"rec1", "rec2"},
          str([(x.get("unit"), x.get("usage")) for x in wcalls[:3]]))
    t6 = (rec1.get("t6") or {}).get("nevertwice-abraw") or {}
    check("T6: the raw twin left no calls.jsonl line, no bodies/ and no flags.jsonl line (its Ollama lines are named)",
          t6.get("calls_jsonl") == 0 and t6.get("bodies") is False and t6.get("flags_jsonl") == 0
          and "ollama_jsonl_lines" in t6 and not any(x.get("arm") == "nevertwice-abraw" for x in calls)
          and not list(run_dir.rglob("bodies/nevertwice-abraw")), str(t6))
    inj = {lg["leg"]: ((lg.get("proxy") or {}).get("nevertwice") or {}).get("thinking_injected") for lg in legs}
    check("T5: under the declared thinking fallback the field is injected on all four legs alike - the 9 writer calls "
          "of each (the reader's body says thinking itself) (M-AB-fallback-one-leg)",
          len(inj) == 4 and all(v == 3 * 3 for v in inj.values()), str(inj))
    pc = PX.ProxyConfig.load(PROXIES[0]["config"], PROXIES[0]["secrets"], test_upstream_ok=True) if PROXIES else None
    by = {a.arm: a for a in pc.arms} if pc else {}
    import dataclasses as _dc  # noqa: E402
    diff = sorted(f.name for f in _dc.fields(PX.ArmConfig)
                  if getattr(by.get("nevertwice"), f.name, None) != getattr(by.get("nevertwice-abraw"), f.name, None))
    pts = PROXIES[0]["ports"]["arms"] if PROXIES else {}
    check("T5, Q-AB-5: the twin, loaded back from the proxy's own config, differs from its arm only in name, mode and "
          "token - and at run time in its ports", diff == ["arm", "mode", "token"]
          and getattr(by.get("nevertwice-abraw"), "mode", None) == "raw" and getattr(by.get("nevertwice"), "mode", None) == "record"
          and pts.get("nevertwice", {}).get("write") != pts.get("nevertwice-abraw", {}).get("write")
          and rec1.get("twin_diff") == {"nevertwice": ["arm", "mode"]}, str(diff))
    seen = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in SEEN.glob("*.json")}
    toks = PROXIES[0]["tokens"] if PROXIES else {}

    def port_of(url):
        return int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1]) if url else None
    want = {leg: ("nevertwice-abraw" if leg.startswith("raw") else "nevertwice") for leg in ("raw1", "rec1", "rec2", "raw2")}
    ok7 = len(seen) == 12 and all(
        port_of(v["url"]) == pts[want[k.split(".")[0]]]["write"]
        and v["token_sha256"] == hashlib.sha256(toks[want[k.split(".")[0]]].encode()).hexdigest() for k, v in seen.items())
    check("Q-AB-7: a raw leg's child speaks to the twin's port with the twin's token, a recording leg's to the arm's; the "
          "record names the mapping", ok7 and [lg["mapping"] for lg in legs]
          == [{"nevertwice": want[x]} for x in ("raw1", "rec1", "rec2", "raw2")], str(list(seen.items())[:2]))
    added = (rec1.get("verdicts") or {}).get("nevertwice", {}).get("ttfb_added_ms") or {}
    AR = RV.load("ab_rule.py", smoke=True)
    hops = {lg["leg"]: ((lg.get("proxy") or {}).get("nevertwice") or {}).get("own_hop_ms") or [] for lg in legs}
    rec_s = [v for k, vs in hops.items() if k.startswith("rec") for v in vs]
    raw_s = [v for k, vs in hops.items() if k.startswith("raw") for v in vs]
    want_add = AR.added_over(rec_s, raw_s) if rec_s and raw_s else []
    check("Q-AB-2: the added time to first byte is the recording legs' own-hop samples over the raw legs' median "
          "(ab_rule.added_over), and hop_benchmark is recorded beside it",
          want_add and added.get("n") == len(rec_s) and added.get("p50") == AR.percentile(want_add, 50)
          and added.get("p95") == AR.percentile(want_add, 95) and len(raw_s) >= 12
          and set((rec1.get("hop_benchmark") or {})) >= {"raw", "record"}, f"{added} {len(rec_s)} {len(raw_s)}")
    pfl = C.runs_root / "_launch" / "preflight.jsonl"
    pf = [json.loads(x) for x in pfl.read_text(encoding="utf-8").splitlines()] if pfl.exists() else []
    check("Q-AB-3: the A/B's preflight carries its forecast (four runs), before any spawn",
          pf and pf[0]["stand_candidate"] == "S4-ab-1" and pf[0]["ok"] is True
          and pf[0]["forecast"]["note"] == "upper bound, not pilot medians", str(pf[:1])[:300])

    print("\n- T2: the key in no child -")
    hits = [str(p) for p in TMP.rglob("*") if p.is_file() and p != KEYFILE
            and ST.SENTINEL_KEY.encode() in p.read_bytes()]
    dumps = list(C.runs_root.rglob("env_dump.json"))
    check("T2 (M-AB-key-in-child): the sentinel key is in no file of the run but its key file - 24 children dumped their "
          "environment and argv, none holds it", hits == [] and len(dumps) == 24, f"{hits[:3]} {len(dumps)}")

    print("\n- T5: a local generation call on one leg; D-AB-2: a leg whose scheduler result lacks a unit -")
    _orig_rs = SC.Scheduler.run_stand

    def _drop_one(self, sp, blocks, **kw):
        res = _orig_rs(self, sp, blocks, **kw)
        if sp.stand.endswith("-raw2"):
            for b in res["blocks"]:
                for arm_, recs in b["write"].items():
                    k = sorted(recs)[-1]
                    recs.pop(k)
                    (b["questions"].get(arm_) or {}).pop(k, None)
        return res
    SC.Scheduler.run_stand = _drop_one
    try:
        res2, crash2, rec2, out2, err2 = run(generate_on=("raw2",))
    finally:
        SC.Scheduler.run_stand = _orig_rs
    check("D-AB-2: a leg whose scheduler result lacks one of the A/B's units is refused by name",
          any(p.startswith("raw2: nevertwice ran units") and "(D-AB-2)" in p for p in rec2.get("problems") or []),
          str(rec2.get("problems")))
    check("T5 (M-AB-fallback-one-leg): a local generation call on raw2 alone refuses the verdict by name, exit 1",
          crash2 is None and res2.rc == 1 and not rec2.get("verdicts")
          and any(p.startswith("raw2: nevertwice made local generation calls") for p in rec2.get("problems") or [])
          and not any(p.startswith(("raw1:", "rec1:", "rec2:")) for p in rec2.get("problems") or []),
          f"{crash2} {rec2.get('problems')}")
    check("next_ab_id: the second A/B is S4-ab-2", rec2.get("ab") == "S4-ab-2", str(rec2.get("ab")))

    print("\n- T6 and D-AB-4 at run time: a proxy that records the twin and never injects for it -")
    BAD_TWIN["on"] = True
    cap0, PX.OWN_HOP_CAP = PX.OWN_HOP_CAP, 5
    try:
        res3, crash3, rec3, out3, err3 = run(thinking_branch="b", thinking_routes={"nevertwice": "fallback"})
    finally:
        BAD_TWIN["on"] = False
        PX.OWN_HOP_CAP = cap0
    p3 = rec3.get("problems") or []
    check("Q-AB-2 (4): own-hop samples dropped past the proxy's cap refuse the verdict by name",
          any("own-hop samples were dropped past the proxy's cap" in p for p in p3), str(p3))
    check("T6: a twin that left calls.jsonl lines is refused by name, exit 1, no verdict",
          crash3 is None and res3.rc == 1 and not rec3.get("verdicts")
          and any(p.startswith("T6: the raw twin nevertwice-abraw left recording traces") for p in p3), f"{crash3} {p3}")
    check("D-AB-4: the thinking field injected on the recording legs only is refused by name",
          any(p.startswith("nevertwice: the declared thinking field was injected on legs ['rec1', 'rec2'] only")
              for p in p3), str(p3))
finally:
    for pr in PROXIES:
        try:
            pr["px"].stop()
        except Exception:  # noqa: BLE001
            pass
    up.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 ab harness: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
