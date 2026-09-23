#!/usr/bin/env python3
"""The held-out baseline stand runs the models its artifact holds, and refuses a missing one first.

da1132e listed `nevertwice-embed-distil` in `MODELS` in the same commit that deleted its
checkpoint. From then on the recorded command of the three live baseline claims -
`python research/embed_universal/heldout_eval.py` - could not finish: it encoded three models,
died loading the fourth and wrote nothing (the 2026-09-23 campaign, step d1_heldout, rc=1,
`FileNotFoundError ... distil_v1_merged not found`). The committed `baseline_v1.json` holds
three models, so the default list is pinned to exactly those, and a local checkpoint that is not
on disk is refused before anything loads.

No GPU and no weights: the encoders are replaced by recorders, so what is tested is the stand's
model list and its refusal, not the numbers.

Run:  python tests/_test_heldout_stand.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "embed_universal"))
import heldout_eval as he  # noqa: E402

BASELINE = ROOT / "research" / "embed_universal" / "heldout" / "baseline_v1.json"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_the_default_list_is_the_artifacts() -> None:
    print("\n- the default model list -")
    committed = sorted(json.loads(BASELINE.read_text(encoding="utf-8"))["models"])
    listed = sorted(label for label, _kind, _path in he.MODELS)
    check("the stand's default models are exactly the committed baseline's", listed == committed,
          f"stand {listed} vs artifact {committed}")
    check("no deleted checkpoint is listed",
          not any("distil" in label or "hard" in label for label, _k, _p in he.MODELS),
          str([label for label, _k, _p in he.MODELS]))


def test_a_missing_local_model_is_refused_before_anything_loads() -> None:
    print("\n- a missing local checkpoint -")
    calls: list[str] = []
    saved = (he.MODELS, he.evaluate_bi, he.evaluate_cross)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "baseline.json"
        he.MODELS = [("stock bge-m3", "bi", "BAAI/bge-m3"),
                     ("gone", "bi", str(Path(tmp) / "gone_merged")),
                     ("bge-reranker-v2-m3", "cross", "BAAI/bge-reranker-v2-m3")]
        he.evaluate_bi = lambda label, *a, **k: calls.append(label) or {}
        he.evaluate_cross = lambda label, *a, **k: calls.append(label) or {}
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = he.main(["--out", str(out)])
        except Exception as exc:                                    # noqa: BLE001
            rc = f"crashed: {type(exc).__name__}: {exc}"
        finally:
            he.MODELS, he.evaluate_bi, he.evaluate_cross = saved
        check("a missing local checkpoint is refused", rc == 2, f"rc={rc}")
        check("nothing was loaded", calls == [], str(calls))
        check("nothing was written", not out.exists())
        check("the refusal names the model and its path",
              "gone" in err.getvalue() and "gone_merged" in err.getvalue(), err.getvalue().strip())
    probe = getattr(he, "_missing_local_models", None)
    check("a Hugging Face id is not mistaken for a missing path",
          probe is not None and probe([("stock bge-m3", "bi", "BAAI/bge-m3")]) == [],
          "no _missing_local_models" if probe is None else "")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_default_list_is_the_artifacts,
               test_a_missing_local_model_is_refused_before_anything_loads):
        fn()
    print(f"\nheldout stand: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
