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
#: The manifest reader, the withdrawal exception and the figure footer live in
#: `tools/claims_footer.py` and are re-exported here, because every caller in this repository
#: already says `render_claims.Claims` and `render_claims.footer`.
#:
#: They were moved out for `research/_figstyle.py`. A figure needs `footer()`, and importing
#: this module to get it put all 1,200 lines of page renderers into the produced_by closure of
#: every command that saves a chart - so adding a renderer for an unrelated region marked those
#: claims stale. `forgetting.coverage_gain_at_20pct` was re-stamped by hand seven times for
#: that reason. The dependency now points at the stable half only.
from claims_footer import (  # noqa: F401 - re-exported for callers that name this module
    MANIFEST_PATH,
    Claims,
    Withdrawn,
    footer,
    load_manifest,
)

#: The markers a generated region is fenced with. They stay here: they are the page format, not
#: the manifest's shape, and a figure has no use for them.
REGION = "<!-- claims:{id} -->"
REGION_END = "<!-- /claims:{id} -->"


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
             c.value(f"locomo.{slug}.recall_at_3"),
             c.value(f"locomo.{slug}.recall_at_5"),
             c.value(f"locomo.{slug}.recall_at_10"),
             round(c.value(f"locomo.{slug}.mrr"), 3)]
            for label, slug in RETRIEVAL_ROWS if slug in ("semantic", "lexical", "hybrid")]
    return _apply_bold(rows, ["method", "R@1", "R@3", "R@5", "R@10", "MRR"], 3)


def render_locomo_categories(c: Claims) -> str:
    """Fused R@5 per LoCoMo question category. `research/LOCOMO.md` carried this line by hand
    and it drifted a whole engine revision behind the table above it (2026-09-11)."""
    if not c.has("locomo.hybrid.by_category.1.recall_at_5"):
        return _not_yet("locomo", "python research/locomo_eval.py --save")
    cells = [f"{c.value(f'locomo.hybrid.by_category.{k}.recall_at_5'):.3f}" for k in "12345"]
    return _table(["fused R@5, category 1", "2", "3", "4", "5"], [cells])


ABSTENTION_THRESHOLDS = ("0.0", "0.1", "0.2", "0.35", "0.5", "0.75")


def render_abstention_sweep(c: Claims) -> str:
    """The recall-abstention sweep (ledger C1) from its artifact's claims. The page carried this
    table by hand and stayed one campaign behind the artifact it said it was read from."""
    shipped = c.value("abstention.recall.shipped_threshold") if c.has("abstention.recall.shipped_threshold") else None
    rows = []
    for t in ABSTENTION_THRESHOLDS:
        base = f"abstention.recall.sweep.t{t.replace('.', '_')}"
        if not c.has(f"{base}.mean_chars"):
            continue
        label = f"{float(t):.2f}" + (" (off)" if float(t) == 0 else "")
        if shipped is not None and abs(float(t) - shipped) < 1e-9:
            label = f"**{label} (shipped)**"
        saved = c.value(f"{base}.char_reduction")
        lost = -c.value(f"{base}.current_delta")
        rows.append([label, f"{c.value(f'{base}.mean_chars'):.1f}", f"{c.value(f'{base}.mean_hits'):.2f}",
                     f"{c.value(f'{base}.current_rate'):.3f}",
                     "-" if float(t) == 0 else f"{saved * 100:.1f}%",
                     "-" if float(t) == 0 else f"{(0.0 if abs(lost) < 5e-4 else lost) * 100:.1f} pts"])
    if not rows:
        return _not_yet("abstention", "python research/abstention_ab.py --save")
    return _table(["threshold", "chars/query", "hits", "wanted fact returned", "chars saved", "recall lost"], rows)


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


SUPERSESSION_ARMS = (("**Nevertwice**", "nevertwice"), ("Mem0", "mem0"),
                     ("Zep/Graphiti (`graphiti-core`, FalkorDB)", "zep"),
                     ("an append-only markdown file", "naive"))

