"""`research/cross_project_bench.py`'s `--dry` stub-extractor path (A9, Q5 widening,
2026-09-23 review): the owner's concern was narrower than the oracle-mode bench alone can
prove - a pre-written, already-clean `principle` only proves `description` never crosses,
never that an EXTRACTED principle carrying a planted identifier gets caught.

TWO rejection stages, now that the "entity" class has two shapes (owner review, 2026-09-23,
closing W17 at promotion): a DECLARED entity is caught by `principle_scan` at WRITE time
(the forbidden-token path); an UNDECLARED one reaches disk and is instead caught by
`principles.py`'s token-provenance gate at PROMOTION time. This suite drives
`run_bench(cases, extractor_mode="stub", dry=True)` over the shipped 3-case, 6-slot fixture
(`cpb._DRY_POISON_PLAN`) and proves both stages fire, that nothing then leaks through the
`universal` arm, and - the mutations the plan calls for - that bypassing EITHER guard turns
the corresponding finding red BY NAME, without editing any shipped source.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

sys.path.insert(0, str(ROOT / "research"))
import cross_project_bench as cpb  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


# C3 (2026-09-23): write_rejections_by_class/promotion_rejections_by_class are now keyed off
# cpb.ALL_CLASSES (IDENTIFIER_CLASSES + DIGITFREE_SHAPES), not IDENTIFIER_CLASSES alone - this
# suite never plants a digit-free shape, so every one of those 4 extra keys is always 0 here;
# `_wc` (with-digitfree-zeros) extends an expected ip/host/path/entity-only dict with them so
# the exact-equality checks below still compare against the dict this population actually
# produces, not a stale 4-key shape.
def _wc(d: dict) -> dict:
    return {**d, **{s: 0 for s in cpb.DIGITFREE_SHAPES}}


print("\n- --dry's stub poisons one identifier of each class, plus a second undeclared entity -")
make_sandbox(m, "cpb_extract_dry_", offline=True)
cases = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
check(f"{cpb._DRY_N_CASES} cases loaded", len(cases) == cpb._DRY_N_CASES, str(len(cases)))

result = cpb.run_bench(cases, extractor_mode="stub", dry=True)
write_rej = result["write_rejections_by_class"]
promo_rej = result["promotion_rejections_by_class"]
check("ip/host/path/entity are each rejected at WRITE time, exactly once",
      write_rej == _wc({"ip": 1, "host": 1, "path": 1, "entity": 1}), str(write_rej))
check("the undeclared entity is rejected at PROMOTION time instead, exactly once",
      promo_rej == _wc({"ip": 0, "host": 0, "path": 0, "entity": 1}), str(promo_rej))

universal = result["arms"]["universal"]
check("nothing leaked into the universal arm at all (neither stage let anything through)",
      universal["leak"] == 0.0, str(universal))
check("the clean fallback in the undeclared-entity case still promotes SOMETHING useful",
      universal["benefit"] > 0.0, str(universal))

print("\n- finding 2 (2026-09-24): the all_arm preview carries its own hit count beside it -")
for r in result["rows"]:
    aa = r["all_arm"]
    for key in ("session_start_cross_preview", "session_start_cross_hits",
               "prompt_cross_preview", "prompt_cross_hits"):
        check(f"{r['case_id']}: all_arm carries {key!r}", key in aa, sorted(aa))
    check(f"{r['case_id']}: 0 session-start hits means an empty preview, not the reverse",
          aa["session_start_cross_hits"] > 0 or not aa["session_start_cross_preview"],
          (aa["session_start_cross_hits"], aa["session_start_cross_preview"][:60]))
    check(f"{r['case_id']}: 0 prompt hits means an empty preview, not the reverse",
          aa["prompt_cross_hits"] > 0 or not aa["prompt_cross_preview"],
          (aa["prompt_cross_hits"], aa["prompt_cross_preview"][:60]))

print("\n- C2b (2026-09-23): the defence has TWO independent layers - restated so each mutation "
     "isolates ONE layer at a time, per the coordinator's bisection (49cc10c introduced a "
     "regression here that 49cc10c's own regression list did not catch: whole-compound "
     "corroboration (B1) already lets PROMOTION-time provenance catch ip/host/path on its "
     "own, so the OLD single check ('remove principle_scan -> leak > 0') went red for the "
     "wrong reason - the layer it thought it was isolating was never the only one left) -")

print("\n- (a) WITHOUT principle_scan ALONE: promotion-time provenance is the remaining "
     "layer, and it must still catch every class by itself -")
make_sandbox(m, "cpb_extract_dry_mut_scan_", offline=True)
cases2 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
saved_scan = m.principle_scan
m.principle_scan = lambda text, forbidden: text          # identity: no de-identification at all
try:
    result_mut = cpb.run_bench(cases2, extractor_mode="stub", dry=True)
finally:
    m.principle_scan = saved_scan
rej_mut = result_mut["write_rejections_by_class"]
check("(a) WITHOUT principle_scan, NOTHING is rejected at write time any more (would FAIL "
     "'ip/host/path/entity are each rejected at WRITE time, exactly once' above)",
     rej_mut == _wc({"ip": 0, "host": 0, "path": 0, "entity": 0}), str(rej_mut))
promo_rej_mut = result_mut["promotion_rejections_by_class"]
check("(a) promotion-time provenance now rejects ip/host/path exactly once each, PLUS both "
     "entity variants (declared and undeclared - write time had nothing left to catch "
     "either one with) - the counts equal what was planted, not just 'something fired'",
     promo_rej_mut == _wc({"ip": 1, "host": 1, "path": 1, "entity": 2}), str(promo_rej_mut))
universal_mut = result_mut["arms"]["universal"]
check("(a) leak stays 0 - the second layer alone is enough",
     universal_mut["leak"] == 0.0, str(universal_mut))
check("(a) universal STILL promotes the clean case (benefit > 0) - the second layer "
     "rejects only the poisoned candidates, not every candidate",
     universal_mut["benefit"] > 0.0, str(universal_mut))

print("\n- (b) WITHOUT principle_scan AND WITHOUT the provenance threshold: both layers off - "
     "the OLD check, now honestly isolating 'no defence at all' instead of 'no write-time "
     "defence' -")
make_sandbox(m, "cpb_extract_dry_mut_both_", offline=True)
cases2b = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
saved_scan_b = m.principle_scan
saved_tp_b = cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS
m.principle_scan = lambda text, forbidden: text
cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = 1
try:
    result_mut_both = cpb.run_bench(cases2b, extractor_mode="stub", dry=True)
finally:
    m.principle_scan = saved_scan_b
    cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = saved_tp_b
universal_mut_both = result_mut_both["arms"]["universal"]
check("(b) WITH BOTH layers off, a poisoned identifier reaches the universal arm (this is "
     "the check that used to stand in for (a) alone, and was red on 49cc10c for that reason)",
     universal_mut_both["leak"] > 0.0, str(universal_mut_both))

print("\n- (c) WITHOUT token provenance ALONE lets the UNDECLARED entity through too "
     "(unchanged from before C2b) -")
make_sandbox(m, "cpb_extract_dry_mut_prov_", offline=True)
cases3 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
saved_tp = cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS
cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = 1
try:
    result_mut2 = cpb.run_bench(cases3, extractor_mode="stub", dry=True)
finally:
    cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = saved_tp
promo_rej_mut = result_mut2["promotion_rejections_by_class"]
check("(c) WITHOUT the provenance threshold, the undeclared entity is no longer "
      "rejected at promotion (would FAIL 'the undeclared entity is rejected at PROMOTION "
      "time instead, exactly once' above)", promo_rej_mut == _wc({"ip": 0, "host": 0, "path": 0,
                                                              "entity": 0}), str(promo_rej_mut))
universal_mut2 = result_mut2["arms"]["universal"]
check("(c) WITHOUT the provenance threshold, the undeclared entity now leaks into the "
      "universal arm (would FAIL 'nothing leaked into the universal arm at all' above)",
      universal_mut2["leak_by_class"]["entity"] > 0.0, str(universal_mut2))

print("\n- sanity: the real, unmutated code still catches both stages after either mutation -")
make_sandbox(m, "cpb_extract_dry_control_", offline=True)
cases4 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
result_control = cpb.run_bench(cases4, extractor_mode="stub", dry=True)
check("write-stage still catches ip/host/path/entity",
      result_control["write_rejections_by_class"] == _wc({"ip": 1, "host": 1, "path": 1,
                                                       "entity": 1}),
      str(result_control["write_rejections_by_class"]))
check("promotion-stage still catches the undeclared entity",
      result_control["promotion_rejections_by_class"] == _wc({"ip": 0, "host": 0, "path": 0,
                                                          "entity": 1}),
      str(result_control["promotion_rejections_by_class"]))

print("\n- main(['--dry']) exits 0 and writes nothing -")
make_sandbox(m, "cpb_extract_dry_main_", offline=True)
#: Compared before/after, not "does not exist": cpb.OUT sits in .loop/explore/, where a real
#: exploration run of this stand legitimately leaves its artifact - a working tree that has run
#: the stand failed this check without --dry writing anything (step-4 merge, 2026-09-24).
_out_before = (cpb.OUT.stat().st_mtime_ns, cpb.OUT.stat().st_size) if cpb.OUT.exists() else None
rc = cpb.main(["--dry"])
check("--dry exits 0", rc == 0, str(rc))
_out_after = (cpb.OUT.stat().st_mtime_ns, cpb.OUT.stat().st_size) if cpb.OUT.exists() else None
check("--dry neither creates nor rewrites the results artifact", _out_after == _out_before,
      f"{cpb.OUT}: {_out_before} -> {_out_after}")

print("\n- CLI flag combinations resolve to the right extractor mode -")


def _mode_from_cli(argv: list[str]) -> str:
    make_sandbox(m, "cpb_extract_dry_cli_", offline=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cpb.main(argv)
    out = buf.getvalue()
    for mode in ("oracle", "extract", "stub"):
        if f"extractor={mode}" in out:
            return mode
    return "?"


check("--dry alone resolves to the 'stub' extractor", _mode_from_cli(["--dry"]) == "stub")
check("--dry --oracle-principles resolves to 'oracle' (the old plumbing-only dry mode)",
      _mode_from_cli(["--dry", "--oracle-principles"]) == "oracle")

print(f"\ncross project bench extract --dry: {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
