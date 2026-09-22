#!/usr/bin/env python3
"""What PreToolUse loads is what PreToolUse uses.

`guards.py` is imported before every Edit, Write and Bash the agent runs. It used to import
`subprocess` and `hashlib` at module level, and neither is reachable from the four functions that
path actually calls. Measured marginally, after `json`, `re`, `os`, `sys` and `marshal` are
already in: `subprocess` 3.91 ms, `hashlib` 3.16 ms. Seven milliseconds of every tool call spent
loading code that could not run.

This suite pins the property behaviourally rather than by grepping the import lines, because the
grep passes on a file that imports `subprocess` through something else. It imports the module in
a fresh interpreter and asks `sys.modules` - the question a profiler would ask.

It also pins the reason: a call-graph walk from the four entry points the hot path uses, so that
moving one of the three functions onto that path goes red here rather than quietly costing seven
milliseconds again.

    python tests/_test_hot_path_imports.py
"""
from __future__ import annotations

import ast
import copy as _copy
import json
import pickle as _pickle
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths

PASSED = FAILED = 0

#: What PreToolUse calls into `guards.py`. `emit_pretooluse_guard` in the engine reaches exactly
#: these; everything else in the module belongs to guard creation, feedback or the CLI.
HOT_ENTRIES = ("load_guards", "check", "already_delivered", "record_fired")

#: Modules no hot-path function needs, and what each cost when it was loaded anyway.
BANNED = {"subprocess": "3.91 ms", "hashlib": "3.16 ms"}


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("# importing guards does not drag in what the hot path cannot call")

probe = ("import sys; sys.path.insert(0, %r); import guards; "
         "print(','.join(sorted(m for m in %r if m in sys.modules)))"
         % (str(PKG), tuple(BANNED)))
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
loaded = [m for m in r.stdout.strip().split(",") if m]
check("guards imports cleanly in a bare interpreter", r.returncode == 0, r.stderr[-300:])
check(f"neither of {sorted(BANNED)} is loaded by importing guards", not loaded,
      "loaded: " + ", ".join(f"{m} ({BANNED[m]})" for m in loaded))


print("# and the reason still holds: no hot-path function reaches them")

tree = ast.parse((PKG / "guards.py").read_text(encoding="utf-8"))
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
#: `all(...)` over an empty HOT_ENTRIES is True, so the count belongs in a condition and not only
#: in the sentence (swept 2026-09-22).
check("there are hot-path entry points to check", len(HOT_ENTRIES) >= 3, str(len(HOT_ENTRIES)))
check(f"all {len(HOT_ENTRIES)} hot-path entry points exist",
      all(e in funcs for e in HOT_ENTRIES),
      str([e for e in HOT_ENTRIES if e not in funcs]))

calls = {name: {c.func.id for c in ast.walk(n)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
         for name, n in funcs.items()}
reach, queue = set(), list(HOT_ENTRIES)
while queue:
    f = queue.pop()
    if f in reach or f not in calls:
        continue
    reach.add(f)
    queue += list(calls[f])

#: Which function owns each banned import, by reading the source rather than restating it: a
#: fourth use added tomorrow is covered on the day it is written.
owners: dict[str, set[str]] = {m: set() for m in BANNED}
for name, node in funcs.items():
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.name in owners:
                    owners[alias.name].add(name)
for mod, where in sorted(owners.items()):
    check(f"{mod} is imported inside a function, not at module scope", bool(where),
          "no function imports it - did it go back to the top of the file?")
    on_hot = sorted(where & reach)
    check(f"and none of its {len(where)} owner(s) is reachable from the hot path", not on_hot,
          f"{on_hot} is called from {sorted(HOT_ENTRIES)} - either the hot path grew or the "
          f"import has to move again")

module_level = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
check("neither name is imported at module scope either", not (module_level & set(BANNED)),
      str(sorted(module_level & set(BANNED))))
print(f"       (the walk reaches {len(reach)} of {len(funcs)} functions from "
      f"{', '.join(HOT_ENTRIES)})")

print("# and the engine's own patterns are compiled only when something uses them")

#: Two dozen module-level regular expressions cost 3.64 ms of a 13.07 ms cold import, and
#: PreToolUse forces none of them: the guard path matches through `re.search(pattern_string, ..)`,
#: which uses `re`'s internal cache, and these belong to session start, session end and the write
#: path. `_lazy_re` compiles on first use. The property worth pinning is not that the proxy exists
#: - that is one grep - but that the hot path still does not touch them, because moving one use
#: onto that path costs the milliseconds back with nothing going red.
#: `eager` is counted separately, and it is not decoration. The lazy population is found by
#: `type(v).__name__ == '_lazy_re'`, which is the same predicate the checks below assert - so a
#: pattern put back to `re.compile` leaves the sample and the suite goes on saying "all lazy
#: patterns are lazy" while one is not. Counting the compiled `re.Pattern` objects on the module
#: asks the question the error message already claims to be asking: has a pattern moved.
probe = (
    "import sys, json, re; sys.path.insert(0, %r)\n"
    "import memory_hook as m\n"
    "lazy = [v for v in vars(m).values() if type(v).__name__ == '_lazy_re']\n"
    "eager = sum(1 for v in vars(m).values() if isinstance(v, re.Pattern))\n"
    "before = sum(1 for v in lazy if v._compiled is not None)\n"
    "m.emit_pretooluse_guard({'tool_name': 'Bash', 'tool_input': {'command': 'rm -rf /tmp/x'}},\n"
    "                        %r)\n"
    "after = sum(1 for v in lazy if v._compiled is not None)\n"
    "print(json.dumps({'total': len(lazy), 'eager': eager,\n"
    "                  'before': before, 'after': after}))\n"
) % (str(PKG), str(ROOT))
env = dict(os.environ)
store = tempfile.mkdtemp(prefix="lazyre_")
env.update({"NEVERTWICE_HOME": store, "NEVERTWICE_VAULT": store, "NEVERTWICE_CLOUD": "none"})
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=240,
                   env=env)
