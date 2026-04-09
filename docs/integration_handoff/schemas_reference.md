# Schemas Reference (DTO Mapping Catalog)

## Canonical note

- **Evaluation canonical output**: `CanonicalEvaluationResult` (`src/modules/evaluation/application/dto/canonical_schema.py`)
- Legacy/alternate response schema `AggregatedReportSchema` exists in `evaluation_schema.py` but active report endpoint currently returns `CanonicalEvaluationResult`.

---

## Evaluation module schemas

### Submit request

`SubmitEvaluationRequest`

- `startup_id: str`
- `documents: List[DocumentInputSchema]`
  - `document_id: str`
  - `document_type: str`
  - `file_url_or_path: str`

### Submit response

`SubmitEvaluationResponse`

- `evaluation_run_id: int`
- `status: str`
- `message: str`

### Canonical report

`CanonicalEvaluationResult`

- `startup_id: str`
- `status: Literal[queued, processing, partial_completed, completed, failed]`
- `classification: ClassificationResult`
- `effective_weights: dict`
- `criteria_results: List[CanonicalCriterionResult]`
- `overall_result: CanonicalOverallResult`
- `narrative: CanonicalNarrative`
- `processing_warnings: List[str]`

### Key nested enums/literals

- `CriterionName`:
  - `Problem_&_Customer_Pain`
  - `Market_Attractiveness_&_Timing`
  - `Solution_&_Differentiation`
  - `Business_Model_&_Go_to_Market`
  - `Team_&_Execution_Readiness`
  - `Validation_Traction_Evidence_Quality`
- Confidence values: `High | Medium | Low`

### DB statuses observed

- Run: `queued`, `processing`, `retry`, `completed`, `failed` (+ `partial_completed` referenced)
- Document processing: `queued`, `processing`, `completed`, `failed`
- Document extraction: `pending`, `extracting`, `done`, `failed`

---

## Recommendation module schemas

### Reindex requests

- `ReindexInvestorRequest`
- `ReindexStartupRequest`

Both include:

- `profile_version`
- `source_updated_at`
- rich profile fields used for ranking

### Public recommendation response

`RecommendationListResponse`

- `investor_id: str`
- `items: List[RecommendationMatchResult]`
- `warnings: List[str]`
- `internal_warnings: List[str]`
- `generated_at: datetime`

`RecommendationMatchResult` key fields:

- identity: `investor_id`, `startup_id`, `startup_name`
- scores: `final_match_score`, `structured_score`, `semantic_score`, `combined_pre_llm_score`, `rerank_adjustment`
- classification: `match_band` (`LOW|MEDIUM|HIGH|VERY_HIGH`), `fit_summary_label`
- diagnostics: `breakdown`, `match_reasons`, `positive_reasons`, `caution_reasons`, `warning_flags`

### Explanation response

`RecommendationExplanationResponse`

- `investor_id`
- `startup_id`
- `result: RecommendationMatchResult`
- `generated_at`

---

## Investor Agent schemas

### Request models

- `ResearchRequest`
  - `query: str`
- `ChatRequest`
  - `query: str`
  - `thread_id: str` (default `default_thread`)

### `/research` response model

`ResearchResponse`

- `intent: str`
- `final_answer: str`
- `references: List[ReferenceItem]`
- `caveats: List[str]`
- `writer_notes: List[str]`
- `processing_warnings: List[str]`
- `grounding_summary: GroundingSummary`

### `/chat` response shape (dict, not strict response_model)

- `intent`
- `final_answer`
- `references`
- `caveats`
- `writer_notes`
- `processing_warnings`
- `grounding_summary`
- `resolved_query`
- `fallback_triggered`

### Streaming event payloads (`/chat/stream`)

- `{"type":"progress","node":"..."}`
- `{"type":"answer_chunk","content":"..."}`
- `{"type":"final_answer","content":"..."}`
- `{"type":"final_metadata", ...}`
- `{"type":"error","content":"..."}`
- terminal marker: `[DONE]`

### GraphState fields relevant to .NET integration

- Input/identity: `user_query`, `resolved_query`, `thread_id`, `intent`
- Follow-up: `is_followup`, `followup_type`, `search_decision`
- Output: `final_answer`, `references`, `caveats`, `writer_notes`, `processing_warnings`, `grounding_summary`

---

## Error payloads

- Evaluation report failures may return:
  - plain string detail (e.g., `202`)
  - object detail for failed run (`409`) including `message`, `failure_reason`, `next_step`
- Recommendation errors:
  - `detail: str` (`404`/`500`)
- Investor endpoints:
  - `500` with `detail: str(e)`

> .NET consumer should tolerate mixed `detail` shapes (`string` vs `object`) for evaluation report endpoint.
