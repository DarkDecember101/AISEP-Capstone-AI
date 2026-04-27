import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Mapping

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator

from src.shared.auth import require_internal_auth
from src.shared.correlation import get_correlation_id
from src.shared.checkpoint import get_checkpointer
from src.shared.config.settings import settings
from src.shared.rate_limit.limiter import RateLimitDep
from src.modules.investor_agent.application.dto.state import GroundingSummary
from src.modules.investor_agent.application.services.final_assembler import (
    assemble_final_response,
)
from src.modules.investor_agent.infrastructure.graph.builder import build_investor_agent_graph

router = APIRouter()
logger = logging.getLogger("aisep.investor_agent")

# Hard timeout for the full graph execution (seconds).
# Set lower than .NET's HttpClient timeout (typically 100–300 s) so we can
# emit a clean error SSE event before the connection is forcibly closed.
_GRAPH_STREAM_TIMEOUT_SECONDS = 240

# Rate limiter dependency
_stream_rate_limit = RateLimitDep("stream", settings.RATE_LIMIT_STREAM_RPM)

_THREAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")

GRAPH_NODE_NAMES = [
    "followup_resolver",
    "router",
    "planner",
    "search",
    "source_selection",
    "extract",
    "fact_builder",
    "claim_verifier",
    "writer",
]

# ── Graph instance ───────────────────────────────────────────────────
# Stateful graph for /chat/stream — backed by shared checkpoint
_chat_graph = build_investor_agent_graph(checkpointer=get_checkpointer())

logger.info(
    "Investor-agent graphs initialised. checkpoint_backend=%s",
    type(get_checkpointer()).__name__,
)


class ChatRequest(BaseModel):
    query: str
    thread_id: str = Field(default="default_thread", max_length=128)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be blank.")
        return v.strip()

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, v: str) -> str:
        if not _THREAD_ID_PATTERN.match(v):
            raise ValueError(
                "thread_id must be 1-128 alphanumeric/dash/underscore characters."
            )
        return v


# ── Stable metadata shape ───────────────────────────────────────────
_EMPTY_GROUNDING = GroundingSummary(
    verified_claim_count=0,
    weakly_supported_claim_count=0,
    conflicting_claim_count=0,
    unsupported_claim_count=0,
    reference_count=0,
    coverage_status="insufficient",
)


def _safe_grounding(raw: Any) -> dict:
    """Ensure grounding_summary is always a stable dict shape."""
    if isinstance(raw, GroundingSummary):
        return raw.model_dump()
    if isinstance(raw, dict):
        try:
            return GroundingSummary(**raw).model_dump()
        except Exception:
            pass
    return _EMPTY_GROUNDING.model_dump()


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def _extract_final_state_from_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    event_data = event.get("data") or {}
    if isinstance(event_data, dict):
        for key in ("output", "chunk", "result"):
            candidate = event_data.get(key)
            as_dict = _as_dict(candidate)
            if as_dict:
                return as_dict
    return {}


