#!/usr/bin/env python3
"""The host-adapter contract: four hosts, one normalized shape, no live account.

Support for a new agent used to be spread across three modules that did not know about each
other - discovery in `watch.known_targets()`, normalisation in an `ingest` text heuristic,
cursoring in a watermark dict - and install knew only about Claude Code, with no way to ask
whether it was wired or to undo it. `nevertwice/hosts.py` states the five answers once.

GOAL D6's exit criterion is the one this suite is built around: **recorded fixtures for each
produce equivalent normalized events, and no live account is needed.** So every fixture in
`tests/fixtures/hosts/` is the same conversation - a user prompt, a tool call, an assistant
reply - written in four different on-disk shapes. If four adapters agree on that, the contract
is real; if they merely each parse their own file, it is four parsers with a shared docstring.

The suite also holds the parts that are easy to fake:

* **normalized events conform to `schemas.EpisodeEvent`** - the D2 boundary, so "normalized"
  means one declared shape rather than four plausible dicts;
* **Codex scaffolding is skipped** - the fixture carries the ~10KB `session_meta` line that,
  treated as flat text, consumed the whole truncation budget and mined zero content on a real
  57MB corpus;
* **cursoring is incremental** - a second read returns nothing new, and a touched file returns
  its events again;
* **uninstall removes only our own entries** - the easy implementation rewrites
  `settings.json` and eats every hook the user configured themselves;
* **Cursor says why it cannot sweep** rather than returning an empty list, because an adapter
  that quietly returns nothing looks exactly like one that is working.

Nothing here touches a real agent, a network, or the owner's store.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures" / "hosts"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import hosts                    # noqa: E402
import schemas                  # noqa: E402

PASSED = 0
FAILED = 0

PROMPT = "why does the invoice endpoint time out on large accounts"
REPLY = "It is an N+1 query: each invoice re-fetches its line items."


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


FIXTURE_FOR = {
    "claude-code": "claude-code.jsonl",
    "codex": "codex.jsonl",
    "cursor": "cursor-export.json",
    "generic-jsonl": "generic.jsonl",
}


def normalized(name: str) -> list[dict]:
    adapter = hosts.get(name)
    path = FIXTURES / FIXTURE_FOR[name]
    return adapter.normalize(path.read_text(encoding="utf-8"), source=path)


# ---------------------------------------------------------- the contract


def test_every_adapter_answers_all_five_questions() -> None:
    print("\n- the contract is five questions, and every adapter answers them -")
    adapters = hosts.registry()
    check("four adapters are registered", len(adapters) == 4,
          str([a.name for a in adapters]))
    check("they are the four D6 names",
          {a.name for a in adapters} == set(FIXTURE_FOR),
          str(sorted(a.name for a in adapters)))
    for adapter in adapters:
        for method in ("discover", "read", "normalize", "install_status", "uninstall"):
            check(f"{adapter.name} implements {method}", callable(getattr(adapter, method, None)))
        status = adapter.install_status()
        check(f"{adapter.name} reports a known state", status["state"] in hosts.STATES,
              str(status))
        check(f"{adapter.name} explains its state in a sentence",
              len(status.get("detail", "")) > 20, str(status))
        check(f"{adapter.name} names the paths its answer rests on",
              isinstance(status.get("evidence"), list))
    check("get() resolves by name and returns None otherwise",
          hosts.get("codex") is not None and hosts.get("nope") is None)


def test_discovery_never_needs_a_live_agent() -> None:
    print("\n- discovery is a filesystem question, and absent is a normal answer -")
    for adapter in hosts.registry():
        found = adapter.discover()
        check(f"{adapter.name} returns a list of existing directories",
              isinstance(found, list) and all(p.is_dir() for p in found),
              str(found))
        check(f"{adapter.name} declares candidate roots even when none exist",
              isinstance(adapter.roots(), list))

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NEVERTWICE_GENERIC_JSONL"] = tmp
        try:
            adapter = hosts.get("generic-jsonl")
            check("a configured generic root is discovered",
                  [str(p) for p in adapter.discover()] == [str(Path(tmp))],
                  str(adapter.discover()))
        finally:
            os.environ.pop("NEVERTWICE_GENERIC_JSONL", None)


# --------------------------------- the exit criterion: equivalent events


def test_four_hosts_produce_equivalent_events() -> None:
    """GOAL D6's exit criterion, from recorded fixtures only."""
    print("\n- one conversation, four shapes, equivalent normalized events -")
    per_host = {name: normalized(name) for name in FIXTURE_FOR}

    for name, events in per_host.items():
        check(f"{name} produced events", bool(events), "empty normalization")
        problems = [p for e in events for p in schemas.conforms(e, "EpisodeEvent")]
        check(f"{name} events conform to schemas.EpisodeEvent", not problems,
              "; ".join(problems[:3]))
        check(f"{name} events all name their hook event",
              all(e.get("hook_event_name") for e in events))
        check(f"{name} events carry a session id",
              all(e.get("session_id") for e in events), str(events[:1]))

    def prompts(events):
        return [e["prompt"] for e in events if e["hook_event_name"] == "UserPromptSubmit"]

    def replies(events):
        return [e["prompt"] for e in events if e["hook_event_name"] == "AssistantMessage"]

    for name, events in per_host.items():
        check(f"{name} recovered the user prompt", prompts(events) == [PROMPT],
              str(prompts(events)))
        check(f"{name} recovered the assistant reply", replies(events) == [REPLY],
              str(replies(events)))

    # The tool call is in three of the four shapes; the generic fallback deliberately does not
    # guess at tool semantics, and saying so is better than inventing a shape for it.
    for name in ("claude-code", "codex", "cursor"):
        tools = [e for e in per_host[name] if e["hook_event_name"] == "PreToolUse"]
        check(f"{name} recovered the tool call", len(tools) == 1, str(tools))
        check(f"{name} named the tool", tools and tools[0].get("tool_name") == "Read",
              str(tools[:1]))
    check("the generic fallback claims no tool semantics it cannot support",
          not [e for e in per_host["generic-jsonl"] if e["hook_event_name"] == "PreToolUse"])

    check("every host agrees on the working directory",
          {e.get("cwd") for events in per_host.values() for e in events
           if e.get("cwd")} == {"/srv/billing"},
          str({name: {e.get("cwd") for e in ev} for name, ev in per_host.items()}))


