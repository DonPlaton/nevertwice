#!/usr/bin/env python3
"""RESEARCH - the cheap-baseline suite (GOAL F2).

`research/BASELINES.md` is a policy: a headline is publishable only if it beats no-memory,
full-history injection, lexical recall, a curated `AGENTS.md`, an LLM session summary, and the
relevant linter or test. F1 built the first three. This file builds the last three and runs all
six through the same matched-condition machinery, so the policy stops being a promise.

The three added here are the ones that hurt:

* **`curated_agents_md`** - one hand-written instructions file, always present. This is the
  honest competitor for most of what agent memory claims. If a person writing a dozen lines
  gets the same prevention, the machinery is not earning its place. Scored at the token cost of
  being present on *every* episode, because that is what always-injected means.
* **`linter_or_test`** - the existing static check that already catches the same class. A guard
  that duplicates a linter is not memory, it is a worse linter. Scored generously **in the
  baseline's favour**: where it was arguable whether ruff, bandit, a secret scanner or CodeQL
  would fire, the answer recorded is yes.
* **`llm_session_summary`** - built, run gated. No local model server is reachable here and a
  frontier call is G8, so what runs is a deterministic extractive summariser, labelled as such
  and never reported as an LLM result. The arm, its scoring and its token accounting are in
  place; only the generator changes.

    python research/cheap_baselines.py           # the six-arm table
    python research/cheap_baselines.py --save    # + research/cheap_baselines.json

Standard library only. No network, no GPU.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard                                    # noqa: E402
sandbox_guard.isolate("cheap_baselines")

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import anticipate as A                                  # noqa: E402
import memory_hook as m                                 # noqa: E402
import matched_conditions as MC                         # noqa: E402

RULES = ROOT / "research" / "cheap_baselines_rules.json"
OUT = ROOT / "research" / "cheap_baselines.json"

#: Tokens an always-present arm spends on every episode, whether it helps or not. The curated
#: file is charged its own length; that is the whole point of the column.
_RULES = json.loads(RULES.read_text(encoding="utf-8"))
AGENTS_MD_TOKENS = sum(len(r["text"].split()) for r in _RULES["curated_agents_md"]["rules"])


# ── the three added arms ────────────────────────────────────────────────

def _rule_index() -> list:
    out = []
    for rule in _RULES["curated_agents_md"]["rules"]:
        out.append({"id": rule["id"], "covers": set(rule["covers"]),
                    "tokens": set(A._content_tokens(rule["text"]))})
    return out


_RULE_INDEX = _rule_index()


def arm_curated_agents_md(episode: str, sigs: list) -> tuple:
    """A hand-written rules file, always in context, matched against the episode.

    The rule "fires" when the episode's wording overlaps it, exactly as a human reading a
    twelve-line file would notice a rule that applies. Returning the covered stem lets it be
    scored on the same ground truth as every other arm rather than on a friendlier one.
    """
    toks = set(A._content_tokens(episode[:A.MAX_CHECK_CHARS]))
    best, best_score = None, 0.0
    for rule in _RULE_INDEX:
        if not rule["tokens"]:
            continue
        score = len(toks & rule["tokens"]) / len(rule["tokens"])
        if score > best_score:
            # A rule can cover several failures; credit the one it names first, which is the
            # rule's own primary target. Crediting whichever happens to match the label would
            # score this arm on hindsight.
            best, best_score = sorted(rule["covers"])[0], score
    return best, best_score


_CAUGHT = {c["stem"]: c["caught"] for c in _RULES["linter_or_test"]["coverage"]}


#: Episode text -> ground-truth label, so the linter arm can be modelled as an ORACLE.
_LABELS = {e["text"]: e["label"] for e in MC.load_corpus()["episodes"]}


def arm_linter_or_test(episode: str, sigs: list) -> tuple:
    """The static check that already covers this class of mistake, as an ORACLE UPPER BOUND.

    A linter does not score a trajectory. It reads code, and on ordinary work it finds nothing -
    so this arm never cries wolf on a negative, and on a positive it fires exactly when the
    failure class is one an existing tool covers. That makes it a step function driven by
    ground truth, which is deliberate: it is the *ceiling* of what static analysis could achieve
    on this corpus, not an estimate of a particular tool's behaviour. Scoring it any weaker
    would be building a strawman and calling it a baseline.

    The first version scored it through the lexical arm's nearest match, which made it fire on
    benign episodes whose closest signature happened to be linter-covered - a linter that flags
    ordinary work is not a linter anybody runs, and the false alarms it invented were enough to
    stop the arm reaching a zero-false-alarm operating point at all.
    """
    label = _LABELS.get(episode)
    if label is None or not _CAUGHT.get(label):
        return None, 0.0
    return label, 1.0


def _extractive_summary(sig: dict, budget: int) -> set:
    """The stub summariser: the highest-density content tokens of a past failure.

    Not an LLM. Deterministic, offline, and a lower bound on what a model asked to summarise
    the same text would retain. It exists so the arm's scoring and token accounting are
    exercised rather than assumed - the model-backed run is the owner's to start.
    """
    tokens = sig["tokens"]
    seen, ordered = set(), []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            ordered.append(tok)
    return set(sorted(ordered, key=len, reverse=True)[:budget])


_SUMMARY_BUDGET = 12
_SUMMARIES: dict = {}


def arm_session_summary(episode: str, sigs: list) -> tuple:
    """Summarise each past session, inject the summaries, match against them.

    Reported as `session_summary_extractive`. It is never labelled an LLM result, because it
    is not one.
    """
    toks = set(A._content_tokens(episode[:A.MAX_CHECK_CHARS]))
    best, best_score = None, 0.0
    for sig in sigs:
        key = sig["stem"]
        if key not in _SUMMARIES:
            _SUMMARIES[key] = _extractive_summary(sig, _SUMMARY_BUDGET)
        summary = _SUMMARIES[key]
        if not summary:
            continue
        score = len(toks & summary) / len(summary)
        if score > best_score:
            best, best_score = key, score
    return best, best_score


ARMS = dict(MC.ARMS)
ARMS["curated_agents_md"] = arm_curated_agents_md
ARMS["linter_or_test"] = arm_linter_or_test
ARMS["session_summary_extractive"] = arm_session_summary

#: What each arm spends per episode REGARDLESS of whether it fires. A retrieval arm spends
#: nothing when silent; an always-injected file spends its whole length every time. Reporting
#: only the per-firing cost would flatter the always-on arms, which is the accounting error
#: this column exists to prevent.
STANDING_TOKENS = {
    "curated_agents_md": AGENTS_MD_TOKENS,
    "session_summary_extractive": _SUMMARY_BUDGET * 3,   # three summaries held in context
    "full_history": 0,      # already charged per firing, and it fires every time
    "linter_or_test": 0,    # runs outside the model's context entirely
}


def build(save: bool = False) -> dict:
    data = MC.load_corpus()
    sigs, episodes = data["signatures"], data["episodes"]
    positives = sum(1 for e in episodes if e["label"])

    results = {name: MC.sweep(fn, episodes, sigs) for name, fn in ARMS.items()}
    shipped = A.BASE_TAU
    operating = next((r for r in results["nevertwice"]["curve"]
                      if abs(r["threshold"] - shipped) < 1e-9), None)
    target = operating["false_positive_rate"] if operating else None

    table = {}
    for name, res in results.items():
        matched = MC.at_matched_fpr(res["curve"], target) if target is not None else None
        zero = MC.at_zero_false_alarms(res["curve"])
        standing = STANDING_TOKENS.get(name, 0)
        table[name] = {
            "reachable_at_matched_fpr": matched is not None,
            "matched": matched,
            "zero_false_alarms": zero,
            "partial_auc": MC.partial_auc(res["curve"]),
            "tokens_per_episode": round(
                (matched["tokens_spent"] / len(episodes) if matched else 0.0) + standing, 2),
            "standing_tokens_per_episode": standing,
            "latency_ms_per_episode": res["latency_ms_per_episode"],
            "recall_ci95": (MC.wilson(zero["tp"], positives) if zero else (None, None)),
        }

    verdict = _verdict(table, positives)
    payload = {
        "schema_version": 1,
        "generated_by": "python research/cheap_baselines.py --save",
        "policy": "research/BASELINES.md",
        "corpus": {"signatures": len(sigs), "episodes": len(episodes),
                   "positives": positives, "limitations": data["limitations"]},
        "matched_false_positive_rate": target,
        "arms": table,
        "arms_not_run": {
            "llm_session_summary": _RULES["llm_session_summary"]["status"],
        },
        "linter_coverage": _RULES["linter_or_test"]["coverage"],
        "verdict": verdict,
    }
    if save:
        m.write_atomic(OUT, json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


def _verdict(table: dict, positives: int) -> dict:
    """F2's exit criterion, decided by the numbers rather than beside them.

    "The central claim survives beyond every cheap alternative, OR is narrowed in writing." So
    the verdict is computed: which arms the memory arm beats at zero false alarms, which beat
    it, and - separately - whether any gap is larger than the interval the sample size supports.
    A win inside the noise is not a win, and saying so here is cheaper than saying it later.
    """
    ours = table["nevertwice"]["zero_false_alarms"] or {}
    our_recall = ours.get("recall") or 0.0
    lo, hi = MC.wilson(ours.get("tp", 0), positives)
    half_width = round((hi - lo) / 2, 4) if lo is not None else None

    beaten, beats_us, within_noise, cannot_operate = [], [], [], []
    for name, entry in table.items():
        if name == "nevertwice":
            continue
        theirs = entry["zero_false_alarms"]
        if theirs is None:
            # Not "scores lower" - CANNOT OPERATE at zero false alarms at all. `full_history`
            # is the type case: firing every time means there is no such operating point.
            # Folding that into "beaten" would overstate the win.
            cannot_operate.append(name)
            continue
        their_recall = theirs.get("recall") or 0.0
        gap = our_recall - their_recall
        if half_width is not None and abs(gap) <= half_width:
            within_noise.append(name)
        elif gap > 0:
            beaten.append(name)
        else:
            beats_us.append(name)

    survives = not beats_us and not within_noise
    return {
        "criterion": "the central claim survives beyond every cheap alternative, or is "
                     "narrowed in writing",
        "survives_every_cheap_alternative": survives,
        "beaten_decisively": sorted(beaten),
        "cannot_operate_at_zero_false_alarms": sorted(cannot_operate),
        "beats_the_memory_arm": sorted(beats_us),
        "indistinguishable_at_this_sample_size": sorted(within_noise),
        "recall_half_width_95": half_width,
        "narrowing": _narrowing(beaten, beats_us, within_noise, half_width),
    }


def _narrowing(beaten: list, beats_us: list, within_noise: list, half_width) -> list:
    """The written narrowing the exit criterion asks for when the claim does not survive."""
    out = []
    if not beats_us and not within_noise:
        out.append(
            "The claim survives every cheap alternative on this corpus. It is still one "
            "corpus, written by the author of the notes.")
        # Deliberately falls through to the standing caveats below rather than returning here.
        # The early return skipped the 'the LLM arm never ran' warning in exactly the branch
        # where someone would be tempted to publish 'beats every cheap alternative' - the one
        # case where the omission would do damage.
        return out + _standing_caveats()
    if beats_us:
        out.append(
            "NARROWED: at zero false alarms the memory arm does NOT beat " +
            ", ".join(sorted(beats_us)) + ". Any published claim must say so, and must not be "
            "stated as 'better than the alternatives'.")
    if within_noise:
        out.append(
            "NARROWED: the difference from " + ", ".join(sorted(within_noise)) +
            f" is smaller than the {half_width} half-width this sample supports. The honest "
            "wording is 'not distinguished from', not 'comparable to' and not 'better than'.")
    if beaten:
        out.append(
            "What DOES survive: the memory arm beats " + ", ".join(sorted(beaten)) +
            " by more than the sampling interval, so those alternatives are ruled out on this "
            "corpus.")
    return out + _standing_caveats()


def _standing_caveats() -> list:
    """The two warnings that belong on EVERY verdict, whatever the numbers said."""
    return [
        "The `linter_or_test` arm is the one to read hardest. Where it covers a failure class "
        "it catches every instance of that class and never cries wolf, because a linter fires "
        "on code rather than on intentions. For those classes a guard is a worse linter, and "
        "the memory system's claim has to be about the classes no linter covers.",
        "The LLM session-summary arm has NOT been run against a model. Until it is, no claim "
        "may say the system beats an LLM summary - only that it beats a deterministic "
        "extractive summariser at the same token budget.",
    ]


def render(p: dict) -> str:
    L = ["", "Cheap-baseline suite (GOAL F2) - the six B5 baselines, matched", ""]
    c = p["corpus"]
    L.append(f"  corpus  {c['signatures']} signatures, {c['episodes']} episodes "
             f"({c['positives']} positive)")
    L.append(f"  matched false-alarm rate: {p['matched_false_positive_rate']}")
    L.append("")
    L.append(f"  {'arm':30} {'rec@0fa':>8} {'rec@match':>10} {'AUC':>7} {'tok/ep':>8} {'ms/ep':>8}")
    for name, e in p["arms"].items():
        zero = e["zero_false_alarms"] or {}
        matched = e["matched"] or {}
        mark = " *" if name == "nevertwice" else ""
        L.append(f"  {name + mark:30} {zero.get('recall')!s:>8} "
                 f"{(matched.get('recall') if e['reachable_at_matched_fpr'] else 'n/a')!s:>10} "
                 f"{e['partial_auc']!s:>7} {e['tokens_per_episode']!s:>8} "
                 f"{e['latency_ms_per_episode']!s:>8}")
    L.append("")
    v = p["verdict"]
    L.append(f"  EXIT CRITERION - survives every cheap alternative: "
             f"{v['survives_every_cheap_alternative']}")
    if v["beaten_decisively"]:
        L.append(f"    ruled out : {', '.join(v['beaten_decisively'])}")
    if v["cannot_operate_at_zero_false_alarms"]:
        L.append(f"    no 0-fa   : {', '.join(v['cannot_operate_at_zero_false_alarms'])} "
                 f"(no zero-false-alarm operating point exists for these)")
    if v["beats_the_memory_arm"]:
        L.append(f"    beats us  : {', '.join(v['beats_the_memory_arm'])}")
    if v["indistinguishable_at_this_sample_size"]:
        L.append(f"    too close : {', '.join(v['indistinguishable_at_this_sample_size'])} "
                 f"(half-width {v['recall_half_width_95']})")
    L.append("")
    L.append("  The narrowing, in writing:")
    for line in v["narrowing"]:
        L.append("    * " + line.replace(". ", ".\n      "))
    L.append("")
    caught = [c["stem"] for c in p["linter_coverage"] if c["caught"]]
    L.append(f"  linter/test already covers {len(caught)} of {len(p['linter_coverage'])} "
             f"failure classes: {', '.join(caught)}")
    L.append("")
    L.append(f"  not run: {p['arms_not_run']['llm_session_summary'].split('.')[0]}.")
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summariser", choices=("extractive", "model"), default="extractive",
                    help="'model' is GATED: it needs a local model server or a billable call")
    args = ap.parse_args(argv)

    if args.summariser == "model":
        print("GATED (G8 / no local model server): the LLM session-summary arm is built and "
              "scored, but running it needs a model. Start a local server or approve a "
              "billable call, then re-run. Nothing was measured.", file=sys.stderr)
        return 2

    payload = build(save=args.save)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render(payload))
        if args.save:
            print(f"  saved -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
