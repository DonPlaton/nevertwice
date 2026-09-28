#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): research/v3/arms/arm_graphiti.py - the zep-graphiti arm, driven as a real child laid out as Q9
lays out an arm directory, against a FAKE graphiti_core (tests/fixtures/v3_fake_products/graphiti_core) whose FalkorDB is
a JSON file per graph, a fake proxy and a fake Ollama on loopback.

* the build (rev1 §2.2): OpenAIGenericClient in json_object mode at /u/<run>.<unit>/v1, deepseek-flash as model and
  small model, the token, NO temperature and NO max_tokens of ours; the reranker on the same config; OpenAIEmbedder on
  Ollama's /v1 with the tag at 1024 dims; FalkorDriver with database = the unit;
* the write: one EpisodeType.message episode per message, "<speaker>: <text>", group_id = unit, reference_time = the
  session date on a dated stand; on an undated stand the wall clock, strictly increasing (Q-46b-2), equal refused;
* the graph between the stages (Q-46b-1): a state seal beside the unit; a graph touched between the stages, another
  server, a read cut short by pagination, a write stage on a graph that is not empty - each refused by name;
* the read (Q-46b-4): edges then nodes, each recipe with limit k; Point K the first k, Point B all; Point V refused;
  validity rendered on a dated stand only; reranker_calls 0 and the logical LLM calls counted (Q-46b-3, K87);
* refusals: no token, a decoy graphiti_core in the arm directory, a tag Ollama lacks; the counter before the pacer.

    python tests/research/_test_v3_arm_graphiti.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_graphiti.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_graphiti", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"
TOKEN = "nvt3-zep-graphiti-" + "a" * 32


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def raises(fn, exc, words: str = "") -> bool:
    try:
        fn()
    except exc as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def safely(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a crash is a named FAIL of the row that reads it
        return default if default is not None else repr(e)


def vec(text: str) -> list[float]:
    v = [0.0] * 32
    for w in str(text).lower().replace(":", " ").split():
        v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 32] += 1.0
    return v if any(v) else [1.0] + [0.0] * 31


