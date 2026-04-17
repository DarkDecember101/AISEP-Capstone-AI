from typing import Dict, Any, List, Optional
import logging
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage
from src.modules.investor_agent.application.dto.state import (
    GraphState,
    ReferenceItem,
    GroundingSummary,
    VerifiedClaim,
    CoverageAssessment,
    as_model,
    as_model_list,
)
from src.modules.investor_agent.application.services.scope_guard import get_refusal, get_caveat
from src.shared.providers.llm.gemini_client import GeminiClient


logger = logging.getLogger(__name__)


def _relevance_tokens(state: GraphState) -> List[str]:
    tokens: List[str] = []
    tokens.extend([str(entity).lower() for entity in (
        getattr(state, "resolved_entities", []) or [])])
    timeframe = (getattr(state, "resolved_timeframe", "")
                 or "").strip().lower()
    if timeframe:
        tokens.append(timeframe)
    query_text = (getattr(state, "resolved_query", "")
                  or getattr(state, "user_query", "") or "").lower()
    tokens.extend([part for part in query_text.split() if len(part) > 3][:8])
    return list(dict.fromkeys([token for token in tokens if token]))


def _claim_is_relevant(claim: VerifiedClaim, tokens: List[str]) -> bool:
    if not tokens:
        return True
    text = f"{claim.claim_text} {claim.verification_note}".lower()
    return any(token in text for token in tokens)


def _reference_is_relevant(reference: ReferenceItem, tokens: List[str]) -> bool:
    if not tokens:
        return True
    text = f"{reference.title} {reference.url} {reference.source_domain}".lower()
    return any(token in text for token in tokens)


def is_valid_reference(url: str, title: str) -> bool:
    if not url or not title:
        return False
    if "example.com" in url or "example.org" in url:
        return False
    if url.strip() == "" or title.strip() == "":
        return False
    return True


class FinalOutput(BaseModel):
    final_answer: str
    references: List[ReferenceItem]
    caveats: List[str]


