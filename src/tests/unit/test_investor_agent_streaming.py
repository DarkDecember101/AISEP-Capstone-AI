import json

from fastapi.testclient import TestClient

from src.main import app
from src.modules.investor_agent.api import router as investor_router_module
from src.modules.investor_agent.application.services.final_assembler import (
    FALLBACK_CONFLICT,
    FALLBACK_NO_EVIDENCE,
    assemble_final_response,
)
from src.shared.config.settings import settings


AUTH_HEADERS = {"X-Internal-Token": settings.AISEP_INTERNAL_TOKEN}


class StubGraph:
    def __init__(self, final_state, graph_output_override=None):
        self.final_state = final_state
        self.graph_output_override = graph_output_override

    async def ainvoke(self, initial_state, config=None):
        return self.final_state

    async def astream_events(self, initial_state, config=None, version="v1"):
        for node in [
            "followup_resolver",
            "router",
            "planner",
            "search",
            "source_selection",
            "extract",
            "fact_builder",
            "claim_verifier",
            "writer",
        ]:
            yield {"event": "on_chain_start", "name": node}

        yield {
            "event": "on_chain_end",
            "name": "writer",
            "data": {"output": self.final_state},
        }
        yield {
            "event": "on_chain_end",
            "name": "LangGraph",
            "data": {"output": self.graph_output_override if self.graph_output_override is not None else self.final_state},
        }

    async def aget_state(self, config):
        class Snapshot:
            def __init__(self, values):
                self.values = values

        return Snapshot(self.final_state)


class OutOfScopeStubGraph:
    def __init__(self, final_state):
        self.final_state = final_state

    async def ainvoke(self, initial_state, config=None):
        return self.final_state

    async def astream_events(self, initial_state, config=None, version="v1"):
        for node in ["followup_resolver", "router", "writer"]:
            yield {"event": "on_chain_start", "name": node}

        yield {
            "event": "on_chain_end",
            "name": "router",
            "data": {"output": {"intent": "out_of_scope"}},
        }
        yield {
            "event": "on_chain_end",
            "name": "writer",
            "data": {"output": self.final_state},
        }
        yield {
            "event": "on_chain_end",
            "name": "LangGraph",
            "data": {"output": self.final_state},
        }

    async def aget_state(self, config):
        class Snapshot:
            def __init__(self, values):
                self.values = values

        return Snapshot(self.final_state)


def _collect_sse_events(response_text: str):
    events = []
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            events.append("[DONE]")
        else:
            events.append(json.loads(payload))
    return events


def test_final_assembler_empty_writer_answer_fallback_no_evidence():
    payload = assemble_final_response(
        {
            "intent": "mixed",
            "final_answer": "   ",
            "verified_claims": [],
            "conflicting_claims": [],
            "references": [],
            "caveats": [],
            "processing_warnings": [],
        }
    )

    assert payload["final_answer"] == FALLBACK_NO_EVIDENCE
    assert "writer_returned_empty_answer" in payload["processing_warnings"]
    assert payload["fallback_triggered"] is True


def test_final_assembler_conflict_fallback_when_answer_empty():
    payload = assemble_final_response(
        {
            "final_answer": "",
            "verified_claims": [{"claim_id": "1"}],
            "conflicting_claims": [{"claim_id": "2"}],
            "references": [],
            "caveats": [],
            "processing_warnings": [],
        }
    )

    assert payload["final_answer"] == FALLBACK_CONFLICT
    assert any("conflict" in caveat.lower() for caveat in payload["caveats"])


def test_final_assembler_repairs_invalid_citation_index_and_filters_refs():
    payload = assemble_final_response(
        {
            "intent": "market_trend",
            "resolved_query": "AI trend SEA",
            "final_answer": "Growth is visible [1] while projection detail is uncertain [3].",
            "verified_claims": [{"claim_id": "1"}],
            "conflicting_claims": [],
            "references": [
                {
                    "title": "Ref 1",
                    "url": "https://example.org/real-ref-1",
                    "source_domain": "example.org",
                },
                {
                    "title": "Ref 2",
                    "url": "https://example.org/real-ref-2",
                    "source_domain": "example.org",
                },
            ],
            "processing_warnings": [],
        }
    )

    assert "[3]" not in payload["final_answer"]
    assert len(payload["references"]) == 1
    assert payload["references"][0]["url"] == "https://example.org/real-ref-1"
    assert "invalid_citation_indexes_detected" in payload["processing_warnings"]


def test_final_assembler_keeps_only_used_reference_ids_and_remaps_numbering():
    payload = assemble_final_response(
        {
            "intent": "news",
            "resolved_query": "Funding update",
            "final_answer": "A notable round closed recently [2].",
            "verified_claims": [{"claim_id": "1"}],
            "references": [
                {
                    "title": "Unused",
                    "url": "https://example.org/unused",
                    "source_domain": "example.org",
                },
                {
                    "title": "Used",
                    "url": "https://example.org/used",
                    "source_domain": "example.org",
                },
            ],
        }
    )

    assert payload["final_answer"].endswith("[1].")
    assert len(payload["references"]) == 1
    assert payload["references"][0]["url"] == "https://example.org/used"


