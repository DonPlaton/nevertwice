#!/usr/bin/env python3
"""A5 (Q5): the sleep-time promoter that turns a `principle` recurring across >=2 DIFFERENT
projects into a single `universal` note - the pool `retrieve_cross_project(mode="universal")`
(A4) actually reads from. No model, no Ollama, no GPU: every test here drives
`nevertwice/principles.py` through a STUB embedder of fixed vectors, keyed by the exact
principle text, so cosine similarity is deterministic and hand-checkable.

    python tests/_test_principle_promote.py
"""
from __future__ import annotations

import re
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
    "tarnish a resource-wrought chisel before scaling a workload.": [1.0, 0.0, 0.0],
    "reckon before assuming a resource curb is the bottleneck.": [0.0, 1.0, 0.0],
    "always obscure confidences before scrivening an inkling to the vellum.": [0.0, 0.0, 1.0],
    # W17 closure (token provenance, 2026-09-23): same cosine as the plain "tarnish a resource-
    # wrought chisel..." rule above, so the stub still clusters it - the PRODUCT NAME is what the
    # provenance check (not clustering) is supposed to catch.
    "tarnish a resource-wrought chisel before scaling acme-widget-server.": [1.0, 0.0, 0.0],
    # H6 (2026-09-24): a genuine PARAPHRASE, different ordinary wording throughout, still
    # clusters here (same vector) - real clustering is a real embedder's job, not this stub's;
    # the stub only has to hold the cosine fixed so the test isolates what provenance decides.
    # C1 (2026-09-24): built ONLY from `_COMMON_WORDS` (resource/ceiling/scaling/increasing/
    # workload/assuming/bottleneck) + stopwords, on BOTH sides - any invented word here would
    # itself be a single-project token needing corroboration it cannot get (the bug this
    # correction fixes: the FIRST rewrite of this fixture swapped one non-exempt word for
    # another instead of staying inside the shrunk list).
    "scaling a workload before increasing the resource ceiling.": [1.0, 0.0, 0.0],
    "assuming a resource ceiling before increasing a workload is a bottleneck.": [1.0, 0.0, 0.0],
    # H6 (b): one private identifier-shaped name per shape, in an otherwise-shared sentence -
    # undeclared (no `entities`), so write time (option A) never touches it and it reaches
    # promotion unchanged; provenance is the only remaining defence.
    # NOTE: _stub_embed's lookup lowercases the text first (`key = text.strip().lower()`), so
    # every _VECS key with any letters must be lowercase here too, whatever case the actual
    # principle text on disk uses (UserRepository/PostgreSQL) - a mixed-case key here would
    # simply never match and _stub_embed would silently return None.
    "tarnish a resource-wrought chisel before scaling userrepository.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling payments-api.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling billing_service.": [1.0, 0.0, 0.0],
    # H6 (c): a PUBLIC tech name, camelCase-shaped exactly like UserRepository, mentioned by
    # BOTH projects - corroborated, so provenance must let it through. C1: common-word-only
    # wording either side of the shared identifier, same reasoning as the pair above.
    "scaling a workload before increasing the resource ceiling for postgresql.": [1.0, 0.0, 0.0],
    "assuming a resource ceiling before increasing postgresql workload is a bottleneck.":
        [1.0, 0.0, 0.0],
    # H6-era update to test (a) below: C's own invented private name, so NEITHER side has a
    # clean fallback the other could be promoted through instead (see that test's own comment).
    "tarnish a resource-wrought chisel before scaling globex-nimbus-array.": [1.0, 0.0, 0.0],
    # The auditor's exact nine-name probe (2026-09-24) - one principle per private name, C always
    # poisoned with its own distinct private name (no clean fallback), plus the public
    # "consumer-group" concept pair (H6, promoted, corroborated by both).
    "tarnish a resource-wrought chisel before scaling useauthstore.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling orderservice.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling db-primary.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling kafka-consumer-group.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling prod-cluster.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling phoenix.": [1.0, 0.0, 0.0],
    "tarnish a resource-wrought chisel before scaling acme-corp.": [1.0, 0.0, 0.0],
    # C1: common-word-only wording either side of the shared "consumer-group" identifier, same
    # reasoning as the two pairs above.
    "scaling a workload before increasing the resource ceiling for a consumer-group.":
        [1.0, 0.0, 0.0],
    "assuming a resource ceiling before increasing a consumer-group workload is a bottleneck.":
        [1.0, 0.0, 0.0],
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
    principle = "Tarnish a resource-wrought chisel before scaling a workload."
    s_a = _write("project_a", "tarnish batch size", principle)
    s_b = _write("project_b", "tarnish worker pool", principle)
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
          "Tarnish a resource-wrought chisel" in text, text)


