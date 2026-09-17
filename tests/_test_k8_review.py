#!/usr/bin/env python3
"""Regression tests for the xhigh code review on k8/zero-loss (HEAD 2de439d).

One file per the repo's test-discipline rule ("or a new tests/_test_k8_review.py"), sectioned by
finding id. Each section is a direct regression check plus, where a single named helper carries the
fix, a mutation check that breaks that helper by hand and confirms the section above would redden.

    python tests/_test_k8_review.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextlib
import io

import memory_hook as m  # noqa: E402
import consolidate_memory as cm  # noqa: E402
import memory_search as ms  # noqa: E402
import mcp_server  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


F = m._FACTS_MARK
PROJ = "k8proj"
OLD_FACT = f"The HTTP client timeout is 30 seconds.{F}the HTTP client timeout is 30 seconds"
NEW_FACT = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"


def fresh():
    d = make_sandbox(m, "k8rv_", offline=True)
    m.generate_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call on the write path"))
    m.call_ollama = m.generate_json
    return d


def write(desc, session, date="2026-06-01", title="http client timeout", ntype="decision", **extra):
    item = {"title": title, "description": desc, **extra}
    return m.write_typed_note(m.TYPE_FOLDER[ntype], item, PROJ, date, ["t"], ntype, session_stem_=session)


def note(stem, ntype="decision", where=""):
    return m.VAULT / m.TYPE_FOLDER[ntype] / where / f"{stem}.md"


def served(stem, ntype="decision"):
    _, desc, _ = m._parse_note_body(note(stem, ntype).read_text(encoding="utf-8").split("\n"))
    return desc or ""


def contested(stem, ntype="decision", where=""):
    return m._read_frontmatter_file(note(stem, ntype, where)).get("contested")


def fm_of(stem, ntype="decision", where=""):
    return m._read_frontmatter_file(note(stem, ntype, where))


S1 = "2026-06-01-1000-k8proj-session-aaaaaaaa"
S2 = "2026-06-01-1100-k8proj-session-bbbbbbbb"

# ── F1: a same-session refresh must not drop or invert the contested stamp ────────────────────
print("\n- F1: a same-session refresh carries the contested stamp forward, in the right direction -")
d = fresh()
old = write(OLD_FACT, S1)
sib = write(NEW_FACT, S2)
check("setup: the earlier note is contested against the sibling", contested(old) == [sib])
refreshed = write(OLD_FACT, S1)                          # same session: a crash-retry / grown-transcript refresh
check("the refresh absorbs in place (same stem), not a new sibling", refreshed == old)
check("the contested stamp survives the absorb rewrite instead of being dropped", contested(old) == [sib])
check("the sibling gains no reverse stamp - direction stays earlier -> later", contested(sib) is None)

print("\n- mutation check: a direction guard blind to the note's own carried stamp re-inverts it -")
d = fresh()
old = write(OLD_FACT, S1)
sib = write(NEW_FACT, S2)
_real_contested_of = m._contested_of
m._contested_of = lambda fm: []                          # pretend the guard cannot see what this note already carries
_ = write(OLD_FACT, S1)
check("blinded, the sibling wrongly gains a reverse stamp pointing at the earlier note",
      contested(sib) == [old])
m._contested_of = _real_contested_of

# ── F3: rule 2 needs a VALUE-shaped literal, not merely a shared context ───────────────────────
print("\n- F3: a shared context path alone does not prove replacement -")
d = fresh()
cfg_old = write(f"The service reads its config from docker-compose.yml.{F}docker-compose.yml",
                S1, title="service config source")
cfg_new = write(f"The service now also binds port 9090, per docker-compose.yml.{F}docker-compose.yml · port 9090",
                S2, title="service config source")
check("a different fact about the same file is a sibling, not an absorb",
      cfg_new == f"{cfg_old}-2" and contested(cfg_old) == [cfg_new], str(contested(cfg_old)))
check("_has_value_literal rejects a bare context literal", not m._has_value_literal({"docker-compose.yml"}))
check("...but accepts a number+unit, a version, or a hex identifier (the commit sha shape)",
      m._has_value_literal({"the timeout is 30 seconds"}) and m._has_value_literal({"v2.0"})
      and m._has_value_literal({"87c8b17"}))

print("\n- mutation check: without the value-shaped gate, the shared context wrongly proves it -")
d = fresh()
_real_hvl = m._has_value_literal
m._has_value_literal = lambda facts: True
old2 = write(f"The service reads its config from docker-compose.yml.{F}docker-compose.yml",
             S1, title="service config source mut")
new2 = write(f"The service now also binds port 9090, per docker-compose.yml.{F}docker-compose.yml · port 9090",
             S2, title="service config source mut")
check("without the gate, a different fact about the same file is wrongly absorbed", new2 == old2)
m._has_value_literal = _real_hvl

print("\n- F3: rule 2' does not fire on disagreeing facts blocks even when the prose is a substring -")
d = fresh()
prose_old = write(f"Confirmed the timeout setting.{F}the http client timeout is 30 seconds",
                  S1, title="timeout confirmation")
prose_new = write(f"Confirmed the timeout setting. Now it is 5 seconds.{F}the http client timeout is 5 seconds",
                  S2, title="timeout confirmation")
check("disagreeing facts block the restated-text rule, even though the old prose is a literal substring "
      "of the new prose",
      prose_new == f"{prose_old}-2" and contested(prose_old) == [prose_new], str(contested(prose_old)))
check("_same_replacement reports it unproven, not restated",
      m._same_replacement(note(prose_old), "timeout confirmation",
                          f"Confirmed the timeout setting. Now it is 5 seconds.{F}the http client timeout is 5 seconds")
      == (False, "unproven"))

print("\n- F3: rule 2/2' run the write-time guard - a hallucinated additional value is not absorbed -")
d = fresh()
pool_old = write(f"The connection pool size is 10.{F}the connection pool size is 10", S1, title="pool size")
pool_new = write(f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10",
                 S2, title="pool size")
check("a value the new facts block does not back is not proven replacement, even though the old value is unchanged",
      pool_new == f"{pool_old}-2" and served(pool_old) == f"The connection pool size is 10.{F}the connection pool size is 10"
      and contested(pool_old) == [pool_new])
check("_same_replacement names the veto reason",
      m._same_replacement(note(pool_old), "pool size",
                          f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10")
      == (False, "unverified_value"))

print("\n- mutation check: without the write-time guard, the hallucinated value is wrongly absorbed -")
d = fresh()
_real_uv_guard = m._unverified_values
m._unverified_values = lambda desc: []
old3 = write(f"The connection pool size is 10.{F}the connection pool size is 10", S1, title="pool size mut")
new3 = write(f"The connection pool size is 10, raised temporarily to 99 under load.{F}the connection pool size is 10",
             S2, title="pool size mut")
check("without the guard, the hallucinated '99' is silently absorbed as the same fact", new3 == old3)
m._unverified_values = _real_uv_guard

# ── F4: _unverified_values is whole-token membership, not substring ────────────────────────────
print("\n- F4: whole-token membership, not substring, for unverified-value tokens -")
check("'5 MB' is not verified merely because the block says '25 MB'",
      m._unverified_values(f"The limit is 5 MB now.{F}the limit is 25 mb") == ["5 mb"])
check("'v2' is not verified merely because the block says 'v2.0'",
      m._unverified_values(f"Now on v2 of the API.{F}the api is now on v2.0") == ["v2"])
check("'80' is not verified merely because the block says '8080'",
      m._unverified_values(f"Now listening on port 80.{F}the service listens on port 8080") == ["80"])
check("a value that IS a whole token in the block is verified",
      m._unverified_values(f"The limit is 25 MB now.{F}the limit is 25 mb") == [])


def _pre_f4_unverified_values(desc):
    """The pre-fix implementation: `tok not in facts` over the block's raw TEXT (a substring scan)."""
    statement = m._norm_statement(desc)
    facts = " ".join(m._facts_in(desc))
    out = []
    for mo in m._VALUE_RE.finditer(statement):
        tok = m._norm_ws(mo.group(0).strip("~+- "))
        if tok and not tok.isdigit() and tok not in facts and tok not in out:
            out.append(tok)
        elif tok and tok.isdigit() and len(tok) >= 2 and tok not in facts and tok not in out:
            out.append(tok)
    return out


