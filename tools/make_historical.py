#!/usr/bin/env python3
"""Move a family of withdrawn claims to historical - the record of an engine or a mode that is
not the shipped one and will not be re-measured.

A withdrawn claim is *pending*: it names the command that restores it, and `remeasure.py --restore`
re-reads it at HEAD. A historical claim is not pending and never will be, because what it measured
no longer exists. The register already holds two such families (`*_k7`, `*_pre_j2b`), each kept with
a reason saying why it cannot be re-measured. Registering one at birth is `register_supersession.py
--historical`; this moves one that is already on the books.

The move is refused unless every claim in the family is uncited. That is the whole safety property:
a historical claim is not restorable, so if a page still prints its number, the page must change
first - the claim's status is not the thing to adjust.

    python tools/make_historical.py --prefix supersession_switch. --reason "..." --dry-run
    python tools/make_historical.py --prefix supersession_switch. --reason "..." --apply

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: A reason shorter than this is a label, not a reason. The point of the field is that a reader a
#: year from now can tell why the number was kept and why it cannot be refreshed.
MIN_REASON = 40


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def claims_of(manifest: dict) -> list:
    claims = manifest.get("claims")
    if isinstance(claims, dict):
        return list(claims.values())
    return list(claims or [])


def select(manifest: dict, prefixes: list) -> list:
    return [c for c in claims_of(manifest)
            if any(str(c.get("id", "")).startswith(p) for p in prefixes)]


def cited(claim: dict) -> list:
    """Where this claim's number is still printed - both the live list and the pending one."""
    return list(claim.get("cited_in") or []) + list(claim.get("cited_in_pending") or [])


def make_historical(claim: dict, reason: str, when: str) -> None:
    claim["stale"] = f"historical: {reason}"
    claim["withdrawn_on"] = claim.get("withdrawn_on") or when
    claim["historical_on"] = when
    claim.pop("pending_remeasure", None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prefix", action="append", required=True,
                    help="claim id prefix; repeatable")
    ap.add_argument("--reason", required=True,
                    help="why this family is kept and cannot be re-measured")
    ap.add_argument("--manifest", default=str(MANIFEST))
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true")
    grp.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if len(args.reason.strip()) < MIN_REASON:
        print(f"--reason must be a sentence ({MIN_REASON}+ chars): why this family is kept and "
              f"why it cannot be re-measured")
        return 2

    path = Path(args.manifest)
    manifest = load(path)
    chosen = select(manifest, args.prefix)
    if not chosen:
        print(f"no claim matches {args.prefix}")
        return 2

    live = [c for c in chosen if not c.get("stale")]
    if live:
        print(f"refusing: {len(live)} claim(s) are still live - withdraw them first, because a "
              f"historical claim is not restorable:")
        for c in live[:10]:
            print(f"  {c['id']}")
        return 1

    still_cited = [(c["id"], cited(c)) for c in chosen if cited(c)]
    if still_cited:
        print(f"refusing: {len(still_cited)} claim(s) are still printed somewhere. A historical "
              f"claim cannot be restored, so the PAGE changes first, not the claim:")
        for cid, where in still_cited[:10]:
            print(f"  {cid}  <- {', '.join(where)}")
        return 1

    when = date.today().isoformat()
    print(f"{len(chosen)} claim(s) matching {args.prefix}, all withdrawn and uncited:")
    for c in chosen[:6]:
        print(f"  {c['id']}")
    if len(chosen) > 6:
        print(f"  ... and {len(chosen) - 6} more")
    print(f"\nreason: historical: {args.reason}")

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0

    for c in chosen:
        make_historical(c, args.reason.strip(), when)
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwritten: {len(chosen)} claim(s) are historical as of {when}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
