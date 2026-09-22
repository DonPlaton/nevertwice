#!/usr/bin/env python3
"""Is the engine we measure the engine we run?

Every research page in this repo that says "measured on the owner's store" is a statement about
whichever build wrote that store - not about the build in the working tree. The two can drift, and
on 2026-09-19 they were found four weeks apart: the installed hook was the 2026-08-24 sync, so the
whole K-series (the facts channel, the sibling/contested mechanism, `_replacement_guard`) was absent
from the code that wrote every note in the vault. A read of the store was mistaken, twice and
independently, for a measurement of a guard that was never deployed.

This probe makes that drift visible instead of assumable. It reads - never writes - the hook path
the agent's own `settings.json` invokes, and reports which engine features it carries.

    python tools/check_installed_engine.py                      # human-readable
    python tools/check_installed_engine.py --json PATH          # artifact for a claim

Exit status is 0 when the installed hook carries every feature listed here, 1 when it lags, and 2
when the installed hook cannot be found or read. Nothing on the installed side is modified: this
tool opens files for reading and nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Feature -> the ledger entry that shipped it. A feature is "carried" when its marker name occurs
#: in the installed source at all; this is deliberately the weakest possible test, so a lag it
#: reports is certain rather than arguable.
FEATURES = {
    "_FACTS_MARK": "K6 - the verified-literal block appended to a description",
    "_note_facts": "J3/K6 - the per-note literal picker",
    "_harvest_literals": "K6 - the deterministic literal harvester",
    "_facts_source": "K6 - the session body the literal channel may quote",
    "_replacement_guard": "K8 - the veto on an unproven `replaces` verdict",
    "_same_replacement": "K8 - the write-time proof rules",
    "_same_fact_verdict": "K8 - the sleep-time judge's verdict shape",
    "CONTESTED_KEY": "K8 - the sibling stamp a pair gets instead of an absorb",
    "DISPUTED_KEY": "K8 - where a vetoed verdict is parked for a human",
    "pair_siblings": "K8 - both statements served, newest first",
    "EARLIER_MAX_CHARS": "K8 - the cap on the attached earlier statement",
}

SETTINGS = Path(os.path.expanduser("~")) / ".claude" / "settings.json"


def installed_hook_path(settings: Path = SETTINGS) -> Path | None:
    """The `memory_hook.py` the agent's own hooks invoke, or None. Read-only."""
    try:
        cfg = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    for groups in (cfg.get("hooks") or {}).values():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                cmd = hook.get("command") or ""
                mo = re.search(r"(\S*memory_hook\.py)", cmd)
                if mo:
                    return Path(mo.group(1).strip('"'))
    return None


