#!/usr/bin/env python3
"""Find, and on request repair, the accumulated damage a fixed defect left behind.

The engine defects that produced these are fixed, so nothing new is created -- but a fix
does not rewrite the past. This finds what is already in the store and, only when told,
repairs it.

Two problems, both measured in the 2026-09 review:

* **twins** -- a basename that exists live AND in Superseded/. The vault then asserts both
  "retired, use the replacement" and "current" for one name, and every consumer keyed on
  stem picks one arbitrarily: recall's filter, the embedding index, memory_search, and
  Obsidian's own link resolution. Five collided that way, on the very lesson one project
  kept re-learning. Fixed forward by 4d634a7; the existing pairs remain.
* **foreign tags** -- a note carrying the signature tag of a DIFFERENT project, from when
  the extraction prompt was grounded on the whole vault. Nine notes in an ML project were
  re-tagged `quantum_computing`, losing their own tags, so they stopped matching their own
  queries while surfacing for someone else's. Fixed forward by 2bc070e.

Like `sync_install.py`, and for the same reason: it writes NOTHING without `--apply`, it
copies every file it will touch into a timestamped backup first, and it prints the command
that undoes the operation. A store is not a place to be brave in.

    python tools/repair_vault.py                     # what is damaged
    python tools/repair_vault.py --apply             # back up, then repair
    python tools/repair_vault.py --only twins        # one check
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path

TYPED = ("Patterns", "Mistakes", "Decisions")
#: A tag this common in ONE project and this rare elsewhere is that project's signature.
SIGNATURE_SHARE = 0.90
SIGNATURE_MIN = 8


def _vault(override: str | None) -> Path:
    return Path(override or os.environ.get("NEVERTWICE_VAULT")
                or (Path.home() / "Obsidian" / "Claude_Memory"))


def _project_of(stem: str) -> str:
    m = re.match(r"^\d{4}-\d{2}-\d{2}-([a-z0-9_]+)-(pattern|mistake|decision)-", stem)
    return m.group(1) if m else ""


def find_twins(vault: Path) -> list[tuple[Path, Path]]:
    out = []
    for folder in TYPED:
        d = vault / folder
        sup = d / "Superseded"
        if not d.is_dir() or not sup.is_dir():
            continue
        retired = {p.name for p in sup.glob("*.md")}
        out += [(p, sup / p.name) for p in sorted(d.glob("*.md")) if p.name in retired]
    return out


def _tag_lines(text: str) -> list[str]:
    return re.findall(r"^tags:\s*\[(.*)\]\s*$", text, flags=re.M)


def find_foreign_tags(vault: Path) -> list[tuple[Path, str, str]]:
    """(note, tag, owning project) for tags that belong to another project."""
    per_project: dict[str, Counter] = {}
    notes: list[tuple[Path, str, list[str]]] = []
    for folder in TYPED:
        for p in sorted((vault / folder).glob("*.md")) if (vault / folder).is_dir() else []:
            proj = _project_of(p.stem)
            if not proj:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tags = [t.strip().strip('"\'') for line in _tag_lines(text)
                    for t in line.split(",") if t.strip()]
            notes.append((p, proj, tags))
            per_project.setdefault(proj, Counter()).update(tags)

    totals = Counter()
    for c in per_project.values():
        totals.update(c)
    signature = {}
    for tag, total in totals.items():
        if total < SIGNATURE_MIN:
            continue
        owner, n = max(((pr, c[tag]) for pr, c in per_project.items()), key=lambda x: x[1])
        if n / total >= SIGNATURE_SHARE:
            signature[tag] = owner

    return [(p, t, signature[t]) for p, proj, tags in notes for t in tags
            if t in signature and signature[t] != proj]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault")
    ap.add_argument("--apply", action="store_true",
                    help="back up and repair; without it nothing is written")
    ap.add_argument("--only", choices=("twins", "tags"))
    args = ap.parse_args(argv)

    vault = _vault(args.vault)
    if not (vault / "Patterns").is_dir():
        print(f"refusing: {vault} does not look like a store (no Patterns/)")
        return 2
    print(f"store {vault}\n")

    twins = find_twins(vault) if args.only in (None, "twins") else []
    foreign = find_foreign_tags(vault) if args.only in (None, "tags") else []

    for live, retired in twins:
        print(f"  twin      {live.name}")
    for p, tag, owner in foreign[:40]:
        print(f"  foreign   {p.name}  carries '{tag}' (belongs to {owner})")
    if len(foreign) > 40:
        print(f"  ... and {len(foreign) - 40} more foreign tags")
    print(f"\n{len(twins)} twin(s), {len(foreign)} foreign tag(s).")
    if not twins and not foreign:
        return 0

    if not args.apply:
        print("\nDRY RUN - nothing was written. Re-run with --apply to back up and repair.")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = vault / f".repair-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    touched = {live for live, _ in twins} | {p for p, _, _ in foreign}
    for p in touched:
        shutil.copy2(p, backup / p.name)
    print(f"\nbacked up {len(touched)} file(s) to {backup}")
    print(f"UNDO: copy every file from {backup} back to its folder")

    # A twin is repaired by RENAMING the live note, never by deleting either: the retired
    # copy is the record of what was replaced, and the live one is somebody's current
    # truth. Which is which is a judgement this tool does not make.
    for live, _retired in twins:
        new = live.with_name(f"{live.stem}-live{live.suffix}")
        i = 2
        while new.exists():
            new = live.with_name(f"{live.stem}-live{i}{live.suffix}")
            i += 1
        live.rename(new)
        print(f"  renamed   {live.name} -> {new.name}")

    by_note: dict[Path, set[str]] = {}
    for p, tag, _owner in foreign:
        by_note.setdefault(p, set()).add(tag)
    for p, tags in by_note.items():
        text = p.read_text(encoding="utf-8", errors="replace")
        # REBUILD the list rather than splicing it. Deleting one element with a regex
        # leaves a trailing comma - `tags: ["a", "b", ]` - which the current parser
        # tolerates and a stricter one will not. Found by reading the output of this
        # tool's own first real run.
        def _drop(mo, _tags=tags):
            kept = [t.strip() for t in mo.group(1).split(",")
                    if t.strip() and t.strip().strip('"\'') not in _tags]
            return "tags: [" + ", ".join(kept) + "]"
        text = re.sub(r"^tags:\s*\[(.*)\]\s*$", _drop, text, flags=re.M)
        for tag in tags:
            text = re.sub(rf"^#{re.escape(tag)}\b\s*", "", text, flags=re.M)
        print(f"  untagged  {p.name}  ({', '.join(sorted(tags))})")

    print("\nRe-run without --apply to confirm the store is clean, then rebuild the index:")
    print("  python -m nevertwice.embed_index --rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
