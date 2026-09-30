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
* its own main(), with the default TLS context, refuses the untrusted test certificate: nothing is saved;
* a rate limit stops the job (the auditor's ruling): a 429 from any host, or a 403 from api.github.com, marks the
  request rate-limited and every later request is recorded as not sent - the server sees none of them; a 403 from
  another host is an ordinary refusal and the job goes on;
* raw_encoding (plan d8's e-print): a request that names gzip / x-gzip keeps a body the server sent so encoded as it
  came - never decoded, its sha256 and size of those bytes, its Content-Encoding and Content-Type recorded; any other
  encoding is still refused, a raw_encoding other than a non-empty list of those two, or on anything but a GET of an
  arXiv e-print URL, is refused before anything is sent, and a request without it keeps its summary's keys.

    python tests/_test_v3_fetch_child.py
"""
from __future__ import annotations

import gzip
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
AX = "arxiv.org"
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
GZ = gzip.compress(DATA, mtime=0)
GHAPI = "api.github.com"
made = TF.make_test_cert(TMP / "cert", HF, extra_hosts=(CDN, "evil.example", GHAPI, AX))
if made is None:
    print("  SKIP the fetch child checks: neither cryptography nor openssl is available (not passed)")
    sys.exit(0 if FAILED == 0 else 1)
#: B-ISS-CLOSE: cdn-lfs.hf.co gets a certificate of its own (by SNI), so a redirect's two hosts name two issuers
made_cdn = TF.make_test_cert(TMP / "cert_cdn", CDN, org="CDN Test CA")
BUNDLE = TMP / "bundle.pem"
BUNDLE.write_bytes(Path(made[0]).read_bytes() + Path(made_cdn[0]).read_bytes())
TRUST = ssl.create_default_context(cafile=str(BUNDLE))
ROUTES = {
    "/api/datasets/x/revision/main": (200, [("Content-Type", "application/json")], META),
    "/datasets/x/resolve/0123/data.json": (302, [("Location", f"https://{CDN}/blob/data.json")], b""),
    "/blob/data.json": (200, [("Content-Type", "application/octet-stream")], DATA),
    "/datasets/x/resolve/0123/two.json": (302, [("Location", f"https://{HF}/datasets/x/resolve/0123/data.json")], b""),
    "/datasets/x/resolve/0123/away.json": (302, [("Location", "https://evil.example/blob/data.json")], b""),
    "/chunked.json": (200, [("Transfer-Encoding", "chunked")], DATA),
    "/gz.json": (200, [("Content-Encoding", "gzip")], DATA),
    "/gzraw.bin": (200, [("Content-Encoding", "x-gzip"), ("Content-Type", "application/x-eprint-tar")], GZ),
    "/e-print/2501.00001v1": (200, [("Content-Encoding", "x-gzip"), ("Content-Type", "application/x-eprint-tar")], GZ),
    "/e-print/2501.00002v1": (200, [("Content-Encoding", "br")], DATA),
    "/src/2501.00001v1": (200, [("Content-Encoding", "x-gzip"), ("Content-Type", "application/gzip")], GZ),
    "/closing": (301, [("Location", f"https://{CDN}/blob/data.json?X-Sig=secret"), ("Connection", "close")], b""),
    "/closing200": (200, [("Connection", "close")], DATA),
    "/big.bin": (200, [], BIG),
    "/limited": (429, [("Retry-After", "60")], b""),
    "/repos/o/r": (403, [], b'{"message": "API rate limit exceeded"}'),
    "/forbidden": (403, [], b""),
}
srv = TF.TlsHttpServer(made[0], made[1], ROUTES, sni={CDN: (made_cdn[0], made_cdn[1])})
hop = TF.TunnelHop(srv.port)
cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="fetch", mode="catch")], run_dir=TMP / "proxy", via_port=hop.port,
                    control_token="ctl")
px = P.Proxy(cfg, None, log=lambda m: None)
ports = px.start()
CATCHER = ports["arms"]["fetch"]["catcher"]
px.windows["a3-test"] = {"hosts": frozenset({HF, CDN, GHAPI, AX}), "arms": frozenset({"fetch"})}


def settled_log(want_tunnelled=frozenset(), wait_s: float = 10.0) -> list[dict]:
    """The catcher's log once no catcher connection is open and ``want_tunnelled`` hosts all have a tunnel line - or
    what is there when ``wait_s`` passes. A tunnel's line is written when it closes, which can be after the child's job
    has returned (CI e8e9088, Windows 3.12): a row that reads the log at once can miss it."""
    import time as _t  # noqa: PLC0415
    end = _t.monotonic() + wait_s
    while True:
        f = TMP / "proxy" / "catcher.jsonl"
        log = [json.loads(x) for x in f.read_bytes().decode().splitlines()] if f.exists() else []
        with px._lock:
            still = sum(c.catcher_open for c in px.counters.values())
        if (still == 0 and set(want_tunnelled) <= {r["host"] for r in log if r["tunnelled"]}) or _t.monotonic() > end:
            return log
        _t.sleep(0.05)


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
check("each request records its peer certificate's issuer (the target of a redirect for a redirected one)",
      r_meta["issuer_cn"] == HF and r_file["issuer_cn"] == CDN and r_file["final_host"] == CDN, str((r_meta["issuer_cn"],
                                                                                                 r_file["issuer_cn"])))
check("a chunked body (no length) still gets its git blob sha1, from the saved file",
      r_chunk["ok"] and r_chunk["git_blob_sha1"] == blob_sha1(DATA) and r_chunk["bytes"] == len(DATA), str(r_chunk))
n_blob = sum(1 for h in srv.heads if h.startswith(b"GET /blob/data.json"))
check("a HEAD reports the redirect's host and never follows it",
      r_head["ok"] and r_head["status"] == 302 and r_head["redirect_host"] == CDN and n_blob == 1
      and not any(h.startswith(b"HEAD /blob") for h in srv.heads), f"{r_head} blob GETs={n_blob}")

print("\n- B-ISS-CLOSE: the issuer read on connect, every host of a redirect kept -")
(c1,), _ = job([{"id": "c1", "url": f"https://{HF}/closing", "save": "cl/a.json", "max_bytes": 1 << 20}],
               max_redirects=0, cwd_name="closing")
(c1h,), _ = job([{"id": "c1h", "method": "HEAD", "url": f"https://{HF}/closing"}], cwd_name="closing_head")
(c2,), _ = job([{"id": "c2", "url": f"https://{HF}/closing200", "save": "cl/b.json", "max_bytes": 1 << 20}],
               cwd_name="closing200")
check("ISS-C1: a response that closes its connection (Connection: close) still has its host's issuer - a refused 301, "
      "a HEAD's 301 and a 200 alike", not c1["ok"] and "more redirects" in (c1["error"] or "") and c1["issuer_cn"] == HF
      and c1h["ok"] and c1h["issuer_cn"] == HF and c2["ok"] and c2["issuer_cn"] == HF, str((c1, c1h, c2)))
check("ISS-C2: a redirect's summary names the Location's path without its query (a signature lives there) - "
      "redirect_path", c1.get("redirect_path") == "/blob/data.json" and c1h.get("redirect_path") == "/blob/data.json"
      and "X-Sig" not in json.dumps([c1, c1h]), str((c1.get("redirect_path"), c1h.get("redirect_path"))))
check("ISS-C3: a followed redirect keeps every host it connected to, each with its own issuer (hops); the summary's "
      "issuer is the last host's", r_file.get("hops") == [{"host": HF, "issuer_o": None, "issuer_cn": HF},
                                                          {"host": CDN, "issuer_o": "CDN Test CA", "issuer_cn": CDN}]
      and r_file["issuer_o"] == "CDN Test CA" and r_file.get("redirect_path") == "/blob/data.json", str(r_file))
check("ISS-C4: a request with no redirect has neither hops nor redirect_path - every other record reads as before",
      "hops" not in r_meta and "redirect_path" not in r_meta and "hops" not in c2 and "hops" not in c1, str(sorted(r_meta)))

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
(r_mis,), _ = job([{"id": "mis", "url": f"https://{CDN}/blob/data.json", "save": "r/n.json", "max_bytes": 1 << 20,
                     "expect": {"sha256": "0" * 64}}], cwd_name="recv")
check("a failed request says how much arrived before it failed: bytes None, bytes_received the body's length",
      r_mis["ok"] is False and r_mis["bytes"] is None and r_mis.get("bytes_received") == len(DATA), str(r_mis))
(r_404,), _ = job([{"id": "nf", "url": f"https://{HF}/missing", "save": "r/o.json", "max_bytes": 1 << 20}], cwd_name="recv404")
check("a request refused before any body byte has bytes_received 0", r_404["bytes"] is None and r_404.get("bytes_received") == 0,
      str(r_404))
check("no request ever reached evil.example through the catcher",
      not any(r.get("host") == "evil.example" and r.get("tunnelled")
              for r in settled_log()))

print("\n- raw_encoding: a body kept as the server encoded it (plan d8's e-print) -")
EP1 = f"https://{AX}/e-print/2501.00001v1"
(r_raw,), cwd_raw = job([{"id": "raw", "url": EP1, "save": "raw/e.bin", "max_bytes": 1 << 20,
                          "raw_encoding": ["gzip", "x-gzip"]}], hosts=(AX,), cwd_name="raw")
check("RE-1: with raw_encoding a body the server sent as x-gzip is saved as sent, never decoded - its sha256 and size are "
      "of the bytes that came, and the request records their Content-Encoding and Content-Type",
      r_raw["ok"] and (cwd_raw / "raw" / "e.bin").read_bytes() == GZ and r_raw["sha256"] == hashlib.sha256(GZ).hexdigest()
      and r_raw["bytes"] == len(GZ) and r_raw.get("content_encoding") == "x-gzip"
      and r_raw.get("content_type") == "application/x-eprint-tar", str(r_raw))
(r_br,), cwd_br = job([{"id": "br", "url": f"https://{AX}/e-print/2501.00002v1", "save": "raw/b.bin",
                        "max_bytes": 1 << 20, "raw_encoding": ["gzip", "x-gzip"]}], hosts=(AX,), cwd_name="raw_br")
check("RE-2: raw_encoding takes only the encodings it names - a br body is still refused, nothing written",
      not r_br["ok"] and "encoded" in (r_br["error"] or "") and not any(p.is_file() for p in cwd_br.rglob("*")), str(r_br))
bad_raw: dict = {}
for label, val in (("br", ["br"]), ("an empty list", []), ("a string", "gzip"), ("gzip and deflate", ["gzip", "deflate"])):
    before = len(srv.heads)
    (r_x,), _ = job([{"id": "x", "url": EP1, "save": "raw/x.bin", "max_bytes": 1 << 20,
                      "raw_encoding": val}], hosts=(AX,), cwd_name=f"raw_bad_{len(bad_raw)}")
    bad_raw[label] = (not r_x["ok"] and "raw_encoding" in (r_x["error"] or "") and len(srv.heads) == before, r_x.get("error"))
check("RE-3: a raw_encoding other than a non-empty list of gzip / x-gzip is refused by name before anything is sent - "
      + ", ".join(bad_raw), all(v[0] for v in bad_raw.values()), str(bad_raw))
elsewhere: dict = {}
for label, req in (("a file on huggingface.co", {"url": f"https://{HF}/gzraw.bin"}),
                   ("a HEAD of the e-print", {"url": EP1, "method": "HEAD"}),
                   ("another path on arxiv.org", {"url": f"https://{AX}/abs/2501.00001v1"}),
                   ("an e-print URL with a query", {"url": EP1 + "?x=1"}),
                   ("a source URL with a query", {"url": f"https://{AX}/src/2501.00001v1?x=1"}),
                   ("a HEAD of the source", {"url": f"https://{AX}/src/2501.00001v1", "method": "HEAD"})):
    before = len(srv.heads)
    (r_y,), cwd_y = job([{"id": "y", "save": "raw/y.bin", "max_bytes": 1 << 20, "raw_encoding": ["x-gzip"], **req}],
                        hosts=(HF, AX), cwd_name=f"raw_else_{len(elsewhere)}")
    elsewhere[label] = (not r_y["ok"] and "raw_encoding is for an arXiv e-print GET only" in (r_y["error"] or "")
                        and len(srv.heads) == before and not any(p.is_file() for p in cwd_y.rglob("*")), r_y.get("error"))
check("RE-5: raw_encoding on anything but a GET of an arXiv e-print URL is refused by name before anything is sent - "
      + ", ".join(elsewhere), all(v[0] for v in elsewhere.values()), str(elsewhere))
(r_src,), cwd_src = job([{"id": "src", "url": f"https://{AX}/src/2501.00001v1", "save": "raw/s.bin", "max_bytes": 1 << 20,
                          "raw_encoding": ["gzip", "x-gzip"]}], hosts=(AX,), cwd_name="raw_src")
check("RE-6 (Q-D8-6 = O-a): the source URL /src/<id>v<n> (a7-arxiv-src s2's redirect_path) takes raw_encoding as the "
      "e-print URL does - saved as sent, its Content-Encoding recorded",
      r_src["ok"] and (cwd_src / "raw" / "s.bin").read_bytes() == GZ and r_src.get("content_encoding") == "x-gzip", str(r_src))
check("RE-4: a request without raw_encoding keeps its summary's keys (no content_encoding, no content_type) - every other "
      "window's record reads as before", r_meta["ok"] and "content_encoding" not in r_meta and "content_type" not in r_meta
      and "content_encoding" not in r_file, str(sorted(r_meta)))

print("\n- a rate limit stops the job -")
for label, first_url in (("a 429", f"https://{HF}/limited"), ("a 403 from api.github.com", f"https://{GHAPI}/repos/o/r")):
    before = len(srv.heads)
    (r1, r2, r3), _ = job([{"id": "first", "url": first_url, "save": "rl/a.json", "max_bytes": 1 << 20},
                           {"id": "second", "url": f"https://{CDN}/blob/data.json", "save": "rl/b.json", "max_bytes": 1 << 20},
                           {"id": "third", "url": f"https://{HF}/api/datasets/x/revision/main", "save": "rl/c.json",
                            "max_bytes": 1 << 20}], hosts=(HF, CDN, GHAPI), cwd_name=f"rl{len(label)}")
    sent_after = len(srv.heads) - before
    check(f"{label}: rate-limited, the rest of the job not sent, the server saw only the first",
          r1["rate_limited"] and "rate-limited" in (r1["error"] or "") and not r1["ok"]
          and r2["error"] == r3["error"] == "not sent: rate-limited earlier" and sent_after == 1, f"{r1} {r2} {sent_after}")
(f1, f2), _ = job([{"id": "f", "url": f"https://{HF}/forbidden", "save": "fb/a.json", "max_bytes": 1 << 20},
                   {"id": "g", "url": f"https://{CDN}/blob/data.json", "save": "fb/b.json", "max_bytes": 1 << 20}],
                  cwd_name="fb")
check("a 403 from another host is an ordinary refusal and the job goes on",
      not f1["rate_limited"] and "status 403" in (f1["error"] or "") and f2["ok"], f"{f1} {f2}")

print("\n- only through the catcher -")
for label, env in (("unset", {}), ("another host", {"HTTPS_PROXY": "http://10.0.0.5:3128"}),
                   ("https scheme", {"HTTPS_PROXY": "https://127.0.0.1:1234"}), ("with a path", {"HTTPS_PROXY": "http://127.0.0.1:1/x"})):
    try:
        FC.catcher_port(env)
        check(f"HTTPS_PROXY {label} is refused", False)
    except FC.Refused:
        check(f"HTTPS_PROXY {label} is refused", True)
check("the loopback catcher is accepted", FC.catcher_port({"HTTPS_PROXY": f"http://127.0.0.1:{CATCHER}"}) == CATCHER)
log = settled_log({HF, CDN, GHAPI, AX})
check("every fetch was a catcher tunnel through the hop, recorded", all(r["via"] == f"127.0.0.1:{hop.port}" for r in log)
      and {r["host"] for r in log if r["tunnelled"]} == {HF, CDN, GHAPI, AX}, str({(r["host"], r["tunnelled"]) for r in log}))
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
