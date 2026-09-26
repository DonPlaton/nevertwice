#!/usr/bin/env python3
"""PREREG-V3 plan step A3.i: the D1 tag nvt3-bge-m3-d1 (§5.1: bge-m3 with num_batch 2048, the embedding parity).

The auditor's Q-A3-1 ruling and its three A3.i conditions:
* the tag is created by the harness through the Ollama API from the committed Modelfile (d1_tag.Modelfile: FROM bge-m3,
  PARAMETER num_batch 2048, nothing else), bound by MODELFILE_SHA256;
* e1: the base is the local bge-m3 at the digest the A3.h inventory run recorded - re-read from /api/tags immediately
  before the create (condition 1); absent or moved = refused, no create, never a pull;
* e2: the tag must be absent - present = refused (never a silent re-create);
* preflight (the auditor, A3.i' and A3.i-3): before the create, /api/show of the base must give a parseable FROM blob; its
  "parameters" field may be ABSENT (Ollama 0.34.4 omits it for a model with no PARAMETER lines - the real bge-m3, measured)
  and then reads as none, but a field that is present and does not parse is refused - BEFORE anything is written to the
  store; the record keeps the blob id and the parameter names only;
* after the create, the TAG's "parameters" must be present and parse with num_batch 2048 - absent or unparseable is a
  named problem (and the stranded rule applies);
* e3: /api/tags after the create differs from the list read right before it by exactly one new line, the tag;
* /api/show confirms num_batch 2048 and the base's model layer (the same FROM blob);
* /api/ps before and after: the loaded set is unchanged (condition 3) - a create that woke a model is named;
* the tag's digest goes to the record and then FREEZE-V3; every embedding stand from A5 on calls verify_tag() before
  its first embed (condition 2) - a missing or moved tag stops the stand (P3).
Stranded (a rule, not an improvisation): if a check after the create still fails, the record names the tag under
"stranded", the tag stays in the store and nothing else runs; the auditor rules, and removal is the owner's `ollama rm`,
recorded - this tool never deletes.
Only GET /api/version, /api/tags, /api/ps and POST /api/create, /api/show on 127.0.0.1; anything else is refused before
a byte is sent. The full tag lists stay in <runs>/_d1tag/<run>/ (Q-A3-7). Cleanup `ollama rm nvt3-bge-m3-d1` after E5,
recorded - never here.

    python research/v3/d1_tag.py --run d1 --inventory <runs>/_inventory/ollama/o1/record.json
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 11434
TAG = "nvt3-bge-m3-d1"
BASE = "bge-m3"
MODELFILE = HERE / "d1_tag.Modelfile"
MODELFILE_SHA256 = "af4f170080797f95bca7191daf1fb743ca1993073ae577e560a9d2d0ea0b4c11"
ALLOWED = {("GET", "/api/version"), ("GET", "/api/tags"), ("GET", "/api/ps"), ("POST", "/api/create"), ("POST", "/api/show")}
D1_OPTION = {"num_batch": 2048}
STRANDED_RULE = ("a check after the create failed: the tag stays in the store and nothing else runs; the auditor rules, "
                 "and removal is the owner's `ollama rm`, recorded")


class TagRefused(RuntimeError):
    pass


def request(method: str, path: str, body: dict | None = None, *, host: str = HOST, port: int = PORT,
            timeout: float = 120.0) -> dict:
    """One allowed request (anything else is refused before a byte is sent), its JSON body."""
    if host != HOST or (method, path) not in ALLOWED:
        raise TagRefused(f"refused: {method} {host}{path} - only {sorted(ALLOWED)} on {HOST}")
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise OSError(f"{method} {path}: status {resp.status}")
        return json.loads(raw)
    finally:
        conn.close()


def parse_modelfile(text: str) -> tuple[str, dict]:
    """(base, {option: value}) - exactly FROM bge-m3 and PARAMETER num_batch <int>; anything else is refused."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 2 or lines[0] != f"FROM {BASE}" or not re.fullmatch(r"PARAMETER num_batch \d+", lines[1]):
        raise TagRefused(f"the Modelfile must be FROM {BASE} and PARAMETER num_batch only, got {lines}")
    return BASE, {"num_batch": int(lines[1].split()[-1])}


