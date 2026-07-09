# Integrations

Nevertwice works with **any** agent; Claude Code is only the zero-config case. The stdlib-only core exposes one
in-process Python API (`nevertwice.api`); the framework adapters and the generic capture helpers
are thin shims over it. Nothing here is required by the core; install only what you use.

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
`memory_what_breaks`. Measured on real tasks (DeepSeek), a fired guard cuts the real error rate
**0.36 → 0.05**; on a task series, active interventions match always-inject's error-prevention for
**~31× fewer tokens** ([`research/LIVE_VALIDATION.md`](../research/LIVE_VALIDATION.md),
[`research/ACTIVE_MEMORY.md`](../research/ACTIVE_MEMORY.md)). Guards are **Popperian** -
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

## Always-on auto-capture for ANY agent: `nevertwice watch`

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
[BENCHMARKS](BENCHMARKS.md) / `research/RETRIEVAL_FUSION.md`). Off by default; enable with
`NEVERTWICE_XRERANK=1` (it imports torch+transformers lazily, runs best on a GPU, and degrades
safely to first-stage order if unavailable). It flows through everything above: `recall`, the
CLI (`memory_search --xrerank`), and both framework retrievers.

## Optional: a cloud embedder (no local model for recall)

Semantic recall defaults to local Ollama (bge-m3). To run it with **no local model**, set
`NEVERTWICE_EMBED_PROVIDER=openai|voyage|cohere|gemini` (or point `NEVERTWICE_EMBED_BASE_URL` at any
OpenAI-compatible `/v1/embeddings` host) and the matching key, then re-embed once:
`python -m nevertwice.embed_index --rebuild`. The cache self-invalidates on a provider/model change
(stale vectors are demoted to text-only, never cross-cosined), and with **no** embedder at all
recall still answers via lexical FTS5 instead of going dark. See `.env.example`.
