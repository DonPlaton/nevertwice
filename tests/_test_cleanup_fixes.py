#!/usr/bin/env python3
"""Regression tests for the 2026-08 cleanup batch and the review that followed it.

Covers: the shared two-generation JSON persistence (including STRICT decoding, the
recovery-message condition and the primary-then-bak write order), the twin-gate
calibration file (wiring into the module globals, bounds validation, space-label
conflict), config resolving VAULT from an env file at import, `_sibling` in BOTH
import shapes, and the context-compaction overflow paths (link drop, tail spill,
byte cap, no re-compaction loop) plus the emergency-spill branch.

Every section is written to FAIL if the code it names is reverted - the first cut of
this suite passed against mutated code, which is worse than no suite at all.

Pure logic + disk; the LLM/embedder/GPU are mocked. No network.

    python _test_cleanup_fixes.py
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants
import memory_hook as m
import guards as g
from _sandbox import make_sandbox

PKG = Path(__file__).resolve().parent.parent / "nevertwice"

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


def _probe(code: str, env_extra: dict = None, cwd: Path = PKG):
    """Run a snippet in a clean child process; returns (stdout, returncode, stderr)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("NEVERTWICE_VAULT", "NEVERTWICE_HOME", "ANAMNESIS_VAULT",
                        "ANAMNESIS_HOME", "CLAUDE_MEMORY_VAULT", "CLAUDE_MEMORY_HOME",
                        "NEVERTWICE_ENV_FILE", "NEVERTWICE_TWIN_FILE",
                        "NEVERTWICE_TWIN_SPACE")}
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                       capture_output=True, text=True, timeout=180)
    return r.stdout.strip(), r.returncode, r.stderr.strip()


# ── two-generation JSON persistence ───────────────────────────────────
print("# json generations - shared load/save")
d = make_sandbox(m, "cleanup_")
p = d / "state.json"
m._save_json_generations(p, json.dumps({"a": 1}))
check("primary + .bak written", p.exists() and p.with_name(p.name + ".bak").exists())
check("clean load", m._load_json_generations(p, "test state") == {"a": 1})
p.write_text("{ truncated", encoding="utf-8")
check("corrupt primary recovers from .bak",
      m._load_json_generations(p, "test state") == {"a": 1})
p.with_name(p.name + ".bak").write_text("also bad", encoding="utf-8")
check("both generations corrupt -> None",
      m._load_json_generations(p, "test state") is None)

# expect= must be evaluated against a file that EXISTS and parses (the first cut
# pointed at a missing file, so the type gate was never reached).
wrong = d / "wrong_shape.json"
wrong.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
check("existing dict file rejected under expect=list",
      m._load_json_generations(wrong, "test state", expect=list) is None)
check("existing dict file accepted under expect=dict",
      m._load_json_generations(wrong, "test state") == {"not": "a list"})

# Write ORDER is a documented contract: primary first, so a crash between the two
# writes leaves the NEWER snapshot in the file the loader reads first.
# Patched on the SEAM, not on the facade. Since GOAL E4 moved this pair into
# `store_state.py`, `m.write_atomic` is a re-exported *reference*: rebinding it leaves the
# seam's own internal call untouched, so patching there silently observes nothing.
_seam = m._store_state
_order, _real_atomic = [], _seam.write_atomic
_seam.write_atomic = lambda path, text, **kw: (_order.append(Path(path).name),
                                               _real_atomic(path, text, **kw))[1]
m._save_json_generations(d / "order.json", "{}")
_seam.write_atomic = _real_atomic
check("save order is primary then .bak", _order == ["order.json", "order.json.bak"])

# STRICT decoding: a high-bit flip inside a JSON string used to parse as U+FFFD with
# errors="replace", so a corrupt primary silently won and the intact .bak was skipped.
gp = d / "guards.json"
m._save_json_generations(gp, json.dumps(
    [{"id": "g1", "pattern": "rm -rf /", "message": "danger", "status": "advisory",
      "project": "p", "scope": "*"}]))
gp.write_bytes(gp.read_bytes().replace(b"rm -rf /", b"rm \xad-rf /"))
_loaded = g.load_guards()
check("encoding-corrupt primary falls back to intact .bak",
      bool(_loaded) and _loaded[0]["pattern"] == "rm -rf /")

# A merely ABSENT primary is not a recovery event - the false line landed in the same
# log someone greps while chasing real corruption.
_logs, _real_log = [], m.log
m.log = lambda msg: _logs.append(str(msg))
absent = d / "absent.json"
absent.with_name("absent.json.bak").write_text(json.dumps({"ok": 1}), encoding="utf-8")
m._load_json_generations(absent, "absent state")
m.log = _real_log
check("absent primary logs no false recovery",
      not any("recovered from .bak" in x for x in _logs))

m.save_processed({"sid": {"transcript": "t"}})
check("processed-DB round trip through the helpers",
      m.load_processed() == {"sid": {"transcript": "t"}})

