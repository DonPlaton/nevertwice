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
                        deliberately smuggles one planted identifier into an otherwise-clean
                        principle, across 6 project_a/project_c slots (one per identifier
                        class, plus a second "entity" scenario left undeclared) - see
                        `_stub_poison_extraction` and `_DRY_POISON_PLAN`.

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

Reported SEPARATELY, once per run (not per arm - each is a WRITE- or PROMOTION-time property,
not a read-time one), and by STAGE (owner review, 2026-09-23 - closing W17 moved the boundary
for an undeclared entity name from "documented gap" to "caught one layer later"):
  write_rejections_by_class      - how many extracted principles `principle_scan` refused
                                    before they ever reached disk, by identifier class.
  promotion_rejections_by_class  - how many planted identifiers reached disk (principle_scan
                                    had nothing to catch them WITH - a real risk for the
                                    "entity" class specifically, W17) but were kept out of the
                                    universal pool by `principles.py`'s token-provenance gate
                                    at promotion time instead.
A rejection at either stage is the principle layer doing its job, not a leak - but which
boundary caught it has to stay visible, or "leak=0" cannot be told apart from "nothing was ever
checked."

    python research/cross_project_bench.py --help
    python research/cross_project_bench.py --dry                  # 3 cases, stub extractor +
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
import re
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


#: `_stub_poison_extraction` always appends a poisoning clause in exactly this shape - stripped
#: here so the stub embedder can hash the SHARED base phrasing underneath it. A real embedder
#: would treat "X (see acme-widget-server)" as a small variation on "X", not a different topic;
#: this is what lets `--dry` cluster a poisoned candidate with its own case's clean twin instead
#: of bypassing `principles.py`'s real clustering/provenance pipeline entirely.
_POISON_SUFFIX_RE = re.compile(r"\s*\(see [^)]*\)\s*$")


def _fixed_stub_vector(text: str) -> list[float]:
    """A deterministic, hash-based unit-ish vector - no model, no randomness across runs.
    Stripping the poisoning suffix FIRST means a poisoned candidate and its case's own clean
    twin hash to the SAME base text and therefore cluster (cosine 1.0) exactly as they would
    under a real embedder's semantic similarity - unlike a bare whole-text hash (which cannot
    cluster two different strings at all), this lets `--dry` drive `principles.promote()`'s
    REAL clustering and token-provenance gate end to end, not a bypass of it. Two DIFFERENT
    cases' base phrasings still hash to different, effectively orthogonal vectors, so cases
    never cluster with each other (each also gets its own isolated per-case vault anyway)."""
    import hashlib                                                  # noqa: PLC0415
    base = _POISON_SUFFIX_RE.sub("", text.strip().lower())
    h = hashlib.sha256(base.encode("utf-8")).digest()
    return [b / 255.0 for b in h[:16]]


# ── oracle mode: pre-written ground truth, no extraction at all ────────────────────────────

