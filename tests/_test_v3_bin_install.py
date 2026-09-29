#!/usr/bin/env python3
"""PREREG-V3 A8, T32 (PREREG-V3-AMENDMENTS.md A3; the auditor's Q-A8-10): research/v3/bin_install.py - spawns NO child.
The window is a fake fetch_a3 whose run_child_window leaves the gh_release job's files:

* before anything, offline: the expectation is the a7-docs record d1 (d4_report.json) the auditor fixed by its sha256 -
  another file, a record with problems, another repository, tag or commit, a draft or a prerelease, or an asset it does
  not hold exactly once refuses by name before the window; the binary's destination must be empty and the run label
  unused;
* the window: the gh_release job for exactly that repository, tag, commit and the two assets (the binary and its
  .sha256) with the record's sizes and digests, on exactly the manifest's hosts; a host reached with no catcher tunnel,
  a window problem, a failed job, or a job whose assets are not the expectation stops everything before any placing;
* after it: the binary re-read from the disk against the digest and the size; the .sha256 file is one line naming the
  binary's own sha256 and file name; only then is the binary copied to <polygon>/supermemory_v3/bin/ and re-read there;
  the binary is never started (the A8 probe starts it, under the catcher);
* the manifest declares the a8-supermemory-bin window exactly as the code does.

    python tests/_test_v3_bin_install.py
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import inspect
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
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


try:
    BI = _load("v3_bin_install_t", ROOT / "research" / "v3" / "bin_install.py")
except Exception as e:  # noqa: BLE001 - on the old tree the module is missing; every row then FAILs by name
    RAISED.append(f"load: {type(e).__name__}: {e}")
    BI = None
L = _load("v3_launch_bin_t", ROOT / "research" / "v3" / "launch.py")

TMP = Path(tempfile.mkdtemp(prefix="nvt3_bin_install_"))
atexit.register(shutil.rmtree, TMP, True)
#: the declared facts, written here - independent of the code's constants, which the rows compare against
API, WEB = "api.github.com", "github.com"
CDN1, CDN2 = "objects.githubusercontent.com", "release-assets.githubusercontent.com"
REPO, TAG, VERSION = "supermemoryai/supermemory", "server-v0.0.8", "0.0.8"
COMMIT = "5d2b5855fe492a3682a1cde4a255e2db0c4db595"
BINARY = "supermemory-server-windows-x64.exe"
SUMS_NAME = BINARY + ".sha256"
DOCS_SHA = "492f92b77098b80eeca7494bb3550a3988aa596873850ca1d1f269bae6a9fcd8"   # the auditor's fixing of a7-docs d1
BIN = b"MZ\x90\x00 a fake supermemory server " * 500


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


SUMS = f"{sha(BIN)}  {BINARY}\n".encode()


def docs_record(**over) -> dict:
    """The a7-docs d1 report's shape (fetch_a3 plan d4): repo, ref_name, commit, head, problems, docs, release."""
    rel = {"status": 200, "tag_name": TAG, "name": "supermemory-server 0.0.8", "draft": False, "prerelease": False,
           "published_at": "2026-08-17T18:39:22Z", "target_commitish": "main",
           "assets": [{"name": "install.sh", "size": 10, "digest": "sha256:" + "1" * 64, "content_type": "application/x-sh"},
                      {"name": "supermemory-server-linux-x64", "size": 20, "digest": "sha256:" + "2" * 64},
                      {"name": BINARY, "size": len(BIN), "digest": f"sha256:{sha(BIN)}"},
                      {"name": SUMS_NAME, "size": len(SUMS), "digest": f"sha256:{sha(SUMS)}"}]}
    rec = {"repo": REPO, "ref_name": TAG, "commit": COMMIT, "head": "b" * 40, "problems": [], "docs": [], "release": rel}
    for k, v in over.items():
        if k.startswith("release_"):
            rel[k[len("release_"):]] = v
        else:
            rec[k] = v
    return rec


def contract(tag):
    base = TMP / tag
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "q",
                      conservation_root=base / "cv")


