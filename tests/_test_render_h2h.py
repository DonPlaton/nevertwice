"""The head-to-head renderers for the LoCoMo and non-oracle families, the pipeline table, and
generated regions on study pages.

A table is generated from claim ids so that a number cannot be typed by hand; these checks pin
what the generator does with a family that is complete, a pipeline arm that blocked (no claim,
so no row, and a note that says so), and a region that sits on a backlog page.
"""
import _env_guard  # noqa: F401
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import render_claims as rc  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def claim(cid, value, stale=None):
    c = {"id": cid, "value": value, "printed": [f"{value:.3f}"], "cited_in": [],
         "command": "python research/head_to_head.py", "commit": "abc"}
    if stale:
        c["stale"] = stale
    return c


def family(prefix, systems, stale=None):
    out = []
    for i, s in enumerate(systems):
        base = 0.3 + 0.1 * i
        out += [claim(f"{prefix}.{s}.recall_at_1", base), claim(f"{prefix}.{s}.recall_at_5", base + 0.2),
                claim(f"{prefix}.{s}.recall_at_10", base + 0.3), claim(f"{prefix}.{s}.mrr", base + 0.05, stale)]
    return out


print("\n- a complete family renders four rows with the best per column in bold -")
man = {"claims": family("h2h_locomo", ["nevertwice", "mem0", "langmem", "amem"]),
       "scope": {"docs": []}, "documents": {}}
c = rc.Claims(man)
table = rc.render_head_to_head_locomo(c)
lines = table.splitlines()
check("header names the four metrics", lines[0] == "| system | R@1 | R@5 | R@10 | MRR |", lines[0])
check("one row per system, in the fixed order",
      [ln.split("|")[1].strip() for ln in lines[2:]]
      == ["**Nevertwice (calibrated fusion)**", "Mem0", "LangMem", "A-MEM"], str(lines[2:]))
check("the best value in each column is bold, and it is A-MEM's here (synthetic)",
      lines[-1].count("**") == 8 and "**0.600**" in lines[-1], lines[-1])
check("three decimals everywhere", all(len(x.strip().strip("*").split(".")[1]) == 3
                                        for ln in lines[2:] for x in ln.split("|")[2:-1]))

print("\n- a blocked arm has no row and is named under the table -")
short = {"claims": family("h2h_s", ["nevertwice", "mem0", "langmem"]), "scope": {"docs": []},
         "documents": {}}
out = rc.render_head_to_head_s(rc.Claims(short))
check("three rows for the three registered arms", out.count("\n| ") == 3 and "| A-MEM |" not in out, out)
check("the missing arm is named as a blocker", "<sub>No row for A-MEM:" in out)
try:
    rc._h2h_rows(rc.Claims(short), "h2h_s", rc.HEAD_TO_HEAD_ROWS, required=True)
    check("a strict caller still gets an error for a partial family", False)
except KeyError as e:
    check("a strict caller still gets an error for a partial family", "h2h_s.amem" in str(e), str(e))

print("\n- a family that was never registered renders as a dated gap -")
none = {"claims": [], "scope": {"docs": []}, "documents": {}}
gap = rc.render_head_to_head_s(rc.Claims(none))
check("the gap names the family and the command that fills it",
      gap.startswith("> **Not measured yet.**") and "h2h_s.*" in gap and "--data=s" in gap, gap)
check("the LoCoMo family has the same behaviour", "--data=locomo" in rc.render_head_to_head_locomo(rc.Claims(none)))

print("\n- a withdrawn claim inside a family surfaces as Withdrawn -")
wd = {"claims": family("h2h_s", ["nevertwice", "mem0", "langmem", "amem"], stale="re-run queued"),
      "scope": {"docs": []}, "documents": {}}
try:
    rc.render_head_to_head_s(rc.Claims(wd))
    check("withdrawn MRR raises Withdrawn", False)
except rc.Withdrawn as w:
    check("withdrawn MRR raises Withdrawn", w.claim_id.endswith(".mrr"))

print("\n- the pipeline table: our row, the arms that ran, a note for the ones that blocked -")
pipe = {"claims": family("h2h_pinned", ["nevertwice", "mem0_infer"]), "scope": {"docs": []},
        "documents": {}}
out = rc.render_head_to_head_full(rc.Claims(pipe))
check("our row first", "| **Nevertwice (calibrated fusion)** |" in out.splitlines()[2])
check("the pipeline arm that ran has a row", "Mem0, full pipeline" in out)
check("arms without claims have no row", "LangMem, full pipeline" not in out.split("<sub>")[0])
check("...and are named under the table", "<sub>No row for LangMem, A-MEM:" in out, out)
full = {"claims": family("h2h_pinned", ["nevertwice", "mem0_infer", "langmem_full", "amem_full"]),
        "scope": {"docs": []}, "documents": {}}
check("no note when every pipeline arm ran", "<sub>" not in rc.render_head_to_head_full(rc.Claims(full)))
check("Claims.has answers without raising", rc.Claims(pipe).has("h2h_pinned.mem0_infer.mrr")
      and not rc.Claims(pipe).has("h2h_pinned.amem_full.mrr"))

