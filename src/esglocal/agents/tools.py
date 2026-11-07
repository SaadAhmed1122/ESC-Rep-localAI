"""Tools the agents can call. All run locally against the user's data."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from esglocal.agents.base import Tool
from esglocal.catalog import Catalog
from esglocal.forecasting import forecast, simulate
from esglocal.rag.store import DocumentStore
from esglocal.scoring import Assessment, NarrativeScorer


@dataclass
class AgentContext:
    catalog: Catalog
    industry: str
    assessment: Assessment
    table: pd.DataFrame
    narratives: dict[str, str]
    scorer: NarrativeScorer | None = None
    documents: DocumentStore | None = None
    company: str = ""


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def kpi_snapshot(ctx: AgentContext, kpi_id: str) -> dict:
    kpi = ctx.catalog.kpi(kpi_id)
    res = ctx.assessment.result(kpi_id)
    bench = ctx.catalog.benchmark_for(kpi_id, ctx.industry)
    out = {
        "kpi_id": kpi.id,
        "name": kpi.name,
        "category": kpi.category,
        "unit": kpi.unit,
        "frameworks": list(kpi.frameworks),
        "value": _r(res.value) if res else None,
        "score_0_100": _r(res.score, 1) if res else None,
        "period": res.period if res else None,
        "history": {k: _r(v) for k, v in (res.history if res else {}).items()},
        "status": "ok" if res and res.ok else (res.error if res else "not computed"),
    }
    if bench:
        out["benchmark"] = {"best": bench.best, "worst": bench.worst}
    if kpi.inputs:
        out["inputs"] = [i.name for i in kpi.inputs]
    if kpi.is_qualitative and res and res.text:
        out["narrative_excerpt"] = res.text[:600]
    return out


def build_tools(ctx: AgentContext) -> list[Tool]:
    def list_kpis(category: str = "") -> list[dict]:
        rows = []
        for r in ctx.assessment.results:
            if category and r.category.lower() != category.lower():
                continue
            rows.append(
                {
                    "kpi_id": r.kpi_id,
                    "name": r.name,
                    "category": r.category,
                    "value": _r(r.value),
                    "unit": r.unit,
                    "score": _r(r.score, 1),
                    "status": "ok" if r.ok else r.error,
                }
            )
        return rows

    def get_kpi(kpi_id: str) -> dict:
        return kpi_snapshot(ctx, kpi_id)

    def get_scores() -> dict:
        a = ctx.assessment
        return {
            "industry": a.industry,
            "overall": _r(a.overall, 1),
            "rating": a.rating,
            "category_scores": {k: _r(v, 1) for k, v in a.category_scores.items()},
            "weights": a.weights,
            "coverage_pct": round(a.coverage * 100, 1),
            "weakest": [r.kpi_id for r in a.weakest(5)],
            "strongest": [r.kpi_id for r in a.strongest(5)],
        }

    def simulate_change(changes: dict) -> dict:
        """changes: {input_name: percent_change}"""
        clean = {str(k): float(v) for k, v in (changes or {}).items()}
        unknown = [k for k in clean if k not in ctx.table.columns]
        new = simulate(ctx.catalog, ctx.industry, ctx.table, clean, ctx.narratives, ctx.scorer)
        old = ctx.assessment
        changed = []
        for r in new.results:
            before = old.result(r.kpi_id)
            if before and before.score is not None and r.score is not None:
                if abs(r.score - before.score) > 0.05:
                    changed.append(
                        {
                            "kpi_id": r.kpi_id,
                            "value_before": _r(before.value),
                            "value_after": _r(r.value),
                            "score_before": _r(before.score, 1),
                            "score_after": _r(r.score, 1),
                        }
                    )
        return {
            "overall_before": _r(old.overall, 1),
            "overall_after": _r(new.overall, 1),
            "category_after": {k: _r(v, 1) for k, v in new.category_scores.items()},
            "changed_kpis": changed,
            "ignored_unknown_inputs": unknown,
            "available_inputs": list(ctx.table.columns),
        }

    def forecast_kpi(kpi_id: str, horizon: int = 3) -> dict:
        res = ctx.assessment.result(kpi_id)
        if not res or len(res.history) < 2:
            return {"kpi_id": kpi_id, "forecast": None, "reason": "needs >= 2 periods of data"}
        fc = forecast(res.history, horizon=max(1, min(int(horizon), 10)))
        return {
            "kpi_id": kpi_id,
            "history": {k: _r(v) for k, v in res.history.items()},
            "forecast": dict(zip(fc.periods, [_r(v) for v in fc.values], strict=True)),
            "trend": fc.direction,
            "r2": _r(fc.r2, 3),
        }

    def search_documents(query: str, k: int = 4) -> list[dict]:
        if ctx.documents is None:
            return [{"note": "No documents have been indexed."}]
        hits = ctx.documents.search(query, k=max(1, min(int(k), 8)))
        return [
            {"source": h.doc, "chunk": h.chunk, "score": _r(h.score, 3), "text": h.text}
            for h in hits
        ]

    tools = [
        Tool(
            "list_kpis",
            "List computed ESG KPIs with values and 0-100 scores, optionally for one category "
            "(Environmental, Social or Governance).",
            {"type": "object", "properties": {"category": {"type": "string"}}},
            list_kpis,
        ),
        Tool(
            "get_kpi",
            "Get full detail for one KPI: value, score, history, benchmark, inputs, frameworks.",
            {
                "type": "object",
                "properties": {"kpi_id": {"type": "string", "description": "e.g. E-GHG-INT"}},
                "required": ["kpi_id"],
            },
            get_kpi,
        ),
        Tool(
            "get_scores",
            "Get the overall ESG score, rating, category scores, coverage and weakest/strongest KPIs.",
            {"type": "object", "properties": {}},
            get_scores,
        ),
        Tool(
            "simulate_change",
            "What-if simulation: apply percentage changes to raw inputs of the latest period "
            '(e.g. {"scope1_tco2e": -20}) and return the new scores.',
            {
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "object",
                        "description": "Map of input name to percent change",
                        "additionalProperties": {"type": "number"},
                    }
                },
                "required": ["changes"],
            },
            simulate_change,
        ),
        Tool(
            "forecast_kpi",
            "Linear trend forecast of a KPI for the next periods.",
            {
                "type": "object",
                "properties": {"kpi_id": {"type": "string"}, "horizon": {"type": "integer"}},
                "required": ["kpi_id"],
            },
            forecast_kpi,
        ),
    ]
    if ctx.documents is not None:
        tools.append(
            Tool(
                "search_documents",
                "Search the company's own uploaded documents (policies, reports). Cite the source.",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
                    "required": ["query"],
                },
                search_documents,
            )
        )
    return tools
