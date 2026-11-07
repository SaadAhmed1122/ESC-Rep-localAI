"""Ollama client (https://github.com/ollama/ollama/blob/main/docs/api.md).

Talks only to the configured local endpoint. Also manages models: list, pull
(the only operation that needs internet — run it once, then go offline),
delete, and **import a local GGUF file** for fully air-gapped installs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx

from esglocal.llm.base import ChatResponse, LLMError, ModelInfo, ToolCall

EMBED_HINTS = ("embed", "bge", "minilm", "e5-", "gte-", "arctic-embed")


def is_embedding_model(name: str) -> bool:
    return any(h in name.lower() for h in EMBED_HINTS)


class OllamaClient:
    provider = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "",
        temperature: float = 0.2,
        num_ctx: int = 8192,
        timeout: float = 600.0,
        think: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.think = think
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)

    # ----------------------------------------------------------------- helpers
    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _to_ollama_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {"role": "tool", "content": m["content"], "tool_name": m.get("name", "")}
                )
            elif m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content", ""),
                        "tool_calls": [
                            {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                            for tc in m["tool_calls"]
                        ],
                    }
                )
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        return out

    def _options(self, temperature: float | None) -> dict:
        return {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }

    # -------------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> ChatResponse:
        if not self.model:
            raise LLMError("No chat model selected. Install one with: ollama pull <model>")
        payload: dict = {
            "model": self.model,
            "messages": self._to_ollama_messages(messages),
            "stream": False,
            "options": self._options(temperature),
            "think": self.think,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        if json_mode:
            payload["format"] = "json"
        data = self._post("/api/chat", payload)
        msg = data.get("message", {})
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args, id=f"call_{i}"))
        return ChatResponse(
            content=msg.get("content", "") or "",
            tool_calls=calls,
            thinking=msg.get("thinking", "") or "",
            model=data.get("model", self.model),
            raw=data,
        )

    def stream(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": self._to_ollama_messages(messages),
            "stream": True,
            "options": self._options(None),
            "think": self.think,
        }
        try:
            with self._http.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    r.read()
                    raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        data = self._post("/api/embed", {"model": model or self.model, "input": texts})
        return data.get("embeddings", [])

    # ------------------------------------------------------------------ models
    def health(self) -> tuple[bool, str]:
        try:
            r = self._http.get("/api/version", timeout=5)
            r.raise_for_status()
            return True, f"Ollama {r.json().get('version', '?')} at {self.base_url}"
        except httpx.HTTPError as exc:
            return False, f"Ollama not reachable at {self.base_url} ({exc.__class__.__name__})"

    def model_details(self) -> list[ModelInfo]:
        try:
            r = self._http.get("/api/tags", timeout=10)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot list models: {exc}") from exc
        out = []
        for m in r.json().get("models", []):
            d = m.get("details", {}) or {}
            out.append(
                ModelInfo(
                    name=m.get("name", ""),
                    size_bytes=int(m.get("size", 0)),
                    family=d.get("family", ""),
                    parameter_size=d.get("parameter_size", ""),
                    quantization=d.get("quantization_level", ""),
                )
            )
        return out

    def list_models(self) -> list[str]:
        return [m.name for m in self.model_details()]

    def pull(self, name: str) -> Iterator[dict]:
        """Download a model from the Ollama registry (requires internet, one-time)."""
        try:
            with self._http.stream("POST", "/api/pull", json={"model": name, "stream": True}) as r:
                if r.status_code >= 400:
                    r.read()
                    raise LLMError(f"Pull failed: {r.text[:300]}")
                for line in r.iter_lines():
                    if line:
                        event = json.loads(line)
                        if "error" in event:
                            raise LLMError(event["error"])
                        yield event
        except httpx.HTTPError as exc:
            raise LLMError(f"Pull failed: {exc}") from exc

    def delete(self, name: str) -> None:
        try:
            r = self._http.request("DELETE", "/api/delete", json={"model": name})
        except httpx.HTTPError as exc:
            raise LLMError(f"Delete failed: {exc}") from exc
        if r.status_code >= 400:
            raise LLMError(f"Delete failed: {r.text[:300]}")

    def import_gguf(self, path: str | Path, name: str, system: str | None = None) -> None:
        """Register a local ``.gguf`` file as an Ollama model — no internet needed.

        Uploads the file as a blob to the local Ollama server, then creates the model.
        """
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != ".gguf" or not path.is_file():
            raise LLMError(f"Not a .gguf file: {path}")
        sha = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
        digest = f"sha256:{sha.hexdigest()}"
        head = self._http.head(f"/api/blobs/{digest}")
        if head.status_code != 200:
            with path.open("rb") as fh:
                r = self._http.post(f"/api/blobs/{digest}", content=fh, timeout=None)
            if r.status_code >= 400:
                raise LLMError(f"Blob upload failed: {r.text[:300]}")
        payload: dict = {"model": name, "files": {path.name: digest}, "stream": False}
        if system:
            payload["system"] = system
        self._post("/api/create", payload)

    def default_chat_model(self) -> str:
        models = [m for m in self.list_models() if not is_embedding_model(m)]
        return models[0] if models else ""