def build_side_oracle(case: dict, side: str, *, principle_override: str | None = None) -> dict:
    """Write `side`'s note straight from the dataset's pre-written `description`/`principle` -
    the CONTROL: it never asks a model anything, so a leak or a benefit measured here is purely
    about retrieval and promotion, never about extraction quality.

    `principle_override`, `--dry` only: the dataset's own project_a/project_c phrasings are
    genuine independent paraphrases (different words for the same rule, by design - the same
    corpus A6's calibration uses), which `--dry`'s hash-based stub embedder cannot cluster (it
    is not a semantic model). Overriding both sides to the SAME text lets `--dry` still drive
    `principles.promote()`'s real pipeline end to end; whether genuinely-different paraphrasing
    clusters under a REAL embedder is exactly what `--cases N` (a real run) and A6 measure, not
    what this control is for."""
    row = case[side]
    principle = principle_override if principle_override is not None else row["principle"]
    # Oracle mode never "extracts" - it is deterministic ground truth, one item - but the row
    # shape is kept identical to `build_side_extracted`'s so a diagnostic row reads the same
    # regardless of `extractor_mode`.
    items = [{"ntype": "pattern", "title": row["title"],
             "has_principle": bool(principle.strip()), "principle": principle}]
    stem = m.write_typed_note("Patterns",
                              {"title": row["title"], "description": row["description"],
                               "principle": principle},
                              row["project"], "2026-09-23", [], "pattern")
    if not stem:
        return {"stem": None, "ntype": "pattern", "written_principle": "",
               "project_relevant": True, "n_items": 1, "items": items,
               "raw_principle": principle}
    m.update_embeddings([(stem, "pattern", row["project"], row["title"], row["description"], "")])
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    written = fm.get("principle") if isinstance(fm.get("principle"), str) else ""
    return {"stem": stem, "ntype": "pattern", "written_principle": written,
           "project_relevant": True, "n_items": 1, "items": items, "raw_principle": principle}


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

    TWO shapes for the "entity" identifier, because they exercise DIFFERENT boundaries
    (W17, then its closure at promotion, owner review 2026-09-23):
      "entity"             - the planted value IS declared in the item's `entities` field, the
                              way a model that followed the extraction prompt's own "entities -
                              2-5 key entities of the lesson" instruction would. `principle_scan`
                              catches this at WRITE time through the forbidden-token path (its
                              only path for this class - it has no standalone regex pattern for
                              "entity", unlike the other five classes).
      "entity_undeclared"   - the SAME planted value, mentioned in the principle's prose but
                              NEVER declared as an entity - a model that did not follow that
                              instruction. `principle_scan` has nothing to catch this WITH (no
                              declared token to forbid, no regex pattern either), so it reaches
                              disk; `principles.py`'s token-provenance check at PROMOTION time
                              is the boundary that has to catch it instead - the actual gap W17
                              named, now closed one layer later than write time."""
    principle = phrasing
    entities: list[str] = []
    if poison_class:
        base_class = "entity" if poison_class == "entity_undeclared" else poison_class
        principle = f"{phrasing} (see {planted[base_class]})"
        if poison_class == "entity":            # declared - NOT "entity_undeclared"
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


# ── diagnostic instrumentation (2026-09-24, owner review of the first real `--extract` run) ──
# leak=0 on `all` (the positive control PREREG G5.1 needs to leak) and an empty universal pool
# with no promotion line at all, on the SAME run, are both symptoms that could come from several
# different places - the extractor never proposing a `principle`, the write-time scanner eating
# it, `promote()` never seeing a candidate, the A/C principles clustering below T_PRINCIPLE, or
# the identifier never reaching the DESCRIPTION `_cross_line` actually renders in the first
# place. `rows` (below) is built so each hypothesis has ONE field that settles it, rather than
# re-running with print statements sprinkled in by hand every time a new guess needs checking.

def _identifier_hits(text: str, planted: dict) -> dict[str, bool]:
    """Which of the fixed `IDENTIFIER_CLASSES` appear (case-insensitive substring) in `text` -
    always all four keys, unlike `_classes_present`'s presence-only list, so a row's shape does
    not depend on which classes happened to fire."""
    low = (text or "").lower()
    return {cls: bool(planted.get(cls)) and str(planted[cls]).lower() in low
           for cls in IDENTIFIER_CLASSES}


def _extraction_items(extraction: dict) -> list[dict]:
    """Every pattern/mistake item a real (or stub) extraction call proposed, title + whether it
    carries a non-empty `principle` - diagnostic only. `_first_pattern_or_mistake` still decides
    which ONE item actually gets written; this exists to answer "did a LATER item have a
    principle the first one lacked" (H1/H2 at the item level, not just the written note's)."""
    out = []
    for ntype, key in (("pattern", "patterns"), ("mistake", "mistakes")):
        items = extraction.get(key)
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                p = it.get("principle") if isinstance(it.get("principle"), str) else ""
                out.append({"ntype": ntype, "title": it.get("title", ""),
                           "has_principle": bool(p.strip()), "principle": p})
    return out


