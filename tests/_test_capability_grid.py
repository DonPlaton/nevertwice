#!/usr/bin/env python3
"""The model-capability grid, checked without a model (GOAL F3).

F3's exit criterion is *quantifies how much the reader bounds actionability*. The word doing the
work is **bounds**: the grid only measures the reader if everything except the reader is held
still, and only quantifies anything if the spread it reports is compared against the interval
the sample supports.

So this suite checks the parts that decide whether the numbers mean anything, and it does so on
the recorded generations rather than by running a GPU - which is also what keeps it hermetic:

* the memory is genuinely **fixed** across cells - same episodes, same prevention, same prompts,
  and the prevention is the very sentence F2's curated-file arm was scored on;
* the grader is **one deterministic function** with no per-model branch and no model in it. An
  LLM judge inside a capability measurement would put a second uncontrolled capability in the
  middle of it;
* adoption is reported over a **strictness sweep**, so no conclusion rests on one cutoff;
* a cell that could not run is **recorded** - `BLOCKED` for missing weights, `NOT RUN` for a
  gate - never quietly missing from the grid;
* the finding **compares the spread to the sampling interval** before calling anything a
  capability effect.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import capability_grid as CG      # noqa: E402
import matched_conditions as MC   # noqa: E402

PASSED = 0
FAILED = 0

RESULTS = CG.load_results()
CELLS = RESULTS.get("cells", {})


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════════════ the memory is held still ══════════════════════════

def test_the_memory_is_fixed_across_every_cell() -> None:
    """Vary the reader; vary nothing else. Otherwise the grid measures the difference."""
    print("\n- same episodes, same prevention, every cell -")
    check("cells were recorded", len(CELLS) >= 2, str(sorted(CELLS)))
    if len(CELLS) < 2:
        return

    per_cell = {name: {r["id"]: tuple(r["markers"]) for r in cell["records"]}
                for name, cell in CELLS.items()}
    first = sorted(per_cell)[0]
    drift = []
    for name, records in per_cell.items():
        if set(records) != set(per_cell[first]):
            drift.append(f"{name}: different episode set")
            continue
        for ep_id, markers in records.items():
            if markers != per_cell[first][ep_id]:
                drift.append(f"{name}/{ep_id}: different prevention")
    check("every cell saw the same episodes and the same prevention", not drift,
          "; ".join(drift[:3]))

    check("the episode set is F1's positives",
          set(per_cell[first]) == {e["id"] for e in MC.load_corpus()["episodes"] if e["label"]},
          "a grid on a different corpus cannot be read beside F1 and F2")


def test_the_prevention_is_the_one_f2_scored() -> None:
    """Reusing F2's rules is what makes 'memory held fixed' true ACROSS tasks, not just within."""
    print("\n- the sentence the reader sees is F2's sentence -")
    rules = json.loads((ROOT / "research" / "cheap_baselines_rules.json")
                       .read_text(encoding="utf-8"))["curated_agents_md"]["rules"]
    texts = {r["text"] for r in rules}
    mismatched = []
    for ep in CG.episodes():
        if ep["prevention"] not in texts:
            mismatched.append(ep["id"])
    check("every prevention comes from F2's curated rules", not mismatched,
          str(mismatched[:4]))
    check("and every positive episode has one", len(CG.episodes()) >= 20,
          str(len(CG.episodes())))


def test_the_two_prompts_differ_only_by_the_memory() -> None:
    print("\n- the only difference between the arms is the surfaced sentence -")
    ep = CG.episodes()[0]
    without, with_ = CG.prompts(ep)
    check("the episode text is in both", ep["text"] in without and ep["text"] in with_)
    check("the prevention is only in the memory arm",
          ep["prevention"] not in without and ep["prevention"] in with_,
          "a control arm that carries the answer measures nothing")
    check("both ask for the same thing",
          "next concrete step in one sentence" in without
          and "next concrete step in one sentence" in with_)
    extra = with_.replace(ep["prevention"], "").replace(ep["title"], "")
    check("and the memory arm adds nothing else of substance",
          len(extra) - len(without) < 120,
          f"the memory arm carries {len(extra) - len(without)} extra characters of scaffolding")


# ═══════════════════════ the grader ════════════════════════════════════

