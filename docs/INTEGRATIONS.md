# Integrations

Nevertwice works with **any** agent; Claude Code is only the zero-config case. The stdlib-only core exposes one
in-process Python API (`nevertwice.api`); the framework adapters and the generic capture helpers
are thin shims over it. Nothing here is required by the core; install only what you use.

> **Just want the config block to paste?** [AGENT_CONFIGS.md](AGENT_CONFIGS.md) has one per host - Claude Code, Cursor, Codex CLI,
> Claude Desktop, Zed - plus how to tell whether it took. This page is the longer form:
> the Python API, the capture paths, and the framework adapters.

## The Python API

```python
from nevertwice.api import recall, remember, capture_session

# write one lesson now (recallable immediately if the embedder is free)
remember("Crash-safe writes", project="myproj", type="pattern",
         prevention="write to a tmp file then os.replace - never partial files")

# recall the most relevant lessons for a query
for hit in recall("how do I persist files safely", project="myproj", k=5):
    print(hit["score"], hit["title"])

# extract memory from a finished session transcript (any agent)
capture_session(transcript_text, project="myproj", agent="my-bot")
```

`recall` returns a list of dicts (`score, ntype, project, title, stem, description, prevention`)
and falls back to lexical search when the GPU/Ollama is busy. `remember` writes a typed note.
`capture_session` runs the full extraction pipeline (Patterns / Mistakes / Decisions + Context).

## Active Memory: memory that acts (the differentiator, on every agent)

Most memory is something you *read* - it taxes every turn with injected text. Nevertwice also
*acts*, and stays silent until it has something worth saying, so it costs **zero context tokens
until an intervention earns its place**. All three interventions are on the Python API **and** the MCP
server, so Cursor / Cline / Codex / Zed / any MCP client get the same active memory Claude Code gets. Full
thesis + measurements: [`research/ACTIVE_MEMORY.md`](../research/ACTIVE_MEMORY.md).

```python
from nevertwice.api import guards_check, anticipate, what_breaks

# A - guard a proposed action against learned mistakes (0 tokens unless it fires)
for hit in guards_check("model = torch.device('cpu')", project="myproj"):
    print(hit["status"], hit["message"])      # 'blocking' → stop and comply or override

# B - predict the failure the current plan is heading toward (one warning, or silence)
anticipate("refactoring the orchestrator, touching prism_orchestrator.py", project="myproj")

# C - counterfactual: what breaks if I change this? (a synthesized answer, not an episode dump)
print(what_breaks("prism-orchestrator", project="myproj"))     # downstream impacts + failure modes
```

From any MCP client the same three are `memory_guard_check`, `memory_anticipate`, and
`memory_what_breaks`. On a task series, active interventions match always-inject's
error-prevention for **31× fewer memory tokens**
([`research/ACTIVE_MEMORY.md`](../research/ACTIVE_MEMORY.md)); the live repeat-error figure that
stood here was withdrawn in 2026-08 with the rest of the paid-API corpus - `python
tools/check_freshness.py --list-stale` says why.

Memory spends your context window, so it has a **budget** and it can decline. `api.budget_policy()`
sets per-turn and per-session token *and* latency caps plus `min_value`, the expected-value
threshold below which memory stays quiet **even when it could afford to speak** - which is the
difference between a policy and a character cap. Pass a ledger to
`guards_check(..., budget=..., policy=...)` and read `.report()` for consumed, avoided and net
tokens with every abstention and its reason, drawn from a closed set so the answers can be
counted. A blocking guard is exempt from the value threshold - a hard stop is not withheld to
save context - and still consumes budget so the accounting stays honest. `avoided` is
caller-supplied and requires an attribution: this project does not report a saving it did not
measure.