#: K8 reads each store twice - once after the replacing session, which is what a user sees between
#: nights, and once after the weekly consolidation has judged the contested pairs. Neither reading
#: is the product on its own, so the tables carry both rather than choosing.
SUPERSESSION_READINGS = (("**Nevertwice**, between nights", "nevertwice"),
                         ("**Nevertwice**, after consolidation", "nevertwice_after_sleep"))


def _cell(c: Claims, cid: str) -> str:
    """A three-decimal figure, or a dash when that arm has no claim yet."""
    return f"{c.value(cid):.3f}" if c.has(cid) else "-"


def _with_ci(c: Claims, cid: str) -> str:
    """`0.058 [0.029, 0.116]` - the interval is not decoration: three of these rates come from
    sixty cases, and a bare figure invites a comparison the sample does not support."""
    v = c.value(cid)
    ci = c.get(cid).get("ci") or {}
    if "low" not in ci:
        return f"{v:.3f}"
    return f"{v:.3f} [{ci['low']:.3f}, {ci['high']:.3f}]"


def render_supersession_pinned(c: Claims) -> str:
    """The three-sided table: the retracted fact, the fact that replaced it, and a still-true
    fact that did not come back. All three are needed - a memory that returned nothing would
    score perfectly on the first column alone.

    The third column is the *control miss rate*: on a control case the still-true fact was not
    returned, for any reason. It is the one figure every arm can be measured on, so it is the
    comparative column. Until 2026-09-11 this table printed our narrow figure - the memory
    itself retired the fact - beside the other arms' broad one under the single name
    "over-retraction"; the rows measured different things. The split by cause is the table
    `render_supersession_causes` draws underneath."""
    if not c.has("supersession.nevertwice.stale_rate"):
        return _not_yet("supersession",
                        "python research/supersession_bench.py --arms nevertwice,naive")
    arms = list(SUPERSESSION_READINGS) + [a for a in SUPERSESSION_ARMS if a[1] != "nevertwice"]
    rows = [[label,
             _with_ci(c, f"supersession.{slug}.stale_rate"),
             _with_ci(c, f"supersession.{slug}.current_rate"),
             _with_ci(c, f"supersession.{slug}.control_miss_rate")]
            for label, slug in arms if c.has(f"supersession.{slug}.stale_rate")]
    out = _table(["arm", "returns the retracted fact", "returns the replacement",
                  "a still-true fact did not come back"], rows)
    if c.has("supersession.nevertwice_after_sleep.stale_rate"):
        out += ("\n\n<sub>Two rows for one system, because there are two moments. A contested pair - "
                "two notes on one topic where no rule proved a replacement - is served whole until the "
                "weekly consolidation judges it, so the first row is what a user sees between nights "
                "and the second is the same store after that judgement. The competitors have one row: "
                "nothing in them waits for a night.</sub>")
    return out


CAUSES = (("retired", "retired by the memory"), ("demoted", "absorbed into another note"),
          ("never_written", "never written"), ("unranked", "served, below the top five"))