def test_the_grader_is_deterministic_and_has_no_model_in_it() -> None:
    print("\n- one grader, no judge -")
    ep = CG.episodes()[0]
    text = "I will " + ep["prevention"]
    first = CG.adopted(text, ep["markers"])
    check("the grader is deterministic",
          all(CG.adopted(text, ep["markers"]) == first for _ in range(5)))
    check("a sentence carrying the prevention is adopted", first is True,
          "the grader cannot recognise its own prevention")
    check("an unrelated sentence is not",
          CG.adopted("I will rename the helper and update its callers.", ep["markers"])
          is False)
    check("an empty answer is not", CG.adopted("", ep["markers"]) is False)
    check("and an episode with no prevention can never be adopted",
          CG.adopted("anything at all", []) is False)

    source = Path(CG.__file__).read_text(encoding="utf-8")
    grader = source[source.index("def adopted("):source.index("def score_cell(")]
    for forbidden in ("generate", "AutoModel", "openai", "anthropic", "judge"):
        check(f"the grader contains no {forbidden}", forbidden not in grader,
              "an LLM judge inside a capability measurement is a second uncontrolled capability")


def test_adoption_is_swept_not_pinned_to_one_cutoff() -> None:
    print("\n- the strictness sweep -")
    check("more than one strictness is defined", len(CG.STRICTNESS_GRID) >= 3,
          str(CG.STRICTNESS_GRID))
    check("the headline cutoff is one of the swept values",
          CG.DEFAULT_STRICTNESS in CG.STRICTNESS_GRID, str(CG.DEFAULT_STRICTNESS))
    for name, cell in CELLS.items():
        check(f"{name}: every strictness is scored",
              set(cell["scores"]) == {str(s) for s in CG.STRICTNESS_GRID},
              str(sorted(cell["scores"])))
        rates = [cell["scores"][str(s)]["rate_with"] for s in CG.STRICTNESS_GRID]
        check(f"{name}: a stricter grader never scores higher",
              all(a >= b - 1e-9 for a, b in zip(rates, rates[1:])), str(rates))


def test_the_scoring_maths_is_right() -> None:
    print("\n- the arithmetic, on a case with a known answer -")
    records = [
        {"id": "a", "markers": ["alpha", "beta"], "no_memory": "alpha beta",
         "with_memory": "alpha beta"},
        {"id": "b", "markers": ["alpha", "beta"], "no_memory": "nothing",
         "with_memory": "alpha beta"},
        {"id": "c", "markers": ["alpha", "beta"], "no_memory": "nothing",
         "with_memory": "nothing"},
    ]
    scored = CG.score_cell(records)["2"]
    check("n is the record count", scored["n"] == 3, str(scored))
    check("adoption without memory is counted", scored["adopted_without_memory"] == 1,
          str(scored))
    check("adoption with memory is counted", scored["adopted_with_memory"] == 2, str(scored))
    # Compared at the resolution the code publishes: `score_cell` rounds to 4dp on purpose,
    # so an exact-equality assertion would be testing float formatting, not the arithmetic.
    check("the lift is the difference over n",
          abs(scored["lift"] - 1 / 3) < 1e-4, str(scored["lift"]))
    check("and an interval accompanies it", scored["lift_ci95"][0] is not None,
          str(scored["lift_ci95"]))


# ══════════════ nothing that could not run goes missing ════════════════

def test_a_cell_that_could_not_run_is_recorded() -> None:
    print("\n- BLOCKED and NOT RUN are states, not absences -")
    g = CG.grid()
    named = set(g["rows"] and [r["model"] for r in g["rows"]]) | set(g["not_run"]) \
        | set(g.get("blocked", {}))
    missing = [n for n in set(CG.LOCAL_MODELS) | set(CG.GATED_MODELS) if n not in named]
    check("every model in the grid's definition appears somewhere", not missing,
          f"{missing} vanished from the grid entirely")
    for name, spec in g["not_run"].items():
        check(f"{name} says why it did not run", bool(spec.get("why")))
        check(f"{name} says what the owner must do",
              bool(spec.get("what_the_owner_must_do")))
    for name, spec in g.get("blocked", {}).items():
        check(f"{name} lists the missing files", bool(spec.get("missing_files")), str(spec))
        check(f"{name} distinguishes blocked from gated",
              "gated" not in spec["why"].lower(),
              "incomplete weights are not a permission problem and must not read as one")


def test_an_incomplete_checkpoint_is_diagnosed_not_crashed() -> None:
    """A half-finished download is the ordinary case on a machine that collects models."""
    print("\n- a partial download is a diagnosis, not a traceback -")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "model.safetensors.index.json").write_text(json.dumps({
            "weight_map": {"a.weight": "model-00001-of-00002.safetensors",
                           "b.weight": "model-00002-of-00002.safetensors"}}),
            encoding="utf-8")
        (root / "model-00002-of-00002.safetensors").write_bytes(b"x")
        absent = CG.missing_shards(str(root))
        check("the missing shard is named", absent == ["model-00001-of-00002.safetensors"],
              str(absent))
        (root / "model-00001-of-00002.safetensors").write_bytes(b"x")
        check("a complete checkpoint reports nothing missing",
              CG.missing_shards(str(root)) == [], str(CG.missing_shards(str(root))))

    with tempfile.TemporaryDirectory() as tmp:
        check("a checkpoint with no weights at all is caught",
              CG.missing_shards(tmp) == ["model.safetensors"], str(CG.missing_shards(tmp)))


