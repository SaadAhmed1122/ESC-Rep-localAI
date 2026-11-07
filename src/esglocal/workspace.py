"""A workspace holds one company's inputs and outputs; persisted encrypted in the vault."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from esglocal.calculator import build_period_table, table_from_values
from esglocal.catalog import Benchmark, Catalog
from esglocal.scoring import Assessment, NarrativeScorer, assess
from esglocal.security.vault import Vault

PREFIX = "workspace:"


@dataclass
class Workspace:
    name: str = "default"
    company: str = ""
    industry: str | None = None
    records: list[dict] = field(default_factory=list)  # uploaded rows
    mapping: dict[str, str] = field(default_factory=dict)  # input -> column
    period_col: str | None = None
    manual_values: dict[str, float] = field(default_factory=dict)
    narratives: dict[str, str] = field(default_factory=dict)
    benchmark_overrides: dict[str, dict] = field(default_factory=dict)
    reports: list[dict] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    updated: str = ""

    # ---------------------------------------------------------------- data
    def dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.records)

    def set_data(self, df: pd.DataFrame) -> None:
        clean = df.copy()
        clean.columns = [str(c) for c in clean.columns]
        self.records = clean.where(pd.notna(clean), None).to_dict(orient="records")

    def overrides(self) -> dict[str, Benchmark]:
        return {
            k: Benchmark(float(v["best"]), float(v["worst"]))
            for k, v in self.benchmark_overrides.items()
        }

    def table(self, catalog: Catalog) -> pd.DataFrame:
        if not self.industry:
            return pd.DataFrame()
        inputs = catalog.required_inputs(self.industry)
        table = pd.DataFrame()
        if self.records and self.mapping:
            table = build_period_table(self.dataframe(), self.mapping, inputs, self.period_col)
        manual = {k: float(v) for k, v in self.manual_values.items() if v is not None}
        if manual:
            if table.empty:
                table = table_from_values(manual)
            else:
                latest = table.index[-1]
                for k, v in manual.items():
                    table.loc[latest, k] = v
        return table

    def assessment(
        self, catalog: Catalog, scorer: NarrativeScorer | None = None
    ) -> Assessment | None:
        if not self.industry:
            return None
        return assess(
            catalog,
            self.industry,
            self.table(catalog),
            self.narratives,
            scorer,
            self.overrides(),
        )

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Workspace:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def save_workspace(vault: Vault, ws: Workspace) -> None:
    ws.updated = datetime.now(timezone.utc).isoformat()
    vault.put(PREFIX + ws.name, ws.to_dict())


def load_workspace(vault: Vault, name: str = "default") -> Workspace:
    data = vault.get(PREFIX + name)
    return Workspace.from_dict(data) if data else Workspace(name=name)


def list_workspaces(vault: Vault) -> list[str]:
    return [n[len(PREFIX) :] for n in vault.names(PREFIX)]
