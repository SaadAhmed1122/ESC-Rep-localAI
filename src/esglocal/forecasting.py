"""Trend forecasting and what-if simulation (pure numpy, no LLM)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from esglocal.calculator import period_sort_key
from esglocal.catalog import Benchmark, Catalog
from esglocal.scoring import Assessment, NarrativeScorer, assess


@dataclass
class Forecast:
    periods: list[str]
    values: list[float]
    slope_per_period: float
    r2: float

    @property
    def direction(self) -> str:
        if abs(self.slope_per_period) < 1e-9:
            return "flat"
        return "rising" if self.slope_per_period > 0 else "falling"


def _numeric_periods(labels: list[str]) -> np.ndarray | None:
    """Labels as numbers if they are all finite and strictly increasing, else None."""
    try:
        x = np.array([float(p) for p in labels])
    except ValueError:
        return None
    if not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
        return None
    return x


def forecast(history: Mapping[str, float], horizon: int = 3) -> Forecast | None:
    """Linear trend projection (least squares). Needs >= 2 finite points."""
    items = [(k, float(v)) for k, v in history.items() if v is not None and np.isfinite(v)]
    items.sort(key=lambda kv: period_sort_key(kv[0]))
    if len(items) < 2:
        return None
    labels = [k for k, _ in items]
    y = np.array([v for _, v in items], dtype=float)
    x = _numeric_periods(labels)
    numeric = x is not None
    if not numeric:
        x = np.arange(len(labels), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    step = float(np.min(np.diff(x))) or 1.0  # base period, robust to a missing year
    future_x = [x[-1] + step * (i + 1) for i in range(horizon)]
    periods = [
        str(int(fx)) if numeric and float(fx).is_integer() else f"+{i + 1}"
        for i, fx in enumerate(future_x)
    ]
    values = [float(slope * fx + intercept) for fx in future_x]
    return Forecast(periods, values, float(slope * step), r2)


def periods_to_target(history: Mapping[str, float], target: float) -> float | None:
    """Estimated number of periods until the linear trend reaches ``target``."""
    fc = forecast(history, horizon=1)
    if fc is None or fc.slope_per_period == 0:
        return None
    latest = list(history.values())[-1]
    n = (target - latest) / fc.slope_per_period
    return n if n >= 0 else None


def simulate(
    catalog: Catalog,
    industry: str,
    table: pd.DataFrame,
    changes_pct: Mapping[str, float],
    narratives: Mapping[str, str] | None = None,
    narrative_scorer: NarrativeScorer | None = None,
    benchmark_overrides: Mapping[str, Benchmark] | None = None,
) -> Assessment:
    """Apply % changes to the latest period's inputs and re-assess.

    ``changes_pct`` maps input name -> percent change, e.g. ``{"scope1_tco2e": -20}``.
    """
    if table.empty:
        raise ValueError("No data to simulate")
    sim = table.copy()
    latest = sim.index[-1]
    for name, pct in changes_pct.items():
        if name in sim.columns and pd.notna(sim.at[latest, name]):
            sim.at[latest, name] = sim.at[latest, name] * (1 + pct / 100.0)
    return assess(catalog, industry, sim, narratives, narrative_scorer, benchmark_overrides)
