#!/usr/bin/env python3
"""Render the published tables from `research/evidence_manifest.json`.

Every table below is data that already lives in the manifest, which in turn is checked
against the raw result files. Writing those numbers by hand is how a document ends up
quoting a run that no longer exists - which is exactly what the manifest found in five
places. So the tables are generated, and CI re-renders them and fails on any difference.

A generated region is delimited in the markdown by a pair of comments:

    <!-- claims:longmem-readme -->
    ...generated table...
    <!-- /claims:longmem-readme -->

Usage:

    python tools/render_claims.py             # check: fail if a region is stale
    python tools/render_claims.py --write     # regenerate the regions in place
    python tools/render_claims.py --footer longmem.hybrid.recall_at_5
                                              # one evidence line for a chart caption

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:                                   # a Windows console defaults to cp1251 here, and
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # footers carry `·`
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"

REGION = "<!-- claims:{id} -->"
REGION_END = "<!-- /claims:{id} -->"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


class Withdrawn(Exception):
    """A renderer asked for a number that is no longer published.

    Raised rather than returned so that withdrawal cannot be forgotten: any renderer that
    touches a stale claim aborts, and the region it was building is replaced by the notice
    below. Task B8 withdrew 118 of 133 claims at once; a design where publishing a withdrawn
    number requires only *forgetting* a check would not have survived that.
    """

    def __init__(self, claim_id: str, reason: str):
        super().__init__(f"{claim_id} is withdrawn: {reason}")
        self.claim_id = claim_id
        self.reason = reason


class Claims:
    """Lookup over the manifest, so a renderer names a claim id, never a number."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self._by_id = {c["id"]: c for c in manifest["claims"]}

    def value(self, claim_id: str):
        claim = self.get(claim_id)
        if claim.get("stale"):
            raise Withdrawn(claim_id, claim["stale"])
        return claim["value"]

    def is_withdrawn(self, claim_id: str) -> bool:
        return bool(self.get(claim_id).get("stale"))

    def has(self, claim_id: str) -> bool:
        return claim_id in self._by_id

    def get(self, claim_id: str) -> dict:
        try:
            return self._by_id[claim_id]
        except KeyError:
            raise KeyError(f"no claim {claim_id!r} in the manifest") from None


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _apply_bold(rows: list[list], header: list[str], places: int) -> str:
    """Format the numeric columns and bold the best value in each.

    Emphasis follows a rule the generator can state ("this column's best"), rather
    than being applied by hand - hand emphasis is how a table ends up highlighting a
    number that stopped being the best two runs ago.
    """
    formatted = [[r[0]] + [f"{v:.{places}f}" for v in r[1:]] for r in rows]
    for col in range(1, len(header)):
        best = max(row[col] for row in rows)
        for i, row in enumerate(rows):
            if row[col] == best:
                formatted[i][col] = f"**{formatted[i][col]}**"
    return _table(header, formatted)


# --------------------------------------------------------------- renderers

RETRIEVAL_ROWS = [
    ("semantic (bge-m3)", "semantic"),
    ("lexical (BM25)", "lexical"),
    ("**calibrated fusion (shipped default, 0 deps)**", "hybrid"),
    ("**+ trained cross-encoder (opt-in)**", "hybrid_xrerank"),
]


def _retrieval_table(c: Claims, places: int) -> str:
    rows = [[label,
             c.value(f"longmem.{slug}.recall_at_1"),
             c.value(f"longmem.{slug}.recall_at_5"),
             c.value(f"longmem.{slug}.recall_at_10"),
             round(c.value(f"longmem.{slug}.mrr"), places)]
            for label, slug in RETRIEVAL_ROWS]
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], places)


HEAD_TO_HEAD_ROWS = [("**Nevertwice (calibrated fusion)**", "nevertwice"),
                     ("Mem0", "mem0"), ("LangMem", "langmem"), ("A-MEM", "amem")]
H2H_HEADER = ["system", "R@1", "R@5", "R@10", "MRR"]

#: The products' own pipelines (LLM extraction included) as opposed to the store arms above,
#: which isolate the retrieval layer. Review 2026-09-05: a reader took the store rows for the
#: products, so the two kinds now sit in separate tables that say which they are.
PIPELINE_ROWS = [
    ("Mem0, full pipeline (`infer=True`: its LLM extraction, then its search)", "mem0_infer"),
    ("LangMem, full pipeline (`create_memory_store_manager`)", "langmem_full"),
    ("A-MEM, full pipeline (`agentic_memory`: LLM notes and link evolution)", "amem_full"),
]


