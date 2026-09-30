#!/usr/bin/env python3
"""PREREG-V3 A7 (the auditor's Q-ZT-1 = O-a, 2026-09-30): fetch_pins_a3.place_d8_eprint - Zep's e-print, a url pin of
PINS_A7, placed and made a pin_fill from a d8 run the auditor cleared, offline, on a temporary runs tree.

* only a run in the cleared list, each of its listed files at its sha256 (a moved byte refuses);
* the report clean, the pin a url pin of that window whose path is the report's own e-print URL, unfilled;
* the licence from the arXivRaw record's licence URL by a fixed table - an unknown URL refuses, never a guess;
* the placement is fetch_pins_a3's own (_place_one via place_window): the precheck of the record (clean, its hosts
  the manifest's, every issuer public), the file re-hashed against the report and the child's summary, moved - never
  copied - to <pins_root>/url/<sha256>/<basename>, an occupied destination never overwritten;
* the fill values are fill_inputs' own (a dry run of fill() on a copy), written to pin_fill.json only when nothing is
  wrong - and pins_apply takes that file as it takes a window's.

    python tests/_test_v3_place_d8.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import types
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


FP = _load("v3_fetch_pins_a3_pd8", ROOT / "research" / "v3" / "fetch_pins_a3.py")
CP = FP.CP
PA = _load("v3_pins_apply_pd8", ROOT / "research" / "v3" / "pins_apply.py")
MANI = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))
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
    """(value, None) or (None, "<Exception>: text") - a row's call never takes the suite down with it."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


TMP = Path(tempfile.mkdtemp(prefix="nvt3_pd8_"))
W, AID, VER, PIN = "a7-arxiv-src", "2501.13956", 1, "zep_paper_src"
URL = f"https://arxiv.org/src/{AID}v{VER}"
NC_SA = "http://creativecommons.org/licenses/by-nc-sa/4.0/"
EPRINT = b"\x1f\x8b\x08\x00" + b"synthetic e-print bytes, not the paper" * 40
PUBLIC = frozenset({"Certainly", "Sectigo Limited"})
DECLARED = (getattr(CP, "PINS_A7_DECLARED", {}) or {}).get(PIN)
HOSTS = MANI["windows"][W]["hosts"]


def world(tag: str, *, report: dict | None = None, issuer: str = "Certainly", eprint: bytes = EPRINT,
          run: str = "s9", clear: bool = True) -> dict:
    """A runs tree with one d8 run of ``window`` (record, report, the two units) and its cleared entry."""
    root = TMP / tag
    runs, polygon = root / "polygon" / "runs" / "v3", root / "polygon"
    base, units = runs / "_fetch" / W / run, runs / f"_fetch.{W}" / run / "fetch"
    for d in (base, units / "j0", units / "j1"):
        d.mkdir(parents=True, exist_ok=True)
    (units / "j1" / "eprint.bin").write_bytes(eprint)
    (units / "j0" / "oai_record.xml").write_bytes(b"<OAI-PMH/>")
    hop = {"ok": True, "status": 200, "issuer_o": issuer, "issuer_cn": f"{issuer} R1", "error": None}
    rec = {"window": W, "run": run, "hosts": list(HOSTS), "problems": [], "jobs": [
        {"index": 0, "unit": str(units / "j0"), "rc": 0, "summary": [
            {**hop, "id": f"oai:{AID}", "final_host": "oaipmh.arxiv.org", "bytes": 10, "sha256": sha(b"<OAI-PMH/>")}]},
        {"index": 1, "unit": str(units / "j1"), "rc": 0, "summary": [
            {**hop, "id": f"eprint:{AID}v{VER}", "final_host": "arxiv.org", "bytes": len(EPRINT), "sha256": sha(EPRINT)}]}]}
    rep = report if report is not None else {
        "arxiv_id": AID, "version": VER, "problems": [],
        "oai": {"status": 200, "licence": NC_SA, "versions": [{"version": "v1"}], "final_host": "oaipmh.arxiv.org"},
        "eprint": {"status": 200, "sha256": sha(EPRINT), "bytes": len(EPRINT), "kind": "tar.gz"}}
    rb = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode()
    pb = (json.dumps(rep, indent=1, sort_keys=True) + "\n").encode()
    (base / "record.json").write_bytes(rb)
    (base / "d8_report.json").write_bytes(pb)
    entry = {"window": W, "run": run, "kind": "report", "files": {
        f"_fetch/{W}/{run}/record.json": sha(rb), f"_fetch/{W}/{run}/d8_report.json": sha(pb)}}
    return {"runs": runs, "polygon": polygon, "pins_root": runs / "_pins", "hf_hub": polygon / "hf_cache" / "hub",
            "base": base, "unit": units / "j1", "cleared": [entry] if clear else []}


