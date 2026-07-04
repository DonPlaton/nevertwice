#!/usr/bin/env python3
"""Tests for research/retention_bench.py (3A.3). Pins the measurement + the honest-verdict logic
so the conclusion ("semantic recurrence is NOT worth adding to the cap") cannot silently flip back
to cherry-picking the best budget:

  • _topics() finds cross-session clusters (durable topics), drops singletons, sizes members;
  • _verdict() judges on the WORST budget delta AND hoarding — never the best delta alone;
  • PRIVACY: content fields (title/desc/prevention) are used only to build coreset tokens, never
    printed or written to the saved aggregate.

Stdlib only — synthetic note list, reads no vault.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "nevertwice"))
sys.path.insert(0, str(HERE.parent.parent / "research"))
import retention_bench as rb

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# ── 1. _topics: cross-session clusters only, with size map ───────────────
A1 = [1.0, 0.0, 0.0, 0.0]
A2 = [0.98, 0.02, 0.0, 0.0]      # ≈A1
B = [0.0, 0.0, 1.0, 0.0]         # orthogonal singleton
ns = [("2026-01-01-proj-mistake-a1", {"vec": A1}),
      ("2026-02-01-proj-mistake-a2", {"vec": A2}),    # different date -> cross-session
      ("2026-01-01-proj-mistake-b", {"vec": B})]
size, topics = rb._topics(ns)
ok(len(topics) == 1, f"_topics: one durable (cross-session) topic (got {len(topics)})")
ok(topics[0] == {"2026-01-01-proj-mistake-a1", "2026-02-01-proj-mistake-a2"},
   "_topics: the topic is exactly the two cross-date near-dups")
ok(size.get("2026-01-01-proj-mistake-a1") == 2 and "2026-01-01-proj-mistake-b" not in size,
   "_topics: size map covers cluster members, excludes singletons")

# a same-date near-dup pair is NOT a durable topic (no cross-session)
ns_same = [("2026-01-01-proj-mistake-x", {"vec": A1}),
           ("2026-01-01-proj-mistake-y", {"vec": A2})]
ok(rb._topics(ns_same)[1] == [], "_topics: same-date cluster is not a durable topic")

# ── 2. _verdict: worst-budget + hoarding, never cherry-picked best ───────
# real-data shape: helps loose (+.027), hurts tight (-.027), hoards (3.67 vs 2.32) -> not a win
ok(rb._verdict([-0.027, 0.027], 2.32, 3.67) == "not_win",
   "_verdict: real-data shape (mixed delta + hoarding) -> not_win")
# regression guard A: a positive best must NOT rescue a negative worst
ok(rb._verdict([-0.05, 0.20], 2.0, 2.0) == "not_win",
   "_verdict: negative worst delta -> not_win even with a large best (no cherry-pick)")
# regression guard B: hoarding alone sinks it even if both deltas are positive
ok(rb._verdict([0.02, 0.03], 2.0, 3.0) == "not_win",
   "_verdict: hoarding (red_sem > 1.25x red_cov) -> not_win despite positive deltas")
# a genuine robust win: positive worst AND no hoarding
ok(rb._verdict([0.02, 0.04], 2.0, 2.1) == "win",
   "_verdict: positive worst + no hoarding -> win")
# neutral: ~0 deltas, no hoarding
ok(rb._verdict([0.0, 0.002], 2.0, 2.0) == "neutral",
   "_verdict: near-zero deltas, no hoarding -> neutral")
ok(rb._verdict([], 0.0, 0.0) == "neutral", "_verdict: empty deltas -> neutral (no crash)")

# ── 3. PRIVACY: content fields never printed or saved ───────────────────
src = (HERE.parent.parent / "research" / "retention_bench.py").read_text(encoding="utf-8")
leak = []
for ln in src.splitlines():
    emits = "print(" in ln or "agg = {" in ln or '"topic_retention"' in ln or '.write_text' in ln
    if emits and any(f in ln for f in ("title", "desc", "prevention")):
        leak.append(ln.strip())
ok(not leak, f"privacy: no content field appears on any print/save line ({len(leak)} leaks)")
ok("_tokens" in src, "sanity: module builds coreset tokens (the only use of content fields)")

print(f"\nretention_bench: {P} passed, {F} failed")
sys.exit(1 if F else 0)
