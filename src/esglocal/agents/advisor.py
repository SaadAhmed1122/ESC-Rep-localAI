"""Conversational ESG advisor: a tool-using agent grounded in the user's data."""

from __future__ import annotations

from esglocal.agents.base import Agent, AgentResult, EventHandler
from esglocal.agents.tools import AgentContext, build_tools
from esglocal.privacy.redact import redact

MAX_HISTORY = 12

ADVISOR_INSTRUCTIONS = """You help the user understand and improve their ESG performance.
- Use tools to look up KPIs, scores, trends and what-if simulations before answering
  questions about the company's data. Cite KPI ids (e.g. E-GHG-INT).
- When documents are available, use search_documents for policy questions and cite the
  source file name.
- Be concise and practical. Use Markdown bullets for lists.
- If asked something unrelated to ESG or the data, answer briefly and steer back.
- Never fabricate figures; if data is missing, say what input is needed."""


class Advisor:
    def __init__(self, llm, ctx: AgentContext, max_steps: int = 6, redact_pii: bool = True):
        self.ctx = ctx
        self.redact_pii = redact_pii
        self.agent = Agent(
            name="advisor",
            role=f"ESG Advisor for {ctx.company or 'a company'} in the {ctx.industry} industry",
            instructions=ADVISOR_INSTRUCTIONS,
            llm=llm,
            tools=build_tools(ctx),
            max_steps=max_steps,
            temperature=0.3,
        )

    def ask(
        self,
        question: str,
        history: list[dict] | None = None,
        on_event: EventHandler | None = None,
    ) -> AgentResult:
        q = redact(question).text if self.redact_pii else question
        trimmed = [
            {"role": m["role"], "content": m["content"]}
            for m in (history or [])
            if m.get("role") in {"user", "assistant"}
        ][-MAX_HISTORY:]
        return self.agent.run(q, history=trimmed, on_event=on_event)
