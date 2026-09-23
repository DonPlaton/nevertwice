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
from collections.abc import Callable
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


MIN_CALLS, MIN_RUNS = 8, 12


def _calls_for(fn: Callable[[], object]) -> int:
    """How many calls one timed batch of `fn` needs to clear the timer (see `_cost_pair`)."""
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        span = time.perf_counter() - t0
        #: MIN_CALLS is about the TIMER, and a call that already takes 50 ms has cleared it
        #: many times over - repeating such a call eight times buys resolution nobody needs and
        #: costs the suite minutes. It is not hypothetical: the pathological shape this gate
        #: exists to catch runs 0.26 s at 12 kB and 4.09 s at 48 kB, so a floor of eight calls
        #: would make the suite spend seven minutes before reporting the pattern as slow. The
        #: escape hatch changes nothing for the eighteen real patterns, whose calls are 0.1 to
        #: 1.3 ms - three orders of magnitude under it - and the sweep above was measured with
        #: it in place.
        #: 5 ms, not 1: the auditing session's reading of the same macOS failure - a batch shorter
        #: than the runner's scheduling quantum is decided by one pause, and the minimum over runs
        #: cannot remove what hit a batch that short every time. Turns remove the long bursts,
        #: the longer batch the short ones.
        if (span >= 0.005 and (n >= MIN_CALLS or span / n >= 0.05)) or n >= 512:
            return n
        n *= 8


def _cost_pair(small_fn: Callable[[], object], big_fn: Callable[[], object]) -> tuple[float, float]:
    """Seconds per call for both inputs: the cheapest of MIN_RUNS runs of enough calls to
    clear the timer, the two inputs measured in TURNS - small, big, small, big.

    Why the cheapest of many runs (this was `_cost`, measured one input at a time):

    A single `perf_counter` pair measures the machine as much as the code, and the gate below
    leaves only TWO times of headroom by construction - the input is four times bigger and the
    bound is eight, so a correctly LINEAR scan sits at exactly 8/4. One scheduler pause that
    doubles either half therefore fails a linear pattern. Measured on this tree: fifteen of the
    eighteen patterns had less than four times of headroom, and the whole harvest 2.07x. The
    comment above says the ratio holds "on any machine, at any load"; two times is not any
    load, and this suite failed once in a full battery with two others running beside it
    (2026-09-21, not reproduced).

    The minimum is the run that was interrupted least. It cannot be faster than the true cost,
    so it does not weaken the assertion - it removes the noise that was being asserted about.
    Repeating the call until the pair spans a millisecond is what lets the floor below drop
    from 1e-4 to 1e-6. At 1e-4, five of the eighteen small measurements came in underneath it,
    and the floor silently replaced the RATIO this block is about with an absolute bound of
    0.8 ms - handing those five between four and eighty-five times of headroom that was not a
    measurement of anything. Measured with the floor at 1e-6: every pattern's true b/a is
    between 3.0 and 4.2, i.e. linear, and the thinnest headroom is 1.90x, so the tighter floor
    reddens nothing and every pattern is judged by its ratio. A floor is still there because a
    measurement of exactly zero must not divide.

    Five runs were not enough, and the replacement is measured rather than chosen. macOS 3.12 of
    the first matrix run reported `0.0001->0.0013` for one pattern - past the gate of eight, where
    every pattern's true ratio is four. Two things let a single interrupted run decide the verdict.
    The escalation stopped at the first n whose batch cleared a millisecond, so the cheap side was
    averaged over 64 calls while the expensive side, clearing the millisecond in one, was averaged
    over ONE - a well-averaged number compared against a single shot, and interference can only
    push the single shot up. And five minima are not enough minima: the least-interrupted of five
    runs is still often interrupted.

    Swept on a quiet machine, 18 patterns x 7 repeats, worst ratio observed against a true 4.0:

    runs=4  n>=1    5.21      0.7 s     <- what shipped, and what macOS crossed
    runs=4  n>=8    5.61      2.2 s
    runs=12 n>=8    4.42      5.5 s     <- here
    runs=30 n>=8    4.49     12.9 s
    runs=12 n>=64   4.33     41.1 s

    The median was 3.97-4.00 at every setting, which answers the question the gate asks: these
    patterns are linear, and what moved was the instrument. Twelve runs is the knee - thirty buys
    nothing, and a floor of 64 calls buys 0.09 for seven times the wall clock. At twelve the worst
    observation sits 11% above the truth instead of 31%, which is the headroom the gate of eight
    needs in order to be about the code.

    Why in turns:

    Measured one at a time, the small input's twelve runs came and then the big input's twelve,
    its own window of wall time. A burst of contention on a shared runner that covers the second
    window - some 150 ms - raises every one of the big input's runs, and the minimum cannot
    remove what hit all of them. That is the macOS 3.12 failure of e0e6924's matrix run:
    `0.00019->0.00154 = 8.3x` on the IP:port pattern, whose ratio is 4.0 at every setting of the
    sweep above. Taking the two in turns puts the same burst over both sides:
    a slow window now costs one run of each, and the minima come from the runs it missed. The
    gate asks about the ratio, so the two numbers must share their moments.
    """
    ns, nb = _calls_for(small_fn), _calls_for(big_fn)
    best_s = best_b = float("inf")
    for _ in range(MIN_RUNS):
        t0 = time.perf_counter()
        for _ in range(ns):
            small_fn()
        best_s = min(best_s, (time.perf_counter() - t0) / ns)
        t0 = time.perf_counter()
        for _ in range(nb):
            big_fn()
        best_b = min(best_b, (time.perf_counter() - t0) / nb)
    return best_s, best_b


