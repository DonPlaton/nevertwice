# Roadmap

Where this is going, honestly. No dates: it ships when it is measured and green.
Done items move to [CHANGELOG.md](CHANGELOG.md).

Every open item below states **Today:** what already exists, so the roadmap cannot quietly
keep promising something that shipped months ago. `tests/_test_roadmap.py` fails if a
shipped feature reappears as an open promise.

## Blocked on the maintainer

- **PyPI release of 2.4.0.** PyPI still serves 2.2.1, so `pip install nevertwice` does not
  get what the README documents. It will skip 2.3.0 entirely: that version was tagged here on
  2026-08-18, never published, and its code has been superseded a hundred commits over.
  **Today:** the version is bumped and the whole release path is built and rehearsed -
  `release.yml` builds once, installs the artifact into a clean environment on three operating
  systems, checks every console entry point, emits an SBOM and checksums, and attests
  provenance. What remains is not code: a `pypi` environment on this repository and a
  Trusted Publishing registration for the `nevertwice` project, which only the maintainer's
  PyPI account can create.

## Near term

**Order is deliberate, decided 2026-08-30.** Everything already started is finished and polished
to work out of the box for a new user first; the branch ships only if the polygon result is
positive; the two benchmark items at the end of this section are what close the gap to the field
and are not started before the rest is done.

- **Governing `docs/COMPARISON.md`.** The page is registered `backlog`, so the number-coverage
  check does not look at it; governing it means every figure on the page - including the vendor
  matrix and the star counts, which are third-party facts rather than measurements - has to
  resolve to a registered claim or an external citation.
  **Today:** the reader-facing half is done and generalised. Thirty-six registered pages printed
  a withdrawn figure with nothing saying so; `tools/stamp_withdrawn.py` stamps the banner and
  `tests/_test_withdrawn_pages.py` fails if a page that needs one loses it. Nothing was deleted,
  because deleting a retracted result is how a project loses the memory of having been wrong.
- **A second external dataset, and the non-oracle pool for every arm.** LongMemEval is pinned
  and re-run, and one benchmark is one benchmark. BEAM is the other candidate named here, on the
  same open local stand. **LoCoMo is not a candidate as a headline**, and the reason is now
  narrower than it was: it was run (`research/LOCOMO.md`) and on the *retrieval* axis it
  separates systems fine - the term-overlap floor scores 0.499 at R@5, not 0.94. The saturation
  concern is about *answer accuracy* with a judge model, which is a different quantity and one
  this project does not measure.
  **Today:** the retrieval head-to-head is closed and reproducible. `research/corpus_pin.py`
  holds the corpus sha256, both harnesses verify it before reading a byte and stamp it into the
  result, and the re-run reproduces every 2026-07 figure to three decimals across all four
  systems (`research/EXTERNAL_RETRIEVAL.md`). The non-oracle pool is done too - 19,206
  retrievable sessions against the oracle's 940, where the shipped fusion reads R@5 0.452 - and
  running it found that the harness's own inertness check had been passing for the wrong reason.
  What is missing there is the competitor arms, which cost about twenty-one times their ingest
  rather than any new machinery.
- **The invariants track: ship it or delete it, on the end-to-end result.** G-A and G-B pass;
  `blast_radius` under `decidable-only` cleared all three out-of-sample gates on 27 repositories
  it was never built against, and the ratchet, `scale`'s static half and the union failed theirs
  and are deleted.
  **Today:** answered — G-C was re-run out of sample at 150 benefit and 150 harm trials and is
  **not met**: 5 fixed against 2 broken on 87 usable pairs, p = 0.453, harm passing at 0.013. The
  verdict is NO-GO on evidence rather than on a missing measurement, so nothing ships;
  `nevertwice/invariants/` is empty and a test pins that. What remains is a rerun on an unloaded
  GPU — 92 of 300 attempts timed out and a timeout correlates with larger inputs, so the usable
  pairs are biased toward smaller files.
