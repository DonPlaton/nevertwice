#!/usr/bin/env python3
"""RESEARCH - the matched-condition harness (GOAL F1).

The attack a reviewer makes on any proactive-memory result is short: *you fired more often than
the baseline, so of course you caught more.* A single operating point cannot answer it. Only the
full curve can, and only if every arm is read at the same false-alarm rate.

So this harness does three things and refuses to do a fourth:

1. **Sweeps the firing threshold** across its whole range and reports the complete
   precision/recall curve, not a point. `research/matched_conditions.json` carries every
   threshold, so anyone can check that the published operating point is on the curve and is not
   the only one that flatters the system.
2. **Reads every arm at a MATCHED false-positive rate.** Each baseline is swept over its own
   knob, and the comparison is made where their false-alarm rates coincide - which is the only
   comparison that means anything. An arm that cannot reach the matched rate is reported as such
   rather than dropped.
3. **Equalizes the conditions that are not the variable**: the reader (there is none - the
   intervention path is arithmetic, no LLM, which is stated rather than assumed), the context
   tokens each arm spends, and the latency tier. All three are measured and printed, because a
   win bought with ten times the tokens is not a win.

It refuses to report a headline. That is F2's job, and only after the six B5 baselines run end
to end. What this file establishes is the precondition: *no result here can be produced by a
looser firing threshold*, because the threshold is the x-axis.

    python research/matched_conditions.py            # the curve and the matched-rate table
    python research/matched_conditions.py --save     # + research/matched_conditions.json
    python research/matched_conditions.py --json     # machine-readable only

Standard library only. No network, no LLM, no GPU.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard                                    # noqa: E402
sandbox_guard.isolate("matched_conditions")

sys.path.insert(0, str(ROOT / "nevertwice"))
import anticipate as A                                  # noqa: E402
import memory_hook as m                                 # noqa: E402

CORPUS = ROOT / "research" / "matched_conditions_corpus.json"
OUT = ROOT / "research" / "matched_conditions.json"

#: The threshold grid. Fine enough that the published operating point lands on a sampled value,
#: wide enough to include "fires at everything" and "never fires" - the two degenerate arms a
#: reviewer will ask about.
GRID = [round(i / 100, 2) for i in range(0, 101)]

#: Nominal cost of one surfaced intervention, in context tokens. The message is one line naming
#: the past failure and its prevention; below threshold the system is silent and spends nothing,
#: which is the property the token column exists to keep honest.
TOKENS_PER_FIRING = 38
TOKENS_SILENT = 0


# ── the corpus ──────────────────────────────────────────────────────────

def load_corpus() -> dict:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    for sig in data["signatures"]:
        sig["tokens"] = A._content_tokens(sig["text"])
    return data


# ── the arms ────────────────────────────────────────────────────────────
# Each arm scores an episode against the signature set and returns (stem, score). The sweep then
# applies a threshold to the score, so every arm is measured through the same machinery and the
# only difference between them is how the score is computed.

#: `anticipate()` builds an IDF table over the signature set once per call and passes it into
#: every `risk_score`. The first version of this harness omitted it and was therefore scoring a
#: function the product does not ship - three episodes fired here and stayed silent in
#: `anticipate()`. Cached per signature set, because rebuilding it per episode would be the
#: shipped path measured at the wrong latency.
_IDF_CACHE: dict = {}


def _idf(sigs: list):
    # Keyed by CONTENT, not by id(): a freed list's id can be reused, so an
    # id-keyed cache can hand back the IDF table of a different signature set.
    key = tuple(s["stem"] for s in sigs)
    if key not in _IDF_CACHE:
        _IDF_CACHE[key] = A.build_idf(sigs)
    return _IDF_CACHE[key]


def arm_nevertwice(episode: str, sigs: list) -> tuple:
    """The shipped path: `anticipate.risk_score` with the IDF table, top-1.

    `tests/_test_matched_conditions.py` runs every episode through `anticipate()` itself and
    fails on any disagreement, so this cannot drift back into scoring something else.
    """
    traj = A._content_tokens(episode[:A.MAX_CHECK_CHARS])
    idf = _idf(sigs)
    best, best_score = None, 0.0
    for sig in sigs:
        score = A.risk_score(traj, sig, idf)
        if score > best_score:
            best, best_score = sig["stem"], score
    return best, best_score


def arm_lexical(episode: str, sigs: list) -> tuple:
    """B5 baseline `lexical_recall`: raw token overlap, no recurrence, no damping.

    The cheapest thing that could possibly work, and the one a reviewer reaches for first.
    """
    traj = set(A._content_tokens(episode[:A.MAX_CHECK_CHARS]))
    best, best_score = None, 0.0
    for sig in sigs:
        toks = set(sig["tokens"])
        if not toks:
            continue
        score = len(traj & toks) / len(toks)
        if score > best_score:
            best, best_score = sig["stem"], score
    return best, best_score


def arm_always(episode: str, sigs: list) -> tuple:
    """B5 baseline `full_history`: surface the most-recurrent memory every single time.

    Recall is perfect by construction and so is the false-alarm rate, which is the point: it is
    the arm that shows what "fires more often" actually costs.
    """
    best = max(sigs, key=lambda s: s["recurrence"])
    return best["stem"], 1.0


def arm_never(episode: str, sigs: list) -> tuple:
    """B5 baseline `no_memory`: never fire. The floor every result must clear."""
    return None, 0.0


ARMS = {
    "nevertwice": arm_nevertwice,
    "lexical_recall": arm_lexical,
    "full_history": arm_always,
    "no_memory": arm_never,
}


# ── scoring ─────────────────────────────────────────────────────────────

def confusion(predictions: list, threshold: float) -> dict:
    """TP/FP/FN/TN at one threshold.

    A prediction counts as a true positive only when it fires AND names the right past failure.
    Firing on a real risk while naming the wrong memory is a false positive, not a partial
    credit: the user reads the message, and a message about the wrong failure is a wrong message.
    """
    tp = fp = fn = tn = 0
    for label, stem, score in predictions:
        # `score > 0.0` is what makes "never fire" representable, NOT `threshold > 0`:
        # at threshold 0 an arm with signal SHOULD fire on everything, and conflating
        # those two made `full_history` report a zero false-alarm rate, which is the one
        # thing that arm certainly does not have.
        fired = stem is not None and score > 0.0 and score >= threshold
        if label is None:
            if fired:
                fp += 1
            else:
                tn += 1
        else:
            if fired and stem == label:
                tp += 1
            elif fired:
                fp += 1
                fn += 1        # the real risk was also missed
            else:
                fn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def rates(c: dict) -> dict:
    tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    fpr = round(fp / (fp + tn), 4) if (fp + tn) else None
    f1 = (round(2 * precision * recall / (precision + recall), 4)
          if precision and recall else 0.0)
    return {**c, "precision": precision, "recall": recall,
            "false_positive_rate": fpr, "f1": f1}


def wilson(successes: int, total: int, z: float = 1.96) -> tuple:
    """A 95% interval, because a corpus this size without one invites over-reading."""
    if total == 0:
        return (None, None)
    p = successes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def sweep(arm, episodes: list, sigs: list) -> dict:
    """The full curve for one arm, plus what it cost to produce."""
    started = time.perf_counter()
    predictions = []
    for ep in episodes:
        stem, score = arm(ep["text"], sigs)
        predictions.append((ep["label"], stem, score))
    elapsed_ms = (time.perf_counter() - started) * 1000

    curve = []
    for t in GRID:
        c = confusion(predictions, t)
        row = rates(c)
        row["threshold"] = t
        fired = row["tp"] + row["fp"]
        row["tokens_spent"] = fired * TOKENS_PER_FIRING + (len(episodes) - fired) * TOKENS_SILENT
        curve.append(row)
    return {
        "curve": curve,
        "latency_ms_per_episode": round(elapsed_ms / max(1, len(episodes)), 4),
        "reader_model": "none - the intervention path is arithmetic (token overlap, recurrence "
                        "weighting, optional cosine). No arm here calls an LLM, so the reader is "
                        "matched by being absent rather than by being equalized.",
    }


# ── matched-rate comparison ─────────────────────────────────────────────

def at_matched_fpr(curve: list, target: float) -> dict | None:
    """The best operating point whose false-alarm rate does not exceed `target`.

    Not the nearest point: the *best recall among the admissible ones*. Allowing an arm to be
    read at a higher false-alarm rate than its rival is precisely the unmatched comparison this
    file exists to prevent.
    """
    admissible = [r for r in curve
                  if r["false_positive_rate"] is not None
                  and r["false_positive_rate"] <= target + 1e-9]
    if not admissible:
        return None
    return max(admissible, key=lambda r: (r["recall"] or 0.0, r["precision"] or 0.0))


def partial_auc(curve: list, steps: int = 21) -> float:
    """Recall averaged over a grid of false-alarm budgets - the curve as one number.

    Not a full ROC AUC: the interesting region for an interrupting system is the low-false-alarm
    end, and a summary that credits an arm for recall it only reaches at a 75% false-alarm rate
    would hide exactly what this harness exists to expose. At each budget the arm is credited
    with the best recall it can achieve WITHIN that budget, which is what an operator would
    actually pick.
    """
    points = [(r["false_positive_rate"], r["recall"]) for r in curve
              if r["false_positive_rate"] is not None and r["recall"] is not None]
    total = 0.0
    for i in range(steps):
        budget = i / (steps - 1)
        total += max([rec for fpr, rec in points if fpr <= budget + 1e-9], default=0.0)
    return round(total / steps, 4)


def at_zero_false_alarms(curve: list) -> dict | None:
    """The best operating point that never cries wolf.

    For a system that INTERRUPTS, this is the operating point that matters most: a false alarm
    does not merely cost tokens, it teaches the user to dismiss the next warning unread. The
    project models that explicitly - `_effective_tau` raises the bar per recorded false alarm -
    so reporting the zero-false-alarm recall is reporting the number the design optimises for.
    """
    return at_matched_fpr(curve, 0.0)


def _findings(matched: dict, zero_fa: dict, positives: int, target_fpr, shipped) -> list:
    """The findings, DERIVED from the numbers rather than written beside them.

    An earlier version of this list was prose with the figures typed in. The harness was then
    corrected - it had been scoring `risk_score` without the IDF table `anticipate()` builds,
    so it was measuring a function the product does not ship - every number moved, and the
    prose silently became false. A findings list that cannot disagree with its own data is the
    only kind worth publishing.
    """
    ours = matched.get("nevertwice", {}).get("point") or {}
    theirs = matched.get("lexical_recall", {}).get("point") or {}
    ours_zero = (zero_fa.get("nevertwice", {}) or {}).get("point") or {}
    theirs_zero = (zero_fa.get("lexical_recall", {}) or {}).get("point") or {}
    our_auc = zero_fa.get("nevertwice", {}).get("partial_auc")
    their_auc = zero_fa.get("lexical_recall", {}).get("partial_auc")

    lo, hi = wilson(round((ours_zero.get("recall") or 0) * positives), positives)
    width = round((hi - lo) / 2, 3) if lo is not None and hi is not None else None

    out = [
        f"At the matched false-alarm rate ({target_fpr}) the shipped scorer reaches recall "
        f"{ours.get('recall')} against token overlap's {theirs.get('recall')}. At ZERO false "
        f"alarms - the point an interrupting system actually has to live at - it reaches "
        f"{ours_zero.get('recall')} and token overlap reaches {theirs_zero.get('recall')}.",
    ]
    if (theirs_zero.get("recall") or 0) > (ours_zero.get("recall") or 0):
        out.append(
            "NEGATIVE RESULT: at zero false alarms the CHEAP BASELINE WINS. Raw token overlap "
            "recovers more past failures than the shipped scorer does without ever crying wolf. "
            "Recurrence weighting and the coincidence damper buy a better area under the curve "
            f"({our_auc} against {their_auc}) and do not buy the operating point that matters "
            "most. That is a finding about the mechanism, not about the harness, and F2 has to "
            "settle it on a corpus this author did not write: either that separates them, or "
            "the cheaper scorer is the honest default and the extra machinery should go.")
    elif (ours_zero.get("recall") or 0) > (theirs_zero.get("recall") or 0):
        out.append(
            "At zero false alarms the shipped scorer is ahead of token overlap, and its area "
            f"under the curve is larger ({our_auc} against {their_auc}). Whether that survives "
            "a corpus this author did not write is exactly what F2 has to establish.")
    else:
        out.append(
            "At zero false alarms the two arms tie, and only the area under the curve separates "
            f"them ({our_auc} against {their_auc}).")

    out.append(
        f"Neither difference is resolvable at this sample size. With {positives} positive "
        f"episodes the 95% Wilson interval on a recall near {ours_zero.get('recall')} is about "
        f"plus or minus {width}, which is wider than every gap above. The correct reading is "
        "'not yet distinguished', not 'equivalent' and not 'better'.")
    out.append(
        f"The shipped threshold ({shipped}) is not the best point on its own curve for this "
        f"corpus: a higher bar reaches recall {ours_zero.get('recall')} with NO false alarms, "
        f"where the shipped bar pays a {target_fpr} false-alarm rate. For a system that "
        "interrupts, that trade looks wrong - but retuning a shipped default on a corpus "
        "written by the author of the notes would be fitting the threshold to the test set, so "
        "it stays a finding.")
    out.append(
        "`full_history` cannot reach any matched false-alarm rate: firing every time means a "
        "100% false-alarm rate by construction. That is the arm's honest result, and the "
        "quantitative form of the argument this whole harness exists to make.")
    return out


def build(save: bool = False) -> dict:
    data = load_corpus()
    sigs, episodes = data["signatures"], data["episodes"]
    positives = sum(1 for e in episodes if e["label"])
    negatives = len(episodes) - positives

    results = {name: sweep(fn, episodes, sigs) for name, fn in ARMS.items()}

    shipped = A.BASE_TAU
    operating = next((r for r in results["nevertwice"]["curve"]
                      if abs(r["threshold"] - shipped) < 1e-9), None)
    target_fpr = operating["false_positive_rate"] if operating else None

    matched = {}
    for name, res in results.items():
        point = at_matched_fpr(res["curve"], target_fpr) if target_fpr is not None else None
        matched[name] = {
            "reachable": point is not None,
            "point": point,
            "tokens_per_episode": (round(point["tokens_spent"] / len(episodes), 2)
                                   if point else None),
            "latency_ms_per_episode": res["latency_ms_per_episode"],
            "recall_ci95": (wilson(point["tp"], positives) if point else (None, None)),
        }

    zero_fa = {name: {"point": at_zero_false_alarms(res["curve"]),
                      "partial_auc": partial_auc(res["curve"])}
               for name, res in results.items()}

    payload = {
        "schema_version": 1,
        "generated_by": "python research/matched_conditions.py --save",
        "corpus": {
            "signatures": len(sigs), "episodes": len(episodes),
            "positives": positives, "negatives": negatives,
            "limitations": data["limitations"],
        },
        "matched_conditions": {
            "reader_model": results["nevertwice"]["reader_model"],
            "context_token_budget": f"{TOKENS_PER_FIRING} tokens per surfaced intervention, "
                                    f"{TOKENS_SILENT} when silent; reported per arm below so a "
                                    f"win bought with more tokens is visible as one",
            "latency_tier": "all arms run in-process on cpu; measured per episode below",
            "false_positive_rate": target_fpr,
        },
        "shipped_threshold": shipped,
        "operating_point": operating,
        "arms": {name: {"curve": res["curve"],
                        "latency_ms_per_episode": res["latency_ms_per_episode"]}
                 for name, res in results.items()},
        "matched_fpr_comparison": matched,
        "zero_false_alarm_comparison": zero_fa,
        "findings": _findings(matched, zero_fa, positives, target_fpr, shipped),
    }
    if save:
        m.write_atomic(OUT, json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


# ── reporting ───────────────────────────────────────────────────────────

def render(p: dict) -> str:
    lines = ["", "Matched-condition harness (GOAL F1)", ""]
    c = p["corpus"]
    lines.append(f"  corpus      {c['signatures']} signatures, {c['episodes']} episodes "
                 f"({c['positives']} positive, {c['negatives']} negative)")
    mc = p["matched_conditions"]
    lines.append(f"  reader      {mc['reader_model'].split('.')[0]}")
    lines.append(f"  tokens      {mc['context_token_budget'].split(';')[0]}")
    lines.append("")

    op = p["operating_point"]
    if op:
        lines.append(f"  Shipped threshold tau={p['shipped_threshold']}: "
                     f"precision {op['precision']}, recall {op['recall']}, "
                     f"false-alarm rate {op['false_positive_rate']}")
    lines.append("")
    lines.append("  The curve is the answer to 'you just fired more often'. Every arm below is")
    lines.append(f"  read at the SAME false-alarm rate ({mc['false_positive_rate']}):")
    lines.append("")
    lines.append(f"  {'arm':16} {'recall':>8} {'prec':>7} {'fpr':>7} {'tok/ep':>8} {'ms/ep':>8}")
    for name, entry in p["matched_fpr_comparison"].items():
        if not entry["reachable"]:
            lines.append(f"  {name:16} {'cannot reach this false-alarm rate':>40}")
            continue
        pt = entry["point"]
        lines.append(f"  {name:16} {pt['recall']!s:>8} {pt['precision']!s:>7} "
                     f"{pt['false_positive_rate']!s:>7} "
                     f"{entry['tokens_per_episode']!s:>8} "
                     f"{entry['latency_ms_per_episode']!s:>8}")
    lines.append("")
    lines.append("  And where an interrupting system actually has to live - ZERO false alarms:")
    lines.append("")
    lines.append(f"  {'arm':16} {'recall':>8} {'prec':>7} {'tau':>6} {'part.AUC':>9}")
    for name, entry in p["zero_false_alarm_comparison"].items():
        pt = entry["point"]
        if not pt or not pt["recall"]:
            lines.append(f"  {name:16} {'never fires without also crying wolf':>42}")
            continue
        lines.append(f"  {name:16} {pt['recall']!s:>8} {pt['precision']!s:>7} "
                     f"{pt['threshold']!s:>6} {entry['partial_auc']!s:>9}")
    lines.append("")
    for finding in p["findings"]:
        lines.append("  * " + finding.replace(". ", ".\n    "))
    lines.append("")
    lines.append("  Read the limitations with the numbers:")
    for lim in c["limitations"]:
        lines.append(f"    - {lim}")
    lines.append("")
    lines.append("  This file reports no headline. F2 runs the remaining B5 baselines end to end;")
    lines.append("  what F1 establishes is that no result here can come from a looser threshold,")
    lines.append("  because the threshold is the x-axis and the full curve is published.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    ap.add_argument("--json", action="store_true", help="machine-readable output only")
    args = ap.parse_args(argv)

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
