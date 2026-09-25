#!/usr/bin/env python3
"""A recorded command must be able to write every arm of the artifact it names.

The night's defect was found by running two recorded commands and looking at what came out:
`--arms nevertwice,naive` produced a three-arm file where the committed one holds seven, and a
positional claim silently took a neighbouring pair's number. The lesson was first written as "an
instruction is verified by executing it". The auditing session narrowed it, correctly and far
more cheaply: an instruction is verified against its PRODUCT, not against its description - and
the product is already on disk. Executing is one way to obtain a product, and the most expensive.

So this suite compares the two without running anything. The stands name their arms by a rule
that is arithmetic rather than a judgement call, and the rule is read from the stand's own
source here rather than remembered:

    --arms a,b       writes arms `a` and `b`; ABSENT, argparse's default applies - which is how
                     the implicit command breaks in silence
    --runs N         adds `nevertwice_run2 .. _runN`  (only the engine arm repeats; others pool)
    --sleep          adds `nevertwice_after_sleep` and its `_run2 .. _runN`
    --llm            adds `guards_llm` (guard_bench.py:342)

Anything on disk this cannot account for came from another run, merged in - so the recorded
command does not reproduce the artifact, and a reader who follows it gets a smaller file under
the same name. Measured when this suite was written (2026-09-22):

    package entries whose artifact has arms      16     of them unable to make the file    11
    register commands behind such artifacts       9     of them unable                      6
                                                                       (155 claims behind them)

The gap is not repaired by inventing a command: the per-arm runs that carried `mem0` and `zep`
are not on disk, so no combination of flags remakes these files. It is repaired by saying so -
each such entry carries `assembled`, naming the arms that came from elsewhere and how. That turns
a false instruction into a true one, and turns a NEW unexplained arm into a failure here.

Second property, from the same audit. No recorded command may carry a flag twice: two package
entries carried `--out` twice after `33b1481` widened them. The command still ran - argparse
keeps the last value, and the value was the same - so nothing broke, and the flag comparison in
`_test_package_commands_match.py` could not see it BY CONSTRUCTION, because it compares SETS and
a set does not distinguish a duplicate. "Is the flag there" is adjacent to "is the command
written correctly", which is the shape of every defect this repository spent the night on.

    python tests/_test_command_makes_the_arms.py
"""
import _env_guard  # noqa: F401
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import reproduce as R  # noqa: E402

FAILS = 0

#: The arm the supersession and as-of stands repeat across runs and re-read after the sleep-time
#: judge (`supersession_bench.py:870-878`, `asof_bench.py:404-406`). Every other arm is pooled
#: rather than duplicated, so only this one grows suffixes.
ENGINE = "nevertwice"

#: A flag that adds an arm by name, outside the `--arms` list. One line of one stand each, so
#: the table is checked against that line below rather than trusted.
ADDS = {"guard_bench.py": ("--llm", "guards_llm")}

#: An arm a stand writes on EVERY run whatever `--arms` says - a declared-blocked row. One line of
#: one stand, checked against that line below: asof_bench records Mem0 as blocked (no as-of filter).
ALWAYS = {"asof_bench.py": ("mem0", 'out["arms"]["mem0"] = {"blocked":')}


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def tokens(cmd) -> list[str]:
    return cmd.split() if isinstance(cmd, str) else list(cmd)


def stand_of(cmd) -> Path | None:
    """The stand a command runs, or None when it runs something else (`-m`, a tool)."""
    for t in tokens(cmd)[1:]:
        if t.endswith(".py"):
            p = ROOT / t
            return p if p.exists() else None
        if t.startswith("-"):
            return None
    return None


_DEFAULTS: dict[Path, tuple[str | None, int]] = {}


def defaults(stand: Path) -> tuple[str | None, int]:
    """What the stand does when `--arms`/`--runs` are not written: argparse's own defaults.

    Read from the source, because a default nobody wrote down is exactly what rewrote
    `supersession.mem0_vs_naive.p_mcnemar`: the explicit command names no `--arms` at all and
    silently takes two of the seven.
    """
    if stand not in _DEFAULTS:
        src = stand.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'add_argument\("--arms",\s*default="([^"]*)"', src)
        r = re.search(r'add_argument\("--runs",\s*type=int,\s*default=(\d+)', src)
        _DEFAULTS[stand] = (m.group(1) if m else None, int(r.group(1)) if r else 1)
    return _DEFAULTS[stand]


