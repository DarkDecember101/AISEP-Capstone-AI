from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any, TypeVar, Type
import operator
from typing_extensions import Annotated
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SearchResult(BaseModel):
    query: str
    title: str
    url: str
    snippet: str
    source_domain: str
    published_date: Optional[str] = None
    score: float = 0.0


class SelectedSource(BaseModel):
    url: str
    title: str
    source_domain: str
    published_date: Optional[str] = None
    selection_reason: str
    trust_tier: Literal["high", "medium", "low"]


class ExtractedDocument(BaseModel):
    url: str
    title: str
    source_domain: str
    content: str
    extract_status: Literal["success", "failed", "partial"]


class FactItem(BaseModel):
    fact_id: str
    statement: str
    entity: str
    topic: str
    date_or_timeframe: Optional[str] = None
    numeric_value: Optional[float] = None
    unit: Optional[str] = None
    source_url: str
    source_title: str
    support_strength: Literal["strong", "medium", "weak"]


class ClaimCandidate(BaseModel):
    claim_id: str
    claim_text: str
    topic: str
    supporting_fact_ids: List[str]


class VerifiedClaim(BaseModel):
    claim_id: str
    claim_text: str
    status: Literal["supported", "weakly_supported",
                    "conflicting", "unsupported"]
    supporting_sources: List[SelectedSource]
    verification_note: str


class CoverageAssessment(BaseModel):
    coverage_status: Literal["sufficient", "insufficient", "conflicting"]
    missing_facets: List[str]
    needs_repair_loop: bool


class RequiredCoverage(BaseModel):
    min_sources: int
    required_facets: List[str]


class ReferenceItem(BaseModel):
    title: str
    url: str
    source_domain: str


class GroundingSummary(BaseModel):
    verified_claim_count: int
    weakly_supported_claim_count: int
    conflicting_claim_count: int
    unsupported_claim_count: int
    reference_count: int
    coverage_status: Literal["sufficient", "insufficient", "conflicting"]


class GraphState(BaseModel):
    messages: Annotated[List[AnyMessage],
                        add_messages] = Field(default_factory=list)
    resolved_query: str = ""
    thread_id: str = ""
    # Core inputs
    user_query: str = ""
    intent: Optional[Literal["market_trend", "regulation",
                             "news", "competitor_context", "mixed", "out_of_scope"]] = None
    router_confidence: Optional[Literal["high", "medium", "low"]] = None
    router_reasoning: str = ""
    router_is_followup_sensitive: Optional[bool] = None
    router_fallback_used: bool = False
    scope_guard_reason: str = ""
    heuristic_intent: Optional[Literal["market_trend", "regulation",
                                       "news", "competitor_context", "mixed", "out_of_scope"]] = None
    is_followup: bool = False
    followup_type: Optional[Literal[
        "entity_drilldown", "source_request", "recency_update", "comparison", "summary_request", "clarification", "none"
    ]] = None
    followup_reasoning: str = ""
    resolved_topic: str = ""
    resolved_entities: List[str] = Field(default_factory=list)
    resolved_timeframe: str = ""
    reuse_previous_verified_claims: bool = False
    requires_fresh_search: bool = True
    search_decision: Literal["full_search", "reuse_only",
                             "reuse_plus_search", "fresh_search"] = "full_search"

    # Thread memory
    conversation_topic: str = ""
    last_entities: List[str] = Field(default_factory=list)
    last_timeframe: str = ""
    previous_final_answer: str = ""
    previous_verified_claims: List[Dict[str, Any]] = Field(
        default_factory=list)
    previous_conflicting_claims: List[Dict[str, Any]] = Field(
        default_factory=list)
    previous_references: List[Dict[str, Any]] = Field(default_factory=list)
    previous_selected_sources: List[Dict[str, Any]] = Field(
        default_factory=list)
    thread_summary: str = ""
    reused_claim_count: int = 0
    reused_reference_count: int = 0

    # Planning
    sub_queries: List[str] = Field(default_factory=list)
    required_coverage: Optional[Dict[str, Any]] = None

    # Searching & Sourcing
    search_results: Annotated[List[Dict[str, Any]],
                              operator.add] = Field(default_factory=list)
    selected_sources: List[Dict[str, Any]] = Field(default_factory=list)
    extracted_documents: List[Dict[str, Any]] = Field(default_factory=list)

    # Fact Building
    facts: List[Dict[str, Any]] = Field(default_factory=list)
    claims_candidate: List[Dict[str, Any]] = Field(default_factory=list)

    # Verification
    verified_claims: List[Dict[str, Any]] = Field(default_factory=list)
    unsupported_claims: List[Dict[str, Any]] = Field(default_factory=list)
    # reusing VerifiedClaim struct for conflicts
    conflicting_claims: List[Dict[str, Any]] = Field(default_factory=list)
    coverage_assessment: Optional[Dict[str, Any]] = None

    # Repair
    refined_sub_queries: List[str] = Field(default_factory=list)
    loop_count: int = 0

    # Output
    final_answer: str = ""
    references: List[Dict[str, Any]] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)
    writer_notes: List[str] = Field(default_factory=list)
    processing_warnings: List[str] = Field(default_factory=list)
    grounding_summary: Optional[Dict[str, Any]] = None


ModelT = TypeVar("ModelT", bound=BaseModel)


def as_model(value: Any, model_cls: Type[ModelT]) -> Optional[ModelT]:
    if isinstance(value, model_cls):
        return value
    if isinstance(value, dict):
        return model_cls(**value)
    if hasattr(value, "model_dump"):
        return model_cls(**value.model_dump())
    return None


def as_model_list(values: Any, model_cls: Type[ModelT]) -> List[ModelT]:
    if not isinstance(values, list):
        return []
    parsed: List[ModelT] = []
    for item in values:
        model = as_model(item, model_cls)
        if model is not None:
            parsed.append(model)
    return parsed
