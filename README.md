<div align="center">

<img src="docs/banner.png" alt="Nevertwice: proactive, local-first memory for AI coding agents that acts before your agent repeats a mistake · plain Markdown + Git · zero dependencies · private by default" width="880">

# Nevertwice

### Proactive, local-first memory for AI coding agents - it acts before your agent repeats a mistake.

*Other memory tools recall text and pad every prompt with it. Nevertwice stays silent until it has
something worth saying, then fires a one-line warning at the moment your agent is about to repeat a
mistake it already made - and costs nothing until it does. The store is plain Markdown in a git
repo you own.*

**No database. No server. No account. No telemetry. Zero pip dependencies.**

[![tests](https://github.com/DonPlaton/nevertwice/actions/workflows/ci.yml/badge.svg)](https://github.com/DonPlaton/nevertwice/actions/workflows/ci.yml)
[![CodeQL](https://github.com/DonPlaton/nevertwice/actions/workflows/codeql.yml/badge.svg)](https://github.com/DonPlaton/nevertwice/actions/workflows/codeql.yml)
[![PyPI](https://img.shields.io/pypi/v/nevertwice)](https://pypi.org/project/nevertwice/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](#install)
[![Core deps](https://img.shields.io/badge/core%20deps-0%20(stdlib)-orange)](#privacy)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Evidence](https://img.shields.io/badge/evidence-every%20number%20traced%20to%20its%20commit-blueviolet)](#the-evidence)
[![Active Memory](https://img.shields.io/badge/active%20memory-0--token%20guards-2ea043)](#memory-that-acts)

```bash
pip install nevertwice          # the library, the CLI, and the MCP server
git clone https://github.com/DonPlaton/nevertwice && cd nevertwice
python install.py               # native Claude Code wiring; everything else via MCP
```

**The whole product, in four beats** - `python examples/guard_demo.py`, no model, no key, no network:

<img src="docs/never-twice.svg" alt="Four frames. In session A the mistake is recorded as one Markdown note under git, named mistake-sql-built-by-fstring. Still in session A, a guard is distilled from it into a ledger rather than into your context. In a later session B the repeat is flagged - the guard fires with a one-line warning, and nothing was spent on context until that line. Then the corrected action, using a query parameter, runs clean and no guard fires, so this is a warning and not a blanket block." width="880">

<sub>Beat 3 is the product: the lesson lives in a JSON ledger, **not** in your context, so it costs
zero tokens until the moment it fires. `--check` prints that transcript byte-identically on three
platforms and CI fails if a beat stops holding. More demos - the whole machine on one project
(**`scenario_demo.py`**), the 25-second recall demo (**`demo.py`**, any OS) - all on a throwaway
store, with a test that runs each one against an exported real vault path to prove yours is
untouched.</sub>

</div>

---

## Memory that acts

Your agent starts every session from zero: same files re-read, same gotchas rediscovered, and now
and then Tuesday's mistake repeated. Nevertwice watches each session, distils the durable lessons
into short Markdown notes, commits them to git, and feeds the relevant ones back. That much is a
better **library**, and the systems in [COMPARISON.md](docs/COMPARISON.md) all stop there -
retrieve, inject, tax every turn. We measured that axis to its end and found it
[reader-bound and commoditizing](research/QA_ACCURACY.md): the LLM, not the memory, is the variable.
So the interventions are the product, and each one is token-budgeted:

- **Guards** - a pattern distilled from a past mistake, checked against the edit before your agent
  writes it. One line fires on a match, at **zero context tokens until it does**. A guard is
  advisory until corroborated across sessions, retires itself after false positives, and is always
  overridable: memory proposes, reality disposes.
- **Anticipation** - predicts the failure the current plan is heading toward by resemblance to past
  ones, and surfaces *one* precise warning. Spend is proportional to risk, not paid per turn.
- **Counterfactual** - *"what breaks if I change X?"*, answered from an induced causal graph
  instead of by recalling every note that mentions the entity.

Every intervention cites the note it was born from, is overridable in one line, and is off by
default where it could be noisy. All three are on the Python API **and** the MCP server, so they
work on every agent ([INTEGRATIONS.md](docs/INTEGRATIONS.md)).

## The evidence

Small samples, run by one person - the point is that the harness is in the repo, the raw results
are committed, and [every number here resolves to one](research/evidence_manifest.json).

**Most of that corpus was withdrawn in 2026-08, and saying so is the point.** Every claim had been
stamped with the commit that last touched its *artifact file* - a directory move - rather than the
commit whose code produced the number. Once each claim was made to name the source files its
command imports, most of them turned out to describe the engine as it stood at the first release, several review rounds after
the ranker beneath them had been rewritten. Re-measuring them needs a third-party dataset this repo
does not ship, the owner's private store, or a paid API, so they are withdrawn rather than
reprinted. `python tools/check_freshness.py --list-stale` names every one and the gate that blocks
it, and CI now fails when a published number outlives the code that made it.

What survived re-measurement at HEAD, and what it cost:

| claim | result | evidence |
|---|---|---|
| acting vs *always-injecting* the same lesson | same error prevention for **31×** fewer memory tokens | [ACTIVE_MEMORY.md](research/ACTIVE_MEMORY.md) |
| memory-poisoning acceptance attacks | **81%** blocked overall - **100%** of prompt injection, **25%** of plausible-false facts | [POISONING.md](research/POISONING.md) |
| what being there costs | PreToolUse **114 ms** end to end - a tenth of a second, and it moves by a third between sessions - and zero context tokens until a guard fires | [BENCHMARKS.md](docs/BENCHMARKS.md) |

The poisoning row is the one to read twice: against the withdrawn first-release artifact, the current
engine blocks *fewer* false facts, not more. That regression is published here because a project
whose argument is reproducibility does not get to report only the deltas that flatter it.

The retrieval comparison against the funded leaders stood on the uncommitted dataset, so it is
withdrawn too:

<!-- claims:head-to-head -->
> **Withdrawn 2026-08.** the LongMemEval-oracle dataset is third-party and not committed (research/data/longmemeval_oracle.json is absent here), and no content hash was recorded when the number was produced, so the run cannot be reproduced or even pinned to a revision
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/head_to_head.py` is what re-measures this one.
<!-- /claims:head-to-head -->

Two caveats we measured rather than hid, and which the withdrawal does not touch: the payoff scales
with the agent's own capability (memory removes the *knowledge* bottleneck, not the reasoning one),
and retrieval R@k had stopped discriminating between serious systems well before these numbers were
pulled - which is why the work that matters is after retrieval, resolving contradictions at write
time, resisting poisoning, forgetting the right things. Those, the baseline gates a headline has to
clear, and the negative results we deleted rather than shipped:
[BENCHMARKS.md](docs/BENCHMARKS.md) · [BASELINES.md](research/BASELINES.md) ·
[WEAKNESSES.md](docs/WEAKNESSES.md) · [the research lab](research/).

## How it works

<p align="center"><img src="docs/architecture.svg" alt="Two lanes. Write time, left to right: your session in any agent; capture through a hook, an MCP call, the watch daemon or ingest; distil into mistakes, patterns and decisions; store as Markdown under git that you own. Read time: the same store; retrieve by semantic and lexical search behind a calibrated abstention gate; decide against a token budget, which may abstain; act. Act is the intervention point, where a guard fires, anticipation warns, or a counterfactual is answered. Capture, decide and act cost no context tokens until something fires, and every stage except act stays on your machine by default." width="880"></p>

Your agent's memory is a folder of Markdown notes in a git repo, and you own every byte: open it
in Obsidian, grep it, diff it, `git pull` it to another machine, delete a note you disagree with.
Recall is hybrid - semantic over local embeddings, fused with lexical, behind a calibrated
abstention gate, so a nonsense query returns *"no confident match"* rather than a confident wrong
answer. A newer lesson that contradicts an older one retires it at write time.
Details: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Privacy

Embeddings run locally on Ollama by default, so nothing leaves the machine;
`NEVERTWICE_EMBED_PROVIDER` points at a cloud embedder if you would rather not run a local model,
and with none at all recall falls back to lexical search rather than going dark. Extraction is
local-first and an optional cloud key only speeds it up. Secrets are redacted before anything is
written or sent. There is **no telemetry** - no account, no usage pings, no phone-home; the only
network calls are the ones you configured, and `NEVERTWICE_LOCAL_ONLY` pins projects local for good.

## Any agent

Claude Code is the zero-config case: `install.py` wires the capture, recall and guard hooks, and
capture is automatic from then on. Agents that write sessions to disk (Codex, Cline, Roo, Aider,
Gemini CLI) are covered by the `nevertwice-watch` daemon; any MCP client - Cursor, Claude Desktop,
Zed - talks to `nevertwice-mcp`. From Python, wrap your client with `auto_capture(client)` or let
the agent write its own lessons through `remember_lessons`, with no extraction model at all.
LangChain and LlamaIndex get drop-in adapters, and `nevertwice-import` moves in the memory Claude
Code, ChatGPT or Cursor already built for you. Recipes: [INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Install

```bash
pip install nevertwice                 # library + nevertwice-* commands + MCP server
git clone https://github.com/DonPlaton/nevertwice && cd nevertwice
python install.py                      # idempotent; backs up ~/.claude/settings.json first
python install.py --ollama             # also pull the local models (bge-m3 + an extractor)
python install.py --profile research   # turn on the opt-in Brain layer (research/general)
python install.py --print              # dry run: shows what it would do, writes nothing
```

With no backend at all, extraction pauses loudly (sessions are kept and retried, never dropped) and
recall runs on lexical search until an embedder shows up. The five-minute walkthrough is in
[QUICKSTART.md](QUICKSTART.md); every environment variable is in [CONFIG.md](docs/CONFIG.md).

Contributing: `pip install -e ".[dev]"`, then `python -m pytest -q`. Eighty-five hermetic suites -
LLMs, embedders, the optional reranker, network and GPU execution are disabled or mocked, and a lint
fails the build if a script reaches a memory store without declaring which store it means. CI runs
them on Linux, Windows and macOS across four Python versions.

## Docs, and who wrote this

[Start here](docs/README.md) · [Quickstart](QUICKSTART.md) · [What ships today](docs/FEATURES.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Integrations](docs/INTEGRATIONS.md) ·
[Configuration](docs/CONFIG.md) · [Brain layer (opt-in)](docs/BRAIN_LAYER_DESIGN.md) ·
[Benchmarks](docs/BENCHMARKS.md) · [Baseline gates](research/BASELINES.md) ·
[Comparison](docs/COMPARISON.md) · [Known weaknesses](docs/WEAKNESSES.md) ·
[Research lab](research/) · [Security](SECURITY.md) · [Threat model](docs/THREAT_MODEL.md) · [Contributing](CONTRIBUTING.md)

Built by **Platon Chernov**; formerly *Anamnesis*, renamed 2026-07 (same project, same store, the
old environment variables still work). A ⭐ is genuinely appreciated and a citation is welcome - a
`CITATION.cff` ships with the repo. MIT, see [LICENSE](LICENSE). Use it, fork it, build on it.
