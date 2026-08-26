---
title: "Publish the latency interval, not the minimum"
labels: good first issue, research
---

<!--
A draft, not a filed issue. Filing these is a maintainer action (they need labels that exist
on the repository); the text is kept here so the work is described even before the issue is
opened, and so a contributor can start without waiting.

Supersedes the earlier draft "Make research/latency_bench.py save its results", which was
completed: `--save` landed, `research/latency_bench.json` is committed, and the four live
latency claims point at it with no `raw_gap`. What is still wrong is one layer up.
-->

## The problem

`research/latency_bench.py --save` measures each hot path five times, and each of those is
itself the best of three invocations. It records all three statistics — `ms` (the minimum),
`median_ms` and `max_ms`. **The manifest publishes only `ms`, and the documents print it as a
single number.**

That number is not stable. On one unchanged tree, within one hour, `PreToolUse end-to-end`
came back **117 ms** and then **102 ms**; the previously published values were **115 ms** and,
before that, **142 ms**. No code changed between any of them. The claim's own `caveat` field
already says this in words — *"read this as ~0.1 s of end-to-end cost including interpreter
start, not as a figure precise to the millisecond"* — but the table beside it still prints
three significant figures.

The result is churn with no information in it: every commit that touches the engine trips the
freshness ratchet, the bench is re-run, and a different point value gets published. The
project's own corpus has a name for this failure class: `m-single-shot-bench`.

## What to do

1. Give the manifest claims an **interval** as well as a point. The raw file already has
   `median_ms` and `max_ms`; the natural published form is the median with the observed range
   beside it, not the minimum alone.
2. Render it that way. `tools/render_claims.py` owns the generated regions in
   `docs/BENCHMARKS.md`; the hand-written figure in `README.md:89` is governed by the numeric
   coverage check in `tests/_test_evidence_manifest.py`, so both have to agree.
3. Record the **host**, not just the platform. The artifact carries `platform` and `python`; it
   does not carry the CPU. The manifest's `environments` block already has a place for it
   (`ryzen7700_win11_py314`), so the artifact should name the same thing rather than leaving the
   join implicit.
4. Decide, in writing, what counts as a **latency regression** once there is an interval. A
   point-value comparison cannot answer that; overlapping ranges can.

## Done when

- the four live `latency.*` claims publish an interval, and the documents render it;
- `research/latency_bench.json` names the CPU it ran on;
- re-running the bench on an unchanged tree does **not** change what the documents claim;
- `python -m pytest -q` is green.

## Where to look

- `research/latency_bench.py` — the bench; `--save` and the `load_note` that explains the
  statistics.
- `research/latency_bench.json` — the committed artifact, with `ms` / `median_ms` / `max_ms`.
- `research/evidence_manifest.json` — search `latency.pretooluse_end_to_end`, and read its
  `caveat`, which is already the argument for this change.
- `tools/render_claims.py`, `docs/BENCHMARKS.md` § *Speed: what the hot paths cost*.

## Scope note

A CI job that **fails** on a latency regression is the natural next step and belongs in its own
issue: shared runners are noisy, and picking a threshold there is a separate decision from
publishing an honest interval here.
