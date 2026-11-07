"""Deterministic fake LLM for tests and UI development without a model.

It is clearly labelled in the UI — it never pretends to be a real model.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

from esglocal.llm.base import ChatResponse

Responder = Callable[[list[dict], list[dict] | None, bool], ChatResponse]


class FakeLLM:
    provider = "fake"

    def __init__(
        self, responses: list[ChatResponse] | None = None, responder: Responder | None = None
    ):
        self.model = "fake-model"
        self._responses = list(responses or [])
        self._responder = responder
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, json_mode=False, temperature=None) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        if self._responses:
            return self._responses.pop(0)
        if self._responder:
            return self._responder(messages, tools, json_mode)
        if json_mode:
            return ChatResponse(
                content=json.dumps(
                    {
                        "summary": "Offline demo analysis (fake model).",
                        "strengths": ["Data provided for key KPIs"],
                        "risks": ["Fake model: connect a local LLM for real insights"],
                        "recommendations": [
                            {
                                "action": "Install a local model with `ollama pull qwen2.5:7b`",
                                "kpi_id": "",
                                "impact": "high",
                                "effort": "low",
                                "horizon": "short",
                            }
                        ],
                        "score": 50,
                        "issues": [],
                    }
                )
            )
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return ChatResponse(content=f"[fake-model] Received: {last[:200]}")

    def stream(self, messages) -> Iterator[str]:
        yield self.chat(messages).content

    def embed(self, texts, model=None):
        from esglocal.embeddings import HashingEmbedder

        return HashingEmbedder(256).embed(texts).tolist()

    def list_models(self) -> list[str]:
        return [self.model]

    def health(self) -> tuple[bool, str]:
        return True, "Fake model (no real inference)"
