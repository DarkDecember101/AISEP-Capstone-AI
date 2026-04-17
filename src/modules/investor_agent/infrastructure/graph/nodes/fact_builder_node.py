from typing import Dict, Any, List
import logging
from pydantic import BaseModel, Field
from src.modules.investor_agent.application.dto.state import GraphState, FactItem, ClaimCandidate, ExtractedDocument, as_model_list
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


class FactExtractionResult(BaseModel):
    items: List[FactItem]
    candidate_claims: List[ClaimCandidate]


def _fallback_build_from_documents(state: GraphState) -> Dict[str, List[Any]]:
    extracted_docs = as_model_list(
        state.extracted_documents, ExtractedDocument)
    facts: List[FactItem] = []
    claims: List[ClaimCandidate] = []

    usable_docs = [
        doc for doc in extracted_docs if doc.content and doc.content.strip()]
    for index, document in enumerate(usable_docs[:5], start=1):
        excerpt = document.content.strip().replace("\n", " ")[:280]
        fact_id = f"fact_{index}"
        facts.append(FactItem(
            fact_id=fact_id,
            statement=excerpt,
            entity=document.source_domain or "unknown_entity",
            topic=(state.intent or "mixed"),
            source_url=document.url,
            source_title=document.title or document.url,
            support_strength="weak",
        ))

    if facts:
        grouped = [fact.fact_id for fact in facts[:3]]
        claims.append(ClaimCandidate(
            claim_id="claim_1",
            claim_text=f"Evidence from selected sources related to '{state.user_query}' indicates actionable findings.",
            topic=(state.intent or "mixed"),
            supporting_fact_ids=grouped,
        ))

    return {"facts": facts, "claims": claims}


async def run(state: GraphState) -> Dict[str, Any]:
    llm = GeminiClient()
    warnings = list(getattr(state, "processing_warnings", []))
    extracted_docs = as_model_list(
        state.extracted_documents, ExtractedDocument)

    # Build context: cap each doc at 2000 chars, total at 8000 chars to keep
    # prompt size manageable and avoid Gemini timeouts on large payloads.
    MAX_CHARS_PER_DOC = 2000
    MAX_TOTAL_CHARS = 8000
    docs_context = ""
    for d in extracted_docs:
        if d.extract_status in ["success", "partial"] and d.content:
            chunk = d.content.strip()[:MAX_CHARS_PER_DOC]
            entry = f"--- Source: {d.url} ---\n{chunk}\n"
            if len(docs_context) + len(entry) > MAX_TOTAL_CHARS:
                break
            docs_context += entry

    logger.info("Fact builder: extracted_doc_count=%s context_chars=%s",
                len(extracted_docs), len(docs_context))

    if not docs_context.strip():
        warnings.append("fact_builder_received_no_usable_documents")
        logger.warning("Fact builder: no usable extracted content")
        return {"facts": [], "claims_candidate": [], "processing_warnings": warnings}

    query_to_use = (state.resolved_query or state.user_query or "").strip()
    prompt = f"""
    You are an expert financial researcher extracting facts for: "{query_to_use}".
    
    Extract specific, verifiable facts (especially numbers, dates, precise statements).
    Then formulate 2-5 Claim Candidates that would form the core of your final answer, grouping related fact_ids into each claim.
    
    {docs_context}
    """

    facts: List[FactItem] = []
    claims_candidate: List[ClaimCandidate] = []

    try:
        res = await llm.generate_structured_async(
            prompt=prompt, response_schema=FactExtractionResult, model_name="gemini-2.5-flash",
            timeout=40.0)
        facts = res.items or []
        claims_candidate = res.candidate_claims or []
    except Exception as error:
        warnings.append("fact_builder_llm_exception")
        logger.warning("Fact builder LLM failed: %r", error)

    if not facts or not claims_candidate:
        fallback = _fallback_build_from_documents(state)
        if not facts:
            facts = fallback["facts"]
        if not claims_candidate:
            claims_candidate = fallback["claims"]
        warnings.append("fact_builder_used_fallback")

    topic_summary: Dict[str, int] = {}
    for fact in facts:
        topic_summary[fact.topic] = topic_summary.get(fact.topic, 0) + 1

    logger.info(
        "Fact builder complete: facts=%s claims_candidate=%s facts_by_topic=%s",
        len(facts),
        len(claims_candidate),
        topic_summary,
    )

    return {
        "facts": [fact.model_dump() for fact in facts],
        "claims_candidate": [claim.model_dump() for claim in claims_candidate],
        "processing_warnings": warnings,
    }
