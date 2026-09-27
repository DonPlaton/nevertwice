#!/usr/bin/env python3
"""PREREG-V3 TB4.8a (A6): research/v3/points.py - the read points' context, identical for every arm (§5.2, §8.2).

* fill: the items in the arm's order (never re-sorted), joined by one newline, whole while the context stays within
  the ONE budget of 7,000 tokens; the first item that does not fit is cut to the cap (its longest fitting prefix) and
  nothing after it is read; a join that merges tokens is re-cut until the whole fits; the budget is a module constant,
  not a parameter an arm could vary; the context is never over it;
* k: 200 at B, 10 at K, the same for every arm; claude-code-memory at K is competitor-lacks-capability:k;
* Claude Code (Q-47-4, Q28): R-all = MEMORY.md, then every other file by lexicographic relative path, nested ones
  included; R-index = MEMORY.md's first 200 lines or 25 KB, whichever ends first, never half a UTF-8 character; a
  symlink, a non-UTF-8 file or a missing directory refuses by name.

    python tests/research/_test_v3_points.py
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_points", ROOT / "research" / "v3" / "points.py")
PT = importlib.util.module_from_spec(_spec)
sys.modules["v3_points"] = PT
_spec.loader.exec_module(PT)
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


# A fake tokenizer: one token per whitespace-separated word; a newline is a token of its own.
def count(text: str) -> int:
    return len(text.split()) + text.count("\n")


def cut(text: str, n: int) -> str:
    words = text.split(" ")
    return " ".join(words[:max(n, 0)])


def words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


print("- fill: order, the one budget, the cut at the cap -")
check("the budget is 7,000 and not a parameter of fill", PT.BUDGET == 7000
      and "budget" not in inspect.signature(PT.fill).parameters)
items = [words(3000, "a"), words(3000, "b"), words(3000, "c"), words(10, "d")]
ctx = PT.fill(items, count=count, cut=cut)
check("whole items while they fit, the next one cut to the cap, nothing after it",
      ctx.items_used == 3 and ctx.last_cut and ctx.tokens == 7000 and "d0" not in ctx.text
      and ctx.text.startswith("a0 ") and "c0" in ctx.text and ctx.text.endswith(" c997"),
      f"used {ctx.items_used} tokens {ctx.tokens}")
check("the items keep the arm's order, joined by one newline",
      ctx.text.split("\n")[0] == items[0] and ctx.text.split("\n")[1] == items[1] and ctx.text.count("\n") == 2)
small = PT.fill(["z y", "a b", "m"], count=count, cut=cut)
check("never re-sorted: a small context is the items in the given order", small.text == "z y\na b\nm"
      and small.items_used == 3 and not small.last_cut and small.tokens == 7, small.text)
exact = PT.fill([words(7000, "e")], count=count, cut=cut)
check("an item of exactly the budget is whole, not cut", exact.items_used == 1 and not exact.last_cut
      and exact.tokens == 7000)
coarse = PT.fill([words(3000, "a"), words(3000, "b"), words(3000, "c"), "d"],
                 count=count, cut=lambda t, n: cut(t, n - 5))
check("nothing after the cut item is read, even when the cut leaves room",
      coarse.last_cut and coarse.items_used == 3 and "d" not in coarse.text.split() and coarse.tokens == 6995,
      f"{coarse.items_used} {coarse.tokens}")
over = PT.fill([words(7001, "f"), "g"], count=count, cut=cut)
check("the first item alone over the cap is cut to it, the next unread",
      over.items_used == 1 and over.last_cut and over.tokens == 7000 and "g" not in over.text.split(), over.tokens)
full = PT.fill([words(6999, "h"), "i j"], count=count, cut=cut)
check("no room left after the join: the next item is not read at all (never an empty cut counted)",
      full.items_used == 1 and not full.last_cut and full.tokens == 6999 and full.text == words(6999, "h"),
      str(full.tokens))
check("the offered count is recorded", ctx.items_offered == 4 and small.items_offered == 3)
check("an empty list is an empty context", PT.fill([], count=count, cut=cut) == PT.Context("", 0, 0, 0, False))
check("a non-text item refuses by name", raises(lambda: PT.fill(["a", 3], count=count, cut=cut), PT.PointError, "text"))


def merging_count(text: str) -> int:
    """A tokenizer whose join merges: a newline followed by 'q' costs one token more - a cost the room computed on
    the prefix alone cannot see, so the first cut is one token too long."""
    return count(text) + text.count("\nq")


try:
    m = PT.fill([words(3499, "p") + " x", words(4000, "q")], count=merging_count, cut=cut)
except PT.PointError as e:
    m = PT.Context(f"refused: {e}", -1, 0, 0, False)
check("a join that costs more than the cut assumed is re-cut until the whole fits",
      m.tokens == 7000 and m.last_cut and merging_count(m.text) == m.tokens and m.text.endswith(" q3497"),
      f"{m.tokens} {m.text[-12:]!r}")


check("a cut that returns something other than a prefix of the item refuses",
      raises(lambda: PT.fill([words(8000, "w")], count=count, cut=lambda t, n: "other " * n), PT.PointError, "prefix"))
first_cut = {"done": False}


def stubborn_cut(text: str, n: int) -> str:
    """Honours the first cut, then hands the text back unchanged."""
    if first_cut["done"]:
        return text
    first_cut["done"] = True
    return cut(text, n)


check("a re-cut that does not shorten refuses instead of looping",
      raises(lambda: PT.fill([words(3499, "p") + " x", words(4000, "q")], count=merging_count, cut=stubborn_cut),
             PT.PointError, "shorter"))
calls = {"n": 0}


def drifting_count(text: str) -> int:
    calls["n"] += 1
    return count(text) + (50 if calls["n"] > 3 else 0)


check("a context over the budget is never returned (a tokenizer that disagrees with itself refuses)",
      raises(lambda: PT.fill([words(3000, "a"), words(3990, "b"), "c"], count=drifting_count, cut=cut),
             PT.PointError, "over the budget"))

print("\n- k at the points -")
check("B asks every arm for 200, K for 10", PT.k_for("B", "mem0") == 200 and PT.k_for("K", "letta") == 10
      and PT.k_for("B", "claude-code-memory") == 200)
check("claude-code-memory has no k at K (competitor-lacks-capability:k)",
      raises(lambda: PT.k_for("K", "claude-code-memory"), PT.PointError, "competitor-lacks-capability:k"))
check("V has no k here (each arm's vendor default)", raises(lambda: PT.k_for("V", "mem0"), PT.PointError))

print("\n- Claude Code: R-all and R-index -")
TMP = Path(tempfile.mkdtemp(prefix="v3points_"))
try:
    md = TMP / "memory"
    (md / "topics" / "deep").mkdir(parents=True)
    (md / "MEMORY.md").write_bytes("# index\n- see topics\n".encode())
    (md / "b_topic.md").write_bytes(b"b file")
    (md / "Alpha.md").write_bytes(b"Alpha file")          # sorts before MEMORY.md: the index is first all the same
    (md / "a_topic.md").write_bytes(b"a file")
    (md / "topics" / "c.md").write_bytes(b"c nested")
    (md / "topics" / "deep" / "a.md").write_bytes(b"deep a")
    got = PT.claude_r_all(md)
    check("R-all: MEMORY.md first, then every other file by lexicographic relative path (Q28), nested ones included",
          got == ["# index\n- see topics\n", "Alpha file", "a file", "b file", "c nested", "deep a"], str(got))
    (md / "MEMORY.md").unlink()
    check("without MEMORY.md, R-all is the other files in the same order",
          PT.claude_r_all(md) == ["Alpha file", "a file", "b file", "c nested", "deep a"])
    check("without MEMORY.md, R-index is empty", PT.claude_r_index(md) == "")
    lines = "".join(f"line {i}\n" for i in range(300))
    (md / "MEMORY.md").write_bytes(lines.encode())
    idx = PT.claude_r_index(md)
    check("R-index: the first 200 lines when they are under 25 KB", idx == "".join(f"line {i}\n" for i in range(200)))
    big = "".join(("é" * 200) + "\n" for _ in range(150))            # 150 lines of ~401 bytes: 25 KB ends first
    (md / "MEMORY.md").write_bytes(big.encode())
    idx = PT.claude_r_index(md)
    raw = idx.encode("utf-8")
    check("R-index: 25 KB when that ends first, never half a UTF-8 character",
          len(raw) <= 25 * 1024 and len(raw) >= 25 * 1024 - 1 and big.startswith(idx), str(len(raw)))
    (md / "bad.md").write_bytes(b"\xff\xfe not utf-8")
    check("a file that is not UTF-8 refuses by name",
          raises(lambda: PT.claude_r_all(md), PT.PointError, "bad.md"))
    (md / "bad.md").unlink()
    target = TMP / "outside.md"
    target.write_bytes(b"outside the memory directory")
    try:
        os.symlink(target, md / "link.md")
        linked = True
    except (OSError, NotImplementedError):
        linked = False
    if linked:
        check("a symlink in the memory directory refuses by name (never followed out)",
              raises(lambda: PT.claude_r_all(md), PT.PointError, "link.md"))
        (md / "link.md").unlink()
    else:
        print("  skip the symlink row: this account cannot create symlinks (not counted)")
    check("a missing memory directory refuses", raises(lambda: PT.claude_r_all(TMP / "nope"), PT.PointError))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 points: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
