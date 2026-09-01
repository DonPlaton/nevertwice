# Docs: what do you want to do?

The [README](../README.md) is the thirty-second version. This page is the map: six things people
actually come here to do, and the shortest path to each. Every document in the repository is one
click from here, and `tests/_test_docs_map.py` fails the build if that stops being true.

## Install

| Doc | What it answers |
|---|---|
| [QUICKSTART.md](../QUICKSTART.md) | Zero to recall in five minutes, with the output you should see |
| [CONFIG.md](CONFIG.md) | Every environment variable, its default, and when to touch it |
| [FEATURES.md](FEATURES.md) | What ships today, including the parts the README no longer lists |

## Integrate

| Doc | What it answers |
|---|---|
| [AGENT_CONFIGS.md](AGENT_CONFIGS.md) | The config block to paste, per host: Claude Code, Cursor, Codex CLI, Claude Desktop, Zed - and how to tell whether it took |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Copy-paste setup for Claude Code, Cursor, Codex, MCP, LangChain, LlamaIndex, the watch daemon, and importing the memory another tool already built |
| [SELF_EXTRACTION.md](SELF_EXTRACTION.md) | How an agent writes its own lessons with no extraction model at all |
| [`skills/nevertwice-remember/SKILL.md`](../skills/nevertwice-remember/SKILL.md) | The agent skill definition, for hosts that load skills |
| [`examples/README.md`](../examples/README.md) | Every demo, what it shows, and how to run it on a throwaway store |
| [DEMO.md](DEMO.md) | Recording the tour GIF, frame by frame |

## Operate

| Doc | What it answers |
|---|---|
| [FEATURES.md](FEATURES.md) | Reading the store: `digest`, the conflict ledger, the offline HTML dashboard, the bootstrapper |
| [WEAKNESSES.md](WEAKNESSES.md) | What Nevertwice is bad at - on purpose, and not |
| [SECURITY.md](../SECURITY.md) | Supported versions and how to report a vulnerability |
| [CHANGELOG.md](../CHANGELOG.md) | What changed, when, and why |
| [ROADMAP.md](../ROADMAP.md) | What is promised, with what already exists stated next to each promise |

## Understand

| Doc | What it answers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit: hooks, store, retrieval, active memory |
| [BRAIN_LAYER_DESIGN.md](BRAIN_LAYER_DESIGN.md) | The opt-in entity/relation layer, and why it stays off the hot path |
| [COMPARISON.md](COMPARISON.md) | Mem0 / Zep / Letta / Cognee / LangMem / A-MEM: what each vendor documents, what could actually be run here, and what we did not win |

## Reproduce