def _h2h_rows(c: Claims, family: str, spec, required: bool = True) -> list[list]:
    """One row per arm of `family`. An arm without claims is left out - a blocked arm has no
    number in the register - and the caller names it under the table; `required` is kept
    for callers that want a partial family to raise."""
    rows = []
    for label, slug in spec:
        base = f"{family}.{slug}"
        if not c.has(f"{base}.recall_at_1"):
            if required:
                raise KeyError(f"no claim {base}.recall_at_1 in the manifest")
            continue
        rows.append([label,
                     c.value(f"{base}.recall_at_1"),
                     c.value(f"{base}.recall_at_5"),
                     c.value(f"{base}.recall_at_10"),
                     round(c.value(f"{base}.mrr"), 3)])
    return rows


def _not_yet(family: str, command: str) -> str | None:
    """A family that has never been registered renders as a dated gap, not a crash: the page
    can carry its region before the run. A family that is only partly registered is an
    inconsistency and is left to raise."""
    return (f"> **Not measured yet.** No `{family}.*` claim is registered; "
            f"`{command}` is the run that produces them.")


def _h2h_family_table(c: Claims, family: str, command: str) -> str:
    """A four-system table that tolerates a blocked arm: the arm has no claim, so it has no
    row, and the note under the table names it - the artifact carries the blocker."""
    if not any(c.has(f"{family}.{slug}.recall_at_1") for _, slug in HEAD_TO_HEAD_ROWS):
        return _not_yet(family, command)
    out = _apply_bold(_h2h_rows(c, family, HEAD_TO_HEAD_ROWS, required=False), H2H_HEADER, 3)
    missing = [label.strip("*") for label, slug in HEAD_TO_HEAD_ROWS
               if not c.has(f"{family}.{slug}.recall_at_1")]
    if missing:
        out += ("\n\n<sub>No row for " + ", ".join(missing) + ": the arm has no number this "
                "stand would publish - a blocker it recorded, or a run the prose above rejects "
                "as measuring the stand rather than the product.</sub>")
    return out


def render_head_to_head_locomo(c: Claims) -> str:
    """Four systems on LoCoMo pooled globally - one store, all ten conversations."""
    return _h2h_family_table(c, "h2h_locomo", "python research/head_to_head.py --data=locomo "
                             "--only=nevertwice,mem0,langmem,amem --save "
                             "--out=research/results/head_to_head_locomo.json")


def render_head_to_head_s(c: Claims) -> str:
    """The same four systems on the non-oracle LongMemEval pool."""
    return _h2h_family_table(c, "h2h_s", "python research/head_to_head.py --data=s "
                             "--only=nevertwice,mem0,langmem,amem --save "
                             "--out=research/results/head_to_head_s.json")


def render_head_to_head_full(c: Claims) -> str:
    """Our shipped ranker beside the products' full pipelines on the oracle pool.

    A pipeline arm that blocked itself - more than a tenth of its LLM calls failed silently,
    or the package would not run - has no claim, so it has no row; the note under the table
    names it rather than letting the table read as if it had never been tried.
    """
    rows = (_h2h_rows(c, "h2h_pinned", HEAD_TO_HEAD_ROWS[:1])
            + _h2h_rows(c, "h2h_pinned", PIPELINE_ROWS, required=False))
    out = _apply_bold(rows, H2H_HEADER, 3)
    missing = [label.split(",")[0] for label, slug in PIPELINE_ROWS
               if not c.has(f"h2h_pinned.{slug}.recall_at_1")]
    if missing:
        out += ("\n\n<sub>No row for " + ", ".join(missing) + ": the arm has no number this "
                "stand would publish - a blocker it recorded, or a run the prose above rejects "
                "as measuring the stand rather than the product.</sub>")
    return out


def render_longmem_benchmarks(c: Claims) -> str:
    return _retrieval_table(c, 3)


