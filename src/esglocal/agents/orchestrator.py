"""Multi-agent ESG analysis pipeline.

    data quality (deterministic)
        └─> E / S / G analysts (parallel, optional tool use)
              └─> strategist (uses what-if simulation tool)
                    └─> critic (deterministic checks + LLM review)
                          └─> strategist revision (once, if needed)
                                └─> reporter  ->  Markdown report with deterministic tables

Numbers are always computed in code; the LLM only interprets them.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from esglocal.agents.base import EventHandler
from esglocal.agents.specialists import (
    category_analyst,
    critic,
    default_analysis,
    normalise_analysis,
    normalise_strategy,
    reporter,
    strategist,
)
from esglocal.agents.tools import AgentContext, build_tools, kpi_snapshot
from esglocal.catalog import CATEGORIES
from esglocal.privacy.redact import redact
from esglocal.quality import check_quality
from esglocal.reporting import build_report

STAGES = ["quality", "analysis", "strategy", "review", "report"]


@dataclass
class PipelineResult:
    company: str
    industry: str
    model: str
    assessment: dict
    quality: list[dict]
    analyses: dict[str, dict]
    strategy: dict
    review: dict
    report_markdown: str
    duration_s: float
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ESGPipeline:
    def __init__(
        self,
        llm,
        ctx: AgentContext,
        on_event: EventHandler | None = None,
        concurrency: int = 1,
        max_steps: int = 6,
        redact_pii: bool = True,
        revise: bool = True,
    ):
        self.llm = llm
        self.ctx = ctx
        self.on_event = on_event
        self.concurrency = max(1, concurrency)
        self.max_steps = max_steps
        self.redact_pii = redact_pii
        self.revise = revise
        self.tools = {t.name: t for t in build_tools(ctx)}
        self.steps: list[dict] = []
        self._events: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._owner = threading.get_ident()

    # ---------------------------------------------------------------- events
    def _emit(self, stage: str, status: str, message: str = "", **extra) -> None:
        event = {"stage": stage, "status": status, "message": message, **extra}
        self.steps.append(event)
        if not self.on_event:
            return
        if threading.get_ident() == self._owner:
            self._drain()
            self.on_event(event)
        else:  # worker thread: UI callbacks (e.g. Streamlit) must run on the caller thread
            self._events.put(event)

    def _drain(self) -> None:
        while self.on_event and not self._events.empty():
            self.on_event(self._events.get_nowait())

    def _agent_event(self, event: dict) -> None:
        if event.get("type") == "tool":
            self._emit(
                "tool",
                "info",
                f"{event['agent']} called {event['tool']}",
                arguments=event["arguments"],
            )

    def _clean(self, text: str) -> str:
        return redact(text).text if self.redact_pii else text

    # ------------------------------------------------------------ data packs
    def _category_pack(self, category: str) -> dict:
        a = self.ctx.assessment
        kpis = [kpi_snapshot(self.ctx, r.kpi_id) for r in a.results if r.category == category]
        for k in kpis:
            if "narrative_excerpt" in k:
                k["narrative_excerpt"] = self._clean(k["narrative_excerpt"])
        return {
            "company": self.ctx.company or "the company",
            "industry": self.ctx.industry,
            "category": category,
            "category_score": a.category_scores.get(category),
            "category_weight": a.weights.get(category),
            "kpis": kpis,
        }

    # ---------------------------------------------------------------- stages
    def _analyse(self, category: str) -> tuple[str, dict]:
        pack = self._category_pack(category)
        if not pack["kpis"]:
            return category, default_analysis(category, "no KPIs for this industry")
        tools = [self.tools["get_kpi"], self.tools["forecast_kpi"]]
        if "search_documents" in self.tools:
            tools.append(self.tools["search_documents"])
        agent = category_analyst(category, self.llm, tools, self.max_steps)
        task = "Analyse this data:\n" + json.dumps(pack, indent=1, default=str)
        try:
            result = agent.run(task, on_event=self._agent_event, expect_json=True)
            return category, normalise_analysis(result.data, category)
        except Exception as exc:  # noqa: BLE001 - one failing analyst must not kill the run
            return category, default_analysis(category, f"error: {exc}")

    def _strategy(self, analyses: dict, feedback: list[str] | None = None) -> dict:
        tools = [self.tools["simulate_change"], self.tools["get_scores"], self.tools["get_kpi"]]
        agent = strategist(self.llm, tools if not feedback else [], self.max_steps)
        payload = {
            "industry": self.ctx.industry,
            "scores": self.tools["get_scores"].fn(),
            "simulatable_inputs": list(self.ctx.table.columns),
            "analyses": analyses,
        }
        task = "Build the roadmap from:\n" + json.dumps(payload, indent=1, default=str)
        if feedback:
            task += "\n\nA reviewer found these issues; fix them:\n- " + "\n- ".join(feedback)
        result = agent.run(task, on_event=self._agent_event, expect_json=True)
        return normalise_strategy(result.data)

    def _deterministic_review(self, strategy: dict, analyses: dict) -> list[str]:
        known = {k.id for k in self.ctx.catalog.kpis}
        issues = []
        referenced = {k for p in strategy["priorities"] for k in p["kpi_ids"]}
        referenced |= {
            r["kpi_id"] for a in analyses.values() for r in a["recommendations"] if r["kpi_id"]
        }
        for k in sorted(referenced - known):
            issues.append(f"Unknown KPI id '{k}' — use only ids from the data.")
        if not strategy["priorities"]:
            issues.append("Strategy has no priorities.")
        weakest = [r.kpi_id for r in self.ctx.assessment.weakest(3)]
        if weakest and strategy["priorities"] and not (referenced & set(weakest)):
            issues.append(f"Strategy ignores the weakest KPIs: {', '.join(weakest)}.")
        return issues

    def _review(self, strategy: dict, analyses: dict) -> dict:
        hard_issues = self._deterministic_review(strategy, analyses)
        issues = list(hard_issues)
        try:
            payload = {
                "scores": self.tools["get_scores"].fn(),
                "kpis": self.tools["list_kpis"].fn(),
                "strategy": strategy,
            }
            result = critic(self.llm).run(
                "Review:\n" + json.dumps(payload, indent=1, default=str), expect_json=True
            )
            data = result.data or {}
            issues += [str(i) for i in data.get("issues") or [] if str(i).strip()]
            verdict = str(data.get("verdict") or "approve").lower()
        except Exception as exc:  # noqa: BLE001
            verdict = "approve"
            issues.append(f"(critic unavailable: {exc})")
        if hard_issues:
            verdict = "revise"  # deterministic findings always trigger a revision
        return {"verdict": verdict, "issues": issues[:10]}

    def _report(self, analyses: dict, strategy: dict, quality: list[dict]) -> str:
        payload = {
            "company": self.ctx.company or "The company",
            "industry": self.ctx.industry,
            "scores": self.tools["get_scores"].fn(),
            "analyses": analyses,
            "strategy": strategy,
            "data_quality_issues": [q["message"] for q in quality if q["severity"] != "info"],
        }
        result = reporter(self.llm).run(
            "Write the report body from:\n" + json.dumps(payload, indent=1, default=str)
        )
        return result.content.strip()

    # ------------------------------------------------------------------- run
    def run(self) -> PipelineResult:
        t0 = time.time()
        self._owner = threading.get_ident()
        ctx = self.ctx

        self._emit("quality", "start", "Checking data quality")
        quality = [i.to_dict() for i in check_quality(ctx.catalog, ctx.assessment, ctx.table)]
        self._emit("quality", "done", f"{len(quality)} data-quality notes")

        self._emit("analysis", "start", "E/S/G analysts working")
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            analyses = dict(pool.map(self._analyse, CATEGORIES))
        self._drain()
        self._emit("analysis", "done", "Category analyses complete")

        self._emit("strategy", "start", "Strategist building roadmap (with what-if simulations)")
        try:
            strategy = self._strategy(analyses)
        except Exception as exc:  # noqa: BLE001
            strategy = normalise_strategy({})
            self._emit("strategy", "error", str(exc))
        self._emit("strategy", "done", f"{len(strategy['priorities'])} priorities")

        self._emit("review", "start", "Critic reviewing strategy")
        review = self._review(strategy, analyses)
        if self.revise and review["verdict"] == "revise" and review["issues"]:
            self._emit("review", "info", "Revising strategy based on review")
            try:
                revised = self._strategy(analyses, feedback=review["issues"])
                if revised["priorities"]:
                    strategy = revised
                    review["revised"] = True
            except Exception as exc:  # noqa: BLE001
                self._emit("review", "error", f"Revision failed: {exc}")
        self._emit("review", "done", f"Verdict: {review['verdict']}")

        self._emit("report", "start", "Writing report")
        try:
            body = self._report(analyses, strategy, quality)
        except Exception as exc:  # noqa: BLE001
            body = f"_Narrative unavailable: {exc}_"
        model = getattr(self.llm, "model", "")
        report = build_report(
            company=ctx.company,
            assessment=ctx.assessment,
            catalog=ctx.catalog,
            body_markdown=body,
            strategy=strategy,
            quality=quality,
            model=model,
        )
        self._emit("report", "done", "Report ready")

        return PipelineResult(
            company=ctx.company,
            industry=ctx.industry,
            model=model,
            assessment=ctx.assessment.to_dict(),
            quality=quality,
            analyses=analyses,
            strategy=strategy,
            review=review,
            report_markdown=report,
            duration_s=round(time.time() - t0, 1),
            steps=self.steps,
        )
