import pytest

from esglocal.catalog import CATEGORIES, CatalogError, parse_catalog
from esglocal.mapping import suggest_mapping


def test_bundled_catalog_is_valid(catalog):
    assert len(catalog.kpis) >= 25
    assert {k.category for k in catalog.kpis} == set(CATEGORIES)
    assert len(catalog.industries) >= 12
    for k in catalog.kpis:
        assert k.frameworks, f"{k.id} should reference a reporting framework"


def test_industry_exclusions_and_overrides(catalog):
    bank = {k.id for k in catalog.kpis_for("Banking & Financial Services")}
    assert "S-LTIFR" not in bank and "E-GHG-INT" in bank
    og = catalog.benchmark_for("E-GHG-INT", "Oil & Gas")
    default = catalog.benchmark_for("E-GHG-INT")
    assert og.worst > default.worst


def test_search_industries(catalog):
    assert [i.name for i in catalog.search_industries("bank")] == ["Banking & Financial Services"]
    assert len(catalog.search_industries("")) == len(catalog.industries)


def _minimal(**overrides):
    data = {
        "kpis": [
            {
                "id": "X",
                "name": "x",
                "category": "Environmental",
                "formula": "a / b",
                "benchmark": {"best": 1, "worst": 0},
                "inputs": [{"name": "a"}, {"name": "b"}],
            }
        ],
        "industries": [
            {"name": "I", "weights": {"Environmental": 0.5, "Social": 0.25, "Governance": 0.25}}
        ],
    }
    data.update(overrides)
    return data


def test_catalog_validation_rejects_bad_weights():
    bad = _minimal(industries=[{"name": "I", "weights": {"Environmental": 0.9}}])
    with pytest.raises(CatalogError, match="sum to 1"):
        parse_catalog(bad)


def test_catalog_validation_rejects_unsafe_formula():
    bad = _minimal()
    bad["kpis"][0]["formula"] = "__import__('os')"
    with pytest.raises(Exception):  # noqa: B017
        parse_catalog(bad)


def test_catalog_validation_rejects_unknown_kpi_reference():
    bad = _minimal(
        industries=[
            {
                "name": "I",
                "weights": {"Environmental": 1, "Social": 0, "Governance": 0},
                "exclude": ["NOPE"],
            }
        ]
    )
    with pytest.raises(CatalogError, match="unknown KPI"):
        parse_catalog(bad)


def test_sample_maps_every_input(mapping, inputs):
    assert set(mapping) == set(inputs)
    assert mapping["revenue_meur"] == "Revenue (EUR m)"
    assert mapping["revenue_low_risk_meur"] == "Low risk revenue (EUR m)"
    assert mapping["total_capex_meur"] == "Capex (EUR m)"
    assert mapping["esg_capex_meur"] == "ESG capex (EUR m)"
    assert mapping["total_suppliers"] == "Suppliers"
    assert mapping["suppliers_screened"] == "Screened suppliers"
    assert len(set(mapping.values())) == len(mapping), "a column must not be used twice"


def test_mapping_ignores_unrelated_columns(inputs):
    m = suggest_mapping(["Favourite colour", "Notes", "scope1_tco2e"], inputs)
    assert m == {"scope1_tco2e": "scope1_tco2e"}
