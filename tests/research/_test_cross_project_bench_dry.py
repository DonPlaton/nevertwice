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

print("\n- C2 (2026-09-24): written_description_full is the UNTRUNCATED on-disk description -")
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
check("the capped snippet is a plain prefix of the full description (proves it's a char-slice)",
      long_desc.startswith(snippet), repr(snippet[-15:]))
check("the capped snippet, unlike the full field, ends mid-word here (the artifact C2 found)",
      snippet[-1].isalpha() and long_desc[len(snippet)].isalpha(), repr(snippet[-15:]))

print("\n- --help exits 0 -")
try:
    cpb.main(["--help"])
    help_rc = 0
except SystemExit as e:
    help_rc = e.code
check("--help exits 0", help_rc == 0, str(help_rc))

print(f"\ncross project bench (--dry only, no embedder): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