def test_codex_scaffolding_is_skipped() -> None:
    """The measured failure this adapter exists to prevent."""
    print("\n- a 10KB session_meta line is scaffolding, not content -")
    raw = (FIXTURES / "codex.jsonl").read_text(encoding="utf-8")
    check("the fixture really carries the scaffolding", len(raw) > 10_000, str(len(raw)))
    events = normalized("codex")
    bodies = " ".join(e.get("prompt", "") for e in events)
    check("no event carries the instruction blob",
          "helpful coding agent" not in bodies,
          "the scaffolding reached the normalized events")
    check("the working directory was still taken from it",
          any(e.get("cwd") == "/srv/billing" for e in events),
          "skipping the line must not lose the one useful field in it")
    check("the tool output became a PostToolUse event",
          any(e["hook_event_name"] == "PostToolUse" for e in events), str(events))


# ------------------------------------------------------------- cursoring


def test_cursoring_is_incremental() -> None:
    print("\n- a second read returns nothing new -")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "session-a.jsonl"
        target.write_text((FIXTURES / "generic.jsonl").read_text(encoding="utf-8"),
                          encoding="utf-8")
        os.environ["NEVERTWICE_GENERIC_JSONL"] = tmp
        try:
            adapter = hosts.get("generic-jsonl")
            first = adapter.read()
            check("the first read returns the events", len(first["events"]) == 2,
                  str(first["new"]))
            check("it reports what it read", first["read"] == 1 and first["new"] == 1)

            second = adapter.read(first["cursor"])
            check("the second read returns nothing", second["events"] == [], str(second))
            check("but still reports the file as seen", second["read"] == 1)
            check("and reports no new work", second["new"] == 0)

            time.sleep(0.01)
            target.write_text(target.read_text(encoding="utf-8")
                              + json.dumps({"speaker": "user", "text": "and now?"}) + "\n",
                              encoding="utf-8")
            third = adapter.read(second["cursor"])
            check("a changed file is read again", len(third["events"]) == 3, str(third["new"]))

            gone = Path(tmp) / "session-b.jsonl"
            gone.write_text('{"speaker":"user","text":"hi"}\n', encoding="utf-8")
            fourth = adapter.read(third["cursor"])
            gone.unlink()
            fifth = adapter.read(fourth["cursor"])
            check("a deleted file drops out of the cursor",
                  str(gone) not in fifth["cursor"], str(sorted(fifth["cursor"])))
        finally:
            os.environ.pop("NEVERTWICE_GENERIC_JSONL", None)


