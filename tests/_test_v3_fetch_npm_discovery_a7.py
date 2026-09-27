#!/usr/bin/env python3
"""PREREG-V3 (the auditor's GO after a7-npm g1's 404): research/v3/fetch_npm_discovery_a7.py - window a7-npm-d, npm
metadata discovery - offline, with real processes under the contract (the catcher-only proxy, two fetch-child jobs, a
fake registry.npmjs.org behind a fake hop on a local TLS server; the test hands the children its CA file).

* exactly two GETs, each its own job: the package document and one search; a link inside either document (a tarball,
  a repository, another package) is never requested;
* the discovery record: whether the package exists, its dist-tags and versions, each search hit as text; a 404 on the
  package document is the finding (package_exists false), not a window fault, and the search still runs;
* a document that is not JSON is a named problem; the window's host, package and search come from the manifest,
  whose a7-npm-d entry must be one host with no redirect.

    python tests/_test_v3_fetch_npm_discovery_a7.py
"""
from __future__ import annotations

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


L = _load("v3_launch_npmd", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_npmd", ROOT / "research" / "v3" / "fetch_a3.py")
D = _load("v3_fetch_npm_discovery", ROOT / "research" / "v3" / "fetch_npm_discovery_a7.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_npmd_"))
HOST = "registry.npmjs.org"
made = TF.make_test_cert(TMP / "cert", HOST, org="Google Trust Services")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch npm discovery a7: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

