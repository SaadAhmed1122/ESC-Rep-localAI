"""Provider-neutral LLM interface.

Internal message format (OpenAI-like, provider adapters convert as needed)::

    {"role": "system" | "user" | "assistant", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Tool spec: ``{"name": str, "description": str, "parameters": <JSON schema>}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """The local model runtime is unreachable or returned an error."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    model: str = ""
    raw: dict | None = None

    def as_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in self.tool_calls
            ]
        return msg


@dataclass
class ModelInfo:
    name: str
    size_bytes: int = 0
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / 1e9, 1)


@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> ChatResponse: ...

    def stream(self, messages: list[dict]) -> Iterator[str]: ...

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...

    def list_models(self) -> list[str]: ...

    def health(self) -> tuple[bool, str]: ...
