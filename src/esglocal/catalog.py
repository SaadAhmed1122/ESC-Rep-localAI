"""KPI catalog: definitions, benchmarks and industry materiality."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml

from esglocal.formula import Formula

CATEGORIES = ("Environmental", "Social", "Governance")


@dataclass(frozen=True)
class KPIInput:
    name: str
    description: str = ""
    unit: str = ""
    aggregate: str = "sum"  # sum | mean | last
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Benchmark:
    best: float
    worst: float

    @property
    def higher_is_better(self) -> bool:
        return self.best > self.worst


@dataclass(frozen=True)
class KPI:
    id: str
    name: str
    category: str
    type: str = "quantitative"  # quantitative | qualitative
    unit: str = ""
    formula: str = ""
    higher_is_better: bool = True
    scored: bool = True
    benchmark: Benchmark | None = None
    frameworks: tuple[str, ...] = ()
    description: str = ""
    inputs: tuple[KPIInput, ...] = ()
    exemplars: dict[str, str] = field(default_factory=dict)

    @property
    def is_qualitative(self) -> bool:
        return self.type == "qualitative"

    @property
    def input_names(self) -> list[str]:
        return [i.name for i in self.inputs]

    def compiled(self) -> Formula:
        return Formula(self.formula, allowed_names=set(self.input_names))


@dataclass(frozen=True)
class Industry:
    name: str
    sector: str
    weights: dict[str, float]
    exclude: tuple[str, ...] = ()
    benchmarks: dict[str, Benchmark] = field(default_factory=dict)


class CatalogError(ValueError):
    pass


class Catalog:
    def __init__(self, kpis: list[KPI], industries: list[Industry]):
        self._kpis = {k.id: k for k in kpis}
        self._industries = {i.name: i for i in industries}
        self._validate()

    # ------------------------------------------------------------------ queries
    @property
    def kpis(self) -> list[KPI]:
        return list(self._kpis.values())

    @property
    def industries(self) -> list[Industry]:
        return sorted(self._industries.values(), key=lambda i: (i.sector, i.name))

    def kpi(self, kpi_id: str) -> KPI:
        try:
            return self._kpis[kpi_id]
        except KeyError as exc:
            raise CatalogError(f"Unknown KPI id: {kpi_id}") from exc

    def industry(self, name: str) -> Industry:
        try:
            return self._industries[name]
        except KeyError as exc:
            raise CatalogError(f"Unknown industry: {name}") from exc

    def search_industries(self, query: str) -> list[Industry]:
        q = query.strip().lower()
        if not q:
            return self.industries
        return [i for i in self.industries if q in i.name.lower() or q in i.sector.lower()]

    def kpis_for(self, industry: str) -> list[KPI]:
        ind = self.industry(industry)
        return [k for k in self._kpis.values() if k.id not in ind.exclude]

    def kpis_by_category(self, industry: str) -> dict[str, list[KPI]]:
        out: dict[str, list[KPI]] = {c: [] for c in CATEGORIES}
        for k in self.kpis_for(industry):
            out[k.category].append(k)
        return out

    def benchmark_for(self, kpi_id: str, industry: str | None = None) -> Benchmark | None:
        if industry:
            override = self.industry(industry).benchmarks.get(kpi_id)
            if override:
                return override
        return self.kpi(kpi_id).benchmark

    def required_inputs(self, industry: str) -> dict[str, KPIInput]:
        """Unique data inputs needed for all quantitative KPIs of an industry."""
        out: dict[str, KPIInput] = {}
        for k in self.kpis_for(industry):
            for inp in k.inputs:
                if inp.name in out:
                    # merge aliases so auto-mapping sees all of them
                    prev = out[inp.name]
                    merged = tuple(dict.fromkeys(prev.aliases + inp.aliases))
                    out[inp.name] = KPIInput(
                        prev.name, prev.description, prev.unit, prev.aggregate, merged
                    )
                else:
                    out[inp.name] = inp
        return out

    # --------------------------------------------------------------- validation
    def _validate(self) -> None:
        for k in self._kpis.values():
            if k.category not in CATEGORIES:
                raise CatalogError(f"{k.id}: unknown category {k.category}")
            if k.is_qualitative:
                if not {"best", "worst"} <= set(k.exemplars):
                    raise CatalogError(f"{k.id}: qualitative KPI needs best/worst exemplars")
                continue
            if not k.formula:
                raise CatalogError(f"{k.id}: quantitative KPI needs a formula")
            k.compiled()  # raises on unsafe / unknown names
            if k.scored and k.benchmark is None:
                raise CatalogError(f"{k.id}: scored KPI needs a benchmark")
        for ind in self._industries.values():
            total = sum(ind.weights.get(c, 0) for c in CATEGORIES)
            if abs(total - 1.0) > 1e-6:
                raise CatalogError(f"{ind.name}: category weights must sum to 1 (got {total})")
            for kid in list(ind.exclude) + list(ind.benchmarks):
                if kid not in self._kpis:
                    raise CatalogError(f"{ind.name}: references unknown KPI {kid}")


def _parse_benchmark(raw: dict | None) -> Benchmark | None:
    if not raw:
        return None
    return Benchmark(best=float(raw["best"]), worst=float(raw["worst"]))


def parse_catalog(data: dict) -> Catalog:
    kpis = []
    for raw in data.get("kpis", []):
        inputs = tuple(
            KPIInput(
                name=i["name"],
                description=i.get("description", ""),
                unit=i.get("unit", ""),
                aggregate=i.get("aggregate", "sum"),
                aliases=tuple(i.get("aliases", [])),
            )
            for i in raw.get("inputs", [])
        )
        bench = _parse_benchmark(raw.get("benchmark"))
        kpis.append(
            KPI(
                id=raw["id"],
                name=raw["name"],
                category=raw["category"],
                type=raw.get("type", "quantitative"),
                unit=raw.get("unit", ""),
                formula=raw.get("formula", ""),
                higher_is_better=bench.higher_is_better
                if bench
                else bool(raw.get("higher_is_better", True)),
                scored=bool(raw.get("scored", True)),
                benchmark=bench,
                frameworks=tuple(raw.get("frameworks", [])),
                description=raw.get("description", ""),
                inputs=inputs,
                exemplars=dict(raw.get("exemplars", {})),
            )
        )
    industries = [
        Industry(
            name=raw["name"],
            sector=raw.get("sector", "Other"),
            weights={k: float(v) for k, v in raw["weights"].items()},
            exclude=tuple(raw.get("exclude", [])),
            benchmarks={k: _parse_benchmark(v) for k, v in raw.get("benchmarks", {}).items()},
        )
        for raw in data.get("industries", [])
    ]
    return Catalog(kpis, industries)


@lru_cache(maxsize=4)
def load_catalog(path: str | None = None) -> Catalog:
    """Load the bundled catalog, or a custom YAML file if ``path`` is given."""
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("esglocal.data").joinpath("kpi_catalog.yaml").read_text("utf-8")
    return parse_catalog(yaml.safe_load(text))
