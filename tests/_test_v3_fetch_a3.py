#!/usr/bin/env python3
"""PREREG-V3 plan step A3 (harness): research/v3/fetch_a3.py, offline, with real processes under the contract.

The a3-discovery window runs end to end: the catcher-only proxy (a real process, no key), the window opened in
windows.jsonl (hosts + the declared hop) and in the catcher (exact hosts, arm "fetch"), four fetch-child jobs (real
processes, requirement "required", the child script the only read exception) against a fake huggingface.co and
api.github.com behind a fake hop and a local TLS server (a throwaway certificate; the test hands the children its CA
file - the one thing a real window never passes):

* phase 1 finds each HF repo's revision; phase 2 its tree and card at that revision; phase 3 HEADs its LFS files
  (the redirect host recorded, never followed) and reads the GitHub repositories named before discovery plus the ones
  a card links; phase 4 each repository's tree at its head commit;
* the record carries every job's summary, the catcher's log (every host tunnelled through the hop), the window log,
  the issuers and a complete check - and no problem;
* the problems are named: a failing request, a host the window does not hold (the catcher refuses it even when the
  child's job allows it), a used run label and a disk under the floor refuse the window before anything starts;
* judge() over a record, table-tested: egress hits (loopback ones included, counted once), an incomplete check, fs
  hits, a catcher refusal, a request not ok, a child's exit 3 and a rate limit each yield exactly one named problem;
* the auditor's cap: after the four named repositories, at most 12 card-linked ones are requested, in card order;
  the rest are recorded by name in the job, never requested;
* plan d2 (the auditor's P1 + P10): api.github.com only, at most 12 requests - AMA-Hub's moved repository is followed
  through one redirect that stays on api.github.com and its tree read under the new name; mem0's newest commit that
  touches evaluation/ is read, and when its tree no longer holds evaluation/ (the deletion) the tree at its first
  parent is the pin, with the reason recorded; a redirect off api.github.com and a rate limit are named problems.

    python tests/_test_v3_fetch_a3.py
"""
from __future__ import annotations

import importlib.util
import inspect
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