def world(tag, *, record=None, write=True):
    """A contract and the a7-docs d1 record; returns (c, the record file's sha256)."""
    c = contract(tag)
    d = c.runs_root / "_fetch" / "a7-docs" / "d1"
    d.mkdir(parents=True)
    data = json.dumps(record if record is not None else docs_record(), indent=1).encode()
    if write:
        (d / "d4_report.json").write_bytes(data)
    return c, sha(data)


def expect_assets():
    return [{"name": BINARY, "size": len(BIN), "digest": f"sha256:{sha(BIN)}"},
            {"name": SUMS_NAME, "size": len(SUMS), "digest": f"sha256:{sha(SUMS)}"}]


class FakeWindow:
    def __init__(self, *, summary=None, problems=(), untunnelled=None, served=None):
        self.summary, self.problems, self.untunnelled = summary, list(problems), untunnelled
        self.served = served or {}
        self.seen: dict = {}
        self.called = 0

    def run_child_window(self, c, L_, **kw):
        self.called += 1
        self.seen.update(kw)
        unit = Path(c.runs_root) / "_fetch" / kw["window"] / kw["run"] / "j0"
        (unit / "release").mkdir(parents=True)
        (unit / "release" / BINARY).write_bytes(self.served.get(BINARY, BIN))
        (unit / "release" / SUMS_NAME).write_bytes(self.served.get(SUMS_NAME, SUMS))
        summ = self.summary or {
            "ok": True, "kind": "gh_release", "commit": COMMIT, "error": None,
            "release": {"id": 1, "tag": TAG, "draft": False, "prerelease": False, "published_at": "2026-08-17T18:39:22Z"},
            "assets": [{**a, "cdn_host": CDN2, "file": {"sha256": a["digest"][7:], "bytes": a["size"],
                                                       "path": f"release/{a['name']}"}} for a in expect_assets()],
            "requests": [{"host": h} for h in (API, API, WEB, CDN2, WEB, CDN2)]}
        hosts = [h for h in kw["hosts"] if h != self.untunnelled]
        return {"problems": list(self.problems), "check": {"complete": True}, "issuers": [],
                "catcher": [{"host": h, "tunnelled": True} for h in hosts], "jobs": [{"unit": str(unit), "summary": [summ]}]}


def run(tag, *, window=None, record=None, write=True, docs_sha=None, run_label="b1", pre=None):
    c, rsha = world(tag, record=record, write=write)
    if pre:
        pre(c)
    w = window or FakeWindow()
    try:
        if BI is None:
            raise AttributeError("research/v3/bin_install.py is missing")
        rec = BI.run_bin_install(c, L, w, run=run_label, via_port=1, parent_env={}, python=Path("py.exe"),
                                 docs_sha256=docs_sha or rsha)
        err = None
    except Exception as e:  # noqa: BLE001 - a refusal is the row's to read
        rec, err = {}, e
    return rec, c, w, err


def dest(c) -> Path:
    return c.polygon_root / "supermemory_v3" / "bin" / BINARY


print("- a whole install -")
rec, C, W, err = run("ok")
check("BI-1: the install has no problem", ok(lambda: err is None and rec["problems"] == []), f"{err!r} {rec.get('problems')}")
check("BI-2: the expectation is the a7-docs record's: the binary and its .sha256, their sizes and digests, and the "
      "record's own path and sha256", ok(lambda: rec["expect"] == expect_assets()
                                         and rec["docs_record"]["sha256"] == sha((C.runs_root / "_fetch" / "a7-docs" / "d1" / "d4_report.json").read_bytes())
                                         and rec["docs_record"]["path"].endswith("d4_report.json")),
      str({k: rec.get(k) for k in ("expect", "docs_record")}))