| Doc | What it answers |
|---|---|
| [BENCHMARKS.md](BENCHMARKS.md) | Retrieval quality and hot-path speed, with the command for each |
| [`research/README.md`](../research/README.md) | Every study, its verdict, and the negative results we publish rather than bury |
| [`research/BASELINES.md`](../research/BASELINES.md) | The baselines a headline has to clear before it may be published, and which ones have not been run |
| [`research/PREREGISTRATION.md`](../research/PREREGISTRATION.md) | What the confirmatory run will test, fixed before it runs: hypotheses, endpoints, decision rules, and how large a corpus each needs |
| [`research/BLAST_RADIUS_THRESHOLDS.md`](../research/BLAST_RADIUS_THRESHOLDS.md) | What the blast-radius checker has to achieve to survive calibration, and the decision to delete it if it does not - written before the first commit was replayed |
| [`research/BLAST_RADIUS_CALIBRATION.md`](../research/BLAST_RADIUS_CALIBRATION.md) | What 150 commits of real history said about it: the flag rate fell from 69.3% to 6.7% without losing a single dependency finding |
| [`research/BLAST_RADIUS_DECISION.md`](../research/BLAST_RADIUS_DECISION.md) | Whether the checker is worth keeping at all: the precision floor, the `git grep` baseline it has to beat, and the decision to delete it if it does not |
| [`research/BLAST_RADIUS_PRECISION.md`](../research/BLAST_RADIUS_PRECISION.md) | The answer, and it is no: 0 of 24 findings are real, with a taxonomy of what each false positive actually was |
| [`research/EMBED_HELDOUT_BASELINE.md`](../research/EMBED_HELDOUT_BASELINE.md) | The shipped embedding measured on material outside the owner's vault, where its advantage over stock bge-m3 does not survive a paired test |
| [`research/EMBED_M2_THRESHOLD.md`](../research/EMBED_M2_THRESHOLD.md) | What hard-negative mining has to achieve to survive, which axis the threshold may live on, and the decision to delete it if it does not |
| [`research/EMBED_HARD_NEGATIVES.md`](../research/EMBED_HARD_NEGATIVES.md) | The answer: mining repaired the axis that was already at ceiling and moved the one that matters the wrong way, with the reason why |
| [`research/EMBED_M3_THRESHOLD.md`](../research/EMBED_M3_THRESHOLD.md) | What distillation from the cross-encoder has to achieve, why its target is recall@5, and the decision to delete it if it does not |
| [`research/EMBED_DISTILLATION.md`](../research/EMBED_DISTILLATION.md) | The answer: a teacher that beats you on one metric and loses on another is not one you can distil wholesale, and the guard that predicted it |
| [`research/EMBED_M4_THRESHOLD.md`](../research/EMBED_M4_THRESHOLD.md) | What a truncatable vector has to achieve, why the gate protects the FULL vector, and the decision to delete it either way if it does not |
| [`research/EMBED_MATRYOSHKA.md`](../research/EMBED_MATRYOSHKA.md) | The training bought nothing - and the baseline it was measured against showed the shipped model already truncates to a quarter of the index |
| [`research/EMBED_M5_THRESHOLD.md`](../research/EMBED_M5_THRESHOLD.md) | When a swept LoRA rank may replace the shipped model, and why half the task cannot run from committed data |
| [`research/EMBED_CAPACITY.md`](../research/EMBED_CAPACITY.md) | Sixty-four times the parameters buys less than one times - and v1 turns out not to be reproducible from its own recipe |
| [`research/EMBED_M6_THRESHOLD.md`](../research/EMBED_M6_THRESHOLD.md) | What the served model had to prove before its numbers could be quoted as the shipped model's |
| [`research/EMBED_SERVING.md`](../research/EMBED_SERVING.md) | It proved it: the f16 GGUF users run is numerically indistinguishable from the checkpoint everything was measured on |
| [`research/invariants_lab/CORPUS.md`](../research/invariants_lab/CORPUS.md) | The corpus the next round of detectors is measured on: eight public repositories chosen before they were cloned, 435 commits that really did break a caller, and why the previous corpus could not have produced a precision at all |
| [`research/invariants_lab/MUTANTS.md`](../research/invariants_lab/MUTANTS.md) | The positive class that did not exist last time: 495 breakages made by reverting the caller half of a real commit, each one re-confirmed by CPython's own argument binder |
| [`research/invariants_lab/POWER.md`](../research/invariants_lab/POWER.md) | What the corpus can actually see: twenty positives tell a precision of 0.8 from 0.5, the previous run had twenty-four, and three cells were underpowered until the corpus was enlarged to the whole history |
| [`research/invariants_lab/PREREGISTRATION.md`](../research/invariants_lab/PREREGISTRATION.md) | Every threshold, baseline and deletion decision for all six mechanisms, with the sample size each will be judged at and whether the corpus can resolve it - written before any of them ran |
| [`research/invariants_lab/DEFECTS.md`](../research/invariants_lab/DEFECTS.md) | The five false-positive classes the previous run counted and left in the code, and which are now closed - the measurement does not run until none are open |
| [`research/invariants_lab/BLAST_RADIUS_D5.md`](../research/invariants_lab/BLAST_RADIUS_D5.md) | The answer once the corpus had positives in it: recall 0.91 and it beats git grep, but it fires on 28% of commits that broke nothing - deleted, and what a next attempt must change |
| [`research/invariants_lab/INVARIANT_NOTES.md`](../research/invariants_lab/INVARIANT_NOTES.md) | An invariant is a scar's machinery with different provenance and a stricter lifecycle: two false positives and it retires, one finding per diff, and three checkers that ship in the box |
| [`research/invariants_lab/RATCHET_R3.md`](../research/invariants_lab/RATCHET_R3.md) | The second mechanism to fail the same way: it discriminates three-to-one between diffs that got more complex and diffs that did not, and still fires on 36% of everything |
| [`research/invariants_lab/SCALE_X4.md`](../research/invariants_lab/SCALE_X4.md) | The first mechanism to pass its gates, and the reason to distrust half of it: a detector scoped to a declared axis fires on a tenth of commits instead of a third |
| [`research/invariants_lab/DB_AUTHORITY.md`](../research/invariants_lab/DB_AUTHORITY.md) | Fifteen attacks on a destructive-operation boundary, two of which landed: a replay guard keyed on a value the attacker picks is not a guard, and a self-verifying capability is a self-forgeable one |
| [`research/invariants_lab/TOGETHER_T1.md`](../research/invariants_lab/TOGETHER_T1.md) | Everything at once: the seam journal executed on a real migration, and a combined flag rate whose declared remedy removes the only mechanism with non-circular evidence behind it |
| [`research/invariants_lab/README.md`](../research/invariants_lab/README.md) | Six mechanisms measured properly: two passed, four did not, and the two that read the whole diff are the two that were deleted - scope buys silence, accuracy does not |
| [`research/invariants_lab/COLDSTART_T3.md`](../research/invariants_lab/COLDSTART_T3.md) | The cold-start axis, and why it failed: a preconfigured invariant can only help where the model is wrong, and portable knowledge is what models already have |
| [`research/invariants_lab/CORPORA.md`](../research/invariants_lab/CORPORA.md) | The eight repositories become a frozen development set and stop being evidence, and the held-out corpus gets two locks so it cannot quietly become a second in-sample one |
| [`research/invariants_lab/ABSTENTION_F1.md`](../research/invariants_lab/ABSTENTION_F1.md) | The untried lever: emitting only what the checker can decide takes the flag rate from 28% to 1%, costs a third of the recall, and doubles the margin over a regex |
| [`research/invariants_lab/AXES_F3.md`](../research/invariants_lab/AXES_F3.md) | The ratchet's three unaudited axes against instruments nobody here wrote, and the defects that turned up the moment something else was asked |
| [`research/invariants_lab/SURFACE_F2.md`](../research/invariants_lab/SURFACE_F2.md) | Why scoping cannot do what abstention did: the declared surface this task wanted is not present in found history, and the conventions that are cost recall in proportion to what they remove |
| [`research/invariants_lab/QUADRATIC_F4.md`](../research/invariants_lab/QUADRATIC_F4.md) | Testing the scale detector against quadratic fixes real maintainers wrote and named, under a declaration generated blind from the parent commit |
| [`research/invariants_lab/COLDSTART_F5.md`](../research/invariants_lab/COLDSTART_F5.md) | The cold-start experiment that was never run, run: a project-specific declared axis escapes T3's portability argument and still does not help, because the detector cannot see the shape the model writes |
| [`research/invariants_lab/PREREGISTRATION-SHIP.md`](../research/invariants_lab/PREREGISTRATION-SHIP.md) | Every threshold for the out-of-sample measurement and the end-to-end gate, with its harm bound, fixed before a single held-out repository was cloned |
| [`research/invariants_lab/HELDOUT_H2.md`](../research/invariants_lab/HELDOUT_H2.md) | Thirty repositories across six domains, why each domain, and what every block is predicted to show - committed before the first clone |
| [`research/invariants_lab/HELDOUT_H3_BLOCKED.md`](../research/invariants_lab/HELDOUT_H3_BLOCKED.md) | Why the held-out corpus does not exist: DNS resolves nothing on this machine, and what that makes unevaluable rather than failed |
| [`research/invariants_lab/ENDTOEND_E.md`](../research/invariants_lab/ENDTOEND_E.md) | The gate that decides shipping: does an agent fix a stale caller better with the mechanism than without, and what does a false flag cost when it fires on correct code |
| [`research/invariants_lab/VERDICT_G1.md`](../research/invariants_lab/VERDICT_G1.md) | NO-GO, and every reason for it: one gate unevaluable, one inconclusive, and what would change each |
| [`research/invariants_lab/SHIP_G4.md`](../research/invariants_lab/SHIP_G4.md) | Finishing four half-built mechanisms: abstention takes a flag rate from 28% to 1% and doubles the margin over a regex, and it is available only to a checker that cannot decide |
| [`research/invariants_lab/PROVENANCE_V.md`](../research/invariants_lab/PROVENANCE_V.md) | Three artifacts call themselves in-sample and are not: which corpus each Phase V measurement really ran on, derived from the data because the labels are hardcoded |
| [`research/invariants_lab/HELDOUT_V.md`](../research/invariants_lab/HELDOUT_V.md) | Twenty-seven repositories the code never saw: recall reproduces to three decimals, every flag rate gets worse, one mechanism survives its gates - and repository size, not domain, is what governs precision |
| [`research/invariants_lab/ENDTOEND_HELDOUT.md`](../research/invariants_lab/ENDTOEND_HELDOUT.md) | The gate that decides shipping, re-run out of sample: 87 usable paired trials, five fixed against two broken, and a NO-GO that is now a result rather than a missing measurement |
| [`research/evidence_manifest.json`](../research/evidence_manifest.json) | Every published number, with its dataset, sample size, model, command, raw file and caveat |
| [`research/data/README.md`](../research/data/README.md) | Fetching the LongMemEval dataset the benchmarks need |

## Contribute

| Doc | What it answers |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | The test command, the support matrix, and what a good change looks like |
| [starter-issues/](starter-issues/01-latency-interval-not-point.md) | Three pieces of work described in full before they were filed: [a latency published as a point when it is a range](starter-issues/01-latency-interval-not-point.md), [an assumption under the one live comparative claim](starter-issues/02-linter-arm-in-the-simulation.md), and [note labels stuck in one language](starter-issues/03-note-labels-i18n.md) |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | How we behave with each other |
| [`examples/sample-store/README.md`](../examples/sample-store/README.md) | The fixture vault the demos and tests read - and the four notes in it, so you can see the on-disk format without installing anything: [mistake](../examples/sample-store/Mistakes/2026-01-10-demo_api-mistake-n-plus-one-queries.md) · [pattern](../examples/sample-store/Patterns/2026-01-11-demo_api-pattern-assert-query-count.md) · [decision](../examples/sample-store/Decisions/2026-01-12-demo_api-decision-adopt-cursor-pagination.md) · [project card](../examples/sample-store/Context/demo_api.md) |
