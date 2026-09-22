#!/usr/bin/env python3
"""A pointer that indexes a list by position is an address only while the list keeps its shape.

Found by running the campaign rather than by reading it. The two supersession commands, executed
exactly as the register records them, produced an artifact with three arms instead of seven -
`--arms nevertwice,naive` cannot make the mem0 and zep arms, which had come from separate runs
pooled with `--with`, and the explicit command relies on that same pair being argparse's default.
`pairs[0]` therefore stopped being `(mem0, naive)` and became `(naive, nevertwice)`. The restore
rewrote `supersession.mem0_vs_naive.p_mcnemar` from 1.0 to 2.78e-17 - the naive-against-
nevertwice figure - while its statement still read "mem0 against naive on the same 60 cases".

Every check passed: the pointer resolved, the value matched what had just been written there,
freshness was OK, the battery was green. No guard in this repository could have caught it,
because all of them compare something written against something else written. This one was
caught by executing the recorded command and looking at what came out.

`tools/remeasure.py` now refuses a claim whose `X_vs_Y` id no longer matches the arms at its
index. That covers the pairs; it does not cover the rest of the class, and the rest is larger:

    abstention_ab.json      33   recall_sweep[4].threshold
    token_ab.json           27   longmem_token_ab.by_k[0].recall_at_k
    supersession_v1         20   pairs[N] and arms.zep.per_run_stale[0]
    supersession_implicit   20   the same
    guard_bench_v1.json      4   arms.guards_deterministic.curve[0].recall
    asof_v1.json             4   arms["nevertwice"].per_run[0]

The purest instance is not supersession but `abstention.recall.shipped_threshold`: it holds
**0.35** and points at `recall_sweep[4].threshold`. The claim's value IS the signature of the
position it addresses. Today `THRESHOLDS[4] == 0.35`; insert one threshold before index 4 and
all thirty-three abstention claims move silently to a neighbouring row, still resolving, still
matching. Self-confirmation with no campaign required.

So this suite does not forbid positional pointers - they are how these artifacts are shaped. It
pins the inventory, so that adding one is a deliberate act with a number attached rather than a
default nobody notices, and it keeps the measured facts beside the count.

    python tests/_test_positional_pointers.py
"""
import _env_guard  # noqa: F401
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


INDEXED = re.compile(r"\[\d+\]")

MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_claims = MANIFEST["claims"]
claims = list(_claims.values() if isinstance(_claims, dict) else _claims)
positional = [c for c in claims if INDEXED.search(c.get("pointer") or "")]

#: The inventory as measured on 2026-09-22. A new entry here is a decision: the artifact's list
#: must be one whose shape cannot change under any flag of the claim's own command.
KNOWN = {
    "research/results/abstention_ab.json": 33,
    "research/token_ab.json": 27,
    "research/results/supersession_v1.json": 20,
    "research/results/supersession_v1_implicit.json": 20,
    "research/results/guard_bench_v1.json": 4,
    "research/results/asof_v1.json": 4,
}

print("\n- the inventory is pinned, so a new positional pointer is a decision -")
by_artifact = collections.Counter(c.get("raw") for c in positional)
check("the total has not moved", len(positional) == sum(KNOWN.values()),
      f"{len(positional)} against {sum(KNOWN.values())}")
check("and no artifact has gained or lost one", dict(by_artifact) == KNOWN,
      f"{dict(by_artifact)}")

print("\n- none of them is live, which is why the corruption was recoverable -")
live = [c for c in positional if not (c.get("stale") or c.get("pending_remeasure"))]
check("every positional-pointer claim is withdrawn or pending", not live,
      ", ".join(c["id"] for c in live[:4]))

print("\n- the self-confirming case is named, not merely counted -")
by_id = {c["id"]: c for c in claims}
ab = by_id.get("abstention.recall.shipped_threshold")
check("abstention.recall.shipped_threshold is still in the register", ab is not None)
if ab:
    check("its value is the signature of the row it points at - that is the defect, stated",
          str(ab.get("value")) == "0.35" and "[4]" in (ab.get("pointer") or ""),
          f"value {ab.get('value')}, pointer {ab.get('pointer')}")

print("\n- and the pairs are guarded at the restore, which the rest are not -")
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402

art = {"pairs": [{"a": "naive", "b": "nevertwice", "p_mcnemar": 2.78e-17}]}
check("a moved pair is refused by name",
      bool(rm.pair_mismatch({"id": "supersession.mem0_vs_naive.p_mcnemar",
                             "pointer": "pairs[0].p_mcnemar"}, art)))
check("a sweep index has no such guard, and this suite says so rather than implying it",
      rm.pair_mismatch({"id": "abstention.recall.shipped_threshold",
                        "pointer": "recall_sweep[4].threshold"},
                       {"recall_sweep": [{"threshold": 0.9}] * 5}) is None)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
