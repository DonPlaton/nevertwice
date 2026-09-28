#!/usr/bin/env python3
"""PREREG-V3 A7 (.loop/A7-PLAN-2026-09-27.md phase 1): research/v3/fetch_a3.py plan d3 - the a7-discovery window,
metadata only - offline, with real processes under the contract (the catcher-only proxy, four fetch-child jobs, a fake
api.github.com and huggingface.co behind a fake hop on a local TLS server; the test hands the children its CA file).

* job 1: each repository and its head, and for the tagged ones (the auditor's addition 2) their tags and releases;
  job 2: the model's revision; job 3: each found repository's recursive tree at its head, under the name its answer
  gave; job 4: the model's tree at the revision found - nothing else is requested (no tag tarball, release asset or
  link in any answer);
* a repository the API does not know (404) is named in the report and gets no tree request - never guessed around;
* the report: names, licences, heads, tree sizes, tags and releases, the candidate paths for phase 2 by what each
  repository is read for, and the model's revision and files; the manifest's a7-discovery hosts are the plan's.

    python tests/_test_v3_fetch_a7_discovery.py
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


L = _load("v3_launch_d3", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d3", ROOT / "research" / "v3" / "fetch_a3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_d3_"))
GH, HF = "api.github.com", "huggingface.co"
made = TF.make_test_cert(TMP / "cert", GH, extra_hosts=(HF,), org="Sectigo Limited")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 discovery: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

J = lambda o: (200, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
SM, CG, ZP, MINI = "supermemoryai/supermemory", "topoteretes/cognee", "getzep/zep-papers", "sentence-transformers/all-MiniLM-L6-v2"
H_SM, H_CG, REV = "a" * 40, "b" * 40, "c" * 40
routes = {
    f"/repos/{SM}": J({"full_name": SM, "default_branch": "main", "license": {"spdx_id": "MIT"}}),
    f"/repos/{SM}/commits/HEAD": J({"sha": H_SM}),
    f"/repos/{SM}/tags?per_page=100": J([{"name": "v1.2.0", "commit": {"sha": "d" * 40},
                                          "tarball_url": f"https://{GH}/repos/{SM}/tarball/v1.2.0"}]),
    f"/repos/{SM}/releases?per_page=30": J([{"tag_name": "v1.2.0", "name": "1.2", "published_at": "2026-08-01T00:00:00Z",
                                             "prerelease": False, "assets": [{"browser_download_url": f"https://{GH}/x.zip"}]}]),
    f"/repos/{SM}/git/trees/{H_SM}?recursive=1": J({"sha": H_SM, "truncated": False, "tree": [
        {"path": "README.md", "type": "blob", "sha": "1" * 40}, {"path": "apps/docs/self-hosting.mdx", "type": "blob", "sha": "2" * 40},
        {"path": "docker-compose.yml", "type": "blob", "sha": "3" * 40}, {"path": "apps/web/page.tsx", "type": "blob", "sha": "4" * 40},
        {"path": "apps/docs", "type": "tree", "sha": "5" * 40}]}),
    f"/repos/{CG}": J({"full_name": CG, "default_branch": "dev", "license": {"spdx_id": "Apache-2.0"}}),
    f"/repos/{CG}/commits/HEAD": J({"sha": H_CG}),
    f"/repos/{CG}/tags?per_page=100": J([{"name": "v0.3.4", "commit": {"sha": "e" * 40}}]),
    f"/repos/{CG}/releases?per_page=30": J([]),
    f"/repos/{CG}/git/trees/{H_CG}?recursive=1": J({"sha": H_CG, "truncated": False, "tree": [
        {"path": "evals/beam/run_beam.py", "type": "blob", "sha": "6" * 40},
        {"path": "cognee/api/v1/add.py", "type": "blob", "sha": "7" * 40}]}),
    f"/api/models/{MINI}/revision/main": J({"sha": REV}),
    f"/api/models/{MINI}/tree/{REV}?recursive=true": J([
        {"type": "file", "path": "config.json", "size": 612, "oid": "8" * 40},
        {"type": "file", "path": "model.safetensors", "size": 90868376, "oid": "9" * 40, "lfs": {"oid": "f" * 64, "size": 90868376}},
        {"type": "directory", "path": "1_Pooling"}]),
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
    MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
    check("the manifest's a7-discovery hosts are the plan's", sorted(MANI["windows"]["a7-discovery"]["hosts"]) == F.D3_HOSTS)
    base = TMP / "w"
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    rec = F.run_child_window(c, L, window="a7-discovery", hosts=F.D3_HOSTS, jobs=F.d3_jobs(), python=Path(sys.executable),
                             via_port=hop.port, run="d3", parent_env=os.environ,
                             native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                             fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                             child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    want = [f"/repos/{SM}", f"/repos/{SM}/commits/HEAD", f"/repos/{SM}/tags?per_page=100", f"/repos/{SM}/releases?per_page=30",
            f"/repos/{CG}", f"/repos/{CG}/commits/HEAD", f"/repos/{CG}/tags?per_page=100", f"/repos/{CG}/releases?per_page=30",
            f"/repos/{ZP}", f"/repos/{ZP}/commits/HEAD", f"/api/models/{MINI}/revision/main",
            f"/repos/{SM}/git/trees/{H_SM}?recursive=1", f"/repos/{CG}/git/trees/{H_CG}?recursive=1",
            f"/api/models/{MINI}/tree/{REV}?recursive=true"]
    check("exactly the declared requests: repositories, heads, tags and releases of the tagged ones, the model's "
          "revision, the found trees - no tarball, asset or link followed, no tree for the unknown repository",
          asked == want, str(asked))
    rep = F.d3_report(rec)
    sm, cg, zp = rep["repos"][SM], rep["repos"][CG], rep["repos"][ZP]
    check("supermemory: name, licence, head, tags and releases as data, and its candidate paths (README, "
          "self-hosting, docker)", sm["full_name"] == SM and sm["licence"] == "MIT" and sm["head_sha"] == H_SM
          and sm["tags"] == [{"name": "v1.2.0", "commit": "d" * 40}] and sm["releases"][0]["tag_name"] == "v1.2.0"
          and sm["candidates"] == ["README.md", "apps/docs/self-hosting.mdx", "docker-compose.yml"], json.dumps(sm)[:400])
    check("cognee: tags read (the harness must match the pinned product), the BEAM harness among the candidates",
          cg["tags"] == [{"name": "v0.3.4", "commit": "e" * 40}] and cg["releases"] == []
          and cg["candidates"] == ["evals/beam/run_beam.py"] and cg["default_branch"] == "dev", json.dumps(cg)[:300])
    check("the unknown repository is named with its 404 - no head, no tree, no candidates, and no tags asked for it",
          zp["status"] == 404 and zp["full_name"] is None and zp["tree_entries"] == 0 and zp["candidates"] == []
          and "tags" not in zp, json.dumps(zp))
    check("the model: its revision and its files (sizes, oids, the LFS sha256)", rep["model"]["revision"] == REV
          and [f["path"] for f in rep["model"]["files"]] == ["config.json", "model.safetensors"]
          and rep["model"]["files"][1]["lfs_sha256"] == "f" * 64)
    check("the window's own problems are the named 404s of the unknown repository only",
          any(ZP in p for p in rec["problems"]) and all(ZP in p or "exited with 3" in p for p in rec["problems"])
          and rec["check"]["complete"]
          and rec["check"]["native_hits"] == 0, str(rec["problems"]))
    check("B-D3P: one page of tags and releases here - not full, so nothing older is hidden",
          sm["tags_page_full"] is False and sm["releases_page_full"] is False)
    units = [TMP / "d3p" / f"u{i}" for i in range(4)]
    for u in units:
        u.mkdir(parents=True)
    sd = units[0] / "gh" / F._safe(SM)
    sd.mkdir(parents=True)
    (sd / "tags.json").write_text(json.dumps([{"name": f"v{i}", "commit": {"sha": "a" * 40}} for i in range(100)]))
    (sd / "releases.json").write_text(json.dumps([{"tag_name": f"v{i}"} for i in range(30)]))
    full = F.d3_report({"jobs": [{"unit": str(u), "summary": []} for u in units]})
    check("B-D3P: a full first page - 100 tags, 30 releases - is flagged: an older pinned tag may be past it, never "
          "'no such version'", full["repos"][SM]["tags_page_full"] is True and full["repos"][SM]["releases_page_full"] is True
          and full["repos"][CG]["tags_page_full"] is False, json.dumps({k: full["repos"][SM].get(k) for k in (
              "tags_page_full", "releases_page_full")}))
    ud = {i: TMP / "d3idx" / f"j{i}" for i in (0, 1, 3)}                  # phase c (2) did not run: no repository
    mdir = F._safe(F.D3_HF_MODEL)
    (ud[1] / "meta" / "models" / mdir).mkdir(parents=True)
    (ud[1] / "meta" / "models" / mdir / "revision.json").write_text(json.dumps({"sha": "b" * 40}))
    (ud[3] / "meta" / "models" / mdir).mkdir(parents=True)
    (ud[3] / "meta" / "models" / mdir / "tree.json").write_text(json.dumps([{"type": "file", "path": "config.json",
                                                                            "size": 1, "oid": "c" * 40}]))
    ud[0].mkdir(parents=True)
    idx = F.d3_report({"jobs": [{"index": i, "unit": str(u), "summary": []} for i, u in ud.items()]})
    check("B-D3IDX: phase c skipped (no repository resolved) - the model's tree phase d fetched is still in the report: "
          "a result is read by its job index, never by its list position", idx["model"]["revision"] == "b" * 40
          and [f["path"] for f in idx["model"]["files"]] == ["config.json"], json.dumps(idx["model"]))
    u2 = {i: TMP / "d2idx" / f"j{i}" for i in (0, 2)}                    # phase 1 did not run
    m0 = u2[0] / "gh" / F._safe(F.MEM0)
    m0.mkdir(parents=True)
    (m0 / "evaluation_commits.json").write_text(json.dumps([{"sha": "d" * 40, "parents": [{"sha": "e" * 40}]}]))
    (u2[2] / "gh" / F._safe(F.MEM0)).mkdir(parents=True)
    (u2[2] / "gh" / F._safe(F.MEM0) / "tree_parent.json").write_text(json.dumps(
        {"tree": [{"path": "evaluation/run.py", "type": "blob"}]}))
    rep2 = F.d2_report({"jobs": [{"index": i, "unit": str(u), "summary": []} for i, u in u2.items()]})
    check("B-D3IDX (d2_report): phase 1 skipped - the parent tree phase 2 fetched still pins mem0's evaluation commit: "
          "read by job index, never by list position", rep2["mem0"]["pinned_commit"] == "e" * 40
          and rep2["mem0"]["evaluation_files"] == ["evaluation/run.py"], json.dumps(rep2["mem0"]))
    print("\n- main(): plan d3 only in window a7-discovery, on the plan's hosts - refused before any spawn -")
    import contextlib  # noqa: E402,PLC0415
    import io  # noqa: E402,PLC0415
    from types import SimpleNamespace  # noqa: E402,PLC0415

    class _WouldSpawn(Exception):
        pass

    def run_main(argv, manifest):
        """main() with the contract, the corpus pins and the window stubbed - a main that gets past its checks raises
        _WouldSpawn instead of starting anything; (rc or 'spawned', stderr)."""
        mp = TMP / "manifest_main.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        saved = (F._load, F.MANIFEST, F.run_child_window)
        stub_l = SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(runs_root=TMP)),
                                 network_via_port=lambda c: 47999)
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
        finally:
            F._load, F.MANIFEST, F.run_child_window = saved
        return rc, err.getvalue()

    tail = ["--run", "t", "--python", sys.executable]
    r1 = run_main(["--window", "a3-discovery", "--plan", "d3", *tail], MANI)
    r2 = run_main(["--window", "a7-discovery", "--plan", "d1", *tail], MANI)
    check("DD: plan d3 in window a3-discovery, and window a7-discovery with plan d1, are refused (rc 2, named)",
          all(rc == 2 and "plan d3 runs in window a7-discovery" in msg for rc, msg in (r1, r2)), f"{r1} {r2}")
    other = json.loads(json.dumps(MANI))
    other["windows"]["a7-discovery"]["hosts"] = sorted(F.D3_HOSTS + ["registry.npmjs.org"])
    r3 = run_main(["--window", "a7-discovery", "--plan", "d3", *tail], other)
    check("DE: a manifest whose a7-discovery hosts are not the plan's is refused (rc 2, named)",
          r3[0] == 2 and "are not the plan's" in r3[1], str(r3))
    check("the tags and releases requests exist only for the tagged repositories",
          sorted(r["id"] for r in F.d3_phase_a()["requests"] if r["id"].startswith(("ghtags", "ghreleases")))
          == sorted([f"ghtags:{SM}", f"ghreleases:{SM}", f"ghtags:{CG}", f"ghreleases:{CG}"]))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 discovery: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
