from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any, Literal  # noqa: F811
from pydantic import field_validator

_NULL_LIKE: frozenset = frozenset(
    {"null", "none", "n/a", "na", "unknown", "undefined", ""})
_ALLOWED_DOC_TYPES: frozenset = frozenset({"pitch_deck", "business_plan"})

# Normalize PascalCase / camelCase sent by .NET → snake_case internal value
_DOC_TYPE_NORMALISE: dict[str, str] = {
    "pitchdeck": "pitch_deck",
    "pitch_deck": "pitch_deck",
    "pitchdeck_": "pitch_deck",
    "businessplan": "business_plan",
    "business_plan": "business_plan",
}


class DocumentInputSchema(BaseModel):
    document_id: str
    document_type: str = Field(..., description="pitch_deck or business_plan")
    file_url_or_path: str

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, v: str) -> str:
        # Strip spaces/dashes/underscores to produce a compact lowercase key
        compact = (v or "").strip().lower().replace("-", "").replace("_", "")
        # Re-insert underscore via normalisation map so PitchDeck → pitch_deck
        normalised = _DOC_TYPE_NORMALISE.get(compact) or _DOC_TYPE_NORMALISE.get(
            (v or "").strip().lower()
        )
        # Return normalised value if known; otherwise keep original (will be
        # filtered out by model_validator rather than rejecting the whole request).
        return normalised if normalised is not None else (v or "").strip().lower()

    @property
    def is_processable(self) -> bool:
        """True if this document has a type Python can evaluate."""
        return self.document_type in _ALLOWED_DOC_TYPES


class SubmitEvaluationRequest(BaseModel):
    startup_id: str
    documents: List[DocumentInputSchema]
    provided_stage: Optional[str] = None
    provided_main_industry: Optional[str] = None
    provided_subindustry: Optional[str] = None

    @field_validator("startup_id")
    @classmethod
    def startup_id_not_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("startup_id must not be blank")
        return cleaned

    @model_validator(mode="after")
    def validate_documents_and_normalize(self):
        # --- normalize null-like subindustry ---
        if self.provided_subindustry is not None:
            if str(self.provided_subindustry).strip().lower() in _NULL_LIKE:
                object.__setattr__(self, "provided_subindustry", None)

        # --- filter out documents with unknown/unsupported document_type ---
        # .NET may send documents of type "unknown" or future types Python
        # cannot evaluate. Silently drop them rather than rejecting the whole
        # request, so a mixed payload (PitchDeck + unknown) still succeeds.
        known_docs = [d for d in self.documents if d.document_type in _ALLOWED_DOC_TYPES]
        unknown_docs = [d for d in self.documents if d.document_type not in _ALLOWED_DOC_TYPES]
        if unknown_docs:
            import logging
            _log = logging.getLogger("aisep.evaluation")
            _log.warning(
                "submit: ignoring %d document(s) with unsupported document_type(s): %s",
                len(unknown_docs),
                [d.document_type for d in unknown_docs],
            )
        object.__setattr__(self, "documents", known_docs)

        # --- documents validation ---
        if not self.documents:
            raise ValueError(
                "No processable documents in request. "
                "document_type must be one of: pitch_deck, business_plan "
                "(PascalCase PitchDeck / BusinessPlan also accepted)."
            )

        type_counts: dict[str, int] = {}
        for doc in self.documents:
            type_counts[doc.document_type] = type_counts.get(
                doc.document_type, 0) + 1

        if type_counts.get("pitch_deck", 0) > 1:
            raise ValueError("Only 1 pitch_deck allowed per evaluation run")
        if type_counts.get("business_plan", 0) > 1:
            raise ValueError("Only 1 business_plan allowed per evaluation run")

        return self

    @property
    def derived_evaluation_mode(self) -> str:
        types = {d.document_type for d in self.documents}
        if types == {"pitch_deck", "business_plan"}:
            return "combined"
        if "pitch_deck" in types:
            return "pitch_deck_only"
        return "business_plan_only"


# --- Submit response ---

class DocumentStatusSchema(BaseModel):
    document_id: str
    document_type: str
    status: str


class SubmitEvaluationResponse(BaseModel):
    evaluation_run_id: int
    startup_id: str
    status: str
    message: str = "Evaluation submitted successfully"
    evaluation_mode: str
    documents: List[DocumentStatusSchema]


# --- Status response ---

class EvaluationStatusResponse(BaseModel):
    id: int  # .NET polls on this field name
    evaluation_run_id: int  # kept for backward compat
    startup_id: str
    status: str
    submitted_at: Optional[Any] = None
    failure_reason: Optional[str] = None
    overall_score: Optional[float] = None
    overall_confidence: Optional[float] = None
    evaluation_mode: Optional[str] = None
    documents: List[Dict[str, Any]] = []
    has_pitch_deck_result: bool = False
    has_business_plan_result: bool = False
    has_merged_result: bool = False
    # Merge lifecycle signal:
    # not_applicable | waiting_for_sources | fallback_source_only |
    # merged | merge_failed | merge_disabled | null (legacy/unknown)
    merge_status: Optional[str] = None


# --- Report envelope ---

class ReportEnvelope(BaseModel):
    """Wraps a canonical report with metadata about how it was produced."""
    report_mode: str  # pitch_deck_only | business_plan_only | merged | source
    evaluation_mode: str  # pitch_deck_only | business_plan_only | combined
    has_merged_result: bool
    available_sources: List[str]
    source_document_type: Optional[str] = None  # set when report_mode=source
    # Merge lifecycle signal — mirrors EvaluationRun.merge_status
    merge_status: Optional[str] = None
    report: Dict[str, Any]  # the actual CanonicalEvaluationResult dict


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
