# Contributing to ESG Local AI

Thanks for helping. This project has one non-negotiable rule:

> **User data never leaves the machine.** Do not add cloud APIs, telemetry, analytics,
> remote fonts or CDNs, or any code path that sends data off-device.

## Development setup

```bash
git clone https://github.com/SaadAhmed1122/ESC-Rep-localAI.git
cd ESC-Rep-localAI
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
pytest                 # tests use a fake LLM, so you don't need a model to run them
ruff check src tests && ruff format --check src tests
esglocal ui            # needs Ollama, or use ESGLOCAL_PROVIDER=fake for UI work
```

## Workflow

1. Open an issue first for anything bigger than a small fix.
2. Branch from `main`: `feat/…`, `fix/…`, `docs/…`.
3. Write tests. New logic needs unit tests. New agent behaviour needs a `FakeLLM`-scripted
   test (see `tests/test_agents.py`). New UI pages need an `AppTest` smoke test (see
   `tests/test_ui.py`).
4. Keep `pytest` and `ruff` green. CI runs them on Python 3.10–3.13.
5. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` …).
6. Update the docs (`README.md`, `docs/`) and `CHANGELOG.md` when behaviour changes.

## Good first contributions

- KPIs: add a KPI to `kpi_catalog.yaml` with GRI/ESRS references and a test.
- Industries: add materiality weights and benchmark overrides, citing your source in the PR.
- Translations of the UI.
- Export formats (PDF, DOCX), generated locally.
- Benchmarks: public, properly licensed peer data sets.

## Guidelines

- **Numbers come from code.** Agents interpret computed data. They must never be the source
  of a figure.
- **Model-agnostic.** Don't rely on one model's quirks. Test with a small model such as `llama3.2:3b`.
- **No new heavy dependencies** without discussion. Keep the install lean.
- **Sanitise AI output** with `privacy.safe_markdown` before rendering it.
- **Security issues:** please report them privately. See [SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the MIT License and you
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
