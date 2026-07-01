# Active Memory: memory that earns its tokens

> Research vision (2026-07). The thesis behind Anamnesis v2 and the next paper. Status:
> design + first system (the Popperian guard layer) under construction; the longitudinal
> benchmark that keeps it honest is built alongside.

## 1. The axis everyone optimizes is already lost

Every "memory for agents" system — Mem0, Zep, Letta, Cognee, memanto, and Anamnesis v1 —
competes on the same axis: **given a fixed corpus of past chats, retrieve and answer
factual-recall questions** (LongMemEval, LoCoMo). We ran that axis to its end and found the
uncomfortable truth, in our own data:

> On LongMemEval-oracle, answer-accuracy climbs **0.614 → 0.678 → 0.748 → 0.788** as we
> upgrade *only the reader model*, with the memory held byte-for-byte fixed. (`QA_ACCURACY.md`)

The memory is not the variable. The **reader LLM** is. A vendor's headline 0.898 is a
stronger reader plus a closed engine, not a better memory — and next year's frontier model
makes everyone's number rise together. **Retrieval-accuracy is a commoditizing,
reader-bound axis.** Optimizing it harder is the "slightly-better-than" trap: you win a
race whose finish line the LLM keeps moving.

So we stop. We do not try to out-retrieve a closed engine on a dead axis.

## 2. The reframe: improvement per token

The question that actually matters for an *agent's* memory is not "can you recall a fact"
but:

> **Does the agent, because of memory, get measurably better — and does it cost less than it
> saves?**

Two quantities, jointly:

- **Task lift** — success / quality / speed on a *series of related tasks*, as a function of
  how much the agent has already done. A memory that works makes task N+1 cheaper than task N.
- **Token cost** — what memory spends to produce that lift. A memory that injects 600 tokens
  every turn to lift task success by 1% is a *net loss*. We measured exactly this in v1
  (`token_ab.py`): always-inject memory is net-negative against a small curated context.

The metric is **improvement per token**. This is the axis no competitor measures, because on
it the whole field's design — *always-inject a wall of recalled text* — is a liability, not a
feature.

## 3. The unifying primitive: the token-budgeted intervention

v1 memory is a thing you **read**: it taxes every turn with injected text. v2 memory is a
thing that **acts**, and is **silent until it has something worth saying**. The unit is the
**intervention**:

```
intervention = {
  trigger:    when this fires (a code pattern, a trajectory match, a query)
  action:     what it does (run a guard, emit one warning, answer a counterfactual)
  cost:       tokens this would spend in context (0 for executable actions)
  value:      expected change in the outcome if it fires
  feedback:   did it help / misfire  →  updates confidence
}
```

Memory ranks candidate interventions by **value per token** and fires only those above a
threshold. The default token cost of memory drops from "600 every turn" to **"0 until an
intervention earns its place."** Token economy is no longer a constraint we defend — it is
the selection pressure that defines the system.

The four research axes are all interventions of different *action* type:

| axis | trigger | action | context-token cost |
|---|---|---|---|
| **A — controller** | a proposed action/diff matches a known failure pattern | run an executable **guard** (assert / lint / test) | **0** until it fires, then ~1 line |
| **B — anticipatory** | current trajectory resembles a past failure trajectory | emit **one** precise pre-action warning | spend ∝ predicted risk, not always-on |
| **C — causal** | the agent asks a counterfactual | answer from a compact **causal model** | one answer, not an episode dump |
| **D — benchmark** | — | *measures* lift-per-token across a task series | defines the axis |

A/B/C are the system; **D is the bench that keeps them honest** — true to this project's
ethos (`research/README.md`): we measure the clever idea and cut it if it loses.

## 4. Axis A and the ossification problem — Popperian memory

Memory-as-controller has an obvious failure mode, and it is the right one to fear: **a guard
that is wrong, or outdated, traps the agent in a constraint reality no longer warrants.** A
memory that can *forbid* actions could ossify the agent. The design answer is that **no guard
is a law; every guard is a falsifiable conjecture**, and reality is allowed to kill it:

1. **Advisory by default.** A new guard *warns* ("this pattern caused X twice — intended?").
   It never blocks until it has earned the right to.
2. **Corroboration-gated promotion.** Advisory → blocking only after **K independent
   confirmations** (distinct sessions/contexts where it was right). One anecdote cannot create
   a law. (Same distinct-session counting that defeats recurrence-gaming in v1's poisoning
   defense.)
3. **Self-retirement on contact with reality.** Every time the agent overrides a guard, or it
   fires on a case that turns out fine, it logs a **false positive**. After **M** false
   positives the guard auto-demotes (blocking → advisory → retired). A wrong guard dies from
   use.
4. **Override is always available, and it is feedback, not defiance.** The agent (or user) can
   override any guard with a one-line reason. That override is *itself a memory*: it teaches
   the guard its boundary ("fires unless context C"), narrowing the conjecture instead of
   discarding it. Overriding refines memory; it does not fight it.

