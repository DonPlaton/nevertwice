"""`research/cross_project_bench.py` (A9, Q5): the `--dry` path end to end - 2 cases, a stub
embedder, no model, no Ollama, no GPU. Proves the plumbing (write project_a/project_c/distractor
-> seed the universal pool -> retrieve_cross_project under all three arms) wires together AND
that the metrics actually distinguish the arms: `all` must leak the positive control's planted
identifiers (proving the metric can see a leak at all), `universal` must not, `off` must inject
nothing.
"""
import sys
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

print("\n- --dry runs the full plumbing over 2 cases with a stub embedder, no model -")
result = cpb.run_bench(cases, dry=True)
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

print("\n- --help exits 0 -")
try:
    cpb.main(["--help"])
    help_rc = 0
except SystemExit as e:
    help_rc = e.code
check("--help exits 0", help_rc == 0, str(help_rc))

print(f"\ncross project bench (--dry only, no embedder): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
