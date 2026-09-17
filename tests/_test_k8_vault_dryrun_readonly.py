#!/usr/bin/env python3
"""K8-B: the vault dry run is read-only in fact, not only in its declaration.

`research/k8_vault_dryrun.py` reads the owner's vault by path and declares
`sandbox_guard.allow_live("read-only: ...")` - which is what the sandbox lint asks for, and a
declaration is a word, not a proof. This suite is the proof. A store is built with the real engine
(every collision kind the script classifies: an other-day retirement, an other-day `-2` sibling, a
same-day absorb with a `## Previous statement`, a rule-4 sibling, a note outside the window),
then the script runs over it with two detectors armed:

* every write-capable function of the hook is replaced by a spy that records the call and raises -
  the set is derived from the hook's source (every module function that calls a filesystem sink, and
  transitively every function that calls one of those), not kept by hand;
* the store is fingerprinted byte by byte before and after.

And the detectors are proved load-bearing: three planted writes (the naryad's `write_typed_note` in the
note loop; a rewrite through the hook's own `write_atomic`; a `Path.write_text` past the hook) each turn
the check red, so a green run of the real script is not green for the wrong reason.

    python tests/_test_k8_vault_dryrun_readonly.py
"""
import _env_guard  # noqa: F401
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

SCRIPT = ROOT / "research" / "k8_vault_dryrun.py"
RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ── the write-capable functions of the hook, from its source ──────────────────────────────────

_SINK_NAMES = {"write_atomic", "save_embed_cache"}
_PATH_ATTRS = {"write_text", "write_bytes", "mkdir", "unlink", "rename", "touch", "rmdir"}
_OS_ATTRS = {"replace", "remove", "rename", "makedirs", "unlink", "rmdir"}
_SHUTIL_ATTRS = {"move", "copy", "copy2", "copyfile", "copytree", "rmtree"}


