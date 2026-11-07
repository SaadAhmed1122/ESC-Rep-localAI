"""Normalise KPI values to 0-100 and roll them up into category and ESG scores."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pandas as pd

from esglocal.calculator import KPIResult, compute_kpis
from esglocal.catalog import CATEGORIES, Benchmark, Catalog

RATING_BANDS = (
    (80.0, "Leader"),
    (60.0, "Advanced"),
    (40.0, "Developing"),
    (20.0, "Lagging"),
    (0.0, "Critical"),
)


def normalise(value: float, benchmark: Benchmark) -> float | None:
    """Linear 0-100 score between the worst (0) and best (100) benchmark, clamped.

    Returns None when the value or benchmark is not a finite number.
    """
    if not all(math.isfinite(x) for x in (value, benchmark.best, benchmark.worst)):
        return None
    if benchmark.best == benchmark.worst:
        return 100.0 if value == benchmark.best else 0.0
    score = (value - benchmark.worst) / (benchmark.best - benchmark.worst) * 100.0
    return max(0.0, min(100.0, score))


def rating_for(score: float | None) -> str:
    if score is None:
        return "n/a"
    for threshold, label in RATING_BANDS:
        if score >= threshold:
            return label
    return "Critical"


@dataclass
class Assessment:
    industry: str
    results: list[KPIResult]
    category_scores: dict[str, float | None] = field(default_factory=dict)
    overall: float | None = None
    coverage: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)

    @property
    def rating(self) -> str:
        return rating_for(self.overall)

    def by_category(self) -> dict[str, list[KPIResult]]:
        out: dict[str, list[KPIResult]] = {c: [] for c in CATEGORIES}
        for r in self.results:
            out[r.category].append(r)
        return out

    def result(self, kpi_id: str) -> KPIResult | None:
        return next((r for r in self.results if r.kpi_id == kpi_id), None)

    def weakest(self, n: int = 5) -> list[KPIResult]:
        scored = [r for r in self.results if r.score is not None]
        return sorted(scored, key=lambda r: r.score)[:n]

    def strongest(self, n: int = 5) -> list[KPIResult]:
        scored = [r for r in self.results if r.score is not None]
        return sorted(scored, key=lambda r: r.score, reverse=True)[:n]

    def to_dict(self) -> dict:
        return {
            "industry": self.industry,
            "overall": None if self.overall is None else round(self.overall, 1),
            "rating": self.rating,
            "coverage_pct": round(self.coverage * 100, 1),
            "weights": self.weights,
            "category_scores": {
                k: None if v is None else round(v, 1) for k, v in self.category_scores.items()
            },
            "kpis": [r.to_dict() for r in self.results],
        }


def score_results(
    catalog: Catalog,
    industry: str,
    results: list[KPIResult],
    benchmark_overrides: Mapping[str, Benchmark] | None = None,
) -> Assessment:
    overrides = benchmark_overrides or {}
    for r in results:
        if r.qualitative or not r.scored or r.value is None:
            continue
        bench = overrides.get(r.kpi_id) or catalog.benchmark_for(r.kpi_id, industry)
        if bench:
            r.score = normalise(r.value, bench)

    ind = catalog.industry(industry)
    cat_scores: dict[str, float | None] = {}
    for cat in CATEGORIES:
        vals = [r.score for r in results if r.category == cat and r.score is not None]
        cat_scores[cat] = sum(vals) / len(vals) if vals else None

    # Weighted overall, re-normalising weights over categories that have data
    present = {c: s for c, s in cat_scores.items() if s is not None}
    if present:
        wsum = sum(ind.weights.get(c, 0) for c in present)
        overall = (
            sum(s * ind.weights.get(c, 0) for c, s in present.items()) / wsum if wsum > 0 else None
        )
    else:
        overall = None

    scorable = [r for r in results if r.scored]
    coverage = sum(1 for r in scorable if r.score is not None) / len(scorable) if scorable else 0.0
    return Assessment(
        industry=industry,
        results=results,
        category_scores=cat_scores,
        overall=overall,
        coverage=coverage,
        weights=dict(ind.weights),
    )


NarrativeScorer = Callable[[str, str, str], float]  # (text, best, worst) -> 0..1


def assess(
    catalog: Catalog,
    industry: str,
    table: pd.DataFrame,
    narratives: Mapping[str, str] | None = None,
    narrative_scorer: NarrativeScorer | None = None,
    benchmark_overrides: Mapping[str, Benchmark] | None = None,
) -> Assessment:
    """One-shot: compute quantitative KPIs, score narratives, aggregate."""
    results = compute_kpis(catalog, industry, table)
    narratives = narratives or {}
    for kpi in catalog.kpis_for(industry):
        if not kpi.is_qualitative:
            continue
        r = KPIResult(kpi.id, kpi.name, kpi.category, "score", qualitative=True)
        text = (narratives.get(kpi.id) or "").strip()
        if not text:
            r.error = "No narrative provided"
        else:
            r.text = text
            if narrative_scorer:
                r.score = 100.0 * narrative_scorer(
                    text, kpi.exemplars["best"], kpi.exemplars["worst"]
                )
                r.value = round(r.score, 1)
        results.append(r)
    return score_results(catalog, industry, results, benchmark_overrides)
