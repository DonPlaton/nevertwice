"""Evidence spans (ledger J1): a note carries the verbatim transcript lines it came from.

Alignment is arithmetic - stemmed-token overlap, two floors, ties to the earlier line, a
word-boundary cut, secrets redacted - so every property here is checkable without a model.
Then the note on disk (frontmatter + block, the body parser unmoved), the post-step that finds
a session's notes through the session note, and the three read surfaces that hand the spans
on: `api.recall`, `api.as_of`, `api.format_note`. The layer is off the ranking path by
construction; the check that it stays off is that the embed cache never sees a span.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import
from unittest import mock  # noqa: E402

import memory_hook as m  # noqa: E402
import evidence as ev  # noqa: E402
import api  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


d = make_sandbox(m, "evidence_", offline=True)

TRANSCRIPT = """Working directory: D:/proj
Trigger: ingest

user: Came back to the API client after the incident.
assistant: That earlier decision is off: the HTTP client timeout is 5 seconds.
assistant: Ran the full test suite afterwards and it stayed green.
ok
user: The cache layer is Redis 7, documented in the README section that was already stale.
assistant: The deploy token is ghp_abcdefghijklmnopqrstuvwxyz0123456789 and the HTTP client timeout stays 5 seconds.
"""

print("\n- candidate lines -")
lines = ev.candidate_lines(TRANSCRIPT)
check("the ingest header is not a candidate", not any(ln.startswith("Working directory") or ln.startswith("Trigger") for ln in lines))
check("short lines are dropped", "ok" not in lines)
check("turn labels are stripped, the words stay",
      any(ln.startswith("That earlier decision is off") for ln in lines) and not any(ln.startswith("assistant:") for ln in lines))
check("every remaining line is long enough to carry a fact", all(len(ln) >= ev.MIN_LINE_CHARS for ln in lines))

print("\n- alignment -")
spans = ev.align("HTTP client timeout is 5 seconds\nthe timeout was lowered from 30 to 5 seconds", lines, max_spans=2)
check("the line that restates the note is the first span", spans and spans[0].startswith("That earlier decision is off: the HTTP client timeout is 5 seconds"), str(spans))
check("a second aligned line follows, secret redacted",
      len(spans) == 2 and "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in spans[1] and "timeout stays 5 seconds" in spans[1], str(spans))
check("an unrelated note gets no span rather than a weak one",
      ev.align("database migrations run in a transaction with alembic", lines) == [])
check("max_spans caps the list", len(ev.align("HTTP client timeout is 5 seconds", lines, max_spans=1)) == 1)
check("zero spans disables the layer", ev.align("HTTP client timeout is 5 seconds", lines, max_spans=0) == [])
check("no candidate lines, no spans", ev.align("HTTP client timeout", []) == [])
check("a note with no stems, no spans", ev.align("   ", lines) == [])
tie = ["the worker count is four in staging", "the worker count is four in production and staging"]
t = ev.align("worker count is four", tie, max_spans=2)
check("ties go to the earlier line", t and t[0] == tie[0], str(t))
dup = ev.align("worker count is four", ["the worker count is four", "the worker count is four"], max_spans=3)
check("duplicate lines collapse to one span", dup == ["the worker count is four"], str(dup))
long_line = "the HTTP client timeout is 5 seconds " * 20
cut = ev.align("HTTP client timeout is 5 seconds", [long_line], max_chars=60)
check("a long line is cut at a word boundary, verbatim, no ellipsis",
      cut and len(cut[0]) <= 60 and long_line.startswith(cut[0]) and not cut[0].endswith(("…", "...")), str(cut))
tok_once = [set(m._token_list(ln)) for ln in lines]
check("a caller may tokenise the transcript once",
      ev.align("HTTP client timeout is 5 seconds", lines, line_tokens=tok_once, max_spans=1)
      == ev.align("HTTP client timeout is 5 seconds", lines, max_spans=1))

print("\n- the note on disk -")
proj, date = "proj", "2026-03-01"
stem = m.write_typed_note("Decisions", {"title": "HTTP client timeout is 5 seconds",
                                        "description": "The timeout was lowered from 30 to 5 seconds after the incident.",
                                        "confidence": 0.9},
                          proj, date, ["api"], "decision", session_stem_="2026-03-01-1000-proj-session-deadbeef")
fp = m.VAULT / "Decisions" / f"{stem}.md"
check("a note was written", fp.exists(), stem)
check("a fresh note carries no evidence", ev.read(fp) == [] and ev.for_hit({"stem": stem, "ntype": "decision"}) == [])
ok = ev.stamp(fp, ["That earlier decision is off: the HTTP client timeout is 5 seconds.", "second line"])
text = fp.read_text(encoding="utf-8")
check("stamp writes frontmatter and a block", ok and ev.read(fp) == ["That earlier decision is off: the HTTP client timeout is 5 seconds.", "second line"]
      and "## Evidence" in text and '- "second line"' in text, text[:400])
check("the block sits before the provenance lines", text.index("## Evidence") < text.index("**Project:**"))
title, desc, prevention = m._parse_note_body(text.split("\n"))
check("the body parser still reads the lesson, not the quote",
      title == "HTTP client timeout is 5 seconds" and desc.startswith("The timeout was lowered") and prevention == "", f"{title!r} {desc!r} {prevention!r}")
ev.stamp(fp, ["only one line now"])
text2 = fp.read_text(encoding="utf-8")
check("a second stamp replaces the block instead of stacking", text2.count("## Evidence") == 1 and "second line" not in text2 and ev.read(fp) == ["only one line now"])
ev.stamp(fp, [])
text3 = fp.read_text(encoding="utf-8")
check("stamping nothing removes the block and empties the list", "## Evidence" not in text3 and ev.read(fp) == [])
check("frontmatter fence survives every stamp", text3.startswith("---\n") and text3.count("\n---\n") == 1)
# a description-less note: the quote must not become the description
stem_nd = m.write_typed_note("Mistakes", {"title": "forgot the lock", "description": "", "prevention": "take the vault lock first"},
                             proj, date, ["api"], "mistake", session_stem_="2026-03-01-1000-proj-session-deadbeef")
fp_nd = m.VAULT / "Mistakes" / f"{stem_nd}.md"
ev.stamp(fp_nd, ["we forgot the lock and two writers raced"])
_, desc_nd, prev_nd = m._parse_note_body(fp_nd.read_text(encoding="utf-8").split("\n"))
check("a description-less note does not acquire the quote as its description",
      desc_nd == "" and prev_nd == "take the vault lock first", f"{desc_nd!r}")

print("\n- the post-step finds a session's notes through the session note -")
sid = "ingest-evidence-test-0001"
sess_stem = m.reserve_session_stem(date, "10:00", proj, sid)
s1 = m.write_typed_note("Decisions", {"title": "HTTP client timeout is 5 seconds now",
                                      "description": "Lowered from 30 seconds to 5 seconds after the incident."},
                        proj, date, ["api"], "decision", session_stem_=sess_stem)
s2 = m.write_typed_note("Patterns", {"title": "run the full test suite after a client change",
                                     "description": "The suite stayed green after the timeout change."},
                        proj, date, ["api"], "pattern", session_stem_=sess_stem)
s3 = m.write_typed_note("Mistakes", {"title": "kubernetes ingress misconfigured",
                                     "description": "The ingress class annotation was missing on the staging cluster.",
                                     "prevention": "lint the manifests"},
                        proj, date, ["api"], "mistake", session_stem_=sess_stem)
m.write_session_note(proj, date, "10:00", "worked on the client", "D:/proj", sid, ["api"],
                     {"pattern": [s2], "mistake": [s3], "decision": [s1]}, "ingest", stem=sess_stem)
res = ev.attach_for_session(sid, TRANSCRIPT, project=proj)
check("three notes were visited", res["notes"] == 3, str(res))
check("the two notes the transcript restates were stamped; the unrelated one was not",
      res["stamped"] == 2 and ev.read(m.VAULT / "Decisions" / f"{s1}.md") and ev.read(m.VAULT / "Patterns" / f"{s2}.md")
      and ev.read(m.VAULT / "Mistakes" / f"{s3}.md") == [], str(res))
check("the timeout note quotes the timeout line",
      any("timeout is 5 seconds" in s for s in ev.read(m.VAULT / "Decisions" / f"{s1}.md")))
check("no span carries the redacted token", all("ghp_abcdefghijklmnopqrstuvwxyz" not in s
                                                for st_, nt in ((s1, "Decisions"), (s2, "Patterns"))
                                                for s in ev.read(m.VAULT / nt / f"{st_}.md")))
check("the cost is counted", ev.STATS["sessions"] >= 1 and ev.STATS["notes"] >= 3 and res["ms"] >= 0)
check("an unknown session is a no-op", ev.attach_for_session("no-such-session", TRANSCRIPT) == {"notes": 0, "stamped": 0, "spans": 0, "ms": 0.0})
check("an empty transcript is a no-op", ev.attach_for_session(sid, "   ")["notes"] == 0)
with mock.patch.object(ev, "MAX_SPANS", 0):
    check("the layer off: nothing visited", ev.attach_for_session(sid, TRANSCRIPT)["notes"] == 0)

print("\n- read surfaces -")
m.update_embeddings([(s1, "decision", proj, "HTTP client timeout is 5 seconds now",
                      "Lowered from 30 seconds to 5 seconds after the incident.", "", 0.9),
                     (s2, "pattern", proj, "run the full test suite after a client change",
                      "The suite stayed green after the timeout change.", "", None),
                     (s3, "mistake", proj, "kubernetes ingress misconfigured",
                      "The ingress class annotation was missing on the staging cluster.", "lint the manifests", None)])
hits = api.recall("http client timeout seconds", project=proj, k=3)
check("recall returns hits without an embedder (lexical)", bool(hits), str(hits)[:200])
top = next((h for h in hits if h["stem"] == s1), None)
check("the timeout note is among them and carries its evidence",
      top is not None and any("timeout is 5 seconds" in s for s in top.get("evidence", [])), str(top))
check("every hit has the field, empty for a note without spans",
      all(isinstance(h.get("evidence"), list) for h in hits)
      and all(h["evidence"] == [] for h in hits if h["stem"] == s3))
cache = m.load_embed_cache()
check("no span reached the embed cache (the ranker is untouched by construction)",
      all("That earlier decision" not in str(e) for e in cache.values()))
held = api.as_of("http client timeout", "2026-04-01", proj)
h1 = next((h for h in held if h["stem"] == s1), None)
check("as_of returns the evidence and its description quotes the line",
      h1 is not None and h1.get("evidence") and "timeout is 5 seconds" in h1["description"], str(h1)[:300])
rendered = api.format_note(top)
check("format_note renders the quote on its own line", 'Evidence: "' in rendered and "timeout is 5 seconds" in rendered, rendered)
check("format_note without the field is unchanged",
      api.format_note({"ntype": "mistake", "title": "OOM", "description": "ran out of memory", "prevention": "lower batch"})
      == "MISTAKE - OOM\nran out of memory\nPrevention: lower batch")

print("\n- capture_session runs the post-step, and a failure there never costs the capture -")
seen = {}


def fake_ps(sid_, cwd, path, trigger, db, run_log=None, agent=None, transcript_text=None,
            project_override=None, timestamp=None):
    (run_log if run_log is not None else []).append({"project": project_override, "patterns": 1,
                                                     "mistakes": 0, "decisions": 0})
    return True


def fake_attach(sid_, text, project=None, only_stems=None):
    seen["sid"] = sid_
    seen["text"] = text
    return {"notes": 1, "stamped": 1, "spans": 2, "ms": 0.1}


with mock.patch.object(m, "llm_available", return_value=True), \
     mock.patch.object(m, "acquire_lock", return_value=True), \
     mock.patch.object(m, "release_lock"), \
     mock.patch.object(m, "load_processed", return_value={}), \
     mock.patch.object(m, "process_session", side_effect=fake_ps), \
     mock.patch.object(m, "rebuild_index"), mock.patch.object(m, "archive_old_sessions"), \
     mock.patch.object(m, "archive_old_typed"), mock.patch.object(m, "prune_processed_db"), \
     mock.patch.object(m, "git_autocommit"):
    with mock.patch.object(ev, "attach_for_session", side_effect=fake_attach):
        r = api.capture_session("a transcript with a fact", project="proj", session_id="sid-1")
        check("the post-step saw the session and the transcript", seen.get("sid") == "sid-1" and seen.get("text") == "a transcript with a fact")
        check("the summary counts the spans", r["stored"] and r["evidence_spans"] == 2, str(r))
    with mock.patch.object(ev, "attach_for_session", side_effect=RuntimeError("boom")):
        r = api.capture_session("a transcript", project="proj", session_id="sid-2")
        check("a failing post-step leaves the capture stored with zero spans", r["stored"] and r["evidence_spans"] == 0, str(r))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
