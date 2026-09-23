#!/usr/bin/env python3
"""Every provenance pin an artifact records, against the thing it claims to pin.

The night's most repeated defect was a quantity written down as evidence that stands in no check:
`code_sha` in a commit message, locomo's `embed_cache.sha256`, the half-life artifact's
`manifest_sha256`. Each was found one at a time, by someone noticing. This asks the question of
every artifact at once.

    artifacts scanned                    163
    pin KINDS (a list of rows is one)    145   over 228 recorded values
    named in no suite and no tool          3   guards_pack.universal_pack_size,
                                               and `bench_commit` in both supersession baselines
    commit pins that are ours             28   all resolve
    commit pins from other histories     103   corpora, not asked of this repository
    sha256 pins verified against a file   19

`bench_commit` is the fourth instance of the shape: the commit the baseline bench was run at,
recorded beside `engine_commit` - which IS read - and itself read by nothing. Both values happen
to resolve today, so nothing is wrong; what was missing is any way to know that.

So this suite checks the pins rather than the prose about them:

* every commit-shaped pin resolves to an object in this repository - a pin naming a commit that
  does not exist is a pin nobody ever followed;
* every `sha256` recorded beside a `path` matches that file's digest, where the file is present
  (third-party corpora are hash-pinned and NOT committed, so an absent file is counted, not
  failed);
* the inventory itself is pinned, so a new artifact cannot quietly arrive with pins nobody
  checks - and the population is asserted non-empty, because "no bad pins" and "no pins looked
  at" print the same.

    python tests/_test_recorded_pins_resolve.py
"""
import _env_guard  # noqa: F401
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


#: A field whose NAME claims provenance. Matched on the whole name or its last underscore-part,
#: so `bench_commit` and `manifest_sha256` are in and `commits_scanned` is not.
PIN = re.compile(r"(^|_)(sha256|sha|digest|hash|commit|head|mtime|identity|pinned)$", re.I)
HEXISH = re.compile(r"^[0-9a-f]{7,64}$")


def tracked(*paths: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", *paths], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout.split()
    return out


#: A node that names a repository, a URL or a remote is describing somebody ELSE'S history, and
#: so is everything beneath it: `corpus_census.json` puts `repo` on the parent and the commit on
#: `repos[0].eligible[0].sha`, so asking only the immediate holder still sent fifty foreign
#: commits to `git cat-file` here. The flag descends.
FOREIGN_KEYS = ("repo", "url", "remote", "upstream")


def walk(node, path="", foreign=False):
    """(dotted path, key, value, containing dict, foreign?) for every pin-named scalar."""
    if isinstance(node, dict):
        here_foreign = foreign or any(k in node for k in FOREIGN_KEYS)
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                yield from walk(v, here, here_foreign)
            elif PIN.search(str(k)):
                yield here, k, v, node, here_foreign
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):          # the shape repeats; three rows is the sample
            if isinstance(v, (dict, list)):
                yield from walk(v, f"{path}[{i}]", foreign)


artifacts = [f for f in tracked("research", "docs") if f.endswith(".json")]
pins: list[tuple[str, str, str, object, bool]] = []
HOLDER_PATH: dict[tuple[str, str], str] = {}
for rel in artifacts:
    p = ROOT / rel
    try:
        if p.stat().st_size > 12_000_000:
            continue
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        continue
    for dotted, key, value, holder, is_foreign in walk(blob):
        if isinstance(value, (str, int, float)):
            pins.append((rel, dotted, key, value, is_foreign))
            #: The sibling the digest is ABOUT, kept beside the pin so the file check does not
            #: have to walk the document again.
            sibling = holder.get("path") or holder.get("file")
            if isinstance(sibling, str) and sibling:
                HOLDER_PATH[(rel, dotted)] = sibling

#: One entry per (artifact, path with its indices stripped): a list of 345 rows carries ONE kind
#: of pin, not 345, and counting rows would make the inventory move whenever a corpus grows.
INDEX = re.compile(r"\[\d+\]")
kinds = {(rel, INDEX.sub("[]", dotted)) for rel, dotted, _, _, _ in pins}

