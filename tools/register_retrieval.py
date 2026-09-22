#!/usr/bin/env python3
"""Register a retrieval family from a `longmem_eval` / `locomo_eval` artifact.

Those two stands write `methods.<slug>.recall@k` and `methods.<slug>.mrr` with `questions` as n.
The pinned families (`longmem_pinned.*`, `longmem_s.*`, `locomo.*`) were registered by three
one-off scripts in a session scratchpad; the morphology ablation adds two more families
(`locomo_raw.*`, `longmem_raw.*`), and a family registered by hand five times is a family
registered five different ways. This tool reads the artifact instead.

    python tools/register_retrieval.py --family locomo_raw \\
        --artifact research/results/locomo_raw.json --dataset locomo10_pinned \\
        --command "python research/locomo_eval.py --no-morphology --save --out=research/results/locomo_raw.json" \\
        --stand "LoCoMo, per conversation, on raw tokens" --label-suffix " (raw tokens)"

A claim already in the register is left alone (`tools/remeasure.py --restore` re-stamps
existing numbers); the artifact must be newer than HEAD and the command's closure clean, the
same two conditions a restore demands.
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
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass


def recall_depths(row: dict) -> list[int]:
    """The k values this row actually carries, ascending, read off the artifact.

    The same defect as in `register_h2h.py`, in the second registrar and found by the same
    sweep: `KS` here was (1, 5, 10) while both stands it serves - `longmem_eval.py` and
    `locomo_eval.py` - carry (1, 3, 5, 10) and write all four into the artifact. Counted in the
    register: longmem_pinned, longmem_s, locomo_raw and longmem_raw hold recall@1, @5 and @10
    and no recall@3 at all; the three recall@3 claims that do exist are in `locomo`, registered
    by the one-off scripts this tool replaced. Four families, three arms each - twelve numbers
    measured on every run and registered on none.

    Nothing is back-filled: a claim registered today carries today's HEAD, and these artifacts
    were measured at other commits. The depths arrive with the next run.
    """
    return sorted(int(key[7:]) for key in row
                  if key.startswith("recall@") and key[7:].isdigit())


ENVIRONMENT = "local_bge_m3_pinned"
#: Artifact method key -> (claim slug, label)
METHODS = {
    "semantic": ("semantic", "semantic recall (bge-m3 bi-encoder)"),
    "lexical": ("lexical", "lexical recall (BM25, no embedder)"),
    "hybrid": ("hybrid", "the shipped ranker (calibrated score fusion)"),
    "hybrid+xrerank": ("hybrid_xrerank", "the shipped ranker plus the opt-in trained cross-encoder"),
}


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _dirty_files() -> set[str]:
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


def build_claims(family: str, artifact: dict, *, dataset: str, stand: str, command: str,
                 raw: str, head: str, produced_by: list[str], existing: set[str],
                 note: str = "", label_suffix: str = "", environment: str = ENVIRONMENT,
                 cite: list[str] | None = None, methods: list[str] | None = None,
                 categories: bool = False) -> tuple[list[dict], list[str]]:
    """Returns (new claims, ids skipped because they already exist)."""
    n = int(artifact.get("questions") or 0)
    if not n:
        raise ValueError("artifact carries no `questions` count")
    rows = artifact.get("methods") or {}
    new, skipped = [], []
    for key, (slug, label) in METHODS.items():
        if key not in rows or (methods and key not in methods):
            continue
        row = rows[key]
        label = label + label_suffix
        base = {"dataset": dataset, "environment": environment, "n": n, "command": command,
                "raw": raw, "cited_in": list(cite or []), "commit": head, "note": note,
                "produced_by": list(produced_by)}
        for k in recall_depths(row):
            cid = f"{family}.{slug}.recall_at_{k}"
            if cid in existing:
                skipped.append(cid)
                continue
            v = float(row[f"recall@{k}"])
            lo, hi = wilson(v, n)
            new.append({"id": cid,
                        "statement": f"{label} reaches RECALL@{k} {v:.3f} on {stand}",
                        "value": v, "printed": [f"{v:.3f}"], "unit": f"recall@{k}", **base,
                        "pointer": f"methods.{key}.recall@{k}",
                        "ci": {"method": "wilson", "level": 0.95, "low": lo, "high": hi}})
        cid = f"{family}.{slug}.mrr"
        if cid in existing:
            skipped.append(cid)
            continue
        v = float(row["mrr"])
        new.append({"id": cid, "statement": f"{label} reaches MRR {v:.3f} on {stand}",
                    "value": v, "printed": [f"{v:.3f}"], "unit": "mrr", **base,
                    "pointer": f"methods.{key}.mrr", "ci": None})
    # LoCoMo's per-category R@5 for the shipped ranker. `research/LOCOMO.md` quoted these by hand
    # and drifted a whole engine revision behind the table above them (2026-09-11).
    cats = artifact.get("by_category_recall_at_5") or {}
    if categories and "hybrid" in rows:
        for cat in sorted(cats, key=lambda x: int(x)):
            cid = f"{family}.hybrid.by_category.{cat}.recall_at_5"
            if cid in existing:
                skipped.append(cid)
                continue
            if "hybrid" not in cats[cat]:
                continue
            v = float(cats[cat]["hybrid"])
            new.append({"id": cid,
                        "statement": f"{METHODS['hybrid'][1]}{label_suffix} reaches RECALL@5 {v:.3f} on the "
                                     f"category-{cat} questions of {stand}",
                        "value": v, "printed": [f"{v:.3f}"], "unit": "recall@5",
                        "dataset": dataset, "environment": environment, "n": None, "command": command,
                        "raw": raw, "cited_in": list(cite or []), "commit": head, "note": note,
                        "produced_by": list(produced_by), "pointer": f'by_category_recall_at_5["{cat}"].hybrid',
                        "ci": None, "ci_note": "the artifact records the per-category rate, not its count"})
    return new, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--family", required=True)
    ap.add_argument("--artifact", required=True, help="repo-relative result file")
    ap.add_argument("--dataset", required=True, help="a key of the manifest's datasets")
    ap.add_argument("--command", required=True, help="the exact command that wrote the artifact")
    ap.add_argument("--stand", required=True, help="how the statement names the setting")
    ap.add_argument("--note", default="")
    ap.add_argument("--label-suffix", default="")
    ap.add_argument("--environment", default=ENVIRONMENT)
    ap.add_argument("--cite", action="append", default=None, metavar="DOC")
    ap.add_argument("--methods", default="", help="comma list of artifact method keys (default: all known)")
    ap.add_argument("--categories", action="store_true",
                    help="also register the shipped ranker's per-category R@5 (`by_category_recall_at_5`)")
    ap.add_argument("--at", metavar="COMMIT", default="",
                    help="register further fields of an artifact the register already carries at COMMIT: "
                         "COMMIT must be an ancestor of HEAD, a live claim on the artifact must carry it, "
                         "and no file in the command's closure may have changed after it")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raw = ROOT / args.artifact
    if not raw.is_file():
        print(f"no artifact at {args.artifact} - run `{args.command}` first")
        return 2
    artifact = json.loads(raw.read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.dataset not in manifest.get("datasets", {}):
        print(f"unknown dataset {args.dataset!r} - register it first")
        return 2
    existing = {c["id"] for c in manifest["claims"]}

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
            print(f"{args.artifact} predates HEAD - a claim stamped {head[:7]} must come from a run "
                  f"at {head[:7]}; re-run `{args.command}`")
            return 2
    dirty = sorted(p for p in closure if p in _dirty_files())
    if dirty:
        print(f"working tree modifies {dirty[0]} - commit first, a claim names one commit")
        return 2

    try:
        new, skipped = build_claims(
            args.family, artifact, dataset=args.dataset, stand=args.stand, command=args.command,
            raw=args.artifact, head=head, produced_by=closure, existing=existing, note=args.note,
            label_suffix=args.label_suffix, environment=args.environment, cite=args.cite,
            methods=[s for s in args.methods.split(",") if s] or None, categories=args.categories)
    except (ValueError, KeyError) as e:
        print(f"artifact does not have the longmem_eval/locomo_eval shape: {e}")
        return 2
    if skipped:
        print(f"  already registered ({len(skipped)}): {', '.join(skipped[:4])}"
              + (" ..." if len(skipped) > 4 else ""))
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                                   encoding="utf-8", newline="\n")
    print(f"registered {len(new)} claim(s) for {args.family} at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
