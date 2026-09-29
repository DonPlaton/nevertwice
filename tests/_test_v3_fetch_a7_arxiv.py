#!/usr/bin/env python3
"""PREREG-V3 A7 phase 2 (the auditor's R3 on Zep's LME template): research/v3/fetch_a3.py plan d6 - the a7-arxiv window,
metadata only - offline, with real processes under the contract (the catcher-only proxy, one fetch-child job, a fake
export.arxiv.org behind a fake hop on a local TLS server; the test hands the children its CA file).

* the window is the manifest's "a7-arxiv" entry, the single source: one host (export.arxiv.org), no redirect, the
  search query (the product's name in the title - never a paper id from memory) and the page size - anything else in
  the entry is refused before any spawn;
* exactly one GET: the arXiv API's query with the declared search, sorted by submission date - no link in the answer
  is followed and no paper is fetched;
* the report: the total the API names, whether the page was full, and per entry its arXiv id and version, title,
  published and updated dates, authors and its abstract-page link - as text, for the auditor to choose from; an answer
  that declares a DOCTYPE (entities) or does not parse is a problem by name and yields no entry.

    python tests/_test_v3_fetch_a7_arxiv.py
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


L = _load("v3_launch_d6", ROOT / "research" / "v3" / "launch.py")
F = _load("v3_fetch_a3_d6", ROOT / "research" / "v3" / "fetch_a3.py")
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
REAL = MANI["windows"].get("a7-arxiv") or {}
print("- the manifest's a7-arxiv entry (the auditor's R3) -")
decl, derr = holds(lambda: F.d6_decl(MANI))
check("the manifest declares a7-arxiv as plan d6 reads it: export.arxiv.org only, no redirect, the search 'ti:zep' (the "
      "product's name in the title) and a page of 50", derr is None and decl["hosts"] == ["export.arxiv.org"]
      and decl["max_redirects"] == 0 and decl["search_query"] == "ti:zep" and decl["max_results"] == 50, str(derr or decl))
bad_cases = {
    "an extra key": {**REAL, "id_list": "2501.00001"},
    "another host": {**REAL, "hosts": ["export.arxiv.org", "arxiv.org"]},
    "a redirect": {**REAL, "max_redirects": 1},
    "a query with an ampersand": {**REAL, "search_query": "ti:zep&max_results=1000"},
    "an empty query": {**REAL, "search_query": ""},
    "a page of 0": {**REAL, "max_results": 0},
    "a page over 100": {**REAL, "max_results": 500},
}
refused = {}
for label, entry in bad_cases.items():
    _v, e = holds(lambda entry=entry: F.d6_decl({"windows": {"a7-arxiv": entry}}))
    refused[label] = bool(e) and e.startswith("D6ManifestError")
check("DF: an entry with anything but the declared shape is refused by name (D6ManifestError) - "
      + ", ".join(bad_cases), all(refused.values()), str([k for k, v in refused.items() if not v]))

TMP = Path(tempfile.mkdtemp(prefix="nvt3_d6_"))
AX = "export.arxiv.org"
made = TF.make_test_cert(TMP / "cert", AX, org="Let's Encrypt")
if made is None:
    print("  SKIP the window checks: neither cryptography nor openssl is available (not passed)")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nv3 fetch a7 arxiv: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)

DECL = {"hosts": ["export.arxiv.org"], "purpose": "test", "search_query": "ti:zep", "max_results": 2, "max_redirects": 0}
Q = "/api/query?search_query=ti:zep&start=0&max_results=2&sortBy=submittedDate&sortOrder=ascending"
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>3</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2501.00001v2</id>
    <updated>2025-01-20T10:00:00Z</updated>
    <published>2025-01-10T09:00:00Z</published>
    <title>Zep: A Temporal
      Memory Layer</title>
    <author><name>A. Author</name></author><author><name>B. Author</name></author>
    <link href="http://arxiv.org/abs/2501.00001v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2501.00001v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <updated>1999-01-01T00:00:00Z</updated>
    <published>1999-01-01T00:00:00Z</published>
    <title>Zeppelins in string theory</title>
    <author><name>C. Author</name></author>
    <link href="http://arxiv.org/abs/hep-th/9901001v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


class AnySampler:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def window(run: str, body: bytes):
    srv = TF.TlsHttpServer(made[0], made[1], {Q: (200, [("Content-Type", "application/atom+xml")], body)})
    hop = TF.TunnelHop(srv.port)
    base = TMP / run
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    jobs, jerr = holds(lambda: F.d6_jobs(DECL))
    rec, rerr = holds(lambda: F.run_child_window(
        c, L, window="a7-arxiv", hosts=F.D6_HOSTS, jobs=jobs, python=Path(sys.executable), via_port=hop.port, run=run,
        parent_env=os.environ, native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
        fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), child_env_extra={"SSL_CERT_FILE": str(made[0])},
        volume=TMP)) if jerr is None else (None, jerr)
    asked = [h.split(b" ")[1].decode() for h in srv.heads]
    rep, perr = holds(lambda: F.d6_report(rec, DECL)) if rec is not None else (None, rerr)
    for x in (hop, srv):
        try:
            x.close()
        except Exception:  # noqa: BLE001
            pass
    return asked, rec, rep, perr


try:
    print("\n- the window, offline -")
    asked, rec, rep, perr = window("t1", ATOM)
    check("D6-1: exactly one GET - the API query with the declared search, one page, oldest first; no link followed",
          perr is None and asked == [Q], f"{perr} {asked}")
    rep = rep or {}
    ents = rep.get("entries") or []
    check("D6-2: the report names the API's total and that the page was full (2 of 2 asked, 3 in all)",
          rep.get("total") == 3 and rep.get("page_full") is True and len(ents) == 2,
          json.dumps({k: rep.get(k) for k in ("total", "page_full")}))
    check("D6-3: each entry - its arXiv id and version apart (a new-style and an old-style id), the title with its white "
          "space collapsed, the dates, the authors and the abstract-page link, as text",
          ents[:1] == [{"id": "2501.00001", "version": 2, "title": "Zep: A Temporal Memory Layer",
                        "published": "2025-01-10T09:00:00Z", "updated": "2025-01-20T10:00:00Z",
                        "authors": ["A. Author", "B. Author"], "abs": "http://arxiv.org/abs/2501.00001v2"}]
          and ents[1:2] and ents[1]["id"] == "hep-th/9901001" and ents[1]["version"] == 1, json.dumps(ents)[:500])
    check("D6-4: the window's check is complete, no native hit, no problem", rec is not None and rec["check"]["complete"]
          and rec["check"]["native_hits"] == 0 and rec["problems"] == [] and rep.get("problems") == [],
          str((rec or {}).get("problems")) + str(rep.get("problems")))
    print("\n- answers that must not be read -")
    evil = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaa">]><feed xmlns="http://www.w3.org/2005/Atom">&a;</feed>'
    _a2, _r2, rep2, perr2 = window("t2", evil)
    check("D6-5: an answer that declares a DOCTYPE (entities) is refused by name - no entry is read from it",
          perr2 is None and (rep2 or {}).get("entries") == [] and any("DOCTYPE" in p for p in (rep2 or {}).get("problems") or []),
          f"{perr2} {rep2}")
    _a3, _r3, rep3, perr3 = window("t3", b"<feed><entry>")
    check("D6-6: an answer that does not parse is a problem by name, no entry", perr3 is None
          and (rep3 or {}).get("entries") == [] and any("does not parse" in p for p in (rep3 or {}).get("problems") or []),
          f"{perr3} {rep3}")

    print("\n- main(): plan d6 only in window a7-arxiv, on its declared entry - refused before any spawn -")
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
    m1 = run_main(["--window", "a7-arxiv", "--plan", "d5", *tail], MANI)
    m2 = run_main(["--window", "a7-docs", "--plan", "d6", *tail], MANI)
    check("D6-7: plan d6 outside window a7-arxiv, and that window with another plan, are refused (rc 2, named)",
          all(rc == 2 and "plan d6 runs in window a7-arxiv" in msg for rc, msg in (m1, m2)), f"{m1} {m2}")
    other = json.loads(json.dumps(MANI))
    other["windows"].setdefault("a7-arxiv", {})["hosts"] = ["export.arxiv.org", "arxiv.org"]
    m3 = run_main(["--window", "a7-arxiv", "--plan", "d6", *tail], other)
    m4 = run_main(["--window", "a7-arxiv", "--plan", "d6", *tail], MANI)
    check("D6-8: a manifest whose a7-arxiv entry is not the declared shape is refused (rc 2, named); the real one gets to "
          "the window", m3[0] == 2 and "a7-arxiv" in m3[1] and m4[0] == "spawned", f"{m3} {m4}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 fetch a7 arxiv: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
