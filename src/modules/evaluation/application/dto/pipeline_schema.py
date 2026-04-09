from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Literal, Dict, Any

# STEP 1: Classification


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


class CapSummary(BaseModel):
    core_cap: Optional[float] = None
    stage_cap: Optional[float] = None
    evidence_quality_cap: Optional[float] = None
    contradiction_cap: Optional[float] = None
    contradiction_penalty_points: float = 0.0


class FinalCriterionResult(BaseModel):
    criterion: str
    status: Literal["scored", "insufficient_evidence",
                    "contradictory", "not_applicable"]
    raw_score: Optional[float] = None
    final_score: Optional[float] = None
    weighted_contribution: Optional[float] = None
    confidence: Literal["High", "Medium", "Low"]
    cap_summary: CapSummary
    evidence_strength_summary: str
    evidence_locations: List[str]
    strengths: List[str] = Field(default_factory=list)
    concerns: List[str] = Field(default_factory=list)
    explanation: str


class OverallResult(BaseModel):
    overall_score: Optional[float] = None
    overall_confidence: Literal["High", "Medium", "Low"]
    evidence_coverage: Literal["strong", "moderate", "weak"]
    interpretation_band: Literal["weak", "below average",
                                 "promising but incomplete", "strong", "very strong"]
    stage_context_note: str


class DeterministicScoringResult(BaseModel):
    effective_weights: Dict[str, float]
    criteria_results: List[FinalCriterionResult]
    overall_result: OverallResult
    processing_warnings: List[str] = Field(default_factory=list)

# STEP 5: Report Writing


class OverallResultNarrative(BaseModel):
    top_strengths: List[str]
    top_concerns: List[str]
    overall_explanation: str


class Recommendation(BaseModel):
    category: Literal["EVIDENCE_GAP", "STRATEGIC_CLARITY",
                      "VALIDATION_PRIORITY", "DOCUMENT_IMPROVEMENT", "RISK_MITIGATION"]
    priority: int = Field(ge=1, le=5)
    recommendation: str
    rationale: str
    expected_impact: str


class KeyQuestion(BaseModel):
    criterion: str
    question: str


class ReportWriterResult(BaseModel):
    overall_result_narrative: OverallResultNarrative
    recommendations: List[Recommendation] = Field(default_factory=list)
    key_questions: List[KeyQuestion] = Field(default_factory=list)
    operational_notes: List[str] = Field(default_factory=list)