def test_the_gated_cells_refuse_rather_than_pretending() -> None:
    """Refusing is not enough: the refusal has to say it is a GATE and how to open it.

    A first version only asserted a non-zero exit. Deleting the gate check entirely still
    exited non-zero - the name simply fell through to "unknown model" - so the mutation
    survived while the owner silently lost the one thing they needed: what to do about it.
    """
    import io
    from contextlib import redirect_stderr
    print("\n- asking for a frontier cell does not quietly give a local one -")
    for name in CG.GATED_MODELS:
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = CG.main(["--model", name])
        message = buf.getvalue()
        check(f"--model {name} exits non-zero", rc != 0, str(rc))
        check(f"--model {name} says it is GATED", "GATED" in message, message[:100])
        check(f"--model {name} says what the owner must do",
              "provider key" in message, message[:100])
        check(f"--model {name} states that nothing was measured",
              "Nothing was measured" in message, message[:100])


# ═══════════════════════ THE EXIT CRITERION ════════════════════════════

def test_the_finding_compares_the_spread_to_the_interval() -> None:
    """F3's exit criterion says *quantifies*. A spread inside the noise quantifies nothing."""
    print("\n- THE EXIT CRITERION: a capability effect smaller than the noise is not one -")
    n = 30
    tight = [{"model": "s", "params_b": 0.5, "lift": 0.40, "n": n},
             {"model": "l", "params_b": 3.0, "lift": 0.44, "n": n}]
    text = " ".join(CG._finding(tight)).lower()
    check("THE EXIT CRITERION: a spread inside the interval is NOT called a capability effect",
          "not distinguished" in text,
          "a 0.04 spread on n=30 is noise and must be reported as such")
    check("and it says so is a statement about the evidence",
          "statement about the evidence" in text, text[:120])

    wide = [{"model": "s", "params_b": 0.5, "lift": 0.10, "n": n},
            {"model": "l", "params_b": 3.0, "lift": 0.70, "n": n}]
    text = " ".join(CG._finding(wide)).lower()
    check("a spread that clears the interval IS reported as bounding actionability",
          "bound actionability" in text or "bounds actionability" in text, text[:160])

    dip = [{"model": "s", "params_b": 0.5, "lift": 0.47, "n": n},
           {"model": "m", "params_b": 1.5, "lift": 0.27, "n": n},
           {"model": "l", "params_b": 3.0, "lift": 0.60, "n": n}]
    text = " ".join(CG._finding(dip)).lower()
    check("a non-monotonic ladder is flagged", "not monotonic" in text, text[:160])
    check("and is read as noise rather than as a real effect", "noise" in text)

    dead = [{"model": "s", "params_b": 0.5, "lift": 0.0, "n": n},
            {"model": "l", "params_b": 3.0, "lift": 0.7, "n": n}]
    text = " ".join(CG._finding(dead)).lower()
    check("a cell where memory never helps is called a deployment constraint",
          "deployment constraint" in text, text[:160])


def test_every_finding_carries_the_standing_caveats() -> None:
    print("\n- the warnings that belong on every version of this grid -")
    for rows in ([{"model": "s", "params_b": 0.5, "lift": 0.4, "n": 30},
                  {"model": "l", "params_b": 3.0, "lift": 0.44, "n": 30}],
                 [{"model": "s", "params_b": 0.5, "lift": 0.1, "n": 30},
                  {"model": "l", "params_b": 7.0, "lift": 0.7, "n": 30}]):
        text = " ".join(CG._finding(rows)).lower()
        check("the frontier cells are named as not run", "g8" in text, text[:120])
        check("the grader's crudeness is stated", "over-credits" in text)
        check("and it says the grader supports comparison, not absolute rates",
              "comparison" in text)


def main() -> int:
    for fn in (test_the_memory_is_fixed_across_every_cell,
               test_the_prevention_is_the_one_f2_scored,
               test_the_two_prompts_differ_only_by_the_memory,
               test_the_grader_is_deterministic_and_has_no_model_in_it,
               test_adoption_is_swept_not_pinned_to_one_cutoff,
               test_the_scoring_maths_is_right,
               test_a_cell_that_could_not_run_is_recorded,
               test_an_incomplete_checkpoint_is_diagnosed_not_crashed,
               test_the_gated_cells_refuse_rather_than_pretending,
               test_the_finding_compares_the_spread_to_the_interval,
               test_every_finding_carries_the_standing_caveats):
        fn()
    print(f"\ncapability grid: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