def _harvest(src: str) -> Callable[[], object]:
    return lambda: m._harvest_literals(src, "a build note about paths", want=10, exclude=set())


_t_small, _t_big = _cost_pair(_harvest(_small), _harvest(_big))
check("four times the text costs less than eight times the work"
      f" ({_t_small:.3f}s -> {_t_big:.3f}s)", _t_big < 8 * max(_t_small, 1e-6))
check(f"and 48 kB of it stays under a second ({_t_big:.3f}s)", _t_big < 1.0)

# The rule for every pattern, so the next one added cannot be quadratic either.
_slow = []
for _rx in m._LIT_PATTERNS:
    _a, _b = _cost_pair(lambda rx=_rx: rx.findall(_small), lambda rx=_rx: rx.findall(_big))
    if _b > 8 * max(_a, 1e-6):
        #: The RATIO, not only the two times it came from. `0.0001->0.0013` is where the macOS
        #: report stopped, leaving a reader to divide two rounded numbers to learn whether the
        #: gate was crossed by three times or by a hair.
        _slow.append(f"{_rx.pattern[:40]} {_a:.5f}->{_b:.5f} = {_b / max(_a, 1e-6):.1f}x "
                     f"(linear is 4.0, gate is 8.0)")
check("no literal pattern is superlinear: " + "; ".join(_slow[:3]), not _slow)

#: The control the gate must keep failing (auditing session, e0e6924): a pattern that IS
#: quadratic - from every start in a colon-free run it scans to the end - timed by the same
#: instrument at a size where it costs milliseconds, not seconds (2.4 kB and 9.5 kB: 16.2x
#: measured, the square of four). An instrument steadied until it can no longer see this would
#: be a gate reporting on nothing.
import re  # noqa: E402
_ctl = re.compile(r"[a-z/.-]+:")
_ctl_small, _ctl_big = "a/b.c-d" * 340, "a/b.c-d" * 1360
_ca, _cb = _cost_pair(lambda: _ctl.findall(_ctl_small), lambda: _ctl.findall(_ctl_big))
check(f"and the instrument still sees a quadratic one ({_cb / max(_ca, 1e-6):.1f}x, gate 8.0)",
      _cb > 8 * max(_ca, 1e-6))

# And it still finds what it exists for.
_IMG = m._LIT_PATTERNS[2]
for _s, _want in (("ghcr.io/owner/repo:1.2", ["ghcr.io/owner/repo:1.2"]),
                  ("/usr/lib/foo:bar", ["/usr/lib/foo:bar"]),
                  ("docker pull nvidia/cuda:12.4.0-devel now", ["nvidia/cuda:12.4.0-devel"]),
                  ("no-slash:tag", [])):
    check("the image-tag pattern still reads " + repr(_s), _IMG.findall(_s) == _want)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
