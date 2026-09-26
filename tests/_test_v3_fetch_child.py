#!/usr/bin/env python3
"""PREREG-V3 plan step A3.b: the fetch child (research/v3/fetch_child.py), through a real catcher and a fake hop.

The auditor's O1 ruling: every A3 fetch is a child under the contract. Here the child's code runs against a catch-mode
proxy (the real one) whose window tunnels through a fake hop to a local TLS server for huggingface.co and
cdn-lfs.hf.co (a throwaway certificate made at test time):

* a metadata GET is saved in the working directory with its sha256 and git blob sha1; a file behind one redirect to
  an allowed host is fetched, checked against the expected sha256 and size, and renamed from .partial only then;
  a chunked body (no length) still gets its git blob sha1, taken from the saved file;
* a HEAD reports the redirect's host and never follows it (discovery);
* refused, with nothing written: a second redirect or one the job does not allow, a redirect to a host outside the
  job, http, another host, userinfo, a save path leaving the working directory, a body over max_bytes, an encoded
  body, a hash or size mismatch, a status other than 200/redirect;
* the child only ever talks to the catcher: the contract's HTTPS_PROXY must be the loopback catcher (anything else
  refuses the job), every tunnel shows in the catcher's log and on the hop, and nothing dials a host directly;
* its own main(), with the default TLS context, refuses the untrusted test certificate: nothing is saved.

    python tests/_test_v3_fetch_child.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_llm_proxy_fc", ROOT / "research" / "_llm_proxy.py")
FC = _load("v3_fetch_child", ROOT / "research" / "v3" / "fetch_child.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_child_"))
HF, CDN = "huggingface.co", "cdn-lfs.hf.co"
DIALS: list[tuple] = []
_real_connect = socket.create_connection


def _guarded(address, *a, **kw):
    DIALS.append(tuple(address))
    if address[0] not in ("127.0.0.1", "localhost", "::1"):
        raise OSError("direct dial refused by the test")
    return _real_connect(address, *a, **kw)


socket.create_connection = _guarded


def blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


META = json.dumps({"sha": "0123456789abcdef0123456789abcdef01234567", "cardData": {"license": "mit"}}).encode()
DATA = b'{"question": "synthetic", "answer": "synthetic"}\n' * 200
BIG = b"x" * 5000
made = TF.make_test_cert(TMP / "cert", HF, extra_hosts=(CDN, "evil.example"))
if made is None:
    print("  SKIP the fetch child checks: neither cryptography nor openssl is available (not passed)")
    sys.exit(0 if FAILED == 0 else 1)
TRUST = ssl.create_default_context(cafile=str(made[0]))
ROUTES = {
    "/api/datasets/x/revision/main": (200, [("Content-Type", "application/json")], META),
    "/datasets/x/resolve/0123/data.json": (302, [("Location", f"https://{CDN}/blob/data.json")], b""),
    "/blob/data.json": (200, [("Content-Type", "application/octet-stream")], DATA),
    "/datasets/x/resolve/0123/two.json": (302, [("Location", f"https://{HF}/datasets/x/resolve/0123/data.json")], b""),
    "/datasets/x/resolve/0123/away.json": (302, [("Location", "https://evil.example/blob/data.json")], b""),
    "/chunked.json": (200, [("Transfer-Encoding", "chunked")], DATA),
    "/gz.json": (200, [("Content-Encoding", "gzip")], DATA),
    "/big.bin": (200, [], BIG),
}
srv = TF.TlsHttpServer(made[0], made[1], ROUTES)
hop = TF.TunnelHop(srv.port)
cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="fetch", mode="catch")], run_dir=TMP / "proxy", via_port=hop.port,
                    control_token="ctl")
px = P.Proxy(cfg, None, log=lambda m: None)
ports = px.start()
CATCHER = ports["arms"]["fetch"]["catcher"]
px.windows["a3-test"] = {"hosts": frozenset({HF, CDN}), "arms": frozenset({"fetch"})}


def job(requests, *, hosts=(HF, CDN), max_redirects=1, cwd_name="w"):
    cwd = TMP / cwd_name
    cwd.mkdir(exist_ok=True)
    try:
        return FC.run_job({"hosts": list(hosts), "max_redirects": max_redirects, "requests": requests}, cwd=cwd,
                          port=CATCHER, ctx=TRUST), cwd
    except Exception as e:  # noqa: BLE001 - a crash fails the checks that read these results, by name
        return [{"id": r.get("id"), "ok": False, "error": f"crash {type(e).__name__}", "status": None,
                 "final_host": None, "redirect_host": None, "bytes": 0, "sha256": None, "git_blob_sha1": None}
                for r in requests], cwd


print("\n- metadata, one redirect, chunked, HEAD -")
(r_meta, r_file, r_chunk, r_head), cwd = job([
    {"id": "meta", "url": f"https://{HF}/api/datasets/x/revision/main", "save": "meta/rev.json", "max_bytes": 1 << 20},
    {"id": "file", "url": f"https://{HF}/datasets/x/resolve/0123/data.json", "save": "files/data.json",
     "max_bytes": 1 << 20, "expect": {"sha256": hashlib.sha256(DATA).hexdigest(), "size": len(DATA)}},
    {"id": "chunk", "url": f"https://{HF}/chunked.json", "save": "files/chunk.json", "max_bytes": 1 << 20},
    {"id": "head", "method": "HEAD", "url": f"https://{HF}/datasets/x/resolve/0123/data.json"}])
check("a metadata GET is saved with its sha256 and git blob sha1",
      r_meta["ok"] and (cwd / "meta" / "rev.json").read_bytes() == META and r_meta["sha256"] == hashlib.sha256(META).hexdigest()
      and r_meta["git_blob_sha1"] == blob_sha1(META), str(r_meta))
check("a file behind one redirect to an allowed host is fetched, checked and renamed from .partial",
      r_file["ok"] and r_file["final_host"] == CDN and (cwd / "files" / "data.json").read_bytes() == DATA
      and not (cwd / "files" / "data.json.partial").exists() and r_file["git_blob_sha1"] == blob_sha1(DATA), str(r_file))
check("a chunked body (no length) still gets its git blob sha1, from the saved file",
      r_chunk["ok"] and r_chunk["git_blob_sha1"] == blob_sha1(DATA) and r_chunk["bytes"] == len(DATA), str(r_chunk))
n_blob = sum(1 for h in srv.heads if h.startswith(b"GET /blob/data.json"))
check("a HEAD reports the redirect's host and never follows it",
      r_head["ok"] and r_head["status"] == 302 and r_head["redirect_host"] == CDN and n_blob == 1
      and not any(h.startswith(b"HEAD /blob") for h in srv.heads), f"{r_head} blob GETs={n_blob}")

print("\n- refusals, nothing written -")
cases = [
    ("a redirect when the job allows none", {"url": f"https://{HF}/datasets/x/resolve/0123/data.json", "save": "r/a.json",
                                             "max_bytes": 1 << 20}, {"max_redirects": 0}, "more redirects"),
    ("a second redirect", {"url": f"https://{HF}/datasets/x/resolve/0123/two.json", "save": "r/b.json",
                           "max_bytes": 1 << 20}, {}, "more redirects"),
    ("a redirect to a host outside the job", {"url": f"https://{HF}/datasets/x/resolve/0123/away.json",
                                              "save": "r/c.json", "max_bytes": 1 << 20}, {}, "not an allowed"),
    ("plain http", {"url": f"http://{HF}/blob/data.json", "save": "r/d.json", "max_bytes": 1 << 20}, {}, "not an allowed"),
    ("another host", {"url": "https://evil.example/blob/data.json", "save": "r/e.json", "max_bytes": 1 << 20}, {},
     "not an allowed"),
    ("userinfo in the URL", {"url": f"https://u:p@{HF}/blob/data.json", "save": "r/f.json", "max_bytes": 1 << 20}, {},
     "not an allowed"),
    ("a save path with ..", {"url": f"https://{CDN}/blob/data.json", "save": "../escaped.json", "max_bytes": 1 << 20},
     {}, "inside the working directory"),
    ("an absolute save path", {"url": f"https://{CDN}/blob/data.json", "save": "C:/escaped.json", "max_bytes": 1 << 20},
     {}, "inside the working directory"),
    ("a body over max_bytes (by Content-Length)", {"url": f"https://{HF}/big.bin", "save": "r/g.bin", "max_bytes": 100},
     {}, "larger than max_bytes"),
    ("a chunked body over max_bytes", {"url": f"https://{HF}/chunked.json", "save": "r/h.json", "max_bytes": 100},
     {}, "larger than max_bytes"),
    ("an encoded body", {"url": f"https://{HF}/gz.json", "save": "r/i.json", "max_bytes": 1 << 20}, {}, "encoded"),
    ("a sha256 mismatch", {"url": f"https://{CDN}/blob/data.json", "save": "r/j.json", "max_bytes": 1 << 20,
                           "expect": {"sha256": "0" * 64}}, {}, "sha256"),
    ("a size mismatch", {"url": f"https://{CDN}/blob/data.json", "save": "r/k.json", "max_bytes": 1 << 20,
                         "expect": {"size": 1}}, {}, "size"),
    ("a 404", {"url": f"https://{HF}/missing", "save": "r/l.json", "max_bytes": 1 << 20}, {}, "status 404"),
    ("a GET without max_bytes", {"url": f"https://{CDN}/blob/data.json", "save": "r/m.json"}, {}, "max_bytes"),
    ("a method other than GET/HEAD", {"method": "POST", "url": f"https://{HF}/x"}, {}, "only GET and HEAD"),
]
for label, req, kw, want in cases:
    (res,), cwd = job([{"id": label, **req}], cwd_name="ref", **kw)
    saved = [p.relative_to(cwd).as_posix() for p in cwd.rglob("*") if p.is_file()]
    check(f"{label}: refused by name, nothing written", not res["ok"] and want in (res["error"] or "") and saved == [],
          f"{res['error']} saved={saved}")
check("no request ever reached evil.example through the catcher",
      not any(r.get("host") == "evil.example" and r.get("tunnelled")
              for r in (json.loads(x) for x in (TMP / "proxy" / "catcher.jsonl").read_bytes().decode().splitlines())))

print("\n- only through the catcher -")
for label, env in (("unset", {}), ("another host", {"HTTPS_PROXY": "http://10.0.0.5:3128"}),
                   ("https scheme", {"HTTPS_PROXY": "https://127.0.0.1:1234"}), ("with a path", {"HTTPS_PROXY": "http://127.0.0.1:1/x"})):
    try:
        FC.catcher_port(env)
        check(f"HTTPS_PROXY {label} is refused", False)
    except FC.Refused:
        check(f"HTTPS_PROXY {label} is refused", True)
check("the loopback catcher is accepted", FC.catcher_port({"HTTPS_PROXY": f"http://127.0.0.1:{CATCHER}"}) == CATCHER)
log = [json.loads(x) for x in (TMP / "proxy" / "catcher.jsonl").read_bytes().decode().splitlines()]
check("every fetch was a catcher tunnel through the hop, recorded", all(r["via"] == f"127.0.0.1:{hop.port}" for r in log)
      and {r["host"] for r in log if r["tunnelled"]} == {HF, CDN}, str({(r["host"], r["tunnelled"]) for r in log}))
check("nothing dialled a host directly", [d for d in DIALS if d[0] not in ("127.0.0.1", "localhost", "::1")] == [])

print("\n- its own main(): the default TLS context -")
wd = TMP / "main"
wd.mkdir()
env = {k: v for k, v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")}
env["HTTPS_PROXY"] = f"http://127.0.0.1:{CATCHER}"
r = subprocess.run([sys.executable, str(ROOT / "research" / "v3" / "fetch_child.py")], cwd=str(wd), env=env,
                   input=json.dumps({"hosts": [CDN], "max_redirects": 0, "requests": [
                       {"id": "m", "url": f"https://{CDN}/blob/data.json", "save": "d.json", "max_bytes": 1 << 20}]}).encode(),
                   capture_output=True, timeout=120)
summary = json.loads((wd / "fetch_summary.json").read_bytes())
check("with the default context the untrusted test certificate is refused: exit 3, nothing saved",
      r.returncode == 3 and not summary[0]["ok"] and "SSL" in (summary[0]["error"] or "") and not (wd / "d.json").exists()
      and not (wd / "d.json.partial").exists(), f"{r.returncode} {summary}")
check("main prints counts only, never a body", DATA[:20] not in r.stdout and b'"requests": 1' in r.stdout, r.stdout[:80])

px.stop()
hop.close()
srv.close()
socket.create_connection = _real_connect
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch child: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
