#!/usr/bin/env python3
"""Self-checks for import_memory - the bring-your-memory importers.

Each source format gets a temp fixture; writes go through the real api.remember_lessons
path into a temp vault with the embedder down (the no-model box), so the checks also
pin that imported notes are lexically recallable at once. Idempotency: a second run
imports nothing new; --dry-run writes nothing."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import api                      # noqa: E402
import import_memory as im      # noqa: E402
import memory_hook as m         # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    print(("  ok  " if cond else "  FAIL ") + name)
    P += 1 if cond else 0
    F += 0 if cond else 1


def _no_model_vault(tmp):
    return (mock.patch.object(m, "VAULT", Path(tmp)),
            mock.patch.object(m, "embedder_available", lambda *a, **k: False),
            mock.patch.object(m, "embed_text", lambda *a, **k: None),
            mock.patch.object(m, "git_autocommit"))


def test_claude_import_end_to_end():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as vault:
        mem = Path(src) / "D--Coding-someproj" / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("# Memory Index\n- [x](x.md)\n", encoding="utf-8")
        (mem / "gpu-batch-size.md").write_text(
            "---\nname: gpu-batch-size\ndescription: CUDA OOM fix - lower the batch size\n"
            "metadata:\n  type: feedback\n---\n\nAt batch=64 the 5090 OOMs; use 32.\n",
            encoding="utf-8")
        (mem / "db-choice.md").write_text(
            "---\nname: db-choice\ndescription: chose Postgres over Mongo\n"
            "metadata:\n  type: project\n---\n\nRelational integrity mattered more.\n",
            encoding="utf-8")
        p1, p2, p3, p4 = _no_model_vault(vault)
        with p1, p2, p3, p4:
            res = im.run_import("claude", Path(src), "user")
            check("claude: found 2 (index skipped)", res["found"] == 2)
            check("claude: wrote 2", res["written"] == 2)
            hits = api.recall("cuda out of memory batch", project="user", k=3)
            check("claude: imported note recallable with no embedder",
                  any("CUDA OOM" in (h.get("title") or "") for h in hits))
            again = im.run_import("claude", Path(src), "user")
            check("claude: rerun imports nothing", again["new"] == 0 and again["written"] == 0)


def test_chatgpt_and_dry_run():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as vault:
        exp = Path(src) / "memories.txt"
        exp.write_text("Prefers concise answers without preamble.\n\n"
                       "- Works on a Windows 11 machine with an RTX 5090.\n\nok\n",
                       encoding="utf-8")
        p1, p2, p3, p4 = _no_model_vault(vault)
        with p1, p2, p3, p4:
            dry = im.run_import("chatgpt", exp, "user", dry_run=True)
            check("chatgpt: dry-run finds 2 (noise line skipped)", dry["new"] == 2)
            check("chatgpt: dry-run writes nothing",
                  dry["written"] == 0 and not (Path(vault) / ".imported.json").exists())
            res = im.run_import("chatgpt", exp, "user")
            check("chatgpt: real run writes 2", res["written"] == 2)


def test_cursor_and_agents():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as vault:
        rules = Path(src) / ".cursor" / "rules"
        rules.mkdir(parents=True)
        (rules / "style.mdc").write_text(
            "---\ndescription: Use strict TypeScript, no any\nglobs: '**/*.ts'\n---\n\n"
            "Never use the any type; prefer unknown.\n", encoding="utf-8")
        (Path(src) / ".cursorrules").write_text(
            "Always run the linter before committing.\n\nKeep functions under 40 lines.\n",
            encoding="utf-8")
        ag = Path(src) / "AGENTS.md"
        ag.write_text("# Agents\n\n- Build with `make all` before testing\n"
                      "<!-- NEVERTWICE:START -->\n- our own exported card line\n"
                      "<!-- NEVERTWICE:END -->\n- Deploys go through staging first\n",
                      encoding="utf-8")
        p1, p2, p3, p4 = _no_model_vault(vault)
        with p1, p2, p3, p4:
            rc = im.run_import("cursor", Path(src), "proj")
            check("cursor: mdc + 2 cursorrules blocks", rc["found"] == 3 and rc["written"] == 3)
            ra = im.run_import("agents", ag, "proj")
            check("agents: 2 bullets, managed block skipped",
                  ra["found"] == 2 and ra["written"] == 2)
            hits = api.recall("typescript any type", project="proj", k=3)
            check("cursor: rule recallable", any("any" in (h.get("title") or "").lower()
                                                 for h in hits))


def test_frontmatter_parser_never_raises():
    ok = im._frontmatter("no frontmatter at all")[0] == {}
    ok2 = im._frontmatter("---\nbroken")[0] == {}
    meta, body = im._frontmatter("---\nname: x\nmetadata:\n  type: feedback\n---\nBody")
    check("frontmatter: plain text and broken headers degrade to empty meta", ok and ok2)
    check("frontmatter: name + nested type parsed", meta.get("name") == "x"
          and meta.get("type") == "feedback" and body == "Body")


if __name__ == "__main__":
    print("=== import_memory self-checks ===")
    test_claude_import_end_to_end()
    test_chatgpt_and_dry_run()
    test_cursor_and_agents()
    test_frontmatter_parser_never_raises()
    print(f"\nimport: {P} passed, {F} failed")
    sys.exit(1 if F else 0)