- **The cheap baselines have been run, on the wrong stand.** The comparison a reader wants is
  against the *headline* - the repeat-error result from the live validation - and what exists is
  the same comparison on the F2 matched-condition corpus, which is a different experiment with a
  different corpus and no model in the loop.
  **Today:** the arms exist and their numbers are registered rather than promised. Held to one
  false-positive rate of 0.2 on 60 episodes: guards catch **0.567**, a hand-written always-present
  `AGENTS.md` catches **0.200** at fifteen times the token cost (203.6 per episode against 13.93,
  and paid on every episode rather than when something fires), and the relevant linter or test -
  scored generously in the baseline's favour - catches **0.467**. What remains is porting the
  `AGENTS.md` arm onto the live-validation stand, where the headline actually lives; until then
  that headline still does not distinguish *this system works* from *writing the rule down works*
  under a real model.
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
- **A CI job that catches a hot-path regression.** Two paths got slower this cycle -
  UserPromptSubmit 88 to 95 ms, SessionStart 85 to 91 ms - and nothing noticed until the claims
  were re-measured by hand for an unrelated reason.
  **Today:** `research/latency_bench.py --save` writes `research/latency_bench.json` with a
  minimum, median and maximum per path, and the four published figures point into it. What is
  missing is the job that runs it and fails on a regression, which is harder than it sounds: the
  same statistic on the same unchanged tree moves by a third between sessions on this machine, so
  a naive threshold would fail on the weather.

### The two that close the gap to the field — after everything above

- **A number on a dataset we did not choose, that still separates systems.** Winning our own
  stand is the same circularity as scoring a detector against positives its author generated, and
  this project has been burned by that shape twice. **LoCoMo is not a candidate as a headline**:
  it has been run on the retrieval axis, where it does separate systems, but the 94%-for-BM25
  concern that excluded it is about answer accuracy with a judge model, and nothing here measures
  that. BEAM is the remaining candidate for a headline, on the same open local stand.
  **Today:** LongMemEval is done, on a corpus hash-pinned in `research/corpus_pin.py` that the
  harness verifies before reading a byte - the property whose absence took the 2026-07 figures
  down. Four systems, one pool, one embedder, one scoring function, and every figure reproduces
  the withdrawn run to three decimals (`research/EXTERNAL_RETRIEVAL.md`). It is one dataset, and
  the 19,829-session non-oracle pool has been embedded but not yet run for the competitor arms.
- **The supersession benchmark's control arm is underpowered.** Over-retraction is the
  design's own worst failure mode, silently retiring a fact that is still true, and nothing
  downstream can catch it the way a stale answer can be caught. It is measured on 20 controls,
  so an observed 0.05 carries a Wilson upper bound of 0.236. Raised by a review of the
  benchmark rather than by the benchmark itself.
  **Today:** the interval is published rather than the bare rate, and the floor sits in the
  same place, so the *comparison* holds even though the absolute number is loose. What is
  missing is power: about 130 controls would put the upper bound under 0.10. It is a dataset
  change, so it re-runs every arm.
- **The supersession benchmark's remaining shape: a fact removed with nothing to replace it.**
  The one stale result left is there, and it is the shape built to be hardest - there is no new
  note for retrieval to rank above the old one, so ranking cannot help and only retirement can.
  **Today:** the benchmark exists, is committed and content-hashed, and separates systems -
  Nevertwice 0.042 stale against Mem0's 0.917 and an append-only file's 0.950, with Mem0 and the
  file statistically indistinguishable from each other (`research/SUPERSESSION.md`). Pooled over
  two runs of the same commit, `narrowed` is clean and the rest are one or two cases each. An
  earlier run had four failures in `approach_abandoned`; they were an artifact of the extractor
  answering in the wrong language, which gave the replacement note a title the slug-keyed match
  could not find.

## Architectural erosion — after everything in Near term