DOC_PATH = "/supermemory-server"
SEARCH_PATH = "/-/v1/search?text=supermemory&size=20"
J = lambda o: (200, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
DOC = {"name": "supermemory-server", "dist-tags": {"latest": "0.1.2"},
       "versions": {"0.1.0": {"dist": {"tarball": f"https://{HOST}/supermemory-server/-/supermemory-server-0.1.0.tgz"}},
                    "0.1.2": {"dist": {"tarball": f"https://{HOST}/supermemory-server/-/supermemory-server-0.1.2.tgz"}}},
       "repository": {"type": "git", "url": "git+https://github.com/supermemoryai/supermemory.git"}}
SEARCH = {"objects": [
    {"package": {"name": "supermemory", "version": "3.0.0", "description": "the SDK",
                 "links": {"npm": f"https://{HOST}/supermemory", "repository": "https://github.com/supermemoryai/sdk"}}},
    {"package": {"name": "@supermemory/server", "version": "0.2.0", "description": "self-hosted server",
                 "links": {"repository": "https://github.com/supermemoryai/supermemory"}}}], "total": 2}
routes: dict = {}
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


def window(tag: str):
    base = TMP / tag
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    n0 = len(srv.heads)
    res = D.run_discovery(c, L, F, run="d1", python=Path(sys.executable), via_port=hop.port, parent_env=os.environ,
                          native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                          fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                          child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP)
    return res, c, [h.split(b" ")[1].decode() for h in srv.heads[n0:]]


try:
    print("\n- the manifest is the window's one source -")
    MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
    w = MANI["windows"]["a7-npm-d"]
    check("host, package and search come from the manifest's a7-npm-d entry",
          [D.HOST] == w["hosts"] == [HOST] and D.PACKAGE == w["package"] == "supermemory-server"
          and D.DOC_URL == f"https://{HOST}{DOC_PATH}" and D.SEARCH_URL == f"https://{HOST}{SEARCH_PATH}")
    for label, bad in (("no window", {"windows": {}}), ("two hosts", {"windows": {"a7-npm-d": dict(w, hosts=[HOST, "x.org"])}}),
                       ("a redirect", {"windows": {"a7-npm-d": dict(w, max_redirects=1)}}),
                       ("an extra key", {"windows": {"a7-npm-d": dict(w, pins=["x"])}}),
                       ("an invalid package name", {"windows": {"a7-npm-d": dict(w, package="../x")}}),
                       ("a search size over 250", {"windows": {"a7-npm-d": dict(w, search_size=500)}})):
        try:
            D.window_decl(bad)
            got = "accepted"
        except D.ManifestError as e:
            got = str(e)
        check(f"window_decl refuses {label}", got != "accepted", got)

    print("\n- the package exists: two GETs, the facts as data, no link followed -")
    routes.clear()
    routes[DOC_PATH] = J(DOC)
    routes[SEARCH_PATH] = J(SEARCH)
    res, c, heads = window("found")
    check("exactly the two declared GETs, in order - no tarball, repository or search-hit link requested",
          heads == [DOC_PATH, SEARCH_PATH], str(heads))
    check("the discovery names the package's versions and dist-tags", res.get("package_exists") is True
          and res.get("package_document", {}).get("versions") == ["0.1.0", "0.1.2"]
          and res["package_document"].get("dist_tags") == {"latest": "0.1.2"}
          and "supermemoryai/supermemory" in (res["package_document"].get("repository") or ""), json.dumps(res)[:400])
    check("each search hit as text (name, version, description, links)",
          [h["name"] for h in res.get("search") or []] == ["supermemory", "@supermemory/server"]
          and res["search"][1]["repository"] == "https://github.com/supermemoryai/supermemory")
    check("no problem, and the record is written in the window's run directory",
          res.get("problems") == [] and res.get("window_problems") == [] and Path(res.get("discovery_path", "")).is_file()
          and json.loads(Path(res["discovery_path"]).read_text(encoding="utf-8"))["search"] == res["search"])
    check("the documents' sha256 are recorded", len(res.get("package_document_sha256") or "") == 64
          and len(res.get("search_sha256") or "") == 64)
    check("nothing is placed as a pin", not (c.runs_root / "_pins").exists())

    print("\n- the package is absent: a 404 is the finding, the search still runs -")
    routes.clear()
    routes[SEARCH_PATH] = J(SEARCH)
    res, c, heads = window("absent")
    check("package_exists false with its 404, the search recorded, no problem beyond the finding",
          res.get("package_exists") is False and res.get("package_status") == 404 and heads == [DOC_PATH, SEARCH_PATH]
          and len(res.get("search") or []) == 2 and res.get("problems") == [] and res.get("window_problems") == [],
          json.dumps({k: res.get(k) for k in ("package_exists", "problems", "window_problems")}))

    print("\n- named problems -")
    routes.clear()
    routes[DOC_PATH] = (200, [("Content-Type", "text/html")], b"<html>not json</html>")
    routes[SEARCH_PATH] = (200, [("Content-Type", "application/json")], b'{"no": "objects"}')
    res, c, heads = window("garbled")
    check("a package document that is not JSON and a search without objects are named problems",
          any("package document does not read" in p for p in res.get("problems") or [])
          and any("search answer does not read" in p for p in res.get("problems") or []), str(res.get("problems")))
    routes.clear()
    routes[DOC_PATH] = J(DOC)
    res, c, heads = window("no-search")
    check("a search the registry does not answer is a named problem (not the finding)",
          any("the search" in p for p in res.get("problems") or []) and res.get("window_problems"), str(res))
    check("NC: discover() names a third request even beside the two declared GETs",
          any("not exactly the two" in p for p in D.discover({"run": "x", "jobs": [
              {"unit": str(TMP), "summary": [{"id": "npm:package-document", "status": 404},
                                            {"id": "npm:search", "ok": False, "error": "x"},
                                            {"id": "npm:tarball", "ok": True}]}]})["problems"]))
    j500 = "job 0 request npm:package-document: status 500"
    j404 = "job 0 request npm:package-document: status 404"
    check("NB: window_problems keeps a package-document 500 (a fault) and drops only its 404 (the finding)",
          D.window_problems({"problems": [j500, j404, "job 0: the fetch child exited with 3"]}) == [j500],
          str(D.window_problems({"problems": [j500, j404, "job 0: the fetch child exited with 3"]})))
    check("discover() names any request beyond the two declared GETs",
          any("not exactly the two" in p for p in D.discover({"run": "x", "jobs": [
              {"unit": str(TMP), "summary": [{"id": "npm:package-document", "status": 404},
                                            {"id": "npm:tarball", "ok": True}]}]})["problems"]))

    print("\n- run_discovery names a window that ran elsewhere (a stand-in window, no child) -")

    class _StubF:
        """fetch_a3 in place: run_child_window returns the given record and starts nothing."""

        def __init__(self, rec: dict) -> None:
            self.rec = rec

        def run_child_window(self, c, L, **kw) -> dict:
            return self.rec

    class _StubC:
        def __init__(self, root: Path) -> None:
            self.runs_root = root

    for label, rec, want in (
            ("a window run on hosts it did not declare", {"hosts": [HOST, "x.org"], "catcher": []}, "ran on"),
            ("a catcher tunnel to another host", {"hosts": [HOST], "catcher": [{"host": "evil.example"}]}, "tunnelled to")):
        root = TMP / "stub" / want.replace(" ", "_")
        (root / "_fetch" / D.WINDOW / "d9").mkdir(parents=True)
        rec = {"run": "d9", "jobs": [{"unit": str(TMP), "summary": [{"id": "npm:package-document", "status": 404},
                                                                  {"id": "npm:search", "error": "stub"}]}],
               "problems": [], **rec}
        got = D.run_discovery(_StubC(root), L, _StubF(rec), run="d9", python=Path(sys.executable), via_port=1,
                              parent_env={})
        check(f"{label} is a named problem", any(want in x for x in got.get("problems") or []), str(got.get("problems")))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch npm discovery a7: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
