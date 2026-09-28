#!/usr/bin/env python3
"""PREREG-V3 A8 B-NLP NLP-2 (the auditor's rule, 07:19, and Q-NLP-1 = O-a): research/v3/fetch_child.py's gh_model job -
spawns NO child. The job runs in process against a fake GitHub (an injected wire):

* the model's version: compatibility.json (raw.githubusercontent.com, explosion/spacy-models) under the LOCKED spaCy's
  major.minor, the newest listed version by its numbers - the list's first entry recorded beside it; no such minor,
  no such model, or no X.Y.Z version is refused by name;
* the release: api.github.com's release by its tag <model>-<version> - exactly one asset named
  <model>-<version>-py3-none-any.whl, at exactly github.com/<repo>/releases/download/<tag>/<name>; its "digest" is the
  sha256 the stream is checked against (a digest that is not sha256:<64 hex> is refused; none is recorded as TLS-only
  trust, a declared limit);
* the file: github.com answers it or redirects it once, to a declared CDN host only (objects.githubusercontent.com,
  release-assets.githubusercontent.com), over https on 443 with no userinfo and no Authorization; a second redirect is
  refused; the CDN host is recorded, the signed query never; the bytes are the digest's and the asset's size, else
  the file is removed and the job refused;
* the job's hosts are exactly the declared five; the job never raises - a refusal or a network error is its summary.

    python tests/_test_v3_fetch_gh_model.py
"""
from __future__ import annotations

import atexit
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


FC = _load("v3_fetch_child_gh_t", ROOT / "research" / "v3" / "fetch_child.py")
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
    """A row's condition; a raise FAILs that row by name and the closing row lists it."""
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


RAW, API, WEB = "raw.githubusercontent.com", "api.github.com", "github.com"
CDN1, CDN2 = "objects.githubusercontent.com", "release-assets.githubusercontent.com"
REPO, MODEL = "explosion/spacy-models", "en_core_web_sm"
COMPAT = "/explosion/spacy-models/master/compatibility.json"
WHL = b"PK\x03\x04 a fake model wheel " * 200
V = "3.8.10"
TAG = f"{MODEL}-{V}"
NAME = f"{TAG}-py3-none-any.whl"
SIG = "X-Amz-Signature=deadbeef&X-Amz-Credential=secret"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class GitHub:
    """A fake GitHub. Knobs: compat (the document), release (the API's JSON), api_status, web (github.com's answer:
    "redirect" | "direct" | an int status), location (the redirect target), double_redirect, served (the CDN's bytes)."""

    def __init__(self, **k):
        self.k = k
        self.calls: list = []

    def send(self, method, host, path, headers, *, max_bytes, save_to=None):
        self.calls.append({"method": method, "host": host, "path": path, "headers": dict(headers),
                           "save_to": str(save_to) if save_to is not None else None})
        k, bare = self.k, path.split("?", 1)[0]

        def ok_(body, hdrs=None):
            if len(body) > max_bytes:
                raise FC.Refused("the body is larger than max_bytes")
            if save_to is not None:
                save_to.parent.mkdir(parents=True, exist_ok=True)
                save_to.write_bytes(body)
                return 200, dict(hdrs or {}), b"", sha(body), len(body)
            return 200, dict(hdrs or {}), body, sha(body), len(body)
        if host == RAW and bare == COMPAT:
            return ok_(json.dumps(k.get("compat", {"spacy": {"3.8": {MODEL: ["3.8.0", V, "3.8.2", "4.0.0.dev1"],
                                                                      "de_core_news_sm": ["3.8.0"]},
                                                             "3.7": {MODEL: ["3.7.1"]}}})).encode())
        if host == API and bare == f"/repos/{REPO}/releases/tags/{TAG}":
            st = k.get("api_status", 200)
            if st != 200:
                return st, {}, b"", None, 0
            rel = k.get("release", {"id": 42, "tag_name": TAG, "assets": [
                {"name": NAME, "size": len(WHL), "digest": f"sha256:{sha(WHL)}",
                 "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{NAME}"},
                {"name": f"{TAG}.tar.gz", "size": 5, "digest": "sha256:" + "0" * 64,
                 "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{TAG}.tar.gz"}]})
            return ok_(json.dumps(rel).encode())
        if host == WEB and bare == f"/{REPO}/releases/download/{TAG}/{NAME}":
            w = k.get("web", "redirect")
            if w == "direct":
                return ok_(k.get("served", WHL))
            if isinstance(w, int):
                return w, {}, b"", None, 0
            return 302, {"location": k.get("location", f"https://{CDN2}/github-production-release-asset/1/2?{SIG}")}, b"", None, 0
        if host in (CDN1, CDN2):
            if k.get("double_redirect"):
                return 302, {"location": f"https://{CDN1}/again"}, b"", None, 0
            return ok_(k.get("served", WHL))
        return 404, {}, b"", None, 0


