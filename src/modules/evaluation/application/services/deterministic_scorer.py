import math
from typing import Dict, Any, List
from src.shared.logging.logger import setup_logger

from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationResult,
    EvidenceMappingResult,
    RawCriterionJudgmentResult,
    CriterionEvidence,
    RawJudgment,
    EvidenceUnit
)

from src.modules.evaluation.application.dto.canonical_schema import (
    DeterministicScoringResult,
    CanonicalCriterionResult,
    CapSummary,
    CanonicalOverallResult as OverallResult,
    EvidenceLocation
)

logger = setup_logger("deterministic_scorer")


def sanitize_page_refs(units: List[EvidenceUnit], total_pages: int, warnings: List[str]) -> List[EvidenceLocation]:
    sanitized = []
    seen = set()
    for u in units:
        try:
            page = int(u.slide_number_or_page_number)
            if 1 <= page <= total_pages:
                key = f"{u.source_id}_{page}"
                if key not in seen:
                    seen.add(key)
                    sanitized.append(EvidenceLocation(
                        source_type=u.source_type,
                        source_id=u.source_id,
                        slide_number_or_page_number=page,
                        excerpt_or_summary=u.excerpt_or_summary
                    ))
            else:
                warnings.append(
                    f"Dropped out-of-bounds page reference: {page} (Max: {total_pages})")
        except (ValueError, TypeError):
            warnings.append(
                f"Dropped invalid page reference format: {u.slide_number_or_page_number}")
    return sanitized


def calculate_evidence_cap(strength: str) -> float:
    mapping = {
        "STRONG_DIRECT": 10.0,
        "DIRECT": 8.0,
        "INDIRECT": 6.0,
        "ABSENT": 4.0
    }
    return mapping.get(strength.upper(), 10.0)


def calculate_contradiction_cap(severity: str) -> tuple:
    if severity == "severe":
        return 5.0, 3.0
    if severity == "moderate":
        return 7.0, 1.5
    if severity == "mild":
        return 9.0, 0.5
    return 10.0, 0.0


