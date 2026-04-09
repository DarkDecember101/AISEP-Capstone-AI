import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from src.modules.investor_agent.application.dto.state import GraphState
from src.modules.investor_agent.infrastructure.graph import builder
from src.modules.investor_agent.infrastructure.graph.nodes import (
    claim_verifier_node,
    extract_node,
    fact_builder_node,
    followup_resolver,
    planner_node,
    router_node,
    search_node,
    source_selection_node,
    writer_node,
)


def _state_with_history(latest_query: str) -> GraphState:
    return GraphState(
        messages=[
            HumanMessage(content="Xu hướng fintech đông nam á 2024"),
            AIMessage(content="SEA fintech growth is strong in 2024."),
            HumanMessage(content=latest_query),
        ],
        conversation_topic="Xu hướng fintech đông nam á 2024",
        resolved_query="Xu hướng fintech đông nam á 2024",
        last_entities=["sea"],
        last_timeframe="2024",
        verified_claims=[
            {
                "claim_id": "c1",
                "claim_text": "Vietnam digital payments and e-commerce payments grew in 2024.",
                "status": "supported",
                "supporting_sources": [
                    {
                        "url": "https://example.vn/report",
                        "title": "Vietnam Fintech Report",
                        "source_domain": "example.vn",
                        "selection_reason": "trusted",
                        "trust_tier": "high",
                    }
                ],
                "verification_note": "supported",
            }
        ],
        references=[
            {
                "title": "Vietnam Fintech Report",
                "url": "https://example.vn/report",
                "source_domain": "example.vn",
            }
        ],
        final_answer="SEA fintech grew in 2024, with Vietnam as a major contributor.",
    )


