#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): research/v3/arms/arm_chroma.py - the chroma-store retrieval arm, driven as a real child laid
out as Q9 lays out an arm directory, against a FAKE chromadb (tests/fixtures/v3_fake_products/chromadb) that records
every call, and a fake Ollama on loopback.

* the adapter imports the standard library, its three siblings and chromadb only; a decoy chromadb in the arm directory
  is refused;
* PersistentClient at <unit>/store/chroma, collection "items" with chroma's defaults (no metadata of ours), the
  embedding function chroma's Ollama one with the v3 tag at the Ollama URL;
* write: add(ids=[str(index)], documents=[<the item's bytes>]) - no header added, item_sha256 / items_sha256;
* Q25 and the seal: the read stage is a new process that checks the seal, opens the collection with get_collection,
  and queries with n_results=k explicit; index and bytes back in rank order; a store touched between the stages is
  refused; a hit that is not the unit's is refused by name (in-process);
* refusals: a token in the environment, a write stage on an existing store, a tag Ollama lacks; no LLM call.

    python tests/research/_test_v3_arm_chroma.py
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
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_chroma.py"
FAKES = ROOT / "tests" / "fixtures" / "v3_fake_products"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_chroma", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
PASSED = FAILED = 0
TAG = "nvt3-bge-m3-d1"


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


class Ollama:
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
                srv.calls.append({"path": self.path})
                self._send({"models": [{"name": t, "model": t} for t in srv.tags]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                srv.calls.append({"path": self.path, "body": body})
                inp = body.get("input")
                self._send({"embeddings": [vec(t) for t in (inp if isinstance(inp, list) else [inp])]})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


TMP = Path(tempfile.mkdtemp(prefix="v3chroma_"))
OLLAMA = Ollama([TAG])
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
                "NO_PROXY": "127.0.0.1,localhost", "OLLAMA_HOST": f"http://127.0.0.1:{OLLAMA.port}",
                "NVT3_FAKE_LOG": str(LOG)})
    env.update(extra or {})
    return env


def spec_for(name: str, stage: str, unit_dir: Path) -> dict:
    return {"arm": "chroma-store", "stage": stage, "stand": "s1", "run": "r1", "unit": unit_dir.name,
            "unit_dir": str(unit_dir), "embed_tag": TAG, "ollama_url": f"http://127.0.0.1:{OLLAMA.port}",
            "record_path": str(TMP / f"{name}.start.json")}


def start(name: str, spec: dict, *, env: dict | None = None, adir: Path | None = None):
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.err.txt"
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_chroma.py"), str(sp)], stdin=subprocess.PIPE,
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


try:
    print("\n- Q-46-1 and Q9: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_chroma imports the standard library, base, _http_count, _ollama_pacer and chromadb only",
          mods <= {"__future__", "json", "os", "re", "sys", "time", "urllib", "pathlib", "typing", "base",
                   "_http_count", "_ollama_pacer", "chromadb"}, str(sorted(mods)))

    print("\n- the write stage -")
    U = TMP / "runs" / "s1" / "r1" / "chroma-store" / "u1"
    U.mkdir(parents=True)
    c, err = start("w", spec_for("w", "write", U))
    h = safely(lambda: c.request("hello"), {})
    check("hello: the venv's chromadb, no LLM, the tag", h.get("system") == "chromadb" and h.get("llm_label") is None
          and h.get("embedder") == TAG and str(FAKES) in str(((h.get("start") or {}).get("product") or {}).get("file")),
          str(h)[:300] + err.read_bytes().decode("utf-8", "replace")[-300:])
    pc = (logged("PersistentClient") or [{}])[-1]
    ef = (logged("OllamaEmbeddingFunction") or [{}])[-1]
    gc = (logged("get_or_create_collection") or [{}])[-1]
    check("PersistentClient at <unit>/store/chroma; collection 'items' with chroma's defaults (no metadata of ours)",
          pc.get("path") == str(U / "store" / "chroma") and gc.get("name") == "items" and gc.get("metadata") is None,
          f"{pc} {gc}")
    check("the embedding function: chroma's Ollama one, the v3 tag at the Ollama URL, nothing else",
          ef.get("model_name") == TAG and ef.get("url") == f"http://127.0.0.1:{OLLAMA.port}" and ef.get("extra") == [],
          str(ef))
    ITEMS = {5: "Conversation from 2023-05-20:\nthe cat sat on the mat", 2: "a bounded retry fixed the upload",
             9: "paris is in france", 11: "  \n\tspaces and newlines at both ends \n\n  "}
    ans = {i: safely(lambda i=i, t=t: c.request("write", item={"item_id": f"u1:{i}", "index": i, "text": t}), {})
           for i, t in ITEMS.items()}
    adds = logged("add")[-4:]
    check("write: add(ids=[str(index)], documents=[<the item's bytes EXACTLY, whitespace at both ends kept (C6)>]) - no "
          "header added, no metadata",
          [(a.get("ids"), a.get("documents"), a.get("metadatas")) for a in adds]
          == [([str(i)], [t], None) for i, t in ITEMS.items()], str(adds)[:300])
    check("write: item_sha256 per item", all(ans[i].get("item_sha256") == B.text_sha256(t) for i, t in ITEMS.items()))
    check("a repeated index is refused",
          raises(lambda: c.request("write", item={"item_id": "d", "index": 2, "text": "x"}), B.ArmError, "new"))
    check("a read in the write stage is refused (Q25)",
          raises(lambda: c.request("read", qid="q", query="x", k=2), B.ArmError, "read stage"))
    e = safely(lambda: c.request("end_write"), {})
    check("end_write: the count, items_sha256 over (index, sha), the store sealed",
          (e.get("footprint") or {}).get("retrievable") == 4
          and e.get("items_sha256") == B.items_digest({i: B.text_sha256(t) for i, t in ITEMS.items()})
          and (U / B.SEAL_NAME).is_file(), str(e)[:200])
    c.close()

    print("\n- the read stage: a new process on the sealed store -")
    c, err = start("r", spec_for("r", "read", U))
    r = safely(lambda: c.request("read", qid="q", query="bounded retry upload", k=2), {})
    q = (logged("query") or [{}])[-1]
    check("read: get_collection (never a create on the read stage), then query with n_results=k explicit",
          (logged("get_collection") or [{}])[-1].get("name") == "items" and q.get("n_results") == 2
          and q.get("query_texts") == ["bounded retry upload"], str(q))
    check("read: the index and the item's bytes back, in rank order, k of them",
          (r.get("items") or [{}])[0] == {"index": 2, "text": ITEMS[2], "rank": 1} and r.get("items_returned") == 2,
          str(r.get("items"))[:200])
    cn = safely(lambda: c.request("counters"), {})
    check("no LLM call: llm_calls 0; the embeds counted as Ollama calls",
          cn.get("llm_calls") == 0 and ((cn.get("http") or {}).get("counts") or {}).get("ollama:embed", {}).get(
              "attempts", 0) >= 1, str(cn)[:200])
    check("a write in the read stage is refused (Q25)",
          raises(lambda: c.request("write", item={"item_id": "x", "index": 7, "text": "t"}), B.ArmError, "write stage"))
    c.close()

    print("\n- a hit that is not the unit's (in-process, on a fake collection) -")
    sys.path.insert(0, str(ARMS_DIR))
    _aspec = importlib.util.spec_from_file_location("v3_arm_chroma_inproc", ADAPTER)
    AC = importlib.util.module_from_spec(_aspec)
    _aspec.loader.exec_module(AC)

    def fake_read(ids):
        hh = AC.Handler(spec_for("inp", "read", TMP / "fake_unit"),
                        {"collection": NS(query=lambda **k: {"ids": [ids], "documents": [["d"] * len(ids)]}),
                         "pacer": None}, {})
        return hh.read(qid="q", query="x", k=3)
    check("ids that are indices read back", safely(lambda: fake_read(["4", "0"]), {}).get("items")
          == [{"index": 4, "text": "d", "rank": 1}, {"index": 0, "text": "d", "rank": 2}])
    for bad in ("x1", "-3", "", "4.0"):
        check(f"a hit with id {bad!r} is not one of the unit's items - refused by name",
              raises(lambda bad=bad: fake_read([bad]), RuntimeError, "not one of the unit's items"))

    print("\n- refusals by name -")
    U2 = TMP / "runs" / "s1" / "r1" / "chroma-store" / "u2"
    U2.mkdir(parents=True)
    msg = hello_error("x1", spec_for("x1", "write", U2), env=child_env({"DEEPSEEK_API_KEY": "nvt3-x-" + "a" * 32}))
    check("a token in the environment is refused (no LLM)", "has no LLM" in msg, msg[:200])
    msg = hello_error("x2", spec_for("x2", "write", U))
    check("a write stage on an existing store is refused", "fresh store" in msg, msg[:200])
    OLLAMA.tags = ["bge-m3:latest"]
    msg = hello_error("x3", spec_for("x3", "write", U2))
    check("a tag Ollama lacks is refused by name", "not in Ollama" in msg, msg[:200])
    OLLAMA.tags = [TAG]
    decoy = arm_dir("decoy")
    (decoy / "chromadb.py").write_text("__version__ = 'decoy'\n", encoding="utf-8")
    msg = hello_error("x4", spec_for("x4", "write", U2), adir=decoy)
    check("a chromadb module inside the arm directory is refused by name (Q-46-1)", "arm's directory" in msg, msg[:200])
    src = ADAPTER.read_text(encoding="utf-8")
    check("the counter is installed before the pacer (Q-46-4)", 0 < src.index("HC.install(") < src.index("P.install("))
    victim = next(f for f in sorted((U / "store").rglob("*")) if f.is_file())
    victim.write_bytes(victim.read_bytes() + b" ")
    msg = hello_error("x5", spec_for("x5", "read", U))
    check("a store touched between the stages is refused by name (Q-45-5)", "changed between the stages" in msg,
          msg[:200])
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
    OLLAMA.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm chroma: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
