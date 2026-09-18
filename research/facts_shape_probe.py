"""Does the facts block decide "replaces" vs "separate" with no model at all?

The TRIZ pass says the highest-ideality answer is a verdict the pair gives about itself from data
already in it: two verified literals of the SAME SHAPE with DIFFERENT VALUES are a replacement by
construction ("timeout is 30 seconds" / "timeout is 5 seconds"), and two literals of different
shapes are separate facts ("upload limit 10 MB" / "rate limit 100 a second").

This scores that rule on the pairs K8 already recorded, before a line of it goes into the engine.
The number that decides is not accuracy: a false `replaces` retires a still-true fact, which is the
one thing the project measures at 0.000. So `replaces` precision must be 1.000, and recall is what
tells us how much of the judge's work the rule can take away.

    python research/facts_shape_probe.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path("D:/Coding/nevertwice")
FACTS_MARK = "  [facts] "
NUM = re.compile(r"\d+(?:[.,]\d+)*")
WORD = re.compile(r"[a-z0-9]+")
#: words that carry no topic - the shape key must not be equal just because both say "the"
STOP = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "on", "at", "for", "and",
        "or", "it", "its", "this", "that", "be", "been", "has", "have", "with", "as", "by", "from",
        "we", "our", "us", "now", "then", "set", "uses", "use", "used", "using"}


def facts_of(desc: str) -> list[str]:
    """The verified literals the extractor named, as the note stores them."""
    if FACTS_MARK not in (desc or ""):
        return []
    tail = desc.split(FACTS_MARK, 1)[1]
    return [f.strip() for f in tail.split(" · ") if f.strip()]


def shape(lit: str) -> tuple[str, tuple[str, ...]]:
    """(topic key with every number blanked, the numbers themselves)."""
    nums = tuple(NUM.findall(lit))
    blanked = NUM.sub("<n>", lit.lower())
    key = tuple(w for w in WORD.findall(blanked.replace("<n>", " NUM ")) if w not in STOP)
    return " ".join(key), nums


def verdict(old_desc: str, new_desc: str) -> str | None:
    """`replaces`, `separate`, or None when the literals say nothing."""
    old, new = facts_of(old_desc), facts_of(new_desc)
    if not old or not new:
        return None
    o = {shape(x)[0]: shape(x)[1] for x in old}
    n = {shape(x)[0]: shape(x)[1] for x in new}
    common = [k for k in o if k in n and k]
    if not common:
        #: no shared topic among the verified literals - the pair settles different things
        return "separate" if (o and n) else None
    #: a shared topic whose number moved is a replacement by construction
    if any(o[k] != n[k] for k in common):
        return "replaces"
    #: same topic, same value: the new note restates the old one. Not a replacement of a value,
    #: and not a separate fact either - the rule declines rather than guessing.
    return None


def score(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    pairs = d.get("pairs") or []
    tally = Counter()
    misses = []
    for p in pairs:
        truth = p.get("truth")
        got = verdict(p.get("old_desc") or "", p.get("new_desc") or "")
        tally[(truth, got)] += 1
        if got == "replaces" and truth != "replaces":
            misses.append(p)
    tp = tally[("replaces", "replaces")]
    fp = sum(v for (t, g), v in tally.items() if g == "replaces" and t != "replaces")
    fn = sum(v for (t, g), v in tally.items() if t == "replaces" and g != "replaces")
    tn = sum(v for (t, g), v in tally.items() if g == "separate" and t == "separate")
    fs = sum(v for (t, g), v in tally.items() if g == "separate" and t != "separate")
    silent = sum(v for (t, g), v in tally.items() if g is None)
    return {"n": len(pairs), "replaces_tp": tp, "replaces_fp": fp, "replaces_fn": fn,
            "separate_tn": tn, "separate_fp": fs, "declined": silent,
            "replaces_precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "replaces_recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "separate_precision": round(tn / (tn + fs), 4) if tn + fs else None,
            "truth_counts": dict(Counter(p.get("truth") for p in pairs)),
            "misses": [{"case": p.get("case"), "truth": p.get("truth"),
                        "old": facts_of(p.get("old_desc") or ""),
                        "new": facts_of(p.get("new_desc") or "")} for p in misses[:6]]}


for name in ("k8_collisions_explicit", "k8_collisions_implicit", "k8_collisions_asof"):
    p = ROOT / "research/results" / f"{name}.json"
    if not p.exists():
        continue
    s = score(p)
    print(f"\n=== {name} ===")
    print(f"  pairs {s['n']}   truth {s['truth_counts']}")
    print(f"  replaces: precision {s['replaces_precision']}  recall {s['replaces_recall']}"
          f"   (tp {s['replaces_tp']} fp {s['replaces_fp']} fn {s['replaces_fn']})")
    print(f"  separate: precision {s['separate_precision']}   (tn {s['separate_tn']} fp {s['separate_fp']})")
    print(f"  declined (no verdict): {s['declined']}")
    for mss in s["misses"]:
        print(f"    FALSE REPLACES  {mss['case']}  truth={mss['truth']}")
        print(f"       old {mss['old']}")
        print(f"       new {mss['new']}")
