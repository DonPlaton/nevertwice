#!/usr/bin/env python3
"""RESEARCH - the model-capability grid (GOAL F3).

A memory system does not prevent anything by itself. It surfaces a sentence, and a *reader*
either turns that sentence into a different action or does not. So the honest question is not
"does the memory help" but "how much of the help is bounded by the reader", and the only way to
answer it is to hold the memory fixed and vary the model.

This grid does exactly that. Every cell sees the **same** episode and the **same** surfaced
prevention - F2's curated rules, which are already mapped to failure classes - and differs only
in who is reading. Four cells run locally on the RTX 5090 (Qwen2.5 at 0.5B, 1.5B, 3B and 7B);
the two frontier cells are **G8** and are reported as not-run rather than dropped.

Two design decisions that decide whether the numbers mean anything:

* **The grader is deterministic and identical in every cell.** It checks whether the model's
  next step contains the content of the prevention. That is a crude measure of "acted on it" -
  and crude in exactly the same way for a 0.5B and a 7B, which is what a capability grid needs.
  An LLM judge would have been the obvious alternative and the wrong one: it would put a second,
  uncontrolled capability in the middle of a capability measurement.
* **Adoption is reported as a curve over the grader's strictness**, not at one cutoff, for the
  same reason F1 publishes a threshold sweep. A result that exists only at one marker threshold
  is a result about the threshold.

    python research/capability_grid.py --model qwen2.5-0.5b   # one cell
    python research/capability_grid.py --all                  # every local cell, in size order
    python research/capability_grid.py --report               # the grid from saved cells

Results accumulate per cell in `research/capability_grid.json`, so a cell that fails or is
interrupted costs only that cell.

Requires torch + transformers for the RUN. The report and the grading are stdlib-only, which is
what lets `tests/_test_capability_grid.py` check them without a GPU.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard                                    # noqa: E402
sandbox_guard.isolate("capability_grid")

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import anticipate as A                                  # noqa: E402
import memory_hook as m                                 # noqa: E402
import matched_conditions as MC                         # noqa: E402

OUT = ROOT / "research" / "capability_grid.json"
RULES = json.loads((ROOT / "research" / "cheap_baselines_rules.json").read_text(encoding="utf-8"))

#: The local ladder. Sizes are the point: the grid exists to show where actionability starts.
LOCAL_MODELS = {
    "qwen2.5-0.5b": {"path": r"D:/Local_AI_Models/Qwen2.5-0.5B-Instruct", "params_b": 0.5},
    "qwen2.5-1.5b": {"path": r"D:/Local_AI_Models/Qwen2.5-1.5B-Instruct", "params_b": 1.5},
    "qwen2.5-3b": {"path": r"D:/Local_AI_Models/Qwen2.5-3B-Instruct", "params_b": 3.0},
    "qwen2.5-7b": {"path": r"D:/Local_AI_Models/Qwen2.5-7B-Instruct", "params_b": 7.0},
}

#: The cells that need somebody's money. Built, scored, not run - the same treatment F2 gave the
#: LLM session-summary arm, for the same reason.
GATED_MODELS = {
    "frontier-a": {"why": "a frontier API call spends the owner's money (gate G8)",
                   "what_the_owner_must_do": "set the provider key and run "
                                             "`python research/capability_grid.py --model frontier-a`"},
    "frontier-b": {"why": "a second frontier API, same gate",
                   "what_the_owner_must_do": "set the provider key and run "
                                             "`python research/capability_grid.py --model frontier-b`"},
}

class IncompleteCheckpoint(RuntimeError):
    """A local model whose weight files are not all on disk."""

    def __init__(self, name: str, path: str, absent: list):
        self.name, self.path, self.absent = name, path, absent
        super().__init__(f"{name}: {len(absent)} weight file(s) missing from {path}: "
                         f"{', '.join(absent)}")


MAX_NEW_TOKENS = 70

#: How many of the prevention's content words must appear before we call it adopted. Reported
#: as a sweep; this is only the value the headline table uses.
DEFAULT_STRICTNESS = 2
STRICTNESS_GRID = (1, 2, 3, 4)


# ── the fixed memory ────────────────────────────────────────────────────

def prevention_for(stem: str) -> str | None:
    """The one-line prevention this failure class carries - F2's curated rules, reused.

    Reusing them rather than writing a second set keeps the memory genuinely FIXED across F2 and
    F3: the sentence the reader sees here is the same sentence the curated-file arm was scored
    on, so a difference between the two tasks is a difference in the reader, not in the wording.
    """
    for rule in RULES["curated_agents_md"]["rules"]:
        if stem in rule["covers"]:
            return rule["text"]
    return None


def markers_for(stem: str) -> set:
    text = prevention_for(stem)
    return set(A._content_tokens(text)) if text else set()


def episodes() -> list:
    """The positive episodes only: memory has nothing to surface on the negatives."""
    corpus = MC.load_corpus()
    out = []
    for ep in corpus["episodes"]:
        if not ep["label"]:
            continue
        prevention = prevention_for(ep["label"])
        if not prevention:
            continue
        title = next((s["title"] for s in corpus["signatures"] if s["stem"] == ep["label"]), "")
        out.append({**ep, "prevention": prevention, "title": title,
                    "markers": sorted(markers_for(ep["label"]))})
    return out


# ── the two prompts ─────────────────────────────────────────────────────

NO_MEMORY = ("You are an AI coding agent. You are about to do this:\n\n{episode}\n\n"
             "State your next concrete step in one sentence.")

WITH_MEMORY = ("You are an AI coding agent. You are about to do this:\n\n{episode}\n\n"
               "Your memory surfaced a past failure that resembles this situation:\n"
               "  {title} - {prevention}\n\n"
               "State your next concrete step in one sentence.")


def prompts(ep: dict) -> tuple:
    return (NO_MEMORY.format(episode=ep["text"]),
            WITH_MEMORY.format(episode=ep["text"], title=ep["title"],
                               prevention=ep["prevention"]))


# ── the grader ──────────────────────────────────────────────────────────

def adopted(output: str, markers: list, strictness: int = DEFAULT_STRICTNESS) -> bool:
    """Did the next step take the prevention on board?

    Deterministic, identical in every cell, and deliberately crude. It counts how many of the
    prevention's content words the model's own sentence reproduces. That over-credits a model
    that parrots and under-credits one that paraphrases - equally, in every cell, which is the
    property that makes the COMPARISON valid even though the absolute number is soft. Reported
    across a strictness sweep so no conclusion rests on where this line was drawn.
    """
    if not markers:
        return False
    said = set(A._content_tokens(output or ""))
    return len(said & set(markers)) >= strictness


def score_cell(records: list) -> dict:
    """One model's results, swept over grader strictness."""
    by_strictness = {}
    for s in STRICTNESS_GRID:
        without = sum(1 for r in records if adopted(r["no_memory"], r["markers"], s))
        with_ = sum(1 for r in records if adopted(r["with_memory"], r["markers"], s))
        n = len(records)
        by_strictness[str(s)] = {
            "n": n,
            "adopted_without_memory": without,
            "adopted_with_memory": with_,
            "rate_without": round(without / n, 4) if n else None,
            "rate_with": round(with_ / n, 4) if n else None,
            "lift": round((with_ - without) / n, 4) if n else None,
            "lift_ci95": MC.wilson(with_, n) if n else (None, None),
        }
    return by_strictness


