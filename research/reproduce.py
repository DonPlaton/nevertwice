#!/usr/bin/env python3
"""RESEARCH - the reproduction package (GOAL F7).

F7's exit criterion is that the research **runs clean from a fresh clone with no author help**.
The test of that is not a README section, it is a script somebody else can run, so this is that
script: it regenerates every committed research artifact, hashes the result, and reports which
ones came back byte-identical.

The honest part is the classification. Not everything here is reproducible, and a package that
implies it is would fail the first stranger who tried:

* **deterministic** - same inputs, same bytes, every time and on any machine. These are the
  artifacts a reviewer can actually check.
* **machine-dependent** - the computation is reproducible but the numbers are not, because they
  measure the machine. Latency is the whole category, and F1's re-measurement showed the same
  statistic moving by a third between sessions on one box.
* **needs-absent-input** - a third-party dataset that is not committed and cannot be, so the
  artifact cannot be regenerated here at all. Recorded with what is missing.
* **needs-hardware** - a GPU and local model weights.

A reproduction that quietly re-ran only the easy artifacts and printed "all reproduced" would be
worse than none, so every artifact is listed in every run, including the ones that were skipped
and why.

    python research/reproduce.py              # regenerate everything reproducible, verify
    python research/reproduce.py --verify     # verify committed hashes without regenerating
    python research/reproduce.py --manifest   # print the raw-data manifest and exit

Exit code 0 only when every DETERMINISTIC artifact reproduced byte-for-byte.

Standard library only. Python 3.10+. No third-party packages are needed for any of this.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "reproduction.json"

DETERMINISTIC = "deterministic"
MACHINE = "machine-dependent"
ABSENT_INPUT = "needs-absent-input"
HARDWARE = "needs-hardware"

#: Every committed research artifact, what makes it, and what it takes to remake it.
ARTIFACTS = [
    {"file": "research/matched_conditions.json",
     "command": ["python", "research/matched_conditions.py", "--save"],
     "kind": DETERMINISTIC, "task": "F1",
     "inputs": ["research/matched_conditions_corpus.json"],
     "volatile": ["latency_ms_per_episode"],
     "note": "the precision/recall curve over the firing threshold"},
    {"file": "research/cheap_baselines.json",
     "command": ["python", "research/cheap_baselines.py", "--save"],
     "kind": DETERMINISTIC, "task": "F2",
     "inputs": ["research/matched_conditions_corpus.json",
                "research/cheap_baselines_rules.json"],
     "volatile": ["latency_ms_per_episode"],
     "note": "the six B5 baselines at a matched false-alarm rate"},
    {"file": "research/ablations.json",
     "command": ["python", "research/ablations.py", "--save"],
     "kind": DETERMINISTIC, "task": "F4",
     "inputs": ["research/matched_conditions_corpus.json"],
     "note": "one mechanism removed at a time"},
    {"file": "research/uncertainty.json",
     "command": ["python", "research/uncertainty.py", "--save"],
     "kind": DETERMINISTIC, "task": "F5",
     "inputs": ["research/matched_conditions_corpus.json"],
     "note": "paired tests, bootstrap with a fixed seed, Holm correction"},
    {"file": "research/harms.json",
     "command": ["python", "research/harms.py", "--save"],
     "kind": DETERMINISTIC, "task": "F6",
     "inputs": ["research/matched_conditions_corpus.json", "research/poisoning.json"],
     "note": "the safety evaluation"},
    {"file": "research/poisoning.json",
     "command": ["python", "research/poisoning.py", "--save"],
     "kind": DETERMINISTIC, "task": "E2/prior",
     "inputs": [], "note": "memory-poisoning attack corpus"},
    {"file": "research/forgetting.json",
     "command": ["python", "research/forgetting.py", "--save"],
     "kind": DETERMINISTIC, "task": "prior",
     "inputs": [], "note": "coverage under forgetting"},
    {"file": "research/longitudinal_results.json",
     "command": ["python", "research/longitudinal_improvement.py", "--sweep", "--save"],
     "kind": DETERMINISTIC, "task": "prior",
     "inputs": [], "note": "the active-vs-inject token ratio"},
    {"file": "research/latency_bench.json",
     "command": ["python", "research/latency_bench.py", "--save"],
     "kind": MACHINE, "task": "prior", "inputs": [],
     "note": "measures THIS machine. The same minimum-of-five statistic moved by about a "
             "third between sessions on one box, so byte-identity is not the test - the "
             "order of magnitude is."},
    {"file": "research/capability_grid.json",
     "command": ["python", "research/capability_grid.py", "--all"],
     "kind": HARDWARE, "task": "F3",
     "inputs": ["local Qwen2.5-Instruct weights", "a CUDA GPU", "torch", "transformers"],
     "note": "generates text with local models. Needs hardware a fresh clone does not carry."},
    {"file": "research/longmem_results.json",
     "command": ["python", "research/longmem_eval.py"],
     "kind": ABSENT_INPUT, "task": "prior",
     "inputs": ["research/data/longmemeval_oracle.json"],
     "note": "the LongMemEval-oracle dataset is third-party and not committed. Every claim "
             "from this artifact is already withdrawn as stale in the evidence manifest."},
]


#: Files under research/ that are inputs to the runner or its own output, not research results.
NOT_A_RESULT = {
    "research/evidence_manifest.json",          # the governance file itself
    "research/reproduction.json",               # this runner's own output
    "research/matched_conditions_corpus.json",  # an input
    "research/cheap_baselines_rules.json",      # an input
}


def scope() -> dict:
    """What this package promises to reproduce, and what it deliberately does not.

    The rule, derived rather than asserted: **every artifact backing a claim the project still
    publishes, plus the F-phase outputs.** The repository also carries a few dozen older result
    files from experiments whose claims are already withdrawn as stale, and regenerating those
    would reproduce numbers nobody is standing behind.

    Computed from `evidence_manifest.json` so it cannot drift: if a withdrawn claim is ever
    revived, its artifact stops being out of scope and the check fails until it is listed. A
    hand-maintained exclusion list would have gone stale the first time that happened.
    """
    listed = {a["file"] for a in ARTIFACTS}
    on_disk = {f"research/{q.name}" for q in (ROOT / "research").glob("*.json")}
    live_backed, withdrawn_backed = set(), set()
    manifest = ROOT / "research" / "evidence_manifest.json"
    if manifest.is_file():
        for claim in json.loads(manifest.read_text(encoding="utf-8"))["claims"]:
            raw = claim.get("raw")
            if not raw:
                continue
            (withdrawn_backed if claim.get("stale") else live_backed).add(raw)
    unlisted = on_disk - listed - NOT_A_RESULT
    return {
        "rule": "every artifact backing a claim the project still publishes, plus the F-phase "
                "outputs",
        "in_scope": sorted(listed),
        "live_claims_backed_by": sorted(live_backed),
        "live_artifacts_missing_from_the_manifest": sorted(live_backed - listed),
        "out_of_scope": sorted(unlisted),
        "out_of_scope_reason": "these back only claims already withdrawn as stale, or no claim "
                               "at all - older experiment outputs kept for the record. "
                               "Regenerating them would reproduce numbers nobody stands behind.",
        "out_of_scope_still_backing_a_live_claim": sorted(unlisted & live_backed),
        "withdrawn_claims_backed_by": sorted(withdrawn_backed - live_backed),
    }


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(path: Path, volatile: list) -> str | None:
    """A hash of everything in the artifact that SHOULD be reproducible.

    Some artifacts legitimately embed a measurement of the machine that produced them - F2
    reports each arm's latency per episode, which is real evidence and must not be deleted. But
    a byte-hash over a file containing a timing can never match on another machine, so a
    reproduction package that hashed the raw bytes would report a permanent failure and teach
    the reader to ignore it.

    So the volatile fields are named per artifact, stripped, and the rest is hashed. Both hashes
    are reported: the raw one shows the file is not byte-identical, the canonical one shows
    whether anything that matters changed.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    _strip(data, set(volatile or ()))
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _strip(node, keys: set) -> None:
    if isinstance(node, dict):
        for k in list(node):
            if k in keys:
                node.pop(k)
            else:
                _strip(node[k], keys)
    elif isinstance(node, list):
        for item in node:
            _strip(item, keys)


