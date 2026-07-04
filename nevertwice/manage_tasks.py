#!/usr/bin/env python3
"""Self-register and verify the memory system's Windows scheduled tasks (audit
I-17). The safety-net tasks (catch-up, hourly health, weekly consolidation) were
previously unverifiable from inside the system — a deleted or disabled task
failed silently. This makes their state inspectable and re-creatable, and feeds
a task-status line into the hourly health check.

    python manage_tasks.py                       # check (read-only) — what's registered
    python manage_tasks.py --register            # create any MISSING tasks
    python manage_tasks.py --register --force     # (re)create ALL tasks

Read-only by default. Registration creates per-user tasks (no elevation needed)
and points each at its .bat wrapper; re-run after moving the scripts directory
or changing the Python install. Windows-only (degrades to a no-op elsewhere).
"""
import ctypes
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def _console_encoding() -> str:
    """The codepage schtasks writes in. On a localised Windows (e.g. ru-RU) a
    redirected console app emits the OEM codepage (cp866), NOT the ANSI/locale
    codepage Python's text mode assumes — decoding with the wrong one mangles
    the Cyrillic status fields. Falls back to utf-8 off Windows."""
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"

# Canonical safety-net tasks. Keep in sync with README "Scheduled tasks" and the
# mem_*.bat wrappers (which capture each run's log under %TEMP%).
TASKS = [
    {"name": "Nevertwice_Catchup",
     "bat": SCRIPTS / "mem_catchup.bat",
     "schedule": ["/SC", "HOURLY", "/MO", "4"],
     "desc": "catch up missed transcripts (every 4 h)"},
    {"name": "Nevertwice_Health",
     "bat": SCRIPTS / "mem_health.bat",
     "schedule": ["/SC", "HOURLY", "/MO", "1"],
     "desc": "liveness/backlog check -> health.txt (hourly)"},
    {"name": "Nevertwice_Consolidate",
     "bat": SCRIPTS / "mem_consolidate.bat",
     "schedule": ["/SC", "WEEKLY", "/D", "SUN", "/ST", "03:00"],
     "desc": "weekly dedup + compaction (Sun 03:00)"},
]


def _schtasks(*args, timeout: int = 20):
    """Run schtasks.exe → (returncode, stdout, stderr). Returns rc=127 with no
    output when schtasks is unavailable (non-Windows / locked down), so callers
    can treat 'no schtasks' the same as 'task not found' without crashing."""
    try:
        p = subprocess.run(["schtasks", *args], capture_output=True, timeout=timeout)
        enc = _console_encoding()
        out = p.stdout.decode(enc, "replace") if p.stdout else ""
        err = p.stderr.decode(enc, "replace") if p.stderr else ""
        return p.returncode, out, err
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


# Locale-tolerant field matching for `schtasks /Query /FO LIST`. Windows
# localises the labels, so we match on substrings of the EN and RU variants.
def _field(fields: dict, *needles: str) -> str | None:
    for k, v in fields.items():
        kl = k.lower()
        if any(n in kl for n in needles):
            return v
    return None


def query_task(name: str) -> dict:
    """Status of one task → {name, exists, enabled, next_run, status}. Existence
    is authoritative (schtasks return code); enabled/next_run are best-effort
    parsed from the localised LIST output and may be None."""
    rc, out, _ = _schtasks("/Query", "/TN", name, "/FO", "LIST")
    if rc != 0 or not out.strip():
        return {"name": name, "exists": False, "enabled": None,
                "next_run": None, "status": None}
    fields = {}
    for ln in out.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            fields.setdefault(k.strip(), v.strip())
    status = _field(fields, "status", "состоян") or ""
    enabled = None
    if status:
        low = status.lower()
        enabled = ("disab" not in low) and ("отключ" not in low)
    return {"name": name, "exists": True, "enabled": enabled,
            "next_run": _field(fields, "next run", "следующего запуска"),
            "status": status or None}


def register_task(spec: dict) -> tuple[bool, str]:
    """Create/replace one task pointing at its .bat wrapper. Always passes /F so
    it never blocks on the interactive 'replace?' prompt — callers decide whether
    to call it (skip-if-exists lives in the CLI)."""
    bat = spec["bat"]
    if not bat.exists():
        return False, f"wrapper missing: {bat.name}"
    rc, out, err = _schtasks("/Create", "/TN", spec["name"],
                             "/TR", f'"{bat}"', *spec["schedule"], "/F")
    if rc == 0:
        return True, "ok"
    tail = (err or out).strip().splitlines()
    return False, (tail[-1] if tail else f"schtasks rc={rc}")


def tasks_health() -> tuple[str, bool]:
    """(summary, degrades) for the hourly health line (audit I-17). Only degrades
    when the user has opted into the scheduled setup (≥1 task present) yet one is
    missing or disabled — a never-registered machine is informational, not a
    failure."""
    states = [query_task(t["name"]) for t in TASKS]
    present = [s for s in states if s["exists"]]
    if not present:
        return "not-registered", False
    missing = [s["name"] for s in states if not s["exists"]]
    disabled = [s["name"] for s in present if s["enabled"] is False]
    ok = len(present) - len(disabled)
    parts = [f"{ok}/{len(states)} ok"]
    short = lambda n: n.split("_")[-1]
    if missing:
        parts.append("missing:" + ",".join(short(n) for n in missing))
    if disabled:
        parts.append("disabled:" + ",".join(short(n) for n in disabled))
    return " ".join(parts), bool(missing or disabled)


def cmd_check() -> int:
    print("Scheduled tasks (memory safety net):")
    bad = 0
    for spec in TASKS:
        st = query_task(spec["name"])
        if not st["exists"]:
            bad += 1
            print(f"  [MISSING]  {spec['name']:26s} - {spec['desc']}")
        elif st["enabled"] is False:
            bad += 1
            print(f"  [DISABLED] {spec['name']:26s} - {spec['desc']}")
        else:
            nr = f" | next: {st['next_run']}" if st["next_run"] else ""
            print(f"  [OK]       {spec['name']:26s} - {st['status'] or 'present'}{nr}")
    if bad:
        print(f"\n{bad} task(s) need attention. Register with:"
              f"\n    python manage_tasks.py --register")
    else:
        print("\nAll safety-net tasks registered.")
    return 1 if bad else 0


def cmd_register(force: bool) -> int:
    print(f"Registering scheduled tasks ({'force-recreate all' if force else 'missing only'}):")
    fail = 0
    for spec in TASKS:
        st = query_task(spec["name"])
        if st["exists"] and not force:
            print(f"  =  {spec['name']:26s} already present (use --force to recreate)")
            continue
        ok, msg = register_task(spec)
        print(f"  {'+' if ok else '!'}  {spec['name']:26s} {msg}")
        fail += 0 if ok else 1
    if fail:
        print(f"\n{fail} task(s) failed to register (run from a normal user shell; "
              f"check the .bat wrappers exist).")
    return 1 if fail else 0


def main() -> int:
    args = sys.argv[1:]
    if "--register" in args:
        return cmd_register(force="--force" in args)
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
