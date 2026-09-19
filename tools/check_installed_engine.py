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

    carried = features_in(source)
    repo_src = "".join((ROOT / "nevertwice" / f).read_text(encoding="utf-8", errors="replace")
                       for f in ("_engine.py", "memory_hook.py")
                       if (ROOT / "nevertwice" / f).exists())
    in_repo = features_in(repo_src)

    rec["features"] = {
        name: {"installed": carried[name], "repo": in_repo[name], "shipped_by": FEATURES[name]}
        for name in FEATURES
    }
    missing = sorted(n for n in FEATURES if in_repo[n] and not carried[n])
    rec["missing_from_installed"] = missing

    vault = os.environ.get("CLAUDE_MEMORY_VAULT") or str(Path(r"D:\Obsidian\Claude_Memory"))
    if Path(vault).exists():
        rec["store"] = {"vault": vault, **store_facts_census(Path(vault))}
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
        out.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nartifact: {out}")
    return 1 if rec["missing_from_installed"] else 0


if __name__ == "__main__":
    sys.exit(main())