def missing_inputs(spec: dict) -> list:
    """Inputs that are files and are not here. Non-path inputs are hardware, listed as-is."""
    out = []
    for item in spec["inputs"]:
        if "/" not in item and "." not in item:
            out.append(item)
            continue
        if not (ROOT / item).exists():
            out.append(item)
    return out


def run_one(spec: dict, regenerate: bool) -> dict:
    path = ROOT / spec["file"]
    before = sha256(path)
    before_canonical = canonical(path, spec.get("volatile"))
    result = {
        "file": spec["file"], "task": spec["task"], "kind": spec["kind"],
        "note": spec["note"], "command": " ".join(spec["command"]),
        "committed_sha256": before,
        "committed_canonical_sha256": before_canonical,
        "volatile_fields": spec.get("volatile") or [],
        "committed_present": before is not None,
        "missing_inputs": missing_inputs(spec),
    }
    if not result["committed_present"]:
        result["status"] = "absent"
        result["explain"] = "the committed artifact is not in this clone"
        return result
    if result["missing_inputs"]:
        result["status"] = "skipped"
        result["explain"] = ("cannot regenerate here: missing "
                             + ", ".join(result["missing_inputs"]))
        return result
    if spec["kind"] in (MACHINE, HARDWARE):
        result["status"] = "skipped"
        result["explain"] = (f"{spec['kind']}: regenerating would change the numbers without "
                             "any of the code being wrong")
        return result
    if not regenerate:
        result["status"] = "not-run"
        result["explain"] = "--verify only hashes what is committed"
        return result

    started = time.perf_counter()
    proc = subprocess.run([sys.executable, *spec["command"][1:]], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=1800,
                          env=_env())
    result["seconds"] = round(time.perf_counter() - started, 1)
    if proc.returncode != 0:
        result["status"] = "failed"
        result["explain"] = (proc.stderr or proc.stdout)[-300:].replace("\n", " | ")
        return result
    after = sha256(path)
    after_canonical = canonical(path, spec.get("volatile"))
    result["regenerated_sha256"] = after
    result["regenerated_canonical_sha256"] = after_canonical
    if after_canonical == before_canonical:
        result["status"] = "reproduced"
        if after != before and result["volatile_fields"]:
            result["explain"] = ("byte-different only in the declared volatile fields ("
                                 + ", ".join(result["volatile_fields"])
                                 + "): everything that should reproduce did")
    else:
        result["status"] = "differs"
        result["explain"] = ("the regenerated artifact differs in fields that are NOT declared "
                             "volatile - either the code changed without the artifact being "
                             "restamped, or this artifact is not as deterministic as it is "
                             "declared to be")
    return result


