#!/usr/bin/env python3
"""PREREG-V3 A8 (the auditor's Q-A8-10): research/v3/fetch_a3.py plan d4 - the a7-docs window, text and metadata only -
offline, with real processes under the contract (the catcher-only proxy, the fetch-child jobs, a fake api.github.com
behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-docs" entry, the single source: one host (api.github.com), no redirect, the
  repository, the tag's commit (ref), the head the discovery found, the documentation paths and the release tag -
  anything else in the entry is refused before any spawn;
* job 1: each path through the contents API at the TAG's commit, and the release by its tag (its assets' metadata -
  names, sizes, digests; no asset is downloaded, no link in any answer followed); job 2: only the paths the tag
  answered 404 for, at the head - and those are marked;
* the report: per path where it was read (tag, head or nowhere, each status named), its size, sha256 and the git blob
  sha1 the answer names, checked against the decoded content - a content that is not the named blob is a problem and
  is never written; the verified texts go to docs/<tag|head>/<path>; the release's metadata.

    python tests/_test_v3_fetch_a7_docs.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402

_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_d4", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d4", ROOT / "research" / "v3" / "fetch_a3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def holds(fn):
    """(value, None) or (None, the exception named) - a call that raises FAILs its own row, never the suite."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
REAL = MANI["windows"].get("a7-docs") or {}
print("- the manifest's a7-docs entry (Q-A8-10) -")
decl, derr = holds(lambda: F.d4_decl(MANI))
check("the manifest declares a7-docs as plan d4 reads it: api.github.com only, no redirect, supermemoryai/supermemory "
      "at the tag server-v0.0.8's commit 5d2b585, the head b392bc7 the discovery found, the six self-hosting pages and "
      "the README, the release server-v0.0.8", derr is None and decl["hosts"] == ["api.github.com"]
      and decl["max_redirects"] == 0 and decl["repo"] == "supermemoryai/supermemory"
      and decl["commit"] == "5d2b5855fe492a3682a1cde4a255e2db0c4db595" and decl["ref_name"] == "server-v0.0.8"
      and decl["head"] == "b392bc7d1b294a5b3dd2407d060ddcf229481d6f" and decl["release_tag"] == "server-v0.0.8"
      and decl["paths"] == ["README.md"] + [f"apps/docs/self-hosting/{n}.mdx" for n in (
          "overview", "quickstart", "configuration", "embeddings", "providers", "local-vs-enterprise")],
      str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "follow": True},
    "another host": {**REAL, "hosts": ["api.github.com", "raw.githubusercontent.com"]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a commit that is a branch name": {**REAL, "commit": "main"},
    "a short head": {**REAL, "head": "b392bc7"},
    "a path going up": {**REAL, "paths": ["README.md", "../secrets.env"]},
    "an absolute path": {**REAL, "paths": ["/etc/passwd"]},
    "a path with a query": {**REAL, "paths": ["README.md?ref=main"]},
    "a repeated path": {**REAL, "paths": ["README.md", "README.md"]},
    "no path": {**REAL, "paths": []},
    "a release tag with a slash": {**REAL, "release_tag": "../../x"},
    "a repository that is no name": {**REAL, "repo": "supermemoryai/supermemory/../x"},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d4_decl({"windows": {"a7-docs": entry}}))
    refused[label] = bool(e) and e.startswith("D4ManifestError")
check("DF: an entry with anything but the declared shape is refused by name (D4ManifestError) - "
      + ", ".join(bad_cases), all(refused.values()), str([k for k, v in refused.items() if not v]))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d4_"))
GH = "api.github.com"
made = TF.make_test_cert(TMP / "cert", GH, org="Sectigo Limited")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 docs: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

REPO, REF, HEAD = "supermemoryai/supermemory", "1" * 40, "2" * 40
PATHS = ["README.md", "apps/docs/self-hosting/overview.mdx", "apps/docs/self-hosting/quickstart.mdx",
         "apps/docs/self-hosting/configuration.mdx", "apps/docs/self-hosting/embeddings.mdx"]
DECL = {"hosts": ["api.github.com"], "purpose": "test", "repo": REPO, "commit": REF, "ref_name": "server-v0.0.8",
        "head": HEAD, "paths": PATHS, "release_tag": "server-v0.0.8", "max_redirects": 0}
