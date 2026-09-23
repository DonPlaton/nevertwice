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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures" / "hosts"
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import
import _wall  # noqa: E402 - the HOME/settings wall; not a project module, no ordering rule on it

sys.path.insert(0, str(ROOT / "nevertwice"))
import hosts                    # noqa: E402
import hookwire                 # noqa: E402
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
            # ONE event, not three. This check used to require the whole file back - the
            # behaviour the method's own first line ("Events since `cursor`") denies.
            check("an appended file yields only what was appended",
                  [e.get("prompt") for e in third["events"]] == ["and now?"], str(third["events"]))

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


def test_the_cursor_carries_what_it_did_not_look_at() -> None:
    """Two halves of one defect in `read()`.

    * It re-emitted the WHOLE file whenever the file changed, though its first line promises
      "Events since `cursor`". On a jsonl transcript that grows by one turn, every earlier
      turn arrived again - and the module's own comment elsewhere says double-mining one
      session is how a conversation became two contradictory notes.
    * The returned cursor was built ONLY from the files this call looked at, so an entry for a
      file that still exists but fell outside the `limit` window was dropped. The comment
      claimed vanished files drop out; a quiet file dropped out too, and came back as new.

    `read()` has no caller in the package yet - it is the published adapter contract - so this
    is a latent defect on a surface, not a live duplicate stream.
    """
    print("\n- since the cursor means since the cursor -")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NEVERTWICE_GENERIC_JSONL"] = tmp
        try:
            a = Path(tmp) / "a.jsonl"
            a.write_text('{"speaker":"user","text":"one"}' + "\n", encoding="utf-8", newline="")
            adapter = hosts.get("generic-jsonl")
            first = adapter.read()
            check("the first read takes the file whole", len(first["events"]) == 1,
                  str(first["events"]))

            time.sleep(0.01)
            with a.open("a", encoding="utf-8", newline="") as fh:
                fh.write('{"speaker":"user","text":"two"}' + "\n")
                fh.write('{"speaker":"user","text":"three"}' + "\n")
            second = adapter.read(first["cursor"])
            check("an append yields the appended turns only",
                  [e.get("prompt") for e in second["events"]] == ["two", "three"],
                  str(second["events"]))

            # A REWRITE is not an append: the prefix no longer matches, so the file comes
            # back whole rather than being sliced at a count that means nothing now.
            time.sleep(0.01)
            a.write_text('{"speaker":"user","text":"other"}' + "\n"
                         + '{"speaker":"user","text":"content"}' + "\n",
                         encoding="utf-8", newline="")
            third = adapter.read(second["cursor"])
            check("a rewritten file comes back whole",
                  [e.get("prompt") for e in third["events"]] == ["other", "content"],
                  str(third["events"]))

            # Now a second, newer file pushes the first out of a limit=1 window.
            time.sleep(0.01)
            b = Path(tmp) / "b.jsonl"
            b.write_text('{"speaker":"user","text":"beta"}' + "\n", encoding="utf-8", newline="")
            narrow = adapter.read(third["cursor"], limit=1)
            check("the narrow read only looks at the newest file",
                  [e.get("prompt") for e in narrow["events"]] == ["beta"], str(narrow["events"]))
            check("but the quiet file keeps its cursor entry",
                  str(a) in narrow["cursor"], str(sorted(narrow["cursor"])))
            after = adapter.read(narrow["cursor"])
            check("so it is not mined again when the window widens",
                  after["events"] == [], str(after["events"]))

            # A cursor from the older shape is accepted, not treated as a fresh store.
            legacy = {str(a): [a.stat().st_mtime, a.stat().st_size],
                      str(b): [b.stat().st_mtime, b.stat().st_size]}
            check("a pre-existing cursor shape still means unchanged",
                  adapter.read(legacy)["events"] == [], str(adapter.read(legacy)["events"]))

            b.unlink()
            check("and a vanished file still drops out",
                  str(b) not in adapter.read(after["cursor"])["cursor"])
        finally:
            os.environ.pop("NEVERTWICE_GENERIC_JSONL", None)


