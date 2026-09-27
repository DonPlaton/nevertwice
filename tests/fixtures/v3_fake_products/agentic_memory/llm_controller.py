"""The FAKE agentic_memory.llm_controller, shaped as the pin's: OpenAIController -> openai.OpenAI (base URL and key from
the environment) with response_format and temperature 0.7; OllamaController -> litellm.completion("ollama_chat/<m>")
with response_format and NO temperature (the pin drops it, llm_controller.py:71-80)."""
from __future__ import annotations


class OpenAIController:
    def __init__(self, model: str = "gpt-4", api_key: str | None = None) -> None:
        from openai import OpenAI  # noqa: PLC0415
        self.model = model
        self.client = OpenAI(**({"api_key": api_key} if api_key else {}))

    def get_completion(self, prompt: str, response_format: dict, temperature: float = 0.7) -> str:
        r = self.client.chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}],
                                                response_format=response_format, temperature=temperature)
        return r.choices[0].message.content


class OllamaController:
    def __init__(self, model: str = "llama2") -> None:
        from ollama import chat  # noqa: F401,PLC0415 - the pin imports it at init
        self.model = model

    def get_completion(self, prompt: str, response_format: dict, temperature: float = 0.7) -> str:
        from litellm import completion  # noqa: PLC0415
        r = completion(model=f"ollama_chat/{self.model}", messages=[{"role": "user", "content": prompt}],
                       response_format=response_format)
        return r.choices[0].message.content


class LLMController:
    def __init__(self, backend: str = "openai", model: str = "gpt-4", api_key: str | None = None) -> None:
        if backend == "openai":
            self.llm = OpenAIController(model, api_key)
        elif backend == "ollama":
            self.llm = OllamaController(model)
        else:
            raise ValueError("Backend must be one of: 'openai', 'ollama'")

    def get_completion(self, prompt: str, response_format: dict | None = None, temperature: float = 0.7) -> str:
        return self.llm.get_completion(prompt, response_format, temperature)
