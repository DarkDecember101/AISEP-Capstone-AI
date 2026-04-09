from typing import Dict, Any, List
import logging
from src.modules.investor_agent.application.dto.state import (
    GraphState,
    VerifiedClaim,
    CoverageAssessment,
    SelectedSource,
    ClaimCandidate,
    FactItem,
    RequiredCoverage,
    as_model,
    as_model_list,
)


logger = logging.getLogger(__name__)


def _selected_source_for_url(state: GraphState, url: str, title_fallback: str) -> SelectedSource:
    selected_sources = as_model_list(state.selected_sources, SelectedSource)
    found = next(
        (source for source in selected_sources if source.url == url), None)
    if found:
        return found
    return SelectedSource(
        url=url,
        title=title_fallback or url,
        source_domain=url.split("/")[2] if "//" in url else "",
        selection_reason="Derived from claim fact mapping",
        trust_tier="medium",
    )


def _verify_claim(claim: ClaimCandidate, facts_by_id: Dict[str, FactItem], state: GraphState) -> VerifiedClaim | None:
    supporting_facts = [facts_by_id[fact_id]
                        for fact_id in claim.supporting_fact_ids if fact_id in facts_by_id]
    if not supporting_facts:
        return None

    sources: Dict[str, SelectedSource] = {}
    for fact in supporting_facts:
        source = _selected_source_for_url(
            state, fact.source_url, fact.source_title)
        sources[source.url] = source

    strength_rank = {"weak": 1, "medium": 2, "strong": 3}
    max_strength = max([strength_rank.get(fact.support_strength, 1)
                       for fact in supporting_facts])

    if max_strength >= 2 or len(sources) >= 2:
        status = "supported"
        note = "Supported by mapped facts and sources."
    else:
        status = "weakly_supported"
        note = "Limited evidence; claim remains weakly supported."

    return VerifiedClaim(
        claim_id=claim.claim_id,
        claim_text=claim.claim_text,
        status=status,
        supporting_sources=list(sources.values()),
        verification_note=note,
    )


async def run(state: GraphState) -> Dict[str, Any]:
    warnings = list(getattr(state, "processing_warnings", []))
    facts = as_model_list(state.facts, FactItem)
    claims_candidate = as_model_list(state.claims_candidate, ClaimCandidate)
    required_coverage = as_model(state.required_coverage, RequiredCoverage)

    facts_by_id: Dict[str, FactItem] = {
        fact.fact_id: fact for fact in facts}

    verified_claims: List[VerifiedClaim] = []
    unsupported_claims: List[ClaimCandidate] = []
    conflicting_claims: List[VerifiedClaim] = []
    rejection_reasons: Dict[str, int] = {
        "no_supporting_source": 0,
        "missing_fact_reference": 0,
    }

    for claim in claims_candidate:
        missing_ids = [
            fact_id for fact_id in claim.supporting_fact_ids if fact_id not in facts_by_id]
        if missing_ids:
            rejection_reasons["missing_fact_reference"] += 1

        verified = _verify_claim(claim, facts_by_id, state)
        if verified is None:
            unsupported_claims.append(claim)
            rejection_reasons["no_supporting_source"] += 1
            continue

        if verified.status == "conflicting":
            conflicting_claims.append(verified)
        else:
            verified_claims.append(verified)

    supported_count = len(
        [claim for claim in verified_claims if claim.status == "supported"])
    weak_count = len(
        [claim for claim in verified_claims if claim.status == "weakly_supported"])
    conflict_count = len(conflicting_claims)
    unsupported_count = len(unsupported_claims)

    required = required_coverage.min_sources if required_coverage else 1
    covered = supported_count + weak_count
    coverage_status = "sufficient" if covered >= max(
        1, required) else "insufficient"
    missing_facets = []
    if required_coverage and required_coverage.required_facets:
        missing_facets = list(
            required_coverage.required_facets) if coverage_status == "insufficient" else []

    # Allow at most 1 repair pass (loop_count is incremented by search_node on
    # each re-entry, so loop_count >= 1 means we have already done one repair).
    # Also skip repair if there are no claims at all — the LLM likely failed and
    # re-searching won't help.
    has_any_claims = bool(claims_candidate)
    coverage = CoverageAssessment(
        coverage_status=coverage_status,
        missing_facets=missing_facets,
        needs_repair_loop=(
            has_any_claims
            and coverage_status != "sufficient"
            and state.loop_count < 1
        ),
    )

    if covered == 0:
        warnings.append("claim_verifier_no_verified_claims")

    logger.info(
        "Claim verifier: supported=%s weakly_supported=%s conflicting=%s unsupported=%s rejection_reasons=%s promoted_claim_ids=%s",
        supported_count,
        weak_count,
        conflict_count,
        unsupported_count,
        rejection_reasons,
        [claim.claim_id for claim in verified_claims],
    )

    return {
        "verified_claims": [claim.model_dump() for claim in verified_claims],
        "unsupported_claims": [claim.model_dump() for claim in unsupported_claims],
        "conflicting_claims": [claim.model_dump() for claim in conflicting_claims],
        "coverage_assessment": coverage.model_dump(),
        "processing_warnings": warnings,
    }
