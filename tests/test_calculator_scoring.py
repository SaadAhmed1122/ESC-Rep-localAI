import pandas as pd
import pytest

from esglocal.calculator import build_period_table, compute_kpi, table_from_values, to_number
from esglocal.catalog import Benchmark
from esglocal.forecasting import forecast, periods_to_target, simulate
from esglocal.narrative import NarrativeScorer
from esglocal.scoring import assess, normalise, rating_for


def test_ratio_aggregates_inputs_not_row_ratios(catalog, inputs):
    # Two sites in one year: site A 10 injuries / 1M h, site B 0 / 9M h.
    # Correct LTIFR = 10 / 10M h * 1e6 = 1.0 (mean of row ratios would wrongly give 5.0)
    df = pd.DataFrame({"year": [2024, 2024], "lti": [10, 0], "hours": [1_000_000, 9_000_000]})
    table = build_period_table(df, {"lost_time_injuries": "lti", "hours_worked": "hours"}, inputs)
    res = compute_kpi(catalog.kpi("S-LTIFR"), table)
    assert res.value == pytest.approx(1.0)
    assert res.period == "2024"


def test_mean_aggregate_for_stock_values(catalog, inputs):
    df = pd.DataFrame({"year": [2024, 2024], "board": [10, 10], "indep": [6, 6]})
    table = build_period_table(
        df, {"board_size": "board", "independent_directors": "indep"}, inputs
    )
    res = compute_kpi(catalog.kpi("G-BOARD-INDEP"), table)
    assert res.value == pytest.approx(60.0)


def test_history_and_latest_period(assessment):
    ghg = assessment.result("E-GHG-INT")
    assert list(ghg.history) == ["2022", "2023", "2024"]
    assert ghg.period == "2024"
    assert ghg.value == pytest.approx((19000 + 10000) / 960)


def test_missing_inputs_reported(catalog):
    table = table_from_values({"scope1_tco2e": 10})
    res = compute_kpi(catalog.kpi("E-GHG-INT"), table)
    assert res.value is None and "Missing inputs" in res.error


def test_division_by_zero_reported(catalog):
    table = table_from_values({"waste_recycled_t": 1, "waste_total_t": 0})
    res = compute_kpi(catalog.kpi("E-WASTE-RECYCLED"), table)
    assert res.value is None and "Division by zero" in res.error


def test_to_number_cleans_strings():
    s = to_number(pd.Series(["1,234", "€ 5", "12%", "n/a"]))
    assert s.tolist()[:3] == [1234.0, 5.0, 12.0]
    assert pd.isna(s.iloc[3])


@pytest.mark.parametrize(
    "value,best,worst,expected",
    [
        (5, 10, 0, 50),
        (20, 10, 0, 100),
        (-5, 10, 0, 0),
        (5, 0, 10, 50),
        (0, 0, 10, 100),
        (3, 3, 3, 100),
    ],
)
def test_normalise(value, best, worst, expected):
    assert normalise(value, Benchmark(best, worst)) == pytest.approx(expected)


def test_rating_bands():
    assert rating_for(85) == "Leader"
    assert rating_for(45) == "Developing"
    assert rating_for(None) == "n/a"


def test_assessment_rollup(assessment):
    assert assessment.coverage == 1.0
    w = assessment.weights
    expected = sum(assessment.category_scores[c] * w[c] for c in w)
    assert assessment.overall == pytest.approx(expected)
    assert 0 <= assessment.overall <= 100
    # absolute emissions are reported but never scored
    assert assessment.result("E-GHG-TOTAL").score is None
    assert len(assessment.weakest(3)) == 3


def test_overall_reweights_when_category_missing(catalog, industry):
    table = table_from_values({"scope1_tco2e": 50, "scope2_tco2e": 50, "revenue_meur": 10})
    a = assess(catalog, industry, table)
    assert a.category_scores["Social"] is None
    assert a.overall == pytest.approx(a.category_scores["Environmental"])


def test_benchmark_override(catalog, industry, table):
    base = assess(catalog, industry, table)
    over = assess(catalog, industry, table, benchmark_overrides={"E-GHG-INT": Benchmark(0, 31)})
    assert over.result("E-GHG-INT").score < base.result("E-GHG-INT").score


def test_narrative_scorer_prefers_best_practice(catalog):
    k = catalog.kpi("G-ETHICS")
    scorer = NarrativeScorer()
    good = scorer(k.exemplars["best"], k.exemplars["best"], k.exemplars["worst"])
    bad = scorer(k.exemplars["worst"], k.exemplars["best"], k.exemplars["worst"])
    assert good > 0.8 and bad < 0.2
    assert scorer("", "a", "b") == 0.0


def test_forecast_linear():
    fc = forecast({"2022": 10, "2023": 8, "2024": 6}, horizon=2)
    assert fc.periods == ["2025", "2026"]
    assert fc.values == pytest.approx([4, 2])
    assert fc.direction == "falling" and fc.r2 == pytest.approx(1.0)
    assert forecast({"2024": 1}) is None
    assert periods_to_target({"2022": 10, "2023": 8, "2024": 6}, 2) == pytest.approx(2)


def test_simulate_emission_cut_improves_score(catalog, industry, table, assessment):
    sim = simulate(catalog, industry, table, {"scope1_tco2e": -50, "scope2_tco2e": -50})
    assert sim.result("E-GHG-INT").score > assessment.result("E-GHG-INT").score
    assert table.loc["2024", "scope1_tco2e"] == 19000, "simulation must not mutate input table"