def render_longmem_pinned(c: Claims) -> str:
    """The same table from the 2026-09 re-run on the hash-pinned corpus.

    A separate family from `longmem.*`, deliberately. The withdrawn claims describe a file
    nobody can identify; these describe one whose sha256 is in `research/corpus_pin.py`. Merging
    them would launder the provenance of the first set through the second.
    """
    rows = [[label,
             c.value(f"longmem_pinned.{slug}.recall_at_1"),
             c.value(f"longmem_pinned.{slug}.recall_at_5"),
             c.value(f"longmem_pinned.{slug}.recall_at_10"),
             round(c.value(f"longmem_pinned.{slug}.mrr"), 3)]
            for label, slug in RETRIEVAL_ROWS]
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], 3)


def render_longmem_s(c: Claims) -> str:
    """The same methods on the non-oracle pool: twenty-one times the haystack, same questions."""
    rows = [[label,
             c.value(f"longmem_s.{slug}.recall_at_1"),
             c.value(f"longmem_s.{slug}.recall_at_5"),
             c.value(f"longmem_s.{slug}.recall_at_10"),
             round(c.value(f"longmem_s.{slug}.mrr"), 3)]
            for label, slug in RETRIEVAL_ROWS if slug in ("semantic", "lexical", "hybrid")]
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], 3)


def render_locomo(c: Claims) -> str:
    """LoCoMo retrieval. A different quantity from the LoCoMo accuracy figures vendors publish,
    and the page around this table says so in as many words."""
    rows = [[label,
             c.value(f"locomo.{slug}.recall_at_1"),
             c.value(f"locomo.{slug}.recall_at_5"),
             c.value(f"locomo.{slug}.recall_at_10"),
             round(c.value(f"locomo.{slug}.mrr"), 3)]
            for label, slug in RETRIEVAL_ROWS if slug in ("semantic", "lexical", "hybrid")]
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], 3)


def render_head_to_head_pinned(c: Claims) -> str:
    rows = [[label,
             c.value(f"h2h_pinned.{slug}.recall_at_1"),
             c.value(f"h2h_pinned.{slug}.recall_at_5"),
             c.value(f"h2h_pinned.{slug}.recall_at_10"),
             round(c.value(f"h2h_pinned.{slug}.mrr"), 3)]
            for label, slug in HEAD_TO_HEAD_ROWS]
    return _apply_bold(rows, ["system", "R@1", "R@5", "R@10", "MRR"], 3)


def render_head_to_head(c: Claims) -> str:
    """Every row comes from the one head-to-head run, never spliced across runs -
    that is the only thing that makes 'the same stand' mean anything."""
    rows = [[label,
             c.value(f"head_to_head.{slug}.recall_at_1"),
             c.value(f"head_to_head.{slug}.recall_at_5")]
            for label, slug in HEAD_TO_HEAD_ROWS]
    return _apply_bold(rows, ["system", "R@1", "R@5"], places=3)


LATENCY_ROWS = [
    ("PreToolUse end-to-end", "latency.pretooluse_end_to_end",
     "every tool call (interpreter start included)"),
    ("UserPromptSubmit end-to-end", "latency.userpromptsubmit",
     "per prompt (task-aware recall)"),
    ("SessionStart end-to-end, idle", "latency.sessionstart",
     "per session start with no backlog"),
    ("cold import of the engine", "latency.cold",
     "once per hook process (inside the numbers above)"),
    ("`guards.check()` over a seeded ledger", "latency.guards_check",
     "the actual guard match, pure regex"),
    ("lexical recall, no embedder", "latency.lexical_recall_floor",
     "the zero-model floor recall falls back to"),
]


def render_latency(c: Claims) -> str:
    """The one mixed region: four timings survived re-measurement at HEAD, two did not.

    A partly-withdrawn table is rendered as the rows that still hold plus a line naming the
    rows that left, because a table that silently loses two rows reads as a table that never
    had them.
    """
    rows, gone = [], []
    for label, claim_id, when in LATENCY_ROWS:
        if c.is_withdrawn(claim_id):
            gone.append((label, c.get(claim_id)["stale"]))
            continue
        text = f"{c.value(claim_id):g} ms"
        rows.append([label, f"**{text}**" if claim_id.endswith("pretooluse_end_to_end")
                     else text, when])
    out = _table(["hot path", "cost", "when it is paid"], rows)
    by_reason: dict[str, list[str]] = {}
    for label, reason in gone:
        by_reason.setdefault(reason, []).append(label)
    for reason, labels in by_reason.items():
        out += f"\n\n<sub>**Withdrawn** - {', '.join(labels)}: {reason}</sub>"
    return out