# ── running a cell ──────────────────────────────────────────────────────

def missing_shards(path: str) -> list:
    """Which weight files a local checkpoint claims but does not have.

    A half-finished download is the ordinary case on a machine that collects models, and
    `from_pretrained` reports it as a FileNotFoundError forty frames deep. Checking the index
    up front turns that into a cell marked BLOCKED with the exact files to fetch - which is the
    difference between a grid with a hole in it and a grid that crashed.
    """
    root = Path(path)
    index = root / "model.safetensors.index.json"
    if not index.is_file():
        single = root / "model.safetensors"
        return [] if single.is_file() else ["model.safetensors"]
    try:
        weight_map = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
    except (json.JSONDecodeError, KeyError, OSError):
        return ["model.safetensors.index.json (unreadable)"]
    return sorted({shard for shard in set(weight_map.values())
                   if not (root / shard).is_file()})


def run_local(name: str, limit: int | None = None) -> dict:
    """Generate both conditions for every episode with one local model."""
    import torch                                        # noqa: PLC0415 - GPU-only path
    from transformers import AutoTokenizer, AutoModelForCausalLM   # noqa: PLC0415

    spec = LOCAL_MODELS[name]
    absent = missing_shards(spec["path"])
    if absent:
        raise IncompleteCheckpoint(name, spec["path"], absent)
    eps = episodes()[:limit] if limit else episodes()
    print(f"  loading {name} ({spec['params_b']}B) ...", flush=True)
    started = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(spec["path"])
    model = AutoModelForCausalLM.from_pretrained(spec["path"], dtype=torch.bfloat16,
                                                 device_map="cuda")
    load_s = time.perf_counter() - started

    def generate(prompt: str) -> str:
        enc = tok.apply_chat_template([{"role": "user", "content": prompt}],
                                      add_generation_prompt=True, return_tensors="pt",
                                      return_dict=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        return tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()

    records = []
    started = time.perf_counter()
    for i, ep in enumerate(eps, 1):
        a, b = prompts(ep)
        records.append({"id": ep["id"], "label": ep["label"], "markers": ep["markers"],
                        "no_memory": generate(a), "with_memory": generate(b)})
        if i % 10 == 0:
            print(f"    {i}/{len(eps)} episodes", flush=True)
    gen_s = time.perf_counter() - started

    # No `del model` here: `generate` closes over it, and deleting a name a live closure
    # still needs is how you get an UnboundLocalError on the next call. Both go out of scope
    # when this function returns; the caller frees the VRAM between cells.
    return {
        "model": name, "params_b": spec["params_b"], "kind": "local",
        "device": torch.cuda.get_device_name(0),
        "max_new_tokens": MAX_NEW_TOKENS, "decoding": "greedy (do_sample=False)",
        "load_seconds": round(load_s, 2),
        "seconds_per_generation": round(gen_s / max(1, 2 * len(eps)), 3),
        "records": records,
        "scores": score_cell(records),
    }


def _free_vram() -> None:
    """Release the last cell's weights before the next one loads.

    Called from the caller rather than from inside `run_local`, so it runs after the model and
    its closure have actually gone out of scope. Freeing it any earlier only looks tidy.
    """
    try:
        import torch                                    # noqa: PLC0415 - GPU-only path
        import gc                                       # noqa: PLC0415
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:                                   # noqa: BLE001 - best effort
        pass


def load_results() -> dict:
    if OUT.is_file():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"schema_version": 1, "cells": {}, "gated": {}}