def build_side_extracted(case: dict, side: str, *, use_real: bool, poison_class: str | None,
                         rejections: dict, stub_phrasing: str | None = None) -> dict:
    """Write `side`'s note through the REAL write path (`write_typed_note`), sourced from
    either a real model call (`use_real=True`, `--extract`) or the deterministic poisoning stub
    (`--dry`). Compares the raw PROPOSED `principle` against what actually reached disk to
    detect and CLASSIFY a WRITE-time scanner rejection - `write_typed_note` never exposes this
    itself (a rejected principle degrades silently, by design, A3's degradation contract), so
    this comparison is the bench's own instrumentation, not a new engine surface.

    Always returns a dict (never bare `None`) - `stem`/`ntype` are `None` when nothing was
    written (project_relevant=False, an empty extraction, or a write-time refusal); every other
    field (`n_items`, `items`, `raw_principle`) is still populated, because "the extractor
    proposed nothing usable" and "something was proposed but rejected" are different answers to
    H1/H2 and a diagnostic row has to be able to tell them apart.

    `stub_phrasing`, stub mode only: the SHARED base phrasing to use for BOTH project_a and
    project_c (instead of each side's own, naturally-divergent paraphrase from the dataset) -
    isolates what the token-provenance tests are actually about (one side's poisoning clause)
    from ordinary paraphrase vocabulary drift, the same discipline
    tests/_test_principle_promote.py's own provenance fixtures use."""
    row = case[side]
    project, planted = row["project"], row["planted"]
    if use_real:
        extraction = _extract_real(row["session"], project)
    else:
        phrasing = stub_phrasing if stub_phrasing is not None else row["principle"]
        extraction = _stub_poison_extraction(phrasing, planted, poison_class)
    # H1/H2 diagnostic: EVERY proposed item's principle status, not just whichever one
    # `_first_pattern_or_mistake` picks below - settles "did the extractor emit `principle` at
    # all" independently of "did the bench happen to pick the item that had one".
    items_all = _extraction_items(extraction)
    if not extraction.get("project_relevant", True):
        return {"stem": None, "ntype": None, "written_principle": "",
               "project_relevant": False, "n_items": len(items_all), "items": items_all,
               "raw_principle": ""}
    picked = _first_pattern_or_mistake(extraction)
    if not picked:
        return {"stem": None, "ntype": None, "written_principle": "",
               "project_relevant": True, "n_items": len(items_all), "items": items_all,
               "raw_principle": ""}
    ntype, item = picked
    raw_principle = item.get("principle") if isinstance(item.get("principle"), str) else ""
    stem = m.write_typed_note(m.TYPE_FOLDER[ntype], item, project, "2026-09-23", [], ntype)
    if not stem:
        return {"stem": None, "ntype": ntype, "written_principle": "",
               "project_relevant": True, "n_items": len(items_all), "items": items_all,
               "raw_principle": raw_principle}
    fp = m.VAULT / m.TYPE_FOLDER[ntype] / f"{stem}.md"
    fm = m._read_frontmatter_file(fp)
    written_principle = fm.get("principle") if isinstance(fm.get("principle"), str) else ""
    m.update_embeddings([(stem, ntype, project, item.get("title", ""),
                         item.get("description", ""), item.get("prevention", ""))])
    if raw_principle and not written_principle:
        for cls in _classes_present(raw_principle, planted):
            rejections[cls] = rejections.get(cls, 0) + 1
    return {"stem": stem, "ntype": ntype, "written_principle": written_principle,
           "project_relevant": True, "n_items": len(items_all), "items": items_all,
           "raw_principle": raw_principle}


# ── shared: the distractor is always oracle-written (it is noise, not the thing under test) ──

def build_distractor(case: dict) -> None:
    d = case["distractor"]
    stem = m.write_typed_note("Patterns",
                              {"title": d["title"], "description": d["description"],
                               "principle": d["principle"]},
                              d["project"], "2026-09-23", [], "pattern")
    if stem:
        m.update_embeddings([(stem, "pattern", d["project"], d["title"], d["description"], "")])


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
           "cross_chars": len(text), "n_hits": len(hits),
           # the raw rendered text - NOT aggregated into `summary` below, only read (per case,
           # `all` arm only) to build the diagnostic row's cross-section preview, so the arm
           # summaries stay exactly what they were before this instrumentation existed.
           "text": text}


def _side_written_description(info: dict) -> str:
    """The text `_cross_line` would actually render for this note - `_note_snippet` reads the
    note's DESCRIPTION section from disk, never its `principle` frontmatter (H5): a planted
    identifier can survive all the way into `principle` and still never reach project_b,
    because the injected line is built from the description, not the principle. `""` when
    nothing was written for this side."""
    if not info.get("stem"):
        return ""
    return m._note_snippet(info["stem"], info["ntype"])


