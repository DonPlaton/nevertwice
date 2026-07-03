#!/usr/bin/env python3
"""Anamnesis — `conflicts` and `digest`: the two pull-only review commands.

Most memory systems hide what they changed. Anamnesis resolves contradictions at
write time (M-2: a new fact that supersedes an old one retires the loser to
`<folder>/Superseded/`), so the *contradiction ledger* and the *supersession history*
are the same thing — and both are plain files. These two read-only commands surface
them for a human or an agent, with NO embedder and NO LLM:

  * conflicts — every fact the memory revised: old note -> the note that superseded it,
                newest first. The audit trail behind "the memory stays consistent."
  * digest    — a point-in-time rollup: what was added / revised in the last N days,
                per project and type, plus the store's most-connected entities.

    python -m anamnesis.digest                 # 7-day digest, all projects
    python -m anamnesis.digest --days=30 --project=myproj
    python -m anamnesis.digest --conflicts     # the supersession / contradiction ledger
    python -m anamnesis.digest --conflicts --json

Both are pure frontmatter scans (Superseded/ + live folders), so they work on a vault
with no vectors and never touch the network. The Python API mirrors them as
`anamnesis.api.conflicts()` / `anamnesis.api.digest()`, and the MCP server exposes both.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _live_notes(project=None):
    return m._iter_project_notes(project) if project else m._iter_all_notes()


def compute_conflicts(project=None, limit=50):
    """The supersession / contradiction ledger: each record is a fact that was revised.
    `{kind, project, ntype, old_stem, old_title, old_date, new_stem, new_title,
    new_date, resolved}`. `resolved` is False when the superseding note was itself later
    superseded (a still-evolving chain). Newest revision first. kind is always
    'superseded' today (M-2 turns a detected contradiction into a supersession at write
    time); the field is kept so an explicit unresolved-CONTRADICTS kind can join later."""
    sup = m._iter_superseded_notes(project)
    by_stem = {n["stem"]: n for n in (_live_notes(project) + sup) if "stem" in n}
    out = []
    for old in sup:
        succ_stem = (old.get("superseded_by") or "").strip()
        succ = by_stem.get(succ_stem)
        out.append({
            "kind": "superseded",
            "project": old.get("project", ""),
            "ntype": old.get("ntype", ""),
            "old_stem": old["stem"], "old_title": old.get("title", ""),
            "old_date": old.get("date", ""),
            "new_stem": succ_stem,
            "new_title": (succ or {}).get("title", ""),
            "new_date": (succ or {}).get("date", ""),
            # superseding note still live == this contradiction is settled; missing or
            # itself-superseded successor == the fact is still being revised.
            "resolved": bool(succ) and succ.get("status") != "superseded",
        })
    out.sort(key=lambda r: (r["new_date"] or r["old_date"], r["old_stem"]), reverse=True)
    return out[:limit] if limit else out


def _cutoff(days):
    return (datetime.now() - timedelta(days=max(0, days))).strftime("%Y-%m-%d")


def compute_digest(project=None, days=7, top_entities=8, recent_n=12):
    """A read-only rollup of the store. `{generated, window_days, totals, by_project,
    recent, changed, top_entities}`:
      * totals     — live notes, superseded notes, projects, and recent counts
      * by_project — {project: {total, added, superseded, by_type:{...}}}
      * recent     — the newest notes added inside the window (title/type/project/date)
      * changed    — facts revised inside the window (the conflicts ledger, windowed)
      * top_entities — the most-connected entities (entity graph), each {entity, notes}
    Dates are 'YYYY-MM-DD' so the window is a lexical compare — no parsing, no tz."""
    project = m.slug_project(project) if project else None
    live = _live_notes(project)
    sup = m._iter_superseded_notes(project)
    cutoff = _cutoff(days)
    recent = [n for n in live if n.get("date", "") >= cutoff]
    conflicts = compute_conflicts(project, limit=0)
    changed = [c for c in conflicts if (c["new_date"] or c["old_date"]) >= cutoff]

    by_project = {}
    for n in live:
        p = n.get("project", "?")
        d = by_project.setdefault(p, {"total": 0, "added": 0, "superseded": 0, "by_type": {}})
        d["total"] += 1
        d["by_type"][n["ntype"]] = d["by_type"].get(n["ntype"], 0) + 1
    for n in recent:
        by_project.setdefault(n.get("project", "?"),
                              {"total": 0, "added": 0, "superseded": 0, "by_type": {}})["added"] += 1
    for c in changed:
        by_project.setdefault(c.get("project", "?"),
                              {"total": 0, "added": 0, "superseded": 0, "by_type": {}})["superseded"] += 1

    try:
        eg = m.entity_graph(project, top_entities)         # {entity: {notes, links}}
        top = sorted(eg.items(), key=lambda kv: -kv[1].get("notes", 0))[:top_entities]
        top_ents = [{"entity": e, "notes": v.get("notes", 0)} for e, v in top]
    except Exception:
        top_ents = []

    recent_sorted = sorted(recent, key=lambda n: n.get("date", ""), reverse=True)[:recent_n]
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "window_days": days,
        "project": project or "(all)",
        "totals": {
            "live_notes": len(live), "superseded_notes": len(sup),
            "projects": len(by_project), "added_in_window": len(recent),
            "revised_in_window": len(changed),
        },
        "by_project": by_project,
        "recent": [{"date": n.get("date", ""), "ntype": n["ntype"],
                    "project": n.get("project", ""), "title": n.get("title", "")}
                   for n in recent_sorted],
        "changed": changed[:recent_n],
        "top_entities": top_ents,
    }


# ── CLI rendering ─────────────────────────────────────────────────────

def _print_conflicts(rows):
    if not rows:
        print("No revised facts on record — the memory has not had to supersede anything yet.")
        return
    print(f"CONTRADICTION / SUPERSESSION LEDGER — {len(rows)} revised fact(s), newest first\n")
    for r in rows:
        flag = "" if r["resolved"] else "  (still evolving)"
        print(f"• [{r['new_date'] or r['old_date']}] {r['project']} / {r['ntype']}{flag}")
        print(f"    was: {r['old_title']}  ({r['old_date']})")
        if r["new_stem"]:
            print(f"    now: {r['new_title']}  ({r['new_date']})")
        else:
            print("    now: (successor archived)")
    print(f"\nThese are the write-time contradictions M-2 caught and resolved into "
          f"supersessions — the audit trail under Superseded/.")


def _print_digest(d):
    t = d["totals"]
    print(f"ANAMNESIS DIGEST — {d['project']}   (last {d['window_days']} days)   {d['generated']}")
    print(f"  store: {t['live_notes']} live notes across {t['projects']} project(s), "
          f"{t['superseded_notes']} superseded")
    print(f"  window: +{t['added_in_window']} added, {t['revised_in_window']} revised\n")
    if d["by_project"]:
        print("  by project:")
        for p, v in sorted(d["by_project"].items(), key=lambda kv: -kv[1]["total"]):
            kinds = " ".join(f"{k}:{c}" for k, c in sorted(v["by_type"].items()))
            print(f"    {p:24} {v['total']:4} notes  (+{v['added']} / ~{v['superseded']} revised)  {kinds}")
    if d["recent"]:
        print("\n  recently added:")
        for n in d["recent"]:
            print(f"    [{n['date']}] {n['project']} / {n['ntype']}: {n['title']}")
    if d["changed"]:
        print("\n  recently revised (conflicts resolved):")
        for c in d["changed"]:
            print(f"    [{c['new_date']}] {c['project']}: {c['old_title']} → {c['new_title']}")
    if d["top_entities"]:
        ents = ", ".join(f"{e['entity']}({e['notes']})" for e in d["top_entities"])
        print(f"\n  most-connected entities: {ents}")


def main():
    argv = sys.argv[1:]
    project = m.argval(argv, "project")
    days = int(m.argval(argv, "days", "7"))
    limit = int(m.argval(argv, "limit", "50"))
    as_json = "--json" in argv
    if "--conflicts" in argv:
        rows = compute_conflicts(m.slug_project(project) if project else None, limit=limit)
        if as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            _print_conflicts(rows)
    else:
        d = compute_digest(project, days=days)
        if as_json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            _print_digest(d)


def conflicts_main():
    """Console-script entry for `anamnesis-conflicts` — same module, conflicts view."""
    if "--conflicts" not in sys.argv:
        sys.argv.append("--conflicts")
    main()


if __name__ == "__main__":
    main()
