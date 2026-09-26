"""A withdrawn figure must not live on in page prose.

The register withdraws a claim and the renderer replaces its table cells, but prose is typed by
hand and nothing re-read it: on 2026-09-25 three pages still quoted figures the register had
withdrawn (SUPERSESSION.md's Zep paragraph, LOCOMO.md's recall figures, COMPARISON.md's
"Mem0 leads"). This suite reads every page - docs/*.md, the top-level research/*.md, README.md -
outside the generated regions, the fenced blocks and the HTML comments, and looks for the
printed form (or the one-decimal percentage) of every withdrawn, non-historical claim.

What it does NOT flag, each by rule rather than by silence:
  * a page carrying the withdrawn banner - the whole page is declared withdrawn;
  * a figure whose own SENTENCE (in prose) or own CELL (in a table row) says "withdrawn" - the
    reader is told at the figure. Not the line: a README table row is one line, and a
    "withdrawn" in its link cell once let a new figure in its result cell through (auditor, A6);
  * a form that is also the printed form of a LIVE claim - the number is not withdrawn then;
  * a form too short to identify anything: two decimals or fewer ("0.64"), a whole percentage
    ("34%" - as a rule it would hit ten times on six pages, nine of them coincidences);
  * a figure written in words ("three in ten", "nineteen to two");
  * the reviewed coincidences below: the same digits, a different measurement.

What that leaves to a reader. The paragraph that motivated this suite - SUPERSESSION.md's Graphiti
paragraph, 2026-09-25 - was written in WORDS, and put back today it would pass; so would "34%"
for 34.2%. This suite catches a withdrawn figure quoted in its printed form, nothing more. Prose
is still read by eye before a withdrawn claim's page loses its banner. The same holds for a
rendering the register does not list: EMBED_SERVING.md printed the withdrawn median cosine as
"1.0000" while its printed forms are 1.0 / 1.00 / 1 - found by reading, not by this suite (stage D).

    python tests/_test_withdrawn_figures_not_in_prose.py
"""
import _env_guard  # noqa: F401
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


#: A form specific enough to be a quotation: three or more decimals, a percentage with a decimal,
#: or a number with three integer digits and a decimal part.
DISTINCT = re.compile(r"\d+\.\d{3,}%?|\d+\.\d+%|\d{3,}\.\d+")
#: A multiplier - "31×", "5.9x", "~7×" - is how a headline ratio is quoted. A withdrawn claim's
#: OWN printed multiplier is specific enough even with one digit: the pages are few and a real
#: collision goes to REVIEWED_COINCIDENCES (auditor, stage D: the withdrawn 31× stood unmarked on
#: five pages, and 5.9×, 44× and ~7× on three more, because only decimals counted as distinct).
#: `×` and `x` are one character here - the register printed "44x", DEMO.md wrote "44×".
MULTIPLIER = re.compile(r"\d+(?:\.\d+)?\s?[×x]")
TRIVIAL = {"0.000", "1.000", "0.0%", "100.0%"}
REGION = re.compile(r"<!--\s*(claims|comparison):([\w-]+)\s*-->.*?<!--\s*/\1:\2\s*-->", re.S)
FENCE = re.compile(r"```.*?```", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
BANNER = "<!-- withdrawn-banner -->"

#: (page, form) -> why the digits are not the withdrawn claim. Reviewed 2026-09-25, one by one;
#: every entry must still match (checked below), so a fixed page cannot leave a stale excuse.
REVIEWED_COINCIDENCES = {
    ("docs/CONFIG.md", "0.802"): "the scale-index quantisation study (float16 vs 1-bit), not a register claim",
    ("docs/CONFIG.md", "0.737"): "the dedup threshold's calibration on a live store (a cosine), not a recall",
    ("research/CAUSAL_VOCAB.md", "63.9%"): "the growth of impact-graph nodes, not a LoCoMo category",
    ("research/CLAIM_HALFLIFE.md", "0.333"): "a survival fraction 127/381, not frontier accuracy",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.978"): "an embedder's retrieval score, not code-sessions accuracy",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.975"): "a confidence bound of an embedder's score, not token_ab",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.735"): "a confidence bound of an embedder's score, not the fusion sweep",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.683"): "an embedder's retrieval score, not code-sessions accuracy",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.631"): "a confidence bound of an embedder's score, not the fusion sweep",
    # stage D, 2026-09-25: the B1 withdrawal made these forms dead; each reviewed on its line
    ("research/EMBED_HELDOUT_BASELINE.md", "0.956"): "a confidence bound of an embedder's score, not code-sessions",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.662"): "an embedder's retrieval score on held-out data, not LoCoMo raw",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.782"): "a confidence bound of an embedder's score, not LongMemEval",
    ("research/EMBED_HELDOUT_BASELINE.md", "0.042"): "a confidence bound of a distillation delta, not the capacity claim",
    ("research/EMBED_M3_THRESHOLD.md", "0.042"): "the same distillation delta's bound, not the capacity claim",
}


