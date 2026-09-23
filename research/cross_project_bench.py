#!/usr/bin/env python3
"""A9 (Q5): does the principle layer actually keep an identifier out of a DIFFERENT project's
memory while still letting the underlying lesson travel? Three arms, one dataset
(`research/data/cross_project_v1.json`, 100 cases - see `gen_cross_project_dataset.py`):

  off        - NEVERTWICE_CROSS_PROJECT=off:        no cross-project section at all.
  all        - NEVERTWICE_CROSS_PROJECT=all:         the pre-Q5 behaviour, unrestricted.
  universal  - NEVERTWICE_CROSS_PROJECT=universal:   A4's default - only the promoted pool.

Per case: project_a and project_c each learned the SAME underlying rule independently, each
with its own planted identifiers (IP, host, path, entity). `principles.promote()` runs before
the `universal` arm is read, so that pool actually has something in it. project_b then asks a
topically-related prompt and the bench reads what each arm's cross-project section injects.

THREE extractor modes, because the owner's original concern was narrower than the first cut of
this bench tested (2026-09-23 review):

  --extract            (default for a real run) - each side's lesson is learned IN CONTEXT, in
                        a short synthetic session transcript with the planted identifiers woven
                        into the dialogue (an IP in a log line, a hostname in a command, a path
                        in a traceback, an entity in the prose). The REAL extraction pipeline
                        (EXTRACTION_PROMPT + generate_json, pinned model/temperature) proposes
                        the `principle` field itself - this is what actually exercises A1/A3's
                        write-time scanner, because a pre-written clean principle never does.
  --oracle-principles   a CONTROL: writes each case's pre-written ground-truth principle
                        directly, no extraction at all - isolates retrieval/promotion from
                        extraction, so a regression can be attributed to one or the other.
  (--dry's stub)        `--dry` alone uses a deterministic, no-model stub extractor that
                        deliberately smuggles one planted identifier of each class into an
                        otherwise-clean principle, in some cases - see `_stub_poison_extraction`.

Metrics, per arm, aggregated over every case:
  leak         - fraction of cases where ANY planted identifier reaches project_b's injected
                 text, and leak_by_class breaks that down per identifier class. The claim the
                 whole layer exists to make true: 0 for `universal`, nonzero for `all` (the
                 positive control that proves the metric can see a leak at all).
  benefit      - fraction of cases where project_b's injection surfaces SOMETHING from the
                 shared rule's own vocabulary - "did the layer transfer the lesson," not merely
                 "did it stay silent and safe."
  noise        - fraction of injected lines that came from the unrelated distractor.
  cross_chars  - mean injected-section length (a token-budget proxy).

Reported SEPARATELY, once per run (not per arm - it is a write-time property, not a read-time
one): scanner_rejections_by_class - how many extracted principles `principle_scan` refused,
broken down by which identifier class was in the proposed text. A rejection is the scanner
doing its job, not a leak - but it must be visible, or "leak=0" could mean either "nothing bad
was ever proposed" or "the scanner is silently eating everything," and those are very different
findings.

    python research/cross_project_bench.py --help
    python research/cross_project_bench.py --dry                  # 2 cases, stub extractor +
                                                                    # stub embedder, no model
    python research/cross_project_bench.py --cases 100            # the real --extract sweep,
                                                                    # ~200 extraction calls (2
                                                                    # per case), written under
                                                                    # .loop/explore/ by default
    python research/cross_project_bench.py --oracle-principles     # the control, no extraction
                                                                    # NOT RUN as part of this work
                                                                    # (A9, plan): the GPU is busy
                                                                    # with a measurement campaign;
                                                                    # checked in with --help and
                                                                    # --dry only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for every research script

sandbox_guard.isolate(prefix="nevertwice_cross_project_bench_")

# Pinned BEFORE any nevertwice import - the house pattern (research/supersession_bench.py): one
# extraction model, temperature 0, for every case. A different model or a sampled temperature
# would confound the architecture being measured (the principle layer's scanner) with plain
# extractor variance, and `--extract`'s whole point is measuring what a real model proposes.
LLM = os.environ.get("CROSS_PROJECT_LLM", "qwen3-coder:30b")
os.environ["NEVERTWICE_MODEL"] = LLM
os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"
os.environ["NEVERTWICE_CLOUD"] = "none"        # local only: nothing billed, nothing leaves here

sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import principles as pr  # noqa: E402

DATA = HERE / "data" / "cross_project_v1.json"
# .loop/explore/, not research/results/: an exploratory run must not write where a published,
# cited claim's artifact lives (research/evidence_manifest.json) - promote it explicitly with
# --out once the numbers are trusted, the same discipline research/principle_twins.py follows.
OUT = ROOT / ".loop" / "explore" / "cross_project.json"
ARMS = ("off", "all", "universal")
IDENTIFIER_CLASSES = ("ip", "host", "path", "entity")
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
    `--dry` instead seeds the universal pool directly from whichever side's principle survived
    the scanner (see `_dry_seed_universal`), and this stub exists only so `embed_text` never
    crashes or reaches a real backend while the rest of the plumbing is exercised."""
    import hashlib                                                  # noqa: PLC0415
    h = hashlib.sha256(text.strip().lower().encode("utf-8")).digest()
    return [b / 255.0 for b in h[:16]]


