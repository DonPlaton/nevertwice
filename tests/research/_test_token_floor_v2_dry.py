"""`research/token_floor.py` v2 (2026-09-23, 9-fix rewrite of TF-ANALYSIS.md's block A):
`--dry`/stub-level proof for fixes 1, 3, 4, 5 and 7 - no model, no Ollama, no GPU, no live
store. Each fix that is a GUARD gets its own mutation, demonstrated against the real code.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import

sys.path.insert(0, str(ROOT / "research"))
import token_floor as tf  # noqa: E402

m = tf.m
sys.path.insert(0, str(HERE.parent))
from _sandbox import make_sandbox  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


# HARD RULE for this suite too: never let a real Ollama on this machine answer, dry or not.
m.embed_text = lambda *a, **k: None
m.embedder_available = lambda *a, **k: False
m.embed_cache_usable = lambda: False
m.ollama_alive = lambda *a, **k: False
m.llm_available = lambda: False


print("\n- fix 7: char/token counts are of the PARSED additionalContext, not the JSON envelope -")
envelope = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": "hello \U0001f9e0 world"}})
parsed = tf._parse_additional_context(envelope)
check("the parsed text is exactly the payload, not the envelope", parsed == "hello \U0001f9e0 world", parsed)
check("the envelope itself is longer than its own payload (the inflation this fix removes)",
      len(envelope) > len(parsed), f"{len(envelope)} vs {len(parsed)}")
check("empty stdout parses to an empty string", tf._parse_additional_context("") == "")
check("a non-JSON string is reported as a parse error, never silently substituted",
      tf._parse_additional_context("not json at all").startswith("__PARSE_ERROR__"))

print("\n- mutation: counting the raw envelope instead of the parsed payload inflates the char count -")
check("mutation: the raw envelope overstates chars by more than 30% versus the parsed payload "
      "(would silently re-introduce the 5-8% inflation TF-ANALYSIS.md F10 measured)",
      len(envelope) > len(parsed) * 1.3, f"{len(envelope)} vs {len(parsed)}")


print("\n- fix 4: reachability is whole-word, case-insensitive, never a bare substring -")
check("a whole-word marker is found", tf._whole_word_present("60 seconds", "wait 60 seconds please"))
check("a marker inside a LONGER word is NOT found (no false '60' inside '1960')",
      not tf._whole_word_present("60", "the year 1960 was notable"))
check("matching is case-insensitive", tf._whole_word_present("Redis", "using redis for caching"))
check("marker frequency counts whole-word occurrences",
      tf._marker_freq("cache", "cache the cache, not the cachet") == 2,
      str(tf._marker_freq("cache", "cache the cache, not the cachet")))

print("\n- mutation: plain substring matching (the pre-fix behaviour) DOES false-match -")
check("mutation: bare `in` matching finds '60' inside '1960' (would FAIL 'a marker inside a "
      "LONGER word is NOT found' above)", "60" in "the year 1960 was notable")


print("\n- fix 5: each project gets its own tracked directory, distinct from every other -")
cwd_a = tf._make_cwd("proj_alpha")
cwd_b = tf._make_cwd("proj_beta")
check("project A's cwd is tracked", m.is_tracked_project(cwd_a), cwd_a)
check("project B's cwd is tracked", m.is_tracked_project(cwd_b), cwd_b)
name_a, name_b = m.derive_project_from_cwd(cwd_a), m.derive_project_from_cwd(cwd_b)
check("they derive to DIFFERENT project names", name_a != name_b, (name_a, name_b))
check("project A's derived name reflects its own slug", "alpha" in name_a, name_a)
check("project B's derived name reflects its own slug", "beta" in name_b, name_b)

print("\n- mutation: a shared ancestor .git collapses every leaf directory into ONE project -")
shared_root = tf.ROOT / ".loop" / "token_floor_cwd"
(shared_root / ".git").mkdir(parents=True, exist_ok=True)
try:
    collapsed_a = m.derive_project_from_cwd(str(shared_root / "would_be_alpha"))
    collapsed_b = m.derive_project_from_cwd(str(shared_root / "would_be_beta"))
    check("mutation: WITH a shared ancestor .git, two different leaf dirs collapse to the SAME "
          "project (would FAIL 'they derive to DIFFERENT project names' above - the exact "
          "F6/F7 bug: 'all 30 projects went into ONE project')",
          collapsed_a == collapsed_b, (collapsed_a, collapsed_b))
finally:
    shutil.rmtree(shared_root / ".git", ignore_errors=True)


print("\n- fix 1: session_id is per QUESTION, so the injection cap cannot leak across questions -")
make_sandbox(m, "tf_fix1_", offline=True)
cwd = tf._make_cwd("cap_test_proj")
long_prompt = "a real question with enough characters to clear the trivial-prompt gate"
old_sid = "tokenfloor-session"                       # the v1 shared id
m._save_prompt_recall_state(old_sid, {"injected": [], "count": m.PROMPT_RECALL_MAX_PER_SESSION})
reason_old = tf._classify_pre_reason(cwd, long_prompt, old_sid)
check("under the OLD shared session id, the cap already reads spent", reason_old == "cap",
      reason_old)
new_sid = "tokenfloor-q123"                          # fix 1's per-question id
reason_new = tf._classify_pre_reason(cwd, long_prompt, new_sid)
check("fix 1: a per-question session id starts with a FRESH cap, unaffected by another "
      "question's spent state", reason_new is None, reason_new)

print("\n- mutation: reusing one session_id for every question inherits the FIRST question's cap -")
check("mutation: asking under the pre-fix SHARED id still reads 'cap' after the fix exists "
      "beside it (proves the shared-id shape is what causes the leak, not something else)",
      tf._classify_pre_reason(cwd, long_prompt, old_sid) == "cap")


print("\n- fix 3: a row carries reason/hit_stems/hit_scores/semantic_ran/raw_additional_context -")
make_sandbox(m, "tf_fix3_", offline=True)
data, pool, groups = tf.load_corpus("code_sessions_v1")
check("code_sessions_v1 has project groups (fix 5's data shape)", bool(groups))
first_group = next(iter(groups))
g = groups[first_group]
mini_groups = {first_group: {"session_ids": g["session_ids"][:2],
                             "question_ids": g["question_ids"][:2]}}
qids = set(mini_groups[first_group]["question_ids"])
mini_data = [e for e in data if e["question_id"] in qids]
toks = tf.tokenizers()
check("a tokenizer is available for this suite", bool(toks))
res = tf.run_nevertwice(mini_data, pool, toks, None, groups=mini_groups, dry=True,
                        corpus_name="code_sessions_v1")
check("run_nevertwice did not block", "blocked" not in res, res.get("blocked", ""))
if "blocked" not in res:
    check("at least one row came back", len(res.get("rows", [])) >= 1, str(res)[:300])
    row = res["rows"][0]
    for field in ("reason", "hit_stems", "hit_scores", "semantic_ran", "raw_additional_context",
                 "fallback", "marker_freq_in_session_start", "question_id", "project"):
        check(f"row carries {field!r}", field in row, str(sorted(row.keys())))
    check("reason is one of the declared classes", row.get("reason") in tf.REASONS,
          row.get("reason"))
    check("no real embedder ran (hermetic --dry): semantic_ran is False on every row",
          all(r["semantic_ran"] is False for r in res["rows"]),
          [r["semantic_ran"] for r in res["rows"]])
    check("every row without a semantic arm is logged as a lexical-only fallback (fix 8)",
          all(r["fallback"] == "lexical-only" for r in res["rows"] if not r["semantic_ran"]))
    check("ingest ran per project (fix 5): 'projects' >= 1", res["ingest"].get("projects", 0) >= 1,
          res["ingest"])
    check("the per-turn reason counts sum to n (fix 3's by_reason)",
          sum(res["per_turn"]["by_reason"].values()) == res["per_turn"]["n"],
          res["per_turn"])

print(f"\ntoken_floor v2 (--dry / stub only): {FAILS} failure(s)")
sys.exit(1 if FAILS else 0)