def render_supersession_causes(c: Claims, fam: str = "supersession") -> str:
    """Why a still-true fact did not come back, per arm: the memory retired it (over-retraction
    proper - the only cause that is the design's own failure), the write path never stored it,
    or it was stored and ranked below k. The counts are over the control case-runs the arm ran
    (two runs for ours and for Zep/Graphiti, one for Mem0 and the floor). A cause the stand
    could not read for an arm prints as such rather than as a zero: reading it needs the arm's
    store, which every arm's run has opened since the 2026-09-11 campaign; the cell stays for a
    run that did not."""
    if not c.has(f"{fam}.nevertwice.control_miss_rate"):
        return _not_yet(fam, "python research/supersession_bench.py --arms nevertwice,naive")
    rows = []
    arms = list(SUPERSESSION_READINGS) + [a for a in SUPERSESSION_ARMS if a[1] != "nevertwice"]
    for label, slug in arms:
        if not c.has(f"{fam}.{slug}.control_miss_rate"):
            continue
        row = [label, _with_ci(c, f"{fam}.{slug}.control_miss_rate")]
        for key, _ in CAUSES:
            cid = f"{fam}.{slug}.control_miss.{key}"
            row.append(f"{int(c.value(cid))} of {c.get(cid)['n']}" if c.has(cid) else "not read")
        rows.append(row)
    out = _table(["arm", "a still-true fact did not come back"] + [name for _, name in CAUSES], rows)
    out += ("\n\n<sub>The first column is the rate in the table above; the four after it split its "
            "count by cause. The first two are the memory being too eager - it retired the note, or "
            "it judged a different fact a twin of this one and absorbed that fact into the note: the "
            "note stays on disk, but what it now hands back is the other fact, and this one is no "
            "longer served - the other two are "
            "the extractor's silence and the ranker's depth. A cause reads *not read* where the run "
            "did not inspect that arm's store: a retirement is visible only where the store records "
            "one (our `valid_to` and `## Previous statement`; Graphiti's `invalid_at`/`expired_at`; "
            "Mem0's delete and update events).</sub>")
    if c.has(f"{fam}.nevertwice.over_retraction_rate"):
        out += (f"\n\n<sub>Over-retraction proper - the memory stopped serving a fact that was "
                f"still true, by retiring the note or by absorbing another fact into it - is the "
                f"first two cause columns as a rate: "
                f"{_with_ci(c, f'{fam}.nevertwice.over_retraction_rate')} for Nevertwice over its "
                f"control case-runs.</sub>")
    return out


def render_supersession_readings(c: Claims, fam: str = "supersession") -> str:
    """The two readings of one store, in full, with the price of the second beside it.

    The comparative table above prints the three rates every arm can be measured on. This one is
    about us alone and carries the two columns a competitor has no analogue for: `old value served`,
    the retracted value appearing *anywhere* in what came back - attached to the newer statement or
    not, which is the honest reading of "both facts, newest first" - and what the sleep-time judge
    spent to get from the first row to the second."""
    if not c.has(f"{fam}.nevertwice.stale_rate"):
        return _not_yet(fam, "python research/supersession_bench.py --arms nevertwice --sleep")
    header = ["reading", "returns the retracted fact", "the retracted value appears anywhere",
              "returns the replacement", "retires a still-true fact", "characters per query"]
    rows = []
    for label, slug in SUPERSESSION_READINGS:
        if not c.has(f"{fam}.{slug}.stale_rate"):
            continue
        rows.append([label.replace("**Nevertwice**, ", "").capitalize(),
                     _with_ci(c, f"{fam}.{slug}.stale_rate"),
                     _cell(c, f"{fam}.{slug}.old_value_served_rate"),
                     _with_ci(c, f"{fam}.{slug}.current_rate"),
                     _with_ci(c, f"{fam}.{slug}.over_retraction_rate"),
                     f"{c.value(f'{fam}.{slug}.chars_per_query'):.0f}"
                     if c.has(f"{fam}.{slug}.chars_per_query") else "-"])
    if len(rows) < 2:
        return _not_yet(f"{fam}.nevertwice_after_sleep",
                        "python research/supersession_bench.py --arms nevertwice --sleep")
    out = _table(header, rows)
    out += ("\n\n<sub>The second column counts an item that asserts the retracted value without the "
            "current one, which is the bench's rule and the one the comparison uses. The third counts "
            "the retracted value wherever it appears in the returned text, so a paired hit - the newest "
            "statement with the earlier one attached - is counted here and not there. Both are printed "
            "because the design serves the older statement on purpose rather than hiding it, and a "
            "reader deciding whether that is acceptable needs the number it costs.</sub>")
    if c.has("absorb_judge.tokens_per_pair") and c.has("vault.contested_per_week"):
        out += (f"\n\n<sub>What the second row costs: one model call per contested pair at consolidation "
                f"and none in the hook, at {c.value('absorb_judge.tokens_per_pair'):.0f} tokens a pair; on "
                f"a real store the dry run counted at most "
                f"{c.value('vault.contested_per_week'):.0f} contested pairs a week, against a run budget of "
                f"a hundred thousand tokens. With no model backend at all the first row is what the "
                f"product does, and the contested pairs stay visible through `conflicts()` and "
                f"`integrity()`.</sub>")
    if c.has("absorb_judge.accuracy") and c.has("absorb_judge.replaces_precision"):
        out += (f"\n\n<sub>Why the judge's verdict is not the last word. On a recorded set of pairs whose "
                f"truth is known it rules correctly {c.value('absorb_judge.accuracy'):.3f} of the time, and "
                f"its precision on `replaces` - the verdict that retires a note - is "
                f"{c.value('absorb_judge.replaces_precision'):.3f}, not one. An earlier draw of the same "
                f"prompt over the same bytes read exactly one, so that figure is a draw and not a property "
                f"of the judge. This is why three guards sit between a verdict and a retirement: a note "
                f"with verified literals is never retired for one without, a value the new statement's own "
                f"verified block does not carry vetoes the replacement, and a replacement naming no value "
                f"does not displace one that does. The column above reads zero with the judge making false "
                f"calls, which is the guards doing the work.</sub>")
    return out


