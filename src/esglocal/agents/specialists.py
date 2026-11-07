"""ESG specialist agents: role prompts and output contracts."""

from __future__ import annotations

from esglocal.agents.base import Agent, Tool

ANALYSIS_SCHEMA = """Return ONLY a JSON object:
{
  "summary": "2-4 sentence assessment",
  "strengths": ["..."],
  "risks": ["..."],
  "recommendations": [
    {"action": "...", "kpi_id": "<KPI id or empty>", "impact": "high|medium|low",
     "effort": "high|medium|low", "horizon": "short|medium|long"}
  ]
}"""

STRATEGY_SCHEMA = """Return ONLY a JSON object:
{
  "headline": "one sentence",
  "priorities": [
    {"title": "...", "kpi_ids": ["..."], "actions": ["..."],
     "expected_impact": "quantified where a simulation supports it", "horizon": "short|medium|long"}
  ],
  "quick_wins": ["..."],
  "long_term": ["..."]
}"""

REVIEW_SCHEMA = """Return ONLY a JSON object:
{"verdict": "approve" | "revise", "issues": ["specific problem and how to fix it"]}"""

CATEGORY_FOCUS = {
    "Environmental": "climate (GHG, energy, transition planning), water and waste/circularity",
    "Social": "workforce (turnover, training), health & safety, diversity & pay equity, "
    "customers and human rights",
    "Governance": "board composition, ethics & anti-corruption, compliance, supply-chain "
    "due diligence and sustainable capital allocation",
}


def category_analyst(category: str, llm, tools: list[Tool], max_steps: int) -> Agent:
    return Agent(
        name=f"{category.lower()}_analyst",
        role=f"{category} Analyst, a senior ESG specialist in {CATEGORY_FOCUS[category]}",
        instructions=(
            "Assess the company's performance in your category against the benchmarks. "
            "Scores are 0-100 where 100 = best-practice benchmark. Reference KPI ids. "
            "Recommendations must be concrete, feasible and tied to a KPI where possible. "
            "You may call tools for detail or trends, but you already have the core data.\n"
            + ANALYSIS_SCHEMA
        ),
        llm=llm,
        tools=tools,
        max_steps=max_steps,
        temperature=0.2,
    )


def strategist(llm, tools: list[Tool], max_steps: int) -> Agent:
    return Agent(
        name="strategist",
        role="ESG Strategy Lead who turns analyses into a prioritised improvement roadmap",
        instructions=(
            "Combine the category analyses into 3-5 priorities ranked by impact on the overall "
            "ESG score and by materiality weight for the industry. Use the simulate_change tool "
            "to quantify the score impact of your main levers (e.g. -20% scope1_tco2e) and quote "
            "the simulated result. Only use input names that the tool reports as available.\n"
            + STRATEGY_SCHEMA
        ),
        llm=llm,
        tools=tools,
        max_steps=max_steps,
        temperature=0.3,
    )


def critic(llm) -> Agent:
    return Agent(
        name="critic",
        role="independent ESG Assurance Reviewer",
        instructions=(
            "Review the proposed strategy against the data. Flag: numbers not supported by the "
            "data, KPI ids that do not exist, recommendations unrelated to the weakest areas, "
            "vague or non-actionable items, and greenwashing language. Approve if it is sound.\n"
            + REVIEW_SCHEMA
        ),
        llm=llm,
        temperature=0.0,
    )


def reporter(llm) -> Agent:
    return Agent(
        name="reporter",
        role="ESG Communications Lead writing for the board and sustainability team",
        instructions=(
            "Write clear, factual Markdown. No hype, no greenwashing. Use only the figures "
            "provided. Structure: '## Executive summary' (one paragraph), '## Key findings' "
            "(bullets per E/S/G), '## Recommended roadmap' (numbered, with horizon), "
            "'## Data gaps & next steps'. Do NOT include score tables — they are added "
            "automatically."
        ),
        llm=llm,
        temperature=0.3,
    )


def default_analysis(category: str, reason: str) -> dict:
    return {
        "summary": f"{category} analysis unavailable: {reason}",
        "strengths": [],
        "risks": [],
        "recommendations": [],
    }


def normalise_analysis(data: dict | None, category: str) -> dict:
    if not isinstance(data, dict):
        return default_analysis(category, "model did not return valid JSON")
    out = default_analysis(category, "")
    out["summary"] = str(data.get("summary") or "").strip() or out["summary"]
    for key in ("strengths", "risks"):
        out[key] = [str(x) for x in data.get(key) or [] if str(x).strip()]
    recs = []
    for r in data.get("recommendations") or []:
        if isinstance(r, str):
            r = {"action": r}
        if isinstance(r, dict) and r.get("action"):
            recs.append(
                {
                    "action": str(r.get("action")),
                    "kpi_id": str(r.get("kpi_id") or ""),
                    "impact": str(r.get("impact") or "medium"),
                    "effort": str(r.get("effort") or "medium"),
                    "horizon": str(r.get("horizon") or "medium"),
                }
            )
    out["recommendations"] = recs
    return out


def normalise_strategy(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    priorities = []
    for p in data.get("priorities") or []:
        if isinstance(p, str):
            p = {"title": p}
        if isinstance(p, dict) and p.get("title"):
            priorities.append(
                {
                    "title": str(p["title"]),
                    "kpi_ids": [str(k) for k in p.get("kpi_ids") or []],
                    "actions": [str(a) for a in p.get("actions") or []],
                    "expected_impact": str(p.get("expected_impact") or ""),
                    "horizon": str(p.get("horizon") or "medium"),
                }
            )
    return {
        "headline": str(data.get("headline") or ""),
        "priorities": priorities,
        "quick_wins": [str(x) for x in data.get("quick_wins") or []],
        "long_term": [str(x) for x in data.get("long_term") or []],
    }
