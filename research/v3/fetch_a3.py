#!/usr/bin/env python3
"""PREREG-V3 plan step A3 (A3.e/A3.f harness): the fetch windows - every fetch a child under the contract.

The auditor's O1 ruling: the harness (this process, outside the contract) never fetches. For one declared window it

1. checks the free space on the destination volume (the auditor's Q-A3-2 floor);
2. starts the catcher-only proxy (launch.spawn_proxy(catcher_only=True): no key, catcher ports only) and a boundary
   check (native egress witness + file-system witness);
3. opens the window - windows.jsonl START with its hosts and the declared hop, and the catcher's /window with the same
   exact hosts for the arm "fetch";
4. runs the window's jobs one after another, each a research/v3/fetch_child.py process spawned under the contract
   (requirement "required", its script the only read exception, HTTPS_PROXY = the arm's catcher); a job may be built
   from the files the previous job saved (discovery's phases). A job {"child": "pip", "python", "argv", "env"} runs a
   venv's pip the same way instead (the install windows): declared PIP_* variables, no stdin, ok = its exit code;
5. closes the window and the check, shuts the proxy down, and writes one record: the jobs and their summaries (hashes,
   redirect hosts, peer issuers), the catcher's host log, the window log, the check, and the problems found.

`a3-discovery` fetches metadata only (HF revisions, trees and cards; GitHub repositories, head commits and trees) and
sends HEADs to resolve URLs to learn their redirect hosts, following none. Its record goes to the auditor, who fixes
the CDN hosts (Q-A3-3) and the prompt/scoring repos before any data window runs.

    python research/v3/fetch_a3.py --window a3-discovery --run d1 --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets as _secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
FETCH_CHILD = HERE / "fetch_child.py"
PROXY_SCRIPT = REPO / "research" / "_llm_proxy.py"
MANIFEST = HERE / "fetch_manifest.json"
ARM = "fetch"
GB = 1 << 30
#: GitHub repositories named before discovery (their pins are in corpus_pin_v3); the cards may name more.
GITHUB_NAMED = ("xiaowu0162/LongMemEval", "snap-research/locomo", "agiresearch/A-mem", "mem0ai/mem0")
#: The auditor's cap on card-linked repositories: external content steers these requests, and each costs three of
#: api.github.com's 60 unauthenticated requests an hour. Links beyond it are recorded by name, never requested.
CARD_LINK_CAP = 12
_GH_LINK = re.compile(r"github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})")
#: GitHub's own naming rules: an owner is 1-39 alphanumerics with single inner hyphens (so never "__", which keeps
#: _safe() reversible); a repository is 1-100 of [A-Za-z0-9_.-] and never "." or "..". A name that external content
#: supplies (a card link, an API's full_name) and that breaks them is named in the record and never requested -
#: "../x" would otherwise walk the API's own path.
_GH_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/(?!\.{1,2}\Z)[A-Za-z0-9_.-]{1,100}")


def gh_name_ok(name: object) -> bool:
    return isinstance(name, str) and _GH_NAME.fullmatch(name) is not None


class WindowRefused(RuntimeError):
    """A window the rules do not allow to start (disk floor, a used run label, no hop)."""


# ── the generic child window ──────────────────────────────────────────────

def _control(port: int, token: str, path: str, body: dict) -> bytes:
    data = json.dumps(body).encode("utf-8")
    s = socket.create_connection(("127.0.0.1", port), timeout=30)
    try:
        s.sendall(f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode("latin-1") + data)
        out = bytearray()
        while chunk := s.recv(65536):
            out += chunk
        return bytes(out)
    finally:
        s.close()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_bytes().decode("utf-8").splitlines()] if path.is_file() else []


def disk_floor_ok(volume: Path, need_bytes: int) -> tuple[bool, int]:
    free = shutil.disk_usage(volume).free
    return free >= need_bytes, free


def judge(record: dict) -> list[str]:
    """Every problem of a window, each named. Empty = clean."""
    problems = []
    for j in record["jobs"]:
        if j["rc"] != 0:
            problems.append(f"job {j['index']}: the fetch child exited with {j['rc']}")
        for r in j["summary"]:
            if not r.get("ok") and not r.get("rate_limited") and r.get("error") != "not sent: rate-limited earlier":
                problems.append(f"job {j['index']} request {r.get('id')}: {r.get('error')}")
    refused = [c for c in record["catcher"] if not c.get("tunnelled")]
    if refused:
        problems.append(f"the catcher refused {len(refused)} request(s): {sorted({c['host'] for c in refused})}")
    limited = [r.get("id") for j in record["jobs"] for r in j["summary"] if r.get("rate_limited")]
    if limited:
        problems.append(f"rate-limited: {limited[0]} - the job stopped, nothing retried")
    chk = record["check"]
    if chk.get("native_hits") != 0:                     # W7 counts a loopback hit in hits too (launch.py)
        problems.append(f"the native witness counted {chk.get('native_hits')} egress hit(s), "
                        f"{chk.get('loopback_hits') or 0} of them loopback")
    if not chk.get("complete"):
        problems.append("the boundary check is not complete")
    if chk.get("fs_hits") != 0:
        problems.append("the file-system witness counted a change in the watched set")
    return problems


def run_child_window(c, L, *, window: str, hosts: list[str], jobs: list, python: Path, via_port: int, run: str,
                     parent_env, native=None, fs=None, child_env_extra: dict | None = None, need_bytes: int = 0,
                     volume: Path | None = None, job_timeout: float = 3600.0) -> dict:
    """One declared window, its jobs as fetch children. ``jobs``: dicts, or callables(results so far) -> dict.
    ``child_env_extra`` is for tests only (a CA file for the fake TLS server); a real window passes none."""
    base = c.runs_root / "_fetch" / window / run
    if base.exists():
        raise WindowRefused("this window run label was used before")
    ok, free = disk_floor_ok(volume or Path(c.runs_root.anchor), need_bytes)
    if not ok:
        raise WindowRefused(f"the free space ({free // GB} GB) is under the window's floor ({need_bytes // GB} GB)")
    pdir = base / "_proxy"
    pdir.mkdir(parents=True)
    stand = f"_fetch.{window}"
    proxy_unit = L.make_unit_dirs(c, stand, run, "proxy", "p1")
    cfg_path = pdir / "catch_config.json"
    cfg_path.write_bytes(json.dumps({"run_dir": str(pdir), "via": {"host": "127.0.0.1", "port": via_port},
                                     "catchers": [ARM]}, sort_keys=True).encode("utf-8"))
    token = "ctl-" + _secrets.token_hex(24)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    check_id = f"fetch-{window}-{run}"
    W.begin_check(check_id)
    results: list[dict] = []
    proxy = None
    error = None
    try:
        proxy, ports = L.spawn_proxy(c, python, script=PROXY_SCRIPT, config_path=cfg_path,
                                     stdin_secrets={"control_token": token}, unit=proxy_unit, parent_env=parent_env,
                                     witnesses=W, catcher_only=True)
        catcher = f"http://127.0.0.1:{ports['arms'][ARM]['catcher']}"

        def proxy_control(action: str, name: str, win_hosts) -> None:
            body = {"name": name, "state": "open" if action == "open" else "close"}
            if action == "open":
                body.update(hosts=list(win_hosts), arms=[ARM])
            got = _control(ports["control"], token, "/window", body)
            if not got.startswith(b"HTTP/1.1 200"):
                raise WindowRefused(f"the catcher refused the window {action}")

        with L.fetch_window(c, window, hosts, witnesses=W, proxy_control=proxy_control,
                            via=f"127.0.0.1:{via_port}") as win:
            for i, job in enumerate(jobs):
                spec = job(results) if callable(job) else job
                if spec is None:
                    continue
                unit = L.make_unit_dirs(c, stand, run, ARM, f"j{i}")
                pip = spec.get("child") == "pip"
                exe = Path(spec["python"]) if pip else Path(python)
                argv = [os.fspath(exe), *spec["argv"]] if pip else [os.fspath(python), os.fspath(FETCH_CHILD)]
                declared = {**(spec.get("env") or {}), **(child_env_extra or {})} if pip else dict(child_env_extra or {})
                env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[exe.parent], declared=declared,
                                  catcher_url=catcher)
                child = L.spawn(c, argv, env=env, cwd=unit.cwd,
                                record={"role": "fetch", "stand": None, "run": f"{window}.{run}", "arm": ARM,
                                        "unit": f"j{i}"},
                                parent_env=parent_env, catcher_url=catcher,
                                argv_exception={} if pip else {1: FETCH_CHILD},
                                witnesses=W, requirement="required", window=win,
                                stdin=subprocess.DEVNULL if pip else subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if not pip:
                    child.process.stdin.write((json.dumps(spec) + "\n").encode("utf-8"))
                    child.process.stdin.close()
                try:
                    out, err = child.process.communicate(timeout=job_timeout)
                    rc = child.process.returncode
                except subprocess.TimeoutExpired:
                    child.kill_tree()
                    rc, out, err = None, b"", b""
                if pip:
                    summary = [{"id": "pip", "ok": rc == 0,
                                "error": None if rc == 0 else f"pip exited with {rc}: "
                                + err.decode("utf-8", "replace")[-200:].replace("\n", " ")}]
                else:
                    summary_f = unit.cwd / "fetch_summary.json"
                    summary = json.loads(summary_f.read_bytes()) if summary_f.is_file() else []
                results.append({"index": i, "rc": rc, "unit": str(unit.cwd), "job": spec, "summary": summary,
                                "stdout_tail": out.decode("utf-8", "replace")[-600:] if pip else "",
                                "stderr_tail": err.decode("utf-8", "replace")[-300:]})
    except (L.ContractViolation, WindowRefused) as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        if proxy is not None:
            try:
                _control(ports["control"], token, "/shutdown", {})
            except OSError:
                pass
            try:
                proxy.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proxy.kill_tree()
        chk = W.end_check(check_id)
    native_rec = chk.get("native") or {}
    record = {"window": window, "run": run, "hosts": list(hosts), "via": {"host": "127.0.0.1", "port": via_port},
              "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "error": error, "jobs": results,
              "catcher": _jsonl(pdir / "catcher.jsonl"), "windows_proxy": _jsonl(pdir / "windows_proxy.jsonl"),
              "issuers": sorted({(r.get("final_host"), r.get("issuer_o"), r.get("issuer_cn")) for j in results
                                 for r in j["summary"] if r.get("issuer_cn")}),
              "check": {"id": check_id, "complete": chk.get("complete"), "native_hits": native_rec.get("hits"),
                        "loopback_hits": native_rec.get("loopback_hits"), "fs_hits": (chk.get("fs") or {}).get("fs_hits")}}
    record["problems"] = ([error] if error else []) + judge(record)
    (base / "record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True, default=list) + "\n").encode("utf-8"))
    return record


# ── a3-discovery: metadata only ───────────────────────────────────────────

META_MAX = 32 * 1024 * 1024


def hf_repos(pins: dict) -> list[tuple[str, str]]:
    """(kind, repo) for every HF pin, kind 'datasets' or 'models', in a stable order."""
    out = []
    for p in pins.values():
        if p["source"] in ("hf-dataset", "hf-model") and p["repo"]:
            kr = ("datasets" if p["source"] == "hf-dataset" else "models", p["repo"])
            if kr not in out:
                out.append(kr)
    return out


def _safe(repo: str) -> str:
    return repo.replace("/", "__")


def discovery_phase1(pins: dict) -> dict:
    """The current revision of every HF repo."""
    return {"hosts": ["huggingface.co"], "max_redirects": 0, "requests": [
        {"id": f"rev:{k}:{r}", "url": f"https://huggingface.co/api/{k}/{r}/revision/main",
         "save": f"meta/{k}/{_safe(r)}/revision.json", "max_bytes": META_MAX} for k, r in hf_repos(pins)]}


def _read_prev(results: list, phase_index: int, rel: str):
    p = Path(results[phase_index]["unit"]) / rel
    return json.loads(p.read_bytes()) if p.is_file() else None


def discovery_phase2(pins: dict):
    """Each HF repo's recursive tree and its card, at the revision phase 1 found."""
    def build(results):
        reqs = []
        for k, r in hf_repos(pins):
            rev = _read_prev(results, 0, f"meta/{k}/{_safe(r)}/revision.json") or {}
            sha = rev.get("sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                continue
            prefix = "datasets/" if k == "datasets" else ""
            reqs.append({"id": f"tree:{k}:{r}", "url": f"https://huggingface.co/api/{k}/{r}/tree/{sha}?recursive=true",
                         "save": f"meta/{k}/{_safe(r)}/tree.json", "max_bytes": META_MAX})
            reqs.append({"id": f"card:{k}:{r}", "url": f"https://huggingface.co/{prefix}{r}/raw/{sha}/README.md",
                         "save": f"meta/{k}/{_safe(r)}/README.md", "max_bytes": META_MAX})
        return {"hosts": ["huggingface.co"], "max_redirects": 0, "requests": reqs}
    return build


def github_candidates(card_texts: list[str], cap: int = CARD_LINK_CAP) -> tuple[list[str], list[str], list[str]]:
    """(requested, skipped, invalid): the named repositories, then the github.com/<owner>/<repo> links the cards carry,
    in card order, deduplicated, at most ``cap`` of them; the valid links beyond the cap, and the links whose name
    breaks GitHub's rules, are returned by name, not requested."""
    linked: list[str] = []
    invalid: list[str] = []
    for text in card_texts:
        for owner, repo in _GH_LINK.findall(text):
            name = f"{owner}/{repo.removesuffix('.git')}"
            if name in GITHUB_NAMED or name in linked or name in invalid:
                continue
            (linked if gh_name_ok(name) else invalid).append(name)
    return [*GITHUB_NAMED, *linked[:cap]], linked[cap:], invalid


def discovery_phase3(pins: dict):
    """HEAD on each HF repo's LFS files to learn the redirect host (never followed); GitHub repo metadata and the
    default branch's head commit for the named and card-linked repositories."""
    def build(results):
        reqs, cards = [], []
        for k, r in hf_repos(pins):
            d = Path(results[1]["unit"]) / "meta" / k / _safe(r)
            rev = _read_prev(results, 0, f"meta/{k}/{_safe(r)}/revision.json") or {}
            sha = rev.get("sha")
            tree = json.loads((d / "tree.json").read_bytes()) if (d / "tree.json").is_file() else []
            if (d / "README.md").is_file():
                cards.append((d / "README.md").read_text(encoding="utf-8", errors="replace"))
            lfs = [e["path"] for e in tree if isinstance(e, dict) and e.get("type") == "file" and e.get("lfs")]
            prefix = "datasets/" if k == "datasets" else ""
            for path in lfs[:50]:
                reqs.append({"id": f"head:{k}:{r}:{path}", "method": "HEAD",
                             "url": f"https://huggingface.co/{prefix}{r}/resolve/{sha}/{path}"})
        requested, skipped, invalid = github_candidates(cards)
        for name in requested:
            reqs.append({"id": f"gh:{name}", "url": f"https://api.github.com/repos/{name}",
                         "save": f"gh/{_safe(name)}/repo.json", "max_bytes": META_MAX})
            reqs.append({"id": f"ghhead:{name}", "url": f"https://api.github.com/repos/{name}/commits/HEAD",
                         "save": f"gh/{_safe(name)}/head.json", "max_bytes": META_MAX})
        return {"hosts": ["huggingface.co", "api.github.com"], "max_redirects": 0, "requests": reqs,
                "card_links_skipped": skipped, "card_links_invalid": invalid}
    return build


def discovery_phase4():
    """Each GitHub repository's recursive tree at the head commit phase 3 found."""
    def build(results):
        reqs = []
        gh = Path(results[2]["unit"]) / "gh"
        for d in sorted(gh.iterdir()) if gh.is_dir() else []:
            head = json.loads((d / "head.json").read_bytes()) if (d / "head.json").is_file() else {}
            sha = head.get("sha")
            name = d.name.replace("__", "/", 1)
            if gh_name_ok(name) and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
                reqs.append({"id": f"ghtree:{name}", "url": f"https://api.github.com/repos/{name}/git/trees/{sha}?recursive=1",
                             "save": f"gh/{d.name}/tree.json", "max_bytes": META_MAX})
        return {"hosts": ["api.github.com"], "max_redirects": 0, "requests": reqs} if reqs else None
    return build


def discovery_jobs(pins: dict) -> list:
    return [discovery_phase1(pins), discovery_phase2(pins), discovery_phase3(pins), discovery_phase4()]


# ── a3-discovery plan d2 (the auditor's P1 + P10): api.github.com only, at most 12 requests ──

D2_HOSTS = ["api.github.com"]
AMA_HUB = "AMA-Bench/AMA-Hub"
MEM0 = "mem0ai/mem0"
#: The auditor's P4 naming: BEAM's official code, linked from the paper itself (arXiv:2510.27246 v2, footnote 1).
BEAM_GH = "mohammadtavakoli78/BEAM"
_SHA = re.compile(r"[0-9a-f]{40}")


def d2_phase_a() -> dict:
    """AMA-Hub's repository and head (one redirect allowed, and only to api.github.com: the card's link moved), and
    the newest commits of mem0 that touch evaluation/ (the Mem0 paper's J prompt left mem0's main)."""
    return {"hosts": D2_HOSTS, "max_redirects": 1, "requests": [
        {"id": f"gh:{AMA_HUB}", "url": f"https://api.github.com/repos/{AMA_HUB}", "save": f"gh/{_safe(AMA_HUB)}/repo.json",
         "max_bytes": META_MAX},
        {"id": f"ghhead:{AMA_HUB}", "url": f"https://api.github.com/repos/{AMA_HUB}/commits/HEAD",
         "save": f"gh/{_safe(AMA_HUB)}/head.json", "max_bytes": META_MAX},
        {"id": f"ghcommits:{MEM0}:evaluation", "url": f"https://api.github.com/repos/{MEM0}/commits?path=evaluation&per_page=5",
         "save": f"gh/{_safe(MEM0)}/evaluation_commits.json", "max_bytes": META_MAX},
        {"id": f"gh:{BEAM_GH}", "url": f"https://api.github.com/repos/{BEAM_GH}", "save": f"gh/{_safe(BEAM_GH)}/repo.json",
         "max_bytes": META_MAX},
        {"id": f"ghhead:{BEAM_GH}", "url": f"https://api.github.com/repos/{BEAM_GH}/commits/HEAD",
         "save": f"gh/{_safe(BEAM_GH)}/head.json", "max_bytes": META_MAX}]}


def _newest_mem0(results) -> dict | None:
    commits = _read_prev(results, 0, f"gh/{_safe(MEM0)}/evaluation_commits.json")
    return commits[0] if isinstance(commits, list) and commits and isinstance(commits[0], dict) else None


def d2_phase_b():
    """AMA-Hub's tree at its head, under the name the redirect led to; the tree at the newest mem0 commit found."""
    def build(results):
        reqs = []
        repo = _read_prev(results, 0, f"gh/{_safe(AMA_HUB)}/repo.json") or {}
        head = _read_prev(results, 0, f"gh/{_safe(AMA_HUB)}/head.json") or {}
        name, sha = repo.get("full_name"), head.get("sha")
        if gh_name_ok(name) and isinstance(sha, str) and _SHA.fullmatch(sha):
            reqs.append({"id": f"ghtree:{name}", "url": f"https://api.github.com/repos/{name}/git/trees/{sha}?recursive=1",
                         "save": f"gh/{_safe(AMA_HUB)}/tree.json", "max_bytes": META_MAX})
        beam_head = (_read_prev(results, 0, f"gh/{_safe(BEAM_GH)}/head.json") or {}).get("sha")
        if isinstance(beam_head, str) and _SHA.fullmatch(beam_head):
            reqs.append({"id": f"ghtree:{BEAM_GH}", "url": f"https://api.github.com/repos/{BEAM_GH}/git/trees/{beam_head}?recursive=1",
                         "save": f"gh/{_safe(BEAM_GH)}/tree.json", "max_bytes": META_MAX})
        newest = _newest_mem0(results)
        if newest and isinstance(newest.get("sha"), str) and _SHA.fullmatch(newest["sha"]):
            reqs.append({"id": f"ghtree:{MEM0}@newest", "url": f"https://api.github.com/repos/{MEM0}/git/trees/{newest['sha']}?recursive=1",
                         "save": f"gh/{_safe(MEM0)}/tree_newest.json", "max_bytes": META_MAX})
        return {"hosts": D2_HOSTS, "max_redirects": 0, "requests": reqs} if reqs else None
    return build


def holds_evaluation(tree: dict | None) -> bool:
    return any(isinstance(e, dict) and str(e.get("path", "")).startswith("evaluation/") for e in (tree or {}).get("tree") or [])


def d2_phase_c():
    """If the newest mem0 commit's tree no longer holds evaluation/ (it is the deletion), the tree at its first
    parent - the newest tree that still holds it. Otherwise nothing."""
    def build(results):
        newest = _newest_mem0(results)
        tree = _read_prev(results, 1, f"gh/{_safe(MEM0)}/tree_newest.json") if len(results) > 1 else None
        if not newest or holds_evaluation(tree):
            return None
        parents = newest.get("parents") or []
        parent = parents[0].get("sha") if parents and isinstance(parents[0], dict) else None
        if not (isinstance(parent, str) and _SHA.fullmatch(parent)):
            return None
        return {"hosts": D2_HOSTS, "max_redirects": 0, "requests": [
            {"id": f"ghtree:{MEM0}@parent", "url": f"https://api.github.com/repos/{MEM0}/git/trees/{parent}?recursive=1",
             "save": f"gh/{_safe(MEM0)}/tree_parent.json", "max_bytes": META_MAX}]}
    return build


def d2_jobs() -> list:
    return [d2_phase_a(), d2_phase_b(), d2_phase_c()]


def d2_report(record: dict) -> dict:
    """Names only: AMA-Hub's new name, head and tree size; mem0's newest evaluation commit, whether its tree holds
    evaluation/, the pinned commit (the newest whose tree holds it) and why, and the evaluation/ file names."""
    units = [Path(j["unit"]) for j in record["jobs"]]
    rd = lambda i, rel: json.loads((units[i] / rel).read_bytes()) if i < len(units) and (units[i] / rel).is_file() else None  # noqa: E731
    repo, head = rd(0, f"gh/{_safe(AMA_HUB)}/repo.json") or {}, rd(0, f"gh/{_safe(AMA_HUB)}/head.json") or {}
    ama_tree = rd(1, f"gh/{_safe(AMA_HUB)}/tree.json") or {}
    commits = rd(0, f"gh/{_safe(MEM0)}/evaluation_commits.json") or []
    newest_tree, parent_tree = rd(1, f"gh/{_safe(MEM0)}/tree_newest.json"), rd(2, f"gh/{_safe(MEM0)}/tree_parent.json")
    newest = commits[0] if commits else {}
    pinned_tree = newest_tree if holds_evaluation(newest_tree) else parent_tree
    pinned = newest.get("sha") if holds_evaluation(newest_tree) else ((newest.get("parents") or [{}])[0].get("sha"))
    beam_repo, beam_head = rd(0, f"gh/{_safe(BEAM_GH)}/repo.json") or {}, rd(0, f"gh/{_safe(BEAM_GH)}/head.json") or {}
    beam_tree = rd(1, f"gh/{_safe(BEAM_GH)}/tree.json") or {}
    return {"beam": {"repo": BEAM_GH, "full_name": beam_repo.get("full_name"),
                     "licence": (beam_repo.get("license") or {}).get("spdx_id"), "head_sha": beam_head.get("sha"),
                     "tree_entries": len(beam_tree.get("tree") or []), "tree_truncated": beam_tree.get("truncated"),
                     "evaluation_files": sorted(e["path"] for e in beam_tree.get("tree") or []
                                                if e.get("type") == "blob" and (e["path"].startswith("src/evaluation/")
                                                                                or e["path"] == "src/prompts.py"))},
            "ama_hub": {"asked": AMA_HUB, "full_name": repo.get("full_name"), "full_name_valid": gh_name_ok(repo.get("full_name")),
                        "licence": (repo.get("license") or {}).get("spdx_id"),
                        "head_sha": head.get("sha"), "tree_entries": len(ama_tree.get("tree") or []),
                        "tree_truncated": ama_tree.get("truncated")},
            "mem0": {"evaluation_commits": [c.get("sha") for c in commits], "newest": newest.get("sha"),
                     "newest_holds_evaluation": holds_evaluation(newest_tree),
                     "pinned_commit": pinned if holds_evaluation(pinned_tree) else None,
                     "why": "the newest commit whose tree still holds evaluation/"
                            + ("" if holds_evaluation(newest_tree) else " - the newest one is the deletion; its first parent"),
                     "evaluation_files": sorted(e["path"] for e in (pinned_tree or {}).get("tree") or []
                                                if str(e.get("path", "")).startswith("evaluation/") and e.get("type") == "blob")}}


# ── the command line ──────────────────────────────────────────────────────

def _load(name: str, path: Path):
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the A3 fetch windows (children under the contract)")
    ap.add_argument("--window", required=True, choices=["a3-discovery"])
    ap.add_argument("--plan", default="d1", choices=["d1", "d2"], help="d1: the full discovery; d2: the P1/P10 follow-up")
    ap.add_argument("--run", required=True)
    ap.add_argument("--python", required=True, help="the polygon's py314 interpreter")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    CP = _load("v3_corpus_pin", HERE / "corpus_pin_v3.py")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    c = L.Contract.default()
    via = L.network_via_port(c)
    if via is None:
        print("no declared hop (network.json)", file=sys.stderr)
        return 2
    win = manifest["windows"][args.window]
    floor = manifest["disk"]["floor_gb"] * GB
    hosts, jobs = (win["hosts"], discovery_jobs(CP.PINS)) if args.plan == "d1" else (D2_HOSTS, d2_jobs())
    rec = run_child_window(c, L, window=args.window, hosts=hosts, jobs=jobs,
                           python=Path(args.python), via_port=via, run=args.run, parent_env=os.environ,
                           need_bytes=floor, volume=Path("D:/"))
    if args.plan == "d2":
        print(json.dumps(d2_report(rec), indent=1))
    print(json.dumps({"problems": rec["problems"], "check": rec["check"], "jobs": [
        {"index": j["index"], "rc": j["rc"], "requests": len(j["summary"]),
         "ok": sum(1 for r in j["summary"] if r.get("ok"))} for j in rec["jobs"]],
        "tunnelled_hosts": sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")}),
        "issuers": rec["issuers"]}, indent=1, default=list))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
