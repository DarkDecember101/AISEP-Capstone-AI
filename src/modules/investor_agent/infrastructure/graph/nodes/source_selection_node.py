from typing import Dict, Any, List, Literal
import logging
from pydantic import BaseModel, Field
from src.modules.investor_agent.application.dto.state import GraphState, SelectedSource, SearchResult, as_model_list
from src.shared.providers.llm.gemini_client import GeminiClient
from src.shared.config.settings import settings


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


def _geo_score(result: SearchResult, geo_terms: List[str]) -> int:
    """Return 1 if result title or snippet mentions any geo term, else 0."""
    if not geo_terms:
        return 0
    text = ((result.title or "") + " " + (result.snippet or "")).lower()
    return 1 if any(t in text for t in geo_terms) else 0


def _heuristic_select(
    search_results: List[SearchResult],
    geo_terms: List[str] | None = None,
) -> List[SelectedSource]:
    deduped: List[SearchResult] = []
    seen_urls = set()
    _geo = geo_terms or []
    for result in sorted(
        search_results,
        key=lambda item: (_geo_score(item, _geo), item.score),
        reverse=True,
    ):
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
    warnings = list(getattr(state, "processing_warnings", []))
    search_results = as_model_list(state.search_results, SearchResult)

    if not search_results:
        warnings.append("source_selection_received_empty_search_results")
        logger.warning("Source selection: no search results")
        return {"selected_sources": [], "processing_warnings": warnings}

    # Fast path: heuristic selection (default) — skips one LLM call (~20-30s).
    # Set INVESTOR_AGENT_LLM_SOURCE_SELECTION=true to re-enable LLM selection.
    if not settings.INVESTOR_AGENT_LLM_SOURCE_SELECTION:
        _geo_entities = [
            e.lower() for e in (getattr(state, "resolved_entities", []) or [])
            if e.lower() in {
                "vietnam", "việt nam", "indonesia", "singapore", "thailand", "thái lan",
                "malaysia", "philippines", "myanmar", "cambodia", "laos", "china", "japan", "korea",
            }
        ]
        selected = _heuristic_select(search_results, _geo_entities)
        warnings.append("source_selection_heuristic_fast_path")
        logger.info(
            "Source selection: heuristic fast path selected=%s", len(selected))
        return {
            "selected_sources": [s.model_dump() for s in selected],
            "processing_warnings": warnings,
        }

    llm = GeminiClient()
    reject_reasons: Dict[str, int] = {
        "duplicate_source": 0,
        "missing_metadata": 0,
        "trust_below_threshold": 0,
        "llm_not_selected": 0,
    }

    # Truncate each snippet to 150 chars so the prompt stays within LLM token budget.
    # Full snippets from 10-15 Tavily results can exceed 4000 tokens and cause timeouts.
    sr_snippet = "\n".join(
        [f"[{i}] URL: {r.url}\nDomain: {r.source_domain}\nTitle: {r.title}\nSnippet: {r.snippet[:150] if r.snippet else ''}" for i, r in enumerate(search_results)])

    query_to_use = (state.resolved_query or state.user_query or "").strip()

    _req_cov = getattr(state, "required_coverage", None) or {}
    if isinstance(_req_cov, dict):
        required_facets = _req_cov.get("required_facets", [])
        min_sources = _req_cov.get("min_sources", 3)
    else:
        required_facets = getattr(_req_cov, "required_facets", [])
        min_sources = getattr(_req_cov, "min_sources", 3)

    _geo_focus_entities = [
        e for e in (getattr(state, "resolved_entities", []) or [])
        if e.lower() in {
            "vietnam", "việt nam", "indonesia", "singapore", "thailand", "thái lan",
            "malaysia", "philippines", "myanmar", "cambodia", "laos", "china", "japan", "korea",
        }
    ]
    _geo_focus_str = ", ".join(
        _geo_focus_entities) if _geo_focus_entities else ""

    prompt = f"""
    You are selecting sources for an investor market-intelligence query.

Query: "{query_to_use}"
Intent: {state.intent}
Geography focus: {_geo_focus_str if _geo_focus_str else 'none'}
Required facets: {required_facets}
Minimum useful sources target: {min_sources}

Goal:
Select the strongest sources for factual accuracy, reliability, and facet coverage.

Evaluate each result by:
1. relevance to the query
2. source trustworthiness
3. recency when relevant
4. evidence density (numbers, dates, concrete facts vs generic commentary)
5. uniqueness (avoid duplicates and near-duplicates)
6. contribution to covering the required facets

Trust tier guidance:
- High: regulators, government, exchanges, official statistics, central banks, company filings, major research reports, top-tier financial/business media
- Medium: credible industry publications, company pages, reputable trade media
- Low: aggregators, generic blogs, weak summaries, low-substance videos, SEO pages

Selection rules:
1. Prefer strong written sources over weak summaries or videos when both are available.
2. Do not keep multiple near-duplicate sources unless they materially corroborate an important claim.
3. Try to maximize coverage across the required facets where possible.
4. If only weak sources exist, keep the best available ones but assign lower trust tiers.
5. Return at most 5 sources.
6. Keep enough sources to support the minimum useful source target when possible.
7. If Geography focus is set, STRONGLY prefer sources that are specifically about that country or market.
   Deprioritize broader regional sources (e.g. "Southeast Asia") when a country-specific source covers the same topic.
   Mark broader regional sources that do not mention the target country as lower relevance.

Search results:
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
        _heuristic_geo_terms = [e.lower() for e in (
            getattr(state, "resolved_entities", []) or [])]
        selected_sources = _heuristic_select(
            search_results, geo_terms=_heuristic_geo_terms)
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
