#!/usr/bin/env python3
"""PREREG-V3 TB4.5b (A6): research/v3/arms/bm25_floor.py - the retrieval tier's lexical floor, driven as a real child
through base.py's protocol (the auditor's Q-45-2 O-a).

* the scorer is the anchor's object: hello names its code (nevertwice/_engine_recall.py), says it is the engine
  namespace's own function (not a copy), and records k1 = 1.5 and b = 0.75 from its signature and the morphology switch;
* an item's key tokenizes to nothing and is unique, so the adapter adds no term to any document;
* write keeps the items (a repeated or negative index refused), end_write persists them, and a NEW read-stage process
  ranks them: both query terms first, then one, nothing for no shared term; ties by index; k caps the list;
* IDF is over the unit's items only: the same document scores higher in a unit where its term is rare;
* Q25 and refusals by name: a read in the write stage, a write in the read stage, a read stage without items, a write
  stage on an existing store, a proxy token in the environment.

    python tests/research/_test_v3_bm25_floor.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

FLOOR = ROOT / "research" / "v3" / "arms" / "bm25_floor.py"
_spec = importlib.util.spec_from_file_location("v3_bm25_floor", FLOOR)
BF = importlib.util.module_from_spec(_spec)
sys.modules["v3_bm25_floor"] = BF
_spec.loader.exec_module(BF)
B = BF.B
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def raises(fn, exc, words: str = "") -> bool:
    try:
        fn()
    except exc as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def safely(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a crash is a named FAIL of the row that reads it
        return default if default is not None else repr(e)


TMP = Path(tempfile.mkdtemp(prefix="v3floor_"))
children: list = []


def child_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT",
                                       "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS") if k in os.environ}
    home = TMP / "homes" / uuid.uuid4().hex[:8]
    (home / "Temp").mkdir(parents=True)
    dirs = [str(Path(sys.executable).parent)]
    dirs += [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")] if os.name == "nt" else ["/usr/bin",
                                                                                                           "/bin"]
    env.update({"PATH": os.pathsep.join(dirs), "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home / "Temp"),
                "TMP": str(home / "Temp"), "TMPDIR": str(home / "Temp"), "PYTHONPYCACHEPREFIX": str(TMP / "pycache"),
                "OLLAMA_URL": "http://127.0.0.1:0/api/generate", "OLLAMA_TAGS_URL": "http://127.0.0.1:0/api/tags",
                "OLLAMA_EMBED_URL": "http://127.0.0.1:0/api/embed"})
    env.update(extra or {})
    return env


def start(name: str, stage: str, unit_dir: Path, *, env: dict | None = None):
    spec = {"arm": BF.ARM, "stage": stage, "stand": "s1", "run": "r1", "unit": unit_dir.name, "unit_dir": str(unit_dir),
            "record_path": str(TMP / f"{name}.start.json")}
    sp = TMP / f"{name}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    wd = TMP / f"cwd_{name}"
    wd.mkdir()
    errf = TMP / f"{name}.err.txt"
    p = subprocess.Popen([sys.executable, "-B", str(FLOOR), str(sp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=open(errf, "wb"), cwd=str(wd), env=env or child_env())
    children.append(p)
    return B.ArmClient(p, default_timeout=120), errf


def write_unit(name: str, unit_dir: Path, texts: dict) -> dict:
    c, _ = start(name, "write", unit_dir)
    for i, t in texts.items():
        safely(lambda i=i, t=t: c.request("write", item={"item_id": f"{unit_dir.name}:{i}", "index": i, "text": t}), {})
    e = safely(lambda: c.request("end_write"), {})
    c.close()
    return e


def ranked(c, query: str, k: int = 10) -> list:
    r = safely(lambda: c.request("read", qid="q", query=query, k=k), {})
    return r.get("items") or []


try:
    print("\n- the adapter's key adds no term -")
    ENGINE_TOKEN = re.compile(r"[^\W\d_]{3,}|\d{3,}", re.UNICODE)      # _engine_recall._TOKEN_RE, the pattern
    keys = [BF.item_key(i) for i in (*range(0, 5001), 99999, 123456789)]
    check("an item's key matches no token of the engine's pattern", all(not ENGINE_TOKEN.findall(k) for k in keys),
          next((k for k in keys if ENGINE_TOKEN.findall(k)), ""))
    check("keys are unique", len(set(keys)) == len(keys))

    print("\n- the anchor's objects, on fakes (in-process) -")
    import types

    def fake_engine(morph=True, filename=None):
        mod = types.ModuleType("fake_engine")
        src = "def _bm25_scores(qtokens, cands, k1=1.5, b=0.75):\n    return {}\ndef _tokens(s):\n    return set()\n"
        exec(compile(src, str(filename or ROOT / "nevertwice" / "_engine_recall.py"), "exec"), mod.__dict__)
        mod.LEXICAL_MORPHOLOGY = morph
        return mod
    good = fake_engine()
    check("check_anchor: the engine namespace's own functions from nevertwice/_engine_recall.py pass",
          BF.check_anchor(good) == [], str(BF.check_anchor(good)))
    copy = fake_engine()
    copy._bm25_scores = types.FunctionType(copy._bm25_scores.__code__, dict(vars(copy)), "_bm25_scores",
                                           copy._bm25_scores.__defaults__)
    got = BF.check_anchor(copy)
    check("check_anchor: a copy of the scorer (other globals) is refused by name",
          len(got) == 1 and "scorer is not the engine namespace's own object" in got[0], str(got))
    got = BF.check_anchor(fake_engine(filename=ROOT / "research" / "v3" / "arms" / "my_bm25.py"))
    check("check_anchor: code from another file is refused, for the scorer and the tokenizer",
          len(got) == 2 and all("not the anchor's" in g for g in got), str(got))
    got = BF.check_anchor(fake_engine(morph=False))
    check("check_anchor: the morphology step off is refused", len(got) == 1 and "morphology" in got[0], str(got))
    fh = BF.Handler({"stage": "write", "unit_dir": str(TMP / "fake_unit")},
                    {"m": types.SimpleNamespace(_token_list=lambda s: ["tok"]), "scorer": None, "tokens": None}, {})
    check("write refuses an item whose key would tokenize (the adapter must add no term)",
          raises(lambda: fh.write({"item_id": "k", "index": 7, "text": "t"}), BF.Refused, "would add tokens"))

    real_sg = BF.sandbox_guard
    BF.sandbox_guard = types.SimpleNamespace(mode=lambda: None, store=lambda: None)
    UI = TMP / "iso" / "u1"
    UI.mkdir(parents=True)
    try:
        msg_i = ""
        try:
            BF.bind({"arm": BF.ARM, "stage": "write", "stand": "s1", "run": "r1", "unit": "u1", "unit_dir": str(UI),
                     "record_path": str(TMP / "iso.json")})
        except Exception as e:  # noqa: BLE001
            msg_i = f"{type(e).__name__}: {e}"
        check("bind refuses by name when sandbox_guard.isolate() did not run, before the engine is imported",
              "Refused" in msg_i and "isolate() did not run" in msg_i, msg_i[:200])
    finally:
        BF.sandbox_guard = real_sg

    print("\n- the write stage -")
    UA = TMP / "runs" / "s1" / "r1" / "bm25-floor" / "ua"
    UA.mkdir(parents=True)
    TEXTS = {0: "the cat sat on the mat", 1: "a bounded retry fixed the flaky upload", 2: "upload the quarterly report",
             3: "zebra crossing", 4: "zebra crossing"}
    c, err = start("w", "write", UA)
    h = safely(lambda: c.request("hello"), {})
    sc = (h.get("start") or {}).get("scorer") or {}
    check("the scorer is the anchor's: its code is nevertwice/_engine_recall.py, the engine namespace's own object",
          sc.get("code") == "nevertwice/_engine_recall.py" and sc.get("is_engine_object") is True
          and sc.get("name") == "_bm25_scores" and sc.get("tokenizer") == "_tokens",
          str(sc) + err.read_bytes().decode("utf-8", "replace")[-300:])
    check("k1 and b are recorded from the signature (1.5 / 0.75), and the morphology switch is on",
          sc.get("k1") == 1.5 and sc.get("b") == 0.75 and (h.get("start") or {}).get("lexical_morphology") is True,
          str(sc))
    check("hello: no LLM, no embedder", h.get("llm_label") == "none" and h.get("embedder") is None, str(h)[:200])
    check("the start record declares that the floor may return fewer than k (a lexical path, the auditor's F7)",
          ((h.get("start") or {}).get("returns") or {}).get("fewer_than_k") is True)
    wrote = {i: safely(lambda i=i, t=t: c.request("write", item={"item_id": f"ua:{i}", "index": i, "text": t}), {})
             for i, t in TEXTS.items()}
    check("each write names the item's bytes (item_sha256), as every retrieval arm does",
          all(w.get("item_sha256") == B.text_sha256(TEXTS[i]) for i, w in wrote.items()), str(wrote)[:200])
    check("a repeated index is refused", raises(lambda: c.request("write", item={"item_id": "d", "index": 1, "text": "x"}),
                                                B.ArmError, "new non-negative int index"))
    check("a negative index is refused",
          raises(lambda: c.request("write", item={"item_id": "n", "index": -1, "text": "x"}), B.ArmError, "index"))
    check("a read in the write stage is refused (Q25)",
          raises(lambda: c.request("read", qid="q", query="x", k=1), B.ArmError, "read stage"))
    e = safely(lambda: c.request("end_write"), {})
    check("end_write: the unit's items digest, the one the ranker computes for the same bytes",
          e.get("items_sha256") == B.items_digest({i: B.text_sha256(t) for i, t in TEXTS.items()}))
    check("end_write persists the five items", (e.get("footprint") or {}).get("retrievable") == 5
          and (UA / "store" / "items.json").is_file(), str(e)[:200])
    c.close()

    print("\n- the read stage: a new process on the persisted items -")
    c, err = start("r", "read", UA)
    got = ranked(c, "retry upload")
    check("both query terms rank first, one term next, no shared term never",
          [x["index"] for x in got] == [1, 2] and got[0]["score"] > got[1]["score"] > 0, str(got)[:300])
    check("the item's raw text and its rank come back (\"#<index>\" is the harness's)",
          got and got[0]["text"] == TEXTS[1] and [x["rank"] for x in got] == [1, 2])
    check("ties rank by index", [x["index"] for x in ranked(c, "zebra")] == [3, 4])
    UD = TMP / "runs" / "s1" / "r1" / "bm25-floor" / "ud"
    UD.mkdir(parents=True)
    write_unit("wd", UD, {10: "zebra crossing", 2: "zebra crossing", 5: "the cat"})
    cd, _ = start("rd", "read", UD)
    check("ties rank by index as numbers, not by the persisted file's string order (2 before 10)",
          [x["index"] for x in ranked(cd, "zebra")] == [2, 10])
    cd.close()
    check("k caps the list", [x["index"] for x in ranked(c, "retry upload", k=1)] == [1])
    r3 = safely(lambda: c.request("read", qid="q", query="zebra upload", k=3), {})
    r10 = safely(lambda: c.request("read", qid="q", query="zebra upload", k=10), {})
    check("F7: a read returns exactly min(k, the items sharing a query term): 3 of 4 at k=3, all 4 at k=10",
          len(r3.get("items") or []) == 3 and r3.get("items_returned") == 3
          and len(r10.get("items") or []) == 4 and r10.get("items_returned") == 4, f"{r3.get('items_returned')} "
          f"{r10.get('items_returned')}")
    cr = safely(lambda: c.request("counters"), {})
    check("F7: the counters count every read, the items returned and the reads short of k",
          cr.get("reads") == 5 and cr.get("items_returned") == 2 + 2 + 1 + 3 + 4 and cr.get("reads_short_of_k") == 3,
          str(cr))
    check("a query with no token of three letters or more returns nothing", ranked(c, "a an to") == [])
    check("a write in the read stage is refused (Q25)",
          raises(lambda: c.request("write", item={"item_id": "x", "index": 9, "text": "t"}), B.ArmError, "write stage"))
    c.close()

    print("\n- IDF over the unit's items only -")
    UB = TMP / "runs" / "s1" / "r1" / "bm25-floor" / "ub"
    UB.mkdir(parents=True)
    write_unit("wb", UB, {0: "upload the quarterly report", 1: "upload again", 2: "upload once more",
                          3: "upload twice", 4: "upload the rest"})
    ca, _ = start("ra2", "read", UA)
    cb, _ = start("rb2", "read", UB)
    sa = next((x["score"] for x in ranked(ca, "upload") if x["index"] == 2), None)
    sb = next((x["score"] for x in ranked(cb, "upload") if x["index"] == 0), None)
    check("the same document scores higher where its term is rarer among the unit's items",
          sa is not None and sb is not None and sa > sb, f"{sa} vs {sb}")
    ca.close()
    cb.close()

    print("\n- the seal between the stages (Q-45-5) -")
    UM = TMP / "moved" / "ud"
    shutil.copytree(UD, UM)
    cm, _ = start("m1", "read", UM)
    msg_m = ""
    try:
        cm.request("hello")
    except B.ArmError as ex:
        msg_m = str(ex)
    cm.close()
    check("the same items at another path are refused by name", "not the one the write stage sealed" in msg_m, msg_m[:200])
    (UD / "store" / "items.json").write_bytes((UD / "store" / "items.json").read_bytes().replace(b"cat", b"dog"))
    ct, _ = start("t1", "read", UD)
    msg_t = ""
    try:
        ct.request("hello")
    except B.ArmError as ex:
        msg_t = str(ex)
    ct.close()
    check("items touched between the stages are refused by name", "changed between the stages" in msg_t, msg_t[:200])

    print("\n- refusals by name -")
    UC = TMP / "runs" / "s1" / "r1" / "bm25-floor" / "uc"
    UC.mkdir(parents=True)

    def hello_error(name, stage, unit_dir, env=None) -> str:
        cc, _ = start(name, stage, unit_dir, env=env)
        try:
            cc.request("hello")
            return "no refusal"
        except B.ArmError as ex:
            return str(ex)
        finally:
            cc.close()
    msg = hello_error("x1", "read", UC)
    check("a read stage without persisted items is refused", "there are none" in msg, msg[:200])
    msg = hello_error("x2", "write", UA)
    check("a write stage on an existing store is refused", "fresh store" in msg, msg[:200])
    msg = hello_error("x3", "write", UC, env=child_env({"DEEPSEEK_API_KEY": "nvt3-bm25-" + "d" * 32}))
    check("a proxy token in the floor's environment is refused (it has no LLM)", "has no LLM" in msg, msg[:200])
    msg = hello_error("x4", "write", UC, env=child_env({"NEVERTWICE_LEXICAL_MORPHOLOGY": "0"}))
    check("the engine's morphology step switched off is refused (the shipped default is on)", "morphology" in msg,
          msg[:200])
    check("... and the refusal is recorded",
          safely(lambda: json.loads((TMP / "x3.start.json").read_bytes()), {}).get("ok") is False)
finally:
    for ch in children:
        try:
            ch.kill()
        except OSError:
            pass
        try:
            ch.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 bm25 floor: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