def _principle_cosine(info_a: dict, info_c: dict, project_a: str, project_c: str) -> float | None:
    """Cosine between what is ACTUALLY on disk for A and C, embedded the same way
    `principles.py::_embed` embeds a promotion candidate (own project identity, `doc_embed_kind`)
    - the exact pair `promote()`'s clustering step would see (H4). `None` when either side wrote
    no principle at all, so "nothing to cluster" (H1/H2) is never read as "clustered below
    T_PRINCIPLE" (H4) - they are different rows in the report this instruments."""
    pa, pc = info_a.get("written_principle") or "", info_c.get("written_principle") or ""
    if not pa or not pc:
        return None
    kind = m.doc_embed_kind() if hasattr(m, "doc_embed_kind") else None
    va = m.embed_text(pa, kind=kind, project=project_a)
    vc = m.embed_text(pc, kind=kind, project=project_c)
    if not va or not vc:
        return None
    return m.cosine(va, vc)


def _cross_preview(project_b: str, query: str, max_chars: int = 300) -> str:
    """The `all`-arm cross-project section a real injection would render for this query,
    truncated - built from the retrieval+render PRIMITIVE (`retrieve_cross_project` +
    `_cross_line`), not by calling `emit_session_start_context`/`emit_prompt_recall` directly.
    Those two read `CROSS_PROJECT_MODE`/`INJECT_CROSS_PROJECT` off the module globals bound at
    IMPORT time, not from this bench's per-arm `mode=` override - calling them as-is would
    silently preview the wrong arm, and mutating those globals per case would be a third thing
    to save/restore in the `finally` below, alongside `embed_text` and `llm_available`, for a
    preview that (by design) renders the same lines either way: both real injection paths build
    their cross section from this exact call, just with a different query and a budget/dedup
    pass this preview does not simulate."""
    hits = m.retrieve_cross_project(project_b, query, mode="all")
    return _hit_text(hits)[:max_chars]


def _diagnostic_row(i: int, case: dict, written: dict[str, dict], case_rows: dict[str, dict],
                    promote_report: dict) -> dict:
    """One row of `rows` (owner review, 2026-09-24): everything H1-H5 need to be told apart,
    for A and C plus the promotion step plus what project_b's `all`-arm cross section actually
    renders - see the module docstring's "Diagnostic instrumentation" section."""
    project_b = case["project_b"]["project"]
    prompt = case["project_b"]["prompt"]
    sides = {}
    for side in ("project_a", "project_c"):
        info = written[side]
        planted = case[side]["planted"] or {}
        written_desc = _side_written_description(info)
        sides[side] = {
            "project": case[side]["project"],
            "n_extracted_items": info.get("n_items", 0),
            "items": info.get("items", []),
            "written": bool(info.get("stem")),
            "has_principle": bool((info.get("written_principle") or "").strip()),
            "written_principle": info.get("written_principle") or "",
            "written_description": written_desc,
            "description_identifier_hits": _identifier_hits(written_desc, planted),
        }
    return {
        "case_id": case.get("id", i), "case_index": i,
        "project_a": sides["project_a"], "project_c": sides["project_c"],
        "principle_cosine": _principle_cosine(written["project_a"], written["project_c"],
                                              case["project_a"]["project"],
                                              case["project_c"]["project"]),
        "promote_report": promote_report,
        "all_arm": {
            "session_start_cross_preview": _cross_preview(project_b, project_b),
            # reuses `all`'s own run_case() call above - the SAME retrieve_cross_project(project_b,
            # prompt, mode="all") call, not a second one.
            "prompt_cross_preview": (case_rows["all"].get("text") or "")[:300],
        },
    }


#: --dry's fixture: 3 cases x 2 sides = 6 slots. The first four cover the write-time classes
#: (ip/host/path caught by regex regardless of declaration; entity declared, caught through
#: the forbidden-token path) - the ORIGINAL --dry design. The fifth is the restored first-draft
#: variant (owner review, 2026-09-23): the SAME entity value, mentioned in prose but NEVER
#: declared - write-time has nothing to catch it WITH, so it reaches disk, and the sixth slot
#: (case 2's project_c, left clean) is what lets the token-provenance gate at PROMOTION time
#: either recover via the clean fallback or reject the cluster outright - either way the
#: identifier must not reach project_b, which is exactly what this fixture proves.
_DRY_POISON_PLAN: dict[tuple[int, str], str | None] = {
    (0, "project_a"): "ip", (0, "project_c"): "host",
    (1, "project_a"): "path", (1, "project_c"): "entity",
    (2, "project_a"): "entity_undeclared", (2, "project_c"): None,
}
_DRY_N_CASES = 3