def test_a_truncated_tail_does_not_lose_the_session() -> None:
    print("\n- a clipped final line costs one turn, not the file -")
    raw = (FIXTURES / "generic.jsonl").read_text(encoding="utf-8")
    clipped = raw[: len(raw) - 12]
    events = hosts.get("generic-jsonl").normalize(clipped, source=Path("s.jsonl"))
    check("the earlier turns survive", len(events) >= 1, str(events))
    check("the prompt is still there",
          any(e.get("prompt") == PROMPT for e in events), str(events))


# -------------------------------------------------- install / uninstall


def test_claude_code_install_status_and_reversible_uninstall() -> None:
    print("\n- wired, not wired, and undone without collateral damage -")
    with tempfile.TemporaryDirectory() as tmp:
        settings = Path(tmp) / "settings.json"
        projects = Path(tmp) / "projects"
        projects.mkdir()
        os.environ["NEVERTWICE_CLAUDE_SETTINGS"] = str(settings)
        os.environ["NEVERTWICE_CLAUDE_PROJECTS"] = str(projects)
        try:
            adapter = hosts.get("claude-code")
            settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
            check("a Claude Code install with no hook reads as not_wired",
                  adapter.install_status()["state"] == "not_wired",
                  str(adapter.install_status()))

            mine = {"type": "command", "command": "python .../nevertwice/memory_hook.py"}
            theirs = {"type": "command", "command": "python /home/me/my_own_hook.py"}
            # A hand-rolled flat copy - the shape this project's own author runs. It must be
            # detected, reported, and never touched.
            flat = {"type": "command",
                    "command": "python C:/Users/me/.claude/scripts/memory_hook.py"}
            settings.write_text(json.dumps({
                "hooks": {"SessionStart": [{"hooks": [mine, theirs]}],
                          "PreToolUse": [{"hooks": [mine]}]},
                "otherSetting": {"keep": "me"},
            }, indent=1), encoding="utf-8")
            status = adapter.install_status()
            check("with our hooks present it reads as wired", status["state"] == "wired",
                  str(status))
            check("it names which hooks are ours", len(status["evidence"]) == 2,
                  str(status["evidence"]))

            preview = adapter.uninstall(dry_run=True)
            check("a dry run changes nothing on disk", not preview["changed"], str(preview))
            check("but says what it would remove", len(preview["removed"]) == 2,
                  str(preview["removed"]))
            check("the file is untouched after a dry run",
                  "nevertwice" in settings.read_text(encoding="utf-8"))

            done = adapter.uninstall(dry_run=False)
            data = json.loads(settings.read_text(encoding="utf-8"))
            check("uninstall reports success and the files it wrote",
                  done["ok"] and str(settings) in done["changed"], str(done))
            check("our hook entries are gone",
                  "nevertwice" not in json.dumps(data), json.dumps(data))
            check("the user's own hook is NOT removed",
                  "my_own_hook.py" in json.dumps(data),
                  "rewriting settings.json wholesale eats hooks the user configured")
            check("unrelated settings survive", data.get("otherSetting") == {"keep": "me"})
            check("an emptied hook event is removed rather than left as an empty list",
                  "PreToolUse" not in (data.get("hooks") or {}), json.dumps(data))
            check("a backup was written", any("backup" in c for c in done["changed"]),
                  str(done["changed"]))

            again = adapter.uninstall(dry_run=False)
            check("uninstalling twice is safe and reports nothing to do",
                  again["ok"] and not again["removed"], str(again))

            # A hand-rolled flat copy is a supported deployment. It is neither "ours" nor
            # invisible: telling its owner to run install.py would repoint a setup they chose,
            # and removing it would delete a hook this package never wrote.
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [flat]}]}}),
                                encoding="utf-8")
            status = adapter.install_status()
            check("a hand-rolled flat copy does not read as wired",
                  status["state"] == "not_wired", str(status))
            check("but it is reported rather than ignored",
                  ".claude/scripts" in " ".join(status["evidence"]).replace("\\", "/"),
                  str(status["evidence"]))
            check("and the advice does not tell the owner to repoint it",
                  "did not wire them" in status["detail"], status["detail"])
            removal = adapter.uninstall(dry_run=False)
            check("uninstall refuses to remove a hook it never installed",
                  not removal["removed"]
                  and "memory_hook" in settings.read_text(encoding="utf-8"),
                  str(removal))
        finally:
            os.environ.pop("NEVERTWICE_CLAUDE_SETTINGS", None)
            os.environ.pop("NEVERTWICE_CLAUDE_PROJECTS", None)


