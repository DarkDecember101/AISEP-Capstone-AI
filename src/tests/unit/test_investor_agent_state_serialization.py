import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.modules.investor_agent.application.dto.state import (
    GraphState,
    SearchResult,
    VerifiedClaim,
    SelectedSource,
    as_model_list,
)


def test_graph_state_default_lists_are_not_shared():
    a = GraphState()
    b = GraphState()

    a.sub_queries.append("q1")
    a.search_results.append({"url": "https://a", "title": "A"})

    assert b.sub_queries == []
    assert b.search_results == []


def test_graph_state_accepts_dict_payload_and_roundtrips_dump_load():
    state = GraphState(
        user_query="ai trend",
        search_results=[
            {
                "query": "ai trend",
                "title": "Reuters",
                "url": "https://www.reuters.com/x",
                "snippet": "snippet",
                "source_domain": "www.reuters.com",
                "score": 0.9,
            }
        ],
        verified_claims=[
            {
                "claim_id": "c1",
                "claim_text": "Claim",
                "status": "supported",
                "supporting_sources": [
                    {
                        "url": "https://www.reuters.com/x",
                        "title": "Reuters",
                        "source_domain": "www.reuters.com",
                        "selection_reason": "trusted",
                        "trust_tier": "high",
                    }
                ],
                "verification_note": "ok",
            }
        ],
    )

    dumped = state.model_dump()
    restored = GraphState(**dumped)

    assert isinstance(restored.search_results[0], dict)
    assert isinstance(restored.verified_claims[0], dict)
    assert restored.search_results[0]["url"].startswith("https://")


def test_runtime_model_parse_from_dict_state():
    state = GraphState(
        search_results=[
            SearchResult(
                query="ai trend",
                title="Reuters",
                url="https://www.reuters.com/x",
                snippet="snippet",
                source_domain="www.reuters.com",
                score=0.9,
            ).model_dump()
        ],
        verified_claims=[
            VerifiedClaim(
                claim_id="c1",
                claim_text="Claim",
                status="supported",
                supporting_sources=[
                    SelectedSource(
                        url="https://www.reuters.com/x",
                        title="Reuters",
                        source_domain="www.reuters.com",
                        selection_reason="trusted",
                        trust_tier="high",
                    )
                ],
                verification_note="ok",
            ).model_dump()
        ],
    )

    search_models = as_model_list(state.search_results, SearchResult)
    claim_models = as_model_list(state.verified_claims, VerifiedClaim)

    assert search_models and search_models[0].url == "https://www.reuters.com/x"
    assert claim_models and claim_models[0].status == "supported"


def test_checkpoint_save_restore_with_dict_state_payloads():
    async def _run():
        builder = StateGraph(GraphState)

        async def test_node(state: GraphState):
            return {
                "search_results": [
                    SearchResult(
                        query="ai trend",
                        title="Reuters",
                        url="https://www.reuters.com/x",
                        snippet="snippet",
                        source_domain="www.reuters.com",
                        score=0.9,
                    ).model_dump()
                ],
                "verified_claims": [
                    {
                        "claim_id": "c1",
                        "claim_text": "Claim",
                        "status": "supported",
                        "supporting_sources": [
                            {
                                "url": "https://www.reuters.com/x",
                                "title": "Reuters",
                                "source_domain": "www.reuters.com",
                                "selection_reason": "trusted",
                                "trust_tier": "high",
                            }
                        ],
                        "verification_note": "ok",
                    }
                ],
            }

        builder.add_node("test_node", test_node)
        builder.set_entry_point("test_node")
        builder.add_edge("test_node", END)
        graph = builder.compile(checkpointer=MemorySaver())

        config = {"configurable": {"thread_id": "serialization-test-thread"}}
        await graph.ainvoke({"user_query": "ai trend"}, config=config)
        snapshot = await graph.aget_state(config)
        values = snapshot.values

        assert isinstance(values.get("search_results", [])[0], dict)
        assert isinstance(values.get("verified_claims", [])[0], dict)
        assert values["verified_claims"][0]["status"] == "supported"

    asyncio.run(_run())
