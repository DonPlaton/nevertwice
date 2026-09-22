"""A stand that pins the extraction MODEL pins its TEMPERATURE too, or says here why not.

The engine samples. `_engine_store.py` reads `NEVERTWICE_EXTRACT_TEMP` with a default of **0.2**,
which is right for the live hook and wrong for anything being measured: the same prompt through
`generate_json` at 0.2 gives a different answer every time, and at 0 the same answer from a fresh
interpreter, from the same process, and with unrelated prompts served in between (2026-09-22).

Six of the nine stands that configure the extractor had been measuring at 0.2 without knowing it.

* `supersession_bench.py` wrote "the extraction model is not deterministic at temperature 0" into
  its own artifact, and the sentence reached 24 registered claims as text; the stand has produced
  229 claims in all, of which 144 are waiting to be re-measured. It was never at temperature 0.
  Pinned, three runs of one commit read stale 0.0667 / 0.0667 / 0.0667 and 1 of 80 cases served
  different text, where all 80 had before.
* `guard_bench.py` generates the guard - a regex - with this call, and then measures that regex's
  recall and false-positive rate. Three fresh processes on one note gave two distinct patterns at
  0.2 and one at 0. Its LLM cache hid this from anyone who already had the cache; the cache is
  `*_cache.json` and never committed, so the number was reproducible from the artifact and not
  from the command, and the register stores the command.
* `abstention_ab.py`, `asof_bench.py`, `frontier_eval.py` and `k8_collisions.py` all ingest
  through `api.capture_session`, and all four set the model without setting the temperature.

**Why the population is "sets NEVERTWICE_MODEL" and not "calls the extractor".** The honest
predicate would be the second one, and it cannot be had exactly: `supersession_bench` reaches the
extractor through `api.capture_session`, so a walk for `generate_json` finds `guard_bench` and
misses the stand that started this, and `k8_collisions` never names the call at all - its model
sits under `_same_fact_verdict` on the write path. Pinning the model is the mark of a file that
has decided which extractor its numbers come from, and the two properties belong to the same
call, so a file that fixes one and leaves the other loose is the defect itself. The predicate is
also not the one under test: it is about the MODEL, and the check is about the TEMPERATURE.

The gap that argument leaves is a stand that reaches the extractor and does NOT pin the model -
it would fall out of the population silently. So that is the second check here: every file in
`research/*.py` that names one of the six engine doors below must pin the model. The walk is
crude and its errors go towards strictness, which is the safe direction; today nine stands reach
a door and all nine pin the model.
"""
import _env_guard  # noqa: F401
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


TEMP = "NEVERTWICE_EXTRACT_TEMP"
MODEL = "NEVERTWICE_MODEL"


def writes_env(tree: ast.AST, key: str) -> bool:
    """`os.environ[key] = ...` or `os.environ.setdefault(key, ...)`. A read
    (`os.environ.get(key)`) is not a pin and does not count."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == key):
                    return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == key):
            return True
    return False


#: A stand that pins the model and NOT the temperature, with the reason it may. Empty is the
#: healthy state; an entry is a promise that the number does not depend on the sample.
EXEMPT: dict[str, str] = {}

print("\n- the default this suite is protecting against is still a sampling one -")
store = (ROOT / "nevertwice" / "_engine_store.py").read_text(encoding="utf-8")
check("the engine's extraction temperature defaults to 0.2, not 0",
      f'os.environ.get("{TEMP}", "0.2")' in store,
      "the default moved; if it is now 0 this whole suite is obsolete, not merely passing")

print("\n- every stand that chooses an extractor also fixes its sampling -")
stands = sorted(p for p in (ROOT / "research").glob("*.py") if not p.name.startswith("_"))
check("the research directory still has stands to check", len(stands) > 10, len(stands))

pin_model, unpinned = [], []
for p in stands:
    tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
    if not writes_env(tree, MODEL):
        continue
    pin_model.append(p.name)
    if not writes_env(tree, TEMP) and p.name not in EXEMPT:
        unpinned.append(p.name)

check("the walk found the stands that configure the extractor rather than nothing",
      {"supersession_bench.py", "guard_bench.py", "asof_bench.py", "frontier_eval.py",
       "abstention_ab.py", "k8_collisions.py"} <= set(pin_model), ", ".join(pin_model))
check("a stand that only READS the variable is not counted as pinning it",
      not writes_env(ast.parse("import os\nx = os.environ.get('NEVERTWICE_EXTRACT_TEMP')"), TEMP))
check("no stand picks an extractor and leaves its temperature to the shell",
      not unpinned, ", ".join(unpinned))

print("\n- and nothing reaches the extractor from outside that population -")
#: The five engine functions that call `generate_json`, plus `capture_session`, the public door
#: every ingest goes through: `process_session` and `_retry_if_silent` (extraction),
#: `rerank_notes` (the recall path), `_same_fact_verdict` and `compact_context_if_needed` (the
#: write path). A stand that names any of them is running the model whether or not it says so.
DOORS = ("capture_session", "process_session", "generate_json", "write_typed_note",
         "rerank_notes", "compact_context_if_needed")


def reaches_extractor(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in DOORS:
            return True
        if isinstance(node, ast.Name) and node.id in DOORS:
            return True
    return False


#: A file that reaches a door and deliberately does NOT pin the model, with the reason. It must
#: still pin the temperature - that is checked below, not waived.
REACH_EXEMPT = {
    "qa_eval.py": "the judge is held at deepseek-chat through NEVERTWICE_DEEPSEEK_MODEL rather "
                  "than NEVERTWICE_MODEL, and no claim in the register is pending against it",
}

#: Every `research/*.py`, underscore-prefixed ones included. The population above skips them
#: because a leading underscore means a library rather than a stand, and libraries do not choose
#: models - but the DOOR walk must not skip them, or a future `research/_helper.py` that ingests
#: falls out of this suite by its filename. None do today (audit 2026-09-22); the check is so
#: that the first one does not arrive silently.
all_research = sorted((ROOT / "research").glob("*.py"))
reaching = [p.name for p in all_research
            if reaches_extractor(ast.parse(p.read_text(encoding="utf-8"), str(p)))]
check("the door walk finds stands rather than nothing", len(reaching) >= 6, ", ".join(reaching))
outside = sorted(set(reaching) - set(pin_model) - set(REACH_EXEMPT))
check("every stand that reaches the extractor is inside the population this suite checks",
      not outside, ", ".join(outside))
for name, why in REACH_EXEMPT.items():
    check(f"{name} is exempt from the model half only, and still pins the temperature",
          writes_env(ast.parse((ROOT / "research" / name).read_text(encoding="utf-8")), TEMP), why)
gone = sorted(set(REACH_EXEMPT) - set(reaching))
check("nothing is exempted that no longer reaches the extractor", not gone, ", ".join(gone))

print("\n- an exemption is a written reason, not a name -")
for name, why in EXEMPT.items():
    check(f"{name} is still there to be exempt", (ROOT / "research" / name).exists())
    check(f"{name}'s exemption says why", len(why) > 40, why)
stale = [n for n in EXEMPT if n not in pin_model]
check("nothing is exempted that no longer pins a model", not stale, ", ".join(stale))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