# ── a constant is read where it is used, not frozen at def time ───────────────────────
# Python evaluates default arguments once, at def time, so `k=RETRIEVAL_TOP_K` freezes the
# value this module had at import. The SAME name is read at call time by the injection path,
# so the two disagreed: `m.RETRIEVAL_TOP_K = 2` changed how many facts were injected and not
# how many `rerank_notes` returned. Every `m.X = ...` in this project's tests, and every
# embedder that repoints a constant after import, meets that silence.
print("# module constants are read at call time, not baked into defaults")
_sk = (m.RETRIEVAL_TOP_K, m.CROSS_PROJECT_K, m.generate_json, m._retrieval_candidates)
try:
    def _no_backend(*a, **kw):
        raise RuntimeError("no backend")

    m.generate_json = _no_backend
    rows = [{"stem": "s" + str(i), "title": "t" + str(i), "description": "d"} for i in range(5)]
    m.RETRIEVAL_TOP_K = 2
    check("rerank_notes honours a rebound RETRIEVAL_TOP_K",
          len(m.rerank_notes("q", rows)) == 2)

    m._retrieval_candidates = lambda project, cross=False, cache=None, query="": [
        ("2026-06-0" + str(i) + "-other-mistake-x" + str(i),
         {"ntype": "mistake", "project": "other", "title": "shared stack token lesson",
          "desc": "a shared stack token lesson", "vec": []}) for i in range(1, 5)]
    m.CROSS_PROJECT_K = 1
    check("retrieve_cross_project honours a rebound CROSS_PROJECT_K",
          len(m.retrieve_cross_project("mine", "shared stack token", alive_timeout=0)) == 1)
finally:
    m.RETRIEVAL_TOP_K, m.CROSS_PROJECT_K, m.generate_json, m._retrieval_candidates = _sk

# The rule, over every module: no module-level constant may be captured in a default.
_frozen, _pkg = [], 0
for _f in sorted(Path(m.__file__).resolve().parent.glob("*.py")):
    _pkg += 1
    _tree = ast.parse(_f.read_text(encoding="utf-8"))
    _consts = {n.targets[0].id for n in _tree.body
               if isinstance(n, ast.Assign) and len(n.targets) == 1
               and isinstance(n.targets[0], ast.Name)}
    for _fn in ast.walk(_tree):
        if not isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for _d in list(_fn.args.defaults) + [x for x in _fn.args.kw_defaults if x]:
            for _nm in ast.walk(_d):
                if isinstance(_nm, ast.Name) and _nm.id in _consts:
                    _frozen.append(_f.name + ":" + str(_fn.lineno) + " " + _fn.name
                                   + " <- " + _nm.id)
#: A sweep that discovers nothing reports no offenders, which reads exactly like a
#: sweep that found none. Pinned so the discovery has to keep working. Same class as
#: `1ef491c`; found by a third signature over offender checks whose loop iterates a
#: FILE DISCOVERY and whose size nothing asserts, 2026-09-22.
#: Counted INSIDE the loop: a second, independent call to the same glob would be a copy of
#: the intention, green while the loop looked at nothing. Measured by the auditing
#: session, 2026-09-22.
check(f"the package sweep sees its modules ({_pkg})", _pkg >= 55)
check("no module constant is frozen into a default argument: " + "; ".join(_frozen[:4]),
      not _frozen)

# The rule, over every module: a CLI's exit code is what main() returned.
# `python -m nevertwice.<mod>` runs the module as __main__, so whatever the guard does with
# main()'s return value IS the process's exit code. A bare `main()` discards it - `guards
# feedback <id> accepted` on a busy lock printed "NOT updated" and exited 0, and a script
# branching on $? read that as written. The in-process suites call main() and read its return,
# so none of them can see this; only a real process can, and only this rule can see it cheaply
# for every module at once. Second property, same guard: it must be the LAST statement in the
# file. guards.py's sat above `already_delivered` and `forget_delivery`, so a script's main()
# ran in a module where those two did not exist yet.

