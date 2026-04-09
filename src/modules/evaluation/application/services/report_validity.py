from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReportValidity:
    is_valid: bool
    reason: str


def validate_canonical_report(canonical: Mapping[str, Any] | None) -> ReportValidity:
    """
    Canonical report validity gate.

    A report is considered valid only when:
    1) startup_id is non-empty
    2) at least one criterion has a numeric final_score OR overall_score is numeric

    This prevents classification/narrative-only output from masquerading as a
    successful scored evaluation report.
    """
    if not canonical or not isinstance(canonical, Mapping):
        return ReportValidity(False, "Canonical report payload is missing.")

    startup_id = str(canonical.get("startup_id") or "").strip()
    if not startup_id:
        return ReportValidity(False, "startup_id is missing in canonical report.")

    overall_result = canonical.get("overall_result") or {}
    overall_score = overall_result.get("overall_score")
    if isinstance(overall_score, (int, float)):
        return ReportValidity(True, "ok")

    criteria_results = canonical.get("criteria_results") or []
    for criterion in criteria_results:
        if not isinstance(criterion, Mapping):
            continue
        score = criterion.get("final_score")
        if isinstance(score, (int, float)):
            return ReportValidity(True, "ok")

    return ReportValidity(
        False,
        "No usable scoring data: overall_score is null and all criterion final scores are null.",
    )