def _files_after(t: list[str], flag: str) -> list[str]:
    """The file arguments that follow `flag` up to the next flag."""
    if flag not in t:
        return []
    out = []
    for tok in t[t.index(flag) + 1:]:
        if tok.startswith("--"):
            break
        out.append(tok)
    return out


def pooled_arms(t: list[str]) -> set[str] | None:
    """The arms a `--pool run1 run2 ... [--with other ...]` command writes, read off the run files
    the way `supersession_bench.pool()` names them: the engine arm of file i is `nevertwice` for
    the first file and `nevertwice_run{i}` after it (its after-sleep reading likewise), every other
    arm is pooled under its own name, and a `--with` file adds its non-engine arms. None when a
    named file is not in this clone - an arm set that cannot be read is not guessed."""
    out: set[str] = set()
    for i, f in enumerate(_files_after(t, "--pool"), start=1):
        arms = arms_on_disk(f)
        if arms is None:
            return None
        if ENGINE in arms:
            out.add(ENGINE if i == 1 else f"{ENGINE}_run{i}")
        if f"{ENGINE}_after_sleep" in arms:
            out.add(f"{ENGINE}_after_sleep" if i == 1 else f"{ENGINE}_after_sleep_run{i}")
        out |= {a for a in arms if not a.startswith(ENGINE)}
    for f in _files_after(t, "--with"):
        arms = arms_on_disk(f)
        if arms is None:
            return None
        out |= {a for a in arms if not a.startswith(ENGINE)}
    return out


def producible(cmd) -> set[str] | None:
    """Every arm name this command can write. None when the stand has no arm selector at all."""
    t = tokens(cmd)
    stand = stand_of(cmd)
    if stand is None:
        return None
    if "--pool" in t:
        #: campaign v2 records the pooled artifacts' real writer (`--pool ... --with ...`); its arms
        #: come from the files it names, not from `--arms`/`--runs`
        return pooled_arms(t)
    arms_default, runs_default = defaults(stand)
    if "--arms" in t:
        base = set(t[t.index("--arms") + 1].split(","))
    elif arms_default is not None:
        base = set(arms_default.split(","))         # the silent path, spelled out
    else:
        return None
    runs = int(t[t.index("--runs") + 1]) if "--runs" in t else runs_default
    out = set(base)
    if ENGINE in base:
        out |= {f"{ENGINE}_run{i}" for i in range(2, runs + 1)}
        if "--sleep" in t:
            out.add(f"{ENGINE}_after_sleep")
            out |= {f"{ENGINE}_after_sleep_run{i}" for i in range(2, runs + 1)}
    flag, arm = ADDS.get(stand.name, (None, None))
    if flag and flag in t:
        out.add(arm)
    if stand.name in ALWAYS:
        out.add(ALWAYS[stand.name][0])
    #: `--with` outside a pool (asof_bench, campaign v2): the files' non-engine arms are merged in,
    #: exactly as in the pooled branch - and an unreadable file is not guessed.
    for f in _files_after(t, "--with"):
        arms = arms_on_disk(f)
        if arms is None:
            return None
        out |= {a for a in arms if not a.startswith(ENGINE)}
    return out


def disagreement(disk: set[str], makes: set[str], declared: set[str]) -> set[str]:
    """The arms on which the file and its entry disagree - empty when they agree.

    A SYMMETRIC difference, so it catches both directions: an arm on disk that nothing can write
    and nothing declares, and a declared arm that has vanished from the file. A one-sided
    "nothing new appeared" rule would wave through a run that shrank a seven-arm artifact to two,
    which is the corruption of `33b1481` arriving from the other side.

    It lives in a function because the demonstration at the bottom must call the LINE THAT
    DECIDES, not restate its semantics: the first version of that demonstration compared two
    literals with `!=`, and weakening the real comparison to `<=` left it green (found by the
    auditing session, 2026-09-22 - the fourth tautological check of the night).
    """
    return (disk - makes) ^ declared


def arms_on_disk(artifact: str) -> set[str] | None:
    p = ROOT / artifact
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(blob, dict) and isinstance(blob.get("arms"), dict):
        return set(blob["arms"])
    return None