def test_one_project_twice_is_not_promoted() -> None:
    print("\n- the same principle stated twice in ONE project is not promoted -")
    make_sandbox(m, "pp_one_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Tarnish a resource-wrought chisel before scaling a workload."
    _write("project_a", "tarnish batch size", principle)
    _write("project_a", "tarnish thread pool", principle)

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
    principle_a = "Reckon before assuming a resource curb is the bottleneck."
    principle_b = "Reckon before assuming a resource curb is the bottleneck."
    # A SEPARATE note in project_a carries "gpu-cluster-7" as one of ITS OWN entities - so it
    # is in project_a's VOCABULARY (A5's forbidden set) even though it never appears in the
    # candidate note's own entities (A1's write-time forbidden set).
    _write("project_a", "gpu cluster note", "", entities=["gpu-cluster-7"])  # no principle:
    # only seeds project_a's VOCABULARY, must not itself become a candidate
    s_a = _write("project_a", "reckon resource curb",
                 "Reckon before assuming gpu-cluster-7's resource curb is the bottleneck.")
    check("A1's write-time scan let this one through (it only forbids its own entities)",
          bool(s_a), s_a)
    fm_a = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s_a}.md")
    check("the identifier is really on disk (proves this is testing A5, not re-testing A1)",
          "gpu-cluster-7" in (fm_a.get("principle") or ""), fm_a.get("principle"))
    s_b = _write("project_b", "reckon resource curb too", principle_b)
    check("the clean twin in project_b was written", bool(s_b), s_b)

    summary = pr.promote(apply=True)
    check("no cluster formed (the identifier-carrying candidate was dropped by the re-scan)",
          summary["clusters"] == 0, str(summary))
    check("no candidate survived from project_a's identifier-carrying note",
          summary["candidates"] == 1, str(summary))


