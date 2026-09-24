"""`research/cross_project_bench.py` (A9, Q5): `--dry --oracle-principles` end to end - 2 cases,
pre-written ground-truth principles (no extraction at all), a stub embedder, no model, no
Ollama, no GPU. Proves the RETRIEVAL/PROMOTION plumbing (write project_a/project_c/distractor
-> seed the universal pool -> retrieve_cross_project under all three arms) wires together AND
that the metrics actually distinguish the arms: `all` must leak the positive control's planted
identifiers (proving the metric can see a leak at all), `universal` must not, `off` must inject
nothing.

This file deliberately isolates retrieval from extraction (the oracle control) - the companion
suite `tests/research/_test_cross_project_bench_extract_dry.py` covers the OTHER half, the
extraction-side scanner rejection `--dry`'s stub extractor exists for (the 2026-09-23 widening:
a pre-written principle alone cannot prove an EXTRACTED one gets caught).
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "research"))
import cross_project_bench as cpb  # noqa: E402
import gen_cross_project_dataset as gcpd  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the shipped dataset loads and has the shape the bench expects -")
cases = cpb.load_cases(cpb.DATA, n=2)
check("2 cases loaded", len(cases) == 2, str(len(cases)))
for c in cases:
    for key in ("project_a", "project_c", "project_b", "distractor"):
        check(f"{c['id']} has {key!r}", key in c, str(c.keys()))
    for side in ("project_a", "project_c"):
        planted = c[side].get("planted") or {}
        for cls in gcpd.IDENTIFIER_CLASSES:
            check(f"{c['id']} {side} has a planted {cls!r}", bool(planted.get(cls)))

print("\n- --dry --oracle-principles runs the full plumbing over 2 cases, stub embedder, no model -")
result = cpb.run_bench(cases, extractor_mode="oracle", dry=True)
check("all three arms reported", set(result["arms"]) == set(cpb.ARMS), str(result["arms"].keys()))
for arm in cpb.ARMS:
    check(f"{arm} arm covered both cases", result["arms"][arm]["n_cases"] == 2,
          str(result["arms"][arm]))

off, all_arm, universal = (result["arms"]["off"], result["arms"]["all"],
                           result["arms"]["universal"])

print("\n- 'off' injects nothing at all -")
check("off: leak is 0", off["leak"] == 0.0, str(off))
check("off: benefit is 0 (nothing injected, so nothing can help either)", off["benefit"] == 0.0,
      str(off))
check("off: no cross-project characters injected", off["cross_chars_mean"] == 0.0, str(off))

print("\n- 'all' is the positive control: it DOES leak the planted identifiers -")
check("all: leak is 1.0 on this fixture (proves the metric can see a leak)", all_arm["leak"] == 1.0,
      str(all_arm))
check("all: benefit is 1.0 (the shared rule surfaces)", all_arm["benefit"] == 1.0, str(all_arm))

print("\n- 'universal' surfaces the same rule WITHOUT leaking either project's identifiers -")
check("universal: leak is 0.0", universal["leak"] == 0.0, str(universal))
check("universal: benefit is still 1.0 (de-identified, but still useful)",
      universal["benefit"] == 1.0, str(universal))
check("universal's injected text is smaller than all's (one clean line vs raw duplicates)",
      universal["cross_chars_mean"] < all_arm["cross_chars_mean"], str((universal, all_arm)))

print("\n- main(['--dry']) exits 0 and writes nothing to research/results/ -")
before = cpb.OUT.exists()
rc = cpb.main(["--dry"])
check("--dry exits 0", rc == 0, str(rc))
check("--dry does not create/modify the results artifact",
      cpb.OUT.exists() == before, f"existed before: {before}, exists now: {cpb.OUT.exists()}")

print("\n- C2 (2026-09-23): written_description_full is the UNTRUNCATED on-disk description -")
long_desc = ("Splitting structural changes from large backfills in separate transactions "
            "prevents table locking and reduces flaky failures. The original operation "
            "combined both in one transaction, causing a timeout due to extended lock "
            "contention that blocked other writers for several minutes during peak traffic.")
check("fixture description is longer than the 220-char recall cap", len(long_desc) > 220,
      str(len(long_desc)))
cpb.m._rebase_vault(Path(tempfile.mkdtemp(prefix="nevertwice_cpb_c2_")))
stem = cpb.m.write_typed_note(cpb.m.TYPE_FOLDER["pattern"],
                              {"title": "long desc fixture", "description": long_desc,
                               "principle": "keep migrations idempotent."},
                              "c2_fixture_project", "2026-09-23", [], "pattern")
check("the fixture note was written", bool(stem), str(stem))
info = {"stem": stem, "ntype": "pattern"}
snippet = cpb._side_written_description(info)
full = cpb._side_written_description_full(info)
check("_side_written_description (the recall simulation) IS capped at 220 chars",
      len(snippet) <= 220, str(len(snippet)))
check("_side_written_description_full is the WHOLE description, no truncation",
      full == long_desc, repr(full))
check("the full field is longer than the capped snippet on this fixture",
      len(full) > len(snippet), str((len(full), len(snippet))))
check("the capped snippet is still a prefix of the full description (a boundary-safe cut, "
     "not an arbitrary one, but a prefix either way)",
      long_desc.startswith(snippet), repr(snippet[-15:]))
# C5 (2026-09-23): `_note_snippet` now cuts at a WORD boundary (`_cut_word_boundary`), not a
# plain char-slice - the artifact C2 found here (this exact fixture used to end "...extended
# loc") is fixed at the source. The capped snippet is shorter than a plain [:220] slice would
# be (the straddling word is dropped whole, not fragmented), and full != snippet still holds -
# `written_description_full` (C2) remains the right field for vocabulary reconstruction
# because it is untruncated at all, not merely word-safe.
check("the capped snippet does NOT end mid-word any more (C5 fixed the artifact C2 found)",
      not (snippet and snippet[-1].isalpha() and long_desc[len(snippet):len(snippet) + 1]
          and long_desc[len(snippet)].isalpha()), repr(snippet[-15:]))
check("the capped snippet is strictly shorter than a plain 220-char slice would be here "
     "(the straddling word was dropped whole, not kept as a fragment)",
      len(snippet) < 220, str(len(snippet)))

print("\n- C4(a): measured_at carries git_head/dirty/utc/python/models/threshold/mode -")
stamp = cpb._measured_at()
_EXPECT_STAMP_KEYS = {"git_head", "dirty", "utc", "python", "extractor_model", "embedder_model",
                      "nevertwice_principle_t", "cross_project_mode_by_arm"}
check("measured_at has exactly the required keys", set(stamp) == _EXPECT_STAMP_KEYS, stamp)
real_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cpb.ROOT), capture_output=True,
                           text=True, check=True).stdout.strip()
check("git_head matches git rev-parse HEAD", stamp["git_head"] == real_head,
     (stamp["git_head"], real_head))
check("dirty is a bool", isinstance(stamp["dirty"], bool), str(stamp["dirty"]))
check("utc matches YYYY-MM-DDTHH:MM:SSZ",
     bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp["utc"])), stamp["utc"])
check("python matches sys.version", stamp["python"] == sys.version.split()[0], stamp["python"])
check("extractor_model is cpb.LLM (the pinned extraction model)",
     stamp["extractor_model"] == cpb.LLM, stamp["extractor_model"])
check("embedder_model is m.embed_signature() ('provider:model')",
     stamp["embedder_model"] == cpb.m.embed_signature(), stamp["embedder_model"])
check("nevertwice_principle_t is the LIVE pr.T_PRINCIPLE (NEVERTWICE_PRINCIPLE_T, resolved)",
     stamp["nevertwice_principle_t"] == cpb.pr.T_PRINCIPLE, stamp["nevertwice_principle_t"])
check("cross_project_mode_by_arm names every ARM with the mode actually passed for it",
     stamp["cross_project_mode_by_arm"] == {a: a for a in cpb.ARMS},
     stamp["cross_project_mode_by_arm"])

print("\n- C4(b): leak_hits/leak_hits_by_class are raw COUNTS beside the existing fractions -")
result_b4 = cpb.run_bench(cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES), extractor_mode="stub",
                          dry=True)
for arm_name, s in result_b4["arms"].items():
    check(f"{arm_name}: leak_hits is an int, leak == leak_hits / n_cases",
         isinstance(s["leak_hits"], int) and
         (s["n_cases"] == 0 or abs(s["leak"] - s["leak_hits"] / s["n_cases"]) < 1e-9),
         (s["leak_hits"], s["leak"], s["n_cases"]))
    check(f"{arm_name}: leak_hits_by_class has the SAME keys as leak_by_class (ALL_CLASSES)",
         set(s["leak_hits_by_class"]) == set(s["leak_by_class"]) == set(cpb.ALL_CLASSES),
         (sorted(s["leak_hits_by_class"]), sorted(cpb.ALL_CLASSES)))
    for cls in cpb.ALL_CLASSES:
        check(f"{arm_name}/{cls}: leak_by_class == leak_hits_by_class / n_cases",
             s["n_cases"] == 0 or
             abs(s["leak_by_class"][cls] - s["leak_hits_by_class"][cls] / s["n_cases"]) < 1e-9,
             (arm_name, cls, s["leak_hits_by_class"][cls], s["leak_by_class"][cls]))

print("\n- C4(c)/(d): token_provenance_pairs carry U/S/rule/cold_start; totals aggregate -")
pairs_seen = [p for r in result_b4["rows"] for p in r["token_provenance_pairs"]]
check("at least one pair reached provenance on the --dry fixture (case cpv1-003's poisoned "
     "queue-shard identifier clusters and is rejected)", len(pairs_seen) > 0, len(pairs_seen))
rejected_pairs = [p for p in pairs_seen if not p["U"]["passes"]]
check("at least one pair's U verdict is a rejection, named by rule",
     any(p["U"]["rule"] in ("shape", "uniqueness") for p in rejected_pairs), rejected_pairs)
for p in pairs_seen:
    check(f"{p['source_project']}: U.offending is a SUPERSET of S.offending (S = shape only, "
         "uniqueness forced off - U can only ADD uniqueness rejections on top)",
         set(p["S"]["offending"]) <= set(p["U"]["offending"]),
         (p["U"]["offending"], p["S"]["offending"]))
    check(f"{p['source_project']}: U passing implies rule == 'none' and no offending tokens",
         p["U"]["passes"] == (p["U"]["rule"] == "none") == (not p["U"]["offending"]),
         (p["U"]["passes"], p["U"]["rule"], p["U"]["offending"]))
    check(f"{p['source_project']}: cold_start.narrow_flagged == len(uniqueness_offending_tokens) "
         "(this pair's OWN live scope IS the narrow one)",
         p["cold_start"]["narrow_flagged"] == len(p["cold_start"]["uniqueness_offending_tokens"]),
         p["cold_start"])
    check(f"{p['source_project']}: cold_start.wide_flagged <= narrow_flagged (more corroboration "
         "sources can only REDUCE how many stay flagged, never increase)",
         p["cold_start"]["wide_flagged"] <= p["cold_start"]["narrow_flagged"], p["cold_start"])
check("cold_start totals equal the sum of every pair's own narrow/wide counts",
     result_b4["cold_start"]["narrow_flagged_total"] ==
     sum(p["cold_start"]["narrow_flagged"] for p in pairs_seen) and
     result_b4["cold_start"]["wide_flagged_total"] ==
     sum(p["cold_start"]["wide_flagged"] for p in pairs_seen), result_b4["cold_start"])

print("\n- --help exits 0 -")
try:
    cpb.main(["--help"])
    help_rc = 0
except SystemExit as e:
    help_rc = e.code
check("--help exits 0", help_rc == 0, str(help_rc))

# item 4 (R-v2-ports): the real (non-dry) oracle run wires pacer.install()/attach() around
# its own embedder traffic. "oracle" mode needs no LLM extraction at all (a pre-written
# principle is written directly) and dry=False means m.embed_text is NOT stubbed the way
# --dry stubs it - so this is the cheapest real (non-dry) path through run_bench that still
# needs no model, only a fake urlopen.
print("\n- item 4: the real (non-dry) oracle run wires pacer.install()/attach() around its "
      "own traffic -")
import contextlib
import urllib.request
sys.path.insert(0, str(ROOT / "research"))
import _ollama_pacer as pacer  # noqa: E402


class _JsonResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **kw):
    return _JsonResp({"embedding": [0.1, 0.2, 0.3]})


@contextlib.contextmanager
def _isolated_pacer():
    # (в)/MX3: a bare `assert` here means a real regression (the `not dry` gate removed
    # from run_bench, so --dry itself leaves the pacer installed) reddens this suite with
    # an uncaught Traceback - a crash, not a named FAIL line. check() first (non-fatal),
    # then self-heal (uninstall) so the block that follows still runs on its own merits
    # instead of cascading into more crashes.
    check("pacer starts uninstalled entering this block (a --dry run earlier in this "
          "file must never have left run_bench()'s pacer.install() gate installed)",
          not pacer.installed())
    if pacer.installed():
        pacer.uninstall()
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer._reset_for_tests()


ORACLE_CASES = cpb.load_cases(cpb.DATA, n=2)

with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    result = cpb.run_bench(ORACLE_CASES, extractor_mode="oracle", dry=False)
    check("ollama_transport is written (install() actually wrapped the embedder calls)",
          "ollama_transport" in result, str(sorted(result)))
    check("calls > 0 (write-time + retrieval-time embeds over 2 real cases)",
          result.get("ollama_transport", {}).get("calls", 0) > 0,
          str(result.get("ollama_transport")))
    check("the run still covered both cases per arm despite the fake transport",
          all(result["arms"][arm]["n_cases"] == 2 for arm in cpb.ARMS), str(result["arms"]))

print("\n- item 4 mutations: install()/attach() removed from the real oracle run "
      "(in-process, cpb.pacer IS the _ollama_pacer module - reassigning its attribute "
      "simulates the call site being deleted without editing the file) -")
saved_install, saved_attach = cpb.pacer.install, cpb.pacer.attach
cpb.pacer.install = lambda: None                      # mutation: install() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    result_no_install = cpb.run_bench(ORACLE_CASES, extractor_mode="oracle", dry=False)
    check("mutation 'install() removed': no ollama_transport is written at all (nothing "
          "ever got paced - would FAIL the 'ollama_transport is written' check above)",
          "ollama_transport" not in result_no_install, str(sorted(result_no_install)))
cpb.pacer.install = saved_install

cpb.pacer.attach = lambda *a, **k: None                # mutation: attach() removed
with _isolated_pacer():
    urllib.request.urlopen = _fake_urlopen
    result_no_attach = cpb.run_bench(ORACLE_CASES, extractor_mode="oracle", dry=False)
    check("mutation 'attach() removed': no ollama_transport is written (the pacer paced "
          "the calls but the artifact never learns it - would FAIL the same check above)",
          "ollama_transport" not in result_no_attach, str(sorted(result_no_attach)))
cpb.pacer.attach = saved_attach

check("cpb.pacer.install/attach are restored to the real functions after the mutations",
      cpb.pacer.install is saved_install and cpb.pacer.attach is saved_attach)

print(f"\ncross project bench (--dry, and item 4's real (non-dry) oracle pacer wiring): "
     f"{FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