def render_supersession_switch(c: Claims) -> str:
    """The one loss the default leaves standing, and what removing it costs.

    `NEVERTWICE_EXPLICIT_RETIRE` decides what happens when the extractor writes `contradicts: <another
    note's title>`: `write` retires that note at once, on the extractor's word; `judge` sends the pair
    to the sleep-time judge instead. The campaign ran both, on both corpora, in both readings. The
    default is whichever the rule written before the run selected, and this table is the other arm -
    because a switch whose price is not printed is a switch nobody can decide about."""
    if not c.has("supersession_switch.nevertwice.stale_rate"):
        return _not_yet("supersession_switch",
                        "NEVERTWICE_EXPLICIT_RETIRE=judge python research/supersession_bench.py "
                        "--arms nevertwice --sleep")
    header = ["corpus and reading", "default", "with the switch on"]
    rows = []
    for corpus, fam_a, fam_b in (("explicit", "supersession", "supersession_switch"),
                                 ("implicit", "supersession_implicit", "supersession_implicit_switch")):
        for reading, slug in (("between nights", "nevertwice"),
                              ("after consolidation", "nevertwice_after_sleep")):
            for metric, name in (("over_retraction_rate", "retires a still-true fact"),
                                 ("stale_rate", "returns the retracted fact")):
                a, b = f"{fam_a}.{slug}.{metric}", f"{fam_b}.{slug}.{metric}"
                if not (c.has(a) and c.has(b)):
                    continue
                rows.append([f"{corpus}, {reading} - {name}",
                             f"{c.value(a):.3f}", f"{c.value(b):.3f}"])
    if not rows:
        return _not_yet("supersession_switch",
                        "NEVERTWICE_EXPLICIT_RETIRE=judge python research/supersession_bench.py "
                        "--arms nevertwice --sleep")
    return _table(header, rows)


PAIRS = (("Nevertwice vs Mem0", "nevertwice", "mem0"), ("Nevertwice vs naive", "nevertwice", "naive"),
         ("Nevertwice vs Zep/Graphiti", "nevertwice", "zep"), ("**Mem0 vs naive**", "mem0", "naive"))


