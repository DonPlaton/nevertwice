#!/usr/bin/env python3
"""Register the code-session stand's claims from its artifact (`research/code_sessions_eval.py judge --save`).

Per arm and question type: accuracy (and, for `current`, the stale rate); the brackets; the
corpus gates as declared non-metric booleans in a note. The dataset entry is written on first use
from the corpus file's hash (the synthetic dev set) - or, for the private held-out, from the
manifest the builder wrote, since the corpus itself is not in the repository.

    python tools/register_code_sessions.py --artifact research/results/code_sessions_v1.json \\
        --command "python research/code_sessions_eval.py judge --arms nevertwice_full,naive,mem0_infer --save"
    python tools/register_code_sessions.py --artifact research/results/code_heldout_v1.json --heldout \\
        --command "python research/code_sessions_eval.py judge --arms nevertwice_full,naive --corpus <private> --save"
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

ENVIRONMENT = "local_code_session_stand"
ARM_LABEL = {"nevertwice_full": "Nevertwice's extractor with evidence spans", "naive": "the append-only floor (whole sessions, term overlap)",
             "mem0_infer": "Mem0 2.0.19's own pipeline", "none": "the reader with no memory", "oracle": "the reader with the gold session whole"}
TYPES = ("fact", "current", "lesson", "situation", "core")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    return {line[3:].strip().replace("\\", "/") for line in _git("status", "--porcelain").splitlines() if len(line) > 3}


def dataset_entry(art: dict, heldout: bool) -> tuple[str, dict]:
    corpus = art["corpus"]
    if heldout:
        man = json.loads((ROOT / "research" / "data" / "code_heldout_manifest.json").read_text(encoding="utf-8"))
        return "code_heldout_v1", {
            "name": "code_heldout_v1 - literal-fact questions over the owner's own coding sessions",
            "citation": "this repository, research/code_heldout.py (2026-09-10)", "source": "research/code_heldout.py",
            "local_path": "outside the repository (D:/Coding/_nevertwice_polygon/code_heldout/code_heldout_v1.json)",
            "committed": False, "sha256": man.get("private_file_sha256"),
            "sha256_note": "The private file's hash is recorded in research/data/code_heldout_manifest.json with a hash per item; "
                           "no transcript text, question or answer is in the repository.",
            "note": f"{man.get('questions_kept')} questions kept of {man.get('slices_scanned')} transcript slices scanned; "
                    f"drops: {man.get('drops')}. Generator {man.get('generator_model')}."}
    p = ROOT / "research" / "data" / "code_sessions_v1.json"
    return "code_sessions_v1", {
        "name": "code_sessions_v1 - synthetic coding sessions with gold answers",
        "citation": "this repository, research/gen_code_sessions.py (2026-09-10)", "source": "research/gen_code_sessions.py",
        "local_path": "research/data/code_sessions_v1.json", "committed": True,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else corpus["sha256"],
        "note": f"{corpus['counts']} - facts and gold by program, prose by {corpus.get('generator_model')}; "
                "regenerate with `python research/gen_code_sessions.py --check`."}


def build_claims(fam: str, art: dict, *, dataset: str, command: str, raw: str, head: str, produced_by: list[str],
                 existing: set[str]) -> list[dict]:
    base = {"dataset": dataset, "environment": ENVIRONMENT, "command": command, "raw": raw, "cited_in": [],
            "commit": head, "produced_by": list(produced_by)}
    new = []

    def add(cid, statement, value, printed, unit, n, ci, pointer, note=None):
        if cid in existing:
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit, "n": n,
             "pointer": pointer, "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None), **base}
        if note:
            c["note"] = note
        new.append(c)

    for group in ("arms", "brackets"):
        for arm, sc in art.get(group, {}).items():
            label = ARM_LABEL.get(arm, arm)
            for t in TYPES:
                row = sc.get(t)
                if not row:
                    continue
                what = {"fact": "literal-fact questions", "current": "questions about a fact after it changed",
                        "lesson": "lesson questions, judged", "situation": "situations (the right note in the top three)",
                        "core": "fact, current and lesson questions together"}[t]
                add(f"{fam}.{arm}.{t}", f"{label} answers {row['accuracy'] * 100:.1f}% of the {what}",
                    row["accuracy"], [f"{row['accuracy']:.3f}"], "rate", row["n"], row.get("ci"), f'{group}.{arm}.{t}.accuracy')
                if t == "current" and row.get("stale_rate") is not None:
                    add(f"{fam}.{arm}.stale", f"and answers with the retracted value on {row['stale_rate'] * 100:.1f}% of them",
                        row["stale_rate"], [f"{row['stale_rate']:.3f}"], "rate", row["n"], row.get("stale_ci"), f'{group}.{arm}.{t}.stale_rate')
                if t == "core" and row.get("mean_prompt_tokens") is None and sc.get("fact", {}).get("mean_prompt_tokens") is not None:
                    toks = sc["fact"]["mean_prompt_tokens"]
                    add(f"{fam}.{arm}.tokens", f"at {toks:.0f} prompt tokens per literal-fact question, counted by the reader",
                        toks, [f"{toks:.0f}", f"{toks:,.0f}"], "tokens", sc["fact"]["n"], None, f'{group}.{arm}.fact.mean_prompt_tokens')
    g = art.get("corpus_gates") or {}
    add(f"{fam}.corpus.separates", "the corpus gates (no memory low, oracle high, floor a fifth below the oracle) "
        + ("all hold" if g.get("corpus_separates") else "do not all hold") + f": {g}",
        1 if g.get("corpus_separates") else 0, ["1"] if g.get("corpus_separates") else ["0"], "boolean", 1, None,
        "corpus_gates.corpus_separates",
        note="A boolean read from the artifact: 1 when every corpus gate written before the run holds. The value is the truth of the gates, not a rate.")
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--command", required=True)
    ap.add_argument("--heldout", action="store_true")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    raw = ROOT / args.artifact
    if not raw.is_file():
        print(f"no artifact at {args.artifact}")
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    head = _git("rev-parse", "HEAD")
    if raw.stat().st_mtime < int(_git("log", "-1", "--format=%ct", head)):
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
            "reader": art.get("reader"), "judge": art.get("judge"), "extractor_for_pipeline_arms": art.get("extractor"),
            "embedder": "bge-m3 via local Ollama for every arm that embeds",
            "note": "one project per synthetic project; every arm ingests a project's sessions into its own store and answers "
                    "that project's questions from it; fact and current questions are scored by markers, lessons by the judge, "
                    "situations by retrieval alone",
            "hardware": "AMD Ryzen 7 7700, NVIDIA RTX 5090 (32 GB)", "os": "Windows 11", "network": "none"}
        print(f"  + environment {ENVIRONMENT}")
    dataset, entry = dataset_entry(art, args.heldout)
    if dataset not in manifest["datasets"]:
        manifest["datasets"][dataset] = entry
        print(f"  + dataset {dataset}")
    fam = "code_heldout" if args.heldout else "code_sessions"
    existing = {c["id"] for c in manifest["claims"]}
    new = build_claims(fam, art, dataset=dataset, command=args.command, raw=args.artifact, head=head,
                       produced_by=closure, existing=existing)
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run:
        print(f"would register {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"registered {len(new)} claim(s) for {fam} at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
