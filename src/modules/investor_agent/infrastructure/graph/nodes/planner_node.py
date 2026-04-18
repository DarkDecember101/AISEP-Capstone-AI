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

    _resolved_entities = getattr(state, "resolved_entities", []) or []
    _COUNTRY_NAMES = {
        "vietnam", "vi\u1ec7t nam", "indonesia", "singapore", "thailand", "th\u00e1i lan",
        "malaysia", "philippines", "myanmar", "cambodia", "laos", "china", "japan", "korea",
    }
    _geo_entities = [e for e in _resolved_entities if e.lower()
                     in _COUNTRY_NAMES]

    # Backup: scan the resolved_query text itself for country names in case
    # the resolver returned a regional label (e.g. "\u0110\u00f4ng Nam \u00c1") instead of a country.
    if not _geo_entities:
        _query_lower = query_to_use.lower()
        _geo_entities = [c for c in _COUNTRY_NAMES if c in _query_lower]

    _geo_focus = ", ".join(sorted(set(_geo_entities))) if _geo_entities else ""

    prompt = f"""
    You are an expert AI Planner for a high-stakes investor market-intelligence task.

Query: {query_to_use}
Intent: {state.intent}
Follow-up Type: {getattr(state, 'followup_type', None)}
Search Decision: {getattr(state, 'search_decision', 'full_search')}
Resolved Entities: {_resolved_entities}
Geography Focus: {_geo_focus if _geo_focus else 'none'}

Your task:
Plan a focused research strategy that improves factual accuracy and useful coverage.

Core objective:
First identify the most relevant research angles implied by the query.
Then generate search sub-queries that cover those angles efficiently and are likely to surface strong evidence.

Planning rules:
1. If the query is narrow and factual, return 1-2 highly targeted sub_queries.
2. If the query is broad or mixed, return up to 4 complementary sub_queries covering different important angles.
3. Keep sub_queries short, keyword-dense, and search-engine friendly.
4. Add timeframe, geography, segment, or policy scope when explicit or strongly implied.
5. Avoid redundant queries that retrieve the same angle repeatedly.
6. Prefer queries likely to surface stronger sources such as regulators, official statistics, exchanges, research reports, and credible business/financial media.

Angle selection guidance:
For broad market or sector questions, cover relevant angles such as:
- trend direction
- demand signals
- supply / inventory / capacity
- pricing / economic signals
- growth drivers
- risks / headwinds
- geographic or segment differences
- regulatory context
- catalysts / what to watch next

Intent guidance:
- market_trend:
  prioritize trend direction, demand/supply, pricing, growth drivers, risks, and segment/geographic differences
- regulation:
  prioritize what changed, effective timing, affected entities, compliance implications, and uncertainty
- news:
  prioritize what happened, who is affected, immediate implications, and follow-on signals to monitor
- competitor_context:
  prioritize positioning, market structure, pricing, differentiation, defensibility, and GTM implications
- mixed:
  choose the most decision-relevant angles and diversify the plan across them

Output requirements:
Return structured output matching the schema exactly:
- sub_queries: list of search queries
- rationale: one short sentence explaining the coverage strategy
- required_facets: list of the most important evidence facets the downstream pipeline should try to cover
- min_sources: minimum number of distinct useful sources needed for sufficient coverage

Geography rule:
- If Geography Focus is set, EVERY sub_query must explicitly include that country or market name.
- Do NOT produce generic regional queries (e.g. "Southeast Asia fintech") when the focus is a specific country.
- Prefer queries that will surface country-specific regulators, statistics, reports, and news.

Constraints:
- No extra fields
- No duplicate or near-duplicate sub_queries
- required_facets should be concise, query-specific, and decision-relevant
- min_sources should usually be 1-2 for narrow factual queries and 3-5 for broader market queries
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
