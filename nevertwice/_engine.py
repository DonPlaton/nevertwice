#!/usr/bin/env python3
"""The engine's body: an ordered list of parts, executed into this namespace.

The body used to be one 8,426-line file. It is the same body, cut into contiguous slices along
its own section boundaries, and this file is the index that runs them.

Why slices executed here, and not modules imported. The engine's contract is its *namespace*.
`memory_hook.py` execs this file into its own `globals()` precisely so that `memory_hook.VAULT`,
`memory_hook.GUARD_ENFORCE`, `memory_hook.EARLIER_MAX_CHARS` and some eighty other rebinds reach
the code that reads them, and so that `_rebase_vault`'s `global` statements move every
vault-derived constant at once. A function sees the globals of the module it was DEFINED in, so
moving one into a real module would send its reads to that module's dictionary while every
rebind kept landing here: the suite that sets the name stays green and checks nothing. That is
the silent half of the 2026-08-13 and 2026-08-18 live-cache incidents, and it is why the parts
are exec-ed into this same dictionary rather than imported.

What the split buys is the file length: no part is longer than 1,500 lines, each is one area of
responsibility, and a traceback names the part and its own line. What it does not buy is
isolation, and the parts do not pretend otherwise - they share one namespace by design.

Why the cache below is hand-rolled, which is the part that needs justifying
--------------------------------------------------------------------------
The hooks run `memory_hook.py` as `__main__`, and CPython never serves `__main__` from
`__pycache__`, so the body was recompiled on every tool call: 48.6 ms of a 98.6 ms PreToolUse,
measured 2026-09-18. Routing it through a file loader fixed that, because a loader reads cached
bytecode.

Doing the same thing eight times does not stay free. Measured paired and alternating on
2026-09-22, monolith against parts, 25 rounds each: **+4.47 ms on the minimum, +6.00 ms on the
median** of a whole PreToolUse. What the split ADDED was not the code - re-running the body cost
0.50 ms against 0.68 ms for the eight pieces, measured warm in one process - it was eight
`spec_from_file_location` + `get_code` round trips,
each with its own stats, its own pyc header validation and its own unmarshal. Part 1's gate is
that PreToolUse does not grow, and a refactor that spends five milliseconds of every tool call
on file bookkeeping fails it.

That "0.50 ms" is a warm re-exec and it should not be read as what running the body costs. The
first exec in a fresh process is 10.32 ms, of which the module-level `re.compile` calls are
3.64 ms; a second exec in the same process is 2.22 ms because `re` has cached the patterns, and
7.31 ms again after `re.purge()`. The sentence above is about what the SPLIT added, not about
what the body costs - and reading it the other way is what would stop the next person looking
where several milliseconds actually are. They are in the patterns, and `_lazy_re` in
`_engine_config.py` is where that was taken.

So the eight code objects are cached as one marshalled tuple, validated the way CPython
validates a `.pyc` - source size and mtime, per part, all eight or none. The hot path is then
eight `stat` calls, one read and one `marshal.loads`, which is less work than the single
`get_code` the monolith did. Each code object keeps its own `co_filename`, so a traceback still
names the part and its own line; nothing about the failure mode changes.

Three things this must not do, all of them learned rather than guessed:

* **Never serve stale bytecode.** The key is `(size, mtime_ns)` for every part, in load order.
  Any mismatch, a short read, a marshal error, a Python whose `cache_tag` differs - recompile.
  The cache is a cache; it is never the source of truth.
* **Never fail the agent's tool call.** A read-only install, a missing part, a `__pycache__` the
  process may not create: each degrades to doing the work the slow way, or to the same quiet
  `SystemExit(0)` a missing engine has always produced.
* **Never race.** Five hook events can run at once. The cache is written to a per-process
  temporary and moved into place with `os.replace`, which is atomic on both platforms, so a
  concurrent reader sees either the whole old file or the whole new one.

`tests/_engine_source.py` reconstructs the body from these files for the suites that read the
engine as text, and `tests/_test_entry_point.py` pins that every name any part defines is an
attribute of `memory_hook`, that every function the engine defines has that one dictionary as
its `__globals__`, and that a stale or corrupt cache is never executed.
"""
#: Every name this file binds carries the `_ld_` prefix and is swept at the end. The parts run in
#: `memory_hook`'s own dictionary, which already holds that module's `_code` and `_spec` while
#: this file is running: a temporary called `_code` here would delete the entry point's own
#: variable out from under it, and `memory_hook.py` would die on its last line.
import marshal as _ld_marshal
import os as _ld_os
import sys as _ld_sys

#: Order is load-bearing: a part's module-level code runs after every earlier part.
ENGINE_PARTS = (
    "_engine_config.py",
    "_engine_text.py",
    "_engine_store.py",
    "_engine_notes.py",
    "_engine_write.py",
    "_engine_cards.py",
    "_engine_recall.py",
    "_engine_hooks.py",
)

