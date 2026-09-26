#!/usr/bin/env python3
"""PREREG-V3 plan step A3 (after the windows): the pin table's FILLED block, written from verified pin_fill.json files.

Each A3 window writes pin_fill.json only when it has no problem; the auditor verifies it (m6 --placed) and names its
sha256. This tool takes those files - each bound to the sha256 the auditor named - and writes their values into the
FILLED block of research/v3/corpus_pin_v3.py, between its markers, never by hand:

* the file's sha256 must be the one given, and its window and run are recorded with every value;
* it is bound to its own window run: the record.json and place_record.json beside it name the same window and run,
  and both have no problem - a pin_fill from a run with problems is never applied, whatever its sha256;
* it covers its window: every pin the table gives that window is in it (an alias confirmed), and no pin of another
  window - a partial set is refused, never filled from silently;
* every value goes through fill()'s own rules on a copy of the table first (a 64-hex sha256, a size, the licence found
  against the declared one - P6 - and a pin filled once); one refusal writes nothing;
* an alias (the oracle, Q-A3F-8) is only confirmed - its pinned value is already the v2 one - and never written;
* a pin already in FILLED is refused - fill()'s own rule: a pin is filled once.

    python research/v3/pins_apply.py --fill <runs>\\_fetch\\a3-hf\\h2\\pin_fill.json=<sha256> [--fill ...]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE / "corpus_pin_v3.py"
BEGIN, END = "# >>> FILLED", "# <<< FILLED"
KEYS = ("revision", "sha256", "bytes", "licence_found", "licence_source", "from")


class ApplyRefused(RuntimeError):
    pass


def _load_table(path: Path):
    spec = importlib.util.spec_from_file_location(f"v3_corpus_pin_apply_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_fill(path: Path, sha256: str) -> dict:
    """A pin_fill.json, refused unless its sha256 is the one the auditor named and its window run is clean."""
    raw = Path(path).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != sha256:
        raise ApplyRefused(f"{path}: sha256 {got[:12]}... is not the named {sha256[:12]}...")
    data = json.loads(raw)
    if not isinstance(data.get("pins"), dict) or not data.get("window") or not data.get("run"):
        raise ApplyRefused(f"{path}: not a pin_fill record")
    for side in ("record.json", "place_record.json"):
        f = Path(path).with_name(side)
        if not f.is_file():
            raise ApplyRefused(f"{path}: its window run has no {side}")
        rec = json.loads(f.read_bytes())
        if rec.get("problems") != []:
            raise ApplyRefused(f"{path}: its window run's {side} has problems - never applied")
        if rec.get("window") != data["window"] or (side == "record.json" and rec.get("run") != data["run"]):
            raise ApplyRefused(f"{path}: its {side} is another window run")
    if Path(path).parent.name != data["run"] or Path(path).parent.parent.name != data["window"]:
        raise ApplyRefused(f"{path}: not at <runs>/_fetch/{data['window']}/{data['run']}/")
    return data


def plan_values(fills: list[tuple[dict, str]], table_mod) -> tuple[dict, list[str]]:
    """({pin: value}, the aliases confirmed): every value dry-run through fill() on a copy of the table."""
    pins = copy.deepcopy(table_mod.PINS)
    values, aliases = {}, []
    for data, sha in fills:
        src = f"{data['window']} {data['run']} pin_fill {sha[:12]}"
        window_pins = sorted(n for n, p in pins.items() if p.get("window") == data["window"])
        if sorted(data["pins"]) != window_pins:
            raise ApplyRefused(f"{data['window']} {data['run']}: the pin_fill covers {sorted(data['pins'])[:3]}..., not its "
                               f"window's pins exactly ({len(data['pins'])} of {len(window_pins)}) - a partial set is refused")
        for name, v in sorted(data["pins"].items()):
            if name not in pins:
                raise ApplyRefused(f"{name}: not a pin of the table")
            if "alias_confirmed" in v:
                p = pins[name]
                if not p.get("alias_of") or (v.get("sha256"), v.get("bytes")) != (p["sha256"], p["bytes"]):
                    raise ApplyRefused(f"{name}: an alias confirmation that is not its pinned value")
                aliases.append(f"{name} = {p['alias_of']} ({src})")
                continue
            try:
                table_mod.fill(name, revision=v["revision"], sha256=v["sha256"], size=v["bytes"],
                               licence_found=v["licence_found"], pins=pins)
            except (table_mod.PinRefused, KeyError, TypeError, ValueError) as e:
                raise ApplyRefused(f"{name}: {type(e).__name__}: {e}") from None
            values[name] = {"revision": v["revision"], "sha256": v["sha256"], "bytes": int(v["bytes"]),
                            "licence_found": v["licence_found"], "licence_source": v.get("licence_source"), "from": src}
    return values, aliases


def render(filled: dict) -> str:
    lines = [BEGIN, "FILLED: dict[str, dict] = {"]
    for name in sorted(filled):
        v = filled[name]
        lines.append(f"    {json.dumps(name)}: {{" + ", ".join(f"{json.dumps(k)}: {json.dumps(v[k])}" for k in KEYS) + "},")
    lines += ["}", END]
    return "\n".join(lines)


def apply(table_path: Path, fills: list[tuple[Path, str]]) -> dict:
    """Write the FILLED block of ``table_path``; returns what was written and confirmed. Nothing is written on a refusal."""
    text = Path(table_path).read_bytes().decode("utf-8")
    block = re.search(re.escape(BEGIN) + r"\n.*?\n" + re.escape(END), text, flags=re.S)
    if text.count(BEGIN) != 1 or text.count(END) != 1 or block is None:
        raise ApplyRefused("the table's FILLED markers are not there exactly once")
    table_mod = _load_table(Path(table_path))
    data = [(read_fill(p, s), s) for p, s in fills]
    values, aliases = plan_values(data, table_mod)
    merged = {**{k: dict(v) for k, v in table_mod.FILLED.items()}, **values}
    new = text[:block.start()] + render(merged) + text[block.end():]
    Path(table_path).write_bytes(new.encode("utf-8"))
    return {"written": sorted(values), "aliases_confirmed": aliases, "filled_total": len(merged)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="write verified pin_fill.json values into the pin table's FILLED block")
    ap.add_argument("--fill", action="append", required=True, help="<pin_fill.json>=<its sha256, as the auditor named it>")
    ap.add_argument("--table", default=str(TABLE))
    args = ap.parse_args(argv)
    fills = []
    for f in args.fill:
        path, _, sha = f.rpartition("=")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            print(f"--fill {f}: <path>=<sha256>", file=sys.stderr)
            return 2
        fills.append((Path(path), sha))
    try:
        print(json.dumps(apply(Path(args.table), fills), indent=1))
    except ApplyRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
