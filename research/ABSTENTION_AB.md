# Three switches that shipped with tests and no measurement

> **Re-measured 2026-09-11** at commit 9262543, in the campaign that re-measured the whole register with
> the extractor pinned deterministic and bound to the one the register names. The four runs before it
> read the shipped threshold's loss as eight and a half, eleven and a half, seven and five points; this
> one reads two and a half. The decision - both defaults at 0 - was made on the first run and holds on
> every one since. The table below is generated from claims registered from
> [`research/results/abstention_ab.json`](results/abstention_ab.json); until this date it was typed by
> hand and stood one campaign behind the artifact it said it was read from.

Three abstention mechanisms went into the engine in one sitting. Each had a test proving it
*works*: the filter filters, the threshold thresholds, the reader reads only the new region.
None had a measurement of whether it *helps*. That is the exact fault this project keeps
writing lessons about, committed by the same hand that wrote them.

This page is the measurement. The thresholds were written into `.loop/GOAL-CLOSE.md` before
any of them ran, along with what happens on a miss. Two passed. One did not, and its default
is now off.

## Method

For the two retrieval switches, retrieval runs **once** and every threshold is applied to the
same captured hit lists. Re-running retrieval per threshold would let ranking noise pass for a
policy effect. The corpus is [`supersession_v1`](data/supersession_v1.json), the only labelled
store this project has: 80 projects of two sessions each, with a marker per case saying which
returned text carries the answer.

The sweep is a curve rather than the two points the default sits between, because a default
that is only ever compared against *off* cannot be shown to be the right default.

## C1 - abstention on the per-turn recall path

`NEVERTWICE_PROMPT_RECALL_MIN_VALUE`. A hit scoring below this fraction of the batch's best
hit is refused, even when there is room for it - the distinction between *does it fit* and
*is it worth it*, which truncation cannot make.

**Declared before the run:** the payload must fall by at least 20% and the wanted fact must be
returned no more than 2 points less often. On a miss the default returns to 0.

<!-- claims:abstention-sweep -->
| threshold | chars/query | hits | wanted fact returned | chars saved | recall lost |
|---|---|---|---|---|---|
| 0.00 (off) | 457.7 | 1.47 | 0.992 | - | - |
| 0.10 | 433.8 | 1.38 | 0.987 | 5.2% | 0.4 pts |
| 0.20 | 397.6 | 1.26 | 0.966 | 13.1% | 2.5 pts |
| **0.35 (shipped)** | 377.9 | 1.20 | 0.966 | 17.4% | 2.5 pts |
| 0.50 | 368.0 | 1.17 | 0.966 | 19.6% | 2.5 pts |
| 0.75 | 340.2 | 1.07 | 0.966 | 25.7% | 2.5 pts |
<!-- /claims:abstention-sweep -->

Every threshold from 0.30 upward reads the same as the shipped one: one hit is all that is left to
refuse.

**Missed, by half a point.** At the shipped default the trade is about a hundred and ten characters
against two and a half points of finding the right lesson, where the gate allowed two. No threshold on
the curve clears both gates: the only one inside the recall budget, 0.10, saves a twentieth where the
gate asked for a fifth, and the first threshold that saves a fifth (0.20) already costs the same two
and a half points as the shipped one. The seventy-nine cases are the eighty of the corpus minus the
one where retrieval returned no hit at all, on which no threshold can act. Earlier runs read the loss
at five points and more; the deterministic extractor of this campaign writes fewer, longer notes, so
the mechanism has less to refuse and refuses less that matters - the miss is now narrow, and it is
still a miss.

**Default is now 0.** The mechanism stays as an opt-in switch rather than being deleted,
because the trade plausibly reverses on a store where recall returns ten hits instead of one
and a half. That is a hypothesis, and it is written here as one.

## C2 - abstention on the session-start payload

`NEVERTWICE_INJECT_MIN_VALUE`, on the path capped at 2200 characters.

The sweep is identical to C1's, and that is the result: **the mean payload on this corpus is
457.7 characters (mean of 3 runs), so the cap never binds and the two paths differ in nothing the measurement can
see.** The gate written for it - 15% smaller with no loss of the top-ranked lesson - is
**vacuous as written**: the top item scores 1.0 by construction and cannot be dropped at any
threshold below 1.0, so the second half is satisfied by arithmetic rather than by evidence.

Judged on the evidence C1 actually produced, the same trade applies and the same decision
follows. **Default is now 0.** Measuring this path properly needs a store large enough for the
cap to bind, which this corpus is not.

## C3 - delta re-mining

`NEVERTWICE_REMINE_MIN_GROWTH`. A transcript that has grown past its recorded watermark is
re-mined from the watermark rather than from byte zero.

**Declared before the run** - and corrected before the run, which is recorded here because the
first version of the gate was wrong. It demanded *identical extraction output*, which
contradicts the mechanism: reading only the new region is precisely a change in what the
extractor sees, so that gate could never be met and would have condemned the mechanism on a
definition. What matters is that nothing is lost:

- **coverage, hard gate:** every event appended since the watermark appears in the delta read.
- **efficiency:** bytes read down by at least 50% over a realistic trigger sequence.

Eight growth stages, 400 events each, 3,200 events and 1,391,395 bytes by the end:

| | full re-read | delta |
|---|---|---|
| bytes read across the eight triggers | 6,261,140 | 1,391,395 |
| coverage of appended events | 1.000 | 1.000 |

**77.8% fewer bytes at identical coverage. Passed.**

Running it is what found the defect it existed to rule out. The delta reader seeked to the
watermark and then discarded a line unconditionally, on the assumption that a byte offset
lands mid-line - but a watermark is recorded as the file size after a completed write, so it
lands on a newline nearly every time, and the discard was eating the first complete event of
every re-mine. Coverage read seven events short of the full read's across eight triggers, and
nothing but a coverage column would have shown it.

The full read's own coverage - one half - was itself a fault, in the stand rather than the engine: assistant content is a
list of blocks and the fixture passed a string, so half the events were silently dropped
before either arm saw them. A measurement stand that discards half its own input is worse than
no stand at all.

## What this does not show

- **One corpus, and a small one.** Mean recall depth is 1.45 hits (mean of 3 runs). The abstention mechanisms
  are built for the case where recall returns many hits of uneven quality, and that case is
  not in this corpus. The result is honest about the store it was measured on and says nothing
  about a larger one.
- **The C2 path was never exercised at its cap.** Its number here is C1's number.
- **The delta measurement is synthetic.** Real transcripts are not 400 uniform events per
  growth stage. The byte ratio would move; the coverage gate would not, being exact.
