"""Deterministic data-quality checks (run before any LLM sees the data)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from esglocal.catalog import Catalog
from esglocal.scoring import Assessment


@dataclass
class Issue:
    severity: str  # info | warning | error
    kpi_id: str | None
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "kpi_id": self.kpi_id, "message": self.message}


def check_quality(catalog: Catalog, assessment: Assessment, table: pd.DataFrame) -> list[Issue]:
    issues: list[Issue] = []
    if table.empty:
        issues.append(Issue("error", None, "No quantitative data has been provided."))
    else:
        negatives = [c for c in table.columns if (table[c].dropna() < 0).any()]
        for col in negatives:
            issues.append(Issue("warning", None, f"Input '{col}' contains negative values."))
        if len(table.index) == 1:
            issues.append(
                Issue("info", None, "Only one period of data: trends and forecasts unavailable.")
            )

    for r in assessment.results:
        kpi = catalog.kpi(r.kpi_id)
        if r.error:
            sev = "info" if r.qualitative else "warning"
            issues.append(Issue(sev, r.kpi_id, f"{r.name}: {r.error}"))
            continue
        if r.note:
            issues.append(Issue("warning", r.kpi_id, f"{r.name}: {r.note}"))
        if r.value is None or r.qualitative:
            continue
        if "%" in kpi.unit and not -100 <= r.value <= 100 and kpi.id != "E-GHG-YOY":
            issues.append(Issue("error", r.kpi_id, f"{r.name} = {r.value:.1f}% is outside 0-100%."))
        if len(r.history) >= 2:
            vals = list(r.history.values())
            prev, last = vals[-2], vals[-1]
            bench = catalog.benchmark_for(r.kpi_id, assessment.industry)
            if bench and bench.best != bench.worst:
                # scale-aware: a jump of more than half the benchmark range is suspicious;
                # avoids false alarms like fatalities 1 -> 0 ("-100%")
                jump = abs(last - prev) / abs(bench.best - bench.worst) > 0.5
            else:
                jump = bool(prev) and abs(last - prev) / abs(prev) > 0.5
            if jump and prev:
                issues.append(
                    Issue(
                        "warning",
                        r.kpi_id,
                        f"{r.name} changed by {100 * (last - prev) / abs(prev):+.0f}% vs prior "
                        "period — verify the data.",
                    )
                )
    if assessment.coverage < 0.5:
        issues.append(
            Issue(
                "warning",
                None,
                f"Only {assessment.coverage:.0%} of material KPIs are covered; scores are partial.",
            )
        )
    return issues
