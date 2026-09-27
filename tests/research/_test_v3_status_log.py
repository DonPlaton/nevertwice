#!/usr/bin/env python3
"""PREREG-V3 TB4.1 (A6): research/v3/status_log.py - the STATUS v3 writer (.loop/STATUS-V3-CONTRACT.md + A6 rulings).

* a scripted campaign (1 stand, 2 blocks, 2 runs, 3 arms; a smoke START, an ABORT, a UNIT-ABORT, an exogenous RERUN,
  an INCIDENT, a SET-ASIDE) is written byte for byte as expected: LF, local time + utc= with microseconds, strictly
  increasing, the arm order and its seed on BLOCK START (Q25), aborted= on END derived from the UNIT-ABORT lines (Q1);
* the writer refuses by construction: a reused or live id, END/ABORT without START, a START outside an open block, a
  second block of a stand while one is open (the barrier), BLOCK END with a live run, a scored START outside the
  campaign, an exogenous RERUN on a subset of arms or without a known incident, a space in an id or value, a run id
  with a dot (Q3), an unknown tag, kind or cause, a unit that is not in its block;
* A6 D4: a BLOCK START whose arm_order is not the order sha256(f"{seed}|{arm}") gives is refused (B-D4W);
* A6 D2: a crashed unit is UNIT-ABORT reason=crash with exactly one of rc= / signal=; a ceiling abort carries
  neither; END lists crashes and ceilings alike; a restarted writer replays them;
* S8: a byte changed outside the writer is refused at the next append; the file is only ever appended;
* a restarted writer replays the file through the same state machine and continues; self_check() names a problem;
* where the auditor's m2_v3 is present (.loop, outside the repository by design - R12), its parser and S1-S7 checks
  accept the file; in CI that part is a named SKIP.

    python tests/research/_test_v3_status_log.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
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


SL = _load("v3_status_log", ROOT / "research" / "v3" / "status_log.py")
PASSED = FAILED = SKIPPED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  SKIP {name} - {why}")


MSK = dt.timezone(dt.timedelta(hours=3))


class Clock:
    """A fake clock: 2026-10-01 09:00:00 UTC, +1.5 s per reading."""
    def __init__(self, step: float = 1.5):
        self.t = dt.datetime(2026, 10, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
        self.step = dt.timedelta(seconds=step)

    def __call__(self) -> dt.datetime:
        self.t += self.step
        return self.t


def refused(fn, words: str) -> bool:
    try:
        fn()
    except SL.StatusRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


ANCHOR, PREREG, FREEZE = "a" * 40, "b" * 64, "c" * 64


def campaign(log) -> None:
    log.campaign_start(anchor=ANCHOR, prereg=PREREG, freeze=FREEZE)
    log.set_aside("D:/Coding/_nevertwice_polygon/h2h_v2_stores", "D:/Coding/_nevertwice_polygon/h2h_v2_stores.stale-x")
    log.stand("SX", "START", model="deepseek-v4-flash", changelog="2026-09-10", order=1)
    log.block_start("SX", "b00", units=["s1"], arm_order=["smokearm"], seed=6)
    log.start("SX", "b00", "r1", "smokearm", pid=11, tag="smoke")
    log.end("SX/b00/r1/smokearm", rc=0, wall_s=1.25, units=1, out="runs/SX/b00/r1/smokearm.json")
    log.block_end("SX", "b00")
    log.block_start("SX", "b01", units=["u1", "u2"], arm_order=["mem0", "nevertwice", "letta"], seed=0)
    ids = [log.start("SX", "b01", r, a, pid=100 + i, tag="scored")
           for i, (r, a) in enumerate((r, a) for r in ("r1", "r2") for a in ("mem0", "nevertwice", "letta"))]
    log.unit_abort("SX/b01/r1/mem0", "u2", reason="ceiling")
    for i in ids:
        if i == "SX/b01/r2/letta":
            log.abort(i, reason="blocked-local-server")
        else:
            log.end(i, rc=0, wall_s=12.5, units=2, out=f"runs/{i}.json")
    log.block_end("SX", "b01")
    log.incident("inc1", "START", arms=["mem0", "nevertwice", "letta"], kind="5xx")
    log.incident("inc1", "END", arms=["mem0", "nevertwice", "letta"], kind="5xx")
    log.block_start("SX", "b02", units=["u3"], arm_order=["letta", "mem0", "nevertwice"], seed=5)
    for r in ("r1", "r2"):
        for a in ("mem0", "nevertwice", "letta"):
            i = log.start("SX", "b02", r, a, pid=200, tag="scored")
            log.end(i, rc=0, wall_s=3, units=1, out=f"runs/{i}.json")
    log.block_end("SX", "b02")
    log.rerun("SX", "b01", "r1", "u1", arms="all", cause="exogenous", incident="inc1")
    log.stand("SX", "END", model="deepseek-v4-flash", changelog="2026-09-10")
    log.campaign_end()


print("\n- the scripted campaign, byte for byte -")
with tempfile.TemporaryDirectory(prefix="v3status_") as td:
    p = Path(td) / "STATUS"
    log = SL.StatusLog(p, now=Clock(), local_tz=MSK)
    err = None
    try:
        campaign(log)
    except Exception as e:  # noqa: BLE001  - a refusal inside the script is a named FAIL, not a crash
        err = repr(e)
    check("the scripted campaign is written without a refusal", err is None, str(err))
    raw = p.read_bytes() if p.exists() else b""
    lines = raw.decode("utf-8").split("\n")
    check("the file ends with one LF and holds no CR", raw.endswith(b"\n") and b"\r" not in raw)
    check("the first line is CAMPAIGN V3 START with local time, anchor/prereg/freeze and utc= in microseconds",
          lines[0] == f"2026-10-01 12:00:01 CAMPAIGN V3 START anchor={ANCHOR} prereg={PREREG} freeze={FREEZE} "
                      f"utc=2026-10-01T09:00:01.500000+00:00", lines[0])
    check("the SET-ASIDE line: src -> dst", lines[1].split(" ", 2)[2].startswith(
        "SET-ASIDE D:/Coding/_nevertwice_polygon/h2h_v2_stores -> D:/Coding/_nevertwice_polygon/h2h_v2_stores.stale-x "),
        lines[1])
    check("STAND START carries order, model and changelog",
          " STAND SX START order=1 model=deepseek-v4-flash changelog=2026-09-10 utc=" in lines[2], lines[2])
    check("BLOCK START carries the units, the arm order and its seed (Q25: arms one at a time, seeded order)",
          " BLOCK SX/b01 START units=u1,u2 arm_order=mem0,nevertwice,letta seed=0 utc=" in lines[7], lines[7])
    check("a smoke START is logged with its tag (S2: every launched run, smoke included)",
          " START SX/b00/r1/smokearm pid=11 tag=smoke utc=" in lines[4], lines[4])
    unit_abort = [x for x in lines if " UNIT-ABORT " in x]
    check("UNIT-ABORT names <stand>/<block>/<run>/<arm>/<unit> and reason=ceiling (Q1)",
          len(unit_abort) == 1 and " UNIT-ABORT SX/b01/r1/mem0/u2 reason=ceiling utc=" in unit_abort[0], str(unit_abort))
    end_mem0 = [x for x in lines if " END SX/b01/r1/mem0 " in x]
    check("the arm-run's END carries aborted=<units>, derived from its own UNIT-ABORT lines (Q1)",
          len(end_mem0) == 1 and " rc=0 wall=12.500s units=2 out=runs/SX/b01/r1/mem0.json aborted=u2 utc=" in end_mem0[0],
          str(end_mem0))
    end_nv = [x for x in lines if " END SX/b01/r1/nevertwice " in x]
    check("an END with no unit abort carries no aborted= field", end_nv and "aborted=" not in end_nv[0], str(end_nv))
    check("ABORT carries reason=<word>", any(" ABORT SX/b01/r2/letta reason=blocked-local-server utc=" in x for x in lines))
    check("INCIDENT START and END both carry arms and kind",
          sum(1 for x in lines if " INCIDENT inc1 " in x and " arms=mem0,nevertwice,letta kind=5xx utc=" in x) == 2)
    check("the exogenous RERUN names arms=all, its cause and the incident",
          any(" RERUN SX/b01/r1/u1 arms=all cause=exogenous incident=inc1 utc=" in x for x in lines))
    check("STAND END and CAMPAIGN V3 END close the file",
          " STAND SX END model=deepseek-v4-flash changelog=2026-09-10 utc=" in lines[-3]
          and " CAMPAIGN V3 END utc=" in lines[-2] and lines[-1] == "", str(lines[-3:]))
    utcs = [dt.datetime.fromisoformat(x.rsplit(" utc=", 1)[1]) for x in lines if x]
    check("utc strictly increases line by line (the S3 barrier is strict: last END < next START)",
          all(a < b for a, b in zip(utcs, utcs[1:])))
    check("every line is <local ts> <event> ... utc=<iso> and nothing else is written",
          all(len(x) > 20 and x[:19] == (dt.datetime.fromisoformat(x.rsplit(" utc=", 1)[1]).astimezone(MSK)
                                        .strftime("%Y-%m-%d %H:%M:%S")) for x in lines if x))
    check("self_check() of the finished file names nothing", SL.self_check(p) == [], str(SL.self_check(p)[:3]))

    print("\n- where the auditor's m2_v3 is present, it accepts the file (S1-S7) -")
    m2p = ROOT / ".loop" / "m2_v3.py"
    if not m2p.exists():
        skip("m2_v3 parses the file and S1-S7 pass", "the auditor's m2_v3 lives in .loop, outside the repository, by design (R12)")
    else:
        M2 = _load("v3_m2_for_status", m2p)
        evs, bad = M2.parse(raw.decode("utf-8"))
        knows_unit_abort = "UNIT-ABORT" in m2p.read_text(encoding="utf-8")
        ua_lines = {f"line {n} " for n, x in enumerate(lines, 1) if " UNIT-ABORT " in x}
        bad_other = [b for b in bad if knows_unit_abort or not any(u in b for u in ua_lines)]
        check("m2_v3.parse finds no malformed line", not bad_other, str(bad_other[:3]))
        if not knows_unit_abort:
            skip("m2_v3 parses UNIT-ABORT", "m2_v3 does not parse UNIT-ABORT yet (the auditor's side, ruling Q1)")
        probs = M2.check_status(evs, anchor=ANCHOR, freeze_sha=FREEZE, prereg_shas={PREREG},
                                set_asides=["d:/coding/_nevertwice_polygon/h2h_v2_stores"])
        check("m2_v3.check_status: S1-S7 name nothing", not probs, str(probs[:3]))

print("\n- refusals by construction -")


def fresh(td: str, name: str = "STATUS", **kw):
    log = SL.StatusLog(Path(td) / name, now=Clock(), local_tz=MSK, **kw)
    log.campaign_start(anchor=ANCHOR, prereg=PREREG, freeze=FREEZE)
    log.stand("SX", "START", model="m", changelog="2026-09-10", order=1)
    log.block_start("SX", "b01", units=["u1", "u2"], arm_order=["a1", "a2"], seed=1)
    return log


with tempfile.TemporaryDirectory(prefix="v3status_r_") as td:
    log = fresh(td)
    i = log.start("SX", "b01", "r1", "a1", pid=5, tag="scored")
    check("a live id cannot START again", refused(lambda: log.start("SX", "b01", "r1", "a1", pid=6, tag="scored"),
                                                   "already"))
    check("END of an id that never STARTed is refused", refused(
        lambda: log.end("SX/b01/r9/a1", rc=0, wall_s=1, units=1, out="x"), "no live START"))
    check("ABORT of an id that never STARTed is refused", refused(lambda: log.abort("SX/b01/r9/a1", reason="x"),
                                                                  "no live START"))
    check("BLOCK END while a run of the block is live is refused (the barrier)",
          refused(lambda: log.block_end("SX", "b01"), "live"))
    check("a second block of the stand cannot START while one is open (the barrier)",
          refused(lambda: log.block_start("SX", "b02", units=["u3"], arm_order=["a1"], seed=2), "open"))
    check("a START outside an open block is refused", refused(lambda: log.start("SX", "b07", "r1", "a2", pid=5,
                                                                                 tag="scored"), "not open"))
    check("an unknown tag is refused", refused(lambda: log.start("SX", "b01", "r1", "a2", pid=5, tag="best"), "tag"))
    check("a run id with a dot is refused (Q3: the unit prefix is split at the first dot)",
          refused(lambda: log.start("SX", "b01", "r1.x", "a2", pid=5, tag="scored"), "run id"))
    check("a space in an id is refused", refused(lambda: log.start("SX", "b01", "r2", "a 2", pid=5, tag="scored"), "id"))
    check("a space in a value is refused",
          refused(lambda: log.end(i, rc=0, wall_s=1, units=1, out="runs/a b.json"), "space"))
    check("UNIT-ABORT of a unit that is not in the block is refused",
          refused(lambda: log.unit_abort(i, "u9", reason="ceiling"), "not in block"))
    check("UNIT-ABORT with a reason other than ceiling is refused (S9)",
          refused(lambda: log.unit_abort(i, "u1", reason="slow"), "reason"))
    check("a START of an arm outside the block's arm_order is refused (S10)",
          refused(lambda: log.start("SX", "b01", "r1", "a9", pid=5, tag="scored"), "arm_order"))
    log.unit_abort(i, "u1", reason="ceiling")
    check("the same unit cannot be UNIT-ABORTed twice", refused(lambda: log.unit_abort(i, "u1", reason="ceiling"),
                                                                 "already"))
    log.end(i, rc=0, wall_s=1, units=2, out="runs/x.json")
    check("BLOCK END while an arm of its arm_order never started is refused (S10)",
          refused(lambda: log.block_end("SX", "b01"), "never started"))
    check("an ended id cannot be reused", refused(lambda: log.start("SX", "b01", "r1", "a1", pid=7, tag="scored"),
                                                  "already"))
    check("an exogenous RERUN on a subset of arms is refused (S6/PR7: all arms)",
          refused(lambda: log.rerun("SX", "b01", "r1", "u1", arms=["a1"], cause="exogenous", incident="-"), "all arms"))
    check("an exogenous RERUN without a known incident is refused",
          refused(lambda: log.rerun("SX", "b01", "r1", "u1", arms="all", cause="exogenous", incident="inc9"), "incident"))
    check("an exogenous RERUN with incident=- is refused (it names its incident)",
          refused(lambda: log.rerun("SX", "b01", "r1", "u1", arms="all", cause="exogenous", incident="-"), "incident"))
    check("an unknown cause is refused",
          refused(lambda: log.rerun("SX", "b01", "r1", "u1", arms="all", cause="tuning", incident="-"), "cause"))
    check("an unknown incident kind is refused",
          refused(lambda: log.incident("i2", "START", arms=["a1"], kind="slow"), "kind"))
    check("INCIDENT END without its START is refused", refused(lambda: log.incident("i3", "END", arms=["a1"], kind="5xx"),
                                                               "not open"))
    check("STAND END with an open block is refused", refused(
        lambda: log.stand("SX", "END", model="m", changelog="2026-09-10"), "open block"))
    check("CAMPAIGN V3 START twice is refused", refused(lambda: log.campaign_start(anchor=ANCHOR, prereg=PREREG,
                                                                                    freeze=FREEZE), "already"))
    check("an anchor that is not 40 hex is refused", refused(
        lambda: SL.StatusLog(Path(td) / "S2", now=Clock(), local_tz=MSK).campaign_start(
            anchor="HEAD", prereg=PREREG, freeze=FREEZE), "anchor"))

with tempfile.TemporaryDirectory(prefix="v3status_s7_") as td:
    log = SL.StatusLog(Path(td) / "STATUS", now=Clock(), local_tz=MSK)
    log.stand("SP", "START", model="m", changelog="2026-09-10", order=1)
    log.block_start("SP", "b01", units=["u1"], arm_order=["a1"], seed=1)
    check("a scored START before CAMPAIGN V3 START is refused (S7)",
          refused(lambda: log.start("SP", "b01", "r1", "a1", pid=5, tag="scored"), "campaign"))
    j = log.start("SP", "b01", "r1", "a1", pid=5, tag="smoke")
    check("a smoke START before the campaign is allowed and logged (the pilot)",
          " START SP/b01/r1/a1 pid=5 tag=smoke " in (Path(td) / "STATUS").read_text(encoding="utf-8"))

print("\n- S8: append-only, and a restart -")
with tempfile.TemporaryDirectory(prefix="v3status_s8_") as td:
    p = Path(td) / "STATUS"
    log = fresh(td)
    i = log.start("SX", "b01", "r1", "a1", pid=5, tag="scored")
    before = p.read_bytes()
    p.write_bytes(before.replace(b"pid=5", b"pid=6"))
    check("a byte changed outside the writer is refused at the next append (S8)",
          refused(lambda: log.end(i, rc=0, wall_s=1, units=1, out="x"), "changed outside"))
    p.write_bytes(before)
    log2 = SL.StatusLog(p, now=Clock(step=3600), local_tz=MSK)
    log2.end(i, rc=0, wall_s=1, units=1, out="runs/x.json")
    after = p.read_bytes()
    check("a restarted writer replays the file and continues: the old bytes are an exact prefix",
          after.startswith(before) and after.count(b"\n") == before.count(b"\n") + 1)
    check("... and its state came back: the ended id cannot START again",
          refused(lambda: log2.start("SX", "b01", "r1", "a1", pid=7, tag="scored"), "already"))
    q = Path(td) / "BAD"
    q.write_bytes(before.replace(b" START SX/b01/r1/a1 ", b" END SX/b01/r1/a1 "))
    probs = SL.self_check(q)
    check("self_check() names the problem of a file that did not come from the writer", probs != [], str(probs))
    check("... and a writer refuses to continue such a file",
          refused(lambda: SL.StatusLog(q, now=Clock(), local_tz=MSK), "replay"))
    ua = Path(td) / "UA"
    log3 = SL.StatusLog(ua, now=Clock(), local_tz=MSK)
    log3.stand("SP", "START", model="m", changelog="2026-09-10", order=1)
    log3.block_start("SP", "b01", units=["u1"], arm_order=["a1"], seed=1)
    j = log3.start("SP", "b01", "r1", "a1", pid=5, tag="smoke")
    log3.unit_abort(j, "u1", reason="ceiling")
    log3.end(j, rc=0, wall_s=1, units=1, out="runs/x.json")
    good = ua.read_bytes()
    ua.write_bytes(good.replace(b" aborted=u1", b""))
    check("self_check() names an END whose aborted= does not list its UNIT-ABORT units (Q1)",
          any("aborted=" in x for x in SL.self_check(ua)), str(SL.self_check(ua)))
    first = good.split(b"\n")[0]
    stamp = first.rsplit(b" utc=", 1)[1]
    ua.write_bytes(good.replace(stamp, stamp.replace(b".500000", b""), 1))
    check("self_check() names a line in another form (utc without microseconds does not re-render)",
          any("re-render" in x for x in SL.self_check(ua)), str(SL.self_check(ua)))
    ls = good.split(b"\n")
    s0, s1 = ls[0].rsplit(b" utc=", 1)[1], ls[1].rsplit(b" utc=", 1)[1]
    ua.write_bytes(b"\n".join([ls[0], ls[1].replace(s1, s0)] + ls[2:]))
    check("self_check() names a line whose utc does not follow the previous one (the strict S3 order)",
          any("does not follow" in x for x in SL.self_check(ua)), str(SL.self_check(ua)))

print("\n- A6 D4: BLOCK START's arm_order is the order its seed gives (B-D4W) -")
check("seeded_order sorts the arms by sha256(f'{seed}|{arm}') hexdigest, ascending",
      SL.seeded_order(["mem0", "nevertwice", "letta"], 7) == ["letta", "mem0", "nevertwice"]
      and SL.seeded_order(["mem0", "nevertwice", "letta"], 7) == sorted(
          ["mem0", "nevertwice", "letta"], key=lambda a: hashlib.sha256(f"{7}|{a}".encode("utf-8")).hexdigest()))
with tempfile.TemporaryDirectory(prefix="v3status_d4_") as td:
    log = SL.StatusLog(Path(td) / "STATUS", now=Clock(), local_tz=MSK)
    log.stand("SD", "START", model="m", changelog="2026-09-10", order=1)
    check("a BLOCK START whose arm_order is not its seed's order is refused (D4)",
          refused(lambda: log.block_start("SD", "b01", units=["u1"], arm_order=["mem0", "nevertwice", "letta"], seed=7),
                  "seeded order"))
    log.block_start("SD", "b01", units=["u1"], arm_order=["letta", "mem0", "nevertwice"], seed=7)
    check("... and the seeded order is written", "arm_order=letta,mem0,nevertwice seed=7 " in
          (Path(td) / "STATUS").read_text(encoding="utf-8"))

print("\n- A6 D2: a crashed unit is UNIT-ABORT reason=crash with exactly one of rc= / signal= -")
with tempfile.TemporaryDirectory(prefix="v3status_crash_") as td:
    cp = Path(td) / "STATUS"
    log = SL.StatusLog(cp, now=Clock(), local_tz=MSK)
    log.stand("SC", "START", model="m", changelog="2026-09-10", order=1)
    log.block_start("SC", "b01", units=["u1", "u2", "u3", "u4"], arm_order=["a1"], seed=1)
    k = log.start("SC", "b01", "r1", "a1", pid=5, tag="smoke")

    def wrote(fn) -> str:
        """'' when the writer took the line, else its refusal - a refusal here is a named FAIL, never a traceback."""
        try:
            fn()
            return ""
        except SL.StatusRefused as e:
            return str(e)

    took = [wrote(lambda: log.unit_abort(k, "u1", reason="crash", rc=-11)),
            wrote(lambda: log.unit_abort(k, "u2", reason="crash", signal="SIGKILL")),
            wrote(lambda: log.unit_abort(k, "u4", reason="crash", signal="9"))]
    lines_c = cp.read_text(encoding="utf-8").splitlines()
    check("reason=crash rc=<int>, signal=<SIGNAME> and signal=<number> are taken and written",
          took == ["", "", ""]
          and any(" UNIT-ABORT SC/b01/r1/a1/u1 reason=crash rc=-11 utc=" in x for x in lines_c)
          and any(" UNIT-ABORT SC/b01/r1/a1/u2 reason=crash signal=SIGKILL utc=" in x for x in lines_c)
          and any(" UNIT-ABORT SC/b01/r1/a1/u4 reason=crash signal=9 utc=" in x for x in lines_c), f"{took} {lines_c[-3:]}")
    check("a crash without rc= or signal= is refused", refused(lambda: log.unit_abort(k, "u3", reason="crash"), "crash"))
    check("a crash with both rc= and signal= is refused",
          refused(lambda: log.unit_abort(k, "u3", reason="crash", rc=1, signal="SIGTERM"), "exactly one"))
    check("rc= that is not an integer is refused", refused(lambda: log.unit_abort(k, "u3", reason="crash", rc="x"), "rc="))
    check("signal= that is not a signal name or number is refused",
          refused(lambda: log.unit_abort(k, "u3", reason="crash", signal="kill"), "signal name"))
    check("a ceiling abort carries neither rc= nor signal= (the scheduler killed it)",
          refused(lambda: log.unit_abort(k, "u3", reason="ceiling", rc=1), "ceiling"))
    took_c = wrote(lambda: log.unit_abort(k, "u3", reason="ceiling"))
    took_e = wrote(lambda: log.end(k, rc=1, wall_s=2, units=4, out="runs/c.json"))
    check("END lists every aborted unit, crashes and ceilings alike (Q1)", took_c == took_e == ""
          and " aborted=u1,u2,u4,u3 " in cp.read_text(encoding="utf-8").splitlines()[-1], f"{took_c!r} {took_e!r}")
    check("a restarted writer replays the crash lines and self_check() is clean", SL.self_check(cp) == []
          and SL.StatusLog(cp, now=Clock(), local_tz=MSK) is not None, str(SL.self_check(cp)))
    check("the UNIT-ABORT reasons are ceiling and crash (A6 Q1, D2)", SL.UNIT_ABORT_REASONS == ("ceiling", "crash"))

with tempfile.TemporaryDirectory(prefix="v3status_clk_") as td:
    stuck = dt.datetime(2026, 10, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    log = SL.StatusLog(Path(td) / "STATUS", now=lambda: stuck, local_tz=MSK, clock_wait_s=0.05)
    log.stand("SP", "START", model="m", changelog="2026-09-10", order=1)
    check("a clock that does not advance past the last line is refused, never nudged",
          refused(lambda: log.stand("SQ", "START", model="m", changelog="2026-09-10", order=2), "clock"))

print(f"\nv3 status log: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped")
sys.exit(1 if FAILED else 0)
