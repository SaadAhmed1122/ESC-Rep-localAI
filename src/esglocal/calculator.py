"""KPI calculation from tabular data.

Pipeline:
1. ``build_period_table`` — rename mapped columns to input names, coerce to
   numbers, and aggregate rows *per period* (sum for flows, mean for stocks).
   Aggregating inputs first and then applying the formula gives correct ratios
   (e.g. total injuries / total hours), unlike averaging per-row ratios.
2. ``compute_kpis`` — evaluate every quantitative KPI for every period.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from esglocal.catalog import KPI, Catalog, KPIInput
from esglocal.formula import FormulaError

PERIOD_CANDIDATES = ("period", "year", "fiscal_year", "fy", "reporting_year", "date")
DEFAULT_PERIOD = "current"


@dataclass
class KPIResult:
    kpi_id: str
    name: str
    category: str
    unit: str
    value: float | None = None
    period: str | None = None
    history: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    text: str | None = None  # qualitative narrative
    score: float | None = None  # 0-100, filled by scoring
    scored: bool = True
    qualitative: bool = False
    note: str | None = None  # non-fatal warning, e.g. latest period not computable

    @property
    def ok(self) -> bool:
        return self.error is None and (self.value is not None or self.text is not None)

    def to_dict(self) -> dict:
        return {
            "kpi_id": self.kpi_id,
            "name": self.name,
            "category": self.category,
            "unit": self.unit,
            "value": self.value,
            "period": self.period,
            "history": self.history,
            "error": self.error,
            "note": self.note,
            "score": self.score,
            "qualitative": self.qualitative,
            "text": self.text,
        }


def detect_period_column(df: pd.DataFrame) -> str | None:
    lowered = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    for cand in PERIOD_CANDIDATES:
        if cand in lowered:
            return lowered[cand]
    return None


_NUM_STRIP = re.compile(r"[\s%€$£' ]")
_DECIMAL_COMMA = re.compile(r"^-?[\d.]*,\d{1,2}$")  # "1.234,5" / "1,5" (European decimal comma)
_DATE_LIKE = re.compile(r"^(\d{4})-\d{2}-\d{2}")


def _parse_number(text) -> float:
    if text is None:
        return float("nan")
    s = _NUM_STRIP.sub("", str(text))
    if not s or s.lower() in {"nan", "none", "n/a", "na", "-"}:
        return float("nan")
    negative = s.startswith("(") and s.endswith(")")  # accounting negatives: (5)
    s = s.strip("()")
    if _DECIMAL_COMMA.match(s) and not ("." in s and s.rfind(".") > s.rfind(",")):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return float("nan")
    return -v if negative else v


def to_number(series: pd.Series) -> pd.Series:
    """Coerce a column to floats, understanding '1,234', '1.234,5', '12 %', '(5)', '€ 3'."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    return series.map(_parse_number).astype(float)


def _period_label(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return None
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return str(value.year)  # dates aggregate to calendar years
    text = str(value).strip()
    m = _DATE_LIKE.match(text)
    if m:  # ISO date strings (e.g. after a save/reload round-trip) behave like dates
        return m.group(1)
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text or None


def period_sort_key(label: str):
    """Chronological order: numbers numerically, then labels by their embedded number."""
    try:
        return (0, float(label), label)
    except ValueError:
        m = re.search(r"\d+(?:\.\d+)?", label)
        return (1, float(m.group(0)) if m else float("inf"), label)


def build_period_table(
    df: pd.DataFrame,
    mapping: Mapping[str, str],
    inputs: Mapping[str, KPIInput],
    period_col: str | None = None,
) -> pd.DataFrame:
    """Return a DataFrame indexed by period with one numeric column per mapped input."""
    if period_col is None:
        period_col = detect_period_column(df)
    data = pd.DataFrame(index=df.index)
    for input_name, column in mapping.items():
        if column not in df.columns:
            continue
        data[input_name] = to_number(df[column])
    if period_col and period_col in df.columns:
        data["__period"] = df[period_col].map(_period_label)
        data = data[data["__period"].notna()]  # rows without a period cannot be placed in time
    else:
        data["__period"] = DEFAULT_PERIOD

    agg = {}
    for name in data.columns:
        if name == "__period":
            continue
        how = inputs[name].aggregate if name in inputs else "sum"
        agg[name] = {"sum": "sum", "mean": "mean", "last": "last"}.get(how, "sum")
    if not agg:
        return pd.DataFrame(index=pd.Index([], name="period"))
    # min_count=1 keeps all-NaN groups as NaN instead of 0 for sums
    grouped = data.groupby("__period", sort=True)
    table = pd.DataFrame(
        {
            name: grouped[name].sum(min_count=1) if how == "sum" else grouped[name].agg(how)
            for name, how in agg.items()
        }
    )
    table = table.loc[sorted(table.index, key=period_sort_key)]
    table.index.name = "period"
    return table


def table_from_values(values: Mapping[str, float], period: str = DEFAULT_PERIOD) -> pd.DataFrame:
    """Build a single-period table from manually entered values."""
    table = pd.DataFrame([dict(values)], index=pd.Index([period], name="period"))
    return table.astype(float)


def compute_kpi(kpi: KPI, table: pd.DataFrame) -> KPIResult:
    res = KPIResult(
        kpi_id=kpi.id,
        name=kpi.name,
        category=kpi.category,
        unit=kpi.unit,
        scored=kpi.scored,
    )
    missing = [n for n in kpi.input_names if n not in table.columns]
    if missing:
        res.error = f"Missing inputs: {', '.join(missing)}"
        return res
    formula = kpi.compiled()
    last_error = None
    for period, row in table.iterrows():
        try:
            res.history[str(period)] = formula.evaluate(row[kpi.input_names].to_dict())
        except FormulaError as exc:
            last_error = f"{period}: {exc}"
    if res.history:
        latest = list(res.history)[-1]
        res.period, res.value = latest, res.history[latest]
        newest = str(table.index[-1])
        if latest != newest:
            # don't silently present an older period as current
            res.note = (
                f"Latest period {newest} could not be computed ({last_error}); showing {latest}."
            )
    else:
        res.error = last_error or "No data"
    return res


def compute_kpis(catalog: Catalog, industry: str, table: pd.DataFrame) -> list[KPIResult]:
    return [compute_kpi(k, table) for k in catalog.kpis_for(industry) if not k.is_qualitative]
