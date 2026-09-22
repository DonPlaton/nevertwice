#!/usr/bin/env python3
"""The command a claim names can be run, and a script that ignores it is caught.

`tools/remeasure.py --restore` and every re-measurement campaign take their work from a claim's
`command` field. Four separate times this month a campaign document named a command that could
not run: a flag no script defined, `--save` on five scripts that only take `--out`, a sixth found
only after the checker stopped matching substrings, and one script with no argument parsing at
all - which would have swallowed `--save`, exited zero, written nothing, and left eleven claims
unsupported behind an apparently successful run.

That last shape is the one worth the tool. A command that FAILS costs a restart. A command that
succeeds and produces nothing costs the campaign its result and reports success.

`tools/check_campaign_commands.py` reads the flags out of each script's own parser and compares
them against the register. This suite pins the three ways a script in this repository reads its
arguments, because getting that wrong in either direction is a false alarm or a silent pass - and
the tool's own first version got it wrong, reporting four scripts as rejecting flags they accept
through a hand-rolled walk over `sys.argv`.

    python tests/_test_campaign_commands_can_run.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import check_campaign_commands as cc  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="cmdcheck_"))

SCRIPTS = {
    "with_argparse.py": (
        "import argparse\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--out')\n"
        "ap.add_argument('--print', action='store_true')\n"
        "ap.parse_args()\n"),
    "with_argv.py": (
        "import sys\n"
        "SAVE = '--save' in sys.argv\n"
        "DATA = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--data=')), 'x')\n"),
    "reads_nothing.py": "print('a report, and nothing else')\n",
    # The trap the substring checker fell into: the word appears, the flag does not.
    "mentions_save.py": "import sys\nsaved_rows = []\nif '--out' in sys.argv:\n    pass\n",
}
for name, body in SCRIPTS.items():
    (TMP / name).write_text(body, encoding="utf-8", newline="")


print("# each of the three ways a script reads its arguments is recognised")

for name, expected, n_at_least in (("with_argparse.py", "argparse", 3),
                                   ("with_argv.py", "sys.argv", 3),
                                   ("reads_nothing.py", "no argument parsing", 0)):
    flags, how = cc.declared_flags(TMP / name)
    check(f"{name} reads its arguments by {expected}", how == expected, how)
    check(f"and {len(flags)} flag(s) were found in it", len(flags) >= n_at_least, str(sorted(flags)))

flags, how = cc.declared_flags(TMP / "mentions_save.py")
check("a script that merely contains the word 'save' does not declare --save",
      "--save" not in flags, str(sorted(flags)))


print("# and the verdicts follow from that, in both directions")

#: `audit` resolves entries against the repository root, so the fake scripts are addressed
#: relative to it - the point here is the verdict logic, not path resolution.
rel = TMP.relative_to(TMP.anchor)


def verdict(command: str) -> str:
    return cc.audit({command: 1})[0]["verdict"]


cc.ROOT, saved_root = TMP, cc.ROOT
try:
    check("an argparse script accepts a flag it declares",
          verdict("python with_argparse.py --out x.json") == "ok")
    check("and rejects one it does not",
          "unrecognised" in verdict("python with_argparse.py --save"))
    check("a hand-rolled sys.argv script accepts the literals it compares against",
          verdict("python with_argv.py --save --data=s") == "ok")
    check("and rejects one that appears nowhere in it",
          "unrecognised" in verdict("python with_argv.py --nonesuch"))

    #: The dangerous case and its false alarm, which are one line apart.
    check("a script that parses nothing SWALLOWS a flag, and that is reported",
          "swallowed" in verdict("python reads_nothing.py --save"))
    check("but running the same script with no flags is fine",
          verdict("python reads_nothing.py") == "ok")

    check("a command naming a file that does not exist is reported",
          "does not exist" in verdict("python no_such_script.py --out x"))
finally:
    cc.ROOT = saved_root


print("# and the register's own commands are all runnable today")

import json  # noqa: E402

manifest = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
pending = {}
for c in manifest["claims"]:
    if c.get("pending_remeasure") and c.get("command"):
        pending[c["command"]] = pending.get(c["command"], 0) + 1
rows = cc.audit(pending)
bad = [r for r in rows if r["verdict"] not in ("ok", "module")]
check(f"every one of the {len(rows)} commands behind a pending claim can run as written",
      not bad, "; ".join(f"{r['command']}: {r['verdict']}" for r in bad[:3]))
check("and there are commands to check, so the line above proved something", len(rows) > 10,
      f"only {len(rows)} commands found")

print()
print(f"campaign commands can run: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
