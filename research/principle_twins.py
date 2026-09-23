#!/usr/bin/env python3
"""A6 (Q5): calibrate T_PRINCIPLE - the cosine threshold `nevertwice/principles.py` uses to
decide whether two `principle` sentences, from two DIFFERENT projects, are restatements of the
SAME rule (should cluster and promote) or two DIFFERENT rules that merely share a topic (must
stay apart). `principles.py` ships with a placeholder value marked "not yet measured"; this is
the script that measures it, against `research/data/principle_twins_v1.json` - 40 hand-written
same-rule paraphrase pairs (positives) and 40 hand-written different-rule same-topic pairs
(negatives), every sentence de-identified coding-agent lesson prose.

Method: sweep T over [0.75, 0.95] in steps of 0.01 against the REAL embedder, and pick the
LOWEST T with ZERO false merges on the negatives (a false merge = a negative pair whose cosine
still clears T) - the most permissive threshold that never once confuses two different rules
for one, printed beside the Wilson upper bound on that zero's true rate (a "0/40" empirical
reading is not the same claim as "the true rate is 0").

    python research/principle_twins.py --help
    python research/principle_twins.py --dry     # validate the dataset shape only - embeds
                                                   # NOTHING, writes NOTHING, needs no embedder
    python research/principle_twins.py            # the real sweep - NOT RUN as part of this
                                                   # work (plan A6: "written and NOT run"). A
                                                   # GPU-bound measurement campaign is using the
                                                   # embedder/GPU right now and a competing run
                                                   # would corrupt its timing; this script is
                                                   # checked in unrun, --help and --dry only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for every research script

# Hermetic by default, same as every other research script here: a bare run must never resolve
# the owner's live vault, even though this script's own job (embedding two short sentences) has
# nothing to do with any vault content.
sandbox_guard.isolate(prefix="nevertwice_principle_twins_")
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

DATA = HERE / "data" / "principle_twins_v1.json"
# .loop/explore/, not research/results/: a calibration sweep is exploratory - T_PRINCIPLE stays
# a documented placeholder in principles.py until someone reads this artifact and deliberately
# updates the constant. research/results/ is where a published, cited claim's artifact lives
# (research/evidence_manifest.json); an exploratory run must not write there by default.
OUT = ROOT / ".loop" / "explore" / "principle_twins.json"
#: [0.75, 0.95] inclusive, step 0.01 - the plan's own sweep range for A6.
T_SWEEP = [round(0.75 + 0.01 * i, 2) for i in range(21)]


def load_dataset(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "positives" not in raw or "negatives" not in raw:
        raise ValueError(f"{path}: missing 'positives'/'negatives' top-level keys")
    return raw


def validate_dataset(raw: dict) -> list[str]:
    """Structural checks only - no embedding, no network, no GPU. Returns a list of problems;
    empty means the dataset is shaped correctly (says nothing about the SENTENCES being good
    lessons - that is a human judgement this script does not automate)."""
    problems: list[str] = []
    for key in ("positives", "negatives"):
        rows = raw.get(key)
        if not isinstance(rows, list):
            problems.append(f"{key!r} is not a list")
            continue
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                problems.append(f"{key}[{i}] is not an object")
                continue
            for field in ("a", "b"):
                v = row.get(field)
                if not isinstance(v, str) or not v.strip():
                    problems.append(f"{key}[{i}].{field} is missing or empty")
            if isinstance(row.get("a"), str) and row.get("a") == row.get("b"):
                problems.append(f"{key}[{i}]: a and b are identical - not a real pair")
    pos, neg = raw.get("positives") or [], raw.get("negatives") or []
    if not isinstance(pos, list) or len(pos) < 1:
        problems.append("no positive pairs")
    if not isinstance(neg, list) or len(neg) < 1:
        problems.append("no negative pairs")
    return problems


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval - the house formula (`grep -rn "def wilson" research/`, reproduced here
    rather than imported: every research script that uses it keeps its own copy, and this one
    is a two-line function with no state, not worth a cross-module import for)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def _embed_pair_cosines(pairs: list[dict], kind: str | None) -> list[float | None]:
    """Cosine per pair, or None where either side failed to embed (a busy/absent embedder
    degrades a pair to 'excluded from the sweep', never to a fabricated 0.0 or 1.0)."""
    out: list[float | None] = []
    for row in pairs:
        va = m.embed_text(row["a"], kind=kind)
        vb = m.embed_text(row["b"], kind=kind)
        out.append(m.cosine(va, vb) if va and vb else None)
    return out


def sweep(pos_cos: list[float | None], neg_cos: list[float | None]) -> dict:
    """False-merge count on the negatives and true-merge count on the positives at every T in
    the sweep. `chosen_T` is the LOWEST T with zero false merges - the most permissive
    threshold that never once confuses two different rules for one on this dataset."""
    rows = []
    chosen = None
    n_neg = sum(1 for c in neg_cos if c is not None)
    n_pos = sum(1 for c in pos_cos if c is not None)
    for T in T_SWEEP:
        false_merges = sum(1 for c in neg_cos if c is not None and c >= T)
        true_merges = sum(1 for c in pos_cos if c is not None and c >= T)
        rows.append({"T": T, "false_merges": false_merges, "n_negatives": n_neg,
                     "true_merges": true_merges, "n_positives": n_pos})
        if chosen is None and false_merges == 0:
            chosen = T
    return {"sweep": rows, "chosen_T": chosen, "n_negatives": n_neg, "n_positives": n_pos}


def _git_head() -> str:
    import subprocess                                            # noqa: PLC0415
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=10,
                              check=True).stdout.strip()
    except (OSError, ValueError) as e:
        return f"?({type(e).__name__})"
    except Exception:                       # noqa: BLE001 - CalledProcessError/TimeoutExpired
        return "?"


def _measured_at() -> dict:
    """The commit and the moment this sweep ran - the same shape `research/head_to_head.py`
    stamps every row with, so a reader of the artifact can tell how stale it is."""
    import datetime                                              # noqa: PLC0415
    return {"commit": _git_head(),
           "utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA), help="dataset path (default: %(default)s)")
    parser.add_argument("--out", default=str(OUT),
                        help="results artifact path (default: %(default)s)")
    parser.add_argument("--dry", action="store_true",
                        help="validate the dataset shape only - embeds and writes nothing")
    args = parser.parse_args(argv)

    raw = load_dataset(Path(args.data))
    problems = validate_dataset(raw)
    pos, neg = raw.get("positives", []), raw.get("negatives", [])
    print(f"[principle_twins] dataset: {len(pos)} positive pair(s), {len(neg)} negative pair(s)")
    if problems:
        print(f"[principle_twins] {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("[principle_twins] dataset shape OK")

    if args.dry:
        return 1 if problems else 0
    if problems:
        print("[principle_twins] refusing to embed a malformed dataset", file=sys.stderr)
        return 2

    if not m.embedder_available(4):
        print("[principle_twins] embedder not reachable - nothing to measure", file=sys.stderr)
        return 3

    kind = m.doc_embed_kind() if hasattr(m, "doc_embed_kind") else None
    pos_cos = _embed_pair_cosines(pos, kind)
    neg_cos = _embed_pair_cosines(neg, kind)
    result = sweep(pos_cos, neg_cos)

    chosen = result["chosen_T"]
    if chosen is not None:
        row = next(r for r in result["sweep"] if r["T"] == chosen)
        lo, hi = wilson(row["false_merges"], result["n_negatives"])
        print(f"[principle_twins] chosen T={chosen} - 0 false merges on "
             f"{result['n_negatives']} negatives; Wilson upper bound on the true false-merge "
             f"rate: {hi:.3f}")
    else:
        print("[principle_twins] no T in [0.75, 0.95] reached 0 false merges on this dataset "
             "- widen the sweep or inspect the negatives", file=sys.stderr)

    artifact = {"dataset": str(args.data), "embed_signature": m.embed_signature(),
               "sweep_range": [T_SWEEP[0], T_SWEEP[-1]], "chosen_T": chosen,
               "n_positives": result["n_positives"], "n_negatives": result["n_negatives"],
               "sweep": result["sweep"], "measured_at": _measured_at()}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    print(f"[principle_twins] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
