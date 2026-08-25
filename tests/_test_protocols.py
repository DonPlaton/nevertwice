#!/usr/bin/env python3
"""Five seams, and the promise that filling one costs nothing but `protocols.py`.

Every extension point in this system used to mean importing `memory_hook`: 6,000 lines that
resolve a vault path at import time, read config, and pull in half the engine. So "swap the
store" or "add my agent" was not an afternoon's work, it was a decision to depend on the whole
project. GOAL D8's exit criterion is exactly that promise - *a third-party store and a
third-party host can be added without importing `memory_hook`* - and this suite refuses to take
it on trust.

**The central check runs in a subprocess.** A third-party store and a third-party episode
source are written into a temporary file that imports `protocols` and nothing else, they are
registered and driven, and then the child asserts `memory_hook` never reached `sys.modules`.
Checking that in *this* process would prove nothing: the suite imports the engine itself, so
`memory_hook` is already loaded and every assertion would pass for free.

The rest holds the parts that are easy to claim and hard to keep:

* `conforms()` catches what `isinstance()` cannot - a right-named method with a wrong signature,
  and a non-callable attribute, both of which satisfy a `runtime_checkable` Protocol and fail
  at the first call;
* the shipped engine actually *satisfies* the protocols it publishes, because a plugin surface
  no shipped component fits is a description of an intention;
* registering over an existing provider requires saying so, since silently shadowing one is how
  two plugins both believe they are the store.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import protocols as P           # noqa: E402
import schemas                  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# --------------------------------------------------- the declared surface


def test_the_surface_is_five_seams_and_nothing_from_the_engine() -> None:
    print("\n- five protocols, declared without the engine -")
    check("five protocols are declared", len(P.PROTOCOLS) == 5, str(sorted(P.PROTOCOLS)))
    check("they are the five D8 names",
          set(P.PROTOCOLS) == {"MemoryStore", "Retriever", "Extractor", "EpisodeSource",
                               "InterventionSink"}, str(sorted(P.PROTOCOLS)))
    check("every protocol declares its required members",
          set(P.REQUIRED) == set(P.PROTOCOLS) and all(P.REQUIRED.values()),
          str({k: sorted(v) for k, v in P.REQUIRED.items()}))

    # REQUIRED must match the methods the Protocol actually declares. Without this the contract
    # could quietly shrink - drop a member from REQUIRED and every half-implementation starts
    # "conforming" to a weaker protocol, with no error anywhere. A mutation doing exactly that
    # survived until this check existed.
    for name, protocol in P.PROTOCOLS.items():
        declared = {m for m in vars(protocol)
                    if not m.startswith("_") and callable(vars(protocol)[m])}
        check(f"{name}'s required members are exactly the ones it declares",
              declared == set(P.REQUIRED[name]),
              f"declared {sorted(declared)} vs required {sorted(P.REQUIRED[name])}")
    check("every protocol is exported", set(P.PROTOCOLS) <= set(P.__all__))

    # Checked against the AST, not the text: the module's own docstring explains *why* it does
    # not import memory_hook, and a substring search cannot tell an explanation from an import.
    import ast
    tree = ast.parse((ROOT / "nevertwice" / "protocols.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("the module imports only the standard library",
          imported <= {"__future__", "inspect", "typing"}, str(sorted(imported)))
    for engine in ("memory_hook", "api", "config", "guards", "hosts"):
        check(f"the module does not import {engine}", engine not in imported,
              "a plugin surface that imports the engine is not a plugin surface")


def test_conforms_catches_what_isinstance_cannot() -> None:
    print("\n- the two gaps a runtime_checkable Protocol leaves open -")

    class WrongSignature:
        def search(self):                      # right name, cannot take a query
            return []

    class NotCallable:
        search = "not a function"

    class Fits:
        def search(self, query, *, project=None, k=5):
            return []

    wrong, uncallable, fits = WrongSignature(), NotCallable(), Fits()

    check("isinstance passes a wrong signature", isinstance(wrong, P.Retriever),
          "if this is False the gap has closed and this check can go")
    check("conforms catches it", bool(P.conforms(wrong, "Retriever")),
          "a method that cannot be called the way the engine calls it")
    check("and says why", "cannot be called" in P.conforms(wrong, "Retriever")[0],
          str(P.conforms(wrong, "Retriever")))

    check("isinstance passes a non-callable attribute", isinstance(uncallable, P.Retriever))
    check("conforms catches that too",
          "not callable" in (P.conforms(uncallable, "Retriever") or [""])[0],
          str(P.conforms(uncallable, "Retriever")))

    check("a real implementation fits", P.conforms(fits, "Retriever") == [])
    check("a missing member is named", P.conforms(object(), "MemoryStore") ==
          ["missing write()", "missing read()", "missing all()", "missing delete()"],
          str(P.conforms(object(), "MemoryStore")))
    check("an unknown protocol is refused rather than silently passing",
          bool(P.conforms(fits, "Nonsense"))
          and "unknown protocol" in P.conforms(fits, "Nonsense")[0])
    check("implements() lists every seam an object fills",
          P.implements(fits) == ["Retriever"], str(P.implements(fits)))


def test_the_shipped_engine_satisfies_its_own_protocols() -> None:
    """A plugin surface that no shipped component fits is a description of an intention."""
    print("\n- the engine fits the seams it publishes -")
    import hosts                                       # noqa: E402 - engine side, on purpose
    for adapter in hosts.registry():
        problems = P.conforms(adapter, "EpisodeSource")
        check(f"the {adapter.name} host adapter is an EpisodeSource", not problems,
              "; ".join(problems))

    import memory_search                                # noqa: E402

    class ShippedRetriever:
        """The shipped search behind the protocol's shape - the adapter a caller would write."""
        def search(self, query, *, project=None, k=5):
            results, _mode = memory_search.search_core(query, project, k)
            return results

    check("the shipped search fits Retriever",
          P.conforms(ShippedRetriever(), "Retriever") == [],
          str(P.conforms(ShippedRetriever(), "Retriever")))


