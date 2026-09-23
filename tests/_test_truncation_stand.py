#!/usr/bin/env python3
"""K3: the truncation stand measures the shipped model alone, and refuses what it cannot do.

The Matryoshka checkpoint was deleted by decision, and the stand loaded every model in `MODELS`
before writing - so the two-model run died on the missing one after encoding the first, and the
half of the table the model card quotes (the SHIPPED model truncated naively, K3) could not be
re-measured at all, although it needs nothing that was deleted. The stand now runs a subset
(`--models`), computes only the comparisons whose models were run, checks every model on disk
before anything loads, and refuses a subset run onto the two-model artifact it would cut down.

No GPU and no weights: `evaluate` is replaced by a deterministic fake that returns ranks in the
real shape, so what is tested is the stand's selection and refusal logic, not the encoder.

Run:  python tests/_test_truncation_stand.py
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
import truncation_eval as te  # noqa: E402
from heldout_eval import _retrieval_block, _twin_block  # noqa: E402

MANIFEST = ROOT / "research" / "evidence_manifest.json"
SHIPPED_CLAIM = "embed.truncation.shipped.256d_recall_at_5_cost"
#: literals, not the stand's constants: the suite must run - and fail by name - against a stand
#: that predates them
SHIPPED, MATRYOSHKA = "nevertwice-embed", "nevertwice-embed-matryoshka"
COMPARISONS = ["K1_full_vs_shipped", "K2_truncated_vs_own_full", "K3_shipped_truncated_vs_own_full"]

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


CALLS: list[str] = []


def _fake_evaluate(label: str, path: str, data: dict) -> dict:
    """Ranks in the real shape; the truncated widths lose a few hits, differently per model."""
    CALLS.append(label)
    shift = 1 if label == SHIPPED else 2
    out: dict = {"label": label, "encode_seconds": 0.0, "widths": {}}
    for width in te.WIDTHS:
        lose = {1024: 0, 512: shift, 256: 2 * shift}[width]
        ranks = [1 if i >= lose else 7 for i in range(20)]
        units = [{"label": i % 2, "score": 0.9 if i % 2 else 0.1} for i in range(10)]
        out["widths"][str(width)] = {
            "twin": _twin_block(units), "index_bytes": 408 * width * te.BYTES_PER_DIM,
            "bytes_per_vector": width * te.BYTES_PER_DIM,
            "retrieval_title": _retrieval_block(ranks),
            "retrieval_situation": _retrieval_block(ranks),
            "ranks": {"retrieval_title": ranks, "retrieval_situation": ranks}}
    return out


@contextlib.contextmanager
def stand(present: tuple[str, ...]):
    """The stand with its models pointed at a temp dir where only *present* exist on disk, and
    its two-model artifact pointed at a temp file holding known bytes."""
    saved = (te.MODELS, te.ARTIFACT, te.evaluate)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        models = []
        for label, real in saved[0]:
            d = root / Path(real).name
            if label in present:
                d.mkdir()
            models.append((label, str(d)))
        art = root / "matryoshka_v1.json"
        art.write_text('{"sentinel": true}\n', encoding="utf-8")
        te.MODELS, te.ARTIFACT, te.evaluate = models, art, _fake_evaluate
        CALLS.clear()
        try:
            yield root, art
        finally:
            te.MODELS, te.ARTIFACT, te.evaluate = saved


def run(*argv: str) -> tuple[int, str]:
    """(exit code, stderr). A crash is returned as rc -1 with its exception, so the checks that
    expected a clean refusal or a clean run fail BY NAME instead of taking the suite down."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = te.main(list(argv))
    except SystemExit as exc:                     # argparse: an option this stand does not know
        return (exc.code if isinstance(exc.code, int) else 2), err.getvalue()
    except Exception as exc:                                        # noqa: BLE001
        return -1, f"{err.getvalue()}{type(exc).__name__}: {exc}"
    return rc, err.getvalue()


def test_the_default_is_still_every_model() -> None:
    print("\n- the default run is unchanged -")
    check("the committed artifact is still the stand's default --out",
          te.ARTIFACT.name == "matryoshka_v1.json", str(te.ARTIFACT))
    with stand(present=(SHIPPED, MATRYOSHKA)) as (root, art):
        rc, _ = run()
        data = json.loads(art.read_text(encoding="utf-8"))
        check("with both models on disk the default run writes", rc == 0, f"rc={rc}")
        check("every model was run, in MODELS order", CALLS == [SHIPPED, MATRYOSHKA],
              str(CALLS))
        check("K1, K2 and K3 are all computed",
              sorted(data.get("comparisons", {})) == COMPARISONS, str(data.get("comparisons", {}).keys()))
        check("a full run records nothing as skipped", "comparisons_skipped" not in data,
              str(data.get("comparisons_skipped")))