def test_a_undeclared_product_name_blocks_promotion_via_token_provenance() -> None:
    """(a) W17 closure: A's principle mentions a product name it never declared as an entity
    (so A1's write-time scan and A5's `_rescan` both let it through - see
    test_a_scanner_hit_at_promotion_time_is_not_promoted for that boundary). The PROMOTION-time
    token-provenance check is the one that has to catch this, because nothing upstream of it
    does.

    C ALSO carries its own invented private name here (H6-era update, 2026-09-24) - not the
    plain "before scaling a workload." this test used before H6. `_promote_cluster` tries the
    next-most-central candidate when the medoid fails ("the clean fallback"), and once H6
    stopped flagging every non-identical word, C's plain phrasing had NOTHING left to fail on
    and was promoted in its place - a correct instance of that documented fallback (a cluster
    IS legitimately shareable when one honest member's wording has no leak), just not what THIS
    test is for. So C is given its own private name too, with NO clean escape hatch in the
    cluster at all - the only way to test that the FIRST candidate's leak, specifically,
    is caught."""
    print("\n- (a) an undeclared product name blocks promotion at the provenance gate -")
    make_sandbox(m, "pp_prov_a_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    poisoned = "Tarnish a resource-wrought chisel before scaling acme-widget-server."
    also_poisoned = "Tarnish a resource-wrought chisel before scaling globex-nimbus-array."
    s_a = _write("project_a", "tarnish batch size", poisoned)          # entities=[] - undeclared
    s_c = _write("project_c", "tarnish worker pool", also_poisoned)    # entities=[] - undeclared
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")
    fm_a = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s_a}.md")
    check("the product name is on disk, undeclared as an entity (write-time scan let it "
          "through)", "acme-widget-server" in (fm_a.get("principle") or "")
          and not fm_a.get("entities"), fm_a)

    summary = pr.promote(apply=True)
    check("a cluster formed (the stub embedder still merges the two phrasings)",
          summary["clusters"] == 1, str(summary))
    check("nothing was promoted - neither side has a clean fallback", summary["promoted"] == 0,
          str(summary))
    check("the rejection is counted as rejected_single_project_token",
          summary["rejected_single_project_token"] == 1, str(summary))
    result = summary["results"][0]
    check("BOTH sides' private-name tokens are named as offending (the union over every "
         "candidate tried, not just the medoid's)",
         {"acme", "widget", "server", "globex", "nimbus", "array"}
         <= set(result["offending_tokens"]), str(result["offending_tokens"]))
    universal_dir = m.VAULT / "Patterns"
    universal_notes = [p for p in universal_dir.glob("*.md")
                       if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("no universal note exists on disk", not universal_notes, str(universal_notes))


def test_b_shared_generic_wording_is_promoted() -> None:
    """(b) The positive control for (a): the SAME rule, phrased with only generic words BOTH
    projects' own vocabulary already carries (via the exact-same prefix), is promoted - the
    provenance gate does not block an ordinary cross-project rule, only a single-project
    token inside one."""
    print("\n- (b) the same rule in only shared generic words is promoted -")
    make_sandbox(m, "pp_prov_b_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    clean = "Tarnish a resource-wrought chisel before scaling a workload."
    s_a = _write("project_a", "tarnish batch size", clean)
    s_c = _write("project_c", "tarnish worker pool", clean)
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")

    summary = pr.promote(apply=True)
    check("one cluster formed", summary["clusters"] == 1, str(summary))
    check("it was promoted", summary["promoted"] == 1, str(summary))
    check("nothing was rejected for single-project tokens",
          summary["rejected_single_project_token"] == 0, str(summary))


def test_c_mutation_token_provenance_threshold_of_one() -> None:
    """(c) A monkeypatch of `pr.TOKEN_PROVENANCE_MIN_PROJECTS` down to 1, restored in
    `finally` - proves the provenance gate, not just the cluster-level MIN_CLUSTER_PROJECTS
    check, is what stops (a)'s undeclared product name: at threshold 1, a token appearing in
    even ONE project's own vocabulary already "passes", so the whole point of the check (a
    name genuinely shared by >=2 projects) is gone and (a)'s poisoned case promotes."""
    print("\n- (c) mutation: threshold of 1 project lets (a)'s undeclared product name promote -")
    make_sandbox(m, "pp_prov_c_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    poisoned = "Tarnish a resource-wrought chisel before scaling acme-widget-server."
    also_poisoned = "Tarnish a resource-wrought chisel before scaling globex-nimbus-array."
    _write("project_a", "tarnish batch size", poisoned)
    _write("project_c", "tarnish worker pool", also_poisoned)

    saved = pr.TOKEN_PROVENANCE_MIN_PROJECTS
    pr.TOKEN_PROVENANCE_MIN_PROJECTS = 1
    try:
        summary = pr.promote(apply=False)   # dry: prove it WOULD promote, don't write
    finally:
        pr.TOKEN_PROVENANCE_MIN_PROJECTS = saved
    check("mutation: WITHOUT the real threshold, the undeclared product name now promotes "
          "(would FAIL 'nothing was promoted' in test (a) above)",
          summary["clusters"] == 1 and summary["promoted"] == 1
          and summary["rejected_single_project_token"] == 0, str(summary))

    # sanity: the real, unmutated threshold still refuses it.
    summary_real = pr.promote(apply=False)
    check("and the unmutated threshold (2) still refuses it",
          summary_real["promoted"] == 0 and summary_real["rejected_single_project_token"] == 1,
          str(summary_real))


def test_retire_on_drop_below_two_projects() -> None:
    print("\n- a universal note is retired once its cluster falls below two projects -")
    make_sandbox(m, "pp_retire_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle = "Tarnish a resource-wrought chisel before scaling a workload."
    s_a = _write("project_a", "tarnish batch size", principle)
    s_b = _write("project_b", "tarnish worker pool", principle)
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
    principle = "Tarnish a resource-wrought chisel before scaling a workload."
    _write("project_a", "tarnish batch size", principle)
    _write("project_a", "tarnish thread pool", principle)

    # Two independent guards now stand between a single-project repeat and promotion - the
    # cluster-level MIN_CLUSTER_PROJECTS check (this test's own subject) and the token-level
    # TOKEN_PROVENANCE_MIN_PROJECTS check (W17 closure, its own dedicated mutation test below).
    # Demonstrating THIS guard in isolation means neutralising the other one too, or a real
    # single-project leak would still be caught by provenance and this test would misreport
    # MIN_CLUSTER_PROJECTS as load-bearing when the observed rejection actually came from
    # somewhere else.
    saved = pr.MIN_CLUSTER_PROJECTS
    saved_tp = pr.TOKEN_PROVENANCE_MIN_PROJECTS
    pr.MIN_CLUSTER_PROJECTS = 1
    pr.TOKEN_PROVENANCE_MIN_PROJECTS = 1
    try:
        summary = pr.promote(apply=False)   # dry: prove the cluster forms, don't write
    finally:
        pr.MIN_CLUSTER_PROJECTS = saved
        pr.TOKEN_PROVENANCE_MIN_PROJECTS = saved_tp
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
    principle = "Tarnish a resource-wrought chisel before scaling a workload."
    _write("project_a", "tarnish batch size", principle)
    _write("project_b", "tarnish worker pool", principle)
    summary = pr.promote(apply=False)
    check("a dry run still reports a cluster", summary["clusters"] == 1, str(summary))
    universal_dir = m.VAULT / "Patterns"
    universal_notes = [p for p in universal_dir.glob("*.md")
                       if (m.parse_typed_stem(p.stem) or {}).get("project") == m.UNIVERSAL_PROJECT]
    check("nothing was written to disk", not universal_notes, str(universal_notes))


def test_d_own_generic_entity_no_longer_self_rejects_at_promotion() -> None:
    """(d) 2026-09-24: the v2 100-case real run's actual dominant defect. A note's own declared
    entity is routinely a GENERIC word its own principle also uses ("workload" declared, "...
    scaling a workload." in the text) - `_rescan`'s promotion-time forbidden set used to include
    EVERY declared entity verbatim, so `principle_scan` rejected the candidate against its OWN
    vocabulary before clustering ever got a chance to run. Found on 53 of 57 cosine>=0.75 pairs
    in the real run: one or both sides self-rejected, leaving only the (always entity-free)
    distractor to pad `candidates`, so `clusters` read 0 despite a cosine well above T_PRINCIPLE
    - a divergence between what research/cross_project_bench.py's own principle_cosine measured
    and what promote() ever got the chance to compare."""
    print("\n- (d) a generic self-declared entity no longer self-rejects a candidate -")
    make_sandbox(m, "pp_selfreject_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    clean = "Tarnish a resource-wrought chisel before scaling a workload."
    # "workload" is declared as THIS note's own entity AND appears literally in its own
    # principle - the exact real-world shape (project cpv1_005_alpha declared "storage", its
    # own principle said "...persistent storage...").
    s_a = _write("project_a", "tarnish batch size", clean, entities=["workload"])
    s_c = _write("project_c", "tarnish worker pool", clean, entities=["queue-depth"])
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")
    fm_a = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s_a}.md")
    check("A's principle is on disk, still carrying its own declared entity word",
          "workload" in (fm_a.get("principle") or "").lower(), fm_a.get("principle"))

    candidates = pr._rescan(pr._live_principle_candidates())
    check("BOTH candidates survive the promotion-time rescan (neither self-rejects)",
          len(candidates) == 2, [c["principle"] for c in candidates])

    summary = pr.promote(apply=True)
    check("a cluster formed", summary["clusters"] == 1, str(summary))
    check("it was promoted (a shared generic word, no identifier-shaped token anywhere)",
          summary["promoted"] == 1, str(summary))


# ── H6 (2026-09-24): provenance for IDENTIFIER-SHAPED tokens only ──────────────────────────
# Before this fix, `_token_provenance` required >=2-project corroboration for EVERY content
# token, including the ordinary English words a genuine paraphrase is full of - each project's
# own vocabulary is usually just its one note's own wording, so two honest paraphrases almost
# never share enough exact words to pass. The fix: only IDENTIFIER-SHAPED tokens (the five
# shapes from `_engine_text.py` - digit/dot/slash, underscore, SCREAMING, camelCase/PascalCase,
# hyphen-infra) need corroboration; an ordinary word passes regardless of how many or few
# projects' corpora happen to use it.

def test_g_ordinary_paraphrase_is_promoted() -> None:
    """(a) H6: a paraphrase pair whose wording differs throughout, no identifier-shaped token
    anywhere, is promoted - RED before this fix (every content token needed corroboration, and
    "resource"/"ceiling"/"scaling"/"increasing"/"workload"/"assuming"/"bottleneck" only ever
    appear in ONE side's own vocabulary - here they are all `_COMMON_WORDS`, so none needs it)."""
    print("\n- (a) H6: an ordinary-wording paraphrase (no identifier-shaped token) is promoted -")
    make_sandbox(m, "pp_h6_a_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    s_a = _write("project_a", "tarnish resource", "Scaling a workload before increasing "
                 "the resource ceiling.")
    s_c = _write("project_c", "curb resource", "Assuming a resource ceiling before increasing "
                 "a workload is a bottleneck.")
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")

    summary = pr.promote(apply=True)
    check("one cluster formed", summary["clusters"] == 1, str(summary))
    check("it was promoted - no identifier-shaped token needed corroboration",
          summary["promoted"] == 1, str(summary))
    check("nothing was rejected for single-project tokens",
          summary["rejected_single_project_token"] == 0, str(summary))


def test_h_private_shaped_name_blocks_promotion_per_shape() -> None:
    """(b) H6: A's principle carries a private identifier-shaped name - one case per shape
    option (A) moved OFF the write gate (camelCase, hyphen-infra) plus one it kept (underscore,
    here UNDECLARED so write time never sees it either) - that only A's own vocabulary has.
    None is promoted, and the offending token is named in every case.

    C ALSO carries its own private name (its own invented one, "globex-nimbus-array" - the
    same H6-era reasoning as test (a)'s update above): a plain "...scaling a workload." for C
    would give the cluster a clean fallback candidate once H6 stopped flagging "workload" as
    needing corroboration, and that fallback would promote instead of testing what this case is
    for - A's OWN shape-specific token blocking THAT candidate."""
    print("\n- (b) H6: a private camel/kebab/snake name blocks promotion, token named -")
    c_poisoned = "Tarnish a resource-wrought chisel before scaling globex-nimbus-array."
    # B1 (2026-09-24): the offending token is the WHOLE normalized compound now, not a split
    # part - "billing_service" normalizes to "billing-service" ("_" unified with "-").
    cases = [
        ("camel", "tarnish a resource-wrought chisel before scaling UserRepository.", "userrepository"),
        ("kebab", "tarnish a resource-wrought chisel before scaling payments-api.", "payments-api"),
        ("snake", "tarnish a resource-wrought chisel before scaling billing_service.", "billing-service"),
    ]
    for shape, poisoned, offending_tok in cases:
        make_sandbox(m, f"pp_h6_b_{shape}_", offline=True)
        pr = _import_fresh()
        m.embed_text = _stub_embed
        s_a = _write("project_a", f"tarnish-{shape}", poisoned)      # entities=[] - undeclared
        s_c = _write("project_c", "tarnish plain", c_poisoned)           # entities=[] - undeclared
        check(f"{shape}: both source notes were written", bool(s_a) and bool(s_c),
              f"{s_a}/{s_c}")
        fm_a = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s_a}.md")
        # offending_tok is the NORMALIZED compound (B1: "_" unified to "-"), which does not
        # literally appear in an underscored source ("billing_service" on disk, "billing-
        # service" normalized) - check the un-normalized prefix instead, present either way.
        check(f"{shape}: the private name is on disk, undeclared as an entity (write time no "
             "longer touches this shape)", offending_tok.split("-")[0] in
             (fm_a.get("principle") or "").lower() and not fm_a.get("entities"), fm_a)

        summary = pr.promote(apply=True)
        check(f"{shape}: a cluster formed (the stub still merges the two phrasings)",
              summary["clusters"] == 1, str(summary))
        check(f"{shape}: nothing was promoted", summary["promoted"] == 0, str(summary))
        check(f"{shape}: rejected as rejected_single_project_token",
              summary["rejected_single_project_token"] == 1, str(summary))
        result = summary["results"][0]
        check(f"{shape}: the private token is named as offending ({offending_tok!r})",
              offending_tok in result["offending_tokens"], str(result["offending_tokens"]))


def test_i_public_camel_name_in_both_is_promoted() -> None:
    """(c) H6: "PostgreSQL" - the SAME camelCase shape as the private "UserRepository" test
    above - is promoted when BOTH projects' own principles mention it: shape alone never
    decided this (option A already removed camelCase from the write gate for exactly this
    reason), corroboration does."""
    print("\n- (c) H6: a public camelCase name corroborated by both projects is promoted -")
    make_sandbox(m, "pp_h6_c_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    s_a = _write("project_a", "tarnish postgres a", "Scaling a workload before increasing "
                 "the resource ceiling for PostgreSQL.")
    s_c = _write("project_c", "tarnish postgres c", "Assuming a resource ceiling before "
                 "increasing PostgreSQL workload is a bottleneck.")
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")

    summary = pr.promote(apply=True)
    check("one cluster formed", summary["clusters"] == 1, str(summary))
    check("it was promoted - PostgreSQL is corroborated by BOTH projects' own vocabulary",
          summary["promoted"] == 1, str(summary))
    check("nothing was rejected for single-project tokens",
          summary["rejected_single_project_token"] == 0, str(summary))


def test_i2_public_hyphen_concept_in_both_is_promoted() -> None:
    """(c, continued) "consumer-group" - a GENERIC hyphen concept (both parts are infra nouns,
    `_INFRA_HYPHEN_TOKENS`) - is promoted when BOTH projects mention it, the same reasoning as
    PostgreSQL above but for the hyphen-infra shape instead of camelCase."""
    print("\n- (c) H6: a public hyphen-infra concept corroborated by both projects is promoted -")
    make_sandbox(m, "pp_h6_c2_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    s_a = _write("project_a", "tarnish group a", "Scaling a workload before increasing the "
                 "resource ceiling for a consumer-group.")
    s_c = _write("project_c", "tarnish group c", "Assuming a resource ceiling before increasing "
                 "a consumer-group workload is a bottleneck.")
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")

    summary = pr.promote(apply=True)
    check("one cluster formed", summary["clusters"] == 1, str(summary))
    check("it was promoted - consumer-group is corroborated by BOTH projects' own vocabulary",
          summary["promoted"] == 1, str(summary))
    check("nothing was rejected for single-project tokens",
          summary["rejected_single_project_token"] == 0, str(summary))


def test_h2_auditors_nine_name_probe_all_blocked() -> None:
    """The auditor's exact probe (2026-09-24): nine private names, each present in ONE
    project's vocabulary only, must ALL be NOT promoted, with the token named in the rejection -
    seven shape-classifiable (camelCase/PascalCase, hyphen-infra) already covered per-shape
    above, plus "phoenix" (a lone lowercase word) and "acme-corp" (a hyphenated company name,
    neither part an infra noun) - NEITHER has any shape this layer keys on, so ONLY
    `_is_uncorroborated_private_word`'s corpus-uniqueness rule (the coordinator's decision)
    catches them. Not covered silently: if this test is red, the uniqueness rule is not doing
    its job for these two."""
    print("\n- the auditor's nine-name probe: all nine private names blocked, token named -")
    # B1 (2026-09-24): the seven shape-classifiable names are named as the WHOLE normalized
    # compound now, never a split part - db-primary and kafka-consumer-group used to be named
    # by a coincidentally-surviving fragment ("primary", "kafka"); now the compound itself is
    # the identity that is (or is not) corroborated. phoenix/acme-corp are NOT shape-classified
    # at all, so B1 does not touch them - they still go through the split-token uniqueness path.
    cases = [
        ("payments-api", "payments-api"), ("UserRepository", "userrepository"),
        ("useAuthStore", "useauthstore"), ("OrderService", "orderservice"),
        ("db-primary", "db-primary"), ("kafka-consumer-group", "kafka-consumer-group"),
        ("prod-cluster", "prod-cluster"), ("phoenix", "phoenix"), ("acme-corp", "acme"),
    ]
    c_poisoned = "Tarnish a resource-wrought chisel before scaling globex-nimbus-array."
    for name, offending_tok in cases:
        make_sandbox(m, f"pp_h6_nine_{offending_tok}_", offline=True)
        pr = _import_fresh()
        m.embed_text = _stub_embed
        poisoned = f"Tarnish a resource-wrought chisel before scaling {name}."
        s_a = _write("project_a", f"tarnish {offending_tok}", poisoned)   # entities=[] - undeclared
        s_c = _write("project_c", "tarnish plain", c_poisoned)            # entities=[] - undeclared
        check(f"{name!r}: both source notes were written", bool(s_a) and bool(s_c),
              f"{s_a}/{s_c}")

        summary = pr.promote(apply=True)
        check(f"{name!r}: nothing was promoted", summary["promoted"] == 0, str(summary))
        check(f"{name!r}: rejected as rejected_single_project_token",
              summary["rejected_single_project_token"] >= 1, str(summary))
        all_offending: set = set()
        for r in summary["results"]:
            all_offending |= set(r.get("offending_tokens") or ())
        check(f"{name!r}: the private token is named as offending ({offending_tok!r})",
              offending_tok in all_offending, sorted(all_offending))


def test_j_mutation_ignoring_camel_shape_reddens_h_camel_case() -> None:
    """(d) Mutation: `m._has_camel_transition` forced to always return False makes
    `_identifier_shaped_words` blind to camelCase/PascalCase entirely.

    Isolated from H6's OWN uniqueness backstop (`_is_uncorroborated_private_word`, the
    coordinator's
    rule for "phoenix"/"acme-corp") on purpose: that backstop would ALSO flag "userrepository"
    as long as it is absent from every OTHER live project, so a naive mutation test (just A and
    C in the vault) would still redden nothing - it would be proving the BACKSTOP works, not
    that the camel-shape rule specifically does. A THIRD, unrelated project (`project_x`, not a
    cluster member) is given its OWN note that also happens to use "UserRepository" - now the
    token is NOT vault-wide-unique (the uniqueness backstop stands down), so BEFORE the
    mutation only the camel-SHAPE rule is what fails it against the cluster's own two
    projects (project_x's use of it does not help project_c corroborate it). Calls
    `_token_provenance` directly, sidestepping `promote()`'s own cluster/medoid-fallback
    machinery, which is not what this unit-level property needs."""
    print("\n- mutation: provenance blind to camelCase reddens (b)'s camel case, by name -")
    make_sandbox(m, "pp_h6_mut_", offline=True)
    pr = _import_fresh()
    m.embed_text = _stub_embed
    principle_a = "tarnish a resource-wrought chisel before scaling UserRepository."
    s_a = _write("project_a", "tarnish camel", principle_a)
    s_c = _write("project_c", "tarnish plain", "Tarnish a resource-wrought chisel before scaling "
                 "a workload.")
    s_x = _write("project_x", "unrelated userrepository note",
                 "UserRepository needs its own migration runner, unrelated to this cluster.")
    check("all three source notes were written", bool(s_a) and bool(s_c) and bool(s_x),
          f"{s_a}/{s_c}/{s_x}")

    cluster_projects = {"project_a", "project_c"}
    ok_before, offending_before = pr._token_provenance(principle_a, "project_a",
                                                        cluster_projects, {})
    check("before the mutation: the camel-shaped private name fails provenance against the "
         "CLUSTER's own two projects, even though a third, unrelated project also uses it",
         not ok_before and "userrepository" in offending_before, offending_before)

    saved = m._has_camel_transition
    m._has_camel_transition = lambda t: False
    try:
        ok_after, offending_after = pr._token_provenance(principle_a, "project_a",
                                                          cluster_projects, {})
    finally:
        m._has_camel_transition = saved
    check("mutation: WITHOUT the camel shape, the SAME sentence now passes provenance (would "
         "FAIL 'before the mutation' above) - the camel-shape rule specifically was what "
         "caught it, not the uniqueness backstop", ok_after, offending_after)


# ── B1 (2026-09-24, coordinator review of 1daf871): a compound is corroborated as a WHOLE ──
# Before this fix, _identifier_shaped_tokens ran a shaped word through _content_tokens
# ("payments-api" -> {"payments", "api"}) and each PART was looked up separately in a
# project's ordinary vocabulary - so a project that used "payments" and "api" separately in
# prose, never the compound itself, silently corroborated it. All four tests below call
# _token_provenance directly (no clustering/stub-vector plumbing needed for this property).

def test_k_compound_corroborated_as_whole_not_by_parts() -> None:
    """(1)(2) A compound identifier is corroborated by the WHOLE compound, never by separately-
    corroborated split parts. Red on 1daf871 (the pre-B1 code would have let C's separate use
    of "payments"/"api" corroborate "payments-api")."""
    print("\n- B1 (1)(2): compound corroborated whole, not by separately-used parts -")
    cases = [
        ("Tarnish a resource-wrought chisel before scaling payments-api.",
         "Our payments team owns the api gateway configuration entirely.", "payments-api"),
        ("Tarnish a resource-wrought chisel before scaling kafka-consumer-group.",
         "The kafka topic settings and the consumer group settings are managed separately.",
         "kafka-consumer-group"),
        ("Tarnish a resource-wrought chisel before scaling billing_service.",
         "Our billing team owns the service layer configuration entirely.",
         "billing-service"),
    ]
    for principle_a, prose_c, offending_tok in cases:
        make_sandbox(m, f"pp_h6_k_{offending_tok}_", offline=True)
        pr = _import_fresh()
        s_a = _write("project_a", "tarnish compound", principle_a)
        s_c = _write("project_c", "tarnish parts", prose_c)
        check(f"{offending_tok!r}: both source notes were written", bool(s_a) and bool(s_c),
              f"{s_a}/{s_c}")

        ok, offending = pr._token_provenance(principle_a, "project_a",
                                             {"project_a", "project_c"}, {})
        check(f"{offending_tok!r}: fails provenance (C never used the compound itself)",
             not ok and offending_tok in offending, offending)


def test_l_mutation_corroborating_by_parts_reddens_k_by_name() -> None:
    """(4) The pre-B1 algorithm, reconstructed inline from the SAME building blocks
    `_token_provenance` still uses today (`_identifier_shaped_words`, `_content_tokens`,
    `_project_token_vocabulary` - none of them changed by B1), rather than monkeypatched piece
    by piece: `_token_provenance`'s whole-compound lookup and the old per-part lookup search
    for DIFFERENT keys against DIFFERENT vocabularies, so patching only the vocabulary function
    cannot reproduce the old shape (tried first - the whole-compound key it still searches for
    never matches a vocabulary of loose parts, so nothing wrongly passed; that itself is not
    evidence the fix works, just that the two algorithms use incompatible keys). Reddens test
    (1)'s payments-api case, by name: C's separate use of "payments"/"api" wrongly corroborates
    it under the reconstructed pre-B1 algorithm, the exact defect (1) is red on 1daf871 for."""
    print("\n- mutation: the pre-B1 per-part algorithm wrongly passes the payments-api case -")
    make_sandbox(m, "pp_h6_l_", offline=True)
    pr = _import_fresh()
    principle_a = "Tarnish a resource-wrought chisel before scaling payments-api."
    prose_c = "Our payments team owns the api gateway configuration entirely."
    s_a = _write("project_a", "tarnish compound", principle_a)
    s_c = _write("project_c", "tarnish parts", prose_c)
    check("both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")

    def _old_per_part_provenance(sentence, cluster_projects):
        vocab_cache: dict = {}
        offending = []
        shaped = pr._identifier_shaped_words(sentence)
        id_tokens = {t for w in shaped for t in pr._content_tokens(w)}
        for tok in pr._content_tokens(sentence):
            if tok not in id_tokens:
                continue
            seen_in = 0
            for proj in cluster_projects:
                if proj not in vocab_cache:
                    vocab_cache[proj] = pr._project_token_vocabulary(proj)
                if tok in vocab_cache[proj]:
                    seen_in += 1
            if seen_in < pr.TOKEN_PROVENANCE_MIN_PROJECTS:
                offending.append(tok)
        return not offending, offending

    ok, offending = _old_per_part_provenance(principle_a, {"project_a", "project_c"})
    check("mutation: the PRE-B1 per-part algorithm wrongly lets payments-api pass (test (1) "
         "above shows the CURRENT, fixed _token_provenance correctly rejects the same inputs)",
         ok, offending)


def test_m_underscore_and_hyphen_unify_but_dot_does_not() -> None:
    """B1's normalization addition (coordinator decision, 2026-09-24): "-" and "_" are the SAME
    identity (5) - the same name spelled kebab by one project and snake by another is still
    corroboration; "." is NOT unified with either (6) - a dot carries host/path structure a
    hyphen does not share, so "payments-api" and "payments.api" are different names."""
    print("\n- B1: '-' and '_' unify (5), but '.' does not unify with either (6) -")
    principle_a = "Tarnish a resource-wrought chisel before scaling payments-api."

    make_sandbox(m, "pp_h6_m5_", offline=True)
    pr = _import_fresh()
    s_a = _write("project_a", "tarnish kebab", principle_a)
    s_c = _write("project_c", "tarnish snake", "Tarnish a resource-wrought chisel before scaling "
                 "payments_api.")
    check("(5) both source notes were written", bool(s_a) and bool(s_c), f"{s_a}/{s_c}")
    ok5, offending5 = pr._token_provenance(principle_a, "project_a",
                                           {"project_a", "project_c"}, {})
    check("(5) payments-api / payments_api corroborate each other (same normalized name)",
         ok5, offending5)

    make_sandbox(m, "pp_h6_m6_", offline=True)
    pr = _import_fresh()
    s_a2 = _write("project_a", "tarnish kebab", principle_a)
    s_c2 = _write("project_c", "tarnish dot", "Tarnish a resource-wrought chisel before scaling "
                  "payments.api.")
    check("(6) both source notes were written", bool(s_a2) and bool(s_c2), f"{s_a2}/{s_c2}")
    ok6, offending6 = pr._token_provenance(principle_a, "project_a",
                                           {"project_a", "project_c"}, {})
    check("(6) payments-api / payments.api do NOT corroborate each other (dot is not unified)",
         not ok6 and "payments-api" in offending6, offending6)


def test_n_no_common_word_occurs_in_the_bench_corpus() -> None:
    """C1 (2026-09-24): the mechanical guard `principles._COMMON_WORDS`'s own docstring promises.
    `_COMMON_WORDS` must be grown from genuine, generic vocabulary - NEVER by reading what the
    real `research/` bench corpus happens to contain and exempting those exact words, which would
    quietly tune the "this is an ordinary word" list to make today's benchmark look cleaner than
    the mechanism actually is. Any word occurring >=3 times (word-boundary, case-insensitive)
    across `research/cross_project_bench*.py` + `research/data/cross_project*.json` is named as
    an offender and the check fails; RED before this test existed, the removed 11-word set
    (anything, bound, cap, disk, limit, load, measure, parameter, redact, secrets, writing) would
    have gone undetected forever - see the red-before capture in the commit that adds this test."""
    print("\n- C1: no _COMMON_WORDS entry is itself sourced from the real bench corpus -")
    pr = _import_fresh()
    corpus_files = sorted(ROOT.glob("research/cross_project_bench*.py")) + \
        sorted(ROOT.glob("research/data/cross_project*.json"))
    check("at least one bench corpus file was found to scan", len(corpus_files) > 0,
         str(corpus_files))
    corpus_text = "\n".join(p.read_text(encoding="utf-8") for p in corpus_files)

    offenders = []
    for word in sorted(pr._COMMON_WORDS):
        hits = len(re.findall(rf"\b{re.escape(word)}\b", corpus_text, flags=re.IGNORECASE))
        if hits >= 3:
            offenders.append((word, hits))
    check("no _COMMON_WORDS entry occurs >=3x in the bench corpus", not offenders,
         f"offenders: {offenders}" if offenders else "")


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
               test_a_undeclared_product_name_blocks_promotion_via_token_provenance,
               test_b_shared_generic_wording_is_promoted,
               test_c_mutation_token_provenance_threshold_of_one,
               test_retire_on_drop_below_two_projects,
               test_a_cache_stamp_mismatch_is_refused,
               test_mutation_removing_the_distinct_projects_check,
               test_dry_run_writes_nothing,
               test_d_own_generic_entity_no_longer_self_rejects_at_promotion,
               test_g_ordinary_paraphrase_is_promoted,
               test_h_private_shaped_name_blocks_promotion_per_shape,
               test_h2_auditors_nine_name_probe_all_blocked,
               test_i_public_camel_name_in_both_is_promoted,
               test_i2_public_hyphen_concept_in_both_is_promoted,
               test_j_mutation_ignoring_camel_shape_reddens_h_camel_case,
               test_k_compound_corroborated_as_whole_not_by_parts,
               test_l_mutation_corroborating_by_parts_reddens_k_by_name,
               test_m_underscore_and_hyphen_unify_but_dot_does_not,
               test_n_no_common_word_occurs_in_the_bench_corpus):
        fn()
    print(f"\nprinciple promote: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
