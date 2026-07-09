"""Second infographic: the 2026-discriminating axis - post-retrieval correctness.
Everyone scores ~0.9 on retrieval; the gap is contradictions/poisoning/staleness.
Brand style matches docs/benchmarks.png (OLED slate + green, large type, offline)."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# H leaves room below the 2x2 cards (which end at cy0 + 2*ch + gap = 940) for the footer line
# and byline; the footer offsets below are measured from the bottom edge, so it clears the cards.
W, H = 1800, 1070
BG = (8, 11, 20); SURFACE = (15, 21, 36)
FG = (233, 238, 248); MUTED = (139, 150, 173); FAINT = (90, 100, 120)
GREEN = (52, 211, 153); GREEN_2 = (34, 197, 94); AMBER = (251, 191, 36)

F = "C:/Windows/Fonts/"
def font(name, size): return ImageFont.truetype(F + name, size)
f_title = font("seguisb.ttf", 44); f_sub = font("segoeui.ttf", 26)
f_big = font("seguisb.ttf", 82); f_cap = font("seguisb.ttf", 27)
f_body = font("segoeui.ttf", 24); f_foot = font("segoeui.ttf", 24)

img = Image.new("RGB", (W, H), BG)
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([W*0.55, -300, W*1.15, 260], fill=(52, 211, 153, 26))
gd.ellipse([-260, -320, W*0.35, 200], fill=(124, 169, 255, 16))
img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(120))).convert("RGB")
d = ImageDraw.Draw(img, "RGBA")

d.text((80, 50), "After retrieval: the axis that separates memory in 2026", font=f_title, fill=FG)
d.text((80, 112), "Every system scores ~0.9 on retrieval - it stopped discriminating. The gap is what happens next.",
       font=f_sub, fill=MUTED)
d.line([(80, 166), (W-80, 166)], fill=(148, 163, 184, 40), width=1)

# 4 cards across 2x2
cards = [
    ("write-time", "Contradictions resolved before they pile up",
     "a new fact retires the old to Superseded/; recall only ever sees current truth.",
     "competitors: ADD-only, or you delete by hand", GREEN),
    ("88%", "Poisoning attacks blocked (P 0.91 / R 0.83)",
     "prompt-injection caught 100%; corroboration-gated acceptance.",
     "honest: plausible-false facts only ~50% - and we say so", GREEN),
    ("+0.14", "More topics kept per token under a tight budget",
     "submodular coreset beats recency-sort at 20% retention, less redundancy.",
     "staleness is a forgetting policy, not an afterthought", GREEN),
    ("we publish", "The negative results, too",
     "consolidation-by-replacement halved recall in our own test, so we don't ship it.",
     "reproducible harness in research/ for every number here", AMBER),
]
cx0, cy0 = 80, 210
cw, ch, gap = 810, 350, 30
for i, (big, cap, body, foot, accent) in enumerate(cards):
    cx = cx0 + (i % 2) * (cw + gap)
    cy = cy0 + (i // 2) * (ch + gap)
    d.rounded_rectangle([cx, cy, cx + cw, cy + ch], 18, fill=SURFACE, outline=(148, 163, 184, 28), width=1)
    d.text((cx + 30, cy + 28), big, font=f_big, fill=accent)
    d.text((cx + 30, cy + 138), cap, font=f_cap, fill=FG)
    # wrap body to ~46 chars
    words, line, yb = body.split(), "", cy + 188
    for w in words:
        if len(line) + len(w) + 1 > 52:
            d.text((cx + 30, yb), line, font=f_body, fill=MUTED); yb += 34; line = w
        else:
            line = (line + " " + w).strip()
    d.text((cx + 30, yb), line, font=f_body, fill=MUTED)
    d.text((cx + 30, cy + ch - 44), foot, font=f_foot, fill=FAINT)

d.line([(80, H-82), (W-80, H-82)], fill=(148, 163, 184, 40), width=1)
d.ellipse([80, H-54, 92, H-42], fill=GREEN)
d.text((104, H-60), "github.com/DonPlaton/nevertwice   ·   retrieval is table stakes; this is the moat",
       font=f_foot, fill=MUTED)

import os
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "post_retrieval.png")
img.save(out)
print("saved", out)