def _main_guard_audit(src: str, name: str) -> tuple[list, list]:
    """(exit codes dropped, guards that are not last) for one module source.

    EVERY `if __name__ == "__main__"` block, not the first: nothing forbids a module from
    having two, and a rule that stops at guards[0] reports such a file clean while its
    second block throws an exit code away. Only the LAST top-level statement can be last,
    so with two guards the earlier one is reported by the same walk that reports a guard
    followed by a def.
    """
    tree = ast.parse(src)
    guards = [n for n in tree.body
              if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
    fns = {n.name: n for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    dropped, not_last = [], []
    for g in guards:
        if g is not tree.body[-1]:
            not_last.append(
                name + ": the __main__ guard at line " + str(g.lineno) + " is followed by "
                + ", ".join(sorted(getattr(n, "name", type(n).__name__)
                                   for n in tree.body[tree.body.index(g) + 1:])))
        call = ast.unparse(g.body[0]) if g.body else ""
        if "sys.exit(" in call or "SystemExit(" in call:
            continue
        # The name the guard actually calls, read off the tree rather than off the text:
        # `main()` and `sys.exit(main())` differ by one paren and string surgery gets it wrong.
        target = ""
        for c in ast.walk(g):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id != "exit":
                target = c.func.id
        fn = fns.get(target)
        if fn is None:
            continue
        nested = {c for d in ast.walk(fn)
                  if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                  and d is not fn for c in ast.walk(d)}
        for n in ast.walk(fn):
            if n in nested or not isinstance(n, ast.Return) or n.value is None:
                continue
            if isinstance(n.value, ast.Constant) and n.value.value in (0, None):
                continue
            dropped.append(name + ":" + str(n.lineno) + " " + target
                           + "() returns a non-zero code the guard at line "
                           + str(g.lineno) + " throws away")
    return dropped, not_last



def _rc_of_argint(raw: str) -> int:
    """`m.argint` exits; run it for its code, with the usage line off the transcript.

    A missing `argint` is reported as a failed CHECK (-1), not as a traceback: a revert that
    crashes tells you the function is gone, which you already knew, and says nothing about
    what the check measures.
    """
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            m.argint([f"--days={raw}"], "days", 7)
    except SystemExit as e:
        return int(e.code or 0)
    except AttributeError:
        return -1
    return 0


# ── a CLI flag that must be a number is read through one reader ────────
#
# Four satellite CLIs wrapped `argval` in a bare `int()`: `--days=last-week` came out of
# `main()` as a ValueError traceback - the one output that tells a user nothing about what to
# type instead - and `--days=-5` was accepted into arithmetic that answered a different
# question in silence. `m.argint` refuses at the door with the flag named and exit 2, and this
# rule keeps the fifth CLI from re-inventing the wrong one.
_bare_int = []
for _f in sorted(Path(m.__file__).resolve().parent.glob("*.py")):
    _t = ast.parse(_f.read_text(encoding="utf-8"))
    for _n in ast.walk(_t):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                and _n.func.id == "int" and _n.args):
            continue
        _inner = _n.args[0]
        _name = (_inner.func.attr if isinstance(_inner, ast.Call)
                 and isinstance(_inner.func, ast.Attribute) else
                 _inner.func.id if isinstance(_inner, ast.Call)
                 and isinstance(_inner.func, ast.Name) else "")
        if _name == "argval":
            _bare_int.append(_f.name + ":" + str(_n.lineno))
check("no CLI wraps argval in a bare int(): " + ", ".join(_bare_int), not _bare_int)


def _argint_audit(src: str) -> list:
    out = []
    for n in ast.walk(ast.parse(src)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "int"
                and n.args and isinstance(n.args[0], ast.Call)):
            f = n.args[0].func
            if getattr(f, "attr", getattr(f, "id", "")) == "argval":
                out.append(str(n.lineno))
    return out


check("the rule catches the bare form", _argint_audit('d = int(m.argval(a, "days", "7"))') == ["1"])
check("and passes the reader", _argint_audit('d = m.argint(a, "days", 7)') == [])
check("argint refuses a word", _rc_of_argint("abc") == 2)
check("argint refuses a number below the minimum", _rc_of_argint("-5") == 2)
check("and a good value comes through",
      getattr(m, "argint", lambda *a, **k: None)(["--days=12"], "days", 7) == 12)
check("an absent flag is the default",
      getattr(m, "argint", lambda *a, **k: None)([], "days", 7) == 7)


_dropped, _not_last = [], []
for _f in sorted(Path(m.__file__).resolve().parent.glob("*.py")):
    _a, _b = _main_guard_audit(_f.read_text(encoding="utf-8"), _f.name)
    _dropped += _a
    _not_last += _b
check("every module's __main__ guard propagates main()'s exit code: " + "; ".join(_dropped[:3]),
      not _dropped)
check("and is the last statement in its file: " + "; ".join(_not_last[:3]), not _not_last)

# ... and the rule bites. Both shapes, on sources written to be caught: a rule that has
# never refused anything is indistinguishable from one that cannot.
_BARE = "import sys\ndef main():\n    return 1\nif __name__ == '__main__':\n    main()\n"
_MID = ("import sys\ndef main():\n    return 0\nif __name__ == '__main__':\n"
        "    sys.exit(main())\ndef later():\n    return 2\n")
_OK = "import sys\ndef main():\n    return 1\nif __name__ == '__main__':\n    sys.exit(main())\n"
check("the rule catches a dropped exit code", _main_guard_audit(_BARE, "x.py")[0] != [])
check("the rule catches a guard that is not last", _main_guard_audit(_MID, "x.py")[1] != [])
check("and passes the correct shape", _main_guard_audit(_OK, "x.py") == ([], []))
# A module may hold more than one `if __name__ == "__main__"` block - nothing forbids it,
# and the first cut read guards[0] and stopped, so a second block could drop its exit code
# under a rule that reported the file clean. The rule now audits EVERY guard; the census
# over the package is unchanged only because no module currently has two.
_TWO = """import sys
def main():
    return 0
if __name__ == '__main__':
    sys.exit(main())
def other():
    return 3
if __name__ == '__main__':
    other()
"""
check("the rule reads every __main__ guard, not just the first",
      any("other()" in x for x in _main_guard_audit(_TWO, "x.py")[0]))

# ── twin-gate calibration ─────────────────────────────────────────────
print("# twin calibration - data file, bounds, space label")
tf = d / "twin.json"
# The intercept is NEGATIVE, as every real calibration's is (the shipped one is -3.06):
# most candidate pairs are not twins. With b=+0.5 and five positive weights this fixture
# had no input anywhere in the feature box it would call not-a-twin - a gate that always
# says yes, which is what the joint bounds check refuses.
GOOD = {"space": "test-embed", "w": [1, 2, 3, 4, 5], "b": -0.5,
        "mu": [0, 0, 0, 0, 0], "sd": [1, 1, 1, 1, 1]}
tf.write_text(json.dumps(GOOD), encoding="utf-8")

# The module GLOBALS must come from the loader - asserting only the loader's return
# lets an un-wired constant block pass (mutation-verified gap in the first cut).
check("module globals are wired to the loader",
      (m._TWIN_SPACE, m._TWIN_W, m._TWIN_B, m._TWIN_MU, m._TWIN_SD)
      == m._load_twin_calibration())
_out, _rc, _err = _probe(
    "import memory_hook as m; print(m._TWIN_SPACE, m._TWIN_W[0])",
    {"NEVERTWICE_TWIN_FILE": str(tf)})
check(f"file reaches the globals in a fresh process (rc={_rc} {_err[:80]})",
      _rc == 0 and _out == "test-embed 1.0")

_saved = {k: os.environ.get(k) for k in ("NEVERTWICE_TWIN_FILE", "NEVERTWICE_TWIN_SPACE")}
os.environ["NEVERTWICE_TWIN_FILE"] = str(tf)
os.environ.pop("NEVERTWICE_TWIN_SPACE", None)
space, w, b, mu, sd = m._load_twin_calibration()
check("valid file overrides space + weights",
      space == "test-embed" and w == (1, 2, 3, 4, 5) and b == -0.5)

for label, bad in (("zero sd", {"sd": [0, 1, 1, 1, 1]}),
                   ("near-zero sd saturates the sigmoid", {"sd": [1e-12, 1, 1, 1, 1]}),
                   ("negative sd inverts the cosine feature", {"sd": [-1, 1, 1, 1, 1]}),
                   ("absurd weight", {"w": [1e9, 2, 3, 4, 5]}),
                   ("absurd bias", {"b": 1e9}),
                   ("wrong length", {"w": [1, 2, 3]}),
                   ("non-numeric", {"mu": ["x", 0, 0, 0, 0]}),
                   # Each bound held on its own and the pair defeated both. `sd >= 1e-6` and
                   # `|w| <= 1e3` are satisfied by sd=1e-6 with w=1e3, and the standardized
                   # feature then reaches 1e6: the logit spans +-2e9 over the whole feature
                   # box, the sigmoid is pinned, and every candidate clearing the cosine
                   # prefilter is a "twin" - up to WRITE_DEDUP_MAX_RETIRE live notes retired
                   # per write. The bias has the same reach with no weights at all.
                   ("a tiny sd and a large weight, each within its own bound",
                    {"sd": [1e-6, 1, 1, 1, 1], "w": [1e3, 1, 1, 1, 1]}),
                   ("a bias at the cap, which needs no weights to pin p=1.0",
                    {"b": 1e3, "w": [0.1, 0.1, 0.1, 0.1, 0.1]})):
    tf.write_text(json.dumps({**GOOD, **bad}), encoding="utf-8")
    check(f"rejected: {label}", m._load_twin_calibration()[1][0] == 3.684473)

# What the bound is FOR, measured on the gate rather than on the file: two notes with nothing
# in common except a cosine above the prefilter.
DISTINCT = dict(cos_sim=0.72, title_a="lock reclaimed from a live holder",
                desc_a="The age ceiling broke the PID-reuse wedge.", ents_a=("acquire_lock",),
                title_b="readme count read from the worktree",
                desc_b="git ls-files is the instrument for the tracked tree.",
                ents_b=("README.md",))
_baked = (m._TWIN_W, m._TWIN_B, m._TWIN_MU, m._TWIN_SD)
tf.write_text(json.dumps({**GOOD, "sd": [1e-6, 1, 1, 1, 1], "w": [1e3, 1, 1, 1, 1]}),
              encoding="utf-8")
_sp, m._TWIN_W, m._TWIN_B, m._TWIN_MU, m._TWIN_SD = m._load_twin_calibration()
check("a refused calibration cannot pin an unrelated pair at p=1.0",
      m._twin_probability(**DISTINCT) < m.WRITE_DEDUP_TWIN_P)
m._TWIN_W, m._TWIN_B, m._TWIN_MU, m._TWIN_SD = _baked

# The rule has to admit the gate this project ships, or it is not a bound but a ban.
tf.write_text(json.dumps({"space": "test-embed", "w": list(m._TWIN_W), "b": m._TWIN_B,
                          "mu": list(m._TWIN_MU), "sd": list(m._TWIN_SD)}), encoding="utf-8")
check("and the shipped calibration is not itself refused",
      m._load_twin_calibration()[0] == "test-embed")
tf.write_text("not json", encoding="utf-8")
check("rejected: unreadable file", m._load_twin_calibration()[0] == "bge-m3")

# The label must describe the weights ACTUALLY loaded: an env override that
# contradicts them would re-enable a note-RETIRING gate in a foreign space.
tf.write_text(json.dumps(GOOD), encoding="utf-8")
os.environ["NEVERTWICE_TWIN_SPACE"] = "some-other-space"
_n = len(m._EARLY_WARNINGS)
space, w, _b, _mu, _sd = m._load_twin_calibration()
check("contradicting env label refused (file space wins)", space == "test-embed")
check("file weights still in force after the refusal", w == (1, 2, 3, 4, 5))
check("the refusal is announced",
      any("NEVERTWICE_TWIN_SPACE" in x for x in m._EARLY_WARNINGS[_n:]))
os.environ.pop("NEVERTWICE_TWIN_FILE", None)
_n = len(m._EARLY_WARNINGS)
space, w, _b, _mu, _sd = m._load_twin_calibration()
check("no file + pinned label -> baked space, gate degrades to cosine",
      space == "bge-m3" and w[0] == 3.684473)
check("the mismatch is announced (it used to be silent)",
      any("NEVERTWICE_TWIN_SPACE" in x for x in m._EARLY_WARNINGS[_n:]))

# A refusal that only reaches _EARLY_WARNINGS is a refusal nobody sees: the line is written
# once, at import, into the hook's log - so an operator updating an install learns that the
# gate silently fell back to the baked weights only by reading log tails. `twin_calibration_status`
# is the same verdict as an answer, for `doctor` to print. It reports the VERDICT and never the
# calibration: the file is machine-local data and is not to be echoed anywhere.
os.environ.pop("NEVERTWICE_TWIN_SPACE", None)
os.environ.pop("NEVERTWICE_TWIN_FILE", None)
_st = m.twin_calibration_status()
check("no file: the status says the baked weights are in force",
      _st["present"] is False and _st["accepted"] is False and _st["source"] == "baked"
      and _st["space"] == "bge-m3")

os.environ["NEVERTWICE_TWIN_FILE"] = str(tf)
# Two values picked to be unmistakable if they ever escaped into the status.
tf.write_text(json.dumps({**GOOD, "w": [0.98725, 2, 3, 4, 5], "sd": [0.777, 1, 1, 1, 1]}),
              encoding="utf-8")
_st = m.twin_calibration_status()
check("an accepted file is reported as in use",
      _st["present"] and _st["accepted"] and _st["source"] == "file"
      and _st["space"] == "test-embed")
check("and the status carries no calibration values",
      "0.98725" not in json.dumps(_st) and "0.777" not in json.dumps(_st))

tf.write_text(json.dumps({**GOOD, "sd": [1e-6, 1, 1, 1, 1], "w": [1e3, 1, 1, 1, 1]}),
              encoding="utf-8")
_st = m.twin_calibration_status()
check("a refused file is reported as present but not in use",
      _st["present"] and _st["accepted"] is False and _st["source"] == "baked")
check("and the reason says what the bound was", "bounds" in _st["reason"].lower())

# The promise above is "never the calibration", and the first cut kept it only for files
# that parsed. `float("<value>")` puts the VALUE into the ValueError text, which the reason
# carried verbatim and `doctor` printed - so a calibration with a string where a number
# belongs published that string on both surfaces. The reason names the KEY and the TYPE; a
# type name cannot carry a value.
_MARK = "ZZ-not-a-number-ZZ"
for _field, _doc in (("w", {"w": [_MARK, 2, 3, 4, 5]}),
                     ("mu", {"mu": [0, _MARK, 0, 0, 0]}),
                     ("sd", {"sd": [1, 1, _MARK, 1, 1]}),
                     ("b", {"b": _MARK}),
                     ("space", {"space": {"secret": _MARK}})):
    tf.write_text(json.dumps({**GOOD, **_doc}), encoding="utf-8")
    _st = m.twin_calibration_status()
    check(f"a non-numeric {_field} is refused, not crashed on",
          _st["present"] and _st["accepted"] is False and _st["source"] == "baked")
    check(f"and the {_field} value never reaches the status",
          _MARK not in json.dumps(_st))
    check(f"while the reason still names the field: {_field}",
          _field in _st["reason"] and "str" in _st["reason"])
# The same file must not reach the gate either: a refused calibration falls back to baked.
tf.write_text(json.dumps({**GOOD, "w": [_MARK, 2, 3, 4, 5]}), encoding="utf-8")
check("and the loader falls back to the baked weights on it",
      m._load_twin_calibration()[1][0] == 3.684473)
check("with the early warning carrying no value either",
      not any(_MARK in x for x in m._EARLY_WARNINGS))

tf.write_text("not json", encoding="utf-8")
_st = m.twin_calibration_status()
check("an unreadable file names the error rather than the contents",
      _st["present"] and _st["accepted"] is False
      and "JSONDecodeError" in _st["reason"])
check("the status never contradicts the loader",
      _st["space"] == m._load_twin_calibration()[0])
os.environ.pop("NEVERTWICE_TWIN_FILE", None)

for k, v in _saved.items():
    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

# ── config: VAULT resolvable from an env file, and the guard still wins ──
print("# config - env-file vault pin at import")
envfile = d / "pin.env"
target = d / "store"
envfile.write_text(f"NEVERTWICE_VAULT={target}\n", encoding="utf-8")
_out, _rc, _err = _probe("import config; print(config.VAULT)",
                         {"NEVERTWICE_ENV_FILE": str(envfile)})
check(f"config.VAULT honours a pin living ONLY in the env file (rc={_rc} {_err[:80]})",
      _rc == 0 and _out == str(target))
_out, _rc, _err = _probe(
    "import config; print(config.VAULT)",
    {"NEVERTWICE_ENV_FILE": str(envfile), "NEVERTWICE_VAULT": str(d / "wins")})
check("an explicit process env var still outranks the file",
      _rc == 0 and _out == str(d / "wins"))
# The hermeticity guard must survive a planted env file (2026-08-24 regression).
_probe_home = d / "guarded"
_out, _rc, _err = _probe(
    "import sys; sys.path.insert(0, r'%s'); import _env_guard, config; "
    "print('LEAK' if 'store' in str(config.VAULT) else 'SEALED')"
    % str(Path(__file__).resolve().parent),
    {"NEVERTWICE_ENV_FILE": str(envfile)})
check(f"_env_guard seals the env-file vault vector (rc={_rc} {_err[:80]})",
      _rc == 0 and _out == "SEALED")

# ── _sibling: one dual-shape import resolver ──────────────────────────
print("# _sibling - dual-shape import resolver")
check("resolves a real sibling (flat shape)", hasattr(m._sibling("stats"), "est_tokens"))
try:
    m._sibling("definitely_not_a_module_zzz")
    check("missing sibling raises ImportError", False)
except ImportError:
    check("missing sibling raises ImportError", True)
# The PACKAGE branch is the one the A7 bug was about (a bare `import stats` was dead
# in a pip install) and no other suite exercises it.
_out, _rc, _err = _probe(
    "from nevertwice import memory_hook as m; "
    "s = m._sibling('stats'); print(s.__name__, m.__package__)",
    cwd=PKG.parent)
check(f"package shape resolves nevertwice.stats (rc={_rc} {_err[:80]})",
      _rc == 0 and _out == "nevertwice.stats nevertwice")

# ── sandbox completeness: every vault-derived constant moves ──────────
print("# sandbox - _rebase_vault covers every vault-derived constant")
d2 = make_sandbox(m, "cleanup_rebase_")
_stray = [n for n, v in vars(m).items()
          if n.isupper() and isinstance(v, Path) and n != "PROJECTS_ROOT"   # not vault-derived
          and d2 not in v.parents and v != d2]
check(f"no vault constant left outside the sandbox ({_stray})", not _stray)

# ── context compaction: overflow paths ────────────────────────────────
print("# compaction - overflow never amputates the newest entries")
d3 = make_sandbox(m, "cleanup_a4_")
_saved_ctx = (m.CONTEXT_MAX_BYTES, m.generate_json)
ctx_dir = d3 / "Context"
ctx_dir.mkdir(parents=True)


def _write_ctx(name, entries, head="# proj\n\nintro line"):
    fp = ctx_dir / f"{name}.md"
    fp.write_text(head + "\n\n" + "\n\n".join(entries) + "\n", encoding="utf-8")
    return fp


# (a) the compressed block does not fit: links drop, state trims, tail survives
m.CONTEXT_MAX_BYTES = 4000
m.generate_json = lambda prompt, project=None: {"state": "S" * 2400}   # overflows on purpose
links = " ".join(f"[[note-{i:04d}]]" for i in range(60))
old_e = [f"## 2026-08-{i:02d} - session\n\nwork happened. {links}" for i in range(1, 6)]
new_e = [f"## 2026-08-1{i} - session\n\n" + ("recent work. " * 30) + f"MARKER_{i}"
         for i in range(5, 8)]
fp = _write_ctx("fit", old_e + new_e)
m.compact_context_if_needed(fp, "fit", allow_llm=True)
out = fp.read_text(encoding="utf-8")
check("(a) within the byte cap", len(out.encode("utf-8")) <= m.CONTEXT_MAX_BYTES)
check("(a) newest entry intact", "MARKER_7" in out)
check("(a) state block written", "Accumulated state" in out)
# Count links INSIDE the compressed block only - a kept entry carries its own links,
# so a whole-file count says nothing about the drop loop.
_seg = (out.split("## Accumulated state", 1)[1].split("\n## 2026-", 1)[0]
        if "## Accumulated state" in out else "")
check("(a) archive links dropped to fit, tail untouched", _seg.count("[[note-") < 60)
_before = out
m.compact_context_if_needed(fp, "fit", allow_llm=True)
check("(a) already-compacted file is left alone (no recompaction loop)",
      fp.read_text(encoding="utf-8") == _before)

# (b) a single entry crowds the cap: it is spilled verbatim and truncated in place,
#     instead of being silently chopped by the whole-file cap guard
m.CONTEXT_MAX_BYTES = 3000
m.generate_json = lambda prompt, project=None: {"state": "summary"}
huge = "## 2026-08-20 - session\n\n" + ("H" * 9000) + "\nTAIL_MARKER"
fp2 = _write_ctx("huge", ["## 2026-08-01 - old\n\nold body", huge])
m.compact_context_if_needed(fp2, "huge", allow_llm=True)
out2 = fp2.read_text(encoding="utf-8")
arch = (d3 / "Context" / "Archive" / "huge-overflow.md")
check("(b) within the byte cap", len(out2.encode("utf-8")) <= m.CONTEXT_MAX_BYTES)
check("(b) truncation is marked, not silent", "entry truncated" in out2)
check("(b) full text preserved in the archive",
      arch.exists() and "TAIL_MARKER" in arch.read_text(encoding="utf-8"))
_before2 = out2
m.compact_context_if_needed(fp2, "huge", allow_llm=True)
check("(b) no recompaction loop", fp2.read_text(encoding="utf-8") == _before2)

# (c) emergency spill (no LLM under the lock) also respects the cap
m.CONTEXT_MAX_BYTES = 3000
big_new = "## 2026-08-21 - session\n\n" + ("B" * 9000) + "\nEMERG_MARKER"
fp3 = _write_ctx("emerg", [f"## 2026-08-0{i} - old\n\n" + ("o" * 1500) for i in range(1, 6)]
                 + [big_new])
m.compact_context_if_needed(fp3, "emerg", allow_llm=False)
out3 = fp3.read_text(encoding="utf-8")
arch3 = (d3 / "Context" / "Archive" / "emerg-overflow.md")
check("(c) emergency branch respects the cap",
      len(out3.encode("utf-8")) <= m.CONTEXT_MAX_BYTES)
check("(c) spilled content preserved",
      arch3.exists() and "EMERG_MARKER" in arch3.read_text(encoding="utf-8"))
m.CONTEXT_MAX_BYTES, m.generate_json = _saved_ctx

# ── _json_api_call: one retry loop, three backends, distinct contracts ──
print("# _json_api_call - per-backend retry/dead-flag contracts")
import io as _io
import urllib.error
import urllib.request


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_backend(call, outcome, calls=None):
    """Drive one backend against a scripted transport; returns (result, n_calls)."""
    n = [0]

    def _fake(req, timeout=None):
        n[0] += 1
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):          # (http_code, body)
            raise urllib.error.HTTPError(req.full_url, outcome[0], "err", {},
                                         _io.BytesIO(outcome[1].encode("utf-8")))
        return _Resp(outcome)

    real_open, real_ob, real_gb = (urllib.request.urlopen,
                                   m.OLLAMA_RETRY_BACKOFF, m.GEMINI_RETRY_BACKOFF)
    m._OLLAMA_DOWN = m._CLOUD_DEAD = False
    m.OLLAMA_RETRY_BACKOFF = m.GEMINI_RETRY_BACKOFF = 0     # no real sleeping
    urllib.request.urlopen = _fake
    try:
        return call(), n[0]
    finally:
        urllib.request.urlopen = real_open
        m.OLLAMA_RETRY_BACKOFF, m.GEMINI_RETRY_BACKOFF = real_ob, real_gb


