"""The identifier-bound population (finding 3, 2026-09-24): 50 new cases beside the original
100 (unchanged), registered in `.loop/PREREG-Q5-IDENTIFIER-BOUND-2026-09-23.md` BEFORE any
number from them - `research/gen_cross_project_dataset.py::generate_identifier_bound` /
`validate_identifier_bound`, and `research/cross_project_bench.py`'s `--population` support.

Where the original 100-case population deliberately keeps the planted identifier OUT of the
stated rule, this population is the opposite BY DESIGN: the identifier IS the lesson, so a
faithful extractor cannot state the rule without it, and the `all` arm's PRODUCTION channel
(title/description via `_cross_line`) has something real to leak - the H5 fix's positive
control.

No GPU/Ollama on this side: verified with the shipped `research/data/cross_project_ib_v1.json`
directly, and end to end through `run_bench(..., extractor_mode="oracle")` with a STUBBED
embedder - not through `dry=True`, which forces BOTH sides to share project_a's own phrasing
(the ORIGINAL population's workaround for a hash-based stub embedder that cannot cluster two
genuinely different paraphrases) - wrong here, where A and C must stay genuinely distinct or the
"universal must not leak" half of the proof is meaningless. The stub is applied manually with
`dry=False` instead, which skips that override.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

sys.path.insert(0, str(ROOT / "research"))
import cross_project_bench as cpb  # noqa: E402
import gen_cross_project_dataset as gcpd  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- generate_identifier_bound() matches the registered construction rule exactly -")
ib = gcpd.generate_identifier_bound()
check("50 cases", ib["n_cases"] == 50, ib["n_cases"])
problems = gcpd.validate_identifier_bound(ib)
check("validates with zero problems", not problems, problems[:5])
counts = {cls: sum(1 for c in ib["cases"] if c["identifier_class"] == cls)
         for cls in gcpd.IDENTIFIER_CLASSES}
check("class counts are exactly 13 ip / 13 host / 12 path / 12 entity",
      counts == {"ip": 13, "host": 13, "path": 12, "entity": 12}, counts)
ids = [c["id"] for c in ib["cases"]]
check("ids run cpv1-ib-001 .. cpv1-ib-050 in order",
      ids == [f"cpv1-ib-{i:03d}" for i in range(1, 51)], ids[:3] + ["..."] + ids[-3:])

print("\n- one identifier per case, and it IS the stated rule (not kept out of it) -")
c0 = ib["cases"][0]
cls0 = c0["identifier_class"]
val_a = c0["project_a"]["planted"][cls0]
check("project_a's principle contains its own planted identifier",
      val_a in c0["project_a"]["principle"], c0["project_a"]["principle"])
check("project_a's description contains it too (the PRODUCTION channel, H5's fix target)",
      val_a in c0["project_a"]["description"])
check("project_a's session transcript contains it (the --extract path's source material)",
      val_a in c0["project_a"]["session"])
val_c = c0["project_c"]["planted"][cls0]
check("project_a and project_c use DIFFERENT spellings of the same class",
      val_a != val_c, (val_a, val_c))
check("project_b's prompt does NOT contain either spelling",
      val_a not in c0["project_b"]["prompt"] and val_c not in c0["project_b"]["prompt"])

print("\n- no identifier value or project name collides with the original 100-case population -")
orig = gcpd.generate()
ib_vals, orig_vals = set(), set()
for c in ib["cases"]:
    for side in ("project_a", "project_c"):
        ib_vals |= set((c[side]["planted"] or {}).values())
for c in orig["cases"]:
    for side in ("project_a", "project_c"):
        orig_vals |= set((c[side]["planted"] or {}).values())
check("100 distinct identifier values in the ib population, all distinct from the original's",
      len(ib_vals) == 100 and not (ib_vals & orig_vals),
      sorted(ib_vals & orig_vals)[:5])
ib_projects = {c["project_a"]["project"] for c in ib["cases"]}
orig_projects = {c["project_a"]["project"] for c in orig["cases"]}
check("no project name collides either", not (ib_projects & orig_projects))

print("\n- the shipped data file matches what the generator produces, and the original is untouched -")
check("research/data/cross_project_ib_v1.json exists", cpb.IB_DATA.exists(), str(cpb.IB_DATA))
shipped = cpb.load_cases(cpb.IB_DATA, n=None)
check("50 cases in the shipped file", len(shipped) == 50, len(shipped))
check("shipped file validates with zero problems too",
      not gcpd.validate_identifier_bound({"cases": shipped}), "")
check("the original 100-case file still has exactly 100 cases (unchanged, per the registration)",
      len(cpb.load_cases(cpb.DATA, n=None)) == 100)


print("\n- end to end: `all` leaks on this population, `universal` does not (G5.1's reading rule) -")
#: A REAL embedder would cluster A/C's near-identical (only the identifier differs) principles
#: at high cosine and let token provenance do the actual work of catching the identifier at
#: PROMOTION time - untestable here without GPU. The hash-based stub cannot do that (two
#: different identifier substrings hash to unrelated vectors), so nothing clusters and nothing
#: promotes on THIS population under `--dry`'s stub - which still proves the half that matters
#: most cheaply: the PRODUCTION channel (title/description, unscanned) carries the identifier in
#: `all`, and write-time principle_scan (ip/host/path regex, unconditional) keeps `universal`
#: clean regardless of whether anything ever reaches the promotion step at all.
cpb.m.embed_text = lambda text, kind=None, timeout=None, project=None: cpb._fixed_stub_vector(text)
cpb.m.embedder_available = lambda *a, **k: True
cpb.m.embed_cache_usable = lambda: True
try:
    slice_cases = ib["cases"][:8] + ib["cases"][13:17] + ib["cases"][27:29] + ib["cases"][39:41]
    result = cpb.run_bench(slice_cases, extractor_mode="oracle", dry=False)
finally:
    pass  # this suite owns the whole process; _env_guard already sandboxed it
off, all_arm, universal = (result["arms"]["off"], result["arms"]["all"],
                          result["arms"]["universal"])
check("off injects nothing", off["leak"] == 0.0 and off["cross_chars_mean"] == 0.0)
check("all: leak is 1.0 - the positive control fires on this population (unlike the original "
     "de-identified one, where it structurally cannot - H5)", all_arm["leak"] == 1.0,
     str(all_arm))
check("all: benefit is 1.0 too", all_arm["benefit"] == 1.0)
check("universal: leak is 0.0 - the write-time gate holds", universal["leak"] == 0.0,
     str(universal))
#: leak_by_class is a fraction of the WHOLE slice (16 cases: 8 ip + 4 host + 2 path + 2
#: entity), not of that class's own subset - every class present must be > 0, not == 1.0.
#: C3 (2026-09-23): leak_by_class is now keyed off cpb.ALL_CLASSES (the original four
#: IDENTIFIER_CLASSES plus DIGITFREE_SHAPES) - this population never plants a digit-free
#: shape, so those 4 extra keys are always 0 here; check ONLY the four classes this
#: population actually uses, not the shape-only keys it structurally cannot leak.
check("every identifier class present in this slice leaks under 'all' (each > 0)",
     all(all_arm["leak_by_class"][cls] > 0 for cls in gcpd.IDENTIFIER_CLASSES),
     {cls: all_arm["leak_by_class"][cls] for cls in gcpd.IDENTIFIER_CLASSES})
check("no digit-free shape leaks under 'all' either (this population plants none)",
     all(all_arm["leak_by_class"][s] == 0 for s in cpb.DIGITFREE_SHAPES),
     {s: all_arm["leak_by_class"][s] for s in cpb.DIGITFREE_SHAPES})


print("\n- bench CLI: --population is wired and documented -")
import contextlib, io  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        cpb.main(["--help"])
    except SystemExit:
        pass
help_text = buf.getvalue()
check("--help lists --population", "--population" in help_text)
check("--help names all three choices", all(c in help_text for c in
                                            ("original", "identifier_bound", "both")))
check("--help still lists --case-ids (finding 1/diagnostic instrumentation, unaffected)",
     "--case-ids" in help_text)


print("\n- _cosine_distribution (finding 4): reports the spread, not just pass/fail -")
rows_synth = [{"principle_cosine": v} for v in (0.72, 0.75, 0.81, None, 0.60)]
cd = cpb._cosine_distribution(rows_synth)
check("n counts only the non-None values", cd["n"] == 4, cd)
check("values keeps them in row order, None dropped", cd["values"] == [0.72, 0.75, 0.81, 0.60],
     cd["values"])
check("mean is correct", cd["mean"] == round((0.72 + 0.75 + 0.81 + 0.60) / 4, 4), cd["mean"])
check("min/max are correct", (cd["min"], cd["max"]) == (0.60, 0.81), cd)
empty = cpb._cosine_distribution([{"principle_cosine": None}])
check("no principle pairs at all: n=0, every stat is None",
     empty == {"n": 0, "values": [], "mean": None, "median": None, "min": None, "max": None},
     empty)

print(f"\ncross project ib population: {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