def test_final_assembler_keeps_grouped_citations_and_references():
    payload = assemble_final_response(
        {
            "intent": "news",
            "resolved_query": "gold prices in Vietnam",
            "final_answer": "Nguoi dung co the tham khao cac nguon sau cho gia vang [1, 2, 3].",
            "verified_claims": [{"claim_id": "1"}],
            "references": [
                {
                    "title": "Source 1",
                    "url": "https://example.net/source-1",
                    "source_domain": "example.net",
                },
                {
                    "title": "Source 2",
                    "url": "https://example.net/source-2",
                    "source_domain": "example.net",
                },
                {
                    "title": "Source 3",
                    "url": "https://example.net/source-3",
                    "source_domain": "example.net",
                },
            ],
        }
    )

    assert payload["final_answer"].endswith("[1, 2, 3].")
    assert len(payload["references"]) == 3
    assert "unused_references_removed_no_citations" not in payload["processing_warnings"]


def test_final_assembler_conflict_caveat_syncs_conflicting_count():
    payload = assemble_final_response(
        {
            "intent": "regulation",
            "final_answer": "Available evidence is mixed.",
            "caveats": ["There is conflicting evidence across sources."],
            "references": [],
            "grounding_summary": {
                "verified_claim_count": 1,
                "weakly_supported_claim_count": 0,
                "conflicting_claim_count": 0,
                "unsupported_claim_count": 0,
                "reference_count": 0,
                "coverage_status": "sufficient",
            },
        }
    )

    assert payload["grounding_summary"]["conflicting_claim_count"] > 0
    assert payload["grounding_summary"]["coverage_status"] == "conflicting"


def test_final_assembler_downgrades_sufficient_when_references_missing():
    payload = assemble_final_response(
        {
            "intent": "market_trend",
            "final_answer": "Demand appears resilient.",
            "references": [],
            "grounding_summary": {
                "verified_claim_count": 1,
                "weakly_supported_claim_count": 0,
                "conflicting_claim_count": 0,
                "unsupported_claim_count": 0,
                "reference_count": 0,
                "coverage_status": "sufficient",
            },
        }
    )

    assert payload["grounding_summary"]["coverage_status"] == "insufficient"
    assert "coverage_downgraded_missing_references" in payload["processing_warnings"]


def test_final_assembler_returns_fami_greeting_for_salutation_query():
    payload = assemble_final_response(
        {
            "intent": "out_of_scope",
            "user_query": "Hello",
            "final_answer": "",
            "references": [],
            "caveats": [],
            "processing_warnings": [],
        }
    )

    assert payload["final_answer"].startswith("Xin ch\u00e0o, t\u00f4i l\u00e0 Fami")
    assert payload["caveats"] == []
    assert payload["suggested_next_questions"] == []
    assert "greeting_query" in payload["processing_warnings"]


def test_stream_uses_assembler_output(monkeypatch):
    """/chat/stream must emit final_answer + final_metadata with assembler output."""
    final_state = {
        "intent": "news",
        "resolved_query": "startup funding in SEA",
        "final_answer": "",
        "verified_claims": [],
        "conflicting_claims": [],
        "references": [],
        "caveats": ["Insufficient evidence found during research."],
        "suggested_next_questions": [
            "Nen thu hep pham vi theo quoc gia nao truoc?",
            "Ban co muon dao sau vao startup stage cu the nao khong?",
            "Co nen uu tien tin tuc funding hay nhu cau thi truong?"
        ],
        "processing_warnings": [],
        "grounding_summary": {
            "verified_claim_count": 0,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 1,
            "reference_count": 0,
            "coverage_status": "insufficient",
        },
    }

    monkeypatch.setattr(investor_router_module,
                        "_chat_graph", StubGraph(final_state))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/investor-agent/chat/stream",
        json={"query": "Any update?", "thread_id": "thread-test"},
        headers=AUTH_HEADERS,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = "".join(list(stream_response.iter_text()))

    events = _collect_sse_events(stream_text)
    final_answer_events = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_answer"]
    metadata_events = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_metadata"]

    assert final_answer_events, "final_answer event must exist"
    assert final_answer_events[-1]["content"] == FALLBACK_NO_EVIDENCE

    assert metadata_events, "final_metadata event must exist"
    metadata = metadata_events[-1]
    assert "processing_warnings" in metadata
    assert "writer_returned_empty_answer" in metadata["processing_warnings"]
    assert "references" in metadata
    assert "caveats" in metadata
    assert len(metadata["suggested_next_questions"]) == 3
    assert "grounding_summary" in metadata
    assert metadata["thread_id_used"] == "thread-test"

    assert events[-1] == "[DONE]"


