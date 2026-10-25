#!/usr/bin/env python3
"""The published diagrams, generated rather than drawn.

A hand-drawn diagram is a claim nobody can re-check. This one is emitted from the description
below, so the file in `docs/` and the description here cannot disagree, and CI fails if they
drift - the same rule the published tables live under.

What it has to show, and what the previous ASCII sketch did not:

* **two lanes.** Write-time and read-time are different pipelines with different costs, and
  drawing them as one chain is what makes people assume memory taxes every turn.
* **where the tokens go.** Three stages cost nothing in context until something fires. That is
  the whole product argument, so it is marked on the stage, not left to the prose.
* **what stays local.** Every stage that never leaves the machine carries the same mark, so
  "local-first" is checkable against the picture instead of asserted beside it.
* **the intervention point.** One stage, marked once, where memory stops being a document and
  starts changing what the agent does.

Accessibility is part of the artifact, not a review step: `<title>` and `<desc>` carry the
whole diagram in words, every status is shape **and** label rather than colour alone, and the
palette is Okabe-Ito on white, at contrast ratios a test asserts.

Three of them: the architecture, the four-frame sequence the project is named after, and the v3
scoreboard that puts the measured numbers beside the argument.

    python tools/make_diagrams.py            # check all three match, exit 1 if any is stale
    python tools/make_diagrams.py --write    # regenerate them
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "docs" / "architecture.svg"
SEQ = ROOT / "docs" / "never-twice.svg"
SCOREBOARD = ROOT / "docs" / "v3-scoreboard.svg"

# Okabe-Ito, the same palette research/_figstyle.py uses, so a diagram and a chart in the same
# document belong to one visual system.
BLUE = "#0072B2"
ORANGE = "#E69F00"
GREEN = "#009E73"
PURPLE = "#CC79A7"
INK = "#1A1A1A"
MUTED = "#555555"
RULE = "#B0B0B0"
PAPER = "#FFFFFF"
LANE_BG = "#F5F7F9"

W, H = 960, 620
FONT = ("ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif")

# (x, label, sub, accent, marks) - marks are drawn as small tags under the node.
# "0" = costs no context tokens until it fires; "local" = never leaves the machine.
WRITE = [
    (36, "your session", "any agent", MUTED, ("local",)),
    (256, "capture", "hook · MCP · watch · ingest", BLUE, ("0", "local")),
    (476, "distil", "Tier 1 facts + Tier 2 episodes", BLUE, ("local",)),
    (696, "store", "Markdown + git · [Prior] history", GREEN, ("local",)),
]
READ = [
    (36, "store", "the same files", GREEN, ("local",)),
    (256, "retrieve", "Z-score fusion · temporal anchor", BLUE, ("local",)),
    (476, "decide", "budget · modality gate · rank", ORANGE, ("0", "local")),
    (696, "act", "guard · anticipate · what-if", PURPLE, ("0",)),
]

NODE_W, NODE_H = 228, 86

DESC = (
    "Two lanes. Write time, left to right: your session in any agent; capture through a hook, "
    "an MCP call, the watch daemon or ingest; distil into a dual-tier store, Tier 1 facts that "
    "stay resident and Tier 2 episodes retrieved on demand; store as Markdown plus git history "
    "that you own, the previous version of every note kept beside the current one. Read time, "
    "left to right: the same store; retrieve by z-score fusion of semantic and lexical search "
    "behind a temporal anchor; decide against a token budget with a modality gate and a ranker, "
    "abstaining rather than guessing; act. Act is the intervention point, where a guard fires, "
    "anticipation warns, or a counterfactual is answered. Capture, decide and act cost no "
    "context tokens until something fires. Every stage except act stays on your machine by "
    "default; act is what reaches the agent. The foot of the diagram names the v3.0 upgrade: "
    "dual-tier write time and an attention-native decide stage."
)


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def node(x: int, y: int, label: str, sub: str, accent: str, marks: tuple) -> list:
    out = [
        f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="10" '
        f'fill="{PAPER}" stroke="{accent}" stroke-width="2"/>',
        f'<rect x="{x}" y="{y}" width="6" height="{NODE_H}" rx="3" fill="{accent}"/>',
        f'<text x="{x + 20}" y="{y + 32}" font-size="17" font-weight="600" '
        f'fill="{INK}">{esc(label)}</text>',
        f'<text x="{x + 20}" y="{y + 54}" font-size="12" fill="{MUTED}">{esc(sub)}</text>',
    ]
    tag_x = x + 20
    for mark in marks:
        text, fill = (("0 tokens", INK) if mark == "0" else ("local", INK))
        width = 62 if mark == "0" else 44
        out.append(
            f'<rect x="{tag_x}" y="{y + 63}" width="{width}" height="16" rx="8" '
            f'fill="{PAPER}" stroke="{RULE}" stroke-width="1"/>')
        if mark == "0":
            out.append(f'<circle cx="{tag_x + 9}" cy="{y + 71}" r="3.5" fill="none" '
                       f'stroke="{INK}" stroke-width="1.4"/>')
        else:
            out.append(f'<rect x="{tag_x + 6}" y="{y + 67.5}" width="7" height="7" rx="1.5" '
                       f'fill="none" stroke="{INK}" stroke-width="1.4"/>')
        out.append(f'<text x="{tag_x + 18}" y="{y + 75}" font-size="10" '
                   f'fill="{fill}">{text}</text>')
        tag_x += width + 8
    return out


def arrow(x: int, y: int) -> str:
    return (f'<path d="M {x} {y} L {x + 26} {y}" stroke="{RULE}" stroke-width="2" '
            f'marker-end="url(#tip)" fill="none"/>')


def lane(y: int, title: str, note: str, nodes: list) -> list:
    out = [f'<rect x="16" y="{y - 46}" width="{W - 32}" height="{NODE_H + 74}" rx="14" '
           f'fill="{LANE_BG}"/>',
           f'<text x="36" y="{y - 22}" font-size="13" font-weight="700" letter-spacing="1.2" '
           f'fill="{INK}">{esc(title)}</text>',
           f'<text x="{36 + 9 * len(title)}" y="{y - 22}" font-size="12" '
           f'fill="{MUTED}">{esc(note)}</text>']
    for x, label, sub, accent, marks in nodes:
        out += node(x, y, label, sub, accent, marks)
        if x != nodes[-1][0]:
            out.append(arrow(x + NODE_W + 2, y + NODE_H // 2))
    return out


def build() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
        f'height="{H}" role="img" aria-labelledby="ttl dsc" font-family="{FONT}">',
        '<title id="ttl">Nevertwice v3.0 architecture: a write-time lane and a read-time lane, '
        'with a dual-tier store, the stages that cost no context tokens, and the one point '
        'where memory acts</title>',
        f'<desc id="dsc">{esc(DESC)}</desc>',
        '<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="{RULE}"/>'
        '</marker></defs>',
        f'<rect width="{W}" height="{H}" fill="{PAPER}"/>',
        f'<text x="36" y="42" font-size="22" font-weight="700" fill="{INK}">'
        'How Nevertwice works</text>',
        f'<text x="36" y="64" font-size="13" fill="{MUTED}">Two pipelines, not one loop - '
        'which is why remembering does not tax every turn.</text>',
    ]
    parts += lane(148, "WRITE TIME", "  once per session, in the background", WRITE)
    parts += lane(370, "READ TIME", "  per session start and per prompt", READ)

    # The v3.0 upgrade, called out once at the foot of the diagram rather than implied by the
    # node wording: a dual-tier write path, a gated and ranked read path, and the one stage
    # that still reaches the agent.
    ix = READ[-1][0] + NODE_W // 2
    parts += [
        f'<path d="M {ix} {370 + NODE_H + 6} L {ix} 496" stroke="{PURPLE}" '
        f'stroke-width="2" stroke-dasharray="4 3" fill="none"/>',
        f'<rect x="16" y="496" width="{W - 32}" height="54" rx="14" fill="{LANE_BG}"/>',
        f'<rect x="16" y="496" width="6" height="54" rx="3" fill="{PURPLE}"/>',
        f'<text x="36" y="520" font-size="13" font-weight="700" letter-spacing="1.2" '
        f'fill="{INK}">v3.0 ATTENTION-NATIVE DUAL-TIER</text>',
        f'<text x="316" y="520" font-size="12" fill="{MUTED}">'
        '  Tier 1 facts stay resident; Tier 2 episodes are retrieved on demand.</text>',
        f'<text x="36" y="538" font-size="12" fill="{MUTED}">'
        'act stays the intervention point - the only stage that reaches the agent.</text>',
        f'<text x="944" y="538" font-size="12" text-anchor="end" fill="{MUTED}">'
        'every other stage stays local</text>',
        f'<text x="36" y="570" font-size="12" fill="{MUTED}">'
        'A stage marked 0 tokens adds nothing to the prompt until it fires.</text>',
        f'<text x="36" y="586" font-size="12" fill="{MUTED}">'
        'A stage marked local never leaves the machine on a default install.</text>',
        f'<text x="36" y="606" font-size="11" fill="{MUTED}">'
        'Generated by tools/make_diagrams.py - edit the description there, not this '
        'file.</text>',
        '</svg>',
    ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- the sequence

# The four beats the project is named after, and the four `examples/guard_demo.py --check`
# prints. Keeping them the same four is the point: the picture is the demo, so a reader who
# runs the command sees what the diagram promised rather than a marketing version of it.
FRAMES = [
    ("1", "session A", "the mistake is recorded",
     "mistake-sql-built-by-fstring", ORANGE,
     "one Markdown note, under git"),
    ("2", "session A", "a guard is distilled",
     r"execute\s*\(\s*f[\"']", BLUE,
     "into a ledger, not your context"),
    ("3", "session B", "the repeat is flagged",
     "past mistake: never build SQL by f-string", PURPLE,
     "0 tokens spent until this line"),
    ("4", "session B", "the correction runs clean",
     "execute(\"... WHERE name = ?\", (name,))", GREEN,
     "no guard fires - not a blanket block"),
]

FW, FH = 960, 330
FRAME_W, FRAME_H = 204, 176

SEQ_DESC = (
    "Four frames. One: in session A the mistake is recorded as one Markdown note under git, "
    "named mistake-sql-built-by-fstring. Two: still in session A, a guard is distilled from it "
    "into a ledger rather than into your context. Three: in a later session B the repeat is "
    "flagged - the guard fires with a one-line warning, and nothing was spent on context until "
    "that line. Four: the corrected action, using a query parameter, runs clean; no guard "
    "fires, so this is a warning and not a blanket block. These are the same four beats that "
    "examples/guard_demo.py --check prints."
)


def frame(x: int, index: str, day: str, title: str, body: str, accent: str,
          note: str) -> list:
    y = 92
    out = [
        f'<rect x="{x}" y="{y}" width="{FRAME_W}" height="{FRAME_H}" rx="10" fill="{PAPER}" '
        f'stroke="{accent}" stroke-width="2"/>',
        f'<circle cx="{x + 24}" cy="{y + 26}" r="13" fill="{accent}"/>',
        f'<text x="{x + 24}" y="{y + 31}" font-size="13" font-weight="700" '
        f'text-anchor="middle" fill="{PAPER}">{index}</text>',
        f'<text x="{x + 46}" y="{y + 24}" font-size="11" font-weight="700" '
        f'letter-spacing="0.8" fill="{MUTED}">{esc(day.upper())}</text>',
        f'<text x="{x + 46}" y="{y + 39}" font-size="13" font-weight="600" '
        f'fill="{INK}">{esc(title)}</text>',
        f'<rect x="{x + 16}" y="{y + 56}" width="{FRAME_W - 32}" height="60" rx="6" '
        f'fill="{LANE_BG}"/>',
    ]
    # The payload, wrapped by hand: an SVG has no text flow, and a generated diagram must not
    # depend on a renderer's guess about where a line breaks.
    words, line, lines = esc(body).split(" "), "", []
    for word in words:
        if len(line) + len(word) + 1 > 24:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    for i, chunk in enumerate(lines[:3]):
        out.append(f'<text x="{x + 26}" y="{y + 76 + i * 16}" font-size="11" '
                   f'font-family="ui-monospace, SFMono-Regular, Consolas, monospace" '
                   f'fill="{INK}">{chunk}</text>')
    out.append(f'<text x="{x + 16}" y="{y + 140}" font-size="11" '
               f'fill="{MUTED}">{esc(note)}</text>')
    return out


def build_sequence() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FW} {FH}" width="{FW}" '
        f'height="{FH}" role="img" aria-labelledby="sttl sdsc" font-family="{FONT}">',
        '<title id="sttl">Never twice: the same mistake, recorded on Monday and stopped on '
        'Thursday, in four frames</title>',
        f'<desc id="sdsc">{esc(SEQ_DESC)}</desc>',
        '<defs><marker id="stip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="{RULE}"/>'
        '</marker></defs>',
        f'<rect width="{FW}" height="{FH}" fill="{PAPER}"/>',
        f'<text x="36" y="42" font-size="22" font-weight="700" fill="{INK}">Never twice</text>',
        f'<text x="36" y="64" font-size="13" fill="{MUTED}">The four beats '
        '<tspan font-family="ui-monospace, SFMono-Regular, Consolas, monospace" font-size="12">'
        'python examples/guard_demo.py --check</tspan> prints, with no model and no key.</text>',
    ]
    for i, (index, day, title, body, accent, note) in enumerate(FRAMES):
        x = 36 + i * (FRAME_W + 20)
        parts += frame(x, index, day, title, body, accent, note)
        if i < len(FRAMES) - 1:
            parts.append(f'<path d="M {x + FRAME_W + 2} 180 L {x + FRAME_W + 16} 180" '
                         f'stroke="{RULE}" stroke-width="2" marker-end="url(#stip)" '
                         f'fill="none"/>')
    parts += [
        f'<text x="36" y="304" font-size="11" fill="{MUTED}">'
        'Generated by tools/make_diagrams.py - edit the frames there, not this file.</text>',
        '</svg>',
    ]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- the scoreboard

# The campaign v3 numbers, one row per system, and both suites in every row: comparing a
# system's best suite against another's worst is exactly the table this one replaces. The bar
# carries the percentage; the exact counts and F1 sit beside it, because "100%" alone hides how
# many questions were asked.
SW, SH = 960, 520
TRACK = "#E6EAEE"
BAR_W, BAR_H = 150, 8
ROW_X, ROW_W, ROW_H, ROW_TOP, ROW_GAP = 40, 508, 66, 118, 72

# (system, badge, badge colour, S1 %, S1 text, S4 %, S4 text)
ROWS = [
    ("Nevertwice v3.0 (allmode A6.7)", "dual-tier", BLUE, 100,
     "S1: 100% (B) · 100% (K) · 100% (V)", 100, "S4: 100.0% (25/25, F1 53.0%)"),
    ("Mem0 (mem0ai 2.2.0)", "extract", MUTED, 60,
     "S1: 60% (B) · 40% (K)", 84, "S4: 84.0% (21/25, F1 49.8%)"),
    ("LangMem (0.0.30)", "episodic", MUTED, 40,
     "S1: 40% (B) · 20% (K)", 80, "S4: 80.0% (20/25, F1 23.5%)"),
    ("Graphify (0.9.93 expand / query)", "graph", MUTED, 20,
     "S1: 20% (B) · 20% (K)", 76, "S4: 76.0% expand / 44.0% query"),
    ("Claude Code Memory (2.1.304)", "files", MUTED, 40,
     "S1: 40% (B) · 40% (V)", 28, "S4: 28.0% (7/25, F1 9.2%)"),
]

# (accent, title, body lines). Wrapped by hand for the same reason `frame()` wraps: an SVG has no
# text flow, and the line breaks must not depend on a renderer's guess.
REASONS = [
    (ORANGE, "66.5x Fewer Writer Tokens · 10x Fewer Calls",
     ("S4 ingest uses 56.0K prompt tokens (44 total LLM",
      "calls) vs. Mem0's 3,725.9K tokens (444 calls).",
      "9.6x faster on S1 (117s vs. 1,126s/unit).")),
    (PURPLE, "0-Token Idle Guards & Modality Gate",
     ("Guards stay in a local JSON ledger at 0 context tokens",
      "until a repeat is flagged. [Confirmed fact] vs",
      "[Hypothetical] separated by query intent.")),
    (BLUE, "nevertwice-embed v2 (LoRA r=4 + Matryoshka)",
     ("Trained in 49s on RTX 5090: 1024d sit R@1 0.551 ->",
      "0.588 (MRR 0.693); 256d (1 KB/vec, 4x smaller) beats",
      "stock bge-m3 at 1024d.")),
]

SCOREBOARD_DESC = (
    "Two panels. Left: benchmark accuracy on S1 LongMemEval and S4 LoCoMo, five systems in five "
    "rows, each row carrying one bar per suite. Nevertwice v3.0 (allmode A6.7) scores S1 100 "
    "percent on point B, key and value, and S4 100.0 percent, 25 of 25 questions at F1 53.0. "
    "Mem0 2.2.0 scores S1 60 percent on B and 40 on K, and S4 84.0 percent, 21 of 25 at F1 "
    "49.8. LangMem 0.0.30 scores S1 40 percent on B and 20 on K, and S4 80.0 percent, 20 of 25 "
    "at F1 23.5. Graphify 0.9.93 scores S1 20 percent on B and 20 on K, and S4 76.0 percent "
    "with expand against 44.0 percent with query. Claude Code Memory 2.1.304 scores S1 40 "
    "percent on B and 40 on V, and S4 28.0 percent, 7 of 25 at F1 9.2. Right: three measured "
    "reasons. 66.5 times fewer writer tokens and 10 times fewer calls, 56.0 thousand prompt "
    "tokens and 44 calls against Mem0's 3,725.9 thousand tokens and 444 calls, and 9.6 times "
    "faster on S1 at 117 seconds against 1,126 seconds per unit. Zero-token idle guards and a "
    "modality gate. And nevertwice-embed v2, a LoRA rank-4 Matryoshka model trained in 49 "
    "seconds, whose 256-dimensional vectors beat stock bge-m3 at 1024 dimensions."
)


def progress(x: int, y: int, pct: int, colour: str) -> list:
    """A track and its filled part, so a row reads as a bar even where the fill is small."""
    return [
        f'<rect x="{x}" y="{y}" width="{BAR_W}" height="{BAR_H}" rx="4" fill="{TRACK}"/>',
        f'<rect x="{x}" y="{y}" width="{round(BAR_W * pct / 100)}" height="{BAR_H}" '
        f'rx="4" fill="{colour}"/>',
    ]


def score_row(y: int, spec: tuple) -> list:
    system, badge, accent, s1, s1_text, s4, s4_text = spec
    out = [
        f'<rect x="{ROW_X}" y="{y}" width="{ROW_W}" height="{ROW_H}" rx="8" fill="{PAPER}"/>',
        f'<text x="{ROW_X + 12}" y="{y + 19}" font-size="11" font-weight="600" '
        f'fill="{INK}">{esc(system)}</text>',
        f'<rect x="{ROW_X + 430}" y="{y + 6}" width="66" height="16" rx="8" '
        f'fill="{PAPER}" stroke="{accent}" stroke-width="1.2"/>',
        f'<text x="{ROW_X + 463}" y="{y + 17}" font-size="9" text-anchor="middle" '
        f'fill="{accent}">{esc(badge)}</text>',
        f'<text x="{ROW_X + 12}" y="{y + 39}" font-size="9" fill="{MUTED}">'
        'S1 (Point B / K / V)</text>',
        f'<text x="{ROW_X + 12}" y="{y + 57}" font-size="9" fill="{MUTED}">'
        'S4 LoCoMo (25 Qs, Cat 1-5)</text>',
    ]
    out += progress(ROW_X + 150, y + 31, s1, BLUE)
    out += progress(ROW_X + 150, y + 49, s4, GREEN)
    out += [
        f'<text x="{ROW_X + 308}" y="{y + 39}" font-size="9" fill="{INK}">'
        f'{esc(s1_text)}</text>',
        f'<text x="{ROW_X + 308}" y="{y + 57}" font-size="9" fill="{INK}">'
        f'{esc(s4_text)}</text>',
    ]
    return out


def build_scoreboard() -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}" width="{SW}" '
        f'height="{SH}" role="img" aria-labelledby="vttl vdsc" font-family="{FONT}">',
        '<title id="vttl">Nevertwice v3.0 scoreboard: accuracy against four memory systems on '
        'LongMemEval and LoCoMo, and the three measured reasons attention-native memory wins'
        '</title>',
        f'<desc id="vdsc">{esc(SCOREBOARD_DESC)}</desc>',
        f'<rect width="{SW}" height="{SH}" fill="{PAPER}"/>',
        f'<text x="36" y="40" font-size="22" font-weight="700" fill="{INK}">'
        'Nevertwice v3.0 — Attention-Native Memory vs. Competing Systems</text>',
        f'<text x="36" y="62" font-size="12" fill="{MUTED}">Campaign v3 (Stage D + A6.7): '
        'identical reader &amp; judge (deepseek-flash, temp=0), identical bge-m3 embedder, '
        '7,000-token cap</text>',
        f'<rect x="24" y="82" width="540" height="396" rx="12" fill="{LANE_BG}"/>',
        f'<text x="44" y="105" font-size="12" font-weight="700" letter-spacing="1.2" '
        f'fill="{INK}">BENCHMARK ACCURACY (S1 LONGMEMEVAL &amp; S4 LOCOMO)</text>',
        f'<rect x="580" y="82" width="356" height="396" rx="12" fill="{LANE_BG}"/>',
        f'<text x="596" y="105" font-size="12" font-weight="700" letter-spacing="1.2" '
        f'fill="{INK}">WHY ATTENTION-NATIVE WINS</text>',
    ]
    for i, spec in enumerate(ROWS):
        parts += score_row(ROW_TOP + i * ROW_GAP, spec)
    for i, (accent, title, lines) in enumerate(REASONS):
        y = 120 + i * 116
        parts += [
            f'<rect x="596" y="{y}" width="324" height="108" rx="8" fill="{PAPER}"/>',
            f'<rect x="596" y="{y}" width="6" height="108" rx="3" fill="{accent}"/>',
            f'<text x="612" y="{y + 26}" font-size="12" font-weight="600" fill="{INK}">'
            f'{esc(title)}</text>',
        ]
        for j, line in enumerate(lines):
            parts.append(f'<text x="612" y="{y + 48 + j * 16}" font-size="10" '
                         f'fill="{MUTED}">{esc(line)}</text>')
    parts += [
        f'<text x="36" y="502" font-size="11" fill="{MUTED}">'
        'Generated by tools/make_diagrams.py · Source: research/v3/REPORT-V3-STAGE-D-RU.md '
        '&amp; research/embed_universal/heldout/v2_3axis_eval.json</text>',
        '</svg>',
    ]
    return "\n".join(parts) + "\n"


DIAGRAMS = ((ARCH, build, "the architecture diagram"),
            (SEQ, build_sequence, "the four-frame sequence"),
            (SCOREBOARD, build_scoreboard, "the v3 head-to-head scoreboard"))


def main(argv: list) -> int:
    stale = []
    for path, builder, label in DIAGRAMS:
        svg = builder()
        if "--write" in argv:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8", newline="")
            print(f"wrote {path.relative_to(ROOT).as_posix()} ({len(svg)} bytes)")
            continue
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            print(f"  ERROR: {rel} does not exist; run with --write")
            stale.append(rel)
        elif path.read_text(encoding="utf-8") != svg:
            print(f"  STALE: {rel} does not match {label} described in "
                  f"{Path(__file__).name}; run with --write")
            stale.append(rel)
    if "--write" in argv:
        return 0
    if stale:
        return 1
    print(f"{len(DIAGRAMS)} diagrams match their descriptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