def test_claude_code_is_not_swept_as_well_as_hooked() -> None:
    print("\n- the host captured by hooks must not also be swept -")
    check("claude-code declares itself hook-captured",
          hosts.get("claude-code").hook_captured is True,
          "sweeping it as well mines every session twice")
    check("no other adapter claims to be hook-captured",
          not any(a.hook_captured for a in hosts.registry() if a.name != "claude-code"))


def test_cursor_explains_itself_instead_of_returning_nothing() -> None:
    print("\n- an adapter that cannot sweep says why -")
    adapter = hosts.get("cursor")
    status = adapter.install_status()
    check("it reports unavailable without an export configured",
          status["state"] == "unavailable" or os.environ.get("NEVERTWICE_CURSOR_EXPORT"),
          str(status))
    check("it names the reason - a SQLite blob, not files",
          "vscdb" in status["detail"], status["detail"])
    check("it names the two ways out", "NEVERTWICE_CURSOR_EXPORT" in status["detail"]
          and "mcp" in status["detail"].lower(), status["detail"])

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "chat.json").write_text(
            (FIXTURES / "cursor-export.json").read_text(encoding="utf-8"), encoding="utf-8")
        os.environ["NEVERTWICE_CURSOR_EXPORT"] = tmp
        try:
            fresh = hosts.get("cursor")
            check("with an export configured it becomes sweepable",
                  fresh.install_status()["state"] == "not_wired",
                  str(fresh.install_status()))
            check("and reads the exported chat", len(fresh.read()["events"]) == 3,
                  str(fresh.read()["new"]))
        finally:
            os.environ.pop("NEVERTWICE_CURSOR_EXPORT", None)


def test_the_status_report_covers_every_host() -> None:
    print("\n- one report, every host, states that add up -")
    report = hosts.status_report()
    check("it declares its shape version", report["schema_version"] == hosts.SCHEMA_VERSION)
    check("every registered host appears",
          {h["host"] for h in report["hosts"]} == set(FIXTURE_FOR))
    check("the counts add up to the hosts",
          sum(report["counts"].values()) == len(report["hosts"]), str(report["counts"]))
    check("every counted state is a declared one",
          set(report["counts"]) <= set(hosts.STATES), str(sorted(report["counts"])))


def main() -> int:
    for fn in (test_every_adapter_answers_all_five_questions,
               test_discovery_never_needs_a_live_agent,
               test_four_hosts_produce_equivalent_events,
               test_codex_scaffolding_is_skipped,
               test_cursoring_is_incremental,
               test_a_truncated_tail_does_not_lose_the_session,
               test_claude_code_install_status_and_reversible_uninstall,
               test_claude_code_is_not_swept_as_well_as_hooked,
               test_cursor_explains_itself_instead_of_returning_nothing,
               test_the_status_report_covers_every_host):
        fn()
    print(f"\nhosts: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
