#!/usr/bin/env python3
"""K8 layer 3: the judge outside the hook - `consolidate_memory.adjudicate_contested`.

The write path keeps a same-slug note from another session as a live sibling unless the replacement
is proven and stamps the earlier note `contested` (layer 1). At sleep, oldest pair first and within a
token budget a run (K8-B: 100k, ~240 pairs; a call reporting no tokens is charged the measured mean),
each pair goes to the same-fact judge: `replaces` retires the earlier note with
`valid_to` and `superseded_via: judge` and carries its recurrence and sources into the winner;
`separate` clears the stamp and both stay; no answer leaves the pair contested; a pair whose newer
note is gone is dropped from the stamp. Without a backend nothing is judged and the pairs stay
visible in `conflicts()` and `integrity()`. Dry-run writes nothing. The judge here is a fake with a
scripted verdict; the real one is measured in research/results/k8_judge_eval.json.

    python tests/_test_k8_adjudicate.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
import consolidate_memory as cm  # noqa: E402
import digest as dg  # noqa: E402
import integrity as ig  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


F = m._FACTS_MARK
SA = "2026-06-01-1000-k8p-session-aaaaaaaa"
SB = "2026-06-09-1100-k8p-session-bbbbbbbb"
OLD = f"The HTTP client timeout is 30 seconds.{F}the HTTP client timeout is 30 seconds"
NEW = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"
SEEN: list[tuple] = []


def judge(verdict):
    def fake(old_title, old_desc, new_desc, project):
        SEEN.append((old_title, old_desc[:20], new_desc[:20], project))
        return verdict
    return fake


def pair(title="http client timeout", ntype="decision", old_desc=OLD, new_desc=NEW, d1="2026-06-01", d2="2026-06-09"):
    o = m.write_typed_note(m.TYPE_FOLDER[ntype], {"title": title, "description": old_desc}, "k8p", d1, ["t"], ntype, session_stem_=SA)
    n = m.write_typed_note(m.TYPE_FOLDER[ntype], {"title": title, "description": new_desc}, "k8p", d2, ["t"], ntype, session_stem_=SB)
    return o, n


def fresh():
    SEEN.clear()
    d = make_sandbox(m, "k8l3_", offline=True)
    m.generate_json = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no real call in this suite"))
    return d


def fm(stem, ntype="decision", where=""):
    return m._read_frontmatter_file(m.VAULT / m.TYPE_FOLDER[ntype] / where / f"{stem}.md")


print("\n- replaces: the earlier note retires at sleep, its history carried -")
d = fresh()
o, n = pair()
check("the pair is contested on disk", fm(o).get("contested") == [n])
plan = cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(True))
check("dry-run reads the pair and calls the judge once", plan["pairs"] == 1 and plan["judged"] == 1 and len(SEEN) == 1, str(plan))
check("dry-run writes nothing", (d / "Decisions" / f"{o}.md").exists() and fm(o).get("contested") == [n])
check("the judge saw the earlier title, both statements and the project",
      SEEN[0][0] == "http client timeout" and SEEN[0][1] == OLD[:20] and SEEN[0][2] == NEW[:20] and SEEN[0][3] == "k8p", str(SEEN))
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("apply: one pair judged, one replaces, none left", (res["judged"], res["replaces"], res["left"]) == (1, 1, 0), str(res))
sup = fm(o, where="Superseded")
check("the earlier note is in Superseded/, closed at the winner's date, attributed to the judge",
      sup.get("status") == "superseded" and sup.get("superseded_by") == n and sup.get("superseded_via") == "judge"
      and str(sup.get("valid_to")) == "2026-06-09", str(sup))
win = fm(n)
check("the winner carries the retired note's provenance: recurrence 2, both sessions, supersedes",
      str(win.get("recurrence")) == "2" and set(win.get("sources") or []) == {SA, SB} and win.get("supersedes") == [o], str(win))
check("the stamp is settled: nothing contested remains", m._iter_contested(None) == [] and not fm(o, where="Superseded").get("contested"))
check("as_of still answers for the old day with the retired note",
      any(r["stem"] == o for r in m.as_of("k8p", "2026-06-05")) and not any(r["stem"] == o for r in m.as_of("k8p", "2026-06-10")))

print("\n- separate: both stay, the stamp clears -")
d = fresh()
o, n = pair(old_desc=f"The primary database is PostgreSQL 16.{F}postgresql 16", new_desc=f"The cache layer is Redis 7.{F}redis 7", title="the stack")
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(False))
check("one separate, none left", (res["separate"], res["left"]) == (1, 0), str(res))
check("both notes live, the stamp cleared", (d / "Decisions" / f"{o}.md").exists() and (d / "Decisions" / f"{n}.md").exists()
      and not fm(o).get("contested") and m._iter_contested(None) == [])
check("no retirement happened", not (d / "Decisions" / "Superseded").exists() or not list((d / "Decisions" / "Superseded").glob("*.md")))

print("\n- unanswered: the pair stays contested -")
d = fresh()
o, n = pair()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(None))
check("unanswered counted, left 1, stamp intact", (res["unanswered"], res["left"]) == (1, 1) and fm(o).get("contested") == [n], str(res))

print("\n- the budget, oldest first (K8-B) -")
d = fresh()
o1, n1 = pair(title="alpha", d1="2026-05-01", d2="2026-05-02")
o2, n2 = pair(title="beta", d1="2026-06-01", d2="2026-06-09")
res = cm.adjudicate_contested(apply=True, has_llm=True, cap=1, judge=judge(True))
check("one call under a hard cap of one: the OLDER pair goes first, the newer is left contested",
      res["judged"] == 1 and res["left"] == 1 and SEEN[0][0] == "alpha" and fm(o2).get("contested") == [n2]
      and fm(o1, where="Superseded").get("superseded_via") == "judge", str(res))
d = fresh()
o1, n1 = pair(title="alpha", d1="2026-05-01", d2="2026-05-02")
o2, n2 = pair(title="beta", d1="2026-06-01", d2="2026-06-09")
res = cm.adjudicate_contested(apply=True, has_llm=True, budget=1, judge=judge(True))
check("a stub judge reports no tokens and is charged the measured mean: a budget of one token buys one call",
      res["judged"] == 1 and res["tokens_spent"] == cm.TOKENS_PER_PAIR_EST and res["estimated_calls"] == 1
      and res["left"] == 1 and SEEN[0][0] == "alpha", str(res))
res = cm.adjudicate_contested(apply=True, has_llm=True, budget=cm.TOKENS_PER_PAIR_EST * 2, judge=judge(True))
check("the next run spends its budget on what was left", res["judged"] == 1 and res["left"] == 0, str(res))


def counting_judge(prompt_tokens: int, eval_tokens: int):
    """A judge whose backend reports token counts the way call_ollama does."""
    def fake(old_title, old_desc, new_desc, project):
        SEEN.append((old_title, old_desc[:20], new_desc[:20], project))
        m._LLM_STATS["prompt_tokens"] = m._LLM_STATS.get("prompt_tokens", 0) + prompt_tokens
        m._LLM_STATS["eval_tokens"] = m._LLM_STATS.get("eval_tokens", 0) + eval_tokens
        return True
    return fake


d = fresh()
o1, n1 = pair(title="alpha", d1="2026-05-01", d2="2026-05-02")
o2, n2 = pair(title="beta", d1="2026-06-01", d2="2026-06-09")
o3, n3 = pair(title="gamma", d1="2026-07-01", d2="2026-07-09")
res = cm.adjudicate_contested(apply=True, has_llm=True, budget=680, judge=counting_judge(300, 40))
check("reported tokens are what the budget counts, read before each call: 680 tokens buy two 340-token calls, oldest two, the third left",
      res["judged"] == 2 and res["tokens_spent"] == 680 and res["estimated_calls"] == 0 and res["left"] == 1
      and [s[0] for s in SEEN] == ["alpha", "beta"] and fm(o3).get("contested") == [n3]
      and (res["prompt_tokens"], res["eval_tokens"]) == (600, 80), str(res))
d = fresh()
o, n = pair()
res = cm.adjudicate_contested(apply=True, has_llm=True, budget=0, judge=judge(True))
check("budget 0 switches the judge off: no call, the pair stays contested and visible",
      res["judged"] == 0 and res["skipped"] and SEEN == [] and fm(o).get("contested") == [n], str(res))
check("the defaults: 100k tokens a run - at least three times the 73 pairs a week measured on the owner's vault - and no cap on calls",
      cm.CONTESTED_BUDGET == 100_000 and cm.CONTESTED_CAP == 0 and cm.TOKENS_PER_PAIR_EST == 415
      and cm.CONTESTED_BUDGET // cm.TOKENS_PER_PAIR_EST >= 3 * 73)

print("\n- no backend: nothing judged, the pairs visible -")
d = fresh()
o, n = pair()
res = cm.adjudicate_contested(apply=True, has_llm=False, judge=judge(True))
check("skipped with a reason, left equals pairs, no call", res["skipped"] and res["left"] == 1 and SEEN == [], str(res))
rows = [r for r in dg.compute_conflicts(None) if r["kind"] == "contested"]
check("conflicts() shows the pair", len(rows) == 1 and rows[0]["old_stem"] == o and rows[0]["new_stem"] == n)
rep = ig.check_store(None, links=False)
check("integrity() counts it", rep["totals"].get("contested_pairs") == 1 and "contested pairs: 1" in ig.render(rep))

print("\n- a sibling that is gone drops from the stamp -")
d = fresh()
o, n = pair()
(d / "Decisions" / f"{n}.md").unlink()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("nothing to judge, the stale stamp cleared", res["pairs"] == 0 and not fm(o).get("contested"), str(res))

print("\n- the archived branch -")
d = fresh()
o = m.write_typed_note("Decisions", {"title": "cache backend", "description": "use redis for the cache"}, "k8p", "2026-01-01", ["t"], "decision")
arch = d / "Decisions" / "Archive"
arch.mkdir()
(d / "Decisions" / f"{o}.md").rename(arch / f"{o}.md")
n = m.write_typed_note("Decisions", {"title": "cache backend", "description": "use memcached for the cache"}, "k8p", "2026-09-10", ["t"], "decision")
check("the archived earlier note is stamped where it lives", fm(o, where="Archive").get("contested") == [n])
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("the judge retires it into Archive/Superseded/ with valid_to",
      res["replaces"] == 1 and (arch / "Superseded" / f"{o}.md").exists()
      and str(m._read_frontmatter_file(arch / "Superseded" / f"{o}.md").get("valid_to")) == "2026-09-10")

print("\n- the guards: a `replaces` the proof does not back is not acted on -")
d = fresh()
o, n = pair(new_desc="Pull request approval policy was already documented and enforced.")
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("rule 4 at sleep: a fact note is not retired for a restatement without literals",
      res["vetoed"] == 1 and res["replaces"] == 0 and (d / "Decisions" / f"{o}.md").exists()
      and not fm(o).get("contested") and res["left"] == 0, str(res))
check("the vetoed pair is stamped disputed, off the judge's queue, and conflicts()/integrity() still show it",
      fm(o).get("disputed") == [n] and m._iter_contested(None) == []
      and any(r["kind"] == "disputed" and r["old_stem"] == o and r["new_stem"] == n for r in dg.compute_conflicts(None))
      and ig.check_store(None, links=False)["totals"].get("disputed_pairs") == 1, str(fm(o)))
check("a second run makes no call for a disputed pair",
      cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))["judged"] == 0)
d = fresh()
o, n = pair(old_desc=f"The upload size limit is 25 MB.{F}the upload size limit is 25 MB",
            new_desc=f"The system enforces an upload size limit of 100 MB.{F}the request rate limit is 100 per minute")
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a value the session never said cannot retire a verified one",
      res["vetoed"] == 1 and (d / "Decisions" / f"{o}.md").exists(), str(res))
check("the unverified value is named", m._unverified_values(f"The limit is 100 MB.{F}the rate limit is 100 per minute") == ["100 mb"],
      str(m._unverified_values(f"The limit is 100 MB.{F}the rate limit is 100 per minute")))
d = fresh()
o, n = pair(old_desc=f"The upload size limit is 25 MB.{F}the upload size limit is 25 MB",
            new_desc=f"The upload size limit check is working correctly after the change.{F}the full test suite stayed green")
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a valued fact is not retired for a statement with no value in it, even one with a literal",
      res["vetoed"] == 1 and (d / "Decisions" / f"{o}.md").exists() and fm(o).get("disputed") == [n], str(res))
check("a value in the new block beside a value-less statement does not lift the guard (another fact's literal)",
      cm._replacement_guard(f"The upload size limit is 25 MB.{F}the upload size limit is 25 MB",
                            f"The upload size limit check is working correctly.{F}the request rate limit is 100 per minute") == "no_value_in_new")
check("a retraction that names no value is held too (conservative, both stay served)",
      cm._replacement_guard(f"gRPC on port 9090.{F}port 9090", f"gRPC was removed, plain HTTP now.{F}plain http") == "no_value_in_new")
check("a verified value passes the guard",
      m._unverified_values(f"The timeout is 5 seconds.{F}the http client timeout is 5 seconds") == []
      and cm._replacement_guard(OLD, NEW) == "")
check("no literals on the old side: the guard stays out of the judge's way",
      cm._replacement_guard("use redis for the cache", "use memcached for the cache") == "")
d = fresh()
o, n = pair()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a proper replacement (verified values on both sides) still retires", res["replaces"] == 1 and res["vetoed"] == 0)

d = fresh()
o = m.write_typed_note("Decisions", {"title": "queue backend", "description": f"jobs go through sqs.{F}sqs"}, "k8p", "2026-01-01", ["t"], "decision")
arch = d / "Decisions" / "Archive"
arch.mkdir(exist_ok=True)
(d / "Decisions" / f"{o}.md").rename(arch / f"{o}.md")
n = m.write_typed_note("Decisions", {"title": "queue backend", "description": f"jobs go through nats.{F}nats"}, "k8p", "2026-05-01", ["t"], "decision")
(d / "Decisions" / f"{n}.md").rename(arch / f"{n}.md")          # the newer note aged into Archive/ too
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a pair whose newer note is itself archived is still judged, not dropped as gone",
      res["pairs"] == 1 and res["replaces"] == 1 and (arch / "Superseded" / f"{o}.md").exists(), str(res))

print("\n- tokens are counted per run -")
d = fresh()
o, n = pair()
m._LLM_STATS["prompt_tokens"] = 1000
m._LLM_STATS["eval_tokens"] = 100


def counting(old_title, old_desc, new_desc, project):
    m._LLM_STATS["prompt_tokens"] += 375
    m._LLM_STATS["eval_tokens"] += 40
    return True


res = cm.adjudicate_contested(apply=True, has_llm=True, judge=counting)
check("the run reports the tokens its verdicts cost", (res["prompt_tokens"], res["eval_tokens"]) == (375, 40), str(res))

print("\n- mutation check: a scan that misses the stamp is caught -")
d = fresh()
o, n = pair()
#: The run reads both stamps in one walk (#13, 2026-09-23), so that walk is what is blinded.
_real = m._iter_contested_both
m._iter_contested_both = lambda project=None: ([], [])
try:
    res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
finally:
    m._iter_contested_both = _real
check("with the scan blinded no pair is found - the suite above would redden", res["pairs"] == 0 and fm(o).get("contested") == [n])

print("\n- a run of retirements writes the vector cache once, not once a pair -")
#: K9 tail, measured by the auditing session on a copy of the owner's cache: 6 514 entries,
#: 107 MB, 2 093 ms to serialise and write ONE generation. `supersede_note` loaded the cache,
#: popped the retired stem and saved the whole file back - once per retired note - while the
#: consolidator it runs under already holds that cache, pops the same stem from it (F13) and
#: saves it once when the loop is done. So N retirements cost N+1 full rewrites where one does
#: the job: ~21 s at ten pairs, ~7 min at two hundred.
#:
#: Counted, not timed. The number of full writes is a property of the code and has no spread,
#: so this gate can see a difference of ONE write; a stopwatch on a 107 MB file on a shared disk
#: could not see a difference of ten.
N_PAIRS = 4


def seeded_pairs(n):
    """n independent contested pairs, every stem present in the vector cache."""
    made = []
    for i in range(n):
        made.append(pair(title=f"queue backend number {i}",
                         old_desc=f"jobs go through sqs {i}.{F}sqs {i}",
                         new_desc=f"jobs go through nats {i}.{F}nats {i}"))
    m.save_embed_cache({s: {"title": s, "desc": "d", "vec": [0.1, 0.2]}
                        for o_, n_ in made for s in (o_, n_)})
    return made


def counting_saves(run):
    real, calls = m.save_embed_cache, []

    def spy(cache):
        calls.append(len(cache))
        return real(cache)

    m.save_embed_cache = spy
    try:
        res = run()
    finally:
        m.save_embed_cache = real
    return res, calls


# (a) the consolidator's own path: it passes its cache in and saves it once afterwards
d = fresh()
made = seeded_pairs(N_PAIRS)
shared = m.load_embed_cache()
res, calls = counting_saves(lambda: cm.adjudicate_contested(apply=True, has_llm=True,
                                                            judge=judge(True), cache=shared))
check(f"{N_PAIRS} pairs judged `replaces`", res["replaces"] == N_PAIRS, str(res))
check("with the caller's cache in hand, the judge loop writes the cache ZERO times - "
      "the caller writes it once",
      calls == [], f"{len(calls)} full write(s) inside the loop")
check("and every retired stem is gone from the cache the caller will save",
      not any(o_ in shared for o_, _ in made) and all(n_ in shared for _, n_ in made),
      str(sorted(shared)))

# (b) standalone - no cache passed - still one write for the whole run, not one a pair
d = fresh()
made = seeded_pairs(N_PAIRS)
res, calls = counting_saves(lambda: cm.adjudicate_contested(apply=True, has_llm=True,
                                                            judge=judge(True)))
check(f"standalone: {N_PAIRS} retirements, ONE write of the cache",
      res["replaces"] == N_PAIRS and len(calls) == 1, f"{len(calls)} full write(s)")
on_disk = m.load_embed_cache()
check("and the file on disk holds the winners and none of the retired",
      not any(o_ in on_disk for o_, _ in made) and all(n_ in on_disk for _, n_ in made),
      str(sorted(on_disk)))

# (c) the write path keeps its single retirement: one note, one write, as before
d = fresh()
o, n = pair()
m.save_embed_cache({o: {"title": o, "desc": "d", "vec": [0.1]},
                    n: {"title": n, "desc": "d", "vec": [0.2]}})
ok, calls = counting_saves(lambda: m.supersede_note(m.VAULT / "Decisions" / f"{o}.md", n))
check("a lone supersede (the write path's call) still drops the stem and saves once",
      ok and len(calls) == 1 and o not in m.load_embed_cache(), f"ok={ok} writes={len(calls)}")

print("\n- a run that dies mid-queue leaves no retired note in the cache FILE -")
#: The one-write-per-run change (c929fe3) opened a window: notes moved into Superseded/ one pair
#: at a time while their vectors stayed in memory until the final write. Anything that ended the
#: loop other than an OSError - an exception, Ctrl+C, the OS killing the weekly job - left the
#: file naming notes that were no longer live. Before it, each retirement wrote at once and the
#: window was one pair. Found by the auditing session with a judge that raises on the third call.
#:
#: Read from the FILE, never through `load_embed_cache`: that is memoised by the file's signature
#: and returns the very dict the loop popped from, so after a crash in the same process it
#: reports "clean" about a file that is not. The first reading of this probe said zero ghosts on
#: both versions for exactly that reason.
import json as _json  # noqa: E402


def cache_file():
    return _json.loads(m.EMBED_CACHE.read_text(encoding="utf-8"))


def retired_stems():
    return sorted(q.stem for q in (m.VAULT / "Decisions" / "Superseded").glob("*.md"))


def dies_on(n_call):
    calls = {"n": 0}

    def judge_(old_title, old_desc, new_desc, project):
        calls["n"] += 1
        if calls["n"] == n_call:
            raise RuntimeError("the process dies here")
        return True
    return judge_


for label, pass_cache in (("standalone", False), ("with the consolidator's cache", True)):
    d = fresh()
    seeded_pairs(N_PAIRS)
    kw = {"cache": m.load_embed_cache()} if pass_cache else {}
    died = None
    try:
        cm.adjudicate_contested(apply=True, has_llm=True, judge=dies_on(3), **kw)
    except RuntimeError as e:
        died = e
    gone = retired_stems()
    ghosts = [s for s in gone if s in cache_file()]
    check(f"{label}: the run really died after retiring two notes",
          died is not None and len(gone) == 2, f"died={died!r} retired={gone}")
    check(f"{label}: and none of them is still in the cache file", ghosts == [], str(ghosts))

print("\n- a ghost a killed run left behind is cleared by the next run -")
#: A killed process runs no `except` and no `finally`, so the write above cannot cover it. What
#: covers it is the next run: a vector whose note sits in Superseded/ and is not live anywhere is
#: dropped before judging. Recall already refuses such a note at delivery (one stat a hit), so a
#: ghost is never served - this keeps it from occupying the cache until a full rebuild.
d = fresh()
made = seeded_pairs(N_PAIRS)
ghost_old, ghost_new = made[0]
m.supersede_note(m.VAULT / "Decisions" / f"{ghost_old}.md", ghost_new)
planted = cache_file()
planted[ghost_old] = {"title": ghost_old, "desc": "d", "vec": [0.1, 0.2]}   # what a kill leaves
m.EMBED_CACHE.write_text(_json.dumps(planted), encoding="utf-8")
check("the ghost is planted: its note is retired and its vector is in the file",
      ghost_old in retired_stems() and ghost_old in cache_file())
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("the next run clears it from the file", ghost_old not in cache_file(), str(res))
check("and says how many it cleared", res.get("healed") == 1, str(res.get("healed")))

#: A basename can exist live AND in Superseded/ at once (the 2026-09 review found five). The
#: live one's vector must survive: the rule is "retired and not live", never "has a copy in
#: Superseded/".
d = fresh()
made = seeded_pairs(N_PAIRS)
twin_old, twin_new = made[0]
sup = m.VAULT / "Decisions" / "Superseded"
sup.mkdir(parents=True, exist_ok=True)
(sup / f"{twin_old}.md").write_text((m.VAULT / "Decisions" / f"{twin_old}.md").read_text(
    encoding="utf-8"), encoding="utf-8")                  # a retired copy beside the live note
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(False))
check("a note live in its folder keeps its vector even with a copy in Superseded/",
      twin_old in cache_file() and not res.get("healed"), f"healed={res.get('healed')}")

print("\n- a ghost is cleared on every path, not only when there is something to judge -")
#: The first placement of the heal sat after the three early exits, and this suite tested it with
#: pairs in the queue and a backend up - past all three exits, exactly where healing reached
#: anyway. The same shape as the first race test: the condition of the experiment coincided with
#: the condition under which the thing tested already worked. Found by the auditing session.


def plant_ghost_only(keep_a_pair):
    """A retired note whose vector a killed run left in the FILE; optionally one live pair."""
    made = seeded_pairs(2 if keep_a_pair else 1)
    g_old, g_new = made[0]
    m.supersede_note(m.VAULT / "Decisions" / f"{g_old}.md", g_new)
    #: with one pair only, retiring its earlier note empties the queue - nothing is left to judge
    planted = cache_file()
    planted[g_old] = {"title": g_old, "desc": "d", "vec": [0.1, 0.2]}
    m.EMBED_CACHE.write_text(_json.dumps(planted), encoding="utf-8")
    return g_old


for label, kw, keep_pair in (
        ("nothing to judge", {"has_llm": True}, False),
        ("no LLM backend", {"has_llm": False}, True),
        ("a judge budget of 0", {"has_llm": True, "budget": 0}, True)):
    d = fresh()
    ghost = plant_ghost_only(keep_pair)
    res = cm.adjudicate_contested(apply=True, judge=judge(True), **kw)
    check(f"{label}: the ghost leaves the cache file", ghost not in cache_file(),
          f"skipped={res.get('skipped')!r}")
    check(f"{label}: and the report says healed=1", res.get("healed") == 1, str(res.get("healed")))

d = fresh()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a clean early exit reports healed=0, not nothing", res.get("healed") == 0,
      f"healed={res.get('healed')!r}")

print("\n- a disputed pair goes back on the queue when its content changes, and only then -")
#: F14's second half, recorded as a follow-up in K8 and never built. A pair the judge said
#: `replaces` and the guard vetoed is stamped `disputed` and leaves the queue - so that the
#: judge is not paid again for the same answer on the same text. But nothing ever put it back:
#: a note corrected later (the value the session really said now in its facts) stayed disputed
#: forever, and off limits to the near-duplicate merge forever. The dispute now remembers what
#: it was about - a hash of both statements - and a run re-queues the pair when either changed.
VETO_OLD = f"The upload size limit is 25 MB.{F}the upload size limit is 25 MB"
VETO_NEW = f"The system enforces an upload size limit of 100 MB.{F}the request rate limit is 100 per minute"
FIXED_NEW = f"The upload size limit is 100 MB.{F}the upload size limit is 100 MB"


def rewrite_desc(stem, old_text, new_text):
    fp = m.VAULT / "Decisions" / f"{stem}.md"
    body = fp.read_text(encoding="utf-8")
    assert old_text in body, "the fixture's description is not where the test expects it"
    fp.write_text(body.replace(old_text, new_text), encoding="utf-8")


d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("the pair is vetoed and disputed (the fixture works)", res["vetoed"] == 1
      and fm(o).get("disputed") == [n], str(res))
check("the dispute records what it was about: a hash of both statements, keyed by the newer stem",
      isinstance(fm(o).get("disputed_at"), dict) and n in fm(o)["disputed_at"],
      str(fm(o).get("disputed_at")))

SEEN.clear()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("unchanged text: no call, still disputed - the reason the stamp exists",
      res["judged"] == 0 and not SEEN and fm(o).get("disputed") == [n]
      and res.get("requeued") == 0, str(res))

rewrite_desc(n, VETO_NEW, FIXED_NEW)          # the newer note now carries the value, verified
plan = cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(True))
check("a dry run names the pair it would put back, and writes nothing",
      plan.get("requeued") == 1 and fm(o).get("disputed") == [n], str(plan))
SEEN.clear()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("changed text: the pair is back on the queue and judged again in the same run",
      res.get("requeued") == 1 and res["judged"] == 1 and len(SEEN) == 1, str(res))
check("and this time the proof is there, so the earlier note retires",
      res["replaces"] == 1 and (d / "Decisions" / "Superseded" / f"{o}.md").exists(), str(res))

#: A dispute stamped before this change has no hashes. It is not re-queued - nothing says its
#: text changed since the dispute, and re-judging every old dispute would spend the judge on
#: answers already given. The run records a baseline instead, and from then on a change counts.
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm._set_contested(m.VAULT / "Decisions" / f"{o}.md", [], disputed=n)   # the legacy stamp: no hashes
if "disputed_at" in fm(o):
    fp = m.VAULT / "Decisions" / f"{o}.md"
    fp.write_text("\n".join(ln for ln in fp.read_text(encoding="utf-8").split("\n")
                            if not ln.startswith("disputed_at:")), encoding="utf-8")
SEEN.clear()
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("a dispute without hashes is baselined, not re-judged",
      res["judged"] == 0 and not SEEN and res.get("baselined") == 1
      and isinstance(fm(o).get("disputed_at"), dict) and n in fm(o)["disputed_at"], str(res))
rewrite_desc(n, VETO_NEW, FIXED_NEW)
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
check("and a change after the baseline puts it back on the queue",
      res.get("requeued") == 1 and res["judged"] == 1, str(res))

#: The /code-review xhigh of 2026-09-23, findings #1 #3 #4 #11 #13 - each case below was run
#: against the code before its fix and failed there.
import io  # noqa: E402
import json  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

print("\n- #1: a locked note in a dispute does not abort the weekly run -")
#: `_requeue_changed_disputes` hashed both notes with no guard, before adjudicate's try block:
#: one PermissionError (Obsidian, AV, OneDrive holding the note) ended the whole consolidation -
#: the failure F10 was written to prevent, one step earlier in the same function.
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))          # disputed + hashed
o2, n2 = pair(title="queue depth", d1="2026-06-02", d2="2026-06-10",
              old_desc=f"The queue depth is 10.{F}the queue depth is 10",
              new_desc=f"The queue depth is 50.{F}the queue depth is 50")
rewrite_desc(n, VETO_NEW, FIXED_NEW)                                           # would re-queue
_real_fields = cm._pair_fields


def _locked(p):
    if Path(p).stem == n:
        raise PermissionError(13, "The process cannot access the file", str(p))
    return _real_fields(p)


cm._pair_fields = _locked
try:
    try:
        res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
        raised = None
    except OSError as e:
        res, raised = {}, e
finally:
    cm._pair_fields = _real_fields
check("a PermissionError while hashing a dispute does not escape the run",
      raised is None, repr(raised))
check("and the run still judges the other pair",
      res.get("judged") == 1 and res.get("replaces") == 1, str(res))
check("the locked dispute stays disputed, untouched, for the next run",
      fm(o).get("disputed") == [n], str(fm(o)))

print("\n- #3: a hand-written string list is not iterated one character at a time -")
#: The parser reads an unquoted flow list `[a, b]` or a bare `[[link]]` as ONE string. The replace
#: branch iterated `sources` and `supersedes` without isinstance, so every character became a
#: source (inflating recurrence) and a supersedes entry (written back to the winner).
d = fresh()
o, n = pair()
fp = m.VAULT / "Decisions" / f"{n}.md"
body = fp.read_text(encoding="utf-8")
lines = body.split("\n")
lines = [ln for ln in lines if not ln.startswith(("sources:", "supersedes:"))]
lines.insert(1, "sources: [sess-x1, sess-x2]")
lines.insert(1, "supersedes: [[older-note]]")
fp.write_text("\n".join(lines), encoding="utf-8")
check("the fixture reads as strings, the case the review named",
      isinstance(fm(n).get("sources"), str) and isinstance(fm(n).get("supersedes"), str),
      f"{fm(n).get('sources')!r} {fm(n).get('supersedes')!r}")
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
win = fm(n)
srcs, sups = win.get("sources") or [], win.get("supersedes") or []
check("the winner's sources are session ids, not characters",
      res.get("replaces") == 1 and not any(len(str(s)) == 1 for s in srcs)
      and {"sess-x1", "sess-x2", SA, SB} <= set(map(str, srcs)), str(srcs))
check("recurrence counts sessions, not characters", int(str(win.get("recurrence"))) <= 4,
      str(win.get("recurrence")))
check("supersedes keeps the linked stem and gains the retired one, no single characters",
      o in sups and "older-note" in sups and not any(len(str(s)) == 1 for s in sups), str(sups))

print("\n- #4 / R10: the carry goes first, so a failure on either write leaves the pair as is -")
#: The old note used to retire first and the winner's history follow; an OSError on that second
#: write was reported 'pair failed - left as is' when the old note had already left (#4). The
#: first fix parked the carry in a ledger, and the second review found three failure modes in the
#: ledger itself (R10). Now the carry is an idempotent merge written BEFORE the retirement.
d = fresh()
o, n = pair()
_real_write = m.write_atomic
win_before = (m.VAULT / "Decisions" / f"{n}.md").read_text(encoding="utf-8")


def _winner_locked(path, text, *a, **k):
    if Path(path).stem == n and Path(path).parent.name == "Decisions":
        raise PermissionError(13, "The process cannot access the file", str(path))
    return _real_write(path, text, *a, **k)


m.write_atomic = _winner_locked
try:
    res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
finally:
    m.write_atomic = _real_write
check("a carry that cannot be written is a failed pair, and it really is left as is",
      res.get("errors") == 1 and res.get("left") == 1 and fm(o).get("contested") == [n]
      and not (d / "Decisions" / "Superseded" / f"{o}.md").exists()
      and (m.VAULT / "Decisions" / f"{n}.md").read_text(encoding="utf-8") == win_before, str(res))
check("no ledger file exists any more", not list(m.VAULT.glob(".consolidate_carry*")))
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
win = fm(n)
check("the next run judges it again and carries: recurrence 2, both sessions, supersedes",
      res.get("replaces") == 1 and str(win.get("recurrence")) == "2"
      and set(win.get("sources") or []) == {SA, SB} and win.get("supersedes") == [o], str(win))

d = fresh()
o, n = pair()
win_before = (m.VAULT / "Decisions" / f"{n}.md").read_text(encoding="utf-8")
_real_sup = m.supersede_note
m.supersede_note = lambda *a, **k: False                # the retirement fails (a locked old note)
try:
    res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
finally:
    m.supersede_note = _real_sup
check("a retirement that fails undoes the carry: the winner is byte-identical, the pair contested",
      (m.VAULT / "Decisions" / f"{n}.md").read_text(encoding="utf-8") == win_before
      and fm(o).get("contested") == [n], str(res))

print("\n- #11: the dry run plans what apply would do, and the report prints it -")
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
rewrite_desc(n, VETO_NEW, FIXED_NEW)
SEEN.clear()
plan = cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(True))
check("a dry run counts the re-queued pair in its plan and judges it, as apply would",
      plan.get("requeued") == 1 and plan["pairs"] == 1 and plan["judged"] == 1, str(plan))
check("and still writes nothing", fm(o).get("disputed") == [n] and not fm(o).get("contested"))

d = fresh()
o, n = pair()
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))           # o retires
cache = m.load_embed_cache()
cache[o] = {"title": "ghost", "desc": "", "ntype": "decision", "project": "k8p", "vec": [0.1, 0.2]}
m.save_embed_cache(cache)
plan = cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(True), cache=m.load_embed_cache())
on_disk = json.loads((m.VAULT / ".embeddings_cache.json").read_text(encoding="utf-8"))
check("a dry run handed the cache reports the ghost vector apply would heal, and leaves it on disk",
      plan.get("healed") == 1 and o in (on_disk.get("entries", on_disk) if isinstance(on_disk, dict) else {}),
      str(plan.get("healed")))
check("a standalone dry run does not load a cache only to count (R13): healed is None",
      cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(True)).get("healed") is None)
buf = io.StringIO()
with redirect_stdout(buf):
    cm._run_consolidation(False, "DRY-RUN", False)
line = next((ln for ln in buf.getvalue().splitlines() if "contested pairs" in ln), "")
check("the consolidation report prints what was healed", "healed 1" in line, line)

print("\n- #13: one consolidation walks the typed notes twice, not four times -")
#: requeue, the judge's queue and the merge's exclusion set (once per key) each walked and
#: header-read every typed note - four walks where two answer the same questions.
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
_real_both = m._iter_contested_both
WALKS = [0]


def _counting_both(*a, **k):
    WALKS[0] += 1
    return _real_both(*a, **k)


m._iter_contested_both = _counting_both
try:
    with redirect_stdout(io.StringIO()):
        cm._run_consolidation(False, "DRY-RUN", False)
finally:
    m._iter_contested_both = _real_both
check("two walks a consolidation: one before judging, one for the merge's exclusion set",
      WALKS[0] == 2, f"{WALKS[0]} walks")

print("\n- second review of 2026-09-23: R1, R2, R6, R7 -")
#: R1 - the single walk (#13) read the contested row BEFORE the re-queue stamped its stem into it,
#: and the stale-stamp cleanup rewrote `contested` from that row: a re-queued dispute left both
#: lists at once whenever the judge then did not answer.
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))           # o disputed with n
cm._set_contested(m.VAULT / "Decisions" / f"{o}.md", ["2026-06-20-k8p-decision-gone"])
rewrite_desc(n, VETO_NEW, FIXED_NEW)                                           # the dispute re-queues
res = cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(None))      # the judge is silent
check("a re-queued dispute survives the stale-stamp cleanup of the same note (R1)",
      n in (fm(o).get("contested") or []) and res.get("requeued") == 1 and res.get("left") == 1,
      f"contested {fm(o).get('contested')} disputed {fm(o).get('disputed')} {res}")

#: R2 - a stem in both lists of one note was built into the queue twice and judged twice.
d = fresh()
o, n = pair(old_desc=VETO_OLD, new_desc=VETO_NEW)
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))           # o disputed with n
cm._set_contested(m.VAULT / "Decisions" / f"{o}.md", [n])                       # and contested again
rewrite_desc(n, VETO_NEW, FIXED_NEW)
SEEN.clear()
res = cm.adjudicate_contested(apply=False, has_llm=True, judge=judge(False))
check("a pair named by both stamps of one note is judged once (R2)",
      res.get("pairs") == 1 and len(SEEN) == 1, f"{res.get('pairs')} pairs, {len(SEEN)} calls")

#: R6 - the RETIRING note's hand-written sources were dropped from the carry.
d = fresh()
o, n = pair()
fp = m.VAULT / "Decisions" / f"{o}.md"
lines = [ln for ln in fp.read_text(encoding="utf-8").split("\n") if not ln.startswith("sources:")]
lines.insert(1, "sources: [sess-old1, sess-old2, sess-old3]")
fp.write_text("\n".join(lines), encoding="utf-8")
cm.adjudicate_contested(apply=True, has_llm=True, judge=judge(True))
srcs = set(map(str, fm(n).get("sources") or []))
check("the retiring note's own hand-written sources reach the winner (R6)",
      {"sess-old1", "sess-old2", "sess-old3", SA, SB} <= srcs, str(sorted(srcs)))

#: R7 / R11 - the one list reader, on every shape the review named.
for raw, want in (("[[a]], [[b]]", ["a", "b"]), ("[[a]] [[b]]", ["a", "b"]), ("[a, b]", ["a", "b"]),
                  (["[[sess]]", "sess2"], ["sess", "sess2"]), ("[[a|alias]]", ["a"]),
                  ("plain", ["plain"]), ("", []), (None, [])):
    check(f"_list_field({raw!r}) == {want} (R7)", m._list_field(raw) == want, str(m._list_field(raw)))
check("and `contested` is read through it: a hand-written flow list names two stems",
      m._contested_of({m.CONTESTED_KEY: "[a, b]"}) == ["a", "b"])

print(f"\nK8 layer 3: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