def run_bench(cases: list[dict], *, extractor_mode: str, dry: bool) -> dict:
    """`extractor_mode` is one of "oracle" (pre-written ground truth, no extraction),
    "extract" (the real pipeline) or "stub" (the deterministic --dry poisoning fixture).

    Two REJECTION STAGES, reported separately (owner review, 2026-09-23) - a rejection is the
    principle layer doing its job, not a leak, but WHICH boundary caught it has to be visible,
    or "leak=0" cannot be told apart from "nothing was ever checked":
      write_rejections_by_class      - principle_scan refused it before it ever reached disk.
      promotion_rejections_by_class  - it WAS written (principle_scan had nothing to catch it
                                        with), but the token-provenance gate in
                                        principles.py kept it out of the universal pool (either
                                        by rejecting the whole cluster, or by promoting a
                                        clean fallback candidate instead - either way this
                                        specific identifier never reached project_b)."""
    embed = _fixed_stub_vector if dry else None
    per_arm: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    write_rejections: dict[str, int] = {cls: 0 for cls in IDENTIFIER_CLASSES}
    promotion_rejections: dict[str, int] = {cls: 0 for cls in IDENTIFIER_CLASSES}
    # NOT named `rows`: the summary loop below already uses that name for its own per-arm
    # iteration variable, and shadowing it would leave this bound to whichever arm's per-case
    # list the summary loop happened to visit last - a silent, wrong answer, not a crash.
    diag_rows: list[dict] = []

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
            written: dict[str, dict] = {}
            if extractor_mode == "oracle":
                # --dry: both sides share project_a's own phrasing (see build_side_oracle's
                # docstring) so the hash-based stub embedder can still cluster them; a real
                # (non-dry) oracle run keeps each side's own dataset phrasing untouched.
                oracle_override = case["project_a"]["principle"] if dry else None
                for side in ("project_a", "project_c"):
                    written[side] = build_side_oracle(case, side,
                                                      principle_override=oracle_override)
            else:
                use_real = extractor_mode == "extract"
                for side in ("project_a", "project_c"):
                    poison_class = None if use_real else _DRY_POISON_PLAN.get((i, side))
                    # stub mode: BOTH sides use project_a's OWN phrasing as the shared base, so
                    # a provenance pass/fail is purely about the poisoning clause, never about
                    # the dataset's ordinary (and irrelevant here) paraphrase vocabulary drift.
                    stub_phrasing = None if use_real else case["project_a"]["principle"]
                    written[side] = build_side_extracted(
                        case, side, use_real=use_real, poison_class=poison_class,
                        rejections=write_rejections, stub_phrasing=stub_phrasing)
            build_distractor(case)

            # Guard-minting (principles.py's _mint_global_guard) may call an LLM of its own for
            # a promoted mistake cluster - forcing llm_available() False here keeps this
            # bench's extraction-call budget exactly the ~200 the docstring promises,
            # regardless of extractor_mode, and steers guard proposals to the deterministic
            # fallback rather than silently billing an uncounted extra call per case. Also
            # covers --dry, which needs the REAL clustering/provenance pipeline to run (not a
            # bypass) to actually exercise the token-provenance gate this widening is for.
            saved_llm_available = m.llm_available
            m.llm_available = lambda: False
            try:
                promote_report = pr.promote(apply=True)
            finally:
                m.llm_available = saved_llm_available

            case_rows: dict[str, dict] = {}
            for arm in ARMS:
                row = run_case(case, arm)
                per_arm[arm].append(row)
                case_rows[arm] = row

            # Promotion-stage classification: a planted value that SURVIVED write (present in
            # the written principle) but did not reach the universal arm was caught somewhere
            # between write and the final universal note - by provenance, not principle_scan.
            universal_leak_by_class = case_rows["universal"]["leak_by_class"]
            for side in ("project_a", "project_c"):
                info = written.get(side)
                wp = ((info or {}).get("written_principle") or "").lower()
                if not wp:
                    continue          # write-rejected already, or nothing written at all
                for cls, val in (case[side]["planted"] or {}).items():
                    if val and val.lower() in wp and not universal_leak_by_class[cls]:
                        promotion_rejections[cls] = promotion_rejections.get(cls, 0) + 1

            diag_rows.append(_diagnostic_row(i, case, written, case_rows, promote_report))
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
           "write_rejections_by_class": write_rejections,
           "promotion_rejections_by_class": promotion_rejections,
           # kept for anyone still reading the old key name
           "scanner_rejections_by_class": write_rejections,
           # diagnostic instrumentation (2026-09-24): one entry per case - see _diagnostic_row.
           "rows": diag_rows}


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


