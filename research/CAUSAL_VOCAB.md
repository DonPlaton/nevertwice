# Causal vocabulary coherence: admitting `fixes` / `fixed-by`

**Date:** 2026-08 · **Status:** SHIPPED · **Stand:** the live store (3414 notes, 3989 entities, 2400 typed edges)

## The finding

`integrity.py` reports **vocabulary coherence** - the drift between the relation types
extraction WRITES and the ones the code READS. Its first run on the live store surfaced a
gap nobody had looked for:

| relation | edges | consumed by `causal.py`? |
|---|---|---|
| `fixes` | 457 | **no** |
| `alternative-to` | 344 | no (symmetric, correctly excluded) |
| `related-to` | 97 | no (symmetric, correctly excluded) |
| `fixed-by` | 60 | **no** |

`fixes` and `fixed-by` were the store's **most common typed edges**, the pair the extraction
prompt asks for by name, and the pair the whole "never repeat a mistake" premise rests on -
and the impact graph oriented neither. `what_breaks` / `why` were reasoning over a graph
missing a fifth of its edges.

This is the class of bug no per-note validation can catch: every individual note was
well-formed. Producer and consumer had simply drifted apart.

## The change

```python
_FORWARD += {"fixes"}        # touch the fix and the problem it holds down comes back
_REVERSE += {"fixed-by"}     # src is the problem, tgt is the fix: tgt -> src
```

`alternative-to` / `related-to` stay out: they are symmetric and carry no direction, so they
would add edges without adding causality.

## Measurement

A/B over the top 300 entities by note count, same store, same traversal:

| | before | after | delta |
|---|---|---|---|
| impact-graph nodes | 1193 | 1955 | +63.9% |
| impact-graph edges | 2651 | 3682 | **+38.9%** |
| entities with any causal answer | 102 / 300 (34%) | 210 / 300 (**70%**) | **+105.9%** |
| mean impacts per answered entity | 2.85 | 3.09 | +8.4% |
| cycles in the orientation | 26 | 85 | **+59** |

Before: two thirds of the store's most-referenced entities answered *nothing* when asked what
a change to them might break.

## The cost, stated plainly

59 new cycles. Each is a genuine contradiction in the data - `A fixes B` asserted alongside
`A fixed-by B` - not an artifact of the orientation: consistent statements (`A fixes B` plus
`B fixed-by A`) orient to the **same** edge and add no cycle. `nevertwice-integrity` lists
every one of them for cleanup.

Traversal is cycle-safe (`what_breaks` carries a `seen` set and excludes the queried entity
from its own impacts), so the failure mode is an **over-broad** answer, never a hang. For
"what might break if I touch this", over-reporting beats the silence it replaced.

## Verdict

Shipped. Coverage of the flagship counterfactual doubles; the cost is a measured, listed,
fixable set of data contradictions rather than a code defect.

A regression test pins `{"fixes", "fixed-by"} <= CONSUMED_VOCAB` so the blind spot cannot
silently return, and `integrity.py` mirrors the orientation sets with a test asserting the two
copies stay identical - the drift that caused this finding cannot recur unnoticed.
