# Roadmap

Where this is going, honestly. No dates: it ships when it is measured and green.
Done items move to [CHANGELOG.md](CHANGELOG.md).

## Near term

- **PyPI release** so install is `pip install nevertwice` instead of a git URL.
- **Universal guard pack default-on decision.** The pack (11 high-precision pitfalls,
  0 tokens until they fire) is opt-in today; measuring false-positive rates in real
  use decides whether it becomes the default.
- **Structural guard signatures.** Match the shape of a mistake (identifiers and
  literals stripped) instead of the literal tokens; lifts repeat-catch recall with
  zero model cost.
- **Order-aware anticipation.** N-gram trajectory features on top of the IDF
  coverage score, still lexical, still silent below threshold.

## Exploring

- **More benchmark protocols.** LongMemEval (oracle retrieval) and a live agent
  validation are published; LoCoMo and BEAM protocols are candidates, added only if
  they can run on the same open, local, reproducible stand as everything else.
- **Latency in CI.** The speed table in docs/BENCHMARKS.md is measured by a script;
  a CI job could catch hot-path regressions automatically.

## Not planned

- A server, a database, an account, telemetry, or a required dependency. The core
  stays plain files plus the standard library, and everything the memory does stays
  inspectable on disk.
