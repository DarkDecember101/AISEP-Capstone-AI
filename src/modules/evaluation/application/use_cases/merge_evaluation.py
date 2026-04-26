"""
Merge two per-document CanonicalEvaluationResults into a single combined result.

Used when an evaluation run contains both a Pitch Deck and a Business Plan.
Merging operates at the criterion `final_score` level (NOT raw_score).

Criterion merge algorithm (per criterion, when both PD and BP have it):
  1. Compare evidence_strength_summary for both.
  2. If both are STRONG_DIRECT -> average final_score, merge evidence.
  3. If both are DIRECT -> average final_score, merge evidence.
  4. If strengths differ -> choose the source with stronger evidence;
     tie-break on final_score if strengths are equal but different tier.
     Strength rank: STRONG_DIRECT > DIRECT > INDIRECT > ABSENT.
  5. If only one source has it -> use that source unchanged.
"""

from typing import Dict, List

from src.modules.evaluation.application.dto.canonical_schema import (
    CanonicalEvaluationResult,
)
from src.modules.evaluation.application.services.report_validity import (
    sanitize_canonical_report,
)
from src.modules.evaluation.application.services.processing_warning_sanitizer import (
    sanitize_processing_warnings,
)
from src.shared.logging.logger import setup_logger

logger = setup_logger("merge_evaluation")

_STRENGTH_RANK: Dict[str, int] = {
    "STRONG_DIRECT": 4,
    "DIRECT": 3,
    "INDIRECT": 2,
    "ABSENT": 1,
}
_AVERAGE_TIERS = {"STRONG_DIRECT", "DIRECT"}


def _merge_single_criterion(pd_c: Dict, bp_c: Dict) -> Dict:
    """Merge two criterion dicts using the evidence-strength-first algorithm."""
    pd_str = pd_c.get("evidence_strength_summary", "ABSENT")
    bp_str = bp_c.get("evidence_strength_summary", "ABSENT")
    pd_rank = _STRENGTH_RANK.get(pd_str, 1)
    bp_rank = _STRENGTH_RANK.get(bp_str, 1)

    combined_locs = (
        pd_c.get("evidence_locations", []) + bp_c.get("evidence_locations", [])
    )

    if pd_str in _AVERAGE_TIERS and bp_str in _AVERAGE_TIERS and pd_str == bp_str:
        avg_score = round(
            ((pd_c.get("final_score") or 0) + (bp_c.get("final_score") or 0)) / 2, 2
        )
        stronger = pd_str if pd_rank >= bp_rank else bp_str
        winner = {**pd_c}
        winner["final_score"] = avg_score
        winner["evidence_strength_summary"] = stronger
        winner["evidence_locations"] = combined_locs
        winner["explanation"] = (
            f"[Đã gộp - lấy trung bình Pitch Deck và Business Plan, cùng mức {pd_str}/{bp_str}] "
            f"Pitch Deck={pd_c.get('final_score')}, Business Plan={bp_c.get('final_score')}"
        )
        return winner

    if pd_rank > bp_rank:
        winner = {**pd_c}
        winner["evidence_locations"] = combined_locs
        winner["explanation"] = (
            f"[Đã gộp - ưu tiên Pitch Deck, bằng chứng mạnh hơn {pd_str}>{bp_str}] "
            f"{pd_c.get('explanation', '')}"
        )
        return winner

    if bp_rank > pd_rank:
        winner = {**bp_c}
        winner["evidence_locations"] = combined_locs
        winner["explanation"] = (
            f"[Đã gộp - ưu tiên Business Plan, bằng chứng mạnh hơn {bp_str}>{pd_str}] "
            f"{bp_c.get('explanation', '')}"
        )
        return winner

    if (bp_c.get("final_score") or 0) > (pd_c.get("final_score") or 0):
        winner = {**bp_c}
        winner["evidence_locations"] = combined_locs
        winner["explanation"] = (
            f"[Đã gộp - ưu tiên Business Plan, điểm cao hơn khi cùng mức bằng chứng {pd_str}] "
            f"{bp_c.get('explanation', '')}"
        )
        return winner

    winner = {**pd_c}
    winner["evidence_locations"] = combined_locs
    winner["explanation"] = (
        f"[Đã gộp - ưu tiên Pitch Deck, điểm cao hơn khi cùng mức bằng chứng {pd_str}] "
        f"{pd_c.get('explanation', '')}"
    )
    return winner


