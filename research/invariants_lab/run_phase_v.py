"""Phase V, end to end, unattended: census -> mutate -> verify -> power -> measure.

Every step is a single command against **frozen** code, selected onto the held-out corpus
with `--corpus heldout`. Nothing here changes what any mechanism does; it runs the ones
`heldout_seal.json` hashed, in the order `PREREGISTRATION-SHIP.md` declares, and stops at
the first step that fails rather than measuring on a half-built corpus.

Written after the H1 freeze and outside it: this is a driver, not a mechanism.

Two properties that matter for a run nobody is watching:

* **it resumes.** A step whose artifact already exists is skipped, so an interrupted run
  is restarted with the same command and picks up where it stopped;
* **it refuses to measure a corpus that is not there.** The census and the answer key are
  prerequisites, and a missing one stops the chain instead of producing a number over
  nothing -- which is the failure this project has now written down six times.

    python research/invariants_lab/run_phase_v.py            # run it
    python research/invariants_lab/run_phase_v.py --plan     # what it would do
    python research/invariants_lab/run_phase_v.py --print    # the log of the last run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACT = HERE / "phase_v_log.json"
CORPUS = "heldout"

#: (script, the artifact it writes, what it is). Order is the dependency order, and each
#: step's artifact is the next one's input.
STEPS: tuple[tuple[str, str, str], ...] = (
    ("corpus_census.py", "corpus_census_heldout.json", "H3 census"),
    ("mutate.py", "mutants_heldout.json", "H3 answer key"),
    ("verify_mutants.py", "mutant_controls_heldout.json",
     "H3 control: CPython's binder, independently"),
    ("power_ship.py", "power_ship_heldout.json", "H4 which gates resolve"),
    ("measure_blast_radius.py", "blast_radius_d5_heldout.json", "V1-A baseline"),
    ("measure_abstention.py", "abstention_f1_heldout.json", "V1-A decidable-only"),
    ("measure_surface.py", "surface_f2_heldout.json", "V1-A the scoping grid"),
    ("measure_ratchet.py", "ratchet_r3_heldout.json", "V1-B the ratchet"),
    ("measure_scale.py", "scale_x4_heldout.json", "V1-C scale"),
    ("mine_quadratics.py", "quadratic_f4_heldout.json", "V1-C found quadratics"),
    ("measure_together.py", "together_t1_heldout.json", "V2 the union"),
)


def _disk_from_clone_log() -> float | None:
    log = HERE / "heldout_clone_log.json"
    if not log.exists():
        return None
    try:
        return json.loads(log.read_text(encoding="utf-8")).get("disk_gb")
    except (OSError, json.JSONDecodeError):
        return None


def _run(script: str, timeout: int = 72000) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HERE / script), "--corpus", CORPUS],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False)
    tail = "\n".join((proc.stdout or "").splitlines()[-25:])
    err = "\n".join((proc.stderr or "").splitlines()[-15:])
    return proc.returncode, (tail + ("\n--- stderr ---\n" + err if proc.returncode else ""))


def run(plan_only: bool) -> dict:
    log: list[dict] = []
    for script, artifact, what in STEPS:
        path = HERE / artifact
        if plan_only:
            log.append({"step": script, "what": what, "artifact": artifact,
                        "would": "skip (exists)" if path.exists() else "run"})
            continue
        if path.exists():
            log.append({"step": script, "what": what, "status": "skipped",
                        "why": "artifact already exists"})
            print(f"  skip  {script:26s} {what}", flush=True)
            continue
        print(f"  run   {script:26s} {what}", flush=True)
        t0 = time.time()
        code, tail = _run(script)
        entry = {"step": script, "what": what, "returncode": code,
                 "seconds": round(time.time() - t0, 1), "tail": tail[-3000:],
                 "artifact_written": path.exists()}
        log.append(entry)
        if code != 0 or not path.exists():
            entry["status"] = "FAILED"
            print(f"  FAIL  {script} after {entry['seconds']}s -- chain stops here",
                  flush=True)
            break
        entry["status"] = "ok"
        print(f"  ok    {script} in {entry['seconds']}s", flush=True)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "V", "corpus": CORPUS,
        "plan": plan_only,
        "steps": log,
        "completed": all(e.get("status") in ("ok", "skipped") for e in log)
                     and len(log) == len(STEPS),
        # Read from H3's clone log rather than by naming the corpus root. The seal's
        # static lock forbids that name outside `corpora.HELDOUT_READERS`, and adding
        # this file to the allowlist would mean amending a frozen module **after** the
        # corpus exists -- which `PREREGISTRATION-SHIP.md` §1 says is a deviation and
        # not a correction. The lock was right; the driver changed.
        "disk_gb": _disk_from_clone_log(),
    }


def _print(d: dict) -> None:
    print(f"Phase V on {d['corpus']} -- {'plan' if d.get('plan') else 'run'}")
    for e in d["steps"]:
        if d.get("plan"):
            print(f"  {e['step']:26s} {e['would']:16s} {e['what']}")
        else:
            print(f"  {e.get('status','?'):8s} {e['step']:26s} "
                  f"{e.get('seconds', 0):8.1f}s  {e['what']}")
    if not d.get("plan"):
        print()
        print("completed: " + ("yes" if d["completed"] else "NO -- the chain stopped"))
        print(f"held-out corpus on disk: {d['disk_gb']} GB (from H3's clone log)")
        for e in d["steps"]:
            if e.get("status") == "FAILED":
                print("\nthe step that stopped it:\n" + e.get("tail", ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    data = run(args.plan)
    if not args.plan:
        ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    _print(data)
    return 0 if data.get("completed") or args.plan else 1


if __name__ == "__main__":
    raise SystemExit(main())
