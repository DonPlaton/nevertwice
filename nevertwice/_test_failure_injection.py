#!/usr/bin/env python3
"""Failure-injection probes against memory_hook hardening. Read-only on the real
vault: everything runs in a temp dir by repointing module globals."""
import sys, json, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

FAILS = []
def check(name, cond, detail=""):
    tag = "OK " if cond else "FAIL"
    if not cond:
        FAILS.append((name, detail))
    print(f"  [{tag}] {name}" + (f"  -- {detail}" if (detail and not cond) else ""))

def fresh_vault():
    d = Path(tempfile.mkdtemp(prefix="memfuzz_"))
    m.VAULT = d
    m.PROCESSED_DB = d / ".processed_sessions.json"
    m.EMBED_CACHE = d / ".embeddings_cache.json"
    m.STATUS_FILE = d / "status.txt"
    m.LOCK_FILE = d / ".memory.lock"
    m.LOG_FILE = d / ".logs" / "memory_hook.log"
    for sub in ("Context", "Patterns", "Mistakes", "Decisions", "Sessions"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    m.collect_existing_titles.cache_clear()
    m.collect_existing_tags.cache_clear()
    return d


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 1: compaction when Ollama dies MID-compaction (data loss?)")
d = fresh_vault()
# Build a bootstrap-style context with many session entries, >12KB.
proj = "project_epsilon"
fp = d / "Context" / f"{proj}.md"
head = (
    "---\nproject: project_epsilon\ntype: context\n---\n\n"
    "# project_epsilon\n\nDESCRIPTION LINE.\n\n"
    "## Цель и контекст\n\nПобедить старение.\n\n"
    "## Стек\n\n- PyTorch\n- CUDA\n\n"
    "## Структура\n\nКлючевая инфа о структуре.\n\n"
    "---\n\n## История сессий\n\n"
)
entries = []
for i in range(20):
    big = "x" * 800
    entries.append(f"## 2026-05-{i+1:02d} 10:00\nDecision {i}: keep checkpoint epoch {i}. {big}\n\nСессия: [[sess-{i}]]\nРешения: [[2026-05-{i+1:02d}-{proj}-decision-keep-{i}]]")
fp.write_text(head + "\n\n".join(entries) + "\n", encoding="utf-8")
orig_bytes = len(fp.read_text(encoding="utf-8").encode())
orig_text = fp.read_text(encoding="utf-8")
check("setup >12KB", orig_bytes > 12000, f"bytes={orig_bytes}")

# Ollama returns {} (down) during compaction
m.generate_json = lambda *a, **k: {}
m.compact_context_if_needed(fp, proj)
after = fp.read_text(encoding="utf-8")
check("Ollama-down compaction: file UNCHANGED (no data loss)", after == orig_text,
      f"changed; len {len(orig_text)}->{len(after)}")

# Ollama returns garbage (string, not dict)
m.generate_json = lambda *a, **k: "totally not a dict"
try:
    m.compact_context_if_needed(fp, proj)
    after2 = fp.read_text(encoding="utf-8")
    check("Ollama-garbage(str) compaction: file UNCHANGED", after2 == orig_text)
except Exception as e:
    check("Ollama-garbage(str) compaction did not raise", False, f"{type(e).__name__}: {e}")

# Ollama returns {"state": ""} (empty)
m.generate_json = lambda *a, **k: {"state": "   "}
m.compact_context_if_needed(fp, proj)
check("Ollama empty-state compaction: file UNCHANGED", fp.read_text(encoding="utf-8") == orig_text)


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 2: compaction SUCCESS — are wikilinks/decisions preserved?")
d = fresh_vault()
fp = d / "Context" / f"{proj}.md"
fp.write_text(head + "\n\n".join(entries) + "\n", encoding="utf-8")
# success summary that DROPS all the decision wikilinks
m.generate_json = lambda *a, **k: {"state": "- Текущий статус: обучаем модель\n- Решение: epoch 20"}
m.compact_context_if_needed(fp, proj)
after = fp.read_text(encoding="utf-8")
# the OLD entries (0..7) had wikilinks like decision-keep-0..7. recent kept = last 12 => entries 8..19
lost_links = [f"decision-keep-{i}" for i in range(8) if f"decision-keep-{i}" not in after]
check("compaction drops old wikilinks (decisions unreachable from Context)",
      len(lost_links) == 0,
      f"LOST {len(lost_links)} decision links from old entries: {lost_links[:4]}...")
# bootstrap sections preserved?
for sec in ("## Цель и контекст", "## Стек", "## Структура"):
    check(f"bootstrap section preserved: {sec}", sec in after)
# recent kept verbatim
check("recent entry kept (decision-keep-19)", "decision-keep-19" in after)


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 3: compaction IDEMPOTENCE / re-trigger after compaction")
# After compaction the head now ends with bootstrap '## Структура' BUT the
# compressed block starts with '## Накопленное состояние'. _split_context splits
# at FIRST '## YYYY-MM-DD' OR '## Накопленное'. So head should now include
# bootstrap sections + ... let's see what a 2nd compaction does if still >12KB.
d = fresh_vault()
fp = d / "Context" / f"{proj}.md"
# make recent entries themselves huge so post-compaction still >12KB
big_entries = []
for i in range(20):
    big = "y" * 1400
    big_entries.append(f"## 2026-05-{i+1:02d} 10:00\nNote {i}. {big}\n\nРешения: [[2026-05-{i+1:02d}-{proj}-decision-d{i}]]")
fp.write_text(head + "\n\n".join(big_entries) + "\n", encoding="utf-8")
m.generate_json = lambda *a, **k: {"state": "STATE-SUMMARY-BLOCK " + "z"*200}
m.compact_context_if_needed(fp, proj)
mid = fp.read_text(encoding="utf-8")
n_state_1 = mid.count("## Накопленное состояние")
check("after 1st compaction: exactly 1 state block", n_state_1 == 1, f"count={n_state_1}")
# 2nd compaction (still >12KB because 12 recent * 1400 bytes ~ 17KB)
print(f"    [info] post-1st-compaction bytes={len(mid.encode())}")
m.compact_context_if_needed(fp, proj)
end = fp.read_text(encoding="utf-8")
n_state_2 = end.count("## Накопленное состояние")
check("after 2nd compaction: still exactly 1 state block (no stacking/dup)",
      n_state_2 == 1, f"count={n_state_2} -- OLD state block may be lost or duplicated")
# Is the FIRST state block (containing summary of entries 0-7) preserved into 2nd?
check("2nd compaction preserves prior accumulated state text",
      "STATE-SUMMARY-BLOCK" in end,
      "prior compressed state LOST on re-compaction (cumulative knowledge loss)")


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 4: _split_context on a NEVER-compacted bootstrap file (no sessions)")
d = fresh_vault()
fp = d / "Context" / f"{proj}.md"
boot_only = (
    "---\nproject: x\ntype: context\n---\n\n# x\n\nDESC\n\n"
    "## Цель и контекст\n\nC1\n\n## Стек\n\n- a\n\n"
    "---\n\n## История сессий\n\n_(автоматически обновляется)_\n\n#tags"
)
fp.write_text(boot_only, encoding="utf-8")
hd, ents = m._split_context(fp.read_text(encoding="utf-8"))
check("bootstrap-only: 0 session entries detected", len(ents) == 0, f"entries={len(ents)}")
check("bootstrap-only: head == whole file (nothing swallowed)",
      hd.rstrip() == boot_only.rstrip(), "head differs from input")


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 5: retrieve_relevant / emit_session_start — cosine dim mismatch + empty")
fresh_vault()   # dead store removed (d unused here)
# poison cache: one good vec, one wrong-dimension vec, one non-list
cache = {
    "2026-05-01-project_epsilon-mistake-oom": {"vec":[0.1]*8,"ntype":"mistake","project":"project_epsilon","title":"oom crash"},
    "2026-05-01-project_epsilon-pattern-amp": {"vec":[0.2]*4,"ntype":"pattern","project":"project_epsilon","title":"use amp"},  # wrong dim
    "2026-05-01-project_epsilon-decision-x":  {"vec":"notalist","ntype":"decision","project":"project_epsilon","title":"bad"},
}
m.save_embed_cache(cache)
m.embed_text = lambda t, **kw: [0.1]*8  # query vec dim 8 (accepts kind/timeout kwargs)
try:
    out = m.retrieve_relevant("project_epsilon", "memory leak", 5)
    check("retrieve_relevant tolerates mixed-dim/bad-vec cache", True)
    print(f"    [info] retrieved {len(out)} -> {out}")
except Exception as e:
    check("retrieve_relevant tolerates mixed-dim/bad-vec cache", False, f"{type(e).__name__}: {e}")

# W3: the per-prompt path opts out of the recency fallback so an off-topic prompt stays
# silent instead of injecting recent-but-irrelevant notes as noise.
_oa = m.ollama_alive
m.ollama_alive = lambda *a, **k: False        # force no semantic signal
try:
    silent = m.retrieve_relevant("project_epsilon", "zzznomatchxyz", 5, recency_fallback=False)
    check("recency_fallback=False -> [] when there's no semantic/lexical signal (per-prompt silence)",
          silent == [])
finally:
    m.ollama_alive = _oa

# cosine on different dims directly
try:
    c = m.cosine([1,2,3],[1,2])
    check("cosine(dim3,dim2) no raise", True, f"={c}")
except Exception as e:
    check("cosine(dim3,dim2) no raise", False, f"{type(e).__name__}: {e}")


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 6: emit_session_start_context stdout = pure JSON only")
d = fresh_vault()
# tracked cwd
cwd = m.PROJECT_ROOT_DISPLAY + "\\project_epsilon"
# build a context file
(d/"Context"/"project_epsilon.md").write_text(
    "---\nproject: project_epsilon\n---\n\n# project_epsilon\n\nDesc line here.\n\n## 2026-05-01 10:00\nDid stuff\n", encoding="utf-8")
m.save_embed_cache({})  # empty cache -> fallback path
m.embed_text = lambda t, **kw: None  # embeddings unavailable (accepts kind/timeout kwargs)
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.emit_session_start_context(cwd)
sout = buf.getvalue()
# must be empty OR exactly one json object
stripped = sout.strip()
ok_json = False
if stripped == "":
    ok_json = True
else:
    try:
        # there must be exactly one line
        lines = [l for l in sout.splitlines() if l.strip()]
        obj = json.loads(stripped)
        ok_json = (len(lines) == 1 and isinstance(obj, dict) and "hookSpecificOutput" in obj)
    except Exception:
        ok_json = False
check("SessionStart stdout is empty-or-single-JSON", ok_json, f"got: {sout!r}")


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 7: redact_secrets — backtracking + over/under redaction")
# catastrophic backtracking probe: BEGIN block with no END, 4000 'A's
payload = "-----BEGIN PRIVATE KEY-----\n" + ("A"*5000)
t0 = time.time()
r = m.redact_secrets(payload)
dt = time.time()-t0
check("redact BEGIN-no-END completes <2s", dt < 2.0, f"took {dt:.2f}s (catastrophic?)")

# normal prose containing 'token' word followed by colon shouldn't nuke unrelated
prose = "The password reset flow: user clicks link. My token: see the documentation page now."
r2 = m.redact_secrets(prose)
print(f"    [info] prose redaction -> {r2!r}")
# legit code that we don't want mangled
code = "if password == expected: return True"
r3 = m.redact_secrets(code)
print(f"    [info] code redaction -> {r3!r}")
check("redact does not raise on prose/code", True)


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 8: call_ollama retry reuses req object (valid?)")
import urllib.request
calls = {"n":0}
class _Resp:
    status=200
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return json.dumps({"response":'{"ok":1}'}).encode()
def fake_urlopen(req, timeout=None):
    calls["n"]+=1
    if calls["n"] < 2:
        raise urllib.error.URLError("conn refused")
    return _Resp()
m2_open = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
m.OLLAMA_RETRY_BACKOFF = 0.0
try:
    res = m.call_ollama("hi")
    check("call_ollama retries URLError then succeeds (req reuse OK)", res == {"ok":1}, f"res={res}, calls={calls['n']}")
except Exception as e:
    check("call_ollama retries URLError then succeeds", False, f"{type(e).__name__}: {e}")
finally:
    urllib.request.urlopen = m2_open


# ════════════════════════════════════════════════════════════════════
print("\n# PROBE 9: local-only routing never leaks to cloud (both modes)")
import importlib
importlib.reload(m)  # restore the REAL generate_json (earlier probes mocked it)
m.ACTIVE_CLOUD = "cerebras"
m._CLOUD_KEYS = {"cerebras": "x", "groq": "", "gemini": ""}  # cloud key present
m._CLOUD_DEAD = False
m._OLLAMA_DOWN = False
hit = {"cloud": 0}
m.call_cloud = lambda p: (hit.__setitem__("cloud", hit["cloud"] + 1) or {"x": 1})
m.call_ollama = lambda p: {"local": 1}

# denylist mode
m.CLOUD_ONLY_PROJECTS = set()
m.LOCAL_ONLY_PROJECTS = {"project_alpha", "project_delta"}
for proj in ("project_alpha", "project_delta", "PROJECT_ALPHA", " Project_Delta "):
    hit["cloud"] = 0
    m.generate_json("x", project=proj)
    check(f"denylist: '{proj.strip()}' did NOT touch cloud", hit["cloud"] == 0)
hit["cloud"] = 0
m.generate_json("x", project="project_gamma")
check("denylist: 'project_gamma' used cloud", hit["cloud"] == 1)

# allowlist (fail-safe) mode: only project_gamma may cloud; unknown/empty stay local
m.CLOUD_ONLY_PROJECTS = {"project_gamma"}
for proj in ("project_alpha", "brand_new_project", "", None):
    hit["cloud"] = 0
    m.generate_json("x", project=proj)
    check(f"allowlist: {proj!r} stays local (fail-safe)", hit["cloud"] == 0)
hit["cloud"] = 0
m.generate_json("x", project="project_gamma")
check("allowlist: 'project_gamma' allowed to cloud", hit["cloud"] == 1)


print("\n" + "="*60)
if FAILS:
    print(f"PROBE FAILURES: {len(FAILS)}")
    for n,d_ in FAILS:
        print(f"  - {n}: {d_}")
else:
    print("all probes nominal")
