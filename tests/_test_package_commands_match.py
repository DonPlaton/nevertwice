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
live = [c for c in claims if not (c.get("stale") or c.get("pending_remeasure"))]

by_artifact: dict[str, set[str]] = {}
for c in live:
    if c.get("raw") and c.get("command"):
        by_artifact.setdefault(c["raw"].replace("\\", "/"), set()).add(c["command"])

print("\n- the package names, for each artifact, a command that produces it -")
check("there are artifacts with live claims to check", len(by_artifact) > 5, str(len(by_artifact)))

package = {spec["file"].replace("\\", "/"): spec["command"] for spec in R.ARTIFACTS}
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

check("every artifact behind a live claim is in the package", not missing, ", ".join(missing[:3]))
check("and the package's command carries every flag the claims' commands do",
      not disagree, " | ".join(disagree[:2]))

print("\n- the one artifact written by two commands is known, not assumed -")
multi = {a: cs for a, cs in by_artifact.items() if len(cs) > 1}
check("at most one artifact carries two registered commands", len(multi) <= 1, str(sorted(multi)))
if multi:
    a, cs = next(iter(multi.items()))
    check(f"{a} is the known case and the package names the superset",
          flags(package.get(a, [])) >= set().union(*(flags(c) for c in cs)), str(sorted(cs)))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
