#!/usr/bin/env python3
"""Register the active-memory stand's claims from its artifact (`research/guard_bench.py --save`).

One claim per arm for the matched-rate reading (recall of the right guard at FPR <= 0.05),
the hard-negative false-positive rate, the project-subset recall, tokens per call and latency
per check; plus the corpus counts. The dataset entry is written on first use from the corpus
file's own hash. Existing claims are left alone; the artifact must be newer than HEAD and the
command's closure clean - the same rules as `register_supersession.py`.

    python tools/register_guard_bench.py --artifact research/results/guard_bench_v1.json \\
        --command "python research/guard_bench.py --llm --save"
"""
from __future__ import annotations

import argparse
import hashlib
import json
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

FAMILY = "guards"
DATASET = "guard_bench_v1"
ENVIRONMENT = "local_guard_stand"
ARM_LABEL = {
    "guards_deterministic": "guards written by the engine's no-model path",
    "guards_llm": "guards written by the local model",
    "universal_pack": "the cold-start guard pack",
    "linter_or_test": "the linter or scanner that already models the mistake (scored in its favour)",
    "prompt_recall": "prompt recall over the mistake notes (the right note in the top three)",
    "never": "silence, the floor",
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    import math
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    return {line[3:].strip().replace("\\", "/") for line in _git("status", "--porcelain").splitlines() if len(line) > 3}


def dataset_entry(corpus_path: Path) -> dict:
    raw = corpus_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    c = data["counts"]
    return {"name": "guard_bench_v1 - labelled tool calls for the active-memory stand",
            "citation": "this repository, research/gen_guard_bench.py (2026-09-10)",
            "source": "research/gen_guard_bench.py", "local_path": "research/data/guard_bench_v1.json",
            "committed": True, "sha256": hashlib.sha256(raw).hexdigest(),
            "note": (f"{c['mistakes']} mistake notes ({c['generic']} generic, {c['project']} project-specific), "
                     f"{c['positives']} tool calls that repeat one, {c['negatives']} that do not "
                     f"({c['hard_negatives']} sharing the identifiers). Deterministic from an audited table; "
                     f"regenerate with `python research/gen_guard_bench.py --check`.")}


def build_claims(art: dict, *, command: str, raw: str, head: str, produced_by: list[str],
                 existing: set[str]) -> tuple[list[dict], list[str]]:
    base = {"dataset": DATASET, "environment": ENVIRONMENT, "command": command, "raw": raw,
            "cited_in": [], "commit": head, "produced_by": list(produced_by)}
    counts = art["corpus"]["counts"]
    fpr = art["target_fpr"]
    key = f"at_fpr_{fpr}"
    new, skipped = [], []

    def add(cid, statement, value, printed, unit, n, ci, pointer, note=None):
        if cid in existing:
            skipped.append(cid)
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit, "n": n,
             "pointer": pointer, "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None),
             **base}
        if note:
            c["note"] = note
        new.append(c)

    for arm, sc in art["arms"].items():
        label = ARM_LABEL.get(arm, arm)
        if sc.get("blocked"):
            continue
        mt = sc.get(key)
        n_pos = counts["positives"]
        if mt is None:
            # a binary arm has one operating point - fire on every match - and it is over the budget;
            # publish that point, because "no row" would read as "not measured"
            z = next((r for r in sc.get("curve", []) if r["threshold"] == 0.0), None)
            if z is not None:
                add(f"{FAMILY}.{arm}.recall_all_fire",
                    f"{label} catches {z['recall'] * 100:.1f}% of the repeats when it fires on every match - its only operating "
                    f"point, and it lies above the false-alarm budget of {fpr}",
                    z["recall"], [f"{z['recall']:.3f}"], "rate", n_pos, list(wilson(z["tp"], n_pos)), f'arms.{arm}.curve[0].recall',
                    note="No operating point at or below the budget: the arm fires or it does not, and firing costs more than one negative in twenty.")
                add(f"{FAMILY}.{arm}.fpr_all_fire",
                    f"and fires on {z['false_positive_rate'] * 100:.1f}% of the calls that repeat nothing",
                    z["false_positive_rate"], [f"{z['false_positive_rate']:.3f}"], "rate", counts["negatives"], None,
                    f'arms.{arm}.curve[0].false_positive_rate')
            add(f"{FAMILY}.{arm}.tokens_per_call",
                f"{label} spends {sc['tokens_per_call']} context tokens per tool call, silence included",
                sc["tokens_per_call"], [f"{sc['tokens_per_call']:.3f}", f"{sc['tokens_per_call']:.2f}", f"{sc['tokens_per_call']:.1f}"], "tokens", sc["n_calls"], None,
                f'arms.{arm}.tokens_per_call')
            add(f"{FAMILY}.{arm}.ms_per_call",
                f"and {sc['ms_per_call']} ms per check", sc["ms_per_call"], [f"{sc['ms_per_call']:.3f}", f"{sc['ms_per_call']:.4f}", f"{sc['ms_per_call']:.2f}"],
                "milliseconds", sc["n_calls"], None, f'arms.{arm}.ms_per_call')
            # The class split is a diagnostic - WHICH repeats an arm catches - and an arm that sits
            # above the false-alarm budget still has one. Skipping it here was the mirror of the
            # defect track O fixed in the bench: the two arms the project-class question is actually
            # about were the only ones with no project row, so "we tie with a linter" was read off a
            # table that had never asked them. Every figure carries the operating point it was taken
            # at, so a split measured at a false-alarm rate of 0.155 can never be read as one taken
            # at zero.
            at = (sc.get("split_at") or {}).get("false_positive_rate")
            where = f" (measured where the arm fires, a false-alarm rate of {at})" if at is not None else ""
            # The model-written arm reuses its patterns from an uncommitted cache, by design: a re-score
            # costs no generation. The scoring is this run's; the patterns may be an earlier draw, and a
            # claim that did not say so would read as a fresh model measurement.
            fresh = (sc.get("info") or {}).get("fresh_generations")
            split_note = (f"the arm's patterns came from {fresh} fresh generation(s) in this run and the "
                          f"rest from research/data/guard_bench_llm_cache.json, which is not committed; "
                          f"the scoring is this run's") if fresh is not None else None
            pr0 = sc.get("project") or {}
            if pr0.get("recall") is not None:
                add(f"{FAMILY}.{arm}.project_recall",
                    f"{label} catches {pr0['recall'] * 100:.1f}% of the repeats of project-specific mistakes - "
                    f"the facts only memory can know - {pr0['tp']} of {pr0['tp'] + pr0['fn']}{where}",
                    pr0["recall"], [f"{pr0['recall']:.3f}"], "rate", pr0["tp"] + pr0["fn"], None,
                    f'arms.{arm}.project.recall', split_note)
            if pr0.get("false_positive_rate") is not None:
                add(f"{FAMILY}.{arm}.project_fpr",
                    f"and raises a flag on {pr0['false_positive_rate'] * 100:.1f}% of the project-class calls "
                    f"that repeat nothing{where}",
                    pr0["false_positive_rate"], [f"{pr0['false_positive_rate']:.3f}"], "rate",
                    pr0["fp"] + pr0["tn"], None, f'arms.{arm}.project.false_positive_rate', split_note)
            gen0 = sc.get("generic") or {}
            if gen0.get("recall") is not None:
                add(f"{FAMILY}.{arm}.generic_recall",
                    f"{label} catches {gen0['recall'] * 100:.1f}% of the repeats a linter could also catch{where}",
                    gen0["recall"], [f"{gen0['recall']:.3f}"], "rate", gen0["tp"] + gen0["fn"], None,
                    f'arms.{arm}.generic.recall', split_note)
            hn0 = sc.get("hard_negatives") or {}
            if hn0.get("false_positive_rate") is not None:
                add(f"{FAMILY}.{arm}.hard_negative_fpr",
                    f"{label} fires on {hn0['false_positive_rate'] * 100:.1f}% of the hard negatives - "
                    f"the same identifiers, correct code{where}",
                    hn0["false_positive_rate"], [f"{hn0['false_positive_rate']:.3f}"], "rate",
                    hn0["fp"] + hn0["tn"], None, f'arms.{arm}.hard_negatives.false_positive_rate', split_note)
            continue
        add(f"{FAMILY}.{arm}.recall_at_fpr",
            f"{label} catches {mt['recall'] * 100:.1f}% of the tool calls that repeat a recorded mistake, at a "
            f"false-positive rate of {mt['false_positive_rate']} on the calls that do not (the best operating point "
            f"with a false-positive rate at or below {fpr})",
            mt["recall"], [f"{mt['recall']:.3f}"], "rate", n_pos, sc.get("recall_ci"), f'arms.{arm}["{key}"].recall')
        if mt.get("precision") is not None:
            add(f"{FAMILY}.{arm}.precision_at_fpr",
                f"with precision {mt['precision']}", mt["precision"], [f"{mt['precision']:.3f}"],
                "rate", mt["tp"] + mt["fp"], None, f'arms.{arm}["{key}"].precision')
        hn = sc.get("hard_negatives") or {}
        if hn.get("false_positive_rate") is not None:
            add(f"{FAMILY}.{arm}.hard_negative_fpr",
                f"{label} fires on {hn['false_positive_rate'] * 100:.1f}% of the hard negatives - the same identifiers, correct code",
                hn["false_positive_rate"], [f"{hn['false_positive_rate']:.3f}"], "rate", hn["fp"] + hn["tn"], None,
                f'arms.{arm}.hard_negatives.false_positive_rate')
        pr = sc.get("project") or {}
        if pr.get("recall") is not None:
            add(f"{FAMILY}.{arm}.project_recall",
                f"{label} catches {pr['recall'] * 100:.1f}% of the repeats of project-specific mistakes - the facts only memory can know",
                pr["recall"], [f"{pr['recall']:.3f}"], "rate", pr["tp"] + pr["fn"], None, f'arms.{arm}.project.recall')
        add(f"{FAMILY}.{arm}.tokens_per_call",
            f"{label} spends {sc['tokens_per_call']} context tokens per tool call, silence included",
            sc["tokens_per_call"], [f"{sc['tokens_per_call']:.3f}", f"{sc['tokens_per_call']:.2f}", f"{sc['tokens_per_call']:.1f}"], "tokens", sc["n_calls"], None,
            f'arms.{arm}.tokens_per_call')
        add(f"{FAMILY}.{arm}.ms_per_call",
            f"and {sc['ms_per_call']} ms per check", sc["ms_per_call"], [f"{sc['ms_per_call']:.3f}", f"{sc['ms_per_call']:.4f}", f"{sc['ms_per_call']:.2f}"],
            "milliseconds", sc["n_calls"], None, f'arms.{arm}.ms_per_call')
    for k_, what in (("mistakes", "mistake notes"), ("positives", "tool calls that repeat one"),
                     ("negatives", "tool calls that do not"), ("hard_negatives", "hard negatives")):
        add(f"{FAMILY}.dataset.{k_}", f"the guard corpus carries {counts[k_]} {what}", counts[k_], [str(counts[k_])],
            "count", counts[k_], None, f"corpus.counts.{k_}")
    return new, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--artifact", default="research/results/guard_bench_v1.json")
    ap.add_argument("--command", required=True)
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    raw = ROOT / args.artifact
    if not raw.is_file():
        print(f"no artifact at {args.artifact}")
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
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
    if ENVIRONMENT not in manifest["environments"]:
        manifest["environments"][ENVIRONMENT] = {
            "embedder": "bge-m3 via local Ollama for the prompt_recall arm; none for the guard arms",
            "extractor": "qwen3-coder:30b via local Ollama writes the guards_llm arm's patterns; the other arms call no model",
            "note": "guards.check is regex-only and runs on the CPU; the token and latency columns are per tool call",
            "hardware": "AMD Ryzen 7 7700, 32 GB RAM, NVIDIA RTX 5090 (32 GB) for the two model-backed arms",
            "os": "Windows 11", "network": "none"}
        print(f"  + environment {ENVIRONMENT}")
    if DATASET not in manifest["datasets"]:
        manifest["datasets"][DATASET] = dataset_entry(ROOT / "research" / "data" / "guard_bench_v1.json")
        print(f"  + dataset {DATASET}")
    if manifest["datasets"][DATASET]["sha256"] != art["corpus"]["sha256"]:
        print("the artifact was produced from a corpus whose hash differs from the registered dataset")
        return 2
    existing = {c["id"] for c in manifest["claims"]}
    new, skipped = build_claims(art, command=args.command, raw=args.artifact, head=head,
                                produced_by=closure, existing=existing)
    if skipped:
        print(f"  already registered ({len(skipped)})")
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run:
        print(f"would register {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"registered {len(new)} claim(s) for {FAMILY} at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
