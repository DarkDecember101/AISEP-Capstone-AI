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
    prior_timeframe = state.last_timeframe or _extract_timeframe(
        prior_topic or "")

    # Build a clean, search-ready standalone query.
    # Avoid "{prior_topic}. Follow-up focus: {user_query}" pattern which is
    # noisy and confuses downstream search/planner nodes.
    _new_geos = _geo_in_text(user_query) - _geo_in_text(
        (prior_topic or "") + " " + " ".join(state.last_entities or []))

    if is_followup and prior_topic and inferred_type in {"entity_drilldown", "comparison"}:
        base = prior_topic.strip().rstrip(".")
        timeframe_suffix = f" {prior_timeframe}" if prior_timeframe else ""
        if _new_geos:
            # Make the country the explicit focus so search queries are specific.
            geo_fragment = user_query.strip().rstrip("?").strip()
            resolved_query = f"{base} {geo_fragment}{timeframe_suffix}".strip()
        else:
            resolved_query = f"{base} — {user_query.strip()}{timeframe_suffix}"
    elif is_followup and prior_topic and inferred_type == "clarification":
        base = prior_topic.strip().rstrip(".")
        resolved_query = f"{base}: {user_query.strip()}"
    else:
        resolved_query = user_query

    # New-geography drilldown should NOT reuse old broader-region evidence.
    _geo_drilldown_new = inferred_type == "entity_drilldown" and bool(
        _new_geos)
    reuse_previous_verified_claims = inferred_type in {
        "entity_drilldown", "source_request", "summary_request", "clarification", "comparison"
    } and not _geo_drilldown_new
    requires_fresh_search = inferred_type in {
        "entity_drilldown", "comparison", "recency_update"} or _geo_drilldown_new

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
        # If the LLM (or override below) flagged a fresh search, honour it.
        if resolution.requires_fresh_search:
            return "fresh_search"
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


# Map of surface forms → canonical geography name.
_GEO_TERMS: Dict[str, str] = {
    "việt nam": "vietnam", "vietnam": "vietnam",
    "indonesia": "indonesia",
    "singapore": "singapore",
    "thái lan": "thailand", "thailand": "thailand",
    "malaysia": "malaysia",
    "philippines": "philippines",
    "myanmar": "myanmar",
    "campuchia": "cambodia", "cambodia": "cambodia",
    "lào": "laos", "laos": "laos",
    "trung quốc": "china", "china": "china",
    "nhật bản": "japan", "japan": "japan",
    "hàn quốc": "korea", "korea": "korea",
}


def _geo_in_text(text: str) -> set:
    """Return the set of canonical geography names found in *text*."""
    lowered = (text or "").lower()
    return {norm for term, norm in _GEO_TERMS.items() if term in lowered}


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

Your job:
Determine whether the latest query is a follow-up that depends on prior context.
If it is, resolve it into a clean standalone query for downstream research.

Return strict JSON with these fields:
- is_followup: boolean
- followup_type: entity_drilldown | source_request | recency_update | comparison | summary_request | clarification | none
- resolved_query: standalone enriched query
- resolved_topic: short topic phrase
- resolved_entities: list of entities / markets / countries / sectors
- resolved_timeframe: timeframe if any
- reuse_previous_verified_claims: boolean
- requires_fresh_search: boolean
- reasoning: one short sentence

Definitions:
- entity_drilldown: user narrows the same topic to a geography, segment, or sub-entity
- source_request: user asks for sources, citations, evidence, or references
- recency_update: user asks for the latest version, newer information, or a changed timeframe
- comparison: user asks to compare the prior topic/entity with another entity or market
- summary_request: user asks to summarize or condense prior results
- clarification: user asks to explain a point from prior context without necessarily needing fresh search
- none: a standalone new query

Rules:
1. If the latest query is short, referential, elliptical, or context-dependent, mark is_followup = true.
2. Preserve only necessary prior context. Do not drag irrelevant entities, markets, or timeframes into the resolved query.
3. For source_request, summary_request, and most clarification cases:
   - prefer reuse_previous_verified_claims = true
   - prefer requires_fresh_search = false
4. For recency_update:
   - requires_fresh_search = true
5. For comparison:
   - requires_fresh_search = true if a new entity/market/geography is introduced
6. For entity_drilldown:
   - keep the prior topic
   - keep the prior timeframe unless the user changes it
   - update the entities to reflect the new focus
   - if the new focus introduces a country or market NOT present in previous_entities
     (e.g. user asks about Vietnam after a Southeast Asia question), set
     requires_fresh_search = true AND reuse_previous_verified_claims = false
   - resolved_query must explicitly name the new country/market so search is specific
7. If previous_verified_claim_count or previous_reference_count is zero, be more cautious about reuse.
8. resolved_query must be directly usable by downstream search/research.
9. reasoning must be one short sentence only.

Examples:
- "Việt Nam thì sao?" -> entity_drilldown
- "Nguồn đâu?" -> source_request
- "Cập nhật mới nhất thì sao?" -> recency_update
- "So với Indonesia thì sao?" -> comparison
- "Tóm tắt ngắn hơn" -> summary_request
- "Ý đó nghĩa là gì?" -> clarification

Return JSON only.
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

    # Hard override: if the user drills into a new geography that was NOT in the
    # prior context (e.g. "Việt Nam thì sao?" after a SEA-wide question), force
    # fresh_search regardless of what the LLM decided.  Old broader-region
    # verified claims are not useful evidence for a country-specific follow-up.
    _prior_geo_context = " ".join(
        state.last_entities or []) + " " + (state.conversation_topic or "")
    _new_geos_run = _geo_in_text(
        resolution.resolved_query) - _geo_in_text(_prior_geo_context)
    if resolution.followup_type == "entity_drilldown" and _new_geos_run:
        search_decision = "fresh_search"
        resolution = ResolverOutput(
            **{
                **resolution.model_dump(),
                "reuse_previous_verified_claims": False,
                "requires_fresh_search": True,
            }
        )
        logger.info(
            "Follow-up resolver override: new geography detected=%s → fresh_search, reuse=False",
            _new_geos_run,
        )

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