Recorded 2026-09-02 from a developer's account of eighteen months building a large project
with an AI assistant. The failure he describes is not bad code: *"the problem is not that the
AI's code is bad from scratch. With small daily changes hardcode accumulates, unneeded modules
appear, DRY is broken completely."* Two symptoms recur — *"I changed it, but file X had a
hardcoded value so everything broke"* and *"module X pulled in module Y, though they are
supposed to be fully independent."* He ends: **"there are only two moments when you can turn
back — when you stop understanding your own code, or when everything collapses."**

Both are too late, and that is the finding. Every invariant this project has built is
**per-diff**: it answers *is this change bad?* None answers *where is the integral heading over
ninety days?*

Three things this account refutes about work already done, kept here so a next attempt does not
rebuild them:

- **The blast-radius checker would not have saved that project.** Of the four symptoms it
  addresses one, partially. A hardcoded constant is not a contract change; DRY violations pass;
  copy-paste passes and each copy is individually clean.
- **A per-file complexity ratchet cannot see duplication**, because duplication is a
  *cross-file* property. Three copies of a function raise no single file's complexity.
- **A ratchet whose baseline moves after every accepted diff is theatre.** It permits unbounded
  degradation over a thousand commits, each one individually not a regression. The baseline has
  to be sticky and monotone.

- **Module boundary contracts.** A vault note declares which modules may import which — the
  human states the *intention*, the machine holds the line. `ast` over the import graph,
  standard library, small. This has the shape Phase V measured as the only one that survives:
  it is silent until a project declares an axis, exactly like the scale assertions that passed
  their gates while every whole-repository detector failed on flag rate.
  **Today:** nothing declares module boundaries; `networkx` import-cycle detection exists in the
  lab from R1 but answers a different question, and `nevertwice/invariants/` is empty.
- **Cross-file duplication and constant sprawl.** Normalised AST-subtree hashing catches
  copy-paste through renamed variables; a repeated-literal counter catches constants spreading
  across files. Standard library.
  **Today:** nothing measures either. **The open risk is silence, not detection** — real
  repositories are full of duplication, and Phase V deleted two mechanisms that were
  substantially right and could not stop talking. This one must be scoped to a declaration
  before it is built, or it will fail the same gate.
- **A structural-health trend in `digest`.** The third turning point the account says does not
  exist: not *is this diff bad* but *what has the integral done this quarter*. Cheapest item
  here and it aims straight at the gap.
  **Today:** `digest --days 7` and the offline dashboard report activity, not structural drift.
- **Anticipation on the derivative.** Score by the trend of a structural metric rather than by
  similarity to a past break.
  **Today:** anticipation scores IDF-weighted coverage of a past mistake, so it can only fire on
  something that already happened once.

**Why the ordering changed.** The database authority boundary moves below these: it addresses a
rare catastrophic event, and this is daily erosion. The account is eighteen months of field
evidence that the quiet integral is what kills a project, not the dramatic single failure.

**Where the account's prescription is wrong, and it matters.** It concludes "do the key parts by
hand". That does not follow from its own diagnosis: the human dropped out *before* the collapse,
for the same reason — no signal. Nobody notices 0.5% degradation per commit, human or machine.
Manual work relocates the integral problem rather than solving it. What *is* right, and is the
real boundary between doing and delegating: **"X and Y must be independent" is an intention, not
a property.** It exists nowhere in the code and cannot be derived from it. It has to be
declared — which is precisely what the first item above builds.

## Exploring

- **More benchmark protocols.** BEAM is a candidate, added only if it runs on the same open,
  local, reproducible stand as everything else. LoCoMo is **not** a candidate as a headline:
  measured on the retrieval axis it separates systems (`research/LOCOMO.md`), while the
  saturation concern that excluded it is about judge-scored answer accuracy, which is a
  different axis and one nothing here measures.
  **Today:** the LongMemEval-oracle retrieval figures and the head-to-head that stood on the same
  corpus were **withdrawn in 2026-08** - the dataset is third-party, uncommitted and unhashed, so
  the run cannot be reproduced or pinned. The live agent validation stands. Every figure, live or
  withdrawn, is registered in
  [`research/evidence_manifest.json`](research/evidence_manifest.json) with its reason, which is
  why the withdrawal is visible at all.
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
