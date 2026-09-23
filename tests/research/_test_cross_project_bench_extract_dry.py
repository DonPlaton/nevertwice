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


print("\n- --dry's stub poisons one identifier of each class, plus a second undeclared entity -")
make_sandbox(m, "cpb_extract_dry_", offline=True)
cases = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
check(f"{cpb._DRY_N_CASES} cases loaded", len(cases) == cpb._DRY_N_CASES, str(len(cases)))

result = cpb.run_bench(cases, extractor_mode="stub", dry=True)
write_rej = result["write_rejections_by_class"]
promo_rej = result["promotion_rejections_by_class"]
check("ip/host/path/entity are each rejected at WRITE time, exactly once",
      write_rej == {"ip": 1, "host": 1, "path": 1, "entity": 1}, str(write_rej))
check("the undeclared entity is rejected at PROMOTION time instead, exactly once",
      promo_rej == {"ip": 0, "host": 0, "path": 0, "entity": 1}, str(promo_rej))

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

print("\n- mutation: bypassing principle_scan lets the write-time classes through -")
make_sandbox(m, "cpb_extract_dry_mut_scan_", offline=True)
cases2 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
saved_scan = m.principle_scan
m.principle_scan = lambda text, forbidden: text          # identity: no de-identification at all
try:
    result_mut = cpb.run_bench(cases2, extractor_mode="stub", dry=True)
finally:
    m.principle_scan = saved_scan
rej_mut = result_mut["write_rejections_by_class"]
check("mutation: WITHOUT principle_scan, NOTHING is rejected at write time any more (would "
      "FAIL 'ip/host/path/entity are each rejected at WRITE time, exactly once' above)",
      rej_mut == {"ip": 0, "host": 0, "path": 0, "entity": 0}, str(rej_mut))
universal_mut = result_mut["arms"]["universal"]
check("mutation: WITHOUT principle_scan, a poisoned identifier reaches the universal arm "
      "(would FAIL 'nothing leaked into the universal arm at all' above)",
      universal_mut["leak"] > 0.0, str(universal_mut))

print("\n- mutation: bypassing token provenance lets the UNDECLARED entity through too -")
make_sandbox(m, "cpb_extract_dry_mut_prov_", offline=True)
cases3 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
saved_tp = cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS
cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = 1
try:
    result_mut2 = cpb.run_bench(cases3, extractor_mode="stub", dry=True)
finally:
    cpb.pr.TOKEN_PROVENANCE_MIN_PROJECTS = saved_tp
promo_rej_mut = result_mut2["promotion_rejections_by_class"]
check("mutation: WITHOUT the provenance threshold, the undeclared entity is no longer "
      "rejected at promotion (would FAIL 'the undeclared entity is rejected at PROMOTION "
      "time instead, exactly once' above)", promo_rej_mut == {"ip": 0, "host": 0, "path": 0,
                                                              "entity": 0}, str(promo_rej_mut))
universal_mut2 = result_mut2["arms"]["universal"]
check("mutation: WITHOUT the provenance threshold, the undeclared entity now leaks into the "
      "universal arm (would FAIL 'nothing leaked into the universal arm at all' above)",
      universal_mut2["leak_by_class"]["entity"] > 0.0, str(universal_mut2))

print("\n- sanity: the real, unmutated code still catches both stages after either mutation -")
make_sandbox(m, "cpb_extract_dry_control_", offline=True)
cases4 = cpb.load_cases(cpb.DATA, n=cpb._DRY_N_CASES)
result_control = cpb.run_bench(cases4, extractor_mode="stub", dry=True)
check("write-stage still catches ip/host/path/entity",
      result_control["write_rejections_by_class"] == {"ip": 1, "host": 1, "path": 1,
                                                       "entity": 1},
      str(result_control["write_rejections_by_class"]))
check("promotion-stage still catches the undeclared entity",
      result_control["promotion_rejections_by_class"] == {"ip": 0, "host": 0, "path": 0,
                                                          "entity": 1},
      str(result_control["promotion_rejections_by_class"]))

print("\n- main(['--dry']) exits 0 and writes nothing -")
make_sandbox(m, "cpb_extract_dry_main_", offline=True)
rc = cpb.main(["--dry"])
check("--dry exits 0", rc == 0, str(rc))
check("--dry does not create the results artifact", not cpb.OUT.exists(), str(cpb.OUT))

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
