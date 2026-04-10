import asyncio
from typing import Dict, Any, List
import logging
from src.modules.investor_agent.application.dto.state import GraphState, ExtractedDocument, SelectedSource, as_model_list
from src.shared.config.settings import settings
from src.shared.observability.provider_tracker import track_provider_async
from tavily import AsyncTavilyClient


logger = logging.getLogger(__name__)


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
            async with track_provider_async("tavily_extract"):
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