# Ollama: an HTTP response is a real answer - never retried, never a CLOUD death.
res, n = _run_backend(lambda: m.call_ollama("p"), (500, "boom"))
check("ollama HTTP 500: no retry, no flags",
      res == {} and n == 1 and not m._OLLAMA_DOWN and not m._CLOUD_DEAD)
_logs, _real_log = [], m.log
m.log = lambda msg: _logs.append(str(msg))
res, n = _run_backend(lambda: m.call_ollama("p"), (404, "model not found"))
m.log = _real_log
check("ollama 404 keeps the 'ollama pull' remedy",
      n == 1 and any("ollama pull" in x for x in _logs))
# ...but a transport failure retries and marks the LOCAL backend down, not the cloud.
res, n = _run_backend(lambda: m.call_ollama("p"), urllib.error.URLError("refused"))
check("ollama transport failure: retries then _OLLAMA_DOWN only",
      res == {} and n == m.OLLAMA_RETRIES + 1 and m._OLLAMA_DOWN and not m._CLOUD_DEAD)
# Cloud: 503 IS transient - retried, then the cloud is marked dead for the run.
res, n = _run_backend(lambda: m.call_cerebras("p"), (503, "unavailable"))
check("cerebras HTTP 503: retried then _CLOUD_DEAD",
      res == {} and n == m.GEMINI_RETRIES + 1 and m._CLOUD_DEAD and not m._OLLAMA_DOWN)
