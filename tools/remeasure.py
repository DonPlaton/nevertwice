#!/usr/bin/env python3
"""Withdraw the claims a code change invalidated, and restore them from re-measured artifacts.

`tools/check_freshness.py` fails when a published number was produced by code that has since
moved. The review of 2026-09-05 changed the engine, so every claim whose closure reaches it
became stale at once: 101 of 115. The rule is right - a number measured on the old engine says
nothing about the new one - and the honest state between the change and the re-run is that
those numbers are withdrawn from the documents, with the gate that blocks their re-measurement
named on each. Doing that by hand for a hundred claims is how a withdrawal gets skipped, so
this tool does both halves:

    python tools/remeasure.py --withdraw --reason "..."     # mark the affected claims stale
    python tools/remeasure.py --pending                      # what is waiting, by command
    python tools/remeasure.py --restore [--select PREFIX,..] # re-read the artifacts at HEAD

`--withdraw` selects the live claims whose `produced_by` includes a file changed since their
`commit` (the same question the freshness check asks; `--touching auto` asks it of the working
tree instead, so the change and its withdrawal can land in one commit), moves `cited_in` to
`cited_in_pending`
so the documents can be re-rendered without the numbers, and stamps `stale` and `withdrawn_on`.

`--restore` takes every claim marked `pending_remeasure` whose raw artifact is readable, refuses
if any file in its closure is modified in the working tree (a number restamped on a dirty tree
would name a commit that never produced it), re-reads the value through the claim's pointer,
recomputes a Wilson interval where one was declared, rewrites the printed forms in the format the
claim already used, patches the statement where it quoted the old form, restamps `commit` to
HEAD, and puts `cited_in` back. Claims whose artifact is missing or older than the code commit
stay withdrawn - `--pending` lists them.

After a restore: `python tools/render_claims.py --write`, then the prose around the tables,
then `python -m pytest -q`. The tool never edits a document; the numbers in prose are yours.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

sys.path.insert(0, str(ROOT / "tools"))
import check_freshness as cf  # noqa: E402


def _git(*args: str) -> str:
    r = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def load(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(manifest: dict, path: Path = MANIFEST) -> None:
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ── pointer / value plumbing ───────────────────────────────────────────────────

def resolve(data, pointer: str):
    """Walk a dotted pointer with [index] segments - the manifest test's own walker."""
    node = data
    for part in pointer.split("."):
        while part.endswith("]"):
            part, _, idx = part[:-1].rpartition("[")
            if part:
                node = node[part]
            node = node[int(idx)]
            part = ""
        if part:
            node = node[part]
    return node


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def reformat(old: str, value) -> str | None:
    """`value` printed the way `old` was. None when the shape is not one this tool knows.

    The shapes come from the manifest itself: `0.802`, `89`, `89 ms`, `19,206`, `81%`,
    `+0.14`, `31×`, `1.1 x 10^-16`. A p-value or a signed delta keeps its own convention.
    """
    old = old.strip()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)", old)
    if m:
        return f"{v:.{len(m.group(2))}f}"
    m = re.fullmatch(r"([+−-])(\d+)\.(\d+)", old)
    if m:
        sign = "+" if v >= 0 else "-"
        return f"{sign}{abs(v):.{len(m.group(3))}f}"
    if re.fullmatch(r"\d{1,3}(,\d{3})+", old):
        return f"{int(round(v)):,}"
    m = re.fullmatch(r"(\d+)(\.(\d+))?%", old)
    if m:
        dec = len(m.group(3)) if m.group(3) else 0
        return f"{v * 100:.{dec}f}%"
    m = re.fullmatch(r"(\d+) ms", old)
    if m:
        return f"{int(round(v))} ms"
    m = re.fullmatch(r"(\d+)([x×])", old)
    if m:
        return f"{int(round(v))}{m.group(2)}"
    if re.fullmatch(r"\d+", old):
        return f"{int(round(v))}"
    m = re.fullmatch(r"(\d+\.\d+) x 10\^(-?\d+)", old)
    if m:
        s = f"{v:.1e}"                                    # 1.1e-16
        mant, exp = s.split("e")
        return f"{mant} x 10^{int(exp)}"
    return None


# ── the two halves ─────────────────────────────────────────────────────────────

def affected(manifest: dict, touching: set[str] | None = None) -> list[dict]:
    """Live claims whose closure moved after they were measured.

    With `touching` (a set of repo-relative paths, e.g. the working tree's modified files), a
    claim is affected when its `produced_by` meets the set - the question the freshness check
    will ask once those files are committed, answered before the commit so the withdrawal
    and the change land together. Without it, the freshness check's own failures are used.
    """
    live = [c for c in manifest["claims"] if not c.get("stale")]
    if touching is not None:
        norm = {t.replace("\\", "/") for t in touching}
        return [c for c in live if norm & set(c.get("produced_by") or [])]
    failures, _declared, _cited = cf.check(manifest, cf.Git())
    return [f["claim"] for f in failures
            if isinstance(f.get("claim"), dict) and not f["claim"].get("stale")]


def withdraw(manifest: dict, reason: str, today: str, select: set[str] | None = None,
             touching: set[str] | None = None) -> list[str]:
    done = []
    for c in affected(manifest, touching):
        if select and not any(c["id"].startswith(p) for p in select):
            continue
        c["cited_in_pending"] = list(c.get("cited_in") or [])
        c["cited_in"] = []
        c["stale"] = reason
        c["withdrawn_on"] = today
        c["pending_remeasure"] = True
        done.append(c["id"])
    return done


def _dirty_files() -> set[str]:
    out = set()
    for line in _git("status", "--porcelain").splitlines():
        if len(line) > 3:
            out.add(line[3:].strip().replace("\\", "/"))
    return out