JOB = {"kind": "gh_model", "model": MODEL, "spacy": "3.8.7", "repo": REPO, "raw_host": RAW, "api_host": API,
       "web_host": WEB, "compat_path": COMPAT, "cdn_hosts": [CDN1, CDN2], "hosts": [RAW, API, WEB, CDN1, CDN2],
       "max_meta_bytes": 1 << 20, "max_asset_bytes": 1 << 26}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_gh_"))
atexit.register(shutil.rmtree, TMP, True)


def run(tag, job=None, **knobs):
    cwd = TMP / tag
    cwd.mkdir()
    gh = GitHub(**knobs)
    try:
        out = FC.gh_model_job(copy.deepcopy(job or JOB), send=gh.send, cwd=cwd)
    except Exception as e:  # noqa: BLE001 - the job promises never to raise
        RAISED.append(f"{tag}: {type(e).__name__}: {e}")
        out = {"ok": None, "error": f"RAISED {type(e).__name__}: {e}"}
    return out, gh, cwd


print("- a whole model -")
o, g, cw = run("ok")
check("the job passes", ok(lambda: o.get("ok") is True and o.get("error") is None), str(o.get("error")))
check("the version: compatibility.json under spaCy 3.8 (the locked 3.8.7's major.minor), the newest X.Y.Z by its "
      "numbers (3.8.10, not the list's first 3.8.0, never a dev release) - the first recorded beside it",
      ok(lambda: o["version"] == V and o["compat"] == {"spacy_minor": "3.8", "listed": ["3.8.0", V, "3.8.2", "4.0.0.dev1"],
                                                      "first": "3.8.0", "newest": V}), str(o.get("compat")))
check("the release: the one asset named <model>-<version>-py3-none-any.whl, its digest the sha256, not TLS-only",
      ok(lambda: o["asset"] == {"name": NAME, "size": len(WHL), "digest": f"sha256:{sha(WHL)}",
                                "path": f"/{REPO}/releases/download/{TAG}/{NAME}"} and o["tls_only"] is False
         and o["release"] == {"id": 42, "tag": TAG}), str(o.get("asset")))
dest = cw / "model" / NAME
check("the file: saved under model/<name>, the digest's bytes and the asset's size",
      ok(lambda: dest.read_bytes() == WHL and o["file"] == {"sha256": sha(WHL), "bytes": len(WHL), "path": f"model/{NAME}"}),
      str(o.get("file")))
check("github.com redirected once, to a declared CDN host - recorded, its signed query never; no request carried an "
      "Authorization", ok(lambda: o["cdn_host"] == CDN2 and not any("Signature" in json.dumps(r) for r in o["requests"])
                         and "Signature" not in json.dumps(o) and all("Authorization" not in c["headers"] for c in g.calls)),
      str(o.get("requests")))
check("the requests in order: compatibility.json, the release, github.com, the CDN",
      ok(lambda: [(r["host"], r["status"]) for r in o["requests"]] == [(RAW, 200), (API, 200), (WEB, 302), (CDN2, 200)]),
      str(o.get("requests")))
o, g, cw = run("direct", web="direct")
check("github.com answering the file itself is taken (no redirect, cdn_host None) - one redirect is the most, never "
      "required", ok(lambda: o.get("ok") is True and o.get("cdn_host") is None), str(o.get("error")))
REL_NODIG = {"id": 7, "tag_name": TAG, "assets": [{"name": NAME, "size": len(WHL),
                                                   "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{NAME}"}]}
o, g, cw = run("nodigest", release=REL_NODIG)
check("no digest from the API: taken as TLS-only trust (a declared limit), the stream's sha256 recorded",
      ok(lambda: o.get("ok") is True and o["tls_only"] is True and o["asset"]["digest"] is None
         and o["file"]["sha256"] == sha(WHL)), str(o.get("error")))

o, g, cw = run("spacy37", job={**JOB, "spacy": "3.7.5"})
check("the locked spaCy's OWN major.minor keys the table: 3.7.5 reads spaCy 3.7's list (its newest 3.7.1)",
      ok(lambda: o["compat"]["spacy_minor"] == "3.7" and o["compat"]["newest"] == "3.7.1"), str(o.get("compat")))
o, g, cw = run("nodigest_short", release=REL_NODIG, served=WHL[:-10])
check("with no digest (TLS-only) the asset's size still binds: a short file is refused by name and not kept",
      ok(lambda: o.get("ok") is False and "not the digest's bytes or the asset's size" in (o.get("error") or "")
         and not (cw / "model" / NAME).exists()), str(o.get("error")))

