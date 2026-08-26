# Baseline gates: what a headline has to beat before it is published

A memory system is easy to make look good. Pick a metric it was designed for, compare it
against nothing in particular, and publish the number. The result is not wrong so much as
uninformative: the reader cannot tell whether the machinery caused the improvement or
whether a much simpler thing would have done the same.

This file is the gate. It names the baselines a headline must clear, the conditions the
comparison must hold fixed, and — the part that costs something — the current verdict for
every headline this project publishes, including the ones that have not been tested against
a baseline yet and the one that loses.

The tables below are generated from `evidence_manifest.json` by
[`tools/render_claims.py`](../tools/render_claims.py). Editing them by hand fails CI.

## The rule

<!-- claims:baselines-conditions -->
A headline is publishable only when it clears every applicable baseline below under matched conditions. A baseline that was not run is recorded as `not_compared`, never omitted: an unrun baseline is the most likely explanation a reader will reach for.

1. equal reader model - the same LLM answers in both arms
2. equal context-token budget - neither arm is allowed more room
3. equal latency tier - a baseline is not handicapped by being run slower
4. matched false-positive rate - an intervention that fires more often is not better, it is louder; compare at the same FP rate or report the full curve

A measured verdict (beats / ties / loses_to) must name where the comparison lives: a raw result file, or the claim ids of the two arms. A verdict without evidence is an assertion, and flipping `not_compared` to `beats` must therefore cost something.
<!-- /claims:baselines-conditions -->

## The baselines

<!-- claims:baselines-registry -->
| id | baseline | what it is | why it is the test | how to run it |
|---|---|---|---|---|
| `no_memory` | no memory | the same agent, same task, with the memory system switched off | the floor. Any memory that cannot beat its own absence is overhead. | run the harness with the memory arm disabled |
| `full_history` | full-history injection | paste the entire accumulated history into the context instead of retrieving | the dumb baseline that often wins when the history is small; it is what a user does by hand before installing anything. | python research/token_ab.py (the `net vs the full history` column) |
| `lexical_recall` | lexical recall (BM25) | term-overlap retrieval with no embedder and no model | zero-dependency, zero-latency, and repeatedly close to far more complicated systems. LoCoMo was discredited partly because BM25 nearly won it. | python research/longmem_eval.py (the `lexical` row) |
| `curated_agents_md` | a curated AGENTS.md | one hand-written instructions file that a person maintains | the honest competitor for most of what agent memory claims. If a human writing five lines gets the same prevention, the machinery is not earning its place. | python research/cheap_baselines.py (the `curated_agents_md` row) - **built, F2** |
| `llm_session_summary` | an LLM session summary | ask the model to summarise the session and inject that summary next time | the cheapest possible 'memory', available in one prompt with no system at all | python research/cheap_baselines.py - **arm built, run GATED**: it runs against a deterministic extractive summariser, never against a model, and is reported as `session_summary_extractive` |
| `linter_or_test` | the relevant linter or test | the existing static check that already catches the same class of mistake | several guard-shaped pitfalls (f-string SQL, bare except, missing close) are caught by ruff, bandit or a unit test. A guard that duplicates a linter is not memory, it is a worse linter. | python research/cheap_baselines.py (the `linter_or_test` row) - **built, F2**, as an oracle upper bound on static analysis |
| `curated_haystack` | an already-curated small haystack *(additional)* | the handful of sessions that actually contain the answer, pasted whole | stricter than full-history injection at small scale, and this project already runs it. Recording only the six the policy names would hide the harder test. | python research/token_ab.py (the `net vs a curated small haystack` column) |
<!-- /claims:baselines-registry -->

## Where every headline currently stands

`not compared` is not a neutral state. It means the most obvious alternative explanation
for the number has not been ruled out, and a reader is entitled to assume it might hold.

<!-- claims:baselines-matrix -->
| headline claim | `no_memory` | `full_history` | `lexical_recall` | `curated_agents_md` | `llm_session_summary` | `linter_or_test` | `curated_haystack` |
|---|---|---|---|---|---|---|---|
| `longitudinal.active_vs_inject_token_ratio` | **beats** | **beats** | n/a | not compared | not compared | not compared | n/a |
<!-- /claims:baselines-matrix -->

<!-- claims:baselines-summary -->
1 headline claims x 7 baselines = 7 pairs.

| verdict | count | what it means |
|---|---|---|
| `beats` | 2 | measured, under matched conditions, and the claim wins |
| `ties` | 0 | measured and within the interval of the baseline |
| `loses_to` | 0 | measured and the baseline wins - the claim must be narrowed or dropped |
| `not_compared` | 3 | not measured yet; the claim is provisional against this baseline |
| `not_applicable` | 2 | the baseline cannot be constructed for this metric; a reason is required |
<!-- /claims:baselines-summary -->

## Reading the current state honestly

Three things in that matrix are worth saying in plain words rather than leaving in a cell.

**The written-down-rule comparison has now been run, and the rule loses.** A hand-written
`AGENTS.md` of the same provenance, charged its full length on every episode rather than only
when it helps, is beaten decisively on the F1 corpus — the always-present file costs an order
of magnitude more context per episode and prevents far less. That settles the question this
file called its single most important missing comparison, on one corpus.

**The linter comparison has been run too, and it does not lose.** Built as an *oracle upper
bound* — it never cries wolf, because a linter reads code rather than intentions, and it
catches every instance of a class it covers — it ties the memory arm at zero false alarms,
at a fraction of the latency. For the failure classes an existing tool already covers, a guard
is a worse linter. What the memory system can still claim has to be about the classes no
linter covers, and that is now a written constraint rather than an open question.

**And the cheapest arm of all is not yet beaten.** Raw lexical recall is not distinguished from
the memory arm on this corpus, and at zero false alarms it is nominally ahead. See
`research/cheap_baselines.json` for the full verdict, including which differences the sample
size cannot resolve.

**One headline already fails the gate.** The live two-arm run cuts input tokens sharply,
and crude answer-match falls with them. The rule above requires matched accuracy, and this
comparison does not have it. As a statement about tokens it holds; as a statement about
memory under matched conditions it does not pass, and it is marked `LOSES` rather than
quietly reported as a win.

## What this file obliges

- A new headline arrives with a verdict for every baseline, or it is not a headline.
- `not_applicable` requires a reason, and the reason is checked. "The baseline does not fit"
  is a claim about the metric, and a reader may disagree with it.
- A baseline that is run and lost is not deleted. It narrows the claim in writing, per the
  project's standing rule that a mechanism which loses its baseline is dropped rather than
  kept for completeness.
- Building the three unbuilt arms was task **F2**. `curated_agents_md` and `linter_or_test`
  are built and run; `llm_session_summary` is built and its run is gated on a model the owner
  must provide, and it is reported as not-run rather than quietly folded into the stub that
  stands in for it.

`tests/_test_baselines.py` enforces all of it.
