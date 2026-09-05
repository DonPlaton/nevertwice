# The defect list, and D5 does not run until it is empty

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](../evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

**Phase D.** Rule 1 of `.loop/GOAL-INVARIANTS.md` §0: *a defect that is understood and unfixed is
a reason to fix it, never a reason to discount the result it produces.* The previous run
quantified five false-positive classes, left three of them in the code, and then reported the
number those defects produced as a verdict on the idea.

This page is that list, with its state. **D5 is blocked while any row is open.**

**The list is empty.** Eight rows: five inherited from the census, and **three the corpus produced
that 150 commits of a single repository could never have shown.** D5 ran and its result is in
[`BLAST_RADIUS_D5.md`](BLAST_RADIUS_D5.md).

```bash
python tests/_test_blast_radius.py     # every row below has its regression here
```

Skill used: `test-driven-development` — each row's regression was written and **watched fail**
before its fix existed.

---

## The list

| # | class | share of the old census | task | state |
|---|---|---:|---|---|
| 1 | **widened signature** — a defaulted or keyword-only parameter appeared | 8 of 24 (33%) | D1 | **closed** |
| 2 | **rebound by tuple unpacking** — `A, B, C = load()` deletes three symbols as far as the extractor knows | 5 of 24 (21%) | D2 | **closed** |
| 3 | **annotation-only** — types added, runtime identical | 4 of 24 (17%) | D3 | **closed** |
| 4 | **class gained a member** — a member list growing is what a compatible change looks like | 1 of 24 (4%) | D3 | **closed** |
| 5 | **moved and still resolves** — the facade shapes I2 did not solve | 6 of 24 (25%) | D4 | **closed** |
| 6 | **foreign receiver** — a method contract change matched against an attribute call on a *different* class | not in the old census | D4b | **closed** |
| 7 | **an unreadable file read as a file full of deletions** — an empty parse makes every symbol look removed | not in the old census | D6 | **closed** |
| 8 | **quadratic diff** — `difflib` on repeated lines; a 6 MB file took the suite from 2 s to over 2 min | not in the old census | D7 | **closed** |

Shares are from `research/blast_radius_precision.json`, the artifact of the run being replaced.

---

## 1 · Widened signatures — closed by D1

**The defect.** `contract_changes` compared the *rendered signature text*. Two signatures whose
text differs can accept exactly the same calls, and comparing text cannot tell the difference.
`f(a)` becoming `f(a, b=2)` reads as a change and is not one.

**The fix.** `Symbol` now carries a `Shape` — the parameter list as `(name, kind, has_default)`
plus the decorator list — alongside the rendered text, and `accepts_everything(old, new)` decides
whether every call the old signature accepted still binds. A signature difference that is a
widening is recorded as an **implementation change**, not a contract change, so no references are
chased for it.

The predicate is a conjunction of the ways a caller can be written, and each clause is a way it
can be false:

| a caller who wrote… | breaks when… |
|---|---|
| `f(1, 2)` — positional | the parameter at that index disappears with no `*args` |
| `f(a=1)` — by keyword | that parameter is renamed, reordered, or becomes positional-only |
| `f(1)` — omitting a defaulted parameter | that parameter loses its default |
| `f(1)` — knowing nothing of a new parameter | the new parameter is required |
| `f(**opts)` | `**kwargs` disappears |
| anything at all | the decorator list changes — a decorator can change what the call returns without touching a parameter |

**18 regressions**, in `WidenedSignatures`, split evenly. **Eight were watched failing** before
the fix existed — the widenings — and a ninth pins that a widening whose body also changed is
recorded as an implementation change rather than silently dropped. The other **nine must keep
firing**: a new required parameter, a parameter disappearing, a required keyword-only appearing, a
lost default, a rename, a reorder, a promotion to keyword-only, a promotion to positional-only,
and a decorator appearing.

That the halves are equal is the point. A fix for a false-positive class can always be made to
work by weakening the checker until it says nothing — the old census found that removing all three
easy classes by suppression leaves **zero findings on 150 commits**. Half of D1's regressions
exist to make that failure mode visible if it is ever reintroduced.

---

## 2 · Tuple-unpacking rebinds — closed by D2

**The defect.** `visit_Assign` recorded only `ast.Name` targets, so `A, B, C = load()` bound
nothing as far as the extractor was concerned, and the three names looked **removed** at the next
commit that kept them.

**The fix.** `_Collector._bind` walks the target recursively — tuples, lists and starred targets —
and stops at `Attribute` and `Subscript` targets, because `obj.x = 1` and `d[k] = 1` bind nothing
a caller can import.

**10 regressions**, in `TupleUnpackingRebinds`. **Five were watched failing**: a tuple target, a
list target, a starred target, a nested tuple, and losing one name from a tuple. The rest hold the
line in the other direction — a chained assignment binds both names, a tuple *inside a function*
is still not a module symbol, and unchanged tuple bindings chase no references end to end.

The answer key never had this hole: `sigscan._bind` walked tuples from its first line of code,
deliberately, so D2's defect lived in one instrument rather than both.

## 3 · Annotation-only changes, and a class gaining a member — closed by D3

Two shapes of one mistake: the text of the contract changed and its runtime meaning did not.

**The prediction, made in writing before the regressions ran, was half right.** D1's `Shape`
ignores annotations by construction, so five of the seven annotation cases passed the moment they
were written, as did a class gaining a *private* method — `_class_signature` already filtered
underscore-prefixed names. Only two of fourteen were **watched failing**, both the class-gained-a-
*public*-member case.

**The fix.** A `ClassShape` — bases, decorators, and the public member set — beside the rendered
text, and `still_offers(old, new)` returns true when the bases and decorators match and the member
set only grew. A member *disappearing* is still reported, and separately as that member's own
removal, so nothing is lost by the class-level rule.

Bases stay part of the shape on purpose: `isinstance` and the MRO are observable, so widening a
base list is not free the way adding a method is. Four of the fourteen regressions pin exactly
that — a changed base, a gained base, a gained decorator, and a lost public method must all still
fire.

## 4 · The remaining facade shapes — closed by D4

The census names two shapes I2 does not solve and adds that there is *"no reason to believe the
list ends"*. Guessing the third from this repository's habits is how the list stayed short the
first time, so `facade_shapes.py` reads the shapes off **1,118 corpus commits** instead.

**Both named shapes already passed** the moment their regressions were written — I2's
`from X import name` branch covers them. Two others did not, and both are corpus idioms:

| shape | why it failed |
|---|---|
| `from nodes import math_reference as eqref` | the facade **renames**. `resolve_facades` compared the target's *rendered signature text* against the old one, and `math_reference(node)` is not the string `eqref(node)`, so a pure move read as a signature change. It now compares **shapes** — does every call that worked before still bind — which is the question callers actually ask. This is D1's mistake in a second place |
| `try: from fast import helper / except ImportError: from slow import helper` | `find_reexports` walked `tree.body` only, so the optional-dependency idiom was invisible. `_module_level` now descends into `if` and `try`. **The census already found this exact defect once**, in `_module_aliases`, fixed it there, and left it here |

**7 regressions**, in `RemainingFacadeShapes`. Two were watched failing; five hold the line —
including that a symbol which really left with no facade is still a removal, and that a facade
whose target *changed signature* is still a change, because following the pointer is the point.

**The enumeration then found nothing left.** Over 1,118 commits the checker reported
**5,565 removals** and **0 of them are false** — no symbol reported
removed still resolves in its own module.

## 4b · Foreign receivers — a class the old census could not have seen

The removal side was clean; the **reference** side was not. The same enumeration asks a second
question: of the references the checker reports as unhandled, how many are calls that bind
perfectly well? At the time it was first run, **1,092 of 1,299** decidable false findings were one
shape:

```
EmailBackend.__init__ changed  ->  MIMEText.__init__(self, _text, ...)   reported as stale
```

A method contract change matched against an attribute call on a **different class**, purely on the
shared short name. 150 commits of one disciplined repository contained no collision large enough
to notice; eight repositories with between 270 and 3,638 contributors contain thousands.

**The fix** is narrow on purpose. An attribute call `X.member(...)` is dropped only when `X` is a
name **this file binds** to a class or a module and is not the owner of the changed member. A
receiver the file does not bind — a parameter, a local, an attribute chain, `self`, `cls` — stays
a candidate, because discarding those would buy precision with recall. **7 regressions**, three
watched failing, four holding the other direction.

## 5 · What is left is the design, not a defect — and it is declared before D5 runs

After all six rows, **67%** of the checker's decidable high-confidence findings are still calls that
bind: `self.get_connection(fail_silently)` where `get_connection` grew a parameter that this
caller does not pass.

That is not a bug. The mechanism is specified as *"who depends on the changed contract"* — a
**dependency** reporter. `PREREGISTRATION.md` defines a TRUE finding as *"a call that cannot bind
or an import that cannot resolve"* — a **breakage**. The gap between those two questions is the
mechanism's honest precision, and closing it by teaching the checker to decide bindability would
give it the answer key's own rule, making the precision gate measure agreement with itself.

So the primary measurement runs the mechanism **as specified**, and the compatibility filter is
declared as a labelled **exploratory secondary arm** in `PREREGISTRATION.md` §9 — before D5 runs,
with the circularity stated, rather than discovered as a convenient improvement afterwards.


## 6 · An unreadable file is not a file full of deletions — closed by D6

**The defect.** `extract_symbols` returns an empty map on a `SyntaxError`. Safe on its own,
catastrophic in a diff: an empty *after* makes every symbol the *before* defined look removed. One
`flask` commit that reintroduced a Python-2 `print` statement produced **30 findings** this way,
for symbols still defined three lines below.

**5.43% of this corpus cannot be parsed by Python 3.14**, so on a history reaching back to 2005
this is not an edge case — it is a steady source of confident nonsense.

**The fix.** `contract_changes` yields nothing when either side fails to parse, and `check_sources`
**says so in a note**. Silence nobody can see is indistinguishable from a clean bill.

**The answer key had the identical hole** and is fixed the same way, with its own five regressions
in `tests/_test_invariants_lab.py`. Two instruments, one blind spot, found because the silence pool
disagreed with itself.

## 7 · A big diff is not a hang — closed by D7

**The defect.** `changed_lines` runs `difflib.SequenceMatcher(autojunk=False)`, which is quadratic
in the **multiplicity of repeated lines**, not in file length. 40,000 lines of distinct source diff
in 0.04 s; 8,000 lines of pretty-printed JSON take 5 s; 185,000 take minutes. The spec promises a
**2.00 s hard ceiling**.

It stayed invisible until a 6 MB research artifact landed in the working tree and the checker's own
test suite went from 2 seconds to over two minutes.

**The fix** guards on repetition as well as length, because length alone is the wrong measure: a
hard ceiling at 50,000 lines — the longest module in this repository is 6,333 — and a repetition
test above 2,000 lines. A regression asserts that this repository's longest module is still diffed
**precisely**, so the guard cannot quietly grow into source code.

Not a correctness defect. A usability one, and a checker nobody can run is a checker nobody
measures.
