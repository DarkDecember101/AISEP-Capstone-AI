import asyncio
from typing import Dict, Any, List
import logging
from src.modules.investor_agent.application.dto.state import GraphState, SearchResult
from src.shared.config.settings import settings
from tavily import AsyncTavilyClient

logger = logging.getLogger(__name__)


async def run(state: GraphState) -> Dict[str, Any]:
    queries = state.refined_sub_queries if state.refined_sub_queries else state.sub_queries
    if not queries:
        fallback_query = (
            state.resolved_query or state.user_query or "").strip()
        if fallback_query:
            queries = [fallback_query]

    results: List[Dict[str, Any]] = []
    warnings = list(getattr(state, "processing_warnings", []))

    # Geography fallback: if a specific country is in resolved_entities but none
    # of the planner's sub_queries explicitly mentions it, prepend one targeted
    # country-specific query so search always retrieves country-level evidence.
    _GEO_SURFACE_FORMS = {
        "vietnam": ["vietnam", "việt nam"],
        "indonesia": ["indonesia"],
        "singapore": ["singapore"],
        "thailand": ["thailand", "thái lan"],
        "malaysia": ["malaysia"],
        "philippines": ["philippines"],
        "myanmar": ["myanmar"],
        "cambodia": ["cambodia", "campuchia"],
        "laos": ["laos", "lào"],
    }
    _resolved_entities_lower = [e.lower() for e in (
        getattr(state, "resolved_entities", []) or [])]
    _queries_lower = " ".join(queries).lower() if queries else ""
    for _canon, _forms in _GEO_SURFACE_FORMS.items():
        if any(f in _resolved_entities_lower for f in _forms):
            if not any(f in _queries_lower for f in _forms):
                _base_topic = (
                    state.resolved_query or state.user_query or "").strip()
                _geo_query = f"{_base_topic} {_canon}".strip()
                if _geo_query not in queries:
                    queries = [_geo_query] + list(queries)
                    warnings.append(
                        f"search_added_geography_fallback_{_canon}")
                    logger.info(
                        "Search node: prepended geography fallback query=%s", _geo_query)
            break

    # Increment loop_count each time search is re-entered as part of a repair loop.
    # followup_resolver resets loop_count to 0 at the start of every new turn,
    # so a non-zero value here means we are inside a repair iteration.
    new_loop_count = (state.loop_count or 0) + 1
    logger.info("Search node: loop_count=%s", new_loop_count)

    # Determine max_results per query.
    # Settings cap takes priority — keeps latency predictable.
    required_coverage = getattr(state, "required_coverage", None) or {}
    _min_src = required_coverage.get("min_sources", 3) if isinstance(
        required_coverage, dict) else getattr(required_coverage, "min_sources", 3)
    _setting_cap = max(
        2, min(5, settings.INVESTOR_AGENT_MAX_RESULTS_PER_QUERY))
    max_results_per_query = max(2, min(_setting_cap, int(_min_src)))

    # If search_decision is reuse_previous, skip new search entirely.
    search_decision = getattr(state, "search_decision",
                              "full_search") or "full_search"
    if search_decision == "reuse_previous":
        logger.info("Search node: skipping search, reuse_previous decision")
        return {"loop_count": new_loop_count, "search_results": list(state.search_results or []), "processing_warnings": warnings}

    logger.info(
        "Search decision | mode=%s reason=%s resolved_query=%s",
        getattr(state, "search_decision", "full_search"),
        getattr(state, "followup_reasoning", ""),
        getattr(state, "resolved_query", ""),
    )

    try:
        if not settings.TAVILY_API_KEY:
            warnings.append("search_skipped_missing_tavily_api_key")
            logger.warning("Search skipped: missing TAVILY_API_KEY")
            return {"loop_count": new_loop_count, "search_results": results, "processing_warnings": warnings}

        if not queries:
            warnings.append("search_skipped_empty_queries")
            logger.warning("Search skipped: no sub_queries available")
            return {"loop_count": new_loop_count, "search_results": results, "processing_warnings": warnings}

        tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
        # "basic" (fast) or "advanced" (thorough)
        _search_depth = settings.INVESTOR_AGENT_SEARCH_DEPTH
        tasks = [tavily_client.search(query=q, search_depth=_search_depth, max_results=max_results_per_query, include_domains=[
        ], exclude_domains=[]) for q in queries]

        logger.info("Search node: query_count=%s queries=%s",
                    len(queries), queries)
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        raw_results_count = 0
        failures = 0

        for q, resp in zip(queries, responses):
            if isinstance(resp, Exception):
                failures += 1
                logger.warning(
                    "Search query failed: query=%s error=%s", q, resp)
                continue

            for item in resp.get("results", []):
                raw_results_count += 1
                url = item.get("url", "")
                title = item.get("title", "")
                snippet = item.get("content", "")
                published_date = item.get(
                    "published_date") or item.get("published")
                # Basic dedup check
                if url and not any(r.get("url") == url for r in results):
                    result_obj = SearchResult(
                        query=q,
                        title=title,
                        url=url,
                        snippet=snippet,
                        published_date=published_date,
                        score=item.get("score", 0.0),
                        source_domain=url.split("/")[2] if "//" in url else ""
                    )
                    results.append(result_obj.model_dump())

        if failures:
            warnings.append(f"search_partial_failures={failures}")
        if not results:
            warnings.append("search_returned_no_results")

        logger.info(
            "Search node complete: raw_results=%s deduped_results=%s top_domains=%s",
            raw_results_count,
            len(results),
            [r.get("source_domain", "") for r in results[:5]],
        )
    except Exception as e:
        logger.exception("Tavily Search Failed: %s", e)
        warnings.append("search_node_exception")

    return {"loop_count": new_loop_count, "search_results": results, "processing_warnings": warnings}
