#!/usr/bin/env python3
"""Write-path literal-fact preservation (ledger J3 held-out; fact_survival).

The defect this pins: the extractor summarises and drops the one token a future question asks
for - `nvcc -arch=sm_120` becomes "sm_120 support", a git hash vanishes into "reproducibility".
On the owner's hand-marked held-out the answer survived into the returned notes for 5 of 52
questions. The fix keeps the literal by two channels, both verified against the session so a
paraphrase (never a substring) cannot enter: the extractor names it, and a deterministic
harvester salvages it from the session text near the note's topic. Pure logic; no LLM, no embedder.

    python _test_fact_survival.py
"""
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env before the package bakes path constants
import memory_hook as m

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sandbox import make_sandbox

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


SRC = ("We built the CUDA runner with nvcc -arch=sm_120 on the RTX 5090. Then committed 87c8b17 "
       "for F5, whose reproducibility could not be checked. Pulled docker image "
       "zricethezav/gitleaks:latest. The default restore layout is: Every session in a window "
       "of its own. Set seed=20260827. WIDTHS = [1024, 512, 256]. Memory grew to ~2.8 GB.")

# ── 1. _verbatim_facts: keep the verbatim, drop paraphrase and invention ───────────────
vf = m._verbatim_facts(["nvcc -arch=sm_120", "sm_120 support", "87c8b17", "made-up-hash-xyz", 42], SRC)
check("verbatim substring kept (command)", "nvcc -arch=sm_120" in vf)
check("verbatim substring kept (hash)", "87c8b17" in vf)
check("paraphrase dropped (not a substring)", "sm_120 support" not in vf)
check("invention dropped", "made-up-hash-xyz" not in vf)
check("non-string ignored", 42 not in vf and all(isinstance(x, str) for x in vf))
check("dedup: repeated fact appears once",
      m._verbatim_facts(["87c8b17", "87c8b17"], SRC).count("87c8b17") == 1)
check("empty / bad input is safe", m._verbatim_facts(None, SRC) == [] and m._verbatim_facts("x", SRC) == [])

# ── 2. _harvest_literals: salvage literals near the note's own topic ───────────────────
h_cuda = m._harvest_literals(SRC, "cuda-port confirmed the environment with sm_120", want=6, exclude=set())
check("harvester salvages the CUDA command near a CUDA note", any("sm_120" in x for x in h_cuda))
check("harvester respects `want`", len(m._harvest_literals(SRC, "docker image gitleaks pulled", 2, set())) <= 2)
check("harvester honours exclude",
      "87c8b17" not in m._harvest_literals(SRC, "reproducibility commit hash for f5",
                                           6, {m._norm_ws("87c8b17")}))
check("no content overlap -> nothing harvested",
      m._harvest_literals(SRC, "", 6, set()) == [])

# ── 3. _note_facts: named first, then harvested, deduped, capped ───────────────────────
item = {"title": "reproducibility-issue",
        "description": "Reproducibility could not be checked by comparing two runs",
        "facts": ["87c8b17", "not-in-source"]}
nf = m._note_facts(item, SRC)
check("note_facts keeps the extractor-named verbatim literal", "87c8b17" in nf)
check("note_facts drops the extractor's invention", "not-in-source" not in nf)
check("note_facts obeys the per-note count cap", len(nf) <= m._FACTS_MAX_N)
check("note_facts obeys the per-note char cap", sum(len(x) for x in nf) <= m._FACTS_MAX_CHARS)
already = {"title": "x", "description": "value is 87c8b17 already in the text", "facts": ["87c8b17"]}
check("note_facts does not duplicate a literal already in the description",
      "87c8b17" not in m._note_facts(already, SRC))

