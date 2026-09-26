#!/usr/bin/env python3
"""PREREG-V3 plan step A3.h: the Ollama inventory and the model pins (§8.3 J1/J2, §3 S6L, §5.1 D1's base).

Only GET /api/version and GET /api/tags on 127.0.0.1:11434 - no model is loaded, pulled or woken. The owner's full
inventory (/api/tags as Ollama gave it) is kept in the runs tree only (<runs>/_inventory/ollama/<run>/tags.json, the
auditor's Q-A3-7); the record names only the pinned models' results, which go to FREEZE-V3.

A pin is the first 12 hex digits of the model's digest - what `ollama list` shows. A pinned model that is missing, or
present with another digest, is a named problem; J2 is an ordered list (the first that passes the judge contract is
used), so a missing J2 candidate is recorded, not a problem. hermes3-8b and llama3 have no digest in the PREREG: theirs
are recorded here (§8.3 "recorded from `ollama list` at A3").

    python research/v3/ollama_inventory.py --run o1
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
from pathlib import Path

HOST, PORT = "127.0.0.1", 11434
ALLOWED = {("GET", "/api/version"), ("GET", "/api/tags")}
#: name -> the 12-hex digest pin (None: recorded at A3), its role, the PREREG section.
PINS = {
    "qwen3.6:27b": {"digest12": "a50eda8ed977", "role": "J1", "prereg": "§8.3"},
    "glm-4.7-flash:q4_K_M": {"digest12": "d1a8a26252f1", "role": "J2-1", "prereg": "§8.3"},
    "gemma3n:e4b": {"digest12": "15cb39fd9394", "role": "J2-2", "prereg": "§8.3"},
    "hermes3-8b": {"digest12": None, "role": "J2-3", "prereg": "§8.3"},
    "llama3": {"digest12": None, "role": "J2-4", "prereg": "§8.3"},
    "qwen3-coder:30b": {"digest12": "06c1097efce0", "role": "S6L", "prereg": "§3.1 S6L"},
    "bge-m3": {"digest12": None, "role": "D1 base", "prereg": "§5.1"},
}


class InventoryRefused(RuntimeError):
    pass


def request(method: str, path: str, *, host: str = HOST, port: int = PORT, timeout: float = 10.0) -> dict:
    """One allowed request (o1: anything else is refused before a byte is sent), its JSON body."""
    if host != HOST or (method, path) not in ALLOWED:
        raise InventoryRefused(f"refused: {method} {host}{path} - only GET /api/version and GET /api/tags on {HOST}")
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise OSError(f"status {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def _find(models: list, name: str) -> dict | None:
    """The model ``name`` names: exactly, or - a name without a tag - its :latest only."""
    want = {name} if ":" in name else {f"{name}:latest", name}
    hits = [m for m in models if m.get("name") in want or m.get("model") in want]
    return hits[0] if hits else None


def check_pins(models: list) -> tuple[dict, list[str]]:
    """({pin: result}, problems). o2: the pin is the digest's FIRST 12 hex digits."""
    results, problems = {}, []
    for name, pin in PINS.items():
        m = _find(models, name)
        digest = (m or {}).get("digest") or ""
        got12 = digest[:12] if digest else None
        results[name] = {"role": pin["role"], "present": m is not None, "found": (m or {}).get("name"),
                         "digest": digest or None, "digest12": got12, "pin": pin["digest12"]}
        label = f"{name} ({pin['role']}, {pin['prereg']})"
        if m is None:
            if not pin["role"].startswith("J2"):
                problems.append(f"{label}: missing from the store")
        elif pin["digest12"] is not None and got12 != pin["digest12"]:
            problems.append(f"{label}: digest {got12} is not the pinned {pin['digest12']}")
    return results, problems


def run_inventory(runs_root: Path, *, run: str, port: int = PORT) -> dict:
    """One inventory run: the two GETs, the full tags kept in the runs tree, the pins' record."""
    base = Path(runs_root) / "_inventory" / "ollama" / run
    if base.exists():
        raise InventoryRefused("this inventory run label was used before")
    base.mkdir(parents=True)
    record: dict = {"run": run, "host": f"{HOST}:{port}", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "problems": []}
    try:
        record["version"] = request("GET", "/api/version", port=port).get("version")
        tags = request("GET", "/api/tags", port=port)
    except (OSError, ValueError) as e:
        record["problems"].append(f"Ollama at {HOST}:{port} did not answer: {type(e).__name__}: {e}")
        tags = None
    if tags is not None:
        (base / "tags.json").write_bytes((json.dumps(tags, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        record["models_in_store"] = len(tags.get("models") or [])
        record["pins"], problems = check_pins(tags.get("models") or [])
        record["problems"] += problems
    (base / "record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the Ollama inventory and model pins (A3.h)")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    here = Path(__file__).resolve().parent
    spec = __import__("importlib.util").util.spec_from_file_location("v3_launch", here / "launch.py")
    L = __import__("importlib.util").util.module_from_spec(spec)
    sys.modules["v3_launch"] = L
    spec.loader.exec_module(L)
    rec = run_inventory(L.Contract.default().runs_root, run=args.run)
    print(json.dumps({k: rec.get(k) for k in ("version", "models_in_store", "problems")}, indent=1))
    print(json.dumps({n: {k: r[k] for k in ("present", "found", "digest12", "pin")} for n, r in (rec.get("pins") or {}).items()},
                     indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