So the agent is never ossified: a guard that helps survives and hardens; a guard that
over-constrains is falsified and retired. Memory proposes; reality disposes. (This is the
exact inverse of a static rules engine — the rules are alive, and the agent's freedom to act
*is* the learning signal.)

## 5. Build order

1. **D (the bench) and A (guards) together.** A proves the 0-token-controller thesis and the
   Popperian safety; D measures whether it actually lifts a task series net of tokens. Neither
   ships on faith.
2. **B (anticipatory).** *Built* (`anticipate.py`). The trigger is trajectory-similarity to a
   past failure, not a static pattern, so it catches novel manifestations a regex misses. It is
   **precision-first**: an IDF-weighted overlap-coverage score, calibrated on the real vault so
   generic trajectories stay silent (~0 false alarms) while strong resemblances fire ONE warning.
   Lexical recall is moderate by design (the honest limit of matching a short trajectory to a
   long note); the optional embedding blend lifts it, and the adaptive threshold silences any
   leak (a cry-wolf failure-mode has its bar raised, capped at 0.9 so an overwhelming signal
   always breaks through). 0 tokens below threshold.
3. **C (causal).** *Built* (`causal.py`). Induces a compact causal model from the store's typed
   relation edges (`causes`, `caused-by`, `depends-on`, `requires`, `enables`, `part-of`, …),
   orienting each into a single impact direction, and answers the counterfactual **"what breaks
   if I change X?"** by traversing it downstream plus the failure modes mistakes attach to X. On
   the real vault it induces a **507-node / 1014-edge** impact graph, and a full counterfactual
   for `prism-orchestrator` (its downstream impact + six real failure modes + evidence) costs
   **~300 tokens** — versus ~2,250 to dump the 15 notes that mention it. **~7× cheaper, and it
   answers the question instead of returning the library.** That ratio is axis C's token thesis
   in one number.

## 5b. First measured result (axis D, and it holds up)

The bench exists (`longitudinal_improvement.py`), and the first numbers land where the thesis
predicts — over a 200-task family, 25 seeds, same knowledge given to both memory arms:

| arm | errors | memory tokens | total tokens vs no-mem | improvement / 1k tok |
|---|---|---|---|---|
| no memory | 46.9 | 0 | — | — |
| v1 always-inject | 16.7 | 80,000 | **+55,840 (net cost)** | 0.38 |
| **v2 active (guards)** | 17.6 | **2,581** | **−20,811 (net saving)** | **11.33** |

Both memory designs prevent ~the same errors (the knowledge is identical); **v2 delivers it for
31× fewer tokens** and is a **net token saving** (prevented redo outweighs the tiny guard cost),
while v1's always-inject tax makes memory a net *cost*. Improvement-per-token: **v2 is ~30×
v1**. And it is not rigged — the sensitivity sweep shows v2's edge **shrinks honestly** as the
guard false-positive rate rises (Popperian retirement trades prevention for safety), it just
never inverts in the tested regimes. This is the axis the field does not measure, and on it the
field's own design — always-inject — is the liability.

**And it is real, not just simulated** ([`LIVE_VALIDATION.md`](LIVE_VALIDATION.md)). The sim's one
assumption — that a fired guard changes a real model's output — was measured end-to-end on
DeepSeek across 12 coding tasks: the guard cut the real pitfall rate **0.36 → 0.05 (−86%)**, with
a measured effect **`eff` = 0.88** (the sim assumed a *conservative* 0.75). The help concentrates
exactly where the thesis says it should — **project-specific constraints the model cannot know
from training** (eff 0.79), while doing no harm on the textbook pitfalls a strong model already
avoids. Feeding the measured 0.88 back into D leaves the ~30× improvement-per-token and the net
token saving intact. The number rests on measurement now, not an assumption.

Re-running the same experiment on a **weak local agent** (`qwen2.5:3b`) surfaced a truth the
simulation could not: memory's payoff **scales with the agent's ability to use it**. Both models
err at the same base rate, but the strong reader extracts nearly 2× the benefit from the identical
memory (eff 0.79 vs 0.44) — told a constraint, the 3B model often still can't apply it. Memory
removes the *knowledge* bottleneck; it cannot remove the *capability* one. A stronger agent is
worth *more* memory, not less — the same "the reader is the variable" lesson as the retrieval axis.

## 6. What stays sacred

Everything v1 earned is preserved: plain-markdown-under-git substrate, zero required deps,
fully local, hybrid recall with abstention. v2 *subtracts* tokens (interventions replace
always-inject), it does not add a server or a dependency. The store is still files you own;
the guards are a small JSON ledger beside the notes; the causal model is a compact artifact,
not a database. **Local, auditable, token-frugal — now also active.**