async def run(state: GraphState) -> Dict[str, Any]:
    if getattr(state, "intent", None) == "out_of_scope":
        query = (getattr(state, "user_query", "") or "").strip()
        refusal = get_refusal(query)
        caveat = get_caveat(query)
        summary = GroundingSummary(
            verified_claim_count=0,
            weakly_supported_claim_count=0,
            conflicting_claim_count=0,
            unsupported_claim_count=0,
            reference_count=0,
            coverage_status="insufficient",
        )
        return {
            "final_answer": refusal,
            "references": [],
            "caveats": [caveat],
            "writer_notes": ["scope_guard_refusal"],
            "processing_warnings": list(dict.fromkeys((state.processing_warnings or []) + ["out_of_scope_query"])),
            "grounding_summary": summary.model_dump(),
            "messages": [AIMessage(content=refusal)],
        }

    llm = GeminiClient()

    # Filter verified claims
    verified_claims = as_model_list(state.verified_claims, VerifiedClaim)
    supported_claims = [c for c in verified_claims if c.status in [
        "supported", "weakly_supported"]]

    using_previous_context = False
    if not supported_claims and bool(getattr(state, "reuse_previous_verified_claims", False)):
        previous_claims = as_model_list(
            getattr(state, "previous_verified_claims", []), VerifiedClaim)
        relevance_tokens = _relevance_tokens(state)
        supported_claims = [
            claim for claim in previous_claims if claim.status in ["supported", "weakly_supported"] and _claim_is_relevant(claim, relevance_tokens)
        ]
        using_previous_context = bool(supported_claims)

    conflicts = as_model_list(
        getattr(state, "conflicting_claims", []), VerifiedClaim)
    if not conflicts and bool(getattr(state, "reuse_previous_verified_claims", False)):
        previous_conflicts = as_model_list(
            getattr(state, "previous_conflicting_claims", []), VerifiedClaim)
        relevance_tokens = _relevance_tokens(state)
        conflicts = [claim for claim in previous_conflicts if _claim_is_relevant(
            claim, relevance_tokens)]

    logger.info(
        "Writer context | using_previous_context=%s followup_type=%s resolved_query=%s search_decision=%s",
        using_previous_context,
        getattr(state, "followup_type", None),
        getattr(state, "resolved_query", ""),
        getattr(state, "search_decision", "full_search"),
    )

    real_refs = {}
    for claim in supported_claims + conflicts:
        for s in claim.supporting_sources:
            if is_valid_reference(s.url, s.title):
                real_refs[s.url] = s

    verified_str = "\n".join(
        [f"- [SRC:{c.supporting_sources[0].url if c.supporting_sources else ''}] {c.claim_text} ({c.status})" for c in supported_claims])
    conflicts_str = "\n".join(
        [f"- CONFLICT: {c.claim_text}" for c in conflicts])

    if not supported_claims and not conflicts:
        empty_summary = GroundingSummary(
            verified_claim_count=0,
            weakly_supported_claim_count=0,
            conflicting_claim_count=0,
            unsupported_claim_count=len(
                getattr(state, "unsupported_claims", [])),
            reference_count=0,
            coverage_status="insufficient"
        )
        return {
            "final_answer": "No sufficient, verifiable data was found to confidently answer this query based on credible sources.",
            "references": [],
            "caveats": ["Insufficient evidence found during research."],
            "writer_notes": ["fallback_insufficient_evidence"],
            "processing_warnings": ["Zero supported claims extracted."],
            "grounding_summary": empty_summary,
            "messages": [AIMessage(content="No sufficient, verifiable data was found to confidently answer this query based on credible sources.")]
        }

    query_to_use = getattr(state, "resolved_query",
                           state.user_query) or state.user_query
    prompt = f"""
    You are an expert AI Research Writer for top tier Investors.
    Query: "{query_to_use}"
    Intent: {state.intent}
    
    Your task: Write a comprehensive, nuanced answer using ONLY the Verified Claims below.
    
    CRITICAL RULES:
    1. DO NOT fabricate or guess ANY numbers, dates, precise qualitative trends, market sizes, or growth rates.
    2. DO NOT include any claim that is not explicitly in the Verified Claims.
    3. EVERY critical claim or group of claims MUST have an inline citation marker mapping to its url like [1] or (Source: URL).
    4. If 'weakly_supported', use tentative language ("some early indications suggest...").
    5. Summarize any CONFLICTS neutrally without resolving them yourself.
    6. Include ONLY the real reference URLs that correspond to the valid claims you used. No placeholders like example.com.
    
    Verified Claims (Use ONLY these):
    {verified_str}
    
    Conflicting Information (Highlight if relevant):
    {conflicts_str}
    """

    res = await llm.generate_structured_async(
        prompt=prompt, response_schema=FinalOutput, model_name="gemini-2.5-flash")

    answer_text = (res.final_answer or "").strip()
    writer_notes = []
    if not answer_text:
        writer_notes.append("writer_model_returned_empty_answer")
        answer_text = "I found limited verified evidence for this question, so I cannot provide a strong conclusion."

    final_refs = []
    warnings = []
    for r in res.references:
        if is_valid_reference(r.url, r.title):
            final_refs.append(r)
        else:
            warnings.append(f"Dropped invalid/placeholder reference: {r.url}")

    if not final_refs and using_previous_context:
        previous_refs = as_model_list(
            getattr(state, "previous_references", []), ReferenceItem)
        relevance_tokens = _relevance_tokens(state)
        final_refs = [reference for reference in previous_refs if _reference_is_relevant(
            reference, relevance_tokens)]
        if final_refs:
            warnings.append("writer_reused_previous_references")

    final_caveats = res.caveats
    coverage_assessment = as_model(
        getattr(state, "coverage_assessment", None), CoverageAssessment)
    coverage_status = "insufficient"
    if coverage_assessment:
        coverage_status = coverage_assessment.coverage_status
        if coverage_assessment.coverage_status != "sufficient":
            final_caveats.append(
                f"Research coverage: {coverage_assessment.coverage_status}")

    v_count = len([c for c in supported_claims if c.status == "supported"])
    w_count = len(
        [c for c in supported_claims if c.status == "weakly_supported"])
    c_count = len(conflicts)
    u_count = len(getattr(state, "unsupported_claims", []))

    smry = GroundingSummary(
        verified_claim_count=v_count,
        weakly_supported_claim_count=w_count,
        conflicting_claim_count=c_count,
        unsupported_claim_count=u_count,
        reference_count=len(final_refs),
        coverage_status=coverage_status
    )

    logger.info(
        "Writer grounding summary: verified=%s weak=%s conflicting=%s unsupported=%s references=%s coverage=%s",
        v_count,
        w_count,
        c_count,
        u_count,
        len(final_refs),
        coverage_status,
    )

    return {
        "final_answer": answer_text,
        "references": [ref.model_dump() for ref in final_refs],
        "caveats": final_caveats,
        "writer_notes": writer_notes + (["writer_used_previous_context"] if using_previous_context else []),
        "processing_warnings": warnings + getattr(state, "processing_warnings", []),
        "grounding_summary": smry.model_dump(),
        "messages": [AIMessage(content=answer_text)]
    }
