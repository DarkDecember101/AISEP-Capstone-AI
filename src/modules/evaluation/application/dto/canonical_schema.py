from typing import List, Optional, Literal, Dict
from pydantic import BaseModel, Field

ConfidenceLevel = Literal["High", "Medium", "Low"]
EvidenceStrength = Literal["STRONG_DIRECT", "DIRECT", "INDIRECT", "ABSENT"]
CriterionStatus = Literal["scored", "insufficient_evidence",
                          "contradictory", "not_applicable"]
ContradictionSeverity = Literal["none", "mild", "moderate", "severe"]

CriterionName = Literal[
    "Problem_&_Customer_Pain",
    "Market_Attractiveness_&_Timing",
    "Solution_&_Differentiation",
    "Business_Model_&_Go_to_Market",
    "Team_&_Execution_Readiness",
    "Validation_Traction_Evidence_Quality",
]

SourceType = Literal["Pitch Deck", "Business Plan"]


class EvidenceLocation(BaseModel):
    source_type: SourceType
    source_id: str
    slide_number_or_page_number: int = Field(ge=1)
    excerpt_or_summary: str
    section_name: Optional[str] = None


class ClassificationItem(BaseModel):
    value: Optional[str]
    confidence: ConfidenceLevel
    resolution_source: Literal["provided", "inferred"]
    supporting_evidence_locations: List[EvidenceLocation] = []


class ClassificationResult(BaseModel):
    stage: ClassificationItem
    main_industry: ClassificationItem
    subindustry: ClassificationItem
    operational_notes: List[str] = []


class CriterionEvidenceMap(BaseModel):
    criterion: CriterionName
    strongest_evidence_level: EvidenceStrength
    evidence_units: List[EvidenceLocation] = []
    weakening_evidence_units: List[EvidenceLocation] = []
    possible_contradictions: List[str] = []
    gaps: List[str] = []


class RawCriterionJudgment(BaseModel):
    criterion: CriterionName
    raw_score: float = Field(ge=0, le=10)
    criterion_confidence: ConfidenceLevel
    suggested_core_cap: Optional[float] = None
    suggested_stage_cap: Optional[float] = None
    suggested_contradiction_severity: ContradictionSeverity
    reasoning: str


class CapSummary(BaseModel):
    core_cap: Optional[float] = None
    stage_cap: Optional[float] = None
    evidence_quality_cap: float
    contradiction_cap: float
    contradiction_penalty_points: float


class CanonicalCriterionResult(BaseModel):
    criterion: CriterionName
    status: CriterionStatus
    raw_score: Optional[float] = None
    final_score: Optional[float] = None
    weighted_contribution: Optional[float] = None
    confidence: ConfidenceLevel
    cap_summary: CapSummary
    evidence_strength_summary: EvidenceStrength
    evidence_locations: List[EvidenceLocation] = []
    supporting_pages_count: int = 0
    strengths: List[str] = []
    concerns: List[str] = []
    explanation: str


class CanonicalOverallResult(BaseModel):
    overall_score: Optional[float] = None
    overall_confidence: ConfidenceLevel
    evidence_coverage: Literal["strong", "moderate", "weak"]
    interpretation_band: Literal["weak", "below average",
                                 "promising but incomplete", "strong", "very strong"]
    stage_context_note: str


class Recommendation(BaseModel):
    category: Literal[
        "EVIDENCE_GAP",
        "STRATEGIC_CLARITY",
        "VALIDATION_PRIORITY",
        "DOCUMENT_IMPROVEMENT",
        "RISK_MITIGATION",
    ]
    priority: int = Field(ge=1, le=5)
    recommendation: str
    rationale: str
    expected_impact: CriterionName


class KeyQuestion(BaseModel):
    criterion: CriterionName
    question: str


class CanonicalNarrative(BaseModel):
    executive_summary: str
    top_strengths: List[str] = []
    top_concerns: List[str] = []
    missing_information: List[str] = []
    overall_explanation: str
    recommendations: List[Recommendation] = []
    key_questions: List[KeyQuestion] = []
    operational_notes: List[str] = []


class DeterministicScoringResult(BaseModel):
    effective_weights: Dict[str, float]
    criteria_results: List[CanonicalCriterionResult]
    overall_result: CanonicalOverallResult
    processing_warnings: List[str] = Field(default_factory=list)


class CanonicalEvaluationResult(BaseModel):
    startup_id: str
    document_type: Optional[str] = None
    status: Literal["queued", "processing",
                    "partial_completed", "completed", "failed"]
    classification: ClassificationResult
    effective_weights: dict
    criteria_results: List[CanonicalCriterionResult]
    overall_result: CanonicalOverallResult
    narrative: CanonicalNarrative
    processing_warnings: List[str] = []
