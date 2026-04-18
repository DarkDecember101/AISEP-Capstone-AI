import re
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

# Detects numbers, percentages, dollar amounts, growth rates, rankings.
_MATERIAL_RE = re.compile(
    r"\d+\.?\d*\s*%"
    r"|\$\s*\d"
    r"|\b\d+\s*(billion|million|trillion|bn|mn|k)\b"
    r"|\b(rank(ed)?|#\s*\d|top\s+\d|largest|fastest|leading|highest|lowest)\b"
    r"|\b\d{4}\b",
    re.IGNORECASE,
)


def _is_material_claim(text: str) -> bool:
    """True if the claim contains numbers, rates, rankings, or directional
    superlatives that require stronger evidence to be trusted."""
    return bool(_MATERIAL_RE.search(text or ""))


def _unique_domains(sources: Dict[str, SelectedSource]) -> set:
    domains = set()
    for s in sources.values():
        domain = s.source_domain or (
            s.url.split("/")[2] if "//" in s.url else "")
        if domain:
            domains.add(domain)
    return domains


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

    domains = _unique_domains(sources)
    strength_rank = {"weak": 1, "medium": 2, "strong": 3}
    max_strength = max(strength_rank.get(f.support_strength, 1)
                       for f in supporting_facts)
    is_material = _is_material_claim(claim.claim_text)

    if is_material:
        # Material claims (numbers, rates, rankings) need either:
        #   >=2 unique source domains, OR 1 strong-strength fact.
        if len(domains) >= 2 or max_strength >= 3:
            status = "supported"
            note = f"Material claim corroborated across {len(domains)} domain(s)."
        elif max_strength >= 2:
            status = "weakly_supported"
            note = "Material claim has single-source medium evidence; treat with caution."
        else:
            status = "weakly_supported"
            note = "Material claim has weak single-source support."
    else:
        # Non-material claims: 1 medium/strong fact OR >=2 unique domains is sufficient.
        if max_strength >= 2 or len(domains) >= 2:
            status = "supported"
            note = "Claim supported by fact evidence."
        else:
            status = "weakly_supported"
            note = "Limited single-source weak evidence."

    return VerifiedClaim(
        claim_id=claim.claim_id,
        claim_text=claim.claim_text,
        status=status,
        supporting_sources=list(sources.values()),
        verification_note=note,
    )


def _detect_conflicts(
    verified_claims: List[VerifiedClaim],
) -> tuple[List[VerifiedClaim], List[VerifiedClaim]]:
    """Conservative conflict detection.

    Two material claims are flagged as conflicting when they:
    - Both contain material signals (numbers, rates, rankings)
    - Share >=5 meaningful topic keywords (likely about the same subject)
    - Have zero source URL overlap (different evidence bases disagree)

    Non-material claims are not flagged to avoid over-sensitivity.
    Pool of <=2 claims is never flagged — too few claims to distinguish
    a genuine conflict from simply two complementary facts.
    """
    # Too few claims: risk of false positives is too high.
    if len(verified_claims) <= 2:
        return verified_claims, []

    _STOP = {"the", "a", "an", "is", "in", "of", "and", "or", "to", "that",
             "for", "by", "at", "with", "its", "it", "this", "are", "was", "be"}

    clean: List[VerifiedClaim] = []
    conflict_ids: set = set()
    conflict_pairs: List[tuple] = []

    for i, ca in enumerate(verified_claims):
        if not _is_material_claim(ca.claim_text):
            continue
        urls_a = {s.url for s in ca.supporting_sources}
        words_a = set(ca.claim_text.lower().split()) - _STOP

        for j, cb in enumerate(verified_claims):
            if i >= j or not _is_material_claim(cb.claim_text):
                continue
            urls_b = {s.url for s in cb.supporting_sources}
            if urls_a & urls_b:  # shared sources → not a conflict
                continue
            words_b = set(cb.claim_text.lower().split()) - _STOP
            if len(words_a & words_b) >= 5:
                conflict_ids.add(ca.claim_id)
                conflict_ids.add(cb.claim_id)
                conflict_pairs.append((ca.claim_id, cb.claim_id))

    conflicts: List[VerifiedClaim] = []
    for claim in verified_claims:
        if claim.claim_id in conflict_ids:
            pair_note = next(
                (f"Conflicts with claim {b if claim.claim_id == a else a}"
                 for a, b in conflict_pairs
                 if claim.claim_id in (a, b)),
                "Potential conflict detected with another claim on the same topic.",
            )
            conflicts.append(VerifiedClaim(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                status="conflicting",
                supporting_sources=claim.supporting_sources,
                verification_note=pair_note,
            ))
        else:
            clean.append(claim)

    return clean, conflicts


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

        verified_claims.append(verified)

    # Conflict detection: promote conflicting pairs out of verified_claims.
    verified_claims, conflicting_claims = _detect_conflicts(verified_claims)

    supported_count = len(
        [c for c in verified_claims if c.status == "supported"])
    weak_count = len(
        [c for c in verified_claims if c.status == "weakly_supported"])
    conflict_count = len(conflicting_claims)
    unsupported_count = len(unsupported_claims)

    # Coverage is measured by unique source domains across all verified claims,
    # not by claim count — this is a better proxy for evidence breadth.
    all_domains: set = set()
    for claim in verified_claims:
        all_domains |= _unique_domains(
            {s.url: s for s in claim.supporting_sources})

    min_sources = (
        required_coverage.min_sources if required_coverage else 1) or 1
    covered = supported_count + weak_count
    # Sufficient if we have enough unique source domains OR enough claims.
    coverage_status = "sufficient" if (
        len(all_domains) >= min_sources or covered >= max(1, min_sources)
    ) else "insufficient"

    missing_facets = []
    if required_coverage and required_coverage.required_facets:
        missing_facets = list(
            required_coverage.required_facets) if coverage_status == "insufficient" else []

    # Allow at most 1 repair pass: search_node increments loop_count on every
    # entry, so after the first search loop_count==1.  We allow repair when
    # loop_count < 2 (i.e. exactly one search has run so far).
    has_any_claims = bool(claims_candidate)
    coverage = CoverageAssessment(
        coverage_status=coverage_status,
        missing_facets=missing_facets,
        needs_repair_loop=(
            has_any_claims
            and coverage_status != "sufficient"
            and state.loop_count < 2
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
