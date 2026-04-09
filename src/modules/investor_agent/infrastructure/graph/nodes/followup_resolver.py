import logging
import re
from typing import Dict, Any, List, Literal
from pydantic import BaseModel, Field
from src.modules.investor_agent.application.dto.state import GraphState
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class ResolverOutput(BaseModel):
    is_followup: bool
    followup_type: Literal[
        "entity_drilldown", "source_request", "recency_update", "comparison", "summary_request", "clarification", "none"
    ]
    resolved_query: str
    resolved_topic: str = ""
    resolved_entities: List[str] = Field(default_factory=list)
    resolved_timeframe: str = ""
    reuse_previous_verified_claims: bool = False
    requires_fresh_search: bool = True
    reasoning: str = ""


def _extract_user_query(state: GraphState) -> str:
    if state.messages and state.messages[-1].type == "human":
        return str(state.messages[-1].content or "").strip()
    return (state.user_query or "").strip()


def _recent_history(state: GraphState) -> str:
    return "\n".join([f"{m.type}: {m.content}" for m in state.messages[:-1][-6:]])


def _is_short_contextual_followup(user_query: str) -> bool:
    q = (user_query or "").strip().lower()
    if len(q.split()) <= 5:
        markers = [
            "thì sao", "còn", "nguồn", "tóm tắt", "ngắn hơn", "vậy", "cập nhật mới", "mới hơn",
            "what about", "and vietnam", "and indonesia", "source", "shorter", "summarize",
        ]
        return any(marker in q for marker in markers)
    return False


def _infer_followup_type(user_query: str) -> Literal[
    "entity_drilldown", "source_request", "recency_update", "comparison", "summary_request", "clarification", "none"
]:
    q = (user_query or "").strip().lower()
    if not q:
        return "none"
    if any(token in q for token in ["nguồn", "source", "reference", "căn cứ"]):
        return "source_request"
    if any(token in q for token in ["cập nhật", "mới hơn", "latest", "newer", "recent"]):
        return "recency_update"
    if any(token in q for token in ["tóm tắt", "ngắn hơn", "summary", "shorter"]):
        return "summary_request"
    if any(token in q for token in ["so sánh", "vs", "đối thủ", "compare", "competitor"]):
        return "comparison"
    if any(token in q for token in ["thì sao", "what about", "còn", "vietnam", "indonesia", "thái lan", "singapore"]):
        return "entity_drilldown"
    if len(q.split()) <= 6:
        return "clarification"
    return "none"


def _fallback_resolution(user_query: str, state: GraphState) -> ResolverOutput:
    inferred_type = _infer_followup_type(user_query)
    is_followup = len(state.messages) > 1 and (
        _is_short_contextual_followup(user_query) or inferred_type != "none")
    prior_topic = state.conversation_topic or state.resolved_query or state.user_query
    resolved_query = user_query
    if is_followup and prior_topic and inferred_type in {"entity_drilldown", "comparison", "clarification"}:
        resolved_query = f"{prior_topic}. Follow-up focus: {user_query}"

    reuse_previous_verified_claims = inferred_type in {
        "entity_drilldown", "source_request", "summary_request", "clarification", "comparison"
    }
    requires_fresh_search = inferred_type in {
        "entity_drilldown", "comparison", "recency_update"}

    return ResolverOutput(
        is_followup=is_followup,
        followup_type=inferred_type,
        resolved_query=resolved_query,
        resolved_topic=state.conversation_topic or state.resolved_query or state.user_query,
        resolved_entities=list(state.last_entities or []),
        resolved_timeframe=state.last_timeframe or "",
        reuse_previous_verified_claims=reuse_previous_verified_claims,
        requires_fresh_search=requires_fresh_search,
        reasoning="fallback_followup_resolution",
    )


def _derive_search_decision(resolution: ResolverOutput, previous_verified_claims_count: int) -> Literal[
    "full_search", "reuse_only", "reuse_plus_search", "fresh_search"
]:
    if not resolution.is_followup:
        return "full_search"

    if resolution.followup_type in {"source_request", "summary_request", "clarification"}:
        if resolution.reuse_previous_verified_claims and previous_verified_claims_count > 0 and not resolution.requires_fresh_search:
            return "reuse_only"
        return "reuse_plus_search"

    if resolution.followup_type in {"entity_drilldown", "comparison"}:
        return "reuse_plus_search"

    if resolution.followup_type == "recency_update":
        return "fresh_search"

    return "full_search"


def _extract_entities_from_text(text: str) -> List[str]:
    if not text:
        return []
    known_entities = [
        "việt nam", "vietnam", "indonesia", "singapore", "thái lan", "thailand", "malaysia", "philippines", "xendit", "sea"
    ]
    lowered = text.lower()
    return [entity for entity in known_entities if entity in lowered]


def _extract_timeframe(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1)
    for token in ["latest", "mới nhất", "recent", "2024", "2025"]:
        if token in text.lower():
            return token
    return ""


