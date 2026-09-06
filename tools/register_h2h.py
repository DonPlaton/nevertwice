#!/usr/bin/env python3
"""Register a head-to-head family from its artifact: one claim per system and metric.

The four-system table on the oracle pool was registered by hand once (`h2h_pinned.*`); the
LoCoMo and non-oracle families never existed, because their competitor arms had not run. A
family registered by hand twice is a family registered differently twice, so this tool reads
the artifact instead: every unblocked arm gets RECALL@1/5/10 and MRR claims in the shape the
pinned family uses - a Wilson interval on each recall, the package version the artifact
recorded in the label, the import closure of the command stamped as `produced_by`, and the
artifact's own `n`.

    python tools/register_h2h.py --family h2h_locomo                 # what is missing
    python tools/register_h2h.py --family h2h_s --dry-run
    python tools/register_h2h.py --family h2h_pinned --systems mem0_infer,langmem_full,amem_full \\
        --command "python research/head_to_head.py --only=mem0_infer,langmem_full,amem_full --save --out=research/results/head_to_head_v2.json"

A claim already in the register is left alone - `tools/remeasure.py --restore` is what
re-stamps an existing number, never this. A blocked arm is skipped and named: a blocker is a
record, not a number. The artifact must be newer than HEAD and the command's closure clean in
the working tree, the same two conditions a restore demands, because a claim stamped with a
commit that did not produce its number is the drift the register exists to prevent.
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
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

KS = (1, 5, 10)
ENVIRONMENT = "local_bge_m3_pinned"

#: Per family: the artifact, the pinned corpus, the phrase every statement ends on, the command
#: whose closure becomes `produced_by`, and the governed page that prints the table.
FAMILIES: dict[str, dict] = {
    "h2h_pinned": {
        "raw": "research/results/head_to_head_v2.json",
        "dataset": "longmemeval_oracle_pinned",
        "stand": "the pinned stand",
        "command": ("python research/head_to_head.py --only=nevertwice,mem0,langmem,amem "
                    "--save --out=research/results/head_to_head_v2.json"),
        "cite": ["docs/BENCHMARKS.md"],
        "note": ("Same 940-session pool, same 500 questions, same local bge-m3, same scoring "
                 "function. Competitor versions are recorded in the artifact."),
    },
    "h2h_locomo": {
        "raw": "research/results/head_to_head_locomo.json",
        "dataset": "locomo10_pinned",
        "stand": "the LoCoMo global pool (all ten conversations in one store)",
        "command": ("python research/head_to_head.py --data=locomo "
                    "--only=nevertwice,mem0,langmem,amem --save "
                    "--out=research/results/head_to_head_locomo.json"),
        "cite": ["docs/BENCHMARKS.md"],
        "note": ("LoCoMo pooled globally: one store holds the turns of all ten conversations, "
                 "which is what a user's whole history looks like to a competitor store and is "
                 "harder than LoCoMo's own per-conversation setting (`locomo.*`). Retrieval of "
                 "the annotated evidence turn, never judge-scored answer accuracy. Competitor "
                 "versions are recorded in the artifact."),
    },
    "h2h_s": {
        "raw": "research/results/head_to_head_s.json",
        "dataset": "longmemeval_s_pinned",
        "stand": "the non-oracle LongMemEval pool",
        "command": ("python research/head_to_head.py --data=s "
                    "--only=nevertwice,mem0,langmem,amem --save "
                    "--out=research/results/head_to_head_s.json"),
        "cite": ["docs/BENCHMARKS.md"],
        "note": ("The standard non-oracle pool: 19,206 retrievable sessions against the oracle "
                 "variant's 940, the same 500 questions and annotated evidence, twenty-one times "
                 "the haystack. Same local bge-m3 and scoring function for every system. "
                 "Competitor versions are recorded in the artifact."),
    },
}

#: How each arm is named in a statement. The version comes from the artifact row
#: (`mem0ai==2.0.19` -> `2.0.19`), so a statement pins the package it was measured against.
LABELS = {
    "nevertwice": "Nevertwice (calibrated fusion)",
    "mem0": "Mem0 {v}",
    "langmem": "LangMem {v}",
    "amem": "A-MEM (chromadb {v})",
    "mem0_infer": "Mem0 {v} full pipeline (its LLM extraction on)",
    "langmem_full": "LangMem {v} full pipeline (create_memory_store_manager)",
    "amem_full": "A-MEM {v} full pipeline (agentic_memory)",
}


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def _version(arm: dict) -> str:
    v = str(arm.get("version") or "")
    return v.split("==", 1)[1] if "==" in v else ""


def label_for(system: str, arm: dict) -> str:
    tmpl = LABELS.get(system, system + " {v}")
    return " ".join(tmpl.format(v=_version(arm)).split()).replace("( ", "(").replace(" )", ")")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _dirty_files() -> set[str]:
    out = set()
    for line in _git("status", "--porcelain").splitlines():
        if len(line) > 3:
            out.add(line[3:].strip().replace("\\", "/"))
    return out


def build_claims(family: str, artifact: dict, *, systems: list[str] | None, head: str,
                 produced_by: list[str], existing: set[str], command: str | None = None,
                 note: str | None = None, environment: str = ENVIRONMENT,
                 cite: list[str] | None = None) -> tuple[list[dict], list[str], list[str]]:
    """Returns (new claims, skipped-because-existing ids, blocked arms)."""
    cfg = FAMILIES[family]
    command = command or cfg["command"]
    note = note or cfg["note"]
    cite = cfg["cite"] if cite is None else cite
    arms = [k for k in artifact if not k.startswith("_")]
    if systems:
        arms = [s for s in systems if s in artifact]
    new, skipped, blocked = [], [], []
    for system in arms:
        arm = artifact[system]
        if not isinstance(arm, dict):
            continue
        if arm.get("blocked"):
            blocked.append(f"{system}: {arm['blocked']}")
            continue
        if "recall@1" not in arm or "mrr" not in arm or not arm.get("n"):
            blocked.append(f"{system}: no recall/mrr/n row in the artifact")
            continue
        label = label_for(system, arm)
        n = int(arm["n"])
        base = {"dataset": cfg["dataset"], "environment": environment, "n": n,
                "command": command, "raw": cfg["raw"], "cited_in": list(cite),
                "commit": head, "note": note, "produced_by": list(produced_by)}
        for k in KS:
            cid = f"{family}.{system}.recall_at_{k}"
            if cid in existing:
                skipped.append(cid)
                continue
            v = float(arm[f"recall@{k}"])
            lo, hi = wilson(v, n)
            new.append({"id": cid,
                        "statement": f"{label} reaches RECALL@{k} {v:.3f} on {cfg['stand']}, "
                                     f"same embedder and scoring for every system",
                        "value": v, "printed": [f"{v:.3f}"], "unit": f"recall@{k}",
                        **base, "pointer": f"{system}.recall@{k}",
                        "ci": {"method": "wilson", "level": 0.95, "low": lo, "high": hi}})
        cid = f"{family}.{system}.mrr"
        if cid in existing:
            skipped.append(cid)
        else:
            v = float(arm["mrr"])
            new.append({"id": cid,
                        "statement": f"{label} reaches MRR {v:.3f} on {cfg['stand']}",
                        "value": v, "printed": [f"{v:.3f}"], "unit": "mrr",
                        **base, "pointer": f"{system}.mrr", "ci": None})
    return new, skipped, blocked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--family", required=True, choices=sorted(FAMILIES))
    ap.add_argument("--systems", default="", help="comma list of arms to register (default: "
                    "every unblocked arm in the artifact that has no claim yet)")
    ap.add_argument("--command", default=None, help="override the family command (the "
                    "pipeline arms run under a different --only)")
    ap.add_argument("--note", default=None, help="override the family note")
    ap.add_argument("--environment", default=ENVIRONMENT)
    ap.add_argument("--cite", action="append", default=None, metavar="DOC",
                    help="governed document(s) that print the table (default per family)")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = FAMILIES[args.family]
    raw = ROOT / cfg["raw"]
    if not raw.is_file():
        print(f"no artifact at {cfg['raw']} - run `{cfg['command']}` first")
        return 2
    artifact = json.loads(raw.read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    existing = {c["id"] for c in manifest["claims"]}

    head = _git("rev-parse", "HEAD")
    code_time = int(_git("log", "-1", "--format=%ct", head))
    if raw.stat().st_mtime < code_time:
        print(f"{cfg['raw']} predates HEAD - a claim stamped {head[:7]} must come from a run at "
              f"{head[:7]}; re-run `{args.command or cfg['command']}`")
        return 2

    import produced_by as pb                                     # noqa: PLC0415
    command = args.command or cfg["command"]
    closure = pb.closure(command)
    dirty = sorted(p for p in closure if p in _dirty_files())
    if dirty:
        print(f"working tree modifies {dirty[0]} - commit first, a claim names one commit")
        return 2

    systems = [s.strip() for s in args.systems.split(",") if s.strip()] or None
    new, skipped, blocked = build_claims(
        args.family, artifact, systems=systems, head=head, produced_by=closure,
        existing=existing, command=args.command, note=args.note,
        environment=args.environment, cite=args.cite)
    for b in blocked:
        print(f"  blocked, no claim: {b}")
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
                                   encoding="utf-8")
    print(f"registered {len(new)} claim(s) for {args.family} at {head[:7]}; now "
          f"`python tools/render_claims.py --write`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