# A content block is deterministic - retrying it just burns quota.
res, n = _run_backend(lambda: m.call_gemini("p"),
                      {"promptFeedback": {"blockReason": "SAFETY"}})
check("gemini blockReason: no retry", res == {} and n == 1)
res, n = _run_backend(lambda: m.call_gemini("p"), {"candidates": []})
check("gemini empty candidates: retried (transient)", n == m.GEMINI_RETRIES + 1)
res, n = _run_backend(lambda: m.call_ollama("p"), {"response": '{"ok": 1}'})
check("ollama happy path parses JSON", res == {"ok": 1} and n == 1)
m._OLLAMA_DOWN = m._CLOUD_DEAD = False

# -- an entry with no date is unprunable, not immortal ----------------------------
# `prune_processed_db` drops a corrupt non-dict entry and ages out a dated one. An entry that
# IS a dict but carries no parseable `processed_at` hit `except (ValueError, TypeError):
# continue` and was kept forever - so the records that survive a store's whole life are exactly
# the ones nothing can reason about, and the DB the hook reads before every session grows
# without bound. It cannot be aged out on evidence it does not carry, so it is given today's
# date the first time it is seen: kept now, and ordinary from here on.
print("# an entry with no date starts ageing rather than living forever")
_db = {"dated": {"processed_at": "2000-01-01T00:00:00", "ok": True},
       "fresh": {"processed_at": m.datetime.now().isoformat(timespec="seconds"), "ok": True},
       "undated": {"ok": True},
       "unparseable": {"processed_at": "last tuesday", "ok": True},
       "corrupt": "not a dict"}