async def _recover_state_from_checkpointer(config: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        snapshot = await _chat_graph.aget_state(config)
        if snapshot is None:
            return {}
        values = getattr(snapshot, "values", None)
        return _as_dict(values)
    except Exception as error:
        logger.warning("Failed to recover state from checkpointer: %s", error)
        return {}


def _chunk_text(text: str, chunk_size: int = 180) -> List[str]:
    content = (text or "").strip()
    if not content:
        return []
    return [content[index:index + chunk_size] for index in range(0, len(content), chunk_size)]


def _build_chat_payload(final_state: Mapping[str, Any]) -> Dict[str, Any]:
    logger.info(
        "Pre-assembler state: verified=%s conflicting=%s caveats=%s warnings=%s",
        len(final_state.get("verified_claims") or []),
        len(final_state.get("conflicting_claims") or []),
        len(final_state.get("caveats") or []),
        len(final_state.get("processing_warnings") or []),
    )

    assembled = assemble_final_response(final_state)
    logger.info(
        "Assembled response: keys=%s final_answer_length=%s fallback_triggered=%s",
        sorted(list(assembled.keys())),
        len(assembled.get("final_answer", "")),
        assembled.get("fallback_triggered", False),
    )

    return {
        "intent": assembled.get("intent", "unknown"),
        "final_answer": assembled.get("final_answer", ""),
        "references": assembled.get("references", []),
        "caveats": assembled.get("caveats", []),
        "suggested_next_questions": assembled.get("suggested_next_questions", []),
        "writer_notes": assembled.get("writer_notes", []),
        "processing_warnings": assembled.get("processing_warnings", []),
        "grounding_summary": _safe_grounding(assembled.get("grounding_summary")),
        "resolved_query": assembled.get("resolved_query", ""),
        "fallback_triggered": assembled.get("fallback_triggered", False),
    }


def _sse(payload: Any) -> str:
    """Serialize payload to an SSE data line with Unicode preserved (no escape)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_research_stream(
    request: ChatRequest,
    _rl=Depends(_stream_rate_limit),
    _auth: None = Depends(require_internal_auth),
):
    """
    Streaming version of Stateful Multi-turn Investor Agent Pipeline.
    Streams LangGraph events.
    Rate limit is checked BEFORE the stream generator starts.
    """
    config = {"configurable": {
        "thread_id": request.thread_id}, "recursion_limit": 50}
    initial_state = {
        "messages": [HumanMessage(content=request.query)],
        "thread_id": request.thread_id,
    }

    async def event_generator():
        latest_writer_output: Dict[str, Any] = {}
        latest_graph_output: Dict[str, Any] = {}
        try:
            async with asyncio.timeout(_GRAPH_STREAM_TIMEOUT_SECONDS):
                async for event in _chat_graph.astream_events(initial_state, config=config, version="v1"):
                    event_type = event.get("event")
                    event_name = event.get("name")

                    if event_type == "on_chain_start" and event_name in GRAPH_NODE_NAMES:
                        yield _sse({"type": "progress", "node": event["name"]})

                    if event_type == "on_chain_end" and event_name == "writer":
                        latest_writer_output = _extract_final_state_from_event(
                            event)
                        logger.info(
                            "Writer output observed: keys=%s preview=%s",
                            sorted(list(latest_writer_output.keys())),
                            str(latest_writer_output.get(
                                "final_answer", ""))[:120],
                        )

                    if event_type == "on_chain_end" and event_name == "router":
                        router_output = _extract_final_state_from_event(event)
                        if router_output.get("intent") == "out_of_scope":
                            yield _sse({"type": "progress", "node": "scope_guard"})

                    if event_type == "on_chain_end" and event_name == "LangGraph":
                        latest_graph_output = _extract_final_state_from_event(
                            event)

            final_state = latest_graph_output or latest_writer_output
            if not (final_state.get("final_answer") or "").strip():
                recovered_state = await _recover_state_from_checkpointer(config)
                if recovered_state:
                    final_state = recovered_state

            if not (final_state.get("final_answer") or "").strip():
                logger.warning(
                    "Empty final_answer before assembler fallback; source branch=graph_end")

            payload = _build_chat_payload(final_state)
            answer = payload.get("final_answer", "")
            for answer_chunk in _chunk_text(answer):
                yield _sse({"type": "answer_chunk", "content": answer_chunk})

            yield _sse({"type": "final_answer", "content": answer})

            yield _sse({
                "type": "final_metadata",
                "references": payload.get("references", []),
                "caveats": payload.get("caveats", []),
                "suggested_next_questions": payload.get("suggested_next_questions", []),
                "writer_notes": payload.get("writer_notes", []),
                "processing_warnings": payload.get("processing_warnings", []),
                "grounding_summary": _safe_grounding(payload.get("grounding_summary")),
            })
        except asyncio.TimeoutError:
            logger.error(
                "investor_agent.stream timed out after %ss correlation_id=%s",
                _GRAPH_STREAM_TIMEOUT_SECONDS, get_correlation_id(),
            )
            yield _sse({"type": "error", "content": "Request timed out. The research pipeline took too long to complete.", "correlation_id": get_correlation_id()})
        except Exception as e:
            logger.error("investor_agent.stream error=%s correlation_id=%s",
                         e, get_correlation_id())
            yield _sse({"type": "error", "content": "An internal error occurred during streaming.", "correlation_id": get_correlation_id()})

        # Always emit terminal marker
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream; charset=utf-8")
