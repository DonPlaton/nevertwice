"""Optional framework integrations for Nevertwice.

Each submodule wraps the stdlib-only `nevertwice.api` for a specific framework and is
imported explicitly so the package never hard-depends on a framework:

    from nevertwice.integrations.langchain_memory import NevertwiceRetriever, NevertwiceMemory
    from nevertwice.integrations.llamaindex_retriever import NevertwiceRetriever

Install the extra you need:  pip install nevertwice-memory[langchain]  (or [llamaindex]).
The adapters add memory to an existing agent without touching the stdlib core.
"""