# ── oracle mode: pre-written ground truth, no extraction at all ────────────────────────────

def build_side_oracle(case: dict, side: str) -> dict | None:
    """Write `side`'s note straight from the dataset's pre-written `description`/`principle` -
    the CONTROL: it never asks a model anything, so a leak or a benefit measured here is purely
    about retrieval and promotion, never about extraction quality."""
    row = case[side]
    stem = m.write_typed_note("Patterns",
                              {"title": row["title"], "description": row["description"],
                               "principle": row["principle"]},
                              row["project"], "2026-09-23", [], "pattern")
    if not stem:
        return None
    m.update_embeddings([(stem, "pattern", row["project"], row["title"], row["description"], "")])
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    written = fm.get("principle") if isinstance(fm.get("principle"), str) else ""
    return {"stem": stem, "ntype": "pattern", "written_principle": written}


# ── --extract mode: the real extraction pipeline over a synthetic transcript ───────────────

def _extract_real(session_text: str, project: str) -> dict:
    """One real `generate_json` call, over exactly the prompt `process_session` would build for
    this transcript (empty tag_vocab/existing-notes - a fresh per-case sandbox has none). This
    is the ONE thing `--oracle-principles` cannot test: whether a real model, given raw session
    text with identifiers woven into it, proposes a `principle` that still carries one."""
    prompt = m.EXTRACTION_PROMPT.format(
        transcript=session_text, project_hint=project, tag_vocab="(empty - pick freely)",
        existing_patterns="(none)", existing_mistakes="(none)", existing_decisions="(none)",
        brain_block="", language_rule=m.language_rule(session_text),
        principle_rubric=m._principle_prompt_rubric(), principle_schema=m._principle_schema_field())
    return m.generate_json(prompt, project=project) or {}


def _stub_poison_extraction(phrasing: str, planted: dict, poison_class: str | None) -> dict:
    """A deterministic, no-model stand-in for `_extract_real`, used only by `--dry`. The
    `principle` is the clean phrasing unless `poison_class` names one identifier class to
    smuggle into it - exactly the shape a real extractor's own mistake would take if it forgot
    to de-identify before filling the field. `principle_scan` runs on this exactly as it would
    on a real model's output, so this is a faithful test of the write-time gate even though
    nothing here asked a model anything.

    KNOWN LIMIT, worth stating rather than hiding: `principle_scan` has no standalone pattern
    for the "entity" class (unlike ip/url_fqdn/path/email/host_port/version, which are
    regex-detected regardless of what the extractor declares) - an entity/product name is only
    caught through the FORBIDDEN-TOKEN path, i.e. only when the extractor also lists it in the
    item's own `entities` field (the extraction prompt asks for exactly that - "entities - 2-5
    key entities of the lesson"). This stub therefore declares `poison_class`'s planted value
    as an entity whenever it poisons the "entity" class, matching a model that FOLLOWED the
    entities instruction - the case this bench can demonstrate cleanly. It does NOT cover a
    model that mentions a product name in prose but never declares it as an entity, which is a
    real, separate risk (nothing regex-based would catch it either) worth its own bench case,
    not fixed or hidden here."""
    principle = phrasing
    entities: list[str] = []
    if poison_class:
        principle = f"{phrasing} (see {planted[poison_class]})"
        if poison_class == "entity":
            entities = [planted["entity"]]
    return {"project_relevant": True,
           "patterns": [{"title": "flaky failure fix", "description": phrasing, "facts": [],
                        "principle": principle, "entities": entities, "relations": [],
                        "confidence": 0.9}],
           "mistakes": [], "decisions": []}


def _first_pattern_or_mistake(extraction: dict) -> tuple[str, dict] | None:
    for ntype, key in (("pattern", "patterns"), ("mistake", "mistakes")):
        items = extraction.get(key)
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return ntype, items[0]
    return None


