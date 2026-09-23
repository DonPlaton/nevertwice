#!/usr/bin/env python3
"""A9 (Q5): does the principle layer actually keep an identifier out of a DIFFERENT project's
memory while still letting the underlying lesson travel? Three arms, one dataset
(`research/data/cross_project_v1.json`, 100 cases - see `gen_cross_project_dataset.py`):

  off        - NEVERTWICE_CROSS_PROJECT=off:        no cross-project section at all.
  all        - NEVERTWICE_CROSS_PROJECT=all:         the pre-Q5 behaviour, unrestricted.
  universal  - NEVERTWICE_CROSS_PROJECT=universal:   A4's default - only the promoted pool.

Per case: project_a and project_c each wrote a note about the SAME underlying rule, each with
its own planted identifiers (IP, host, path, entity) in the note's DESCRIPTION (never scanned -
only `principle` is) and a clean `principle` (scanned at write time regardless of arm). For the
`universal` arm, `principles.promote()` is run first so the pool actually has something in it.
project_b then asks a topically-related prompt and the bench reads what each arm's
cross-project section would inject.

Four metrics, per arm, aggregated over every case:
  leak     - fraction of cases where ANY of project_a's or project_c's planted identifiers
             (all four classes) appears verbatim in project_b's injected cross-project text.
             The claim the whole layer exists to make true: 0 for `universal`, nonzero for
             `all` (the positive control that proves this metric can see a leak at all).
  benefit  - fraction of cases where project_b's injection surfaces SOMETHING from the shared
             rule (a lexical overlap with the rule's own key tokens) - "did the layer actually
             transfer the lesson," not merely "did it stay silent and safe."
  noise    - fraction of INJECTED LINES that came from the unrelated distractor, not from the
             shared rule - a layer that leaks the decoy as often as the real lesson is not
             useful even where it is safe.
  cross_chars - mean character length of the injected cross-project section (a token-budget
             proxy, comparable to the receipt's own accounting elsewhere in this repository).

DELIBERATE SCOPE NARROWING: this bench does not run a real LLM extraction step. Each case's
`principle`/`description` are pre-written ground truth (playing the role of "what a correct
extraction would have produced" - A1's own extraction-prompt fidelity is a separate,
already-covered concern). What DOES exercise the real backend, in a non-`--dry` run, is
embedding: the interesting measured question here is whether the REAL embedder's cosine space
actually clusters two independently-phrased statements of the same rule across projects, and
whether the resulting universal note is genuinely leak-free - not whether an LLM can follow the
extraction prompt (tested elsewhere, `tests/_test_principle_prompt.py`/`_test_principle_write.py`).

    python research/cross_project_bench.py --help
    python research/cross_project_bench.py --dry           # 2 cases, stub embedder, no model
    python research/cross_project_bench.py --cases 100     # the real sweep, written under
                                                             # .loop/explore/ by default (an
                                                             # exploratory run, not a published
                                                             # claim - pass --out to publish
                                                             # under research/results/ once the
                                                             # numbers are trusted). NOT RUN as
                                                             # part of this work (A9, plan): the
                                                             # GPU is busy with a measurement
                                                             # campaign; checked in unrun.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for every research script

sandbox_guard.isolate(prefix="nevertwice_cross_project_bench_")
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import principles as pr  # noqa: E402

DATA = HERE / "data" / "cross_project_v1.json"
# .loop/explore/, not research/results/: an exploratory run must not write where a published,
# cited claim's artifact lives (research/evidence_manifest.json) - promote it explicitly with
# --out once the numbers are trusted, the same discipline research/principle_twins.py follows.
OUT = ROOT / ".loop" / "explore" / "cross_project.json"
ARMS = ("off", "all", "universal")
_STOPWORDS = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "it", "its",
             "not", "this", "that", "so", "before", "than", "with", "at", "by", "be", "as"}


def load_cases(path: Path, n: int | None = None) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    return cases[:n] if n else cases


def _fixed_stub_vector(text: str) -> list[float]:
    """A deterministic, hash-based unit-ish vector - no model, no randomness across runs. Two
    DIFFERENT phrasings of the SAME rule (project_a/project_c in one case) will NOT hash to the
    same vector (they are different strings), so `--dry` cannot rely on cosine clustering to
    actually promote a universal note the way a real embedder's semantic similarity would -
    `--dry` instead seeds the universal pool directly (see `_dry_build_universal_pool`), and
    this stub exists only so `embed_text` never crashes or reaches a real backend while the
    rest of the plumbing (write -> retrieve, three arms) is exercised."""
    import hashlib
    h = hashlib.sha256(text.strip().lower().encode("utf-8")).digest()
    return [b / 255.0 for b in h[:16]]


def build_case(case: dict, *, embed) -> None:
    """Write project_a's, project_c's and the distractor's notes (stub extraction: the
    dataset's own `principle`/`description` play the role of what a correct extraction would
    have produced), and embed each one - `embed` is `m.embed_text` for a real run or the fixed
    stub for `--dry`."""
    for side in ("project_a", "project_c"):
        p = case[side]
        stem = m.write_typed_note("Patterns",
                                  {"title": p["title"], "description": p["description"],
                                   "principle": p["principle"]},
                                  p["project"], "2026-09-23", [], "pattern")
        if stem:
            m.update_embeddings([(stem, "pattern", p["project"], p["title"], p["description"], "")])
    d = case["distractor"]
    stem_d = m.write_typed_note("Patterns",
                                {"title": d["title"], "description": d["description"],
                                 "principle": d["principle"]},
                                d["project"], "2026-09-23", [], "pattern")
    if stem_d:
        m.update_embeddings([(stem_d, "pattern", d["project"], d["title"], d["description"], "")])


def _dry_build_universal_pool(case: dict) -> None:
    """`--dry`'s stub embedder cannot cluster project_a/project_c by real semantic similarity
    (see `_fixed_stub_vector`), so the universal-arm plumbing is exercised by seeding the pool
    DIRECTLY with a de-identified note - the point of `--dry` is proving retrieve_cross_project
    and write_typed_note wire together correctly across all three arms, not re-measuring
    clustering (that is what the non-dry run, and A6's calibration, are for)."""
    rule_a = case["project_a"]["principle"]
    stem = m.write_typed_note("Patterns", {"title": case["topic"], "description": rule_a},
                              m.UNIVERSAL_PROJECT, "2026-09-23", [], "pattern")
    if stem:
        m.update_embeddings([(stem, "pattern", m.UNIVERSAL_PROJECT, case["topic"], rule_a, "")])


def _hit_text(hits: list[dict]) -> str:
    return "\n".join(m._cross_line(h) for h in hits)


def _rule_keywords(case: dict) -> set[str]:
    words = set()
    for side in ("project_a", "project_c"):
        for tok in case[side]["principle"].lower().replace(",", " ").replace(".", " ").split():
            tok = tok.strip("-'\"")
            if len(tok) >= 4 and tok not in _STOPWORDS:
                words.add(tok)
    return words


def run_case(case: dict, arm: str) -> dict:
    project_b = case["project_b"]["project"]
    prompt = case["project_b"]["prompt"]
    hits = m.retrieve_cross_project(project_b, prompt, mode=arm)
    text = _hit_text(hits)
    low = text.lower()

    planted_vals = []
    for side in ("project_a", "project_c"):
        planted_vals.extend((case[side]["planted"] or {}).values())
    leaked = any(v and v.lower() in low for v in planted_vals)

    keywords = _rule_keywords(case)
    benefit = any(k in low for k in keywords) if keywords else False

    distractor_title = case["distractor"]["title"].lower()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    noise_lines = sum(1 for ln in lines if distractor_title in ln.lower())
    noise = (noise_lines / len(lines)) if lines else 0.0

    return {"leaked": leaked, "benefit": benefit, "noise": noise, "cross_chars": len(text),
           "n_hits": len(hits)}


def run_bench(cases: list[dict], *, dry: bool) -> dict:
    import tempfile                                                # noqa: PLC0415
    embed = _fixed_stub_vector if dry else None   # None -> real embed_text via update_embeddings
    per_arm: dict[str, list[dict]] = {arm: [] for arm in ARMS}

    for case in cases:
        # A FRESH vault per case, not a shared one: two cases share slug-independent random
        # planted identifiers but re-use the SAME distractor/topic pool at scale, and this
        # bench's metrics are per-case (does THIS case's own pair leak) - a shared store would
        # let one case's universal note answer another case's query, which is a real thing A4
        # does on purpose but not what "leak" is measuring here.
        m._rebase_vault(Path(tempfile.mkdtemp(prefix="nevertwice_cpb_case_")))
        if dry:
            saved_embed = m.embed_text
            m.embed_text = lambda text, kind=None, timeout=None, project=None: embed(text)
        try:
            build_case(case, embed=embed)
            if dry:
                _dry_build_universal_pool(case)
            else:
                pr.promote(apply=True)
            for arm in ARMS:
                per_arm[arm].append(run_case(case, arm))
        finally:
            if dry:
                m.embed_text = saved_embed

    summary = {}
    for arm, rows in per_arm.items():
        n = len(rows) or 1
        summary[arm] = {
            "n_cases": len(rows),
            "leak": sum(1 for r in rows if r["leaked"]) / n,
            "benefit": sum(1 for r in rows if r["benefit"]) / n,
            "noise": sum(r["noise"] for r in rows) / n,
            "cross_chars_mean": sum(r["cross_chars"] for r in rows) / n,
        }
    return {"arms": summary, "n_cases": len(cases)}


def _git_head() -> str:
    import subprocess                                            # noqa: PLC0415
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
    except Exception:                       # noqa: BLE001
        return "?"


def _measured_at() -> dict:
    import datetime                                              # noqa: PLC0415
    return {"commit": _git_head(),
           "utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA), help="dataset path (default: %(default)s)")
    parser.add_argument("--out", default=str(OUT), help="results artifact path (default: %(default)s)")
    parser.add_argument("--cases", type=int, default=None, help="limit to the first N cases")
    parser.add_argument("--dry", action="store_true",
                        help="2 cases, stub extractor + stub embedder, no model - plumbing only")
    args = parser.parse_args(argv)

    n = 2 if args.dry else args.cases
    cases = load_cases(Path(args.data), n)
    if not cases:
        print("[cross_project_bench] no cases loaded", file=sys.stderr)
        return 1
    print(f"[cross_project_bench] {len(cases)} case(s), arms: {', '.join(ARMS)}"
         + (" (--dry: stub embedder, no model)" if args.dry else ""))

    result = run_bench(cases, dry=args.dry)
    for arm, s in result["arms"].items():
        print(f"  {arm:9} leak={s['leak']:.3f} benefit={s['benefit']:.3f} "
             f"noise={s['noise']:.3f} cross_chars~{s['cross_chars_mean']:.0f}")

    if args.dry:
        # `--dry`'s stub embedder cannot cluster by real similarity, so leak/benefit on
        # "all"/"universal" are plumbing smoke, not a measurement - printed, not written.
        print("[cross_project_bench] --dry: plumbing exercised, nothing written to disk")
        return 0

    artifact = {**result, "dataset": str(args.data), "embed_signature": m.embed_signature(),
               "measured_at": _measured_at()}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    print(f"[cross_project_bench] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
