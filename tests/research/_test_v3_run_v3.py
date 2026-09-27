#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A6 part 1: research/v3/run_v3.py - the smoke's pieces that need no child, proxy or model:

* CLI-load-guard: a smoke loads no scorer (score_v3.py, fafr_probe.py);
* CLI-smoke-id: the next <stand>-smoke-<n> from the STATUS file's STAND START lines, 1 on a new file; a line the
  writer did not write refuses - no id is guessed;
* CLI-config: the run config's keys exactly, the key file by path only (inside the secrets directory, never opened);
* CLI-s1-order: the order is the committed ls1 list, its ids hashing to its ids_sha256;
* CLI-smoke-units: the S4 smoke is the S1 order's positions 481-500, in that order, one speaker map per unit;
* CLI-unit-tokens, CLI-questions: each unit's input tokens; each question's template (S4-cat5 for an abstention
  question, never the arm's) and its text as the benchmark asks it;
* CLI-probe: the gate's probe is the hooks' 1-token body on the scheduler's port, its record {status, complete, model};
* CLI-scored-refused, CLI-stand-refused: --tag scored waits for A9; a smoke of a stand without a template is refused.

    python tests/research/_test_v3_run_v3.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RV = _load("v3_run_v3_t", ROOT / "research" / "v3" / "run_v3.py")
SL = RV.load("status_log.py", smoke=True)
SS = _load("v3_subsample_for_run_v3_t", ROOT / "research" / "v3" / "subsample.py")
H = RV.load("run_v3_hooks.py", smoke=True)
RP = RV.load("run_v3_proxy.py", smoke=True)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def err(fn) -> str:
    try:
        fn()
        return "accepted"
    except RV.CLIError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not the CLI's named refusal: the row FAILs by name
        return f"not refused by the CLI: {type(e).__name__}: {e}"


def lme_rec(i: int) -> dict:
    return {"question_id": f"q{i:03d}", "question_type": "multi-session", "question": f"what about {i}?",
            "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"],
            "haystack_sessions": [[{"role": "user", "content": f"hello {i}"}, {"role": "assistant", "content": "hi"}],
                                  [{"role": "user", "content": f"more {i}"}]]}


