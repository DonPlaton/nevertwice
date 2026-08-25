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
| [`research/evidence_manifest.json`](../research/evidence_manifest.json) | Every published number, with its dataset, sample size, model, command, raw file and caveat |
| [`research/data/README.md`](../research/data/README.md) | Fetching the LongMemEval dataset the benchmarks need |

## Contribute

| Doc | What it answers |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | The test command, the support matrix, and what a good change looks like |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | How we behave with each other |
| [`examples/sample-store/README.md`](../examples/sample-store/README.md) | The fixture vault the demos and tests read - and the four notes in it, so you can see the on-disk format without installing anything: [mistake](../examples/sample-store/Mistakes/2026-01-10-demo_api-mistake-n-plus-one-queries.md) · [pattern](../examples/sample-store/Patterns/2026-01-11-demo_api-pattern-assert-query-count.md) · [decision](../examples/sample-store/Decisions/2026-01-12-demo_api-decision-adopt-cursor-pagination.md) · [project card](../examples/sample-store/Context/demo_api.md) |
