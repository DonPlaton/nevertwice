"""A FAKE ollama for the v3 adapter suite: only the `chat` name A-mem's OllamaController imports at init."""
__version__ = "0.0.0-fake"


def chat(*a, **k):
    raise RuntimeError("the fake ollama.chat is never called: A-mem's OllamaController completes through litellm")