TASK_A_ROWS = [("semantic (bge-m3)", "semantic"), ("lexical", "lexical"),
               ("hybrid (RRF)", "hybrid_rrf")]


def render_task_a(c: Claims) -> str:
    rows = [[label,
             c.value(f"task_a.{slug}.recall_at_1"),
             c.value(f"task_a.{slug}.recall_at_3"),
             c.value(f"task_a.{slug}.recall_at_5"),
             c.value(f"task_a.{slug}.mrr")]
            for label, slug in TASK_A_ROWS]
    return _apply_bold(rows, ["method", "R@1", "R@3", "R@5", "MRR"], places=3)


def render_task_b(c: Claims) -> str:
    return _table(["", "accuracy"], [
        ["bi-temporal graph", f"**{c.value('task_b.bitemporal_accuracy'):.3f}**"],
        ['flat "use newest"', f"{c.value('task_b.flat_newest_accuracy'):.3f}"],
    ])


def render_task_c(c: Claims) -> str:
    ratios = {p: c.value(f"task_c.{p}.ratio")
              for p in ("project_alpha", "project_beta", "project_delta")}
    best = max(ratios.values())
    rows = []
    for project, ratio in ratios.items():
        text = f"{ratio:g}×"
        rows.append([project,
                     str(c.value(f"task_c.{project}.card")),
                     str(c.value(f"task_c.{project}.full_context")),
                     f"**{text}**" if ratio == best else text])
    return _table(["project", "card", "full Context", "ratio"], rows)


def render_token_ab_raw(c: Claims) -> str:
    rows = []
    for k in (3, 5, 10):
        rows.append([
            str(k),
            f"{c.value(f'token_ab.raw.k{k}.recall_at_k'):.3f}",
            f"{c.value(f'token_ab.raw.k{k}.mean_topk_tok'):,}",
            f"**{c.value(f'token_ab.raw.k{k}.net_vs_curated_haystack_tok'):+,}**",
            f"{c.value(f'token_ab.raw.k{k}.net_vs_full_history_tok'):+,}",
        ])
    return _table(["k", "recall@k", "top-k cost (tok)",
                   "net vs a curated small haystack", "net vs the full history"], rows)


def render_token_ab_distill(c: Claims) -> str:
    rows = []
    for k in (3, 5, 10):
        rows.append([
            str(k),
            f"{c.value(f'token_ab.distill.k{k}.recall_at_k'):.3f}",
            f"{c.value(f'token_ab.distill.k{k}.raw_topk_tok'):,}",
            f"**{c.value(f'token_ab.distill.k{k}.distilled_topk_tok'):,}**",
            f"{c.value(f'token_ab.distill.k{k}.net_raw_vs_curated'):+,}",
            f"**{c.value(f'token_ab.distill.k{k}.net_distilled_vs_curated'):+,}**",
        ])
    return _table(["k", "recall@k", "raw top-k (tok)", "**distilled top-k (tok)**",
                   "net raw vs curated", "**net distilled vs curated**"], rows)


def render_token_ab_live(c: Claims) -> str:
    return _table(["arm", "mean input tokens", "answer-match (crude)"], [
        ["no memory (full haystack)",
         f"**{c.value('token_ab.live_two_arm.mean_prompt_tok_no_memory'):,}**",
         f"{c.value('token_ab.live_two_arm.answer_match_no_memory'):.3f}"],
        ["with memory (top-3 distilled)",
         f"**{c.value('token_ab.live_two_arm.mean_prompt_tok_with_memory'):,}**",
         f"{c.value('token_ab.live_two_arm.answer_match_with_memory'):.3f}"],
    ])


VERDICT_MARK = {"beats": "**beats**", "ties": "ties", "loses_to": "**LOSES**",
                "not_compared": "not compared", "not_applicable": "n/a"}


def render_baselines_conditions(c: Claims) -> str:
    policy = c.manifest["baseline_policy"]
    lines = [policy["rule"], ""]
    lines += [f"{i + 1}. {cond}" for i, cond in
              enumerate(policy["matched_conditions"])]
    lines += ["", policy["evidence_rule"]]
    return "\n".join(lines)


