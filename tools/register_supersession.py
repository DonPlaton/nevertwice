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

Since 2026-09-11 the family also carries the two control measures under their own names - the
broad `control_miss_rate` for every arm (the still-true fact did not come back, any cause) and the
narrow `over_retraction_rate` only where the arm's store records a retirement - the miss split by
cause per arm, the control case-run count, and each pair's discordant counts.

The dataset must already be in the manifest. Existing claims are left alone; the artifact must
be newer than HEAD and the command's closure clean - or, with `--at COMMIT`, already registered at
COMMIT with no closure file changed since (the tool learned fields after the run).
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

    def add(cid, statement, value, printed, unit, n, ci, pointer, note=None, derivation=None):
        if cid in existing:
            skipped.append(cid)
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed),
             "unit": unit, "n": n, "pointer": pointer,
             "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None),
             **base}
        if note:
            c["note"] = note
        if derivation:
            c["derivation"] = derivation
        new.append(c)

    def engine_runs() -> list[dict]:
        return [res for arm, res in art["arms"].items()
                if arm.startswith("nevertwice") and not res.get("blocked")]

    def controls(res: dict) -> list[dict]:
        return [r for r in res.get("rows", []) if r.get("shape") == "control"]

    st, cu, ov = P["stale"], P["current"], P["over_retraction"]
    add(f"{family}.nevertwice.stale_rate",
        f"Nevertwice returns a retracted fact as a current assertion on {_pct(st['rate'])} of "
        f"supersession case-runs on {stand}, pooled over {runs} runs of the same commit",
        st["rate"], [f"{st['rate']:.3f}"], "rate", st["n"], st["ci"], "pooled_nevertwice.stale.rate", note_pooled)
    add(f"{family}.nevertwice.current_rate",
        f"Nevertwice returns the replacement fact on {_pct(cu['rate'])} of supersession case-runs on {stand}",
        cu["rate"], [f"{cu['rate']:.3f}"], "rate", cu["n"], cu["ci"], "pooled_nevertwice.current.rate", note_pooled)
    # Two metrics lived under one name until 2026-09-11. `over_retraction_rate` is the NARROW
    # one - the memory retired a still-true fact, read from the store - and `control_miss_rate`
    # the BROAD one - the still-true fact did not come back, whatever the cause. The published
    # comparison table used the narrow figure for us and the broad one for every other arm.
    add(f"{family}.nevertwice.over_retraction_rate",
        f"Nevertwice retires a still-true fact (the memory closed its interval) on {ov['k']} of {ov['n']} "
        f"control case-runs on {stand}",
        ov["rate"], [f"{ov['rate']:.2f}", f"{ov['rate']:.3f}"], "rate", ov["n"], ov["ci"],
        "pooled_nevertwice.over_retraction.rate",
        note="Over-retraction proper: only a control case whose still-true fact the store shows as retired "
             "counts. A fact that was never written, or was written and ranked below k, is a control miss "
             "(`control_miss_rate`) and is split by cause in `control_miss.*`.")
    add(f"{family}.nevertwice.control_case_runs",
        f"Nevertwice's control measures on {stand} rest on {ov['n']} control case-runs ({runs} runs of the corpus's controls)",
        int(ov["n"]), [str(ov["n"])], "control case-runs", int(ov["n"]), None, "pooled_nevertwice.over_retraction.n")
    cm = P.get("control_miss")
    if cm is not None:
        add(f"{family}.nevertwice.control_miss_rate",
            f"on {stand} a still-true fact did not come back for Nevertwice on {cm['k']} of {cm['n']} control "
            f"case-runs, whatever the cause",
            cm["rate"], [f"{cm['rate']:.3f}", f"{cm['rate']:.2f}"], "rate", cm["n"], cm["ci"],
            "pooled_nevertwice.control_miss.rate")
    else:
        ctl = [r for res in engine_runs() for r in controls(res)]
        if ctl:
            k, n = sum(1 for r in ctl if not r.get("current_returned")), len(ctl)
            add(f"{family}.nevertwice.control_miss_rate",
                f"on {stand} a still-true fact did not come back for Nevertwice on {k} of {n} control "
                f"case-runs, whatever the cause",
                round(k / n, 4), [f"{k / n:.3f}", f"{k / n:.2f}"], "rate", n, wilson(k, n), None,
                derivation=f"{k} control rows with current_returned false over the {n} control rows of the "
                           f"engine runs in arms.nevertwice*.rows; the pooled artifact predates the "
                           f"`pooled_nevertwice.control_miss` field")
    causes = (("retired", "control_retired_by_memory", "the memory retired it"),
              ("never_written", "control_never_written", "the extractor never wrote it"),
              ("unranked", "control_written_but_unranked", "it was written and live but ranked below the top five"))
    pooled_causes = P.get("control_causes")
    for key, field, why in causes:
        if pooled_causes is not None and key in pooled_causes:
            v, n = int(pooled_causes[key]), int(P["control_miss"]["n"])
            add(f"{family}.nevertwice.control_miss.{key}",
                f"of Nevertwice's control case-runs on {stand}, {v} of {n} lost the still-true fact because {why}",
                v, [str(v)], "control case-runs", n, None, f"pooled_nevertwice.control_causes.{key}")
        else:
            rs = [res for res in engine_runs() if field in res]
            if rs:
                v, n = sum(int(res[field]) for res in rs), sum(int(res.get("n_control", 0)) for res in rs)
                add(f"{family}.nevertwice.control_miss.{key}",
                    f"of Nevertwice's control case-runs on {stand}, {v} of {n} lost the still-true fact because {why}",
                    v, [str(v)], "control case-runs", n, None, None,
                    derivation=f"sum of arms.nevertwice*.{field} over the engine runs; the pooled artifact "
                               f"predates the `pooled_nevertwice.control_causes` field")
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
        new_shape = "control_miss_rate" in res
        v = res["control_miss_rate"] if new_shape else res["over_retraction_rate"]
        k = round(v * n_ctl)
        add(f"{family}.{arm}.control_miss_rate",
            f"on {stand} a still-true fact did not come back for {label} on {k} of {n_ctl} control cases, "
            f"whatever the cause",
            v, [f"{v:.3f}", f"{v:.2f}"], "rate", n_ctl, wilson(k, n_ctl),
            f"arms.{arm}.{'control_miss_rate' if new_shape else 'over_retraction_rate'}",
            note=None if new_shape else ("Read from the artifact's `over_retraction_rate`, which for an arm whose "
                                         "store the stand did not inspect is the broad control-miss rate: the "
                                         "still-true fact was not returned, for any cause."))
        narrow = res.get("over_retraction_rate") if new_shape else res.get("true_over_retraction_rate")
        if narrow is not None:
            kn = round(narrow * n_ctl)
            add(f"{family}.{arm}.over_retraction_rate",
                f"{label} retires a still-true fact (its store shows it invalidated or deleted) on {kn} of {n_ctl} "
                f"control cases on {stand}",
                narrow, [f"{narrow:.2f}", f"{narrow:.3f}"], "rate", n_ctl, wilson(kn, n_ctl),
                f"arms.{arm}.{'over_retraction_rate' if new_shape else 'true_over_retraction_rate'}")
        for key, field, why in causes:
            if field in res:
                add(f"{family}.{arm}.control_miss.{key}",
                    f"of {label}'s control cases on {stand}, {int(res[field])} of {n_ctl} lost the still-true "
                    f"fact because {why}",
                    int(res[field]), [str(int(res[field]))], "control cases", n_ctl, None, f"arms.{arm}.{field}")
            elif k == 0:
                add(f"{family}.{arm}.control_miss.{key}",
                    f"of {label}'s control cases on {stand}, 0 of {n_ctl} lost the still-true fact because {why}",
                    0, ["0"], "control cases", n_ctl, None, None,
                    derivation="no control case was missed on this run, so every cause is zero")
            elif arm == "naive":
                v_c = k if key == "unranked" else 0
                add(f"{family}.{arm}.control_miss.{key}",
                    f"of the floor's control cases on {stand}, {v_c} of {n_ctl} lost the still-true fact because {why}",
                    v_c, [str(v_c)], "control cases", n_ctl, None, None,
                    derivation="by construction: the floor stores every sentence and never retires one, so "
                               "every control miss is a ranking miss")
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
        for side in (a, b):
            add(f"{family}.{a}_vs_{b}.discordant.{side}",
                f"of the {pr['n']} supersession cases of {stand}, {pr[f'stale_only_{side}']} were stale for "
                f"{side} alone in the {a}-against-{b} pairing",
                int(pr[f"stale_only_{side}"]), [str(pr[f"stale_only_{side}"])], "cases", pr["n"], None,
                f"pairs[{i}].stale_only_{side}")
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
    ap.add_argument("--at", metavar="COMMIT", default="",
                    help="register further claims from an artifact the register already carries at COMMIT "
                         "(the tool learned new fields after the run): the artifact may predate HEAD, but "
                         "COMMIT must be an ancestor of HEAD, a live claim on the same artifact must already "
                         "carry it, and no file in the command's closure may have changed after it")
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
    import produced_by as pb                                     # noqa: PLC0415
    closure = pb.closure(args.command)
    if args.at:
        head = _git("rev-parse", args.at)
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=ROOT).returncode != 0:
            print(f"{args.at} is not an ancestor of HEAD")
            return 2
        if not any(c.get("raw") == args.artifact and c.get("commit") == head and not c.get("stale")
                   for c in manifest["claims"]):
            print(f"no live claim on {args.artifact} carries {args.at} - register the artifact at HEAD instead")
            return 2
        moved = [p for p in closure
                 if subprocess.run(["git", "merge-base", "--is-ancestor",
                                    _git("log", "-1", "--format=%H", "HEAD", "--", p), head],
                                   cwd=ROOT).returncode != 0]
        if moved:
            print(f"{moved[0]} changed after {args.at} - the artifact no longer describes HEAD's code")
            return 2
    else:
        head = _git("rev-parse", "HEAD")
        code_time = int(_git("log", "-1", "--format=%ct", head))
        if raw.stat().st_mtime < code_time:
            print(f"{args.artifact} predates HEAD - re-run `{args.command}`")
            return 2
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
