import asyncio
from typing import Dict, Any, List
import logging
from src.modules.investor_agent.application.dto.state import GraphState, SearchResult
from src.shared.config.settings import settings
from src.shared.observability.provider_tracker import track_provider_async
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

    # Increment loop_count each time search is re-entered as part of a repair loop.
    # followup_resolver resets loop_count to 0 at the start of every new turn,
    # so a non-zero value here means we are inside a repair iteration.
    new_loop_count = (state.loop_count or 0) + 1
    logger.info("Search node: loop_count=%s", new_loop_count)

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
            return {"search_results": results, "processing_warnings": warnings}

        if not queries:
            warnings.append("search_skipped_empty_queries")
            logger.warning("Search skipped: no sub_queries available")
            return {"search_results": results, "processing_warnings": warnings}

        tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
        tasks = []
        for q in queries:
            async def _search_one(_q=q):
                async with track_provider_async("tavily_search"):
                    return await tavily_client.search(
                        query=_q, search_depth="advanced", max_results=3,
                        include_domains=[], exclude_domains=[],
                    )
            tasks.append(_search_one())

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

    return {"search_results": results, "processing_warnings": warnings}
