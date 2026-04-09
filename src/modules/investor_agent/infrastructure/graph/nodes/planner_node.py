from typing import Dict, Any, List
import logging
from pydantic import BaseModel
from src.modules.investor_agent.application.dto.state import GraphState, RequiredCoverage
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class PlannerOutput(BaseModel):
    sub_queries: List[str]
    rationale: str
    required_facets: List[str]
    min_sources: int


async def run(state: GraphState) -> Dict[str, Any]:
    llm = GeminiClient()
    warnings = list(getattr(state, "processing_warnings", []))
    query_to_use = (state.resolved_query or state.user_query or "").strip()

    prompt = f"""
    You are an expert AI Planner for a high-stake investor research task.
    Query: {query_to_use}
    Intent: {state.intent}
    Follow-up Type: {getattr(state, 'followup_type', None)}
    Search Decision: {getattr(state, 'search_decision', 'full_search')}
    
    Generate targeted Google search sub-queries to find exact numeric and factual answers.
    1. If intent is simple, return 1-2 queries.
    2. If mixed, return up to 4.
    3. Keep queries short, keyword-dense, add timeframe or geo-restrictions if implicit.
    """

    try:
        plan = await llm.generate_structured_async(
            prompt=prompt, response_schema=PlannerOutput)
    except Exception as error:
        logger.warning("Planner LLM failed: %s", error)
        warnings.append("planner_llm_exception")
        fallback_query = (
            state.resolved_query or state.user_query or "").strip()
        plan = PlannerOutput(
            sub_queries=[fallback_query] if fallback_query else [],
            rationale="Fallback planner path",
            required_facets=[],
            min_sources=1,
        )

    sub_queries = [query.strip()
                   for query in plan.sub_queries if query and query.strip()]
    if not sub_queries:
        fallback_query = (
            state.resolved_query or state.user_query or "").strip()
        if fallback_query:
            sub_queries = [fallback_query]
            warnings.append("planner_used_fallback_query")

    req_cov = RequiredCoverage(
        min_sources=max(1, plan.min_sources),
        required_facets=plan.required_facets
    )

    logger.info(
        "Planner: query=%s search_decision=%s sub_queries=%s min_sources=%s facets=%s",
        query_to_use,
        getattr(state, "search_decision", "full_search"),
        sub_queries,
        req_cov.min_sources,
        req_cov.required_facets,
    )

    return {
        "sub_queries": sub_queries,
        "required_coverage": req_cov.model_dump(),
        "processing_warnings": warnings,
    }
