#!/usr/bin/env python3
"""PREREG-V3 plan step A3.k: research/v3/freeze_a3.py on a temporary runs tree, and the committed fragment, offline.

* every cleared record is read by its sha256 - missing or moved bytes stop the build, nothing is written;
* the runs that were not cleared (a3-hf h1, facts j1, facts j2) are listed by sha256 with their reasons, and their
  records are bound by sha256 too (the auditor's A3.k addition);
* every TLS issuer organisation is public (R-A3-7), or the build stops;
* every non-alias v3 pin is filled, or the build stops;
* models, the D1 tag, the local-v2 places, the venvs and the facts record come from their cleared records;
* the same records give the same bytes (sorted JSON, LF);
* the declared lists name exactly the runs the auditor cleared and failed, and the pin_fill shas are the committed
  evidence's (tests/fixtures/v3_pin_fill);
* the committed research/v3/freeze_a3.json, once there, agrees with the declared lists and the pin table.

    python tests/_test_v3_freeze_a3.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
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


F = _load("v3_freeze_a3", ROOT / "research" / "v3" / "freeze_a3.py")
CP = _load("v3_corpus_pin_fz", ROOT / "research" / "v3" / "corpus_pin_v3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except F.FreezeRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_freeze_"))


def put(root: Path, rel: str, obj: dict) -> str:
    f = root / Path(*rel.split("/"))
    f.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj).encode("utf-8")
    f.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


D1_PROBLEMS = ["job 2: the fetch child exited with 3", "job 2 request gh:Org/Moved: Refused: more redirects than the job allows",
               "job 2 request ghhead:Org/Moved: Refused: more redirects than the job allows"]


def tree(tag: str, *, issuer_org: str = "Amazon", d1_problems: list | None = None, tool_org: str = "Microsoft Corporation",
         i1_hosts: tuple = ("pypi.org", "files.pythonhosted.org")) -> tuple[Path, list, list, Path]:
    """A fake runs tree with one record of every kind; (runs_root, cleared, failed, prereg)."""
    r = TMP / tag / "runs"
    cleared = [
        {"window": "py-base-314", "run": "b1", "kind": "tool", "files": {"_tools/py/py.json": put(
            r, "_tools/py/py.json", {"version": "3.14.4", "python_exe_sha256": "e" * 64, "nupkg_sha256": "n" * 64,
                                     "tools_tree_sha256": "t" * 64, "package": "https://api.nuget.org/x.nupkg",
                                     "peer": {"issuer_o": tool_org, "issuer_cn": "Microsoft TLS G2 ECC CA OCSP 02",
                                              "subject_cn": "api.nuget.org"}})}},
        {"window": "a3-pyarrow", "run": "i1", "kind": "install", "files": {
            "_fetch/a3-pyarrow/i1/record.json": put(r, "_fetch/a3-pyarrow/i1/record.json", {
                "hosts": list(i1_hosts), "issuers": [["pypi.org", "GlobalSign nv-sa", "G1"]]}),
            "_install/a3-pyarrow/i1/install_record.json": put(r, "_install/a3-pyarrow/i1/install_record.json", {
                "wheels": {"pyarrow": {"version": "25.0.1", "sha256": "w" * 64}}, "installed_set_sha256": "s" * 64,
                "installed_files": 761, "tag": "cp314", "pip": {"version": "26.0.1", "certifi_sha256": "c" * 64, "trust": "certifi", "args": ["x"]}})}},
        {"window": "a3-hf", "run": "h2", "kind": "fetch", "files": {
            "_fetch/a3-hf/h2/record.json": put(r, "_fetch/a3-hf/h2/record.json", {"issuers": [
                ["huggingface.co", issuer_org, "Amazon RSA 2048 M01"], ["huggingface.co", "Amazon", "Amazon RSA 2048 M01"],
                ["us.aws.cdn.hf.co", "Amazon", "Amazon RSA 2048 M04"]]}),
            "_fetch/a3-hf/h2/pin_fill.json": put(r, "_fetch/a3-hf/h2/pin_fill.json", {"window": "a3-hf", "run": "h2", "pins": {}})},
         "note": "a ruled note"},
        {"window": "facts", "run": "j3", "kind": "facts", "files": {"_facts/j3/facts.json": put(r, "_facts/j3/facts.json", {"problems": []})}},
        {"window": "a3-discovery", "run": "d1", "kind": "discovery", "files": {"_fetch/a3-discovery/d1/record.json": put(
            r, "_fetch/a3-discovery/d1/record.json", {"problems": d1_problems if d1_problems is not None else D1_PROBLEMS})}, "note": "a ruled note",
         "problems_verbatim": list(D1_PROBLEMS), "excluded": [{"job": 2, "requests": ["gh:Org/Moved", "ghhead:Org/Moved"]}],
         "feeds_pins": {"0": "revisions"}},
        {"window": "ollama", "run": "o1", "kind": "inventory", "files": {"_inventory/ollama/o1/record.json": put(
            r, "_inventory/ollama/o1/record.json", {"version": "0.34.4", "pins": {"bge-m3": {
                "role": "D1 base", "found": "bge-m3:latest", "digest": "7" * 64, "digest12": "7" * 12, "pin": None, "present": True}}})}},
        {"window": "d1-tag", "run": "d1", "kind": "d1-tag", "files": {"_d1tag/d1/record.json": put(
            r, "_d1tag/d1/record.json", {"tag": {"name": "nvt3-bge-m3-d1:latest", "digest": "6" * 64},
                                         "base": {"name": "bge-m3:latest", "digest": "7" * 64}, "modelfile_sha256": "m" * 64,
                                         "version": "0.34.4", "ps": {}})}},
        {"window": "local-v2", "run": "l1", "kind": "local-v2", "files": {"_fetch/local-v2/l1/place_record.json": put(
            r, "_fetch/local-v2/l1/place_record.json", {"placed": {"locomo10": {"status": "placed", "dest": "local-v2/x/locomo10.json"}}})}},
    ]
    failed = [{"window": "facts", "run": "j1", "files": {"_facts/j1/facts.json": put(r, "_facts/j1/facts.json", {"problems": ["x"]})},
               "reason": "a named reason"}]
    prereg = TMP / tag / "PREREG.md"
    prereg.write_bytes(b"# prereg\n")
    return r, cleared, failed, prereg


print("\n- a clean build -")
runs, cl, fl, pre = tree("ok")
try:
    fz = F.build(runs, pins=CP.PINS, cleared=cl, failed=fl, prereg=pre)
except F.FreezeRefused as e:                      # a refusal of the clean tree is a named failure, not a crash
    check("the clean fake tree builds (every contacted host's issuer recorded - F1 the tool's peer included)", False, str(e))
    print(f"\nv3 freeze a3: {PASSED} passed, {FAILED} failed")
    sys.exit(1)
check("windows: every cleared run with its kind, files by sha256 and a ruled note",
      [{k: v for k, v in w.items() if k != "problems"} for w in fz["windows"]]
      == [{k: e[k] for k in ("window", "run", "kind", "files", "note", "problems_verbatim", "excluded", "feeds_pins") if k in e}
          for e in cl], str(fz["windows"][:2]))
check("windows: each record's problem count, from the record itself",
      next(w for w in fz["windows"] if w["run"] == "d1" and w["window"] == "a3-discovery")["problems"]
      == {"_fetch/a3-discovery/d1/record.json": 3}
      and next(w for w in fz["windows"] if w["run"] == "j3")["problems"] == {"_facts/j3/facts.json": 0}, str(fz["windows"][-2:]))
check("failed_runs: the not-cleared runs by sha256, with their reasons and problem counts",
      fz["failed_runs"] == [{"window": "facts", "run": "j1", "files": fl[0]["files"], "reason": "a named reason",
                             "problems": {"_facts/j1/facts.json": 1}}], str(fz["failed_runs"]))
check("issuers: per host, each (organisation, CN) once, sorted - from the fetch and install records, and F1 the tool "
      "record's TLS peer", fz["issuers"] == {"api.nuget.org": [["Microsoft Corporation", "Microsoft TLS G2 ECC CA OCSP 02"]],
                                             "huggingface.co": [["Amazon", "Amazon RSA 2048 M01"]],
                                             "pypi.org": [["GlobalSign nv-sa", "G1"]],
                                             "us.aws.cdn.hf.co": [["Amazon", "Amazon RSA 2048 M04"]]}, str(fz["issuers"]))
check("F2: a host contacted with no recorded issuer is listed with its declared reason",
      fz["issuers_unrecorded"] == {"files.pythonhosted.org": F.ISSUERS_UNRECORDED["files.pythonhosted.org"]},
      str(fz.get("issuers_unrecorded")))
check("models: the inventory's version and pins", fz["models"] == {"ollama_version": "0.34.4", "run": "o1", "pins": {"bge-m3": {
    "role": "D1 base", "found": "bge-m3:latest", "digest": "7" * 64, "digest12": "7" * 12, "pin": None}}}, str(fz.get("models")))
check("d1_tag: the tag's digest, its base, its Modelfile", fz["d1_tag"] == {
    "tag": {"name": "nvt3-bge-m3-d1:latest", "digest": "6" * 64}, "base": {"name": "bge-m3:latest", "digest": "7" * 64},
    "modelfile_sha256": "m" * 64, "version": "0.34.4", "run": "d1"}, str(fz.get("d1_tag")))
check("local_v2: the v2 pins' polygon places", fz["local_v2"] == {"run": "l1", "placed": {
    "locomo10": {"status": "placed", "dest": "local-v2/x/locomo10.json"}}})
check("venvs: py314 and v3_data (pyarrow wheel, installed set, pip and its trust)",
      fz["venvs"]["py314"] == {"version": "3.14.4", "python_exe_sha256": "e" * 64, "nupkg_sha256": "n" * 64, "tools_tree_sha256": "t" * 64}
      and fz["venvs"]["v3_data"] == {"wheels": {"pyarrow": {"version": "25.0.1", "sha256": "w" * 64}}, "installed_set_sha256": "s" * 64,
                                     "installed_files": 761, "tag": "cp314",
                                     "pip": {"version": "26.0.1", "certifi_sha256": "c" * 64, "trust": "certifi"}}, str(fz.get("venvs")))
check("facts: the cleared record by sha256, for rev2's slot at A10 only",
      fz["facts"]["sha256"] == next(e for e in cl if e["run"] == "j3")["files"]["_facts/j3/facts.json"] and "A10" in fz["facts"]["use"])
check("pins: the table as filled", fz["pins"] == F.pins_section(CP.PINS) and len(fz["pins"]) == len(CP.PINS))
check("C1: the pins from excluded requests are counted per ruled record - 0", fz["pins_from_excluded_requests"] == {"a3-discovery d1": 0},
      str(fz.get("pins_from_excluded_requests")))
check("prereg_rev1: the sha256 of the revision file", fz["prereg_rev1"] == hashlib.sha256(pre.read_bytes()).hexdigest())
b1, b2 = F.render(fz), F.render(F.build(runs, pins=CP.PINS, cleared=cl, failed=fl, prereg=pre))
check("the same records give the same bytes: sorted JSON, LF, a final newline",
      b1 == b2 and b"\r" not in b1 and b1.endswith(b"}\n") and json.loads(b1) == fz
      and b1 == (json.dumps(json.loads(b1), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode())

print("\n- refusals: nothing is built -")
runs2, cl2, fl2, pre2 = tree("moved")
(runs2 / "_facts" / "j3" / "facts.json").write_bytes(b'{"problems": ["edited"]}')
check("a cleared record whose bytes moved stops the build, named", refused(
    lambda: F.build(runs2, pins=CP.PINS, cleared=cl2, failed=fl2, prereg=pre2), "_facts/j3/facts.json: sha256"))
runs3, cl3, fl3, pre3 = tree("failed_moved")
(runs3 / "_facts" / "j1" / "facts.json").write_bytes(b'{}')
check("a failed run's record is bound by sha256 too", refused(
    lambda: F.build(runs3, pins=CP.PINS, cleared=cl3, failed=fl3, prereg=pre3), "_facts/j1/facts.json: sha256"))
runs4, cl4, fl4, pre4 = tree("missing")
(runs4 / "_inventory" / "ollama" / "o1" / "record.json").unlink()
check("a missing cleared record stops the build", refused(
    lambda: F.build(runs4, pins=CP.PINS, cleared=cl4, failed=fl4, prereg=pre4), "no such record"))
runs5, cl5, fl5, pre5 = tree("issuer", issuer_org="Corp Proxy Inspection CA")
check("a non-public issuer organisation stops the build (R-A3-7)", refused(
    lambda: F.build(runs5, pins=CP.PINS, cleared=cl5, failed=fl5, prereg=pre5), "not a public issuer"))
runs6, cl6, fl6, pre6 = tree("no_note")
next(e for e in cl6 if e["run"] == "d1").pop("note")
check("a cleared record with problems and no ruling note stops the build", refused(
    lambda: F.build(runs6, pins=CP.PINS, cleared=cl6, failed=fl6, prereg=pre6), "problem(s) and no ruling note"))
runs7, cl7, fl7, pre7 = tree("no_excluded")
next(e for e in cl7 if e["run"] == "d1").pop("excluded")
check("C3: ... and one with a note but no excluded list stops it too", refused(
    lambda: F.build(runs7, pins=CP.PINS, cleared=cl7, failed=fl7, prereg=pre7), "no ruling note with an excluded list"))
runs8, cl8, fl8, pre8 = tree("unexplained", d1_problems=D1_PROBLEMS[:2] + ["job 1 request tree:x: 404"])
next(e for e in cl8 if e["run"] == "d1")["problems_verbatim"] = D1_PROBLEMS[:2] + ["job 1 request tree:x: 404"]
check("C3: a problem no excluded request explains stops the build", refused(
    lambda: F.build(runs8, pins=CP.PINS, cleared=cl8, failed=fl8, prereg=pre8), "a problem no excluded request explains"))
runs9, cl9, fl9, pre9 = tree("orphan_exit")
next(e for e in cl9 if e["run"] == "d1")["excluded"] = [{"job": 2, "requests": []}, {"job": 5, "requests": ["gh:Org/Moved", "ghhead:Org/Moved"]}]
check("C2: an exit line whose job has no excluded request stays unexplained", refused(
    lambda: F.build(runs9, pins=CP.PINS, cleared=cl9, failed=fl9, prereg=pre9), "a problem no excluded request explains"))
runs10, cl10, fl10, pre10 = tree("traced")
check("C3: a pin whose licence was read by an excluded request stops the build", refused(
    lambda: F.build(runs10, pins=CP.PINS, filled={"x_pin": {"licence_source": "d1 repo Org/Moved"}}, cleared=cl10,
                    failed=fl10, prereg=pre10), "traces to an excluded request"))
runs11, cl11, fl11, pre11 = tree("verbatim")
next(e for e in cl11 if e["run"] == "d1")["problems_verbatim"] = D1_PROBLEMS[:2]
check("the entry must name its record's problems verbatim", refused(
    lambda: F.build(runs11, pins=CP.PINS, cleared=cl11, failed=fl11, prereg=pre11), "not the ones its entry names verbatim"))
runs12, cl12, fl12, pre12 = tree("tool_issuer", tool_org="Corp Proxy Inspection CA")
check("F1: a tool record whose TLS peer issuer is not public stops the build", refused(
    lambda: F.build(runs12, pins=CP.PINS, cleared=cl12, failed=fl12, prereg=pre12), "not a public issuer"))
runs13, cl13, fl13, pre13 = tree("host_unrecorded", i1_hosts=("pypi.org", "files.pythonhosted.org", "mirror.example"))
check("F2: a contacted host with neither a recorded issuer nor a declared reason stops the build", refused(
    lambda: F.build(runs13, pins=CP.PINS, cleared=cl13, failed=fl13, prereg=pre13), "mirror.example was contacted"))
runs14, cl14, fl14, pre14 = tree("job_other", d1_problems=D1_PROBLEMS[:2] + ["job 2: the child was killed by its window"])
next(e for e in cl14 if e["run"] == "d1")["problems_verbatim"] = D1_PROBLEMS[:2] + ["job 2: the child was killed by its window"]
check("G2: a job-level problem in an excluded job that is not its exit stays unexplained", refused(
    lambda: F.build(runs14, pins=CP.PINS, cleared=cl14, failed=fl14, prereg=pre14), "a problem no excluded request explains"))
runs15, cl15, fl15, pre15 = tree("repo_trace")
pins15 = copy.deepcopy(CP.PINS)
pins15["amem_source"]["repo"] = "Org/Moved"
check("G3: a pin whose REPO is an excluded request's repo is refused even when its licence_source names another run",
      refused(lambda: F.build(runs15, pins=pins15, filled={"amem_source": {"licence_source": "d2 repo Other/Repo"}},
                              cleared=cl15, failed=fl15, prereg=pre15), "traces to an excluded request"))
runs16, cl16, fl16, pre16 = tree("other_job_req", d1_problems=["job 3: the fetch child exited with 3",
                                                               "job 3 request gh:Org/Moved: Refused: more redirects than the job allows"])
e16 = next(e for e in cl16 if e["run"] == "d1")
e16["problems_verbatim"] = ["job 3: the fetch child exited with 3", "job 3 request gh:Org/Moved: Refused: more redirects than the job allows"]
check("C2: an excluded request explains problems of its OWN job only", refused(
    lambda: F.build(runs16, pins=CP.PINS, cleared=cl16, failed=fl16, prereg=pre16), "a problem no excluded request explains"))
unfilled = copy.deepcopy(CP.PINS)
unfilled["beam_128k"]["sha256"] = None
check("an unfilled non-alias v3 pin stops the build", refused(
    lambda: F.build(runs, pins=unfilled, cleared=cl, failed=fl, prereg=pre), "beam_128k: not filled"))

print("\n- the declared lists: exactly the auditor's verdicts -")
got_cleared = sorted((e["window"], e["run"]) for e in F.CLEARED)
check("CLEARED names exactly the cleared runs (py-base b1, discovery d1/d2, pyarrow i1, hf h2, github g1, tiktoken t1, "
      "git r1, facts j3, ollama o1, local-v2 l1, d1-tag d1)",
      set(got_cleared) == {("local-v2", "l1"), ("d1-tag", "d1"),
          ("py-base-314", "b1"), ("a3-discovery", "d1"), ("a3-discovery", "d2"), ("a3-pyarrow", "i1"), ("a3-hf", "h2"),
          ("a3-github", "g1"), ("a3-tiktoken", "t1"), ("a3-git", "r1"), ("facts", "j3"), ("ollama", "o1")}, str(got_cleared))
check("FAILED names a3-hf h1, facts j1 and facts j2, each with a one-line reason",
      sorted((e["window"], e["run"]) for e in F.FAILED) == [("a3-hf", "h1"), ("facts", "j1"), ("facts", "j2")]
      and all(e["reason"] and "\n" not in e["reason"] for e in F.FAILED))
all_files = [(rel, sha) for e in F.CLEARED + F.FAILED for rel, sha in e["files"].items()]
check("every record is named once, by a 64-hex sha256", len({r for r, _ in all_files}) == len(all_files)
      and all(re.fullmatch(r"[0-9a-f]{64}", s) for _, s in all_files))
FIX = ROOT / "tests" / "fixtures" / "v3_pin_fill"
fix_shas = {p.relative_to(FIX).parent.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in FIX.rglob("pin_fill.json")}
decl_shas = {rel.split("/", 2)[1] + "/" + rel.split("/")[2]: sha for e in F.CLEARED for rel, sha in e["files"].items()
             if rel.endswith("/pin_fill.json")}
check("the pin_fill shas are the committed evidence's (tests/fixtures/v3_pin_fill)", decl_shas == fix_shas, str((decl_shas, fix_shas)))
check("the facts record is j3's, the one the auditor cleared (b2d373f0…)", any(
    e["run"] == "j3" and e["files"] == {"_facts/j3/facts.json": "b2d373f0d40d8c4ed885fb799416cf5c682ab35acdb7c636d09af786c3c14af8"}
    for e in F.CLEARED))

print("\n- the committed fragment -")
OUT = ROOT / "research" / "v3" / "freeze_a3.json"
if OUT.exists():
    raw = OUT.read_bytes()
    fz_c = json.loads(raw)
    check("the committed fragment's windows and failed runs are the declared lists",
          [{k: v for k, v in w.items() if k != "problems"} for w in fz_c.get("windows", [])]
          == [{k: e[k] for k in ("window", "run", "kind", "files", "note", "problems_verbatim", "excluded", "feeds_pins")
               if k in e} for e in F.CLEARED]
          and [{k: v for k, v in w.items() if k != "problems"} for w in fz_c.get("failed_runs", [])]
          == [{k: e[k] for k in ("window", "run", "files", "reason")} for e in F.FAILED])
    check("... and every cleared entry with problems carries its ruling note and its excluded requests",
          all(w.get("note") and w.get("excluded") for w in fz_c.get("windows", []) if sum(w["problems"].values())))
    check("C1: ... and no pin traces to an excluded request (counted from the real FILLED table)",
          fz_c.get("pins_from_excluded_requests") and all(v == 0 for v in fz_c["pins_from_excluded_requests"].values()))
    check("... its pins are the table's, as filled", fz_c.get("pins") == F.pins_section(CP.PINS))
    check("... every issuer organisation is public", all(o in F.PUBLIC_ISSUER_ORGS for v in fz_c.get("issuers", {}).values() for o, _ in v))
    check("... api.nuget.org (py-base b1's peer) is among the issuers, and files.pythonhosted.org is declared unrecorded",
          "api.nuget.org" in fz_c.get("issuers", {}) and set(fz_c.get("issuers_unrecorded", {})) == {"files.pythonhosted.org"})
    check("... its prereg_rev1 is the committed revision file's",
          fz_c.get("prereg_rev1") == hashlib.sha256((ROOT / "research" / "v3" / "PREREG-V3-rev1.md").read_bytes()).hexdigest())
    check("... it is the renderer's bytes (sorted JSON, LF)", raw == F.render(fz_c))
else:
    check("the fragment is not committed yet (A3.k's data commit writes it from the cleared records)", not OUT.exists())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 freeze a3: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
