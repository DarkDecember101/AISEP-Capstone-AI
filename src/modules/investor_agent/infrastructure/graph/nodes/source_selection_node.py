from typing import Dict, Any, List, Literal
import logging
from pydantic import BaseModel, Field
from src.modules.investor_agent.application.dto.state import GraphState, SelectedSource, SearchResult, as_model_list
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class SourceEvaluation(BaseModel):
    url: str
    trust_tier: Literal["high", "medium", "low"]
    selection_reason: str
    keep: bool


class SelectorOutput(BaseModel):
    evaluations: List[SourceEvaluation]


HIGH_TRUST_DOMAINS = (
    ".gov",
    ".edu",
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "sec.gov",
    "worldbank.org",
    "imf.org",
)


def _tier_for_domain(domain: str) -> Literal["high", "medium", "low"]:
    if any(marker in domain for marker in HIGH_TRUST_DOMAINS):
        return "high"
    if domain:
        return "medium"
    return "low"


def _heuristic_select(search_results: List[SearchResult]) -> List[SelectedSource]:
    deduped: List[SearchResult] = []
    seen_urls = set()
    for result in sorted(search_results, key=lambda item: item.score, reverse=True):
        if not result.url or result.url in seen_urls:
            continue
        seen_urls.add(result.url)
        deduped.append(result)

    selected: List[SelectedSource] = []
    for result in deduped[:5]:
        selected.append(SelectedSource(
            url=result.url,
            title=result.title,
            source_domain=result.source_domain,
            published_date=result.published_date,
            selection_reason="Kept by heuristic ranking (score + dedup)",
            trust_tier=_tier_for_domain(result.source_domain),
        ))
    return selected


async def run(state: GraphState) -> Dict[str, Any]:
    llm = GeminiClient()
    warnings = list(getattr(state, "processing_warnings", []))
    search_results = as_model_list(state.search_results, SearchResult)
    reject_reasons: Dict[str, int] = {
        "duplicate_source": 0,
        "missing_metadata": 0,
        "trust_below_threshold": 0,
        "llm_not_selected": 0,
    }

    if not search_results:
        warnings.append("source_selection_received_empty_search_results")
        logger.warning("Source selection: no search results")
        return {"selected_sources": [], "processing_warnings": warnings}

    # We only want top 5 sources
    sr_snippet = "\\n".join(
        [f"[{i}] URL: {r.url}\\nDomain: {r.source_domain}\\nTitle: {r.title}\\nSnippet: {r.snippet}" for i, r in enumerate(search_results)])

    query_to_use = (state.resolved_query or state.user_query or "").strip()
    prompt = f"""
    Filter the following search results for an investor query '{query_to_use}' (Intent: {state.intent}).
    
    Trust Tier Logic:
    - High: Government, regulators, top tier news
    - Medium: Company pages, reputable blogs
    - Low: Aggregators, generic blogs
    
    Select the BEST 5 to keep. Drop duplicates.
    
    {sr_snippet}
    """

    keep_urls: Dict[str, SourceEvaluation] = {}
    try:
        res = await llm.generate_structured_async(
            prompt=prompt, response_schema=SelectorOutput)
        keep_urls = {
            ev.url: ev for ev in res.evaluations if ev.keep and ev.url}
    except Exception as error:
        warnings.append("source_selection_llm_exception")
        logger.warning("Source selection LLM failed: %s", error)

    selected_sources = []
    seen_urls = set()
    for r in search_results:
        if not r.url or not r.title:
            reject_reasons["missing_metadata"] += 1
            continue
        if r.url in seen_urls:
            reject_reasons["duplicate_source"] += 1
            continue

        ev = keep_urls.get(r.url)
        if keep_urls and not ev:
            reject_reasons["llm_not_selected"] += 1
            continue

        trust_tier = ev.trust_tier if ev else _tier_for_domain(r.source_domain)
        if trust_tier == "low" and len(selected_sources) >= 3:
            reject_reasons["trust_below_threshold"] += 1
            continue

        seen_urls.add(r.url)
        selected_sources.append(SelectedSource(
            url=r.url,
            title=r.title,
            source_domain=r.source_domain,
            published_date=r.published_date,
            selection_reason=ev.selection_reason if ev else "Kept by fallback heuristic",
            trust_tier=trust_tier
        ))

        if len(selected_sources) >= 5:
            break

    if not selected_sources:
        selected_sources = _heuristic_select(search_results)
        if selected_sources:
            warnings.append("source_selection_used_heuristic_fallback")

    if not selected_sources:
        warnings.append("source_selection_selected_zero_sources")

    logger.info(
        "Source selection: raw=%s selected=%s rejected=%s selected_domains=%s",
        len(search_results),
        len(selected_sources),
        reject_reasons,
        [source.source_domain for source in selected_sources],
    )

    return {
        "selected_sources": [source.model_dump() for source in selected_sources],
        "processing_warnings": warnings,
    }