def test_followup_resolver_vietnam_fallback_resolution(monkeypatch):
    def fail_llm(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(followup_resolver.GeminiClient,
                        "generate_structured", fail_llm)

    state = _state_with_history("Việt Nam thì sao")
    result = asyncio.run(followup_resolver.run(state))

    assert result["is_followup"] is True
    assert result["followup_type"] == "entity_drilldown"
    assert "việt nam" in result["resolved_query"].lower()
    assert result["reuse_previous_verified_claims"] is True
    assert result["search_decision"] == "reuse_plus_search"


@pytest.mark.parametrize(
    "query,expected_type",
    [
        ("Indonesia thì sao", "entity_drilldown"),
        ("Nguồn nào nói vậy", "source_request"),
        ("Tóm tắt ngắn hơn", "summary_request"),
    ],
)
def test_followup_resolver_required_followup_types(monkeypatch, query, expected_type):
    def fail_llm(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(followup_resolver.GeminiClient,
                        "generate_structured", fail_llm)

    state = _state_with_history(query)
    result = asyncio.run(followup_resolver.run(state))

    assert result["is_followup"] is True
    assert result["followup_type"] == expected_type


def test_followup_recency_update_triggers_fresh_search(monkeypatch):
    def fail_llm(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(followup_resolver.GeminiClient,
                        "generate_structured", fail_llm)

    state = _state_with_history("Có cập nhật mới hơn không?")
    result = asyncio.run(followup_resolver.run(state))

    assert result["followup_type"] == "recency_update"
    assert result["requires_fresh_search"] is True
    assert result["search_decision"] == "fresh_search"


def test_planner_uses_resolved_query_in_prompt(monkeypatch):
    captured = {"prompt": ""}

    def fake_generate_structured(self, prompt, response_schema, model_name=None, image_paths=None):
        captured["prompt"] = prompt
        return planner_node.PlannerOutput(
            sub_queries=["Vietnam fintech 2024 trends"],
            rationale="ok",
            required_facets=[],
            min_sources=1,
        )

    monkeypatch.setattr(planner_node.GeminiClient,
                        "generate_structured", fake_generate_structured)

    state = GraphState(
        user_query="Việt Nam thì sao",
        resolved_query="What are the 2024 fintech trends in Vietnam within Southeast Asia context?",
        intent="market_trend",
        search_decision="reuse_plus_search",
    )

    asyncio.run(planner_node.run(state))

    assert "What are the 2024 fintech trends in Vietnam within Southeast Asia context?" in captured[
        "prompt"]


async def _stub_search_run(state):
    if state.is_followup and state.followup_type == "entity_drilldown":
        assert "vietnam" in (state.resolved_query or "").lower(
        ) or "việt nam" in (state.resolved_query or "").lower()
    return {
        "search_results": [
            {
                "query": state.resolved_query,
                "title": "Vietnam fintech growth 2024",
                "url": "https://news.vn/vn-fintech-2024",
                "snippet": "Vietnam fintech digital payments grew in 2024",
                "source_domain": "news.vn",
                "score": 0.9,
            }
        ],
        "processing_warnings": list(state.processing_warnings or []),
    }


async def _stub_source_selection_run(state):
    result = state.search_results[0]
    return {
        "selected_sources": [
            {
                "url": result["url"],
                "title": result["title"],
                "source_domain": result["source_domain"],
                "selection_reason": "test",
                "trust_tier": "high",
            }
        ],
        "processing_warnings": list(state.processing_warnings or []),
    }


async def _stub_extract_run(state):
    source = state.selected_sources[0]
    return {
        "extracted_documents": [
            {
                "url": source["url"],
                "title": source["title"],
                "source_domain": source["source_domain"],
                "content": "Vietnam fintech payments and e-commerce transactions increased significantly in 2024.",
                "extract_status": "success",
            }
        ],
        "processing_warnings": list(state.processing_warnings or []),
    }


async def _stub_fact_builder_run(state):
    return {
        "facts": [
            {
                "fact_id": "f1",
                "statement": "Vietnam fintech payments grew in 2024.",
                "entity": "Vietnam",
                "topic": "market_trend",
                "date_or_timeframe": "2024",
                "numeric_value": None,
                "unit": None,
                "source_url": "https://news.vn/vn-fintech-2024",
                "source_title": "Vietnam fintech growth 2024",
                "support_strength": "strong",
            }
        ],
        "claims_candidate": [
            {
                "claim_id": "c1",
                "claim_text": "Vietnam showed strong fintech payment growth in 2024.",
                "topic": "market_trend",
                "supporting_fact_ids": ["f1"],
            }
        ],
        "processing_warnings": list(state.processing_warnings or []),
    }


async def _stub_claim_verifier_run(state):
    return {
        "verified_claims": [
            {
                "claim_id": "c1",
                "claim_text": "Vietnam showed strong fintech payment growth in 2024.",
                "status": "supported",
                "supporting_sources": [
                    {
                        "url": "https://news.vn/vn-fintech-2024",
                        "title": "Vietnam fintech growth 2024",
                        "source_domain": "news.vn",
                        "selection_reason": "test",
                        "trust_tier": "high",
                    }
                ],
                "verification_note": "supported",
            }
        ],
        "unsupported_claims": [],
        "conflicting_claims": [],
        "coverage_assessment": {
            "coverage_status": "sufficient",
            "missing_facets": [],
            "needs_repair_loop": False,
        },
        "processing_warnings": list(state.processing_warnings or []),
    }


async def _stub_writer_run(state):
    claim_texts = [claim["claim_text"]
                   for claim in (state.verified_claims or [])]
    if not claim_texts and state.previous_verified_claims:
        claim_texts = [claim["claim_text"]
                       for claim in state.previous_verified_claims]
    answer = " ".join(claim_texts) if claim_texts else "No data"
    refs = state.references or state.previous_references or [
        {
            "title": "Vietnam fintech growth 2024",
            "url": "https://news.vn/vn-fintech-2024",
            "source_domain": "news.vn",
        }
    ]
    return {
        "final_answer": answer,
        "references": refs,
        "caveats": [],
        "writer_notes": ["stub_writer"],
        "processing_warnings": list(state.processing_warnings or []),
        "grounding_summary": {
            "verified_claim_count": len(claim_texts),
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 0,
            "reference_count": len(refs),
            "coverage_status": "sufficient" if claim_texts else "insufficient",
        },
    }


def test_multiturn_vietnam_followup_continuity(monkeypatch):
    def fake_generate_structured(self, prompt, response_schema, model_name=None, image_paths=None):
        if response_schema is router_node.RouterOutput:
            return router_node.RouterOutput(
                intent="market_trend",
                confidence="high",
                reasoning="in scope",
                is_followup_sensitive=True,
            )
        if response_schema is planner_node.PlannerOutput:
            return planner_node.PlannerOutput(
                sub_queries=["Vietnam fintech trends 2024"],
                rationale="drilldown",
                required_facets=[],
                min_sources=1,
            )
        if response_schema is followup_resolver.ResolverOutput:
            return followup_resolver.ResolverOutput(
                is_followup=True,
                followup_type="entity_drilldown",
                resolved_query="What are the 2024 fintech trends in Vietnam within Southeast Asia context?",
                resolved_topic="SEA fintech trends 2024",
                resolved_entities=["Vietnam"],
                resolved_timeframe="2024",
                reuse_previous_verified_claims=True,
                requires_fresh_search=True,
                reasoning="country drilldown",
            )
        raise RuntimeError(f"Unexpected response schema: {response_schema}")

    monkeypatch.setattr(router_node.GeminiClient,
                        "generate_structured", fake_generate_structured)

    monkeypatch.setattr(search_node, "run", _stub_search_run)
    monkeypatch.setattr(source_selection_node, "run",
                        _stub_source_selection_run)
    monkeypatch.setattr(extract_node, "run", _stub_extract_run)
    monkeypatch.setattr(fact_builder_node, "run", _stub_fact_builder_run)
    monkeypatch.setattr(claim_verifier_node, "run", _stub_claim_verifier_run)
    monkeypatch.setattr(writer_node, "run", _stub_writer_run)

    graph = builder.build_investor_agent_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "thread-vn-followup"}}

    turn1 = asyncio.run(graph.ainvoke({"messages": [HumanMessage(
        content="Xu hướng fintech đông nam á 2024")]}, config=config))
    assert turn1["intent"] == "market_trend"

    turn2 = asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="Việt Nam thì sao")]}, config=config))

    assert turn2["intent"] != "out_of_scope"
    assert "vietnam" in (turn2.get("resolved_query", "").lower())
    assert "vietnam" in (turn2.get("final_answer", "").lower())
    assert "no data" not in (turn2.get("final_answer", "").lower())


