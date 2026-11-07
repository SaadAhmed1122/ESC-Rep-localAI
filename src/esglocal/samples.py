"""Bundled demo data (a fictional mid-size industrial manufacturer)."""

from __future__ import annotations

import io
import json
from importlib import resources

import pandas as pd

SAMPLE_COMPANY = "Northwind Components (fictional)"
SAMPLE_INDUSTRY = "Industrial Manufacturing"


def sample_csv_text() -> str:
    return resources.files("esglocal.data").joinpath("sample_company.csv").read_text("utf-8")


def sample_dataframe() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(sample_csv_text()))


def sample_narratives() -> dict[str, str]:
    text = resources.files("esglocal.data").joinpath("sample_narratives.json").read_text("utf-8")
    return json.loads(text)