def render_baselines_registry(c: Claims) -> str:
    rows = []
    for bid, b in c.manifest["baselines"].items():
        rows.append([f"`{bid}`", b["name"] + (" *(additional)*" if b.get("additional")
                                              else ""),
                     b["definition"], b["why"], b["how"]])
    return _table(["id", "baseline", "what it is", "why it is the test", "how to run it"],
                  rows)


def _headline_claims(c: Claims) -> list[dict]:
    """Headline claims that are still published.

    A withdrawn headline has no baseline verdicts worth tabulating - the result it was
    compared against no longer stands - so it leaves the matrix with the number it carried.
    """
    return [x for x in c.manifest["claims"] if x.get("headline") and not x.get("stale")]


def render_baselines_matrix(c: Claims) -> str:
    baselines = list(c.manifest["baselines"])
    rows = []
    for claim in _headline_claims(c):
        verdicts = claim["baseline_verdicts"]
        rows.append([f"`{claim['id']}`"] +
                    [VERDICT_MARK[verdicts[b]["verdict"]] if b in verdicts else "**missing**"
                     for b in baselines])
    return _table(["headline claim"] + [f"`{b}`" for b in baselines], rows)


def render_baselines_summary(c: Claims) -> str:
    counts: dict[str, int] = {}
    for claim in _headline_claims(c):
        for entry in claim["baseline_verdicts"].values():
            counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    order = ("beats", "ties", "loses_to", "not_compared", "not_applicable")
    total = sum(counts.values())
    rows = [[f"`{v}`", str(counts.get(v, 0)),
             c.manifest["baseline_policy"]["verdicts"][v]] for v in order]
    head = _table(["verdict", "count", "what it means"], rows)
    return (f"{len(_headline_claims(c))} headline claims x "
            f"{len(c.manifest['baselines'])} baselines = {total} pairs.\n\n{head}")


MORPHOLOGY_METHODS = [("semantic (bge-m3)", "semantic"), ("lexical (BM25)", "lexical"),
                      ("**calibrated fusion (shipped)**", "hybrid")]


def _morphology_pair_table(c: Claims, raw_family: str, morph_family: str) -> str:
    """Each method twice: on raw tokens (the ablation family) and with stop words + stems (the
    live family), the best value of each column in bold across both. A family missing a method
    is an error - both arms are measured by the same stand on the same run."""
    if not c.has(f"{raw_family}.lexical.recall_at_1"):
        return _not_yet(raw_family, "the --no-morphology run of the same stand (research/LEXICAL_MORPHOLOGY.md, Reproducing)")
    rows = []
    for label, slug in MORPHOLOGY_METHODS:
        for tokens, fam in (("raw tokens", raw_family), ("stop words + stems", morph_family)):
            rows.append([f"{label}, {tokens}",
                         c.value(f"{fam}.{slug}.recall_at_1"),
                         c.value(f"{fam}.{slug}.recall_at_5"),
                         c.value(f"{fam}.{slug}.recall_at_10"),
                         round(c.value(f"{fam}.{slug}.mrr"), 3)])
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], 3)


def render_lexical_morphology_locomo(c: Claims) -> str:
    """LoCoMo per conversation: the ablation family `locomo_raw` beside the live `locomo`."""
    return _morphology_pair_table(c, "locomo_raw", "locomo")


def render_lexical_morphology_oracle(c: Claims) -> str:
    """LongMemEval-oracle: `longmem_raw` beside the live `longmem_pinned`."""
    return _morphology_pair_table(c, "longmem_raw", "longmem_pinned")


def render_lexical_morphology_s(c: Claims) -> str:
    """The non-oracle pool: `longmem_s_raw` beside the live `longmem_s` - the pool that was
    outside the gate and moved the other way."""
    return _morphology_pair_table(c, "longmem_s_raw", "longmem_s")


def render_lexical_morphology_vault(c: Claims) -> str:
    """The owner's store, session summary -> the notes extracted from it, by language half.
    Lexical only (no embedder); the rows come from `research/lexical_morphology_probe.py`."""
    if not c.has("morphology.vault.session.ru.raw.recall_at_1"):
        return _not_yet("morphology.vault.session", "NEVERTWICE_VAULT=<store> python research/lexical_morphology_probe.py --protocol both --out research/results/lexical_morphology_vault.json")
    rows = []
    for lang, name in (("ru", "Russian half"), ("en", "English half")):
        for tokens, arm in (("raw tokens", "raw"), ("stop words + stems", "morph")):
            base = f"morphology.vault.session.{lang}.{arm}"
            rows.append([f"{name}, {tokens}",
                         c.value(f"{base}.recall_at_1"),
                         c.value(f"{base}.recall_at_5"),
                         c.value(f"{base}.recall_at_10"),
                         round(c.value(f"{base}.mrr"), 3)])
    return _apply_bold(rows, ["half", "R@1", "R@5", "R@10", "MRR"], 3)