print("\n- refusals by name -")
cases = [
    ("hosts", {"job": {**JOB, "hosts": [RAW, API, WEB, CDN2]}}, "not exactly its declared hosts"),
    ("hosts_extra", {"job": {**JOB, "hosts": [RAW, API, WEB, CDN1, CDN2, "evil.example"]}}, "not exactly its declared hosts"),
    ("spacy_ver", {"job": {**JOB, "spacy": "3.8"}}, "not an X.Y.Z version"),
    ("model_name", {"job": {**JOB, "model": "../x"}}, "not a model name"),
    ("no_minor", {"compat": {"spacy": {"3.7": {MODEL: ["3.7.1"]}}}}, "lists no spaCy 3.8"),
    ("no_model", {"compat": {"spacy": {"3.8": {"de_core_news_sm": ["3.8.0"]}}}}, f"lists no {MODEL} for spaCy 3.8"),
    ("no_xyz", {"compat": {"spacy": {"3.8": {MODEL: ["3.8.0.dev1", "latest"]}}}}, "no X.Y.Z version"),
    ("api_404", {"api_status": 404}, "the release API answered 404"),
    ("no_asset", {"release": {"id": 1, "tag_name": TAG, "assets": []}}, "0 assets named"),
    ("two_assets", {"release": {"id": 1, "tag_name": TAG, "assets": [
        {"name": NAME, "size": 1, "digest": f"sha256:{sha(WHL)}", "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{NAME}"}] * 2}},
     "2 assets named"),
    ("url_host", {"release": {"id": 1, "tag_name": TAG, "assets": [
        {"name": NAME, "size": len(WHL), "digest": f"sha256:{sha(WHL)}", "browser_download_url": f"https://evil.example/{REPO}/releases/download/{TAG}/{NAME}"}]}},
     "not github.com's own download path"),
    ("url_path", {"release": {"id": 1, "tag_name": TAG, "assets": [
        {"name": NAME, "size": len(WHL), "digest": f"sha256:{sha(WHL)}", "browser_download_url": f"https://{WEB}/other/repo/releases/download/{TAG}/{NAME}"}]}},
     "not github.com's own download path"),
    ("bad_digest", {"release": {"id": 1, "tag_name": TAG, "assets": [
        {"name": NAME, "size": len(WHL), "digest": "md5:abc", "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{NAME}"}]}},
     "not sha256:<64 hex>"),
    ("bad_size", {"release": {"id": 1, "tag_name": TAG, "assets": [
        {"name": NAME, "size": -1, "digest": f"sha256:{sha(WHL)}", "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{NAME}"}]}},
     "a size -1 outside"),
    ("cdn_undeclared", {"location": f"https://evil.example/x?{SIG}"}, "an undeclared host"),
    ("cdn_http", {"location": f"http://{CDN2}/x"}, "not https"),
    ("cdn_port", {"location": f"https://{CDN2}:8443/x"}, "port 8443"),
    ("cdn_user", {"location": f"https://u:p@{CDN2}/x"}, "userinfo"),
    ("double", {"double_redirect": True}, "redirects a second time"),
    ("web_500", {"web": 500}, "github.com answered 500"),
    ("tampered", {"served": WHL[:-1] + b"!"}, "not the digest's bytes or the asset's size"),
    ("short", {"served": WHL[:-10]}, "not the digest's bytes or the asset's size"),
]
for tag, knobs, want in cases:
    job = knobs.pop("job", None)
    o, g, cw = run(tag, job=job, **knobs)
    check(f"refused by name: {tag}", ok(lambda: o.get("ok") is False and want in (o.get("error") or "")), str(o.get("error")))
o, g, cw = run("tampered_file", served=WHL[:-1] + b"!")
check("a refused file is not left on the disk", ok(lambda: not (cw / "model" / NAME).exists()), str(list(cw.rglob("*"))))


def boom(*a, **k):
    raise OSError("connection reset")


cw = TMP / "boom"
cw.mkdir()
try:
    ob = FC.gh_model_job(copy.deepcopy(JOB), send=boom, cwd=cw)
except Exception as e:  # noqa: BLE001
    RAISED.append(f"boom: {type(e).__name__}: {e}")
    ob = {}
check("a network error is the job's summary (ok False, the error named), never a raise",
      ok(lambda: ob.get("ok") is False and "OSError" in (ob.get("error") or "")), str(ob))
check("no row's condition, and no job, raised", RAISED == [], str(RAISED))
print(f"\nv3 fetch gh_model: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