def _classes_present(text: str, planted: dict) -> list[str]:
    low = (text or "").lower()
    return [cls for cls, val in planted.items() if val and val.lower() in low]


def build_side_extracted(case: dict, side: str, *, use_real: bool, poison_class: str | None,
                         rejections: dict) -> dict | None:
    """Write `side`'s note through the REAL write path (`write_typed_note`), sourced from
    either a real model call (`use_real=True`, `--extract`) or the deterministic poisoning stub
    (`--dry`). Compares the raw PROPOSED `principle` against what actually reached disk to
    detect and CLASSIFY a scanner rejection - `write_typed_note` never exposes this itself (a
    rejected principle degrades silently, by design, A3's degradation contract), so this
    comparison is the bench's own instrumentation, not a new engine surface."""
    row = case[side]
    project, planted = row["project"], row["planted"]
    extraction = (_extract_real(row["session"], project) if use_real
                 else _stub_poison_extraction(row["principle"], planted, poison_class))
    if not extraction.get("project_relevant", True):
        return None
    picked = _first_pattern_or_mistake(extraction)
    if not picked:
        return None
    ntype, item = picked
    raw_principle = item.get("principle") if isinstance(item.get("principle"), str) else ""
    stem = m.write_typed_note(m.TYPE_FOLDER[ntype], item, project, "2026-09-23", [], ntype)
    if not stem:
        return None
    fp = m.VAULT / m.TYPE_FOLDER[ntype] / f"{stem}.md"
    fm = m._read_frontmatter_file(fp)
    written_principle = fm.get("principle") if isinstance(fm.get("principle"), str) else ""
    m.update_embeddings([(stem, ntype, project, item.get("title", ""),
                         item.get("description", ""), item.get("prevention", ""))])
    if raw_principle and not written_principle:
        for cls in _classes_present(raw_principle, planted):
            rejections[cls] = rejections.get(cls, 0) + 1
    return {"stem": stem, "ntype": ntype, "written_principle": written_principle}


# ── shared: the distractor is always oracle-written (it is noise, not the thing under test) ──

def build_distractor(case: dict) -> None:
    d = case["distractor"]
    stem = m.write_typed_note("Patterns",
                              {"title": d["title"], "description": d["description"],
                               "principle": d["principle"]},
                              d["project"], "2026-09-23", [], "pattern")
    if stem:
        m.update_embeddings([(stem, "pattern", d["project"], d["title"], d["description"], "")])


def _dry_seed_universal(case: dict, written: dict) -> None:
    """`--dry`'s stub embedder cannot cluster project_a/project_c by real semantic similarity
    (`_fixed_stub_vector` hashes two different phrasings to two different vectors), so the
    universal-arm plumbing is exercised by seeding the pool DIRECTLY from whichever side's
    principle actually survived the scanner this case - a poisoned side legitimately
    contributes NOTHING here, the same as it would in a real cosine-clustering run (a rejected
    principle is never even a promotion CANDIDATE, `principles.py::_live_principle_candidates`).
    """
    for side in ("project_a", "project_c"):
        info = written.get(side)
        principle = (info or {}).get("written_principle")
        if not principle:
            continue
        stem = m.write_typed_note("Patterns", {"title": case["topic"], "description": principle},
                                  m.UNIVERSAL_PROJECT, "2026-09-23", [], "pattern")
        if stem:
            m.update_embeddings([(stem, "pattern", m.UNIVERSAL_PROJECT, case["topic"],
                                 principle, "")])
        return


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

    leak_by_class = {cls: False for cls in IDENTIFIER_CLASSES}
    for side in ("project_a", "project_c"):
        for cls, val in (case[side]["planted"] or {}).items():
            if val and val.lower() in low:
                leak_by_class[cls] = True
    leaked = any(leak_by_class.values())

    keywords = _rule_keywords(case)
    benefit = any(k in low for k in keywords) if keywords else False

    distractor_title = case["distractor"]["title"].lower()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    noise_lines = sum(1 for ln in lines if distractor_title in ln.lower())
    noise = (noise_lines / len(lines)) if lines else 0.0

    return {"leaked": leaked, "leak_by_class": leak_by_class, "benefit": benefit, "noise": noise,
           "cross_chars": len(text), "n_hits": len(hits)}


