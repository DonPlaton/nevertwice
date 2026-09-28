#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): research/v3/arms/arm_amem.py - the a-mem arm (A-mem at the pinned ceffb86), driven as a real
child laid out as Q9 lays out an arm directory, against FAKE agentic_memory / chromadb / openai / litellm / ollama /
sentence_transformers / rank_bm25 packages shaped as the pin (tests/fixtures/v3_fake_products), a fake proxy, a fake
Ollama leg and a fake Ollama on loopback.

* the auditor's Q-46b-5: one process for both stages (store_persistence memory); the embedding function is chroma's
  Ollama one with the v3 tag in EVERY retriever A-mem builds - the temporary one, the real one and the rebuild after
  evo_threshold notes; SentenceTransformer is never built (a product that asks is refused by name);
* S6L: OllamaController through litellm at OLLAMA_API_BASE = the arm's Ollama leg, qwen3-coder:30b, the json_schema
  request, no temperature sent; one note per turn "Speaker <speaker> says : <text>", time = the session date on a dated
  stand only; search_agentic(q, k); the timestamp rendered on a dated stand only; Point K the first k, Point B all;
* DeepSeek: OpenAIController reads OPENAI_BASE_URL (the proxy route) and OPENAI_API_KEY (the token) from the
  environment; DeepSeek refuses json_schema, so the write is refused as blocked:structured-output, by name;
* refusals: a wrong OPENAI_BASE_URL, no token, a token on S6L, a wrong OLLAMA_API_BASE, a stage other than "both", a
  model that is not the llm's, a decoy agentic_memory, a tag Ollama lacks.

    python tests/research/_test_v3_arm_amem.py
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_amem.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_amem", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"
TOKEN = "nvt3-a-mem-" + "b" * 32


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
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v] if any(v) else [1.0] + [0.0] * 31


class Server:
    """kind: 'proxy' refuses json_schema with a 400 (DeepSeek's answer); 'leg' answers /api/chat; 'ollama' embeds."""

    def __init__(self, kind: str, tags: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.kind, self.tags = kind, tags or []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code: int = 200) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
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
                if srv.kind == "proxy":
                    if (body.get("response_format") or {}).get("type") == "json_schema":
                        return self._send({"error": {"message": "This response_format type is unavailable now"}}, 400)
                    return self._send({"choices": [{"message": {"content": "{}"}}]})
                if srv.kind == "leg":
                    return self._send({"message": {"role": "assistant", "content": json.dumps(
                        {"keywords": ["k"], "context": "c", "tags": []})}})
                inp = body.get("input")
                self._send({"embeddings": [vec(t) for t in (inp if isinstance(inp, list) else [inp])]})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


TMP = Path(tempfile.mkdtemp(prefix="v3amem_"))
PROXY, LEG, OLLAMA = Server("proxy"), Server("leg"), Server("ollama", [TAG])
LOG = TMP / "fake_calls.jsonl"
children: list = []


def arm_dir(name: str) -> Path:
    d = TMP / "armdirs" / name
    d.mkdir(parents=True)
    for src in (ADAPTER, ARMS_DIR / "base.py", ARMS_DIR / "_http_count.py", ROOT / "research" / "_ollama_pacer.py"):
        shutil.copyfile(src, d / src.name)
    return d


