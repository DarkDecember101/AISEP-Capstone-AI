# This file is a backup reference copy.
# The canonical implementation is deterministic_scorer.py.
# DO NOT import DeterministicScoringService from this file.
# This copy has been updated to use the same STAGE_WEIGHT_PROFILES table
# so it can never silently diverge from the approved stage weight profiles.
import math
from typing import Dict, Any, List
from src.shared.logging.logger import setup_logger
from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationResult,
    EvidenceMappingResult,
    CriterionEvidence,
    RawCriterionJudgmentResult,
    RawJudgment,
    EvidenceUnit
)
# Authoritative result types come from canonical_schema, NOT pipeline_schema.
from src.modules.evaluation.application.dto.canonical_schema import (
    DeterministicScoringResult,
    CanonicalCriterionResult as FinalCriterionResult,
    CapSummary,
    CanonicalOverallResult as OverallResult,
)
from src.modules.evaluation.application.services.deterministic_scorer import STAGE_WEIGHT_PROFILES

logger = setup_logger("deterministic_scorer_backup")


def sanitize_page_refs(units: List[EvidenceUnit], total_pages: int, warnings: List[str]) -> List[str]:
    sanitized = []
    for u in units:
        try:
            page = int(u.slide_number_or_page_number)
            if 0 <= page <= total_pages:
                sanitized.append(f"page_{page}")
            else:
                warnings.append(
                    f"Dropped out-of-bounds page reference: {page} (Max: {total_pages})")
                continue
        except (ValueError, TypeError):
            warnings.append(
                f"Dropped invalid page reference format: {u.slide_number_or_page_number}")
            continue
    return list(set(sanitized))


def calculate_evidence_cap(strength: str) -> float:
    mapping = {
        "STRONG_DIRECT": 10.0,
        "DIRECT": 8.0,
        "INDIRECT": 6.0,
        "ABSENT": 4.0
    }
    return mapping.get(strength.upper(), 10.0)


def calculate_contradiction_cap(severity: str) -> (float, float):
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

        stage = (classification.stage.value or "MVP").upper().strip()
        weights = STAGE_WEIGHT_PROFILES.get(
            stage, STAGE_WEIGHT_PROFILES["MVP"]).copy()

        final_criteria = []
        overall_score = 0.0
        active_weights_sum = 0.0
        confidences = []

        # Convert evidence list to dict for merging
        from src.modules.evaluation.domain.scoring_policy import normalize_criterion_code
        ev_map: Dict[str, CriterionEvidence] = {(normalize_criterion_code(
            c.criterion) or c.criterion): c for c in evidence.criteria_evidence}

        for raw_j in raw_judgments.raw_judgments:
            criterion = raw_j.criterion
            norm_crit = normalize_criterion_code(criterion) or criterion
            ev = ev_map.get(norm_crit)
            if not ev:
                self.warnings.append(
                    f"No evidence mapping found for criterion: {criterion}")
                continue

            # Merge processing
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

            # Determine final score and status
            valid_pages = sanitize_page_refs(
                ev.evidence_units, self.total_pages, self.warnings)

            status = "scored"
            final_c_score = None
            if strength == "ABSENT":
                status = "insufficient_evidence"
            elif severity == "severe":
                status = "contradictory"

            if status == "scored" and raw_j.raw_score is not None:
                # Apply cap and penalty
                math_score = min(raw_j.raw_score, effective_cap) - contra_pen
                final_c_score = max(0.0, math_score)
                # Convert 10-scale to 100-scale
                final_c_score *= 10.0
            else:
                final_c_score = None

            # Confidence mapping (High=1.0, Medium=0.5, Low=0.1)
            cf = {"High": 1.0, "Medium": 0.5, "Low": 0.1}.get(
                raw_j.criterion_confidence, 0.5)
            confidences.append(cf)

            weight = weights.get(criterion, 16.66) / 100.0
            weighted_contrib = None

            if final_c_score is not None:
                weighted_contrib = final_c_score * weight
                overall_score += weighted_contrib
                active_weights_sum += weight

            final_criteria.append(FinalCriterionResult(
                criterion=criterion,
                status=status,
                raw_score=(raw_j.raw_score *
                           10.0) if raw_j.raw_score is not None else None,
                final_score=final_c_score,
                weighted_contribution=weighted_contrib,
                confidence=raw_j.criterion_confidence,
                cap_summary=cap_summary,
                evidence_strength_summary=strength,
                evidence_locations=valid_pages,
                strengths=[
                    u.excerpt_or_summary for u in ev.evidence_units[:2]],
                concerns=[
                    u.excerpt_or_summary for u in ev.weakening_evidence_units[:2]] + ev.gaps[:2],
                explanation=raw_j.reasoning
            ))

        # Overall Score normalization
        if active_weights_sum > 0:
            overall_score = overall_score / active_weights_sum
        else:
            overall_score = None
            self.warnings.append(
                "No active criterion weights could be evaluated. Overall score is completely omitted.")

        # Band
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

        return DeterministicScoringResult(
            effective_weights=weights,
            criteria_results=final_criteria,
            overall_result=OverallResult(
                overall_score=overall_score,
                overall_confidence=overall_conf,
                evidence_coverage="strong" if avg_conf > 0.6 else (
                    "weak" if avg_conf < 0.3 else "moderate"),
                interpretation_band=band,
                stage_context_note=f"Evaluated against standard benchmarks for {stage} stage."
            ),
            processing_warnings=self.warnings
        )
