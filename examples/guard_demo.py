#!/usr/bin/env python3
"""Nevertwice - the canonical story: a mistake in one session, prevented in the next.

Four beats, on a throwaway vault, with no model, no key and no network:

    session A   the mistake happens and the lesson is recorded
    session A   a guard is distilled from it - into a ledger, not your context
    session B   a fresh session is about to repeat it, and the guard fires
    session B   the corrected action passes clean

    python examples/guard_demo.py           # narrated, paced for a screen recording
    python examples/guard_demo.py --check   # deterministic transcript, fixed exit codes

`--check` is what CI runs. Its stdout is byte-identical on Linux, Windows and macOS and
across runs: no colours, no pauses, no timestamps, no temporary paths. The engine's own
log lines go to stderr, so stdout carries the transcript alone.

Exit codes are fixed, so a failure says which beat broke:

    0  all four beats held
    1  an unexpected error
    2  the lesson was not recorded
    3  no guard was distilled from it
    4  the repeat was NOT flagged - the prevention claim is what failed
    5  the corrected action was flagged anyway - a false positive

Record it for the README:
    asciinema rec -c "python examples/guard_demo.py" guard.cast
    agg --theme monokai --speed 1.2 guard.cast docs/guard.gif
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox  # noqa: F401, E402 - throwaway store, before any project import
sys.path.insert(0, str(ROOT / "nevertwice"))

import api      # noqa: E402
import guards as G  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOLD, GREEN, YELLOW, RED, DIM, CYAN, X = (
    "\033[1m", "\033[1;32m", "\033[1;33m", "\033[1;31m", "\033[2m", "\033[1;36m", "\033[0m")
if not sys.stdout.isatty():
    BOLD = GREEN = YELLOW = RED = DIM = CYAN = X = ""

PAUSE = float(os.environ.get("NEVERTWICE_DEMO_PAUSE", "1.3"))


def beat(t=None):
    sys.stdout.flush()
    time.sleep(t if t is not None else PAUSE)


def type_out(prefix, text, color=""):
    sys.stdout.write(prefix)
    for ch in text:
        sys.stdout.write(color + ch + X if color else ch)
        sys.stdout.flush()
        time.sleep(0.012)
    sys.stdout.write("\n")


MISTAKE = {
    "type": "mistake", "title": "sql-built-by-fstring",
    "description": "A filter was interpolated into the SQL string - an injection hole.",
    "prevention": "Never build SQL by f-string - pass values as query parameters.",
    "entities": ["database", "security"],
}
REPEAT = "cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")"
CORRECTED = 'cursor.execute("SELECT * FROM users WHERE name = ?", (name,))'


def check() -> int:
    """The same four beats, printed as a transcript a machine can diff.

    Every value printed here is fixed by the inputs above: the note stem, the regex the
    offline generator distils, and the guard's message. Nothing carries a date, a path or
    a duration, which is what lets CI compare this byte for byte across three platforms.
    """
    # `newline=""` stops Windows translating \n to \r\n, so the transcript is identical
    # on every platform rather than merely equivalent.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="")
    except Exception:                             # pragma: no cover - very old streams
        pass

    def line(beat: str, session: str, label: str, detail: str) -> None:
        sys.stdout.write(f"{beat} session {session}  {label:<22}{detail}\n")

    sys.stdout.write("nevertwice: prevented repeat (no model, no key, no network)\n")

    stems = api.remember_lessons([MISTAKE], project="app", embed=False)
    if not stems:
        sys.stdout.write("FAIL the lesson was not recorded\n")
        return 2
    line("1", "A", "mistake recorded", stems[0].split("-app-", 1)[-1]
         if "-app-" in stems[0] else stems[0])

    G.generate_from_vault("app", min_recurrence=1, use_llm=False)
    ledger = G.load_guards()
    if not ledger:
        sys.stdout.write("FAIL no guard was distilled from the lesson\n")
        return 3
    line("2", "A", "guard distilled", ledger[0]["pattern"])

    hits = api.guards_check(REPEAT, project="app")
    if not hits:
        sys.stdout.write("FAIL the repeat was not flagged\n")
        return 4
    line("3", "B", "repeat flagged", hits[0]["message"])

    if api.guards_check(CORRECTED, project="app"):
        sys.stdout.write("FAIL the corrected action was flagged too\n")
        return 5
    line("4", "B", "correction clean", "no guard fires")

    sys.stdout.write("OK 4/4 beats\n")
    return 0


def main():
    print(f"\n{BOLD}Nevertwice - memory that acts{X}  {DIM}(a guard fires before the mistake repeats){X}\n")
    beat()

    print(f"{DIM}Monday. Your agent hits a SQL-injection bug and Nevertwice records the lesson:{X}")
    beat(0.6)
    api.remember_lessons([{
        "type": "mistake", "title": "sql-built-by-fstring",
        "description": "A filter was interpolated into the SQL string - an injection hole.",
        "prevention": "Never build SQL by f-string - pass values as query parameters.",
        "entities": ["database", "security"]}], project="app", embed=False)
    G.generate_from_vault("app", min_recurrence=1, use_llm=False)
    print(f"  {GREEN}✓ lesson stored{X}  {DIM}(one file under git; it now sits in a guard ledger, "
          f"not your context - 0 tokens){X}")
    beat()

    print(f"\n{DIM}Thursday. A fresh session, a new file. The agent is about to write:{X}")
    beat(0.5)
    action = "cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")"
    type_out("  ", action, YELLOW)
    beat()

    print(f"\n{DIM}Nevertwice checks the edit against what you've learned…{X}")
    beat()
    hits = api.guards_check(action, project="app")
    if hits:
        print(f"\n  {RED}⛔ guard fires - a past mistake is about to repeat:{X}")
        print(f"     {BOLD}{hits[0]['message']}{X}")
        beat()
        print(f"\n{DIM}The agent corrects it before the bug ever lands:{X}")
        beat(0.5)
        type_out("  ", "cursor.execute(\"SELECT * FROM users WHERE name = ?\", (name,))", GREEN)
        beat()
        clean = api.guards_check("cursor.execute(\"SELECT * FROM users WHERE name = ?\", (name,))",
                                 project="app")
        print(f"  {GREEN}✓ clean - the guard stays silent now{X}" if not clean
              else f"  {RED}⚠ guard still firing - unexpected{X}")
    beat()

    print(f"\n{CYAN}That's memory that acts.{X} {DIM}Not a wall of recalled text every turn - a "
          f"single warning,{X}")
    print(f"{DIM}exactly when it matters, at zero tokens until it does.{X}")
    print(f"\n  {BOLD}github.com/DonPlaton/nevertwice{X}  {DIM}· local-first · MIT · works with your agent{X}\n")


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        try:
            raise SystemExit(check())
        except SystemExit:
            raise
        except Exception as exc:                  # noqa: BLE001 - report, do not traceback
            sys.stdout.write(f"FAIL unexpected error: {type(exc).__name__}: {exc}\n")
            raise SystemExit(1) from None
    main()
