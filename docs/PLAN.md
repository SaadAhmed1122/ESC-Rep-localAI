# Project Plan — ESG Local AI

## 1. Goal

A privacy-first ESG (Environmental, Social, Governance) reporting and advisory
platform that runs **entirely on the user's machine**. All AI inference goes
through a local model runtime (Ollama by default, or any OpenAI-compatible
local server such as LM Studio / llama.cpp / vLLM). No API keys, no telemetry,
no cloud.

Inspired by the feature set of
[Ashwin3290/ESG-Reporting](https://github.com/Ashwin3290/ESG-Reporting)
(industry KPI selection, CSV upload, dashboards, multi-agent advisor). This is a
**clean-room re-implementation**: that repository has no license, so no code or
data was copied. The KPI catalog here was written from scratch and mapped to
public frameworks (GRI, ESRS).

## 2. Reverse-engineering findings (reference app)

| Area | Reference behaviour | Issue | Our approach |
|---|---|---|---|
| LLM | CrewAI + Gemini cloud API | Data leaves the machine; needs API key | Local LLM via Ollama/OpenAI-compatible localhost; network egress guard |
| KPI formulas | Python `eval()` on strings | Arbitrary code execution | AST-whitelisted safe evaluator |
| Aggregation | Mean of per-row ratios | Mathematically wrong for ratios | Aggregate inputs per period, then compute |
| Benchmarks | Absolute values (e.g. MWh) | Penalises large companies | Intensity KPIs (per €m revenue / per FTE) |
| Narrative scoring | sentence-transformers download | Pulls model from internet at runtime | Ollama embeddings, offline hashing fallback |
| Persistence | Plain files in `session_files/` | Unencrypted user data | Encrypted vault (scrypt + Fernet) |
| Paths | `data\kpis.json` | Windows-only | `importlib.resources` |
| Tests | None | — | pytest suite, fake LLM, CI |

## 3. Architecture (see [ARCHITECTURE.md](ARCHITECTURE.md))

```
UI (Streamlit, 127.0.0.1)  ─┐
CLI (esglocal)              ├─> core: catalog · formula · mapping · calculator · scoring · forecasting
                            ├─> agents: orchestrator → specialists (E/S/G) → strategist → critic → reporter
                            ├─> llm: ollama | openai-compatible | fake   (loopback-only)
                            ├─> rag: chunk → embed → SQLite vector store
                            └─> privacy/security: network guard · PII redaction · vault · audit log
```

## 4. Milestones

1. **Core engine** — catalog, safe formulas, column auto-mapping, calculator, scoring.
2. **Local LLM layer** — Ollama + OpenAI-compatible clients, model manager (list/pull), loopback enforcement.
3. **Agents** — tool-calling agent loop, ESG specialists, orchestrated pipeline with critic step, advisor chat with tools.
4. **Advanced features** — what-if simulator, trend forecasting, narrative scoring, document RAG, report export (Markdown/HTML).
5. **Privacy & security** — egress guard, encrypted vault, hash-chained audit log, PII redaction, telemetry off.
6. **UI + CLI** — Streamlit multipage app, `esglocal` CLI (`doctor`, `models`, `analyze`, `ui`).
7. **Quality** — unit tests, lint (ruff), CI on GitHub Actions, agent-driven code & security review.
8. **Open-source packaging** — README, docs, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, templates, Docker.

## 5. Verification log (v0.1.0)

| Step | How | Result |
|---|---|---|
| Unit, agent and UI tests | `pytest` (fake LLM, Streamlit AppTest) | 118 passing, 82% line coverage |
| Lint | `ruff check` + `ruff format --check` | clean |
| Real local model run | `esglocal analyze sample` on `gemma4:26b` via Ollama | Full pipeline in ~3 min. The strategist called the what-if tool on its own, and the critic triggered a revision. |
| UI boot | `esglocal ui` | Healthy, listening on 127.0.0.1 only |
| Security review | security-reviewer agent | 1 HIGH (browser-side exfiltration through rendered Markdown images) and 4 MEDIUM, all fixed with regression tests |
| Code review | python-reviewer agent | 3 HIGH (period ordering, date round-trip, locale numbers) and several MEDIUM, all fixed with regression tests |
| Release hygiene | opensource-sanitizer agent | PASS: no secrets, PII or copied third-party content |
| Docker | `docker compose up` (app + Ollama), run by the maintainer | Image builds and runs. The app works against the containerised Ollama. |

## 6. Roadmap (post-v0.1, contributions welcome)

- PDF export and branded report templates
- Multi-company / portfolio comparison
- User-editable benchmark sets per region
- CSRD/ESRS datapoint checklist mode
- Local speech input for the advisor (whisper.cpp)
- Role-based access for shared on-prem deployments
