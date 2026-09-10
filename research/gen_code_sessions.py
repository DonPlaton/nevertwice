#!/usr/bin/env python3
"""Build the code-session corpus: facts and gold by program, prose by a foreign model (ledger J3).

No corpus of coding-agent sessions with gold answers exists, here or elsewhere, and half of the
extraction gate in `.loop/GOAL-CLOSE.md` - "not worse than the current extractor on code
sessions" - cannot be measured without one. This generator writes it, and the two halves of the
work are kept apart on purpose:

* **The program chooses the facts and writes the gold.** Every synthetic project gets a handful
  of durable facts drawn from pools (a port, a Python version, a timeout, a config path, a
  branch, a flag ...), two of which are replaced in a later session; three lessons (a mistake,
  its prevention, the tool call that repeats it) drawn from the guard corpus's own table; and
  the questions with their answers and markers follow from those choices exactly. Gold is never
  a model's opinion.
* **A model from a foreign family writes the prose.** The extractor, reader and judge on every
  stand here are Qwen; the sessions are written by GLM (`glm-4.7-flash`, local Ollama, already
  present - nothing is pulled) so the extractor is not reading its own family's phrasing. The
  model is told which facts a session must state verbatim and which it must not mention; the
  program checks both against the text and retries a session that fails, and records how many
  retries it took. A session that still fails after three attempts is written with a marked
  fallback sentence per missing fact, so the corpus is complete and the artifact says where the
  model did not comply.

Four question types per project, the ledger's four:
* `fact`     - the literal value ("what port does the API listen on?"), answer by markers;
* `current`  - a replaced fact asked after its replacement: the new value is the answer and the
               old one is a stale marker, the supersession stand's three-sided reading;
* `lesson`   - "what should be checked before X?", judged against the prevention text;
* `situation`- a tool call or error text that should surface one specific note: the I8 dataset.

Deterministic where it can be: the fact draw is seeded, the model is called with a fixed seed
and temperature, and every generated session is cached by its prompt hash so a rebuild reuses
them. The committed file is content-hashed and `--check` verifies it against a fresh build from
the cache.

    python research/gen_code_sessions.py --projects 30                # writes research/data/code_sessions_v1.json
    python research/gen_code_sessions.py --check
    python research/gen_code_sessions.py --projects 2 --sessions 2    # a smoke build
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import gen_guard_bench as guards_table  # noqa: E402 - the lessons come from the same audited table

OUT = HERE / "data" / "code_sessions_v1.json"
CACHE = HERE / "data" / "code_sessions_v1_cache.json"          # *_cache.json: never committed
OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.environ.get("CODE_GEN_MODEL", "glm-4.7-flash:q4_K_M")
SEED = 20260910
RETRIES = 3
DAYS = ["2026-01-12", "2026-02-03", "2026-02-24", "2026-04-07", "2026-05-19"]   # session dates, in order
_SEP = re.compile(r"[\s_\-]+")

# ── fact pools: (kind, question, [values], phrase) ────────────────────────────────────────
# The phrase is what the session must state; {v} is the value. Markers are the value itself
# and, for numbers with units, the compact variant, so an extractor that writes "30s" is not
# scored as having lost "30 seconds".
POOLS = [
    ("port", "what port does the API server listen on", [str(p) for p in range(8010, 8099, 7)],
     "the API server listens on port {v}"),
    ("python", "which Python version does the project target", ["3.10", "3.11", "3.12", "3.13"],
     "the project targets Python {v}"),
    ("db", "which database version runs in production", ["PostgreSQL 14", "PostgreSQL 15", "PostgreSQL 16", "MySQL 8.4", "SQLite 3.45"],
     "production runs {v}"),
    ("timeout", "what is the HTTP client timeout", ["5 seconds", "10 seconds", "15 seconds", "30 seconds", "60 seconds"],
     "the HTTP client timeout is {v}"),
    ("branch", "what is the name of the release branch", ["release/2026.1", "release/2026.2", "stable-main", "rc/summer", "prod-track"],
     "releases are cut from the {v} branch"),
    ("config", "where does the service read its configuration from", ["config/settings.toml", "etc/app.yaml", "conf/service.ini", ".env.production", "config/base.json"],
     "the service reads its configuration from {v}"),
    ("flag", "which feature flag gates the new dashboard", ["beta_dashboard", "new_dash_v2", "dash_redesign", "ff_dashboard_next", "dashboard_2026"],
     "the {v} feature flag gates the new dashboard"),
    ("batch", "what batch size does training use", ["16", "32", "64", "128", "256"],
     "the training batch size is {v}"),
    ("lr", "what learning rate does training use", ["1e-4", "3e-4", "5e-4", "1e-3", "2e-5"],
     "the learning rate is {v}"),
    ("ttl", "what is the cache TTL", ["5 minutes", "15 minutes", "1 hour", "6 hours", "24 hours"],
     "the cache TTL is {v}"),
    ("retries", "how many times does the client retry a failed request", ["twice", "three times", "five times", "once", "seven times"],
     "the client retries a failed request {v}"),
    ("loglevel", "what log level does production run at", ["WARNING", "INFO", "ERROR", "DEBUG"],
     "production runs at log level {v}"),
    ("region", "which cloud region hosts the primary deployment", ["eu-central-1", "us-east-2", "eu-west-3", "ap-southeast-1", "us-west-1"],
     "the primary deployment is hosted in {v}"),
    ("node", "which Node version does the frontend build target", ["Node 18", "Node 20", "Node 22", "Node 24"],
     "the frontend build targets {v}"),
    ("pm", "which package manager does the frontend use", ["pnpm", "npm", "yarn", "bun"],
     "the frontend uses {v} as its package manager"),
    ("queue", "which message queue does the worker consume from", ["RabbitMQ", "Redis Streams", "Kafka", "SQS", "NATS"],
     "the worker consumes from {v}"),
    ("owner", "who owns the on-call rotation for the service", ["the platform team", "the data team", "the API guild", "the SRE pod", "the payments squad"],
     "the on-call rotation is owned by {v}"),
    ("ci", "which CI system runs the test suite", ["GitHub Actions", "GitLab CI", "Buildkite", "CircleCI", "Jenkins"],
     "the test suite runs on {v}"),
]
STACKS = [("py-api", "a Python FastAPI service with PostgreSQL"), ("py-ml", "a PyTorch training pipeline"),
          ("ts-web", "a TypeScript React frontend with a Node build"), ("py-cli", "a Python command-line tool"),
          ("go-svc", "a Go microservice behind a message queue"), ("py-data", "a Python data pipeline on Airflow"),
          ("rs-lib", "a Rust library with Python bindings"), ("py-bot", "a Python chat bot with a Redis cache")]


def norm(text: str) -> str:
    return _SEP.sub(" ", (text or "").lower())


def markers_for(kind: str, value: str) -> list[str]:
    """The value and its compact variants: '30 seconds' -> ['30 seconds', '30s', '30 second']."""
    out = [value]
    mnum = re.match(r"^(\d+)\s+(second|minute|hour)s?$", value)
    if mnum:
        n, unit = mnum.group(1), mnum.group(2)
        out += [f"{n}{unit[0]}", f"{n} {unit}"]
    if kind == "db" and " " in value:
        name, ver = value.rsplit(" ", 1)
        out.append(f"{name.lower()[:2]} {ver}" if name.lower().startswith("postgres") else value)
    return sorted(set(out), key=len, reverse=True)


def hit(markers: list[str], text: str) -> bool:
    low = norm(text)
    return any(norm(mk) in low for mk in markers)


# ── the program's half: facts, replacements, lessons, questions ──────────────────────────

def plan_project(i: int, rng: random.Random, n_sessions: int) -> dict:
    slug, stack = STACKS[i % len(STACKS)]
    pid = f"cs{i:03d}"
    kinds = rng.sample(POOLS, 8)
    facts = []
    for kind, q, values, phrase in kinds:
        v = rng.choice(values)
        facts.append({"key": kind, "question": q, "value": v, "phrase": phrase.format(v=v),
                      "markers": markers_for(kind, v), "pool": values, "template": phrase})
    # two facts are replaced in the fourth session (or the last, in a short build)
    replaced = []
    for f in rng.sample(facts, 2):
        new = rng.choice([x for x in f["pool"] if x != f["value"]])
        replaced.append({"key": f["key"], "old": f["value"], "new": new, "question": f["question"],
                         "new_phrase": f["template"].format(v=new), "new_markers": markers_for(f["key"], new),
                         "old_markers": f["markers"]})
    lessons_src = rng.sample(guards_table.MISTAKES, 3)
    lessons = []
    for stem, family, title, desc, prevention, _lint, positives, _neg in lessons_src:
        lessons.append({"stem": stem, "title": title, "desc": desc, "prevention": prevention,
                        "trigger": positives[0]["text"], "trigger_tool": positives[0]["tool"]})
    # which session states what: facts 0-5 across sessions 1-3, lessons one per session 1-3,
    # replacements in session 4, session 5 is noise plus the situation triggers
    n = max(2, n_sessions)
    plan = [{"facts": [], "lessons": [], "replace": [], "noise": False} for _ in range(n)]
    for j, f in enumerate(facts):
        plan[j % max(1, min(3, n - 1))]["facts"].append(f["key"])
    for j, les in enumerate(lessons):
        plan[j % max(1, min(3, n - 1))]["lessons"].append(les["stem"])
    plan[min(3, n - 1)]["replace"] = [r["key"] for r in replaced]
    if n >= 5:
        plan[4]["noise"] = True
    return {"id": pid, "slug": f"{slug}-{i:03d}", "stack": stack, "facts": facts, "replaced": replaced,
            "lessons": lessons, "plan": plan}


def questions_for(p: dict) -> list[dict]:
    qs = []
    replaced_keys = {r["key"] for r in p["replaced"]}
    for f in p["facts"]:
        if f["key"] in replaced_keys:
            continue
        sess = next(i for i, s in enumerate(p["plan"]) if f["key"] in s["facts"])
        qs.append({"id": f"{p['id']}-fact-{f['key']}", "type": "fact", "question": f["question"] + "?",
                   "answer": f["value"], "markers": f["markers"], "stale_markers": [],
                   "gold_sessions": [f"{p['id']}-s{sess}"]})
    for r in p["replaced"]:
        first = next(i for i, s in enumerate(p["plan"]) if r["key"] in s["facts"])
        rep = next(i for i, s in enumerate(p["plan"]) if r["key"] in s["replace"])
        qs.append({"id": f"{p['id']}-current-{r['key']}", "type": "current", "question": r["question"] + " now?",
                   "answer": r["new"], "markers": r["new_markers"], "stale_markers": r["old_markers"],
                   "gold_sessions": [f"{p['id']}-s{rep}"], "first_session": f"{p['id']}-s{first}"})
    for les in p["lessons"]:
        sess = next(i for i, s in enumerate(p["plan"]) if les["stem"] in s["lessons"])
        qs.append({"id": f"{p['id']}-lesson-{les['stem']}", "type": "lesson",
                   "question": f"What did we learn about this: {les['title']}? What should be done instead?",
                   "answer": les["prevention"], "markers": [], "stale_markers": [],
                   "gold_sessions": [f"{p['id']}-s{sess}"], "note_stem": les["stem"]})
        qs.append({"id": f"{p['id']}-situation-{les['stem']}", "type": "situation",
                   "question": les["trigger"], "tool": les["trigger_tool"], "answer": les["stem"],
                   "markers": [], "stale_markers": [], "gold_sessions": [f"{p['id']}-s{sess}"],
                   "note_stem": les["stem"]})
    return qs


# ── the model's half: prose ──────────────────────────────────────────────────────────────

PROMPT = """You write a realistic transcript of a coding-agent session (like Claude Code) for a software project.