check("BI-3: the window: the gh_release job for exactly that repository, tag, commit and the two assets, on exactly the "
      "declared hosts, one job",
      ok(lambda: W.seen["window"] == BI.WINDOW == "a8-supermemory-bin" and W.seen["hosts"] == [API, WEB, CDN1, CDN2]
         and W.seen["jobs"] == [BI.gh_job(expect_assets())] and len(W.seen["jobs"]) == 1
         and {k: W.seen["jobs"][0][k] for k in ("kind", "repo", "tag", "commit", "hosts", "cdn_hosts", "assets")}
         == {"kind": "gh_release", "repo": REPO, "tag": TAG, "commit": COMMIT, "hosts": [API, WEB, CDN1, CDN2],
             "cdn_hosts": [CDN1, CDN2], "assets": expect_assets()}), str(W.seen.get("jobs")))
check("BI-4: the binary placed at <polygon>/supermemory_v3/bin/<name>, the digest's bytes, re-read there; version, tag, "
      "commit and sha256 recorded",
      ok(lambda: dest(C).read_bytes() == BIN and rec["path"] == str(dest(C)) and rec["sha256"] == sha(BIN)
         and rec["placed_sha256"] == sha(BIN) and rec["bytes"] == len(BIN) and rec["version"] == VERSION
         and rec["tag"] == TAG and rec["commit"] == COMMIT and rec["cdn_hosts"] == [CDN2, CDN2]),
      str({k: rec.get(k) for k in ("path", "sha256", "placed_sha256", "version", "tag", "commit", "cdn_hosts")}))
check("BI-5: the .sha256 file's line is recorded and names the binary's own sha256 and name; the binary is never started "
      "and no partial copy stays",
      ok(lambda: rec["sums_line"] == {"sha256": sha(BIN), "name": BINARY} and rec["binary_started"] is False
         and sorted(p.name for p in dest(C).parent.iterdir()) == [BINARY]), str(rec.get("sums_line")))
def sums_world(sums: bytes):
    """A record and a window that agree on a .sha256 file of these bytes (the vendor's own file): its digest and size in
    the record, the live release and the job alike - so only the line's own reading can object."""
    ex = expect_assets()
    ex[1] = {"name": SUMS_NAME, "size": len(sums), "digest": f"sha256:{sha(sums)}"}
    r = docs_record()
    r["release"]["assets"][3].update(size=len(sums), digest=f"sha256:{sha(sums)}")
    w = FakeWindow(served={SUMS_NAME: sums})
    w.summary = {"ok": True, "kind": "gh_release", "commit": COMMIT, "error": None,
                 "release": {"id": 1, "tag": TAG, "draft": False, "prerelease": False},
                 "assets": [{**a, "cdn_host": CDN2, "file": {"sha256": a["digest"][7:], "bytes": a["size"],
                                                            "path": f"release/{a['name']}"}} for a in ex],
                 "requests": [{"host": h} for h in (API, API, WEB, CDN2, WEB, CDN2)]}
    return r, w


_rs, _ws = sums_world(f"{sha(BIN)} *{BINARY}\n".encode())
rec_s, C_s, W_s, err_s = run("star", window=_ws, record=_rs)
check("BI-5b: the .sha256 line's binary form '<hex> *<name>' is taken as the text form '<hex>  <name>' is",
      ok(lambda: err_s is None and rec_s["problems"] == [] and rec_s["sums_line"] == {"sha256": sha(BIN), "name": BINARY}
         and dest(C_s).read_bytes() == BIN), f"{err_s!r} {rec_s.get('problems')}")
rp = C.runs_root / "_install" / "a8-supermemory-bin" / "b1" / "bin_record.json"
check("BI-6: the record is written to <runs>/_install/a8-supermemory-bin/<run>/bin_record.json and is what the call returned",
      ok(lambda: json.loads(rp.read_bytes()) == rec), str(rp))
check("BI-7: the fixed facts are the auditor's and the release's: the a7-docs record's sha256 492f92b7..., the "
      "repository, tag, commit, binary and destination",
      ok(lambda: inspect.signature(BI.run_bin_install).parameters["docs_sha256"].default == DOCS_SHA == BI.DOCS_REPORT_SHA256
         and (BI.REPO, BI.TAG, BI.VERSION, BI.COMMIT, BI.BINARY, BI.DEST) == (REPO, TAG, VERSION, COMMIT, BINARY, "supermemory_v3")
         and (BI.DOCS_WINDOW, BI.DOCS_RUN) == ("a7-docs", "d1")), "")

