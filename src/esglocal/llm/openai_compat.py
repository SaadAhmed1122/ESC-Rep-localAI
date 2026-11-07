"""Client for OpenAI-compatible servers running on this machine.

Works with LM Studio (default :1234), llama.cpp ``llama-server`` (:8080),
vLLM, LocalAI, Jan, etc. The base URL is validated to be loopback (or
explicitly allow-listed) by :class:`esglocal.config.Settings`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from esglocal.llm.base import ChatResponse, LLMError, ToolCall


class OpenAICompatClient:
    provider = "openai_compat"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        model: str = "",
        temperature: float = 0.2,
        timeout: float = 600.0,
        api_key: str = "local",
    ):
        base = base_url.rstrip("/")
        self.base_url = base if base.endswith("/v1") else f"{base}/v1"
        self.model = model
        self.temperature = temperature
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            trust_env=False,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach local server at {self.base_url}: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"Server error {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _to_openai_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": m["content"],
                    }
                )
            elif m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc.get("id") or f"call_{i}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["arguments"]),
                                },
                            }
                            for i, tc in enumerate(m["tool_calls"])
                        ],
                    }
                )
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        return out

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> ChatResponse:
        payload: dict = {
            "model": self.model or "local-model",
            "messages": self._to_openai_messages(messages),
            "temperature": self.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post("/chat/completions", payload)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response: {str(data)[:300]}") from exc
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args, id=tc.get("id", "")))
        return ChatResponse(
            content=msg.get("content") or "",
            tool_calls=calls,
            model=data.get("model", self.model),
            raw=data,
        )

    def stream(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model or "local-model",
            "messages": self._to_openai_messages(messages),
            "temperature": self.temperature,
            "stream": True,
        }
        try:
            with self._http.stream("POST", "/chat/completions", json=payload) as r:
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    delta = json.loads(body)["choices"][0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
        except httpx.HTTPError as exc:
            raise LLMError(f"Stream failed: {exc}") from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        data = self._post("/embeddings", {"model": model or self.model, "input": texts})
        return [d["embedding"] for d in data.get("data", [])]

    def list_models(self) -> list[str]:
        try:
            r = self._http.get("/models", timeout=10)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot list models: {exc}") from exc
        return [m.get("id", "") for m in r.json().get("data", [])]

    def health(self) -> tuple[bool, str]:
        try:
            self.list_models()
            return True, f"OpenAI-compatible server at {self.base_url}"
        except LLMError as exc:
            return False, str(exc)