def save_blocked(name: str, why: str, absent: list) -> None:
    """A cell that could not run is recorded, never silently absent from the grid."""
    data = load_results()
    data.setdefault("blocked", {})[name] = {
        "why": why,
        "missing_files": absent,
        "what_the_owner_must_do": f"complete the download of {LOCAL_MODELS[name]['path']} "
                                  f"({len(absent)} file(s)), then re-run "
                                  f"`python research/capability_grid.py --model {name}`",
    }
    data["cells"].pop(name, None)
    m.write_atomic(OUT, json.dumps(data, ensure_ascii=False, indent=1))


def save_cell(cell: dict) -> None:
    data = load_results()
    data["cells"][cell["model"]] = cell
    data["gated"] = {k: v for k, v in GATED_MODELS.items()}
    data["generated_by"] = "python research/capability_grid.py --all"
    m.write_atomic(OUT, json.dumps(data, ensure_ascii=False, indent=1))


# ── the grid ────────────────────────────────────────────────────────────

def grid(data: dict | None = None, strictness: int = DEFAULT_STRICTNESS) -> dict:
    data = data or load_results()
    cells = data.get("cells", {})
    rows = []
    for name, cell in sorted(cells.items(), key=lambda kv: kv[1]["params_b"]):
        s = cell["scores"][str(strictness)]
        rows.append({"model": name, "params_b": cell["params_b"], **s,
                     "seconds_per_generation": cell.get("seconds_per_generation")})
    return {
        "strictness": strictness,
        "strictness_grid": list(STRICTNESS_GRID),
        "rows": rows,
        "not_run": data.get("gated", GATED_MODELS),
        "blocked": data.get("blocked", {}),
        "finding": _finding(rows, data.get("blocked", {})),
    }