print("\n- mutation check: substring semantics restored by hand let all three through unflagged -")
_real_uv = m._unverified_values
m._unverified_values = _pre_f4_unverified_values
check("'5 MB' passes against '25 MB' unflagged (the bug)",
      m._unverified_values(f"The limit is 5 MB now.{F}the limit is 25 mb") == [])
check("'v2' passes against 'v2.0' unflagged (the bug)",
      m._unverified_values(f"Now on v2 of the API.{F}the api is now on v2.0") == [])
check("'80' passes against '8080' unflagged (the bug)",
      m._unverified_values(f"Now listening on port 80.{F}the service listens on port 8080") == [])
m._unverified_values = _real_uv

# ── F9: the near-dup gate and the retirement filters must key on the REAL absorb target ────────
print("\n- F9: a same-session refresh into a `-2` sibling excludes its OWN stem from the near-dup gate -")
d = fresh()
qbase = write("Use Kafka for the queue.", S2, title="queue backend f9")
qsib = write("Use RabbitMQ for the queue.", S1, title="queue backend f9")   # disagrees -> S1's note is the '-2'
check("setup: S1's note is the '-2' sibling, S2's note kept the base stem", qsib == f"{qbase}-2", qsib)
seen_exclude = []


def _fake_ndp(folder_path, project, ntype, title, desc, prevention, exclude, entities=None):
    seen_exclude.append(set(exclude))
    return []


