import pytest
import asyncio

from src.modules.investor_agent.application.dto.state import (
    ClaimCandidate,
    ExtractedDocument,
    FactItem,
    GraphState,
    ReferenceItem,
    RequiredCoverage,
    SearchResult,
    SelectedSource,
    VerifiedClaim,
)
from src.modules.investor_agent.infrastructure.graph.nodes import (
    claim_verifier_node,
    extract_node,
    fact_builder_node,
    search_node,
    source_selection_node,
    writer_node,
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


def test_search_node_retries_simplified_query_when_initial_batch_fails(monkeypatch):
    class FakeTavilyClient:
        def __init__(self, api_key: str):
            self.api_key = api_key
            self.calls = []

        async def search(self, query, search_depth, max_results):
            self.calls.append(query)
            if query in {"query a", "query b", "query c"}:
                raise RuntimeError("temporary upstream failure")
            return {
                "results": [
                    {
                        "url": "https://www.reuters.com/markets/vietnam-fintech",
                        "title": "Vietnam fintech outlook",
                        "content": "Reuters reports on Vietnam fintech momentum.",
                        "published_date": "2026-04-27",
                        "score": 0.93,
                    }
                ]
            }

    monkeypatch.setattr(search_node.settings, "TAVILY_API_KEY", "fake")
    monkeypatch.setattr(search_node, "AsyncTavilyClient", FakeTavilyClient)

    state = GraphState(
        user_query="Vietnam fintech outlook",
        resolved_query="Vietnam fintech outlook",
        sub_queries=["query a", "query b", "query c"],
        required_coverage=RequiredCoverage(
            min_sources=2, required_facets=[]).model_dump(),
    )

    result = asyncio.run(search_node.run(state))

    assert len(result["search_results"]) == 1
    assert result["search_results"][0]["url"] == "https://www.reuters.com/markets/vietnam-fintech"
    assert "search_partial_failures=3" in result["processing_warnings"]
    assert "search_recovered_via_single_query_retry" in result["processing_warnings"]
    assert any(
        warning.startswith("search_failure_samples=")
        for warning in result["processing_warnings"]
    )


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


def test_fact_builder_repairs_invalid_claim_fact_mapping(monkeypatch):
    async def fake_generate_structured_async(self, prompt, response_schema, model_name=None, timeout=None, image_paths=None):
        return fact_builder_node.FactExtractionResult(
            items=[
                FactItem(
                    fact_id="fact_1",
                    statement="Vietnam AI startups raised capital in 2025.",
                    entity="Vietnam AI startups",
                    topic="news",
                    source_url="https://www.reuters.com/example-news",
                    source_title="Reuters funding article",
                    support_strength="strong",
                )
            ],
            candidate_claims=[
                ClaimCandidate(
                    claim_id="claim_1",
                    claim_text="Vietnam AI startups raised capital in 2025.",
                    topic="news",
                    supporting_fact_ids=["missing_fact"],
                )
            ],
        )

    monkeypatch.setattr(
        fact_builder_node.GeminiClient,
        "generate_structured_async",
        fake_generate_structured_async,
    )

    state = GraphState(
        user_query="AI startup funding trend in Vietnam",
        intent="news",
        extracted_documents=[
            ExtractedDocument(
                url="https://www.reuters.com/example-news",
                title="Reuters funding article",
                source_domain="www.reuters.com",
                content="Vietnam AI startups raised capital in 2025.",
                extract_status="success",
            ).model_dump()
        ],
    )

    result = asyncio.run(fact_builder_node.run(state))

    assert len(result["facts"]) == 1
    assert len(result["claims_candidate"]) == 1
    assert result["claims_candidate"][0]["supporting_fact_ids"] == ["fact_1"]
    assert "fact_builder_invalid_claim_fact_mapping" in result["processing_warnings"]
    assert "fact_builder_repaired_claim_fact_mapping" in result["processing_warnings"]


def test_writer_fallback_preserves_upstream_processing_warnings():
    state = GraphState(
        user_query="Gia vang hom nay",
        intent="news",
        processing_warnings=[
            "search_returned_no_results",
            "fact_builder_used_fallback",
            "claim_verifier_no_verified_claims",
        ],
    )

    result = asyncio.run(writer_node.run(state))

    assert result["final_answer"].strip() != ""
    assert "Zero supported claims extracted." in result["processing_warnings"]
    assert "search_returned_no_results" in result["processing_warnings"]
    assert "fact_builder_used_fallback" in result["processing_warnings"]
    assert "claim_verifier_no_verified_claims" in result["processing_warnings"]


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


def test_writer_uses_only_grounded_references(monkeypatch):
    call_count = {"count": 0}

    async def fake_generate_structured_async(self, prompt, response_schema, model_name=None, timeout=None, image_paths=None):
        call_count["count"] += 1
        if response_schema is writer_node.FinalOutput:
            return writer_node.FinalOutput(
                final_answer="Enterprise AI demand remains active [1].",
                references=[
                    ReferenceItem(
                        title="Made up source",
                        url="https://fake.example.com/invented",
                        source_domain="fake.example.com",
                    )
                ],
                caveats=[],
            )
        return writer_node.SuggestedQuestionsOutput(
            suggested_next_questions=[
                "Which sectors are driving enterprise AI demand most strongly?",
                "What budget risks could slow enterprise AI spending next year?",
                "Which vendors are best positioned to capture that demand?",
            ]
        )

    monkeypatch.setattr(
        writer_node.GeminiClient,
        "generate_structured_async",
        fake_generate_structured_async,
    )

    state = GraphState(
        user_query="Enterprise AI demand",
        intent="market_trend",
        verified_claims=[
            VerifiedClaim(
                claim_id="claim_1",
                claim_text="Enterprise AI demand remains active.",
                status="supported",
                supporting_sources=[
                    SelectedSource(
                        url="https://www.reuters.com/technology/enterprise-ai-demand",
                        title="Reuters enterprise AI demand",
                        source_domain="www.reuters.com",
                        selection_reason="trusted",
                        trust_tier="high",
                    )
                ],
                verification_note="Claim supported by fact evidence.",
            ).model_dump()
        ],
    )

    result = asyncio.run(writer_node.run(state))

    assert len(result["references"]) == 1
    assert result["references"][0]["url"] == "https://www.reuters.com/technology/enterprise-ai-demand"
    assert all(ref["url"] != "https://fake.example.com/invented" for ref in result["references"])
    assert len(result["suggested_next_questions"]) == 3
    assert call_count["count"] == 2
    assert "writer_used_grounded_reference_fallback" in result["processing_warnings"]


def test_writer_conflict_only_prompt_includes_disputed_claims(monkeypatch):
    captured = {}

    async def fake_generate_structured_async(self, prompt, response_schema, model_name=None, timeout=None, image_paths=None):
        if response_schema is writer_node.FinalOutput:
            captured["prompt"] = prompt
            return writer_node.FinalOutput(
                final_answer="The available evidence is disputed [1].",
                references=[
                    ReferenceItem(
                        title="Reuters report",
                        url="https://www.reuters.com/markets/disputed-metric",
                        source_domain="www.reuters.com",
                    )
                ],
                caveats=["Treat the claim cautiously."],
            )
        return writer_node.SuggestedQuestionsOutput(
            suggested_next_questions=[
                "Which sources could help validate the disputed metric further?",
                "What downside risks emerge if the reported growth rate is overstated?",
                "Which competing data points should investors compare next?",
            ]
        )

    monkeypatch.setattr(
        writer_node.GeminiClient,
        "generate_structured_async",
        fake_generate_structured_async,
    )

    state = GraphState(
        user_query="Is the metric reliable?",
        intent="news",
        conflicting_claims=[
            VerifiedClaim(
                claim_id="claim_conflict_1",
                claim_text="Reported growth rate reached 30% in 2025.",
                status="conflicting",
                supporting_sources=[
                    SelectedSource(
                        url="https://www.reuters.com/markets/disputed-metric",
                        title="Reuters report",
                        source_domain="www.reuters.com",
                        selection_reason="trusted",
                        trust_tier="high",
                    )
                ],
                verification_note="Conflicts with claim claim_conflict_2",
            ).model_dump()
        ],
    )

    result = asyncio.run(writer_node.run(state))

    assert "Reported growth rate reached 30% in 2025." in captured["prompt"]
    assert "[DISPUTED]" in captured["prompt"]
    assert result["final_answer"].strip() != ""
    assert len(result["suggested_next_questions"]) == 3


def test_writer_skips_suggested_questions_when_grounding_is_degraded(monkeypatch):
    call_count = {"count": 0}

    async def fake_generate_structured_async(self, prompt, response_schema, model_name=None, timeout=None, image_paths=None):
        call_count["count"] += 1
        return writer_node.FinalOutput(
            final_answer="Gold pricing details are not directly confirmed, but several finance sources can be checked [1].",
            references=[
                ReferenceItem(
                    title="Gold source",
                    url="https://www.reuters.com/markets/gold-source",
                    source_domain="www.reuters.com",
                )
            ],
            caveats=["Evidence is indirect."],
        )

    monkeypatch.setattr(
        writer_node.GeminiClient,
        "generate_structured_async",
        fake_generate_structured_async,
    )

    state = GraphState(
        user_query="Gia vang hom nay",
        intent="news",
        processing_warnings=["fact_builder_used_fallback"],
        verified_claims=[
            VerifiedClaim(
                claim_id="claim_1",
                claim_text="Several finance sources publish branded gold prices.",
                status="weakly_supported",
                supporting_sources=[
                    SelectedSource(
                        url="https://www.reuters.com/markets/gold-source",
                        title="Gold source",
                        source_domain="www.reuters.com",
                        selection_reason="trusted",
                        trust_tier="high",
                    )
                ],
                verification_note="Indirect source coverage only.",
            ).model_dump()
        ],
    )

    result = asyncio.run(writer_node.run(state))

    assert result["suggested_next_questions"] == []
    assert "suggested_questions_skipped_low_grounding" in result["processing_warnings"]
    assert call_count["count"] == 1
