#!/usr/bin/env python3
"""PREREG-V3 T34 (the auditor's Q-BM25-FILES, 2026-09-30): research/v3/fetch_a3.py plan d10 - the a7-discovery-2
window, metadata only - offline, with real processes under the contract (the catcher-only proxy, two fetch-child jobs,
a fake huggingface.co behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-discovery-2" entry, the single source: huggingface.co only, no redirect followed,
  the repository Qdrant/bm25 and the revision mem0's BM25 is pinned at (22b8d2af...) - anything else is refused before
  any spawn;
* job a reads the revision, the recursive tree at it - strictly
  https://huggingface.co/api/models/Qdrant/bm25/tree/<revision>?recursive=true, the form m6's discovery index reads -
  and the card; job b is one HEAD on each listed file's resolve URL at the revision: the redirect's host is recorded
  and never followed, nothing is downloaded, the signed query is kept nowhere;
* the report: the revision the answer names, the files with their sizes and object ids, the licence from the card data,
  the tags and the card's front matter, each file's HEAD status and redirect host, and every failure by name - from
  which the auditor fixes PINS_A7 and the hosts of the window a7-hf-bm25.

    python tests/_test_v3_fetch_a7_discovery2.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


L = _load("v3_launch_d10", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d10", ROOT / "research" / "v3" / "fetch_a3.py")
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


REPO_, REV_ = "Qdrant/bm25", "22b8d2af71a76161e18dd432d2cee0eefa66e412"
TREE_URL = f"https://huggingface.co/api/models/{REPO_}/tree/{REV_}?recursive=true"
HF, CDN = "huggingface.co", "cas-bridge.xethub.hf.co"
SIG = "X-Amz-Signature=deadbeef&X-Amz-Credential=secret"
MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
REAL = MANI["windows"].get("a7-discovery-2") or {}
print("- the manifest's a7-discovery-2 entry (the auditor's Q-BM25-FILES) -")
decl, derr = holds(lambda: F.d10_decl(MANI))
check("DS2-1: the manifest declares a7-discovery-2 as plan d10 reads it: huggingface.co only, no redirect, Qdrant/bm25 at "
      "the revision mem0's BM25 is pinned at (22b8d2af...)",
      derr is None and decl["hosts"] == [HF] and decl["max_redirects"] == 0 and decl["repo"] == REPO_
      and decl["revision"] == REV_, str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "path": "README.md"},
    "a missing key": {k: v for k, v in REAL.items() if k != "revision"},
    "another host": {**REAL, "hosts": [HF, CDN]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a revision that is no commit": {**REAL, "revision": "main"},
    "a repository that climbs": {**REAL, "repo": "../bm25"},
    "a repository with a query": {**REAL, "repo": "Qdrant/bm25?x=1"},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d10_decl({"windows": {"a7-discovery-2": entry}}))
    refused[label] = bool(e) and e.startswith("D10ManifestError")
check("DS2-2: an entry with anything but the declared shape is refused by name (D10ManifestError) - " + ", ".join(bad_cases),
      bool(REAL) and all(refused.values()), str([k for k, v in refused.items() if not v]))
DECL = {"hosts": [HF], "purpose": "test", "repo": REPO_, "revision": REV_, "max_redirects": 0}
ja, jaerr = holds(lambda: F.d10_jobs(DECL)[0])
tree_q = [q for q in (ja or {}).get("requests") or [] if "/tree/" in q.get("url", "")]
check("DS2-3: job a asks for the tree strictly at the auditor's URL (m6's _HF_TREE form), saved - the listing m6's "
      "discovery index reads by the request's URL", jaerr is None and len(tree_q) == 1 and tree_q[0]["url"] == TREE_URL
      and tree_q[0].get("save") and tree_q[0].get("method", "GET") == "GET" and ja["hosts"] == [HF]
      and ja["max_redirects"] == 0, str(jaerr or ja)[:400])

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d10_"))
made = TF.make_test_cert(TMP / "cert", HF, org="Amazon")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 discovery-2: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

J = lambda o: (200, [("Content-Type", "application/json")], json.dumps(o).encode())  # noqa: E731
TREE = [{"type": "file", "path": ".gitattributes", "size": 1519, "oid": "1" * 40},
        {"type": "file", "path": "README.md", "size": 43, "oid": "2" * 40},
        {"type": "file", "path": "config.json", "size": 200, "oid": "3" * 40},
        {"type": "directory", "path": "stopwords", "oid": "4" * 40},
        {"type": "file", "path": "stopwords/english.txt", "size": 936, "oid": "5" * 40},
        {"type": "file", "path": "stopwords/pt br.txt", "size": 1200, "oid": "6" * 40},
        {"type": "file", "path": "big.bin", "size": 123456, "oid": "7" * 40,
         "lfs": {"oid": "f" * 64, "size": 123456, "pointerSize": 134}}]
FILES = [e["path"] for e in TREE if e["type"] == "file"]
REV_JSON = {"id": REPO_, "sha": REV_, "cardData": {"license": "apache-2.0"}, "tags": ["fastembed", "license:apache-2.0"],
            "siblings": [{"rfilename": p} for p in FILES]}
CARD = b"---\nlicense: apache-2.0\n---\n# BM25\n\nstopwords\n"
RES = f"/{REPO_}/resolve/{REV_}/"
routes = {
    f"/api/models/{REPO_}/revision/{REV_}": J(REV_JSON),
    f"/api/models/{REPO_}/tree/{REV_}?recursive=true": J(TREE),
    f"/{REPO_}/raw/{REV_}/README.md": (200, [("Content-Type", "text/plain")], CARD),
    RES + ".gitattributes": (200, [], b""),
    RES + "README.md": (307, [("Location", f"/api/resolve-cache/models/{REPO_}/{REV_}/README.md?etag=x")], b""),
    RES + "config.json": (200, [], b""),
    RES + "stopwords/english.txt": (200, [], b""),
    RES + "stopwords/pt%20br.txt": (200, [], b""),
    RES + "big.bin": (302, [("Location", f"https://{CDN}/xet-bridge-us/abc/def?{SIG}")], b""),
}


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def window(run: str):
    srv = TF.TlsHttpServer(made[0], made[1], routes)
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d10_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-discovery-2", hosts=[HF], jobs=jobs, python=Path(sys.executable), via_port=hop.port, run=run,
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    heads = list(srv.heads)
    rep, perr = holds(lambda: F.d10_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return heads, rec, rep, perr


_n = [0]


def offline(*, rev=REV_JSON, tree=TREE, card=CARD, fail_a=(), head=None, head_missing=(), decl=DECL):
    """A record built as the window would leave it, with no window: job a's answers saved where its requests say, job
    b built by the plan's own builder from them; ``fail_a`` names job a's requests that failed ('rev', 'tree', 'card'),
    ``head`` maps a path to its HEAD summary fields, ``head_missing`` names paths whose HEAD left no summary. Returns
    (report, the HEADs job b asked for) or the exception named."""
    def build():
        _n[0] += 1
        ua, ub = TMP / f"off{_n[0]}" / "a", TMP / f"off{_n[0]}" / "b"
        ua.mkdir(parents=True)
        ub.mkdir(parents=True)
        spec_a, builder = F.d10_jobs(decl)
        summ_a = []
        for q in spec_a["requests"]:
            kind = "tree" if "/tree/" in q["url"] else "rev" if "/revision/" in q["url"] else "card"
            body = {"rev": None if rev is None else json.dumps(rev).encode(), "tree": None if tree is None
                    else json.dumps(tree).encode(), "card": card}[kind]
            ok = kind not in fail_a and body is not None
            if ok:
                p = ua.joinpath(*q["save"].split("/"))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(body)
            summ_a.append({"id": q["id"], "status": 200 if ok else 404, "final_host": HF, "redirect_host": None,
                           "ok": ok, "error": None if ok else "Refused: status 404", "bytes": len(body or b"")})
        results = [{"index": 0, "rc": 0, "unit": str(ua), "job": spec_a, "summary": summ_a}]
        spec_b = builder(results)
        asked = []
        if spec_b is not None:
            summ_b = []
            for q in spec_b["requests"]:
                path = q["url"].split(f"/resolve/{REV_}/", 1)[1]
                asked.append((q.get("method"), path))
                plain = path.replace("%20", " ")
                if plain in head_missing:
                    continue
                s = {"id": q["id"], "status": 200, "final_host": HF, "redirect_host": None, "ok": True, "error": None}
                s.update((head or {}).get(plain, {}))
                summ_b.append(s)
            results.append({"index": 1, "rc": 0, "unit": str(ub), "job": spec_b, "summary": summ_b})
        return F.d10_report({"jobs": results}, decl), asked
    return holds(build)


def probs(r) -> list:
    return list((r or {}).get("problems") or [])


try:
    print("\n- the window, offline -")
    heads, rec, rep, perr = window("t1")
    lines = [h.split(b" ")[:2] for h in heads]
    want = [[b"GET", f"/api/models/{REPO_}/revision/{REV_}".encode()],
            [b"GET", f"/api/models/{REPO_}/tree/{REV_}?recursive=true".encode()],
            [b"GET", f"/{REPO_}/raw/{REV_}/README.md".encode()]]
    want_heads = sorted([b"HEAD", (RES + p.replace(" ", "%20")).encode()] for p in FILES)
    check("DS2-4: job a asks exactly for the revision, the tree and the card at the revision; job b is one HEAD per "
          "listed file on its resolve URL (quoted) and none for a directory - no redirect followed, nothing else asked",
          perr is None and lines[:3] == want and sorted(lines[3:]) == want_heads and len(lines) == 3 + len(FILES),
          f"{perr} {lines}")
    rep = rep or {}
    files = {f.get("path"): f for f in rep.get("files") or []}
    check("DS2-5: the report names the revision the answer gives, every listed file with its size and object id (the "
          "LFS sha256 where the tree has one), each file's HEAD status and redirect host, the redirect hosts as a set, "
          "and no problem",
          rep.get("revision") == REV_ and rep.get("revision_answer") == REV_ and sorted(files) == sorted(FILES)
          and files["big.bin"].get("lfs_sha256") == "f" * 64 and files["big.bin"].get("size") == 123456
          and files["config.json"].get("oid") == "3" * 40 and files["config.json"].get("lfs_sha256") is None
          and files["big.bin"].get("head_status") == 302 and files["big.bin"].get("redirect_host") == CDN
          and files["README.md"].get("head_status") == 307 and files["README.md"].get("redirect_host") == HF
          and files["config.json"].get("head_status") == 200 and files["config.json"].get("redirect_host") is None
          and rep.get("redirect_hosts") == sorted([CDN, HF]) and rep.get("problems") == [],
          json.dumps(rep)[:600])
    check("DS2-6: the licence is read from the card data, the tags and the card's front matter, one name; the tree agrees "
          "with the revision's siblings; the card's bytes and sha256 are named; the signed query is kept nowhere",
          rep.get("licence") == {"card_data": "apache-2.0", "tags": ["apache-2.0"], "front_matter": "apache-2.0"}
          and rep.get("tree_vs_siblings") == "equal" and (rep.get("card") or {}).get("bytes") == len(CARD)
          and (rep.get("card") or {}).get("sha256") is not None
          and "Signature" not in json.dumps(rep) and "Signature" not in json.dumps(rec, default=str),
          json.dumps({k: rep.get(k) for k in ("licence", "tree_vs_siblings", "card")}))
    check("DS2-7: the window's check is complete, no native hit, no problem", rec is not None and rec["check"]["complete"]
          and rec["check"]["native_hits"] == 0 and rec["problems"] == [], str((rec or {}).get("problems")))

    print("\n- the report's reading, offline -")
    r8, e8 = offline(rev={**REV_JSON, "sha": "9" * 40})
    check("DS2-8: a revision answer naming another commit is a problem by name",
          e8 is None and any("revision" in p and "9" * 8 in p for p in probs(r8[0])), str(e8 or probs(r8[0])))
    bad_paths = ["../evil.txt", "/abs.txt", "a//b.txt", "a\\b.txt", "x?y.txt", "./x.txt", "stopwords/../../up.txt", "c#d.txt"]
    r9, e9 = offline(tree=TREE + [{"type": "file", "path": p, "size": 1, "oid": "8" * 40} for p in bad_paths],
                     rev={**REV_JSON, "siblings": [{"rfilename": p} for p in FILES + bad_paths]})
    asked9 = [p for _m, p in (r9 or ({}, []))[1]]
    check("DS2-9: a listed path that climbs, is absolute, has an empty or dot segment, a backslash, a query or a fragment "
          "is never asked for; the report names each and a problem",
          e9 is None and sorted(asked9) == sorted(p.replace(" ", "%20") for p in FILES)
          and sorted(r9[0].get("refused_paths") or []) == sorted(bad_paths)
          and any("refused" in p for p in probs(r9[0])), str(e9 or (asked9, r9[0].get("refused_paths"), probs(r9[0]))))
    r10, e10 = offline(head={"config.json": {"status": 404, "ok": False, "error": "Refused: status 404"},
                             "big.bin": {"status": 302, "redirect_host": None}},
                       head_missing=("stopwords/english.txt",))
    p10 = probs((r10 or [None])[0])
    check("DS2-10: a failed HEAD, a redirect with no host to record, and a listed file whose HEAD left no answer are "
          "problems by name",
          e10 is None and any("config.json" in p and "404" in p for p in p10)
          and any("big.bin" in p and "no redirect host" in p for p in p10)
          and any("stopwords/english.txt" in p and "no HEAD answer" in p for p in p10), str(e10 or p10))
    r11, e11 = offline(rev={**REV_JSON, "cardData": {}, "tags": ["fastembed"]}, card=b"# BM25\n")
    r11b, e11b = offline(rev={**REV_JSON, "cardData": {"license": "mit"}})
    check("DS2-11: no licence named anywhere is a problem by name; a licence named differently by the card data and the "
          "front matter is a problem by name",
          e11 is None and any("licence" in p for p in probs(r11[0])) and e11b is None
          and any("licence" in p and "mit" in p and "apache-2.0" in p for p in probs(r11b[0])),
          str((e11 or probs(r11[0]), e11b or probs(r11b[0]))))
    r12, e12 = offline(rev={**REV_JSON, "siblings": [{"rfilename": p} for p in FILES + ["extra.txt"]]})
    r12b, e12b = offline(rev={k: v for k, v in REV_JSON.items() if k != "siblings"})
    check("DS2-12: a tree that does not list every sibling the revision names is a problem by name (a paged or partial "
          "tree); a revision answer with no siblings is said so, not a problem",
          e12 is None and any("extra.txt" in p for p in probs(r12[0])) and e12b is None
          and "no siblings" in str(r12b[0].get("tree_vs_siblings")) and probs(r12b[0]) == [],
          str((e12 or probs(r12[0]), e12b or (r12b[0].get("tree_vs_siblings"), probs(r12b[0])))))
    r13, e13 = offline(fail_a=("tree",))
    r13b, e13b = offline(fail_a=("card", "rev"))
    check("DS2-13: a failed tree leaves no HEAD job and is a problem by name; a failed card or revision request is a "
          "problem by name",
          e13 is None and r13[1] == [] and any("tree" in p and "failed" in p for p in probs(r13[0]))
          and e13b is None and any("card" in p and "failed" in p for p in probs(r13b[0]))
          and any("revision" in p and "failed" in p for p in probs(r13b[0])),
          str((e13 or (r13[1], probs(r13[0])), e13b or probs(r13b[0]))))
    cap = getattr(F, "D10_HEAD_CAP", None)
    many = [{"type": "file", "path": f"w/{i:04d}.txt", "size": 1, "oid": "a" * 40} for i in range((cap or 0) + 2)]
    r14, e14 = offline(tree=many, rev={**REV_JSON, "siblings": [{"rfilename": e["path"]} for e in many]})
    check("DS2-14: more listed files than D10_HEAD_CAP - the first D10_HEAD_CAP are asked, the rest named, a problem by "
          "name", isinstance(cap, int) and 0 < cap and e14 is None and len(r14[1]) == cap
          and any("D10_HEAD_CAP" in p and str(cap + 2) in p for p in probs(r14[0])),
          str(e14 or (cap, len((r14 or [None, []])[1]), probs((r14 or [None])[0]))))

    print("\n- main(): plan d10 only in window a7-discovery-2, on its declared entry - refused before any spawn -")

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
    m1 = run_main(["--window", "a7-discovery-2", "--plan", "d7", *tail], MANI)
    m2 = run_main(["--window", "a7-hf-d", "--plan", "d10", *tail], MANI)
    check("DS2-15: plan d10 outside window a7-discovery-2, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d10 runs in window a7-discovery-2" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-discovery-2", {})["max_redirects"] = 1
    m3 = run_main(["--window", "a7-discovery-2", "--plan", "d10", *tail], other)
    m4 = run_main(["--window", "a7-discovery-2", "--plan", "d10", *tail], MANI)
    check("DS2-16: a manifest whose a7-discovery-2 entry is not the declared shape is refused (rc 2, named); the real one "
          "gets to the window", m3[0] == 2 and "a7-discovery-2" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 discovery-2: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