def merge_canonical_results(
    pd_result: CanonicalEvaluationResult,
    bp_result: CanonicalEvaluationResult,
) -> CanonicalEvaluationResult:
    """
    Merge a Pitch Deck result and a Business Plan result into one combined result.

    Strategy:
    - Classification: prefer PD values; record conflicts in operational_notes;
      merge evidence_locations from both sources on conflict.
    - Criteria: evidence-strength-first algorithm.
    - Overall: recompute weighted average from merged criteria final_scores.
    - Overall confidence: conservative (lower of PD/BP).
    - Narrative: union of strengths/concerns; prefer PD executive_summary.
    """
    pd = pd_result.model_dump()
    bp = bp_result.model_dump()

    operational_notes: List[str] = [
        "KẾT_QUẢ_GỘP: Đã hợp nhất kết quả đánh giá từ Pitch Deck và Business Plan."
    ]

    merged_classification = {k: v for k, v in pd.get("classification", {}).items()}
    bp_classification = bp.get("classification", {})

    for field in ["stage", "main_industry", "subindustry"]:
        pd_val = merged_classification.get(field, {}).get("value")
        bp_val = bp_classification.get(field, {}).get("value")
        if pd_val and bp_val and pd_val != bp_val:
            operational_notes.append(
                f"XUNG_ĐỘT_PHÂN_LOẠI: {field} - Pitch Deck={pd_val}, Business Plan={bp_val}. Sử dụng giá trị từ Pitch Deck."
            )
            pd_locs = merged_classification.get(field, {}).get(
                "supporting_evidence_locations", []
            )
            bp_locs = bp_classification.get(field, {}).get(
                "supporting_evidence_locations", []
            )
            merged_classification[field] = {
                **merged_classification[field],
                "supporting_evidence_locations": pd_locs + bp_locs,
            }
        elif not pd_val and bp_val:
            merged_classification[field] = bp_classification[field]

    pd_criteria = {c["criterion"]: c for c in pd.get("criteria_results", [])}
    bp_criteria = {c["criterion"]: c for c in bp.get("criteria_results", [])}
    all_criteria_names = set(pd_criteria.keys()) | set(bp_criteria.keys())

    merged_criteria = []
    for name in sorted(all_criteria_names):
        pd_c = pd_criteria.get(name)
        bp_c = bp_criteria.get(name)
        if pd_c and bp_c:
            merged_criteria.append(_merge_single_criterion(pd_c, bp_c))
        elif pd_c:
            merged_criteria.append(pd_c)
        else:
            merged_criteria.append(bp_c)

    effective_weights = pd.get("effective_weights") or bp.get("effective_weights") or {}

    total_weighted = 0.0
    total_weight = 0.0
    for criterion in merged_criteria:
        weight = effective_weights.get(criterion["criterion"], 0)
        total_weighted += (criterion.get("final_score") or 0) * weight
        total_weight += weight

    merged_overall_score = round(total_weighted / total_weight, 2) if total_weight > 0 else 0.0

    conf_rank = {"High": 3, "Medium": 2, "Low": 1}
    pd_conf = pd.get("overall_result", {}).get("overall_confidence", "Medium")
    bp_conf = bp.get("overall_result", {}).get("overall_confidence", "Medium")
    merged_conf = pd_conf if conf_rank.get(pd_conf, 2) <= conf_rank.get(bp_conf, 2) else bp_conf

    merged_overall = {
        "overall_score": merged_overall_score,
        "overall_confidence": merged_conf,
        "evidence_coverage": pd.get("overall_result", {}).get("evidence_coverage", "moderate"),
        "interpretation_band": pd.get("overall_result", {}).get("interpretation_band", "promising but incomplete"),
        "stage_context_note": pd.get(
            "overall_result", {}
        ).get("stage_context_note", "Đã hợp nhất từ Pitch Deck và Business Plan."),
    }

    pd_narr = pd.get("narrative", {})
    bp_narr = bp.get("narrative", {})

    def _unique_list(a: list, b: list) -> list:
        seen = set()
        result = []
        for item in a + b:
            text = str(item)
            if text not in seen:
                seen.add(text)
                result.append(item)
        return result

    merged_narrative = {
        "executive_summary": pd_narr.get("executive_summary", "") or bp_narr.get("executive_summary", ""),
        "top_strengths": _unique_list(pd_narr.get("top_strengths", []), bp_narr.get("top_strengths", [])),
        "top_concerns": _unique_list(pd_narr.get("top_concerns", []), bp_narr.get("top_concerns", [])),
        "top_risks": _unique_list(pd_narr.get("top_risks", []), bp_narr.get("top_risks", [])),
        "missing_information": _unique_list(pd_narr.get("missing_information", []), bp_narr.get("missing_information", [])),
        "overall_explanation": pd_narr.get("overall_explanation", "") or bp_narr.get("overall_explanation", ""),
        "recommendations": _unique_list(pd_narr.get("recommendations", []), bp_narr.get("recommendations", [])),
        "key_questions": _unique_list(pd_narr.get("key_questions", []), bp_narr.get("key_questions", [])),
        "operational_notes": operational_notes + pd_narr.get("operational_notes", []) + bp_narr.get("operational_notes", []),
    }

    merged_warnings = sanitize_processing_warnings(_unique_list(
        pd.get("processing_warnings", []),
        bp.get("processing_warnings", []),
    ))

    merged_dict = {
        "startup_id": pd.get("startup_id") or bp.get("startup_id", ""),
        "document_type": "merged",
        "status": "completed",
        "classification": merged_classification,
        "effective_weights": effective_weights,
        "criteria_results": merged_criteria,
        "overall_result": merged_overall,
        "narrative": merged_narrative,
        "processing_warnings": merged_warnings,
    }
    merged_dict = sanitize_canonical_report(merged_dict)

    return CanonicalEvaluationResult(**merged_dict)
