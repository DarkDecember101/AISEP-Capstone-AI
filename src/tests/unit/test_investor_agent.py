import pytest
import asyncio
from src.modules.investor_agent.application.dto.state import GraphState
from src.modules.investor_agent.infrastructure.graph.builder import build_investor_agent_graph


def test_router_node():
    from src.modules.investor_agent.infrastructure.graph.nodes import router_node

    state = GraphState(
        user_query="What is the regulation for crypto in Singapore 2024?")
    res = asyncio.run(router_node.run(state))
    assert "intent" in res
    assert res["intent"] in ["regulation", "market_trend",
                             "news", "competitor_context", "mixed", "out_of_scope"]


def test_route_after_router_out_of_scope_short_circuit():
    from src.modules.investor_agent.infrastructure.graph.builder import route_after_router

    state = GraphState(user_query="weather", intent="out_of_scope")
    assert route_after_router(state) == "writer"


def test_full_graph_empty_mock():
    # Demonstrating the graph compilation
    graph = build_investor_agent_graph()
    assert graph is not None
    # We would mock gemini_client and tavily_client for a true integration test.
