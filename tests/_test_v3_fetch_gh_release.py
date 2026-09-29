#!/usr/bin/env python3
"""PREREG-V3 A8, T32 (PREREG-V3-AMENDMENTS.md A3; the auditor's Q-A8-10): research/v3/fetch_child.py's gh_release job -
spawns NO child. The job runs in process against a fake GitHub (an injected wire):

* the expectation comes with the job, fixed before the window (bin_install reads it from the a7-docs record): the
  repository, the tag, the tag's commit, and each asset's name, size and sha256 digest - a job whose expectation is not
  that shape (a name with a slash, a name twice, no asset, a digest that is not sha256:<64 hex>, a size outside
  1..max_asset_bytes, a commit that is not 40 hex, a tag or repository name that is not one) is refused before any
  request;
* the tag: api.github.com's commits/<tag> must name the expected commit; the release by its tag must be that tag, not a
  draft, not a prerelease, and hold exactly one asset of each expected name, at exactly
  github.com/<repo>/releases/download/<tag>/<name>, with exactly the expected size and digest - the live answer and the
  record must agree, neither is taken alone;
* each file: github.com answers it or redirects it once, to a declared CDN host only (objects.githubusercontent.com,
  release-assets.githubusercontent.com), over https on 443 with no userinfo and no Authorization; a second redirect is
  refused; the CDN host is recorded, the signed query never; the bytes are the digest's and the size's, else the file
  is removed and the job refused; no other asset of the release is requested;
* the job's hosts are exactly the declared four; the job never raises - a refusal or a network error is its summary.

    python tests/_test_v3_fetch_gh_release.py
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


FC = _load("v3_fetch_child_ghr_t", ROOT / "research" / "v3" / "fetch_child.py")
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


API, WEB = "api.github.com", "github.com"
CDN1, CDN2 = "objects.githubusercontent.com", "release-assets.githubusercontent.com"
REPO, TAG = "supermemoryai/supermemory", "server-v0.0.8"
COMMIT = "5d2b5855fe492a3682a1cde4a255e2db0c4db595"
BIN = b"MZ\x90\x00 a fake server binary " * 400
BIN_NAME = "supermemory-server-windows-x64.exe"
SUMS_NAME = BIN_NAME + ".sha256"
SIG = "X-Amz-Signature=deadbeef&X-Amz-Credential=secret"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


SUMS = f"{sha(BIN)}  {BIN_NAME}\n".encode()
OTHER = b"\x7fELF a linux binary"


def asset(name: str, body: bytes, **over) -> dict:
    a = {"name": name, "size": len(body), "digest": f"sha256:{sha(body)}",
         "browser_download_url": f"https://{WEB}/{REPO}/releases/download/{TAG}/{name}"}
    a.update(over)
    return a


REL = {"id": 99, "tag_name": TAG, "draft": False, "prerelease": False, "published_at": "2026-08-17T18:39:22Z",
       "assets": [asset("install.sh", b"#!/bin/sh\n"), asset("supermemory-server-linux-x64", OTHER),
                  asset(BIN_NAME, BIN), asset(SUMS_NAME, SUMS)]}
BODIES = {BIN_NAME: BIN, SUMS_NAME: SUMS, "supermemory-server-linux-x64": OTHER, "install.sh": b"#!/bin/sh\n"}


class GitHub:
    """A fake GitHub. Knobs: commit (the SHA commits/<tag> answers), commit_status, release (the API's JSON), api_status,
    web (github.com's answer: "redirect" | "direct" | an int status), location (the redirect target), double_redirect,
    served ({name: bytes} the CDN serves instead)."""

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
        if host == API and bare == f"/repos/{REPO}/commits/{TAG}":
            st = k.get("commit_status", 200)
            if st != 200:
                return st, {}, b"", None, 0
            return ok_(json.dumps({"sha": k.get("commit", COMMIT), "commit": {"message": "release"}}).encode())
        if host == API and bare == f"/repos/{REPO}/releases/tags/{TAG}":
            st = k.get("api_status", 200)
            if st != 200:
                return st, {}, b"", None, 0
            return ok_(json.dumps(k.get("release", REL)).encode())
        pre = f"/{REPO}/releases/download/{TAG}/"
        if host == WEB and bare.startswith(pre):
            name = bare[len(pre):]
            w = k.get("web", "redirect")
            if w == "direct":
                return ok_((k.get("served") or {}).get(name, BODIES.get(name, b"")))
            if isinstance(w, int):
                return w, {}, b"", None, 0
            loc = k.get("location", f"https://{CDN2}/github-production-release-asset/1/{name}?{SIG}")
            return 302, {"location": loc}, b"", None, 0
        if host in (CDN1, CDN2):
            if k.get("double_redirect"):
                return 302, {"location": f"https://{CDN1}/again"}, b"", None, 0
            name = bare.rsplit("/", 1)[-1]
            return ok_((k.get("served") or {}).get(name, BODIES.get(name, b"")))
        return 404, {}, b"", None, 0


EXPECT = [{"name": BIN_NAME, "size": len(BIN), "digest": f"sha256:{sha(BIN)}"},
          {"name": SUMS_NAME, "size": len(SUMS), "digest": f"sha256:{sha(SUMS)}"}]
JOB = {"kind": "gh_release", "repo": REPO, "tag": TAG, "commit": COMMIT, "api_host": API, "web_host": WEB,
       "cdn_hosts": [CDN1, CDN2], "hosts": [API, WEB, CDN1, CDN2], "assets": EXPECT,
       "max_meta_bytes": 1 << 20, "max_asset_bytes": 1 << 26}
TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_ghr_"))
atexit.register(shutil.rmtree, TMP, True)


def run(tag, job=None, **knobs):
    cwd = TMP / tag
    cwd.mkdir()
    gh = GitHub(**knobs)
    fn = getattr(FC, "gh_release_job", None)
    try:
        if fn is None:
            raise AttributeError("fetch_child has no gh_release_job")
        out = fn(copy.deepcopy(job or JOB), send=gh.send, cwd=cwd)
    except Exception as e:  # noqa: BLE001 - the job promises never to raise
        RAISED.append(f"{tag}: {type(e).__name__}: {e}")
        out = {"ok": None, "error": f"RAISED {type(e).__name__}: {e}"}
    return out, gh, cwd


print("- a whole release -")
o, g, cw = run("ok")
check("GR-1: the job passes", ok(lambda: o.get("ok") is True and o.get("error") is None), str(o.get("error")))
check("GR-2: the tag's commit is the expected one, read from commits/<tag>, and recorded",
      ok(lambda: o["commit"] == COMMIT and (API, f"/repos/{REPO}/commits/{TAG}") in [(c["host"], c["path"]) for c in g.calls]),
      str(o.get("commit")))
check("GR-3: the release: its id, its tag, not a draft, not a prerelease, its publication time",
      ok(lambda: o["release"] == {"id": 99, "tag": TAG, "draft": False, "prerelease": False,
                                  "published_at": "2026-08-17T18:39:22Z"}), str(o.get("release")))
check("GR-4: each expected asset saved under release/<name> - the digest's bytes, the expected size - in the job's order",
      ok(lambda: [a["name"] for a in o["assets"]] == [BIN_NAME, SUMS_NAME]
         and (cw / "release" / BIN_NAME).read_bytes() == BIN and (cw / "release" / SUMS_NAME).read_bytes() == SUMS
         and o["assets"][0]["file"] == {"sha256": sha(BIN), "bytes": len(BIN), "path": f"release/{BIN_NAME}"}
         and o["assets"][1]["file"] == {"sha256": sha(SUMS), "bytes": len(SUMS), "path": f"release/{SUMS_NAME}"}
         and o["assets"][0]["size"] == len(BIN) and o["assets"][0]["digest"] == f"sha256:{sha(BIN)}"), str(o.get("assets")))
check("GR-5: github.com redirected each once, to a declared CDN host - recorded, its signed query never; no request "
      "carried an Authorization",
      ok(lambda: [a["cdn_host"] for a in o["assets"]] == [CDN2, CDN2] and "Signature" not in json.dumps(o)
         and all("Authorization" not in c["headers"] for c in g.calls)), str(o.get("requests")))
check("GR-5b: a CDN request is recorded by its host and path and the sha256 of its signed query - the query itself "
      "never (the auditor, 2026-09-30 00:43); the other requests carry no query hash",
      ok(lambda: [r.get("query_sha256") for r in o["requests"] if r["host"] == CDN2]
         == [hashlib.sha256(SIG.encode()).hexdigest()] * 2
         and all(r["path"] == f"/github-production-release-asset/1/{n}" for r, n in
                 zip([r for r in o["requests"] if r["host"] == CDN2], [BIN_NAME, SUMS_NAME]))
         and all("query_sha256" not in r for r in o["requests"] if r["host"] != CDN2)), str(o.get("requests")))
check("GR-6: the requests in order: the commit, the release, then github.com and the CDN for each asset",
      ok(lambda: [(r["host"], r["status"]) for r in o["requests"]]
         == [(API, 200), (API, 200), (WEB, 302), (CDN2, 200), (WEB, 302), (CDN2, 200)]), str(o.get("requests")))
check("GR-7: no other asset of the release is requested (the linux binary, install.sh)",
      ok(lambda: not any(("linux" in c["path"] or "install.sh" in c["path"]) for c in g.calls)
         and sorted(p.name for p in (cw / "release").iterdir()) == sorted([BIN_NAME, SUMS_NAME])), str(g.calls))
o, g, cw = run("direct", web="direct")
check("GR-8: github.com answering the file itself is taken (no redirect, cdn_host None) - one redirect is the most, "
      "never required", ok(lambda: o.get("ok") is True and [a["cdn_host"] for a in o["assets"]] == [None, None]),
      str(o.get("error")))

print("\n- refusals by name -")


def rel_with(name, **over):
    r = copy.deepcopy(REL)
    for a in r["assets"]:
        if a["name"] == name:
            a.update(over)
    return r


def exp_with(i, **over):
    e = copy.deepcopy(EXPECT)
    e[i].update(over)
    return e


cases = [   # (tag, knobs, the error's words, refused before any request)
    ("hosts_short", {"job": {**JOB, "hosts": [API, WEB, CDN2]}}, "not exactly its declared hosts", True),
    ("hosts_extra", {"job": {**JOB, "hosts": [API, WEB, CDN1, CDN2, "evil.example"]}}, "not exactly its declared hosts", True),
    ("hosts_twice", {"job": {**JOB, "hosts": [API, WEB, CDN1, CDN2, API]}}, "not exactly its declared hosts", True),
    ("repo_dots", {"job": {**JOB, "repo": "../x"}}, "not a GitHub repository name", True),
    ("repo_deep", {"job": {**JOB, "repo": "a/b/c"}}, "not a GitHub repository name", True),
    ("tag_slash", {"job": {**JOB, "tag": "server/v0.0.8"}}, "not a tag name", True),
    ("tag_dots", {"job": {**JOB, "tag": ".."}}, "not a tag name", True),
    ("tag_dots_inside", {"job": {**JOB, "tag": "server-v0..8"}}, "not a tag name", True),    # git refuses ".." in a ref
    ("commit_short", {"job": {**JOB, "commit": COMMIT[:12]}}, "not a 40-hex commit", True),
    ("exp_none", {"job": {**JOB, "assets": []}}, "expects no asset", True),
    ("exp_twice", {"job": {**JOB, "assets": [EXPECT[0], EXPECT[0]]}}, "expects an asset name twice", True),
    ("exp_name", {"job": {**JOB, "assets": exp_with(0, name="../evil.exe")}}, "not an asset name", True),
    ("exp_name_dots_inside", {"job": {**JOB, "assets": exp_with(0, name="server..exe")}}, "not an asset name", True),
    ("exp_digest", {"job": {**JOB, "assets": exp_with(0, digest=None)}}, "not sha256:<64 hex>", True),
    ("exp_md5", {"job": {**JOB, "assets": exp_with(1, digest="md5:abc")}}, "not sha256:<64 hex>", True),
    ("exp_size", {"job": {**JOB, "assets": exp_with(0, size=(1 << 26) + 1)}}, "outside 1..max_asset_bytes", True),
    ("exp_size0", {"job": {**JOB, "assets": exp_with(1, size=0)}}, "outside 1..max_asset_bytes", True),
    ("commit_404", {"commit_status": 404}, "the commit API answered 404", False),
    ("commit_moved", {"commit": "f" * 40}, f"names commit {'f' * 40}, not the expected {COMMIT}", False),
    ("api_404", {"api_status": 404}, "the release API answered 404", False),
    ("rel_tag", {"release": {**REL, "tag_name": "server-v0.0.9"}}, "the release names the tag 'server-v0.0.9'", False),
    ("draft", {"release": {**REL, "draft": True}}, "is a draft", False),
    ("prerelease", {"release": {**REL, "prerelease": True}}, "is a prerelease", False),
    ("no_asset", {"release": {**REL, "assets": [a for a in REL["assets"] if a["name"] != SUMS_NAME]}},
     f"holds 0 assets named {SUMS_NAME}", False),
    ("two_assets", {"release": {**REL, "assets": REL["assets"] + [asset(BIN_NAME, BIN)]}}, f"holds 2 assets named {BIN_NAME}", False),
    ("url_query", {"release": rel_with(BIN_NAME, browser_download_url=f"https://{WEB}/{REPO}/releases/download/{TAG}/{BIN_NAME}?x=1")},
     "not github.com's own download path", False),
    ("url_host", {"release": rel_with(BIN_NAME, browser_download_url=f"https://evil.example/{REPO}/releases/download/{TAG}/{BIN_NAME}")},
     "not github.com's own download path", False),
    ("live_size", {"release": rel_with(BIN_NAME, size=len(BIN) + 1)}, f"{BIN_NAME} declares size {len(BIN) + 1}, not the expected {len(BIN)}", False),
    ("live_digest", {"release": rel_with(SUMS_NAME, digest="sha256:" + "0" * 64)},
     f"{SUMS_NAME} declares digest sha256:{'0' * 64}, not the expected", False),
    ("live_nodigest", {"release": rel_with(BIN_NAME, digest=None)}, f"{BIN_NAME} declares digest None, not the expected", False),
    ("cdn_undeclared", {"location": f"https://evil.example/x?{SIG}"}, "an undeclared host", False),
    ("cdn_http", {"location": f"http://{CDN2}/x"}, "not https", False),
    ("cdn_port", {"location": f"https://{CDN2}:8443/x"}, "port 8443", False),
    ("cdn_user", {"location": f"https://u:p@{CDN2}/x"}, "userinfo", False),
    ("double", {"double_redirect": True}, "redirects a second time", False),
    ("web_500", {"web": 500}, "github.com answered 500", False),
    ("tampered", {"served": {BIN_NAME: BIN[:-1] + b"!"}}, f"{BIN_NAME} is not the digest's bytes or the expected size", False),
    ("short", {"served": {SUMS_NAME: SUMS[:-3]}}, f"{SUMS_NAME} is not the digest's bytes or the expected size", False),
]
for tag, knobs, want, before in cases:
    job = knobs.pop("job", None)
    o, g, cw = run(tag, job=job, **knobs)
    check(f"GR refused by name: {tag}", ok(lambda: o.get("ok") is False and want in (o.get("error") or "")
                                           and (not before or g.calls == [])), str(o.get("error")))
o, g, cw = run("tampered_file", served={BIN_NAME: BIN[:-1] + b"!"})
check("GR-9: a refused file is not left on the disk, and nothing after it is requested",
      ok(lambda: o.get("ok") is False and "not the digest's bytes" in (o.get("error") or "")
         and any(c["path"].endswith(BIN_NAME) and c["save_to"] for c in g.calls)
         and not (cw / "release" / BIN_NAME).exists() and not any(SUMS_NAME in c["path"] for c in g.calls)),
      str(list(cw.rglob("*"))))
o, g, cw = run("moved_nothing", commit="f" * 40)
check("GR-10: a moved tag stops the job before the release and before any file",
      ok(lambda: [c["path"] for c in g.calls] == [f"/repos/{REPO}/commits/{TAG}"]), str(g.calls))


def boom(*a, **k):
    raise OSError("connection reset")


cw = TMP / "boom"
cw.mkdir()
try:
    ob = FC.gh_release_job(copy.deepcopy(JOB), send=boom, cwd=cw)
except Exception as e:  # noqa: BLE001
    RAISED.append(f"boom: {type(e).__name__}: {e}")
    ob = {}
check("GR-11: a network error is the job's summary (ok False, the error named), never a raise",
      ok(lambda: ob.get("ok") is False and "OSError" in (ob.get("error") or "")), str(ob))
check("no row's condition, and no job, raised", RAISED == [], str(RAISED))
print(f"\nv3 fetch gh_release: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