L = _load("v3_launch_fa3", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3", ROOT / "research" / "v3" / "fetch_a3.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_fetch_a3_"))
HF, GH, CDN = "huggingface.co", "api.github.com", "cdn-lfs.hf.co"
made = TF.make_test_cert(TMP / "cert", HF, extra_hosts=(GH, CDN, "evil.example"))
if made is None:
    print("  SKIP the discovery window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a3: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

S_DS, S_MOD, S_GH = "1" * 40, "2" * 40, "3" * 40
PINS = {"ds": {"source": "hf-dataset", "repo": "org/ds"}, "tok": {"source": "hf-model", "repo": "org/model"},
        "gh": {"source": "github", "repo": None}}
J = lambda o: (200, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
routes = {
    "/api/datasets/org/ds/revision/main": J({"sha": S_DS, "cardData": {"license": "mit"}}),
    "/api/models/org/model/revision/main": J({"sha": S_MOD}),
    f"/api/datasets/org/ds/tree/{S_DS}?recursive=true": J([
        {"type": "file", "path": "data.json", "size": 9, "oid": "a" * 40, "lfs": {"oid": "b" * 64, "size": 9}},
        {"type": "file", "path": "README.md", "size": 30, "oid": "c" * 40}]),
    f"/api/models/org/model/tree/{S_MOD}?recursive=true": J([
        {"type": "file", "path": "tokenizer.json", "size": 5, "oid": "d" * 40, "lfs": {"oid": "e" * 64, "size": 5}}]),
    f"/datasets/org/ds/raw/{S_DS}/README.md": (200, [("Content-Type", "text/plain")],
                                               b"# ds\nCode: https://github.com/cardowner/cardrepo\n"),
    f"/org/model/raw/{S_MOD}/README.md": (200, [("Content-Type", "text/plain")], b"# model\n"),
    f"/datasets/org/ds/resolve/{S_DS}/data.json": (302, [("Location", f"https://{CDN}/blob/1")], b""),
    f"/org/model/resolve/{S_MOD}/tokenizer.json": (302, [("Location", f"https://{CDN}/blob/2")], b""),
}
for name in (*F.GITHUB_NAMED, "cardowner/cardrepo"):
    routes[f"/repos/{name}"] = J({"full_name": name, "default_branch": "main", "license": {"spdx_id": "MIT"}})
    routes[f"/repos/{name}/commits/HEAD"] = J({"sha": S_GH})
    routes[f"/repos/{name}/git/trees/{S_GH}?recursive=1"] = J({"sha": S_GH, "tree": [{"path": "x.py", "type": "blob",
                                                                                     "sha": "f" * 40}]})
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


def contract(tag):
    base = TMP / tag
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    return c, base


def window(tag, jobs, hosts=(HF, GH), **kw):
    c, base = contract(tag)
    rec = F.run_child_window(c, L, window="a3-discovery", hosts=list(hosts), jobs=jobs, python=Path(sys.executable),
                             via_port=hop.port, run="d1", parent_env=os.environ,
                             native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                             fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]),
                             child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP, **kw)
    return rec, c, base


print("\n- a3-discovery end to end -")
rec, C, BASE = window("ok", F.discovery_jobs(PINS))
check("the window has no problem", rec["problems"] == [], str(rec["problems"]))
check("four jobs ran, every request ok", len(rec["jobs"]) == 4 and all(j["rc"] == 0 for j in rec["jobs"])
      and all(r["ok"] for j in rec["jobs"] for r in j["summary"]), str([(j["rc"], len(j["summary"])) for j in rec["jobs"]]))
units = [Path(j["unit"]) for j in rec["jobs"]]
check("phase 1 saved each HF repo's revision", json.loads((units[0] / "meta/datasets/org__ds/revision.json").read_bytes())["sha"]
      == S_DS and (units[0] / "meta/models/org__model/revision.json").is_file())
check("phase 2 saved each repo's tree and card at that revision",
      (units[1] / "meta/datasets/org__ds/tree.json").is_file() and (units[1] / "meta/models/org__model/README.md").is_file())
heads = [r for r in rec["jobs"][2]["summary"] if r["id"].startswith("head:")]
check("phase 3 HEADs each LFS file and records the redirect host, following none",
      len(heads) == 2 and all(r["ok"] and r["status"] == 302 and r["redirect_host"] == CDN for r in heads)
      and sum(1 for h in srv.heads if h.startswith(b"HEAD ") and b"/resolve/" in h.split(b"\r\n", 1)[0]) == 2
      and not any(h.startswith(b"GET ") and b"/resolve/" in h.split(b"\r\n", 1)[0] for h in srv.heads)
      and not any(h.startswith(b"GET /blob") or h.startswith(b"HEAD /blob") for h in srv.heads), str(heads))
gh_ids = sorted(r["id"] for r in rec["jobs"][2]["summary"] if r["id"].startswith("gh:"))
check("phase 3 reads the named GitHub repositories and the one a card links",
      gh_ids == sorted(f"gh:{n}" for n in (*F.GITHUB_NAMED, "cardowner/cardrepo")), str(gh_ids))
check("phase 4 reads each repository's tree at its head commit",
      sorted(r["id"] for r in rec["jobs"][3]["summary"]) == sorted(f"ghtree:{n}" for n in (*F.GITHUB_NAMED, "cardowner/cardrepo")))
check("the catcher tunnelled only the window's hosts, every one through the hop",
      {x["host"] for x in rec["catcher"] if x["tunnelled"]} == {HF, GH}
      and all(x["via"] == f"127.0.0.1:{hop.port}" for x in rec["catcher"]), str({(x["host"], x["tunnelled"]) for x in rec["catcher"]}))
check("the window log names the hop and the hosts; the catcher window opened and closed for the arm",
      [w["event"] for w in F._jsonl(C.runs_root / "_launch" / "windows.jsonl")] == ["START", "END"]
      and F._jsonl(C.runs_root / "_launch" / "windows.jsonl")[0]["via"] == f"127.0.0.1:{hop.port}"
      and [w["event"] for w in rec["windows_proxy"]] == ["open", "close"] and rec["windows_proxy"][0]["arms"] == ["fetch"])
check("the issuers are recorded per host", {i[0] for i in rec["issuers"]} == {HF, GH}, str(rec["issuers"]))
check("the check is complete with 0 hits and 0 fs hits",
      rec["check"]["complete"] is True and rec["check"]["native_hits"] == 0 and rec["check"]["fs_hits"] == 0, str(rec["check"]))
check("the record carries the witness's window_hosts - no child dialled past the catcher", rec["check"]["window_hosts"] == [],
      str(rec["check"]))
check("F-P2-6: the catcher's own count matches its log, line for line; nothing is torn", rec["log_problems"] == [],
      str(rec["log_problems"]))
torn = TMP / "torn.jsonl"
torn.write_bytes(b'{"a": 1}\n{"a": 2}{"a"\n{"a": 3}\n')
bad_lines: list = []
try:
    torn_rows = F._jsonl(torn, bad_lines)
except Exception as e:  # noqa: BLE001 - a crash fails the check by its name
    torn_rows = f"crash: {type(e).__name__}"
check("F-P2-6: a torn log line is named, the rest still read - never a crash",
      torn_rows == [{"a": 1}, {"a": 3}] and bad_lines == ["torn.jsonl line 2 does not parse"], str((torn_rows, bad_lines)))
spawns = F._jsonl(L.spawns_log(C))
fetch_sp = [s for s in spawns if s.get("role") == "fetch"]
proxy_sp = [s for s in spawns if s.get("role") == "proxy"]
check("every fetch child was spawned required and witnessed, its script the only read exception",
      len(fetch_sp) == 4 and all(s["witness"]["requirement"] == "required" and s["witness"]["native"] == "on"
                                 and s["argv_exception"] == {"1": str(F.FETCH_CHILD)} for s in fetch_sp))
check("the proxy was the catcher-only one: no key file in its argv",
      len(proxy_sp) == 1 and proxy_sp[0]["argv_exception"] == {"1": str(F.PROXY_SCRIPT)})
check("the record is written beside the window's run", json.loads((C.runs_root / "_fetch/a3-discovery/d1/record.json")
                                                                 .read_bytes())["problems"] == [])

orig_jsonl, orig_cj = F._jsonl, F._control_json


def dropping(path, bad=None):
    rows = orig_jsonl(path, bad)
    return rows[1:] if Path(path).name == "catcher.jsonl" else rows


F._jsonl = dropping
try:
    rec_lost, _, _ = window("lostline", F.discovery_jobs(PINS))
finally:
    F._jsonl = orig_jsonl
check("F-P2-6: a catcher line lost from the log is named against the catcher's own count",
      any("catcher.jsonl holds" in p and "the catcher counted" in p for p in rec_lost["problems"]), str(rec_lost["problems"]))
F._control_json = lambda *a, **k: None
try:
    rec_nc, _, _ = window("nocounters", F.discovery_jobs(PINS))
finally:
    F._control_json = orig_cj
check("F-P2-6: counters that cannot be read are named, never taken as matching",
      any("counters could not be read" in p for p in rec_nc["problems"]), str(rec_nc["problems"]))
F._control_json = lambda *a, **k: {"fetch": {"catcher_hosts": ["huggingface.co"], "catcher_open": 1}}
F.TUNNEL_DRAIN_S = 0.3
try:
    rec_open, _, _ = window("stillopen", F.discovery_jobs(PINS))
finally:
    F._control_json, F.TUNNEL_DRAIN_S = orig_cj, 5.0
check("F-P2-6: a connection still open after the drain wait is named, and no count is compared",
      any("still open 0.3 s after the window: {'fetch': 1}" in p for p in rec_open["problems"])
      and not any("catcher.jsonl holds" in p for p in rec_open["problems"]), str(rec_open["problems"]))
check("F-P2-6: the drain waits for the open count to reach 0, then compares",
      F.drained_counters.__defaults__ is None and F.TUNNEL_DRAIN_S == 5.0)

import types  # noqa: E402


class Dialled(L.Witnesses):
    """G2: the witness reports a window root's direct dial (as launch.py files it: native.window_hosts)."""

    def end_check(self, check_id):
        got = super().end_check(check_id)
        return {**got, "native": {**(got.get("native") or {}), "window_hosts": ["1.2.3.4:443"]}}


LD = types.SimpleNamespace(**{k: getattr(L, k) for k in dir(L) if not k.startswith("__")})
LD.Witnesses = Dialled
cd, bd = contract("dialled")
recd = F.run_child_window(cd, LD, window="a3-discovery", hosts=[HF, GH], jobs=F.discovery_jobs(PINS), python=Path(sys.executable),
                          via_port=hop.port, run="d1", parent_env=os.environ,
                          native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                          fs=L.FsWitness([L.WatchSpec("watched", bd / "watched")]),
                          child_env_extra={"SSL_CERT_FILE": str(made[0])}, volume=TMP)
check("G2: a direct dial the witness saw reaches the record's check and is named - end to end, not only in judge()",
      recd["check"]["window_hosts"] == ["1.2.3.4:443"]
      and any(p.startswith("a window root dialled past the catcher") and "1.2.3.4:443" in p for p in recd["problems"]),
      str((recd["check"].get("window_hosts"), recd["problems"])))

print("\n- the problems, by name -")
bad_job = {"hosts": [HF], "max_redirects": 0, "requests": [{"id": "missing", "url": f"https://{HF}/nothing/here",
                                                             "save": "x.json", "max_bytes": 1024}]}
rec2, _, _ = window("fail", [bad_job])
check("a failing request is a named problem", any("missing" in p and "404" in p for p in rec2["problems"]), str(rec2["problems"]))
off_window = {"hosts": [HF, "evil.example"], "max_redirects": 0, "requests": [
    {"id": "evil", "url": "https://evil.example/x", "save": "e.json", "max_bytes": 1024}]}
rec3, _, _ = window("off", [off_window])
check("a host the window does not hold is refused by the catcher even when the job allows it, and named",
      any("catcher refused" in p and "evil.example" in p for p in rec3["problems"])
      and not any(x["host"] == "evil.example" and x["tunnelled"] for x in rec3["catcher"]), str(rec3["problems"]))
try:
    F.run_child_window(C, L, window="a3-discovery", hosts=[HF], jobs=[], python=Path(sys.executable), via_port=hop.port,
                       run="d1", parent_env=os.environ, volume=TMP)
    check("a used run label refuses the window", False)
except F.WindowRefused:
    check("a used run label refuses the window", True)
except Exception as e:  # noqa: BLE001 - another failure is a failed check, by name
    check("a used run label refuses the window", False, type(e).__name__)
c4, _ = contract("disk")
try:
    F.run_child_window(c4, L, window="a3-discovery", hosts=[HF], jobs=[], python=Path(sys.executable), via_port=hop.port,
                       run="d1", parent_env=os.environ, need_bytes=1 << 62, volume=TMP)
    check("a disk under the floor refuses the window before anything starts", False)
except F.WindowRefused:
    check("a disk under the floor refuses the window before anything starts",
          not (c4.runs_root / "_launch" / "windows.jsonl").exists() and not (c4.runs_root / "_fetch").exists())
except Exception as e:  # noqa: BLE001
    check("a disk under the floor refuses the window before anything starts", False, type(e).__name__)
check("main() never hands the children a CA file or any extra variable",
      "child_env_extra" not in inspect.getsource(F.main) and "SSL_CERT_FILE" not in inspect.getsource(F))
check("GitHub links in cards are found, deduplicated, after the named ones",
      F.github_candidates(["see https://github.com/a/b and github.com/a/b.git and github.com/c/d"])
      == ([*F.GITHUB_NAMED, "a/b", "c/d"], [], []))
got_inv = F.github_candidates(["github.com/../x github.com/a/.git github.com/a__b/c github.com/-a/b github.com/a-/b "
                               "github.com/a--b/c github.com/ok/.. github.com/ok/. github.com/ok/repo github.com/ok/.x"])
check("a card link whose name breaks GitHub's rules is named as invalid and never requested",
      got_inv == ([*F.GITHUB_NAMED, "ok/repo", "ok/.x"], [],
                  ["../x", "a/", "a__b/c", "-a/b", "a-/b", "a--b/c", "ok/..", "ok/."]), str(got_inv))
check("GitHub's name rule: 39-character owners pass, 40 do not; 100-character repositories pass, 101 do not",
      F.gh_name_ok("a" * 39 + "/r") and not F.gh_name_ok("a" * 40 + "/r")
      and F.gh_name_ok("o/" + "r" * 100) and not F.gh_name_ok("o/" + "r" * 101)
      and not F.gh_name_ok(None) and not F.gh_name_ok("o/r\n") and not F.gh_name_ok("o/r/"))
many = " ".join(f"https://github.com/o{i}/r{i}" for i in range(20)) + " https://github.com/mem0ai/mem0"
req, skipped, _ = F.github_candidates([many])
check("the card-linked repositories are capped at 12, in card order; the rest are named, not requested",
      F.CARD_LINK_CAP == 12 and req == [*F.GITHUB_NAMED, *(f"o{i}/r{i}" for i in range(12))]
      and skipped == [f"o{i}/r{i}" for i in range(12, 20)], str((len(req), skipped)))
u0, u1 = TMP / "p1", TMP / "p2"
(u0 / "meta/datasets/org__ds").mkdir(parents=True)
(u0 / "meta/datasets/org__ds/revision.json").write_text(json.dumps({"sha": S_DS}), encoding="utf-8")
(u1 / "meta/datasets/org__ds").mkdir(parents=True)
(u1 / "meta/datasets/org__ds/tree.json").write_text("[]", encoding="utf-8")
(u1 / "meta/datasets/org__ds/README.md").write_text(many + " https://github.com/../x", encoding="utf-8")
job3 = F.discovery_phase3({"ds": PINS["ds"]})([{"unit": str(u0)}, {"unit": str(u1)}])
gh_repos = sorted({r["id"].split(":", 1)[1] for r in job3["requests"] if r["id"].startswith("gh:")})
check("phase 3 requests only the named and the first 12 card-linked repositories, and records the rest by name",
      len(gh_repos) == 4 + 12 and job3["card_links_skipped"] == [f"o{i}/r{i}" for i in range(12, 20)]
      and job3["card_links_invalid"] == ["../x"]
      and not any("o15/r15" in r["url"] or "/.." in r["url"] for r in job3["requests"]),
      str((len(gh_repos), job3.get("card_links_skipped"), job3.get("card_links_invalid"))))
u2 = TMP / "p3"
for dname in ("ok__repo", "a_b__c", "..__x"):
    (u2 / "gh" / dname).mkdir(parents=True)
    (u2 / "gh" / dname / "head.json").write_text(json.dumps({"sha": "a" * 40}), encoding="utf-8")
job4 = F.discovery_phase4()([{}, {}, {"unit": str(u2)}])
check("phase 4 requests a tree only for a directory whose name maps back to a valid GitHub name",
      [r["id"] for r in job4["requests"]] == ["ghtree:ok/repo"], str([r["id"] for r in job4["requests"]]))

print("\n- judge(), table-tested -")
CLEAN = {"jobs": [{"index": 0, "rc": 0, "summary": [{"id": "a", "ok": True}]}], "catcher": [{"host": HF, "tunnelled": True}],
         "check": {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0, "window_hosts": []}}
check("a clean record has no problem", F.judge(CLEAN) == [], str(F.judge(CLEAN)))


def with_(**change):
    rec = json.loads(json.dumps(CLEAN))
    for k, v in change.items():
        rec[k] = v
    return rec


ck = CLEAN["check"]
for label, rec, want in (
        ("native_hits 1", with_(check={**ck, "native_hits": 1}), "1 egress hit"),
        ("a loopback hit (counted in hits too)", with_(check={**ck, "native_hits": 1, "loopback_hits": 1}), "1 of them loopback"),
        ("complete False", with_(check={**ck, "complete": False}), "not complete"),
        ("fs_hits 1", with_(check={**ck, "fs_hits": 1}), "file-system"),
        ("a direct dial by a window root", with_(check={**ck, "window_hosts": ["140.82.121.4:443"]}), "past the catcher"),
        ("a torn log line", with_(log_problems=["catcher.jsonl line 3 does not parse"]), "a window log is not whole"),
        ("an unknown window_hosts list", with_(check={**ck, "window_hosts": None}), "past the catcher"),
        ("a catcher refusal", with_(catcher=[{"host": HF, "tunnelled": True}, {"host": "evil.example", "tunnelled": False}]),
         "catcher refused"),
        ("a request not ok", with_(jobs=[{"index": 0, "rc": 0, "summary": [{"id": "a", "ok": False, "error": "status 404"}]}]),
         "request a: status 404"),
        ("a child's exit 3", with_(jobs=[{"index": 0, "rc": 3, "summary": [{"id": "a", "ok": True}]}]), "exited with 3"),
        ("a rate limit", with_(jobs=[{"index": 0, "rc": 0, "summary": [
            {"id": "gh:x", "ok": False, "rate_limited": True, "error": "Refused: rate-limited: status 403"},
            {"id": "gh:y", "ok": False, "rate_limited": False, "error": "not sent: rate-limited earlier"}]}]), "rate-limited")):
    got = F.judge(rec)
    check(f"{label}: exactly one named problem", len(got) == 1 and want in got[0], str(got))

print("\n- plan d2: AMA-Hub moved, mem0's evaluation/ deleted -")
A_SHA, C_DEL, C_PAR = "4" * 40, "5" * 40, "6" * 40


def d2_routes(*, newest_holds=False, ama_location=f"https://{GH}/repositories/777", mem0_status=200):
    return {
        "/repos/AMA-Bench/AMA-Hub": (301, [("Location", ama_location)], b""),
        "/repositories/777": J({"full_name": "AMA-Bench/AMA-Hub-v2", "license": {"spdx_id": "MIT"}}),
        "/repos/AMA-Bench/AMA-Hub/commits/HEAD": (301, [("Location", f"https://{GH}/repositories/777/commits/HEAD")], b""),
        "/repositories/777/commits/HEAD": J({"sha": A_SHA}),
        "/repos/mem0ai/mem0/commits?path=evaluation&per_page=5": (
            J([{"sha": C_DEL, "parents": [{"sha": C_PAR}]}, {"sha": "7" * 40, "parents": [{"sha": "8" * 40}]}])
            if mem0_status == 200 else (mem0_status, [], b'{"message": "API rate limit exceeded"}')),
        f"/repos/AMA-Bench/AMA-Hub-v2/git/trees/{A_SHA}?recursive=1": J({"tree": [{"path": "judge/prompt.py", "type": "blob"}],
                                                                        "truncated": False}),
        f"/repos/mem0ai/mem0/git/trees/{C_DEL}?recursive=1": J({"tree": [{"path": "docs/x.md", "type": "blob"}]
                                                                + ([{"path": "evaluation/metrics/llm_judge.py", "type": "blob"}]
                                                                   if newest_holds else [])}),
        "/repos/mohammadtavakoli78/BEAM": J({"full_name": "mohammadtavakoli78/BEAM", "license": {"spdx_id": "MIT"}}),
        "/repos/mohammadtavakoli78/BEAM/commits/HEAD": J({"sha": "9" * 40}),
        f"/repos/mohammadtavakoli78/BEAM/git/trees/{'9' * 40}?recursive=1": J({"tree": [
            {"path": "src/evaluation/compute_metrics.py", "type": "blob"}, {"path": "src/prompts.py", "type": "blob"},
            {"path": "README.md", "type": "blob"}], "truncated": False}),
        f"/repos/mem0ai/mem0/git/trees/{C_PAR}?recursive=1": J({"tree": [{"path": "evaluation", "type": "tree"},
                                                                         {"path": "evaluation/metrics/llm_judge.py", "type": "blob"},
                                                                         {"path": "evaluation/prompts.py", "type": "blob"}]}),
    }


srv.routes.update(d2_routes())
rec5, C5, _ = window("d2", F.d2_jobs(), hosts=F.D2_HOSTS)
rep5 = F.d2_report(rec5)
n_req = sum(len(j["summary"]) for j in rec5["jobs"])
check("d2 runs clean on api.github.com only, within 12 requests",
      rec5["problems"] == [] and n_req <= 12 and {x["host"] for x in rec5["catcher"] if x["tunnelled"]} == {GH},
      f"{rec5['problems']} requests={n_req}")
check("d2 phase A follows AMA-Hub's one redirect, staying on api.github.com",
      all(r["ok"] and r["final_host"] == GH for r in rec5["jobs"][0]["summary"]) and rec5["jobs"][0]["job"]["max_redirects"] == 1)
check("d2 reads AMA-Hub's tree at its head under the name the redirect led to",
      rep5["ama_hub"] == {"asked": "AMA-Bench/AMA-Hub", "full_name": "AMA-Bench/AMA-Hub-v2", "full_name_valid": True, "licence": "MIT",
                          "head_sha": A_SHA, "tree_entries": 1, "tree_truncated": False}, str(rep5["ama_hub"]))
check("d2 reads BEAM's official repository (P4): its head and its evaluation files, names only",
      rep5["beam"]["head_sha"] == "9" * 40 and rep5["beam"]["licence"] == "MIT"
      and rep5["beam"]["evaluation_files"] == ["src/evaluation/compute_metrics.py", "src/prompts.py"], str(rep5["beam"]))
check("d2: the newest mem0 commit is the deletion, so the pin is its first parent, with the reason",
      rep5["mem0"]["newest"] == C_DEL and rep5["mem0"]["newest_holds_evaluation"] is False
      and rep5["mem0"]["pinned_commit"] == C_PAR and "first parent" in rep5["mem0"]["why"]
      and rep5["mem0"]["evaluation_files"] == ["evaluation/metrics/llm_judge.py", "evaluation/prompts.py"], str(rep5["mem0"]))
start5 = F._jsonl(C5.runs_root / "_launch" / "windows.jsonl")[0]
check("D2-m1: the d2 window holds exactly api.github.com - the plan's hosts, every job's, its START and the catcher's /window",
      F.D2_HOSTS == ["api.github.com"] and all(j["job"]["hosts"] == ["api.github.com"] for j in rec5["jobs"])
      and start5["event"] == "START" and start5["hosts"] == ["api.github.com"]
      and rec5["windows_proxy"][0]["hosts"] == ["api.github.com"],
      str((start5.get("hosts"), [j["job"]["hosts"] for j in rec5["jobs"]], rec5["windows_proxy"][:1])))
srv.routes.update(d2_routes(newest_holds=True))
rec6, _, _ = window("d2hold", F.d2_jobs(), hosts=F.D2_HOSTS)
rep6 = F.d2_report(rec6)
check("d2: when the newest commit still holds evaluation/, it is the pin and no parent tree is read",
      rec6["problems"] == [] and len(rec6["jobs"]) == 2 and rep6["mem0"]["pinned_commit"] == C_DEL, str((len(rec6["jobs"]), rep6["mem0"])))
srv.routes.update(d2_routes(ama_location="https://evil.example/repositories/777"))
rec7, _, _ = window("d2off", F.d2_jobs(), hosts=F.D2_HOSTS)
check("d2: a redirect off api.github.com is refused by name",
      any("AMA-Hub" in p and "not an allowed" in p for p in rec7["problems"])
      and not any(x["host"] == "evil.example" and x["tunnelled"] for x in rec7["catcher"]), str(rec7["problems"]))
for i_bad, bad_name in enumerate(("a/b/c", "AMA-Bench/x?y", "AMA-Bench/has space", "../AMA-Bench", "AMA-Bench/..")):
    routes_bad = d2_routes()
    routes_bad["/repositories/777"] = J({"full_name": bad_name, "license": {"spdx_id": "MIT"}})
    srv.routes.update(routes_bad)
    n_heads = len(srv.heads)
    rec_b, _, _ = window(f"d2bad{i_bad}", F.d2_jobs(), hosts=F.D2_HOSTS)
    rep_b = F.d2_report(rec_b)
    trees_b = {r["id"] for j in rec_b["jobs"] for r in j["summary"] if r["id"].startswith("ghtree:")}
    check(f"D2-m2: a malformed full_name {bad_name!r} is never requested (no AMA tree reaches the server), "
          "and the report names it as invalid, with an empty tree",
          trees_b == {f"ghtree:{F.BEAM_GH}", f"ghtree:{F.MEM0}@newest", f"ghtree:{F.MEM0}@parent"}
          and not any(f"/git/trees/{A_SHA}".encode() in h.split(b"\r\n", 1)[0] for h in srv.heads[n_heads:])
          and rep_b["ama_hub"]["full_name"] == bad_name and rep_b["ama_hub"]["full_name_valid"] is False
          and rep_b["ama_hub"]["tree_entries"] == 0, str((sorted(trees_b), rep_b["ama_hub"])))
srv.routes.update(d2_routes(mem0_status=403))
rec8, _, _ = window("d2rl", F.d2_jobs(), hosts=F.D2_HOSTS)
check("d2: a 403 from api.github.com is a rate limit - a named problem, the job stopped",
      any(p.startswith("rate-limited") for p in rec8["problems"]), str(rec8["problems"]))

hop.close()
srv.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 fetch a3: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
