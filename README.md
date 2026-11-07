# ESG Local AI

**Private ESG reporting and a multi-agent AI advisor that run 100% on your own computer.**
No cloud, no API keys, no telemetry. Your sustainability data never leaves the machine.

[![CI](https://github.com/SaadAhmed1122/ESC-Rep-localAI/actions/workflows/ci.yml/badge.svg)](https://github.com/SaadAhmed1122/ESC-Rep-localAI/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

ESG Local AI turns a spreadsheet of operational data into scored ESG KPIs (mapped to
GRI and ESRS), dashboards, what-if simulations and a board-ready report written by a team
of local AI agents. The AI runs on [Ollama](https://ollama.com) or any OpenAI-compatible
server on your machine (LM Studio, llama.cpp, vLLM, LocalAI, Jan).

> Inspired by the feature set of [Ashwin3290/ESG-Reporting](https://github.com/Ashwin3290/ESG-Reporting),
> rebuilt from scratch to run fully offline. See [docs/PLAN.md](docs/PLAN.md) for what changed and why.

---

## Features

| | |
|---|---|
| 📥 **Data in** | Upload CSV/Excel. Columns are **auto-mapped** to KPI inputs (fuzzy matching, no LLM). Multi-site and multi-year data is aggregated correctly. Manual entry is also supported. |
| 📏 **27 KPIs, 15 industries** | Energy, GHG, water, waste, turnover, training, safety, diversity, pay gap, board, ethics, supply chain, sustainable CapEx and more. Each KPI maps to **GRI / ESRS**. Industry materiality weights and benchmark overrides are included. |
| 🔒 **Safe formulas** | KPI formulas run through an AST-whitelisted evaluator. There is no `eval()` anywhere. |
| 📊 **Dashboard** | Gauges, category bars, radars, trend lines with **linear forecasts**, and a full KPI table you can export. |
| 🧪 **What-if simulator** | Change any input (for example −20 % Scope 1) and see the KPI, category and overall scores move. |
| 📝 **Narrative scoring** | Qualitative disclosures (transition plan, human rights, anti-corruption) are scored locally against best-practice exemplars. |
| 🤖 **Multi-agent report** | E, S and G analyst agents → a strategist that **runs simulations itself** → a critic that checks claims and forces a revision → a report writer. All numbers are computed in code, and agents only interpret them. |
| 💬 **Advisor chat** | A tool-calling agent answers questions using your KPIs, scores, forecasts, what-if results and your own documents. |
| 📚 **Document RAG** | Index your own policies and reports (PDF/MD/TXT). Text is encrypted at rest, and PII is redacted before indexing. |
| 🧠 **Any local model** | Pick from installed models, pull new ones, or **import a `.gguf` file** for air-gapped machines. |
| 🛡️ **Privacy by design** | An offline network guard blocks the app's own outbound traffic. The model endpoint must be loopback. The vault is encrypted (scrypt + Fernet), the audit log is tamper-evident, PII is redacted from prompts, and remote links and images are stripped from AI output. |

## Quick start

```bash
# 1. Install a local model runtime (one time). See https://ollama.com
ollama pull qwen2.5:7b          # or any chat model you like
ollama pull nomic-embed-text    # optional: better document search & narrative scoring

# 2. Install ESG Local AI
git clone https://github.com/SaadAhmed1122/ESC-Rep-localAI.git
cd ESC-Rep-localAI
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all]"

# 3. Check everything is local and ready
esglocal doctor

# 4. Launch the app at http://127.0.0.1:8501
esglocal ui
```

On the Home page, click **Load demo company** to explore with a fictional manufacturer.

### Command line

```bash
esglocal industries                                   # list industries and materiality weights
esglocal score sample                                 # deterministic KPIs and scores (no LLM)
esglocal score data.csv --industry "Utilities" --json
esglocal analyze sample --company "Northwind" --out report.md --html report.html
esglocal ask "What are our three weakest KPIs?" sample
esglocal models list | recommended | pull qwen2.5:7b | import ./model.gguf --as my-model
esglocal audit verify                                 # check the audit log hash chain
```

### Docker (app + Ollama)

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen2.5:7b
# open http://127.0.0.1:8501
```

## How your data stays private

1. **Local inference only.** The model endpoint is checked to be loopback (`127.0.0.1`, `::1`, `localhost`). Remote hosts are refused unless you add them to `ESGLOCAL_ALLOWED_HOSTS` yourself.
2. **Egress guard.** In offline mode (the default), the app blocks DNS lookups, connections and UDP sends to any non-local address from its own process.
3. **No browser leaks.** Remote images and links are stripped from AI-generated text before it is rendered, so injected content cannot make the browser leak data.
4. **Encrypted at rest.** Workspaces, reports, chat history and document text live in an encrypted vault in `~/.esglocal`. You can protect it with a passphrase.
5. **Data minimisation.** Emails, phone numbers, IBANs, card numbers and similar PII are redacted before text reaches a prompt or the document index.
6. **No telemetry.** Streamlit usage statistics are disabled and the UI binds to `127.0.0.1`.
7. **You own the data.** Export your workspace as JSON or delete everything with one click.

Details and limits: [docs/PRIVACY.md](docs/PRIVACY.md).

## Configuration

All settings are environment variables. See [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `ESGLOCAL_PROVIDER` | `ollama` | `ollama`, `openai_compat` (LM Studio / llama.cpp / vLLM) or `fake` (tests) |
| `ESGLOCAL_BASE_URL` | `http://127.0.0.1:11434` | Local model endpoint |
| `ESGLOCAL_CHAT_MODEL` | first installed | Chat model name |
| `ESGLOCAL_EMBED_MODEL` | `nomic-embed-text` | Embedding model (falls back to an offline hashing embedder) |
| `ESGLOCAL_OFFLINE` | `1` | Network egress guard |
| `ESGLOCAL_ALLOWED_HOSTS` | — | Extra trusted model hosts, e.g. `ollama` in Docker |
| `ESGLOCAL_PASSPHRASE` | — | Vault passphrase (otherwise a 0600 key file is used) |
| `ESGLOCAL_HOME` | `~/.esglocal` | Data directory |
| `ESGLOCAL_REDACT_PII` | `1` | Redact PII in prompts |
| `ESGLOCAL_AGENT_CONCURRENCY` | `1` | Parallel analyst agents (match `OLLAMA_NUM_PARALLEL`) |

## Documentation

- [Project plan and reverse-engineering notes](docs/PLAN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Agents](docs/AGENTS.md)
- [Models and hardware](docs/MODELS.md)
- [KPI catalog and data format](docs/KPI_CATALOG.md)
- [Privacy and threat model](docs/PRIVACY.md)
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## Disclaimer

Benchmarks in the bundled catalog are **illustrative defaults**. Replace them with your own
peer or target data for real reporting. The output supports decisions. It is not assured
disclosure and does not replace professional advice.

## License

[MIT](LICENSE)