def create_body(version: str, from_: str, params: dict) -> dict:
    """/api/create's body for the recorded Ollama version: {model, from, parameters} from 0.6 on, the legacy
    {name, modelfile} before 0.5; anything else (the 0.5 transition, an unparsable version) is refused, never guessed."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(version))
    v = tuple(int(x) for x in m.groups()) if m else None
    if v is not None and v >= (0, 6, 0):
        return {"model": TAG, "from": from_, "parameters": dict(params), "stream": False}
    if v is not None and v < (0, 5, 0):
        text = f"FROM {from_}\n" + "".join(f"PARAMETER {k} {val}\n" for k, val in params.items())
        return {"name": TAG, "modelfile": text, "stream": False}
    raise TagRefused(f"Ollama version {version!r}: no known /api/create form - refused, never guessed")


def _find(models: list, name: str) -> dict | None:
    want = {name} if ":" in name else {f"{name}:latest", name}
    hits = [m for m in models if m.get("name") in want or m.get("model") in want]
    return hits[0] if hits else None


def _blob(show: dict) -> str | None:
    """The FROM blob id of an /api/show answer (the last part of its path), or None."""
    fr = re.search(r"^FROM\s+(\S+)", show.get("modelfile") or "", flags=re.M)
    return re.split(r"[\\/]", fr.group(1))[-1] if fr else None


def _params(show: dict, *, absent_ok: bool) -> dict | None:
    """The parameters of an /api/show answer as {name: value}; None when a line does not parse, or when the field is
    missing and ``absent_ok`` is false. Ollama 0.34.4 omits the field for a model with no PARAMETER lines (the real
    bge-m3): the base reads it as {} (absent_ok), the tag - which must carry num_batch - does not."""
    if "parameters" not in show:
        return {} if absent_ok else None
    text = show.get("parameters")
    if not isinstance(text, str):
        return None
    out = {}
    for ln in text.splitlines():
        if ln.strip():
            m = re.fullmatch(r"\s*(\S+)\s+(.+?)\s*", ln)
            if m is None:
                return None
            out[m.group(1)] = m.group(2)
    return out


def _line(m: dict) -> tuple:
    return (m.get("digest"), m.get("size"), m.get("modified_at"))


def verify_tag(tags: dict, digest: str) -> list[str]:
    """What every embedding stand calls before its first embed (condition 2): [] when the tag is in ``tags`` (an
    /api/tags answer the stand read) at the recorded digest. Never creates anything."""
    m = _find(tags.get("models") or [], TAG)
    if m is None:
        return [f"{TAG} is missing from the store - the stand stops (P3), no re-create"]
    if m.get("digest") != digest:
        return [f"{TAG} is at {str(m.get('digest'))[:12]}, not the recorded {digest[:12]} - the stand stops (P3)"]
    return []


def _write(base: Path, record: dict) -> dict:
    (base / "record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def run_create(runs_root: Path, *, run: str, inventory: Path, port: int = PORT) -> dict:
    """One D1-tag run; returns the record, also written to <runs_root>/_d1tag/<run>/record.json."""
    base = Path(runs_root) / "_d1tag" / run
    if base.exists():
        raise TagRefused("this d1-tag run label was used before")
    base.mkdir(parents=True)
    mf = MODELFILE.read_bytes()
    record: dict = {"run": run, "host": f"{HOST}:{port}", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "modelfile_sha256": hashlib.sha256(mf).hexdigest(), "inventory": str(inventory), "problems": []}
    if record["modelfile_sha256"] != MODELFILE_SHA256:
        raise TagRefused("the Modelfile is not the committed one (sha256)")
    from_, params = parse_modelfile(mf.decode("utf-8"))
    inv = json.loads(Path(inventory).read_bytes())
    pin = (inv.get("pins") or {}).get(from_) or {}
    if inv.get("problems") or not pin.get("present") or not pin.get("digest"):
        record["problems"].append(f"e1: the inventory {inventory} is not a clean record of the base {from_}")
        return _write(base, record)
    record["version"] = request("GET", "/api/version", port=port).get("version")
    if record["version"] != inv.get("version"):
        record["problems"].append(f"e1: Ollama is {record['version']}, the inventory recorded {inv.get('version')}")
        return _write(base, record)
    first = request("GET", "/api/tags", port=port)
    if _find(first.get("models") or [], TAG) is not None:
        record["problems"].append(f"e2: {TAG} is already in the store - refused, never re-created")
        return _write(base, record)
    loaded_before = sorted(m.get("name") for m in request("GET", "/api/ps", port=port).get("models") or [])
    pre = request("POST", "/api/show", {"model": pin["found"]}, port=port)      # preflight: before anything is written
    pre_params = _params(pre, absent_ok=True)
    record["preflight"] = {"model": pin["found"], "from_blob": _blob(pre), "parameter_names": sorted(pre_params or {})}
    if _blob(pre) is None or pre_params is None:
        record["problems"].append("preflight: the base's /api/show has " + ("no parseable FROM" if _blob(pre) is None
                                  else "a parameters field that does not parse") + " - the checks after a create could "
                                  "not read it; refused, no create")
        return _write(base, record)
    before = request("GET", "/api/tags", port=port)                     # condition 1: right before the create
    (base / "tags_before.json").write_bytes((json.dumps(before, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    bm = _find(before.get("models") or [], pin["found"])
    if bm is None or bm.get("digest") != pin["digest"]:
        record["problems"].append(f"e1: the base {pin['found']} is "
                                  + ("absent" if bm is None else f"at {str(bm.get('digest'))[:12]}")
                                  + f", not the inventory's {pin['digest'][:12]} - refused, no create, no pull")
        return _write(base, record)
    if _find(before.get("models") or [], TAG) is not None:
        record["problems"].append(f"e2: {TAG} appeared in the store - refused, never re-created")
        return _write(base, record)
    record["base"] = {"name": bm["name"], "digest": bm["digest"]}
    body = create_body(record["version"], pin["found"], params)
    record["create_form"] = "from" if "from" in body else "modelfile"
    resp = request("POST", "/api/create", body, port=port)
    if resp.get("status") != "success":
        record["problems"].append(f"create: Ollama answered {str(resp.get('status'))[:80]}")
    after = request("GET", "/api/tags", port=port)
    (base / "tags_after.json").write_bytes((json.dumps(after, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    b = {m.get("name"): _line(m) for m in before.get("models") or []}
    a = {m.get("name"): _line(m) for m in after.get("models") or []}
    tm = _find(after.get("models") or [], TAG)
    new = sorted(set(a) - set(b))
    if tm is None or new != [tm.get("name")]:
        record["problems"].append(f"e3: the new lines are {new[:5]}, not exactly the tag")
    if set(b) - set(a):
        record["problems"].append(f"e3: {len(set(b) - set(a))} model(s) removed from the store")
    changed = sorted(n for n in set(a) & set(b) if a[n] != b[n])
    if changed:
        record["problems"].append(f"e3: {len(changed)} other model line(s) changed: {changed[:5]}")
    if tm is not None:
        record["tag"] = {"name": tm["name"], "digest": tm["digest"]}
        st = request("POST", "/api/show", {"model": tm["name"]}, port=port)
        sb = request("POST", "/api/show", {"model": bm["name"]}, port=port)
        tag_params = _params(st, absent_ok=False)
        if tag_params is None:
            record["problems"].append("show: the tag's /api/show has no parameters field that parses - num_batch unread")
        nb = (tag_params or {}).get("num_batch")
        record["show"] = {"num_batch": int(nb) if nb is not None and nb.isdigit() else None,
                          "same_model_layer": _blob(st) is not None and _blob(st) == _blob(sb)}
        if record["show"]["num_batch"] != D1_OPTION["num_batch"]:
            record["problems"].append(f"show: the tag carries num_batch {record['show']['num_batch']}, not 2048")
        if not record["show"]["same_model_layer"]:
            record["problems"].append("show: the tag's model layer is not the base's")
    loaded_after = sorted(m.get("name") for m in request("GET", "/api/ps", port=port).get("models") or [])
    record["ps"] = {"before": loaded_before, "after": loaded_after}
    if loaded_after != loaded_before:
        record["problems"].append(f"ps: the loaded set changed {loaded_before} -> {loaded_after} - a model was woken")
    if record["problems"] and "tag" in record:
        record["stranded"] = {"tag": record["tag"]["name"], "rule": STRANDED_RULE}
    return _write(base, record)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the D1 tag nvt3-bge-m3-d1 (A3.i)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--inventory", required=True, help="the cleared A3.h record.json")
    args = ap.parse_args(argv)
    spec = importlib.util.spec_from_file_location("v3_launch", HERE / "launch.py")
    L = importlib.util.module_from_spec(spec)
    sys.modules["v3_launch"] = L
    spec.loader.exec_module(L)
    try:
        rec = run_create(L.Contract.default().runs_root, run=args.run, inventory=Path(args.inventory))
    except TagRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps({k: rec.get(k) for k in ("version", "base", "preflight", "tag", "show", "ps", "problems", "stranded")},
                     indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