def test_the_read_cap_is_the_bytes_it_is_named_for() -> None:
    """MAX_BYTES is documented in bytes, `_jsonl` says the cut lands on a byte boundary, and
    the read passed it to `TextIOWrapper.read()`, which counts CHARACTERS. A Russian
    transcript therefore held twice the cap and a CJK one three times - the same class
    `ingest.py` fixed in its own sweep (review 2026-08 D2), on the module that reads the
    biggest files in the project.

    The property is asserted as an IDENTITY - what comes back is the decode of the file's
    first MAX_BYTES bytes - not as a size of the returned text. The first cut of this check
    measured `len(got.encode())` against the cap, which is the wrong quantity in both
    directions: a cut inside a character replaces one or two source bytes with a three-byte
    U+FFFD, so a correct read of 103 bytes returns 105, and the check passed only because the
    fixture's cap happened to divide by its character width (review 2026-09-21).
    """
    print("\n- the cap is in bytes, on files that are not ASCII -")
    with tempfile.TemporaryDirectory() as tmp:
        cap = hosts.MAX_BYTES
        try:
            for label, ch, width, caps in (("Russian", "\u0438", 2, (200, 103)),
                                           ("CJK", "\u4e2d", 3, (200, 103))):
                path = Path(tmp) / f"{label}.jsonl"
                path.write_bytes((ch * 400).encode("utf-8"))
                raw = path.read_bytes()
                check(f"the {label} fixture really is {width} bytes per character",
                      len(raw) == 400 * width, f"{len(raw)} bytes")
                for n in caps:
                    hosts.MAX_BYTES = n
                    got = hosts._read_capped(path)
                    # an even cap and an odd one: the odd one lands inside a character, which
                    # is the case a size-based assertion gets wrong.
                    check(f"{label} at a {n}-byte cap returns exactly those bytes, decoded",
                          got == raw[:n].decode("utf-8", errors="replace"),
                          f"{len(got)} chars, {len(got.encode('utf-8', 'replace'))} bytes out")
                    check(f"{label} at {n} reads no more than the cap",
                          len(got) <= n and len(got) >= (n // width) - 1,
                          f"{len(got)} chars for {n} bytes")
        finally:
            hosts.MAX_BYTES = cap
        # A cut that lands mid-codepoint is normal - `_jsonl` already discards the clipped
        # final line - so it must decode rather than raise.
        odd = Path(tmp) / "odd.jsonl"
        odd.write_bytes(('{"x": "' + "\u0438" * 40 + '"}').encode("utf-8"))
        hosts.MAX_BYTES = 11                     # 7 ASCII + half of the 3rd Cyrillic pair
        try:
            check("a cut inside a character decodes instead of raising",
                  isinstance(hosts._read_capped(odd), str))
        finally:
            hosts.MAX_BYTES = cap


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

            # A REAL file: since dead_reason() (schema 2) checks existence, a wired entry whose
            # script is not on disk now reads as `dead`, not `wired` - this fixture is about
            # "wired", so `mine` has to point at something that is actually there. A synthetic
            # `.../nevertwice/memory_hook.py` (the pre-schema-2 shape of this fixture) used to
            # read as wired purely by substring match; it now reads `dead`, which is its own
            # property, covered separately below.
            engine = Path(tmp) / "clone" / "nevertwice" / "memory_hook.py"
            engine.parent.mkdir(parents=True)
            engine.write_text("x = 1\n", encoding="utf-8")
            mine = {"type": "command", "command": f'python "{engine}"'}
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


def test_a_hook_whose_files_are_gone_reads_as_dead() -> None:
    """Schema 2: a wired entry whose command names a file that is not there any more reads as
    `dead`, not `wired` - and the detail says whether that blocks the agent, which depends on
    WHICH file is missing (`hookwire.dead_reason`'s whole reason for existing).
    """
    print("\n- a wired entry whose files are gone reads as dead, not wired -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with _wall.walled_in_process(tmp):
            settings = hookwire.settings_path()
            adapter = hosts.get("claude-code")

            # (a) an old-style entry (no shim) whose script is gone: dead, and BLOCKS.
            gone_engine = tmp / "gone_clone" / "nevertwice" / "memory_hook.py"
            settings.write_text(json.dumps({"hooks": {
                "PreToolUse": [{"hooks": [{"type": "command",
                                          "command": f'python "{gone_engine}"'}]}]}}),
                                encoding="utf-8")
            status = adapter.install_status()
            check("(a) an old-style entry with no engine reads as dead",
                  status["state"] == "dead", str(status))
            check("(a) and the detail says it BLOCKS", "BLOCKS" in status["detail"],
                  status["detail"])

            # (b) a shim entry: live shim + missing ENGINE -> dead, does NOT block.
            live_shim = tmp / "shimhome" / "nevertwice" / "hook_shim.py"
            live_shim.parent.mkdir(parents=True)
            live_shim.write_text("x = 1\n", encoding="utf-8")
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command",
                 "command": hookwire.hook_command("python", live_shim, gone_engine)}]}]}}),
                                encoding="utf-8")
            status = adapter.install_status()
            check("(b) a shim with a missing ENGINE reads as dead",
                  status["state"] == "dead", str(status))
            check("(b) and the detail says it does NOT block (exits 0)",
                  "exit 0" in status["detail"] and "BLOCKS" not in status["detail"],
                  status["detail"])

            # (b, continued) now the shim ITSELF is also gone too: for the CURRENT -c form
            # this still does NOT block - the -c payload's own `os.path.isfile(s)` guard is
            # exactly what a later correction to this track added, so a hand-deleted
            # hook_shim.py stops being the "last block path". A missing shim only blocks for
            # a LEGACY 3-token entry (built by hand below - `hookwire.hook_command` no longer
            # builds that shape), which has nothing catching the interpreter's own
            # file-not-found exit.
            live_shim.unlink()
            status = adapter.install_status()
            check("(b) a missing SHIM under the CURRENT -c form reads as dead but does NOT "
                  "block (the -c wrapper degrades itself)",
                  status["state"] == "dead" and "BLOCKS" not in status["detail"]
                  and "exit 0" in status["detail"], str(status))

            legacy_shim_cmd = " ".join(f'"{str(p).replace(chr(92), "/")}"'
                                       for p in ("python", live_shim, gone_engine))
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command", "command": legacy_shim_cmd}]}]}}), encoding="utf-8")
            status = adapter.install_status()
            check("(b) a missing SHIM under the LEGACY 3-token form reads as dead and DOES "
                  "block (nothing catches the interpreter's own file-not-found exit)",
                  status["state"] == "dead" and "BLOCKS" in status["detail"], str(status))

            # (c) a missing interpreter: dead, does not block.
            live_shim.parent.mkdir(parents=True, exist_ok=True)
            live_shim.write_text("x = 1\n", encoding="utf-8")
            real_engine = tmp / "realclone" / "nevertwice" / "memory_hook.py"
            real_engine.parent.mkdir(parents=True)
            real_engine.write_text("x = 1\n", encoding="utf-8")
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command", "command": hookwire.hook_command(
                    tmp / "gone" / "python", live_shim, real_engine)}]}]}}),
                                encoding="utf-8")
            status = adapter.install_status()
            check("(c) a missing interpreter reads as dead",
                  status["state"] == "dead", str(status))
            check("(c) and names 'interpreter missing'",
                  "interpreter missing" in status["detail"], status["detail"])

            # (d) one live entry beside one dead one: still dead overall - a single blocked
            # event is still a block, whatever the other four are doing.
            settings.write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [{"type": "command",
                                            "command": hookwire.hook_command(
                                                "python", live_shim, real_engine)}]}],
                "PreToolUse": [{"hooks": [{"type": "command",
                                          "command": f'python "{gone_engine}"'}]}],
            }}), encoding="utf-8")
            status = adapter.install_status()
            check("(d) one live + one dead hook still reads as dead overall",
                  status["state"] == "dead", str(status))

            # (e) the report and the schema agree dead is a real state.
            report = hosts.status_report()
            check("(e) 'dead' is one of the declared states", "dead" in hosts.STATES)
            check("(e) the report's counts include 'dead'", "dead" in report["counts"],
                  str(report["counts"]))
            check("(e) schema_version is 2", hosts.SCHEMA_VERSION == 2)

            # (f) the CLI marks it [dead], not [wired].
            env = _wall.walled(tmp)
            r = subprocess.run([sys.executable, "-m", "nevertwice.hosts"], cwd=str(ROOT),
                               env=env, capture_output=True, text=True, timeout=60)
            check("(f) `python -m nevertwice.hosts` exits 0", r.returncode == 0,
                  f"exit {r.returncode}: {r.stderr[-300:]}")
            check("(f) it prints [dead], not [wired], for claude-code",
                  "[dead]" in r.stdout and "[wired]" not in r.stdout,
                  r.stdout)


