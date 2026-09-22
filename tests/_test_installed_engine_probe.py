#!/usr/bin/env python3
"""The repo can tell whether the engine it measures is the engine that runs.

On 2026-09-19 a review read the owner's store, found 0 of 7,991 live typed notes carrying a
`[facts]` block, and concluded that `_replacement_guard` was inert in production. A peer recounted
independently and reached the same conclusion. Both were wrong in the same way: the hook the agent
actually invokes was the 2026-08-24 sync, a monolith carrying none of K6/K7/K8, so the store was
evidence about a build four weeks older than the one being judged. Nothing in the repo could have
said so - there was no probe for the gap between the measured engine and the running one.

This pins the probe's two load-bearing behaviours. It cannot assert the owner's machine state (that
is not hermetic and not the repo's business), so it feeds `features_in` and `installed_hook_path`
sources and settings of both shapes and checks they are told apart.

    python tests/_test_installed_engine_probe.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before anything reads it

import check_installed_engine as probe  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


print("# a source carrying the K-series is told apart from one that predates it")
NEW = "\n".join(f"{name} = 1" for name in probe.FEATURES)
OLD = "def write_typed_note(folder, item):\n    return ''\n"

new_f, old_f = probe.features_in(NEW), probe.features_in(OLD)
check("every listed feature is found in a source that has them all", all(new_f.values()),
      f"missed {sorted(n for n, v in new_f.items() if not v)}")
check("and none is found in a source that predates them", not any(old_f.values()),
      f"claimed {sorted(n for n, v in old_f.items() if v)}")

print("# the feature list is not empty and names the ledger entry that shipped each one")
check("the list has features", len(probe.FEATURES) >= 8, f"{len(probe.FEATURES)} listed")
check("each carries provenance", all(v and v.strip() for v in probe.FEATURES.values()))

print("# the hook path is read out of the settings file the agent actually uses")
with tempfile.TemporaryDirectory() as td:
    s = Path(td) / "settings.json"
    s.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"hooks": [{"type": "command",
                                   "command": "C:/py.exe C:/Users/x/.claude/scripts/memory_hook.py"}]}],
    }}), encoding="utf-8")
    got = probe.installed_hook_path(s)
    check("the invoked hook path is recovered from the command line",
          got is not None and got.name == "memory_hook.py", f"got {got!r}")

    #: A BOM is what `hosts.py` was found reading blind in the same review; the probe reads the same
    #: file, so it reads it the same tolerant way.
    s.write_text("\ufeff" + json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"command": "py /opt/scripts/memory_hook.py"}]}]}}), encoding="utf-8")
    check("a settings file with a BOM is still parsed", probe.installed_hook_path(s) is not None)

    s.write_text("{not json", encoding="utf-8")
    check("an unparseable settings file is a None, not an exception",
          probe.installed_hook_path(s) is None)

    s.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"command": "ruff check ."}]}]}}),
                 encoding="utf-8")
    check("a settings file that invokes no memory hook is a None",
          probe.installed_hook_path(s) is None)

print("")
print("- the census reads BOTH sides of the split, not one -")
#: The repository side was taught about the split (`repo_engine_files` walks the loader's own
#: `ENGINE_PARTS`); the installed side kept reading the single file the hook command names, which
#: after the split is a 3 KB loader. So the probe compared 3 KB against ~460 KB and called every
#: feature living in a part missing: measured 2026-09-22, right after a successful
#: `sync_install.py --apply`, it printed "installed hook LAGS the repo on 11 feature(s)" with all
#: eleven physically present beside the loader. Found by the auditing session; a check extended on
#: one side of a seam is the split's own defect.
_td2 = tempfile.mkdtemp()
_dir = Path(_td2) / "scripts"
_dir.mkdir(parents=True, exist_ok=True)
(_dir / "memory_hook.py").write_text("import _engine  # loader only\n", encoding="utf-8")
_parts = "\n".join(f"{name} = 1" for name in list(probe.FEATURES)[:4])
(_dir / "_engine_write.py").write_text(_parts + "\n", encoding="utf-8")

_loader_only = probe.features_in((_dir / "memory_hook.py").read_text(encoding="utf-8"))
check("reading the loader alone finds none of the features it loads",
      not any(_loader_only.values()), str(sum(_loader_only.values())))
_both = probe.features_in("".join(f.read_text(encoding="utf-8")
                                  for f in [_dir / "memory_hook.py"]
                                  + sorted(_dir.glob("_engine*.py"))))
check("reading the loader AND its parts finds them",
      sum(_both.values()) == 4, str(sum(_both.values())))

#: And the live probe, on this machine's real install, must not report a lag it cannot name.
_rec = probe.probe()
check("the probe reads more than the loader from the install",
      _rec.get("installed_engine_bytes", 0) > 4 * _rec.get("installed_bytes", 1),
      f"engine {_rec.get('installed_engine_bytes')} vs loader {_rec.get('installed_bytes')}")
check("and it names the files it read",
      len(_rec.get("installed_parts") or []) >= 2, str(_rec.get("installed_parts"))[:90])


print()
print(f"installed engine probe: {P} passed, {F} failed")
sys.exit(1 if F else 0)