m.prune_processed_db(_db, days=30)
check("the old entry is pruned", "dated" not in _db)
check("the corrupt value is dropped", "corrupt" not in _db)
check("the fresh entry is kept", "fresh" in _db)
check("an entry with no date is KEPT this pass", "undated" in _db)
for _k in ("undated", "unparseable"):
    _stamp = _db.get(_k, {}).get("processed_at", "")
    try:
        m.datetime.fromisoformat(_stamp)
        _ok = True
    except (ValueError, TypeError):
        _ok = False
    check("and " + _k + " now carries a date it can be aged by", _ok)
check("so the ordinary rule can reach it", m.prune_processed_db(_db, days=-1) >= 2
      and "undated" not in _db and "unparseable" not in _db)


# -- two values captured at import that a rebind cannot reach ----------
#
# T1 names three in one row. One is already closed: `archive_old_sessions`,
# `archive_old_typed` and `prune_processed_db` took their day counts as DEFAULT ARGUMENTS,
# evaluated once at def time, and now resolve the module constant on call. Two were open.
print("# values captured at import")

# 1. The cloud key. Free on the hook path - a process per event - but `mcp_server` is a stdio
# server that lives for the whole client session and `watch` is a daemon: on those, a key
# exported after start, or rotated, silently kept doing nothing until a restart. Same reason
# `_twin_file_path` and `_lock_file` resolve on call.
import os as _os  # noqa: E402

