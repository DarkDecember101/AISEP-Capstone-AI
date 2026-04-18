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

Your job is to classify the user query into EXACTLY ONE intent from this fixed set:
- market_trend: market outlook, sector trends, growth, demand/supply, pricing, macro themes, segment attractiveness
- regulation: legal, regulatory, policy, compliance, enforcement, approval environment, rule changes
- news: recent announcements, funding events, acquisitions, launches, partnerships, major company updates
- competitor_context: comparing companies, products, positioning, market structure, differentiation, pricing, competitive dynamics
- mixed: the query genuinely combines two or more major intents with similar importance
- out_of_scope: only if the query is clearly unrelated to investor research or market intelligence

Classification rules:
1. Choose the SINGLE best dominant intent whenever possible.
2. Use mixed only when the query truly combines multiple major intents with similar importance.
3. Do NOT overuse out_of_scope.
4. If the query is investor-related but somewhat ambiguous, prefer market_trend or mixed rather than out_of_scope.
5. Broad questions about an industry, market, or sector usually belong to market_trend.
6. Questions about companies, rivalry, positioning, or “who is stronger / different” usually belong to competitor_context.
7. Questions about recent events or announcements usually belong to news.
8. Questions about legal changes, policy risk, compliance burden, or rule impact usually belong to regulation.

Language support:
- Vietnamese and English.

Follow-up sensitivity:
Set is_followup_sensitive = true if the query is short, referential, or likely depends on prior context.

Return strict JSON only with these fields:
- intent
- confidence: high | medium | low
- reasoning: one short sentence
- is_followup_sensitive: boolean

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
