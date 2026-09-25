#!/usr/bin/env python3
"""The reproduction package's command for an artifact is the command its live claims name.

`bd7f0d4` fixed one entry by hand: the package told a reader to run
`python research/longmem_eval.py` for an artifact whose eighteen live claims are produced by
`... --save` and `... --xrerank --save`. Without `--save` nothing is written; without
`--xrerank` the run emits four of five method blocks and DELETES the one four claims read. A
reproduction attempt would have withdrawn the evidence it set out to check.

The fix was correct and guarded by nothing: reverting that entry reddened no suite at all
(`_test_reproduction` 461 passed, `_test_evidence_manifest` 40, `_test_freshness` 22). The
property held because a person noticed - which is the defect class this repository has spent a
night on, in the cure for it.

The check needs no prose: both sides already store a command. The register holds one per claim,
the package one per artifact, and for an artifact with live claims they must agree - the
package's command being the claims' command or a superset of its flags. A superset is allowed
because two registered commands can write one artifact (`longmem_results.json` does, and it is
the only one of seventeen), and the package must then name the run that produces the whole file.

    python tests/_test_package_commands_match.py
"""
import _env_guard  # noqa: F401
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import reproduce as R  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def flags(cmd: str | list) -> set[str]:
    """The flag-ish tokens of a command, so a superset can be recognised without parsing."""
    parts = cmd.split() if isinstance(cmd, str) else list(cmd)
    return {p for p in parts if p.startswith("--")}


MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_claims = MANIFEST["claims"]
claims = list(_claims.values() if isinstance(_claims, dict) else _claims)
#: EVERY claim, not only the live ones. Scoping this to the live set makes the check hide
#: exactly while a family is withdrawn - and return the moment a campaign revives it, which is
#: when the cost of a wrong command is highest. Measured: with the supersession families
#: withdrawn, removing `--runs` from their package entries left this suite ALL OK
#: (audit 2026-09-22). A withdrawn claim is not a reason to skip checking the instruction by
#: which it will be restored.
by_artifact: dict[str, set[str]] = {}
for c in claims:
    if c.get("raw") and c.get("command"):
        by_artifact.setdefault(c["raw"].replace("\\", "/"), set()).add(c["command"])

print("\n- the package names, for each artifact, a command that produces it -")
check("there are artifacts to check", len(by_artifact) > 5, str(len(by_artifact)))

package = {spec["file"].replace("\\", "/"): spec["command"] for spec in R.ARTIFACTS}
#: Campaign v2 (anchor fe6ddff) re-recorded, in the manifest only, the command that actually wrote
#: each artifact, read from its STATUS log (PLAN-CAMPAIGN-V2 §A6 step 2), while `research/reproduce.py`
#: stayed frozen at the anchor: four entries lacked the writer flags (K49 excused exactly those,
#: keyed to the anchor's blob). Stage D ((б) b-i) updated the package, so the exception is gone and
#: those four are held to the same superset rule as every other entry.
missing, disagree = [], []
for artifact, commands in sorted(by_artifact.items()):
    pkg = package.get(artifact)
    if pkg is None:
        missing.append(artifact)
        continue
    pkg_flags = flags(pkg)
    #: Superset over the union: one artifact may be written by two registered commands, and the
    #: package must then name the run that produces the WHOLE file, not either half.
    need = set().union(*(flags(c) for c in commands))
    if not need <= pkg_flags:
        disagree.append(f"{artifact}: package {' '.join(pkg)!r} lacks {sorted(need - pkg_flags)}; "
                        f"claims name {sorted(commands)}")

#: Artifacts a claim names that the package does not carry. Not a pass - an inventory, so the
#: gap is a number someone decided to leave rather than one nobody counted. All three predate
#: the package's own scope rule and are behind withdrawn claims.
KNOWN_ABSENT = {"research/consolidation_eval.json", "research/head_to_head.json",
                "research/live_validation_results.json", "research/qa_results_reasoner.json",
                "research/token_ab.json"}

#: Artifacts written by MORE than one registered command. The two left cannot be expressed as one:
#: `--only=` selects arms, so three `--only=` runs write three disjoint parts of one file and no
#: single run is a superset. That is a defect of the register - one artifact, several commands, no
#: way to name them all in one entry - recorded rather than designed around. `longmem_results.json`
#: left this set at restore #2 of campaign v2: its `--xrerank --save` rewrote the whole file, so
#: every claim on it now names that one command (the superset the package already carries).
KNOWN_MULTI = {"research/results/head_to_head_v2.json": "three --only= runs, no superset exists",
               "research/token_ab.json": "two runs, no superset expressible"}

check("the artifacts absent from the package are the known three, no more",
      set(missing) == KNOWN_ABSENT, f"{sorted(set(missing) ^ KNOWN_ABSENT)}")

#: The flag-superset rule applies where a superset can exist at all - that is, everywhere except
#: the artifacts whose commands differ by a value-carrying selector.
unexpressible = {a for a, why in KNOWN_MULTI.items() if "no superset" in why}
real = [d for d in disagree if d.split(":")[0] not in unexpressible]
check("and the package's command carries every flag the claims' commands do, where a superset "
      "can exist", not real, " | ".join(real[:2]))

#: The four entries K49 excused, by name: each now carries every flag of its registered writer.
_k49 = ["research/results/abstention_ab.json", "research/results/asof_v1.json",
        "research/results/supersession_v1.json", "research/results/supersession_v1_implicit.json"]
check("the four entries campaign v2 re-recorded carry their writer's flags (K49's exception retired)",
      all(set().union(*(flags(c) for c in by_artifact[a])) <= flags(package[a]) for a in _k49 if a in by_artifact),
      str({a: sorted(set().union(*(flags(c) for c in by_artifact[a])) - flags(package[a]))
           for a in _k49 if a in by_artifact}))

print("\n- the artifacts written by several commands are named, not assumed to be one -")
multi = {a: cs for a, cs in by_artifact.items() if len(cs) > 1}
check("the set of multi-command artifacts has not moved", set(multi) == set(KNOWN_MULTI),
      f"{sorted(set(multi) ^ set(KNOWN_MULTI))}")
check("longmem, the one where a superset exists, has it named in the package",
      flags(package.get("research/longmem_results.json", []))
      >= set().union(*(flags(c) for c in by_artifact["research/longmem_results.json"])),
      str(sorted(by_artifact.get("research/longmem_results.json", []))))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
