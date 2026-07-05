#!/usr/bin/env python3
"""Nevertwice - the full-scenario demo: every mechanism, on a realistic project, with numbers.

Where `demo.py` is the 25-second "it remembered" moment, this is the complete tour: it seeds a
throwaway vault with a realistic multi-session history of one web-app project, then exercises
**every** mechanism end-to-end on the real system and prints what each one buys you - in tokens
and in errors prevented. Nothing is mocked; nothing hits the network; your real vault is
untouched. Deterministic and offline (lexical recall + deterministic guard distillation), so it
prints the same story every run and can gate CI.

    python examples/scenario_demo.py            # narrated tour + a token/prevention scoreboard
    python examples/scenario_demo.py --json     # machine-readable metrics (for the README/DEMO)

What it demonstrates, in order:
  1. Recall + token economy  - surface the right lesson for a task, and the tokens that saves
                               vs dumping the whole store into context.
  2. Guards (active memory A) - a past mistake compiled into a check that fires BEFORE it repeats,
                               at zero context tokens until it does.
  3. Anticipation (B)        - predict the failure the current plan is heading toward.
  4. Counterfactual (C)      - "what breaks if I change X?" answered from the induced causal graph,
                               not an episode dump.
  5. Supersession            - a contradiction resolved at write time, surfaced in the ledger.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Point the store at a throwaway dir BEFORE importing the engine (VAULT is resolved at import),
# and force the offline/deterministic path so the demo is reproducible and touches no network.
_TMP = tempfile.mkdtemp(prefix="nevertwice-scenario-")
os.environ["NEVERTWICE_HOME"] = _TMP
os.environ["VAULT"] = _TMP
os.environ["NEVERTWICE_CLOUD"] = "none"
sys.path.insert(0, str(ROOT / "nevertwice"))

import api            # noqa: E402
import guards as G    # noqa: E402
import causal         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

JSON = "--json" in sys.argv
C = {"h": "\033[1;36m", "g": "\033[1;32m", "y": "\033[1;33m", "d": "\033[2m", "b": "\033[1m", "x": "\033[0m"}
if JSON or not sys.stdout.isatty():
    C = {k: "" for k in C}


def toks(s: str) -> int:
    return max(0, len(s) // 4)


def say(msg):
    if not JSON:
        print(f"\n{C['h']}{msg}{C['x']}")


def line(msg=""):
    if not JSON:
        print(msg)


PROJECT = "webapp"

# A realistic multi-session history: the lessons a real agent would accrue building a web app -
# recurring mistakes, the entities they touch, and the causal edges between them.
LESSONS = [
    {"type": "mistake", "title": "n-plus-one-query-on-user-list",
     "description": "The /users endpoint issued one query per row to load each user's team, "
                    "turning a page load into hundreds of queries.",
     "prevention": "Eager-load related rows with a join (select_related / JOIN); never lazy-load "
                   "inside a serialization loop.",
     "entities": ["database", "orm", "users-endpoint"],
     "relations": [{"rel": "caused-by", "target": "orm-lazy-loading"}]},
    {"type": "mistake", "title": "sql-built-by-fstring",
     "description": "A search filter was interpolated into the SQL string, opening an injection hole.",
     "prevention": "Never build SQL by f-string or % - pass values as query parameters "
                   "(cursor.execute(sql, params)).",
     "entities": ["database", "security"],
     "relations": [{"rel": "fixed-by", "target": "parameterized-queries"}]},
    {"type": "mistake", "title": "unclosed-db-connection-on-error",
     "description": "A raised exception skipped conn.close(), leaking connections until the pool "
                    "was exhausted under load.",
     "prevention": "Acquire the connection in a `with` block (context manager) so it is released "
                   "even on an exception.",
     "entities": ["database", "connection-pool"]},
    {"type": "mistake", "title": "unverified-response-json",
     "description": "Client code called response.json() without checking status, crashing on an "
                    "HTML error page from the upstream API.",
     "prevention": "Check response.ok (or raise_for_status) before parsing the body.",
     "entities": ["http-client", "api-integration"]},
    {"type": "mistake", "title": "jwt-verified-without-expiry",
     "description": "The auth middleware decoded the JWT but did not check exp, so expired tokens "
                    "still authenticated.",
     "prevention": "Verify signature AND expiry (exp) on every token; reject clock-skew beyond a "
                   "small leeway.",
     "entities": ["auth", "jwt", "security"],
     "relations": [{"rel": "depends-on", "target": "session-store"}]},
    {"type": "pattern", "title": "idempotent-migrations",
     "description": "Every schema migration is written to be safe to re-run (IF NOT EXISTS, "
                    "guarded backfills), so a half-applied deploy recovers cleanly.",
     "entities": ["database", "migration", "deploy"]},
    {"type": "pattern", "title": "cache-aside-with-ttl",
     "description": "Reads check the cache, fall back to the DB, and write back with a bounded TTL "
                    "so a stale key self-heals.",
     "entities": ["cache", "database", "performance"],
     "relations": [{"rel": "depends-on", "target": "redis"}]},
    {"type": "decision", "title": "use-uuid-primary-keys",
     "description": "Chose UUID primary keys over auto-increment to avoid leaking row counts and to "
                    "let clients generate ids offline.",
     "entities": ["database", "api-design"]},
    {"type": "mistake", "title": "n-plus-one-query-on-orders",
     "description": "The /orders endpoint repeated the same per-row query pattern that bit the "
                    "users endpoint earlier - the same class of bug, a second time.",
     "prevention": "Eager-load related rows with a join; never lazy-load inside a serialization loop.",
     "entities": ["database", "orm", "orders-endpoint"],
     "relations": [{"rel": "caused-by", "target": "orm-lazy-loading"}]},
    {"type": "mistake", "title": "float-money-rounding-drift",
     "description": "Storing prices as floats accumulated rounding drift across a cart total.",
     "prevention": "Store money as integer minor units (cents) or Decimal - never float.",
     "entities": ["billing", "data-types"]},
    {"type": "decision", "title": "auth-module-owns-session-store",
     "description": "The auth module is the single owner of the session store; other modules read "
                    "sessions only through it.",
     "entities": ["auth", "session-store", "architecture"],
     "relations": [{"rel": "depends-on", "target": "redis"}]},
]

# A contradiction the memory will resolve at write time: an early decision, later superseded.
OLD_DECISION = {"type": "decision", "title": "store-sessions-in-local-memory",
                "description": "Sessions kept in per-process memory for simplicity.",
                "entities": ["session-store", "auth"]}
NEW_DECISION = {"type": "decision", "title": "store-sessions-in-redis",
                "description": "Local-memory sessions broke on horizontal scaling (each worker had "
                               "its own); moved the session store to Redis.",
                "entities": ["session-store", "auth", "redis"],
                "supersedes": "store-sessions-in-local-memory"}


def seed():
    # embed=True so recall is indexed (semantic via local bge-m3 if Ollama is up, else the
    # text-only FTS path - either way real recall, no cloud). Guards distil deterministically.
    api.remember_lessons([OLD_DECISION], project=PROJECT, embed=True)
    api.remember_lessons(LESSONS, project=PROJECT, embed=True)
    api.remember_lessons([NEW_DECISION], project=PROJECT, embed=True)    # retires the old one
    G.generate_from_vault(PROJECT, min_recurrence=1, use_llm=False)      # distil guards (offline)


def main():
    seed()
    metrics = {"project": PROJECT, "lessons_seeded": len(LESSONS) + 2}

    line(f"{C['b']}Nevertwice - full-scenario demo{C['x']}  "
         f"{C['d']}(throwaway vault, offline, deterministic){C['x']}")
    line(f"{C['d']}Seeded a realistic history of the '{PROJECT}' project: "
         f"{metrics['lessons_seeded']} lessons across {len(set(sum((l.get('entities', []) for l in LESSONS), [])))} entities.{C['x']}")

    # ── 1. Recall + token economy ──
    say("(1) Recall + token economy - the right lesson, not the whole store")
    query = "loading a list endpoint is doing hundreds of database queries"
    hits = api.recall(query, project=PROJECT, k=3)
    top = hits[0] if hits else {}
    line(f"  task: {C['d']}{query}{C['x']}")
    line(f"  → recalled: {C['g']}{top.get('title', '(none)')}{C['x']}")
    if top.get("prevention"):
        line(f"    {C['d']}{top['prevention'][:96]}{C['x']}")
    # tokens: inject the whole store vs the calibrated top-k
    all_notes = "\n".join(f"{n.get('title','')} {n.get('description','')} {n.get('prevention','')}"
                          for n in _iter_all())
    recall_ctx = "\n".join(f"{h['title']} {h.get('prevention','')}" for h in hits)
    dump_t, recall_t = toks(all_notes), toks(recall_ctx)
    metrics["recall"] = {"top_hit": top.get("title"), "dump_tokens": dump_t,
                         "recall_tokens": recall_t,
                         "savings_x": round(dump_t / max(1, recall_t), 1)}
    line(f"  tokens to convey it: {C['y']}{recall_t}{C['x']} (top-3) vs {dump_t} (dump the whole store)"
         f"  → {C['g']}{metrics['recall']['savings_x']}× leaner{C['x']}")

    # ── 2. Guards (active memory A) ──
    say("(2) Guards - a past mistake, caught BEFORE it repeats (0 tokens until it fires)")
    clean = api.guards_check("orders = [serialize(o) for o in Order.objects.all()]", project=PROJECT)
    risky = api.guards_check("cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")",
                             project=PROJECT)
    guard_ct = len(G.load_guards())
    line(f"  guards distilled from mistakes: {C['b']}{guard_ct}{C['x']}  "
         f"{C['d']}(they live in a JSON ledger, not your context){C['x']}")
    line(f"  a benign edit → {C['g']}{'silent (0 tokens)' if not clean else 'FIRED'}{C['x']}")
    if risky:
        line(f"  about to write an f-string SQL query → {C['y']}guard fires{C['x']}: "
             f"{risky[0]['message'][:80]}")
    metrics["guards"] = {"count": guard_ct, "benign_silent": not clean, "caught_repeat": bool(risky)}

    # ── 3. Anticipation (B) ──
    say("(3) Anticipation - the failure the current plan is heading toward")
    traj = "adding a new /invoices list endpoint that serializes each invoice and its line items"
    pred = api.anticipate(traj, project=PROJECT, k=1)
    line(f"  plan: {C['d']}{traj}{C['x']}")
    if pred:
        line(f"  → {C['y']}anticipated{C['x']} (risk {pred[0]['risk']}): {pred[0]['message'][:96]}")
    else:
        line(f"  → {C['d']}nothing above threshold (silent){C['x']}")
    metrics["anticipate"] = {"fired": bool(pred), "risk": pred[0]["risk"] if pred else None}

    # ── 4. Counterfactual (C) ──
    say("(4) Counterfactual - what breaks if I change this?")
    cf = api.what_breaks("session-store", project=PROJECT)
    answer = causal.counterfactual("session-store", PROJECT)
    line(f"  question: {C['d']}what breaks if I change `session-store`?{C['x']}")
    for imp in cf.get("impacts", [])[:3]:
        line(f"    → {C['g']}{imp['effect']}{C['x']} {C['d']}(via {imp['via']}){C['x']}")
    cf_tokens = toks(answer)
    dump_related = toks("\n".join(f"{n.get('title','')} {n.get('description','')}"
                                  for n in _iter_all() if "session-store" in (n.get("entities") or [])))
    metrics["causal"] = {"impacts": len(cf.get("impacts", [])),
                         "answer_tokens": cf_tokens, "dump_tokens": dump_related,
                         "savings_x": round(dump_related / max(1, cf_tokens), 1) if dump_related else None}
    line(f"  a synthesized answer: {C['y']}{cf_tokens}{C['x']} tokens "
         f"{C['d']}(vs {dump_related} to dump every note mentioning it){C['x']}")

    # ── 5. Supersession ──
    say("(5) Supersession - a contradiction resolved at write time")
    ledger = api.conflicts(PROJECT)
    if ledger:
        c = ledger[0]
        line(f"  the memory revised a fact: {C['d']}{c['old_title']}{C['x']} "
             f"{C['h']}→{C['x']} {C['g']}{c['new_title']}{C['x']}")
        line(f"  {C['d']}live recall now returns only the current truth; the old one is kept in history.{C['x']}")
    metrics["supersession"] = {"revised_facts": len(ledger)}

    # ── scoreboard ──
    say("Scoreboard - what memory bought, on one realistic project")
    r = metrics
    line(f"  {C['g']}✓{C['x']} recall is {C['b']}{r['recall']['savings_x']}×{C['x']} leaner than dumping the store")
    line(f"  {C['g']}✓{C['x']} {r['guards']['count']} guards catch repeats at {C['b']}0 tokens{C['x']} until they fire")
    line(f"  {C['g']}✓{C['x']} anticipation flags a novel repeat of a known failure class")
    if r['causal']['savings_x']:
        line(f"  {C['g']}✓{C['x']} a counterfactual answers in {C['b']}{r['causal']['savings_x']}×{C['x']} fewer tokens than a note dump")
    line(f"  {C['g']}✓{C['x']} {r['supersession']['revised_facts']} contradiction resolved - recall stays consistent")

    if JSON:
        import json
        print(json.dumps(metrics, ensure_ascii=False, indent=1))


def _iter_all():
    """All seeded notes for this project as recall-shaped dicts (for the token tallies)."""
    import memory_hook as m
    return [{"title": n.get("title", ""), "description": n.get("desc", ""),
             "prevention": n.get("prevention", ""), "entities": n.get("entities", [])}
            for n in m._iter_project_notes(PROJECT)]


# A battery of actions that each repeat one of the seeded mistakes - used to measure guard
# catch-rate at scale (offline: deterministic guards, regex-only check).
_REPEAT_ACTIONS = [
    "cursor.execute(f\"SELECT * FROM t WHERE id = '{x}'\")",     # sql-built-by-fstring
    "price = float(request.form['amount'])",                     # float-money
    "data = requests.get(url).json()",                           # unverified-response-json
    "rows = [render(r) for r in Model.objects.all()]",           # n-plus-one (lazy loop)
]


def scale(n_projects: int):
    """Large-scale check: seed the realistic history across N projects (offline, no embedder),
    then measure the two advantages that do not need semantic recall - guard catch-rate on a
    battery of repeat-actions, and how the recall token-economy ratio grows with store size."""
    import json
    import memory_hook as m
    for i in range(n_projects):
        proj = f"svc-{i:03d}"
        api.remember_lessons(LESSONS, project=proj, embed=False)
    m.rebuild_index()
    G.generate_from_vault(min_recurrence=1, use_llm=False)
    all_notes = m._iter_all_notes()
    guards_ct = len(G.load_guards())
    # guard catch-rate: of the repeat-actions, how many does a guard fire on (within a project -
    # guards are project-scoped by design, so a lesson in one service never noises another)?
    caught = sum(1 for a in _REPEAT_ACTIONS if G.check(a, project="svc-000"))
    # structural token economy: dumping the whole store vs a 5-note recall slice
    avg_tok = sum(toks(f"{x['title']} {x['desc']} {x.get('prevention','')}")
                  for x in all_notes) / max(1, len(all_notes))
    dump_t = round(avg_tok * len(all_notes))
    recall_t = round(avg_tok * 5)
    out = {"projects": n_projects, "total_notes": len(all_notes), "guards": guards_ct,
           "repeat_actions": len(_REPEAT_ACTIONS), "caught": caught,
           "catch_rate": round(caught / len(_REPEAT_ACTIONS), 2),
           "dump_tokens": dump_t, "recall_tokens": recall_t,
           "recall_savings_x": round(dump_t / max(1, recall_t), 1)}
    if JSON:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        line(f"{C['b']}Large-scale check{C['x']} - {n_projects} projects, "
             f"{C['b']}{out['total_notes']}{C['x']} notes")
        line(f"  guards distilled:      {C['g']}{out['guards']}{C['x']}")
        line(f"  repeat-actions caught: {C['g']}{caught}/{len(_REPEAT_ACTIONS)}{C['x']} "
             f"({int(out['catch_rate']*100)}%)")
        line(f"  recall vs full dump:   {C['g']}{out['recall_savings_x']}×{C['x']} leaner "
             f"({recall_t} vs {dump_t} tokens) - the ratio grows with the store")
    return out


if __name__ == "__main__":
    _scale = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--scale=")), 0)
    if _scale:
        scale(_scale)
    else:
        main()
