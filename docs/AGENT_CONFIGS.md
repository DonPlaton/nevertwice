# Copy-paste agent configs

One block per agent. Every one of them assumes `pip install nevertwice` has put the
`nevertwice-mcp` command on your `PATH` - check with `nevertwice-mcp --help` before editing a
config file, because "command not found" inside an MCP host usually surfaces as a silent
missing-server rather than an error.

The MCP server is stdlib-only and speaks JSON-RPC on stdin/stdout. It needs no port, no
daemon and no key. What it exposes: `memory_search`, `memory_remember`, `memory_ingest`,
`memory_digest`, `memory_conflicts`, and the three active-memory tools
(`memory_guards_check`, `memory_anticipate`, `memory_counterfactual`).

`NEVERTWICE_HOME` is optional in every block below - leave it out and the store defaults to
`~/.nevertwice`. Set it (and `NEVERTWICE_VAULT`, which wins when both are set) only if your
store lives somewhere else.

## Claude Code

Native, and the one case with no config to write:

```bash
git clone https://github.com/DonPlaton/nevertwice && cd nevertwice
python install.py
```

That wires the hooks that capture sessions, inject the project card at session start, recall
per prompt, and run the guard before an edit lands. It backs up `~/.claude/settings.json`
first and is idempotent, so re-running it is safe. `python install.py --print` shows exactly
what it would change and writes nothing.

To add the MCP tools on top - useful when you want the agent to *call* memory explicitly
rather than only receive it - put this in `.mcp.json` at the root of a project:

```json
{
  "mcpServers": {
    "nevertwice": {
      "command": "nevertwice-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

## Cursor

`~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` inside one project:

```json
{
  "mcpServers": {
    "nevertwice": {
      "command": "nevertwice-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

Cursor keeps its chat history in a local SQLite blob rather than plain log files, so the
`nevertwice-watch` daemon cannot mine it directly. The MCP path above needs no export; if you
want automatic capture as well, see the export step in
[INTEGRATIONS.md](INTEGRATIONS.md).

## Codex CLI

Codex reads TOML. In `~/.codex/config.toml`:

```toml
[mcp_servers.nevertwice]
command = "nevertwice-mcp"
args = []
```

Codex also writes plain `*.jsonl` session logs to `~/.codex/sessions`, which the watch daemon
finds on its own:

```bash
nevertwice-watch            # start once at login; it picks up finished sessions as they land
```

## Claude Desktop

`claude_desktop_config.json` - on macOS
`~/Library/Application Support/Claude/`, on Windows `%APPDATA%\Claude\`:

```json
{
  "mcpServers": {
    "nevertwice": {
      "command": "nevertwice-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

## Zed

`~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "nevertwice": {
      "command": {
        "path": "nevertwice-mcp",
        "args": [],
        "env": {}
      }
    }
  }
}
```

## Anything else

If it speaks MCP, the `nevertwice-mcp` command is the whole integration. If it writes session
transcripts to disk, `nevertwice-watch` will find them. If it does neither, call
`nevertwice.api` from Python or pipe a transcript through `nevertwice-ingest`. All three paths,
plus LangChain and LlamaIndex adapters, are in [INTEGRATIONS.md](INTEGRATIONS.md).

## Checking it worked

```bash
nevertwice-stats                       # what the store holds right now
nevertwice-search "anything" myproj    # the same query path the agent takes
```

If the agent's tool list does not show `memory_search`, the usual causes are that
`nevertwice-mcp` is not on the `PATH` the host process sees (GUI apps often do not inherit a
shell `PATH` - use the absolute path from `which nevertwice-mcp`), or that the host needs a
restart to re-read its config.