def repo_engine_files() -> list[Path]:
    """Every file in the repository that carries engine code, loader and parts alike.

    The body is no longer one file. `_engine.py` is an ordered index that execs
    `_engine_config.py`, `_engine_text.py` and six more into `memory_hook`'s namespace, and a
    feature census that reads only the index would find an empty file and report every feature
    in this repository as missing from the install - the probe would say the install is a month
    behind for the wrong reason, which is worse than saying nothing. The list is read from the
    loader's own `ENGINE_PARTS` so a ninth part is covered the day it is added.
    """
    import ast

    pkg = ROOT / "nevertwice"
    loader = pkg / "_engine.py"
    files = [f for f in (loader, pkg / "memory_hook.py") if f.exists()]
    if not loader.exists():
        return files
    for node in ast.parse(loader.read_text(encoding="utf-8", errors="replace")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ENGINE_PARTS" for t in node.targets):
            files += [pkg / n for n in ast.literal_eval(node.value) if (pkg / n).exists()]
            break
    return files


def features_in(source: str) -> dict:
    """Which of FEATURES the given source carries. A marker present anywhere counts."""
    return {name: (name in source) for name in FEATURES}


def sync_commit(path: Path) -> dict:
    """The install directory's own last commit, when it is a git checkout. Read-only."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%H%x00%cI%x00%s"],
                             cwd=str(path.parent), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0 or not out.stdout.strip():
        return {}
    sha, when, subject = (out.stdout.strip().split("\0") + ["", "", ""])[:3]
    return {"commit": sha, "committed": when, "subject": subject}


def store_facts_census(vault: Path, since: str = "2026-09-16") -> dict:
    """How many live typed notes in a store carry a `[facts]` block. Read-only.

    This is the count that was twice mistaken for a measurement of `_replacement_guard`. It belongs
    beside the feature table, because only the two together say what it means: a store with no
    blocks, written by a build with no channel, is silent about the guard rather than damning of it.
    """
    mark = "  [facts] "
    typed = block = recent = recent_block = 0
    for folder in ("Mistakes", "Patterns", "Decisions"):
        base = vault / folder
        if not base.exists():
            continue
        for f in base.rglob("*.md"):
            if "Superseded" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.startswith("---"):
                continue
            head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
            if not re.search(r"^type:\s*\S", head, re.M):
                continue
            typed += 1
            has = mark in text
            block += bool(has)
            mo = re.search(r"^date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", head, re.M)
            if mo and mo.group(1) >= since:
                recent += 1
                recent_block += bool(has)
    return {"live_typed_notes": typed, "carrying_facts_block": block,
            "since": since, "typed_since": recent, "carrying_since": recent_block}


#: Defects the INSTALLED build carries. The feature table above answers "what does the owner not
#: have yet"; this answers the other half, "what is the owner running into today". A marker is a
#: fragment of the defective source: present in the installed file means the defect is live, absent
#: from the repo means the working tree has moved past it.
DEFECTS = {
    "dedup_window_evicts_the_day": {
        "marker": "other[-(max(0, TITLE_WINDOW - len(same))):]",
        "what": "the extractor's dedup window drops every same-day note once the day has "
                "TITLE_WINDOW of its own, because other[-0:] is the whole list",
    },
}


def defects_in(installed: str, repo: str) -> dict:
    out = {}
    for name, spec in DEFECTS.items():
        out[name] = {"in_installed": spec["marker"] in installed,
                     "still_in_repo": spec["marker"] in repo,
                     "what": spec["what"]}
    return out


def dedup_blindness(vault: Path, window: int = 40) -> dict:
    """How often the dedup window went blind on this store, counted two ways.

    `collect_existing_titles` globs the type folder FLAT, so `Superseded/` is out of its view: the
    live-only count is what the window sees today. But a note now in `Superseded/` was live on the
    day it was written, so the historical blindness - the thing that would have caused duplicates -
    is closer to the count that includes it. Both are reported because they answer different
    questions and only one of them is about today.
    """
    stem_re = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+?)-(mistake|pattern|decision)-(.+)$")
    rec = {"window": window}
    for label, include_retired in (("live", False), ("including_superseded", True)):
        groups: dict = {}
        for folder in ("Mistakes", "Patterns", "Decisions"):
            base = vault / folder
            if not base.exists():
                continue
            for f in base.rglob("*.md"):
                if not include_retired and "Superseded" in f.parts:
                    continue
                mo = stem_re.match(f.stem)
                if mo:
                    key = f"{mo.group(2)}|{mo.group(1)}|{mo.group(3)}"
                    groups[key] = groups.get(key, 0) + 1
        big = {k: v for k, v in groups.items() if v >= window}
        top = sorted(big.items(), key=lambda kv: -kv[1])[:5]
        rec[label] = {"groups": len(groups), "blind_groups": len(big),
                      "notes_in_blind_groups": sum(big.values()),
                      "largest": [{"group": k, "notes": v} for k, v in top]}
    return rec


def probe() -> dict:
    path = installed_hook_path()
    rec = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": str(SETTINGS),
        "installed_hook": str(path) if path else None,
        "repo_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                    capture_output=True, text=True).stdout.strip() or None,
    }
    if path is None:
        rec["status"] = "no-hook-configured"
        return rec
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        rec["status"] = "unreadable"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec

    rec["installed_bytes"] = len(source.encode("utf-8"))
    rec["installed_is_loader"] = "_engine" in source and len(source) < 20000
    rec.update(sync_commit(path))

    #: Read the INSTALLED side the way the repository side is read, or the census compares a
    #: 3 KB loader against ~460 KB of engine and calls every feature that lives in a part
    #: missing. The docstring of `repo_engine_files` says exactly this about the repository and
    #: the same sentence was never applied here: measured 2026-09-22, right after a successful
    #: `sync_install.py --apply`, this probe printed "installed hook LAGS the repo on 11
    #: feature(s)" while all eleven were physically present in `_engine_write.py`,
    #: `_engine_notes.py` and `_engine_recall.py` next to the loader. A check extended on one
    #: side of a seam is the split's own defect, found by the auditing session.
    installed_files = [path] + sorted(path.parent.glob("_engine*.py"))
    installed_src = "".join(f.read_text(encoding="utf-8", errors="replace")
                            for f in installed_files)
    rec["installed_parts"] = [f.name for f in installed_files]
    rec["installed_engine_bytes"] = len(installed_src.encode("utf-8"))

    carried = features_in(installed_src)
    repo_src = "".join(f.read_text(encoding="utf-8", errors="replace") for f in repo_engine_files())
    in_repo = features_in(repo_src)

    rec["features"] = {
        name: {"installed": carried[name], "repo": in_repo[name], "shipped_by": FEATURES[name]}
        for name in FEATURES
    }
    missing = sorted(n for n in FEATURES if in_repo[n] and not carried[n])
    rec["missing_from_installed"] = missing
    rec["defects"] = defects_in(source, repo_src)

    vault = os.environ.get("CLAUDE_MEMORY_VAULT") or str(Path(r"D:\Obsidian\Claude_Memory"))
    if Path(vault).exists():
        rec["store"] = {"vault": vault, **store_facts_census(Path(vault))}
        rec["dedup_blindness"] = dedup_blindness(Path(vault))
    rec["status"] = "in-sync" if not missing else "installed-lags-repo"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", metavar="PATH", help="also write the record as a JSON artifact")
    args = ap.parse_args()

    rec = probe()
    print(f"settings        : {rec['settings']}")
    print(f"installed hook  : {rec.get('installed_hook')}")
    if rec["status"] in ("no-hook-configured", "unreadable"):
        print(f"status          : {rec['status']} {rec.get('error', '')}")
        return 2
    print(f"installed bytes : {rec['installed_bytes']:,}"
          f"{'  (loader shim)' if rec['installed_is_loader'] else '  (monolith)'}")
    if rec.get("commit"):
        print(f"install commit  : {rec['commit'][:7]}  {rec.get('committed', '')}  {rec.get('subject', '')}")
    print(f"repo HEAD       : {(rec.get('repo_head') or '')[:7]}")
    print()
    for name, row in rec["features"].items():
        mark = "ok  " if row["installed"] else ("LAGS" if row["repo"] else "n/a ")
        print(f"  {mark} {name:22} {row['shipped_by']}")
    print()
    live = [(n, d) for n, d in (rec.get("defects") or {}).items() if d["in_installed"]]
    if live:
        print("defects the installed build carries:")
        for name, d in live:
            print(f"  LIVE {name}" + ("  (fixed in the working tree)" if not d["still_in_repo"]
                                       else "  (STILL in the working tree)"))
            print(f"       {d['what']}")
        print()
    db = rec.get("dedup_blindness")
    if db:
        for label in ("live", "including_superseded"):
            s = db[label]
            print(f"  dedup window, {label:20} {s['blind_groups']} of {s['groups']} "
                  f"project/day/type groups hold {db['window']}+ notes "
                  f"({s['notes_in_blind_groups']} notes)")
        big = db["including_superseded"]["largest"]
        if big:
            print(f"    largest: " + ", ".join(f"{x['notes']} ({x['group']})" for x in big[:3]))
        print()

    st = rec.get("store")
    if st:
        print(f"store           : {st['carrying_facts_block']} of {st['live_typed_notes']:,} live "
              f"typed notes carry a [facts] block "
              f"({st['carrying_since']} of {st['typed_since']} since {st['since']})")
        print()
    if rec["missing_from_installed"]:
        print(f"status: installed hook LAGS the repo on {len(rec['missing_from_installed'])} "
              f"feature(s): {', '.join(rec['missing_from_installed'])}")
        print("every 'measured on the owner's store' statement describes the INSTALLED build.")
    else:
        print("status: in sync - the store is written by the code this repo measures.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"\nartifact: {out}")
    return 1 if rec["missing_from_installed"] else 0


if __name__ == "__main__":
    sys.exit(main())