print("\n- the population this suite looks at, so a green line is not an empty one -")
check("there are artifacts with pins to check", len(artifacts) >= 100, str(len(artifacts)))
check("and the pins number in the dozens", len(kinds) >= 80,
      f"{len(kinds)} kinds over {len(pins)} values")

print("\n- every commit-shaped pin resolves to an object in this repository -")
#: FOREIGN commits are excluded by a fact about the row, not by a guess: a holder that names a
#: `repo`, `url` or `remote` is describing somebody else's history. `blast_radius_d5.json` holds
#: 345 rows of django/django commits, and the first draft of this check demanded that git resolve
#: them here - a rule applied to a context it had never been asked about, which is mechanism 5 of
#: the night's catalogue, caught by its own red line before it was published.
def is_commit_pin(key: str, value) -> bool:
    return (("commit" in key.lower() or key.lower() in ("head", "sha"))
            and isinstance(value, str) and bool(HEXISH.match(value)))


#: Ours are the commits recorded ABOUT the artifact - `engine_commit`, `bench_commit`, `head` -
#: and they sit at the document's own level. A commit inside a LIST is a row of a corpus: 345
#: django commits in `blast_radius_d5`, 68 more across the census, a `head` per cloned repository
#: in `heldout_clone_log`. Two drafts of this rule tried to tell them apart by sibling key names
#: (`repo`, `url`) and each time a third shape appeared that used neither - so the rule is now
#: structural: not inside a list, and not under a node that names someone else's repository.
def ours(dotted: str, is_foreign: bool) -> bool:
    return "[" not in dotted and not is_foreign


commitish = [(rel, dotted, str(v)) for rel, dotted, key, v, is_foreign in pins
             if is_commit_pin(key, v) and ours(dotted, is_foreign)]
foreign_n = sum(1 for _, dotted, key, v, is_foreign in pins
                if is_commit_pin(key, v) and not ours(dotted, is_foreign))
#: One artifact records a commit that is NOT an object here, for a reason worth keeping rather
#: than papering over: `installed_engine.json` describes the hook INSTALLED in the user's
#: `~/.claude/scripts`, built from the installer's own checkout. Its `repo_head` (9b0b700) does
#: resolve here; its `commit` (5687219) does not, and the subject it carries names a dev commit
#: (bac8b79) that does. So the pin is honest and unfollowable from this repository - an inventory
#: of one, which fails the moment a second such pin appears.
#: A SECOND such pin appeared on 2026-09-22, exactly as the sentence above predicted. It is
#: exempted by COMMIT rather than by artifact, because the artifact is not what is unfollowable:
#: `f0ed0809` is a real commit on `invariants/v3-preclean`, a branch that was never pushed, and
#: it is pinned by three files. Exempting the files would also exempt their other pins, which
#: resolve everywhere and must keep being checked. CI's first matrix run reported it `(unknown)`
#: on every job; the fix is to publish that ref or re-stamp the artifacts, and both are the
#: owner's call.
UNPUBLISHED = {"f0ed0809ae3b30c6a596ec438c5d45496e023ce4"}

KNOWN_ELSEWHERE = {"research/results/installed_engine.json"}