SWEEP_WEIGHTS = ("0.25", "0.5", "0.75", "1.0", "1.5")


def render_fusion_sweep(c: Claims) -> str:
    """The dense-weight sweep on both pinned corpora: R@1 / R@5 / MRR per weight, the shipped
    weight marked, the best value per column in bold."""
    if not c.has("fusion_sweep.shipped_weight"):
        return _not_yet("fusion_sweep", "python research/fusion_sweep.py --save")
    shipped = str(c.value("fusion_sweep.shipped_weight"))
    rows = []
    for w in SWEEP_WEIGHTS:
        key = w.replace(".", "_")
        label = f"**{w} (shipped)**" if w == shipped or float(w) == float(shipped) else w
        rows.append([label,
                     c.value(f"fusion_sweep.oracle.w{key}.recall_at_1"),
                     c.value(f"fusion_sweep.oracle.w{key}.recall_at_5"),
                     round(c.value(f"fusion_sweep.oracle.w{key}.mrr"), 3),
                     c.value(f"fusion_sweep.locomo.w{key}.recall_at_1"),
                     c.value(f"fusion_sweep.locomo.w{key}.recall_at_5"),
                     round(c.value(f"fusion_sweep.locomo.w{key}.mrr"), 3)])
    return _apply_bold(rows, ["dense weight", "oracle R@1", "oracle R@5", "oracle MRR",
                              "LoCoMo R@1", "LoCoMo R@5", "LoCoMo MRR"], 3)


RENDERERS = {
    "longmem-benchmarks": render_longmem_benchmarks,
    "longmem-pinned": render_longmem_pinned,
    "longmem-s": render_longmem_s,
    "locomo": render_locomo,
    "head-to-head-pinned": render_head_to_head_pinned,
    "head-to-head-locomo": render_head_to_head_locomo,
    "head-to-head-s": render_head_to_head_s,
    "head-to-head-full": render_head_to_head_full,
    "lexical-morphology-locomo": render_lexical_morphology_locomo,
    "lexical-morphology-oracle": render_lexical_morphology_oracle,
    "lexical-morphology-vault": render_lexical_morphology_vault,
    "lexical-morphology-s": render_lexical_morphology_s,
    "fusion-sweep": render_fusion_sweep,
    "head-to-head": render_head_to_head,
    "latency": render_latency,
    "task-a": render_task_a,
    "task-b": render_task_b,
    "task-c": render_task_c,
    "token-ab-raw": render_token_ab_raw,
    "token-ab-distill": render_token_ab_distill,
    "token-ab-live": render_token_ab_live,
    "baselines-conditions": render_baselines_conditions,
    "baselines-registry": render_baselines_registry,
    "baselines-matrix": render_baselines_matrix,
    "baselines-summary": render_baselines_summary,
}


# ------------------------------------------------------------------ footers

def footer(c: Claims, claim_id: str) -> str:
    """One evidence line for a chart caption: n, dataset, model, commit, command.

    Task C5 puts one of these under every published figure; the renderer lives here
    so a caption cannot say something the manifest does not.
    """
    claim = c.get(claim_id)
    manifest = load_manifest()
    bits = []
    if claim["n"]:
        bits.append(f"n={claim['n']}")
    if claim["dataset"]:
        bits.append(manifest["datasets"][claim["dataset"]]["name"])
    bits.append(claim["unit"])
    env = manifest["environments"].get(claim["environment"] or "", {})
    for key in ("reader", "embedder"):
        if env.get(key):
            bits.append(f"{key}: {env[key]}")
    if claim["ci"]:
        bits.append(f"95% CI {claim['ci']['low']:.3f}-{claim['ci']['high']:.3f}")
    bits.append(f"commit {claim['commit'][:7]}" if claim["commit"]
                else "commit unrecorded")
    bits.append(f"`{claim['command']}`")
    if claim.get("stale"):
        bits.append(f"**withdrawn** - {claim['stale']}")
    return " · ".join(bits)