TMP = Path(tempfile.mkdtemp(prefix="nvt3_run_v3_"))
try:
    print("- the load guard and the smoke id -")
    check("CLI-load-guard: a smoke loads no scorer - score_v3.py and fafr_probe.py are refused by name",
          all("scorer" in err(lambda f=f: RV.load(f, smoke=True)) for f in ("score_v3.py", "fafr_probe.py")))
    stp = TMP / "STATUS"
    log = SL.StatusLog(stp, local_tz=dt.timezone.utc)
    for sid, n in (("S4-smoke-1", 1), ("S4-smoke-2", 2), ("S1-smoke-7", 7)):
        log.stand(sid, "START", model="m", changelog="unread", order=n)
        log.stand(sid, "END", model="m", changelog="unread")
    got = (RV.next_smoke_id(stp, "S4"), RV.next_smoke_id(stp, "S1"), RV.next_smoke_id(TMP / "none", "S4"))
    check("CLI-smoke-id: one past the highest of the stand's own smoke ids (S4-smoke-3, S1-smoke-8), 1 on a new file",
          got == (("S4-smoke-3", 3), ("S1-smoke-8", 8), ("S4-smoke-1", 1)), str(got))
    bad = TMP / "STATUS_bad"
    bad.write_bytes(stp.read_bytes() + b"not a line the writer writes\n")
    check("CLI-smoke-id: a line the STATUS writer did not write refuses - no smoke id is guessed",
          "did not write" in err(lambda: RV.next_smoke_id(bad, "S4")), err(lambda: RV.next_smoke_id(bad, "S4")))

    print("\n- the run config -")
    sec = TMP / "secrets"
    cfg = {"proxy_python": str(TMP / "py" / "python.exe"), "key_file": str(sec / "deepseek.env"), "git": str(TMP / "git.exe"),
           "campaign_seed": 20260928, "embed_tag": "nvt3-bge-m3-d1:latest", "ollama_url": "http://127.0.0.1:11434",
           "arms": {"bm25-floor": {"python": str(TMP / "arms314" / "python.exe"), "llm": None, "llm_transport": None,
                                   "embeds_via_ollama": False, "extra": {}}}}
    cf = TMP / "pilot.json"
    cf.write_bytes(json.dumps(cfg).encode("utf-8"))
    rc = RV.load_run_config(cf, secrets_dir=sec)
    check("CLI-config: the config read whole, its sha256 recorded - and the key file named by path only (it does not "
          "even exist here: the CLI never opens it)", rc.key_file == sec / "deepseek.env" and not rc.key_file.exists()
          and rc.sha256 == hashlib.sha256(cf.read_bytes()).hexdigest() and rc.arms["bm25-floor"].llm is None, str(rc))

    def cfg_err(**over):
        c2 = {**cfg, **over}
        f = TMP / "c2.json"
        f.write_bytes(json.dumps({k: v for k, v in c2.items() if v is not None}).encode("utf-8"))
        return err(lambda: RV.load_run_config(f, secrets_dir=sec))

    outs = {"key elsewhere": cfg_err(key_file=str(TMP / "deepseek.env")), "key relative": cfg_err(key_file="deepseek.env"),
            "no git": cfg_err(git=None), "a stray key": cfg_err(stray=1),
            "arm python relative": cfg_err(arms={"x": {**cfg["arms"]["bm25-floor"], "python": "python.exe"}}),
            "a bool seed (RVe)": cfg_err(campaign_seed=True), "no arm (RVh)": cfg_err(arms={})}
    check("CLI-config: a key file outside the secrets directory or relative, a missing or a stray key, an arm's "
          "relative python, a bool campaign seed, no arm at all - each refused by name",
          all(v != "accepted" and not v.startswith("not refused") for v in outs.values())
          and "campaign_seed is an int" in outs["a bool seed (RVe)"] and "no arm" in outs["no arm (RVh)"], str(outs))

    print("\n- the S4 smoke's units -")
    LME = [lme_rec(i) for i in range(500)]
    ORDER = [r["question_id"] for r in reversed(LME)]
    lists = TMP / "lists"
    lists.mkdir()
    (lists / "S1.json").write_bytes(json.dumps(SS.list_record("S1", ORDER, seed=SS.SEED, rule=SS.RULE)).encode("utf-8"))
    check("CLI-s1-order: the order is the committed ls1 list", RV.s1_order(lists) == ORDER)
    tampered = json.loads((lists / "S1.json").read_text(encoding="utf-8"))
    tampered["ids"] = ORDER[::-1]
    (TMP / "lists2").mkdir()
    (TMP / "lists2" / "S1.json").write_bytes(json.dumps(tampered).encode("utf-8"))
    check("CLI-s1-order: a list whose ids do not hash to its ids_sha256 is refused, and so is a missing one",
          "not the one ls1 wrote" in err(lambda: RV.s1_order(TMP / "lists2"))
          and "comes from the ls1 run only" in err(lambda: RV.s1_order(TMP / "nolists")))
    try:
        sm = RV.s4_smoke_units(LME, ORDER, stand_id="S4-smoke-3")
    except Exception as e:  # noqa: BLE001 - a refusal here FAILs the rows below by name
        print(f"  (s4_smoke_units raised {type(e).__name__}: {e})")
        sm = {"units": [], "smaps": {}, "item_texts": {}, "order_positions": []}
    want_ids = [f"smoke-{q}" for q in ORDER[480:500]]
    check("CLI-smoke-units: the S4 smoke is the S1 order's positions 481-500, in that order - never its head",
          [u.unit_id for u in sm["units"]] == want_ids and sm["order_positions"] == list(range(481, 501))
          and all(u.stand == "S4-smoke-3" for u in sm["units"]), str([u.unit_id for u in sm["units"]][:3]))
    check("CLI-smoke-units: one speaker map per unit (B-S4-SMAP), each unit's item texts its own",
          set(sm["smaps"]) == set(want_ids) and all(m == {"user": "user", "assistant": "assistant"}
                                                     for m in sm["smaps"].values())
          and sm["item_texts"].get(want_ids[0]) == ["hello 19", "hi", "more 19"], str(sm["item_texts"].get(want_ids[0])))
    LDm = RV.load("loaders.py", smoke=True)
    _real_samples = LDm.s4_smoke_samples
    wrong = {}
    for label, pick in (("the head", lambda recs, order: _real_samples(recs, list(order[20:]) + list(order[:20]))),
                        ("the tail reversed", lambda recs, order: _real_samples(recs, order)[::-1])):
        LDm.s4_smoke_samples = pick
        try:
            wrong[label] = err(lambda: RV.s4_smoke_units(LME, ORDER, stand_id="S4-smoke-3"))
        finally:
            LDm.s4_smoke_samples = _real_samples
    check("CLI-smoke-units (RVg): samples that are not the order's positions 481-500, or are them out of order, are "
          "refused by name - the smoke is those positions, in that order",
          all("positions 481-500" in v for v in wrong.values()), str(wrong))
    ut = RV.unit_tokens(sm["units"], lambda s: len(s.split()))
    check("CLI-unit-tokens: each unit's input in tokens - its sessions' text as every text-API arm gets it",
          ut.get(want_ids[0]) == len("user: hello 19\nassistant: hi\nuser: more 19".split()) and set(ut) == set(want_ids),
          str(ut.get(want_ids[0])))

    print("\n- the questions -")
    LD = RV.load("loaders.py", smoke=True)
    loc = [{"sample_id": "conv-1", "conversation": {"speaker_a": "A", "speaker_b": "B", "session_1_date_time":
            "1:56 pm on 8 May, 2023", "session_1": [{"speaker": "A", "dia_id": "D1:1", "text": "hi"}]},
            "qa": [{"question": "when?", "category": 2, "answer": "x"},
                   {"question": "who?", "category": 5, "adversarial_answer": "y"}]}]
    lu = LD.locomo_units(loc, ["conv-1"], prefix=1)
    qs = RV.questions_for(lu, template="T4", template_abstain="T4c5",
                          locomo_question=lambda text, cat: f"{text}|{cat}")
    check("CLI-questions: the stand's template for each question and S4-cat5's for an abstention one; the text as the "
          "benchmark asks it, the category as the loader keeps it", qs == {("conv-1", "conv-1:q0"): ("T4", "when?|2"),
                                                                            ("conv-1", "conv-1:q1"): ("T4c5", "who?|5")},
          str(qs))

    print("\n- the gate's probe -")
    sent = []

    def fake_post(port, path, body, token, *, timeout):
        sent.append((port, path, body, token))
        return fake_post.reply

    fake_post.reply = (200, {"model": "deepseek-v4-flash", "choices": [{"message": {"content": "o"}}]})
    pr = RV.probe(fake_post, 43100, "tok-s")
    r1 = pr()
    fake_post.reply = (502, {"error": "x"})
    r2 = pr()
    check("CLI-probe: the hooks' 1-token body on the scheduler's port with its token; the record {status, complete, "
          "model} - a 502 is no complete call", r1 == {"status": 200, "complete": True, "model": "deepseek-v4-flash"}
          and r2 == {"status": 502, "complete": False, "model": None} and sent[0][:2] == (43100, H.PROBE_PATH)
          and sent[0][2] == {"model": RP.PINNED_MODEL, **H.PROBE_BODY} and sent[0][3] == "tok-s", str(sent[:1]))

    print("\n- the command line -")
    base = ["stand", "--arms", "bm25-floor", "--runs", "r1", "--config", str(cf)]
    check("CLI-scored-refused: --tag scored waits for A9, refused by name",
          "A9" in err(lambda: RV.main([*base, "--stand", "S4", "--tag", "scored"])))
    check("CLI-stand-refused: a smoke of a stand without a reader template (S1, S5, S7) is refused by name",
          all("not possible yet" in err(lambda s=s: RV.main([*base, "--stand", s, "--smoke"])) for s in ("S1", "S5", "S7")))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run_v3: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
