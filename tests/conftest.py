from __future__ import annotations

import pytest

from esglocal.calculator import build_period_table
from esglocal.catalog import load_catalog
from esglocal.mapping import suggest_mapping
from esglocal.narrative import NarrativeScorer
from esglocal.samples import SAMPLE_INDUSTRY, sample_dataframe, sample_narratives
from esglocal.scoring import assess


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.esglocal during tests."""
    monkeypatch.setenv("ESGLOCAL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ESGLOCAL_PASSPHRASE", raising=False)
    yield
    from esglocal.privacy import network_guard

    network_guard.uninstall()  # CLI commands install the guard; don't leak it between tests


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def industry():
    return SAMPLE_INDUSTRY


@pytest.fixture(scope="session")
def sample_df():
    return sample_dataframe()


@pytest.fixture(scope="session")
def inputs(catalog, industry):
    return catalog.required_inputs(industry)


@pytest.fixture(scope="session")
def mapping(sample_df, inputs):
    return suggest_mapping(sample_df.columns, inputs)


@pytest.fixture(scope="session")
def table(sample_df, mapping, inputs):
    return build_period_table(sample_df, mapping, inputs)


@pytest.fixture
def assessment(catalog, industry, table):
    return assess(catalog, industry, table, sample_narratives(), NarrativeScorer())
