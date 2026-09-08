#!/usr/bin/env python3
"""Sweep the dense weight of the calibrated fusion on both pinned corpora, from cached vectors.

The weight (`NEVERTWICE_FUSION_SEM_WEIGHT`) was tuned when the semantic arm embedded the first
2,000 characters of a session and the lexical arm scored raw tokens. Both changed on 2026-09-06
(whole-session vectors; stop words and stems), and the shipped 0.5 gave back eight thousandths
at R@5 on the oracle pool while the semantic arm alone rose four points. This stand re-runs the
two evaluation harnesses once per weight with everything else fixed and writes one artifact,
so the published operating point can be checked against the whole curve rather than trusted.

Gate, written in the ledger (I1) before the first run: the new weight must beat 0.5 by at least
0.01 R@5 on one corpus and lose no more than 0.005 on the other; ties keep 0.5.

    python research/fusion_sweep.py --save              # ~20 min CPU, vectors cached
    python research/fusion_sweep.py --weights 0.5,1.0 --save

Each point is the harness's own `--save` artifact for that weight (written to a temporary
directory and folded in here), so a point on this curve is exactly what `longmem_eval.py` or
`locomo_eval.py` would print with that weight set.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402

sandbox_guard.isolate(prefix="nevertwice_fusion_sweep_")
# Imported for the register's closure, not for use: each point below is produced by running
# these two harnesses as subprocesses, and a claim from this artifact must go stale when either
# of them changes. The freshness tool follows imports; a subprocess it cannot see.
sys.path.insert(0, str(HERE))
import locomo_eval  # noqa: E402,F401
import longmem_eval  # noqa: E402,F401

OUT = ROOT / "research" / "results" / "fusion_weight_sweep.json"
DEFAULT_WEIGHTS = (0.25, 0.5, 0.75, 1.0, 1.5)
STANDS = {
    "oracle": ["python", "research/longmem_eval.py"],
    "locomo": ["python", "research/locomo_eval.py"],
}


def run_point(stand: str, weight: float, tmp: Path) -> dict:
    out = tmp / f"{stand}_w{weight}.json"
    env = {**os.environ, "NEVERTWICE_FUSION_SEM_WEIGHT": str(weight), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, *STANDS[stand][1:], "--save", f"--out={out}"]
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"{stand} at {weight}: exit {r.returncode}\n{r.stderr[-800:]}")
    blob = json.loads(out.read_text(encoding="utf-8"))
    row = dict(blob["methods"]["hybrid"])
    row["n"] = blob["questions"]
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weights", default=",".join(str(w) for w in DEFAULT_WEIGHTS))
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    weights = [float(w) for w in args.weights.split(",") if w.strip()]
    sys.path.insert(0, str(ROOT / "nevertwice"))
    import memory_hook as m                                      # noqa: PLC0415

    res = {"weights": weights, "shipped": m.FUSION_SEM_WEIGHT,
           "morphology": bool(getattr(m, "LEXICAL_MORPHOLOGY", False)),
           "embedder": m.EMBED_MODEL, "points": {s: {} for s in STANDS}}
    with tempfile.TemporaryDirectory(prefix="nevertwice_sweep_") as td:
        for stand in STANDS:
            for w in weights:
                row = run_point(stand, w, Path(td))
                res["points"][stand][str(w)] = row
                print(f"  {stand:7s} w={w:<5} R@1 {row['recall@1']:.3f}  R@5 {row['recall@5']:.3f}  "
                      f"R@10 {row['recall@10']:.3f}  MRR {row['mrr']:.3f}", flush=True)
    base = {s: res["points"][s].get("0.5") for s in STANDS}
    for s in STANDS:
        if base[s]:
            for w, row in res["points"][s].items():
                row["delta_recall@5_vs_0.5"] = round(row["recall@5"] - base[s]["recall@5"], 4)
    if args.save:
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"  saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
