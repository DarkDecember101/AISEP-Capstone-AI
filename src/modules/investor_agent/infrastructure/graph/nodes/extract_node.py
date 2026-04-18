import asyncio
from typing import Dict, Any, List
import logging
from src.modules.investor_agent.application.dto.state import GraphState, ExtractedDocument, SelectedSource, SearchResult, as_model_list
from src.shared.config.settings import settings
from tavily import AsyncTavilyClient

try:
    from tavily.errors import ForbiddenError as _TavilyForbiddenError
except ImportError:
    _TavilyForbiddenError = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


def _snippet_fallback(
    selected_sources: List[SelectedSource],
    search_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build lightweight ExtractedDocuments from search snippets when full
    extraction is unavailable (quota exceeded, network error, etc.)."""
    snippet_map: Dict[str, str] = {}
    for r in search_results:
        url = r.get("url", "") if isinstance(
            r, dict) else getattr(r, "url", "")
        snippet = (
            (r.get("snippet") or r.get("content") or "")
            if isinstance(r, dict)
            else (getattr(r, "snippet", "") or getattr(r, "content", ""))
        )
        if url and snippet:
            snippet_map[url] = snippet.strip()

    docs: List[Dict[str, Any]] = []
    for source in selected_sources:
        if not source.url or not source.url.startswith("http"):
            continue
        snippet = snippet_map.get(source.url, "").strip()
        if not snippet:
            snippet = f"{source.title}. {source.source_domain}".strip()
        content = snippet[:1500]
        extract_status = "partial" if len(content) >= 80 else "failed"
        docs.append(
            ExtractedDocument(
                url=source.url,
                title=source.title or source.url,
                source_domain=source.source_domain,
                content=content,
                extract_status=extract_status,
            ).model_dump()
        )
    return docs


async def run(state: GraphState) -> Dict[str, Any]:
    extracted: List[Dict[str, Any]] = []
    warnings = list(getattr(state, "processing_warnings", []))
    selected_sources = as_model_list(state.selected_sources, SelectedSource)
    success_count = 0
    partial_count = 0
    failed_count = 0
    empty_content_count = 0

    try:
        if not settings.TAVILY_API_KEY:
            warnings.append("extract_skipped_missing_tavily_api_key")
            logger.warning("Extract skipped: missing TAVILY_API_KEY")
            return {"extracted_documents": extracted, "processing_warnings": warnings}

        tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
        urls = [
            s.url for s in selected_sources if s.url and s.url.startswith("http")]

        if not urls:
            warnings.append("extract_received_no_valid_urls")
            logger.warning(
                "Extract skipped: no valid URLs from selected_sources")
            return {"extracted_documents": extracted, "processing_warnings": warnings}

        logger.info("Extract node: selected_sources=%s",
                    len(selected_sources))

        if urls:
            try:
                response = await tavily_client.extract(urls=urls)
                for raw_res in response.get("results", []) if isinstance(response, dict) else []:
                    content = (raw_res.get("raw_content")
                               or raw_res.get("content") or "")[:6000]
                    content_length = len(content.strip())
                    if content_length == 0:
                        extract_status = "failed"
                        failed_count += 1
                        empty_content_count += 1
                    elif content_length < 200:
                        extract_status = "partial"
                        partial_count += 1
                    else:
                        extract_status = "success"
                        success_count += 1

                    extracted_doc = ExtractedDocument(
                        url=raw_res.get("url"),
                        title=next(
                            (s.title for s in selected_sources if s.url == raw_res.get("url")), ""),
                        source_domain=next(
                            (s.source_domain for s in selected_sources if s.url == raw_res.get("url")), ""),
                        content=content,
                        extract_status=extract_status
                    )
                    extracted.append(extracted_doc.model_dump())

            except Exception as extract_err:
                # Classify the failure so downstream nodes can react appropriately.
                err_str = str(extract_err).lower()
                if _TavilyForbiddenError and isinstance(extract_err, _TavilyForbiddenError):
                    warn_key = "extract_quota_exceeded"
                elif "forbidden" in err_str or "usage limit" in err_str or "upgrade" in err_str:
                    warn_key = "extract_quota_exceeded"
                elif "timeout" in err_str or "connect" in err_str or "network" in err_str:
                    warn_key = "extract_network_error"
                else:
                    warn_key = "extract_api_error"
                warnings.append(warn_key)
                logger.warning("Extract failed (%s): %s",
                               warn_key, extract_err)

                # Fall back to search-result snippets so fact_builder has
                # something to work with instead of an empty document list.
                search_results_raw = list(
                    getattr(state, "search_results", []) or [])
                extracted = _snippet_fallback(
                    selected_sources, search_results_raw)
                warnings.append("extract_using_snippet_fallback")
                logger.info(
                    "Extract snippet fallback: built %s lightweight docs", len(
                        extracted)
                )

        if not extracted:
            warnings.append("extract_returned_no_documents")
        if failed_count:
            warnings.append(f"extract_failed_count={failed_count}")
        if empty_content_count:
            warnings.append(
                f"extract_empty_content_count={empty_content_count}")

        logger.info(
            "Extract node complete: success=%s partial=%s failed=%s empty_content=%s content_lengths=%s",
            success_count,
            partial_count,
            failed_count,
            empty_content_count,
            [len((doc.get("content") or "").strip()) for doc in extracted[:5]],
        )
    except Exception as e:
        logger.exception("Extract failed: %s", e)
        warnings.append("extract_node_exception")

    return {"extracted_documents": extracted, "processing_warnings": warnings}
