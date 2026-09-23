"""The digit-free identifier sub-population (C3, 2026-09-23): 40 new cases beside the original
100 and the identifier-bound 50 (both unchanged), registered in
`.loop/PREREG-Q5-DIGITFREE-2026-09-23.md` BEFORE any number from them -
`research/gen_cross_project_dataset.py::generate_digitfree` / `validate_digitfree`, and
`research/cross_project_bench.py`'s `--population digit_free` support.

Why it exists: the write-time classifier (`principle_scan`'s regex groups, plus its narrowed
`_looks_like_identifier`) forbids only tokens with a digit, a dot or a slash - the
identifier-bound population plants exactly such tokens (an IP, a hostname, a path, an entity
name), so a leak of a DIGIT-FREE code identifier (`billing_service`, `BillingService`,
`BILLING_CONFIG_FLAG`, `billing-service`) is outside what G5.1 can observe without a population
that plants one. Same "identifier IS the lesson" construction as identifier-bound: one shape
per case, 10 cases per shape (snake_case, camel_pascal, screaming_snake, kebab).

No GPU/Ollama on this side: verified with the shipped `research/data/cross_project_df_v1.json`
directly, and end to end through `run_bench(..., extractor_mode="oracle")` with a STUBBED
embedder - the SAME limitation `_test_cross_project_ib_population.py` already documents and
accepts: a hash-based stub cannot cluster two genuinely different identifier substrings, so
`universal`'s 0.0 leak here proves the PRODUCTION channel (`all`) fires and NOTHING is promoted
under the stub - not that promotion-time provenance correctly rejected a genuine leak. The
gate this population exists for (does token-provenance actually catch a digit-free identifier
once a REAL embedder clusters two honest paraphrases) needs a real embedder and is explicitly
OUT OF SCOPE this turn (no GPU, no Ollama) - reported here as untested, not as a PASS.
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


print("\n- generate_digitfree() matches the registered construction rule exactly -")
df = gcpd.generate_digitfree()
check("40 cases", df["n_cases"] == 40, df["n_cases"])
problems = gcpd.validate_digitfree(df)
check("validates with zero problems", not problems, problems[:5])
shape_counts = {shape: sum(1 for c in df["cases"] if c["identifier_shape"] == shape)
               for shape in gcpd.DIGITFREE_SHAPES}
check("shape counts are exactly 10 each",
      shape_counts == {s: 10 for s in gcpd.DIGITFREE_SHAPES}, shape_counts)
ids = [c["id"] for c in df["cases"]]
check("ids run cpv1-df-001 .. cpv1-df-040 in order",
      ids == [f"cpv1-df-{i:03d}" for i in range(1, 41)], ids[:3] + ["..."] + ids[-3:])

print("\n- C3 fix (the coordinator's finding): shape is CROSSED with rule template, not "
     "confounded with it -")
from collections import Counter  # noqa: E402
shape_template_table = Counter((c["identifier_shape"], c["rule_template"]) for c in df["cases"])
check("every case carries a rule_template field",
      all("rule_template" in c for c in df["cases"]))
check("every rule_template value is itself one of the four shape names (template and shape "
     "share a vocabulary, by design - see _DF_SHAPE_TEMPLATE_COUNTS)",
     all(c["rule_template"] in gcpd.DIGITFREE_SHAPES for c in df["cases"]))
min_cell = min(shape_template_table[(s, t)] for s in gcpd.DIGITFREE_SHAPES
              for t in gcpd.DIGITFREE_SHAPES)
check("no shape has FEWER than 2 cases of any template (the confound this fix removes: the "
     "first generation had exactly ONE template per shape, i.e. a 0 in every off-diagonal "
     "cell)", min_cell >= 2, dict(shape_template_table))
row_sums = {s: sum(shape_template_table[(s, t)] for t in gcpd.DIGITFREE_SHAPES)
           for s in gcpd.DIGITFREE_SHAPES}
col_sums = {t: sum(shape_template_table[(s, t)] for s in gcpd.DIGITFREE_SHAPES)
           for t in gcpd.DIGITFREE_SHAPES}
check("every shape uses 10 cases total across templates (row sums)",
      all(v == 10 for v in row_sums.values()), row_sums)
check("every template is used 10 times total across shapes (column sums, so a per-template "
     "leak number is equally balanced)", all(v == 10 for v in col_sums.values()), col_sums)

print("\n- no digit, dot or slash anywhere in any planted value -")
import re  # noqa: E402
all_vals = [v for c in df["cases"] for side in ("project_a", "project_c")
           for v in (c[side]["planted"] or {}).values()]
check("80 planted values total (40 cases x 2 sides)", len(all_vals) == 80, len(all_vals))
bad = [v for v in all_vals if re.search(r"[0-9./]", v)]
check("none contain a digit, dot or slash", not bad, bad[:5])

print("\n- one identifier per case, and it IS the stated rule (not kept out of it) -")
c0 = df["cases"][0]
shape0 = c0["identifier_shape"]
val_a = c0["project_a"]["planted"][shape0]
check("project_a's principle contains its own planted identifier",
      val_a in c0["project_a"]["principle"], c0["project_a"]["principle"])
check("project_a's description contains it too (the PRODUCTION channel)",
      val_a in c0["project_a"]["description"])
check("project_a's session transcript contains it (the --extract path's source material)",
      val_a in c0["project_a"]["session"])
val_c = c0["project_c"]["planted"][shape0]
check("project_a and project_c use DIFFERENT spellings of the same shape",
      val_a != val_c, (val_a, val_c))
check("project_b's prompt does NOT contain either spelling",
      val_a not in c0["project_b"]["prompt"] and val_c not in c0["project_b"]["prompt"])

print("\n- no identifier value or project name collides with the original 100 or ib 50 -")
orig = gcpd.generate()
ib = gcpd.generate_identifier_bound()
df_vals, orig_vals, ib_vals = set(), set(), set()
for c in df["cases"]:
    for side in ("project_a", "project_c"):
        df_vals |= set((c[side]["planted"] or {}).values())
for c in orig["cases"]:
    for side in ("project_a", "project_c"):
        orig_vals |= set((c[side]["planted"] or {}).values())
for c in ib["cases"]:
    for side in ("project_a", "project_c"):
        ib_vals |= set((c[side]["planted"] or {}).values())
check("80 distinct identifier values in the df population, none shared with original or ib",
      len(df_vals) == 80 and not (df_vals & orig_vals) and not (df_vals & ib_vals),
      (sorted(df_vals & orig_vals)[:3], sorted(df_vals & ib_vals)[:3]))
df_projects = {c["project_a"]["project"] for c in df["cases"]} | \
              {c["project_c"]["project"] for c in df["cases"]}
orig_projects = {c["project_a"]["project"] for c in orig["cases"]}
ib_projects = {c["project_a"]["project"] for c in ib["cases"]}
check("no project name collides either",
      not (df_projects & orig_projects) and not (df_projects & ib_projects))

print("\n- the shipped data file matches what the generator produces, and neither original nor "
     "ib is touched -")
check("research/data/cross_project_df_v1.json exists", cpb.DF_DATA.exists(), str(cpb.DF_DATA))
shipped = cpb.load_cases(cpb.DF_DATA, n=None)
check("40 cases in the shipped file", len(shipped) == 40, len(shipped))
check("shipped file validates with zero problems too",
      not gcpd.validate_digitfree({"cases": shipped}), "")
check("the original 100-case file still has exactly 100 cases (unchanged)",
      len(cpb.load_cases(cpb.DATA, n=None)) == 100)
check("the identifier-bound 50-case file still has exactly 50 cases (unchanged)",
      len(cpb.load_cases(cpb.IB_DATA, n=None)) == 50)


print("\n- end to end, all 40 cases: `all` leaks on this population; `universal`'s 0.0 here is "
     "UNTESTED, not a G5.1 pass (see module docstring) -")
#: Same limitation `_test_cross_project_ib_population.py` documents: the hash-based stub cannot
#: cluster two genuinely-different identifier substrings, so nothing ever reaches promotion -
#: `universal`'s leak==0.0 here proves write-time principle_scan has NOTHING to catch these
#: shapes with (by construction, matching the PREREG's own premise) and that the production
#: channel (`all`) genuinely carries the identifier - it does NOT prove token-provenance
#: catches a digit-free leak, because nothing was ever promoted for it to catch. That gate
#: needs a REAL embedder and is out of scope this turn (no GPU, no Ollama).
cpb.m.embed_text = lambda text, kind=None, timeout=None, project=None: cpb._fixed_stub_vector(text)
cpb.m.embedder_available = lambda *a, **k: True
cpb.m.embed_cache_usable = lambda: True
result = cpb.run_bench(df["cases"], extractor_mode="oracle", dry=False)
off, all_arm, universal = (result["arms"]["off"], result["arms"]["all"], result["arms"]["universal"])
check("off injects nothing", off["leak"] == 0.0 and off["cross_chars_mean"] == 0.0)
check("all: leak is 1.0 - the positive control fires on this population (write-time "
     "principle_scan never touches title/description, and these shapes have no regex class "
     "at all)", all_arm["leak"] == 1.0, str(all_arm))
check("all: benefit is 1.0 too", all_arm["benefit"] == 1.0)
check("all: every shape present leaks at exactly 0.25 (10 of 40 cases each)",
     all(v == 0.25 for v in all_arm["leak_by_class"].values()
        if v > 0) and sum(1 for v in all_arm["leak_by_class"].values() if v > 0) == 4,
     all_arm["leak_by_class"])
check("universal: leak is 0.0 (see module docstring - untested under the stub, not a G5.1 "
     "pass: benefit is ALSO 0.0, meaning nothing was promoted at all)",
     universal["leak"] == 0.0, str(universal))
check("universal: benefit is 0.0 too - confirms the 0.0 leak is 'nothing reached promotion', "
     "not 'promotion correctly rejected a leak' (the honest distinction this test exists to "
     "keep visible)", universal["benefit"] == 0.0, str(universal))
check("write_rejections_by_class has ALL_CLASSES keys (C3 widening), every one 0 here - "
     "digit-free shapes have no regex class to trip at write time",
     set(result["write_rejections_by_class"]) == set(cpb.ALL_CLASSES)
     and all(v == 0 for v in result["write_rejections_by_class"].values()),
     result["write_rejections_by_class"])


print("\n- bench CLI: --population digit_free is wired and documented -")
import contextlib, io  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        cpb.main(["--help"])
    except SystemExit:
        pass
help_text = buf.getvalue()
check("--help lists --population", "--population" in help_text)
check("--help names digit_free among the choices", "digit_free" in help_text)
check("--help still names the original three (original/identifier_bound/both)",
     all(c in help_text for c in ("original", "identifier_bound", "both")))

print(f"\ncross project digit-free population: {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