_saved_env = {k: _os.environ.get(k) for k in ("CEREBRAS_API_KEY", "GROQ_API_KEY")}
_saved_active = m.ACTIVE_CLOUD
try:
    m.ACTIVE_CLOUD = "cerebras"
    _os.environ["CEREBRAS_API_KEY"] = "sk-EXPORTED-AFTER-IMPORT"
    check("a key exported after import is the key that is used",
          m.cloud_key() == "sk-EXPORTED-AFTER-IMPORT")
    _os.environ["CEREBRAS_API_KEY"] = "sk-ROTATED"
    check("and a rotated key takes effect without a restart", m.cloud_key() == "sk-ROTATED")
    _os.environ["CEREBRAS_API_KEY"] = ""
    check("an unset key reads as absent, not as the one from start-up", m.cloud_key() == "")
    m.ACTIVE_CLOUD = "groq"
    _os.environ["GROQ_API_KEY"] = "gsk-ANOTHER"
    check("each provider reads its own variable", m.cloud_key() == "gsk-ANOTHER")
    m.ACTIVE_CLOUD = "none"
    check("and a provider with no variable reads empty", m.cloud_key() == "")
finally:
    m.ACTIVE_CLOUD = _saved_active
    for _k, _v in _saved_env.items():
        if _v is None:
            _os.environ.pop(_k, None)
        else:
            _os.environ[_k] = _v

