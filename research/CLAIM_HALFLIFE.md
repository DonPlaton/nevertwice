# How long a claim lives here

The register publishes its ratio honestly - most claims are withdrawn, few are live - and that
ratio tells a reader nothing on its own. It cannot distinguish a project that retires claims as it
learns from one that cannot keep a number alive for a week. This page is the missing half: the
survival curve, computed from the register's own git history rather than asserted.

    python tools/claim_halflife.py --save    ->  research/results/claim_halflife.json

**Definitions.** A claim is *born* in the first commit where its id appears, and *dies* in the
first commit after that where it carries `stale` or `pending_remeasure`. Right-censoring is
handled the only honest way: a claim born five days ago cannot be asked whether it survived seven,
so it is excluded from the seven-day row rather than counted as a survivor.

## The curve

| horizon | survived | old enough to ask | share |
|---|---|---|---|
| 1 day | 214 | 869 | 0.246 |
| 7 days | 27 | 757 | 0.036 |
| 14 days | 14 | 272 | 0.051 |
| 30 days | 0 | 0 | the register is younger than the question |

The register's history spans 28.5 days over 160 commits, so no claim in it has had the chance to
live a month. That row stays empty until the calendar fills it; a six-month figure is not
something this repository can produce by computing harder.

**Each row is its own cohort.** A claim appears only in the rows it was old enough to be asked, so
these four numbers are not one curve and need not fall monotonically - the 14-day share exceeding
the 7-day share is an older, smaller population, not a resurrection.

## What survives, and why it is the finding

Of the 14 claims that reached two weeks, 13 are embedder claims and 1 is a seeded simulation.
Every survivor measures a **frozen artefact**.

No claim about engine behaviour has ever reached that horizon. That is not bad luck: a claim is
withdrawn when the code it closes over changes, the engine's closure is most of the package, and
the engine changes daily. The design is working exactly as specified, and the cost of it is
visible here - comparative gates keep finding nothing to compare against, because the thing they
would compare against was withdrawn by the commit that made the comparison worth running.

So the answer to "is that ratio normal?" is: for this design, yes, and the number to watch is not
the ratio but the survival of engine claims, which is zero at two weeks. Raising it means either
freezing more of what is measured, or narrowing what a claim closes over - not measuring more.