def test_the_registry_refuses_to_shadow_silently() -> None:
    print("\n- two plugins cannot both quietly be the store -")

    class A:
        def write(self, note): return "a"
        def read(self, stem): return None
        def all(self, project=None): return []
        def delete(self, stem): return False

    try:
        P.register("MemoryStore", "suite-a", A)
        check("a provider registers", P.get("MemoryStore", "suite-a") is A)
        check("it is listed", "suite-a" in P.providers("MemoryStore")["MemoryStore"])

        shadowed = False
        try:
            P.register("MemoryStore", "suite-a", object)
        except ValueError as exc:
            shadowed = "already registered" in str(exc)
        check("registering over it is refused by default", shadowed,
              "silently shadowing a provider is how the loser finds out in production")

        P.register("MemoryStore", "suite-a", object, replace=True)
        check("overriding works when it is said out loud",
              P.get("MemoryStore", "suite-a") is object)

        bad_protocol = False
        try:
            P.register("Nonsense", "x", object)
        except ValueError:
            bad_protocol = True
        check("an unknown protocol is refused", bad_protocol)

        no_name = False
        try:
            P.register("MemoryStore", "  ", object)
        except ValueError:
            no_name = True
        check("a nameless provider is refused", no_name)
    finally:
        P.unregister("MemoryStore", "suite-a")
    check("unregistering removes it",
          P.get("MemoryStore", "suite-a") is None)
    check("providers() covers every protocol", set(P.providers()) == set(P.PROTOCOLS))


# ------------------------------------- the exit criterion, in a subprocess


