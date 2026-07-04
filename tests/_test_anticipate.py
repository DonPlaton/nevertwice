#!/usr/bin/env python3
"""Self-check for anticipate.py (active memory, axis B). Verifies the 0-token silence below
threshold, firing on trajectory-similarity, top-1 discipline, recurrence weighting, and the
Popperian adaptive threshold (false alarms raise the bar until a predictor goes quiet).
Synthetic signatures + a temp state dir — no vault, no embedder, no network."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m          # noqa: E402
import anticipate as A           # noqa: E402


def _sigs():
    return [
        {"stem": "m-cpu", "project": "p", "recurrence": 4,
         "tokens": A._content_tokens("training silently fell back to cpu device halved throughput"),
         "title": "model-left-on-cpu", "prevention": "assert device is cuda"},
        {"stem": "m-nan", "project": "p", "recurrence": 1,
         "tokens": A._content_tokens("nan gradients exploded during mixed precision training"),
         "title": "nan-gradients", "prevention": "clip gradients and check scaler"},
    ]


def test_silent_below_threshold_costs_zero():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        hits = A.anticipate("writing a REST endpoint for the billing dashboard",
                            sigs=_sigs(), state={})
        assert hits == [], hits            # unrelated trajectory → SILENT, 0 tokens
    print("ok test_silent_below_threshold_costs_zero")


def test_fires_on_resemblance_top1():
    hits = A.anticipate("about to start the training loop on gpu but device config looks like cpu "
                        "and throughput seems halved", sigs=_sigs(), state={}, k=1)
    assert len(hits) == 1, hits            # top-1 only, never a dump
    assert hits[0]["stem"] == "m-cpu"
    assert hits[0]["risk"] >= A.BASE_TAU
    assert "past failure" in hits[0]["message"]
    print("ok test_fires_on_resemblance_top1")


def test_recurrence_weights_risk():
    lo = dict(_sigs()[1]); lo["recurrence"] = 1
    hi = dict(_sigs()[1]); hi["recurrence"] = 6
    traj = A._content_tokens("gradients exploded somewhere")   # partial overlap, unsaturated
    r_lo, r_hi = A.risk_score(traj, lo), A.risk_score(traj, hi)
    assert 0 < r_lo < r_hi, (r_lo, r_hi)                        # recurrence lifts an unsaturated score
    print("ok test_recurrence_weights_risk")


def test_adaptive_threshold_silences_crywolf():
    st = {}
    traj = "the device throughput here"                            # a MODERATE resemblance (risk < 0.9)
    hits = A.anticipate(traj, sigs=_sigs(), state=st, k=1)
    assert hits and hits[0]["risk"] < 0.9, hits                    # fires, but not an overwhelming signal
    r0, stem = hits[0]["risk"], hits[0]["stem"]
    # drive false alarms until the bar clears this risk, then it must go SILENT
    import math as _m
    n = _m.ceil((r0 - A.BASE_TAU) / A.FP_STEP) + 2
    for _ in range(n):
        A.feedback(stem, "false_alarm", state=st, persist=False)
    assert A.anticipate(traj, sigs=_sigs(), state=st, k=1) == []   # cry-wolf → now SILENT
    # a near-certain signal, by contrast, is NOT permanently suppressible (bar caps at 0.9)
    strong = ("training loop device cpu throughput halved silently fell back")
    assert A.anticipate(strong, sigs=_sigs(), state=st, k=1), "a strong signal must still break through"
    print("ok test_adaptive_threshold_silences_crywolf")


def test_feedback_persists_and_effective_tau():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        A.feedback("m-cpu", "false_alarm")
        A.feedback("m-cpu", "false_alarm")
        st = A.load_state()
        assert st["m-cpu"]["false_alarms"] == 2
        assert A._effective_tau(st, "m-cpu") > A.BASE_TAU
        # a 'helped' keeps it sensitive (doesn't raise the bar)
        A.feedback("m-cpu", "helped")
        assert A._effective_tau(A.load_state(), "m-cpu") == A._effective_tau(st, "m-cpu")
    print("ok test_feedback_persists_and_effective_tau")


if __name__ == "__main__":
    test_silent_below_threshold_costs_zero()
    test_fires_on_resemblance_top1()
    test_recurrence_weights_risk()
    test_adaptive_threshold_silences_crywolf()
    test_feedback_persists_and_effective_tau()
    print("\nall anticipate self-checks passed")