async def run(state: GraphState) -> Dict[str, Any]:
    llm = GeminiClient()
    user_query = _extract_user_query(state)
    previous_verified_claims = list(state.verified_claims or [])
    previous_conflicting_claims = list(state.conflicting_claims or [])
    previous_references = list(state.references or [])
    previous_selected_sources = list(state.selected_sources or [])
    previous_final_answer = state.final_answer or ""
    history_str = _recent_history(state)

    if len(state.messages) <= 1:
        resolution = ResolverOutput(
            is_followup=False,
            followup_type="none",
            resolved_query=user_query,
            resolved_topic=user_query,
            resolved_entities=_extract_entities_from_text(user_query),
            resolved_timeframe=_extract_timeframe(user_query),
            reuse_previous_verified_claims=False,
            requires_fresh_search=True,
            reasoning="single_turn_initial_query",
        )
    else:
        prompt = f"""
        You are a follow-up resolver for an investor research chatbot.
        Support Vietnamese and English.

        Conversation history:
        {history_str}

        Latest user query: "{user_query}"

        Previous memory:
        - previous_topic: {state.conversation_topic or state.resolved_query or state.user_query}
        - previous_entities: {state.last_entities}
        - previous_timeframe: {state.last_timeframe}
        - previous_verified_claim_count: {len(previous_verified_claims)}
        - previous_reference_count: {len(previous_references)}

        Return strict JSON fields:
        - is_followup: bool
        - followup_type: entity_drilldown | source_request | recency_update | comparison | summary_request | clarification | none
        - resolved_query: standalone enriched query
        - resolved_topic: short topic phrase
        - resolved_entities: list of entities/markets/countries
        - resolved_timeframe: timeframe if any
        - reuse_previous_verified_claims: bool
        - requires_fresh_search: bool
        - reasoning: short reason

        Rules:
        - If query is short/context-dependent, mark follow-up.
        - For source_request/summary_request/clarification: prefer reuse_previous_verified_claims=true.
        - For recency_update: requires_fresh_search=true.
        - For entity drilldown like "Việt Nam thì sao": keep prior topic/timeframe and resolve into a rich standalone query.
        """

        try:
            resolution = await llm.generate_structured_async(
                prompt=prompt, response_schema=ResolverOutput, model_name="gemini-2.5-flash")
        except Exception as error:
            logger.warning("Follow-up resolver LLM failed: %s", error)
            resolution = _fallback_resolution(user_query, state)

    resolved_entities = list(dict.fromkeys(resolution.resolved_entities or []))
    if not resolved_entities:
        resolved_entities = _extract_entities_from_text(
            resolution.resolved_query)
    resolved_timeframe = resolution.resolved_timeframe or _extract_timeframe(
        resolution.resolved_query)

    previous_verified_claim_count = len(previous_verified_claims)
    previous_reference_count = len(previous_references)
    search_decision = _derive_search_decision(
        resolution, previous_verified_claims_count=previous_verified_claim_count)

    if search_decision == "reuse_only" and previous_verified_claim_count == 0:
        search_decision = "reuse_plus_search"

    reused_claim_count = previous_verified_claim_count if (
        resolution.reuse_previous_verified_claims and search_decision in {
            "reuse_only", "reuse_plus_search"}
    ) else 0
    reused_reference_count = previous_reference_count if (
        resolution.reuse_previous_verified_claims and search_decision in {
            "reuse_only", "reuse_plus_search"}
    ) else 0

    logger.info(
        "Follow-up resolver | raw_query=%s | is_followup=%s | followup_type=%s | resolved_query=%s | resolved_topic=%s | resolved_entities=%s | resolved_timeframe=%s | reuse_previous_verified_claims=%s | requires_fresh_search=%s | search_decision=%s",
        user_query,
        resolution.is_followup,
        resolution.followup_type,
        resolution.resolved_query,
        resolution.resolved_topic,
        resolved_entities,
        resolved_timeframe,
        resolution.reuse_previous_verified_claims,
        resolution.requires_fresh_search,
        search_decision,
    )
    logger.info(
        "Memory reuse | previous_verified_claim_count=%s | reused_claim_count=%s | previous_reference_count=%s | reused_reference_count=%s",
        previous_verified_claim_count,
        reused_claim_count,
        previous_reference_count,
        reused_reference_count,
    )

    # Resetting research state for the new turn
    return {
        "resolved_query": (resolution.resolved_query or user_query).strip(),
        "user_query": user_query,
        "is_followup": bool(resolution.is_followup),
        "followup_type": resolution.followup_type,
        "followup_reasoning": resolution.reasoning,
        "resolved_topic": (resolution.resolved_topic or state.conversation_topic or state.resolved_query or user_query).strip(),
        "resolved_entities": resolved_entities,
        "resolved_timeframe": resolved_timeframe,
        "reuse_previous_verified_claims": bool(resolution.reuse_previous_verified_claims),
        "requires_fresh_search": bool(resolution.requires_fresh_search),
        "search_decision": search_decision,
        "conversation_topic": (resolution.resolved_topic or state.conversation_topic or state.resolved_query or user_query).strip(),
        "last_entities": resolved_entities,
        "last_timeframe": resolved_timeframe,
        "previous_final_answer": previous_final_answer,
        "previous_verified_claims": previous_verified_claims,
        "previous_conflicting_claims": previous_conflicting_claims,
        "previous_references": previous_references,
        "previous_selected_sources": previous_selected_sources,
        "thread_summary": (state.thread_summary or previous_final_answer[:500]),
        "reused_claim_count": reused_claim_count,
        "reused_reference_count": reused_reference_count,
        "loop_count": 0,
        "search_results": [],
        "sub_queries": [],
        "verified_claims": [],
        "facts": [],
        "selected_sources": [],
        "extracted_documents": [],
        "claims_candidate": [],
        "unsupported_claims": [],
        "conflicting_claims": [],
        "processing_warnings": [],
        "router_confidence": None,
        "router_reasoning": "",
        "router_is_followup_sensitive": None,
        "router_fallback_used": False,
        "scope_guard_reason": "",
        "heuristic_intent": None,
        "caveats": [],
        "references": [],
        "writer_notes": [],
        "final_answer": "",
        "grounding_summary": None,
        "coverage_assessment": None,
    }
