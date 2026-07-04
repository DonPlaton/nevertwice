#!/usr/bin/env python3
"""Nevertwice — the 15-second "memory that acts" GIF: a guard fires before a real mistake repeats.

The single beat that earns the star. A throwaway vault, a mistake recorded once, then the agent
about to repeat it — and memory speaks up *before* the edit lands, at zero cost until this moment.

    python examples/guard_demo.py           # narrated, paced for a screen recording

Record it for the README:
    asciinema rec -c "python examples/guard_demo.py" guard.cast
    agg --theme monokai --speed 1.2 guard.cast docs/guard.gif
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = tempfile.mkdtemp(prefix="nevertwice-guard-")
os.environ["NEVERTWICE_HOME"] = _TMP
os.environ["VAULT"] = _TMP
os.environ["NEVERTWICE_CLOUD"] = "none"
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


def main():
    print(f"\n{BOLD}Nevertwice — memory that acts{X}  {DIM}(a guard fires before the mistake repeats){X}\n")
    beat()

    print(f"{DIM}Monday. Your agent hits a SQL-injection bug and Nevertwice records the lesson:{X}")
    beat(0.6)
    api.remember_lessons([{
        "type": "mistake", "title": "sql-built-by-fstring",
        "description": "A filter was interpolated into the SQL string — an injection hole.",
        "prevention": "Never build SQL by f-string — pass values as query parameters.",
        "entities": ["database", "security"]}], project="app", embed=False)
    G.generate_from_vault("app", min_recurrence=1, use_llm=False)
    print(f"  {GREEN}✓ lesson stored{X}  {DIM}(one file under git; it now sits in a guard ledger, "
          f"not your context — 0 tokens){X}")
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
        print(f"\n  {RED}⛔ guard fires — a past mistake is about to repeat:{X}")
        print(f"     {BOLD}{hits[0]['message']}{X}")
        beat()
        print(f"\n{DIM}The agent corrects it before the bug ever lands:{X}")
        beat(0.5)
        type_out("  ", "cursor.execute(\"SELECT * FROM users WHERE name = ?\", (name,))", GREEN)
        beat()
        clean = api.guards_check("cursor.execute(\"SELECT * FROM users WHERE name = ?\", (name,))",
                                 project="app")
        print(f"  {GREEN}✓ clean — the guard stays silent now{X}")
    beat()

    print(f"\n{CYAN}That's memory that acts.{X} {DIM}Not a wall of recalled text every turn — a "
          f"single warning,{X}")
    print(f"{DIM}exactly when it matters, at zero tokens until it does.{X}")
    print(f"\n  {BOLD}github.com/DonPlaton/nevertwice{X}  {DIM}· local-first · MIT · works with your agent{X}\n")


if __name__ == "__main__":
    main()
