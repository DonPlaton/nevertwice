#!/usr/bin/env python3
"""PREREG-V3 A8 C4 (the auditor's Q-C4-1..3): research/v3/fetch_child.py's OCI job, in process against a fake registry
(no child, no network - the wire is injected):

* Q-C4-1: every page of the tags list is read; the newest release is the largest ^v?X.Y.Z$ by the numbers (a pre-
  release, "latest" and "nightly" never count); X.Y.Z and vX.Y.Z of one version must name the same index, else refused
  by name; the index's ONE linux/amd64 manifest (no variant) is taken - none or two is refused, naming attempt 2 as
  Q-A8-4's; the digest behind "latest" is recorded as information, never chosen;
* Q-C4-2: the token is asked for exactly repository:<repo>:pull, sent only to the registry, never recorded (the
  summary says <redacted> and its expiry) - and never sent to the CDN host a blob redirects to;
* Q-C4-3: a blob redirect is followed once, only to a declared CDN host, without Authorization; the signed URL's
  query is recorded nowhere; an undeclared host is refused by name with the host recorded; a second redirect is
  refused;
* every blob on the disk is its digest's bytes and its manifest size; a manifest that is not its digest's bytes, an
  index that is not an index, a next page off the registry, a rate limit, and a job whose hosts are not exactly its
  registry, token service and CDN hosts are each refused by name.

    python tests/_test_v3_fetch_oci.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _env_guard  # noqa: F401,E402  hermetic like every suite


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


CFG = json.dumps({"architecture": "amd64", "os": "linux", "config": {"Env": ["A=1"]},
                  "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "d" * 64]}}).encode()
L1, L2 = b"layer one bytes", b"layer two, the larger one" * 10
MAN = json.dumps({"schemaVersion": 2, "mediaType": MAN_T, "config": {"mediaType": "x", "digest": sha(CFG), "size": len(CFG)},
                  "layers": [{"mediaType": "l", "digest": sha(L1), "size": len(L1)},
                             {"mediaType": "l", "digest": sha(L2), "size": len(L2)}]}).encode()
ARM = json.dumps({"schemaVersion": 2, "mediaType": MAN_T, "config": {}, "layers": []}).encode()


def index(manifests):
    return json.dumps({"schemaVersion": 2, "mediaType": IDX_T, "manifests": manifests}).encode()


IDX = index([{"mediaType": MAN_T, "digest": sha(ARM), "size": len(ARM), "platform": {"os": "linux", "architecture": "arm64"}},
             {"mediaType": MAN_T, "digest": sha(MAN), "size": len(MAN), "platform": {"os": "linux", "architecture": "amd64"}},
             {"mediaType": MAN_T, "digest": "sha256:" + "e" * 64, "size": 1,
              "platform": {"os": "linux", "architecture": "amd64", "variant": "v3"}}])
LATEST = index([{"mediaType": MAN_T, "digest": "sha256:" + "f" * 64, "size": 1, "platform": {"os": "linux", "architecture": "amd64"}}])


class Registry:
    """A fake Docker Hub: token, two pages of tags, indexes, manifests, blobs (layers redirected to the CDN)."""

    def __init__(self, **knobs):
        self.k = knobs
        self.calls: list = []

    def send(self, method, host, path, headers, *, max_bytes, save_to=None):
        self.calls.append({"method": method, "host": host, "path": path, "headers": dict(headers)})
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
                return ok(LATEST, {"docker-content-digest": sha(LATEST)})
            if ref in ("0.10.0", "v0.10.0"):
                body = k.get("index", IDX)
                if ref == "v0.10.0" and k.get("v_differs"):
                    body = index([])
                hd = k.get("header_digest", sha(body))
                return ok(body, {"docker-content-digest": hd, "content-type": json.loads(body).get("mediaType", "")})
            if ref == sha(MAN):
                return ok(k.get("man_bytes", MAN))
            return 404, {}, b"", None, 0
        if host == REG and bare.startswith(f"/v2/{REPO}/blobs/"):
            d = bare.rsplit("/", 1)[1]
            if d == sha(CFG):
                return ok(CFG)
            target = k.get("redirect_host", CDN)
            return 307, {"location": f"https://{target}/registry-v2/docker/blobs/{d}/data?{SIG}"}, b"", None, 0
        if host in (CDN, "evil.cdn.example"):
            if k.get("double_redirect"):
                return 307, {"location": f"https://{CDN}/again"}, b"", None, 0
            d = bare.split("/blobs/", 1)[1].split("/", 1)[0]
            body = {sha(L1): L1, sha(L2): L2}[d]
            return ok(k.get("wrong_blob", body) if d == sha(L2) else body)
        return 404, {}, b"", None, 0


JOB = {"kind": "oci", "repo": REPO, "registry": REG, "auth": AUTH, "service": "registry.docker.io", "cdn_hosts": [CDN],
       "hosts": [REG, AUTH, CDN], "platform": {"os": "linux", "architecture": "amd64"}, "max_meta_bytes": 1 << 20,
       "max_blob_bytes": 1 << 24}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_oci_"))


def run(tag, job=None, **knobs):
    cwd = TMP / tag
    cwd.mkdir()
    reg = Registry(**knobs)
    out = FC.oci_job(copy.deepcopy(job or JOB), send=reg.send, cwd=cwd)
    return out, reg, cwd


print("- the whole image, by the rules -")
out, reg, cwd = run("ok")
check("Q-C4-1: every page of the tags read; the newest release by the numbers is 0.10.0 (0.11.0-rc1, latest, nightly, "
      "0.9.9 and 0.2.99 are not), named by both its tags, one index", out.get("ok") and out.get("version") == "0.10.0"
      and out.get("tag_variants") == ["0.10.0", "v0.10.0"] and out.get("tag_pages") == 2 and out.get("tags_seen") == 7
      and out.get("index_digest") == sha(IDX), str({k: out.get(k) for k in ("ok", "error", "version", "tag_variants", "tag_pages")}))
check("Q-C4-1: the index's ONE linux/amd64 manifest without a variant is taken (not arm64, not amd64/v3)",
      out.get("manifest_digest") == sha(MAN) and out.get("manifest_media_type") == MAN_T, str(out.get("manifest_digest")))
blobs = cwd / "oci" / "blobs" / "sha256"
check("every blob on the disk is its digest's bytes - the manifest, the config and both layers - with the sizes recorded",
      all((blobs / d.split(":")[1]).read_bytes() == b for d, b in ((sha(MAN), MAN), (sha(CFG), CFG), (sha(L1), L1), (sha(L2), L2)))
      and out.get("config") == {"digest": sha(CFG), "size": len(CFG)}
      and [x["digest"] for x in out.get("layers", [])] == [sha(L1), sha(L2)], str(sorted(p.name[:8] for p in blobs.iterdir()))
      if blobs.is_dir() else "no blobs")
check("Q-C4-1: the digest behind 'latest' is recorded as information - and it is not the one chosen",
      out.get("latest_digest") == sha(LATEST) and out.get("latest_digest") != out.get("index_digest"))
tok_calls = [c for c in reg.calls if c["host"] == AUTH]
check("Q-C4-2: the token is asked for exactly repository:letta/letta:pull, once, without credentials",
      len(tok_calls) == 1 and __import__("urllib.parse").parse.parse_qs(tok_calls[0]["path"].split("?", 1)[1])
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
cdn_rec = [r for r in out.get("requests", []) if r["host"] == CDN]
check("Q-C4-3: each layer followed exactly one redirect to the declared CDN host; its record keeps host and path only",
      len(cdn_rec) == 2 and all("?" not in r["path"] and r["status"] == 200 for r in cdn_rec), str(cdn_rec))

print("\n- refusals by name -")
cases = [
    ("v_differs", {"v_differs": True}, "resolve to different indexes", "Q-C4-1: X.Y.Z and vX.Y.Z naming different indexes"),
    ("no_amd64", {"index": index([{"mediaType": MAN_T, "digest": sha(ARM), "size": 1, "platform": {"os": "linux", "architecture": "arm64"}}])},
     "attempt 2 is Q-A8-4's", "Q-C4-1: an index with no linux/amd64 manifest (attempt 2 named)"),
    ("two_amd64", {"index": index([{"mediaType": MAN_T, "digest": sha(MAN), "size": 1, "platform": {"os": "linux", "architecture": "amd64"}},
                                   {"mediaType": MAN_T, "digest": sha(ARM), "size": 1, "platform": {"os": "linux", "architecture": "amd64"}}])},
     "holds 2 linux/amd64", "Q-C4-1: an index with two linux/amd64 manifests"),
    ("not_index", {"index": MAN}, "names no image index", "an index that is not an index"),
    ("header_digest", {"header_digest": "sha256:" + "0" * 64}, "is not its bytes' digest", "a registry digest that is not the bytes'"),
    ("man_bytes", {"man_bytes": MAN + b" "}, "not its digest's bytes", "a platform manifest that is not its digest's bytes"),
    ("wrong_blob", {"wrong_blob": b"tampered"}, "is not its digest's bytes", "a layer whose bytes are not its digest"),
    ("undeclared_cdn", {"redirect_host": "evil.cdn.example"}, "undeclared host 'evil.cdn.example'", "Q-C4-3: a redirect to an undeclared host"),
    ("double", {"double_redirect": True}, "redirects a second time", "Q-C4-3: a second redirect"),
    ("link_off", {"link": '<https://evil.example/v2/letta/letta/tags/list?last=x>; rel="next"'}, "leaves the registry's list",
     "a next page off the registry"),
    ("rate", {"rate_limit": True}, "rate-limited", "a rate limit"),
]
for tag, knobs, want, label in cases:
    o, r, cw = run(tag, **knobs)
    check(f"refused by name: {label}", o.get("ok") is False and want in (o.get("error") or ""), str(o.get("error")))
o, r, cw = run("undeclared_rec", redirect_host="evil.cdn.example")
check("Q-C4-3: the undeclared host is recorded, and nothing was sent to it", o.get("refused_redirect_host") == "evil.cdn.example"
      and not any(c["host"] == "evil.cdn.example" for c in r.calls), str(o.get("refused_redirect_host")))
o, r, cw = run("wrong_blob_gone", wrong_blob=b"tampered")
check("a layer refused for its bytes leaves no file on the disk", not (cw / "oci" / "blobs" / "sha256" / sha(L2).split(":")[1]).exists())
o, r, cw = run("hosts", job={**JOB, "hosts": [REG, AUTH]})
check("refused by name: a job whose hosts are not exactly its registry, token service and CDN hosts",
      o.get("ok") is False and "not exactly its registry" in (o.get("error") or "") and r.calls == [], str(o.get("error")))
check("release_tags: the largest ^v?X.Y.Z$ by the numbers, with every tag that names it; none is None",
      FC.release_tags(["1.9.0", "1.10.0", "v1.10.0", "2.0.0-rc1", "latest"]) == ((1, 10, 0), ["1.10.0", "v1.10.0"])
      and FC.release_tags(["latest", "nightly"]) is None)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch oci: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
