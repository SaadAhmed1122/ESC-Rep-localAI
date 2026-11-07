# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-07

### Added
- KPI catalog with 27 KPIs (24 quantitative and 3 narrative) mapped to GRI/ESRS, and 15
  industries with materiality weights and benchmark overrides.
- Safe AST formula evaluator, fuzzy column auto-mapping, and per-period aggregation
  (aggregate inputs first, then compute).
- Scoring (0–100, materiality-weighted), ratings, coverage, data-quality checks.
- Trend forecasting and a what-if simulator.
- Local narrative scoring (Ollama embeddings with an offline hashing fallback).
- Local LLM layer: Ollama (chat, tools, streaming, pull, delete, GGUF import) and
  OpenAI-compatible localhost servers.
- Multi-agent pipeline (E/S/G analysts → strategist with what-if tool → critic → revision →
  reporter) and a tool-using advisor chat.
- Document RAG with chunk text encrypted at rest and PII redaction.
- Streamlit UI (8 pages), `esglocal` CLI, Docker Compose setup.
- Privacy and security: loopback-only endpoints, process egress guard (TCP, DNS and UDP),
  sanitisation of AI Markdown (no remote images or links), encrypted vault (scrypt n=2^17 +
  Fernet), passphrase rotation that re-encrypts documents, secure delete, and a
  hash-chained audit log.
- 118 tests (unit, agent-scripted, UI smoke), CI on Python 3.10–3.13.
