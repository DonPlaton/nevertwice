"""What counts as a *document* - shared by the two suites that govern documentation.

`_test_docs_map` requires every tracked document to be reachable from the README, and
`_test_evidence_manifest` requires every tracked document to carry a governance mode. Both
enumerate `git ls-files *.md`, and both were right to, until recorded test fixtures arrived.

A fixture under `tests/fixtures/` is **recorded input data that happens to be Markdown** - a
Claude auto-memory note, a bullet list an importer has to parse. It is not documentation: it is
not written to be read for guidance, linking it from the README would be noise, and registering
each one would add an entry per fixture forever while teaching nobody anything.

The carve-out is deliberately narrow, and stated here once rather than duplicated into two
suites that would drift:

* only under `tests/fixtures/`, so it cannot quietly cover anything published;
* a `README.md` in a fixture directory is **still a document** - it is prose a contributor
  reads before adding a source, and it stays reachable and registered like any other.
"""
from __future__ import annotations

FIXTURE_ROOT = "tests/fixtures/"


def is_recorded_fixture(path: str) -> bool:
    """True for fixture *data*; False for a fixture's own README, which is real prose."""
    rel = str(path).replace("\\", "/")
    return rel.startswith(FIXTURE_ROOT) and not rel.endswith("/README.md")


def documents(paths) -> list[str]:
    """The tracked Markdown that the documentation contracts actually govern."""
    return sorted(p for p in paths if not is_recorded_fixture(p))
