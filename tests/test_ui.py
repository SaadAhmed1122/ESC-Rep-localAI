"""Headless UI smoke tests (Streamlit AppTest + fake LLM, no browser, no network)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import esglocal.ui  # noqa: E402

APP = Path(esglocal.ui.__file__).with_name("app.py")
PAGES = ["home", "data", "dashboard", "what_if", "agents", "advisor", "documents", "settings_page"]


@pytest.fixture(autouse=True)
def _fake_provider(monkeypatch):
    monkeypatch.setenv("ESGLOCAL_PROVIDER", "fake")


def _page(page: str, with_sample: bool = True):
    from esglocal.ui import state, views

    if state.vault() is None:
        state.open_vault()
    if with_sample and not state.workspace().industry:
        views.load_sample()
    getattr(views, page)()


def test_app_entrypoint_renders():
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("ESG Local AI" in t.value for t in at.title)
    demo = next(b for b in at.button if b.label == "Load demo company")
    demo.click().run()
    assert not at.exception
    assert any("Loaded" in s.value for s in at.success)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_with_demo_data(page):
    at = AppTest.from_function(_page, args=(page,), default_timeout=60).run()
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", ["dashboard", "what_if", "agents", "advisor"])
def test_pages_prompt_for_data_when_empty(page):
    at = AppTest.from_function(_page, args=(page, False), default_timeout=60).run()
    assert not at.exception
    assert any("Data & KPIs" in i.value for i in at.info)


def test_agents_page_runs_pipeline():
    at = AppTest.from_function(_page, args=("agents",), default_timeout=120).run()
    next(b for b in at.button if b.label == "Run agents").click().run()
    assert not at.exception
    assert any("# ESG Report" in m.value for m in at.markdown)


def test_advisor_chat_roundtrip():
    at = AppTest.from_function(_page, args=("advisor",), default_timeout=60).run()
    at.chat_input[0].set_value("What is our weakest KPI?").run()
    assert not at.exception
    assert any("fake-model" in m.value for m in at.markdown)