def restore(manifest: dict, select: set[str] | None = None, head: str | None = None,
            dry_run: bool = False) -> tuple[list[str], list[str], list[str]]:
    """Returns (restored ids, ids left pending with a reason, statements to review)."""
    head = head or _git("rev-parse", "HEAD")
    dirty = _dirty_files()
    cache: dict[str, object] = {}
    restored, left, review = [], [], []
    for c in manifest["claims"]:
        if not c.get("pending_remeasure"):
            continue
        if select and not any(c["id"].startswith(p) for p in select):
            continue
        raw = c.get("raw")
        if not raw or not c.get("pointer"):
            left.append(f"{c['id']}: no raw pointer - restore by hand")
            continue
        touched = [p for p in c.get("produced_by", []) if p in dirty]
        if touched:
            left.append(f"{c['id']}: working tree modifies {touched[0]} - commit first")
            continue
        if raw not in cache:
            try:
                cache[raw] = json.loads((ROOT / raw).read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                cache[raw] = e
        data = cache[raw]
        if isinstance(data, Exception):
            left.append(f"{c['id']}: {raw} unreadable ({type(data).__name__})")
            continue
        try:
            value = resolve(data, c["pointer"])
        except (KeyError, IndexError, TypeError):
            left.append(f"{c['id']}: pointer {c['pointer']} missing in {raw}")
            continue
        # The artifact must be newer than the code it was produced by. A file untouched since
        # the code commit is the OLD measurement wearing a new commit hash.
        code_time = int(_git("log", "-1", "--format=%ct", head))
        if (ROOT / raw).stat().st_mtime < code_time:
            left.append(f"{c['id']}: {raw} predates HEAD - re-run `{c.get('command', '?')}`")
            continue
        if dry_run:
            restored.append(c["id"])
            continue
        old_value = c.get("value")
        old_printed = [str(p) for p in c.get("printed", [])]
        new_printed = []
        for p in old_printed:
            f = reformat(p, value)
            if f is None:
                review.append(f"{c['id']}: printed form {p!r} kept - not a shape this tool formats")
                f = p
            new_printed.append(f)
        stmt = c.get("statement") or ""
        for o, nw in zip(old_printed, new_printed):
            if o != nw and o in stmt:
                stmt = stmt.replace(o, nw)
        if isinstance(old_value, (int, float)) and isinstance(value, (int, float)) \
                and abs(float(old_value) - float(value)) > 1e-9 and re.search(r"\d", stmt) \
                and not any(nw in stmt for nw in new_printed if nw not in old_printed):
            review.append(f"{c['id']}: value {old_value} -> {value}, statement quotes no printed form")
        c["value"] = value
        c["printed"] = new_printed
        c["statement"] = stmt
        ci = c.get("ci")
        if isinstance(ci, dict) and ci.get("method") == "wilson" and c.get("n") \
                and isinstance(value, (int, float)):
            lo, hi = wilson(float(value), int(c["n"]))
            ci["low"], ci["high"] = lo, hi
        c["commit"] = head
        c["cited_in"] = list(c.pop("cited_in_pending", []) or [])
        c.pop("stale", None)
        c.pop("withdrawn_on", None)
        c.pop("pending_remeasure", None)
        restored.append(c["id"])
    return restored, left, review


def pending(manifest: dict) -> dict[str, list[str]]:
    by_cmd: dict[str, list[str]] = {}
    for c in manifest["claims"]:
        if c.get("pending_remeasure"):
            by_cmd.setdefault(c.get("command") or "?", []).append(c["id"])
    return by_cmd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--withdraw", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--reason", default="")
    ap.add_argument("--select", default="", help="comma list of claim-id prefixes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--touching", default="",
                    help="withdraw by closure: `auto` = files modified in the working tree, or a "
                         "comma list of repo-relative paths; default = the freshness failures")
    ap.add_argument("--manifest", default=str(MANIFEST))
    a = ap.parse_args(argv)
    path = Path(a.manifest)
    manifest = load(path)
    select = {s.strip() for s in a.select.split(",") if s.strip()} or None

    if a.withdraw:
        if len(a.reason.strip()) < 20 or not any(g.lower() in a.reason.lower() for g in
                                                 ("gpu", "dataset", "model", "embedder", "api")):
            print("--reason must be a sentence naming the gate that blocks re-measurement "
                  "(GPU, dataset, model, embedder, API)", file=sys.stderr)
            return 2
        today = _dt.date.today().isoformat()
        touching = None
        if a.touching == "auto":
            touching = _dirty_files()
        elif a.touching:
            touching = {t.strip().replace("\\", "/") for t in a.touching.split(",") if t.strip()}
        ids = withdraw(manifest, a.reason.strip(), today, select, touching)
        if not a.dry_run:
            save(manifest, path)
        print(f"{'would withdraw' if a.dry_run else 'withdrew'} {len(ids)} claim(s)")
        for cid in ids:
            print(f"  {cid}")
        return 0

    if a.restore:
        restored, left, review = restore(manifest, select, dry_run=a.dry_run)
        if not a.dry_run:
            save(manifest, path)
        print(f"{'would restore' if a.dry_run else 'restored'} {len(restored)} claim(s)")
        for cid in restored:
            print(f"  {cid}")
        if left:
            print(f"\n{len(left)} still withdrawn:")
            for line in left:
                print(f"  {line}")
        if review:
            print(f"\n{len(review)} statement(s) to review by hand:")
            for line in review:
                print(f"  {line}")
        return 0

    by_cmd = pending(manifest)
    n = sum(len(v) for v in by_cmd.values())
    print(f"{n} claim(s) pending re-measurement" + (":" if n else ""))
    for cmd, ids in sorted(by_cmd.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):3d}  {cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
