import asyncio

import pytest

from src.modules.investor_agent.application.dto.state import GraphState
from src.modules.investor_agent.application.services.scope_guard import decide_scope, get_refusal
from src.modules.investor_agent.infrastructure.graph.builder import route_after_router
from src.modules.investor_agent.infrastructure.graph.nodes import router_node


@pytest.mark.parametrize(
    "query,expected_intent,expected_out_of_scope",
    [
        ("Xu hướng Fintech Đông Nam Á 2024", "market_trend", False),
        ("Quy định thanh toán số Indonesia 2024", "regulation", False),
        ("Các startup fintech nổi bật ở SEA năm 2024", None, False),
        ("Đối thủ của Xendit là ai?", "competitor_context", False),
        ("Thời tiết Hà Nội hôm nay", "out_of_scope", True),
        ("2+2 bằng bao nhiêu?", "out_of_scope", True),
    ],
)
def test_scope_guard_policy_required_queries(query, expected_intent, expected_out_of_scope):
    decision = decide_scope(
        query=query,
        router_intent="out_of_scope",
        router_confidence="low",
        router_reasoning="uncertain",
    )

    assert decision.is_out_of_scope is expected_out_of_scope
    if expected_intent is not None:
        assert decision.final_intent == expected_intent


def test_scope_guard_router_in_scope_always_wins():
    decision = decide_scope(
        query="2+2 bằng bao nhiêu?",
        router_intent="market_trend",
        router_confidence="high",
        router_reasoning="classified by router as market trend",
    )

    assert decision.is_out_of_scope is False
    assert decision.final_intent == "market_trend"
    assert decision.heuristic_used is False


def test_scope_guard_high_confidence_out_of_scope_refuses():
    decision = decide_scope(
        query="Thời tiết Hà Nội hôm nay",
        router_intent="out_of_scope",
        router_confidence="high",
        router_reasoning="weather query",
    )

    assert decision.is_out_of_scope is True
    assert decision.final_intent == "out_of_scope"
    assert decision.heuristic_used is False


def test_scope_guard_greeting_short_circuits_to_fami_intro():
    decision = decide_scope(
        query="Xin chao",
        router_intent="out_of_scope",
        router_confidence="low",
        router_reasoning="short greeting",
    )

    assert decision.is_out_of_scope is True
    assert decision.final_intent == "out_of_scope"
    assert decision.heuristic_used is True
    assert "heuristic_greeting_short_circuit" in decision.reason
    assert get_refusal("Xin chao").startswith("Xin ch\u00e0o, t\u00f4i l\u00e0 Fami")


def test_route_after_router_contract():
    in_scope_state = GraphState(user_query="q", intent="market_trend")
    out_scope_state = GraphState(user_query="q", intent="out_of_scope")

    assert route_after_router(in_scope_state) == "planner"
    assert route_after_router(out_scope_state) == "writer"


def test_router_node_output_schema(monkeypatch):
    def fake_generate_structured(self, prompt, response_schema, model_name=None, image_paths=None):
        return router_node.RouterOutput(
            intent="market_trend",
            confidence="high",
            reasoning="Vietnamese market trend query",
            is_followup_sensitive=False,
        )

    monkeypatch.setattr(router_node.GeminiClient,
                        "generate_structured", fake_generate_structured)

    state = GraphState(
        user_query="Xu hướng Fintech Đông Nam Á 2024",
        resolved_query="Xu hướng Fintech Đông Nam Á 2024",
    )

    result = asyncio.run(router_node.run(state))

    assert result["intent"] == "market_trend"
    assert result["router_confidence"] == "high"
    assert "router_reasoning" in result
    assert "router_is_followup_sensitive" in result


def test_router_low_confidence_out_scope_is_rescued_by_heuristic(monkeypatch):
    def fake_generate_structured(self, prompt, response_schema, model_name=None, image_paths=None):
        return router_node.RouterOutput(
            intent="out_of_scope",
            confidence="low",
            reasoning="not sure",
            is_followup_sensitive=False,
        )

    monkeypatch.setattr(router_node.GeminiClient,
                        "generate_structured", fake_generate_structured)

    state = GraphState(
        user_query="Xu hướng Fintech Đông Nam Á 2024",
        resolved_query="Xu hướng Fintech Đông Nam Á 2024",
    )

    result = asyncio.run(router_node.run(state))

    assert result["intent"] == "market_trend"
    assert "scope_guard_heuristic_fallback_used" in result["processing_warnings"]
    assert "out_of_scope_query" not in result["processing_warnings"]


def test_followup_resolved_query_stays_in_scope(monkeypatch):
    def fake_generate_structured(self, prompt, response_schema, model_name=None, image_paths=None):
        return router_node.RouterOutput(
            intent="market_trend",
            confidence="medium",
            reasoning="follow-up still on same market topic",
            is_followup_sensitive=True,
        )

    monkeypatch.setattr(router_node.GeminiClient,
                        "generate_structured", fake_generate_structured)

    state = GraphState(
        user_query="Còn Indonesia thì sao?",
        resolved_query="Xu hướng Fintech Đông Nam Á 2024, tập trung vào Indonesia",
    )

    result = asyncio.run(router_node.run(state))

    assert result["intent"] == "market_trend"
    assert result["router_is_followup_sensitive"] is True
    assert "out_of_scope_query" not in result["processing_warnings"]