def render_supersession_pairs(c: Claims, fam: str = "supersession") -> str:
    """Paired McNemar tests on the same cases: discordant pairs (ours first) and the exact p.
    The p prints in the form its claim registered - a tiny p in its power-of-ten form - so the
    table cannot show `0.00` for a value that is small rather than zero."""
    rows = []
    for label, a, b in PAIRS:
        x, y = sorted((a, b))
        pid = f"{fam}.{x}_vs_{y}.p_mcnemar"
        if not c.has(pid):
            continue
        da, db = f"{fam}.{x}_vs_{y}.discordant.{a}", f"{fam}.{x}_vs_{y}.discordant.{b}"
        disc = (f"{int(c.value(da))} - {int(c.value(db))}" if c.has(da) and c.has(db) else "-")
        c.value(pid)                                       # raises Withdrawn if it is
        rows.append([label, disc, str(c.get(pid)["printed"][0])])
    if not rows:
        return _not_yet(fam, "python research/supersession_bench.py --pool ...")
    return _table(["pair", "discordant (first - second)", "p, McNemar exact"], rows)


def render_supersession_variants(c: Claims) -> str:
    """How often each system hands back the fact that was retracted - on the corpus that says so
    and on the one that does not. Nothing is bolded: bold reads as a win, and the win in the
    first two columns is the small number, which is the opposite convention from every other
    table on these pages."""
    if not c.has("supersession.nevertwice.stale_rate"):
        return _not_yet("supersession",
                        "python research/supersession_bench.py --arms nevertwice,naive")
    rows = [[label,
             _cell(c, f"supersession.{slug}.stale_rate"),
             _cell(c, f"supersession_implicit.{slug}.stale_rate"),
             _cell(c, f"supersession.{slug}.current_rate"),
             _cell(c, f"supersession_implicit.{slug}.current_rate")]
            for label, slug in SUPERSESSION_ARMS]
    out = _table(["system", "stale, explicit", "stale, implicit",
                  "current, explicit", "current, implicit"], rows)
    out += ("\n\n<sub>Stale = the retracted fact came back, lower is better. Current = the fact "
            "that replaced it was returned, higher is better. *Explicit* names the retraction in "
            "the second session; *implicit* frames the replacement like any first assertion.</sub>")
    if not c.has("supersession_implicit.nevertwice.stale_rate"):
        out += ("\n\n<sub>The implicit columns are empty until `python "
                "research/supersession_bench.py --dataset "
                "research/data/supersession_v1_implicit.json` has run.</sub>")
    return out


def asof_verdict(c: Claims) -> str:
    """The sentence under the as-of table, computed from the claims.

    Until 2026-09-11 this caption was a constant - "and this is below it" - written when the
    gate was missed and never compared again, so it went on printing a miss on a run that had
    met the gate (both days 0.800 against 0.80). A verdict is a comparison, and a comparison
    the renderer does not perform is a number typed by hand. The sentence names the run-to-run
    spread and the interval too, because a value that sits exactly on its threshold is a
    boundary, not a margin, and the reader should not have to infer that.
    """
    thr = c.value("asof.gate.threshold")
    both = c.value("asof.nevertwice.both_correct")
    old_thr = c.value("asof.gate.old_day_threshold") if c.has("asof.gate.old_day_threshold") else None
    old = c.value("asof.nevertwice.old_day")
    new = c.value("asof.nevertwice.new_day")
    gate = f"The gate written before the run was {thr:.2f} on both days"
    if old_thr is not None:
        gate += f" and {old_thr:.2f} on the old day"
    met = both >= thr - 1e-9 and (old_thr is None or old >= old_thr - 1e-9)
    if met:
        at = (" - exactly at the threshold, a boundary rather than a margin"
              if abs(both - thr) < 1e-9 else "")
        verdict = f"{gate}; this run meets it{at}: both days {both:.3f}"
        if old_thr is not None:
            verdict += f", the old day {old:.3f}"
    else:
        verdict = f"{gate}, and this run is below it: both days {both:.3f}"
        if old_thr is not None and old < old_thr:
            verdict += f", the old day {old:.3f}"
    runs = [c.value(f"asof.nevertwice.per_run.{i}") for i in ("one", "two")
            if c.has(f"asof.nevertwice.per_run.{i}")]
    if len(runs) == 2:
        verdict += f"; the two runs behind the pooled figure read {runs[0]:.3f} and {runs[1]:.3f}"
    ci = c.get("asof.nevertwice.both_correct").get("ci") or {}
    if "low" in ci:
        rel = "covers" if ci["low"] <= thr <= ci["high"] else "excludes"
        verdict += f", and the interval [{ci['low']:.3f}, {ci['high']:.3f}] {rel} the threshold"
    side = "the old day" if old <= new else "the day after"
    verdict += f". The larger loss is on {side}"
    kinds = {k: c.value(f"asof.nevertwice.old_day_miss.{k}")
             for k in ("never_written", "absorbed", "unranked", "paraphrase", "leak")
             if c.has(f"asof.nevertwice.old_day_miss.{k}")}
    if kinds:
        verdict += ("; the old-day misses split by kind in the artifact: "
                    f"{int(kinds.get('never_written', 0))} where the extractor left the first session "
                    f"without a note, {int(kinds.get('absorbed', 0))} where its note was absorbed into the "
                    f"second session's and no longer serves the old fact, {int(kinds.get('unranked', 0))} where "
                    f"its note existed and nothing came back, {int(kinds.get('paraphrase', 0))} where the note "
                    f"came back without the marker, {int(kinds.get('leak', 0))} where the new fact leaked into "
                    f"the old day")
    return verdict