def test_uninstall_also_removes_the_shim() -> None:
    print("\n- uninstall undoes the shim too, and only a shim that is really ours -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with _wall.walled_in_process(tmp):
            settings = hookwire.settings_path()
            adapter = hosts.get("claude-code")
            engine = tmp / "clone" / "nevertwice" / "memory_hook.py"
            engine.parent.mkdir(parents=True)
            engine.write_text("x = 1\n", encoding="utf-8")

            verb = hookwire.write_shim(settings, (ROOT / "nevertwice" / "hook_shim.py")
                                       .read_bytes(), dry_run=False)
            check("the shim template installs clean", verb == "written", verb)
            shim = hookwire.shim_path(settings)
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command",
                 "command": hookwire.hook_command("python", shim, engine)}]}]}}),
                                encoding="utf-8")

            preview = adapter.uninstall(dry_run=True)
            check("dry-run leaves the shim on disk", shim.is_file())
            check("...but names it in what it would remove",
                  any(str(shim) in c for c in preview["changed"]), str(preview["changed"]))

            done = adapter.uninstall(dry_run=False)
            check("a real uninstall removes the hook entry",
                  done["ok"] and len(done["removed"]) == 1, str(done))
            check("...and removes the shim too", not shim.is_file())
            check("...and reports it in 'changed'",
                  any(str(shim) in c for c in done["changed"]), str(done["changed"]))

            # A file at the shim's path WITHOUT the marker is not ours to delete, whoever put
            # it there and however it got there - `remove_shim`'s own contract, exercised here
            # through the adapter rather than directly against `hookwire`.
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text("not a nevertwice shim\n", encoding="utf-8")
            settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
                {"type": "command",
                 "command": hookwire.hook_command("python", shim, engine)}]}]}}),
                                encoding="utf-8")
            done2 = adapter.uninstall(dry_run=False)
            check("a marker-less file at the shim path survives uninstall",
                  shim.is_file() and shim.read_text(encoding="utf-8") == "not a nevertwice shim\n",
                  str(done2))


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