def test_multiturn_source_request_reuse_only_skips_search(monkeypatch):
    call_counter = {"count": 0}

    async def fail_if_search_called(state):
        call_counter["count"] += 1
        if call_counter["count"] >= 2:
            raise AssertionError("search node should not run for reuse_only")
        return await _stub_search_run(state)

    def fake_generate_structured(self, prompt, response_schema, model_name=None):
        if response_schema is router_node.RouterOutput:
            return router_node.RouterOutput(
                intent="market_trend",
                confidence="high",
                reasoning="in scope",
                is_followup_sensitive=True,
            )
        if response_schema is followup_resolver.ResolverOutput:
            return followup_resolver.ResolverOutput(
                is_followup=True,
                followup_type="source_request",
                resolved_query="Provide sources for prior claim on Vietnam fintech trends 2024",
                resolved_topic="SEA fintech trends 2024",
                resolved_entities=["Vietnam"],
                resolved_timeframe="2024",
                reuse_previous_verified_claims=True,
                requires_fresh_search=False,
                reasoning="source request",
            )
        if response_schema is planner_node.PlannerOutput:
            return planner_node.PlannerOutput(
                sub_queries=["Vietnam fintech trends 2024"],
                rationale="not_used",
                required_facets=[],
                min_sources=1,
            )
        raise RuntimeError(f"Unexpected response schema: {response_schema}")

    monkeypatch.setattr(router_node.GeminiClient,
                        "generate_structured", fake_generate_structured)
    monkeypatch.setattr(search_node, "run", fail_if_search_called)

    monkeypatch.setattr(source_selection_node, "run",
                        _stub_source_selection_run)
    monkeypatch.setattr(extract_node, "run", _stub_extract_run)
    monkeypatch.setattr(fact_builder_node, "run", _stub_fact_builder_run)
    monkeypatch.setattr(claim_verifier_node, "run", _stub_claim_verifier_run)
    monkeypatch.setattr(writer_node, "run", _stub_writer_run)

    graph = builder.build_investor_agent_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "thread-source-reuse"}}

    asyncio.run(graph.ainvoke({"messages": [HumanMessage(
        content="Xu hướng fintech đông nam á 2024")]}, config=config))
    turn2 = asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="Nguồn nào nói vậy?")]}, config=config))

    assert turn2["search_decision"] == "reuse_only"
    refs = turn2.get("references", [])
    assert refs
    assert "http" in refs[0]["url"]