_real_ndp = m._near_duplicate_paths
m._near_duplicate_paths = _fake_ndp
_ = write("Use RabbitMQ for the queue.", S1, title="queue backend f9")      # S1 refreshes its own sibling
check("the gate was called, excluding the absorb target's own stem (write_stem), not just base_stem",
      bool(seen_exclude) and qsib in seen_exclude[-1] and qbase in seen_exclude[-1], str(seen_exclude))
m._near_duplicate_paths = _real_ndp

print("\n- F9: the same refresh never supersedes its own stem, even when the gate returns it anyway -")
d = fresh()
qbase = write("Use Kafka for the queue.", S2, title="queue backend f9b")
qsib = write("Use RabbitMQ for the queue.", S1, title="queue backend f9b")
_real_ndp2 = m._near_duplicate_paths
m._near_duplicate_paths = lambda *a, **k: [note(qsib)]     # the pre-fix gate: ignores exclude, returns the target itself
refreshed = write("Use RabbitMQ for the queue.", S1, title="queue backend f9b")
check("the refresh still absorbs into the sibling, not a spurious self-supersede",
      refreshed == qsib and served(qsib) == "Use RabbitMQ for the queue.")
check("no self-reference lands in its own supersedes list",
      qsib not in (fm_of(qsib).get("supersedes") or []), str(fm_of(qsib).get("supersedes")))
m._near_duplicate_paths = _real_ndp2


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Consolidation integrity: F2, F5, F6, F10, F13
# ═══════════════════════════════════════════════════════════════════════════════════════════

def judge_fixed(verdict):
    def fake(old_title, old_desc, new_desc, project):
        return verdict
    return fake


# ── F5: supersede-then-stamp ordering ───────────────────────────────────────────────────────
print("\n- F5: a failed supersede leaves the pair contested, not orphaned -")
d = fresh()
o = write(OLD_FACT, S1, title="pool retry")
n = write(NEW_FACT, S2, title="pool retry")
check("setup: contested", contested(o) == [n])
_real_supersede = m.supersede_note
m.supersede_note = lambda *a, **k: False
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True))
check("a failed supersede leaves the earlier note live and still contested",
      note(o).exists() and contested(o) == [n], str(contested(o)))
m.supersede_note = _real_supersede

print("\n- mutation check: clearing the stamp separately before a failed retirement reproduces the orphan -")
d = fresh()
o = write(OLD_FACT, S1, title="pool retry mut")
n = write(NEW_FACT, S2, title="pool retry mut")


def _broken_order(p, new_stem, via="slug", extra_fields=None):
    cm._set_contested(p, [])          # the pre-fix bug: clear the stamp with a SEPARATE write first
    return False                       # ... then the retirement itself fails


m.supersede_note = _broken_order
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True))
check("with the old ordering restored by hand, a failed retirement orphans the pair", not contested(o))
m.supersede_note = _real_supersede

# ── F10: one pair's write failure must not abort the rest of the queue ─────────────────────
print("\n- F10: one pair's write failure does not abort the rest of the queue -")
d = fresh()
o1 = write(OLD_FACT, S1, title="pool retry a")
n1 = write(NEW_FACT, S2, title="pool retry a")
o2 = write(f"The retry backoff is 2 seconds.{F}the retry backoff is 2 seconds", S1, title="pool retry b")
n2 = write(f"The retry backoff is 9 seconds.{F}the retry backoff is 9 seconds", S2, title="pool retry b")
_real_write_atomic = m.write_atomic
_calls: list = []


def _raising_write_atomic(path, text):
    if "Superseded" not in path.parts and not _calls:
        _calls.append(path.name)
        raise OSError("simulated: file locked")
    return _real_write_atomic(path, text)


m.write_atomic = _raising_write_atomic
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True))
check("the run did not abort - both pairs were judged", res["judged"] == 2, str(res))
check("one pair recorded an error; 'left' still reflects it as unresolved",
      res["errors"] == 1 and res["left"] == 1, str(res))
m.write_atomic = _real_write_atomic

