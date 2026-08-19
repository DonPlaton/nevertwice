#!/usr/bin/env python3
"""Regression tests for the 2026-08 post-review cleanup batch.

Covers: the shared two-generation JSON persistence (_load/_save_json_generations),
the twin-gate calibration file (twin_calibration.json / NEVERTWICE_TWIN_FILE),
config resolving VAULT from an env file at import (the pin-in-code trap),
the _sibling dual-shape import resolver, and A4 (context compaction fitting the
compressed block instead of amputating the newest entries).

Pure logic + disk; the LLM/embedder/GPU are mocked. No network.

    python _test_cleanup_fixes.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants
import memory_hook as m
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
check("wrong shape rejected (expect=list)",
      m._load_json_generations(p.with_name("nope.json"), "test state", expect=list) is None)
m.save_processed({"sid": {"transcript": "t"}})
check("processed-DB round trip through the helpers",
      m.load_processed() == {"sid": {"transcript": "t"}})

# ── twin-gate calibration file ────────────────────────────────────────
print("# twin calibration - data file, not a code fork")
_saved_env = {k: os.environ.get(k) for k in ("NEVERTWICE_TWIN_FILE", "NEVERTWICE_TWIN_SPACE")}
tf = d / "twin.json"
tf.write_text(json.dumps({"space": "test-embed", "w": [1, 2, 3, 4, 5], "b": 0.5,
                          "mu": [0, 0, 0, 0, 0], "sd": [1, 1, 1, 1, 1]}), encoding="utf-8")
os.environ["NEVERTWICE_TWIN_FILE"] = str(tf)
os.environ.pop("NEVERTWICE_TWIN_SPACE", None)
space, w, b, mu, sd = m._load_twin_calibration()
check("file overrides space + weights",
      space == "test-embed" and w == (1, 2, 3, 4, 5) and b == 0.5)
tf.write_text(json.dumps({"space": "bad", "w": [1, 2, 3, 4, 5], "b": 0.5,
                          "mu": [0, 0, 0, 0, 0], "sd": [0, 0, 0, 0, 0]}), encoding="utf-8")
check("zero sd rejected -> baked bge-m3 weights survive",
      m._load_twin_calibration()[1][0] == 3.684473)
tf.write_text("not json", encoding="utf-8")
check("unreadable file -> baked weights", m._load_twin_calibration()[0] == "bge-m3")
tf.write_text(json.dumps({"space": "test-embed", "w": [1, 2, 3, 4, 5], "b": 0.5,
                          "mu": [0, 0, 0, 0, 0], "sd": [1, 1, 1, 1, 1]}), encoding="utf-8")
os.environ["NEVERTWICE_TWIN_SPACE"] = "forced-space"
check("NEVERTWICE_TWIN_SPACE still wins over the file",
      m._load_twin_calibration()[0] == "forced-space")
for k, v in _saved_env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

# ── config: VAULT resolvable from an env file (no code pin needed) ────
print("# config - env-file vault pin at import")
envfile = d / "pin.env"
target = d / "store"
envfile.write_text(f"NEVERTWICE_VAULT={target}\n", encoding="utf-8")
_scrub = ("NEVERTWICE_VAULT", "NEVERTWICE_HOME", "ANAMNESIS_VAULT", "ANAMNESIS_HOME",
          "CLAUDE_MEMORY_VAULT", "CLAUDE_MEMORY_HOME", "NEVERTWICE_ENV_FILE")
_senv = {k: v for k, v in os.environ.items() if k not in _scrub}
_senv["NEVERTWICE_ENV_FILE"] = str(envfile)
_r = subprocess.run([sys.executable, "-c", "import config; print(config.VAULT)"],
                    cwd=str(PKG), env=_senv, capture_output=True, text=True, timeout=120)
check("config.VAULT honours a vault pin that lives ONLY in the env file",
      _r.stdout.strip() == str(target))

# ── _sibling: one dual-shape import resolver ──────────────────────────
print("# _sibling - dual-shape import resolver")
check("resolves a real sibling", hasattr(m._sibling("stats"), "est_tokens"))
try:
    m._sibling("definitely_not_a_module_zzz")
    check("missing sibling raises ImportError", False)
except ImportError:
    check("missing sibling raises ImportError", True)

# ── A4: compaction never amputates the newest entries ─────────────────
print("# A4 - compressed block fits, newest entries survive")
d2 = make_sandbox(m, "cleanup_a4_")
_saved = (m.CONTEXT_MAX_BYTES, m.generate_json)
m.CONTEXT_MAX_BYTES = 4000
m.generate_json = lambda prompt, project=None: {"state": "compacted summary line"}
ctx_dir = d2 / "Context"
ctx_dir.mkdir(parents=True)
fp = ctx_dir / "proj.md"
links = " ".join(f"[[note-{i:04d}]]" for i in range(150))
old_entries = [f"## 2026-08-{i:02d} - session\n\nwork happened. {links}"
               for i in range(1, 6)]
new_entries = [f"## 2026-08-1{i} - session\n\n" + ("recent work. " * 40) + f"MARKER_{i}"
               for i in range(5, 8)]
fp.write_text("# proj\n\nintro line\n\n" + "\n\n".join(old_entries + new_entries) + "\n",
              encoding="utf-8")
m.compact_context_if_needed(fp, "proj", allow_llm=True)
out = fp.read_text(encoding="utf-8")
check("file within the byte cap", len(out.encode("utf-8")) <= 4000)
check("NEWEST entry intact (old cap guard chopped it)", "MARKER_7" in out)
check("state block present", "Accumulated state" in out)
m.CONTEXT_MAX_BYTES, m.generate_json = _saved

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