print("\n- generated regions on a study page are found and rendered -")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "docs").mkdir()
    (root / "research").mkdir()
    (root / "docs" / "GOV.md").write_text("# governed\n", encoding="utf-8")
    (root / "research" / "STUDY.md").write_text(
        "# study\n\n<!-- claims:head-to-head-locomo -->\nold\n<!-- /claims:head-to-head-locomo -->\n",
        encoding="utf-8")
    (root / "research" / "OTHER.md").write_text("# no region here\n", encoding="utf-8")
    man2 = {"claims": family("h2h_locomo", ["nevertwice", "mem0", "langmem", "amem"]),
            "scope": {"docs": ["docs/GOV.md"]},
            "documents": {"docs/GOV.md": {}, "research/STUDY.md": {}, "research/OTHER.md": {},
                          "research/MISSING.md": {}}}
    saved, saved_renderers = rc.ROOT, rc.RENDERERS
    rc.ROOT = root
    # the family's renderer may not be registered yet (it lands with its region and claims)
    rc.RENDERERS = {**rc.RENDERERS, "head-to-head-locomo": rc.render_head_to_head_locomo}
    try:
        docs = rc.docs_with_regions(man2)
        check("the governed doc comes first", docs[0] == root / "docs" / "GOV.md")
        check("a registered study page with a region is included",
              root / "research" / "STUDY.md" in docs)
        check("a page without a region is not", root / "research" / "OTHER.md" not in docs)
        check("a registered page that does not exist is skipped", len(docs) == 2, str(docs))
        rendered, changed = rc.apply_regions((root / "research" / "STUDY.md").read_text(encoding="utf-8"),
                                             rc.Claims(man2))
        check("the study page's region is rendered from claims",
              changed == ["head-to-head-locomo"] and "| **Nevertwice (calibrated fusion)** |" in rendered)
    finally:
        rc.ROOT, rc.RENDERERS = saved, saved_renderers

print("\n- a partly-withdrawn table prints its live rows and marks the withdrawn cells (restore #2) -")
REASON = "owner decision pending: the arm's traffic was not observed"
TRAP = "; the value is the campaign's and the statement predates it - rewrite the statement before any restore"
fam = family("h2h_locomo", ["nevertwice", "mem0", "langmem", "amem"])
for cl in fam:
    if ".mem0." in cl["id"]:
        cl["stale"] = REASON + (TRAP if cl["id"].endswith("recall_at_1") else "")
page = "x\n<!-- claims:head-to-head-locomo -->\nold\n<!-- /claims:head-to-head-locomo -->\n"
out, changed = rc.apply_regions(page, rc.Claims({"claims": fam, "scope": {"docs": []}, "documents": {}}))
check("the live rows are printed, not a whole-table notice",
      "| **Nevertwice (calibrated fusion)** |" in out and "**Withdrawn 20" not in out, out[:200])
check("every cell of the withdrawn row says so",
      "| Mem0 | withdrawn | withdrawn | withdrawn | withdrawn |" in out, out)
check("no NaN reaches the page", "nan" not in out.lower().replace("withdrawn", ""))
check("the reason is written once under the table, without the register-only suffix",
      out.count(f"<sub>**Withdrawn** cells: {REASON}</sub>") == 1 and "statement predates" not in out, out)
for cl in fam:
    cl["stale"] = REASON
out_all, _ = rc.apply_regions(page, rc.Claims({"claims": fam, "scope": {"docs": []}, "documents": {}}))
check("a region with nothing live stays one withdrawal notice", "**Withdrawn" in out_all
      and "| Mem0 |" not in out_all, out_all[:160])

print("\n- a withdrawn pair's p-value does not leak into the pairs table -")
pairs = []
for _label, a, b in rc.PAIRS:
    x, y = sorted((a, b))
    pid = f"supersession.{x}_vs_{y}"
    stale = REASON if "zep" in (a, b) else None
    p = {"id": f"{pid}.p_mcnemar", "value": 0.0123, "printed": ["0.0123"], "cited_in": [],
         "command": "python research/supersession_bench.py", "commit": "abc"}
    if stale:
        p["stale"] = stale
    pairs.append(p)
    for arm in (a, b):
        pairs.append({**claim(f"{pid}.discordant.{arm}", 3, stale), "printed": ["3"]})
cp = rc.Claims({"claims": pairs, "scope": {"docs": []}, "documents": {}})
body = rc.render_partial(rc.render_supersession_pairs, cp)
zep_row = next((ln for ln in (body or "").splitlines() if "Zep" in ln), "")
check("the Zep pair row is marked, and its p is not printed",
      body is not None and "withdrawn" in zep_row and "0.0123" not in zep_row, zep_row or str(body))
check("the live pairs keep their p", body is not None and body.count("0.0123") == 3, str(body))

print("\n- every renderer registered has a region in a document in scope (the real tree) -")
manifest = json.loads((ROOT / "research/evidence_manifest.json").read_text(encoding="utf-8"))
seen = set()
for doc in rc.docs_with_regions(manifest):
    text = doc.read_text(encoding="utf-8")
    for rid in rc.RENDERERS:
        if rc.region_span(text, rid):
            seen.add(rid)
check("no registered renderer is orphaned", set(rc.RENDERERS) <= seen,
      str(sorted(set(rc.RENDERERS) - seen)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