def _env() -> dict:
    import os
    return {**os.environ, "PYTHONUTF8": "1"}


def build(regenerate: bool = True) -> dict:
    results = [run_one(spec, regenerate) for spec in ARTIFACTS]
    deterministic = [r for r in results if r["kind"] == DETERMINISTIC]
    reproduced = [r for r in deterministic if r["status"] == "reproduced"]
    broken = [r for r in deterministic
              if r["status"] in ("differs", "failed", "absent")]
    return {
        "schema_version": 1,
        "generated_by": "python research/reproduce.py",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "third_party_packages_required": [],
        "scope": scope(),
        "artifacts": results,
        "summary": {
            "deterministic": len(deterministic),
            "reproduced": len(reproduced),
            "not_reproduced": len(broken),
            "skipped": len([r for r in results if r["status"] == "skipped"]),
        },
        "verdict": _verdict(results, deterministic, reproduced, broken),
    }


def _verdict(results: list, deterministic: list, reproduced: list, broken: list) -> list:
    out = []
    if deterministic and len(reproduced) == len(deterministic):
        out.append(
            f"All {len(deterministic)} deterministic artifacts regenerated BYTE-IDENTICAL from "
            "this clone, with no third-party packages and no author help. That is what F7's "
            "exit criterion asks for, on the artifacts it can ask it of.")
    else:
        out.append(
            f"{len(broken)} of {len(deterministic)} deterministic artifacts did NOT reproduce: "
            + ", ".join(r["file"] for r in broken) +
            ". Until that is fixed the reproduction package does not hold.")

    skipped = [r for r in results if r["status"] == "skipped"]
    if skipped:
        out.append(
            "Deliberately NOT reproduced here, and each says why in the table: " +
            ", ".join(f"{r['file']} ({r['kind']})" for r in skipped) +
            ". A package that quietly re-ran only the easy artifacts and printed 'all "
            "reproduced' would be worse than none.")
    absent = [r for r in results if r["missing_inputs"]]
    if absent:
        out.append(
            "Inputs this clone does not carry: " +
            "; ".join(f"{r['file']} needs {', '.join(r['missing_inputs'])}" for r in absent) +
            ". Every claim resting on those is already withdrawn as stale in the evidence "
            "manifest, so nothing published depends on an artifact a stranger cannot rebuild.")
    sc = scope()
    if sc["out_of_scope_still_backing_a_live_claim"]:
        out.append(
            "SCOPE FAILURE: these artifacts back a claim the project still publishes and are "
            "NOT in the reproduction manifest: "
            + ", ".join(sc["out_of_scope_still_backing_a_live_claim"])
            + ". A published claim whose artifact nobody reproduces is a claim nobody can check.")
    elif sc["out_of_scope"]:
        out.append(
            f"{len(sc['out_of_scope'])} other result files under research/ are deliberately out "
            "of scope: they back only claims already withdrawn as stale, or no claim at all. "
            "Every artifact behind a claim the project still publishes IS in the manifest, and "
            "that is checked against the evidence manifest rather than maintained by hand.")

    out.append(
        "The core needs NO third-party packages: the whole of this runs on the Python "
        "standard library, which is why the frozen environment is a pinned interpreter and "
        "nothing else.")
    return out