def test_stream_surfaces_missing_thread_id_warning(monkeypatch):
    final_state = {
        "intent": "news",
        "resolved_query": "startup funding in SEA",
        "final_answer": "Evidence remains thin but a few signals are visible.",
        "verified_claims": [],
        "conflicting_claims": [],
        "references": [],
        "caveats": ["Insufficient evidence found during research."],
        "processing_warnings": [],
        "grounding_summary": {
            "verified_claim_count": 0,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 1,
            "reference_count": 0,
            "coverage_status": "insufficient",
        },
    }

    monkeypatch.setattr(
        investor_router_module,
        "_chat_graph",
        StubGraph(final_state),
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/investor-agent/chat/stream",
        json={"query": "Any update?"},
        headers=AUTH_HEADERS,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = "".join(list(stream_response.iter_text()))

    events = _collect_sse_events(stream_text)
    metadata_events = [
        e for e in events if isinstance(e, dict) and e.get("type") == "final_metadata"
    ]

    assert metadata_events
    metadata = metadata_events[-1]
    assert "missing_thread_id_from_upstream" in metadata["processing_warnings"]
    assert "default_thread_used" in metadata["processing_warnings"]
    assert metadata["thread_id_used"] == "default_thread"


def test_stream_recovers_from_empty_graph_end_output(monkeypatch):
    final_state = {
        "intent": "market_trend",
        "resolved_query": "AI market outlook 2026",
        "final_answer": "Verified trend indicates continued enterprise AI spend growth [1].",
        "verified_claims": [{"claim_id": "claim_1", "status": "supported"}],
        "conflicting_claims": [],
        "references": [
            {
                "title": "Reuters AI spending",
                "url": "https://www.reuters.com/technology/ai-spending",
                "source_domain": "www.reuters.com",
            }
        ],
        "caveats": [],
        "suggested_next_questions": [
            "Which buyer segments are driving that enterprise AI spend?",
            "What risks could slow the outlook over the next 12 months?",
            "Which competitors are best positioned to capture that demand?"
        ],
        "processing_warnings": [],
        "grounding_summary": {
            "verified_claim_count": 1,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 0,
            "reference_count": 1,
            "coverage_status": "sufficient",
        },
    }

    monkeypatch.setattr(
        investor_router_module,
        "_chat_graph",
        StubGraph(final_state=final_state, graph_output_override={}),
    )
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/investor-agent/chat/stream",
        json={"query": "Outlook?", "thread_id": "thread-recover"},
        headers=AUTH_HEADERS,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = "".join(list(stream_response.iter_text()))

    events = _collect_sse_events(stream_text)
    final_answer_events = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_answer"]
    metadata_events = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_metadata"]

    assert final_answer_events
    assert final_answer_events[-1]["content"] == final_state["final_answer"]
    assert final_answer_events[-1]["content"].strip() != ""

    assert metadata_events
    assert metadata_events[-1]["references"]
    assert len(metadata_events[-1]["suggested_next_questions"]) == 3
    assert metadata_events[-1]["grounding_summary"]["verified_claim_count"] == 1


def test_out_of_scope_stream_short_circuits_and_emits_scope_guard(monkeypatch):
    final_state = {
        "intent": "out_of_scope",
        "resolved_query": "What is the weather in Hanoi?",
        "final_answer": "I’m designed to help investors with market trends, regulation, news, and competitor context. I can’t help with this query.",
        "references": [],
        "caveats": [
            "This query is outside investor-research scope (market trends, regulation, news, competitor context)."
        ],
        "processing_warnings": ["out_of_scope_query"],
        "grounding_summary": {
            "verified_claim_count": 0,
            "weakly_supported_claim_count": 0,
            "conflicting_claim_count": 0,
            "unsupported_claim_count": 0,
            "reference_count": 0,
            "coverage_status": "insufficient",
        },
    }

    monkeypatch.setattr(investor_router_module,
                        "_chat_graph", OutOfScopeStubGraph(final_state))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/investor-agent/chat/stream",
        json={"query": "How is weather today?", "thread_id": "thread-scope"},
        headers=AUTH_HEADERS,
    ) as stream_response:
        assert stream_response.status_code == 200
        stream_text = "".join(list(stream_response.iter_text()))

    events = _collect_sse_events(stream_text)
    progress_nodes = [e.get("node") for e in events if isinstance(
        e, dict) and e.get("type") == "progress"]
    metadata_events = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_metadata"]
    final_answers = [e for e in events if isinstance(
        e, dict) and e.get("type") == "final_answer"]

    assert "scope_guard" in progress_nodes
    assert "search" not in progress_nodes
    assert final_answers[-1]["content"].strip() != ""
    assert metadata_events[-1]["references"] == []
    assert metadata_events[-1]["grounding_summary"]["coverage_status"] == "insufficient"


def test_stream_rejects_missing_thread_id_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(
        investor_router_module.settings,
        "INVESTOR_AGENT_REQUIRE_THREAD_ID",
        True,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/investor-agent/chat/stream",
        json={"query": "Any update?"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422