def render_asof(c: Claims) -> str:
    """As-of recall beside the gate written before it ran. The table is here whether the gate
    was met or missed - ledger I6 says a missed gate is published rather than buried - and the
    verdict under it is computed (`asof_verdict`), never typed."""
    if not c.has("asof.nevertwice.both_correct"):
        return _not_yet("asof", "python research/asof_bench.py --arms nevertwice,naive --runs 2")
    rows = [[label, _cell(c, f"asof.{slug}.both_correct"), _cell(c, f"asof.{slug}.old_day"),
             _cell(c, f"asof.{slug}.new_day")]
            for label, slug in (("**Nevertwice** (`api.as_of`)", "nevertwice"),
                                ("Zep/Graphiti (`graphiti-core`, its own bitemporal edges)", "zep"),
                                ("an append-only markdown file, no dates", "naive"))
            if c.has(f"asof.{slug}.both_correct")]
    out = _table(["arm", "both days", "the old day", "the day after"], rows)
    if c.has("asof.gate.threshold"):
        out += (f"\n\n<sub>{asof_verdict(c)}. Mem0 has no row - it stamps a memory with "
                "the wall-clock time of the `add()` call and its search has no as-of filter, so "
                "facts cannot be placed in the past without patching the product.</sub>")
    return out


FRONTIER_ROWS = [
    ("**Nevertwice, shipped ranker, sessions whole**", "nevertwice_whole"),
    ("Nevertwice, shipped ranker, query passages", "nevertwice_snippet"),
    ("Nevertwice, our extractor's notes", "nevertwice_full"),
    ("Mem0 store search, sessions whole", "mem0"),
    ("Mem0 full pipeline, its memories", "mem0_infer"),
    ("A-MEM full pipeline, its notes", "amem_full"),
]