def test_a_missing_model_is_refused_before_anything_loads() -> None:
    print("\n- a missing model -")
    with stand(present=(SHIPPED,)) as (root, art):
        before = art.read_bytes()
        rc, err = run()
        check("the default run is refused when a model is not on disk", rc == 2, f"rc={rc}")
        check("nothing was loaded", CALLS == [], str(CALLS))
        check("the artifact was not touched", art.read_bytes() == before)
        check("the refusal names the missing model and its path",
              MATRYOSHKA in err and "matryoshka_v1_merged" in err, err.strip())
        check("the refusal names the run that still works",
              f"--models {SHIPPED}" in err, err.strip())


def test_an_unknown_label_is_refused() -> None:
    print("\n- an unknown label -")
    with stand(present=(SHIPPED, MATRYOSHKA)) as (root, art):
        rc, err = run("--models", "nevertwice-embed-hard", "--out", str(root / "x.json"))
        check("an unknown label is refused", rc == 2 and "unknown model label" in err
              and "nevertwice-embed-hard" in err,
              f"rc={rc} {err.strip()}")
        check("nothing was loaded", CALLS == [], str(CALLS))
        rc, err = run("--models", " , ", "--out", str(root / "x.json"))
        check("an empty label list is refused", rc == 2 and "unknown model label" in err,
              f"rc={rc} {err.strip()}")


def test_a_subset_is_refused_onto_the_two_model_artifact() -> None:
    print("\n- a subset onto the two-model artifact -")
    with stand(present=(SHIPPED, MATRYOSHKA)) as (root, art):
        before = art.read_bytes()
        rc, err = run("--models", SHIPPED)
        check("a one-model run onto the default --out is refused", rc == 2 and "refusing" in err,
              f"rc={rc} {err.strip()}")
        check("the two-model artifact keeps its bytes", art.read_bytes() == before)
        check("nothing was loaded", CALLS == [], str(CALLS))
        rc, err = run("--models", SHIPPED, "--out", str(art))
        check("the same run with --out spelled out is refused too",
              rc == 2 and "refusing" in err, f"rc={rc} {err.strip()}")


def test_the_shipped_model_alone_measures_k3() -> None:
    print("\n- the shipped model alone -")
    with stand(present=(SHIPPED,)) as (root, art):
        out = root / "truncation_v1.json"
        rc, err = run("--models", f"{SHIPPED},{SHIPPED}", "--out", str(out))
        check("the shipped model alone runs", rc == 0 and out.is_file(), f"rc={rc} {err.strip()}")
        check("a repeated label runs once", CALLS == [SHIPPED], str(CALLS))
        data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
        comps = data.get("comparisons", {})
        check("only K3 is computed", list(comps) == ["K3_shipped_truncated_vs_own_full"], str(list(comps)))
        k3 = comps.get("K3_shipped_truncated_vs_own_full", {})
        check("K3 carries both widths and both axes",
              all(set(k3.get(w, {})) == {"retrieval_title", "retrieval_situation"}
                  for w in ("512", "256")), str({w: list(v) for w, v in k3.items()}))
        skipped = data.get("comparisons_skipped", {})
        check("K1 and K2 are recorded as skipped, naming the model not run",
              sorted(skipped) == ["K1_full_vs_shipped", "K2_truncated_vs_own_full"]
              and all(MATRYOSHKA in v for v in skipped.values()), str(skipped))
        check("only the shipped model is in the artifact",
              list(data.get("models", {})) == [SHIPPED], str(list(data.get("models", {}))))
        check("the two-model artifact was not touched",
              art.read_text(encoding="utf-8") == '{"sentinel": true}\n')
        claim = next((c for c in json.loads(MANIFEST.read_text(encoding="utf-8"))["claims"]
                      if c["id"] == SHIPPED_CLAIM), None)
        node = data
        try:
            for part in (claim or {}).get("pointer", "missing").split("."):
                node = node[part]
            resolved = isinstance(node, (int, float))
        except (KeyError, TypeError):
            resolved = False
        check(f"{SHIPPED_CLAIM}'s pointer resolves in the one-model artifact", resolved,
              (claim or {}).get("pointer", "no such claim"))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                te.report(data)
        except Exception as exc:                                    # noqa: BLE001
            buf.write(f"report crashed: {type(exc).__name__}: {exc}")
        check("the report prints a one-model artifact and says what was skipped",
              "K3: v1 truncated naively" in buf.getvalue() and "skipped" in buf.getvalue(),
              buf.getvalue()[-300:])


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code."""
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_default_is_still_every_model,
               test_a_missing_model_is_refused_before_anything_loads,
               test_an_unknown_label_is_refused,
               test_a_subset_is_refused_onto_the_two_model_artifact,
               test_the_shipped_model_alone_measures_k3):
        fn()
    print(f"\ntruncation stand: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
