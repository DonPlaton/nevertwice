---
title: "Make research/latency_bench.py save its results"
labels: good first issue, research
---

<!--
A draft, not a filed issue. Filing these is a maintainer action (they need labels that exist
on the repository); the text is kept here so the work is described even before the issue is
opened, and so a contributor can start without waiting.
-->

## The problem

`research/latency_bench.py` measures the hot paths - cold import, `PreToolUse` end to end,
`UserPromptSubmit` end to end, idle `SessionStart`, `guards.check()` in process, lexical
recall - and then **prints them and exits**. Nothing is written.

Every other study in `research/` saves a `.json` beside itself, which is what lets
`research/evidence_manifest.json` point a published number at a committed artifact. The speed
figures in `docs/BENCHMARKS.md` are the exception: they are registered in the manifest with a
`raw_gap` saying, honestly, that there is no file to point at.

## What to do

1. Add `--save` to `research/latency_bench.py`, writing `research/latency_results.json`
   beside the other result files.
2. Record what the number *means*, not just the number: for each measurement, the value in
   milliseconds, the number of repetitions, and which statistic it is (the bench takes the
   minimum of three, which is a choice a reader needs to know).
3. Include the machine: CPU, OS, Python version. A latency figure without hardware is not a
   measurement, and the manifest's `environments` block already has a place for it.
4. Update the manifest entries for the speed claims to point at the new file and drop their
   `raw_gap`, then run `python tests/_test_evidence_manifest.py` - it checks that a pointer
   agrees with the raw file it names, so a typo will fail rather than pass quietly.

## Done when

- `python research/latency_bench.py --save` writes `research/latency_results.json`;
- the speed claims in the manifest carry a `raw` and a `pointer` instead of a `raw_gap`;
- `python -m pytest -q` is green.

## Where to look

- `research/latency_bench.py` - the bench itself; it already isolates its store through
  `sandbox_guard`, so you do not need to think about where it writes notes.
- `research/longmem_eval.py` - the closest example of a `--save` that writes a result file.
- `research/evidence_manifest.json` - search for `raw_gap` to see the current honest gap.
- `docs/BENCHMARKS.md` § *Speed: what the hot paths cost*.

## Scope note

This is deliberately the mechanical half. A CI job that *fails on a regression* is the second
half and needs a decision about thresholds on shared runners, where timings are noisy; that
belongs in its own issue.