# ------------------------------------------------------------- region logic

def region_span(text: str, region_id: str) -> tuple[int, int] | None:
    start = text.find(REGION.format(id=region_id))
    if start < 0:
        return None
    end = text.find(REGION_END.format(id=region_id), start)
    if end < 0:
        raise ValueError(f"region {region_id!r} is opened but never closed")
    return start, end


def withdrawal_notice(c: Claims, claim_id: str, reason: str) -> str:
    """What stands where a table used to be.

    It names why the number cannot be re-measured here and the one command that lists the
    whole withdrawn set - so a reader meets an explanation rather than a gap, and the debt
    stays countable instead of quietly disappearing.
    """
    # Month precision on purpose. A full ISO date puts a bare day-of-month into a governed
    # document, and the day would then need its own non-metric exemption - a hole in the
    # coverage check bought for nothing. The exact date stays on each claim's `withdrawn_on`.
    # The month comes from the claim itself when it has one: the manifest-level date is the
    # 2026-08 withdrawal, and a claim withdrawn later must not be stamped with it.
    date = (str(c.get(claim_id).get("withdrawn_on") or "")
            or c.manifest.get("withdrawal", {}).get("date", ""))[:7]
    return (f"> **Withdrawn{' ' + date if date else ''}.** {reason}\n"
            ">\n"
            "> The claim is kept in `research/evidence_manifest.json` marked `stale`, with "
            "the command that would restore it. `python tools/check_freshness.py "
            "--list-stale` prints every withdrawn number and why; "
            f"`{c.get(claim_id)['command']}` is what re-measures this one.")


def apply_regions(text: str, c: Claims) -> tuple[str, list[str]]:
    """Return (rendered text, ids of regions whose content changed)."""
    changed = []
    for region_id, renderer in RENDERERS.items():
        span = region_span(text, region_id)
        if span is None:
            continue
        start, end = span
        open_tag = REGION.format(id=region_id)
        body_start = start + len(open_tag)
        current = text[body_start:end]
        try:
            body = renderer(c)
        except Withdrawn as w:
            body = withdrawal_notice(c, w.claim_id, w.reason)
        fresh = "\n" + body + "\n"
        if current != fresh:
            changed.append(region_id)
            text = text[:body_start] + fresh + text[end:]
    return text, changed


def docs_with_regions(manifest: dict) -> list[Path]:
    """The governed documents, plus any registered document that carries a region.

    Study pages are backlog, not governed, and their tables used to be typed by hand from the
    same artifacts the governed tables are generated from - two copies of one number, one of
    them unchecked. A region on a study page is rendered from the register like any other,
    and its figures do not count against the page's budget, because they are not unregistered.
    """
    docs = [ROOT / d for d in manifest["scope"]["docs"]]
    for d in manifest.get("documents", {}):
        path = ROOT / d
        if path in docs or not path.is_file():
            continue
        if REGION.split("{")[0] in path.read_text(encoding="utf-8", errors="replace"):
            docs.append(path)
    return docs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render published tables from the evidence manifest.")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the regions in place instead of checking")
    ap.add_argument("--footer", metavar="CLAIM_ID",
                    help="print one evidence footer line and exit")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    claims = Claims(manifest)

    if args.footer:
        print(footer(claims, args.footer))
        return 0

    stale: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for doc in docs_with_regions(manifest):
        text = doc.read_text(encoding="utf-8")
        rendered, changed = apply_regions(text, claims)
        for region_id in RENDERERS:
            if region_span(text, region_id):
                seen.add(region_id)
        if changed:
            stale += [f"{doc.relative_to(ROOT).as_posix()}:{r}" for r in changed]
            if args.write:
                doc.write_text(rendered, encoding="utf-8", newline="")

    missing = sorted(set(RENDERERS) - seen)

    if args.write:
        print(f"rewrote {len(stale)} region(s)" if stale else "all regions were current")
    else:
        for s in stale:
            print(f"  STALE: {s} does not match the manifest")
    for m in missing:
        print(f"  ERROR: renderer {m!r} has no region in any document in scope")

    if missing:
        return 1
    if stale and not args.write:
        print("run `python tools/render_claims.py --write` to regenerate")
        return 1
    if not args.write:
        print(f"all {len(seen)} generated regions match the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