def _select_case_ids(path: Path, spec: str) -> list[dict]:
    """A cheap diagnostic subset by case id, falling back to a 0-based index for a token that
    matches no id - `--case-ids 0,3,7,12,19` instead of `--cases N`'s "first N only", so a
    diagnostic run can target the cases a FULL sweep already flagged as interesting (a write
    rejection, an empty principle, whatever the first real run's numbers pointed at), rather
    than re-running from case 0 every time. Unknown ids/indices are reported and skipped, never
    silently dropped - a diagnostic run that quietly returned fewer rows than asked would be
    exactly the kind of "why is this empty" question this instrumentation exists to prevent."""
    all_cases = load_cases(path, None)
    by_id = {str(c.get("id")): c for c in all_cases}
    picked = []
    for tok in (s.strip() for s in spec.split(",")):
        if not tok:
            continue
        if tok in by_id:
            picked.append(by_id[tok])
            continue
        try:
            idx = int(tok)
        except ValueError:
            print(f"[cross_project_bench] --case-ids: no case id {tok!r}", file=sys.stderr)
            continue
        if 0 <= idx < len(all_cases):
            picked.append(all_cases[idx])
        else:
            print(f"[cross_project_bench] --case-ids: index {idx} out of range "
                 f"(0-{len(all_cases) - 1})", file=sys.stderr)
    return picked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA), help="dataset path (default: %(default)s)")
    parser.add_argument("--out", default=str(OUT), help="results artifact path (default: %(default)s)")
    parser.add_argument("--cases", type=int, default=None, help="limit to the first N cases")
    parser.add_argument("--case-ids", default="",
                        help="comma-separated case ids (or 0-based indices, for a token that "
                             "matches no id) - a cheap diagnostic run over exactly these cases "
                             "instead of the first N; takes precedence over --cases, ignored "
                             "under --dry (which always uses its own fixed 3-case fixture)")
    parser.add_argument("--oracle-principles", action="store_true",
                        help="control: write each case's pre-written ground-truth principle "
                             "directly, no extraction at all")
    parser.add_argument("--dry", action="store_true",
                        help=f"{_DRY_N_CASES} cases, a deterministic stub extractor (poisons "
                             "one planted identifier of each class, plus a second entity "
                             "scenario left undeclared) + stub embedder, no model - proves "
                             "principle_scan rejects at write time and principles.py's token-"
                             "provenance gate rejects at promotion time, end to end")
    args = parser.parse_args(argv)

    if args.dry:
        extractor_mode = "oracle" if args.oracle_principles else "stub"
    else:
        extractor_mode = "oracle" if args.oracle_principles else "extract"

    if args.case_ids and not args.dry:
        cases = _select_case_ids(Path(args.data), args.case_ids)
    else:
        n = _DRY_N_CASES if args.dry else args.cases
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
        print(f"  rejected at write by principle_scan (the scanner doing its job, NOT a "
             f"leak): {result['write_rejections_by_class']}")
        print(f"  rejected at promotion by provenance (principles.py, NOT a leak): "
             f"{result['promotion_rejections_by_class']}")
    for r in result["rows"]:
        a, c = r["project_a"], r["project_c"]
        print(f"  case {r['case_id']}: A items={a['n_extracted_items']} "
             f"principle={'y' if a['has_principle'] else 'n'}  "
             f"C items={c['n_extracted_items']} principle={'y' if c['has_principle'] else 'n'}  "
             f"cosine={r['principle_cosine']}  "
             f"promote candidates={r['promote_report']['candidates']} "
             f"clusters={r['promote_report']['clusters']} "
             f"promoted={r['promote_report']['promoted']}")

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
