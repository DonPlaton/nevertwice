"""H4: can the held-out corpus resolve each declared gate, at the size it actually is?

`PREREGISTRATION-SHIP.md` §3 declares every Phase V threshold. This asks, for each one,
**how large a sample resolves it at 80% power** and whether the corpus that was actually
built supplies it. `GOAL-SHIP.md` H4: *any gate the corpus cannot resolve is either
enlarged or withdrawn before the measurement, never after.*

Written after the H1 freeze and outside it: this decides what may be scored, it does not
measure anything, and its hash is not among the 29 in `heldout_seal.json`.

    python research/invariants_lab/power_ship.py --corpus heldout
    python research/invariants_lab/power_ship.py --print
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpora  # noqa: E402
import power as PW  # noqa: E402

CORPUS = "heldout"


def _select_corpus(name: str) -> None:
    global CORPUS, CENSUS, MUTANTS, ARTIFACT
    CORPUS = name
    CENSUS = corpora.census_path(name)
    MUTANTS = corpora.mutants_path(name)
    ARTIFACT = corpora.artifact_path("power_ship.json", name)


CENSUS = corpora.census_path(CORPUS)
MUTANTS = corpora.mutants_path(CORPUS)
ARTIFACT = corpora.artifact_path("power_ship.json", CORPUS)

SILENCE_SAMPLE = 1000     # PREREGISTRATION-SHIP.md section 3, unchanged from D5

#: Every gate that carries a number, its null, and the effect the preregistration says it
#: must distinguish from that null. Copied here rather than re-derived, so a drift between
#: this file and the preregistration is visible in a diff.
GATES = (
    {"id": "V1-A recall", "kind": "one_prop", "null": 0.45, "effect": 0.60,
     "sample": "source_commits",
     "note": "recall floor 0.45; corpus_dev gave 0.600, and the floor sits a further "
             "0.10 below the bottom of that interval for the out-of-sample drop"},
    {"id": "V1-A silence", "kind": "one_prop", "null": 0.05, "effect": 0.015,
     "sample": "silence_pool",
     "note": "flag rate ceiling 0.05; corpus_dev gave 0.010 under decidable-only"},
    {"id": "V1-B ratchet agreement", "kind": "one_prop", "null": 0.50, "effect": 0.64,
     "sample": "commits",
     "note": "fires on > 0.50 of ruff-rose diffs; corpus_dev gave 0.637"},
    {"id": "V1-B ratchet silence", "kind": "one_prop", "null": 0.05, "effect": 0.30,
     "sample": "commits",
     "note": "flag rate ceiling 0.05; corpus_dev gave 0.358 -- a gate this far from its "
             "null needs almost no sample to fail, which is worth saying out loud"},
    {"id": "V1-C scale silence", "kind": "one_prop", "null": 0.05, "effect": 0.015,
     "sample": "commits", "note": "flag rate under a blind declaration"},
    {"id": "V1-C scale recall", "kind": "one_prop", "null": 0.30, "effect": 0.60,
     "sample": "mined_quadratics",
     "note": "scored only if the corpus yields >= 20 confirmed quadratic fixes"},
    {"id": "V2 union flag rate", "kind": "one_prop", "null": 0.05, "effect": 0.015,
     "sample": "silence_pool", "note": "one finding per diff, delivered counts"},
)


def corpus_shape() -> dict:
    """What the built corpus actually supplies, per sample kind."""
    shape = {"corpus": CORPUS, "census": CENSUS.exists(), "mutants": MUTANTS.exists()}
    if CENSUS.exists():
        census = json.loads(CENSUS.read_text(encoding="utf-8"))
        shape["repositories"] = len(census.get("repos", []))
        shape["commits"] = sum(len(r.get("eligible", [])) + len(r.get("silent", []))
                               for r in census.get("repos", []))
        shape["silence_pool"] = min(
            SILENCE_SAMPLE,
            sum(len(r.get("silent", [])) for r in census.get("repos", [])))
    if MUTANTS.exists():
        mut = json.loads(MUTANTS.read_text(encoding="utf-8"))
        confirmed = [m for m in mut["mutants"] if m["confirmed"]]
        shape["positives"] = len(confirmed)
        shape["source_commits"] = len({m["sha"] for m in confirmed})
    shape.setdefault("mined_quadratics", None)   # filled by mine_quadratics, if run
    return shape


def run() -> dict:
    shape = corpus_shape()
    rows = []
    for gate in GATES:
        needed = PW.n_for_one_prop(gate["null"], gate["effect"])
        have = shape.get(gate["sample"])
        rows.append({
            **gate,
            "n_needed_at_80pct": needed,
            "n_available": have,
            "resolvable": (None if have is None else have >= needed),
            "mde_at_available": (PW.mde_one_prop(have, gate["null"])
                                 if isinstance(have, int) and have > 0 else None),
        })
    unresolvable = [r["id"] for r in rows if r["resolvable"] is False]
    unknown = [r["id"] for r in rows if r["resolvable"] is None]
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "H4", "corpus": CORPUS,
        "shape": shape,
        "gates": rows,
        "withdraw_before_measuring": unresolvable,
        "sample_size_not_yet_known": unknown,
        "rule": ("GOAL-SHIP.md H4: a gate the corpus cannot resolve is enlarged or "
                 "WITHDRAWN before the measurement, never after."),
    }


def _print(d: dict) -> None:
    s = d["shape"]
    print(f"H4 -- can {d['corpus']} resolve each declared gate?")
    print("  corpus shape: " + ", ".join(
        f"{k}={v}" for k, v in s.items() if k not in ("corpus",)))
    print()
    print(f"{'gate':26s} {'null':>6s} {'effect':>7s} {'n needed':>9s} "
          f"{'n have':>8s}  verdict")
    print("-" * 78)
    for r in d["gates"]:
        have = r["n_available"]
        verdict = ("unknown" if r["resolvable"] is None
                   else ("resolvable" if r["resolvable"] else "WITHDRAW"))
        print(f"{r['id']:26s} {r['null']:6.2f} {r['effect']:7.3f} "
              f"{r['n_needed_at_80pct']:9d} "
              f"{(str(have) if have is not None else '-'):>8s}  {verdict}")
    print("-" * 78)
    if d["withdraw_before_measuring"]:
        print("WITHDRAWN before measuring: " + ", ".join(d["withdraw_before_measuring"]))
    if d["sample_size_not_yet_known"]:
        print("sample not yet built for: " + ", ".join(d["sample_size_not_yet_known"]))
    print(d["rule"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    corpora.add_corpus_argument(ap)
    args = ap.parse_args(argv)
    _select_corpus(args.corpus)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    data = run()
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