def child_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    (home / "Temp").mkdir(parents=True)
    env.update({"PATH": os.pathsep.join([str(Path(sys.executable).parent)] + (
                    [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if os.name == "nt"
                    else ["/usr/bin", "/bin"])),
                "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home / "Temp"), "TMP": str(home / "Temp"),
                "TMPDIR": str(home / "Temp"), "PYTHONPYCACHEPREFIX": str(TMP / "pyc"), "PYTHONPATH": str(FAKES),
                "NO_PROXY": "127.0.0.1,localhost", "OLLAMA_HOST": OLLAMA.url, "NVT3_FAKE_LOG": str(LOG)})
    env.update(extra or {})
    return env


def s6l_env(extra: dict | None = None) -> dict:
    return child_env({"OLLAMA_API_BASE": LEG.url, **(extra or {})})


def ds_env(unit: str = "u1", extra: dict | None = None) -> dict:
    return child_env({"OPENAI_BASE_URL": f"http://127.0.0.1:{PROXY.port}/u/r1.{unit}/v1", "OPENAI_API_KEY": TOKEN,
                      **(extra or {})})


def fake_blobs(root: Path = FAKES) -> dict:
    out = {}
    for f in sorted((root / "agentic_memory").rglob("*.py")):
        if "__pycache__" not in f.parts:
            body = f.read_bytes().replace(b"\r\n", b"\n")
            out["agentic_memory/" + f.relative_to(root / "agentic_memory").as_posix()] = hashlib.sha1(
                b"blob %d\0" % len(body) + body).hexdigest()
    return out


PIN = {"commit": "ceffb860f0712bbae97b184d440df62bc910ca8d", "blobs": fake_blobs()}


def spec_for(name: str, llm: str, unit: str = "u1", *, dated=True, stage="both", model=None, pin=None) -> dict:
    return {"product_pin": pin or PIN, "arm": "a-mem", "stage": stage, "stand": "s6l" if llm == "ollama" else "s1", "run": "r1", "unit": unit,
            "unit_dir": str(TMP / "runs" / unit), "llm": llm,
            "llm_model": model or ("qwen3-coder:30b" if llm == "ollama" else "deepseek-flash"),
            "port": PROXY.port if llm == "deepseek" else None, "ollama_leg_url": LEG.url if llm == "ollama" else None,
            "embed_tag": TAG, "ollama_url": OLLAMA.url, "dated": dated, "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, env: dict, *, adir: Path | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.err.txt"
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_amem.py"), str(sp)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=open(errf, "wb"), cwd=str(wd), env=env)
    children.append(p)
    return B.ArmClient(p, default_timeout=180), errf


def hello_error(name: str, spec: dict, env: dict, *, adir: Path | None = None) -> str:
    c, _ = start(name, spec, env, adir=adir)
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


try:
    print("\n- Q-46-1 and Q9: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_amem imports the standard library, base, _http_count, _ollama_pacer, agentic_memory and chromadb only",
          mods <= {"__future__", "hashlib", "json", "os", "re", "sys", "time", "urllib", "pathlib", "typing", "base",
                   "_http_count", "_ollama_pacer", "agentic_memory", "chromadb"}, str(sorted(mods)))

    print("\n- S6L: the Ollama leg, one process for both stages -")
    c, err = start("l1", spec_for("l1", "ollama"), s6l_env())
    h = safely(lambda: c.request("hello"), {})
    _st = h.get("start") or {}
    check("R-EMBED-PATH (c): the start record - the pacer in observe mode (the proxy leg paces and retries) and the "
          "spec's Ollama URL as the arm's one route", _st.get("pacer") == "observe"
          and _st.get("ollama_route") == OLLAMA.url,
          str({k: _st.get(k) for k in ("pacer", "ollama_route")}))
    check("hello: agentic_memory from the venv, the S6L writer", h.get("system") == "a-mem"
          and h.get("llm_label") == "ollama:qwen3-coder:30b"
          and str(FAKES) in str(((h.get("start") or {}).get("product") or {}).get("file")),
          str(h)[:300] + err.read_bytes().decode("utf-8", "replace")[-400:])
    ams = (logged("AgenticMemorySystem") or [{}])[-1]
    check("AgenticMemorySystem(model_name=<tag>, llm_backend='ollama', llm_model='qwen3-coder:30b'), evo_threshold the "
          "product's default", ams.get("model_name") == TAG and ams.get("llm_backend") == "ollama"
          and ams.get("llm_model") == "qwen3-coder:30b" and ams.get("evo_threshold") == 100, str(ams))
    clients = logged("Client")
    efs = logged("OllamaEmbeddingFunction")
    check("the store is the pin's ephemeral chromadb.Client, and each retriever gets the Ollama EF with the tag",
          len(clients) >= 2 and all(x.get("allow_reset") is True for x in clients)
          and len(efs) >= 2 and all(x.get("model_name") == TAG and x.get("url") == OLLAMA.url for x in efs),
          f"{len(clients)} clients, {efs[:2]}")
    check("a read before end_write is refused (Q25)",
          raises(lambda: c.request("read", qid="q", query="x", k=3, point="K"), B.ArmError, "before end_write"))
    w = safely(lambda: c.request("write", item={"item_id": "u1:0", "speaker": "Caroline", "text": "I adopted a dog"},
                                 date="2023-05-20"), {})
    note = (logged("add_note") or [{}])[-1]
    check("one note per turn: 'Speaker <speaker> says : <text>', time = the session date",
          note.get("content") == "Speaker Caroline says : I adopted a dog" and note.get("time") == "2023-05-20",
          str(note))
    check("a dated write without its date is refused",
          raises(lambda: c.request("write", item={"item_id": "u1:x", "speaker": "C", "text": "t"}), B.ArmError,
                 "carries its date"))
    check("the write names the bytes it handed over", w.get("text_sha256") == B.text_sha256(
        "Speaker Caroline says : I adopted a dog"))
    lc = (logged("litellm.completion") or [{}])[-1]
    check("S6L's writer: litellm 'ollama_chat/qwen3-coder:30b' at the Ollama leg, the json_schema request, no "
          "temperature sent", lc.get("model") == "ollama_chat/qwen3-coder:30b" and lc.get("base") == LEG.url
          and lc.get("response_format_type") == "json_schema" and lc.get("temperature") == "<default>", str(lc))
    for i in range(1, 100):
        safely(lambda i=i: c.request("write", item={"item_id": f"u1:{i}", "speaker": "Melanie",
                                                    "text": f"note number {i}"}, date="2023-05-21"), {})
    cons = logged("consolidate_memories")
    e = safely(lambda: c.request("end_write"), {})
    check("after evo_threshold notes A-mem rebuilt its retriever - and it still embeds with the v3 tag (Q-46b-5)",
          len(cons) == 1 and e.get("ef_ok") is True and e.get("embedding_functions_built")
          == ["OllamaEmbeddingFunction"] * 3, str(e)[:300])
    check("SentenceTransformer was never built", not logged("SentenceTransformer"))
    check("end_write: 100 notes, store_persistence memory",
          (e.get("footprint") or {}).get("retrievable") == 100 and e.get("store_persistence") == "memory", str(e)[:200])
    check("a write after end_write is refused (Q25)",
          raises(lambda: c.request("write", item={"item_id": "x", "speaker": "a", "text": "b"}, date="2023-05-22"),
                 B.ArmError, "write stage is closed"))
    rk = safely(lambda: c.request("read", qid="q", query="Caroline adopted dog", k=3, point="K"), {})
    sa = (logged("search_agentic") or [{}])[-1]
    rb = safely(lambda: c.request("read", qid="q", query="Caroline adopted dog", k=3, point="B"), {})
    check("Point B: everything search_agentic returns, the linked neighbours included (here 2k)",
          len(rb.get("items") or []) == 6, str(len(rb.get("items") or [])))
    check("read: search_agentic(q, k); Point K the first k, the note's content with its timestamp",
          sa.get("k") == 3 and len(rk.get("items") or []) == 3
          and (rk.get("items") or [{}])[0]["text"] == "Speaker Caroline says : I adopted a dog (timestamp: 2023-05-20)",
          str(rk.get("items"))[:300])
    check("Point V is refused", raises(lambda: c.request("read", qid="q", query="x", k=3, point="V"), B.ArmError,
                                       "point is K or B"))
    cn = safely(lambda: c.request("counters"), {})
    tw, tr = (cn.get("stage_times") or {}).get("write") or [None, None], (cn.get("stage_times") or {}).get("read") or [
        None, None]
    check("counters: 100 LLM calls through the leg, write and read times apart",
          cn.get("llm_calls") == 100 and None not in tw + tr and tw[1] <= tr[0], str(cn)[:300])
    c.close()

    print("\n- an undated stand -")
    c, _ = start("l2", spec_for("l2", "ollama", "u2", dated=False), s6l_env())
    safely(lambda: c.request("write", item={"item_id": "u2:0", "speaker": "user", "text": "the flag is on"}), {})
    check("an undated write passes no time", (logged("add_note") or [{}])[-1].get("time") is None)
    safely(lambda: c.request("end_write"), {})
    r2 = safely(lambda: c.request("read", qid="q", query="flag", k=2, point="B"), {})
    check("no timestamp is rendered on an undated stand (A-mem's add-time is never presented as a date)",
          r2.get("items") and all("timestamp" not in x["text"] for x in r2.get("items") or []), str(r2)[:200])
    c.close()

    print("\n- DeepSeek: the expected block -")
    c, err = start("d1", spec_for("d1", "deepseek"), ds_env())
    hd = safely(lambda: c.request("hello"), {})
    check("hello on DeepSeek", hd.get("llm_label") == "deepseek:deepseek-flash",
          str(hd)[:200] + err.read_bytes().decode("utf-8", "replace")[-300:])
    oa = (logged("OpenAI") or [{}])[-1]
    check("OpenAIController's client takes the proxy route and the token from the environment",
          oa.get("base_url") == f"http://127.0.0.1:{PROXY.port}/u/r1.u1/v1" and oa.get("key_set") is True, str(oa))
    msg = ""
    try:
        c.request("write", item={"item_id": "u1:0", "speaker": "C", "text": "t"}, date="2023-05-20")
    except B.ArmError as ex:
        msg = str(ex)
    check("DeepSeek refuses json_schema: the write is refused as blocked:structured-output, by name",
          "blocked:structured-output" in msg, msg[:200])
    body = ((PROXY.calls or [{}])[-1]).get("body") or {}
    check("the refused request carried the pin's temperature 0.7 and json_schema, with the token",
          body.get("temperature") == 0.7 and (body.get("response_format") or {}).get("type") == "json_schema"
          and ((PROXY.calls or [{}])[-1]).get("auth") == f"Bearer {TOKEN}", str(body)[:200])
    c.close()

    print("\n- refusals by name -")
    msg = hello_error("x1", spec_for("x1", "deepseek"), ds_env(extra={"OPENAI_BASE_URL": f"http://127.0.0.1:{PROXY.port}/v1"}))
    check("a wrong OPENAI_BASE_URL is refused", "OPENAI_BASE_URL" in msg, msg[:200])
    env_nt = ds_env()
    env_nt.pop("OPENAI_API_KEY")
    msg = hello_error("x2", spec_for("x2", "deepseek"), env_nt)
    check("DeepSeek without its proxy token is refused", "proxy token" in msg, msg[:200])
    msg = hello_error("x3", spec_for("x3", "ollama"), s6l_env({"OPENAI_API_KEY": TOKEN}))
    check("S6L with a token set is refused (its writer is local)", "writer is local" in msg, msg[:200])
    msg = hello_error("x4", spec_for("x4", "ollama"), s6l_env({"OLLAMA_API_BASE": OLLAMA.url}))
    check("an OLLAMA_API_BASE that is not the arm's Ollama leg is refused", "Ollama leg" in msg, msg[:200])
    msg = hello_error("x5", spec_for("x5", "ollama", stage="write"), s6l_env())
    check("a stage other than 'both' is refused (the store lives in memory)", "stage 'both'" in msg, msg[:200])
    msg = hello_error("x6", spec_for("x6", "ollama", model="qwen3:8b"), s6l_env())
    check("a model that is not the llm's pinned writer is refused", "writes with" in msg, msg[:200])
    msg = hello_error("x7", spec_for("x7", "ollama"), s6l_env({"NVT3_FAKE_USE_ST": "1"}))
    check("a product that asks for a SentenceTransformer is refused by name - by the adapter's stub, before any load",
          "asked for a SentenceTransformer" in msg, msg[:200])
    c, _ = start("x7b", spec_for("x7b", "ollama", "u7"), s6l_env({"NVT3_FAKE_LOSE_EF": "1"}))
    for i in range(99):
        safely(lambda i=i: c.request("write", item={"item_id": f"u7:{i}", "speaker": "a", "text": f"n {i}"},
                                     date="2023-05-20"), {})
    msg = ""
    try:
        c.request("write", item={"item_id": "u7:99", "speaker": "a", "text": "n 99"}, date="2023-05-20")
    except B.ArmError as ex:
        msg = str(ex)
    check("a rebuild that loses the v3 embedding function is refused by name on the write that triggered it",
          "without the v3 embedding function" in msg, msg[:200])
    c.close()
    OLLAMA.tags = ["bge-m3:latest"]
    msg = hello_error("x8", spec_for("x8", "ollama"), s6l_env())
    check("a tag Ollama lacks is refused by name", "not in Ollama" in msg, msg[:200])
    OLLAMA.tags = [TAG]
    decoy = arm_dir("decoy")
    (decoy / "agentic_memory.py").write_text("__version__ = 'decoy'\n", encoding="utf-8")
    msg = hello_error("x9", spec_for("x9", "ollama"), s6l_env(), adir=decoy)
    check("an agentic_memory module inside the arm directory is refused by name (Q-46-1)", "arm's directory" in msg,
          msg[:200])
    src = ADAPTER.read_text(encoding="utf-8")
    check("the counter is installed before the pacer (Q-46-4)", 0 < src.index("HC.install(") < src.index("P.install("))

    print("\n- the pin, file for file (the auditor's rule for a product installed from git) -")
    hp = safely(lambda: json.loads((TMP / "l1.start.json").read_bytes()), {})
    check("the record carries the pinned commit and the installed blob map",
          (hp.get("product") or {}).get("commit") == PIN["commit"] and (hp.get("product") or {}).get("blobs") == PIN["blobs"],
          str(hp.get("product"))[:200])
    msg = hello_error("p1", spec_for("p1", "ollama", pin={"commit": "0" * 40, "blobs": PIN["blobs"]}), s6l_env())
    check("a product_pin naming another commit is refused (the double binding)", "not the pinned A-mem commit" in msg,
          msg[:200])
    foreign = TMP / "fakes_foreign"
    shutil.copytree(FAKES, foreign, ignore=shutil.ignore_patterns("__pycache__"))
    ms_file = foreign / "agentic_memory" / "memory_system.py"
    ms_file.write_bytes(ms_file.read_bytes() + b"\n# a foreign memory_system.py\n")
    msg = hello_error("p2", spec_for("p2", "ollama"), s6l_env({"PYTHONPATH": str(foreign)}))
    check("a foreign memory_system.py is refused by name", "different ['agentic_memory/memory_system.py']" in msg,
          msg[:200])
    extra = TMP / "fakes_extra"
    shutil.copytree(FAKES, extra, ignore=shutil.ignore_patterns("__pycache__"))
    (extra / "agentic_memory" / "extra.py").write_bytes(b"x = 1\n")
    msg = hello_error("p3", spec_for("p3", "ollama"), s6l_env({"PYTHONPATH": str(extra)}))
    check("an extra .py in the package is refused by name", "extra ['agentic_memory/extra.py']" in msg, msg[:200])
    msg = hello_error("p4", spec_for("p4", "ollama", pin={"commit": PIN["commit"],
                                                        "blobs": {**PIN["blobs"], "agentic_memory/gone.py": "0" * 40}}),
                      s6l_env())
    check("a pinned file that is not installed is refused by name", "missing ['agentic_memory/gone.py']" in msg,
          msg[:200])
    crlf = TMP / "fakes_crlf"
    shutil.copytree(FAKES, crlf, ignore=shutil.ignore_patterns("__pycache__"))
    rf = crlf / "agentic_memory" / "retrievers.py"
    rf.write_bytes(rf.read_bytes().replace(b"\n", b"\r\n"))
    c, err = start("p5", spec_for("p5", "ollama"), s6l_env({"PYTHONPATH": str(crlf)}))
    check("a checkout with CRLF line endings matches the pin (CRLF is normalised to LF)",
          safely(lambda: c.request("hello"), {}).get("ok") is True, err.read_bytes().decode("utf-8", "replace")[-200:])
    c.close()
    c, _ = start("p6", spec_for("p6", "ollama", "u6", dated=False), s6l_env())
    safely(lambda: c.request("write", item={"item_id": "u6:0", "speaker": "a", "text": "b"}, date="2023-05-20"), {})
    check("an undated stand passes no time even when a date arrives with the write",
          (logged("add_note") or [{}])[-1].get("time") is None)
    c.close()
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
    for s in (PROXY, LEG, OLLAMA):
        s.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm amem: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