counts = {}
for line in reversed((r.stdout or "").splitlines()):
    try:
        counts = json.loads(line)
        break
    except ValueError:
        continue
check("the probe ran", bool(counts), f"exit {r.returncode}: {r.stderr[-300:]}")
if counts:
    check(f"all {counts['total']} engine patterns are lazy, none compiled at import",
          counts["total"] >= 20 and counts["before"] == 0,
          f"{counts['before']} were already compiled")
    check("and no eagerly compiled pattern is left on the module at all",
          counts.get("eager") == 0,
          f"{counts.get('eager')} `re.Pattern` object(s) on the module - one went back to "
          f"`re.compile` and so left the lazy sample, which is how this suite could otherwise "
          f"keep reporting that every lazy pattern is lazy")
    check("and a PreToolUse guard forces none of them",
          counts["after"] == 0,
          f"{counts['after']} compiled during the guard - either the hot path grew or a pattern "
          f"moved onto it")

#: Both call forms, because the auditing session's own shim broke on the keyword one and only
#: SessionEnd used it - a whole event was failing while the paired measurement looked clean.
import re as _re  # noqa: E402

sys.path.insert(0, str(PKG))
import memory_hook as _m  # noqa: E402

check("a lazy pattern takes its flags positionally and by keyword",
      _m._lazy_re(r"a", _re.I).flags == _m._lazy_re(r"a", flags=_re.I).flags == _re.I | 32)
check("and forwards whatever attribute is asked for, not a fixed list",
      _m._lazy_re(r"(a)(b)").groups == 2 and _m._lazy_re(r"ab").fullmatch("ab") is not None)
check("compiling is deferred until something asks",
      _m._lazy_re(r"z")._compiled is None)

#: `__slots__` plus `__getattr__` is a recursion trap, and the proxy has both. `copy`, `deepcopy`
#: and `pickle` build an instance through `__new__` without running `__init__`, so the slots are
#: unset; the first read of `self._compiled` raises `AttributeError`, which lands in `__getattr__`,
#: which reads `self._compiled` again. Without the guard both lines below raise `RecursionError`
#: instead - and the whole battery stays green, all 197 suites, because nothing else in the
#: package copies a pattern (checked by the audit, 2026-09-22). This is the suite that would have
#: to notice, so it does.
_bare = _m._lazy_re.__new__(_m._lazy_re)          # exactly what copy and pickle do
try:
    _bare.pattern
    check("an instance built without __init__ raises rather than recursing", False)
except AttributeError:
    check("an instance built without __init__ raises rather than recursing", True)
except RecursionError:
    check("an instance built without __init__ raises rather than recursing", False)

try:
    check("copy.copy of a pattern returns a pattern",
          type(_copy.copy(_m._lazy_re(r"a"))) is _m._lazy_re)
except RecursionError:
    check("copy.copy of a pattern returns a pattern", False)

try:
    _rt = _pickle.loads(_pickle.dumps(_m._lazy_re(r"a\d+")))
    check("a pickled pattern survives the round trip and still matches",
          type(_rt) is _m._lazy_re and _rt.findall("a1 a22") == ["a1", "a22"])
except RecursionError:
    check("a pickled pattern survives the round trip and still matches", False)

print()
print(f"hot-path imports: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
