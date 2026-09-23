"""`research/cross_project_bench.py`'s `--dry` stub-extractor path (A9, Q5 widening,
2026-09-23 review): the owner's concern was narrower than the oracle-mode bench alone can
prove - a pre-written, already-clean `principle` only proves `description` never crosses,
never that an EXTRACTED principle carrying a planted identifier gets caught.

This suite drives `run_bench(cases, extractor_mode="stub", dry=True)` - the deterministic,
no-model stand-in for a real extraction that deliberately smuggles one planted identifier of
EACH class (ip, host, path, entity) into an otherwise-clean principle, cycling across the 2
dry cases' project_a/project_c slots (one class per slot, all 4 covered). It proves
`principle_scan` rejects every one of them, that nothing then leaks through the `universal`
arm, and - the mutation the plan calls for - that bypassing `principle_scan` turns that
specific finding red BY NAME, without editing any shipped source.
"""
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


print("\n- --dry's stub poisons exactly one planted identifier of each class -")
make_sandbox(m, "cpb_extract_dry_", offline=True)
cases = cpb.load_cases(cpb.DATA, n=2)
check("2 cases loaded", len(cases) == 2, str(len(cases)))

result = cpb.run_bench(cases, extractor_mode="stub", dry=True)
rej = result["scanner_rejections_by_class"]
check("all 4 identifier classes are rejected by principle_scan, exactly once each",
      rej == {"ip": 1, "host": 1, "path": 1, "entity": 1}, str(rej))

universal = result["arms"]["universal"]
check("nothing leaked into the universal arm (every poisoned principle was caught before "
      "promotion)", universal["leak"] == 0.0, str(universal))
check("nothing was promoted either - a rejected principle is not even a promotion candidate",
      universal["benefit"] == 0.0, str(universal))

print("\n- mutation: bypassing principle_scan lets every poisoned identifier through -")
make_sandbox(m, "cpb_extract_dry_mut_", offline=True)
cases2 = cpb.load_cases(cpb.DATA, n=2)
saved_scan = m.principle_scan
m.principle_scan = lambda text, forbidden: text          # identity: no de-identification at all
try:
    result_mut = cpb.run_bench(cases2, extractor_mode="stub", dry=True)
finally:
    m.principle_scan = saved_scan
rej_mut = result_mut["scanner_rejections_by_class"]
check("mutation: WITHOUT principle_scan, NOTHING is rejected any more (would FAIL 'all 4 "
      "identifier classes are rejected by principle_scan, exactly once each' above)",
      rej_mut == {"ip": 0, "host": 0, "path": 0, "entity": 0}, str(rej_mut))
universal_mut = result_mut["arms"]["universal"]
check("mutation: WITHOUT principle_scan, a poisoned identifier reaches the universal arm "
      "(would FAIL 'nothing leaked into the universal arm' above)",
      universal_mut["leak"] > 0.0, str(universal_mut))

# sanity: the real, unmutated code (re-run once more, fresh) still catches every class.
make_sandbox(m, "cpb_extract_dry_control_", offline=True)
cases3 = cpb.load_cases(cpb.DATA, n=2)
result_control = cpb.run_bench(cases3, extractor_mode="stub", dry=True)
check("and the unmutated scanner still rejects every class after the mutation is reverted",
      result_control["scanner_rejections_by_class"] == {"ip": 1, "host": 1, "path": 1,
                                                        "entity": 1},
      str(result_control["scanner_rejections_by_class"]))

print("\n- main(['--dry']) exits 0 and writes nothing -")
make_sandbox(m, "cpb_extract_dry_main_", offline=True)
rc = cpb.main(["--dry"])
check("--dry exits 0", rc == 0, str(rc))
check("--dry does not create the results artifact", not cpb.OUT.exists(), str(cpb.OUT))

print("\n- CLI flag combinations resolve to the right extractor mode -")
import contextlib  # noqa: E402
import io  # noqa: E402


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
