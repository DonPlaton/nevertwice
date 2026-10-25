<div align="center">

<img src="docs/banner.png" alt="Nevertwice: proactive, local-first memory for AI coding agents that acts before your agent repeats a mistake · plain Markdown + Git · zero dependencies · private by default" width="880">

# Nevertwice

### Attention-native memory for AI coding agents, built around how transformers actually read context.

*Language models lose accuracy when every prompt is padded with noisy chunks, undated snippets or
retracted values. Nevertwice v3 stays silent at 0 context tokens until a guard fires. Behind the
guard sits a two-tier store: Tier 1 holds distilled facts with `[Prior YYYY-MM-DD: ...]` value
history, Tier 2 holds dated, role-tagged verbatim episodes. Calibrated Z-score fusion ranks both,
and an epistemic modality gate separates a `[Confirmed fact]` from a `[Hypothetical / Assistant
suggestion]`. Modes switch automatically: `NEVERTWICE_EXTRACT_MODE=auto` picks `code` for software
engineering and `all` for personal, biographical and research sessions. The store is plain Markdown
in a git repo you own.*

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

- **Guards** - a pattern distilled from a past mistake, checked against the edit before your agent writes
  it; one line fires on a match, at **zero context tokens until it does**. Measured ([GUARD_BENCH.md](research/GUARD_BENCH.md)):
  a linter wins on generic repeats; on the project-specific ones only memory knows, guards catch about two and a half times as many, at a false alarm on a fifth of **that same class's** clean calls where a linter pays none - re-measured on 2026-09-23 by the post-review campaign.
  Advisory until corroborated, self-retiring on false positives, always overridable: memory proposes, reality disposes.
- **Anticipation** - predicts the failure the current plan is heading toward by resemblance to past
  ones, and surfaces *one* precise warning. Spend is proportional to risk, not paid per turn.
- **Counterfactual** - *"what breaks if I change X?"*, answered from an induced causal graph
  instead of by recalling every note that mentions the entity.

Every intervention cites the note it was born from, is overridable in one line, and is off by
default where it could be noisy. All three are on the Python API **and** the MCP server, so they
work on every agent ([INTEGRATIONS.md](docs/INTEGRATIONS.md)).

## The evidence

Small samples, run by one person: the harness is in the repo, the raw results are committed, and
[every number here resolves to one](research/evidence_manifest.json).

<img src="docs/v3-scoreboard.svg" alt="Campaign v3 scoreboard comparing Nevertwice v3 against Mem0, LangMem, Graphify, and Claude Code Memory on LongMemEval S1 and LoCoMo S4, plus token economy and nevertwice-embed v2 results." width="880">

**Campaign v3 (Stage D + A6, 2026-10-26).** Five systems, one frozen reader and judge
(`deepseek-flash`), one `bge-m3` embedder, hash-pinned corpora. [Full report](research/v3/REPORT-V3-STAGE-D-RU.md) · [Embed v2 eval](research/embed_universal/heldout/v2_3axis_eval.json).

```text
System                            S1 LongMemEval (B / K / V)   S4 LoCoMo (J1 / F1)      S4 Write Cost
--------------------------------  ---------------------------  -----------------------  ----------------------------
Nevertwice v3 (allmode A6.7)      5/5  ·  5/5  ·  5/5          25/25 (F1 53.0%)         19+25 calls  /    56,016 tok
Mem0 (mem0ai v2)                  3/5  ·  2/5  ·  3/5          21/25 (F1 49.8%)         419+25 calls / 3,725,884 tok
LangMem (langmem v0)              2/5  ·  1/5  ·  1/5          20/25 (F1 23.5%)         19+25 calls  /    93,067 tok
Graphify (v0_9_93 expand/query)   1/5  ·  0/5  ·  0/5          19/25 / 11/25            1+25 calls   /    19,025 tok
Claude Code Memory (v2_1_304)     2/5  ·  n/a  ·  2/5           7/25 (F1  9.2%)         29+25 calls  /    71,552 tok
```

**Audit history.** The 2026-07 head-to-head was **Withdrawn 2026-08**: its corpus could not be
identified after the fact. The second campaign's acting row was withdrawn on 2026-09-26: stage D changed the simulation's writer, so the token ratio no longer describes this code; the rest of that campaign's rows went the day before, for the same reason. Each withdrawn claim
names its commit, its blocking gate and its restore command in `research/evidence_manifest.json`;
[CLAIM_HALFLIFE.md](research/CLAIM_HALFLIFE.md) explains how long a claim lives and why engine
claims die first.

<!-- claims:head-to-head -->
> **Withdrawn 2026-08.** historical: the unpinned head_to_head stand's first-generation rows, recorded by a bare command with no --save that writes nothing; the same arms on the pinned corpus are the live h2h_pinned.* claims (D-O2, campaign v2 at fe6ddff)
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/head_to_head.py` is what re-measures this one.
<!-- /claims:head-to-head -->

One caveat neither run touches: the payoff scales with the agent's own capability, because memory
removes the *knowledge* bottleneck and not the reasoning one. The baseline gates a headline has to
clear and the results we deleted rather than shipped: [BENCHMARKS.md](docs/BENCHMARKS.md) ·
[BASELINES.md](research/BASELINES.md) · [WEAKNESSES.md](docs/WEAKNESSES.md) ·
[the research lab](research/).

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
and with none at all recall falls back to lexical search rather than going dark. Extraction tries a cloud backend FIRST whenever one of the four supported keys is in your environment (`NEVERTWICE_CLOUD=auto`) and local Ollama otherwise or on failure; `NEVERTWICE_CLOUD=none` keeps every session on the machine. Secrets are redacted before anything is written or sent. There is **no telemetry** - no account, no usage pings, no phone-home; the only
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
python install.py --uninstall          # BEFORE removing the package or clone (QUICKSTART §5)
```

With no backend at all, extraction pauses loudly (sessions are kept and retried, never dropped) and
recall runs on lexical search until an embedder shows up. The five-minute walkthrough is in
[QUICKSTART.md](QUICKSTART.md); every environment variable is in [CONFIG.md](docs/CONFIG.md).

Contributing: `pip install -e ".[dev]"`, then `python -m pytest -q`. Three hundred sixty-three hermetic suites -
LLMs, embedders, the optional reranker, network and GPU execution are disabled or mocked, and a lint
fails the build if a script reaches a memory store without declaring which store it means. CI runs
them on Linux, Windows and macOS across four Python versions. One is a golden store - same sessions
in, same bytes out, with a canary proving the check can fail and [a signed list of what it misses](tests/UNPROVEN.md).

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