`nevertwice/protocols.py` is the plugin surface, and it **imports nothing from the engine** -
only `typing` and the dependency-free `schemas`. Five seams: `MemoryStore` (where notes live),
`Retriever` (query to ranked notes), `Extractor` (transcript to lessons), `EpisodeSource` (where
sessions come from - the shape `hosts.HostAdapter` satisfies), and `InterventionSink` (where a
fired guard goes). Write a class, call `protocols.conforms(obj, "MemoryStore")` to get the list
of what does not fit yet, and `protocols.register(...)` to plug it in. `conforms()` exists
because `isinstance()` against a `runtime_checkable` Protocol passes a method with the wrong
signature *and* a non-callable attribute of the right name - both fail at the first call
instead. The suite proves the promise the hard way: a third-party store and host are built in a
subprocess and `memory_hook` never reaches `sys.modules`.

`nevertwice-migrate` brings the memory you already have, **with its provenance and with a way
back out**. Five sources - Claude auto-memory, a claude-mem SQLite export, a Mem0 JSON export, a
Letta MemFS archive, and generic Markdown/JSONL. Each imported note carries `imported_from`,
`source_author`, `source_created`, `source_ref` and `import_batch` in its own frontmatter, so an
imported claim never passes for one this store worked out itself, and an unknown timestamp stays
empty rather than quietly becoming today. `--dry-run` prints the counts and the provenance gaps
and writes nothing; every real import records a batch that `--revert` can undo. Revert is a dry
run by default and re-checks each note's own stamp before deleting, so it will not remove a note
that has since been edited, superseded, or claimed by a later import. Round-tripped per source
from recorded fixtures in [`tests/fixtures/migrate/`](../tests/fixtures/migrate/README.md).

`nevertwice-hosts` answers the wiring question for every agent at once: where this host's
sessions live on **this** machine, whether Nevertwice is attached, and how to undo it. Four
adapters ship - Claude Code, Codex, Cursor and a generic JSONL fallback - behind one contract
(`nevertwice/hosts.py`): discovery, incremental cursoring, event normalisation into the
`EpisodeEvent` shape, install status, and a reversible uninstall that removes **only** the hook
entries this package wrote. A hand-rolled copy of the engine under `~/.claude/scripts` is a
supported deployment: it is reported as such, and never repointed or removed. Cursor keeps its
chat in a `state.vscdb` SQLite blob a sweep cannot read, so its adapter says so and names the two
ways out rather than returning an empty list. Every adapter is proved from a recorded fixture in
[`tests/fixtures/hosts/`](../tests/fixtures/hosts/README.md) - adding one needs no account with the agent it is for.

`nevertwice-inbox` is where you see all of it at once and change it: guards by status with
what each has earned, unresolved contradictions, and stale facts - a guard whose source note has
left the store, a note nobody has re-confirmed in months. Five actions - approve, edit, override,
retire, confirm - and trace-to-source. **Every action lands in the store and shows up in
`git diff`**: guard actions rewrite `guards.json`, `confirm` writes a `reviewed:` line into the
note's own frontmatter, and each action reports the files it wrote. An operator's decision is
stamped as one (`promoted_by` / `retired_by: operator`, with the reason) rather than smuggled in
as evidence the guard never earned.

Closing the loop is what makes any of it falsifiable. `guard_feedback(id, outcome,
session_id=...)` takes five outcomes: `prevented_failure` and `accepted` are evidence **for**;
`overridden` (you proceeded anyway - a statement about burden) and `false_positive` (it was
wrong here - about correctness) are evidence **against**; `unknown` is recorded and counts for
nothing. Both promotion and demotion count **distinct sessions**, so an outcome with no
`session_id` moves the published rates but no threshold, and one caller repeating itself can
neither promote a guard nor retire one. `guard_outcomes(id)` returns precision and override rate
with Wilson intervals. Firing is never an input: displaying a warning is not evidence that it
helped.

Every one of those surfaces can also answer **why** it fired. `guards_check(..., explain=True)`
and `api.why_fired(id, text)` in Python, `--why` on the CLI, `"explain": true` on the MCP tool,
and the dashboard's guard table all render one object: the matched span, the recorded mistake it
came from and how often that recurred, confidence, age, the policy that made it a warning rather
than a block, and what it cost against reading the source notes. It is opt-in because the default
path runs before every tool call and stays a regex match. Guards are **Popperian** -
advisory until corroborated, self-retiring on false positives, always overridable - so memory
proposes and reality disposes; the agent is never boxed in.

