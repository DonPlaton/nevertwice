#!/usr/bin/env python3
"""Tests for research/real_trace_bench.py (3A.2). Pins the measurement logic and the
privacy invariant so the honest findings cannot silently rot:

  • _date() extracts the date from a typed stem and degrades to "?" - this drives the
    cross-session (>1 date) test that separates genuine recurrence from same-session dups;
  • _clusters() groups note vectors at a cosine threshold and drops singletons, on LIST
    vectors (where m.cosine is correct - the numpy-array trap does not apply to cached lists);
  • cross-session counting: a cluster spanning >1 date counts; a same-date cluster does not;
  • PRIVACY (source-level regression): the module never reads a note's content fields
    (title/desc/prevention) - it may only touch vectors, project, recurrence. If a future
    edit starts printing content, this fails.

Stdlib only - builds a synthetic note list in-process, reads no vault.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "nevertwice"))
sys.path.insert(0, str(HERE.parent.parent / "research"))
import memory_hook as m
import real_trace_bench as rt

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# ── 1. _date ────────────────────────────────────────────────────────────
ok(rt._date("2026-01-02-proj-mistake-foo") == "2026-01-02", "_date extracts date from typed stem")
ok(rt._date("not-a-typed-stem") == "?", "_date degrades to '?' on an unparseable stem")

# ── 2. _clusters on LIST vectors (m.cosine is correct on lists) ──────────
# a1≈a2 (cosine≈0.9998), b orthogonal to both; c2 ~0.53 from c1 (a threshold-boundary pair).
A1 = [1.0, 0.0, 0.0, 0.0]
A2 = [0.98, 0.02, 0.0, 0.0]
B = [0.0, 0.0, 1.0, 0.0]
C1 = [1.0, 0.0, 0.0, 0.0]
C2 = [1.0, 1.6, 0.0, 0.0]
ok(m.cosine(A1, A2) >= 0.99, f"sanity: A1·A2 cosine high ({m.cosine(A1, A2):.3f})")
ok(abs(m.cosine(C1, C2) - 0.53) < 0.02, f"sanity: C1·C2 cosine ≈0.53 ({m.cosine(C1, C2):.3f})")
ok(m.cosine(A1, B) == 0.0, "sanity: A1·B orthogonal (cosine 0)")

notes = [("2026-01-01-proj-mistake-a1", {"vec": A1}),
         ("2026-02-01-proj-mistake-a2", {"vec": A2}),   # different date - cross-session
         ("2026-01-01-proj-mistake-b", {"vec": B})]      # orthogonal - must stay a singleton
groups = rt._clusters(notes, 0.55)
ok(len(groups) == 1, f"_clusters: one multi-member group at thr=0.55 (got {len(groups)})")
ok(len(groups[0]) == 2 and all("a" in s.rsplit('-', 1)[-1] for s in groups[0]),
   "_clusters: the group is exactly the two near-dup notes (singleton dropped)")

# threshold sensitivity: the ~0.53 pair clusters at 0.50 but NOT at 0.55
boundary = [("2026-01-01-proj-mistake-c1", {"vec": C1}),
            ("2026-02-01-proj-mistake-c2", {"vec": C2})]
ok(len(rt._clusters(boundary, 0.50)) == 1, "_clusters: 0.53 pair clusters at thr=0.50")
ok(len(rt._clusters(boundary, 0.55)) == 0, "_clusters: 0.53 pair does NOT cluster at thr=0.55")

# ── 3. cross-session detection (the >1-date rule the headline rests on) ──
g_cross = [s for s, _ in notes[:2]]            # 2026-01-01 vs 2026-02-01
ok(len({rt._date(x) for x in g_cross}) > 1, "cross-session: differing dates counted as recurrence")
g_same = ["2026-01-01-proj-mistake-x", "2026-01-01-proj-mistake-y"]
ok(len({rt._date(x) for x in g_same}) == 1, "same-session: identical dates NOT counted")

# ── 4. PRIVACY - module never touches note content fields ────────────────
src = (HERE.parent.parent / "research" / "real_trace_bench.py").read_text(encoding="utf-8")
for field in ('title', 'desc', 'prevention'):
    ok(f'"{field}"' not in src and f"'{field}'" not in src,
       f"privacy: module never reads the '{field}' content field")
# it may legitimately read only vec / project / recurrence
ok('"vec"' in src and '"project"' in src, "sanity: module reads vectors + project (aggregate keys)")

print(f"\nreal_trace_bench: {P} passed, {F} failed")
sys.exit(1 if F else 0)