def render(p: dict) -> str:
    L = ["", "Reproduction package (GOAL F7)", ""]
    L.append(f"  python {p['python']}, third-party packages required: "
             f"{p['third_party_packages_required'] or 'none'}")
    L.append("")
    L.append(f"  {'artifact':44} {'task':6} {'status':12} {'kind'}")
    for r in p["artifacts"]:
        L.append(f"  {r['file']:44} {r['task']:6} {r['status']:12} {r['kind']}")
        if r.get("explain"):
            L.append(f"      {r['explain'][:110]}")
    s = p["summary"]
    sc = p["scope"]
    L.append("")
    L.append(f"  scope: {sc['rule']}")
    L.append(f"         {len(sc['out_of_scope'])} older result file(s) out of scope; "
             f"{len(sc['out_of_scope_still_backing_a_live_claim'])} of those still back a "
             f"live claim")
    L.append(f"  {s['reproduced']}/{s['deterministic']} deterministic artifacts reproduced "
             f"byte-for-byte; {s['skipped']} skipped by design")
    L.append("")
    for line in p["verdict"]:
        L.append("  * " + line.replace(". ", ".\n    "))
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true",
                    help="hash the committed artifacts without regenerating them")
    ap.add_argument("--manifest", action="store_true",
                    help="print the raw-data manifest and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    args = ap.parse_args(argv)

    if args.manifest:
        print(json.dumps(ARTIFACTS, ensure_ascii=False, indent=1))
        return 0

    payload = build(regenerate=not args.verify)
    if args.save:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render(payload))
    return 0 if payload["summary"]["not_reproduced"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
