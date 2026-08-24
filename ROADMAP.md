# Roadmap

Where this is going, honestly. No dates: it ships when it is measured and green.
Done items move to [CHANGELOG.md](CHANGELOG.md).

Every open item below states **Today:** what already exists, so the roadmap cannot quietly
keep promising something that shipped months ago. `tests/_test_roadmap.py` fails if a
shipped feature reappears as an open promise.

## Blocked on the maintainer

- **PyPI release of 2.3.x.** PyPI still serves 2.2.1 while this repository declares 2.3.0,
  so `pip install nevertwice` does not get what the README documents.
  **Today:** the whole release path is built and rehearsed - `release.yml` builds once,
  installs the artifact into a clean environment on three operating systems, checks every
  console entry point, emits an SBOM and checksums, and attests provenance. What remains is
  not code: a `pypi` environment on this repository and a Trusted Publishing registration
  for the `nevertwice` project, which only the maintainer's PyPI account can create. The
  version also needs bumping - `tools/check_version.py` prints that reminder on every run
  while `v2.3.0` is tagged behind `master`.

## Near term

- **Baselines the headline numbers have never been run against.** Three arms named in
  [`research/BASELINES.md`](research/BASELINES.md) do not exist yet: a hand-written
  `AGENTS.md` carrying the same rule as a guard, an LLM session summary injected at an equal
  token budget, and the linter or test that already catches the same class of mistake.
  **Today:** the gaps are published rather than hidden - the baseline matrix marks each one
  `not_compared`, and a test fails if a headline quietly drops one. The repeat-error result
  is the one that needs them most: until the `AGENTS.md` arm runs, it does not distinguish
  *this system works* from *writing the rule down works*.
- **Universal guard pack default-on decision.** The pack (11 high-precision pitfalls, 0
  tokens until they fire) is opt-in behind `NEVERTWICE_GUARD_PACK`.
  **Today:** the pack ships and is seeded on request; what is missing is a measured
  false-positive rate from real use, which is what should decide the default.
- **Structural guard signatures.** Match the *shape* of a mistake - identifiers and literals
  stripped - so a repeat is caught even when the names differ.
  **Today:** the offline generator recognises well-known anti-patterns by keyword and emits
  a precise regex for the buggy construct, which covers the common pitfalls without a model.
  It does not generalise to project-specific mistakes, which is what shape-matching would add.
- **Order-aware anticipation.** N-gram trajectory features on top of the IDF coverage score,
  so the order of steps carries signal and not just their presence.
  **Today:** anticipation scores IDF-weighted coverage, with an optional embedding blend that
  abstains whenever the cached vectors are from a different embedding space. Both are
  order-blind.
- **Latency measurements that survive the run.** A CI job that catches hot-path
  regressions, on top of a result file the benchmark actually writes.
  **Today:** `research/latency_bench.py` prints its numbers and saves nothing, so the
  hot-path figures in the README and [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) - including
  the 85 ms on the banner - cannot be traced to a committed artifact. Saving that file is
  the prerequisite for the rest.

## Exploring

- **More benchmark protocols.** BEAM is a candidate, added only if it runs on the same open,
  local, reproducible stand as everything else. LoCoMo is **not** a candidate: plain BM25
  scores about 94% on it, so it no longer separates memory systems.
  **Today:** LongMemEval-oracle retrieval and a live agent validation are published, with
  every figure registered in [`research/evidence_manifest.json`](research/evidence_manifest.json).
- **Registering the study write-ups.** Give the 22 study pages under `research/` the same
  backing the front-page documents already have, or decide they should stay self-evidencing
  beside their own `.json` files.
  **Today:** every number printed in the README and `docs/BENCHMARKS.md` resolves through
  [`research/evidence_manifest.json`](research/evidence_manifest.json); the study pages do
  not, and their unregistered numbers are counted and ratcheted so that surface can shrink
  but never grow.

## Not planned

- A server, a database, an account, telemetry, or a required dependency. The core
  stays plain files plus the standard library, and everything the memory does stays
  inspectable on disk.
