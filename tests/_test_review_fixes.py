#!/usr/bin/env python3
"""Regression tests for the 2026-08 live-pipeline review fixes.

Six root causes, each measured on a real deployment before the fix:

  1. WATERMARK   a growing transcript was re-mined IN FULL every sweep (one Codex rollout
                 six times, another sixteen) - now only the appended delta is mined
  2. NEAR-DUP    the LLM re-stated one lesson under twin titles and both stood as current
                 truth with contradictory resolutions - now an embedding gate reconciles
  3. IDENTITY    session_id[:8] collapsed every ingest to the constant 'ingest-f' and
                 pre-uniquified stems conflated same-minute transcripts
  4. CARD SORT   day-granular date sort made the five card slots "alphabetically-first
                 five of today"
  5. ONE-LINE    card Status was hard-truncated mid-word with no marker
  6. STATS       a counterfactual upper bound was booked as realized savings

Plus the anti-confabulation transcript scrub and the relation-target reachability merge.
Hermetic: every vault-derived path constant is patched (see the 2026-08-13 incident).

    python _test_review_fixes.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
import memory_hook as m          # noqa: E402
import ingest                    # noqa: E402
import stats                     # noqa: E402

_ROOT = r"D:\Projects" if os.name == "nt" else "/projects"
m.PROJECT_ROOTS = [_ROOT]

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


from _sandbox import make_sandbox


def sandbox():
    # offline=True: hermeticity (review 2026-08 D14) - no section may reach the LIVE
    # embedder or repo. Sections exercising the near-dup gate install their own fakes.
    return make_sandbox(m, "revfix_", offline=True)


# ── 1. WATERMARK: delta-mining of growing transcripts ─────────────────────────
print("fix 1 - watermark delta-mining (ingest_files)")
d = sandbox()
src = Path(tempfile.mkdtemp(prefix="revfix_src_"))
f1 = src / "rollout.jsonl"


def turn(i):
    return json.dumps({"type": "message", "payload": {
        "role": "user", "content": f"work item {i}: fix the {i}-th bug in module alpha"}})


f1.write_text("\n".join(turn(i) for i in range(6)) + "\n", encoding="utf-8")

calls = []
_orig_ps = m.process_session


def fake_process(sid, cwd, path, trigger, db, run_log=None, agent=None,
                 transcript_text=None, project_override=None):
    calls.append({"sid": sid, "text": transcript_text})
    db[sid] = {"processed_at": "2026-08-18T00:00:00"}     # what mark_processed does
    return True


m.process_session = fake_process
try:
    db = {}
    r1 = ingest.ingest_files([f1], "proj", "codex", db)
    check("first sweep mines the file once", len(calls) == 1 and r1[0] == 1)
    check("first sweep mines the FULL text", "work item 0" in calls[0]["text"]
          and "work item 5" in calls[0]["text"])
    wm = ingest.load_watermarks()
    check("a watermark is recorded after the mine", len(wm) == 1
          and list(wm.values())[0]["chars"] == len(f1.read_text(encoding="utf-8")))

    r2 = ingest.ingest_files([f1], "proj", "codex", db)
    check("unchanged file: zero mines, cheapest skip", len(calls) == 1 and r2[1] == 1)

    with f1.open("a", encoding="utf-8") as fh:            # the file GROWS (resumed session)
        fh.write("\n".join(turn(i) for i in range(6, 9)) + "\n")
    r3 = ingest.ingest_files([f1], "proj", "codex", db)
    check("grown file mines exactly once more", len(calls) == 2 and r3[0] == 1)
    check("...and mines ONLY the delta (the six-times-re-mine bug)",
          "work item 6" in calls[1]["text"] and "work item 0" not in calls[1]["text"])
    check("delta session id carries the offset", "-w" in calls[1]["sid"])

    r4 = ingest.ingest_files([f1], "proj", "codex", db)
    check("re-run after growth: idempotent again", len(calls) == 2 and r4[1] == 1)

    f1.write_text("\n".join(turn(i) for i in range(2)) + "\n", encoding="utf-8")
    ingest.ingest_files([f1], "proj", "codex", db)
    check("a REWRRITTEN file (prefix mismatch) re-mines in full, once",
          len(calls) == 3 and "work item 0" in calls[2]["text"] and "-w" not in calls[2]["sid"])

    # migration: a previously-mined file with NO watermark (legacy ledger) skips via the
    # content-hash id and gets a watermark stamped for future growth
    ingest.save_watermarks({})
    calls.clear()
    r6 = ingest.ingest_files([f1], "proj", "codex", db)
    check("legacy-ledger file: no re-mine, watermark backfilled",
          len(calls) == 0 and r6[1] == 1 and len(ingest.load_watermarks()) == 1)

    # extraction failure (sid NOT marked in db) must NOT advance the watermark
    def failing_process(sid, cwd, path, trigger, db, **kw):
        calls.append({"sid": sid})
        return False                                       # not marked → retry next sweep
    m.process_session = failing_process
    with f1.open("a", encoding="utf-8") as fh:
        fh.write(turn(99) + "\n")
    before_wm = ingest.load_watermarks()
    ingest.ingest_files([f1], "proj", "codex", db)
    check("failed extraction leaves the watermark for retry",
          ingest.load_watermarks() == before_wm)
finally:
    m.process_session = _orig_ps

# ── 3. IDENTITY: entropy-preserving session ids + reserved stems ──────────────
print("fix 3 - session identity")
a = m._sid8("ingest-file-c868752d-782cd04e")
b = m._sid8("ingest-file-c868752d-4c251d2e")
check("prefix-constant ids no longer collapse ('ingest-f' bug)", a != b)
check("_sid8 is stable", a == m._sid8("ingest-file-c868752d-782cd04e"))
check("session_stem uses the hashed identity",
      m.session_stem("2026-08-18", "12:00", "p", "ingest-file-c868752d-782cd04e").endswith(a))

d = sandbox()
s1 = m.reserve_session_stem("2026-08-18", "12:00", "proj", "id-one")
(m.VAULT / "Sessions").mkdir(exist_ok=True)
(m.VAULT / "Sessions" / f"{s1}.md").write_text(
    f"---\ndate: 2026-08-18\nsession_id: id-one\n---\nbody", encoding="utf-8")
check("crash-retry reuses the reserved stem (idempotency preserved)",
      m.reserve_session_stem("2026-08-18", "12:00", "proj", "id-one") == s1)
s2 = m.reserve_session_stem("2026-08-18", "12:00", "proj", "id-two")
check("a different same-minute session gets a DIFFERENT stem", s2 != s1)
link = m.write_session_note("proj", "2026-08-18", "12:00", "sum", "X:/w", "id-two",
                            [], {nt: [] for nt in m.TYPED_TYPES}, "t", stem=s2)
fm, _ = m._read_frontmatter((m.VAULT / "Sessions" / f"{s2}.md").read_text(encoding="utf-8"))
check("the session note lands on exactly the reserved stem", link == s2)
check("frontmatter carries the FULL session id (traceability)",
      fm.get("session_id") == "id-two")

# ── 2. NEAR-DUP: embedding gate reconciles re-statements ──────────────────────
print("fix 2 - near-duplicate reconcile")
d = sandbox()
_emb, _avail, _usable = m.embed_text, m.embedder_available, m.embed_cache_usable
VEC_A = [1.0, 0.0, 0.0]


def fake_embed(text, kind=None, timeout=None, project=None):
    return VEC_A if "deterministic" in text else [0.0, 1.0, 0.0]


m.embed_text = fake_embed
m.embedder_available = lambda *a, **k: True
m.embed_cache_usable = lambda *a, **k: True
try:
    s_old = m.write_typed_note("Patterns", {
        "title": "deterministic qr generation and verification",
        "description": "generate the qr deterministically and verify by decoding it back"},
        "proj", "2026-08-17", ["t"], "pattern", session_stem_="sess-A")
    m.save_embed_cache({s_old: {"vec": VEC_A, "ntype": "pattern", "project": "proj",
                                "title": "deterministic qr generation and verification",
                                "desc": "generate the qr deterministically", "recurrence": 1}})
    s_new = m.write_typed_note("Patterns", {
        "title": "deterministic qr generation with verification",
        "description": "produce qr deterministically, verify by decode-back"},
        "proj", "2026-08-18", ["t"], "pattern", session_stem_="sess-B")
    check("the twin retires instead of standing as a second truth",
          not (m.VAULT / "Patterns" / f"{s_old}.md").exists()
          and (m.VAULT / "Patterns" / "Superseded" / f"{s_old}.md").exists())
    fm, _ = m._read_frontmatter((m.VAULT / "Patterns" / f"{s_new}.md")
                                .read_text(encoding="utf-8"))
    check("the survivor records the supersession", s_old in (fm.get("supersedes") or []))
    check("recurrence carries forward (the lesson RECURS, not fragments)",
          int(fm.get("recurrence") or 0) >= 2)

    # distinct lesson (orthogonal vector) must NOT be gated
    m.save_embed_cache({s_new: {"vec": VEC_A, "ntype": "pattern", "project": "proj",
                                "title": "x", "desc": "y", "recurrence": 2}})
    s_other = m.write_typed_note("Patterns", {
        "title": "hostname filter for node scripts",
        "description": "filter nodes by hostname before running"},
        "proj", "2026-08-18", ["t"], "pattern", session_stem_="sess-C")
    check("a genuinely distinct note is untouched by the gate",
          s_other and (m.VAULT / "Patterns" / f"{s_other}.md").exists()
          and (m.VAULT / "Patterns" / f"{s_new}.md").exists())

    # embedder down → gate silently off, write proceeds (fail-open)
    m.embed_text = lambda *a, **k: None
    m._NDUP_MEMO[0] = None
    s_failopen = m.write_typed_note("Patterns", {
        "title": "deterministic qr rendering with verification",
        "description": "near twin while embedder is down"},
        "proj", "2026-08-18", ["t"], "pattern", session_stem_="sess-D")
    check("embedder down → gate fails OPEN (write proceeds)",
          s_failopen and (m.VAULT / "Patterns" / f"{s_failopen}.md").exists())
finally:
    m.embed_text, m.embedder_available, m.embed_cache_usable = _emb, _avail, _usable

# ── stage 0: learned twin gate ────────────────────────────────────────────────
print("stage 0 - learned twin classifier")
check("a re-phrased twin scores high (same meaning, different words)",
      m._twin_probability(0.95, "deterministic qr generation",
                          "generate qr and verify by decode", ["qr-codes"],
                          "deterministic qr rendering",
                          "produce qr, verify decode-back", ["qr-codes"]) > 0.9)
check("distinct lessons score low even at moderate cosine",
      m._twin_probability(0.55, "hostname filter for nodes",
                          "filter nodes by hostname", ["hostname"],
                          "docker compose cleanup",
                          "remove compose resources after tests", ["docker"]) < 0.3)
check("probability is bounded and total on empty inputs",
      0.0 <= m._twin_probability(0.0, "", "", [], "", "", []) <= 1.0)
check("twin mode is the default, cosine stays the kill-switch",
      m.WRITE_DEDUP_MODE == "twin" and m.WRITE_DEDUP_TWIN_P == 0.90)

# ── 2b. same-day same-slug from a DIFFERENT session: reconcile, never a '-2' twin ──
print("fix 2b - same-stem cross-session reconcile")
d = sandbox()
sA = m.write_typed_note("Mistakes", {"title": "tau restore missing",
                                     "description": "tau not restored after early stop"},
                        "proj", "2026-08-18", ["t"], "mistake", session_stem_="sess-one")
sB = m.write_typed_note("Mistakes", {"title": "tau restore missing",
                                     "description": "tau parameter lost on early stopping"},
                        "proj", "2026-08-18", ["t"], "mistake", session_stem_="sess-two")
check("the second session is ABSORBED into the base note, no '-2' twin", sB == sA and
      not (m.VAULT / "Mistakes" / f"{sA}-2.md").exists())
fmB, _ = m._read_frontmatter((m.VAULT / "Mistakes" / f"{sB}.md").read_text(encoding="utf-8"))
check("recurrence reflects two distinct sessions", int(fmB.get("recurrence") or 0) >= 2)
check("both sessions recorded as sources",
      set(fmB.get("sources") or []) >= {"sess-one", "sess-two"})
check("crash-retry of the absorbing session does NOT bump again",
      m.write_typed_note("Mistakes", {"title": "tau restore missing",
                                      "description": "retry of session two"},
                         "proj", "2026-08-18", ["t"], "mistake",
                         session_stem_="sess-two") == sB
      and int(m._read_frontmatter((m.VAULT / "Mistakes" / f"{sB}.md")
                                  .read_text(encoding="utf-8"))[0].get("recurrence")) == 2)

# ── 8. relation targets become reachable by construction ──────────────────────
print("fix 8 - relation-target reachability")
d = sandbox()
stem = m.write_typed_note("Mistakes", {
    "title": "read barcode method missing",
    "description": "zxing has no read_barcode",
    "entities": ["zxing"],
    "relations": [{"rel": "fixed-by", "target": "zxing-cpp-fallback"}]},
    "proj", "2026-08-18", ["t"], "mistake", session_stem_="sess-R")
fm, _ = m._read_frontmatter((m.VAULT / "Mistakes" / f"{stem}.md").read_text(encoding="utf-8"))
check("the edge target is auto-tagged as an entity on the asserting note",
      "zxing-cpp-fallback" in (fm.get("entities") or []))
check("declared entities are preserved alongside", "zxing" in (fm.get("entities") or []))

# ── 4. CARD SORT: slots are earned, not alphabetical ──────────────────────────
print("fix 4 - card slot ranking")
d = sandbox()


def note(stem_, ntype, date_, rec=1, conf=None, resolved=False):
    return {"stem": stem_, "ntype": ntype, "date": date_, "recurrence": rec,
            "confidence": conf, "resolved": resolved, "tags": [],
            "title": stem_, "desc": "", "prevention": ""}


ns = [note("aaa-alpha-first", "decision", "2026-08-17", conf=0.9),
      note("zzz-earned", "decision", "2026-08-17", rec=3),
      note("mmm-confident", "decision", "2026-08-17", conf=0.99),
      note("bbb-unstamped", "decision", "2026-08-17"),
      note("newer-day", "decision", "2026-08-16")]
# the key semantics, mirrored for readability (the builder itself is exercised
# end-to-end right below - review 2026-08 Dc2: the promised "card-content check
# below" did not exist, so a regression in build_project_card passed unseen)
dec = sorted((n for n in ns if n["ntype"] == "decision"),
             key=lambda n: (n["date"], n.get("recurrence", 1) or 1,
                            n.get("confidence") if n.get("confidence") is not None else 1.0,
                            n["stem"]), reverse=True)
order = [n["stem"] for n in dec]
check("recurrence outranks the alphabet on a same-day tie", order[0] == "zzz-earned")
check("stated confidence breaks the next tie (0.99 above 0.9, alphabet ignored)",
      order.index("mmm-confident") < order.index("aaa-alpha-first"))
check("an unstamped note keeps the fully-confident convention (ranks as 1.0)",
      order.index("bbb-unstamped") < order.index("mmm-confident"))
check("date still dominates overall", order[-1] == "newer-day")

# the REAL builder, end-to-end: three same-day decisions on disk, ranked in the card
d = sandbox()
for _title, _rec, _conf in (("alpha first", 1, 0.9),
                            ("zzz earned", 3, None),
                            ("mmm confident", 1, 0.99)):
    _it = {"title": _title, "description": "x"}
    if _conf is not None:
        _it["confidence"] = _conf
    _st = m.write_typed_note("Decisions", _it, "proj", "2026-08-17", ["t"], "decision",
                             session_stem_=f"s-{_title.split()[0]}")
    if _rec > 1:
        _fp = m.VAULT / "Decisions" / f"{_st}.md"
        m.write_atomic(_fp, m._stamp_frontmatter(_fp.read_text(encoding="utf-8"),
                                                 {"recurrence": _rec}))
_card = m.build_project_card("proj")
_iz, _im, _ia = (_card.find("zzz earned"), _card.find("mmm confident"),
                 _card.find("alpha first"))
check("REAL card: earned-first on a same-day tie, then confidence, then alphabet",
      0 <= _iz < _im < _ia)

# ── 5. ONE-LINE: sentence-aware truncation ────────────────────────────────────
print("fix 5 - _one_line")
check("short strings pass through", m._one_line("done.", 200) == "done.")
long_ru = ("Логи развёрнуты и проверены на всех пяти нодах кластера. "
           "Подтверждена работа всех пяти сервисов после рестарта и обновления")
cut = m._one_line(long_ru, 80)
check("caps within the limit", len(cut) <= 80)
check("cuts at the sentence boundary when one fits",
      cut.endswith("кластера."))
no_sentence = "подтверждена работа всех пяти сервисов после рестарта и обновления конфига"
cut2 = m._one_line(no_sentence, 40)
check("word-boundary cut is marked with an ellipsis", cut2.endswith("…") and len(cut2) <= 40)
check("never ends mid-word", not cut2[:-1].endswith(("серв", "рест", "обно")))
check("whitespace still collapses", m._one_line("a\n\n  b", 50) == "a b")

# ── 7. anti-confabulation transcript scrub ────────────────────────────────────
print("fix 7 - strip_injected_boilerplate")
BOILER = ("## Active Projects\n1. project-aurora - compiler\n2. project-borealis - analytics\n"
          "3. project-cascade - experiments\nAlways use the GPU for training. "
          "Code and commit messages in English. Prefer type hints and pathlib everywhere.")
real = ["user: please set up the VPN config in D:/infra-secrets",
        "assistant: created qr files in vault/qr/",
        "user: verify the artifacts against the baseline"]
body = "\n\n".join([BOILER, real[0], BOILER, real[1], BOILER, real[2]])
out = m.strip_injected_boilerplate(body)
check("repeated boilerplate collapses to a single occurrence", out.count("Active Projects") == 1)
check("every real turn survives", all(r in out for r in real))
check("system-reminder blocks are stripped",
      "secret instructions" not in m.strip_injected_boilerplate(
          "user: hi\n\n<system-reminder>secret instructions</system-reminder>\n\nuser: bye"))
check("a short transcript passes through untouched",
      m.strip_injected_boilerplate("user: quick question") == "user: quick question")

# ── 6. STATS: real injected tokens tracked; saved is a labeled bound ──────────
print("fix 6 - honest ledger")
d = sandbox()
stats.record("recall", saved=190_000, injected=86)
led = stats.load()
check("real injected tokens are tracked", led["totals"].get("tokens_injected") == 86)
check("the bound is still recorded (trend line)", led["totals"].get("tokens_saved") == 190_000)
day = list(led["by_day"].values())[-1]
check("per-day injected recorded", day.get("injected") == 86)
panel = stats.render_panel(led)
check("the panel leads with the real number", "actually injected" in panel)
check("the bound is labeled as a bound", "upper bound" in panel)
check("summary line no longer claims the bound as savings",
      "targeted memory" in stats.summary_line(led))
check("_record_recall_saving returns 0 (receipt shows no inflated 'saved')",
      m._record_recall_saving("some injected text") == 0)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
