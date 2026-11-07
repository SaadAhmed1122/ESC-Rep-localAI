"""Minimal, dependency-free agent runtime with native tool calling.

Why not CrewAI/LangChain? They pull in large dependency trees and some ship
telemetry. A ~150-line loop over the local model's tool-calling API is easier
to audit, works with any provider in :mod:`esglocal.llm`, and keeps all data
in-process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from esglocal.llm.base import ChatResponse

EventHandler = Callable[[dict], None]
MAX_TOOL_RESULT_CHARS = 6000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def run(self, arguments: dict) -> str:
        args = arguments if isinstance(arguments, dict) else {}
        allowed = set(self.parameters.get("properties", {}))
        missing = [r for r in self.parameters.get("required", []) if r not in args]
        if missing:
            return json.dumps({"error": f"missing required arguments: {missing}"})
        try:
            result = self.fn(**{k: v for k, v in args.items() if k in allowed})
        except Exception as exc:  # noqa: BLE001 - tool errors are reported back to the model
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return text[:MAX_TOOL_RESULT_CHARS]


@dataclass
class AgentResult:
    agent: str
    content: str
    steps: list[dict] = field(default_factory=list)
    data: dict | None = None


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of model output (tolerates ``` fences / prose)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    for candidate in (cleaned, *(m.group(0) for m in _JSON_BLOCK.finditer(cleaned))):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class Agent:
    name: str
    role: str
    instructions: str
    llm: Any
    tools: list[Tool] = field(default_factory=list)
    max_steps: int = 6
    temperature: float | None = None

    def system_prompt(self) -> str:
        return (
            f"You are the {self.role}.\n{self.instructions}\n\n"
            "Ground every statement in the data provided or returned by tools. "
            "Never invent numbers. If data is missing, say so."
        )

    def _emit(self, on_event: EventHandler | None, **event) -> None:
        if on_event:
            on_event({"agent": self.name, **event})

    def run(
        self,
        task: str,
        history: list[dict] | None = None,
        on_event: EventHandler | None = None,
        expect_json: bool = False,
    ) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system_prompt()}]
        messages += list(history or [])
        messages.append({"role": "user", "content": task})
        tool_map = {t.name: t for t in self.tools}
        specs = [t.spec() for t in self.tools] or None
        steps: list[dict] = []
        response: ChatResponse | None = None

        for _ in range(self.max_steps):
            response = self.llm.chat(
                messages,
                tools=specs,
                json_mode=expect_json and not specs,
                temperature=self.temperature,
            )
            if not response.tool_calls:
                break
            messages.append(response.as_message())
            for call in response.tool_calls:
                tool = tool_map.get(call.name)
                output = (
                    tool.run(call.arguments)
                    if tool
                    else json.dumps({"error": f"unknown tool {call.name}"})
                )
                steps.append(
                    {"tool": call.name, "arguments": call.arguments, "result": output[:500]}
                )
                self._emit(on_event, type="tool", tool=call.name, arguments=call.arguments)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output}
                )
        else:
            # step budget exhausted while still calling tools: force a final answer
            messages.append(
                {"role": "user", "content": "Stop calling tools and give your final answer now."}
            )
            response = self.llm.chat(messages, json_mode=expect_json, temperature=self.temperature)

        content = response.content if response else ""
        data = None
        if expect_json:
            data = extract_json(content)
            if data is None:
                # one repair attempt in strict JSON mode
                repair = self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": "Convert the text into a single valid JSON object.",
                        },
                        {"role": "user", "content": content or task},
                    ],
                    json_mode=True,
                    temperature=0,
                )
                data = extract_json(repair.content)
        return AgentResult(agent=self.name, content=content, steps=steps, data=data)