# ── F6: refresh_lock, wall-clock budget, consecutive-failure stop, backend recorded ─────────
print("\n- F6: refresh_lock is called after every judge call -")
d = fresh()
write(OLD_FACT, S1, title="lock a")
write(NEW_FACT, S2, title="lock a")
write(f"The retry backoff is 2 seconds.{F}the retry backoff is 2 seconds", S1, title="lock b")
write(f"The retry backoff is 9 seconds.{F}the retry backoff is 9 seconds", S2, title="lock b")
refreshes: list = []
_real_refresh = m.refresh_lock
m.refresh_lock = lambda: refreshes.append(1)
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True))
check("the lock was refreshed once per judge call", len(refreshes) == res["judged"] == 2,
      str((len(refreshes), res["judged"])))
m.refresh_lock = _real_refresh

print("\n- F6: a wall-clock budget stops the step, leaving the rest contested -")
d = fresh()
write(OLD_FACT, S1, title="clock a")
write(NEW_FACT, S2, title="clock a")
write(f"The retry backoff is 2 seconds.{F}the retry backoff is 2 seconds", S1, title="clock b")
write(f"The retry backoff is 9 seconds.{F}the retry backoff is 9 seconds", S2, title="clock b")
_real_monotonic = cm.time.monotonic
# call 1: the deadline computation itself (0.0 -> deadline 1.0). call 2: the first pair's
# pre-check (0.5, still inside budget - that pair is judged). call 3+: the second pair's
# pre-check (10_000.0, blown - the step stops there).
_clock = iter([0.0, 0.5, 10_000.0])


def _fake_monotonic():
    return next(_clock, 10_000.0)


cm.time.monotonic = _fake_monotonic
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True), seconds=1)
check("the step stopped early on the wall-clock budget, one pair left contested",
      res["judged"] == 1 and res["left"] == 1 and "wall-clock" in (res.get("skipped") or ""), str(res))
cm.time.monotonic = _real_monotonic

print("\n- F6: three consecutive unanswered verdicts stop the step, none charged -")
d = fresh()
pairs_od = []
for i, t in enumerate(["stop a", "stop b", "stop c", "stop d"]):
    oo = write(f"Value is {10 + i}.{F}value is {10 + i}", S1, title=t, date=f"2026-06-0{i + 1}")
    nn = write(f"Value is {90 + i}.{F}value is {90 + i}", S2, title=t, date=f"2026-06-0{i + 1}")
    pairs_od.append((oo, nn))
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(None))
check("stopped after 3 consecutive unanswered verdicts, none counted judged or charged",
      res["unanswered"] == 3 and res["judged"] == 0 and res["tokens_spent"] == 0
      and "consecutive" in (res.get("skipped") or ""), str(res))
check("the 4th pair was never even reached, still contested",
      contested(pairs_od[3][0]) == [pairs_od[3][1]])

print("\n- F6: the backend actually used per verdict is broken out in stats -")
d = fresh()
write(OLD_FACT, S1, title="backend a")
write(NEW_FACT, S2, title="backend a")
write(f"The retry backoff is 2 seconds.{F}the retry backoff is 2 seconds", S1, title="backend b")
write(f"The retry backoff is 9 seconds.{F}the retry backoff is 9 seconds", S2, title="backend b")
_i = {"n": 0}


def _judge_backend(old_title, old_desc, new_desc, project):
    _i["n"] += 1
    m._LLM_STATS[("cloud" if _i["n"] == 1 else "ollama")] = \
        m._LLM_STATS.get("cloud" if _i["n"] == 1 else "ollama", 0) + 1
    return True


res = cm.adjudicate_contested(apply=True, has_llm=True, judge=_judge_backend)
check("both backends actually used this run are broken out",
      res.get("judged_cloud") == 1 and res.get("judged_ollama") == 1, str(res))

print("\n- also-fix: the judge's recurrence carry has the same 'known session adds nothing' gate -")
d = fresh()
SA_ = "2026-05-01-1000-k8p-session-aaaaaaaa"
SB_ = "2026-05-09-1100-k8p-session-bbbbbbbb"
o = write(OLD_FACT, SA_, title="gate test", date="2026-05-01")
p_o = note(o)
p_o.write_text(m._stamp_frontmatter(p_o.read_text(encoding="utf-8"), {"recurrence": 2, "sources": [SA_, SB_]}),
               encoding="utf-8")
n = write(NEW_FACT, SB_, title="gate test", date="2026-05-09")      # session B is ALREADY a known source
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True))
check("a known session merging in adds nothing extra - mirrors the write-time 'grew' gate",
      str(fm_of(n).get("recurrence")) == "2", str(fm_of(n)))

# ── F13: pop the retired stem from the caller's cache; never save {} over a real cache ──────
print("\n- F13: a successful supersede pops the retired stem from the caller's cache -")
d = fresh()
o = write(OLD_FACT, S1, title="cache pop test")
n = write(NEW_FACT, S2, title="cache pop test")
fake_cache = {o: {"ntype": "decision", "project": PROJ, "recurrence": 1},
              n: {"ntype": "decision", "project": PROJ, "recurrence": 1}}
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge_fixed(True), cache=fake_cache)
check("the retired stem is popped from the caller's cache; the winner stays",
      o not in fake_cache and n in fake_cache, str(list(fake_cache)))