def run_bench(cases: list[dict], *, extractor_mode: str, dry: bool) -> dict:
    """`extractor_mode` is one of "oracle" (pre-written ground truth, no extraction),
    "extract" (the real pipeline) or "stub" (the deterministic --dry poisoning fixture)."""
    embed = _fixed_stub_vector if dry else None
    per_arm: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    rejections: dict[str, int] = {cls: 0 for cls in IDENTIFIER_CLASSES}

    for i, case in enumerate(cases):
        # A FRESH vault per case, not a shared one: this bench's metrics are per-case (does
        # THIS case's own pair leak) - a shared store would let one case's universal note
        # answer another case's query, which is a real thing A4 does on purpose but not what
        # "leak" is measuring here.
        m._rebase_vault(Path(tempfile.mkdtemp(prefix="nevertwice_cpb_case_")))
        saved_embed = None
        if dry:
            saved_embed = m.embed_text
            m.embed_text = lambda text, kind=None, timeout=None, project=None, _e=embed: _e(text)
        try:
            written: dict[str, dict | None] = {}
            if extractor_mode == "oracle":
                for side in ("project_a", "project_c"):
                    written[side] = build_side_oracle(case, side)
            else:
                use_real = extractor_mode == "extract"
                for slot, side in enumerate(("project_a", "project_c")):
                    poison_class = None
                    if not use_real:
                        # 2 dry cases x 2 sides = 4 slots, one per identifier class - every
                        # class gets exercised exactly once across the whole --dry run.
                        poison_class = IDENTIFIER_CLASSES[(i * 2 + slot) % len(IDENTIFIER_CLASSES)]
                    written[side] = build_side_extracted(
                        case, side, use_real=use_real, poison_class=poison_class,
                        rejections=rejections)
            build_distractor(case)

            if dry:
                _dry_seed_universal(case, written)
            else:
                # Guard-minting (principles.py's _mint_global_guard) may call an LLM of its own
                # for a promoted mistake cluster - forcing llm_available() False here keeps this
                # bench's extraction-call budget exactly the ~200 the docstring promises,
                # regardless of extractor_mode, and steers guard proposals to the deterministic
                # fallback rather than silently billing an uncounted extra call per case.
                saved_llm_available = m.llm_available
                m.llm_available = lambda: False
                try:
                    pr.promote(apply=True)
                finally:
                    m.llm_available = saved_llm_available

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
            "leak_by_class": {cls: sum(1 for r in rows if r["leak_by_class"][cls]) / n
                             for cls in IDENTIFIER_CLASSES},
            "benefit": sum(1 for r in rows if r["benefit"]) / n,
            "noise": sum(r["noise"] for r in rows) / n,
            "cross_chars_mean": sum(r["cross_chars"] for r in rows) / n,
        }
    return {"arms": summary, "n_cases": len(cases), "extractor_mode": extractor_mode,
           "scanner_rejections_by_class": rejections}


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
    parser.add_argument("--oracle-principles", action="store_true",
                        help="control: write each case's pre-written ground-truth principle "
                             "directly, no extraction at all")
    parser.add_argument("--dry", action="store_true",
                        help="2 cases, a deterministic stub extractor (poisons one planted "
                             "identifier of each class across the 4 project_a/project_c slots) "
                             "+ stub embedder, no model - proves principle_scan rejects them "
                             "end to end")
    args = parser.parse_args(argv)

    if args.dry:
        extractor_mode = "oracle" if args.oracle_principles else "stub"
    else:
        extractor_mode = "oracle" if args.oracle_principles else "extract"

    n = 2 if args.dry else args.cases
    cases = load_cases(Path(args.data), n)
    if not cases:
        print("[cross_project_bench] no cases loaded", file=sys.stderr)
        return 1
    print(f"[cross_project_bench] {len(cases)} case(s), extractor={extractor_mode}, "
         f"arms: {', '.join(ARMS)}" + (" (no model)" if args.dry else ""))

    result = run_bench(cases, extractor_mode=extractor_mode, dry=args.dry)
    for arm, s in result["arms"].items():
        print(f"  {arm:9} leak={s['leak']:.3f} benefit={s['benefit']:.3f} "
             f"noise={s['noise']:.3f} cross_chars~{s['cross_chars_mean']:.0f}"
             f"  leak_by_class={s['leak_by_class']}")
    if extractor_mode != "oracle":
        print(f"  scanner rejections by class (the scanner doing its job, NOT a leak): "
             f"{result['scanner_rejections_by_class']}")

    if args.dry:
        print("[cross_project_bench] --dry: plumbing exercised, nothing written to disk")
        return 0

    artifact = {**result, "dataset": str(args.data), "embed_signature": m.embed_signature(),
               "extraction_model": LLM, "measured_at": _measured_at()}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    print(f"[cross_project_bench] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
