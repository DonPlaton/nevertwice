# Q5, the principle layer: the gates, read, and why the defaults did not change

**Measured at `7f40807`** (branch `q5/principle-layer`, from a clean worktree; every artifact
carries `measured_at.git_head` = `7f40807`, `dirty: false`). Gates registered before the runs;
read against those registrations by an analyst session and checked number by number by an
auditing session. The runs are exploration artifacts and are not in the evidence register: the
numbers below are backlog figures, and none of them is quoted on a governed page.

## What was tested

`NEVERTWICE_CROSS_PROJECT` has three modes:

- `off` - no cross-project recall;
- `all` - any other project's notes may be injected, labelled with their source project;
- `universal` - only de-identified `principle` sentences that recurred across two or more
  projects and passed the promoter (`nevertwice/principles.py`) are injected.

The stand (`research/cross_project_bench.py`) plants a project-specific identifier in two
projects' sessions, extracts and writes notes, runs the promoter, and asks whether a third
project's recall ever surfaces a planted identifier (leak) or the shared lesson (benefit).
Three populations, all committed under `research/data/`:

- `cross_project_v1.json`: 100 original cases, 4 identifier classes;
- `cross_project_ib_v1.json`: 50 cases where the identifier *is* the rule;
- `cross_project_df_v1.json`: 40 cases whose identifier has no digit, dot or slash
  (snake_case, camelCase/PascalCase, SCREAMING_SNAKE, kebab), shape crossed with rule template.

Two extractors: `qwen3-coder:30b` (all three populations) and `qwen3.6:35b-a3b` (the second
and third). The cloud extractor that production uses by default was not measured.

## The verdicts

| gate | verdict | why |
|---|---|---|
| leak = 0 | **void / underpowered** | `universal` leaked nothing on either extractor, but only because both extractors abstract the identifier out of every principle they write: no planted identifier ever reached a principle, so the defence layers never saw one. `all` leaks through path identifiers with one extractor, entity identifiers with the other, and all four digit-free shapes with both; ip and host identifiers are never leaked by `all`, so the stand cannot see them at all. Fewer identifiers were visible than registered |
| tokens: universal ≤ all | pass | trivially so where `universal` injected nothing |
| noise: universal ≤ all | not distinguishable | too few injected lines in `universal` to resolve a difference |
| benefit | reported, not gated | `universal` about a third of `all`'s on the identifier-bound and digit-free populations; **zero** on the original population |
| cost of the extra field to extraction | not measured | the session stand has no principle-on/off arm |
| twin threshold | measured | the lowest threshold in the swept range with no false merge on the negative pairs is its floor, 0.75; the previous default, 0.90, merges no paraphrase at all |

Under the registered rule, `universal` could become the default only if the first three held
and the extra field only if the fifth held. Neither did, so the defaults are:
`NEVERTWICE_CROSS_PROJECT=all`, `NEVERTWICE_PRINCIPLE=0`, `NEVERTWICE_PRINCIPLE_T=0.75`.
`universal` stays available as an opt-in, together with `NEVERTWICE_PRINCIPLE=1`.

## What the runs showed beyond the gates

- **The uniqueness rule bought nothing here.** The promoter rejects a word that appears in only
  one project's vocabulary unless two projects corroborate it. On the stand it rejected only
  ordinary words (prevent, avoid, always...), every cluster of the original population among
  them, and caught no planted identifier. The counterfactual without it would have promoted every
  cluster with no identifier exposed. It stays on for a reason the data cannot test: a leaked
  private name cannot be taken back out of another project's sessions.
- **Cold start.** The stand promotes inside a per-case store of three or four projects with one
  note each, which is the worst case for that rule; with all bench projects present, it flags an
  order of magnitude fewer words. A new install sees the smallest benefit.
- **Same owner.** Two projects of one client corroborate that client's private names, so the
  layer treats them as public. The structural fix (corroborating across independent owners, for
  example by git remote) is not built.
- **Respelling.** One extractor rewrote a camelCase service name as two plain words; an exact
  leak scanner does not see that.
- **Stand defects, not fixed:** the session-start injection is not scored for leak; the noise
  metric in code is not the registered definition; single-item writes leave some
  pattern/mistake pairs unclusterable.

## Reproduce

From a clean checkout of `7f40807`, with Ollama serving `bge-m3` and the extractor model:

    NEVERTWICE_PRINCIPLE_T=0.75 python research/cross_project_bench.py --population both --out cross_project_v3.json
    NEVERTWICE_PRINCIPLE_T=0.75 python research/cross_project_bench.py --population digit_free --out cross_project_v3_df.json
    CROSS_PROJECT_LLM=qwen3.6:35b-a3b NEVERTWICE_PRINCIPLE_T=0.75 python research/cross_project_bench.py --population identifier_bound --out cross_project_v3_q36_ib.json
    CROSS_PROJECT_LLM=qwen3.6:35b-a3b NEVERTWICE_PRINCIPLE_T=0.75 python research/cross_project_bench.py --population digit_free --out cross_project_v3_q36_df.json
    python research/principle_twins.py

Extraction runs at temperature 0: a second run of the same commit reproduces the first byte for
byte, which also means a re-run is not new data.
