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

- **A public page still shows withdrawn numbers.** `docs/COMPARISON.md` renders the July
  head-to-head — Nevertwice ahead of Mem0, LangMem and A-MEM on every column — inside a
  generated region with no withdrawal banner, while the evidence register marks all sixteen of
  those figures withdrawn. A reader who clones the repository today sees retracted results
  presented as current.
  **Today:** the withdrawal itself is recorded honestly, with its reason, for 128 of 154 claims;
  `docs/BENCHMARKS.md` carries the banner on every affected section. What is missing is that
  `docs/COMPARISON.md` is registered `backlog` rather than `governed`, so the test that forbids
  citing a withdrawn claim does not look at it, and the banner never reached the page.
- **A head-to-head that survives its own audit.** The July stand ran Nevertwice, Mem0, LangMem
  and A-MEM on the same 500 questions with the same local embedder, and Nevertwice led every
  column. It was withdrawn because the corpus is third-party, uncommitted and unhashed, so nobody
  — including us — can reproduce or even pin it.
  **Today:** the harness exists and runs with one command, the adapters for Zep and Cognee record
  their blocker instead of a fabricated number, and the withdrawal states its reason. What is
  missing is a committed, content-hashed dataset the run can be pinned to.
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

### The two that close the gap to the field — after everything above

- **A number on a dataset we did not choose, that still separates systems.** Winning our own
  stand is the same circularity as scoring a detector against positives its author generated,
  and this project has now been burned by that shape twice. **LoCoMo is not a candidate** and the
  reason is unchanged: plain BM25 scores about 94% on it, so it no longer separates memory
  systems, and a vendor headline of 92.5 there says little about one.
  BEAM is the candidate, or LongMemEval outside the oracle setting, on the same open local stand,
  with the corpus committed and content-hashed so the result cannot be withdrawn for the reason
  the last one was.
  **Today:** every external retrieval figure this project has published is withdrawn, so the
  honest public position is a strong design with no reproducible external number. The stand,
  the adapters and the registration machinery all exist; the dataset and the run do not.
- **A benchmark for supersession, which nobody in the field has.** LoCoMo, LongMemEval and BEAM
  all measure whether a system *recalls* a fact. None measures whether it returns a fact that has
  since been **retracted** - and staleness is a real failure mode, because an agent acting on a
  withdrawn fact writes wrong code. The protocol is small: assert *A*, assert *not-A*, query, and
  score whether the superseded fact comes back and at what rank.
  **Today:** a five-fact probe against Mem0 2.0.19 exists in the polygon and shows the retracted
  fact returned at **rank 1**, ahead of both of its replacements - which follows from that
  system's published ADD-only design rather than from a defect. That is a sketch, not a
  benchmark: it has no dataset, no baselines, no intervals, and Nevertwice's own side of it has
  never been measured at all.

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
  local, reproducible stand as everything else. LoCoMo is **not** a candidate: plain BM25
  scores about 94% on it, so it no longer separates memory systems.
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
