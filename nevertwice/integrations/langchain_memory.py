#!/usr/bin/env python3
"""LangChain integration for Nevertwice - a retriever and a chat-memory helper.

    from nevertwice.integrations.langchain_memory import NevertwiceRetriever
    retriever = NevertwiceRetriever(project="myproj", k=5)
    docs = retriever.invoke("how did we fix the OOM crash")        # → list[Document]

    from nevertwice.integrations.langchain_memory import NevertwiceMemory
    memory = NevertwiceMemory(project="myproj", memory_key="history")
    memory.load_memory_variables({"input": "..."})  # inject relevant past lessons
    memory.save_context({"input": "..."}, {"output": "..."})       # collect the exchange
    memory.flush()                                                  # extract durable lessons

`NevertwiceRetriever` is a real LangChain `BaseRetriever` (needs `pip install
nevertwice-memory[langchain]`). `NevertwiceMemory` implements the classic memory-variables
protocol as a **plain class** - LangChain 1.x removed `BaseMemory` from `langchain-core`,
so subclassing it would break on the very version the extra installs; a plain class with
the same methods works on every version and needs no framework at all. Reads/writes go
through `nevertwice.api`, so this adds nothing to the stdlib core.
"""
import sys
from pathlib import Path

try:                                  # installed package - proper relative imports (no sys.path/shadowing)
    from .. import api as _api
    from ..capture import MemorySession
except ImportError:                   # flat-script use - fall back to a path insert
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import api as _api
    from capture import MemorySession

INSTALL_HINT = "LangChain not installed - `pip install nevertwice-memory[langchain]`"

try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    _HAVE_LC = True
except Exception:  # pragma: no cover - exercised only without langchain
    _HAVE_LC = False


# ── framework-free mapping (unit-tested without LangChain) ──────────────────────

def recall_to_documents(results: list[dict]) -> list[dict]:
    """recall() dicts → [{page_content, metadata}] ready to build LangChain Documents."""
    out = []
    for r in results:
        out.append({
            "page_content": _api.format_note(r),
            "metadata": {"ntype": r.get("ntype"), "project": r.get("project"),
                         "stem": r.get("stem"), "score": r.get("score"),
                         "title": r.get("title"), "source": "nevertwice"},
        })
    return out


def format_memory(results: list[dict], header: str = "Relevant memory from past sessions:") -> str:
    """Render recall results as a single string for a chat-memory variable."""
    if not results:
        return ""
    body = "\n\n".join(f"- {_api.format_note(r)}" for r in results)
    return f"{header}\n{body}"


# ── LangChain retriever (real BaseRetriever) ────────────────────────────────────

if _HAVE_LC:
    class NevertwiceRetriever(BaseRetriever):
        """A LangChain Retriever backed by Nevertwice recall. Drop into any chain or
        RAG pipeline; returns Documents whose metadata carries the note's type/project/stem."""
        project: str | None = None
        k: int = 5
        rerank: bool = False

        def _get_relevant_documents(self, query: str, *, run_manager=None, **kwargs):
            results = _api.recall(query, self.project, self.k, rerank=self.rerank)
            return [Document(page_content=d["page_content"], metadata=d["metadata"])
                    for d in recall_to_documents(results)]

        async def _aget_relevant_documents(self, query: str, *, run_manager=None, **kwargs):
            # recall does blocking network I/O (embed) - run it off the event loop so an
            # async LangChain pipeline is not stalled for up to EMBED_TIMEOUT per call
            import asyncio
            return await asyncio.to_thread(self._get_relevant_documents, query)
else:  # pragma: no cover
    class NevertwiceRetriever:
        def __init__(self, *a, **k):
            raise ImportError(INSTALL_HINT)


# ── chat-memory helper (plain class - version-proof, no framework needed) ────────

class NevertwiceMemory:
    """The classic LangChain memory-variables interface as a plain, dependency-free
    class. `load_memory_variables` injects relevant past lessons into the prompt;
    `save_context` collects the exchange (cheap) so a later `flush()` extracts durable
    lessons via the full pipeline (no per-turn LLM cost). Works on every LangChain
    version - including 1.x, where `BaseMemory` was removed from langchain-core.

    NOTE: it *duck-types* the protocol but is NOT a `BaseMemory` subclass, so it can't be
    passed where a component does `isinstance(memory, BaseMemory)` (legacy chains may). For
    modern LCEL pipelines, prefer `NevertwiceRetriever`. Call `flush()`/`clear()` at each
    conversation boundary - `flush()` extracts then resets; without it turns accumulate."""

    def __init__(self, project: str | None = None, agent: str | None = None, k: int = 5,
                 memory_key: str = "history", input_key: str = "input",
                 output_key: str | None = None, collect: bool = True):
        self.project = project
        self.agent = agent
        self.k = k
        self.memory_key = memory_key
        self.input_key = input_key
        self.output_key = output_key
        self.collect = collect
        self._session = MemorySession(project=project, agent=agent, extract=False)

    @property
    def memory_variables(self) -> list[str]:
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict) -> dict:
        q = inputs.get(self.input_key, "") if isinstance(inputs, dict) else str(inputs)
        return {self.memory_key: format_memory(_api.recall(q, self.project, self.k))}

    def save_context(self, inputs: dict, outputs: dict) -> None:
        if not self.collect:
            return
        i = inputs.get(self.input_key) if isinstance(inputs, dict) else inputs
        if isinstance(outputs, dict):
            o = outputs.get(self.output_key) if self.output_key else (
                next(iter(outputs.values()), "") if outputs else "")
        else:
            o = outputs
        self._session.log_user(i).log_assistant(o)

    def flush(self) -> dict:
        """Extract durable lessons from everything collected so far."""
        return self._session.flush()

    def clear(self) -> None:
        self._session = MemorySession(project=self.project, agent=self.agent, extract=False)
