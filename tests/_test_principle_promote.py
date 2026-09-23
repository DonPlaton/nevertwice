#!/usr/bin/env python3
"""A5 (Q5): the sleep-time promoter that turns a `principle` recurring across >=2 DIFFERENT
projects into a single `universal` note - the pool `retrieve_cross_project(mode="universal")`
(A4) actually reads from. No model, no Ollama, no GPU: every test here drives
`nevertwice/principles.py` through a STUB embedder of fixed vectors, keyed by the exact
principle text, so cosine similarity is deterministic and hand-checkable.

    python tests/_test_principle_promote.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


#: A fixed-vector stub: SAME text -> SAME vector (cosine 1.0), DIFFERENT text -> orthogonal
#: (cosine 0.0). No model, no randomness - deterministic and hand-checkable clustering.
_VECS = {
    "cap a resource-bound parameter before scaling a workload.": [1.0, 0.0, 0.0],
    "measure before assuming a resource limit is the bottleneck.": [0.0, 1.0, 0.0],
    "always redact secrets before writing anything to disk.": [0.0, 0.0, 1.0],
}


def _stub_embed(text: str, kind=None, timeout=None, project=None):
    key = text.strip().lower()
    if key in _VECS:
        return _VECS[key]
    # a de-identification rescan or the medoid text may add/drop a trailing period or vary
    # whitespace; match tolerant of that so the stub does not silently starve the cluster.
    stripped = key.rstrip(".")
    for k, v in _VECS.items():
        if k.rstrip(".") == stripped:
            return v
    return None


def _write(project: str, title: str, principle: str, ntype: str = "pattern",
          entities=None) -> str:
    return m.write_typed_note(m.TYPE_FOLDER[ntype],
                              {"title": title, "description": f"{title} - a description",
                               "principle": principle, "entities": entities or []},
                              project, "2026-09-23", [], ntype)


def _import_fresh():
    """A fresh `principles` import per test - module-level dicts/caches must not leak between
    sandboxes, and re-importing after `make_sandbox` rebases `m.VAULT` is cheap and correct."""
    sys.modules.pop("principles", None)
    import principles as pr
    return pr


def test_two_different_projects_are_promoted() -> None:
    print("\n- the same principle from two DIFFERENT projects is promoted -")
    make_sandbox(m, "pp_two_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Cap a resource-bound parameter before scaling a workload."
    s_a = _write("project_a", "cap batch size", principle)
    s_b = _write("project_b", "cap worker pool", principle)
    check("both source notes were written", bool(s_a) and bool(s_b), f"{s_a} / {s_b}")

    summary = pr.promote(apply=True)
    check("one cluster formed", summary["clusters"] == 1, str(summary))
    check("one note was promoted", summary["promoted"] == 1, str(summary))
    result = summary["results"][0]
    check("both source stems are recorded", {s_a, s_b} <= set(result["members"]), str(result))

    universal_dir = m.VAULT / "Patterns"
    universal_notes = [p for p in universal_dir.glob("*.md")
                       if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("exactly one universal note exists on disk", len(universal_notes) == 1,
          str(universal_notes))
    fm = m._read_frontmatter_file(universal_notes[0])
    check("recurrence is the DISTINCT PROJECT count (2), not the member count",
          int(fm.get("recurrence") or 0) == 2, str(fm.get("recurrence")))
    check("sources carries both member stems", set(fm.get("sources") or ()) == {s_a, s_b},
          str(fm.get("sources")))
    text = universal_notes[0].read_text(encoding="utf-8")
    check("the note's own description is the (de-identified) principle sentence",
          "Cap a resource-bound parameter" in text, text)


def test_one_project_twice_is_not_promoted() -> None:
    print("\n- the same principle stated twice in ONE project is not promoted -")
    make_sandbox(m, "pp_one_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Cap a resource-bound parameter before scaling a workload."
    _write("project_a", "cap batch size", principle)
    _write("project_a", "cap thread pool", principle)

    summary = pr.promote(apply=True)
    check("no cluster spans >=2 projects", summary["clusters"] == 0, str(summary))
    check("nothing was promoted", summary["promoted"] == 0, str(summary))
    universal_dir = m.VAULT / "Patterns"
    universal_notes = [p for p in universal_dir.glob("*.md")
                       if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("no universal note exists on disk", not universal_notes, str(universal_notes))


def test_a_scanner_hit_at_promotion_time_is_not_promoted() -> None:
    """A1's write-time scan only forbids a note's OWN entities; A5's promotion-time re-scan
    forbids the WHOLE project's vocabulary - a principle can clear the first and still be
    caught by the second, which is exactly what this proves (not merely re-testing A1)."""
    print("\n- a principle that clears write-time but fails the project-vocabulary re-scan is dropped -")
    make_sandbox(m, "pp_scanhit_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle_a = "Measure before assuming a resource limit is the bottleneck."
    principle_b = "Measure before assuming a resource limit is the bottleneck."
    # A SEPARATE note in project_a carries "gpu-cluster-7" as one of ITS OWN entities - so it
    # is in project_a's VOCABULARY (A5's forbidden set) even though it never appears in the
    # candidate note's own entities (A1's write-time forbidden set).
    _write("project_a", "gpu cluster note", "", entities=["gpu-cluster-7"])  # no principle:
    # only seeds project_a's VOCABULARY, must not itself become a candidate
    s_a = _write("project_a", "measure resource limit",
                 "Measure before assuming gpu-cluster-7's resource limit is the bottleneck.")
    check("A1's write-time scan let this one through (it only forbids its own entities)",
          bool(s_a), s_a)
    fm_a = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s_a}.md")
    check("the identifier is really on disk (proves this is testing A5, not re-testing A1)",
          "gpu-cluster-7" in (fm_a.get("principle") or ""), fm_a.get("principle"))
    s_b = _write("project_b", "measure resource limit too", principle_b)
    check("the clean twin in project_b was written", bool(s_b), s_b)

    summary = pr.promote(apply=True)
    check("no cluster formed (the identifier-carrying candidate was dropped by the re-scan)",
          summary["clusters"] == 0, str(summary))
    check("no candidate survived from project_a's identifier-carrying note",
          summary["candidates"] == 1, str(summary))


def test_retire_on_drop_below_two_projects() -> None:
    print("\n- a universal note is retired once its cluster falls below two projects -")
    make_sandbox(m, "pp_retire_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Cap a resource-bound parameter before scaling a workload."
    s_a = _write("project_a", "cap batch size", principle)
    s_b = _write("project_b", "cap worker pool", principle)
    summary1 = pr.promote(apply=True)
    check("promoted on the first run", summary1["promoted"] == 1, str(summary1))

    # project_b's contributing note is retired (moved to Archive/) - the cluster now has one
    # project's worth of live sources.
    b_fp = m.VAULT / "Patterns" / f"{s_b}.md"
    arch = b_fp.parent / "Archive"
    arch.mkdir(exist_ok=True)
    b_fp.replace(arch / b_fp.name)
    check("project_b's source note is gone from the live folder", not b_fp.exists())

    summary2 = pr.promote(apply=True)
    check("the universal note was retired", summary2["retired"] == 1, str(summary2))
    universal_dir = m.VAULT / "Patterns"
    live_universal = [p for p in universal_dir.glob("*.md")
                      if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("no universal note remains LIVE", not live_universal, str(live_universal))
    archived_universal = [p for p in (universal_dir / "Archive").glob("*.md")
                          if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("it was moved to Archive/, not deleted", bool(archived_universal),
          str(list((universal_dir / "Archive").glob("*.md"))))


def test_a_cache_stamp_mismatch_is_refused() -> None:
    print("\n- a principles cache stamped with a different embedder is refused, not trusted -")
    make_sandbox(m, "pp_stamp_", offline=True)
    pr = _import_fresh()
    cache_path = pr._cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    cache_path.write_text(json.dumps({"model": "a-different-embedder:v0",
                                      "vectors": {"some-stem": [9.0, 9.0, 9.0]}}),
                          encoding="utf-8", newline="")
    loaded = pr._load_cache()
    check("a stamp mismatch refuses the whole cache (empty, not the stale vectors)",
          loaded == {}, str(loaded))

    # the matching stamp IS trusted
    cache_path.write_text(json.dumps({"model": m.embed_signature(),
                                      "vectors": {"some-stem": [1.0, 2.0, 3.0]}}),
                          encoding="utf-8", newline="")
    loaded2 = pr._load_cache()
    check("a matching stamp IS trusted", loaded2 == {"some-stem": [1.0, 2.0, 3.0]}, str(loaded2))


def test_mutation_removing_the_distinct_projects_check() -> None:
    """A monkeypatch of `pr.MIN_CLUSTER_PROJECTS` down to 1, restored in `finally` - proves the
    distinct-projects gate is what stops a single project's own recurring principle (two notes,
    one project) from promoting itself, which would defeat the entire point of a CROSS-project
    layer."""
    print("\n- mutation: dropping the distinct-projects check promotes a single project's own repeat -")
    make_sandbox(m, "pp_mut_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Cap a resource-bound parameter before scaling a workload."
    _write("project_a", "cap batch size", principle)
    _write("project_a", "cap thread pool", principle)

    saved = pr.MIN_CLUSTER_PROJECTS
    pr.MIN_CLUSTER_PROJECTS = 1
    try:
        summary = pr.promote(apply=False)   # dry: prove the cluster forms, don't write
    finally:
        pr.MIN_CLUSTER_PROJECTS = saved
    # Same-project pairs are never UNIONED at all (a separate, still-active guard inside
    # _cluster's pairwise loop), so the two notes stay two singleton components - but with the
    # distinct-projects FILTER dropped to 1, both singletons now pass it and both "promote"
    # themselves as if independently corroborated by another project, which is precisely the
    # defeat of the cross-project layer this check exists to prevent.
    check("mutation: WITHOUT the check, a single project's own repeat clusters and 'promotes' "
          "itself twice (would FAIL 'no cluster spans >=2 projects' above)",
          summary["clusters"] == 2 and summary["promoted"] == 2, str(summary))

    # sanity: the real, unmutated code still refuses it.
    summary_real = pr.promote(apply=False)
    check("and the unmutated check still refuses a single-project repeat",
          summary_real["clusters"] == 0, str(summary_real))


def test_dry_run_writes_nothing() -> None:
    print("\n- --dry (apply=False) reports what it would do and writes nothing -")
    make_sandbox(m, "pp_dry_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Cap a resource-bound parameter before scaling a workload."
    _write("project_a", "cap batch size", principle)
    _write("project_b", "cap worker pool", principle)
    summary = pr.promote(apply=False)
    check("a dry run still reports a cluster", summary["clusters"] == 1, str(summary))
    universal_dir = m.VAULT / "Patterns"
    universal_notes = [p for p in universal_dir.glob("*.md")
                       if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("nothing was written to disk", not universal_notes, str(universal_notes))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_two_different_projects_are_promoted,
               test_one_project_twice_is_not_promoted,
               test_a_scanner_hit_at_promotion_time_is_not_promoted,
               test_retire_on_drop_below_two_projects,
               test_a_cache_stamp_mismatch_is_refused,
               test_mutation_removing_the_distinct_projects_check,
               test_dry_run_writes_nothing):
        fn()
    print(f"\nprinciple promote: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