def render_frontier(c: Claims) -> str:
    """Accuracy per token: for each arm that ran, judge-scored accuracy and the reader's own
    prompt-token count at k = 1, 3, 5; the two brackets below. Arms without claims are left
    out and named under the table."""
    if not c.has("frontier.none.accuracy"):
        return _not_yet("frontier", "python research/frontier_eval.py judge --save")
    header = ["system", "k=1 acc", "k=1 tokens", "k=3 acc", "k=3 tokens", "k=5 acc", "k=5 tokens"]
    rows, missing = [], []
    for label, slug in FRONTIER_ROWS:
        if not c.has(f"frontier.{slug}.k1.accuracy"):
            missing.append(label.strip("*").split(",")[0])
            continue
        row = [label]
        for k in (1, 3, 5):
            if c.has(f"frontier.{slug}.k{k}.accuracy"):
                row += [f"{c.value(f'frontier.{slug}.k{k}.accuracy'):.3f}",
                        f"{c.value(f'frontier.{slug}.k{k}.prompt_tokens'):,.0f}"]
            else:
                row += ["-", "-"]
        rows.append(row)
    out = _table(header, rows)
    brackets = []
    for b, label in (("none", "no memory, the question alone"), ("oracle", "the oracle ceiling, gold sessions whole")):
        if c.has(f"frontier.{b}.accuracy"):
            brackets.append(f"{label}: accuracy {c.value(f'frontier.{b}.accuracy'):.3f} at "
                            f"{c.value(f'frontier.{b}.prompt_tokens'):,.0f} tokens")
    if brackets:
        out += "\n\nBrackets - " + "; ".join(brackets) + "."
    if c.has("frontier.judge_disagreement"):
        out += (f" The two judges disagree on {c.value('frontier.judge_disagreement'):.3f} of the shipped "
                f"arm's answers; a gap between two rows smaller than that is not a gap.")
    if missing:
        out += "\n\n<sub>Not on this table: " + ", ".join(missing) + " - no run yet, or a blocker recorded in the artifact.</sub>"
    return out


GUARD_ROWS = [
    ("**guards, engine's no-model patterns**", "guards_deterministic"),
    ("**guards, model-written patterns**", "guards_llm"),
    ("cold-start pack (no history)", "universal_pack"),
    ("linter or scanner (scored in its favour)", "linter_or_test"),
    ("prompt recall over the notes (top three)", "prompt_recall"),
    ("silence (floor)", "never"),
]


def render_guard_bench(c: Claims) -> str:
    """The active-memory stand: each arm read at the same false-alarm budget. An arm with no
    operating point under the budget has no recall cell and says so; an arm whose claims are
    absent is left out and named."""
    fam = "guards"
    if not any(c.has(f"{fam}.{slug}.recall_at_fpr") or c.has(f"{fam}.{slug}.recall_all_fire")
               for _, slug in GUARD_ROWS):
        return _not_yet(fam, "python research/guard_bench.py --llm --save")
    header = ["arm", "recall of the right guard", "precision", "hard-negative false alarms",
              "project-only recall", "tokens / call", "ms / check"]
    rows, missing, none = [], [], []
    for label, slug in GUARD_ROWS:
        if c.has(f"{fam}.{slug}.recall_all_fire"):
            none.append(label.strip("*"))
            # An arm above the budget still has a class split, and the two arms the project-class
            # question is about are both here. Their cells are read at the arm's own firing rate,
            # not at the budget the other rows use, so the caption says so - a reader who compares
            # them column-wise without that sentence is comparing two different operating points.
            rows.append([label, f"{_cell(c, f'{fam}.{slug}.recall_all_fire')} at FPR {_cell(c, f'{fam}.{slug}.fpr_all_fire')} (over budget)",
                         "-",
                         _cell(c, f"{fam}.{slug}.hard_negative_fpr") if c.has(f"{fam}.{slug}.hard_negative_fpr") else "-",
                         _cell(c, f"{fam}.{slug}.project_recall") if c.has(f"{fam}.{slug}.project_recall") else "-",
                         _cell(c, f"{fam}.{slug}.tokens_per_call") if c.has(f"{fam}.{slug}.tokens_per_call") else "-",
                         _cell(c, f"{fam}.{slug}.ms_per_call") if c.has(f"{fam}.{slug}.ms_per_call") else "-"])
            continue
        if not c.has(f"{fam}.{slug}.recall_at_fpr"):
            missing.append(label.strip("*"))
            continue
        rows.append([label, _cell(c, f"{fam}.{slug}.recall_at_fpr"),
                     _cell(c, f"{fam}.{slug}.precision_at_fpr") if c.has(f"{fam}.{slug}.precision_at_fpr") else "-",
                     _cell(c, f"{fam}.{slug}.hard_negative_fpr") if c.has(f"{fam}.{slug}.hard_negative_fpr") else "-",
                     _cell(c, f"{fam}.{slug}.project_recall") if c.has(f"{fam}.{slug}.project_recall") else "-",
                     _cell(c, f"{fam}.{slug}.tokens_per_call"), _cell(c, f"{fam}.{slug}.ms_per_call")])
    out = _table(header, rows)
    notes = []
    if none:
        notes.append("no operating point under the false-alarm budget for " + ", ".join(none)
                     + " - a guard fires or it does not, and firing catches the repeats shown at the false-alarm rate "
                       "shown; their hard-negative and project-only cells are read where the arm fires, not at the "
                       "budget the rows below use")
    if missing:
        notes.append("no row for " + ", ".join(missing) + ": the arm has no registered number (not run, or blocked)")
    if notes:
        out += "\n\n<sub>" + "; ".join(notes) + ".</sub>"
    return out