## Entity knowledge graph

Every lesson is tagged with its key entities (tools, concepts, files) as it is captured, so
memory is also a graph you can facet and traverse, with no database and no embedder. The tags
are LLM-emitted during extraction, normalised to lowercase kebab tokens, and stored in the note
frontmatter, so the graph reads straight from your files.

```python
from nevertwice.api import notes_for_entity, co_occurring, entity_graph, related_by, relation_graph

notes_for_entity("cuda", project="myproj")     # every lesson tagged with this entity, newest first
co_occurring("cuda", project="myproj")         # [{entity, shared}] entities that share a note with it
entity_graph(project="myproj")                 # {entity: {notes, links}} overview, most-connected first
```

Lessons also carry **typed relation edges** (`caused-by`, `fixed-by`, `depends-on`, ...), so the
graph is traversable, not just faceted. Each edge `target` is itself an entity, so you can hop:

```python
related_by("cuda", "fixed-by", project="myproj")   # [{rel, target, notes}] - what fixes CUDA issues
hop = related_by("cuda", "fixed-by", project="myproj")[0]["target"]
related_by(hop, "requires", project="myproj")      # multi-hop: cuda -> grad-checkpointing -> pytorch
relation_graph(project="myproj")                   # {entity: [{rel, target, notes}]} typed-edge overview
```

`remember(..., entities=[...], relations=[{"rel": "fixed-by", "target": "..."}])` attaches both
yourself; `remember_lessons` takes `entities` / `relations` keys per lesson. From the CLI and any
MCP client:

```bash
python nevertwice/memory_search.py --entity=cuda myproj     # a lesson's notes, co-occurrence, and edges
python nevertwice/memory_search.py --entities myproj        # the project's entity graph
python nevertwice/memory_search.py --relations myproj       # the project's typed-relation graph
python nevertwice/memory_search.py --graph=mermaid myproj   # export the graph (mermaid | dot | json)
```

`graph_export(fmt="mermaid"|"dot"|"json")` renders the whole graph for a visual: Mermaid drops
straight into an Obsidian note or a GitHub markdown block, DOT into Graphviz, JSON into D3 or your
own viewer.

The MCP `memory_entities` tool exposes all of it to any MCP client. No database, no embedder: the
graph reads straight from your note frontmatter.

