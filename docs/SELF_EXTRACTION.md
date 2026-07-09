# Self-extraction: let the agent be the extractor (no separate model)

Nevertwice's default pipeline distils a finished session into memory with an
extraction LLM (a cloud key or local Ollama). But the agent in your loop is *already*
an LLM. It can decide what it learned and write it directly. That turnkey path needs
**no extraction model at all**, and it works for every agent, Claude Code included.

Two neural steps exist in Nevertwice; self-extraction removes one of them:

| step | default | self-extraction |
|---|---|---|
| **extract** session → lessons | cloud / Ollama | **the agent itself** (this doc) |
| **embed** for semantic recall | Ollama / cloud embedder | still needed for *semantic* recall; lexical (FTS5) recall needs nothing |

So with self-extraction + lexical recall you can run Nevertwice with **zero models**:
the agent writes its own lessons, and they're recalled by full-text search.

## The contract

A lesson is a small JSON object:

```json
{
  "type": "mistake",                       // "mistake" | "pattern" | "decision"
  "title": "CUDA OOM accumulates across epochs",
  "description": "VRAM grew each epoch until OOM; the autograd graph was kept alive",
  "prevention": "call torch.cuda.empty_cache() + detach metrics each epoch",
  "tags": "cuda,memory,training",          // optional, comma-separated
  "supersedes": ""                          // optional: title of a note this replaces
}
```

Only `type` and `title` are required. `prevention` matters for a `mistake`: it is the
one line that saves the next session.

## Three ways to write (pick what your agent can call)

### 1. Claude Code: drop in the skill

Copy [`skills/nevertwice-remember/`](../skills/nevertwice-remember/SKILL.md) into your
`.claude/skills/` (or the repo's). Claude Code then records lessons at end-of-task or
on "remember this", deduping against existing memory first. Zero glue code.

### 2. Any MCP client (Cursor / Cline / Zed / Claude Desktop)

Run the MCP server (`python -m nevertwice.mcp_server`) and the agent gets a
`memory_remember` tool: `project, type, title, description?, prevention?, tags?,
supersedes?`. Add a line to your system prompt (template below) and the agent
self-records through the tool.

### 3. Any Python agent: `remember_lessons`

Have the model emit a JSON list of lessons, then persist the batch in one call (one
vault lock, one git commit):

```python
from nevertwice.api import remember_lessons

lessons = my_agent.extract_lessons(transcript)     # your model returns the JSON above
stems = remember_lessons(lessons, project="myproj")
print(f"recorded {len(stems)} lessons")            # injection-shaped/empty ones are skipped
```

`remember_lessons` runs no extraction model; it just writes what you give it. Single
lessons can also use `nevertwice.api.remember(...)` or the CLI
`python -m nevertwice.remember --project P --type T --title "..."`.

## System-prompt template (provider-agnostic)

Append this to any agent's system prompt to make it self-extract:

```
You have a long-term memory. When you finish a task and learned something DURABLE and
NON-OBVIOUS - a gotcha a future session must avoid, a fix that worked, or a decision
and its rationale - record it. First search memory for the topic to avoid duplicates;
then write each lesson as: {type: mistake|pattern|decision, title, description,
prevention (for mistakes), tags}. Keep 1-3 of the genuinely durable ones; never record
secrets, transient state, or restatements of the task.
```

## Why this is safe

Self-written lessons go through the **same** write path as everything else: secret
redaction, the danger-content guard, atomic writes, supersession, and git
versioning all apply. A lesson that looks like a prompt-injection payload is rejected
(the write returns no stem) rather than stored. You review and `git diff` the store
like any other file.
