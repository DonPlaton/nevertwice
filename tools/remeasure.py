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
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`
import check_freshness as cf  # noqa: E402


def _git(*args: str) -> str:
    r = subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def load(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(manifest: dict, path: Path = MANIFEST) -> None:
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# ── pointer / value plumbing ───────────────────────────────────────────────────

#: One segment of a pointer: a quoted key - the way to address a key that itself contains a dot,
#: as a weight sweep's `"0.25"` does - a list index, or a bare key.
SEGMENT = re.compile(r'\["([^"]*)"\]|\[(\d+)\]|([^.\[\]]+)')


def resolve(data, pointer: str):
    """Walk a pointer: `a.b[2].c`, and `a["0.25"].c` for a key with a dot in it -
    the manifest test's own walker."""
    node = data
    for quoted, index, key in SEGMENT.findall(pointer):
        node = node[int(index)] if index else node[quoted or key]
    return node


#: `pairs[2].p_mcnemar` - a pointer whose first step indexes a list by POSITION.
PAIR_POINTER = re.compile(r"^(\w+)\[(\d+)\]")
#: `supersession_implicit.mem0_vs_naive.discordant.naive` - the id names the two arms compared.
PAIR_ID = re.compile(r"\.([A-Za-z0-9_]+?)_vs_([A-Za-z0-9_]+?)\.")


def pair_mismatch(claim: dict, data) -> str | None:
    """Does a positional pointer still address the pair the claim's id names?

    A list index is an address only while the list keeps its shape. The supersession artifact's
    `pairs` is built from whichever arms a run produced, so a run with fewer arms renumbers it
    and every positional pointer silently moves to a different pair - measured 2026-09-22, four
    live claims rewritten to another arm-pair's figures with every check still green.
    """
    ptr, cid = claim.get("pointer") or "", claim.get("id") or ""
    mp, mi = PAIR_POINTER.match(ptr), PAIR_ID.search(cid)
    if not (mp and mi):
        return None
    try:
        row = resolve(data, f"{mp.group(1)}[{mp.group(2)}]")
    except (KeyError, IndexError, TypeError) as e:
        #: A guard that goes SILENT when it cannot do its job is only safe while something else
        #: refuses the same input - here `restore` resolving the pointer five lines earlier. That
        #: is a dependency on call ORDER, and nothing recorded it: move the guards above the
        #: resolve and their silence becomes the whole answer. Measured 2026-09-22 across the
        #: register: 807 pointers walked, ZERO exceptions in either guard - the branch is dead
        #: code, so refusing here costs nothing today and removes the order dependency entirely.
        return f"`{ptr}` cannot be walked in this artifact ({type(e).__name__})"
    if not isinstance(row, dict) or "a" not in row or "b" not in row:
        return None
    want, got = {mi.group(1), mi.group(2)}, {str(row["a"]), str(row["b"])}
    if want != got:
        return (f"`{ptr}` now addresses {sorted(got)}, but the claim is about {sorted(want)} - "
                f"the artifact's arm list changed shape, so the index moved to another pair")
    return None


def _utc_epoch(stamp: str) -> int | None:
    """`2026-09-23T06:03:47Z` -> seconds since the epoch; None for anything else."""
    from datetime import datetime, timezone                      # noqa: PLC0415
    try:
        return int(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _closure_moved(commit: str, head: str, produced_by: list[str]) -> str | None:
    """None when `commit` IS `head` or none of `produced_by` differs between them; otherwise a
    reason. A commit git cannot resolve is a reason too - it cannot be vouched for."""
    if commit == head:
        return None
    try:
        out = subprocess.run(["git", "diff", "--name-only", commit, head, "--", *produced_by],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return f"commit {commit[:7]} is not one git can resolve"
    return (f"its code differs from HEAD in {out.splitlines()[0]}" if out else None)


def row_refusal(data, pointer: str, code_time: int, head: str | None = None,
                produced_by: list[str] | None = None) -> str | None:
    """Would restoring THIS row put a number back that no valid run at HEAD produced?

    Two ways it would, both found before the v2 campaign (2026-09-24):
    - a container on the pointer's path says `"valid": false` - the stands mark a run invalid
      when its transport bypassed the pacer or its embeds failed, and `restore` never read it;
    - the nearest `measured_at` on the path is older than HEAD. The file-level mtime check below
      cannot see this: `head_to_head.py --save` MERGES rows, so a file written today carries rows
      measured weeks ago. Restore #1 put twelve such claims back as if re-measured
      (`h2h_pinned.{mem0_infer,langmem_full,amem_full}`, rows stamped 2026-09-08 at f0ed080,
      restored at 358fa75).
    Every container on the path is asked, the artifact root included; the deepest `measured_at`
    wins, because that is the stamp of the row the value was read from."""
    nodes, node = [data], data
    try:
        for quoted, index, key in SEGMENT.findall(pointer):
            node = node[int(index)] if index else node[quoted or key]
            nodes.append(node)
    except (KeyError, IndexError, TypeError) as e:
        return f"`{pointer}` cannot be walked in this artifact ({type(e).__name__})"
    stamp = None
    for n in nodes[:-1]:
        if not isinstance(n, dict):
            continue
        if n.get("valid") is False:
            return (f"the row is marked invalid by its own run: "
                    f"{n.get('invalid_reason') or 'no reason recorded'}")
        ma = n.get("measured_at")
        if isinstance(ma, dict) and ma.get("utc"):
            stamp = ma
    if stamp is not None:
        t = _utc_epoch(stamp.get("utc"))
        if t is not None and t < code_time:
            return (f"the row was measured at {stamp['utc']} (commit "
                    f"{str(stamp.get('commit') or '?')[:7]}), before HEAD - a row merged in from an "
                    f"older run, not a re-measurement")
        #: A fresh time is not fresh code: a run started today from an old worktree stamps a new
        #: utc on old code (the auditor's K15, 2026-09-24). The commit must be HEAD, or the
        #: claim's own closure must be identical between them - m2_check's commit_ok, per closure.
        if head is not None:
            commit = str(stamp.get("commit") or "")
            if not commit or commit == "?":
                return "the row carries a measurement time but no commit - it cannot be vouched for"
            moved = _closure_moved(commit, head, list(produced_by or []))
            if moved:
                return f"the row was measured at commit {commit[:7]}, and {moved}"
    return None


def list_shape(data, pointer: str) -> list[dict]:
    """The shape of every list a pointer indexes by position, as it stands in this artifact.

    `pair_mismatch` above covers the claims whose id names the two arms compared - thirty-six of
    the hundred and eight positional claims. The other seventy-two index a list whose rows carry
    no such signature: `recall_sweep[4].threshold`, `by_k[0].recall_at_k`, `per_run[0]`. For
    those the only thing that can be compared is the list's SHAPE, so it is recorded when the
    claim is registered and compared on every restore. Inserting one threshold before index 4
    moves thirty-three abstention claims to a neighbouring row while every existing check still
    passes - the value resolves, and it is a number of the same kind.
    """
    out: list[dict] = []
    node, path = data, ""
    for quoted, index, key in SEGMENT.findall(pointer):
        if index:
            row = node[int(index)]
            out.append({"at": path, "len": len(node),
                        "keys": sorted(row) if isinstance(row, dict) else None})
            node = row
        else:
            k = quoted or key
            path = f"{path}.{k}" if path else k
            node = node[k]
    return out


def shape_mismatch(claim: dict, data) -> str | None:
    """Has the list this claim indexes changed shape since the claim was registered?

    A claim with no recorded `shape` is one registered before this layer; it is not refused here
    (that would withdraw the register wholesale), it is counted by
    `tests/_test_positional_pointers.py`, which is where the number lives.
    """
    recorded = claim.get("shape")
    if not recorded:
        return None
    try:
        now = list_shape(data, claim.get("pointer") or "")
    except (KeyError, IndexError, TypeError) as e:
        #: Refuse rather than fall silent - see `pair_mismatch` above for why the silent form was
        #: safe only by accident of call order.
        return f"`{claim.get('pointer')}` cannot be walked in this artifact ({type(e).__name__})"
    if now == recorded:
        return None
    for was, isnow in zip(recorded, now + [None] * len(recorded)):
        if isnow is None or was != isnow:
            where = (was or {}).get("at") or "the artifact's top level"
            if isnow and was["len"] != isnow["len"]:
                return (f"`{where}` held {was['len']} rows when this claim was registered and "
                        f"holds {isnow['len']} now - the index addresses a different row, and "
                        "the value it finds there is the same kind of number")
            if isnow:
                return (f"`{where}` still holds {isnow['len']} rows, but the row at this index "
                        f"changed shape: keys were {was['keys']}, are {isnow['keys']}")
            return f"`{where}` no longer indexes a list at all"
    return (f"the shape of the lists `{claim.get('pointer')}` indexes changed: "
            f"{recorded} became {now}")


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
        out = f"{v:.{len(m.group(2))}f}"
        if v > 0 and float(out) == 0.0:
            # a p-value that shrank under the old precision is small, not zero: "0.00" would
            # print a claim the artifact does not make (nevertwice_vs_zep, 2026-09-11)
            mant, exp = f"{v:.1e}".split("e")
            return f"{mant} x 10^{int(exp)}"
        return out
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

#: A number as a statement prints it: digits with optional thousands commas and decimals, an
#: optional percent sign. Used to find the numbers a rewrite INTRODUCED.
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")
#: A match must be a WHOLE number: no digit or decimal point before it, and after it no digit and
#: no point or comma that continues it with a digit - "45" is not the start of "45.6%" or of
#: "19,206" and "2.0" is not the start of the version "2.0.19" (the auditing session's (в)1).
#: Module constants so the suite can take them away and prove the post-check below still holds.
_BEFORE = r"(?<![\d.])"
_AFTER = r"(?!\d|[.,]\d)"


def _rewrite_statement(stmt: str, old_printed: list[str], new_printed: list[str]
                       ) -> tuple[str, list[str]]:
    """The statement with each old printed form replaced by its new one, in ONE anchored pass.

    Restore #1 (2026-09-23) replaced the forms one after another with `str.replace`, so a short
    old form could match INSIDE a new form written a moment earlier: ['0.017', '0.0167', '0.02']
    -> ['0.027', '0.0272', '0.03'] turned "0.0167" into "0.0272" and then "0.02" into "0.0372".
    Here every old form is one alternative of a single regex, longest first, anchored so a match
    is a whole number (no digit or decimal point before it, no digit after), and each match maps
    straight to its new form - a replaced number is never scanned again. Returns the statement
    and the numbers it would newly print that are neither in the old statement nor new printed
    forms; the caller keeps the old statement when that list is not empty.
    """
    mapping = {o: n for o, n in zip(old_printed, new_printed) if o and o != n}
    if not mapping:
        return stmt, []
    alts = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(_BEFORE + "(?:" + "|".join(re.escape(o) for o in alts) + ")" + _AFTER)
    out = pattern.sub(lambda mt: mapping[mt.group(0)], stmt)
    before = set(_NUMBER.findall(stmt))
    #: a printed form can be several numbers ("3.4 x 10^-9"): the numbers INSIDE each new form are
    #: vouched for too, or the check would refuse every p-value rewrite it exists to allow
    allowed = {p.strip() for p in new_printed}
    for p in new_printed:
        allowed.update(_NUMBER.findall(p))
    unvouched = sorted({n for n in _NUMBER.findall(out) if n not in before and n not in allowed})
    return out, unvouched


def affected(manifest: dict, touching: set[str] | None = None) -> list[dict]:
    """Live claims whose closure moved after they were measured.

    With `touching` (a set of repo-relative paths, e.g. the working tree's modified files), a
    claim is affected when its `produced_by` meets the set - the question the freshness check
    will ask once those files are committed, answered before the commit so the withdrawal
    and the change land together. Without it, the freshness check's own failures are used.
    """
    #: A declared value (see `declaration` in check_freshness) is decided, not measured, so no
    #: code change can affect it and no campaign can restore it.
    live = [c for c in manifest["claims"] if not c.get("stale") and not c.get("declaration")]
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
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


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
        #: Before trusting the number: does the index still point at the same pair? Restoring
        #: from a renumbered list is how four live claims took another pair's figures while
        #: keeping their own sentence (2026-09-22).
        moved = pair_mismatch(c, data) or shape_mismatch(c, data)
        if moved:
            left.append(f"{c['id']}: {moved}")
            continue
        # The artifact must be newer than the code it was produced by. A file untouched since
        # the code commit is the OLD measurement wearing a new commit hash.
        code_time = int(_git("log", "-1", "--format=%ct", head))
        #: ...and so must the ROW, which a merging --save can carry over from an older run, and
        #: the row must not be one its own run marked invalid (see `row_refusal`).
        refusal = row_refusal(data, c["pointer"], code_time, head=head,
                              produced_by=c.get("produced_by") or [])
        if refusal:
            left.append(f"{c['id']}: {refusal}")
            continue
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
        stmt, unvouched = _rewrite_statement(c.get("statement") or "", old_printed, new_printed)
        if unvouched:
            review.append(f"{c['id']}: statement NOT rewritten - the rewrite would print "
                          f"{', '.join(unvouched)}, which is neither in the old statement nor a new "
                          f"printed form")
            stmt = c.get("statement") or ""
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


def _assembled_artifacts() -> dict[str, str]:
    """{artifact: why its recorded command cannot write it}, read from the package.

    `research/reproduce.py` declares eleven entries whose recorded command produces a SMALLER
    file than the committed one - arms that came from other runs merged in with `--with`. The
    register carries the same commands for 281 claims and said nothing about it: measured
    2026-09-22, zero of those 281 mentioned the assembly, 171 had an empty `note` and the other
    110 talked about something else. `--pending` is the surface a person acts on - it prints the
    command they are about to run - so the caveat is printed HERE rather than copied into the
    manifest, which would be a second source of truth that agrees on the day it is written.
    """
    try:
        sys.path.insert(0, str(ROOT / "research"))
        import reproduce as _r
    except Exception:                       # the package is research-only; absence is not fatal
        return {}
    out = {}
    for entry in getattr(_r, "ARTIFACTS", []):
        how = (entry.get("assembled") or {}).get("how") if entry.get("assembled") else None
        if how:
            out[entry["file"]] = how
    return out


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
    caveats = _assembled_artifacts()
    raw_of = {c.get("command") or "?": c.get("raw") for c in manifest["claims"]
              if c.get("pending_remeasure")}
    flagged = 0
    for cmd, ids in sorted(by_cmd.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(ids):3d}  {cmd}")
        how = caveats.get(raw_of.get(cmd) or "")
        if how:
            flagged += len(ids)
            print(f"       ! this command cannot write that file as it stands: {how}")
    #: The third state, which this listing used to hide. A claim is withdrawn AND queued, or
    #: withdrawn and left out of the queue with its reason in `stale`. `--pending` printed the
    #: queue and its size and never said the second group existed, so a campaign planned from
    #: "572 pending" inherited the blindness: after a perfect campaign the register is not whole,
    #: it is whole minus these. Counted here rather than stamped anywhere - the reasons are
    #: already in the claims (2026-09-22).
    third: dict[str, int] = {}
    for c in manifest["claims"]:
        if c.get("stale") and not c.get("pending_remeasure"):
            third[str(c["stale"]).split(":")[0][:56]] = third.get(
                str(c["stale"]).split(":")[0][:56], 0) + 1
    if third:
        t = sum(third.values())
        print("")
        print(f"  and {t} withdrawn claim(s) are NOT in this queue - re-measuring everything "
              f"above leaves them where they are:")
        for reason, k in sorted(third.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    {k:3d}  {reason}")
    if flagged:
        print("")
        print(f"  {flagged} of the {n} stand behind a command the package declares incomplete "
              f"(`assembled` in research/reproduce.py). Running it overwrites a merged "
              f"artifact with a smaller one under the same name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