print("\n- refusals before the window (nothing fetched, nothing placed) -")


def refused(tag, want, **kw):
    rec, c, w, err = run(tag, **kw)
    check(f"BI refused before the window: {tag}", ok(lambda: err is not None and type(err).__name__ == "BinRefused"
                                                     and want in str(err) and w.called == 0 and not dest(c).exists()),
          f"{err!r}")


refused("no_record", "no a7-docs record", write=False)
refused("other_record", "is not the fixed record", docs_sha="0" * 64)
refused("record_problems", "the a7-docs record has problems", record=docs_record(problems=["a path answered 404"]))
refused("record_repo", "names the repository 'someone/else'", record=docs_record(repo="someone/else"))
refused("record_ref", "names the tag 'server-v0.0.9'", record=docs_record(ref_name="server-v0.0.9"))
refused("record_commit", f"names commit {'c' * 40}", record=docs_record(commit="c" * 40))
refused("release_tag", "the record's release is 'server-v0.0.7'", record=docs_record(release_tag_name="server-v0.0.7"))
refused("release_draft", "a draft or a prerelease", record=docs_record(release_draft=True))
refused("release_pre", "a draft or a prerelease", record=docs_record(release_prerelease=True))
_r = docs_record()
_r["release"]["assets"] = [a for a in _r["release"]["assets"] if a["name"] != SUMS_NAME]
refused("record_no_sums", f"holds 0 assets named {SUMS_NAME}", record=_r)
_r = docs_record()
_r["release"]["assets"].append(dict(_r["release"]["assets"][2]))
refused("record_twice", f"holds 2 assets named {BINARY}", record=_r)
_r = docs_record()
_r["release"]["assets"][2]["digest"] = None
refused("record_no_digest", f"the record's {BINARY} has no sha256 digest", record=_r)


def _placed(c):
    dest(c).parent.mkdir(parents=True)
    dest(c).write_bytes(b"an older binary")


rec_x, c_x, w_x, err_x = run("dest_taken", pre=_placed)
check("BI refused before the window: a binary already at the destination is never overwritten",
      ok(lambda: type(err_x).__name__ == "BinRefused" and "already holds" in str(err_x) and w_x.called == 0
         and dest(c_x).read_bytes() == b"an older binary"), f"{err_x!r}")


def _used(c):
    (c.runs_root / "_install" / "a8-supermemory-bin" / "b1").mkdir(parents=True)


refused("label_used", "was used before", pre=_used)


def _partial(c):
    dest(c).parent.mkdir(parents=True)
    (dest(c).parent / (BINARY + ".partial")).write_bytes(b"a copy cut short")


# the auditor's BI1 (2026-09-30): a leftover .partial was not refused before the window - open(part, "xb") then failed
# after the 291 MB download, with no bin_record
rec_p, c_p, w_p, err_p = run("partial_left", pre=_partial)
check("BI refused before the window: a leftover <binary>.partial at the destination (a copy cut short) is refused by "
      "name, the window never runs and the leftover stays for a person to look at",
      ok(lambda: type(err_p).__name__ == "BinRefused" and "already holds" in str(err_p) and w_p.called == 0
         and (dest(c_p).parent / (BINARY + ".partial")).read_bytes() == b"a copy cut short"
         and not dest(c_p).exists()), f"{err_p!r}")

print("\n- problems after the window (nothing placed) -")


def problem(tag, want, **kw):
    rec, c, w, err = run(tag, **kw)
    check(f"BI problem by name: {tag}", ok(lambda: err is None and any(want in p for p in rec["problems"])
                                           and not dest(c).exists() and "sha256" not in rec),
          f"{err!r} {rec.get('problems')}")


