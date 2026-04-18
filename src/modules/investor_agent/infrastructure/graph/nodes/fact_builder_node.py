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
        # Generate one claim candidate per fact so claim_verifier always has
        # something to evaluate.  These are weak fallback claims — the verifier
        # will mark them weakly_supported at best, which is honest and still
        # lets the writer produce a cautious answer rather than a hard refusal.
        for fact in facts:
            claims.append(ClaimCandidate(
                claim_id=f"claim_{fact.fact_id}",
                claim_text=fact.statement[:200],
                topic=fact.topic,
                supporting_fact_ids=[fact.fact_id],
            ))

    return {"facts": facts, "claims": claims}


async def run(state: GraphState) -> Dict[str, Any]:
    llm = GeminiClient()
    warnings = list(getattr(state, "processing_warnings", []))
    extracted_docs = as_model_list(
        state.extracted_documents, ExtractedDocument)

    # Build context: cap each doc at 1000 chars, total at 3500 chars.
    # Keeping input small prevents Gemini from generating a response that
    # exceeds its effective output-token budget and gets truncated mid-JSON,
    # which would cause a JSONDecodeError → retry sleep → asyncio timeout.
    MAX_CHARS_PER_DOC = 1000
    MAX_TOTAL_CHARS = 3500
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
        # Before giving up, try to build lightweight context from search result
        # snippets — this covers the case where Tavily Extract quota is exceeded
        # but search results still contain usable snippet text.
        search_results_raw = list(getattr(state, "search_results", []) or [])
        for r in search_results_raw[:6]:
            url = r.get("url", "") if isinstance(
                r, dict) else getattr(r, "url", "")
            snippet = (
                (r.get("snippet") or r.get("content") or "")
                if isinstance(r, dict)
                else (getattr(r, "snippet", "") or getattr(r, "content", ""))
            )
            title = r.get("title", "") if isinstance(
                r, dict) else getattr(r, "title", "")
            if snippet and url:
                entry = f"--- Source: {url} ---\n{title}\n{snippet.strip()[:600]}\n"
                if len(docs_context) + len(entry) <= MAX_TOTAL_CHARS:
                    docs_context += entry

        if docs_context.strip():
            warnings.append("fact_builder_using_snippet_fallback")
            logger.info(
                "Fact builder: using search snippet fallback, chars=%s", len(
                    docs_context)
            )
        else:
            warnings.append("fact_builder_received_no_usable_documents")
            logger.warning(
                "Fact builder: no usable extracted content and no search snippets")
            return {"facts": [], "claims_candidate": [], "processing_warnings": warnings}

    query_to_use = (state.resolved_query or state.user_query or "").strip()
    prompt = f"""
    You are a fact builder for an investor market-intelligence pipeline.

Query: "{query_to_use}"
Intent: {state.intent}

Your job:
Extract atomic, verifiable evidence units from the source material.
Then build cautious claim candidates that stay very close to the extracted facts.

FACT RULES:
1. Each fact must be atomic: one fact per item.
2. Preserve numbers, percentages, price ranges, dates, years, and time periods exactly.
3. Do not paraphrase away precision.
4. Prefer direct factual statements over commentary, opinion, or narrative framing.
5. If a statement is forecast, interpretation, or subjective commentary, still extract it only if useful, but mark it with weaker support_strength.
6. Tag each fact with the most relevant topic/facet when possible.
7. Do not merge multiple independent facts into one fact item.

CLAIM CANDIDATE RULES:
1. Build 1-5 claim candidates only from extracted facts.
2. Each claim candidate must be narrow enough to be verified against its supporting_fact_ids.
3. Do not create broad conclusions that go beyond the facts.
4. Do not invent causal explanations unless they are explicitly stated in the source material.
5. If evidence is fragmented or weak, produce fewer and narrower claims.
6. If no good claim candidate can be formed safely, return facts only and leave candidate_claims minimal.

QUALITY STANDARD:
- Accuracy is more important than coverage.
- Claims must be easy for a verifier to evaluate.
- Numeric or directional claims must remain conservative unless directly supported.

Source material:
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
            if facts:
                # LLM extracted facts but no claims: auto-generate one-to-one
                # claim candidates so the verifier has something to evaluate.
                claims_candidate = [
                    ClaimCandidate(
                        claim_id=f"auto_{f.fact_id}",
                        claim_text=f.statement[:200],
                        topic=f.topic,
                        supporting_fact_ids=[f.fact_id],
                    )
                    for f in facts[:5]
                ]
                warnings.append("fact_builder_auto_claims_from_facts")
            else:
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
