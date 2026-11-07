import json

from esglocal.agents.advisor import Advisor
from esglocal.agents.base import Agent, Tool, extract_json
from esglocal.agents.orchestrator import ESGPipeline
from esglocal.agents.tools import AgentContext, build_tools
from esglocal.llm.base import ChatResponse, ToolCall
from esglocal.llm.fake import FakeLLM
from esglocal.samples import sample_narratives


def _ctx(catalog, industry, assessment, table):
    return AgentContext(catalog, industry, assessment, table, sample_narratives(), company="ACME")


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    assert extract_json("no json") is None


def test_tool_run_validates_and_catches_errors():
    t = Tool(
        "div",
        "",
        {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a", "b"]},
        lambda a, b: a / b,
    )
    assert "missing required" in t.run({"a": 1})
    assert "ZeroDivisionError" in t.run({"a": 1, "b": 0})
    assert t.run({"a": 4, "b": 2, "evil": "ignored"}) == "2.0"


def test_agent_tool_loop():
    llm = FakeLLM(
        responses=[
            ChatResponse("", tool_calls=[ToolCall("add", {"x": 2, "y": 3}, "c1")]),
            ChatResponse("The answer is 5"),
        ]
    )
    tool = Tool(
        "add", "add", {"type": "object", "properties": {"x": {}, "y": {}}}, lambda x, y: x + y
    )
    res = Agent("t", "tester", "", llm, [tool]).run("2+3?")
    assert res.content == "The answer is 5"
    assert res.steps[0]["tool"] == "add" and res.steps[0]["result"] == "5"
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"] == "5"


def test_agent_step_budget_forces_final_answer():
    looping = ChatResponse("", tool_calls=[ToolCall("noop", {}, "c")])
    llm = FakeLLM(responses=[looping, looping, ChatResponse("final")])
    tool = Tool("noop", "", {"type": "object", "properties": {}}, lambda: "ok")
    res = Agent("t", "tester", "", llm, [tool], max_steps=2).run("go")
    assert res.content == "final" and len(res.steps) == 2


def test_agent_json_repair():
    llm = FakeLLM(responses=[ChatResponse("not json at all"), ChatResponse('{"fixed": true}')])
    res = Agent("t", "tester", "", llm).run("x", expect_json=True)
    assert res.data == {"fixed": True}


def test_tools_against_real_assessment(catalog, industry, assessment, table):
    tools = {t.name: t for t in build_tools(_ctx(catalog, industry, assessment, table))}
    scores = json.loads(tools["get_scores"].run({}))
    assert scores["rating"] == assessment.rating
    kpi = json.loads(tools["get_kpi"].run({"kpi_id": "E-GHG-INT"}))
    assert kpi["benchmark"]["best"] == 5 and "2024" in kpi["history"]
    sim = json.loads(tools["simulate_change"].run({"changes": {"scope1_tco2e": -40, "bogus": 5}}))
    assert sim["overall_after"] > sim["overall_before"]
    assert sim["ignored_unknown_inputs"] == ["bogus"]
    fc = json.loads(tools["forecast_kpi"].run({"kpi_id": "S-TURNOVER"}))
    assert fc["trend"] == "falling"
    assert "error" in json.loads(tools["get_kpi"].run({"kpi_id": "NOPE"}))
    assert "search_documents" not in tools


def _scripted_responder(messages, tools, json_mode):
    """Simulates a model: strategist calls the what-if tool once, others answer in JSON."""
    system = messages[0]["content"]
    if "Strategy Lead" in system and tools and not any(m["role"] == "tool" for m in messages):
        return ChatResponse(
            "", tool_calls=[ToolCall("simulate_change", {"changes": {"scope1_tco2e": -20}}, "s1")]
        )
    if "Strategy Lead" in system:
        return ChatResponse(
            json.dumps(
                {
                    "headline": "Focus on diversity and capex",
                    "priorities": [
                        {
                            "title": "Women in leadership",
                            "kpi_ids": ["S-WOMEN-MGMT", "S-TRAINING-SPEND"],
                            "actions": ["Sponsorship programme"],
                            "horizon": "medium",
                            "expected_impact": "+3 overall (simulated)",
                        }
                    ],
                    "quick_wins": ["Publish pay gap"],
                    "long_term": [],
                }
            )
        )
    if "Assurance Reviewer" in system:
        return ChatResponse('{"verdict": "approve", "issues": []}')
    if "Communications Lead" in system:
        return ChatResponse("## Executive summary\nSolid performance.")
    return ChatResponse(
        json.dumps(
            {
                "summary": "ok",
                "strengths": ["a"],
                "risks": ["b"],
                "recommendations": [
                    {"action": "do x", "kpi_id": "E-RENEW-SHARE"},
                    "plain string rec",
                ],
            }
        )
    )


def test_pipeline_end_to_end(catalog, industry, assessment, table):
    events = []
    llm = FakeLLM(responder=_scripted_responder)
    result = ESGPipeline(
        llm, _ctx(catalog, industry, assessment, table), on_event=events.append
    ).run()
    assert set(result.analyses) == {"Environmental", "Social", "Governance"}
    assert result.analyses["Social"]["recommendations"][1]["action"] == "plain string rec"
    assert result.strategy["priorities"][0]["title"] == "Women in leadership"
    assert any(e["stage"] == "tool" for e in events), "strategist should have used the what-if tool"
    assert result.review["verdict"] in {"approve", "revise"}
    md = result.report_markdown
    assert "# ESG Report — ACME" in md and "## ESG scorecard" in md and "Solid performance." in md
    assert "| E-GHG-INT |" in md


def test_pipeline_flags_hallucinated_kpi_and_revises(catalog, industry, assessment, table):
    calls = {"n": 0}

    def responder(messages, tools, json_mode):
        system = messages[0]["content"]
        if "Strategy Lead" in system:
            calls["n"] += 1
            kpi = "E-MADE-UP" if calls["n"] == 1 else "S-WOMEN-MGMT"
            return ChatResponse(json.dumps({"priorities": [{"title": "t", "kpi_ids": [kpi]}]}))
        if "Assurance Reviewer" in system:
            return ChatResponse('{"verdict": "approve", "issues": []}')
        return _scripted_responder(messages, None, json_mode)

    result = ESGPipeline(
        FakeLLM(responder=responder), _ctx(catalog, industry, assessment, table)
    ).run()
    assert any("E-MADE-UP" in i for i in result.review["issues"])
    assert result.review["verdict"] == "revise" and result.review.get("revised")
    assert result.strategy["priorities"][0]["kpi_ids"] == ["S-WOMEN-MGMT"]


def test_pipeline_survives_model_garbage(catalog, industry, assessment, table):
    llm = FakeLLM(responder=lambda m, t, j: ChatResponse("¯\\_(ツ)_/¯"))
    result = ESGPipeline(llm, _ctx(catalog, industry, assessment, table)).run()
    assert "unavailable" in result.analyses["Environmental"]["summary"]
    assert "## ESG scorecard" in result.report_markdown


def test_advisor_redacts_pii_and_uses_history(catalog, industry, assessment, table):
    llm = FakeLLM()
    adv = Advisor(llm, _ctx(catalog, industry, assessment, table))
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "should be dropped"},
    ]
    res = adv.ask("Email me at boss@corp.example about E-GHG-INT", history)
    sent = llm.calls[0]["messages"]
    assert "boss@corp.example" not in sent[-1]["content"] and "[EMAIL]" in sent[-1]["content"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert res.content.startswith("[fake-model]")
    assert {t["name"] for t in llm.calls[0]["tools"]} >= {"get_kpi", "simulate_change"}