print("\n- F13: save_embed_cache refuses to overwrite a non-empty on-disk cache with an empty one -")
d = fresh()
m.save_embed_cache({"some-stem": {"ntype": "decision", "vec": [1.0]}})
before = m.EMBED_CACHE.read_text(encoding="utf-8")
m.save_embed_cache({})
after = m.EMBED_CACHE.read_text(encoding="utf-8")
check("the on-disk cache is unchanged, not clobbered with {}", after == before and after != "{}", after)

print("\n- mutation check: without the guard, an empty cache clobbers a non-empty one -")
d = fresh()
m.save_embed_cache({"some-stem": {"ntype": "decision", "vec": [1.0]}})
m._save_json_generations(m.EMBED_CACHE, __import__("json").dumps({}), prev=False)   # the pre-fix write path
check("without the guard, {} silently overwrites the real cache (the bug)",
      m.EMBED_CACHE.read_text(encoding="utf-8") == "{}")

# ── F2: the near-dup merge must not cluster a pair K8 is keeping apart ──────────────────────
print("\n- F2: a contested/disputed pair is excluded from the near-dup merge, both members -")
cache2 = {
    "2026-06-01-proj-mistake-a": {"ntype": "mistake", "project": "proj", "title": "oom",
        "desc": "cuda out of memory during the training loop", "vec": [1.0, 0.0], "recurrence": 1},
    "2026-06-02-proj-mistake-b": {"ntype": "mistake", "project": "proj", "title": "oom2",
        "desc": "cuda out of memory during the training loop", "vec": [1.0, 0.0], "recurrence": 1},
}
fl_no_exclude = [set(c) for c in cm.find_clusters(cache2)]
check("setup: without exclude they would cluster (the merge this fix must NOT apply to a kept-apart pair)",
      any(set(cache2) <= c for c in fl_no_exclude), str(fl_no_exclude))
fl_excluded = [set(c) for c in cm.find_clusters(cache2, exclude=set(cache2))]
check("with both members excluded, no cluster forms", fl_excluded == [])
fl_one = [set(c) for c in cm.find_clusters(cache2, exclude={"2026-06-01-proj-mistake-a"})]
check("excluding just ONE member still keeps the pair apart on the other side too",
      not any("2026-06-02-proj-mistake-b" in c for c in fl_one))


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Identity and stamps: F7, F8, F11
# ═══════════════════════════════════════════════════════════════════════════════════════════

print("\n- F7: a coincidental digit-suffixed title is not folded without a base note -")
d = fresh()
p3 = write("Python 3 support was added.", S1, title="python 3", ntype="pattern")
check("'python 3' stands on its own stem - no base 'python' note exists",
      p3 == "2026-06-01-k8proj-pattern-python-3" and m._sibling_key(p3) == (PROJ, "pattern", "python-3"), p3)

print("\n- F7: writing a real base note does not retroactively swallow a pre-existing digit-suffixed title -")
d = fresh()
p3b = write("Python 3 support was added.", S1, title="python 3", ntype="pattern")
pbase = write("Use Python for scripting.", S2, title="python", ntype="pattern")
check("both notes stand on their own exact stems - no collision, no contested stamp",
      pbase == "2026-06-01-k8proj-pattern-python" and p3b == "2026-06-01-k8proj-pattern-python-3"
      and contested(pbase, "pattern") is None and contested(p3b, "pattern") is None, (pbase, p3b))

print("\n- F7: a TRUE stamped sibling whose slug itself ends in a digit still folds correctly -")
d = fresh()
h1 = write(f"Use HTTP/2 for the API.{F}use http 2 for the api", S1, title="use HTTP/2")
h2 = write(f"Use HTTP/3 for the API.{F}use http 3 for the api", S2, title="use HTTP/2")
check("setup: h2 is minted as a true '-2' sibling of h1 (h1's own slug already ends in a digit)",
      h2 == f"{h1}-2", h2)
check("the sibling carries the sibling_of stamp naming the base",
      m._read_frontmatter_file(note(h2)).get("sibling_of") == h1)
check("_sibling_key folds them by the stamp, not the coincidental trailing digit",
      m._sibling_key(h1) == m._sibling_key(h2), (m._sibling_key(h1), m._sibling_key(h2)))

print("\n- mutation check: without the stamp/existence check, 'python 3' folds into any 'python' -")
d = fresh()
_real_slug_family = m._slug_family
m._slug_family = lambda p, parsed, slug, folder_path: (
    parsed["slug"] == slug or (parsed["slug"][:-2] == slug and parsed["slug"][-2] == "-"
                               and parsed["slug"][-1] in "23456789"))