#: Bumped when the cache's own layout changes, so an old file is refused rather than misread.
_ld_FORMAT = 1

#: `__file__` is the entry point's path (`memory_hook.py`), which is what the engine wants and
#: what `_sibling` walks from; the parts sit beside it. `os.path` rather than `pathlib`, because
#: this is the hot path and `pathlib` is not otherwise needed before the body loads.
_ld_here = _ld_os.path.dirname(_ld_os.path.abspath(__file__))
_ld_paths = [_ld_os.path.join(_ld_here, _ld_p) for _ld_p in ENGINE_PARTS]

try:
    _ld_key = tuple((_ld_st.st_size, _ld_st.st_mtime_ns)
                    for _ld_st in (_ld_os.stat(_ld_q) for _ld_q in _ld_paths))
except OSError as _ld_exc:                                # a half-copied install, not a bug here
    _ld_sys.stderr.write(
        f"[memory_hook] an engine part is missing or unreadable: {_ld_exc}\n"
        "[memory_hook] every _engine_*.py must sit beside memory_hook.py.\n"
        "[memory_hook] copy them across (tools/sync_install.py --apply copies them).\n")
    raise SystemExit(0)                                   # never fail the agent's tool call

_ld_cache = _ld_os.path.join(_ld_here, "__pycache__",
                             f"_engine_body.{_ld_sys.implementation.cache_tag}.bin")
_ld_codes = None
try:
    #: `loads` of one read, never `load` of the file object: `marshal.load` pulls the stream in
    #: small pieces and measured 4.78 ms against 1.00 ms for the same 487 KB read whole, which
    #: is the entire difference between this cache paying for itself and costing more than the
    #: eight loaders it replaces.
    with open(_ld_cache, "rb") as _ld_fh:
        _ld_blob = _ld_marshal.loads(_ld_fh.read())
    #: Shape first, then freshness. A truncated or foreign file unmarshals to anything at all,
    #: and indexing it before checking its shape would raise where a recompile is the answer.
    if (isinstance(_ld_blob, tuple) and len(_ld_blob) == 3 and _ld_blob[0] == _ld_FORMAT
            and _ld_blob[1] == _ld_key and len(_ld_blob[2]) == len(ENGINE_PARTS)):
        _ld_codes = _ld_blob[2]
except Exception:                                         # noqa: BLE001 - a cache never raises
    _ld_codes = None

if _ld_codes is None:
    #: The slow path, paid once per edit of a part. `compile` rather than a loader, because the
    #: loader's own `.pyc` is exactly what this cache replaces; writing both would be two writes
    #: of the same bytecode on every edit.
    _ld_codes = []
    for _ld_q in _ld_paths:
        with open(_ld_q, "rb") as _ld_fh:
            _ld_codes.append(compile(_ld_fh.read(), _ld_q, "exec", dont_inherit=True))
    _ld_codes = tuple(_ld_codes)
    if not _ld_sys.dont_write_bytecode:
        try:
            _ld_os.makedirs(_ld_os.path.dirname(_ld_cache), exist_ok=True)
            #: Unique per process: five hook events can be compiling at the same moment, and two
            #: writers sharing one temporary name is how a reader gets half a file.
            _ld_tmp = f"{_ld_cache}.{_ld_os.getpid()}.tmp"
            with open(_ld_tmp, "wb") as _ld_fh:
                _ld_fh.write(_ld_marshal.dumps((_ld_FORMAT, _ld_key, _ld_codes)))
            _ld_os.replace(_ld_tmp, _ld_cache)            # atomic on POSIX and on Windows
        except OSError:
            #: A read-only install just recompiles - and so does the loser of a race, which is
            #: the case the first version did not clean up after. On Windows `os.replace` raises
            #: `PermissionError [WinError 5]` when the destination is open for reading, and
            #: during a cold-cache race the other processes are reading it. Eight simultaneous
            #: hooks on an empty cache left three orphans of 493 KB each, named after dead pids
            #: so nothing would ever pick them up. One per loser per race, and a race follows
            #: every engine edit on a machine running more than one session.
            try:
                _ld_os.remove(_ld_tmp)
            except (OSError, NameError):                  # never created, or already gone
                pass

for _ld_code in _ld_codes:
    exec(_ld_code, globals())                             # noqa: S102 - our own cached bytecode

#: Keep the namespace exactly the engine's. `_ld_exc`, `_ld_tmp` and `_ld_st` exist only on some
#: paths, so the sweep is by prefix rather than a list that would go stale the next time a branch
#: is added - `tests/_test_entry_point.py` fails on any `_ld_` name left on the module.
for _ld_name in [_ld_n for _ld_n in list(globals()) if _ld_n.startswith("_ld_")]:
    del globals()[_ld_name]
del _ld_name