Project: {slug} - {stack}.
Format: 25 to 40 lines. Alternate lines starting with "user:", "assistant:" and "tool:" (tool lines show a shell command or a file edit and a short result; include at least one file path and one error message that gets fixed). Plain text, no markdown headings.

The transcript MUST contain each of these sentences, verbatim, inside natural assistant or user turns (you may add words around them but keep the sentence intact):
{must}

{lessons}
{replace}
The transcript must NOT mention any of these values: {forbid}.
{noise}
Return ONLY JSON: {{"transcript": "<the lines joined with \\n>"}}"""


def _prompt(p: dict, j: int) -> str:
    step = p["plan"][j]
    must = [f["phrase"] for f in p["facts"] if f["key"] in step["facts"]]
    must += [r["new_phrase"] for r in p["replaced"] if r["key"] in step["replace"]]
    lessons = [les for les in p["lessons"] if les["stem"] in step["lessons"]]
    lesson_txt = ""
    if lessons:
        lesson_txt = "It must also show the team running into this mistake and recording the lesson:\n" + "\n".join(
            f"- mistake: {les['desc']}. Prevention (state this sentence verbatim): {les['prevention']}" for les in lessons) + "\n"
    replace_txt = ""
    if step["replace"]:
        olds = [r for r in p["replaced"] if r["key"] in step["replace"]]
        replace_txt = ("These facts CHANGED in this session - say clearly that the earlier value is no longer true and state the new one: "
                       + "; ".join(f"{r['question']}: it was {r['old']}, now {r['new']}" for r in olds) + ".\n")
    forbid = []
    for f in p["facts"]:
        if f["key"] not in step["facts"] and f["key"] not in step["replace"]:
            forbid.append(f["value"])
    for r in p["replaced"]:
        if r["key"] not in step["replace"]:
            forbid.append(r["new"])
    noise = ("This session is routine maintenance unrelated to the facts above (dependency bumps, a flaky test, formatting); "
             "do not state any configuration value.\n") if step["noise"] else ""
    return PROMPT.format(slug=p["slug"], stack=p["stack"], must="\n".join(f"- {s}" for s in must) or "- (none)",
                         lessons=lesson_txt, replace=replace_txt, forbid=", ".join(forbid) or "(none)", noise=noise)


def ollama_json(prompt: str, seed: int, timeout: int = 600, num_ctx: int = 8192, temperature: float = 0.7) -> dict:
    # `think: False` - GLM and Qwen 3 are thinking models; left on, the reasoning consumed the token
    # budget and the JSON came back with an empty transcript (every required sentence then failed
    # verification and the first 17 projects of the 2026-09-10 run were fallbacks; found and fixed).
    body = json.dumps({"model": MODEL, "stream": False, "format": "json", "think": False,
                       "messages": [{"role": "user", "content": prompt}],
                       "options": {"temperature": temperature, "seed": seed, "num_ctx": num_ctx, "num_predict": 3000}}).encode("utf-8")
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    txt = ((d.get("message") or {}).get("content") or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    try:
        return json.loads(txt)
    except ValueError:
        return {"transcript": txt}


def verify(text: str, must: list[list[str]], forbid: list[str]) -> tuple[list[int], list[str]]:
    """(indices of required facts missing, forbidden values present)."""
    missing = [i for i, mk in enumerate(must) if not hit(mk, text)]
    leaked = [v for v in forbid if hit([v], text)]
    return missing, leaked


def required(p: dict, j: int) -> tuple[list[list[str]], list[str], list[str]]:
    step = p["plan"][j]
    must, phrases = [], []
    for f in p["facts"]:
        if f["key"] in step["facts"]:
            must.append(f["markers"]); phrases.append(f["phrase"])
    for r in p["replaced"]:
        if r["key"] in step["replace"]:
            must.append(r["new_markers"]); phrases.append(r["new_phrase"])
    for les in p["lessons"]:
        if les["stem"] in step["lessons"]:
            must.append([les["prevention"]]); phrases.append(les["prevention"])
    forbid = [f["value"] for f in p["facts"] if f["key"] not in step["facts"] and f["key"] not in step["replace"]]
    forbid += [r["new"] for r in p["replaced"] if r["key"] not in step["replace"]]
    return must, forbid, phrases


def generate_session(p: dict, j: int, cache: dict, call=ollama_json) -> dict:
    prompt = _prompt(p, j)
    must, forbid, phrases = required(p, j)
    key = hashlib.sha1(f"{MODEL}|{SEED}|{prompt}".encode("utf-8")).hexdigest()
    if key in cache:
        return cache[key]
    text, attempts, last = "", 0, ([], [])
    for attempt in range(RETRIES):
        attempts = attempt + 1
        try:
            got = call(prompt, SEED + attempt)
            text = str(got.get("transcript") or "")
            if not text.strip():
                text = f"assistant: (empty transcript from the model; keys {sorted(got)[:5]})"
        except Exception as e:                               # noqa: BLE001 - recorded, retried
            text = f"assistant: (generation error {type(e).__name__})"
        # a leaked forbidden value is removed rather than regenerated: it is the model's
        # improvisation, and the fact it leaks belongs to another session
        for v in forbid:
            if hit([v], text):
                text = re.sub(re.escape(v), "[value]", text, flags=re.IGNORECASE)
        missing, leaked = verify(text, must, forbid)
        last = (missing, leaked)
        if not missing and not leaked:
            break
    fallback = []
    if last[0]:
        for i in last[0]:
            fallback.append(phrases[i])
            text += f"\nassistant: For the record, {phrases[i]}."
    rec = {"text": text, "attempts": attempts, "fallback_sentences": fallback, "model": MODEL}
    cache[key] = rec
    return rec


def runtime_stamp(call) -> dict:
    """The server and model behind the prose - a seed alone does not make a recipe reproducible.
    Empty for a fake caller (tests); best-effort otherwise."""
    if call is not ollama_json:
        return {}
    out = {}
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/version", timeout=5) as r:
            out["ollama_version"] = json.loads(r.read()).get("version")
        body = json.dumps({"model": MODEL}).encode("utf-8")
        req = urllib.request.Request(f"{OLLAMA}/api/show", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        det = d.get("details") or {}
        out["model_family"] = det.get("family")
        out["model_parameters"] = det.get("parameter_size")
        out["model_quantization"] = det.get("quantization_level")
        out["model_digest"] = (d.get("modelinfo") or {}).get("general.uuid") or hashlib.sha1(
            json.dumps(det, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    except Exception as e:                                       # noqa: BLE001 - a stamp, not a gate
        out["error"] = f"{type(e).__name__}"
    return out


def build(n_projects: int, n_sessions: int, cache: dict, call=ollama_json, verbose: bool = True) -> dict:
    rng = random.Random(SEED)
    projects, t0 = [], time.time()
    for i in range(n_projects):
        p = plan_project(i, rng, n_sessions)
        sessions = []
        for j in range(len(p["plan"])):
            rec = generate_session(p, j, cache, call=call)
            sessions.append({"id": f"{p['id']}-s{j}", "idx": j, "day": DAYS[j % len(DAYS)], "text": rec["text"],
                             "attempts": rec["attempts"], "fallback_sentences": rec["fallback_sentences"],
                             "states": p["plan"][j]["facts"], "replaces": p["plan"][j]["replace"],
                             "lessons": p["plan"][j]["lessons"], "noise": p["plan"][j]["noise"]})
        p["sessions"] = sessions
        p["questions"] = questions_for(p)
        for f in p["facts"]:
            f.pop("pool", None); f.pop("template", None)
        p.pop("plan", None)
        projects.append(p)
        if verbose:
            print(f"  [{i + 1}/{n_projects}] {p['slug']}  sessions {len(sessions)}  questions {len(p['questions'])}  "
                  f"retries {sum(s['attempts'] - 1 for s in sessions)}  fallbacks {sum(len(s['fallback_sentences']) for s in sessions)}  "
                  f"({time.time() - t0:.0f}s)", flush=True)
    qtypes = {}
    for p in projects:
        for q in p["questions"]:
            qtypes[q["type"]] = qtypes.get(q["type"], 0) + 1
    return {
        "name": "code_sessions_v1", "schema_version": 1, "generator_model": MODEL, "seed": SEED,
        "generator_runtime": runtime_stamp(call),
        "purpose": ("Coding-agent sessions with gold answers (ledger J3): facts, replacements, lessons and "
                    "situations chosen by the program, prose written by a model from a foreign family, every "
                    "required fact verified in the text."),
        "limitations": [
            "Synthetic: the sessions are what one model thinks a coding session reads like; the real held-out "
            "set (research/data/code_heldout_manifest.json) is the check on that.",
            "Facts are template phrases the model was told to state verbatim; an extractor that copies "
            "identifiers is favoured, one that paraphrases is not - the markers include compact variants.",
            "A fallback sentence marks where the model did not comply after three attempts; the count is in "
            "the artifact and a session with fallbacks is still in the corpus.",
        ],
        "days": DAYS, "projects": projects,
        "counts": {"projects": len(projects), "sessions": sum(len(p["sessions"]) for p in projects),
                   "questions": sum(len(p["questions"]) for p in projects), "by_type": qtypes,
                   "sessions_with_fallback": sum(1 for p in projects for s in p["sessions"] if s["fallback_sentences"]),
                   "retries": sum(s["attempts"] - 1 for p in projects for s in p["sessions"])},
    }


def dump(data: dict) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--projects", type=int, default=30)
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="rebuild from the cache and compare with the committed file")
    args = ap.parse_args()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    data = build(args.projects, args.sessions, cache, verbose=not args.check)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=0, ensure_ascii=False), encoding="utf-8")
    raw = dump(data)
    digest = hashlib.sha256(raw).hexdigest()
    if args.check:
        p = Path(args.out)
        ok = p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest
        print(f"{'ok' if ok else 'MISMATCH'}  {p}  sha256={digest[:16]}")
        return 0 if ok else 1
    Path(args.out).write_bytes(raw)
    c = data["counts"]
    print(f"wrote {args.out}  projects {c['projects']}  sessions {c['sessions']}  questions {c['questions']} {c['by_type']}  "
          f"retries {c['retries']}  sessions with fallback {c['sessions_with_fallback']}  sha256={digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