p3c = write("Python 3 support was added.", S1, title="python 3", ntype="pattern")
pbase2 = write("Use Python for scripting.", S2, title="python", ntype="pattern")
check("with the old pattern-only rule restored, the unrelated titles collide (the bug)",
      pbase2 != "2026-06-01-k8proj-pattern-python" or contested(pbase2, "pattern") is not None
      or contested(p3c, "pattern") is not None)
m._slug_family = _real_slug_family

# ── F8: `resolves:` targets the exact slug only ─────────────────────────────────────────────
print("\n- F8: `resolves:` resolves the exact slug, not the whole slug family -")
d = fresh()
mbase = write("Retries loop forever.", S1, title="retry storm", ntype="mistake")
msib = write("Something else about retries entirely, unrelated wording here.", S2, title="retry storm", ntype="mistake")
check("setup: the mistake has a contested sibling", msib == f"{mbase}-2", msib)
write("Cap retries at 3 with backoff.", S1, title="cap retries", ntype="decision", resolves="retry storm")
check("the named mistake is resolved", m._read_frontmatter_file(note(mbase, "mistake")).get("status") == "resolved")
check("its contested sibling is NOT silently resolved too - it was never named",
      m._read_frontmatter_file(note(msib, "mistake")).get("status") != "resolved",
      m._read_frontmatter_file(note(msib, "mistake")))

# ── F11: _stamp_frontmatter recognises and re-emits a UTF-8 BOM ─────────────────────────────
print("\n- F11: _stamp_frontmatter recognizes and re-emits a UTF-8 BOM -")
BOM_TEXT = "﻿---\ndate: 2026-06-01\nproject: p\ntype: decision\n---\n\n# t\n\nbody\n"
stamped = m._stamp_frontmatter(BOM_TEXT, {"contested": ["x"]})
check("the BOM is preserved on the result", stamped.startswith("﻿"))
fm_bom, _ = m._read_frontmatter(stamped)
check("the field was actually written, not a silent no-op", fm_bom.get("contested") == ["x"], stamped)


