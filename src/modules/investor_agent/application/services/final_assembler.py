from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

from src.modules.investor_agent.application.dto.state import GroundingSummary
from src.modules.investor_agent.application.services.scope_guard import (
    build_out_of_scope_payload,
    decide_scope,
    get_caveat,
)

FALLBACK_NO_EVIDENCE = "I could not find enough reliable evidence to answer this confidently."
FALLBACK_CONFLICT = "The available sources conflict on key points, so I cannot provide a confident conclusion."
FALLBACK_GENERIC = "I could not produce a grounded final answer from the available verified evidence."
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_CONFLICT_PATTERN = re.compile(
    r"\b(conflict|conflicting|contradict|inconsistent|disagree)\b", re.IGNORECASE)


def _to_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return {}


def _normalize_references(references: Any) -> List[Dict[str, str]]:
    if not isinstance(references, list):
        return []

    normalized: List[Dict[str, str]] = []
    for reference in references:
        data = _to_dict(reference)
        title = (data.get("title") or "").strip()
        url = (data.get("url") or "").strip()
        source_domain = (data.get("source_domain") or "").strip()
        if not title or not url:
            continue
        normalized.append(
            {
                "title": title,
                "url": url,
                "source_domain": source_domain,
            }
        )
    return normalized


def _default_grounding_summary(state: Mapping[str, Any], reference_count: int) -> Dict[str, Any]:
    verified_claims = state.get("verified_claims") or []
    unsupported_claims = state.get("unsupported_claims") or []
    conflicting_claims = state.get("conflicting_claims") or []

    verified_claim_count = len([c for c in verified_claims if getattr(
        c, "status", None) == "supported" or (isinstance(c, dict) and c.get("status") == "supported")])
    weakly_supported_claim_count = len([c for c in verified_claims if getattr(
        c, "status", None) == "weakly_supported" or (isinstance(c, dict) and c.get("status") == "weakly_supported")])

    coverage_assessment = state.get("coverage_assessment")
    coverage_status = "insufficient"
    if coverage_assessment is not None:
        if isinstance(coverage_assessment, dict):
            coverage_status = coverage_assessment.get(
                "coverage_status", "insufficient")
        elif hasattr(coverage_assessment, "coverage_status"):
            coverage_status = getattr(
                coverage_assessment, "coverage_status", "insufficient")

    summary = GroundingSummary(
        verified_claim_count=verified_claim_count,
        weakly_supported_claim_count=weakly_supported_claim_count,
        conflicting_claim_count=len(conflicting_claims),
        unsupported_claim_count=len(unsupported_claims),
        reference_count=reference_count,
        coverage_status=coverage_status,
    )
    return summary.model_dump()


def _fallback_answer(verified_claim_count: int, conflicting_claim_count: int) -> str:
    if verified_claim_count == 0:
        return FALLBACK_NO_EVIDENCE
    if conflicting_claim_count > 0:
        return FALLBACK_CONFLICT
    return FALLBACK_GENERIC


def _enforce_scope_payload(state: Mapping[str, Any], assembled: Dict[str, Any]) -> Dict[str, Any]:
    query = (state.get("resolved_query")
             or state.get("user_query") or "").strip()
    intent = assembled.get("intent") or state.get("intent")
    confidence = state.get("router_confidence") or "low"
    reasoning = state.get("router_reasoning") or ""
    decision = decide_scope(
        query=query,
        router_intent=intent,
        router_confidence=confidence,
        router_reasoning=reasoning,
    )

    if decision.is_out_of_scope:
        payload = build_out_of_scope_payload(query)
        payload["resolved_query"] = state.get("resolved_query", "")
        payload["processing_warnings"] = list(
            dict.fromkeys(list(assembled.get("processing_warnings")
                          or []) + payload["processing_warnings"])
        )
        payload["caveats"] = list(dict.fromkeys(
            list(assembled.get("caveats") or []) + [get_caveat(query)]))
        if decision.refusal_reason:
            payload["writer_notes"] = list(dict.fromkeys(
                list(payload.get("writer_notes") or []) + [f"scope_guard_refusal_reason:{decision.refusal_reason}"]))
        return payload
    assembled["intent"] = decision.final_intent
    return assembled


def _canonicalize_citations(
    final_answer: str,
    references: List[Dict[str, str]],
    processing_warnings: List[str],
) -> tuple[str, List[Dict[str, str]], List[str]]:
    if not final_answer:
        return final_answer, [], processing_warnings

    matches = list(_CITATION_PATTERN.finditer(final_answer))
    if not matches:
        if references:
            processing_warnings.append(
                "unused_references_removed_no_citations")
        return final_answer, [], processing_warnings

    max_ref_idx = len(references)
    citation_order: List[int] = []
    invalid_indexes: List[int] = []
    for match in matches:
        idx = int(match.group(1))
        if 1 <= idx <= max_ref_idx:
            if idx not in citation_order:
                citation_order.append(idx)
        else:
            invalid_indexes.append(idx)

    if invalid_indexes:
        processing_warnings.append("invalid_citation_indexes_detected")

    if not citation_order:
        repaired_answer = _CITATION_PATTERN.sub("", final_answer)
        processing_warnings.append("all_citations_invalid_repaired")
        return repaired_answer.strip(), [], processing_warnings

    old_to_new = {old_idx: new_idx for new_idx,
                  old_idx in enumerate(citation_order, start=1)}

    def _replace(match: re.Match[str]) -> str:
        old_idx = int(match.group(1))
        new_idx = old_to_new.get(old_idx)
        if new_idx is None:
            return ""
        return f"[{new_idx}]"

    rewritten_answer = _CITATION_PATTERN.sub(_replace, final_answer)
    assembled_refs = [references[idx - 1] for idx in citation_order]
    return rewritten_answer.strip(), assembled_refs, processing_warnings