def test_an_uninstall_that_cannot_write_answers_instead_of_raising() -> None:
    """Every other path out of `uninstall` hands the caller a dict; the two writes did not.

    The read above them already answers a failure with `ok: False` and a detail. The backup
    and the settings rewrite were bare, so a read-only directory or a full disk raised OSError
    out of a method whose callers - `install_status` among them - are written on the promise
    that they work when something is wrong. Both branches are driven, because which write
    failed decides what is on disk and therefore what the answer has to say.
    """
    print("\n- an uninstall that cannot write says so -")
    for label, break_it in (("the backup cannot be written", "backup"),
                            ("the settings rewrite fails", "atomic")):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            projects = Path(tmp) / "projects"
            projects.mkdir()
            os.environ["NEVERTWICE_CLAUDE_SETTINGS"] = str(settings)
            os.environ["NEVERTWICE_CLAUDE_PROJECTS"] = str(projects)
            try:
                mine = {"type": "command",
                        "command": "python .../nevertwice/memory_hook.py"}
                before = json.dumps({"hooks": {"PreToolUse": [{"hooks": [mine]}]}})
                settings.write_text(before, encoding="utf-8")
                adapter = hosts.get("claude-code")
                real_atomic = hosts.m.write_atomic
                if break_it == "backup":
                    # A directory where the backup file belongs: the write raises OSError the
                    # way a read-only parent or a full disk does, without needing either.
                    settings.with_suffix(".json.nevertwice-backup").mkdir()
                else:
                    def _boom(*a, **k):
                        raise OSError(28, "No space left on device")
                    hosts.m.write_atomic = _boom
                try:
                    result = adapter.uninstall(dry_run=False)
                except Exception as exc:                       # noqa: BLE001 - the finding
                    check(f"{label}: answers instead of raising", False,
                          f"{type(exc).__name__}: {exc}")
                    continue
                finally:
                    hosts.m.write_atomic = real_atomic
                check(f"{label}: answers instead of raising", True)
                check(f"{label}: and the answer is not ok", result.get("ok") is False,
                      str(result)[:120])
                check(f"{label}: and it names what is still on disk",
                      "unchanged" in result.get("detail", "")
                      or "still holds" in result.get("detail", ""),
                      result.get("detail", "")[:120])
                check(f"{label}: the settings file was not half-written",
                      settings.read_text(encoding="utf-8") == before,
                      settings.read_text(encoding="utf-8")[:80])
            finally:
                os.environ.pop("NEVERTWICE_CLAUDE_SETTINGS", None)
                os.environ.pop("NEVERTWICE_CLAUDE_PROJECTS", None)


