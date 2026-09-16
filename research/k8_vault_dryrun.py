#!/usr/bin/env python3
"""K8 layer 3 price, measured before the code: how many `contested` pairs a week would a real
store produce under layer 1's rule?

A dry run of the write-time rule over a real vault, read-only. Every same-slug collision of the
last N weeks is found from what the store already holds: a later note minted on a slug that an
earlier note of the same project and type already carried (a `-2` sibling, or a retirement into
`Superseded/` - the `r` branch), and a same-day absorb that left its earlier text under
`## Previous statement` (the `d` branch). Each collision is then read as layer 1 would read it:
rule 2 (both sides carry literals and the earlier ones are all in the later block) absorbs; rule 4
(a later item without literals against a note with them) never absorbs; rule 1 (an explicit
`supersedes` / `contradicts` naming the title) cannot be told apart from a slug retirement on notes
written before J2b stamped `superseded_via`, so it is reported as unresolvable and the contested
count is the upper bound. What is left is a `contested` pair - one adjudication call at sleep time.

Nothing is written. The vault path is read from the argument; the engine's own parsers are used
on the text (pure functions, no store constant is touched).

    python research/k8_vault_dryrun.py "D:/Obsidian/Claude_Memory" --weeks 4 --out research/results/k8_vault_dryrun.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: E402,F401 - a read-only script must not let a store path into the engine
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

FOLDERS = {"pattern": "Patterns", "mistake": "Mistakes", "decision": "Decisions"}
_PREV_RE = re.compile(r"^## Previous statement\s*\n((?:- .*\n?)+)", re.M)


def _note(p: Path) -> dict | None:
    parsed = m.parse_typed_stem(re.sub(r"-\d+$", "", p.stem)) or m.parse_typed_stem(p.stem)
    if not parsed:
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = m._read_frontmatter(text)
    _, desc, prevention = m._parse_note_body(body.split("\n"))
    prev = _PREV_RE.search(body)
    return {"path": p, "stem": p.stem, "date": parsed["date"], "project": parsed["project"],
            "ntype": parsed["ntype"], "slug": parsed["slug"], "where": p.parent.name,
            "session": str(fm.get("session") or ""), "sources": fm.get("sources") or [],
            "supersedes": fm.get("supersedes") or [], "via": str(fm.get("superseded_via") or ""),
            "facts": m._facts_in(desc or ""), "has_desc": bool((desc or "").strip()),
            "previous": [ln[2:].strip() for ln in prev.group(1).splitlines() if ln.startswith("- ")] if prev else []}


def rule(old_facts: set, new_facts: set) -> str:
    """Layer 1 on one pair, rules 2 and 4 (rule 1 needs the extractor's item, see the docstring)."""
    if old_facts and not new_facts:
        return "sibling_rule4"           # a lesson without literals never absorbs a note with them
    if old_facts and new_facts and old_facts <= new_facts:
        return "absorb_rule2"            # the same fact restated or refined
    return "contested"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vault")
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    vault = Path(a.vault)
    cutoff = (dt.date.fromisoformat(a.today) - dt.timedelta(weeks=a.weeks)).isoformat()
    notes: list[dict] = []
    for ntype, folder in FOLDERS.items():
        d = vault / folder
        if not d.exists():
            continue
        for sub in (d, d / "Superseded", d / "Archive", d / "Archive" / "Superseded"):
            if sub.exists():
                for p in sub.glob("*.md"):
                    n = _note(p)
                    if n:
                        notes.append(n)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for n in notes:
        groups[(n["project"], n["ntype"], n["slug"])].append(n)
    events: list[dict] = []
    # (i) a later note on an existing slug - a `-2` sibling or a retirement (branch `r` / `d`-sibling)
    for key, ns in groups.items():
        ns.sort(key=lambda n: n["stem"])
        for i in range(1, len(ns)):
            new, old = ns[i], ns[i - 1]
            if new["date"] < cutoff:
                continue
            if new["session"] and new["session"] == old["session"]:
                continue                    # the same session re-encountering its own note
            events.append({"kind": "later_note", "branch": "d" if new["date"] == old["date"] else "r",
                           "project": key[0], "ntype": key[1], "slug": key[2],
                           "old": old["stem"], "new": new["stem"], "old_where": old["where"],
                           "old_via": old["via"], "rule": rule(old["facts"], new["facts"]),
                           "old_has_facts": bool(old["facts"]), "new_has_facts": bool(new["facts"])})
    # (ii) a same-day absorb: the earlier statement survives under `## Previous statement`
    for n in notes:
        if n["date"] < cutoff or not n["previous"] or n["where"] not in FOLDERS.values():
            continue
        # one event per note: the block keeps only the last absorb's fragments (description and
        # prevention), so a note absorbed twice still shows one earlier statement
        old_facts = set().union(*(m._facts_in(frag) for frag in n["previous"]))
        events.append({"kind": "absorbed", "branch": "d", "project": n["project"], "ntype": n["ntype"],
                       "slug": n["slug"], "old": f"{n['stem']} (previous statement)", "new": n["stem"],
                       "rule": rule(old_facts, n["facts"]),
                       "old_has_facts": bool(old_facts), "new_has_facts": bool(n["facts"])})
    by_rule = Counter(e["rule"] for e in events)
    facts_seen = {"old_has_facts": sum(1 for e in events if e["old_has_facts"]),
                  "new_has_facts": sum(1 for e in events if e["new_has_facts"]),
                  "notes_with_facts_block_in_window": sum(1 for n in notes if n["date"] >= cutoff and n["facts"]),
                  "notes_in_window": sum(1 for n in notes if n["date"] >= cutoff)}
    by_kind = Counter(e["kind"] for e in events)
    by_branch = Counter(e["branch"] for e in events)
    contested = [e for e in events if e["rule"] == "contested"]
    per_week = Counter()
    for e in contested:
        d = e["new"][:10]
        per_week[d[:4] + "-W" + str(dt.date.fromisoformat(d).isocalendar()[1]).zfill(2)] += 1
    out = {"vault": str(vault), "today": a.today, "weeks": a.weeks, "cutoff": cutoff,
           "notes_scanned": len(notes), "slug_groups": len(groups),
           "collision_events": len(events), "by_kind": dict(by_kind), "by_branch": dict(by_branch),
           "by_rule": dict(by_rule), "facts_seen": facts_seen,
           "contested_total": len(contested), "contested_per_week": round(len(contested) / a.weeks, 2),
           "contested_by_iso_week": dict(sorted(per_week.items())),
           "contested_by_project": dict(Counter(e["project"] for e in contested).most_common()),
           "rule1_note": "explicit supersedes/contradicts cannot be told from a slug retirement on notes "
                         "written before superseded_via was stamped; contested is the upper bound",
           "events": events}
    print(json.dumps({k: v for k, v in out.items() if k != "events"}, indent=1, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
