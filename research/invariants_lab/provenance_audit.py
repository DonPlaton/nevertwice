"""Which corpus did each Phase V artifact actually measure?

Three artifacts written by the unattended Phase V chain carry `"corpus": "corpus_dev"`
and `"in_sample": true` while sitting under a `*_heldout.json` name. The label is a
hardcoded literal in the measuring script's output dict; the *data selection* is done by
`_select_corpus(args.corpus)`, which rebinds the paths before anything runs. So the label
and the data can disagree, and on this run they do.

This module refuses to settle that by reading either label. It derives provenance from the
artifact's own contents, by two independent routes:

* **block names.** Every repository slug in each manifest is searched for in the artifact's
  raw text. The two corpora share no repository -- `corpus_manifest.json` names eight,
  `heldout_manifest.json` names thirty, and the intersection is empty -- so any artifact
  carrying block names is decided outright.
* **census shape.** An artifact with no block names is matched against the two answer keys'
  cluster and mutant counts, and against the two silence censuses' commit counts. These
  differ by roughly an order of magnitude and cannot be confused.

An artifact that neither route can decide is reported as `undecided`, never guessed.

    python research/invariants_lab/provenance_audit.py
    python research/invariants_lab/provenance_audit.py --print
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "provenance_v.json"

#: (manifest, the answer key it produced, a human name)
CORPORA = (
    ("corpus_dev", "corpus_manifest.json", "mutants.json"),
    ("heldout", "heldout_manifest.json", "mutants_heldout.json"),
)

_SLUG_KEYS = frozenset({"slug", "name", "repo", "repository"})


def _slugs(obj: object, acc: set[str]) -> set[str]:
    """Every repository slug named anywhere in a manifest."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SLUG_KEYS and isinstance(value, str) and "/" in value:
                acc.add(value)
            _slugs(value, acc)
    elif isinstance(obj, list):
        for value in obj:
            _slugs(value, acc)
    return acc