def test_install_py_uninstall_is_reachable_and_takes_only_ours() -> None:
    """The reversible uninstall existed in `hosts` with nothing a person could run to reach it.

    The third premortem (2026-09-23) named the likeliest launch failure: the hooks point into the
    checkout, a trial user removes the package and the clone while they are still wired, every hook
    command exits 2, and Claude Code treats exit 2 from PreToolUse and UserPromptSubmit as a block -
    every edit, command and prompt refused. `python install.py --uninstall` is the command the
    README now tells people to run first. Driven as a real process against a settings file it is
    pointed at, so the check is of the command a person types, not of the adapter underneath.
    """
    print("\n- `install.py --uninstall` reaches the reversible uninstall -")
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        settings = Path(tmp) / "settings.json"
        mine = {"type": "command", "command": "python D:/gone/nevertwice/memory_hook.py"}
        theirs = {"type": "command", "command": "python /home/me/my_own_hook.py"}
        settings.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"hooks": [mine, theirs]}],
            "UserPromptSubmit": [{"hooks": [mine]}]}}), encoding="utf-8")
        #: HOME and USERPROFILE point into the temp dir as well, so the process cannot reach the
        #: real ~/.claude even when the installer under test is wrong. It was: checking this test
        #: against the previous install.py - which did not know --uninstall, ran the full install,
        #: and read only Path.home() - wrote five hooks into the owner's real settings.json
        #: (2026-09-23, restored from the installer's backup). A test must be safe against the
        #: code it tests being broken, because that is exactly when it runs against it.
        env = _wall.walled(tmp)          # walled() names settings.json the same way: tmp/settings.json
        before = settings.read_text(encoding="utf-8")

        typo = subprocess.run([sys.executable, str(root / "install.py"), "--unistall"],
                              capture_output=True, text=True, env=env, timeout=120)
        check("an unknown flag is refused before anything is written (a typo used to run the "
              "full install)",
              typo.returncode == 2 and settings.read_text(encoding="utf-8") == before
              and not (Path(tmp) / ".claude").exists(),
              f"exit {typo.returncode}; {(typo.stdout + typo.stderr)[-200:]}")

        dry = subprocess.run([sys.executable, str(root / "install.py"), "--uninstall", "--print"],
                             capture_output=True, text=True, env=env, timeout=120)
        check("`--uninstall --print` exits 0 and names both entries it would remove",
              dry.returncode == 0 and dry.stdout.count("nevertwice/memory_hook.py") == 2,
              (dry.stdout + dry.stderr)[-300:])
        check("and writes nothing", settings.read_text(encoding="utf-8") == before)

        real = subprocess.run([sys.executable, str(root / "install.py"), "--uninstall"],
                              capture_output=True, text=True, env=env, timeout=120)
        data = json.loads(settings.read_text(encoding="utf-8"))
        commands = [h.get("command", "") for groups in data.get("hooks", {}).values()
                    for g in groups for h in g.get("hooks", [])]
        check("`--uninstall` exits 0", real.returncode == 0, (real.stdout + real.stderr)[-300:])
        check("no hook command is left pointing at nevertwice - nothing can exit 2 into a block",
              not any("nevertwice" in c for c in commands), str(commands))
        check("the user's own hook stays", commands == [theirs["command"]], str(commands))

        #: The installer and the adapter must find settings.json the same way - the incident's
        #: root was install.py reading only Path.home() while the adapter honoured the override.
        #: Above, HOME and the override share one temp dir, so a Path.home()-only installer passed
        #: (the auditor's E2 mutation, a617af2). Here they differ; --print writes nothing.
        home = Path(tmp) / "home"
        home.mkdir()
        split = dict(env, HOME=str(home), USERPROFILE=str(home))
        plan = subprocess.run([sys.executable, str(root / "install.py"), "--print"],
                              capture_output=True, text=True, env=split, timeout=120)
        check("the installer wires the settings file the adapter reads, not one under HOME",
              plan.returncode == 0 and f"[hooks] {settings}" in plan.stdout
              and not (home / ".claude").exists(),
              (plan.stdout + plan.stderr)[-300:])

    print("\n- `--uninstall` still finds its own entries after the CLONE ITSELF has moved -")
    with tempfile.TemporaryDirectory() as tmp2:
        moved_home = Path(tmp2) / "home"
        moved_home.mkdir()
        env2 = _wall.walled(moved_home)
        settings2 = Path(env2["NEVERTWICE_CLAUDE_SETTINGS"])

        clone_a = Path(tmp2) / "clone_a"
        shutil.copytree(root / "nevertwice", clone_a / "nevertwice",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(root / "install.py", clone_a / "install.py")
        clone_b = Path(tmp2) / "clone_b"
        os.rename(clone_a, clone_b)                  # the clone moved AFTER it was installed

        old_style = {"type": "command", "command": "python D:/gone/nevertwice/memory_hook.py"}
        decoy_shim = {"type": "command", "command": hookwire.hook_command(
            "python", Path(tmp2) / "decoy" / "nevertwice" / "hook_shim.py",
            Path(tmp2) / "decoy" / "nevertwice" / "memory_hook.py")}
        theirs2 = {"type": "command", "command": "python /home/me/my_own_hook.py"}
        settings2.write_text(json.dumps({"hooks": {
            "PreToolUse": [{"hooks": [old_style, theirs2]}],
            "UserPromptSubmit": [{"hooks": [decoy_shim]}]}}), encoding="utf-8")

        # A REAL, marked shim at the settings-relative path (independent of what any entry's
        # command names) - uninstall has to find and remove THIS one too.
        real_shim = hookwire.shim_path(settings2)
        real_shim.parent.mkdir(parents=True, exist_ok=True)
        real_shim.write_bytes((root / "nevertwice" / "hook_shim.py").read_bytes())

        moved = subprocess.run([sys.executable, str(clone_b / "install.py"), "--uninstall"],
                               capture_output=True, text=True, env=env2, timeout=120)
        data2 = json.loads(settings2.read_text(encoding="utf-8"))
        commands2 = [h.get("command", "") for groups in data2.get("hooks", {}).values()
                    for g in groups for h in g.get("hooks", [])]
        check("`--uninstall` from the MOVED clone exits 0", moved.returncode == 0,
              (moved.stdout + moved.stderr)[-300:])
        check("no command is left pointing at nevertwice, moved or not - neither entry needed "
              "its OWN file to exist to be recognised as ours",
              not any("nevertwice" in c for c in commands2), str(commands2))
        check("the user's own hook stays", commands2 == [theirs2["command"]], str(commands2))
        check("the real shim at the settings-relative path is gone too", not real_shim.is_file())


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"
    assert _wall.settings_unchanged(), (
        "the real ~/.claude/settings.json changed during this suite - see _wall.py")


def main() -> int:
    for fn in (test_install_py_uninstall_is_reachable_and_takes_only_ours,
               test_every_adapter_answers_all_five_questions,
               test_discovery_never_needs_a_live_agent,
               test_four_hosts_produce_equivalent_events,
               test_codex_scaffolding_is_skipped,
               test_cursoring_is_incremental,
               test_the_cursor_carries_what_it_did_not_look_at,
               test_a_truncated_tail_does_not_lose_the_session,
               test_the_read_cap_is_the_bytes_it_is_named_for,
               test_claude_code_install_status_and_reversible_uninstall,
               test_a_hook_whose_files_are_gone_reads_as_dead,
               test_uninstall_also_removes_the_shim,
               test_claude_code_is_not_swept_as_well_as_hooked,
               test_cursor_explains_itself_instead_of_returning_nothing,
               test_the_status_report_covers_every_host,
               test_an_uninstall_that_cannot_write_answers_instead_of_raising):
        fn()
    print(f"\nhosts: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