THIRD_PARTY = '''
"""A third party's store and host. Imports `protocols` and `schemas` - nothing else of ours.

If this file needed `memory_hook`, D8 would be false: filling a seam would mean depending on
the whole engine, which is the coupling the protocols exist to break.
"""
import json, sys

sys.path.insert(0, PKG)
import protocols as P
import schemas

class DictStore:
    """A whole MemoryStore in fifteen lines, backed by nothing."""
    def __init__(self): self._notes = {}
    def write(self, note):
        stem = note["stem"]; self._notes[stem] = dict(note); return stem
    def read(self, stem): return self._notes.get(stem)
    def all(self, project=None):
        return [n for n in self._notes.values()
                if project is None or n.get("project") == project]
    def delete(self, stem): return self._notes.pop(stem, None) is not None

class SlackSource:
    """An EpisodeSource for a host this project has never heard of."""
    def discover(self): return ["#engineering"]
    def read(self, cursor=None):
        raw = json.dumps({"ts": "1", "user": "u1", "text": "the deploy failed again"})
        return {"events": self.normalize(raw), "cursor": {"#engineering": "1"}}
    def normalize(self, raw, *, source=None):
        msg = json.loads(raw)
        return [{"hook_event_name": "UserPromptSubmit",
                 "session_id": msg["ts"], "prompt": msg["text"]}]

store, host = DictStore(), SlackSource()

problems = {"MemoryStore": P.conforms(store, "MemoryStore"),
            "EpisodeSource": P.conforms(host, "EpisodeSource")}

P.register("MemoryStore", "dict", DictStore)
P.register("EpisodeSource", "slack", SlackSource)

note = {"stem": "2026-08-25-acme-mistake-deploy", "ntype": "mistake",
        "title": "The deploy failed", "project": "acme"}
stem = store.write(note)
events = host.read()["events"]

print(json.dumps({
    "problems": problems,
    "wrote": stem,
    "read_back": store.read(stem) == note,
    "listed": len(list(store.all("acme"))),
    "deleted": store.delete(stem),
    "gone": store.read(stem) is None,
    "events": events,
    "events_conform": [schemas.conforms(e, "EpisodeEvent") for e in events],
    "registered": P.providers(),
    "engine_modules": sorted(m for m in sys.modules
                             if m in ("memory_hook", "api", "config", "guards", "hosts")),
}))
'''


def test_a_third_party_needs_nothing_but_this_file() -> None:
    """GOAL D8's exit criterion, proven where it can actually be proven.

    In-process this would be meaningless: the suite has already imported the engine, so
    `memory_hook` is in `sys.modules` no matter what the child code does.
    """
    print("\n- a third-party store and host, with the engine never imported -")
    import json

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "third_party.py"
        script.write_text(f"PKG = {str(ROOT / 'nevertwice')!r}\n" + THIRD_PARTY,
                          encoding="utf-8")
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    check("the third-party module runs", proc.returncode == 0,
          (proc.stdout + proc.stderr)[-400:].replace("\n", " | "))
    if proc.returncode != 0:
        return
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    check("THE EXIT CRITERION: the engine was never imported",
          result["engine_modules"] == [],
          f"a third party pulled in {result['engine_modules']}")

    check("the third-party store conforms", result["problems"]["MemoryStore"] == [],
          str(result["problems"]["MemoryStore"]))
    check("the third-party host conforms", result["problems"]["EpisodeSource"] == [],
          str(result["problems"]["EpisodeSource"]))

    check("the store wrote and read back", result["read_back"], str(result))
    check("it listed by project", result["listed"] == 1, str(result["listed"]))
    check("it deleted", result["deleted"] and result["gone"], str(result))

    check("the host produced events", len(result["events"]) == 1, str(result["events"]))
    check("its events conform to the declared episode shape",
          all(p == [] for p in result["events_conform"]), str(result["events_conform"]))
    check("both plugins registered",
          result["registered"]["MemoryStore"] == ["dict"]
          and result["registered"]["EpisodeSource"] == ["slack"],
          str(result["registered"]))


def main() -> int:
    for fn in (test_the_surface_is_five_seams_and_nothing_from_the_engine,
               test_conforms_catches_what_isinstance_cannot,
               test_the_shipped_engine_satisfies_its_own_protocols,
               test_the_registry_refuses_to_shadow_silently,
               test_a_third_party_needs_nothing_but_this_file):
        fn()
    print(f"\nprotocols: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
