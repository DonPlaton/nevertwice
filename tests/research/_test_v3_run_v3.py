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
    (TMP / "cfg_dir.json").mkdir()
    unread = {"missing": err(lambda: RV.load_run_config(TMP / "no_such_config.json", secrets_dir=sec)),
              "a directory": err(lambda: RV.load_run_config(TMP / "cfg_dir.json", secrets_dir=sec))}
    check("CLI-config (B-RV-CFG): a missing or unreadable run config is the CLI's own refusal - 'no run config', by path "
          "- never a raw OSError", all("no run config" in v and not v.startswith("not refused") for v in unread.values()),
          str(unread))

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
    lq = getattr(RV, "lme_questions_for", None)
    u_d = LD._lme_unit("S1", dict(lme_rec(1), haystack_session_ids=["s1", "s2"], question_date="2023/05/30 (Tue) 23:40"))
    u_n = LD._lme_unit("S1", dict(lme_rec(2), haystack_session_ids=["s1", "s2"]))
    got_d = lq([u_d], template="T1") if lq else None
    e_n = err(lambda: lq([u_n], template="T1")) if lq else "no lme_questions_for"
    check("B1-5 (the auditor, 2026-09-30): S1/S3's questions carry their question_date as the third element - the "
          "template's {question_date} (the vendor's 'Current Date'); a question without one is refused by name",
          got_d == {("q001", "q001"): ("T1", "what about 1?", {"question_date": "2023/05/30 (Tue) 23:40"})}
          and "question_date" in e_n and "q002" in e_n and not e_n.startswith(("accepted", "not refused")), f"{got_d} | {e_n}")

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

    class InnerHalted(Inner):
        def halt_kind(self):
            return "402"

    g_h = RV.WallCapGate(InnerHalted(True), deadline=200.0, monotonic=lambda: 100.0, cap_h=RV.SMOKE_WALL_CAP_H)
    try:
        hk = (g_h.halt_kind(), g_yes.halt_kind())
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        hk = f"{type(e).__name__}: {e}"
    check("Q2: WallCapGate.halt_kind is its incident gate's (402), None for a gate without one - and the tripped wall "
          "ceiling is no halt", hk == ("402", None), str(hk))

    print("\n- Q3: the smoke's verdict - its witnesses, the stand's partial, one harness catcher -")
    ok_check = {"check_id": "S4-smoke-9.b01", "complete": True, "native": {"hits": 0, "complete": True},
                "containers": [], "fs": {"fs_hits": 0, "changed_labels": []}}
    try:
        wp = {"clean": RV.witness_problems(ok_check),
              "incomplete": RV.witness_problems({**ok_check, "complete": False}),
              "native": RV.witness_problems({**ok_check, "native": {"hits": 1, "complete": True}}),
              "container": RV.witness_problems({**ok_check, "containers": [{"hits": 3, "complete": True}]}),
              "fs": RV.witness_problems({**ok_check, "fs": {"fs_hits": 2, "changed_labels": ["watched"]}}),
              "none": RV.witness_problems(None)}
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        wp = {"error": f"{type(e).__name__}: {e}"}
    check("Q3: the witness checks are the smoke's verdict - a clean check says nothing; an incomplete one is not measured "
          "(never 0); a native, a container egress and an fs hit are P0h by name; a block with no check record is named",
          wp.get("clean") == [] and len(wp.get("incomplete") or []) == 2
          and all("incomplete" in x for x in wp.get("incomplete") or [])
          and wp.get("native") == ["witness: S4-smoke-9.b01: egress hits=1 (P0h)"]
          and wp.get("container") == ["witness: S4-smoke-9.b01: egress hits=3 (P0h)"]
          and wp.get("fs") == ["witness: S4-smoke-9.b01: fs hits=2 (P0h) ['watched']"]
          and len(wp.get("none") or []) == 1 and "no check record" in wp["none"][0], str(wp))

    class Partial(Exception):
        partial = {"stand": "S4-smoke-9", "blocks": [{}]}

    class Resulting(Exception):
        result = {"stand": "not the scheduler's"}

    try:
        sr = (RV.stand_result(Partial("x"), None), RV.stand_result(Resulting("y"), None),
              RV.stand_result(Partial("z"), {"stand": "kept"}))
    except Exception as e:  # noqa: BLE001
        sr = f"{type(e).__name__}: {e}"
    check("Q3: a failed stand's result is the scheduler's partial - never a result attribute the scheduler does not set; "
          "a result already in hand stays", sr == ({"stand": "S4-smoke-9", "blocks": [{}]}, None, {"stand": "kept"}),
          str(sr))
    try:
        bp = RV.boundary_problems({"a1/r1": {"canary_hits": 0, "owner_marker_hits": 0, "ollama_refused": 2},
                                   "a2/r1": {"canary_hits": 1, "owner_marker_hits": 0, "ollama_refused": 0}},
                                  {"a2": {"canary": 1}, "a3": {"tool_violation": 2}}, ["r1"])
    except Exception as e:  # noqa: BLE001
        bp = [f"{type(e).__name__}: {e}"]
    check("Q3, B-OLM-VIS: the smoke's boundary problems - the leg's refusals are P0h by name, a canary is one event with "
          "its flag, any other flag stands alone", any("a1/r1 ollama_refused=2" in x for x in bp)
          and sum("canary" in x for x in bp) == 1 and any(x.startswith("flag: tool_violation a3 x2") for x in bp)
          and len(bp) == 3, str(bp))
    check("Q3: an arm's own egress count in a smoke reads 'unmeasured: one harness catcher (Q-12-1)' - never {} (every "
          "child speaks to the harness's one catcher)", RV.EGRESS_UNMEASURED == "unmeasured: one harness catcher (Q-12-1)")
    tree_dirty = {**ok_check, "check_id": "S4-smoke-9.tree-start", "fs": {"fs_hits": 1, "changed_labels": ["watched"]}}
    try:
        sc = RV.smoke_checks({"tree_start": {"clean": True, "witness_check": tree_dirty}, "blocks": [{"check": ok_check}],
                              "tree_end": {"clean": True, "witness_check": {**ok_check,
                                                                            "check_id": "S4-smoke-9.tree-end"}}})
        sc_probs = [p for _w, ch in sc for p in RV.witness_problems(ch)]
        sc_part = RV.smoke_checks({"tree_start": {"clean": True, "witness_check": ok_check}, "blocks": []})
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        sc, sc_probs, sc_part = f"{type(e).__name__}: {e}", [], None
    check("Q3 (the auditor): the tree checks' witnesses are the smoke's too - tree-start, each block, tree-end, labeled "
          "by window; an fs hit in the STAND START window is a problem by name; a window never reached is left out",
          isinstance(sc, list) and [w for w, _c in sc] == ["tree-start", "block", "tree-end"]
          and sc_probs == ["witness: S4-smoke-9.tree-start: fs hits=1 (P0h) ['watched']"]
          and [w for w, _c in sc_part or []] == ["tree-start"], f"{sc} {sc_probs} {sc_part}")
    try:
        sc_none = RV.smoke_checks({"tree_start": {"clean": True}, "blocks": [{"order": 1}]})
        sc_none_probs = [p for _w, ch in sc_none for p in RV.witness_problems(ch)]
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        sc_none, sc_none_probs = f"{type(e).__name__}: {e}", []
    check("Q3 (the auditor's T5): a window the stand reached without a check record keeps None - a block with no "
          "'check' and a tree check with no 'witness_check' are both listed, and witness_problems names both as not "
          "measured (never silently dropped)", sc_none == [("tree-start", None), ("block", None)]
          and len(sc_none_probs) == 2 and all("no check record" in p for p in sc_none_probs), f"{sc_none} {sc_none_probs}")
    print("\n- the forecast (Q-A6-2): an upper bound, not pilot medians -")
    fc_out = RV.forecast_arms({"bm25-floor": None, "nevertwice": "deepseek-flash"},
                              {"nevertwice": {"u1": ["a" * 100, "é" * 20000, "x" * 60000]}}, {("u1", "q0"): "p" * 50},
                              runs=2, max_token_bytes=128, bounds=RV.WRITER_BOUNDS)
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
    try:
        fc_est = RV.forecast_arms({"bm25-floor": None, "nevertwice": "deepseek-flash"},
                                  {"nevertwice": {"u1": ["a" * 100, "é" * 20000, "x" * 60000]}}, {("u1", "q0"): "p" * 50},
                                  runs=2, max_token_bytes=128, bounds=RV.WRITER_BOUNDS,
                                  measured={**meas, "source": "test texts"}, measured_ops={"nevertwice": meas})
    except Exception as e:  # noqa: BLE001 - the row below FAILs by name
        print(f"  (forecast with an estimate raised {type(e).__name__}: {e})")
        fc_est = {"per_arm": None, "usd_total": None}
    est = fc_est.get("estimate") or {}
    check("FC-estimate (Q-A6-3, [A-EST-1]): beside the bound, an estimate labelled 'estimate, not a bound' - the "
          "reader's context at the measured bytes per cl100k token (60 bytes / 12 tokens = 5.0, its source recorded); the "
          "writer at 1 request per op, its in-bytes per op over the arm's measured ratio (the session texts here: "
          "2 x 127,100 / 5.0 = 50,840) and 200 output tokens per op; the bound itself unchanged",
          meas == {"ratio": 5.0, "bytes": 60, "tokens": 12} and est.get("note") == "estimate, not a bound"
          and est.get("bytes_per_cl100k_token") == 5.0 and est.get("measured") == {"bytes": 60, "tokens": 12,
                                                                                   "source": "test texts"}
          and est.get("per_arm", {}).get("bm25-floor", {}).get("reader_in_tokens") == 142248
          and {k: est.get("per_arm", {}).get("nevertwice", {}).get(k) for k in ("writer_requests", "writer_in_tokens",
                                                                                "writer_out_tokens", "usd")}
          == {"writer_requests": 6, "writer_in_tokens": 50840, "writer_out_tokens": 1200, "usd": 0.0643}
          and est.get("usd_total") == 0.1119 and est.get("out_tokens_per_op") == 200
          and fc_est["per_arm"] == fc_out["per_arm"] and fc_est["usd_total"] == 2.4788 and fc_out["estimate"] is None,
          json.dumps(est)[:400])
    check("FC-ratio-refuse: no cl100k token in the texts - no ratio is guessed",
          "no ratio is guessed" in err(lambda: RV.bytes_per_cl100k_token(["", ""], lambda s: 0)))
    check("FC-refuse: a writer without a bound in WRITER_BOUNDS, or a writer on another model, stops the forecast - "
          "no forecast, no smoke",
          "no upper bound" in err(lambda: RV.forecast_arms({"mem0": "deepseek-flash"}, {}, {}, runs=1, max_token_bytes=128,
                                                           bounds=RV.WRITER_BOUNDS))
          and "the price does not apply" in err(lambda: RV.forecast_arms({"nevertwice": "gpt-4o"}, {}, {}, runs=1,
                                                                         max_token_bytes=128, bounds=RV.WRITER_BOUNDS)))

    print("\n- C2 (C6, Q-C6-4..7, [A-M0-1], [A-EST-1]): each writer arm's ops, mem0's bound from its probe, one estimate -")
    import ast  # noqa: E402 - the adapter's write, read as data
    PL = RV.load("run_v3_plan.py", smoke=True)
    AM = _load("v3_arm_mem0_for_run_v3_t", ROOT / "research" / "v3" / "arms" / "arm_mem0.py")
    PA = RV.load("probe_a8.py", smoke=True)
    C2_RAISED: list = []

    def c2ok(fn) -> bool:
        try:
            return bool(fn())
        except Exception as e:  # noqa: BLE001 - the row reads it
            C2_RAISED.append(f"{type(e).__name__}: {e}")
            return False

    def c2val(fn, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - the rows that read it FAIL by name
            C2_RAISED.append(f"{type(e).__name__}: {e}")
            return default

    def raises(fn, exc) -> bool:
        try:
            fn()
        except exc:
            return True
        return False

    U = sm["units"]
    ot_nv = c2val(lambda: RV.op_texts_for("nevertwice", U, dated=True, smaps=sm["smaps"]), {})
    ot_m0 = c2val(lambda: RV.op_texts_for("mem0", U, dated=True, smaps=sm["smaps"]), {})
    ops_m0 = c2val(lambda: PL.write_ops(PL.ARMS["mem0"], U[0], dated=True, smap=sm["smaps"][U[0].unit_id]), [])
    check("C2 FC-ops (Q-C6-4): each writer arm's op texts, per unit, are the scheduler's own write ops - one text per op: "
          "nevertwice a session's text, mem0 one message framed as its adapter frames it",
          c2ok(lambda: set(ot_nv) == set(want_ids) == set(ot_m0)
               and all(len(ot_nv[u.unit_id]) == len(PL.write_ops(PL.ARMS["nevertwice"], u, dated=True,
                                                                 smap=sm["smaps"][u.unit_id]))
                       and len(ot_m0[u.unit_id]) == len(PL.write_ops(PL.ARMS["mem0"], u, dated=True,
                                                                     smap=sm["smaps"][u.unit_id])) for u in U)
               and ot_nv[want_ids[0]] == [PL.session_text(s) for s in U[0].sessions]
               and len(ot_m0[want_ids[0]]) == 3 and ops_m0[0]["date"][:10] == "2023-05-20"
               and ot_m0[want_ids[0]][0] == "Conversation from 2023-05-20:\nuser: hello 19"
               and ot_m0[want_ids[0]][2] == "Conversation from 2023-05-21:\nuser: more 19"),
          str({k: (v or {}).get(want_ids[0]) for k, v in (("nevertwice", ot_nv), ("mem0", ot_m0))})[:400])
    frame_items = [({"item_id": "i1", "role": "user", "speaker": "Ana", "text": "héllo ☕"}, "2023-05-20T02:21:00", True),
                   ({"item_id": "i2", "role": "assistant", "speaker": "assistant", "text": "x"}, None, False)]
    check("C2 FC-frame (F-C6-1): mem0's op text is its adapter's own content(), byte for byte - dated and undated, "
          "non-ASCII too - so the bytes priced are the bytes the adapter sends and reports by text_sha256",
          c2ok(lambda: all(RV.op_text("mem0", {"item": it, "date": d}, dated=dd) == AM.content(it, d, dd)
                           for it, d, dd in frame_items)
               and all(RV.op_text("mem0", op, dated=True) == AM.content(op["item"], op["date"], True) for op in ops_m0)
               and AM.content(*frame_items[0]) == "Conversation from 2023-05-20:\nAna: héllo ☕"
               and AM.content(*frame_items[1]) == "assistant: x"))
    AMT = ast.parse((ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8"))
    _writes = [n for n in ast.walk(AMT) if isinstance(n, ast.FunctionDef) and n.name == "write"]
    _texts = [ast.unparse(n) for w in _writes for n in ast.walk(w) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "text" for t in n.targets)]
    _adds = [ast.unparse(n) for w in _writes for n in ast.walk(w) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "self.mem.add" and "infer=True" in ast.unparse(n)]
    check("C2 FC-frame: the adapter's write hands mem0's add content()'s text and no other - the text priced is the text "
          "sent", _texts == ["text = content(item, date, self.spec['dated'])"]
          and _adds == ["self.mem.add([{'role': item['role'], 'content': text}], user_id=self.unit, infer=True)"],
          str((_texts, _adds))[:300])
    check("C2 FC-frame: a dated op without its date is refused on both sides - never framed without its header",
          "date" in err(lambda: RV.op_text("mem0", {"item": frame_items[0][0], "date": None}, dated=True))
          and c2ok(lambda: raises(lambda: AM.content(frame_items[0][0], None, True), ValueError)))
    check("C2 FC-op-text-refuse: an arm with no declared writer op text is refused - its writer is not priced by a guess",
          "no writer op text" in err(lambda: RV.op_text("letta", {"item": {"item_id": "s1"}, "date": None}, dated=False)))
    BFK = sorted(set(PA.M0_BOUND_SOURCE) | set(PA.M0_HELPER_SHAPES) | set(PA.M0_WRITES) | set(PA.M0_DEFAULTS)
                 | set(PA.M0_CALLS) | set(PA.M0_CONFIG_DEFAULTS) | {"m0_adapter"})
    bf = {k: {"value": "x", "source": "s"} for k in BFK}
    bf.update({"m0_max_retries": {"value": "2"}, "m0_max_tokens": {"value": "2000"}, "m0_system_prompt": {"value": 1000},
               "m0_agent_suffix": {"value": 100}, "m0_last_k": {"value": "10"}, "m0_top_k": {"value": "10"},
               "m0_trunc_limit": {"value": "300"}, "m0_message_frame": {"value": ["assistant", "system", "user"]},
               "m0_user_prompt": {"value": {"parts": [{"const_bytes": 20, "fields": ["observation_date"], "conditional": False},
                                                      {"const_bytes": 30, "fields": [], "conditional": True}],
                                            "separator": "\n\n"}},
               "m0_client_ctor": {"value": [{"line": 3, "keywords": ["base_url"], "starstar": False}]},
               "m0_client_override": {"value": []}, "m0_prompt_call": {"value": sorted(PA.M0_PROMPT_CALL)}})
    REC = {"outcome": "pass", "bound_facts": bf, "bound_blocked": [],
           "fields": {"m0_calls_per_add": {"ok": True, "value": {"bound_per_add": 1, "adds": 3, "answered": 3}}}}
    wb = c2val(lambda: RV.writer_bound(REC), {})
    check("C2 FC-mem0-bound (Q-C6-5, Q-C6-6, [A-M0-1]): mem0's per-op bound from its probe record - R = 1 site x (1 + 2 "
          "SDK retries) = 3, O = 2,000, F = 1,000 + 100 + (20 + 30) + 1 separator of 2 + 2 ISO days of 10 + '[]' = 1,174, "
          "last-k = 10 x (9 + 2 + 4 x 300 + 3 + 1) = 12,150, the memories at M = 1 KiB = 2 + 10 x (23 + 1,024) + 9 x 2 = "
          "10,490, the new message's frame 9 + 2 + 1 = 12: 23,826 fixed bytes per op, no cap on the op's own bytes",
          c2ok(lambda: RV.M0_DEFAULT_M == 1024 and wb == {"requests_per_op": 3, "out_tokens": 2000, "fixed_in_bytes": 23826,
                                                          "transcript_chars": None, "m_bytes": 1024,
                                                          "terms": {"F": 1174, "last_k": 12150, "memories": 10490,
                                                                    "frame": 12, "sites": 1}}), str(wb)[:400])
    check("C2 FC-mem0-bound: M is an argument - 2 KiB makes the memories' term 2 + 10 x (23 + 2,048) + 18 = 20,730",
          c2ok(lambda: RV.writer_bound(REC, m_bytes=2048)["terms"]["memories"] == 20730))

    def rec_with(**kw):
        return {**REC, **kw}

    bf_blk = {**bf, "m0_top_k": {"value": None, "blocked": "blocked:source-changed:m0_top_k"}}
    bf_miss = {k: v for k, v in bf.items() if k != "m0_fn_dates"}
    refusals = {"a failed probe": err(lambda: RV.writer_bound(rec_with(outcome="fail"))),
                "reasons in the record": err(lambda: RV.writer_bound(rec_with(bound_blocked=["m0_top_k: blocked"]))),
                "a blocked fact the record did not list": err(lambda: RV.writer_bound(rec_with(bound_facts=bf_blk))),
                "a declared fact missing": err(lambda: RV.writer_bound(rec_with(bound_facts=bf_miss))),
                "no site count": err(lambda: RV.writer_bound(rec_with(fields={}))),
                "no record": err(lambda: RV.writer_bound(None))}
    check("C2 FC-mem0-refuse (Q-C6-5): no bound without a passed probe, with a reason in the record or found again in its "
          "facts, with a declared fact missing, or without the probe's site count - each refused by name",
          "outcome" in refusals["a failed probe"] and "m0_top_k" in refusals["reasons in the record"]
          and "m0_top_k" in refusals["a blocked fact the record did not list"]
          and "m0_fn_dates" in refusals["a declared fact missing"] and "m0_calls_per_add" in refusals["no site count"]
          and all(v != "accepted" and not v.startswith("not refused") for v in refusals.values()), str(refusals)[:600])
    sites_bad = {repr(x): err(lambda x=x: RV.writer_bound(rec_with(fields={"m0_calls_per_add": {
        "ok": True, "value": {"bound_per_add": x, "adds": 3, "answered": 3}}}))) for x in (0, True, -1, "1", None)}
    check("C2 FC-mem0-refuse (the auditor's R5): a site count of 0, True, -1, \"1\" or none is refused by name - a zero or "
          "a bool R would price mem0's writer at nothing, an underestimate by construction",
          all("m0_calls_per_add" in v and not v.startswith("not refused") for v in sites_bad.values()), str(sites_bad)[:500])
    check("C3: one forecast, per arm - the session form is gone (its R19 guard with it), and a per-arm forecast counts "
          "each writer arm's ops", c2ok(lambda: not hasattr(RV, "forecast") and fc_out["ops_per_run"] == {"nevertwice": 3}),
          str(fc_out.get("ops_per_run")))
    fa_m0 = c2val(lambda: RV.forecast_arms({"mem0": "deepseek-flash"}, {"mem0": {"u1": ["ab", "é"]}},
                                           {("u1", "q0"): "p" * 50}, runs=1, max_token_bytes=128, bounds={"mem0": wb}), {})
    check("C2 FC-arms: mem0's writer at its probe's bound - 2 ops x 3 requests, in 3 x (23,826 + 2) twice = 142,968, out "
          "6 x 2,000; its reader as every arm's; priced at the peak rates: $0.5977",
          c2ok(lambda: {k: fa_m0["per_arm"]["mem0"][k] for k in ("writer_requests", "writer_in_tokens", "writer_out_tokens",
                                                                   "reader_in_tokens", "reader_out_tokens", "usd")}
               == {"writer_requests": 6, "writer_in_tokens": 142968, "writer_out_tokens": 12000,
                   "reader_in_tokens": 1793124, "reader_out_tokens": 2048, "usd": 0.5977}
               and fa_m0["writer_bounds"]["mem0"] == wb), str(fa_m0.get("per_arm"))[:400])
    m_nv = {"ratio": 4.0, "bytes": 8, "tokens": 2, "source": "t"}
    m_m0 = {"ratio": 2.0, "bytes": 4, "tokens": 2, "source": "t"}
    fa_e = c2val(lambda: RV.forecast_arms({"nevertwice": "deepseek-flash", "mem0": "deepseek-flash"},
                                          {"nevertwice": {"u1": ["a" * 100]}, "mem0": {"u1": ["ab", "é"]}},
                                          {("u1", "q0"): "p" * 50}, runs=1, max_token_bytes=128,
                                          bounds={"nevertwice": RV.WRITER_BOUNDS["nevertwice"], "mem0": wb},
                                          measured={**meas, "source": "t"}, measured_ops={"nevertwice": m_nv, "mem0": m_m0}),
                 {})
    ee = fa_e.get("estimate") or {}
    check("C2 FC-est-one-method ([A-EST-1]): every writer arm's estimate comes from the same function with the same "
          "pre-pilot output constant (200 tokens per op) - only its bound and its own measured ratio differ",
          c2ok(lambda: RV.PRE_PILOT_OUT_TOKENS == 200 and ee["out_tokens_per_op"] == 200
               and all({k: ee["per_arm"][a][k] for k in ("writer_requests", "writer_in_tokens", "writer_out_tokens")}
                       == RV.writer_estimate(b, t, ratio=r, out_tokens=RV.PRE_PILOT_OUT_TOKENS, runs=1)
                       for a, b, t, r in (("nevertwice", RV.WRITER_BOUNDS["nevertwice"], ["a" * 100], 4.0),
                                          ("mem0", wb, ["ab", "é"], 2.0)))
               and ee["writer_ratios"] == {"nevertwice": 4.0, "mem0": 2.0}
               and RV.writer_estimate(wb, ["ab", "é"], ratio=2.0, out_tokens=200, runs=1)
               == {"writer_requests": 2, "writer_in_tokens": 23828, "writer_out_tokens": 400}), str(ee)[:500])
    check("C2 FC-est-refuse: an estimate without a writer arm's own measured ratio is refused by name - no ratio is borrowed",
          "['mem0'] - no ratio is borrowed" in err(lambda: RV.forecast_arms({"mem0": "deepseek-flash"}, {"mem0": {"u1": ["ab"]}}, {}, runs=1,
                                                  max_token_bytes=128, bounds={"mem0": wb},
                                                  measured={**meas, "source": "t"}, measured_ops={})))
    check("C2 FC-refuse: a writer arm without a bound, or without op texts, stops the per-arm forecast - each by name",
          "no upper bound" in err(lambda: RV.forecast_arms({"mem0": "deepseek-flash"}, {"mem0": {"u1": ["ab"]}}, {}, runs=1,
                                                           max_token_bytes=128, bounds={}))
          and "no op texts" in err(lambda: RV.forecast_arms({"mem0": "deepseek-flash"}, {}, {}, runs=1, max_token_bytes=128,
                                                            bounds={"mem0": wb})))
    check("C2 FORECAST_FORMULA: states mem0's per-op bound from its probe with M at 1 KiB [A-M0-1], and one estimate "
          "method for every writer arm, once [A-EST-1]",
          c2ok(lambda: "[A-M0-1]" in RV.FORECAST_FORMULA and RV.FORECAST_FORMULA.count("[A-EST-1]") == 1
               and "mem0" in RV.FORECAST_FORMULA))

    print("\n- C3: the stand's forecast, one function for the smoke and the A/B; mem0's bound by --mem0-probe-run -")

    class _Arm:
        def __init__(self, llm):
            self.llm = llm

    def words(s):
        return max(1, len(s.split()))

    ses = [PL.session_text(s) for u in U for s in u.sessions]
    nv_flat = [t for v in ot_nv.values() for t in v]
    m0_flat = [t for v in ot_m0.values() for t in v]
    sf = c2val(lambda: RV.stand_forecast({"bm25-floor": _Arm(None), "nevertwice": _Arm("deepseek-flash"),
                                          "mem0": _Arm("deepseek-flash")}, U, smaps=sm["smaps"],
                                         prompts={("u", "q"): "p" * 50}, runs=2, count=words, cl100k_source="c" * 64,
                                         max_token_bytes=128, writer_bounds={**RV.WRITER_BOUNDS, "mem0": wb},
                                         label="S4-smoke-3's 20 units"), {})
    want_sf = c2val(lambda: RV.forecast_arms({"bm25-floor": None, "nevertwice": "deepseek-flash", "mem0": "deepseek-flash"},
                                             {"nevertwice": ot_nv, "mem0": ot_m0}, {("u", "q"): "p" * 50}, runs=2,
                                             max_token_bytes=128, bounds={**RV.WRITER_BOUNDS, "mem0": wb},
                                             measured={**RV.bytes_per_cl100k_token(ses, words), "source": "s"},
                                             measured_ops={"nevertwice": RV.bytes_per_cl100k_token(nv_flat, words),
                                                           "mem0": RV.bytes_per_cl100k_token(m0_flat, words)}), {})
    src = "cl100k " + "c" * 12
    check("C3 ST-forecast: the stand's forecast is forecast_arms over each writer arm's op texts (op_texts_for, dated), "
          "its bound from writer_bounds, the reader's ratio over the units' session texts and each writer arm's over its "
          "own op texts - mem0's framed messages are not nevertwice's sessions; every ratio's source named",
          c2ok(lambda: sf["per_arm"] == want_sf["per_arm"] and sf["estimate"]["per_arm"] == want_sf["estimate"]["per_arm"]
               and sf["estimate"]["writer_ratios"]["mem0"] == RV.bytes_per_cl100k_token(m0_flat, words)["ratio"]
               != sf["estimate"]["bytes_per_cl100k_token"]
               and sf["estimate"]["measured"]["source"] == f"{src} over the {len(ses)} session texts of S4-smoke-3's 20 units"
               and sf["estimate"]["writer_measured"]["mem0"]["source"]
               == f"{src} over mem0's {len(m0_flat)} op texts of S4-smoke-3's 20 units"
               and sf["ops_per_run"] == {"mem0": len(m0_flat), "nevertwice": len(nv_flat)}), str(sf.get("estimate"))[:400])
    check("C3 ST-refuse: a writer arm without a bound in writer_bounds stops the stand's forecast by name",
          "no upper bound" in err(lambda: RV.stand_forecast({"mem0": _Arm("deepseek-flash")}, U, smaps=sm["smaps"],
                                                            prompts={}, runs=1, count=words, cl100k_source="c" * 64,
                                                            max_token_bytes=128, writer_bounds=RV.WRITER_BOUNDS, label="x")))
    PRUN = TMP / "runs_c3"
    for run_id, rec in (("p1", REC), ("p3", rec_with(outcome="fail"))):
        (PRUN / "_a8" / run_id / "mem0").mkdir(parents=True, exist_ok=True)
        (PRUN / "_a8" / run_id / "mem0" / "probe.json").write_text(json.dumps(rec), encoding="utf-8")
    P1 = PRUN / "_a8" / "p1" / "mem0" / "probe.json"
    wbf = c2val(lambda: RV.writer_bounds_for(["nevertwice", "mem0"], PRUN, mem0_probe_run="p1"), {})
    check("C3 writer_bounds_for (Q-C6-5): WRITER_BOUNDS' and mem0's from its probe record <runs>/_a8/<run>/mem0/probe.json, "
          "that file named by path and sha256; without mem0, WRITER_BOUNDS' alone",
          c2ok(lambda: wbf["nevertwice"] == RV.WRITER_BOUNDS["nevertwice"]
               and {k: v for k, v in wbf["mem0"].items() if k != "source"} == wb
               and wbf["mem0"]["source"] == f"{P1}@sha256:{hashlib.sha256(P1.read_bytes()).hexdigest()}"
               and RV.writer_bounds_for(["nevertwice"], PRUN, mem0_probe_run=None) == RV.WRITER_BOUNDS), str(wbf)[:400])
    wbf_bad = {"mem0 without a probe run": err(lambda: RV.writer_bounds_for(["mem0"], PRUN, mem0_probe_run=None)),
               "a probe run without mem0": err(lambda: RV.writer_bounds_for(["nevertwice"], PRUN, mem0_probe_run="p1")),
               "no such record": err(lambda: RV.writer_bounds_for(["mem0"], PRUN, mem0_probe_run="p2")),
               "a run id that is a path": err(lambda: RV.writer_bounds_for(["mem0"], PRUN, mem0_probe_run="../_a8/p1")),
               "a failed probe": err(lambda: RV.writer_bounds_for(["mem0"], PRUN, mem0_probe_run="p3"))}
    check("C3 writer_bounds_for: --mem0-probe-run is required iff mem0 is an arm; a missing record, a run id that is not a "
          "name and a probe that did not pass are each refused by name",
          "--mem0-probe-run" in wbf_bad["mem0 without a probe run"] and "--mem0-probe-run" in wbf_bad["a probe run without mem0"]
          and "probe.json" in wbf_bad["no such record"] and "not a run id" in wbf_bad["a run id that is a path"]
          and "outcome" in wbf_bad["a failed probe"]
          and all(v != "accepted" and not v.startswith("not refused") for v in wbf_bad.values()), str(wbf_bad)[:600])
    def cli_err(argv):
        try:
            return err(lambda: RV.main(argv))
        except SystemExit as e:                  # argparse's own exit: not the CLI's named refusal
            return f"not refused by the CLI: SystemExit {e.code}"

    cli = {"mem0 without": cli_err(["stand", "--stand", "S4", "--smoke", "--arms", "nevertwice,mem0", "--runs", "r1",
                                    "--config", str(TMP / "none.json")]),
           "probe run without mem0": cli_err(["stand", "--stand", "S4", "--smoke", "--arms", "nevertwice", "--runs", "r1",
                                              "--config", str(TMP / "none.json"), "--mem0-probe-run", "p1"])}
    check("C3 CLI (Q-C6-5): --mem0-probe-run is required iff mem0 is in --arms - refused by name before the config or "
          "anything else is read", all("--mem0-probe-run" in v for v in cli.values()), str(cli)[:400])
    cli_path = cli_err(["stand", "--stand", "S4", "--smoke", "--arms", "mem0", "--runs", "r1", "--config",
                        str(TMP / "none.json"), "--mem0-probe-run", "../_a8/p1"])
    check("C3 CLI (R-AB-CLI): a --mem0-probe-run that is a path is refused as not a run id before the config is read",
          "not a run id" in cli_path and "none.json" not in cli_path, cli_path[:300])
    print("\n- R-AB-CLI (M30): the A/B's command line takes --mem0-probe-run exactly as the stand's -")
    AB = _load("v3_ab_harness_for_run_v3_t", ROOT / "research" / "v3" / "ab_harness.py")

    def ab_err(argv):
        try:
            getattr(AB, "main")(argv)
            return "accepted"
        except SystemExit as e:
            return f"not refused by the CLI: SystemExit {e.code}"
        except Exception as e:  # noqa: BLE001 - the A/B's own run_v3 instance: its CLIError is named by class
            return f"{type(e).__name__}: {e}"

    none_cfg = str(TMP / "none.json")
    ab_cli = {"mem0 without": ab_err(["--stand", "S4", "--arms", "nevertwice,mem0", "--config", none_cfg]),
              "probe run without mem0": ab_err(["--stand", "S4", "--arms", "nevertwice", "--config", none_cfg,
                                                "--mem0-probe-run", "p1"]),
              "a path": ab_err(["--stand", "S4", "--arms", "mem0", "--config", none_cfg, "--mem0-probe-run", "..\\p1"])}
    check("R-AB-CLI: the A/B's command line refuses --mem0-probe-run without mem0, mem0 without it, and a run id that is "
          "a path - each by the CLI's own refusal, before the config is read",
          ab_cli["mem0 without"].startswith("CLIError: --mem0-probe-run")
          and ab_cli["probe run without mem0"].startswith("CLIError: --mem0-probe-run")
          and ab_cli["a path"].startswith("CLIError:") and "not a run id" in ab_cli["a path"]
          and all("none.json" not in v for v in ab_cli.values()), str(ab_cli)[:500])
    no_cfg = {"stand": cli_err(["stand", "--stand", "S4", "--smoke", "--arms", "nevertwice", "--runs", "r1", "--config",
                                none_cfg]),
              "A/B": ab_err(["--stand", "S4", "--arms", "nevertwice", "--config", none_cfg])}
    check("B-RV-CFG: both command lines refuse a missing run config by name ('no run config', the path named) - a "
          "CLIError, exit 2 through __main__, never a traceback",
          "no run config" in no_cfg["stand"] and not no_cfg["stand"].startswith("not refused")
          and no_cfg["A/B"].startswith("CLIError:") and "no run config" in no_cfg["A/B"]
          and all("none.json" in v for v in no_cfg.values()), str(no_cfg)[:400])

    class SimpleContract:
        def __init__(self, runs_root):
            self.runs_root = runs_root

    seen_deps: list = []
    _real = getattr(RV, "real_smoke_deps", None)
    RV.real_smoke_deps = lambda c_, cfg_, **kw: seen_deps.append(kw) or "deps"
    try:
        cd = c2val(lambda: RV.cli_deps(SimpleContract(PRUN), "cfg", ["nevertwice", "mem0"], "p1"))
        cd0 = c2val(lambda: RV.cli_deps(SimpleContract(PRUN), "cfg", ["nevertwice"], None))
    finally:
        RV.real_smoke_deps = _real
    check("R-AB-CLI: cli_deps builds the real deps with writer_bounds from writer_bounds_for - mem0's from its probe record "
          "when mem0 is an arm, never the default; WRITER_BOUNDS' without it",
          cd == "deps" and cd0 == "deps" and len(seen_deps) == 2
          and "source" in seen_deps[0]["writer_bounds"].get("mem0", {}) and seen_deps[1]["writer_bounds"] == RV.WRITER_BOUNDS,
          str(seen_deps)[:300])

    def main_calls(src, owner):
        tree = ast.parse(src)
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        return [ast.unparse(n) for n in sorted((n for f in fns for n in ast.walk(f) if isinstance(n, ast.Call)
                                                 and ast.unparse(n.func).split(".")[-1] in ("cli_deps", "SmokeDeps",
                                                                                            "check_mem0_probe_run",
                                                                                            "read_run_config", "default",
                                                                                            "load_run_config")),
                                                key=lambda n: (n.lineno, n.col_offset))]

    rsd = [n for n in ast.parse((ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8")).body
           if isinstance(n, ast.FunctionDef) and n.name == "real_smoke_deps"]
    rsd_kw = [{k.arg: ast.unparse(k.value) for k in x.keywords} for f in rsd for x in ast.walk(f)
              if isinstance(x, ast.Call) and ast.unparse(x.func) == "SmokeDeps"]
    check("R-AB-CLI: real_smoke_deps hands its writer_bounds argument to the one SmokeDeps it builds - never the default",
          len(rsd_kw) == 1 and rsd_kw[0].get("writer_bounds") == "writer_bounds", str(rsd_kw)[:300])
    rv_main = main_calls((ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8"), "run_v3")
    ab_main = main_calls((ROOT / "research" / "v3" / "ab_harness.py").read_text(encoding="utf-8"), "ab_harness")
    check("R-AB-CLI: both command lines check --mem0-probe-run first, read the config before the machine's contract "
          "(F4), and build their deps only through cli_deps - no SmokeDeps of their own, so mem0 is never left at the "
          "default bounds",
          rv_main == ["check_mem0_probe_run(arm_names, args.mem0_probe_run)", "read_run_config(args.config)",
                      "load('launch.py', smoke=True).Contract.default()",
                      "load_run_config(args.config, secrets_dir=c.secrets_dir, raw=raw)",
                      "cli_deps(c, cfg, arm_names, args.mem0_probe_run)"]
          and ab_main == ["RV.check_mem0_probe_run(arm_names, args.mem0_probe_run)", "RV.read_run_config(args.config)",
                          "RV.load('launch.py', smoke=True).Contract.default()",
                          "RV.load_run_config(args.config, secrets_dir=c.secrets_dir, raw=raw)",
                          "RV.cli_deps(c, cfg, arm_names, args.mem0_probe_run)"], str((rv_main, ab_main))[:600])
    import dataclasses  # noqa: E402
    fdef = {f.name: f for f in dataclasses.fields(RV.SmokeDeps)}
    check("C3 SmokeDeps.writer_bounds: WRITER_BOUNDS' by default, as a copy - a stand without mem0 needs no probe",
          c2ok(lambda: fdef["writer_bounds"].default_factory() == RV.WRITER_BOUNDS
               and fdef["writer_bounds"].default_factory() is not RV.WRITER_BOUNDS))
    ABSRC = (ROOT / "research" / "v3" / "ab_harness.py").read_text(encoding="utf-8")

    def forecast_calls(src, fn_name):
        tree = ast.parse(src)
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name]
        return [{k.arg: ast.unparse(k.value) for k in n.keywords} for f in fns for n in ast.walk(f)
                if isinstance(n, ast.Call) and ast.unparse(n.func).split(".")[-1] in ("stand_forecast", "forecast",
                                                                                     "forecast_arms")
                and ast.unparse(n.func) != "self.forecast"]

    rs_calls = forecast_calls((ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8"), "run_smoke")
    ab_calls = forecast_calls(ABSRC, "run_ab")
    check("C3: run_smoke and the A/B harness forecast through stand_forecast only, at deps.writer_bounds, dated - their "
          "children suites run it under the lock",
          len(rs_calls) == 1 and len(ab_calls) == 1
          and all(c.get("writer_bounds") == "deps.writer_bounds" and c.get("dated") == "True" for c in rs_calls + ab_calls)
          and "stand_forecast" in ABSRC and "RV.forecast(" not in ABSRC, str((rs_calls, ab_calls))[:400])
    stale = []
    for f in sorted((ROOT / "research" / "v3").rglob("*.py")):
        tree_f = c2val(lambda f=f: ast.parse(f.read_text(encoding="utf-8")))
        if tree_f is None:
            stale.append(f"{f.name}: unparsable")
            continue
        for n in ast.walk(tree_f):
            name = (n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else None
                    ) if isinstance(n, ast.Call) else n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
            if name == "forecast":
                stale.append(f"{f.relative_to(ROOT).as_posix()}:{n.lineno}")
    check("C3 (the auditor's condition): no module under research/v3 defines or calls forecast() any more - only "
          "forecast_arms and stand_forecast", stale == [] and len(list((ROOT / "research" / "v3").rglob("*.py"))) > 20,
          str(stale)[:300])
    check("C2: no row's condition raised", C2_RAISED == [], str(C2_RAISED)[:400])
    SCH = RV.load("scheduler.py", smoke=True)
    check("WC-cap: the smoke's wall ceiling is Q26's 6 h; with one unit ceiling (scheduler.DEBUG_CEILING_S) the worst "
          "case is 12 h", RV.SMOKE_WALL_CAP_H == 6.0 and RV.SMOKE_WALL_CAP_H + SCH.DEBUG_CEILING_S / 3600 == 12.0)

    print("\n- part 2b's seams: what only the real deps name -")
    import ast  # noqa: E402
    SRC = (ROOT / "research" / "v3" / "run_v3.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)
    fns = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}

    outside_main = [n for n in TREE.body if not (isinstance(n, ast.FunctionDef) and n.name == "real_smoke_deps")]
    loop_outside = [ast.unparse(x)[:60] for n in outside_main for x in ast.walk(n)
                    if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value in (".loop", "campaign-v3-log")]
    env_outside = [ast.unparse(x)[:60] for n in outside_main for x in ast.walk(n)
                   if isinstance(x, ast.Attribute) and ast.unparse(x) == "os.environ"]
    rsd_callers = sorted({f.name for f in TREE.body if isinstance(f, ast.FunctionDef) for x in ast.walk(f)
                          if isinstance(x, ast.Call) and ast.unparse(x.func) == "real_smoke_deps"})
    check("CLI-status-injected, CLI-environ-injected: the STATUS path (.loop/campaign-v3-log) and os.environ are named "
          "in real_smoke_deps() alone, which only cli_deps() calls (R-AB-CLI: both command lines) - run_smoke takes both "
          "from its deps, so a test never reaches the owner's STATUS or env",
          "real_smoke_deps" in fns and loop_outside == [] and env_outside == [] and rsd_callers == ["cli_deps"]
          and any(isinstance(x, ast.Constant) and x.value == ".loop" for x in ast.walk(fns["real_smoke_deps"]))
          and "os.environ" in ast.unparse(fns["real_smoke_deps"]),
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
