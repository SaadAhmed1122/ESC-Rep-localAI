# Architecture

```
┌──────────────────────── your machine ────────────────────────────────────────┐
│                                                                              │
│  Streamlit UI (127.0.0.1)      esglocal CLI                                  │
│        │                            │                                        │
│        └──────────────┬─────────────┘                                        │
│                       ▼                                                      │
│  ┌─────────── deterministic core (no LLM) ───────────┐                       │
│  │ catalog.yaml → Catalog   mapping (fuzzy)           │                       │
│  │ calculator (aggregate per period → safe formula)   │                       │
│  │ scoring (normalise, weight)  forecasting, what-if  │                       │
│  │ quality checks   narrative scorer (embeddings)     │                       │
│  └───────────────────────┬────────────────────────────┘                       │
│                          ▼                                                   │
│  ┌─────────── agents ─────────────────────────────────┐   ┌──────────────┐   │
│  │ Agent loop (tool calling)  ← tools: get_kpi,        │   │ rag: chunk → │   │
│  │   get_scores, list_kpis, simulate_change,           │◄──┤ embed → SQLite│  │
│  │   forecast_kpi, search_documents                    │   │ (encrypted)  │   │
│  │ ESGPipeline: E/S/G analysts → strategist → critic   │   └──────────────┘   │
│  │   → (revision) → reporter                           │                      │
│  │ Advisor (chat)                                      │                      │
│  └───────────────────────┬────────────────────────────┘                       │
│                          ▼                                                   │
│  llm/: OllamaClient | OpenAICompatClient | FakeLLM  ── loopback only ──►  Ollama / LM Studio
│                                                                              │
│  security/: Vault (scrypt+Fernet, SQLite)  AuditLog (hash chain)             │
│  privacy/:  network_guard (egress block)   redact (PII)                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | Env-based `Settings`; refuses non-loopback model endpoints |
| `catalog.py` + `data/kpi_catalog.yaml` | KPI definitions, benchmarks, industries; validated on load |
| `formula.py` | AST-whitelisted arithmetic evaluator (`+ - * /`, `min/max/abs`) |
| `mapping.py` | Column → input auto-mapping (token overlap + fuzzy ratio + unit hints, greedy unique assignment) |
| `calculator.py` | `build_period_table` (rename, coerce, aggregate per period by `sum`/`mean`/`last`), `compute_kpis` |
| `scoring.py` | Linear 0–100 normalisation, category means, materiality-weighted overall, coverage, ratings |
| `forecasting.py` | Linear trend forecast, periods-to-target, what-if `simulate` |
| `narrative.py` / `embeddings.py` | Qualitative scoring by similarity to exemplars; Ollama embeddings or offline hashing |
| `quality.py` | Deterministic data-quality issues |
| `agents/` | Agent runtime, tools, specialists, orchestrated pipeline, advisor |
| `rag/` | Document ingest (TXT/MD/PDF/CSV), chunking, SQLite vector store |
| `reporting.py` | Report assembly (deterministic tables + LLM narrative), safe Markdown → HTML |
| `security/` | Encrypted vault, tamper-evident audit log |
| `privacy/` | Network egress guard, PII redaction |
| `workspace.py` | Per-company state persisted in the vault |
| `ui/` | Streamlit app (`app.py` navigation, `views.py` pages, `state.py` services) |
| `cli.py` | `esglocal` commands |

## Key design decisions

1. **Numbers come from code, never from the LLM.** Agents receive computed KPIs and can call
   tools for more data. The report's score and KPI tables are generated deterministically.
   This removes the most damaging kind of hallucination.
2. **Aggregate first, then compute.** Ratios are computed on per-period sums of inputs.
   They are never averages of per-row ratios.
3. **Intensity KPIs for scoring.** Absolute values such as total tCO2e are reported but not
   scored, so a company is not penalised for its size.
4. **No agent framework dependency.** A small tool-calling loop (`agents/base.py`) works with
   any provider, is easy to audit, and ships no telemetry.
5. **Graceful degradation.** If no embedding model is installed, an offline hashing embedder
   is used. If an agent returns malformed JSON, one repair pass runs and safe defaults are
   used after that. If one analyst fails, the rest of the pipeline still finishes.
6. **Provider-neutral message format.** The internal format is OpenAI-like, and each client
   adapts it to its provider (for example, Ollama's `tool_name` on tool messages).

## Data flow for one analysis

1. Upload → `Workspace.records`. `suggest_mapping` produces `{input: column}`.
2. `Workspace.table()` builds the period table and applies manual overrides to the latest period.
3. `assess()` computes KPIs, scores narratives, and produces an `Assessment`.
4. `ESGPipeline.run()` runs the quality checks, the analysts, the strategist (with tool
   calls), the critic, an optional revision, and the reporter, then `build_report()`.
5. The report is stored encrypted in the workspace and can be exported as Markdown or HTML.