def _is_sink(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in _SINK_NAMES:
            return True
        if f.id == "open":
            mode = call.args[1].value if len(call.args) > 1 and isinstance(call.args[1], ast.Constant) else None
            for kw in call.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            return isinstance(mode, str) and any(c in mode for c in "wax+")
        return False
    if isinstance(f, ast.Attribute):
        if f.attr in _PATH_ATTRS:
            return True
        if f.attr == "replace" and len(call.args) == 1 and not call.keywords:
            return True                        # Path.replace(target) - str.replace takes two
        if isinstance(f.value, ast.Name):
            if f.value.id == "os" and f.attr in _OS_ATTRS:
                return True
            if f.value.id == "shutil" and f.attr in _SHUTIL_ATTRS:
                return True
            if f.value.id == "json" and f.attr == "dump":
                return True
    return False


def hook_writers() -> set:
    """Every module-level function of memory_hook that can reach a filesystem write, plus the two
    primitives the hook re-exports from store_state."""
    tree = ast.parse((ROOT / "nevertwice" / "memory_hook.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    direct, calls = set(), {}
    for name, fn in funcs.items():
        cs = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                if _is_sink(node):
                    direct.add(name)
                if isinstance(node.func, ast.Name) and node.func.id in funcs:
                    cs.add(node.func.id)
        calls[name] = cs
    writers = set(direct) | _SINK_NAMES
    grew = True
    while grew:
        grew = False
        for name, cs in calls.items():
            if name not in writers and cs & writers:
                writers.add(name)
                grew = True
    return writers


class StoreWrite(Exception):
    """Raised by a spy the moment a write-capable hook function is called."""


def _spy(writers: set):
    saved, calls = {}, []
    for name in sorted(writers):
        fn = getattr(m, name, None)
        if not callable(fn):
            continue
        saved[name] = fn

        def make(n):
            def spy(*a, **k):
                calls.append(n)
                raise StoreWrite(n)
            return spy
        setattr(m, name, make(name))
    return saved, calls


def fingerprint(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_readonly(script: Path, vault: Path, out: Path, writers: set) -> dict:
    """One run of `script` over `vault` with the spies armed: which hook writers were called, which
    store files changed, whether the run finished."""
    before = fingerprint(vault)
    saved, calls = _spy(writers)
    argv, buf = sys.argv, io.StringIO()
    finished, error = False, ""
    try:
        sys.argv = [str(script), str(vault), "--weeks", "4", "--today", "2026-09-16", "--out", str(out)]
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            mod = _load(script, f"_k8_dryrun_{abs(hash(str(script)))}")
            try:
                finished = mod.main() == 0
            except StoreWrite:
                pass
            except Exception as e:                    # noqa: BLE001 - a mutant may crash after its write
                error = f"{type(e).__name__}: {e}"
    finally:
        sys.argv = argv
        for k, v in saved.items():
            setattr(m, k, v)
    after = fingerprint(vault)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return {"calls": calls, "changed": changed, "finished": finished, "error": error, "stdout": buf.getvalue()}


def is_read_only(result: dict) -> bool:
    return not result["calls"] and not result["changed"] and result["finished"]


# ── the store: every collision kind, written by the real engine ─────────────────────────────────

F = m._FACTS_MARK
S1 = "2026-09-01-1000-k8v-session-aaaaaaaa"
S2 = "2026-09-03-1100-k8v-session-bbbbbbbb"
S3 = "2026-09-05-1200-k8v-session-cccccccc"


def note(title, desc, date, ntype="decision", session=S1):
    return m.write_typed_note(m.TYPE_FOLDER[ntype], {"title": title, "description": desc}, "k8v", date, ["t"], ntype,
                              session_stem_=session)


print("\n- the write-capable set is derived, and it is the right one -")
WRITERS = hook_writers()
check("the primitives and the four store writers are in the set",
      {"write_atomic", "save_embed_cache", "write_typed_note", "supersede_note", "_mark_contested"} <= WRITERS,
      str(sorted(WRITERS)[:12]))
check("the set is not a hand-kept list of four", len(WRITERS) >= 20, str(len(WRITERS)))
check("the parsers the dry run uses are not in it",
      not ({"parse_typed_stem", "_read_frontmatter", "_parse_note_body", "_facts_in"} & WRITERS),
      str({"parse_typed_stem", "_read_frontmatter", "_parse_note_body", "_facts_in"} & WRITERS))

d = make_sandbox(m, "k8v_", offline=True)
m.generate_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no real call in this suite"))
# (i) other day, proven replacement -> the earlier note retires into Superseded/ (branch r)
note("http client timeout", f"The HTTP client timeout is 30 seconds.{F}the http client timeout is 30 seconds", "2026-09-01")
note("http client timeout", f"The HTTP client timeout is 30 seconds, raised from 10.{F}the http client timeout is 30 seconds · raised from 10", "2026-09-03", session=S2)
# (ii) other day, unproven -> a -2 sibling, the earlier note stamped contested
note("primary database", f"The primary database is PostgreSQL 16.{F}postgresql 16", "2026-09-01", ntype="pattern")
note("primary database", f"The primary database is MySQL 8.{F}mysql 8", "2026-09-03", ntype="pattern", session=S2)
# (iii) other day, no literals in the new against literals in the old -> sibling by rule 4
note("upload size limit", f"The upload size limit is 25 MB.{F}the upload size limit is 25 mb", "2026-09-02", ntype="mistake")
note("upload size limit", "The upload size limit check was reviewed and found to be enforced correctly.", "2026-09-05", ntype="mistake", session=S3)
# (iv) same day, another session, disagreeing literals -> a -2 sibling (branch d kept apart)
note("cache backend", f"The cache backend is Redis 7.{F}redis 7", "2026-09-04", ntype="pattern")
note("cache backend", f"The cache backend is Memcached.{F}memcached", "2026-09-04", ntype="pattern", session=S2)
# (iv') same day, restated -> absorbed in place with a `## Previous statement`
note("retry policy", f"Retries use exponential backoff.{F}exponential backoff", "2026-09-05")
note("retry policy", f"Retries use exponential backoff with jitter, three attempts.{F}exponential backoff · three attempts", "2026-09-05", session=S3)
# (v) outside the window
note("old thing", "An old decision from June.", "2026-06-01")
files_before = fingerprint(d)
check("the store carries a retirement, a sibling and a contested stamp - the collision kinds exist",
      any("Superseded" in k for k in files_before) and any(k.endswith("-2.md") for k in files_before)
      and m._iter_contested(None) != [], str(sorted(files_before)[:8]))

print("\n- the real script: no hook writer called, no byte moved -")
with tempfile.TemporaryDirectory(prefix="k8v_out_") as tmp:
    out = Path(tmp) / "dryrun.json"
    res = run_readonly(SCRIPT, d, out, WRITERS)
    check("the run finished", res["finished"], res["error"] or res["stdout"][-200:])
    check("no write-capable hook function was called", res["calls"] == [], str(res["calls"][:5]))
    check("the store is byte-identical", res["changed"] == [], str(res["changed"][:5]))
    check("the check passes as a whole", is_read_only(res))
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    check("the script did its job on this store: collisions of both branches classified, a contested pair among them",
          report.get("collision_events", 0) >= 3 and report.get("by_rule", {}).get("contested", 0) >= 1
          and set(report.get("by_branch", {})) >= {"d", "r"}, json.dumps({k: report.get(k) for k in ("collision_events", "by_rule", "by_branch")}))
    check("the note outside the window was skipped",
          not any(e["slug"] == "old-thing" for e in report.get("events", [])), str([e["slug"] for e in report.get("events", [])]))
    check("the output went where it was told, outside the store", out.exists() and not (d / "dryrun.json").exists())

print("\n- the mutants: each planted write turns the check red -")
SRC = SCRIPT.read_text(encoding="utf-8")
ANCHOR = '        text = p.read_text(encoding="utf-8", errors="replace")\n'
check("the mutation anchor is in the script once", SRC.count(ANCHOR) == 1, str(SRC.count(ANCHOR)))
MUTANTS = {
    "the naryad's mutant: a write_typed_note call in the note loop":
        ANCHOR + '        m.write_typed_note("Decisions", {"title": p.stem, "description": "planted"}, "k8v", "2026-09-09", ["t"], "decision")\n',
    "a rewrite through the hook's own write_atomic (the same bytes - only the spy sees it)":
        ANCHOR + '        m.write_atomic(p, text)\n',
    "a write past the hook with Path.write_text (only the fingerprint sees it)":
        ANCHOR + '        p.write_text(text + "\\n", encoding="utf-8")\n',
}
with tempfile.TemporaryDirectory(prefix="k8v_mut_") as tmp:
    for i, (name, replacement) in enumerate(MUTANTS.items()):
        # each mutant runs over its own copy of the engine's store: the third one writes into it
        copy = Path(tmp) / f"store_{i}"
        shutil.copytree(d, copy)
        mutant = Path(tmp) / f"k8_vault_dryrun_mutant_{i}.py"
        mutant.write_text(SRC.replace(ANCHOR, replacement, 1), encoding="utf-8")
        res = run_readonly(mutant, copy, Path(tmp) / f"out_{i}.json", WRITERS)
        caught_by_spy, caught_by_bytes = bool(res["calls"]), bool(res["changed"])
        check(f"{name}: the check goes red", not is_read_only(res),
              f"calls={res['calls'][:3]} changed={res['changed'][:3]} finished={res['finished']}")
        if i == 0:
            check("  ...caught by the spy before any byte moved",
                  caught_by_spy and not caught_by_bytes and res["calls"][0] == "write_typed_note", str(res["calls"][:3]))
        elif i == 1:
            check("  ...caught by the spy before any byte moved",
                  caught_by_spy and not caught_by_bytes and res["calls"][0] == "write_atomic", str(res["calls"][:3]))
        else:
            check("  ...caught by the fingerprint, which the spy alone would have missed",
                  caught_by_bytes and not caught_by_spy, f"calls={res['calls'][:3]} changed={len(res['changed'])}")
check("the engine's own store was never touched by a mutant", fingerprint(d) == files_before)

check("the spies are gone after every run: the hook's writers are the originals again",
      all(callable(getattr(m, n, None)) and getattr(m, n).__name__ != "spy" for n in WRITERS if hasattr(m, n)))

print(f"\n{len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
