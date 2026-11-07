import json

from esglocal.cli import main
from esglocal.samples import SAMPLE_INDUSTRY, sample_dataframe, sample_narratives
from esglocal.security.vault import Vault
from esglocal.workspace import Workspace, list_workspaces, load_workspace, save_workspace


def test_cli_score_json(capsys):
    assert main(["score", "sample", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["industry"] == SAMPLE_INDUSTRY and out["coverage_pct"] == 100.0


def test_cli_industries_and_sample(capsys, tmp_path):
    assert main(["industries"]) == 0
    assert "Banking & Financial Services" in capsys.readouterr().out
    out = tmp_path / "s.csv"
    assert main(["sample", "--out", str(out)]) == 0
    assert out.read_text().startswith("Year,")


def test_cli_score_requires_industry_for_custom_file(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("Year,Revenue\n2024,10\n")
    try:
        main(["score", str(f)])
    except SystemExit as exc:
        assert "--industry" in str(exc)


def test_cli_rejects_remote_model_endpoint(capsys):
    rc = main(["doctor", "--base-url", "https://api.example.com"])
    assert rc != 0


def test_cli_analyze_with_fake_provider(tmp_path, capsys):
    out = tmp_path / "r.md"
    html = tmp_path / "r.html"
    rc = main(
        [
            "analyze",
            "sample",
            "--provider",
            "fake",
            "--company",
            "ACME",
            "--out",
            str(out),
            "--html",
            str(html),
        ]
    )
    assert rc == 0
    assert "# ESG Report — ACME" in out.read_text()
    assert html.read_text().startswith("<!doctype html>")
    assert main(["audit", "verify"]) == 0


def test_workspace_roundtrip(tmp_path, catalog):
    vault = Vault(tmp_path / "v.db")
    ws = Workspace(name="acme", company="ACME", industry=SAMPLE_INDUSTRY)
    df = sample_dataframe()
    ws.set_data(df)
    from esglocal.mapping import suggest_mapping

    ws.mapping = suggest_mapping(df.columns, catalog.required_inputs(SAMPLE_INDUSTRY))
    ws.narratives = sample_narratives()
    ws.manual_values = {"fatalities": 3}
    save_workspace(vault, ws)
    assert list_workspaces(vault) == ["acme"]
    loaded = load_workspace(vault, "acme")
    assert loaded.company == "ACME" and len(loaded.records) == 3
    a = loaded.assessment(catalog)
    assert a.result("S-FATALITIES").value == 3, "manual value overrides latest period"
    assert load_workspace(vault, "missing").records == []
