# Agents

All agents run on your local model through `esglocal.agents.base.Agent`, a small loop:

```
system prompt (role + instructions) + history + task
  └─► model.chat(tools=…)
        ├─ tool calls? → run tools locally → append results → loop (≤ max_steps)
        └─ final text  → (optional) parse JSON → one repair pass if malformed
```

## The analysis pipeline (`ESGPipeline`)

| Stage | Agent | Tools | Output |
|---|---|---|---|
| 1. Data quality | *deterministic code* | — | Issues: missing inputs, out-of-range %, suspicious jumps, low coverage |
| 2. Analysis | Environmental, Social, Governance analysts (can run in parallel) | `get_kpi`, `forecast_kpi`, `search_documents`* | JSON: summary, strengths, risks, recommendations |
| 3. Strategy | Strategist | `simulate_change`, `get_scores`, `get_kpi` | JSON roadmap with priorities and quantified impact |
| 4. Review | Critic + deterministic checks | — | `approve` / `revise` plus issues (unknown KPI ids, ignored weak KPIs, unsupported claims) |
| 4b. Revision | Strategist | — | Revised roadmap that addresses the review issues |
| 5. Report | Reporter | — | Markdown narrative. Score and KPI tables are added by code. |

\* Only when documents are indexed.

The critic's deterministic checks always force a revision when the strategy cites a KPI id
that does not exist, or when it ignores all three weakest KPIs. The critic model alone
cannot approve its way past them.

## Advisor (chat)

`Advisor` is one agent with every tool. It sees the last 12 chat messages. PII in the
question is redacted before the question reaches the model.

## Tools

| Tool | What it does |
|---|---|
| `list_kpis(category?)` | KPIs with value, unit, score and status |
| `get_kpi(kpi_id)` | Full detail: history, benchmark, inputs, frameworks, narrative excerpt |
| `get_scores()` | Overall score, rating, category scores, weights, coverage, weakest and strongest KPIs |
| `simulate_change(changes)` | What-if: `{input: %change}` on the latest period, returns before and after |
| `forecast_kpi(kpi_id, horizon?)` | Linear trend forecast |
| `search_documents(query, k?)` | Retrieval over the user's indexed documents |

Tools are read-only and run in-process. They cannot write files, run code or reach the
network. AI output has remote links and images stripped before it is rendered. A prompt
injection hidden in an uploaded document can therefore, at worst, distort the text the
model writes. It cannot exfiltrate data.

## Adding an agent or tool

1. Add a tool in `agents/tools.py` (`Tool(name, description, json_schema, fn)`). Keep it
   read-only and return JSON-serialisable data.
2. Add a role in `agents/specialists.py` with an explicit output contract.
3. Wire it into `orchestrator.py` and emit progress events with `self._emit(stage, status, msg)`.
4. Add a test with `FakeLLM(responder=...)` that scripts the model's tool calls (see
   `tests/test_agents.py`).

## Tips for local models

- Models with native tool calling work best: Qwen 2.5/3, Llama 3.1+, Mistral Nemo, Gemma 3+.
- 7–8B models handle the pipeline. 14B+ models write noticeably better reports.
- If your model "thinks" (reasoning traces), keep `ESGLOCAL_THINK=0` for speed.
- Set `ESGLOCAL_AGENT_CONCURRENCY` to match `OLLAMA_NUM_PARALLEL` to run E, S and G in parallel.
