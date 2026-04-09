import logging
from typing import Dict, Any, Literal
from pydantic import BaseModel
from src.modules.investor_agent.application.dto.state import GraphState
from src.modules.investor_agent.application.services.scope_guard import decide_scope
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class RouterOutput(BaseModel):
    intent: Literal["market_trend", "regulation",
                    "news", "competitor_context", "mixed", "out_of_scope"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    is_followup_sensitive: bool


def _fallback_router_output() -> RouterOutput:
    return RouterOutput(
        intent="mixed",
        confidence="low",
        reasoning="fallback_due_to_router_failure",
        is_followup_sensitive=True,
    )


async def run(state: GraphState) -> Dict[str, Any]:
    """Router Node: classifies intention of query."""
    llm = GeminiClient()

    raw_query = (state.user_query or "").strip()
    query_to_use = state.resolved_query if getattr(
        state, "resolved_query", None) else state.user_query
    query_to_use = (query_to_use or "").strip()

    prompt = f"""
    You are an expert router for an investor research assistant.
    You MUST classify the query into ONE intent from this fixed set:
    - market_trend: overall market direction, size, growth, macro themes.
    - regulation: local or global legal, policy, compliance factors.
    - news: latest announcements, funding events, acquisitions.
    - competitor_context: comparing products, positioning, players.
    - mixed: multiple aspects.
    - out_of_scope: only if the query is clearly unrelated to investor research.

    Language support: Vietnamese and English.
    Avoid overusing out_of_scope. If unsure but investor-related, choose mixed.

    Return strict JSON with fields:
    - intent
    - confidence: high | medium | low
    - reasoning: short string
    - is_followup_sensitive: boolean (true if short follow-up likely depends on context)

    Query: {query_to_use}
    """

    fallback_used = False
    try:
        result = await llm.generate_structured_async(
            prompt=prompt, response_schema=RouterOutput)
    except Exception as error:
        logger.warning("Router LLM classification failed: %s", error)
        result = _fallback_router_output()
        fallback_used = True

    decision = decide_scope(
        query=query_to_use,
        router_intent=result.intent,
        router_confidence=result.confidence,
        router_reasoning=result.reasoning,
    )

    logger.info(
        "ScopeGuard decision | raw_query=%s | resolved_query=%s | router_intent=%s | router_confidence=%s | router_reasoning=%s | heuristic_used=%s | heuristic_intent=%s | final_intent=%s | is_out_of_scope=%s | refusal_reason=%s",
        raw_query,
        query_to_use,
        result.intent,
        result.confidence,
        result.reasoning,
        decision.heuristic_used,
        decision.heuristic_intent,
        decision.final_intent,
        decision.is_out_of_scope,
        decision.refusal_reason,
    )

    warnings = list(state.processing_warnings or [])
    if fallback_used:
        warnings.append("router_llm_failed_fallback_used")
    if decision.heuristic_used:
        warnings.append("scope_guard_heuristic_fallback_used")
    if decision.is_out_of_scope:
        warnings.append("out_of_scope_query")

    return {
        "intent": decision.final_intent,
        "router_confidence": result.confidence,
        "router_reasoning": result.reasoning,
        "router_is_followup_sensitive": bool(result.is_followup_sensitive),
        "router_fallback_used": fallback_used,
        "scope_guard_reason": decision.reason,
        "heuristic_intent": decision.heuristic_intent,
        "processing_warnings": list(dict.fromkeys(warnings)),
    }