problem("window_problem", "the catcher refused", window=FakeWindow(problems=["the catcher refused a host"]))
problem("untunnelled", "past the catcher", window=FakeWindow(untunnelled=CDN2))
problem("job_failed", "the gh_release job failed", window=FakeWindow(summary={"ok": False, "error": "Refused: moved tag",
                                                                              "requests": []}))
_s = {"ok": True, "kind": "gh_release", "commit": COMMIT, "release": {"tag": TAG},
      "assets": [{**expect_assets()[0], "digest": "sha256:" + "9" * 64, "cdn_host": CDN2, "file": {}}],
      "requests": [{"host": API}]}
problem("job_assets", "the job's assets are not the expectation", window=FakeWindow(summary=_s))
problem("tampered", f"{BINARY} on the disk is not the expected sha256 and size",
        window=FakeWindow(served={BINARY: BIN[:-1] + b"!"}))
problem("sums_tampered", f"{SUMS_NAME} on the disk is not the expected sha256 and size",
        window=FakeWindow(served={SUMS_NAME: SUMS[:-2] + b"!\n"}))
for _tag, _want, _bytes in (      # the vendor's own .sha256 (the record, the release and the job agree on its bytes)
        ("sums_hex", f"names sha256 {'a' * 64}, not the binary's", f"{'a' * 64}  {BINARY}\n".encode()),
        ("sums_name", "names the file 'other.exe'", f"{sha(BIN)}  other.exe\n".encode()),
        ("sums_lines", "is not one line", f"{sha(BIN)}  {BINARY}\n{sha(BIN)}  x\n".encode()),
        ("sums_garbage", "is not one line", b"not a checksum\n"),
        ("sums_one_space", "is not one line", f"{sha(BIN)} {BINARY}\n".encode()),
        ("sums_upper", "is not one line", f"{sha(BIN).upper()}  {BINARY}\n".encode())):
    _r, _w = sums_world(_bytes)
    problem(_tag, _want, window=_w, record=_r)

# the auditor's BI5 (2026-09-30): the placed file re-read at its destination is the last check - a copy whose bytes are
# not the fetched ones (a disk fault, say; here the rename is made to land other bytes) is a problem by name
import os as _os  # noqa: E402
from types import SimpleNamespace as _NS  # noqa: E402


def _bad_replace(src, dst):
    Path(dst).write_bytes(b"MZ not the fetched bytes")
    Path(src).unlink()


_saved_os = getattr(BI, "os", None) if BI is not None else None
try:
    if BI is not None:
        BI.os = _NS(replace=_bad_replace, environ=_os.environ, fspath=_os.fspath)
    rec_b, c_b, w_b, err_b = run("placed_other")
finally:
    if BI is not None:
        BI.os = _saved_os
check("BI problem by name: placed_other - the placed binary re-read at its destination is not the fetched one",
      ok(lambda: err_b is None and any("the placed binary is not the fetched one" in p for p in rec_b["problems"])
         and rec_b["placed_sha256"] != sha(BIN) and BI.os is _os), f"{err_b!r} {rec_b.get('problems')}")

print("\n- the manifest -")
MANW = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))["windows"]
mw = MANW.get("a8-supermemory-bin") or {}
check("BI-8: the manifest declares a8-supermemory-bin exactly as the code does - its four hosts, the two GitHub CDN hosts, "
      "the repository, tag, commit and binary, the a7-docs record's sha256, one redirect",
      ok(lambda: mw["hosts"] == [API, WEB, CDN1, CDN2] == [BI.API, BI.WEB, *BI.CDN_HOSTS]
         and mw["cdn_hosts"] == [CDN1, CDN2] == list(BI.CDN_HOSTS) and mw["repo"] == REPO and mw["tag"] == TAG
         and mw["commit"] == COMMIT and mw["binary"] == BINARY and mw["docs_record_sha256"] == DOCS_SHA
         and mw["max_redirects"] == 1), str(mw)[:300])
check("no row's condition raised", RAISED == [], str(RAISED))
print(f"\nv3 bin install: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
