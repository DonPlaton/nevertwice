#!/usr/bin/env python3
"""The scheduled-task watchdog judges the last run, not the registration.

`health.txt` is the only automatic signal this system produces about itself, and the task line in
it came from `manage_tasks.tasks_health()`, which built its verdict out of two facts: does the
task exist, and is it enabled. The result of the last run was never read, because `query_task`
asked `schtasks` for the short LIST and the short LIST does not carry it.

So a task that fails on every run reported as healthy. Measured on the owner's own machine,
2026-09-22: `health.txt` said `tasks=4/4 ok` while the weekly consolidation had last run on the
20th and exited -2147020576 (0x800710E0, "the request was refused"). Two days of a silent
watchdog, on the one job that compacts the store.

This is the third surface in this project found saying WIRED where the question was DELIVERING -
`install.py` ends with "Done. Restart your agent" without ever running the engine, and
`hosts.install_status()` reports on the settings file. The shape is worth naming because it
repeats: a check that can only see its own configuration will always be green.

The suite drives the real functions with `schtasks` faked at the `_schtasks` boundary, in both
the English and the Russian field spellings, because the field is localised and the label this
was first written against ("last result") is not the one the owner's machine prints.

    python tests/_test_watchdog_reads_the_result.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001 - a redirected stream
    pass

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import manage_tasks as mt  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


EN = ("TaskName:      {name}\n"
      "Status:        Ready\n"
      "Next Run Time: 27.09.2026 3:00:00\n"
      "Last Run Time: 20.09.2026 10:00:08\n"
      "Last Result:   {result}\n")
RU = ("Имя задачи:               {name}\n"
      "Состояние:                Готово\n"
      "Время следующего запуска: 27.09.2026 3:00:00\n"
      "Время прошлого запуска:   20.09.2026 10:00:08\n"
      "Прошлый результат:        {result}\n")


def _scheduler(results: dict, template: str = EN):
    """A fake `schtasks` where each task name answers with the given last result."""
    def fake(*args, timeout: int = 20):
        argv = list(args)
        name = argv[argv.index("/TN") + 1] if "/TN" in argv else ""
        if name not in results:
            return 1, "", "ERROR: The system cannot find the file specified."
        return 0, template.format(name=name, result=results[name]), ""
    return fake


ALL = [t["name"] for t in mt.TASKS]
HEALTHY = {n: 0 for n in ALL}


print("# a task that fails every run is not healthy")

with mock.patch.object(mt, "_schtasks", _scheduler(HEALTHY)):
    summary, degrades = mt.tasks_health()
check("all four succeeding reads as ok", summary.startswith("4/4 ok") and not degrades, summary)

#: 0x800710E0 as `schtasks` prints it: signed, which is why a check for a positive value would
#: have missed the very failure this was written for.
BROKEN = dict(HEALTHY, **{ALL[2]: -2147020576})
with mock.patch.object(mt, "_schtasks", _scheduler(BROKEN)):
    summary, degrades = mt.tasks_health()
check("one failing task drops the count", summary.startswith("3/4 ok"), summary)
check("and degrades the health line", degrades, summary)
check("and names the task and its result",
      "failing:" in summary and "-2147020576" in summary, summary)

with mock.patch.object(mt, "_schtasks", _scheduler(BROKEN, RU)):
    summary, degrades = mt.tasks_health()
check("the Russian field spelling is read too", summary.startswith("3/4 ok") and degrades,
      summary)


print("# but the results that are not failures stay quiet")

for label, code in (("never run yet", 267011), ("running right now", 267009)):
    with mock.patch.object(mt, "_schtasks", _scheduler(dict(HEALTHY, **{ALL[0]: code}))):
        summary, degrades = mt.tasks_health()
    check(f"a task {label} ({code}) is not a failure",
          summary.startswith("4/4 ok") and not degrades, summary)

#: A locale whose label we cannot match leaves the field unparsed. Reporting a failure there
#: would be the same mistake pointed the other way: a watchdog that cries wolf on a language.
UNKNOWN = ("TaskName: {name}\nStatus: Ready\nNext Run Time: 27.09.2026 3:00:00\n"
           "Resultat vum leschte Laf: -2147020576\n")
with mock.patch.object(mt, "_schtasks", _scheduler(HEALTHY, UNKNOWN)):
    summary, degrades = mt.tasks_health()
check("an unreadable result field is unknown, not failing",
      summary.startswith("4/4 ok") and not degrades, summary)


print("# and the query asks for the verbose listing, which is what carries the field")

src = (ROOT / "nevertwice" / "manage_tasks.py").read_text(encoding="utf-8")
check("query_task requests /V", '"/V"' in src,
      "without it schtasks omits Last Result and the verdict silently loses its only evidence")
check("the verdict looks at last_result", "last_result" in src and "_TASK_RESULT_OK" in src)

print()
print(f"watchdog reads the result: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