class Server:
    def __init__(self, tags: list[str]) -> None:
        self.calls: list[dict] = []
        self.tags = tags
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                srv.calls.append({"method": "GET", "path": self.path})
                self._send({"models": [{"name": t, "model": t} for t in srv.tags]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                srv.calls.append({"method": "POST", "path": self.path, "body": body,
                                  "auth": self.headers.get("Authorization")})
                if self.path.endswith("/embeddings"):
                    return self._send({"data": [{"embedding": vec(t)} for t in body.get("input") or []]})
                self._send({"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def paths(self, suffix: str) -> list[dict]:
        return [c for c in self.calls if c["path"].endswith(suffix)]

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


TMP = Path(tempfile.mkdtemp(prefix="v3graphiti_"))
PROXY = Server([])
OLLAMA = Server([TAG])
LOG = TMP / "fake_calls.jsonl"
FALKOR = TMP / "falkor"
children: list = []


def arm_dir(name: str) -> Path:
    d = TMP / "armdirs" / name
    d.mkdir(parents=True)
    for src in (ADAPTER, ARMS_DIR / "base.py", ARMS_DIR / "_http_count.py", ROOT / "research" / "_ollama_pacer.py"):
        shutil.copyfile(src, d / src.name)
    return d


def child_env(*, token: str | None = TOKEN, extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    (home / "Temp").mkdir(parents=True)
    env.update({"PATH": os.pathsep.join([str(Path(sys.executable).parent)] + (
                    [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if os.name == "nt"
                    else ["/usr/bin", "/bin"])),
                "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home / "Temp"), "TMP": str(home / "Temp"),
                "TMPDIR": str(home / "Temp"), "PYTHONPYCACHEPREFIX": str(TMP / "pyc"), "PYTHONPATH": str(FAKES),
                "NO_PROXY": "127.0.0.1,localhost", "OLLAMA_HOST": f"http://127.0.0.1:{OLLAMA.port}",
                "NVT3_FAKE_LOG": str(LOG), "NVT3_FAKE_FALKOR_DIR": str(FALKOR), "GRAPHITI_TELEMETRY_ENABLED": "false"})
    if token is not None:
        env["DEEPSEEK_API_KEY"] = token
    env.update(extra or {})
    return env


def spec_for(name: str, stage: str, unit: str, *, dated=True, falkor_port=6380) -> dict:
    return {"arm": "zep-graphiti", "stage": stage, "stand": "s1", "run": "r1", "unit": unit,
            "unit_dir": str(TMP / "runs" / unit), "port": PROXY.port, "embed_tag": TAG,
            "ollama_url": f"http://127.0.0.1:{OLLAMA.port}", "falkor_host": "127.0.0.1", "falkor_port": falkor_port,
            "dated": dated, "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None):
    Path(spec["unit_dir"]).mkdir(parents=True, exist_ok=True)
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.err.txt"
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_graphiti.py"), str(sp)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=open(errf, "wb"), cwd=str(wd), env=env or child_env())
    children.append(p)
    return B.ArmClient(p, default_timeout=120), errf


def hello_error(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None) -> str:
    c, _ = start(name, spec, env=env, adir=adir)
    try:
        c.request("hello")
        return "no refusal"
    except B.ArmError as e:
        return str(e)
    finally:
        c.close()


def logged(event: str) -> list[dict]:
    if not LOG.exists():
        return []
    return [r for r in (json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x) if r["event"] == event]


MSGS = [{"item_id": "u1:0", "session_id": "s0", "role": "user", "speaker": "Caroline", "text": "I adopted a dog"},
        {"item_id": "u1:1", "session_id": "s0", "role": "assistant", "speaker": "Melanie", "text": "What breed is it"}]

try:
    print("\n- Q-46-1 and Q9: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_graphiti imports the standard library, base, _http_count, _ollama_pacer and graphiti_core only",
          mods <= {"__future__", "asyncio", "hashlib", "json", "os", "re", "sys", "time", "urllib", "datetime",
                   "pathlib", "typing", "base", "_http_count", "_ollama_pacer", "graphiti_core"}, str(sorted(mods)))

    print("\n- the write stage on a dated stand -")
    c, err = start("w1", spec_for("w1", "write", "u1"))
    h = safely(lambda: c.request("hello"), {})
    _st = h.get("start") or {}
    check("R-EMBED-PATH (c): the start record - the pacer in observe mode (the proxy leg paces and retries) and the "
          "spec's Ollama URL as the arm's one route", _st.get("pacer") == "observe"
          and _st.get("ollama_route") == f"http://127.0.0.1:{OLLAMA.port}",
          str({k: _st.get(k) for k in ("pacer", "ollama_route")}))
    check("hello: graphiti from the venv, the DeepSeek label, the tag",
          h.get("system") == "graphiti" and h.get("llm_label") == "deepseek:deepseek-flash"
          and str(FAKES) in str(((h.get("start") or {}).get("product") or {}).get("file")),
          str(h)[:300] + err.read_bytes().decode("utf-8", "replace")[-400:])
    lc = (logged("LLMConfig") or [{}])[-1]
    check("LLMConfig: deepseek-flash as model and small model, /u/r1.u1/v1, the token, NO temperature, NO max_tokens",
          lc.get("model") == "deepseek-flash" and lc.get("small_model") == "deepseek-flash"
          and lc.get("base_url") == f"http://127.0.0.1:{PROXY.port}/u/r1.u1/v1" and lc.get("api_key_set") is True
          and lc.get("temperature") == "<default>" and lc.get("max_tokens") == "<default>" and lc.get("extra") == [],
          str(lc))
    gc = (logged("OpenAIGenericClient") or [{}])[-1]
    check("OpenAIGenericClient in json_object mode, the product's own max_tokens",
          gc.get("structured_output_mode") == "json_object" and gc.get("max_tokens") == 16384, str(gc))
    em = (logged("OpenAIEmbedder") or [{}])[-1]
    fd = (logged("FalkorDriver") or [{}])[-1]
    check("OpenAIEmbedder on Ollama's /v1 with the tag at 1024 dims; FalkorDriver database = the unit",
          em.get("model") == TAG and em.get("dim") == 1024 and em.get("base_url") == f"http://127.0.0.1:{OLLAMA.port}/v1"
          and fd.get("database") == "u1" and fd.get("port") == 6380, f"{em} {fd}")
    check("the reranker is built on the same config (the constructor needs one)",
          (logged("OpenAIRerankerClient") or [{}])[-1].get("model") == "deepseek-flash")
    ws = [safely(lambda m=m: c.request("write", item=m, date="2023-05-20"), {}) for m in MSGS]
    eps = logged("add_episode")[-2:]
    check("one message episode per message, '<speaker>: <text>', group_id = unit, reference_time = the session date",
          [(e.get("episode_body"), e.get("source"), e.get("group_id"), e.get("reference_time")) for e in eps]
          == [("Caroline: I adopted a dog", "message", "u1", "2023-05-20T00:00:00+00:00"),
              ("Melanie: What breed is it", "message", "u1", "2023-05-20T00:00:00+00:00")], str(eps)[:300])
    check("the write names the bytes it handed over", ws[0].get("text_sha256") == B.text_sha256("Caroline: I adopted a dog"))
    chat = (PROXY.paths("/chat/completions") or [{}])[-1]
    check("the LLM call reaches /u/r1.u1/v1/chat/completions in json_object mode with the token",
          chat.get("path") == "/u/r1.u1/v1/chat/completions" and (chat.get("body") or {}).get("response_format")
          == {"type": "json_object"} and chat.get("auth") == f"Bearer {TOKEN}", str(chat)[:200])
    check("a dated write without its date is refused",
          raises(lambda: c.request("write", item=MSGS[0]), B.ArmError, "carries its date"))
    e = safely(lambda: c.request("end_write"), {})
    seal = json.loads((TMP / "runs" / "u1" / B.SEAL_NAME).read_bytes()) if (TMP / "runs" / "u1" / B.SEAL_NAME).exists() \
        else {}
    check("end_write: a state seal of the unit's graph beside the unit, with its counts",
          seal.get("kind") == "state" and seal.get("store") == "falkordb://127.0.0.1:6380/u1"
          and (e.get("footprint") or {}).get("edges") == 2 and (e.get("footprint") or {}).get("episodes") == 2,
          str(e)[:300])
    cn = safely(lambda: c.request("counters"), {})
    check("counters: two logical LLM calls, no reranker call", cn.get("llm_calls") == 2 and cn.get("reranker_calls") == 0,
          str(cn)[:200])
    c.close()

    print("\n- the read stage: a new process, the sealed graph -")
    c, err = start("r1", spec_for("r1", "read", "u1"))
    rk = safely(lambda: c.request("read", qid="q", query="Caroline dog", k=2, point="K"), {})
    srch = logged("search_")[-2:]
    check("read: the edge recipe then the node recipe, each with limit k, on the unit's group",
          [(s.get("kind"), s.get("limit"), s.get("group_ids")) for s in srch] == [("edge", 2, ["u1"]),
                                                                                ("node", 2, ["u1"])], str(srch))
    ki = rk.get("items") or []
    check("Point K: the first k of 'edges, then nodes' - here the two edges, the fact with its validity",
          [x.get("kind") for x in ki] == ["edge", "edge"] and ki[0]["text"] == "Caroline: I adopted a dog "
          "(2023-05-20 - present)", str(ki)[:300])
    rb = safely(lambda: c.request("read", qid="q", query="Caroline dog", k=2, point="B"), {})
    check("Point B: all edges then all nodes (the harness's budget cuts)",
          [x.get("kind") for x in rb.get("items") or []] == ["edge", "edge", "node", "node"]
          and (rb.get("items") or [{}, {}, {}])[2]["text"].endswith(" speaks"), str(rb.get("items"))[:300])
    check("Point V is refused until the Zep LME template is pinned (Q-46b-4)",
          raises(lambda: c.request("read", qid="q", query="x", k=20, point="V"), B.ArmError, "Point V"))
    cr = safely(lambda: c.request("counters"), {})
    check("no reranker call and no LLM call on the RRF reads (Q-46b-3)",
          cr.get("reranker_calls") == 0 and cr.get("llm_calls") == 0, str(cr)[:200])
    c.close()

    c, _ = start("r1b", spec_for("r1b", "read", "u1"), env=child_env(extra={"NVT3_FAKE_RERANK": "1"}))
    safely(lambda: c.request("read", qid="q", query="Caroline dog", k=2, point="K"), {})
    crr = safely(lambda: c.request("counters"), {})
    check("the reranker counter works: a recipe that reranks shows up in reranker_calls (so its 0 means none)",
          crr.get("reranker_calls") == 2, str(crr)[:200])
    c.close()

    print("\n- an undated stand: the wall clock, strictly increasing, no validity rendered -")
    c, _ = start("w2", spec_for("w2", "write", "u2", dated=False))
    w2 = [safely(lambda m=m: c.request("write", item=dict(m, item_id="u2:" + m["item_id"][-1])), {}) for m in MSGS]
    refs = [x.get("reference_time") for x in w2]
    check("reference_time strictly increases over the unit's episodes",
          None not in refs and refs[0] < refs[1], str(refs))
    safely(lambda: c.request("end_write"), {})
    c.close()
    c, _ = start("r2", spec_for("r2", "read", "u2", dated=False))
    r2 = safely(lambda: c.request("read", qid="q", query="dog", k=5, point="K"), {})
    check("no validity is rendered on an undated stand", all("(" not in x["text"] for x in r2.get("items") or [])
          and r2.get("items"), str(r2.get("items"))[:200])
    c.close()
    sys.path.insert(0, str(ARMS_DIR))
    _aspec = importlib.util.spec_from_file_location("v3_arm_graphiti_inproc", ADAPTER)
    AG = importlib.util.module_from_spec(_aspec)
    _aspec.loader.exec_module(AG)
    hh = AG.Handler(dict(spec_for("inp", "write", "u9", dated=False)), {"g": None, "loop": None, "tally": {},
                                                                       "pacer": None}, {})
    hh.last_ref = datetime.now(timezone.utc) + timedelta(hours=1)
    check("an undated reference_time that does not strictly increase is refused by name (Q-46b-2)",
          raises(lambda: hh._reference_time(None), AG.Refused, "strictly increasing"))

    print("\n- refusals by name -")
    msg = hello_error("x1", spec_for("x1", "write", "u1"))
    check("a write stage on a graph that is not empty is refused", "fresh graph" in msg, msg[:200])
    c, _ = start("w3", spec_for("w3", "write", "u3"), env=child_env(extra={"NVT3_FAKE_GBG_LIMIT": "1"}))
    for m in MSGS:
        safely(lambda m=m: c.request("write", item=m, date="2023-05-20"), {})
    check("end_write refuses a graph the product reads back cut short (pagination), by name",
          raises(lambda: c.request("end_write"), B.ArmError, "cut short"))
    c.close()
    msg = hello_error("x2", spec_for("x2", "read", "u1", falkor_port=6390))
    check("a read stage on another server's graph is refused (the sealed store URI)", "not the one" in msg, msg[:200])
    gfile = FALKOR / "127.0.0.1_6380_u1.json"
    original = gfile.read_text(encoding="utf-8")
    # one field of the digest's canon at a time (the auditor's Z8 / Z9): each change alone is refused
    CANON = [("nodes", "uuid"), ("nodes", "name"), ("nodes", "summary"), ("nodes", "labels"),
             ("edges", "uuid"), ("edges", "source_node_uuid"), ("edges", "target_node_uuid"), ("edges", "name"),
             ("edges", "fact"), ("edges", "valid_at"), ("edges", "invalid_at"), ("edges", "expired_at"),
             ("episodes", "uuid"), ("episodes", "content"), ("episodes", "valid_at")]
    for n_field, (kind, fld) in enumerate(CANON):
        db = json.loads(original)
        row = db[kind][0]
        if fld in ("valid_at", "invalid_at", "expired_at"):
            row[fld] = "2024-01-02T03:04:05+00:00" if row.get(fld) != "2024-01-02T03:04:05+00:00" else None
        elif fld == "labels":
            row[fld] = list(row[fld]) + ["Edited"]
        else:
            row[fld] = str(row[fld]) + "-edited"
        gfile.write_text(json.dumps(db), encoding="utf-8")
        msg = hello_error(f"x3_{n_field}", spec_for(f"x3_{n_field}", "read", "u1"))
        check(f"a graph whose {kind[:-1]} {fld} alone changed between the stages is refused by name",
              "changed between the stages" in msg, msg[:160])
    gfile.write_text(original, encoding="utf-8")
    db = json.loads(original)
    db["edges"][0]["fact"] += " (edited)"
    gfile.write_text(json.dumps(db), encoding="utf-8")
    msg = hello_error("x3", spec_for("x3", "read", "u1"))
    check("a graph touched between the stages is refused by name, before the first search",
          "changed between the stages" in msg, msg[:200])
    msg = hello_error("x4", spec_for("x4", "write", "u4"), env=child_env(token=None))
    check("zep without its proxy token is refused", "proxy token" in msg, msg[:200])
    OLLAMA.tags = ["bge-m3:latest"]
    msg = hello_error("x5", spec_for("x5", "write", "u4"))
    check("a tag Ollama lacks is refused by name", "not in Ollama" in msg, msg[:200])
    OLLAMA.tags = [TAG]
    decoy = arm_dir("decoy")
    (decoy / "graphiti_core.py").write_text("__version__ = 'decoy'\n", encoding="utf-8")
    msg = hello_error("x6", spec_for("x6", "write", "u4"), adir=decoy)
    check("a graphiti_core module inside the arm directory is refused by name (Q-46-1)", "arm's directory" in msg,
          msg[:200])
    src = ADAPTER.read_text(encoding="utf-8")
    check("the counter is installed before the pacer (Q-46-4)", 0 < src.index("HC.install(") < src.index("P.install("))
finally:
    for ch in children:
        try:
            ch.kill()
        except OSError:
            pass
        try:
            ch.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass
    PROXY.close()
    OLLAMA.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm graphiti: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
