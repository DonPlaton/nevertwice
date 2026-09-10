#!/usr/bin/env python3
"""Register a supersession family from a pooled bench artifact (`supersession_bench.py --pool`).

The v1 family (`supersession.*`) was registered by two one-off scripts in a session scratchpad.
The implicit-replacement variant (ledger I5) needs the same seventeen-claim shape on another
dataset, and a shape typed twice is typed differently twice. This tool reads the artifact: the
pooled engine rates with their Wilson intervals over case-runs, each other arm's rates, the
paired McNemar tests, the characters returned per query, and the dataset's case counts.

    python tools/register_supersession.py --family supersession_implicit \\
        --artifact research/results/supersession_v1_implicit.json --dataset supersession_v1_implicit \\
        --command "python research/supersession_bench.py --dataset research/data/supersession_v1_implicit.json ..."

The dataset must already be in the manifest. Existing claims are left alone; the artifact must
be newer than HEAD and the command's closure clean.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
sys.path.insert(0, str(ROOT / "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001
    pass

ENVIRONMENT = "local_supersession_stand"
ARM_LABEL = {"mem0": "Mem0 2.0.19", "naive": "the append-only floor (markdown + term overlap)",
             "zep": "Zep/Graphiti (graphiti-core 0.30.2, FalkorDB)"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _dirty_files() -> set[str]:
    out = set()
    for line in _git("status", "--porcelain").splitlines():
        if len(line) > 3:
            out.add(line[3:].strip().replace("\\", "/"))
    return out


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def build_claims(family: str, art: dict, *, dataset: str, command: str, raw: str, head: str,
                 produced_by: list[str], existing: set[str], stand: str = "",
                 cite: list[str] | None = None) -> tuple[list[dict], list[str]]:
    P = art["pooled_nevertwice"]
    runs = P["runs"]
    ds = art["dataset"]
    n_sup, n_ctl = int(ds["supersession_cases"]), int(ds["control_cases"])
    stand = stand or f"the {ds['name']} corpus"
    base = {"dataset": dataset, "environment": ENVIRONMENT, "command": command, "raw": raw,
            "cited_in": list(cite or []), "commit": head, "produced_by": list(produced_by)}
    note_pooled = (f"Pooled over {runs} runs of the same commit: the extraction model is not "
                   f"deterministic at temperature 0 and the runs read "
                   + " and ".join(str(x) for x in P["stale"]["per_run"])
                   + f". One run of this stand is not a result, so the published figure is the pooled "
                   f"rate over {P['stale']['n']} case-runs with the per-run values beside it in the artifact.")
    new: list[dict] = []
    skipped: list[str] = []

    def add(cid, statement, value, printed, unit, n, ci, pointer, note=None):
        if cid in existing:
            skipped.append(cid)
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed),
             "unit": unit, "n": n, "pointer": pointer,
             "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None),
             **base}
        if note:
            c["note"] = note
        new.append(c)

    st, cu, ov = P["stale"], P["current"], P["over_retraction"]
    add(f"{family}.nevertwice.stale_rate",
        f"Nevertwice returns a retracted fact as a current assertion on {_pct(st['rate'])} of "
        f"supersession case-runs on {stand}, pooled over {runs} runs of the same commit",
        st["rate"], [f"{st['rate']:.3f}"], "rate", st["n"], st["ci"], "pooled_nevertwice.stale.rate", note_pooled)
    add(f"{family}.nevertwice.current_rate",
        f"Nevertwice returns the replacement fact on {_pct(cu['rate'])} of supersession case-runs on {stand}",
        cu["rate"], [f"{cu['rate']:.3f}"], "rate", cu["n"], cu["ci"], "pooled_nevertwice.current.rate", note_pooled)
    add(f"{family}.nevertwice.over_retraction_rate",
        f"Nevertwice retires a still-true fact on {ov['k']} of {ov['n']} control case-runs on {stand}",
        ov["rate"], [f"{ov['rate']:.2f}"], "rate", ov["n"], ov["ci"], "pooled_nevertwice.over_retraction.rate")
    add(f"{family}.nevertwice.chars_per_query",
        f"Nevertwice returns {P['mean_chars_returned']:.0f} characters per query on {stand}",
        P["mean_chars_returned"], [f"{P['mean_chars_returned']:.0f}"], "characters", (n_sup + n_ctl) * runs,
        None, "pooled_nevertwice.mean_chars_returned")
    for arm, res in art["arms"].items():
        if arm.startswith("nevertwice") or res.get("blocked"):
            continue
        label = ARM_LABEL.get(arm, arm)
        for key, unit_n, what in (("stale_rate", n_sup, "returns a retracted fact as a current assertion on"),
                                  ("current_rate", n_sup, "returns the replacement fact on")):
            v = res[key]
            k = round(v * unit_n)
            add(f"{family}.{arm}.{key}", f"{label} {what} {_pct(v)} of supersession cases on {stand}",
                v, [f"{v:.3f}"], "rate", unit_n, wilson(k, unit_n), f"arms.{arm}.{key}")
        v = res["over_retraction_rate"]
        k = round(v * n_ctl)
        add(f"{family}.{arm}.over_retraction_rate",
            f"{label} loses a still-true fact on {k} of {n_ctl} control cases on {stand}",
            v, [f"{v:.2f}"], "rate", n_ctl, wilson(k, n_ctl), f"arms.{arm}.over_retraction_rate")
        add(f"{family}.{arm}.chars_per_query",
            f"{label} returns {res['mean_chars_returned']:.0f} characters per query on {stand}",
            res["mean_chars_returned"], [f"{res['mean_chars_returned']:.0f}"], "characters", n_sup + n_ctl,
            None, f"arms.{arm}.mean_chars_returned")
    for i, pr in enumerate(art["pairs"]):
        a, b = pr["a"], pr["b"]
        pid = f"{family}.{a}_vs_{b}.p_mcnemar"
        p = pr["p_mcnemar"]
        printed = [f"{p:.2f}"] if p >= 0.001 else [f"{p:.1e}".replace("e-", " x 10^-")]
        add(pid, f"{a} against {b} on the same {pr['n']} supersession cases of {stand}: "
                 f"{pr[f'stale_only_{a}']} against {pr[f'stale_only_{b}']} discordant pairs, McNemar exact",
            p, printed, "p-value", pr["n"], None, f"pairs[{i}].p_mcnemar")
    add(f"{family}.dataset.supersession_cases",
        f"the benchmark variant carries {n_sup} cases in which a fact is replaced", n_sup, [str(n_sup)],
        "cases", n_sup, None, "dataset.supersession_cases")
    add(f"{family}.dataset.control_cases",
        f"and {n_ctl} control cases in which two facts differ and both remain true", n_ctl, [str(n_ctl)],
        "cases", n_ctl, None, "dataset.control_cases")
    return new, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--family", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--command", required=True)
    ap.add_argument("--stand", default="", help="how statements name the corpus")
    ap.add_argument("--cite", action="append", default=None)
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raw = ROOT / args.artifact
    if not raw.is_file():
        print(f"no artifact at {args.artifact}")
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.dataset not in manifest.get("datasets", {}):
        print(f"unknown dataset {args.dataset!r} - register it first")
        return 2
    head = _git("rev-parse", "HEAD")
    code_time = int(_git("log", "-1", "--format=%ct", head))
    if raw.stat().st_mtime < code_time:
        print(f"{args.artifact} predates HEAD - re-run `{args.command}`")
        return 2
    import produced_by as pb                                     # noqa: PLC0415
    closure = pb.closure(args.command)
    dirty = sorted(p for p in closure if p in _dirty_files())
    if dirty:
        print(f"working tree modifies {dirty[0]} - commit first")
        return 2
    art = json.loads(raw.read_text(encoding="utf-8"))
    existing = {c["id"] for c in manifest["claims"]}
    new, skipped = build_claims(args.family, art, dataset=args.dataset, command=args.command,
                                raw=args.artifact, head=head, produced_by=closure, existing=existing,
                                stand=args.stand, cite=args.cite)
    if skipped:
        print(f"  already registered ({len(skipped)})")
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
    print(f"registered {len(new)} claim(s) for {args.family} at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
