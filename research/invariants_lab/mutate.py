"""C2: turn a maintainer's own caller update into a breakage with a proved answer key.

The recipe is one sentence. Take a commit that changed a callable's parameter list --
or moved a symbol out of a module -- **and** updated a caller in a different file in the
same commit. Keep the definition half. Revert the caller half. What remains is a tree in
which a call that used to work does not, and nobody had to have an opinion about it.

## Why the maintainer's edit is not, by itself, the answer key

"They changed the caller, so it must have been broken" is an inference, and a weak one:
maintainers rename variables, reflow arguments and switch to keywords for readability
constantly. A corpus built on that inference would have a positive class contaminated
with cosmetic edits, and every recall number computed from it would be wrong in an
unknown direction.

So each candidate has to clear a **mechanical** bar before it is called a positive:

* **arity** -- the reverted call raises `TypeError` against the definition as it stands
  after the commit. Too many positionals with no `*args`, too few for the required ones,
  a keyword the callee does not accept and no `**kwargs`, or a required keyword-only
  parameter left unsupplied.
* **vanished** -- the reverted caller imports the symbol from a module that, after the
  commit, no longer defines it. The import raises `ImportError` at load time.

Both are properties of the code, decidable by reading it, and neither consults any
checker. Candidates that fail the bar are **discarded**, not downgraded: a smaller
positive class that is certainly positive is worth more than a large one that is
probably positive, and C3 gets to say whether what survives is enough.

## The control that makes the pair mean something

For every positive the generator also proves the **negative** side of the pair: at the
real commit, the same call sites are compatible with the same definition. Without that,
a detector could score well by flagging the symbol in both trees, and the corpus could
not tell a detector from a rubber stamp.

    python research/invariants_lab/mutate.py            # generate, prove, write the artifact
    python research/invariants_lab/mutate.py --print     # summarise the committed one
    python research/invariants_lab/mutate.py --materialise <id> --into <dir>
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import binding  # noqa: E402
from corpora import dev_repos
from corpusio import BlobReader, progress  # noqa: E402
from sigscan import Def, RefSite, scan_defs, scan_refs  # noqa: E402

CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("mutants.json")


# --------------------------------------------------------------------------
# the mechanical bar
# --------------------------------------------------------------------------


#: Re-exported from `binding`, which F1 extracted so the checker's abstention policy
#: can simulate a call without importing the mutation generator. The names stay here
#: because 160 pinned regressions and the answer-key controls call them by these names.
call_fails = binding.call_fails
_module_of = binding.module_of
_import_sources = binding.import_sources
_defines = binding.defines



# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------


@dataclass
class Mutant:
    mid: str
    repo: str
    sha: str
    parent: str
    subject: str
    caller_path: str
    defining_path: str
    qualname: str
    symbol: str
    breakage: str          # arity | vanished
    evidence: str          # why the reverted call cannot work
    control: str           # why the un-reverted call can
    call_line: int
    diff_paths: list[str]  # files the mutant's diff shows against the parent

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _defs_at(blobs: BlobReader, rev: str, path: str, cache: dict) -> dict[str, Def]:
    key = (rev, path)
    if key not in cache:
        src = blobs.read(rev, path)
        cache[key] = scan_defs(src) if src is not None else {}
    return cache[key]


def generate_for_repo(repo: Path, entries: list[dict]) -> list[Mutant]:
    out: list[Mutant] = []
    cache: dict = {}
    with BlobReader(repo) as blobs:
        for n, e in enumerate(entries):
            if n % 25 == 0:
                progress(f"  {repo.name}: {n}/{len(entries)}")
            sha, parent = e["sha"], e["parent"]
            deltas = e["deltas"]
            by_short: dict[str, list[dict]] = {}
            for d in deltas:
                by_short.setdefault(d["qualname"].rsplit(".", 1)[-1], []).append(d)

            for hit in e["caller_updates"]:
                g = hit["path"]
                old_g = blobs.read(parent, g)
                new_g = blobs.read(sha, g)
                if old_g is None or new_g is None:
                    continue
                for short in hit["symbols"]:
                    for delta in by_short.get(short, []):
                        m = _judge(
                            blobs, cache, repo, e, g, old_g, new_g, short, delta
                        )
                        if m is not None:
                            out.append(m)
    return out


def _judge(
    blobs: BlobReader,
    cache: dict,
    repo: Path,
    e: dict,
    g: str,
    old_g: str,
    new_g: str,
    short: str,
    delta: dict,
) -> Mutant | None:
    sha, parent = e["sha"], e["parent"]
    f = delta["path"]
    qualname = delta["qualname"]

    if delta["kind"] == "removed":
        # The reverted caller imports the symbol from a module that, after the commit,
        # no longer defines it.
        if qualname in _defs_at(blobs, sha, f, cache):
            return None
        sources = _import_sources(old_g, short)
        if not any(_defines(s, f) for s in sources):
            return None
        # Control: at the real commit the caller no longer imports it from there.
        if any(_defines(s, f) for s in _import_sources(new_g, short)):
            return None
        line = next(
            (s.lineno for s in scan_refs(old_g, {short})), 0
        )
        return Mutant(
            mid=f"{repo.name}:{sha[:9]}:{g}:{f}:{qualname}",
            repo=repo.name,
            sha=sha,
            parent=parent,
            subject=e["subject"],
            caller_path=g,
            defining_path=f,
            qualname=qualname,
            symbol=short,
            breakage="vanished",
            evidence=(
                f"the reverted {g} imports {short!r} from {_module_of(f)}, "
                f"which no longer defines it at {sha[:9]}"
            ),
            control=(
                f"at {sha[:9]} the real {g} imports {short!r} from somewhere else, "
                f"so the un-reverted tree loads"
            ),
            call_line=line,
            diff_paths=sorted(set(e_paths(e)) - {g}),
        )

    # kind == "params": the reverted call raises TypeError against the new definition.
    new_def = _defs_at(blobs, sha, f, cache).get(qualname)
    if new_def is None or new_def.kind != "func":
        return None
    old_calls = [s for s in scan_refs(old_g, {short}) if s.is_call]
    new_calls = [s for s in scan_refs(new_g, {short}) if s.is_call]
    if not old_calls or not new_calls:
        return None

    broken = [(s, why) for s in old_calls if (why := call_fails(s, new_def))]
    if not broken:
        return None
    # Control: every call in the real, un-reverted caller works against the same
    # definition. If any does not, this commit left a breakage of its own behind and
    # the pair cannot be attributed to the mutation.
    if any(call_fails(s, new_def) for s in new_calls):
        return None

    site, why = broken[0]
    return Mutant(
        mid=f"{repo.name}:{sha[:9]}:{g}:{f}:{qualname}",
        repo=repo.name,
        sha=sha,
        parent=parent,
        subject=e["subject"],
        caller_path=g,
        defining_path=f,
        qualname=qualname,
        symbol=short,
        breakage="arity",
        evidence=f"{g}:{site.lineno} calls {short} and {why}",
        control=(
            f"all {len(new_calls)} call site(s) in the real {g} are compatible with "
            f"{delta['after'] or qualname}"
        ),
        call_line=site.lineno,
        diff_paths=sorted(set(e_paths(e)) - {g}),
    )


def e_paths(e: dict) -> list[str]:
    paths = {d["path"] for d in e["deltas"]}
    paths |= {h["path"] for h in e["caller_updates"]}
    return sorted(paths)


# --------------------------------------------------------------------------
# materialising one mutant, for a detector to run on
# --------------------------------------------------------------------------


def materialise(repo: Path, mutant: dict, into: Path) -> dict[str, dict[str, str | None]]:
    """Return ``{path: {"before": src, "after": src}}`` as the mutant's diff.

    The mutant is the commit with one file held at its parent state, so its diff
    against the parent is the commit's diff minus that file. Written to *into* as
    ``before/`` and ``after/`` trees when a directory is given, so a checker that
    wants files rather than strings can run unchanged.
    """
    out: dict[str, dict[str, str | None]] = {}
    with BlobReader(repo) as blobs:
        for path in mutant["diff_paths"]:
            out[path] = {
                "before": blobs.read(mutant["parent"], path),
                "after": blobs.read(mutant["sha"], path),
            }
        # the reverted caller: identical on both sides, and that is the whole point
        stale = blobs.read(mutant["parent"], mutant["caller_path"])
        out[mutant["caller_path"]] = {"before": stale, "after": stale}
    if into is not None:
        for side in ("before", "after"):
            for path, pair in out.items():
                if pair[side] is None:
                    continue
                dest = into / side / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(pair[side], encoding="utf-8")
    return out


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--materialise", default=None, metavar="ID")
    ap.add_argument("--into", default=None, type=Path)
    args = ap.parse_args(argv)

    if args.show:
        if not ARTIFACT.exists():
            progress("no artifact yet")
            return 1
        data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        head = f"{'repo':<22}{'mutants':>9}{'commits':>9}{'arity':>8}{'vanished':>10}"
        print(head)
        print("-" * len(head))
        for r in data["by_repo"]:
            print(
                f"{r['repo']:<22}{r['mutants']:>9}{r['source_commits']:>9}"
                f"{r['arity']:>8}{r['vanished']:>10}"
            )
        print("-" * len(head))
        t = data["totals"]
        print(
            f"{'total':<22}{t['mutants']:>9}{t['source_commits']:>9}"
            f"{t['arity']:>8}{t['vanished']:>10}"
        )
        print(f"\ndiscarded, unproved: {t['candidates'] - t['mutants']} of {t['candidates']}")
        return 0

    if args.materialise:
        data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        found = [m for m in data["mutants"] if m["mid"] == args.materialise]
        if not found:
            progress(f"no mutant with id {args.materialise}")
            return 1
        m = found[0]
        repo = next(r for r in dev_repos() if r.name == m["repo"])
        files = materialise(repo, m, args.into)
        progress(f"{len(files)} files; caller {m['caller_path']} held at parent")
        print(json.dumps({k: {s: (v is not None) for s, v in p.items()}
                          for k, p in files.items()}, indent=1))
        return 0

    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    by_name = {r["repo"]: r for r in census["repos"]}
    started = time.time()

    all_mutants: list[Mutant] = []
    by_repo: list[dict] = []
    candidates = 0
    for repo in dev_repos():
        entries = by_name.get(repo.name, {}).get("eligible", [])
        candidates += sum(len(h["symbols"]) for e in entries for h in e["caller_updates"])
        progress(f"== {repo.name} ({len(entries)} eligible commits)")
        got = generate_for_repo(repo, entries)
        all_mutants.extend(got)
        by_repo.append(
            {
                "repo": repo.name,
                "eligible_commits": len(entries),
                "mutants": len(got),
                "source_commits": len({m.sha for m in got}),
                "arity": sum(1 for m in got if m.breakage == "arity"),
                "vanished": sum(1 for m in got if m.breakage == "vanished"),
            }
        )
        progress(f"   proved {len(got)}")

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.time() - started, 1),
        "by_repo": by_repo,
        "totals": {
            "candidates": candidates,
            "mutants": len(all_mutants),
            "source_commits": len({(m.repo, m.sha) for m in all_mutants}),
            "arity": sum(1 for m in all_mutants if m.breakage == "arity"),
            "vanished": sum(1 for m in all_mutants if m.breakage == "vanished"),
        },
        "mutants": [m.to_dict() for m in all_mutants],
    }
    ARTIFACT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}: {len(all_mutants)} proved of {candidates} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
