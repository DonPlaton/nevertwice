# The defect list, and D5 does not run until it is empty

**Phase D.** Rule 1 of `.loop/GOAL-INVARIANTS.md` §0: *a defect that is understood and unfixed is
a reason to fix it, never a reason to discount the result it produces.* The previous run
quantified five false-positive classes, left three of them in the code, and then reported the
number those defects produced as a verdict on the idea.

This page is that list, with its state. **D5 is blocked while any row is open.**

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
| 5 | **moved and still resolves** — the facade shapes I2 did not solve | 6 of 24 (25%) | D4 | open |

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

## 4 · The remaining facade shapes — open, D4

I2 solved one shape, `write_atomic = _store_state.write_atomic`, which is this repository's own
idiom. The census names two it does not solve: a module attribute (`_st.est_tokens(...)`) where
the module imports the name back, and an import that reaches past the module the symbol left.

The census write-up says there is *"no reason to believe the list ends"*, so D4 enumerates the
shapes **from the corpus** rather than from this repository's habits — eight repositories with
between 270 and 3,638 contributors are a better source of idioms than one author.

There is already a warning from Phase C worth carrying: the **answer key had this same blind
spot** and CPython's binder found it — a definition that left a module and returned as
`from x import y as name`. If the shape can hide in a 200-line instrument written to avoid it, it
can hide in the checker.