def _load(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _blocks(manifest: Path) -> set[str]:
    """Slugs in both the `owner/name` and the `owner_name` spelling artifacts use."""
    data = _load(manifest)
    if data is None:
        return set()
    raw = _slugs(data, set())
    return raw | {s.replace("/", "_") for s in raw}


def _scale(answer_key: Path) -> dict[str, int]:
    """The cluster and mutant counts an answer key implies, for the shape route."""
    data = _load(answer_key)
    if not isinstance(data, dict):
        return {}
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    mutants = data.get("mutants")
    n_mutants = totals.get("mutants") or (len(mutants) if isinstance(mutants, list) else None)
    n_clusters = totals.get("source_commits") or totals.get("clusters")
    out: dict[str, int] = {}
    if isinstance(n_mutants, int):
        out["n_mutants"] = n_mutants
    if isinstance(n_clusters, int):
        out["n_clusters"] = n_clusters
    return out


def _nearest_by_shape(art: dict, scales: dict[str, dict[str, int]]) -> tuple[str | None, str]:
    """Match an artifact's own counts against each corpus's, within 5%."""
    for field in ("n_mutants", "n_clusters"):
        value = art.get(field)
        if not isinstance(value, int) or value <= 0:
            continue
        for corpus, known in scales.items():
            ref = known.get(field)
            if isinstance(ref, int) and ref > 0 and abs(value - ref) / ref <= 0.05:
                return corpus, f"{field}={value} within 5% of {corpus}'s {ref}"
    return None, ""


def _by_exclusion(path: Path, art: dict) -> tuple[str | None, str]:
    """Falsify the development corpus, then use disjointness.

    An artifact with no block names and no mutant counts can still carry a *census*:
    how many sampled commits it considered and how many had no axis to check. The
    development run's own artifact records the same two numbers. If they differ, the
    artifact did not measure the development corpus -- and `heldout_seal.json` admits
    exactly two corpora, so not-development is held-out.

    This is weaker than the other two routes and is labelled as such: it identifies by
    elimination rather than by recognition, and it is only valid while two corpora exist.
    """
    twin = HERE / path.name.replace("_heldout.json", ".json")
    mine = art.get("corpus_silence")
    if not twin.exists() or not isinstance(mine, dict):
        return None, ""
    theirs = _load(twin)
    theirs = theirs.get("corpus_silence") if isinstance(theirs, dict) else None
    if not isinstance(theirs, dict):
        return None, ""
    fields = [f for f in ("considered", "no_axis_present", "fired")
              if isinstance(mine.get(f), int) and isinstance(theirs.get(f), int)]
    differing = [f for f in fields if mine[f] != theirs[f]]
    if not fields or not differing:
        return None, ""
    detail = ", ".join(f"{f} {mine[f]} vs {theirs[f]}" for f in differing)
    return "heldout", f"census differs from the development run ({detail}); two corpora exist"


def audit() -> dict:
    blocks = {name: _blocks(HERE / manifest) for name, manifest, _ in CORPORA}
    scales = {name: _scale(HERE / key) for name, _, key in CORPORA}
    overlap = sorted(blocks["corpus_dev"] & blocks["heldout"])

    rows: list[dict] = []
    for path in sorted(HERE.glob("*_heldout.json")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = {name: sorted(s for s in slugs if s in text) for name, slugs in blocks.items()}
        art = _load(path)
        art = art if isinstance(art, dict) else {}
        label = art.get("corpus")

        derived: str | None = None
        route = ""
        named = {n: len(v) for n, v in hits.items()}
        if named["heldout"] and not named["corpus_dev"]:
            derived, route = "heldout", f"{named['heldout']} held-out block names, 0 development"
        elif named["corpus_dev"] and not named["heldout"]:
            derived, route = "corpus_dev", f"{named['corpus_dev']} development block names, 0 held-out"
        elif named["heldout"] and named["corpus_dev"]:
            derived, route = None, "block names from both corpora -- undecided"
        else:
            derived, route = _nearest_by_shape(art, scales)
            if derived is None:
                derived, route = _by_exclusion(path, art)
            route = route or "no block names, no counts, no census twin -- undecided"

        rows.append({
            "artifact": path.name,
            "label": label,
            "label_in_sample": art.get("in_sample"),
            "derived": derived,
            "route": route,
            "block_names": named,
            "mislabelled": bool(derived and label and derived != label),
            "unlabelled": label is None,
        })

    mislabelled = [r["artifact"] for r in rows if r["mislabelled"]]
    undecided = [r["artifact"] for r in rows if r["derived"] is None]
    return {
        "generated": "2026-08-29",
        "task": "V provenance",
        "corpora_overlap": overlap,
        "scales": scales,
        "artifacts": rows,
        "mislabelled": mislabelled,
        "undecided": undecided,
        "all_heldout": all(r["derived"] == "heldout" for r in rows if r["derived"]),
    }


def _print(d: dict) -> None:
    print(f"Provenance of Phase V artifacts -- corpora overlap: "
          f"{d['corpora_overlap'] or 'none (disjoint)'}")
    width = max(len(r["artifact"]) for r in d["artifacts"])
    print(f"  {'artifact':{width}s}  {'label':11s} {'derived':11s} route")
    for r in d["artifacts"]:
        flag = "  <-- MISLABELLED" if r["mislabelled"] else ""
        print(f"  {r['artifact']:{width}s}  {str(r['label']):11s} "
              f"{str(r['derived']):11s} {r['route']}{flag}")
    print()
    print(f"mislabelled: {d['mislabelled'] or 'none'}")
    print(f"undecided:   {d['undecided'] or 'none'}")
    print(f"every decided artifact is held-out: {'yes' if d['all_heldout'] else 'NO'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true",
                    help="re-print the stored audit instead of running it")
    args = ap.parse_args(argv)
    if args.show and ARTIFACT.exists():
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    data = audit()
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
