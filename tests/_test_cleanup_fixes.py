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

# ── twin-gate calibration ─────────────────────────────────────────────
print("# twin calibration - data file, bounds, space label")
tf = d / "twin.json"
GOOD = {"space": "test-embed", "w": [1, 2, 3, 4, 5], "b": 0.5,
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
      space == "test-embed" and w == (1, 2, 3, 4, 5) and b == 0.5)

for label, bad in (("zero sd", {"sd": [0, 1, 1, 1, 1]}),
                   ("near-zero sd saturates the sigmoid", {"sd": [1e-12, 1, 1, 1, 1]}),
                   ("negative sd inverts the cosine feature", {"sd": [-1, 1, 1, 1, 1]}),
                   ("absurd weight", {"w": [1e9, 2, 3, 4, 5]}),
                   ("absurd bias", {"b": 1e9}),
                   ("wrong length", {"w": [1, 2, 3]}),
                   ("non-numeric", {"mu": ["x", 0, 0, 0, 0]})):
    tf.write_text(json.dumps({**GOOD, **bad}), encoding="utf-8")
    check(f"rejected: {label}", m._load_twin_calibration()[1][0] == 3.684473)
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

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