def pins_with(**over) -> dict:
    p = copy.deepcopy(DECLARED) if DECLARED else CP._pin("arm-source", ["S1", "S2", "S3"], "url", None, URL,
                                                         "CC-BY-NC-SA-4.0", W, 334)
    p.update(over)
    return {PIN: p}


def place(wd: dict, pins: dict | None = None, orgs=PUBLIC, run: str = "s9"):
    fn = getattr(FP, "place_d8_eprint", None)
    if fn is None:
        raise AttributeError("fetch_pins_a3 has no place_d8_eprint")
    return fn(wd["runs"], window=W, run=run, pin=PIN, pins=pins if pins is not None else pins_with(),
              cleared=wd["cleared"], pins_root=wd["pins_root"], polygon_root=wd["polygon"], hf_hub=wd["hf_hub"],
              manifest=MANI, issuer_orgs=orgs)


def dest_of(wd: dict) -> Path:
    return wd["pins_root"] / "url" / sha(EPRINT) / f"{AID}v{VER}"


try:
    print("- the declared pin (Q-ZT-1 = O-a) -")
    check("PD8-0: PINS_A7 declares zep_paper_src: an arm-source url pin of window a7-arxiv-src for the LME stands S1-S3 "
          "(zep-graphiti's Point V, rev1 line 334), its path Zep's e-print URL https://arxiv.org/src/2501.13956v1, "
          "CC-BY-NC-SA-4.0 declared, unfilled until its pin_fill is applied",
          bool(DECLARED) and DECLARED["role"] == "arm-source" and DECLARED["stands"] == ("S1", "S2", "S3")
          and DECLARED["source"] == "url" and DECLARED["path"] == URL and DECLARED["licence"] == "CC-BY-NC-SA-4.0"
          and DECLARED["window"] == W and DECLARED["prereg"] == 334 and DECLARED["sha256"] is None
          and "zep-graphiti" in DECLARED["note"], str(DECLARED))

    print("\n- a clean cleared run: placed, then its pin_fill -")
    wd = world("ok")
    res, err = holds(lambda: place(wd))
    pr = json.loads((wd["base"] / "place_record.json").read_bytes()) if (wd["base"] / "place_record.json").is_file() else {}
    pf_path = wd["base"] / "pin_fill.json"
    pf = json.loads(pf_path.read_bytes()) if pf_path.is_file() else {}
    check("PD8-1: from a clean cleared d8 run the e-print is MOVED to <pins_root>/url/<sha256>/2501.13956v1 (fetch_pins_a3's "
          "own placement: verified-placed, re-hashed), the unit no longer holds it, and the place record names the d8 "
          "record it read by sha256 and the request that is the pin's",
          err is None and res["problems"] == [] and dest_of(wd).is_file() and sha(dest_of(wd).read_bytes()) == sha(EPRINT)
          and not (wd["unit"] / "eprint.bin").exists() and pr.get("problems") == [] and pr.get("window") == W
          and (pr.get("entries") or {}).get(PIN, {}).get("state") == "verified-placed"
          and pr.get("source_record") == {"path": "record.json", "sha256": wd["cleared"][0]["files"][f"_fetch/{W}/s9/record.json"],
                                          "request": f"eprint:{AID}v{VER}"},
          f"{err or res} {pr}")
    check("PD8-2: pin_fill.json holds exactly the pin: revision = its sha256 (a url pin, as fill_inputs gives it), the "
          "sha256 and size of the bytes, the licence CC-BY-NC-SA-4.0 from the arXivRaw record's licence URL, named",
          pf.get("window") == W and pf.get("run") == "s9" and sorted(pf.get("pins") or {}) == [PIN]
          and pf["pins"][PIN]["revision"] == sha(EPRINT) and pf["pins"][PIN]["sha256"] == sha(EPRINT)
          and pf["pins"][PIN]["bytes"] == len(EPRINT) and pf["pins"][PIN]["licence_found"] == "CC-BY-NC-SA-4.0"
          and NC_SA in pf["pins"][PIN]["licence_source"], str(pf))
    got, perr = holds(lambda: PA.plan_values([(PA.read_fill(pf_path, sha(pf_path.read_bytes())), sha(pf_path.read_bytes()))],
                                             types.SimpleNamespace(PINS_A7=CP.PINS_A7_DECLARED, fill=CP.fill,
                                                                   PinRefused=CP.PinRefused), table="PINS_A7"))
    check("PD8-3: pins_apply takes that pin_fill as it takes a window's (read_fill beside a clean record and place record, "
          "plan_values through fill() on a copy of the declared PINS_A7 - the real table has zep_paper_src filled now, "
          "a pin is filled once) - one value, from a7-arxiv-src s9",
          perr is None and list(got[0]) == [PIN] and got[0][PIN]["sha256"] == sha(EPRINT)
          and got[0][PIN]["from"].startswith(f"{W} s9 pin_fill"), str(perr or got))
    first = {n: (wd["base"] / n).read_bytes() for n in ("place_record.json", "pin_fill.json") if (wd["base"] / n).is_file()}
    again, aerr = holds(lambda: place(wd))
    check("PD8-4: a second placement of the same run is refused before anything is rewritten (its place record exists); "
          "the first placement, its place record and its pin_fill stand byte for byte",
          aerr is None and any("already placed" in p for p in again["problems"]) and dest_of(wd).is_file()
          and len(first) == 2 and all((wd["base"] / n).read_bytes() == b for n, b in first.items()), str(aerr or again))

    print("\n- refusals: nothing moved, no pin_fill -")

    def untouched(wd_: dict, res_, err_) -> bool:
        return (err_ is None and bool(res_["problems"]) and (wd_["unit"] / "eprint.bin").is_file()
                and not dest_of(wd_).exists() and not (wd_["base"] / "pin_fill.json").exists())

    w1 = world("notcleared", clear=False)
    r1, e1 = holds(lambda: place(w1))
    check("PD8-5: a run the auditor has not cleared is refused by name", untouched(w1, r1, e1)
          and any("not cleared" in p for p in r1["problems"]), str(e1 or r1))
    w2 = world("moved")
    (w2["base"] / "d8_report.json").write_bytes((w2["base"] / "d8_report.json").read_bytes() + b" ")
    r2, e2 = holds(lambda: place(w2))
    check("PD8-6: a cleared file whose bytes moved is refused, naming the file", untouched(w2, r2, e2)
          and any("d8_report.json" in p and "sha256" in p for p in r2["problems"]), str(e2 or r2))
    bad = {"arxiv_id": AID, "version": VER, "problems": ["the e-print was not asked for"],
           "oai": {"licence": NC_SA}, "eprint": {"status": 200, "sha256": sha(EPRINT), "bytes": len(EPRINT)}}
    w3 = world("reportprob", report=bad)
    r3, e3 = holds(lambda: place(w3))
    check("PD8-7: a report with problems is refused", untouched(w3, r3, e3)
          and any("report" in p and "problem" in p for p in r3["problems"]), str(e3 or r3))
    rep_nd = {"arxiv_id": AID, "version": VER, "problems": [],
              "oai": {"status": 200, "licence": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/", "versions": [{"version": "v1"}]},
              "eprint": {"status": 200, "sha256": sha(EPRINT), "bytes": len(EPRINT), "kind": "tar.gz"}}
    w4 = world("licence", report=rep_nd)
    r4, e4 = holds(lambda: place(w4))
    check("PD8-8: a licence URL outside the fixed table is refused by name - never mapped by guess", untouched(w4, r4, e4)
          and any("licence" in p and "nonexclusive-distrib" in p for p in r4["problems"]), str(e4 or r4))
    w5 = world("path")
    r5, e5 = holds(lambda: place(w5, pins=pins_with(path="https://arxiv.org/src/2501.99999v1")))
    check("PD8-9: a pin whose path is not the report's e-print URL is refused, naming both", untouched(w5, r5, e5)
          and any("2501.99999v1" in p and URL in p for p in r5["problems"]), str(e5 or r5))
    w6 = world("diskbytes")
    (w6["unit"] / "eprint.bin").write_bytes(EPRINT + b"x")
    r6, e6 = holds(lambda: place(w6))
    check("PD8-10: bytes on disk that are not the report's (and the child's summary's) are not placed (P6/P7/P8)",
          untouched(w6, r6, e6) and any(p[:2] in ("P6", "P7", "P8") for p in r6["problems"]), str(e6 or r6))
    w7 = world("issuer", issuer="Evil CA")
    r7, e7 = holds(lambda: place(w7))
    check("PD8-11: a record whose issuer is not public is refused by the precheck (P3), before any file moves",
          untouched(w7, r7, e7) and any(p.startswith("P3") for p in r7["problems"]), str(e7 or r7))
    w8 = world("occupied")
    dest_of(w8).parent.mkdir(parents=True, exist_ok=True)
    dest_of(w8).write_bytes(b"other bytes")
    r8, e8 = holds(lambda: place(w8))
    check("PD8-12: a destination that holds other bytes is never overwritten (P12)",
          e8 is None and any(p.startswith("P12") for p in r8["problems"]) and dest_of(w8).read_bytes() == b"other bytes"
          and (w8["unit"] / "eprint.bin").is_file() and not (w8["base"] / "pin_fill.json").exists(), str(e8 or r8))
    w9 = world("filled")
    r9, e9 = holds(lambda: place(w9, pins=pins_with(sha256="0" * 64, bytes=1, revision="0" * 64)))
    check("PD8-13: a pin already filled is refused before anything moves (a pin is filled once), no pin_fill",
          untouched(w9, r9, e9) and any("already" in p for p in r9["problems"]), str(e9 or r9))

    print("\n- main(): --place-d8, offline, on the real lists -")
    from types import SimpleNamespace  # noqa: E402,PLC0415
    F7 = _load("v3_freeze_a7_pd8", ROOT / "research" / "v3" / "freeze_a7.py")
    F3 = _load("v3_freeze_a3_pd8", ROOT / "research" / "v3" / "freeze_a3.py")
    CALLS: list = []

    def run_main(argv):
        saved = (FP._load, getattr(FP, "place_d8_eprint", None))
        real_load = FP._load
        runs = TMP / "main" / "runs"
        stub_l = SimpleNamespace(Contract=SimpleNamespace(default=lambda: SimpleNamespace(runs_root=runs, polygon_root=TMP / "main")),
                                 network_via_port=lambda c_: None)
        FP._load = lambda name, path: stub_l if name == "v3_launch" else real_load(name, path)

        def fake(runs_root, **kw):
            CALLS.append({"runs_root": runs_root, **kw})
            return {"problems": []}
        FP.place_d8_eprint = fake
        try:
            return FP.main(argv)
        except SystemExit as e:
            return f"exit {e.code}"
        finally:
            FP._load, FP.place_d8_eprint = saved
    rc1 = run_main(["--window", W, "--run", "s3", "--place-d8", PIN])
    kw = CALLS[-1] if CALLS else {}
    check("PD8-14: main --window a7-arxiv-src --run s3 --place-d8 zep_paper_src places offline (no hop asked) with the real "
          "lists: freeze_a7's CLEARED_A7, PINS_A7 (the window's table), freeze_a3's public issuers (Certainly among them), "
          "the pins root <runs>/_pins",
          rc1 == 0 and kw.get("window") == W and kw.get("run") == "s3" and kw.get("pin") == PIN
          and kw.get("cleared") == F7.CLEARED_A7 and kw.get("pins") is CP.PINS_A7
          and kw.get("issuer_orgs") == F3.PUBLIC_ISSUER_ORGS and "Certainly" in (kw.get("issuer_orgs") or ())
          and kw.get("pins_root") == TMP / "main" / "runs" / "_pins", f"{rc1} {sorted(kw)}")
    CALLS.clear()
    rc2 = run_main(["--window", W, "--run", "s3"])
    rc3 = run_main(["--window", "a7-github", "--run", "g9", "--place-d8", PIN])
    check("PD8-15: a7-arxiv-src without --place-d8, and --place-d8 in any other window, are refused (rc 2) - nothing placed",
          rc2 == 2 and rc3 == 2 and not CALLS, f"{rc2} {rc3} {len(CALLS)}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 place d8: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
