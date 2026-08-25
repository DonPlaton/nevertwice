# Recorded host fixtures

One conversation, four on-disk shapes. Every fixture is the *same three beats* - a user
prompt, a tool call, an assistant reply - so `tests/_test_hosts.py` can assert that four
adapters produce equivalent normalized events rather than four unrelated transcripts.

Recorded by hand from the documented formats, not copied from a real session: a transcript
carries paths, project names and prose from whoever produced it, and a test fixture is a
file that gets read by strangers.

* `claude-code.jsonl`  - `{type, cwd, message:{role, content}}` per line, content either a
  string or a block list.
* `codex.jsonl`        - `{timestamp, type, payload}` rollout lines. The first is a
  `session_meta` carrying ~10KB of instructions: the scaffolding that, treated as flat text,
  consumed the whole truncation budget and mined zero content on a real 57MB corpus. An
  adapter that stops skipping it fails the suite.
* `cursor-export.json` - the exported chat JSON. Cursor's live chat is a `state.vscdb`
  SQLite blob that a file sweep cannot read, so the export is the only shape worth pinning.
* `generic.jsonl`      - deliberately uses `speaker`/`text` rather than `role`/`content`, so
  the fallback adapter is proved against key names it was not written around.
