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
from src.modules.investor_agent.application.services.scope_guard import get_refusal, get_caveat, is_greeting
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


def _reference_from_source(source: Any) -> ReferenceItem:
    return ReferenceItem(
        title=getattr(source, "title", "") or "",
        url=getattr(source, "url", "") or "",
        source_domain=getattr(source, "source_domain", "") or "",
    )


def _format_verified_claim(claim: VerifiedClaim) -> str:
    primary_url = claim.supporting_sources[0].url if claim.supporting_sources else ""
    note = f" Note: {claim.verification_note}" if claim.verification_note else ""
    return f"- [SRC:{primary_url}] {claim.claim_text} ({claim.status}).{note}"


async def run(state: GraphState) -> Dict[str, Any]:
    if getattr(state, "intent", None) == "out_of_scope":
        query = (getattr(state, "user_query", "") or "").strip()
        refusal = get_refusal(query)
        caveat = get_caveat(query)
        greeting_query = is_greeting(query)
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
            "caveats": [caveat] if caveat else [],
            "writer_notes": ["greeting_response"] if greeting_query else ["scope_guard_refusal"],
            "processing_warnings": list(dict.fromkeys((state.processing_warnings or []) + (["greeting_query"] if greeting_query else ["out_of_scope_query"]))),
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

    if not supported_claims and conflicts:
        # Promote conflicting claims to weakly_supported so the writer has
        # something to work with. The prompt explicitly marks them as disputed.
        supported_claims = [
            VerifiedClaim(
                claim_id=c.claim_id,
                claim_text=c.claim_text,
                status="weakly_supported",
                supporting_sources=c.supporting_sources,
                verification_note=f"[DISPUTED] {c.verification_note}",
            )
            for c in conflicts
        ]
        conflicts = []  # already embedded in supported_claims with DISPUTED tag

    grounded_refs_by_url: Dict[str, ReferenceItem] = {}
    for claim in supported_claims + conflicts:
        for source in claim.supporting_sources:
            if is_valid_reference(source.url, source.title):
                grounded_refs_by_url[source.url] = _reference_from_source(source)

    verified_str = "\n".join(_format_verified_claim(c) for c in supported_claims)
    conflicts_str = "\n".join([f"- CONFLICT: {c.claim_text}" for c in conflicts])
    allowed_refs_str = "\n".join(
        f"[{index}] {ref.title} | {ref.url} | {ref.source_domain}"
        for index, ref in enumerate(grounded_refs_by_url.values(), start=1)
    )

    if not supported_claims and not conflicts:
        _raw_query_for_fallback = (
            getattr(state, "user_query", "") or "").strip()
        _vi_chars = sum(
            1 for ch in _raw_query_for_fallback if "\u00c0" <= ch <= "\u1ef9")
        _is_vi_fallback = _vi_chars >= 2 or any(w in _raw_query_for_fallback.lower() for w in (
            "bạn", "tôi", "của", "là", "không", "có", "các", "trong", "này", "được", "việt"))
        if _is_vi_fallback:
            _fallback_msg = "Không tìm thấy đủ dữ liệu có thể xác minh để trả lời câu hỏi này một cách đáng tin cậy."
            _fallback_caveat = "Bằng chứng tìm thấy không đủ để đưa ra kết luận."
        else:
            _fallback_msg = "No sufficient, verifiable data was found to confidently answer this query based on credible sources."
            _fallback_caveat = "Insufficient evidence found during research."
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
            "final_answer": _fallback_msg,
            "references": [],
            "caveats": [_fallback_caveat],
            "writer_notes": ["fallback_insufficient_evidence"],
            "processing_warnings": ["Zero supported claims extracted."],
            "grounding_summary": empty_summary.model_dump(),
            "messages": [AIMessage(content=_fallback_msg)]
        }

    query_to_use = getattr(state, "resolved_query",
                           state.user_query) or state.user_query

    # Compute coverage_status early so it can be referenced in the prompt.
    coverage_assessment = as_model(
        getattr(state, "coverage_assessment", None), CoverageAssessment)
    coverage_status = coverage_assessment.coverage_status if coverage_assessment else "insufficient"

    prompt = f"""
You are an expert AI Research Writer for investors.

Query: "{query_to_use}"
Intent: {state.intent}
Coverage status: {coverage_status}

LANGUAGE RULE:
- Detect the language of the Query and respond in the same language if it is clearly Vietnamese or English.
- If the query is in Vietnamese, respond in natural Vietnamese.
- If the query is in English, respond in English.
- If the query is in another language or unclear, default to English.
- Common investment, product, or technical terms (e.g. fintech, funding, Series A, market cap, unit economics) may remain in English when more natural.

Your task:
Write an investor-oriented answer using ONLY the Verified Claims below.

IMPORTANT:
- Do not stop at summarizing facts.
- Explain why the information matters from an investor perspective.
- Stay grounded in the Verified Claims only.
- You may make limited, careful implications if they directly follow from the Verified Claims, but do NOT invent numbers, dates, market sizes, growth rates, or unsupported causal claims.
- Do NOT present the answer as investment advice.

CRITICAL RULES:
1. DO NOT fabricate or guess any numbers, dates, growth rates, rankings, or unsupported directional conclusions.
2. DO NOT include any factual claim that is not explicitly supported by the Verified Claims.
3. Treat weakly supported points cautiously and signal uncertainty clearly.
4. If there is conflicting information and it is relevant, summarize it neutrally without resolving it yourself.
5. If coverage is thin or insufficient, say so clearly and keep the answer appropriately cautious.
6. Use only the provided real references. Do not invent or alter URLs.

WRITING STYLE:
- Clear, direct, investor-relevant.
- Avoid generic textbook definitions unless explicitly asked.
- Prioritize materiality over background detail.
- Avoid long introductory paragraphs.

DEFAULT ANSWER STRUCTURE:
Use this structure whenever appropriate:
1. Brief summary
2. Why this matters for investors
3. Key risks or caveats
4. What to watch next

REFERENCE RULE:
- In final_answer, use inline citation markers like [1], [2].
- The references list must map cleanly to the sources actually used.
- Do not cite a source that is not used in the answer.
- The references list must be a subset of the Allowed References below, copied exactly.
- If a point cannot be tied to an Allowed Reference, do not cite it.

Allowed References:
{allowed_refs_str}

Verified Claims:
{verified_str}

Conflicting Information:
{conflicts_str}
    """

    res = await llm.generate_structured_async(
        prompt=prompt, response_schema=FinalOutput, model_name="gemini-2.5-flash")

    answer_text = (res.final_answer or "").strip()
    writer_notes = []
    if not answer_text:
        writer_notes.append("writer_model_returned_empty_answer")
        answer_text = "I found limited verified evidence for this question, so I cannot provide a strong conclusion."

    final_refs: List[ReferenceItem] = []
    warnings = []
    seen_urls = set()
    for r in res.references:
        grounded_ref = grounded_refs_by_url.get(r.url)
        if grounded_ref and grounded_ref.url not in seen_urls:
            final_refs.append(grounded_ref)
            seen_urls.add(grounded_ref.url)
        else:
            warnings.append(f"Dropped ungrounded/invalid reference: {r.url}")

    if not final_refs and grounded_refs_by_url:
        final_refs = list(grounded_refs_by_url.values())[:5]
        warnings.append("writer_used_grounded_reference_fallback")

    if not final_refs and using_previous_context:
        previous_refs = as_model_list(
            getattr(state, "previous_references", []), ReferenceItem)
        relevance_tokens = _relevance_tokens(state)
        final_refs = [reference for reference in previous_refs if _reference_is_relevant(
            reference, relevance_tokens)]
        if final_refs:
            warnings.append("writer_reused_previous_references")

    final_caveats = res.caveats
    if coverage_assessment and coverage_assessment.coverage_status != "sufficient":
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
