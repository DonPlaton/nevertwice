#!/usr/bin/env python3
"""Every command the register names can actually be run, before an hour is spent finding out.

The register records, for each claim, the command that produced it. A re-measurement campaign
takes its work from that field. Four separate times this month a campaign document named a
command that could not run: a flag the script never defined (`corpus_pin.py --commit --hash`,
which does not exist), `--save` on five scripts that only take `--out`, a sixth found only after
the checker stopped matching substrings, and one script with no argument parsing at all - which
would have swallowed `--save`, exited zero, written nothing, and left eleven claims unsupported
behind an apparently successful run.

That last one is the shape worth naming. A command that FAILS costs the operator a restart. A
command that succeeds and produces nothing costs the campaign its result and says so nowhere.

So this reads the flags out of each script's own parser - by AST, not by grepping the source, as
a substring match is what let the sixth through - and compares them against the commands the
register actually carries. It runs nothing and writes nothing.

    python tools/check_campaign_commands.py              # every command with a pending claim
    python tools/check_campaign_commands.py --all        # every command in the register
    python tools/check_campaign_commands.py --json PATH  # machine-readable

Exit status is 0 when every command's flags are accepted by its script, 1 when any command would
fail or silently do nothing, and 2 when the register cannot be read.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: Flags argparse gives every parser for free.
BUILTIN = {"-h", "--help"}


def declared_flags(path: Path) -> tuple[set[str], str]:
    """(flags the script accepts, how it reads them).

    Read from the AST rather than by searching the text: an earlier checker looked for the flag
    as a substring and passed a script whose source merely contained the letters `save` inside
    another word.

    Three ways a script here reads its arguments, and they need different treatment - the first
    version of THIS function knew only the first, and reported four scripts as rejecting flags
    they accept perfectly well:

    * `argparse` - the flags are the string arguments to `add_argument`, and the set is complete;
    * a hand-rolled walk over `sys.argv` - which nine of these scripts do, comparing against
      literals like `"--save" in sys.argv` - so the flags are the `--`-prefixed string literals
      in the source, and the set is as complete as the source is honest;
    * neither, which is the dangerous one: the script takes no arguments at all, a flag is
      swallowed in silence, and whatever it was meant to produce is never produced.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (SyntaxError, OSError):
        return set(), "unreadable"
    argparse_flags: set[str] = set()
    literals: set[str] = set()
    reads_argv = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name == "add_argument":
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        argparse_flags.add(a.value)
        elif isinstance(node, ast.Attribute) and node.attr == "argv":
            reads_argv = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("--") and len(v) > 2:
                literals.add(v.split("=", 1)[0])
    if argparse_flags:
        return argparse_flags | BUILTIN, "argparse"
    if reads_argv:
        return literals | BUILTIN, "sys.argv"
    return set(), "no argument parsing"


def entry_of(command: str) -> Path | None:
    parts = shlex.split(command, posix=False)
    for tok in parts[1:]:
        if tok.endswith(".py"):
            return ROOT / tok.replace("\\", "/")
        if tok == "-m":
            continue
    return None


def flags_of(command: str) -> list[str]:
    out = []
    for tok in shlex.split(command, posix=False):
        if tok.startswith("--"):
            out.append(tok.split("=", 1)[0])
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            out.append(tok)
    return out


def audit(commands: dict[str, int]) -> list[dict]:
    rows = []
    for command, n in sorted(commands.items(), key=lambda kv: -kv[1]):
        entry = entry_of(command)
        row: dict = {"command": command, "claims": n, "entry": str(entry) if entry else None}
        if command.strip().startswith("python -m "):
            row["verdict"] = "module"           # `-m nevertwice.guards pack --count`
            rows.append(row)
            continue
        if entry is None:
            row["verdict"] = "no entry file named in the command"
            rows.append(row)
            continue
        if not entry.is_file():
            row["verdict"] = "entry file does not exist"
            rows.append(row)
            continue
        declared, how = declared_flags(entry)
        used = flags_of(command)
        row["reads_arguments"] = how
        if how == "no argument parsing":
            #: The dangerous one, but only when the command actually passes something: a script
            #: that takes no arguments swallows a flag in silence, exits zero, and never produces
            #: whatever the flag was for. A command with no flags at all is simply a script being
            #: run, and saying otherwise would be a false alarm of the kind this tool exists to
            #: prevent - two of the four it first reported were exactly that.
            if not used:
                row["verdict"] = "ok"
                rows.append(row)
                continue
            row["verdict"] = ("the script parses no arguments at all - flags are swallowed, the "
                              "run exits zero and writes nothing")
            row["unknown"] = used
            rows.append(row)
            continue
        if how == "unreadable":
            row["verdict"] = "the entry file does not parse"
            rows.append(row)
            continue
        unknown = [f for f in used if f not in declared]
        row["verdict"] = "ok" if not unknown else "unrecognised: " + ", ".join(unknown)
        row["unknown"] = unknown
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="every command in the register, not only those with a pending claim")
    ap.add_argument("--json", type=Path, help="write the rows here as well")
    args = ap.parse_args(argv)

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {MANIFEST}: {exc}")
        return 2

    counts: collections.Counter = collections.Counter()
    for c in manifest["claims"]:
        if not args.all and not c.get("pending_remeasure"):
            continue
        if c.get("command"):
            counts[c["command"]] += 1

    rows = audit(counts)
    bad = [r for r in rows if r["verdict"] not in ("ok", "module")]
    print(f"{len(rows)} distinct command(s) carrying "
          f"{'every' if args.all else 'a pending'} claim; {len(bad)} would not do what the "
          f"campaign expects\n")
    for r in rows:
        mark = "  ok  " if r["verdict"] in ("ok", "module") else " FAIL "
        print(f"{mark} {r['claims']:4d}  {r['command']}")
        if r["verdict"] not in ("ok", "module"):
            print(f"        {r['verdict']}")
    if bad:
        print(f"\n{sum(r['claims'] for r in bad)} pending claim(s) sit behind a command that "
              f"cannot run as written.")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="")
        print(f"\nwritten: {args.json}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
