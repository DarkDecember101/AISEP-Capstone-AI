from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from pydantic import field_validator


class DocumentInputSchema(BaseModel):
    document_id: str
    document_type: str = Field(..., description="pitch_deck or business_plan")
    file_url_or_path: str


class SubmitEvaluationRequest(BaseModel):
    startup_id: str
    documents: List[DocumentInputSchema]

    @field_validator("startup_id")
    @classmethod
    def startup_id_not_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("startup_id must not be blank")
        return cleaned


class SubmitEvaluationResponse(BaseModel):
    evaluation_run_id: int
    status: str
    message: str


class CriterionResultSchema(BaseModel):
    criterion_code: str
    status: str = Field(
        default="scored", description="scored / insufficient_evidence / not_applicable")
    score: Optional[float] = Field(..., ge=0, le=100,
                                   description="Score from 0 to 100. Null if insufficient evidence")
    confidence: float = Field(..., ge=0, le=1.0,
                              description="Confidence of the evaluation based on evidence (0 to 1)")
    reason: str = Field(..., description="Explanation for the score")
    evidence_refs: List[str] = Field(
        default_factory=list, description="List of page references, e.g. page_1, page_3")
    supporting_pages_count: int = Field(
        default=0, description="Number of supporting pages")


class DocumentEvaluationResult(BaseModel):
    status: str
    extraction_quality: Dict[str, Any] = Field(default_factory=dict)
    section_coverage: Dict[str, Any] = Field(default_factory=dict)
    criteria_details: List[CriterionResultSchema] = Field(default_factory=list)
    strengths: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    summary: str


class LLMDutchEvaluationResult(BaseModel):
    """Structured output expected from LLM per chunk/document"""
    criteria_results: List[CriterionResultSchema]
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    summary: str = Field(...,
                         description="Short summary of this section or document")


class AggregatedReportSchema(BaseModel):
    startup_id: str
    status: str = Field(
        default="completed", description="completed / partial_completed / failed_quality_gate")
    overall_score: Optional[float]
    overall_confidence: float
    dimension_scores: Dict[str, float]
    executive_summary: str
    top_strengths: List[str]
    top_risks: List[str]
    missing_information: List[str]
    criteria_details: List[CriterionResultSchema]
    processing_warnings: List[str] = Field(default_factory=list)