package = {spec["file"].replace("\\", "/"): spec for spec in R.ARTIFACTS}

MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_claims = MANIFEST["claims"]
claims = list(_claims.values() if isinstance(_claims, dict) else _claims)

print("\n- the naming rules are read from the stands, not remembered here -")
for stand, (flag, arm) in ADDS.items():
    src = (ROOT / "research" / stand).read_text(encoding="utf-8", errors="replace")
    check(f"{stand} still adds {arm} for {flag}", f'"{arm}"] if args.{flag[2:]}' in src)
for stand, (arm, line) in ALWAYS.items():
    src = (ROOT / "research" / stand).read_text(encoding="utf-8", errors="replace")
    check(f"{stand} still writes its {arm} row on every run", line in src)
sup = defaults(ROOT / "research" / "supersession_bench.py")
aso = defaults(ROOT / "research" / "asof_bench.py")
check("the supersession stand's own defaults are what this suite uses",
      sup == ("nevertwice,naive", 1), str(sup))
check("and the as-of stand's, which differ in --runs - a default worth not guessing",
      aso == ("nevertwice,naive", 2), str(aso))

print("\n- no recorded command carries a flag twice -")
dups = []
for f, spec in sorted(package.items()):
    flags = [t for t in spec["command"] if t.startswith("--")]
    repeated = sorted({t for t in flags if flags.count(t) > 1})
    if repeated:
        dups.append(f"{f}: {repeated}")
check("every package command spells each flag once", not dups, " | ".join(dups[:3]))

dups = []
for c in claims:
    flags = [t for t in tokens(c.get("command") or "") if t.startswith("--")]
    repeated = sorted({t for t in flags if flags.count(t) > 1})
    if repeated:
        dups.append(f"{c['id']}: {repeated}")
check("and so does every claim's command", not dups, " | ".join(dups[:3]))

print("\n- the package's command can write every arm its artifact holds, or declares that it cannot -")
checked = 0
for f, spec in sorted(package.items()):
    disk = arms_on_disk(f)
    if disk is None:
        continue                            # not in this clone, or not an arm-shaped artifact
    makes = producible(spec["command"])
    if makes is None:
        continue                            # the stand has no arm selector: nothing to compare
    checked += 1
    unexplained = disk - makes
    declared = set(spec.get("assembled", {}).get("arms", ()))
    check(f"{f}: the arms it cannot make are the ones it declares",
          not disagreement(disk, makes, declared),
          f"unmakeable {sorted(unexplained)}, declared {sorted(declared)}")
    if declared:
        check(f"{f}: and the declaration says where they came from",
              bool((spec.get("assembled", {}).get("how") or "").strip()))
check("there were arm-shaped artifacts to compare", checked >= 14, str(checked))

print("\n- a claim's command is held to the same rule, through the package's declaration -")
by_artifact: dict[str, set[str]] = {}
for c in claims:
    if c.get("raw") and c.get("command"):
        by_artifact.setdefault(c["raw"].replace("\\", "/"), set()).add(c["command"])

undeclared = []
for artifact, commands in sorted(by_artifact.items()):
    disk = arms_on_disk(artifact)
    if disk is None:
        continue
    declared = set(package.get(artifact, {}).get("assembled", {}).get("arms", ()))
    for cmd in sorted(commands):
        makes = producible(cmd)
        if makes is None:
            continue
        gap = disk - makes - declared
        if gap:
            n = sum(1 for c in claims if (c.get("raw") or "").replace("\\", "/") == artifact
                    and c.get("command") == cmd)
            undeclared.append(f"{artifact} ({n} claims) cannot make {sorted(gap)}")
check("no claim names a command that silently makes a smaller artifact", not undeclared,
      " | ".join(undeclared[:2]))

print("\n- and a command's --out names the file the entry claims, or declares that it does not -")
#: A flag-SET comparison cannot see this: `--out` is present in both, and only its VALUE differs.
#: One entry writes another artifact entirely - `asof_k7_d07375e.json`'s command says
#: `--out research/results/asof_v1.json`, which 29 claims read - and the record is accurate rather
#: than wrong, because that WAS this file's name when the run happened. Following it today is what
#: is dangerous, so the entry declares it. Swept 2026-09-22: 58 entries, 1 like this, 22 naming no
#: `--out` at all (their stand writes a fixed path).
mismatched, no_out = [], 0
for f, spec in sorted(package.items()):
    outs = [spec["command"][i + 1] for i, t in enumerate(spec["command"])
            if t == "--out" and i + 1 < len(spec["command"])]
    outs += [t.split("=", 1)[1] for t in spec["command"] if t.startswith("--out=")]
    if not outs:
        no_out += 1
        continue
    if not any(o.replace("\\", "/") == f for o in outs):
        if not spec.get("writes_elsewhere"):
            mismatched.append(f"{f} -> --out {outs}")