# ── 4. _append_facts: one line, re-parse safe, idempotent ──────────────────────────────
desc = "Confirmed the environment is an RTX 5090 with sm_120 support"
app = m._append_facts(desc, ["nvcc -arch=sm_120", "87c8b17"])
check("append keeps a single line", "\n" not in app)
check("append carries the literal verbatim", "nvcc -arch=sm_120" in app)
check("append with no facts returns the base unchanged", m._append_facts(desc, []) == desc)
re_app = m._append_facts(app, ["zricethezav/gitleaks:latest"])
check("re-append replaces the prior block, never stacks it", re_app.count("[facts]") == 1)
check("re-append drops the old literal, carries the new", "87c8b17" not in re_app
      and "zricethezav/gitleaks:latest" in re_app)

# ── 5. end-to-end: the literal reaches the stored note and parses back as description ───
make_sandbox(m, "fs_", offline=True)
it = {"title": "cuda-port-environment-check",
      "description": "Confirmed the environment is an RTX 5090 with sm_120 support",
      "facts": []}                                   # extractor named nothing: harvester must carry it
vf5 = m._note_facts(it, SRC)
it["description"] = m._append_facts(it["description"], vf5)
stem = m.write_typed_note("Patterns", it, "proj", "2026-09-11", ["cuda"], "pattern")
note = (m.VAULT / "Patterns" / f"{stem}.md").read_text(encoding="utf-8")
check("the salvaged command is on disk in the note", "sm_120" in note)
_, parsed_desc, _ = m._parse_note_body(note.split("\n"))
check("the literal parses back inside the description (so recall returns it)",
      "sm_120" in parsed_desc)

# ── the harvester's cost is linear in the session, not quadratic ──────────────────────
# `_harvest_literals` runs on the WRITE path, under the vault lock, over text the session
# supplied. The image-tag pattern `[A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+:...` has a class containing
# the `/` it then requires, so every start position in a run of path-like characters is
# re-split every way that could reach a `:` - and on ordinary path-heavy text with no colon at
# all, that is the whole run, from every start. Measured: 0.26 s at 12 kB, 4.09 s at 48 kB,
# 16.3 s at 96 kB - four times the cost for twice the text, with the lock held throughout.
#
# The ratio is the assertion, not the seconds: it is what separates quadratic from linear on
# any machine, at any load. A doubling of the input costs a quadratic scan four times as much
# and a linear one twice; the gate sits between, at eight times for a FOURfold input.
print()
print("- the literal harvest is linear in the size of the session -")
_small = "a/b.c-d" * 1700          # ~12 kB of ordinary path-like text, no colon anywhere
_big = "a/b.c-d" * 6800            # ~48 kB of the same


def _harvest_cost(src: str) -> float:
    t0 = time.perf_counter()
    m._harvest_literals(src, "a build note about paths", want=10, exclude=set())
    return time.perf_counter() - t0


_t_small, _t_big = _harvest_cost(_small), _harvest_cost(_big)
check("four times the text costs less than eight times the work"
      f" ({_t_small:.3f}s -> {_t_big:.3f}s)", _t_big < 8 * max(_t_small, 1e-4))
check(f"and 48 kB of it stays under a second ({_t_big:.3f}s)", _t_big < 1.0)

# The rule for every pattern, so the next one added cannot be quadratic either.
_slow = []
for _rx in m._LIT_PATTERNS:
    _a = time.perf_counter(); _rx.findall(_small); _a = time.perf_counter() - _a
    _b = time.perf_counter(); _rx.findall(_big); _b = time.perf_counter() - _b
    if _b > 8 * max(_a, 1e-4):
        _slow.append(f"{_rx.pattern[:40]} {_a:.3f}->{_b:.3f}")
check("no literal pattern is superlinear: " + "; ".join(_slow[:3]), not _slow)

# And it still finds what it exists for.
_IMG = m._LIT_PATTERNS[2]
for _s, _want in (("ghcr.io/owner/repo:1.2", ["ghcr.io/owner/repo:1.2"]),
                  ("/usr/lib/foo:bar", ["/usr/lib/foo:bar"]),
                  ("docker pull nvidia/cuda:12.4.0-devel now", ["nvidia/cuda:12.4.0-devel"]),
                  ("no-slash:tag", [])):
    check("the image-tag pattern still reads " + repr(_s), _IMG.findall(_s) == _want)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