def _finding(rows: list, blocked: dict | None = None) -> list:
    """What the grid says, derived from the rows.

    Written as a function of the data because a finding typed in beside the numbers survives the
    numbers changing - which is exactly what happened in F1. It also has to answer the question
    F1 and F2 both had to answer: is the spread across cells bigger than the interval this
    sample supports? A capability effect smaller than the noise is not a capability effect.
    """
    blocked = blocked or {}
    if len(rows) < 2:
        return ["Too few cells to say anything about how the reader bounds actionability. "
                "Run more of the ladder."]

    lifts = [r["lift"] for r in rows if r["lift"] is not None]
    spread = round(max(lifts) - min(lifts), 4) if lifts else 0.0
    n = rows[0]["n"]
    # The half-width on a rate near the middle, which is the widest it gets - the honest bar to
    # clear before calling a difference between two cells real.
    lo, hi = MC.wilson(round(0.5 * n), n)
    half = round((hi - lo) / 2, 4) if lo is not None else None
    monotonic = all(a["lift"] <= b["lift"] + 1e-9 for a, b in zip(rows, rows[1:]))
    top = max(r["params_b"] for r in rows)

    out = [
        f"Memory held fixed, reader varied from {rows[0]['params_b']}B to {top}B. The "
        f"prevention-adoption lift by size: " +
        ", ".join(f"{r['params_b']}B {r['lift']}" for r in rows) + ".",
    ]
    if half is not None and spread <= half:
        out.append(
            f"NOT DISTINGUISHED: the spread across the ladder ({spread}) is no larger than the "
            f"half-width this sample supports ({half} on n={n}). On these episodes the reader "
            "is not shown to bound actionability - which is a statement about the evidence, "
            "not a finding that model size does not matter.")
    else:
        out.append(
            f"The spread across the ladder ({spread}) exceeds the {half} half-width this "
            f"sample supports, so the reader does appear to bound actionability here: the same "
            "surfaced sentence produces different behaviour depending on who reads it, and a "
            "prevention rate published without naming the reader is not a property of the "
            "memory system.")
    if not monotonic:
        out.append(
            "The lift is NOT monotonic in model size - it dips in the middle of the ladder. "
            "With this sample size that is most economically read as noise rather than as a "
            "real non-monotonicity, and it is the strongest single reason not to quote any one "
            "cell as 'the' number.")
    floor = [r for r in rows if (r["lift"] or 0) <= 0]
    if floor:
        out.append(
            "Cells where memory did not help at all: " +
            ", ".join(f"{r['model']} ({r['params_b']}B)" for r in floor) +
            ". A model that cannot act on the sentence is a model for which this system's "
            "central claim is simply false, and that is a deployment constraint, not a "
            "footnote.")
    if blocked:
        out.append(
            "BLOCKED cells: " + ", ".join(sorted(blocked)) +
            ". These are not gated on money or permission - the weights are incomplete on this "
            "machine. The ladder therefore stops at " + f"{top}B, short of where it was meant "
            "to reach, and every conclusion above is bounded by that.")
    out.append(
        "The two frontier cells are NOT run (gate G8). Until they are, no claim may describe "
        f"how this scales to a frontier reader - this ladder ends at {top}B.")
    out.append(
        "The grader is deterministic word-overlap against the prevention, identical in every "
        "cell. It over-credits parroting and under-credits paraphrase, equally everywhere, so "
        "it supports the COMPARISON across cells and not the absolute rate in any of them.")
    return out


def render(g: dict) -> str:
    L = ["", "Model-capability grid (GOAL F3) - memory fixed, reader varied", ""]
    L.append(f"  grader strictness: {g['strictness']} of the prevention's content words "
             f"(swept over {g['strictness_grid']})")
    L.append("")
    L.append(f"  {'model':16} {'params':>7} {'no mem':>8} {'w/ mem':>8} {'lift':>8} {'s/gen':>7}")
    for r in g["rows"]:
        L.append(f"  {r['model']:16} {str(r['params_b']) + 'B':>7} {r['rate_without']!s:>8} "
                 f"{r['rate_with']!s:>8} {r['lift']!s:>8} {r['seconds_per_generation']!s:>7}")
    if not g["rows"]:
        L.append("  (no cells run yet)")
    L.append("")
    for name, spec in g.get("blocked", {}).items():
        L.append(f"  BLOCKED  {name}: {spec['why']}")
        L.append(f"           -> {spec['what_the_owner_must_do']}")
    for name, spec in g["not_run"].items():
        L.append(f"  NOT RUN  {name}: {spec['why']}")
        L.append(f"           -> {spec['what_the_owner_must_do']}")
    L.append("")
    for line in g["finding"]:
        L.append("  * " + line.replace(". ", ".\n    "))
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="run one cell")
    ap.add_argument("--all", action="store_true", help="run every local cell, smallest first")
    ap.add_argument("--report", action="store_true", help="print the grid from saved cells")
    ap.add_argument("--limit", type=int, help="episodes per cell (for a smoke run)")
    ap.add_argument("--strictness", type=int, default=DEFAULT_STRICTNESS)
    args = ap.parse_args(argv)

    if args.model in GATED_MODELS:
        spec = GATED_MODELS[args.model]
        print(f"GATED: {spec['why']}. {spec['what_the_owner_must_do']}. Nothing was measured.",
              file=sys.stderr)
        return 2

    targets = list(LOCAL_MODELS) if args.all else ([args.model] if args.model else [])
    for name in targets:
        if name not in LOCAL_MODELS:
            print(f"unknown model {name!r}; known: {sorted(LOCAL_MODELS)}", file=sys.stderr)
            return 1
        try:
            cell = run_local(name, limit=args.limit)
        except IncompleteCheckpoint as exc:
            print(f"  BLOCKED {exc}", file=sys.stderr)
            save_blocked(name, str(exc), sorted(exc.absent))
            _free_vram()
            continue
        save_cell(cell)
        _free_vram()
        s = cell["scores"][str(args.strictness)]
        print(f"  {name}: lift {s['lift']} ({s['rate_without']} -> {s['rate_with']})", flush=True)

    if args.report or targets:
        print(render(grid(strictness=args.strictness)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