def _sync_conflict_consistency(
    final_answer: str,
    caveats: List[str],
    grounding_summary: Dict[str, Any],
    processing_warnings: List[str],
) -> tuple[List[str], Dict[str, Any], List[str]]:
    caveats = list(caveats)
    conflict_mentioned = bool(_CONFLICT_PATTERN.search(final_answer or "")) or any(
        bool(_CONFLICT_PATTERN.search(caveat or "")) for caveat in caveats
    )

    conflicting_count = int(grounding_summary.get(
        "conflicting_claim_count", 0) or 0)
    if conflict_mentioned and conflicting_count == 0:
        grounding_summary["conflicting_claim_count"] = 1
        processing_warnings.append("conflict_count_repaired_from_caveat")
        conflicting_count = 1

    if conflicting_count > 0:
        if not any(bool(_CONFLICT_PATTERN.search(caveat or "")) for caveat in caveats):
            caveats.append(
                "Sources contain conflicting evidence on key points.")
        grounding_summary["coverage_status"] = "conflicting"
    else:
        caveats = [c for c in caveats if not bool(
            _CONFLICT_PATTERN.search(c or ""))]

    return caveats, grounding_summary, processing_warnings


def assemble_final_response(state: Mapping[str, Any]) -> Dict[str, Any]:
    final_answer = (state.get("final_answer") or "") if isinstance(
        state.get("final_answer"), str) else ""

    references = _normalize_references(state.get("references"))
    caveats = list(state.get("caveats") or [])
    writer_notes = list(state.get("writer_notes") or [])
    processing_warnings = list(state.get("processing_warnings") or [])

    verified_claims = state.get("verified_claims") or []
    conflicting_claims = state.get("conflicting_claims") or []
    verified_claim_count = len(verified_claims)
    conflicting_claim_count = len(conflicting_claims)

    fallback_triggered = False
    if not final_answer.strip():
        processing_warnings.append("writer_returned_empty_answer")
        final_answer = _fallback_answer(
            verified_claim_count, conflicting_claim_count)
        fallback_triggered = True

    coverage_assessment = state.get("coverage_assessment")
    coverage_status = None
    if coverage_assessment is not None:
        if isinstance(coverage_assessment, dict):
            coverage_status = coverage_assessment.get("coverage_status")
        elif hasattr(coverage_assessment, "coverage_status"):
            coverage_status = getattr(coverage_assessment, "coverage_status")

    if coverage_status and coverage_status != "sufficient":
        caveat = f"Research coverage: {coverage_status}"
        if caveat not in caveats:
            caveats.append(caveat)

    grounding_summary = state.get("grounding_summary")
    if grounding_summary is None:
        grounding_summary_dict = _default_grounding_summary(
            state, len(references))
    elif isinstance(grounding_summary, dict):
        grounding_summary_dict = grounding_summary
    elif hasattr(grounding_summary, "model_dump"):
        grounding_summary_dict = grounding_summary.model_dump()
    else:
        grounding_summary_dict = _default_grounding_summary(
            state, len(references))

    grounding_summary_dict["reference_count"] = len(references)

    final_answer, references, processing_warnings = _canonicalize_citations(
        final_answer=final_answer,
        references=references,
        processing_warnings=processing_warnings,
    )
    grounding_summary_dict["reference_count"] = len(references)

    caveats, grounding_summary_dict, processing_warnings = _sync_conflict_consistency(
        final_answer=final_answer,
        caveats=caveats,
        grounding_summary=grounding_summary_dict,
        processing_warnings=processing_warnings,
    )

    processing_warnings = list(dict.fromkeys(processing_warnings))

    assembled = {
        "intent": state.get("intent", "unknown"),
        "resolved_query": state.get("resolved_query", ""),
        "final_answer": final_answer,
        "references": references,
        "caveats": caveats,
        "writer_notes": writer_notes,
        "processing_warnings": processing_warnings,
        "grounding_summary": grounding_summary_dict,
        "fallback_triggered": fallback_triggered,
    }

    assembled = _enforce_scope_payload(state, assembled)
    if not (assembled.get("final_answer") or "").strip():
        assembled["final_answer"] = FALLBACK_GENERIC
        assembled["processing_warnings"] = list(
            dict.fromkeys(list(assembled.get("processing_warnings")
                          or []) + ["final_integrity_fallback_triggered"])
        )
    return assembled