# 2. `cache_clear` bound to the dict OBJECT rather than to the name. `_clear_tag_counts` is
# the sibling done right - a function with `global` - and the titles cache got `dict.clear`
# of whichever dict existed at import, so any rebinding of the name (a reset written the
# obvious way, a test monkeypatching the cache) left `cache_clear` scrubbing a dict nobody
# reads any more, silently.
_old_slugs = m._TITLE_SLUGS
try:
    m._TITLE_SLUGS = {"probe": {"pattern": [("2026-01-01", "a-title")]}}
    m.collect_existing_titles.cache_clear()
    check("cache_clear clears the cache the module is reading now", m._TITLE_SLUGS == {})
finally:
    m._TITLE_SLUGS = _old_slugs

_bound = type({}.clear)
check("neither cache_clear is bound to a container object",
      not isinstance(getattr(m.collect_existing_tags, "cache_clear", None), _bound)
      and not isinstance(getattr(m.collect_existing_titles, "cache_clear", None), _bound))
check("and both are still there for the callers that use them",
      callable(getattr(m.collect_existing_tags, "cache_clear", None))
      and callable(getattr(m.collect_existing_titles, "cache_clear", None)))

# The third of the row, held so it cannot come back: a day count read on call, not at def.
import inspect as _inspect  # noqa: E402

_defaults = []
for _fn in ("archive_old_sessions", "archive_old_typed", "prune_processed_db"):
    _sig = _inspect.signature(getattr(m, _fn))
    if _sig.parameters["days"].default not in (None, _inspect.Parameter.empty):
        _defaults.append(_fn)
check("no day count is frozen in a default argument: " + ", ".join(_defaults), not _defaults)


# -- a catch-up that is detached on one platform only -------------------
#
# `_spawn_detached_catchup` says DETACHED in its name and in its first line, and set the
# detaching flags only under `os.name == "nt"`. On POSIX the child stayed in the agent's
# process group and session, so a Ctrl-C at the terminal, or closing it, killed the catch-up
# - which takes the vault lock and writes notes - in the middle of its work. The stall this
# function exists to remove is a SessionStart stall, so the child outliving its parent is the
# whole point of it.
#
# Both branches are exercised here rather than the one this machine happens to be: a check
# that only runs on the platform that already worked is how this stayed open.
print("# the catch-up detaches on both platforms")

import subprocess as _subprocess  # noqa: E402

# Reached through getattr so a revert of the engine reports a named failure rather than an
# AttributeError: a run that crashes tells you the function is gone, which you already knew,
# and nothing about the property.
_detach = getattr(m, "_detach_kwargs", None)
check("the platform branch is reachable without faking os.name", callable(_detach))
_posix = _detach("posix") if callable(_detach) else {}
_nt = _detach("nt") if callable(_detach) else {}
check("on POSIX the child gets its own session", _posix.get("start_new_session") is True)
check("and no Windows-only flag is passed there", "creationflags" not in _posix)
# getattr on both sides: the constants are Windows-only, and this check has to be able to
# ask the Windows question from a POSIX machine - that is the whole point of it.
_DP = getattr(_subprocess, "DETACHED_PROCESS", 0x08)
_NG = getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
check("on Windows it is detached and in its own process group",
      bool(_nt.get("creationflags", 0) & _DP) and bool(_nt.get("creationflags", 0) & _NG))
check("and it does not also ask for a POSIX session", "start_new_session" not in _nt)

# ... and the spawn actually passes them, on whichever platform this is.
_seen = {}


class _RecordingPopen:
    def __init__(self, argv, **kwargs):
        _seen.clear()
        _seen.update(kwargs)
        _seen["argv"] = argv


_real_popen = _subprocess.Popen
try:
    _subprocess.Popen = _RecordingPopen
    m._spawn_detached_catchup()
    _here = _detach() if callable(_detach) else {}
    check("the spawn passes the detaching arguments for this platform",
          bool(_here) and all(_seen.get(k) == v for k, v in _here.items()))
    check("its stdio is detached from the agent's pipes",
          _seen.get("stdin") == _subprocess.DEVNULL
          and _seen.get("stdout") == _subprocess.DEVNULL
          and _seen.get("stderr") == _subprocess.DEVNULL)
    check("and it spawns the catch-up script",
          str(_seen.get("argv", ["", ""])[1]).endswith("process_now.py"))
finally:
    _subprocess.Popen = _real_popen

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
