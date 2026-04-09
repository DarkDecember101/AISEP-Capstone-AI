from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import StateGraph, END

from src.modules.investor_agent.application.dto.state import GraphState
from src.modules.investor_agent.infrastructure.graph.nodes import (
    followup_resolver,
    router_node,
    planner_node,
    search_node,
    source_selection_node,
    extract_node,
    fact_builder_node,
    claim_verifier_node,
    writer_node
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


def should_repair_loop(state: GraphState) -> str:
    """Conditional Edge logic for repair loop."""
    coverage_assessment = getattr(state, "coverage_assessment", None)
    needs_repair_loop = False
    if isinstance(coverage_assessment, dict):
        needs_repair_loop = bool(
            coverage_assessment.get("needs_repair_loop", False))
    elif hasattr(coverage_assessment, "needs_repair_loop"):
        needs_repair_loop = bool(
            getattr(coverage_assessment, "needs_repair_loop", False))

    if state.loop_count < 2 and needs_repair_loop:
        return "search"
    return "writer"


def route_after_router(state: GraphState) -> str:
    if getattr(state, "intent", None) == "out_of_scope":
        return "writer"
    if getattr(state, "search_decision", "full_search") == "reuse_only":
        return "writer"
    return "planner"


def build_investor_agent_graph(checkpointer: BaseCheckpointSaver | None = None):
    """
    Build and compile the investor-agent LangGraph.

    Parameters
    ----------
    checkpointer : BaseCheckpointSaver | None
        If *None*, the graph is compiled **without** a checkpointer (useful for
        the stateless ``/research`` endpoint or unit tests).  Pass an explicit
        checkpointer for stateful ``/chat`` flows.
    """
    builder = StateGraph(GraphState)

    # 0. Followup Resolver
    builder.add_node("followup_resolver", followup_resolver.run)
    # 1. Router
    builder.add_node("router", router_node.run)
    # 2. Planner
    builder.add_node("planner", planner_node.run)
    # 3. Search
    builder.add_node("search", search_node.run)
    # 4. Source Selection
    builder.add_node("source_selection", source_selection_node.run)
    # 5. Extract
    builder.add_node("extract", extract_node.run)
    # 6. Fact Builder
    builder.add_node("fact_builder", fact_builder_node.run)
    # 7. Claim Verifier
    builder.add_node("claim_verifier", claim_verifier_node.run)
    # 8. Writer
    builder.add_node("writer", writer_node.run)

    # Flow Definitions
    builder.set_entry_point("followup_resolver")
    builder.add_edge("followup_resolver", "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            "writer": "writer",
            "planner": "planner",
        },
    )
    builder.add_edge("planner", "search")
    builder.add_edge("search", "source_selection")
    builder.add_edge("source_selection", "extract")
    builder.add_edge("extract", "fact_builder")
    builder.add_edge("fact_builder", "claim_verifier")

    # Conditional Repair Loop
    builder.add_conditional_edges(
        "claim_verifier",
        should_repair_loop,
        {
            "search": "search",
            "writer": "writer"
        }
    )

    builder.add_edge("writer", END)

    return builder.compile(checkpointer=checkpointer)
