"""Build an LLM client from settings."""

from __future__ import annotations

from esglocal.config import Settings
from esglocal.llm.base import LLMError

# Suggested models for first-time users. Any local chat model works; models
# with native tool calling give the best agent experience.
RECOMMENDED_MODELS = [
    {"name": "qwen2.5:7b", "size": "~4.7 GB", "use": "Balanced default, good tool calling"},
    {"name": "llama3.1:8b", "size": "~4.9 GB", "use": "General purpose, tool calling"},
    {"name": "llama3.2:3b", "size": "~2.0 GB", "use": "Low-RAM laptops"},
    {"name": "mistral-nemo:12b", "size": "~7.1 GB", "use": "Longer context"},
    {"name": "qwen2.5:14b", "size": "~9.0 GB", "use": "Higher quality reports (16 GB+ RAM)"},
    {"name": "nomic-embed-text", "size": "~0.3 GB", "use": "Embeddings for documents & narratives"},
]


def create_client(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    settings.validate()
    if settings.provider == "fake":
        from esglocal.llm.fake import FakeLLM

        return FakeLLM()
    if settings.provider == "openai_compat":
        from esglocal.llm.openai_compat import OpenAICompatClient

        return OpenAICompatClient(
            base_url=settings.base_url,
            model=settings.chat_model,
            temperature=settings.temperature,
            timeout=settings.request_timeout,
        )
    if settings.provider == "ollama":
        from esglocal.llm.ollama import OllamaClient

        client = OllamaClient(
            base_url=settings.base_url,
            model=settings.chat_model,
            temperature=settings.temperature,
            num_ctx=settings.num_ctx,
            timeout=settings.request_timeout,
            think=settings.think,
        )
        if not client.model:
            try:
                client.model = client.default_chat_model()
            except LLMError:
                pass  # runtime not up yet — surfaced by health()
        return client
    raise LLMError(f"Unknown provider: {settings.provider}")
