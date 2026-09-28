#!/usr/bin/env python3
"""PREREG-V3 A8 C4 (the auditor's Q-C4-1..3, C4A-1..6): research/v3/fetch_child.py's OCI job - spawns NO child. The job
runs in process against a fake registry (an injected wire); the real wire, tunnel_send, runs in process too, through
a loopback tunnel hop to a local TLS server with a throwaway certificate (no network, no process):

* Q-C4-1: every page of the tags list; the newest release is the largest ^v?X.Y.Z$ by the numbers (a pre-release,
  "latest", "nightly" never count); X.Y.Z and vX.Y.Z of one version must name the same index, else refused by name;
  the index's ONE linux/amd64 manifest without a variant (none or two refused, attempt 2 named as Q-A8-4's); C4A-6: a
  plain image manifest is taken when its config says linux/amd64 without a variant (C4A-7: amd64 has microarchitecture
  levels) - checked before any layer, index_digest None; C4A-8: oci_job never raises - a raise FAILs its row by name;
  C4A-5: the digest behind "latest" is read right after the tags, information only, a non-200 there never fatal;
* Q-C4-2: the token asked for exactly repository:<repo>:pull, sent only to the registry, recorded as <redacted> -
  never to the CDN host;
* Q-C4-3, C4A-2: a blob redirect is followed once, only to a declared CDN host, over https on 443, without userinfo
  and without Authorization; the signed query is recorded nowhere; anything else is refused by name with the host
  recorded and nothing sent there; a second redirect is refused;
* C4A-3: each blob on the disk must be its digest's bytes AND its declared size (each check alone); a malformed or
  traversing digest, and a declared size that is negative, a bool or past max_blob_bytes, are refused before any file
  - no file is ever aimed outside oci/blobs/sha256;
* C4A-4: a platform manifest of another media type, a job whose hosts are not exactly its registry, token service and
  CDN hosts, a next page anywhere but the registry's own list, and a repository name that is not one are refused;
* C4A-1: tunnel_send streams a 200 body to .partial then renames it; a Content-Length past max_bytes and a chunked
  body that grows past it are refused with no file and no .partial left; an encoded body is refused; a HEAD and a
  non-200 come back with no body.

    python tests/_test_v3_fetch_oci.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import ssl
import sys
import tempfile
import urllib.parse
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


FC = _load("v3_fetch_child_oci", ROOT / "research" / "v3" / "fetch_child.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


REG, AUTH, CDN = "registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com"
REPO = "letta/letta"
TOKEN = "nvt3-fake-registry-token-" + "q" * 40
SIG = "sig=SIGNEDQUERYSECRET123&exp=1790000000"
IDX_T, MAN_T = FC.OCI_INDEX[0], FC.OCI_MANIFEST[0]


def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def conf(os_="linux", arch="amd64", variant=None):
    return json.dumps({"architecture": arch, "os": os_, "config": {"Env": ["A=1"]},
                       "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "d" * 64]},
                       **({"variant": variant} if variant else {})}).encode()


CFG = conf()
L1, L2 = b"layer one bytes", b"layer two, the larger one" * 10


def manifest(cfg=CFG, layers=((L1, None), (L2, None)), mtype=MAN_T, digest_of=None):
    return json.dumps({"schemaVersion": 2, "mediaType": mtype, "config": {"mediaType": "x", "digest": sha(cfg), "size": len(cfg)},
                       "layers": [{"mediaType": "l", "digest": (digest_of or {}).get(i, sha(b)),
                                   "size": len(b) if size is None else size} for i, (b, size) in enumerate(layers)]}).encode()


MAN = manifest()
ARM = manifest(conf(arch="arm64"), ())


def index(entries):
    return json.dumps({"schemaVersion": 2, "mediaType": IDX_T, "manifests": entries}).encode()


def entry(man, arch="amd64", variant=None, mtype=MAN_T):
    p = {"os": "linux", "architecture": arch, **({"variant": variant} if variant else {})}
    return {"mediaType": mtype, "digest": sha(man), "size": len(man), "platform": p}


IDX = index([entry(ARM, "arm64"), entry(MAN), {**entry(b"x"), "digest": "sha256:" + "e" * 64,
                                              "platform": {"os": "linux", "architecture": "amd64", "variant": "v3"}}])
LATEST = index([{**entry(b"y"), "digest": "sha256:" + "f" * 64}])


class Registry:
    """A fake Docker Hub. Knobs: tag_body (what 0.10.0 and v0.10.0 serve), v_differs, header_digest, manifests,
    blobs, redirect (a function of the digest), double_redirect, link, rate_limit, latest_status."""

    def __init__(self, **k):
        self.k = k
        self.calls: list = []
        self.manifests = {sha(MAN): MAN, sha(ARM): ARM, **k.get("manifests", {})}
        self.blobs = {sha(CFG): CFG, sha(L1): L1, sha(L2): L2, **k.get("blobs", {})}

    def send(self, method, host, path, headers, *, max_bytes, save_to=None):
        self.calls.append({"method": method, "host": host, "path": path, "headers": dict(headers),
                           "save_to": str(save_to) if save_to is not None else None})
        k, bare = self.k, path.split("?", 1)[0]

        def ok(body, hdrs=None):
            if len(body) > max_bytes:
                raise FC.Refused("the body is larger than max_bytes")
            if save_to is not None:
                save_to.write_bytes(body)
                return 200, dict(hdrs or {}), b"", hashlib.sha256(body).hexdigest(), len(body)
            return 200, dict(hdrs or {}), body, hashlib.sha256(body).hexdigest(), len(body)
        if host == AUTH and bare == "/token":
            return ok(json.dumps({"token": TOKEN, "expires_in": 300}).encode())
        if host == REG and bare == f"/v2/{REPO}/tags/list":
            if k.get("rate_limit"):
                return 429, {}, b"", None, 0
            if "last=" not in path:
                link = k.get("link", f'</v2/{REPO}/tags/list?last=nightly&n=1000>; rel="next"')
                return ok(json.dumps({"tags": ["0.9.9", "latest", "0.11.0-rc1", "nightly"]}).encode(), {"link": link})
            return ok(json.dumps({"tags": ["0.10.0", "v0.10.0", "0.2.99"]}).encode())
        if host == REG and bare.startswith(f"/v2/{REPO}/manifests/"):
            ref = bare.rsplit("/", 1)[1]
            if ref == "latest":
                st = k.get("latest_status", 200)
                return ok(LATEST, {"docker-content-digest": sha(LATEST)}) if st == 200 else (st, {}, b"", None, 0)
            if ref in ("0.10.0", "v0.10.0"):
                body = k.get("tag_body", IDX)
                if ref == "v0.10.0" and k.get("v_differs"):
                    body = index([])
                return ok(body, {"docker-content-digest": k.get("header_digest", sha(body))})
            if ref in self.manifests:
                return ok(self.manifests[ref])
            return 404, {}, b"", None, 0
        if host == REG and bare.startswith(f"/v2/{REPO}/blobs/"):
            d = bare.split("/blobs/", 1)[1]
            if d == sha(CFG) or d not in (sha(L1), sha(L2)):
                if d in self.blobs:
                    return ok(self.blobs[d])
                return 404, {}, b"", None, 0
            loc = k["redirect"](d) if "redirect" in k else f"https://{CDN}/registry-v2/docker/blobs/{d}/data?{SIG}"
            return 307, {"location": loc}, b"", None, 0
        if host == CDN:
            if k.get("double_redirect"):
                return 307, {"location": f"https://{CDN}/again"}, b"", None, 0
            d = bare.split("/blobs/", 1)[1].split("/", 1)[0]
            return ok(self.blobs[d])
        return 404, {}, b"", None, 0


JOB = {"kind": "oci", "repo": REPO, "registry": REG, "auth": AUTH, "service": "registry.docker.io", "cdn_hosts": [CDN],
       "hosts": [REG, AUTH, CDN], "platform": {"os": "linux", "architecture": "amd64"}, "max_meta_bytes": 1 << 20,
       "max_blob_bytes": 1 << 24}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_oci_"))


RAISED: list = []


def run(tag, job=None, **knobs):
    """C4A-8: oci_job promises never to raise - a raise is recorded against its row (whose ok is then None, so the row
    FAILs by name) and against the census row at the end, never a crash of the whole suite."""
    cwd = TMP / tag
    cwd.mkdir()
    reg = Registry(**knobs)
    try:
        out = FC.oci_job(copy.deepcopy(job or JOB), send=reg.send, cwd=cwd)
    except Exception as e:  # noqa: BLE001 - the promise under test
        RAISED.append(f"{tag}: {type(e).__name__}: {e}")
        out = {"ok": None, "error": f"RAISED {type(e).__name__}: {e}"}
    return out, reg, cwd


def outside(reg, cwd) -> list:
    """Every file a request aimed at, or the tree holds, outside oci/blobs/sha256."""
    root = (cwd / "oci" / "blobs" / "sha256").resolve()
    aimed = [c["save_to"] for c in reg.calls if c["save_to"] and Path(c["save_to"]).resolve().parent != root]
    held = [str(p) for p in cwd.rglob("*") if p.is_file() and p.resolve().parent != root]
    return aimed + held


print("- the whole image, by the rules -")
out, reg, cwd = run("ok")
check("Q-C4-1: every page of the tags read; the newest release by the numbers is 0.10.0 (0.11.0-rc1, latest, nightly, "
      "0.9.9 and 0.2.99 are not), named by both its tags, one index", out.get("ok") and out.get("version") == "0.10.0"
      and out.get("tag_variants") == ["0.10.0", "v0.10.0"] and out.get("tag_pages") == 2 and out.get("tags_seen") == 7
      and out.get("index_digest") == sha(IDX) and out.get("single_manifest") is False,
      str({k: out.get(k) for k in ("ok", "error", "version", "tag_variants", "tag_pages")}))
check("Q-C4-1: the index's ONE linux/amd64 manifest without a variant is taken (not arm64, not amd64/v3)",
      out.get("manifest_digest") == sha(MAN) and out.get("manifest_media_type") == MAN_T, str(out.get("manifest_digest")))
blobs = cwd / "oci" / "blobs" / "sha256"
def _on_disk(d):
    f = blobs / d.split(":")[1]
    return f.read_bytes() if f.is_file() else None


check("every blob on the disk is its digest's bytes - the manifest, the config and both layers - with the sizes recorded",
      all(_on_disk(d) == b for d, b in ((sha(MAN), MAN), (sha(CFG), CFG), (sha(L1), L1), (sha(L2), L2)))
      and out.get("config") == {"digest": sha(CFG), "size": len(CFG)}
      and [x["digest"] for x in out.get("layers", [])] == [sha(L1), sha(L2)])
reqs = out.get("requests") or []
tag_i = [i for i, r in enumerate(reqs) if "/tags/list" in r["path"]]
lat_i = [i for i, r in enumerate(reqs) if r["path"].endswith("/manifests/latest")]
check("C4A-5: the digest behind 'latest' is read right after the last tags page (the same minute), recorded as "
      "information - and it is not the one chosen", tag_i and lat_i == [tag_i[-1] + 1]
      and out.get("latest_digest") == sha(LATEST) and out.get("latest_status") == 200
      and out.get("latest_digest") != out.get("index_digest"), f"tags at {tag_i}, latest at {lat_i}")
for st in (404, 429):
    o, r, cw = run(f"latest{st}", latest_status=st)
    check(f"C4A-5: a {st} on 'latest' is recorded and never fatal - the image is still fetched",
          o.get("ok") is True and o.get("latest_status") == st and o.get("latest_digest") is None
          and not o.get("rate_limited"), str({k: o.get(k) for k in ("ok", "error", "latest_status")}))
tok_calls = [c for c in reg.calls if c["host"] == AUTH]
check("Q-C4-2: the token is asked for exactly repository:letta/letta:pull, once, without credentials",
      len(tok_calls) == 1 and urllib.parse.parse_qs(tok_calls[0]["path"].split("?", 1)[1])
      == {"service": ["registry.docker.io"], "scope": ["repository:letta/letta:pull"]}
      and "Authorization" not in tok_calls[0]["headers"], str(tok_calls))
check("Q-C4-2: the token goes to the registry only - every registry call carries it, no CDN call does",
      all(c["headers"].get("Authorization") == f"Bearer {TOKEN}" for c in reg.calls if c["host"] == REG)
      and [c for c in reg.calls if c["host"] == CDN] and not any("Authorization" in c["headers"] for c in reg.calls if c["host"] == CDN),
      str([(c["host"], "Authorization" in c["headers"]) for c in reg.calls]))
dump = json.dumps(out)
check("Q-C4-2, Q-C4-3: the summary holds neither the token (only <redacted> and its expiry) nor the signed query",
      TOKEN not in dump and "SIGNEDQUERYSECRET" not in dump and out.get("token") == {"value": "<redacted>",
      "scope": "repository:letta/letta:pull", "expires_in": 300}, str(out.get("token")))
cdn_rec = [r for r in reqs if r["host"] == CDN]
check("Q-C4-3: each layer followed exactly one redirect to the declared CDN host; its record keeps host and path only",
      len(cdn_rec) == 2 and all("?" not in r["path"] and r["status"] == 200 for r in cdn_rec), str(cdn_rec))

print("\n- C4A-6: a plain image manifest -")
o, r, cw = run("single", tag_body=MAN)
check("C4A-6: a tag that names a plain image manifest is taken when its config says linux/amd64 - index_digest None, "
      "the manifest digest the tag's own, every blob fetched", o.get("ok") is True and o.get("single_manifest") is True
      and o.get("index_digest") is None and o.get("manifest_digest") == sha(MAN)
      and [x["digest"] for x in o.get("layers", [])] == [sha(L1), sha(L2)], str({k: o.get(k) for k in ("ok", "error", "index_digest")}))
ARMMAN = manifest(conf(arch="arm64"))
o, r, cw = run("single_arm", tag_body=ARMMAN, blobs={sha(conf(arch="arm64")): conf(arch="arm64")})
check("C4A-6: a plain manifest whose config is linux/arm64 is refused by name (attempt 2 named) - and no layer was asked "
      "for (the config is checked before any layer)", o.get("ok") is False and "linux/arm64" in (o.get("error") or "")
      and "attempt 2 is Q-A8-4's" in (o.get("error") or "")
      and not any(c["path"].endswith(sha(L1)) or c["path"].endswith(sha(L2)) for c in r.calls), str(o.get("error")))
V3CFG = conf(variant="v3")
o, r, cw = run("single_v3", tag_body=manifest(V3CFG), blobs={sha(V3CFG): V3CFG})
check("C4A-7: a plain manifest whose config is linux/amd64 with variant v3 (a microarchitecture level) is refused by "
      "name - and no layer was asked for", o.get("ok") is False and "linux/amd64/v3" in (o.get("error") or "")
      and not any(c["path"].endswith(sha(L1)) or c["path"].endswith(sha(L2)) for c in r.calls), str(o.get("error")))
o, r, cw = run("neither", tag_body=json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.in-toto+json"}).encode())
check("C4A-6: a tag that names neither an index nor an image manifest is refused by name",
      o.get("ok") is False and "neither an image index nor an image manifest" in (o.get("error") or ""), str(o.get("error")))

print("\n- refusals by name -")
cases = [
    ("v_differs", {"v_differs": True}, "resolve to different indexes", "Q-C4-1: X.Y.Z and vX.Y.Z naming different indexes"),
    ("no_amd64", {"tag_body": index([entry(ARM, "arm64")])}, "attempt 2 is Q-A8-4's", "Q-C4-1: an index with no linux/amd64 manifest (attempt 2 named)"),
    ("two_amd64", {"tag_body": index([entry(MAN), entry(ARM)])}, "holds 2 linux/amd64", "Q-C4-1: an index with two linux/amd64 manifests"),
    ("header_digest", {"header_digest": "sha256:" + "0" * 64}, "is not its bytes' digest", "a registry digest that is not the bytes'"),
    ("man_bytes", {"manifests": {sha(MAN): MAN + b" "}}, "not its digest's bytes", "a platform manifest that is not its digest's bytes"),
    ("undeclared_cdn", {"redirect": lambda d: f"https://evil.cdn.example/blobs/{d}?{SIG}"}, "an undeclared host",
     "Q-C4-3: a redirect to an undeclared host"),
    ("cdn_http", {"redirect": lambda d: f"http://{CDN}/blobs/{d}?{SIG}"}, "not https", "C4A-2: a redirect to the CDN over http"),
    ("cdn_port", {"redirect": lambda d: f"https://{CDN}:8443/blobs/{d}?{SIG}"}, "port 8443", "C4A-2: a redirect to the CDN on port 8443"),
    ("cdn_user", {"redirect": lambda d: f"https://u:p@{CDN}/blobs/{d}?{SIG}"}, "userinfo", "C4A-2: a redirect to the CDN with userinfo"),
    ("double", {"double_redirect": True}, "redirects a second time", "Q-C4-3: a second redirect"),
    ("link_off", {"link": '<https://evil.example/v2/letta/letta/tags/list?last=x>; rel="next"'}, "leaves the registry's list",
     "a next page off the registry"),
    ("link_path", {"link": '</v2/other/repo/tags/list?last=x>; rel="next"'}, "leaves the registry's list",
     "C4A-4: a next page to another list on the registry"),
    ("rate", {"rate_limit": True}, "rate-limited", "a rate limit"),
]
for tag, knobs, want, label in cases:
    o, r, cw = run(tag, **knobs)
    check(f"refused by name: {label}", o.get("ok") is False and want in (o.get("error") or ""), str(o.get("error")))
    if tag.startswith("cdn_") or tag == "undeclared_cdn":
        check(f"... {label}: the host is recorded and nothing was sent past the redirect",
              o.get("refused_redirect_host") in (CDN, "evil.cdn.example")
              and not any(c["host"] in (CDN, "evil.cdn.example") for c in r.calls), str(o.get("refused_redirect_host")))

print("\n- C4A-3: each blob check alone, digests and sizes -")
L2B = bytes(len(L2))                                      # the same size, other bytes
o, r, cw = run("same_size", blobs={sha(L2): L2B})
check("C4A-3: a layer of the right size but other bytes is refused (the digest check alone)",
      o.get("ok") is False and "not its digest's bytes or its size" in (o.get("error") or ""), str(o.get("error")))
MAN_SZ = manifest(layers=((L1, None), (L2, len(L2) + 1)))
o, r, cw = run("wrong_size", tag_body=index([entry(MAN_SZ)]), manifests={sha(MAN_SZ): MAN_SZ})
check("C4A-3: the right bytes with a wrong declared size are refused (the size check alone)",
      o.get("ok") is False and "not its digest's bytes or its size" in (o.get("error") or ""), str(o.get("error")))
for i_bad, (label, bad_digest) in enumerate((("a traversing digest", "sha256:../../evil"), ("a malformed digest", "sha256:ABC"))):
    m = manifest(layers=((L1, None), (L2, None)), digest_of={1: bad_digest})
    o, r, cw = run(f"dig_{i_bad}", tag_body=index([entry(m)]), manifests={sha(m): m}, blobs={bad_digest: b"evil"})
    check(f"C4A-3: {label} is refused by name before any file - nothing aimed or left outside oci/blobs/sha256",
          o.get("ok") is False and "is not sha256:<64 hex>" in (o.get("error") or "") and outside(r, cw) == [],
          f"{o.get('error')} | {outside(r, cw)}")
for label, size in (("a negative declared size", -1), ("a bool declared size", True), ("a declared size past max_blob_bytes", (1 << 24) + 1)):
    m = manifest(layers=((L1, None), (L2, size)))
    o, r, cw = run(f"size_{abs(hash(label)) % 10000}", tag_body=index([entry(m)]), manifests={sha(m): m})
    check(f"C4A-3: {label} is refused by name before its blob is asked for",
          o.get("ok") is False and "outside 0..max_blob_bytes" in (o.get("error") or "")
          and not any(c["path"].endswith(sha(L2)) for c in r.calls), str(o.get("error")))
o, r, cw = run("tamper_gone", blobs={sha(L2): b"tampered"})
check("a layer refused for its bytes leaves no file on the disk",
      not (cw / "oci" / "blobs" / "sha256" / sha(L2).split(":")[1]).exists())

print("\n- C4A-4: the job, the list and the manifest -")
PM = manifest(mtype="application/vnd.in-toto+json")
o, r, cw = run("pm_type", tag_body=index([entry(PM)]), manifests={sha(PM): PM})
check("C4A-4: a platform manifest of another media type (an attestation) is refused by name",
      o.get("ok") is False and "not an image manifest" in (o.get("error") or ""), str(o.get("error")))
PI = json.dumps({"schemaVersion": 2, "mediaType": IDX_T, "manifests": []}).encode()
o, r, cw = run("pm_index", tag_body=index([entry(PI)]), manifests={sha(PI): PI})
check("C4A-4: a platform manifest that is itself an index is refused by name",
      o.get("ok") is False and "not an image manifest" in (o.get("error") or ""), str(o.get("error")))
for label, hosts in (("a subset", [REG, AUTH]), ("a superset", [REG, AUTH, CDN, "x.example"])):
    o, r, cw = run(f"hosts_{label[2:5]}", job={**JOB, "hosts": hosts})
    check(f"C4A-4: a job whose hosts are {label} of its registry, token service and CDN hosts is refused by name, nothing sent",
          o.get("ok") is False and "not exactly its registry" in (o.get("error") or "") and r.calls == [], str(o.get("error")))
for name in ("Letta/Letta", "../x", "letta"):
    o, r, cw = run(f"repo_{abs(hash(name)) % 1000}", job={**JOB, "repo": name})
    check(f"C4A-4: the repository name {name!r} is refused by name, nothing sent",
          o.get("ok") is False and "is not a repository name" in (o.get("error") or "") and r.calls == [], str(o.get("error")))
check("release_tags: the largest ^v?X.Y.Z$ by the numbers, with every tag that names it; none is None",
      FC.release_tags(["1.9.0", "1.10.0", "v1.10.0", "2.0.0-rc1", "latest"]) == ((1, 10, 0), ["1.10.0", "v1.10.0"])
      and FC.release_tags(["latest", "nightly"]) is None)

print("\n- C4A-1: the real wire, tunnel_send, through a loopback tunnel to a local TLS server -")
made = TF.make_test_cert(TMP / "cert", REG)
if made is None:
    print("  SKIP the wire checks: neither cryptography nor openssl is available (not passed)")
else:
    BIG = b"x" * 5000
    routes = {"/ok": (200, [("Content-Type", "application/octet-stream")], b"the body"),
              "/big": (200, [], BIG), "/stream": (200, [("Transfer-Encoding", "chunked")], BIG),
              "/gz": (200, [("Content-Encoding", "gzip")], b"zz"), "/nf": (404, [], b"not found body")}
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    send = FC.tunnel_send(hop.port, ctx=ssl.create_default_context(cafile=str(made[0])), timeout=10)
    wd = TMP / "wire"
    wd.mkdir()

    def attempt(*a, **k):
        try:
            return send(*a, **k), None
        except Exception as e:  # noqa: BLE001 - a refusal is the row's to read
            return None, e
    got, err = attempt("GET", REG, "/ok", {}, max_bytes=100, save_to=wd / "ok.bin")
    check("C4A-1: a 200 body streams to .partial and is renamed - the file is the body, its sha256 and size returned, "
          "no .partial left", err is None and got[0] == 200 and got[2] == b"" and (wd / "ok.bin").read_bytes() == b"the body"
          and got[3] == hashlib.sha256(b"the body").hexdigest() and got[4] == 8 and not (wd / "ok.bin.partial").exists(),
          f"{err} {got}")
    got, err = attempt("GET", REG, "/ok", {}, max_bytes=100)
    check("C4A-1: without save_to the body comes back in memory", err is None and got[2] == b"the body", f"{err} {got}")
    got, err = attempt("GET", REG, "/big", {}, max_bytes=100, save_to=wd / "big.bin")
    check("C4A-1: a Content-Length past max_bytes is refused - no file, no .partial",
          isinstance(err, FC.Refused) and "larger than max_bytes" in str(err) and not (wd / "big.bin").exists()
          and not (wd / "big.bin.partial").exists(), repr(err))
    got, err = attempt("GET", REG, "/big", {}, max_bytes=100, save_to=wd / "no_such_dir" / "big.bin")
    check("C4A-1: a Content-Length past max_bytes is refused before anything is opened on the disk (its directory need "
          "not even exist)", isinstance(err, FC.Refused) and "larger than max_bytes" in str(err), repr(err))
    got, err = attempt("GET", REG, "/stream", {}, max_bytes=100, save_to=wd / "stream.bin")
    check("C4A-1: a chunked body (no Content-Length) that grows past max_bytes is refused as it streams - no file, no "
          ".partial left", isinstance(err, FC.Refused) and "larger than max_bytes" in str(err)
          and not (wd / "stream.bin").exists() and not (wd / "stream.bin.partial").exists(), repr(err))
    got, err = attempt("GET", REG, "/gz", {}, max_bytes=100, save_to=wd / "gz.bin")
    check("C4A-1: an encoded body is refused by name, nothing saved", isinstance(err, FC.Refused) and "encoded" in str(err)
          and not (wd / "gz.bin").exists(), repr(err))
    got, err = attempt("HEAD", REG, "/ok", {}, max_bytes=100)
    got2, err2 = attempt("GET", REG, "/nf", {}, max_bytes=100, save_to=wd / "nf.bin")
    check("C4A-1: a HEAD and a non-200 come back with their status and no body, nothing saved",
          err is None and got[0] == 200 and got[2] == b"" and got[3] is None and err2 is None and got2[0] == 404
          and got2[2] == b"" and not (wd / "nf.bin").exists(), f"{got} {got2}")
    srv.close(), hop.close()

check("C4A-8: oci_job raised on no row - every failure came back as ok False with an error", RAISED == [], str(RAISED))
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch oci: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