def forms(claim) -> set:
    out = set()
    for p in claim.get("printed") or []:
        s = str(p)
        if DISTINCT.fullmatch(s):
            out.add(s)
        elif MULTIPLIER.fullmatch(s):
            #: a page writes "44×" where the register printed "44x", and the other way round
            out |= {s.replace("×", "x"), s.replace("x", "×")}
    v = claim.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1:
        out.add(f"{v * 100:.1f}%")
    return out - TRIVIAL


def withdrawn_forms(claims) -> dict:
    live, dead = set(), {}
    for c in claims:
        if c.get("stale") and not c.get("historical_on"):
            for f in forms(c):
                dead.setdefault(f, []).append(c["id"])
        elif not c.get("stale"):
            live |= forms(c)
    return {f: ids for f, ids in dead.items() if f not in live}


def _blank(match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


#: Where a sentence ends: terminal punctuation (and any closing quote, bracket or emphasis) before
#: whitespace, a blank line, or a new list item, heading or table row. A figure's own decimal
#: point is followed by a digit, so it never ends one.
#: A semicolon ends the unit too (a clause): docs/INTEGRATIONS.md put the withdrawn 31× and "the
#: live repeat-error figure that stood here was withdrawn in 2026-08" in one sentence, two clauses
#: apart, and the second clause's "withdrawn" - about another figure - let the first through
#: (auditor, stage D).
SENTENCE_END = re.compile(r"[.!?;][\"')\]*_]*(?=\s)|\n[ \t]*\n|\n(?=[ \t]*(?:[-*+]|\d+\.|#+|\|)\s)")


def unit(prose: str, start: int, end: int) -> str:
    """The sentence a figure at prose[start:end] stands in, cut to its table cell in a table row.

    A table row is one line, so a line-wide rule let a figure through on the strength of a
    "withdrawn" three sentences away in another cell - or in the same cell (README's external
    retrieval row says "withdrawn in 2026-08" about other figures)."""
    # The paragraph bounds the search (auditor, 2026-09-25): scanning back from the start of the
    # page made the scan quadratic in the number of hits.
    para = prose.rfind("\n\n", 0, start)
    lo, hi = (para + 1 if para >= 0 else 0), len(prose)
    ls = prose.rfind("\n", 0, start) + 1
    le = prose.find("\n", end)
    le = len(prose) if le < 0 else le
    if prose[ls:le].lstrip().startswith("|"):
        left = prose.rfind("|", ls, start)
        right = prose.find("|", end, le)
        lo, hi = (ls if left < 0 else left + 1), (le if right < 0 else right)
    begin = lo
    for m in SENTENCE_END.finditer(prose, lo, start):
        begin = m.end()
    m = SENTENCE_END.search(prose, end, hi)
    return prose[begin:m.end() if m else hi]


def scan(page: str, text: str, dead: dict) -> list:
    """(page, line, form, claim id) for every withdrawn figure quoted in prose on this page."""
    if BANNER in text:
        return []
    prose = COMMENT.sub(_blank, REGION.sub(_blank, FENCE.sub(_blank, text)))
    hits = []
    for form, ids in dead.items():
        for m in re.finditer(r"(?<![\d.])" + re.escape(form) + r"(?![\dA-Za-z])", prose):
            if "withdrawn" in unit(prose, m.start(), m.end()).lower():
                continue
            hits.append((page, prose.count("\n", 0, m.start()) + 1, form, ids[0]))
    return hits


dead = withdrawn_forms(MANIFEST["claims"])
#: docs/ recursively (starter issues quote headlines), the top-level research pages, every README
#: under research/ (the embedder's model card is published text), and the repository README.
pages = (sorted((ROOT / "docs").rglob("*.md")) + sorted((ROOT / "research").glob("*.md"))
         + sorted(p for p in (ROOT / "research").rglob("README.md")
                  if p.parent != ROOT / "research" and "data" not in p.relative_to(ROOT).parts)
         + sorted((ROOT / "examples").rglob("*.md"))
         + [ROOT / "README.md"])

print("\n- no page quotes a withdrawn figure in its prose -")
check("there are withdrawn figures to look for", len(dead) > 50, str(len(dead)))
hits = []
for p in pages:
    rel = p.relative_to(ROOT).as_posix()
    hits += scan(rel, p.read_text(encoding="utf-8"), dead)
unexplained = [h for h in hits if (h[0], h[2]) not in REVIEWED_COINCIDENCES]
check("every withdrawn figure in prose is either gone, marked withdrawn, or a reviewed coincidence",
      not unexplained, "; ".join(f"{h[0]}:{h[1]} {h[2]} ({h[3]})" for h in unexplained[:4]))
seen = {(h[0], h[2]) for h in hits}
stale_excuses = sorted(k for k in REVIEWED_COINCIDENCES if k not in seen)
check("every reviewed coincidence still occurs (a fixed page leaves no stale excuse)",
      not stale_excuses, str(stale_excuses))

print("\n- the scan can fail: a withdrawn Zep figure typed into prose is caught by name -")
sup = (ROOT / "research" / "SUPERSESSION.md").read_text(encoding="utf-8")
probe = sup + "\nZep/Graphiti served the old value in 34.2% of the explicit cases.\n"
caught = [h for h in scan("research/SUPERSESSION.md", probe, dead) if h[2] == "34.2%"]
check("the probe line is flagged, naming a withdrawn Zep claim",
      bool(caught) and caught[0][3].startswith("supersession.zep."), str(caught[:1]))
marked = probe.replace("in 34.2% of the explicit cases.", "in 34.2% of the explicit cases (withdrawn).")
check("and the same line is let through once it says the figure is withdrawn",
      not [h for h in scan("research/SUPERSESSION.md", marked, dead) if h[2] == "34.2%"])

#: Multipliers, both spellings: the register printed "44x", DEMO.md wrote "44×" (stage D).
_mdead = withdrawn_forms([{"id": "probe.ratio", "stale": "probe", "printed": ["44x"]}])
check("a withdrawn multiplier printed '44x' is found where a page writes '44×'",
      [h[2] for h in scan("docs/PROBE.md", "The economy grows to **44×** on a big store.\n", _mdead)] == ["44×"],
      str(sorted(_mdead)))
check("and a one-digit multiplier of a withdrawn claim ('~7×') is found too",
      [h[2] for h in scan("docs/PROBE.md", "It is ~7× cheaper than a dump.\n",
                          withdrawn_forms([{"id": "p7", "stale": "p", "printed": ["7x"]}]))] == ["7×"])


def _zep(page: str, text: str) -> list:
    return [h for h in scan(page, text, dead) if h[2] == "34.2%"]


print("\n- the exemption is the figure's own sentence or cell, not its line (auditor, A6) -")
#: The shape of README's external retrieval row as the auditor's A6 found it (0a2c0ad): one cell
#: carrying a figure AND, two sentences on, "withdrawn" about OTHER figures. The live README row
#: no longer prints a figure (stage D withdrew it), so the shape is kept here verbatim.
row = ("| external retrieval, one pool and one embedder for everyone | R@5 **0.800** on a hash-pinned "
       "LongMemEval corpus; re-measured on 2026-09-25 by the second campaign, every arm on one frozen "
       "commit. The page it links to also keeps the figures withdrawn in 2026-08, and why | "
       "[EXTERNAL_RETRIEVAL.md](research/EXTERNAL_RETRIEVAL.md) |\n")
check("a withdrawn figure typed into that row is flagged - the 'withdrawn' is another sentence",
      bool(_zep("README.md", row.replace("R@5 **0.800**", "R@5 **0.800** (Zep stale 34.2%)", 1))))
check("and let through when its own sentence says withdrawn",
      not _zep("README.md", row.replace("R@5 **0.800**", "R@5 **0.800** (Zep stale 34.2%, withdrawn)", 1)))
check("a 'withdrawn' in another cell of the row does not excuse it",
      bool(_zep("x.md", "| a row | Zep was stale in 34.2% of cases | figures withdrawn in 2026-08 |\n")))
check("a 'withdrawn' in the previous sentence of the same line does not excuse it",
      bool(_zep("x.md", "That figure is withdrawn. Zep was stale in 34.2% of the explicit cases.\n")))
check("a sentence wrapped over two lines is one unit",
      not _zep("x.md", "Zep was stale in 34.2% of the explicit cases\n(the figure is withdrawn now).\n"))
check("but a new list item is a new unit",
      bool(_zep("x.md", "- the figure below is withdrawn\n- Zep was stale in 34.2% of the cases\n")))

print("\n- the banner tool does not read a live region's interval bounds as a withdrawn figure -")
sys.path.insert(0, str(ROOT / "tools"))
import stamp_withdrawn as sw  # noqa: E402
_hist = [{"id": "embed.hard_negatives.situation.delta_recall_at_5", "printed": ["0.005"],
          "stale": "historical: a rejected experiment", "historical_on": "2026-09-23"}]
_region = ("<!-- claims:supersession-readings -->\n| Between nights | 0.017 [0.005, 0.059] |\n"
           "<!-- /claims:supersession-readings -->\n")
check("a bound inside a claims region is not a withdrawn figure (research/SUPERSESSION.md, 2026-09)",
      sw.figures_on_page(_region, _hist, set()) == [], str(sw.figures_on_page(_region, _hist, set())))
check("while the same digits in prose still are",
      sw.figures_on_page("the delta was 0.005 on situation recall\n", _hist, set())
      == [("embed.hard_negatives.situation.delta_recall_at_5", "0.005")])

print(f"\nwithdrawn figures not in prose: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
