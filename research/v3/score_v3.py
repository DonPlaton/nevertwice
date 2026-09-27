#!/usr/bin/env python3
"""PREREG-V3 TB4.12 (A6): the stands' official scores (rev1 §8.3, §8.4; the auditor's Q-51 O-c).

Q-51 O-c: the short official scorers are re-written here in pure Python, their golden values taken ONCE from the
pinned code (a differential probe - no third-party code runs in a campaign run); only LoCoMo's F1 is the pinned code
itself, run in the scorer venv (a separate step, research/v3/locomo_f1_child.py - not this module).

* MAB (utils/eval_other_utils.py @5380260): normalize_answer, f1_score with its special answers (yes / no / noanswer),
  drqa exact match, substring exact match, the max over ground truths, parse_output, and default_post_process (the
  metrics on the whole output and on the parsed answer, the larger kept) - FactConsolidation takes the default path.
  The ROUGE metrics are not reproduced (rouge_score is not a dependency; S6 scores exact match), and that is
  declared. On S6 the prediction is the reader's SHORT ANSWER line (Q-48-3).
* AMA (src/evaluate.py @ddfd319): avg_score = the mean of the per-question scores, accuracy = the share of 1.0.
"""
from __future__ import annotations

import re
import string
from collections import Counter
from typing import Any, Iterable, Sequence

MAB_SPECIAL = {"yes", "no", "noanswer"}


def mab_normalize(text: str) -> str:
    text = text.lower()
    text = "".join(ch for ch in text if ch not in string.punctuation)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def mab_f1(prediction: str, ground_truth: str) -> tuple[float, float, float]:
    p, g = mab_normalize(prediction), mab_normalize(ground_truth)
    zero = (0, 0, 0)
    if (p in MAB_SPECIAL or g in MAB_SPECIAL) and p != g:
        return zero
    pt, gt = p.split(), g.split()
    common = sum((Counter(pt) & Counter(gt)).values())
    if common == 0:
        return zero
    precision, recall = common / len(pt), common / len(gt)
    return (2 * precision * recall) / (precision + recall), precision, recall


def mab_em(prediction: str, ground_truth: str) -> bool:
    return mab_normalize(prediction) == mab_normalize(ground_truth)


def mab_subem(prediction: str, ground_truth: str) -> bool:
    return mab_normalize(ground_truth) in mab_normalize(prediction)


def _gts(ground_truths: Any) -> list[str]:
    if isinstance(ground_truths, str):
        return [ground_truths]
    if ground_truths and isinstance(ground_truths[0], list):
        return [gt for sub in ground_truths for gt in sub]
    return list(ground_truths)


def mab_max_over(metric, prediction: str, ground_truths: Any):
    return max(metric(prediction, gt) for gt in _gts(ground_truths))


def mab_parse_output(output_text: str, answer_prefix: str = "Answer:") -> str | None:
    for pattern in (re.compile(f"(?:{answer_prefix})(.*)(?:\n|$)", flags=re.IGNORECASE), re.compile(r"(?:^)(.*)(?:\n|$)")):
        match = pattern.search(output_text)
        if match:
            extracted = match[1].strip()
            return re.sub(f"^{re.escape(answer_prefix)}", "", extracted, flags=re.IGNORECASE).strip()
    return None


def mab_metrics(prediction: str, ground_truths: Any) -> dict:
    """calculate_metrics without ROUGE: exact_match, f1, substring_exact_match."""
    return {"exact_match": mab_max_over(mab_em, prediction, ground_truths),
            "f1": mab_max_over(lambda x, y: mab_f1(x, y)[0], prediction, ground_truths),
            "substring_exact_match": mab_max_over(mab_subem, prediction, ground_truths)}


def mab_default_post_process(prediction: str, ground_truths: Any) -> dict:
    """default_post_process: the whole output's metrics and the parsed answer's, the larger of each kept."""
    metrics = mab_metrics(prediction, ground_truths)
    parsed = mab_parse_output(prediction)
    if parsed is not None:
        pm = mab_metrics(parsed, ground_truths)
        metrics = {k: max(v, pm[k]) for k, v in metrics.items()}
    return metrics


def ama_summary(scores: Sequence[float]) -> dict:
    """AMA's evaluate.py: the mean score and the share of exact 1.0 scores."""
    scores = list(scores)
    return {"avg_score": sum(scores) / len(scores) if scores else 0, "accuracy":
            sum(1 for s in scores if s == 1.0) / len(scores) if scores else 0, "n": len(scores)}
