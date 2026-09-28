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
* CAN-one-object, CAN-decoys, CAN-proxy-flags (part 2a): one Canaries per stand - every value planted in a home is one
  the proxy scans for, the decoy env stays in parent_env, and a home's CLAUDE.md sent to a real (in-process) proxy's
  write port is refused and flagged canary;
* PF-pass, PF-refuse, PF-no-status: the preflight's chained record and per-attempt file, with the forecast;
* WC-gate, WC-cap: the stand's wall ceiling (Q26, 6 h);
* FC-bound, FC-labels, FC-estimate, FC-ratio-refuse, FC-refuse: the forecast's upper bound and its estimate;
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
    own = SS.list_record("S1", ORDER, seed=SS.SEED, rule=SS.RULE)["ids_sha256"]
    check("CLI-s1-order: the order is the committed ls1 list", RV.s1_order(lists, expected_sha256=own) == ORDER)
    check("CLI-s1-order: a consistent list that is not the verified reference is refused - by default the reference is "
          "S1_IDS_SHA256", "not the verified reference" in err(lambda: RV.s1_order(lists))
          and RV.S1_IDS_SHA256 == "73841e1dff06ffe6c2b4d6eb95691b274a5d4ed39ebe3b5c92e47f95248e2da2")
    tampered = json.loads((lists / "S1.json").read_text(encoding="utf-8"))
    tampered["ids"] = ORDER[::-1]
    (TMP / "lists2").mkdir()
    (TMP / "lists2" / "S1.json").write_bytes(json.dumps(tampered).encode("utf-8"))
    check("CLI-s1-order: a list whose ids do not hash to its ids_sha256 is refused, and so is a missing one",
          "not the one ls1 wrote" in err(lambda: RV.s1_order(TMP / "lists2", expected_sha256=own))
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

    print("\n- the stand's boundary: one Canaries object (the auditor's condition for run_smoke) -")
    import os  # noqa: E402
    import re  # noqa: E402
    import socket  # noqa: E402
    L = RV.load("launch.py", smoke=True)
    PX = _load("v3_llm_proxy_for_run_v3_t", ROOT / "research" / "_llm_proxy.py")
    BC = L.Contract(polygon_root=TMP / "bc" / "polygon", runs_root=TMP / "bc" / "polygon" / "runs" / "v3", repo_root=ROOT,
                    owner_home=TMP / "bc" / "owner", secrets_dir=TMP / "bc" / "secrets",
                    quarantine_root=TMP / "bc" / "quarantine", conservation_root=TMP / "bc" / "conservation")
    penv: dict = {"PATH": os.environ.get("PATH", "")}
    wiring = RV.boundary_canaries(L, BC, penv)
    unit_d = L.make_unit_dirs(BC, "S4-smoke-1", "r1", "bm25-floor", "u1")
    L.plant_canaries(unit_d, wiring["scheduler"]["home_canaries"])
    planted = {p.relative_to(unit_d.home).as_posix(): re.findall(r"nvt3c-[a-z_]+-[0-9a-f]{32}", p.read_text("utf-8"))
               for p in (unit_d.home / ".claude" / "CLAUDE.md", unit_d.home / ".claude" / ".credentials.json",
                         unit_d.home / ".claude.json")}
    proxy_set = set(wiring["proxy"].values())
    check("CAN-one-object: every value planted in a unit's home is one the proxy scans for, and the scheduler's env "
          "check holds the same set - one Canaries object feeds all three",
          all(len(v) == 1 and v[0] in proxy_set for v in planted.values())
          and set(wiring["scheduler"]["canaries"]) == proxy_set and len(proxy_set) == 5, str(planted))
    runs_decoy = (BC.runs_root / "CLAUDE.md").read_text("utf-8") if (BC.runs_root / "CLAUDE.md").exists() else ""
    check("CAN-decoys: the decoy env is in the parent_env mapping (never os.environ) and the runs-root decoy holds the "
          "ancestor canary - both values the proxy knows",
          penv.get(L.DECOY_ENV_NAME) in proxy_set and L.DECOY_ENV_NAME not in os.environ
          and wiring["proxy"]["ancestor"] in runs_decoy, str(sorted(penv)))
    keyf = TMP / "bc" / "deepseek.env"
    keyf.write_bytes(b"DEEPSEEK_API_KEY=nvt3-test-not-a-key-0000\n")
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()                                              # a closed upstream port: forwarding would fail, not flag
    arm_tok = L.new_token("bm25-floor") if hasattr(L, "new_token") else "nvt3-tok-bm25"
    pcfg = PX.ProxyConfig(arms=[PX.ArmConfig(arm="bm25-floor", mode="record", token=arm_tok,
                                             pinned_model="deepseek-flash")],
                          run_dir=TMP / "bc" / "proxy", upstream_host="127.0.0.1", upstream_port=dead_port,
                          upstream_tls=False, control_token="ctl-bc")
    px = PX.Proxy(pcfg, PX.read_key(keyf), canaries=wiring["proxy"], log=lambda m: None)
    wport = px.start()["arms"]["bm25-floor"]["write"]
    home_text = (unit_d.home / ".claude" / "CLAUDE.md").read_text("utf-8")    # what a child reading its home finds
    body = json.dumps({"model": "deepseek-flash", "thinking": {"type": "disabled"},
                       "messages": [{"role": "user", "content": "my notes: " + home_text}]}).encode()
    cs = socket.create_connection(("127.0.0.1", wport))
    cs.sendall((f"POST /u/r1.u1/v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {arm_tok}\r\n"
                f"Content-Type: application/json\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n").encode()
               + body)
    cs.settimeout(10)
    got = b""
    try:
        while chunk := cs.recv(65536):
            got += chunk
    except OSError:
        pass
    cs.close()
    px.stop()
    ff = TMP / "bc" / "proxy" / "flags.jsonl"
    flags = [json.loads(x) for x in ff.read_bytes().decode().splitlines()] if ff.exists() else []
    check("CAN-proxy-flags: the home's CLAUDE.md text sent to the write port is refused and flagged canary - P0h's "
          "canary_hits > 0 in the proxy's counters", not got.startswith(b"HTTP/1.1 200")
          and [f["kind"] for f in flags] == ["canary"] and px.counters["bm25-floor"].canary_hits >= 1,
          f"{got[:40]!r} {flags} {vars(px.counters['bm25-floor']).get('canary_hits')}")

    print("\n- the preflight (Q-A6-1 O-a) and the wall ceiling (Q26) -")
    PC = L.Contract(polygon_root=TMP / "pf" / "polygon", runs_root=TMP / "pf" / "polygon" / "runs" / "v3",
                    repo_root=ROOT, owner_home=TMP / "pf" / "owner", secrets_dir=TMP / "pf" / "secrets",
                    quarantine_root=TMP / "pf" / "quarantine", conservation_root=TMP / "pf" / "conservation")
    two = {"bm25-floor": RV.ArmRun(python=Path(sys.executable), llm=None, llm_transport=None, embeds_via_ollama=True),
           "nevertwice": RV.ArmRun(python=Path(sys.executable), llm="deepseek-flash", llm_transport="cloud:deepseek",
                                   embeds_via_ollama=True)}

    def decl_ok(py, *, arm):
        return {"python": str(py), "arm": arm, "version": "3.14.0"}

    def decl_refuse(py, *, arm):
        if arm == "nevertwice":
            raise ValueError("nevertwice: its python is not the declared interpreter")
        return decl_ok(py, arm=arm)

    fc = {"note": "upper bound, not pilot medians", "usd": 1.5}
    ok_rec = RV.preflight(PC, L, two, stand_id="S4-smoke-1", config_sha256="c" * 64, decl=decl_ok,
                          now=lambda: "2026-09-28T00:00:00Z", forecast=fc)
    ref = err(lambda: RV.preflight(PC, L, two, stand_id="S4-smoke-1", config_sha256="c" * 64, decl=decl_refuse,
                                   now=lambda: "2026-09-28T00:01:00Z", forecast=fc))
    plog = PC.runs_root / "_launch" / "preflight.jsonl"
    plines = [json.loads(x) for x in plog.read_bytes().decode().splitlines()] if plog.exists() else []
    pfiles = sorted(p.name for p in (PC.runs_root / "_launch" / "preflight").glob("*.json"))
    check("PF-pass: every arm declared, the record ok with each declaration and the forecast, chained in "
          "preflight.jsonl and written whole to its own file", ok_rec["ok"] and set(ok_rec["decl"]) == set(two)
          and ok_rec["forecast"] == fc and plines[:1] and plines[0]["ok"] is True
          and pfiles[:1] == ["00001-S4-smoke-1.json"], f"{pfiles} {plines[:1]}")
    check("PF-refuse: a refused arm stops the stand by name (CLIError, exit 2) - the attempt is still chained and "
          "written, ok false with the refusal, and the log's chain holds", "refused ['nevertwice']" in ref
          and len(plines) == 2 and plines[1]["ok"] is False and "nevertwice" in plines[1]["refused"]
          and pfiles == ["00001-S4-smoke-1.json", "00002-S4-smoke-1.json"] and L.verify_chain(plog), f"{ref} {pfiles}")
    check("PF-no-status: the preflight writes no STATUS line - a refusal leaves the smoke id unspent",
          not any("STATUS" in p.name for p in PC.runs_root.rglob("*")) and not (TMP / "pf" / "STATUS").exists())

    class Inner:
        def __init__(self, admit):
            self.admit, self.asked, self.started = admit, 0, False

        def admits_new_unit(self):
            self.asked += 1
            return self.admit

        def start(self):
            self.started = True

    clock = [100.0]
    inner_no, inner_yes = Inner(False), Inner(True)
    g_no = RV.WallCapGate(inner_no, deadline=200.0, monotonic=lambda: clock[0], cap_h=RV.SMOKE_WALL_CAP_H)
    g_yes = RV.WallCapGate(inner_yes, deadline=200.0, monotonic=lambda: clock[0], cap_h=RV.SMOKE_WALL_CAP_H)
    before = (g_no.admits_new_unit(), g_yes.admits_new_unit())
    g_yes.start()
    clock[0] = 200.0
    after = err(lambda: g_yes.admits_new_unit())
    check("WC-gate: before the deadline the incident gate decides (its refusal and its admission pass through, as do "
          "its other methods); at the deadline admits_new_unit raises the ceiling by name and the gate is tripped",
          before == (False, True) and inner_yes.started and "wall ceiling of 6.0 h" in after and g_yes.tripped
          and not g_no.tripped and inner_yes.asked == 1, f"{before} {after}")
    print("\n- the forecast (Q-A6-2): an upper bound, not pilot medians -")
    fc_out = RV.forecast({"bm25-floor": None, "nevertwice": "deepseek-flash"},
                         {"u1": ["a" * 100, "é" * 20000, "x" * 60000]}, {("u1", "q0"): "p" * 50},
                         runs=2, max_token_bytes=128)
    nw, bm = fc_out["per_arm"].get("nevertwice", {}), fc_out["per_arm"].get("bm25-floor", {})
    check("FC-bound: nevertwice's writer <= 3 requests per session op, each <= 13,000 fixed bytes + the session's UTF-8 "
          "bytes capped at 4 x 12,000, out 4,096; the reader <= 2 requests per question, in <= 2 x (prompt bytes + "
          "7,000 x the longest token's bytes) + 1,024, out 2 x 1,024; x 2 runs; priced at the peak cache-miss and "
          "output rates", nw == {"writer_requests": 18, "writer_in_tokens": 762600, "writer_out_tokens": 73728,
                                 "reader_requests": 4, "reader_in_tokens": 3586248, "reader_out_tokens": 4096,
                                 "usd": 1.398}
          and bm == {"writer_requests": 0, "writer_in_tokens": 0, "writer_out_tokens": 0, "reader_requests": 4,
                     "reader_in_tokens": 3586248, "reader_out_tokens": 4096, "usd": 1.0808}
          and fc_out["usd_total"] == 2.4788, json.dumps(fc_out["per_arm"]))
    check("FC-labels: the record says 'upper bound, not pilot medians', carries the formula, the price as data (URL, "
          "date, peak and off-peak numbers) and no hours - only the wall ceiling",
          fc_out["note"] == "upper bound, not pilot medians" and fc_out["formula"] == RV.FORECAST_FORMULA
          and fc_out["price"]["url"].startswith("https://api-docs.deepseek.com/")
          and fc_out["price"]["read"] == "2026-09-28" and fc_out["price"]["input_cache_miss"] == {"off_peak": 0.15,
                                                                                                 "peak": 0.3}
          and fc_out["price"]["output"] == {"off_peak": 0.6, "peak": 1.2}
          and fc_out["hours"].startswith("not forecast") and "6.0 h" in fc_out["hours"], fc_out["hours"])
    meas = RV.bytes_per_cl100k_token(["abcde" * 10, "é" * 5], lambda s: len(s.encode("utf-8")) // 5)
    fc_est = RV.forecast({"bm25-floor": None, "nevertwice": "deepseek-flash"},
                         {"u1": ["a" * 100, "é" * 20000, "x" * 60000]}, {("u1", "q0"): "p" * 50},
                         runs=2, max_token_bytes=128, measured={**meas, "source": "test texts"})
    est = fc_est.get("estimate") or {}
    check("FC-estimate (Q-A6-3): beside the bound, an estimate labelled 'estimate, not a bound' - the reader's context "
          "at the measured bytes per cl100k token (60 bytes / 12 tokens = 5.0, its source recorded); the bound itself "
          "unchanged", meas == {"ratio": 5.0, "bytes": 60, "tokens": 12} and est.get("note") == "estimate, not a bound"
          and est.get("bytes_per_cl100k_token") == 5.0 and est.get("measured") == {"bytes": 60, "tokens": 12,
                                                                                   "source": "test texts"}
          and est.get("per_arm", {}).get("bm25-floor", {}).get("reader_in_tokens") == 142248
          and est.get("per_arm", {}).get("nevertwice", {}).get("usd") == 0.3648 and est.get("usd_total") == 0.4124
          and fc_est["per_arm"] == fc_out["per_arm"] and fc_est["usd_total"] == 2.4788 and fc_out["estimate"] is None,
          json.dumps(est)[:300])
    check("FC-ratio-refuse: no cl100k token in the texts - no ratio is guessed",
          "no ratio is guessed" in err(lambda: RV.bytes_per_cl100k_token(["", ""], lambda s: 0)))
    check("FC-refuse: a writer without a bound in WRITER_BOUNDS, or a writer on another model, stops the forecast - "
          "no forecast, no smoke", "no upper bound" in err(lambda: RV.forecast({"mem0": "deepseek-flash"}, {}, {}, runs=1,
                                                                                max_token_bytes=128))
          and "the price does not apply" in err(lambda: RV.forecast({"nevertwice": "gpt-4o"}, {}, {}, runs=1,
                                                                    max_token_bytes=128)))
    SCH = RV.load("scheduler.py", smoke=True)
    check("WC-cap: the smoke's wall ceiling is Q26's 6 h; with one unit ceiling (scheduler.DEBUG_CEILING_S) the worst "
          "case is 12 h", RV.SMOKE_WALL_CAP_H == 6.0 and RV.SMOKE_WALL_CAP_H + SCH.DEBUG_CEILING_S / 3600 == 12.0)

    print("\n- part 2b's seams: what only main() names -")
    import ast  # noqa: E402
    SRC = (ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)
    fns = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}

    outside_main = [n for n in TREE.body if not (isinstance(n, ast.FunctionDef) and n.name == "main")]
    loop_outside = [ast.unparse(x)[:60] for n in outside_main for x in ast.walk(n)
                    if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value in (".loop", "campaign-v3-log")]
    env_outside = [ast.unparse(x)[:60] for n in outside_main for x in ast.walk(n)
                   if isinstance(x, ast.Attribute) and ast.unparse(x) == "os.environ"]
    check("CLI-status-injected, CLI-environ-injected: the STATUS path (.loop/campaign-v3-log) and os.environ are named "
          "in main() alone - run_smoke takes both from its deps, so a test never reaches the owner's STATUS or env",
          "main" in fns and loop_outside == [] and env_outside == []
          and any(isinstance(x, ast.Constant) and x.value == ".loop" for x in ast.walk(fns["main"]))
          and "os.environ" in ast.unparse(fns["main"]),
          f"{loop_outside} {env_outside}")
    reads_key = [ast.unparse(x)[:80] for x in ast.walk(TREE) if isinstance(x, ast.Call)
                 and (ast.unparse(x.func) in ("open", "read_key") or ast.unparse(x.func).endswith((".read_text",
                                                                                                  ".read_bytes", ".open")))
                 and "key_file" in ast.unparse(x)]
    check("CLI-key-by-path: nothing in run_v3 opens or reads the key file - it is handed to the proxy by path",
          reads_key == [] and "key_file=cfg.key_file" in ast.unparse(fns["run_smoke"]), str(reads_key))

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