class DeterministicScoringService:
    def __init__(self, total_pages: int = 100):
        self.total_pages = total_pages
        self.warnings = []

    def score(
        self,
        classification: ClassificationResult,
        evidence: EvidenceMappingResult,
        raw_judgments: RawCriterionJudgmentResult
    ) -> DeterministicScoringResult:
        logger.info("[Step 4] Running Deterministic Scoring...")

        stage = classification.stage.value.upper() if classification.stage.value else "MVP"

        weights = {
            "Problem_&_Customer_Pain": 20.0,
            "Market_Attractiveness_&_Timing": 15.0,
            "Solution_&_Differentiation": 15.0,
            "Business_Model_&_Go_to_Market": 15.0,
            "Team_&_Execution_Readiness": 20.0,
            "Validation_Traction_Evidence_Quality": 15.0
        }

        if stage in ["GROWTH", "SEED"]:
            weights["Validation_Traction_Evidence_Quality"] = 25.0
            weights["Problem_&_Customer_Pain"] = 10.0

        final_criteria = []
        overall_score = 0.0
        active_weights_sum = 0.0
        confidences = []

        from src.modules.evaluation.domain.scoring_policy import normalize_to_canonical_criterion_name

        canon_ev_map: Dict[str, CriterionEvidence] = {}
        for ev in evidence.criteria_evidence:
            canon = normalize_to_canonical_criterion_name(ev.criterion)
            if canon:
                canon_ev_map[canon] = ev

        canon_raw_map: Dict[str, RawJudgment] = {}
        for raw in raw_judgments.raw_judgments:
            canon = normalize_to_canonical_criterion_name(raw.criterion)
            if canon:
                canon_raw_map[canon] = raw

        canonical_keys = [
            "Problem_&_Customer_Pain",
            "Market_Attractiveness_&_Timing",
            "Solution_&_Differentiation",
            "Business_Model_&_Go_to_Market",
            "Team_&_Execution_Readiness",
            "Validation_Traction_Evidence_Quality"
        ]

        for canon_key in canonical_keys:
            ev = canon_ev_map.get(canon_key)
            raw_j = canon_raw_map.get(canon_key)

            if not raw_j or not ev:
                self.warnings.append(
                    f"Missing evidence or raw judgment for canonical criterion: {canon_key}")

                final_criteria.append(CanonicalCriterionResult(
                    criterion=canon_key,
                    status="not_applicable",
                    confidence="Low",
                    cap_summary=CapSummary(
                        evidence_quality_cap=0.0, contradiction_cap=0.0, contradiction_penalty_points=0.0),
                    evidence_strength_summary="ABSENT",
                    evidence_locations=[],
                    supporting_pages_count=0,
                    explanation=f"Missing evaluation data for {canon_key}."
                ))
                continue

            strength = ev.strongest_evidence_level
            severity = raw_j.suggested_contradiction_severity

            ev_cap = calculate_evidence_cap(strength)
            contra_cap, contra_pen = calculate_contradiction_cap(severity)

            core_cap = raw_j.suggested_core_cap if raw_j.suggested_core_cap is not None else 10.0
            stage_cap = raw_j.suggested_stage_cap if raw_j.suggested_stage_cap is not None else 10.0
            effective_cap = min(core_cap, stage_cap, ev_cap, contra_cap)

            cap_summary = CapSummary(
                core_cap=core_cap,
                stage_cap=stage_cap,
                evidence_quality_cap=ev_cap,
                contradiction_cap=contra_cap,
                contradiction_penalty_points=contra_pen
            )

            valid_locations = sanitize_page_refs(
                ev.evidence_units, self.total_pages, self.warnings)

            if len(valid_locations) == 0 and len(ev.evidence_units) > 0:
                self.warnings.append(
                    f"Critical: All evidence references for {canon_key} were dropped because they were out of bounds.")

            supporting_pages_count = len(valid_locations)

            status = "scored"
            if strength == "ABSENT":
                status = "insufficient_evidence"
            elif severity == "severe":
                status = "contradictory"

            final_c_score = None
            if status == "scored" and raw_j.raw_score is not None:
                math_score = min(raw_j.raw_score, effective_cap) - contra_pen
                final_c_score = max(0.0, math_score) * 10.0

            if final_c_score is None and status == "scored":
                status = "insufficient_evidence"

            cf = {"High": 1.0, "Medium": 0.5, "Low": 0.1}.get(
                raw_j.criterion_confidence, 0.5)
            confidences.append(cf)

            weight = weights.get(canon_key, 16.66) / 100.0
            weighted_contrib = None

            if final_c_score is not None:
                weighted_contrib = final_c_score * weight
                overall_score += weighted_contrib
                active_weights_sum += weight

            final_criteria.append(CanonicalCriterionResult(
                criterion=canon_key,
                status=status,
                raw_score=(raw_j.raw_score *
                           10.0) if raw_j.raw_score is not None else None,
                final_score=final_c_score,
                weighted_contribution=weighted_contrib,
                confidence=raw_j.criterion_confidence,
                cap_summary=cap_summary,
                evidence_strength_summary=strength,
                evidence_locations=valid_locations,
                supporting_pages_count=supporting_pages_count,
                strengths=[
                    u.excerpt_or_summary for u in ev.evidence_units[:2]],
                concerns=[
                    u.excerpt_or_summary for u in ev.weakening_evidence_units[:2]] + ev.gaps[:2],
                explanation=raw_j.reasoning
            ))

        if active_weights_sum > 0:
            overall_score = overall_score / active_weights_sum
        else:
            overall_score = None
            self.warnings.append(
                "No active criterion weights could be evaluated. Overall score is omitted.")

        band = "weak"
        if overall_score:
            if overall_score >= 85:
                band = "very strong"
            elif overall_score >= 70:
                band = "strong"
            elif overall_score >= 50:
                band = "promising but incomplete"
            elif overall_score >= 35:
                band = "below average"

        overall_conf = "Medium"
        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        if avg_conf > 0.75:
            overall_conf = "High"
        elif avg_conf < 0.3:
            overall_conf = "Low"

        # Temporary object passing back before wrapping in CanonicalEvaluationResult
        return DeterministicScoringResult(**{
            'effective_weights': weights,
            'criteria_results': final_criteria,
            'overall_result': OverallResult(
                overall_score=overall_score,
                overall_confidence=overall_conf,
                evidence_coverage="strong" if avg_conf > 0.6 else (
                    "weak" if avg_conf < 0.3 else "moderate"),
                interpretation_band=band,
                stage_context_note=f"Evaluated against standard benchmarks for {stage} stage."
            ),
            'processing_warnings': self.warnings
        })
