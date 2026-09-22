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
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

#: Kept only for the docstrings that name it. The depths a run is registered at come from the
#: ARTIFACT, below - see `recall_depths`.
KS = (1, 5, 10)


def recall_depths(arm: dict) -> list[int]:
    """The k values this arm actually carries, ascending, read off the row.

    This file used to hold `KS = (1, 5, 10)` against the stand's `KS = (1, 3, 5, 10)`, and the
    cost was silent and years long: the stand measures recall@3 on every run and the registrar
    never looked for it, so the register holds 19 recall@1, 19 recall@5 and 19 recall@10 claims
    from the head-to-head family and ZERO recall@3. The numbers were never wrong - they were
    never there. Two copies of one constant, each sensible on its own, and nothing compared them.

    Reading the depths off the artifact makes "what was measured is what is registered" a
    property rather than a coincidence. It does not retroactively add the missing claims: a
    claim registered now would be stamped with today's HEAD while the artifact was measured at
    another commit, which is a freshness lie. They arrive with the next run of the stand.
    """
    out = []
    for key in arm:
        if key.startswith("recall@") and key[7:].isdigit():
            out.append(int(key[7:]))
    return sorted(out)

def mrr_key(arm: dict) -> str | None:
    """The arm's reciprocal-rank field, taken from the ARTIFACT rather than remembered here.

    The stand renamed it from `mrr` to `mrr@10` when it started reading only the first max(KS)
    candidates - the truncation that makes arms with different return depths comparable. A
    registrar carrying its own copy of the name is a second definition of the same thing, and
    this file already holds one: `KS` here is (1, 5, 10) while the stand's is (1, 3, 5, 10). So
    the key is read off the row, and an ambiguous row registers nothing rather than guessing.
    """
    keys = [k for k in arm if k == "mrr" or k.startswith("mrr@")]
    return keys[0] if len(keys) == 1 else None
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
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


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
        #: The stand names this metric for the depth it is taken at - `mrr@10`, from max(KS) -
        #: because it reads the first max(KS) candidates and no further. Read it by the same
        #: name the stand writes, so a rename cannot leave the registrar quietly skipping every
        #: arm for want of a key.
        mkey = mrr_key(arm)
        if "recall@1" not in arm or mkey is None or not arm.get("n"):
            blocked.append(f"{system}: no recall / single reciprocal-rank field / n row in the "
                           f"artifact (mrr-like keys: "
                           f"{[k for k in arm if k.startswith('mrr')] or 'none'})")
            continue
        label = label_for(system, arm)
        n = int(arm["n"])
        base = {"dataset": cfg["dataset"], "environment": environment, "n": n,
                "command": command, "raw": cfg["raw"], "cited_in": list(cite),
                "commit": head, "note": note, "produced_by": list(produced_by)}
        for k in recall_depths(arm):
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
            v = float(arm[mkey])
            new.append({"id": cid,
                        "statement": f"{label} reaches {mkey.upper()} {v:.3f} on {cfg['stand']}",
                        "value": v, "printed": [f"{v:.3f}"], "unit": mkey,
                        **base, "pointer": f"{system}.{mkey}", "ci": None})
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
                                   encoding="utf-8", newline="\n")
    print(f"registered {len(new)} claim(s) for {args.family} at {head[:7]}; now "
          f"`python tools/render_claims.py --write`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