check("every --out names its own artifact, or the entry says where it really writes",
      not mismatched, " | ".join(mismatched[:2]))
check("the declared exception is still exactly one",
      sum(1 for s in package.values() if s.get("writes_elsewhere")) == 1)
check("and there were entries with an --out to compare", len(package) - no_out >= 20,
      f"{len(package) - no_out} of {len(package)}")

print("\n- the rule is exercised, so a green line above can still go red -")
sup_stand = "python research/supersession_bench.py"
check("a two-arm command against a seven-arm file leaves the foreign arms unexplained",
      {"mem0", "zep"} - producible(f"{sup_stand} --arms nevertwice,naive --runs 2")
      == {"mem0", "zep"})
check("--runs and --sleep are accounted for rather than ignored",
      producible(f"{sup_stand} --arms nevertwice --runs 2 --sleep")
      == {"nevertwice", "nevertwice_run2", "nevertwice_after_sleep", "nevertwice_after_sleep_run2"},
      str(sorted(producible(f"{sup_stand} --arms nevertwice --runs 2 --sleep"))))
_sup_pool = next((c["command"] for c in claims if c.get("raw") == "research/results/supersession_v1.json"
                  and "--pool" in (c.get("command") or "")), None)
if _sup_pool and arms_on_disk("research/results/supersession_v1.json") is not None         and pooled_arms(tokens(_sup_pool)) is not None:
    check("a --pool command is read from the run files it names: exactly the pooled file's arms",
          producible(_sup_pool) == arms_on_disk("research/results/supersession_v1.json"),
          f"{sorted(producible(_sup_pool))} vs {sorted(arms_on_disk('research/results/supersession_v1.json'))}")
    _no_with = _sup_pool.split(" --with ")[0] + " --out x.json"
    check("and the same pool without its --with files cannot write the arms those files carried",
          arms_on_disk("research/results/supersession_v1.json") - producible(_no_with) >= {"mem0", "zep"},
          str(sorted(producible(_no_with))))
_asof_cmd = next((c["command"] for c in claims if c.get("raw") == "research/results/asof_v1.json"
                  and "--with" in (c.get("command") or "")), None)
if _asof_cmd and arms_on_disk("research/results/asof_v1.json") is not None and producible(_asof_cmd) is not None:
    check("a --with outside a pool is read from its files too: the as-of command makes every arm on disk",
          not arms_on_disk("research/results/asof_v1.json") - producible(_asof_cmd),
          f"{sorted(producible(_asof_cmd))} vs {sorted(arms_on_disk('research/results/asof_v1.json'))}")
    check("and without its --with files it cannot make the zep arm they carried",
          "zep" not in producible(_asof_cmd.split(" --with ")[0] + " --out x.json"))
check("and a command with no --arms at all is read as the stand's default, not as 'anything'",
      producible(f"{sup_stand} --out x.json") == {"nevertwice", "naive"},
      str(sorted(producible(f"{sup_stand} --out x.json"))))
#: Both directions, through the same `disagreement` the loop above calls - so weakening it to a
#: containment reddens HERE. A shrunk file leaves nothing unexplained while the entry still
#: declares two arms; a grown one leaves an arm nobody declared.
check("a file that LOST its declared arms is refused, not only one that gained arms",
      disagreement({"nevertwice", "naive"}, {"nevertwice", "naive"}, {"mem0", "zep"})
      == {"mem0", "zep"})
check("and a file that gained an undeclared arm is refused by the same line",
      disagreement({"nevertwice", "naive", "zep"}, {"nevertwice", "naive"}, set()) == {"zep"})
check("while a file that agrees with its entry passes",
      not disagreement({"nevertwice", "naive", "zep"}, {"nevertwice", "naive"}, {"zep"}))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
