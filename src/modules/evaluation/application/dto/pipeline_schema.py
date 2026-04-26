from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Literal, Dict, Any

# STEP 1: Classification

_NULL_STRINGS: frozenset = frozenset({"null", "none", "n/a", "na", ""})


class ClassificationContextInput(BaseModel):
    """Optional user-provided classification hints passed through the API."""
    provided_stage: Optional[str] = None
    provided_main_industry: Optional[str] = None
    provided_subindustry: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_null_strings(cls, data: Any) -> Any:
        """Coerce string sentinels like 'null', 'None', '' to actual None."""
        if isinstance(data, dict):
            for field_name in ("provided_stage", "provided_main_industry", "provided_subindustry"):
                val = data.get(field_name)
                if isinstance(val, str) and val.strip().lower() in _NULL_STRINGS:
                    data[field_name] = None
        return data

    def to_prompt_block(self) -> str:
        """Format as a text block to inject into the classification prompt."""
        parts: list[str] = []
        if self.provided_stage:
            parts.append(f"Provided stage: {self.provided_stage}")
        if self.provided_main_industry:
            parts.append(
                f"Provided main_industry: {self.provided_main_industry}")
        if self.provided_subindustry:
            parts.append(
                f"Provided subindustry (hint only): {self.provided_subindustry}")
        if not parts:
            return "No classification context was provided. Infer all fields from the document."
        return (
            "The evaluator provided the following classification context. "
            "Use these as initial values but VERIFY against document evidence. "
            "Override if document evidence strongly contradicts the provided value. "
            "Record any override or conflict in operational_notes.\n"
            + "\n".join(f"- {p}" for p in parts)
        )


class ClassificationField(BaseModel):
    value: Optional[str]
    confidence: Literal["High", "Medium", "Low"]
    resolution_source: Literal["provided", "inferred"]
    supporting_evidence_locations: List[str]


class ClassificationResult(BaseModel):
    stage: ClassificationField
    main_industry: ClassificationField
    subindustry: Optional[ClassificationField] = None
    operational_notes: List[str] = Field(default_factory=list)

# STEP 2: Evidence Mapping


class EvidenceUnit(BaseModel):
    source_type: Literal["Pitch Deck", "Business Plan"]
    source_id: str
    slide_number_or_page_number: int
    excerpt_or_summary: str


class CriterionEvidence(BaseModel):
    criterion: str
    strongest_evidence_level: Literal["STRONG_DIRECT",
                                      "DIRECT", "INDIRECT", "ABSENT"]
    evidence_units: List[EvidenceUnit]
    weakening_evidence_units: List[EvidenceUnit] = Field(default_factory=list)
    possible_contradictions: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)


class EvidenceMappingResult(BaseModel):
    criteria_evidence: List[CriterionEvidence]
    cross_document_contradictions: List[str] = Field(default_factory=list)
    coverage_notes: List[str] = Field(default_factory=list)

# STEP 3: Raw Criterion Judgment


class RawJudgment(BaseModel):
    criterion: str
    raw_score: Optional[float] = Field(ge=0, le=10)
    criterion_confidence: Literal["High", "Medium", "Low"]
    suggested_core_cap: Optional[float] = None
    suggested_stage_cap: Optional[float] = None
    suggested_contradiction_severity: Literal["none",
                                              "mild", "moderate", "severe"]
    reasoning: str


class RawCriterionJudgmentResult(BaseModel):
    raw_judgments: List[RawJudgment]


# FINAL DETERMINISTIC SCORING SCHEMAS (Python Step)
# NOTE: The authoritative result DTOs for the scorer live in canonical_schema.py.
# DeterministicScoringResult, CanonicalCriterionResult, CanonicalOverallResult,
# CapSummary, and EvidenceLocation are imported from there.
# They are NOT defined here to prevent shadowing / type confusion.
#
# Re-export the canonical scorer result type so callers can import from one place:
from src.modules.evaluation.application.dto.canonical_schema import (  # noqa: F401
    DeterministicScoringResult,
)

# STEP 5: Report Writing


class OverallResultNarrative(BaseModel):
    top_strengths: List[str]
    top_concerns: List[str]
    overall_explanation: str


class Recommendation(BaseModel):
    category: Literal[
        "EVIDENCE_GAP", "STRATEGIC_CLARITY", "VALIDATION_PRIORITY",
        "DOCUMENT_IMPROVEMENT", "RISK_MITIGATION", "FINANCIAL_IMPROVEMENT"
    ]
    priority: int = Field(ge=1, le=5)
    recommendation: str
    rationale: str
    expected_impact: str


class KeyQuestion(BaseModel):
    criterion: str
    question: str


class TopRiskItem(BaseModel):
    risk_type: str
    severity: Literal["High", "Medium", "Low"]
    description: str
    related_criterion: str


class ReportWriterResult(BaseModel):
    overall_result_narrative: OverallResultNarrative
    recommendations: List[Recommendation] = Field(default_factory=list)
    key_questions: List[KeyQuestion] = Field(default_factory=list)
    top_risks: List[TopRiskItem] = Field(default_factory=list)
    operational_notes: List[str] = Field(default_factory=list)


class EvidenceExcerptLocalizationResult(BaseModel):
    excerpts: List[str] = Field(default_factory=list)
