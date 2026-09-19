#!/usr/bin/env python3
"""Register the `[facts]`-dilution probe (ledger K1) from `research/results/facts_dilution.json`.

The probe ranks the correct note for every control case of a supersession corpus twice - with
the `[facts]` block in the notes' cached text and with it stripped and re-embedded - and reports,
for the seven cases finding 5a named and for all controls, how often the rank was worse, better or
the same with the block, how many were in the top k either way, and the one-sided sign test. The
gate was written in the ledger before the run; the verdict the artifact carries is the page's, in
words - this tool registers the numbers behind it.

    python tools/register_dilution.py [--at COMMIT] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
sys.path.insert(0, str(ROOT / "tools"))
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001
    pass

RAW = "research/results/facts_dilution.json"
COMMAND = "python research/facts_dilution_probe.py --save --out research/results/facts_dilution.json"
DATASET = "supersession_v1_implicit"
ENVIRONMENT = "local_supersession_stand"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


def build_claims(art: dict, *, head: str, produced_by: list[str], existing: set[str]) -> list[dict]:
    base = {"dataset": DATASET, "environment": ENVIRONMENT, "command": COMMAND, "raw": RAW,
            "cited_in": [], "commit": head, "produced_by": list(produced_by), "ci": None}
    new: list[dict] = []
    k = int(art.get("k") or 5)

    def add(cid, statement, value, printed, unit, n, pointer, note=None):
        if cid in existing:
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit,
             "n": n, "pointer": pointer, **base}
        if note:
            c["note"] = note
        new.append(c)

    for group, label in (("named", "the seven control cases finding 5a named"),
                         ("all_controls", "all control cases of the implicit corpus")):
        s = art.get(group)
        if not s:
            continue
        n = int(s["n"])
        for key, what in (("worse_with_block", "ranked worse with the block than without it"),
                          ("better_with_block", "ranked better with the block than without it"),
                          ("same", "ranked the same with and without the block"),
                          ("in_top_k_with", f"were in the top {k} with the block"),
                          ("in_top_k_without", f"were in the top {k} without the block")):
            v = int(s[key])
            add(f"dilution.{group}.{key}",
                f"of {label} ({n}), the correct note {what} for {v}",
                v, [str(v)], "control cases", n, f"{group}.{key}")
        add(f"dilution.{group}.n", f"the dilution probe ranked {n} of {label}", n, [str(n)], "control cases", n,
            f"{group}.n")
        p = float(s["p_sign_one_sided"])
        add(f"dilution.{group}.p_sign", f"one-sided sign test over {label}: p = {p:.2f} (ties carry no information)",
            p, [f"{p:.2f}", f"{p:.4f}"], "p-value", n, f"{group}.p_sign_one_sided")
    if art.get("notes") is not None:
        add("dilution.notes", f"the probe's store held {art['notes']} notes after ingesting the controls",
            int(art["notes"]), [str(art["notes"])], "notes", int(art["notes"]), "notes")
        add("dilution.notes_with_facts_block",
            f"{art['notes_with_facts_block']} of the {art['notes']} notes carried a [facts] block; all were re-embedded from the stripped text",
            int(art["notes_with_facts_block"]), [str(art["notes_with_facts_block"])], "notes", int(art["notes"]),
            "notes_with_facts_block")
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--at", metavar="COMMIT", default="")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raw = ROOT / RAW
    if not raw.is_file():
        print(f"no artifact at {RAW} - run `{COMMAND}` first")
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if DATASET not in manifest.get("datasets", {}):
        print(f"unknown dataset {DATASET!r} - register it first")
        return 2
    import produced_by as pb                                     # noqa: PLC0415
    closure = pb.closure(COMMAND)
    if args.at:
        head = _git("rev-parse", args.at)
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=ROOT).returncode != 0:
            print(f"{args.at} is not an ancestor of HEAD")
            return 2
        if not any(c.get("raw") == RAW and c.get("commit") == head and not c.get("stale") for c in manifest["claims"]):
            print(f"no live claim on {RAW} carries {args.at}")
            return 2
        moved = [p for p in closure
                 if subprocess.run(["git", "merge-base", "--is-ancestor",
                                    _git("log", "-1", "--format=%H", "HEAD", "--", p), head],
                                   cwd=ROOT).returncode != 0]
        if moved:
            print(f"{moved[0]} changed after {args.at}")
            return 2
    else:
        head = _git("rev-parse", "HEAD")
        if raw.stat().st_mtime < int(_git("log", "-1", "--format=%ct", head)):
            print(f"{RAW} predates HEAD - re-run `{COMMAND}`")
            return 2
    dirty = sorted(p for p in closure if p in _dirty_files())
    if dirty:
        print(f"working tree modifies {dirty[0]} - commit first")
        return 2
    art = json.loads(raw.read_text(encoding="utf-8"))
    new = build_claims(art, head=head, produced_by=closure, existing={c["id"] for c in manifest["claims"]})
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"registered {len(new)} claim(s) at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