def _pre_f11_stamp(text, fields):
    """The pre-fix implementation: no BOM handling at all."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body = text[end:]
    pending = dict(fields)
    out = []
    for ln in text[:end].split("\n"):
        key = ln.split(":", 1)[0].strip() if ":" in ln else ""
        if key in pending:
            out.append(f"{key}: {m._yaml_scalar(pending.pop(key))}")
        else:
            out.append(ln)
    for k, v in pending.items():
        out.append(f"{k}: {m._yaml_scalar(v)}")
    return "\n".join(out) + body


print("\n- mutation check: without BOM-awareness, the stamp silently no-ops -")
broken = _pre_f11_stamp(BOM_TEXT, {"contested": ["x"]})
check("the pre-fix implementation returns the text byte-identical (the bug: a silent no-op)",
      broken == BOM_TEXT)

print("\n- F11: a BOM-saved note's stamp actually lands, through every stamp writer -")
d = fresh()
(m.VAULT / "Decisions").mkdir(exist_ok=True)
bom_note = m.VAULT / "Decisions" / "2026-06-01-p-decision-bom-note.md"
bom_note.write_text("﻿---\ndate: 2026-06-01\nproject: p\ntype: decision\n---\n\n# bom note\n\nsome text\n",
                    encoding="utf-8")
ok1 = m._mark_contested(bom_note, "2026-06-01-p-decision-other")
check("_mark_contested actually stamps a BOM note",
      ok1 and m._read_frontmatter_file(bom_note).get("contested") == ["2026-06-01-p-decision-other"])
cm._set_contested(bom_note, [], disputed="2026-06-01-p-decision-other")
check("_set_contested actually stamps a BOM note",
      m._read_frontmatter_file(bom_note).get("disputed") == ["2026-06-01-p-decision-other"])
winner = write("winner content", S1, title="bom winner")
ok3 = m.supersede_note(bom_note, winner, via="slug")
check("supersede_note actually retires a BOM note",
      ok3 and not bom_note.exists()
      and (m.VAULT / "Decisions" / "Superseded" / "2026-06-01-p-decision-bom-note.md").exists())


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Surfaces: F12, F15, and the read-path also-fix items
# ═══════════════════════════════════════════════════════════════════════════════════════════

print("\n- F12: the CLI text path prints the earlier sibling from its own field -")
d = fresh()
old_cli = write(OLD_FACT, S1, title="cli earlier test")
new_cli = write(NEW_FACT, S2, title="cli earlier test")
m.save_embed_cache({
    old_cli: {"ntype": "decision", "project": PROJ, "title": "cli earlier test", "desc": OLD_FACT,
             "prevention": "", "recurrence": 1},
    new_cli: {"ntype": "decision", "project": PROJ, "title": "cli earlier test", "desc": NEW_FACT,
             "prevention": "", "recurrence": 1},
})
buf = io.StringIO()
_argv = sys.argv
sys.argv = ["memory_search.py", "http client timeout", PROJ]
with contextlib.redirect_stdout(buf):
    ms.main()
sys.argv = _argv
out = buf.getvalue()
check("the CLI prints the earlier statement's own line, not just the on-disk snippet",
      "earlier under this title:" in out and "30 seconds" in out, out)

print("\n- F12: the MCP tool prints the earlier sibling from its own field -")
res_text, _is_err = mcp_server._tool_memory_search({"query": "http client timeout", "project": PROJ})
check("the MCP tool prints the earlier statement's own line too",
      "earlier under this title:" in res_text and "30 seconds" in res_text, res_text)

print("\n- F12: search_core folds over a window well past the final cut, not just the cut itself -")
d = fresh()
_extra_cache = {}
for i in range(6):
    stem_i = write(f"Unrelated note number {i}.", S1, title=f"unrelated {i}", ntype="pattern")
    _extra_cache[stem_i] = {"ntype": "pattern", "project": PROJ, "title": f"unrelated {i}",
                            "desc": f"Unrelated note number {i}.", "prevention": "", "recurrence": 1,
                            "vec": [0.1, 0.2]}   # a "vec" key routes search_core past _lexical_only
m.save_embed_cache(_extra_cache)
_seen_len = []
_real_pair_siblings = m.pair_siblings


def _spy_pair_siblings(hits, attach=False):
    _seen_len.append(len(hits))
    return _real_pair_siblings(hits, attach=attach)


m.pair_siblings = _spy_pair_siblings
ms.search_core("unrelated note", PROJ, k=2)
m.pair_siblings = _real_pair_siblings
check("pair_siblings was handed a window of at least 2*k candidates, not just k",
      bool(_seen_len) and _seen_len[0] >= 4, str(_seen_len))

# ── F15: fold over the FULL ranked list; every retrieval path folds ────────────────────────
print("\n- F15: retrieve_relevant folds a sibling ranked past the old 2k window -")
d = fresh()
old15 = write(OLD_FACT, S1, title="deep fold test")
new15 = write(NEW_FACT, S2, title="deep fold test")
# push the sibling pair far down the ranking with a wall of higher-recurrence unrelated notes
m.save_embed_cache({
    old15: {"ntype": "decision", "project": PROJ, "title": "deep fold test", "desc": OLD_FACT,
            "prevention": "", "recurrence": 1},
    new15: {"ntype": "decision", "project": PROJ, "title": "deep fold test", "desc": NEW_FACT,
            "prevention": "", "recurrence": 1},
    **{f"2026-06-01-{PROJ}-decision-filler-{i}": {
        "ntype": "decision", "project": PROJ, "title": f"filler {i}",
        "desc": "the http client timeout matters for filler purposes", "prevention": "",
        "recurrence": 50} for i in range(10)},
})
hits15 = m.retrieve_relevant(PROJ, "http client timeout", 3, cache=m.load_embed_cache())
stems15 = [h["stem"] for h in hits15]
check("the sibling still folds even ranked well past a 2k (k=3 -> 6) window",
      new15 in stems15 and old15 not in stems15
      and any(h.get("earlier") == [old15] for h in hits15 if h["stem"] == new15), str(hits15))

print("\n- F15: retrieve_cross_project also folds same-slug siblings (previously never did) -")
d = fresh()
oldx = write(OLD_FACT, S1, title="cross project fold", ntype="pattern")
newx = write(NEW_FACT, S2, title="cross project fold", ntype="pattern")
m.save_embed_cache({
    oldx: {"ntype": "pattern", "project": "other", "title": "cross project fold", "desc": OLD_FACT,
          "prevention": "", "recurrence": 1},
    newx: {"ntype": "pattern", "project": "other", "title": "cross project fold", "desc": NEW_FACT,
          "prevention": "", "recurrence": 1},
})
xhits = m.retrieve_cross_project(PROJ, "http client timeout", k=5, cache=m.load_embed_cache())
xstems = [h["stem"] for h in xhits]
check("cross-project recall folds the pair too - one hit, the earlier attached",
      newx in xstems and oldx not in xstems, str(xhits))

print("\n- F15: _recency_fallback also folds same-slug siblings (previously never did) -")
d = fresh()
oldr = write("First note on the topic.", S1, title="recency fold", ntype="mistake")
newr = write("Something else about it entirely, unrelated wording here.", S2, title="recency fold", ntype="mistake")
rhits = m._recency_fallback(PROJ, 5)
rstems = [h["stem"] for h in rhits]
check("the recency fallback folds the pair too - one hit, not two slots for one topic",
      newr in rstems and oldr not in rstems, str(rhits))

print("\n- also-fix: dead index rows are filtered BEFORE folding, not after -")
d = fresh()
olddead = write(OLD_FACT, S1, title="stale lead test")
newdead = write(NEW_FACT, S2, title="stale lead test")
# simulate a stale cache: the LEAD was properly superseded (moved to Superseded/, same as
# supersede_note does) outside this cache's knowledge, but the earlier sibling is still live.
(m.VAULT / "Decisions" / "Superseded").mkdir(exist_ok=True)
note(newdead).rename(m.VAULT / "Decisions" / "Superseded" / f"{newdead}.md")
m.save_embed_cache({
    olddead: {"ntype": "decision", "project": PROJ, "title": "stale lead test", "desc": OLD_FACT,
              "prevention": "", "recurrence": 1},
    newdead: {"ntype": "decision", "project": PROJ, "title": "stale lead test", "desc": NEW_FACT,
              "prevention": "", "recurrence": 1},
})
deadhits = m.retrieve_relevant(PROJ, "http client timeout", 3, cache=m.load_embed_cache())
deadstems = [h["stem"] for h in deadhits]
check("the live earlier note survives - it is not folded into (and dropped with) a dead lead",
      olddead in deadstems, str(deadhits))

print("\n- also-fix: a folded group takes its best member's score -")


def _score_for(old_recur, new_recur):
    fresh()
    o = write(OLD_FACT, S1, title="score fold test")
    n = write(NEW_FACT, S2, title="score fold test")
    m.save_embed_cache({
        o: {"ntype": "decision", "project": PROJ, "title": "score fold test", "desc": OLD_FACT,
            "prevention": "", "recurrence": old_recur},
        n: {"ntype": "decision", "project": PROJ, "title": "score fold test", "desc": NEW_FACT,
            "prevention": "", "recurrence": new_recur},
    })
    hh = m.retrieve_relevant(PROJ, "http client timeout", 3, cache=m.load_embed_cache())
    return next(h for h in hh if h["stem"] == n)["score"]


score_weak_lead = _score_for(20, 1)      # the EARLIER (folded-out) note has the high recurrence
score_strong_lead = _score_for(1, 20)    # the LEAD itself has the high recurrence
check("the group's best member sets the score - a strong earlier sibling lifts a weak lead to "
      "roughly the same reported score as when the lead itself is strong",
      score_weak_lead > 0 and abs(score_weak_lead - score_strong_lead) <= 0.05 * max(score_strong_lead, 1e-9),
      (score_weak_lead, score_strong_lead))

print("\n- also-fix: GRAPH_HOPS never re-adds a stem that was just folded into its lead -")
d = fresh()
oldg = write(f"See also [[2026-06-01-{PROJ}-decision-graph-hop-lead]] for context.{F}graph hop link",
            S1, title="graph hop earlier")
leadg = write(f"The current answer, linking [[{oldg}]] as history.{F}graph hop link current",
             S2, title="graph hop earlier")
m.save_embed_cache({
    oldg: {"ntype": "decision", "project": PROJ, "title": "graph hop earlier",
          "desc": f"See also for context.{F}graph hop link", "prevention": "", "recurrence": 1},
    leadg: {"ntype": "decision", "project": PROJ, "title": "graph hop earlier",
           "desc": f"The current answer, linking as history.{F}graph hop link current",
           "prevention": "", "recurrence": 1},
})
ghits = m.retrieve_relevant(PROJ, "graph hop link", 3, cache=m.load_embed_cache(), expand_hops=1)
gstems = [h["stem"] for h in ghits]
check("the folded-away earlier note is not re-added as its own hit via the graph-hop link",
      gstems.count(oldg) == 0, str(gstems))

print("\n- also-fix: _earlier_text keeps a note's Prevention in the compact earlier line -")
d = fresh()
prevention_note = write("The retry logic looped forever.", S1, title="prevention carry", ntype="mistake")
p_path = note(prevention_note, "mistake")
p_path.write_text(p_path.read_text(encoding="utf-8").replace(
    "\n\n**Project:**", "\n\n**Prevention:** Always cap retries with backoff.\n\n**Project:**"),
    encoding="utf-8")
etext = m._earlier_text(prevention_note, "mistake")
check("the earlier line carries the Prevention text, not just the statement",
      "cap retries" in etext.lower(), etext)

print(f"\nxhigh review (write path + consolidation integrity + identity/stamps + surfaces): "
      f"{len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