missing = []
for rel, dotted, value in commitish:
    if rel in KNOWN_ELSEWHERE or value in UNPUBLISHED:
        continue
    kind = subprocess.run(["git", "cat-file", "-t", value], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if kind.stdout.strip() != "commit":
        missing.append(f"{rel}:{dotted} = {value[:12]} ({kind.stdout.strip() or 'unknown'})")
check(f"{len(commitish) - len(KNOWN_ELSEWHERE)} commit pins here, and each names a commit that "
      "exists", not missing, " | ".join(missing[:3]))
#: The exemption is an inventory, so it cannot grow quietly.
check("exactly one artifact pins a commit from another checkout",
      len(KNOWN_ELSEWHERE) == 1, str(sorted(KNOWN_ELSEWHERE)))
check("and exactly one commit is real here but on no pushed branch",
      len(UNPUBLISHED) == 1, str(sorted(UNPUBLISHED)))
check("there were commit pins to resolve", len(commitish) >= 5, str(len(commitish)))

#: And the condition itself, counted rather than left inside the inventory: a pin that resolves
#: HERE but sits on no remote-tracking branch is provenance only this machine can check.
#:
#: The unit matters more than the number, and the first version of this got it wrong: it counted
#: OCCURRENCES (12), which conflates "a second unverifiable commit appeared" with "the same one
#: is written in four more places". Two units answer the two questions anyone would ask, and
#: both are pinned: how many distinct COMMITS nobody else can follow (one - `f0ed0809`, on the
#: unpushed `invariants/v3-preclean`), and what it costs in CLAIMS (sixty, across three
#: artifacts). Occurrences answer neither. Named by the auditing session, which counted the same
#: twelve and found them spread over three files rather than one.
_unpublished_shas, _unpublished_files = set(), set()
for rel, dotted, value in commitish:
    if subprocess.run(["git", "cat-file", "-t", value], cwd=ROOT, capture_output=True,
                      text=True).stdout.strip() != "commit":
        continue
    if not subprocess.run(["git", "branch", "-r", "--contains", value], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip():
        _unpublished_shas.add(value[:12])
        _unpublished_files.add(rel)
check(f"distinct commits that no clone can follow ({len(_unpublished_shas)})",
      len(_unpublished_shas) <= 1, ", ".join(sorted(_unpublished_shas)))
check(f"and the artifacts carrying them ({len(_unpublished_files)})",
      len(_unpublished_files) <= 3, ", ".join(sorted(_unpublished_files)))
if _unpublished_shas:
    _man = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
    _at_risk = [c for c in _man["claims"] if c.get("raw") in _unpublished_files]
    check(f"and the claims standing on them ({len(_at_risk)})", len(_at_risk) <= 60,
          f"{len(_at_risk)} claims on {sorted(_unpublished_files)}")
    print(f"       ({len(_unpublished_shas)} commit(s) exist only on this machine, carried by "
          f"{len(_unpublished_files)} artifact(s) under {len(_at_risk)} claim(s) - publish the "
          f"ref or re-stamp)")

print("\n- every sha256 recorded beside a path matches that file, where the file is here -")
checked = absent = 0
wrong = []
for rel, dotted, key, value, _is_foreign in pins:
    if key.lower() not in ("sha256", "digest", "hash") or not isinstance(value, str):
        continue
    target = HOLDER_PATH.get((rel, dotted))
    if not isinstance(target, str) or not target:
        continue
    f = ROOT / target
    if not f.exists():
        absent += 1
        continue
    checked += 1
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    if digest != value:
        wrong.append(f"{rel}:{dotted} -> {target}: recorded {value[:12]}, file {digest[:12]}")
check(f"{checked} digests verified against the file they name", not wrong, " | ".join(wrong[:2]))
#: An absent file is the normal case for a hash-pinned third-party corpus: the pin exists SO THAT
#: a stranger can fetch and compare. Counted rather than passed over in silence.
print(f"       ({absent} named a file that is not in this clone - third-party corpora, by design)")

print("\n- and the inventory is pinned, so new pins cannot arrive unnoticed -")
#: Measured 2026-09-22 at `b956966`. A change here is a decision: either a new artifact arrived
#: with provenance, or one lost it.
#: 2026-09-23, restore #1 at 358fa75: +1 artifact (research/results/token_floor.json, block A - its
#: code_sha, measured_by[].code_sha and the oracle corpus sha256) and +2 kinds on locomo_raw.json
#: (embed_cache.mtime/.sha256, which the stand now records beside its cache). Diffed against a
#: worktree at HEAD, none lost.
check("the number of pinned artifacts has not moved", len(artifacts) == 164, str(len(artifacts)))
check("and the number of pin KINDS has not moved", len(kinds) == 150,
      f"{len(kinds)} kinds over {len(pins)} values")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
