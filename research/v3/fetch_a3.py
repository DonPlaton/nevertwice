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
   {"child": "git", "exe", "argv", "env"} runs git so (A3.g); a job's own "timeout_s" bounds it (Q-A3F-11);
5. closes the window and the check, shuts the proxy down, and writes one record: the jobs and their summaries (hashes,
   redirect hosts, peer issuers), the catcher's host log, the window log, the check, and the problems found.

`a3-discovery` fetches metadata only (HF revisions, trees and cards; GitHub repositories, head commits and trees) and
sends HEADs to resolve URLs to learn their redirect hosts, following none. Its record goes to the auditor, who fixes
the CDN hosts (Q-A3-3) and the prompt/scoring repos before any data window runs.

`a7-docs` (plan d4, the auditor's Q-A8-10) reads the documentation paths its manifest entry names as text through the
contents API at a release tag's commit - a path absent there at the head, marked - and the release's asset metadata;
each text is checked against the git blob its answer names before it is written. `a7-cognee-tag` (plan d5, the
auditor's R2) asks which commit a tag names and, only when it is the declared one, reads that commit's tree.
`a7-arxiv` (plan d6, the auditor's R3) asks the arXiv API one declared query and lists what it finds, as text.

    python research/v3/fetch_a3.py --window a3-discovery --run d1 --python D:\\Coding\\_nevertwice_polygon\\py314\\python.exe
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
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


def _jsonl(path: Path, bad: list | None = None) -> list[dict]:
    """A JSONL log, line by line. F-P2-6: an unparseable line is skipped and named in ``bad`` (when given) - the
    window's record is still written, and judge() names it - instead of crashing the window."""
    out = []
    if not path.is_file():
        return out
    lines = path.read_bytes().decode("utf-8", "replace").split("\n")   # LF only: never splitlines() (U+2028, A6 j4)
    if lines and lines[-1] == "":
        lines.pop()
    for n, line in enumerate(lines, 1):
        try:
            out.append(json.loads(line))
        except ValueError:
            if bad is None:
                raise
            bad.append(f"{path.name} line {n} does not parse")
    return out


#: F-P2-6: how long the harness waits, after the window, for the catcher's open connections to reach 0.
TUNNEL_DRAIN_S = 5.0


def drained_counters(port: int, token: str, *, wait_s: float | None = None) -> tuple[dict | None, dict | None]:
    """(the catcher's counters once no connection is open, None) - or (the last counters read, the arms still open)
    when ``wait_s`` passes first. A connection's log line is written when it closes, so only then may the counts be
    compared with the log."""
    deadline = time.monotonic() + (TUNNEL_DRAIN_S if wait_s is None else wait_s)
    while True:
        ctr = _control_json(port, token, "/counters")
        if ctr is not None:
            still = {a: c.get("catcher_open") for a, c in ctr.items() if c.get("catcher_open") != 0}
            if not still:
                return ctr, None
        else:
            still = None
        if time.monotonic() >= deadline:
            return ctr, still
        time.sleep(0.1)


def _control_json(port: int, token: str, path: str) -> dict | None:
    """A control endpoint's JSON body, or None."""
    try:
        raw = _control(port, token, path, {})
        return json.loads(raw.split(b"\r\n\r\n", 1)[1]) if raw.startswith(b"HTTP/1.1 200") else None
    except (OSError, ValueError, IndexError):
        return None


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
    for b in record.get("log_problems") or []:
        problems.append(f"a window log is not whole: {b}")
    tunnelled = {c.get("host") for c in record["catcher"] if c.get("tunnelled")}
    bypass = sorted({r["final_host"] for j in record["jobs"] for r in j["summary"]
                     if r.get("ok") and r.get("final_host") and r["final_host"] not in tunnelled})
    if bypass:
        problems.append(f"a request succeeded on {bypass} with no catcher tunnel - past the catcher")
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
    # A window root's non-loopback connection is filed by the witness under window_hosts, not hits (launch.py): the
    # harness-fetch windows meant it. Every A3 fetch is a child through the loopback catcher (O1), so any remote a
    # child dialled itself went past the catcher - a problem; an unknown list is one too.
    if chk.get("window_hosts") != []:
        problems.append(f"a window root dialled past the catcher: {chk.get('window_hosts')}")
    return problems


def run_child_window(c, L, *, window: str, hosts: list[str], jobs: list, python: Path, via_port: int, run: str,
                     parent_env, native=None, fs=None, child_env_extra: dict | None = None, need_bytes: int = 0,
                     volume: Path | None = None, job_timeout: float = 3600.0, arm: str = ARM) -> dict:
    """One declared window, its jobs as fetch children on ``arm`` (the catcher's arm; a3-git has its own). ``jobs``:
    dicts, or callables(results so far) -> dict. ``child_env_extra`` is for tests only (a CA file for the fake TLS
    server); a real window passes none."""
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
                                     "catchers": [arm]}, sort_keys=True).encode("utf-8"))
    token = "ctl-" + _secrets.token_hex(24)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    check_id = f"fetch-{window}-{run}"
    W.begin_check(check_id, tags={"window": window, "run": run, "arm": arm})
    results: list[dict] = []
    proxy = None
    error = None
    try:
        proxy, ports = L.spawn_proxy(c, python, script=PROXY_SCRIPT, config_path=cfg_path,
                                     stdin_secrets={"control_token": token}, unit=proxy_unit, parent_env=parent_env,
                                     witnesses=W, catcher_only=True)
        catcher = f"http://127.0.0.1:{ports['arms'][arm]['catcher']}"

        def proxy_control(action: str, name: str, win_hosts) -> None:
            body = {"name": name, "state": "open" if action == "open" else "close"}
            if action == "open":
                body.update(hosts=list(win_hosts), arms=[arm])
            got = _control(ports["control"], token, "/window", body)
            if not got.startswith(b"HTTP/1.1 200"):
                raise WindowRefused(f"the catcher refused the window {action}")

        with L.fetch_window(c, window, hosts, witnesses=W, proxy_control=proxy_control,
                            via=f"127.0.0.1:{via_port}") as win:
            for i, job in enumerate(jobs):
                spec = job(results) if callable(job) else job
                if spec is None:
                    continue
                unit = L.make_unit_dirs(c, stand, run, arm, f"j{i}")
                mode = spec.get("child")
                pip = mode in ("pip", "git")                    # a program of its own, not the fetch child
                exe = Path(spec["python"] if mode == "pip" else spec["exe"]) if pip else Path(python)
                argv = [os.fspath(exe), *spec["argv"]] if pip else [os.fspath(python), os.fspath(FETCH_CHILD)]
                declared = {**(spec.get("env") or {}), **(child_env_extra or {})} if pip else dict(child_env_extra or {})
                env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[exe.parent], declared=declared,
                                  catcher_url=catcher)
                child = L.spawn(c, argv, env=env, cwd=unit.cwd,
                                record={"role": "fetch", "stand": None, "run": f"{window}.{run}", "arm": arm,
                                        "unit": f"j{i}"},
                                parent_env=parent_env, catcher_url=catcher,
                                argv_exception={} if pip else {1: FETCH_CHILD},
                                witnesses=W, requirement="required", window=win,
                                stdin=subprocess.DEVNULL if pip else subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                # The job goes in through communicate(input=...), never a hand-written stdin closed before
                # communicate(): on POSIX before 3.13 that flushes a closed file (ValueError - CI 76cb0e9, class B3).
                payload = None if pip else (json.dumps(spec) + "\n").encode("utf-8")
                try:
                    out, err = child.process.communicate(input=payload, timeout=spec.get("timeout_s", job_timeout))
                    rc = child.process.returncode
                except subprocess.TimeoutExpired:
                    child.kill_tree()
                    rc, out, err = None, b"", b""
                if pip:
                    summary = [{"id": mode, "ok": rc == 0,
                                "error": None if rc == 0 else f"{mode} exited with {rc}: "
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
        counters = still_open = None
        if proxy is not None:
            counters, still_open = drained_counters(ports["control"], token)
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
    log_bad: list[str] = []
    record = {"window": window, "run": run, "arm": arm, "hosts": list(hosts), "via": {"host": "127.0.0.1", "port": via_port},
              "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "error": error, "jobs": results,
              "catcher": _jsonl(pdir / "catcher.jsonl", log_bad), "windows_proxy": _jsonl(pdir / "windows_proxy.jsonl", log_bad),
              "issuers": sorted({(r.get("final_host"), r.get("issuer_o"), r.get("issuer_cn")) for j in results
                                 for r in j["summary"] if r.get("issuer_cn")}),
              "check": {"id": check_id, "complete": chk.get("complete"), "native_hits": native_rec.get("hits"),
                        "loopback_hits": native_rec.get("loopback_hits"), "fs_hits": (chk.get("fs") or {}).get("fs_hits"),
                        "window_hosts": sorted(native_rec["window_hosts"]) if "window_hosts" in native_rec else None}}
    # the catcher's own count of the CONNECTs it saw against the lines its log holds (F-P2-6's "should"), once no
    # connection is open (each line is written when its connection closes)
    for a, ctr in sorted((counters or {}).items() if not still_open else []):
        n_log = sum(1 for x in record["catcher"] if x.get("arm") == a)
        if len(ctr.get("catcher_hosts") or []) != n_log:
            log_bad.append(f"catcher.jsonl holds {n_log} line(s) for arm {a}, the catcher counted "
                           f"{len(ctr.get('catcher_hosts') or [])}")
    if proxy is not None and counters is None:
        log_bad.append("the catcher's counters could not be read")
    elif still_open:
        log_bad.append(f"catcher connections still open {TUNNEL_DRAIN_S:g} s after the window: {still_open}")
    record["log_problems"] = log_bad
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


def _unit_of(results: list, job_index: int) -> Path | None:
    """The unit directory of the job at ``job_index`` in its jobs list, None when that job did not run. B-D3IDX: a
    builder that returns None leaves no result, so a result's list position is not its job index - its "index" is."""
    for pos, r in enumerate(results):
        if r.get("index", pos) == job_index:
            return Path(r["unit"])
    return None


def _read_prev(results: list, phase_index: int, rel: str):
    u = _unit_of(results, phase_index)
    p = u / rel if u is not None else None
    return json.loads(p.read_bytes()) if p is not None and p.is_file() else None


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
    rd = lambda i, rel: _read_prev(record["jobs"], i, rel)  # noqa: E731 - by job index (B-D3IDX)
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


# ── a7-discovery plan d3 (.loop/A7-PLAN-2026-09-27.md, accepted with the auditor's two additions) ─────────────

D3_GH_HOSTS = ["api.github.com"]
D3_HF_HOSTS = ["huggingface.co"]
D3_HOSTS = sorted(D3_GH_HOSTS + D3_HF_HOSTS)
#: The repositories A7 reads: Supermemory's self-hosting documentation (Q-47-8g), cognee's BEAM harness (Q-46b-6), the
#: Zep LME template (Q-46b-4; the name is ours - a 404 is named, never guessed around).
D3_REPOS = ("supermemoryai/supermemory", "topoteretes/cognee", "getzep/zep-papers")
#: The auditor's addition 2: a harness must match the PINNED product version, so its tags and releases are read too.
D3_TAGGED = ("supermemoryai/supermemory", "topoteretes/cognee")
D3_TAGS_PAGE, D3_RELEASES_PAGE = 100, 30         # one page each; a full page is flagged (B-D3P)
#: Q-49-3 O-b: BEAM's event-ordering alignment model.
D3_HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
#: Candidate paths, by what each repository is read for - names the auditor fixes before phase 2 (text, never fetched).
D3_CANDIDATES = {"supermemoryai/supermemory": re.compile(r"(^|/)readme\.md$|self[-_ ]?host|docker|deploy", re.I),
                 "topoteretes/cognee": re.compile(r"beam", re.I),
                 "getzep/zep-papers": re.compile(r"longmemeval|(^|[/_-])lme([/_.-]|$)", re.I)}


def d3_phase_a() -> dict:
    """Each repository and its head; the tagged ones' tags and releases (api.github.com; one redirect, to itself)."""
    reqs = []
    for r in D3_REPOS:
        reqs += [{"id": f"gh:{r}", "url": f"https://api.github.com/repos/{r}", "save": f"gh/{_safe(r)}/repo.json",
                  "max_bytes": META_MAX},
                 {"id": f"ghhead:{r}", "url": f"https://api.github.com/repos/{r}/commits/HEAD",
                  "save": f"gh/{_safe(r)}/head.json", "max_bytes": META_MAX}]
        if r in D3_TAGGED:
            reqs += [{"id": f"ghtags:{r}", "url": f"https://api.github.com/repos/{r}/tags?per_page={D3_TAGS_PAGE}",
                      "save": f"gh/{_safe(r)}/tags.json", "max_bytes": META_MAX},
                     {"id": f"ghreleases:{r}", "url": f"https://api.github.com/repos/{r}/releases?per_page={D3_RELEASES_PAGE}",
                      "save": f"gh/{_safe(r)}/releases.json", "max_bytes": META_MAX}]
    return {"hosts": D3_GH_HOSTS, "max_redirects": 1, "requests": reqs}


def d3_phase_b() -> dict:
    """The model's current revision (huggingface.co)."""
    return {"hosts": D3_HF_HOSTS, "max_redirects": 0, "requests": [
        {"id": f"rev:models:{D3_HF_MODEL}", "url": f"https://huggingface.co/api/models/{D3_HF_MODEL}/revision/main",
         "save": f"meta/models/{_safe(D3_HF_MODEL)}/revision.json", "max_bytes": META_MAX}]}


def d3_phase_c():
    """Each repository's recursive tree at its head, under the name the repository answer gave (api.github.com)."""
    def build(results):
        reqs = []
        for r in D3_REPOS:
            repo = _read_prev(results, 0, f"gh/{_safe(r)}/repo.json") or {}
            head = _read_prev(results, 0, f"gh/{_safe(r)}/head.json") or {}
            name, sha = repo.get("full_name"), head.get("sha")
            if gh_name_ok(name) and isinstance(sha, str) and _SHA.fullmatch(sha):
                reqs.append({"id": f"ghtree:{name}", "url": f"https://api.github.com/repos/{name}/git/trees/{sha}?recursive=1",
                             "save": f"gh/{_safe(r)}/tree.json", "max_bytes": META_MAX})
        return {"hosts": D3_GH_HOSTS, "max_redirects": 0, "requests": reqs} if reqs else None
    return build


def d3_phase_d():
    """The model's tree at the revision phase b found (huggingface.co)."""
    def build(results):
        rev = (_read_prev(results, 1, f"meta/models/{_safe(D3_HF_MODEL)}/revision.json") or {}).get("sha")
        if not (isinstance(rev, str) and _SHA.fullmatch(rev)):
            return None
        return {"hosts": D3_HF_HOSTS, "max_redirects": 0, "requests": [
            {"id": f"tree:models:{D3_HF_MODEL}", "url": f"https://huggingface.co/api/models/{D3_HF_MODEL}/tree/{rev}?recursive=true",
             "save": f"meta/models/{_safe(D3_HF_MODEL)}/tree.json", "max_bytes": META_MAX}]}
    return build


def d3_jobs() -> list:
    return [d3_phase_a(), d3_phase_b(), d3_phase_c(), d3_phase_d()]


def d3_report(record: dict) -> dict:
    """Names only: each repository's name, licence, head, tree size, tags and releases (for the tagged ones) and the
    candidate paths for phase 2; the model's revision and its files. Every value is data from the answers. Tags and
    releases are ONE page each (B-D3P): tags_page_full / releases_page_full say the page was full, so an older pinned
    tag may be past it - phase 2 then asks for the next page or the tag by name, and never reads 'no such version'."""
    rd = lambda i, rel: _read_prev(record["jobs"], i, rel)  # noqa: E731 - by job index: phase c may not run (B-D3IDX)
    status = {r.get("id"): r.get("status") for j in record["jobs"] for r in j["summary"]}
    repos = {}
    for r in D3_REPOS:
        repo, head, tree = rd(0, f"gh/{_safe(r)}/repo.json") or {}, rd(0, f"gh/{_safe(r)}/head.json") or {}, rd(2, f"gh/{_safe(r)}/tree.json") or {}
        entry = {"asked": r, "status": status.get(f"gh:{r}"), "full_name": repo.get("full_name"),
                 "full_name_valid": gh_name_ok(repo.get("full_name")), "licence": (repo.get("license") or {}).get("spdx_id"),
                 "default_branch": repo.get("default_branch"), "head_sha": head.get("sha"),
                 "tree_entries": len(tree.get("tree") or []), "tree_truncated": tree.get("truncated"),
                 "candidates": sorted(e["path"] for e in tree.get("tree") or [] if isinstance(e, dict)
                                      and e.get("type") == "blob" and D3_CANDIDATES[r].search(str(e.get("path", ""))))}
        if r in D3_TAGGED:
            tags = rd(0, f"gh/{_safe(r)}/tags.json") or []
            rels = rd(0, f"gh/{_safe(r)}/releases.json") or []
            entry["tags"] = [{"name": x.get("name"), "commit": (x.get("commit") or {}).get("sha")} for x in tags
                             if isinstance(x, dict)]
            entry["releases"] = [{"tag_name": x.get("tag_name"), "name": x.get("name"), "published_at": x.get("published_at"),
                                  "prerelease": x.get("prerelease")} for x in rels if isinstance(x, dict)]
            entry["tags_page_full"] = len(tags) >= D3_TAGS_PAGE
            entry["releases_page_full"] = len(rels) >= D3_RELEASES_PAGE
        repos[r] = entry
    rev = rd(1, f"meta/models/{_safe(D3_HF_MODEL)}/revision.json") or {}
    mtree = rd(3, f"meta/models/{_safe(D3_HF_MODEL)}/tree.json") or []
    return {"repos": repos, "model": {"repo": D3_HF_MODEL, "revision": rev.get("sha"),
                                      "files": [{"path": e.get("path"), "size": e.get("size"), "oid": e.get("oid"),
                                                 "lfs_sha256": (e.get("lfs") or {}).get("oid")}
                                                for e in mtree if isinstance(e, dict) and e.get("type") == "file"]}}


# ── a7-docs plan d4 (the auditor's Q-A8-10: how the vendor ships supermemory-server 0.0.8) ─────────────────────

D4_WINDOW = "a7-docs"
D4_HOSTS = ["api.github.com"]
D4_KEYS = frozenset({"hosts", "purpose", "repo", "commit", "ref_name", "head", "paths", "release_tag", "max_redirects"})
D4_DOC_MAX = 4 * 1024 * 1024                  # a contents answer: the file base64-encoded inside JSON
_D4_SEG = re.compile(r"[A-Za-z0-9_.-]+")
_D4_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class D4ManifestError(ValueError):
    """The manifest does not declare a7-docs as plan d4 reads it."""


def d4_decl(manifest: dict) -> dict:
    """The manifest's a7-docs entry - the window's single source: exactly D4_KEYS; api.github.com only, no redirect;
    a repository name; the tag's commit and the head as full shas; distinct relative paths of plain segments
    (no '..', no query, no leading '/'); a tag name. Anything else is refused by name before any spawn."""
    w = (manifest.get("windows") or {}).get(D4_WINDOW)
    if not isinstance(w, dict) or set(w) != D4_KEYS:
        raise D4ManifestError(f"the manifest's {D4_WINDOW} entry must have exactly the keys {sorted(D4_KEYS)}")
    probs = []
    if w["hosts"] != D4_HOSTS:
        probs.append(f"its hosts {w['hosts']} are not {D4_HOSTS}")
    if isinstance(w["max_redirects"], bool) or w["max_redirects"] != 0:
        probs.append("its max_redirects is not 0")
    if not gh_name_ok(w["repo"]):
        probs.append(f"its repo {w['repo']!r} is no repository name")
    for k in ("commit", "head"):
        if not (isinstance(w[k], str) and _SHA.fullmatch(w[k])):
            probs.append(f"its {k} is not a full commit sha")
    for k in ("ref_name", "release_tag"):
        if not (isinstance(w[k], str) and _D4_TAG.fullmatch(w[k])):
            probs.append(f"its {k} {w[k]!r} is no tag name")
    paths = w["paths"]
    if not (isinstance(paths, list) and paths and len(set(map(str, paths))) == len(paths)
            and all(isinstance(p, str) and all(_D4_SEG.fullmatch(s) and s not in (".", "..") for s in p.split("/"))
                    for p in paths)):
        probs.append("its paths must be distinct relative paths of plain segments")
    if probs:
        raise D4ManifestError(f"the manifest's {D4_WINDOW} entry: " + "; ".join(probs))
    return dict(w)


def _d4_contents(decl: dict, path: str, ref: str, where: str) -> dict:
    return {"id": f"doc:{where}:{path}", "url": f"https://api.github.com/repos/{decl['repo']}/contents/{path}?ref={ref}",
            "save": f"{where}/{path}.json", "max_bytes": D4_DOC_MAX}


def _job_status(results: list, job_index: int) -> dict:
    """{request id: status} of the job at ``job_index`` (by its index, B-D3IDX), {} when it did not run."""
    for pos, r in enumerate(results):
        if r.get("index", pos) == job_index:
            return {s.get("id"): s.get("status") for s in r.get("summary") or [] if isinstance(s, dict)}
    return {}


def d4_jobs(decl: dict) -> list:
    """Job 1: every path at the tag's commit and the release by its tag (its assets' metadata; no asset is fetched).
    Job 2: only the paths the tag answered 404 for, at the head - None (no job) when every path was found."""
    first = {"hosts": D4_HOSTS, "max_redirects": 0, "requests": [
        *(_d4_contents(decl, p, decl["commit"], "tag") for p in decl["paths"]),
        {"id": f"release:{decl['release_tag']}", "save": "release.json", "max_bytes": META_MAX,
         "url": f"https://api.github.com/repos/{decl['repo']}/releases/tags/{decl['release_tag']}"}]}

    def at_head(results):
        st = _job_status(results, 0)
        missing = [p for p in decl["paths"] if st.get(f"doc:tag:{p}") == 404]
        if not missing:
            return None
        return {"hosts": D4_HOSTS, "max_redirects": 0,
                "requests": [_d4_contents(decl, p, decl["head"], "head") for p in missing]}
    return [first, at_head]


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def d4_report(record: dict, decl: dict) -> tuple[dict, dict]:
    """(the report, {"docs/<tag|head>/<path>": bytes}). Per path: where it was read (the tag, the head - marked - or
    nowhere, each status named), its size, sha256 and the git blob sha1 its answer names, checked against the decoded
    content; a content that is not that blob is a problem and is not returned. The release: its tag, name, date,
    flags and per asset the name, size, digest and type - no download URL. Every value is data from the answers."""
    jobs = record["jobs"]
    st_tag, st_head = _job_status(jobs, 0), _job_status(jobs, 1)
    docs, files, problems = [], {}, []
    for p in decl["paths"]:
        e = {"path": p, "tag_status": st_tag.get(f"doc:tag:{p}"), "head_status": st_head.get(f"doc:head:{p}")}
        where = "tag" if e["tag_status"] == 200 else "head" if e["head_status"] == 200 else None
        e.update(read_at=where, ref={"tag": decl["commit"], "head": decl["head"]}.get(where), marked=where == "head")
        docs.append(e)
        if where is None:
            problems.append(f"{p}: not at the tag ({e['tag_status']}) nor at the head ({e['head_status']})")
            continue
        ans = _read_prev(jobs, 0 if where == "tag" else 1, f"{where}/{p}.json") or {}
        body = None
        if ans.get("type") == "file" and ans.get("encoding") == "base64" and isinstance(ans.get("content"), str):
            try:
                body = base64.b64decode(ans["content"])      # GitHub breaks its base64 into lines
            except (ValueError, binascii.Error):
                body = None
        if body is None:
            problems.append(f"{p}: the answer at the {where} is no base64-encoded file")
            continue
        e.update(size=len(body), sha256=hashlib.sha256(body).hexdigest(), blob_sha=ans.get("sha"),
                 blob_ok=git_blob_sha1(body) == ans.get("sha") and ans.get("size") == len(body))
        if e["blob_ok"]:
            files[f"docs/{where}/{p}"] = body
        else:
            problems.append(f"{p}: the content at the {where} is not the blob its answer names ({ans.get('sha')})")
    rel = _read_prev(jobs, 0, "release.json") or {}
    release = {"status": st_tag.get(f"release:{decl['release_tag']}"), "tag_name": rel.get("tag_name"),
               "name": rel.get("name"), "published_at": rel.get("published_at"), "prerelease": rel.get("prerelease"),
               "draft": rel.get("draft"), "target_commitish": rel.get("target_commitish"),
               "assets": [{"name": a.get("name"), "size": a.get("size"), "digest": a.get("digest"),
                           "content_type": a.get("content_type")} for a in rel.get("assets") or [] if isinstance(a, dict)]}
    if release["status"] != 200 or release["tag_name"] != decl["release_tag"]:
        problems.append(f"the release {decl['release_tag']}: status {release['status']}, tag {release['tag_name']!r}")
    return {"repo": decl["repo"], "commit": decl["commit"], "ref_name": decl["ref_name"], "head": decl["head"], "docs": docs,
            "release": release, "problems": problems}, files


# ── a7-cognee-tag plan d5 (the auditor's R2: cognee's BEAM harness at the tag the pinned product carries) ──────

D5_WINDOW = "a7-cognee-tag"
D5_HOSTS = ["api.github.com"]
D5_KEYS = frozenset({"hosts", "purpose", "repo", "tag", "commit", "prefixes", "files", "max_redirects"})


class D5ManifestError(ValueError):
    """The manifest does not declare a7-cognee-tag as plan d5 reads it."""


def _d5_path_ok(p: object, *, prefix: bool) -> bool:
    """A relative path of plain segments; a prefix ends with '/' (so a near name outside it is never selected)."""
    if not isinstance(p, str) or (prefix and not p.endswith("/")):
        return False
    segs = p[:-1].split("/") if prefix else p.split("/")
    return all(_D4_SEG.fullmatch(s) and s not in (".", "..") for s in segs)


def d5_decl(manifest: dict) -> dict:
    """The manifest's a7-cognee-tag entry - the window's single source: exactly D5_KEYS; api.github.com only, no
    redirect; a repository name, a tag name, the commit the discovery found the tag names (a full sha), and the
    selection - prefixes ending in '/' and single files, plain relative paths, at least one. Else refused by name."""
    w = (manifest.get("windows") or {}).get(D5_WINDOW)
    if not isinstance(w, dict) or set(w) != D5_KEYS:
        raise D5ManifestError(f"the manifest's {D5_WINDOW} entry must have exactly the keys {sorted(D5_KEYS)}")
    probs = []
    if w["hosts"] != D5_HOSTS:
        probs.append(f"its hosts {w['hosts']} are not {D5_HOSTS}")
    if isinstance(w["max_redirects"], bool) or w["max_redirects"] != 0:
        probs.append("its max_redirects is not 0")
    if not gh_name_ok(w["repo"]):
        probs.append(f"its repo {w['repo']!r} is no repository name")
    if not (isinstance(w["tag"], str) and _D4_TAG.fullmatch(w["tag"])):
        probs.append(f"its tag {w['tag']!r} is no tag name")
    if not (isinstance(w["commit"], str) and _SHA.fullmatch(w["commit"])):
        probs.append("its commit is not a full commit sha")
    pre, fil = w["prefixes"], w["files"]
    if not (isinstance(pre, list) and all(_d5_path_ok(p, prefix=True) for p in pre)
            and isinstance(fil, list) and all(_d5_path_ok(p, prefix=False) for p in fil) and (pre or fil)):
        probs.append("its selection must be prefixes ending in '/' and single files, plain relative paths, at least one")
    if probs:
        raise D5ManifestError(f"the manifest's {D5_WINDOW} entry: " + "; ".join(probs))
    return dict(w)


def d5_jobs(decl: dict) -> list:
    """Job 1: the commit the tag names now. Job 2: the recursive tree at the DECLARED commit - only when the tag still
    names it (None, no job, when it moved: the declared commit is never silently replaced)."""
    first = {"hosts": D5_HOSTS, "max_redirects": 0, "requests": [
        {"id": f"tagcommit:{decl['tag']}", "url": f"https://api.github.com/repos/{decl['repo']}/commits/{decl['tag']}",
         "save": "tag_commit.json", "max_bytes": META_MAX}]}

    def tree(results):
        if (_read_prev(results, 0, "tag_commit.json") or {}).get("sha") != decl["commit"]:
            return None
        return {"hosts": D5_HOSTS, "max_redirects": 0, "requests": [
            {"id": f"tree:{decl['commit']}", "save": "tree.json", "max_bytes": META_MAX,
             "url": f"https://api.github.com/repos/{decl['repo']}/git/trees/{decl['commit']}?recursive=1"}]}
    return [first, tree]


def d5_report(record: dict, decl: dict) -> dict:
    """The tag's commit and whether it is the declared one; the tree's size and truncation; the selected blobs (path,
    blob sha1, size) in path order; the declared single files the tag does not hold. A moved tag, a missing or a
    truncated tree are problems by name. Every value is data from the answers."""
    jobs = record["jobs"]
    got = (_read_prev(jobs, 0, "tag_commit.json") or {}).get("sha")
    tree = _read_prev(jobs, 1, "tree.json") or {}
    blobs = {e["path"]: e for e in tree.get("tree") or []
             if isinstance(e, dict) and e.get("type") == "blob" and isinstance(e.get("path"), str)}
    matches = got == decl["commit"]
    problems = []
    if not matches:
        problems.append(f"the tag {decl['tag']} names {got}, not the declared {decl['commit']} - no tree read")
    elif not tree:
        problems.append(f"no tree at {decl['commit']}")
    if tree.get("truncated"):
        problems.append(f"the tree at {decl['commit']} is truncated - a selection from part of a tree is none")
    chosen = sorted(p for p in blobs if any(p.startswith(x) for x in decl["prefixes"]) or p in decl["files"])
    return {"repo": decl["repo"], "tag": decl["tag"], "commit": decl["commit"], "tag_commit": got, "tag_matches": matches,
            "tree_entries": len(tree.get("tree") or []), "tree_truncated": tree.get("truncated"),
            "selected": [{"path": p, "blob": blobs[p].get("sha"), "size": blobs[p].get("size")} for p in chosen]
            if matches else [],
            "missing_files": [f for f in decl["files"] if f not in blobs] if matches else list(decl["files"]),
            "problems": problems}


# ── a7-arxiv plan d6 (the auditor's R3: Zep's LME template from its paper - found by a query, never from memory) ──

D6_WINDOW = "a7-arxiv"
D6_HOSTS = ["export.arxiv.org"]
D6_KEYS = frozenset({"hosts", "purpose", "search_query", "max_results", "max_redirects"})
D6_MAX = 2 * 1024 * 1024                         # one Atom page of at most 100 entries
_D6_QUERY = re.compile(r"[A-Za-z0-9:_ \"()+.-]{1,200}")
_D6_ID = re.compile(r"arxiv\.org/abs/(.+?)v(\d+)$")
_ATOM = {"a": "http://www.w3.org/2005/Atom", "os": "http://a9.com/-/spec/opensearch/1.1/"}


class D6ManifestError(ValueError):
    """The manifest does not declare a7-arxiv as plan d6 reads it."""


def d6_decl(manifest: dict) -> dict:
    """The manifest's a7-arxiv entry - the window's single source: exactly D6_KEYS; export.arxiv.org only, no redirect;
    a search query of plain query characters (no '&', '?', '#' or '%': the query cannot smuggle a parameter); a page
    of 1 to 100. Anything else is refused by name before any spawn."""
    w = (manifest.get("windows") or {}).get(D6_WINDOW)
    if not isinstance(w, dict) or set(w) != D6_KEYS:
        raise D6ManifestError(f"the manifest's {D6_WINDOW} entry must have exactly the keys {sorted(D6_KEYS)}")
    probs = []
    if w["hosts"] != D6_HOSTS:
        probs.append(f"its hosts {w['hosts']} are not {D6_HOSTS}")
    if isinstance(w["max_redirects"], bool) or w["max_redirects"] != 0:
        probs.append("its max_redirects is not 0")
    if not (isinstance(w["search_query"], str) and _D6_QUERY.fullmatch(w["search_query"])):
        probs.append(f"its search_query {w['search_query']!r} is not a plain query")
    n = w["max_results"]
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 100:
        probs.append(f"its max_results {n!r} is not 1 to 100")
    if probs:
        raise D6ManifestError(f"the manifest's {D6_WINDOW} entry: " + "; ".join(probs))
    return dict(w)


def d6_jobs(decl: dict) -> list:
    """One GET: the API's query with the declared search, one page, oldest first. No link is followed."""
    q = urllib.parse.quote(decl["search_query"], safe=":")
    return [{"hosts": D6_HOSTS, "max_redirects": 0, "requests": [
        {"id": "arxiv:query", "save": "arxiv_query.xml", "max_bytes": D6_MAX,
         "url": f"https://export.arxiv.org/api/query?search_query={q}&start=0&max_results={decl['max_results']}"
                "&sortBy=submittedDate&sortOrder=ascending"}]}]


def d6_report(record: dict, decl: dict) -> dict:
    """The API's total, whether the page was full, and per entry its arXiv id and version, title (white space
    collapsed), published and updated dates, authors and abstract-page link - text for the auditor to choose from.
    An answer that declares a DOCTYPE (entities: never expanded here) or does not parse yields no entry, by name."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    unit = _unit_of(record["jobs"], 0)
    f = unit / "arxiv_query.xml" if unit is not None else None
    raw = f.read_bytes() if f is not None and f.is_file() else None
    problems, entries, total = [], [], None
    if raw is None:
        problems.append("no answer saved")
    elif b"<!doctype" in raw.lower():
        problems.append("the answer declares a DOCTYPE - refused, no entity is expanded")
    else:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            problems.append(f"the answer does not parse ({e})")
            root = None
        if root is not None:
            t = root.findtext("os:totalResults", namespaces=_ATOM)
            total = int(t) if t is not None and t.strip().isdigit() else None
            for e in root.findall("a:entry", _ATOM):
                m = _D6_ID.search((e.findtext("a:id", default="", namespaces=_ATOM) or "").strip())
                abs_link = next((x.get("href") for x in e.findall("a:link", _ATOM) if x.get("rel") == "alternate"), None)
                entries.append({"id": m.group(1) if m else None, "version": int(m.group(2)) if m else None,
                                "title": " ".join((e.findtext("a:title", default="", namespaces=_ATOM) or "").split()),
                                "published": e.findtext("a:published", namespaces=_ATOM),
                                "updated": e.findtext("a:updated", namespaces=_ATOM),
                                "authors": [a.findtext("a:name", namespaces=_ATOM) for a in e.findall("a:author", _ATOM)],
                                "abs": abs_link})
    return {"search_query": decl["search_query"], "max_results": decl["max_results"], "total": total,
            "page_full": len(entries) >= decl["max_results"], "entries": entries, "problems": problems}


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
    ap.add_argument("--window", required=True, choices=["a3-discovery", "a7-discovery", D4_WINDOW, D5_WINDOW, D6_WINDOW])
    ap.add_argument("--plan", default="d1", choices=["d1", "d2", "d3", "d4", "d5", "d6"],
                    help="d1: the full discovery; d2: the P1/P10 follow-up; d3: the A7 discovery (window a7-discovery); "
                         "d4: supermemory's self-hosting documentation at the release tag (window a7-docs); "
                         "d5: cognee's tree at the tag the pinned product carries (window a7-cognee-tag); "
                         "d6: the arXiv entries a declared query finds (window a7-arxiv)")
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
    for plan, window in (("d6", D6_WINDOW), ("d5", D5_WINDOW), ("d4", D4_WINDOW), ("d3", "a7-discovery")):
        if (args.plan == plan) != (args.window == window):
            print(f"plan {plan} runs in window {window}, and only it", file=sys.stderr)
            return 2
    decl = None
    if args.plan in ("d4", "d5", "d6"):
        try:
            decl = {"d4": d4_decl, "d5": d5_decl, "d6": d6_decl}[args.plan](manifest)
        except (D4ManifestError, D5ManifestError, D6ManifestError) as e:
            print(str(e), file=sys.stderr)
            return 2
    win = manifest["windows"][args.window]
    floor = manifest["disk"]["floor_gb"] * GB
    if args.plan == "d3" and sorted(win["hosts"]) != D3_HOSTS:
        print(f"the manifest's a7-discovery hosts {win['hosts']} are not the plan's {D3_HOSTS}", file=sys.stderr)
        return 2
    hosts = {"d1": win["hosts"], "d2": D2_HOSTS, "d3": D3_HOSTS, "d4": D4_HOSTS, "d5": D5_HOSTS, "d6": D6_HOSTS}[args.plan]
    jobs = (discovery_jobs(CP.PINS) if args.plan == "d1" else d2_jobs() if args.plan == "d2"
            else d3_jobs() if args.plan == "d3" else {"d4": d4_jobs, "d5": d5_jobs, "d6": d6_jobs}[args.plan](decl))
    rec = run_child_window(c, L, window=args.window, hosts=hosts, jobs=jobs,
                           python=Path(args.python), via_port=via, run=args.run, parent_env=os.environ,
                           need_bytes=floor, volume=Path("D:/"))
    if args.plan == "d2":
        print(json.dumps(d2_report(rec), indent=1))
    if args.plan == "d3":
        report = d3_report(rec)
        (c.runs_root / "_fetch" / args.window / args.run / "d3_report.json").write_bytes(
            (json.dumps(report, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(json.dumps(report, indent=1))
    if args.plan == "d4":
        report, files = d4_report(rec, decl)
        out = c.runs_root / "_fetch" / args.window / args.run
        for rel, data in files.items():                 # the verified texts only (their blob sha1 checked)
            dst = out.joinpath(*rel.split("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        (out / "d4_report.json").write_bytes((json.dumps(report, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(json.dumps(report, indent=1))
    if args.plan in ("d5", "d6"):
        report = (d5_report if args.plan == "d5" else d6_report)(rec, decl)
        (c.runs_root / "_fetch" / args.window / args.run / f"{args.plan}_report.json").write_bytes(
            (json.dumps(report, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(json.dumps(report, indent=1))
    print(json.dumps({"problems": rec["problems"], "check": rec["check"], "jobs": [
        {"index": j["index"], "rc": j["rc"], "requests": len(j["summary"]),
         "ok": sum(1 for r in j["summary"] if r.get("ok"))} for j in rec["jobs"]],
        "tunnelled_hosts": sorted({x["host"] for x in rec["catcher"] if x.get("tunnelled")}),
        "issuers": rec["issuers"]}, indent=1, default=list))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
