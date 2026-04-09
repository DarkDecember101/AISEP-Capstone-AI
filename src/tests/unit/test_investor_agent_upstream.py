import pytest
import asyncio

from src.modules.investor_agent.application.dto.state import (
    ClaimCandidate,
    ExtractedDocument,
    FactItem,
    GraphState,
    RequiredCoverage,
    SearchResult,
    SelectedSource,
)
from src.modules.investor_agent.infrastructure.graph.nodes import (
    claim_verifier_node,
    extract_node,
    fact_builder_node,
    source_selection_node,
)


def test_source_selection_keeps_usable_sources_when_llm_fails(monkeypatch):
    def fail_llm(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(source_selection_node.GeminiClient,
                        "generate_structured", fail_llm)

    state = GraphState(
        user_query="AI market trend SEA",
        intent="market_trend",
        search_results=[
            SearchResult(
                query="ai market sea",
                title="Reuters report",
                url="https://www.reuters.com/markets/asia/ai-growth",
                snippet="Reuters says growth in AI demand...",
                source_domain="www.reuters.com",
                score=0.91,
            ).model_dump(),
            SearchResult(
                query="ai market sea",
                title="Gov update",
                url="https://www.gov.sg/ai-initiative",
                snippet="Policy update",
                source_domain="www.gov.sg",
                score=0.88,
            ).model_dump(),
        ],
    )

    result = asyncio.run(source_selection_node.run(state))
    assert len(result["selected_sources"]) > 0


def test_extract_parsing_returns_non_empty_documents(monkeypatch):
    class FakeTavilyClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        async def extract(self, urls):
            return {
                "results": [
                    {
                        "url": urls[0],
                        "content": "This is extracted content with enough length. " * 20,
                    }
                ]
            }

    monkeypatch.setattr(extract_node.settings, "TAVILY_API_KEY", "fake")
    monkeypatch.setattr(extract_node, "AsyncTavilyClient", FakeTavilyClient)

    state = GraphState(
        selected_sources=[
            SelectedSource(
                url="https://www.reuters.com/markets/asia/ai-growth",
                title="Reuters report",
                source_domain="www.reuters.com",
                selection_reason="trusted",
                trust_tier="high",
            ).model_dump()
        ]
    )

    result = asyncio.run(extract_node.run(state))
    assert len(result["extracted_documents"]) == 1
    assert result["extracted_documents"][0]["extract_status"] in [
        "success", "partial"]
    assert len(result["extracted_documents"][0]["content"].strip()) > 0


def test_fact_builder_fallback_creates_facts_and_claims(monkeypatch):
    def fail_llm(self, *args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(fact_builder_node.GeminiClient,
                        "generate_structured", fail_llm)

    state = GraphState(
        user_query="AI startup funding trend in Vietnam",
        intent="news",
        extracted_documents=[
            ExtractedDocument(
                url="https://www.reuters.com/example-news",
                title="Reuters funding article",
                source_domain="www.reuters.com",
                content="Vietnam AI startups raised funding in 2025 and investors increased allocation to applied AI companies.",
                extract_status="success",
            ).model_dump()
        ],
    )

    result = asyncio.run(fact_builder_node.run(state))
    assert len(result["facts"]) > 0
    assert len(result["claims_candidate"]) > 0


def test_claim_verifier_promotes_supported_claims():
    state = GraphState(
        user_query="AI regulation in Singapore",
        intent="regulation",
        selected_sources=[
            SelectedSource(
                url="https://www.mas.gov.sg/regulation/ai-guidance",
                title="MAS guidance",
                source_domain="www.mas.gov.sg",
                selection_reason="authoritative",
                trust_tier="high",
            ).model_dump()
        ],
        facts=[
            FactItem(
                fact_id="fact_1",
                statement="MAS published AI risk governance guidance.",
                entity="MAS",
                topic="regulation",
                source_url="https://www.mas.gov.sg/regulation/ai-guidance",
                source_title="MAS guidance",
                support_strength="strong",
            ).model_dump()
        ],
        claims_candidate=[
            ClaimCandidate(
                claim_id="claim_1",
                claim_text="Singapore has official AI governance guidance from MAS.",
                topic="regulation",
                supporting_fact_ids=["fact_1"],
            ).model_dump()
        ],
        required_coverage=RequiredCoverage(
            min_sources=1, required_facets=[]).model_dump(),
    )

    result = asyncio.run(claim_verifier_node.run(state))
    assert len(result["verified_claims"]) >= 1
    assert result["coverage_assessment"]["coverage_status"] in [
        "sufficient", "conflicting"]