CODE_SESSION_ROWS = [
    ("**Nevertwice, our extractor's notes**", "nevertwice_full"),
    ("append-only sessions, term overlap (floor)", "naive"),
    ("Mem0 full pipeline, its memories", "mem0_infer"),
    ("no memory (bracket)", "none"),
    ("the gold session whole (bracket)", "oracle"),
]


def render_code_sessions(c: Claims, fam: str = "code_sessions") -> str:
    """The code-session stand: accuracy by question type per arm; the `current` column carries
    the stale rate beside it, the situation column is retrieval only. `fam` selects the synthetic
    corpus (`code_sessions`) or the private held-out (`code_heldout`)."""
    if not any(c.has(f"{fam}.{slug}.fact") for _, slug in CODE_SESSION_ROWS):
        return _not_yet(fam, "python research/code_sessions_eval.py judge --arms nevertwice_full,naive,mem0_infer --save")
    header = ["system", "fact", "current", "stale", "lesson", "situation (top three)", "tokens"]
    rows, missing = [], []
    for label, slug in CODE_SESSION_ROWS:
        if not c.has(f"{fam}.{slug}.fact"):
            missing.append(label.strip("*"))
            continue
        cells = [(_cell(c, f"{fam}.{slug}.{k}") if c.has(f"{fam}.{slug}.{k}") else "-")
                 for k in ("fact", "current", "stale", "lesson", "situation")]
        cells.append(f"{c.value(f'{fam}.{slug}.tokens'):,.0f}" if c.has(f"{fam}.{slug}.tokens") else "-")
        rows.append([label] + cells)
    out = _table(header, rows)
    if missing:
        out += "\n\n<sub>No row for " + ", ".join(missing) + ": no registered number for the arm.</sub>"
    return out


RENDERERS = {
    "supersession-pinned": render_supersession_pinned,
    "supersession-variants": render_supersession_variants,
    "supersession-causes": render_supersession_causes,
    "supersession-causes-implicit": lambda c: render_supersession_causes(c, "supersession_implicit"),
    "supersession-pairs": render_supersession_pairs,
    "supersession-pairs-implicit": lambda c: render_supersession_pairs(c, "supersession_implicit"),
    "supersession-readings": render_supersession_readings,
    "supersession-readings-implicit": lambda c: render_supersession_readings(c, "supersession_implicit"),
    "supersession-switch": render_supersession_switch,
    "asof": render_asof,
    "locomo-categories": render_locomo_categories,
    "abstention-sweep": render_abstention_sweep,
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
    "frontier": render_frontier,
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
    "guard-bench": render_guard_bench,
    "code-sessions": render_code_sessions,
    "code-heldout": lambda c: render_code_sessions(c, "code_heldout"),
}


# ------------------------------------------------------------------ footers

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