J = lambda o, st=200: (st, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
NOT_FOUND = J({"message": "Not Found"}, 404)
TEXT = {p: (f"# {p}\n\nself-hosting text of {p}\n" * 3).encode() for p in PATHS}


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def contents(p: str, data: bytes, sha: str | None = None):
    b64 = base64.encodebytes(data).decode()                  # GitHub's own form: base64 with line breaks
    return J({"type": "file", "encoding": "base64", "size": len(data), "name": p.rsplit("/", 1)[-1], "path": p,
              "sha": sha or blob(data), "content": b64,
              "download_url": f"https://raw.githubusercontent.com/{REPO}/{REF}/{p}"})


def url(p: str, ref: str) -> str:
    return f"/repos/{REPO}/contents/{p}?ref={ref}"


routes = {
    url(PATHS[0], REF): contents(PATHS[0], TEXT[PATHS[0]]),
    url(PATHS[1], REF): contents(PATHS[1], TEXT[PATHS[1]]),
    url(PATHS[2], REF): NOT_FOUND, url(PATHS[2], HEAD): contents(PATHS[2], TEXT[PATHS[2]]),   # added after the tag
    url(PATHS[3], REF): NOT_FOUND, url(PATHS[3], HEAD): NOT_FOUND,                            # nowhere
    url(PATHS[4], REF): contents(PATHS[4], TEXT[PATHS[4]], sha="9" * 40),                     # not the named blob
    f"/repos/{REPO}/releases/tags/server-v0.0.8": J({
        "tag_name": "server-v0.0.8", "name": "supermemory-server 0.0.8", "published_at": "2026-08-17T18:39:22Z",
        "prerelease": False, "draft": False, "target_commitish": "main",
        "assets": [{"name": "supermemory-server-linux-x64.tar.gz", "size": 1234, "digest": "sha256:" + "a" * 64,
                    "content_type": "application/gzip",
                    "browser_download_url": f"https://github.com/{REPO}/releases/download/server-v0.0.8/x.tar.gz"},
                   {"name": "supermemory-server-windows-x64.zip", "size": 5678, "digest": None,
                    "content_type": "application/zip"}]}),
}
srv = TF.TlsHttpServer(made[0], made[1], routes)
hop = TF.TunnelHop(srv.port)


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


try:
    print("\n- the window, offline: the fetch children under the contract -")
    base = TMP / "w"
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d4_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-docs", hosts=F.D4_HOSTS, jobs=jobs, python=Path(sys.executable), via_port=hop.port, run="t1",
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    want = [url(p, REF) for p in PATHS] + [f"/repos/{REPO}/releases/tags/server-v0.0.8"] \
        + [url(PATHS[2], HEAD), url(PATHS[3], HEAD)]
    check("D4-1: exactly the declared requests - every path at the TAG's commit and the release by its tag, then at the "
          "head only the paths the tag answered 404 for; no asset, no download_url and no other link followed",
          rerr is None and asked == want, f"{rerr} {asked}")
    rep, files = (None, None)
    got, gerr = holds(lambda: F.d4_report(rec, DECL)) if rec is not None else (None, rerr)
    if got is not None:
        rep, files = got
    docs = {d["path"]: d for d in (rep or {}).get("docs") or []}
    d0, d2, d3, d4 = (docs.get(PATHS[i]) or {} for i in (0, 2, 3, 4))
    check("D4-2: a path found at the tag is read there, unmarked - its size, sha256 and the answer's blob sha1, checked "
          "against the decoded content (GitHub's base64 with line breaks)",
          gerr is None and d0.get("read_at") == "tag" and d0.get("ref") == REF and d0.get("marked") is False
          and d0.get("size") == len(TEXT[PATHS[0]]) and d0.get("sha256") == hashlib.sha256(TEXT[PATHS[0]]).hexdigest()
          and d0.get("blob_sha") == blob(TEXT[PATHS[0]]) and d0.get("blob_ok") is True, f"{gerr} {d0}")
    check("D4-3: a path absent at the tag (404) is read at the head and MARKED, its tag status named",
          d2.get("read_at") == "head" and d2.get("ref") == HEAD and d2.get("marked") is True
          and d2.get("tag_status") == 404 and d2.get("blob_ok") is True, str(d2))
    check("D4-4: a path at neither is named with both statuses - read nowhere, a problem of the report, never guessed "
          "around", d3.get("read_at") is None and d3.get("tag_status") == 404 and d3.get("head_status") == 404
          and any(PATHS[3] in p and "404" in p for p in (rep or {}).get("problems") or []), f"{d3} {(rep or {}).get('problems')}")
    check("D4-5: a content that is not the blob its answer names is a problem by name and is NOT written",
          d4.get("blob_ok") is False and any(PATHS[4] in p and "not the blob" in p for p in (rep or {}).get("problems") or [])
          and not any(k.endswith(PATHS[4]) for k in (files or {})), f"{d4} {sorted(files or {})}")
    check("D4-6: the verified texts are returned for docs/<tag|head>/<path>, byte for byte - and nothing else",
          files == {f"docs/tag/{PATHS[0]}": TEXT[PATHS[0]], f"docs/tag/{PATHS[1]}": TEXT[PATHS[1]],
                    f"docs/head/{PATHS[2]}": TEXT[PATHS[2]]}, str(sorted(files or {})))
    relm = (rep or {}).get("release") or {}
    check("D4-7: the release's metadata - tag, name, date, prerelease and draft flags, and per asset its name, size, "
          "digest and type (a missing digest stays None); no download URL is carried",
          relm.get("status") == 200 and relm.get("tag_name") == "server-v0.0.8" and relm.get("prerelease") is False
          and relm.get("draft") is False
          and relm.get("assets") == [{"name": "supermemory-server-linux-x64.tar.gz", "size": 1234,
                                      "digest": "sha256:" + "a" * 64, "content_type": "application/gzip"},
                                     {"name": "supermemory-server-windows-x64.zip", "size": 5678, "digest": None,
                                      "content_type": "application/zip"}]
          and "download" not in json.dumps(relm), json.dumps(relm)[:400])
    check("D4-8: the window's check is complete with no native hit; its problems are the named 404s only",
          rec is not None and rec["check"]["complete"] and rec["check"]["native_hits"] == 0
          and all("404" in p or "exited with 3" in p for p in rec["problems"]), str((rec or {}).get("problems")))
    no404 = {**DECL, "paths": PATHS[:2]}
    b, berr = holds(lambda: F.d4_jobs(no404)[1]([{"index": 0, "unit": str(TMP), "summary": [
        {"id": f"doc:tag:{p}", "status": 200} for p in PATHS[:2]]}]))
    check("D4-9: with every path found at the tag, no head job is built at all", berr is None and b is None, f"{berr} {b}")

    print("\n- main(): plan d4 only in window a7-docs, on its declared entry - refused before any spawn -")
    import contextlib  # noqa: E402,PLC0415
    import io  # noqa: E402,PLC0415
    from types import SimpleNamespace  # noqa: E402,PLC0415

    class _WouldSpawn(Exception):
        pass

    def run_main(argv, manifest):
        mp = TMP / "manifest_main.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        saved = (F._load, F.MANIFEST, F.run_child_window)
        stub_l = SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(runs_root=TMP)),
                                 network_via_port=lambda c_: 47999)
        F._load = lambda name, path: stub_l if name == "v3_launch" else SimpleNamespace(PINS={})
        F.MANIFEST = mp

        def no_spawn(*a_, **k_):
            raise _WouldSpawn("main reached the window")
        F.run_child_window = no_spawn
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = F.main(argv)
        except _WouldSpawn:
            rc = "spawned"
        except SystemExit as e:
            rc = f"exit {e.code}"
        finally:
            F._load, F.MANIFEST, F.run_child_window = saved
        return rc, err.getvalue()

    tail = ["--run", "t", "--python", sys.executable]
    m1 = run_main(["--window", "a7-docs", "--plan", "d3", *tail], MANI)
    m2 = run_main(["--window", "a7-discovery", "--plan", "d4", *tail], MANI)
    m3 = run_main(["--window", "a7-docs", "--plan", "d1", *tail], MANI)
    check("D4-10: plan d4 outside window a7-docs, and window a7-docs with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d4 runs in window a7-docs" in msg for rc, msg in (m1, m2, m3)), f"{m1} {m2} {m3}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-docs", {})["hosts"] = ["api.github.com", "raw.githubusercontent.com"]
    m4 = run_main(["--window", "a7-docs", "--plan", "d4", *tail], other)
    m5 = run_main(["--window", "a7-docs", "--plan", "d4", *tail], MANI)
    check("D4-11: a manifest whose a7-docs entry is not the declared shape is refused (rc 2, named); the real one gets "
          "to the window", m4[0] == 2 and "a7-docs" in m4[1] and m5[0] == "spawned", f"{m4} {m5}")
finally:
    try:
        hop.close()
        srv.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 docs: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