The edges also feed **relation-aware retrieval**: `recall(query, expand_relations=True)` appends the
lessons the hits' typed edges reach, so a query about a bug surfaces its fix even when the fix shares
no words with the query. Off by default (plain recall is unchanged); each expansion result carries a
`via` field naming the edge that pulled it in. From the CLI it is `memory_search.py "query" myproj
--expand-relations`, and the MCP `memory_search` tool takes an `expand_relations` flag. To make the
**SessionStart** card relation-aware too (so a project's bug also carries its fix automatically), set
`NEVERTWICE_RELATION_EXPAND=N`; it stays off the per-prompt path to keep that precise and token-lean.

## Generic capture (any agent)

`MemorySession` collects turns and extracts memory once on close. Give any agent a memory in
four lines:

```python
from nevertwice.capture import MemorySession

with MemorySession(project="myproj", agent="my-bot") as mem:
    mem.log_user(prompt)
    mem.log_assistant(reply)
# on clean exit → salient lessons are extracted and stored
```

Already have an OpenAI-style chat function? Decorate it:

```python
from nevertwice.capture import capture_chat

@capture_chat(project="myproj", agent="my-bot")
def chat(messages):
    return client.chat.completions.create(model="gpt-4o", messages=messages).choices[0].message.content

chat([{"role": "user", "content": "how did we fix the OOM?"}])
chat.memory.flush()      # extract what was learned this session
```

Or wrap the **client itself** (zero rewrite, every call captured transparently):

```python
from openai import OpenAI
from nevertwice.capture import auto_capture

client = auto_capture(OpenAI(), project="myproj", agent="my-bot")
client.chat.completions.create(model="gpt-4o", messages=[...])   # captured automatically
client.memory.flush()    # at a conversation boundary (or pass auto_flush=True for short scripts)
```

`auto_capture` works with any OpenAI-shaped client (`openai`, Azure OpenAI, Groq, Together,
DeepSeek, Ollama's OpenAI-compatible endpoint) and passes every other attribute straight through;
only `…chat.completions.create` / `…responses.create` are observed, and a parse error never breaks
the real call.

## Import the memory other tools already built

`nevertwice-import` turns what another tool learned about you into ordinary typed notes,
so a fresh agent starts with your history instead of a blank slate:

```bash
nevertwice-import --from claude                        # Claude Code auto-memory (~/.claude/projects/*/memory)
nevertwice-import --from chatgpt --path memories.txt   # ChatGPT: copy Settings -> Personalization ->
                                                       #   Manage memories into a text file first
nevertwice-import --from cursor --path <repo>          # .cursor/rules/*.mdc + legacy .cursorrules
nevertwice-import --from agents --path AGENTS.md       # each top-level bullet becomes a note
```

Where things land: claude/chatgpt default to the `user` project (override with `--project`),
cursor/agents default to the directory name. Every item goes through the same write path as
`remember` (secrets are redacted, injection-shaped content is rejected, and the note is
recallable at once even with no model), and a content-hash ledger (`<vault>/.imported.json`)
makes re-runs no-ops.
`--dry-run` prints the plan and writes nothing. The Nevertwice-managed block inside an
AGENTS.md is skipped automatically: that text came out of this store, importing it back
would feed the memory its own output.

## Self-extraction: the agent is the extractor (no separate model)

`capture_session` runs an extraction LLM over a transcript. But your agent is already an LLM:
it can decide what it learned and write it directly, with **no extraction model**. Have the model
emit a JSON list of lessons and persist the batch in one call (one lock, one commit):

```python
from nevertwice.api import remember_lessons

lessons = [
    {"type": "mistake", "title": "CUDA OOM accumulates across epochs",
     "prevention": "empty_cache() + detach metrics each epoch", "tags": "cuda,training"},
    {"type": "pattern", "title": "Crash-safe writes: tmp then os.replace"},
]
remember_lessons(lessons, project="myproj")        # injection-shaped/empty lessons are skipped
```

For Claude Code, drop in the [`nevertwice-remember` skill](../skills/nevertwice-remember/SKILL.md);
for any MCP client, the `memory_remember` tool does the same. Full guide, the JSON contract, and a
provider-agnostic system-prompt template: [SELF_EXTRACTION.md](SELF_EXTRACTION.md).

## Always-on auto-capture for ANY agent: `nevertwice-watch`

Claude Code captures automatically via hooks. Every *other* agent that writes its sessions to
disk gets the same "magic" from the **watch daemon**: a tiny stdlib polling loop (no new deps)
that auto-detects the known agent log dirs on your machine and idempotently mines finished
sessions:

```bash
python -m nevertwice.watch            # auto-detect known agent logs, poll every 60s
python -m nevertwice.watch --list     # show exactly what it would watch, then exit
python -m nevertwice.watch --once     # one sweep then exit (good for cron / a smoke test)
```

It takes one short-held vault lock per cycle and yields instantly if Claude Code is mid-write, so
it never starves the live agent. A finished session is captured within one interval. Run it at
login (Task Scheduler / a `launchd`/`systemd --user` unit / `nohup … &`) and forget it.

### What it auto-detects

| Agent | Where | Status |
|---|---|---|
| **Codex CLI** | `~/.codex/sessions`, `~/.codex/history` (`*.jsonl`) | ✅ auto |
| **Cline** | VSCode `globalStorage/saoudrizwan.claude-dev/tasks` | ✅ auto |
| **Roo Code** | VSCode `globalStorage/rooveterinaryinc.roo-cline/tasks` | ✅ auto |
| **Gemini CLI** | `~/.gemini/tmp` (`*.json`) | ✅ auto |
| **Aider** | `.aider.chat.history.md` in your project roots | ✅ auto |
| **Claude Code** | `~/.claude/projects` | already captured by hooks (excluded to avoid double-mining) |
| **Cursor / Windsurf** | chat lives in a `state.vscdb` SQLite blob, not files | export to a folder first, then `--dir` (below) |
| **Anything else** | any dir of transcript files | `--dir` (below) |

Point it at anything explicitly (covers SQLite-based editors after an export, or a custom agent):

```bash
python -m nevertwice.watch --dir ~/exported_cursor_chats --agent cursor --project myproj
```

### The one-shot sweep (cron alternative)

The same idempotent engine is also a one-shot command, if you prefer cron / Task Scheduler over a
resident daemon. A file is keyed by path + content hash, so an unchanged transcript is never
mined twice and a changed one is re-mined once:

```bash
python -m nevertwice.ingest --dir ~/.codex/sessions --project myproj --agent codex
python -m nevertwice.ingest --dir ./agent_logs --recursive --glob "*.jsonl,*.md"
```

Both paths need an extraction backend (one cloud key or local Ollama), apply the same
secret-redaction and danger guards, and skip files over `NEVERTWICE_MAX_SWEEP_BYTES` and any
symlink that escapes the swept dir. Honest scope: polling, not native file events, but always-on
for every agent that logs to disk, which is all of them except the SQLite-only editors.

## LangChain  ·  `pip install nevertwice[langchain]`

```python
from nevertwice.integrations.langchain_memory import NevertwiceRetriever, NevertwiceMemory

retriever = NevertwiceRetriever(project="myproj", k=5)        # a LangChain Retriever
docs = retriever.invoke("how did we fix the OOM crash")      # → list[Document]

memory = NevertwiceMemory(project="myproj", memory_key="history")
# load_memory_variables injects relevant past lessons into the prompt;
# save_context collects the exchange - call memory.flush() to extract durable lessons.
```

## LlamaIndex  ·  `pip install nevertwice[llamaindex]`

```python
from nevertwice.integrations.llamaindex_retriever import NevertwiceRetriever

retriever = NevertwiceRetriever(project="myproj", k=5)
nodes = retriever.retrieve("how did we fix the OOM crash")   # → list[NodeWithScore]

# plug into a query engine:
from llama_index.core.query_engine import RetrieverQueryEngine
engine = RetrieverQueryEngine.from_args(retriever)
```

## Optional: the trained reranker  ·  `pip install nevertwice[reranker]`

A purpose-trained cross-encoder (bge-reranker-v2-m3) reorders recall results for a precision
gain on top of the calibrated fusion: **recall@1 0.55 → 0.61, MRR +0.06 on LongMemEval** (see
[BENCHMARKS](BENCHMARKS.md) / `research/RETRIEVAL_FUSION.md`). Opting in:
`pip install nevertwice[reranker]`, then one run with `NEVERTWICE_XRERANK=1` (that first run
downloads the ~2 GB model). From then on it stays on by itself; `=0` forces it off. It imports
torch+transformers lazily, runs best on a GPU, degrades safely to first-stage order if
unavailable, and flows through everything above: `recall`, the CLI (`memory_search --xrerank`),
and both framework retrievers.

## Optional: a cloud embedder (no local model for recall)

Semantic recall defaults to local Ollama (bge-m3). To run it with **no local model**, set
`NEVERTWICE_EMBED_PROVIDER=openai|voyage|cohere|gemini` (or point `NEVERTWICE_EMBED_BASE_URL` at any
OpenAI-compatible `/v1/embeddings` host) and the matching key, then re-embed once:
`python -m nevertwice.embed_index --rebuild`. The cache self-invalidates on a provider/model change
(stale vectors are demoted to text-only, never cross-cosined), and with **no** embedder at all
recall still answers via lexical FTS5 instead of going dark. See `.env.example`.
