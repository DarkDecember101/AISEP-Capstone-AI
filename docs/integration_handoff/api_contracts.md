# API Contracts (from code)

Source files:

- `src/main.py`
- `src/modules/evaluation/api/router.py`
- `src/modules/recommendation/api/router.py`
- `src/modules/investor_agent/api/router.py`

> Note: `src/modules/evaluation/api/router_backup.py` exists but is **not registered** in `main.py`.

---

## Global endpoint

### `GET /health`

- **Purpose**: backwards-compatible simple health check
- **Auth**: none
- **Response**: `{"status": "ok"}`
- **Mode**: sync
- **Module**: `src/shared/health.py`

### `GET /health/live`

- **Purpose**: Kubernetes-style liveness probe
- **Auth**: none
- **Response**: `{"status": "alive"}`
- **Mode**: sync

### `GET /health/ready`

- **Purpose**: deep readiness probe — checks DB, Redis, Celery workers, provider API keys, recommendation storage
- **Auth**: none
- **Response**:
  ```json
  {
    "ready": true,
    "checks": {
      "database": { "ok": true },
      "redis": { "ok": true },
      "celery_workers": { "ok": true, "workers": 1 },
      "providers": { "ok": true, "configured": ["gemini", "tavily"] },
      "recommendation_storage": { "ok": true, "path": "..." }
    }
  }
  ```
- **Mode**: sync

---

## Unified error response envelope

All error responses across all modules use a consistent shape:

```json
{
  "code": "EVALUATION_NOT_FOUND",
  "message": "Evaluation run 42 not found.",
  "detail": null,
  "retryable": false,
  "correlation_id": "abc123def456"
}
```

Every response includes an `X-Correlation-Id` header for end-to-end tracing.

---

## Evaluation module (`/api/v1/evaluations` prefix)

### 1) `GET /api/v1/evaluations/history?startup_id={startup_id}`

- **Purpose**: list historical evaluation runs for a startup
- **Auth**: none observed
- **Request**:
  - query param: `startup_id: str` (required)
- **Response**: array of objects:
  - `id`
  - `status`
  - `submitted_at`
  - `overall_score`
  - `failure_reason`
- **Status codes observed**:
  - `200`
- **Mode**: sync
- **Module**: `src/modules/evaluation/api/router.py`

### 2) `POST /api/v1/evaluations/`

- **Purpose**: submit evaluation run (async processing)
- **Auth**: none observed
- **Request schema**: `SubmitEvaluationRequest`
  - `startup_id: str`
  - `documents: List[DocumentInputSchema]`
    - `document_id: str`
    - `document_type: str` (`pitch_deck` or `business_plan` by description)
    - `file_url_or_path: str`
- **Response schema**: `SubmitEvaluationResponse`
  - `evaluation_run_id: int`
  - `status: str` (current code returns `queued`)
  - `message: str`
- **Status codes observed**:
  - `200` (no explicit alternative mapping in router)
- **Mode**: async-submit (enqueue Celery task)
- **Module**: `router.py` + `application/use_cases/submit_evaluation.py`

### 3) `GET /api/v1/evaluations/{id}`

- **Purpose**: read run status + document-level processing summary
- **Auth**: none observed
- **Path param**: `id: int`
- **Response object fields**:
  - `id`
  - `startup_id`
  - `status`
  - `submitted_at`
  - `failure_reason`
  - `overall_score`
  - `overall_confidence`
  - `documents: List[{id, document_type, status, extraction_status, summary}]`
- **Status codes observed**:
  - `200`
  - `404` (`"Evaluation run not found"`)
- **Mode**: sync/polling endpoint
- **Module**: `router.py`

### 4) `GET /api/v1/evaluations/{id}/report`

- **Purpose**: fetch canonical evaluation report after completion
- **Auth**: none observed
- **Path param**: `id: int`
- **Response model**: `CanonicalEvaluationResult`
- **Status codes observed**:
  - `200` if report ready and parse succeeds
  - `404` run not found
  - `202` run not completed (`"Report is not ready yet. Please retry shortly."`)
  - `409` when run failed or no successful document evaluation
  - `500` canonical payload parse failure
- **Mode**: sync, but expected to be polled after async submit
- **Module**: `router.py`

---

## Recommendation module (no router prefix in `main.py`; absolute paths defined in router)

### 1) `POST /internal/recommendations/reindex/startup/{startup_id}`

- **Purpose**: upsert startup recommendation document
- **Auth expectation**:
  - header `X-Internal-Token` validated against `AISEP_INTERNAL_TOKEN` if configured
- **Path param**: `startup_id: str`
- **Request schema**: `ReindexStartupRequest` (key fields)
  - profile fields: `profile_version`, `source_updated_at`, `startup_name`, `tagline`, `stage`, `primary_industry`, `location`, ...
  - visibility/verification: `is_profile_visible_to_investors`, `verification_label`, `account_active`
  - AI profile: `ai_evaluation_status`, `ai_overall_score`, `ai_summary`, `ai_strength_tags`, `ai_weakness_tags`, `ai_dimension_scores`
- **Response**:
  - `success: true`
  - `startup_id`
  - `profile_version`
  - `source_updated_at`
  - `message`
- **Status codes observed**:
  - `200`
  - `401` invalid internal token
- **Mode**: sync
- **Module**: `src/modules/recommendation/api/router.py`

### 2) `POST /internal/recommendations/reindex/investor/{investor_id}`

- **Purpose**: upsert investor recommendation document
- **Auth expectation**: same `X-Internal-Token`
- **Path param**: `investor_id: str`
- **Request schema**: `ReindexInvestorRequest` (key fields)
  - investor identity/preferences/thesis/support lists, score preference, visibility requirements
- **Response**:
  - `success: true`
  - `investor_id`
  - `profile_version`
  - `source_updated_at`
  - `message`
- **Status codes observed**:
  - `200`
  - `401` invalid internal token
- **Mode**: sync

### 3) `GET /api/v1/recommendations/startups?investor_id={id}&top_n={1..10}`

- **Purpose**: get ranked startup list for investor
- **Auth**: none observed
- **Query params**:
  - `investor_id: str` required
  - `top_n: int` default `10`, min `1`, max `10`
- **Response schema**: `RecommendationListResponse`
  - `investor_id`
  - `items: List[RecommendationMatchResult]`
  - `warnings: List[str]`
  - `internal_warnings: List[str]`
  - `generated_at`
- **Status codes observed**:
  - `200`
  - `404` (`ValueError` converted)
  - `500` generic exception
- **Mode**: sync

### 4) `GET /api/v1/recommendations/startups/{startup_id}/explanation?investor_id={id}`

- **Purpose**: explain one investor-startup match
- **Auth**: none observed
- **Path param**: `startup_id: str`
- **Query param**: `investor_id: str` required
- **Response schema**: `RecommendationExplanationResponse`
  - `investor_id`
  - `startup_id`
  - `result: RecommendationMatchResult`
  - `generated_at`
- **Status codes observed**:
  - `200`
  - `404`
  - `500`
- **Mode**: sync

---

## Investor Agent module (`/api/v1/investor-agent` prefix)

### 1) `POST /api/v1/investor-agent/research`

- **Purpose**: one-shot investor research pipeline
- **Auth**: none observed
- **Request schema**: `ResearchRequest`
  - `query: str`
- **Response schema**: `ResearchResponse`
  - `intent: str`
  - `final_answer: str`
  - `references: List[{title,url,source_domain}]`
  - `caveats: List[str]`
  - `writer_notes: List[str]`
  - `processing_warnings: List[str]`
  - `grounding_summary: GroundingSummary`
- **Status codes observed**:
  - `200`
  - `500`
- **Mode**: sync (long-running)

### 2) `POST /api/v1/investor-agent/chat`

- **Purpose**: multi-turn non-stream chat using thread memory
- **Auth**: none observed
- **Request schema**: `ChatRequest`
  - `query: str`
  - `thread_id: str = "default_thread"`
- **Response**: dict from `_build_chat_payload`
  - `intent`
  - `final_answer`
  - `references`
  - `caveats`
  - `writer_notes`
  - `processing_warnings`
  - `grounding_summary`
  - `resolved_query`
  - `fallback_triggered`
- **Status codes observed**:
  - `200`
  - `500`
- **Mode**: sync

### 3) `POST /api/v1/investor-agent/chat/stream`

- **Purpose**: SSE streaming chat events
- **Auth**: none observed
- **Request schema**: `ChatRequest`
- **Transport**: `text/event-stream`
- **Event payloads (JSON in `data:` line)**:
  - `{"type":"progress","node":"<graph_node>"}`
  - `{"type":"answer_chunk","content":"..."}`
  - `{"type":"final_answer","content":"..."}`
  - `{"type":"final_metadata","references":[],"caveats":[],"writer_notes":[],"processing_warnings":[],"grounding_summary":{...}}`
  - `{"type":"error","content":"..."}`
  - terminal marker: `data: [DONE]`
- **Status codes observed**:
  - streaming response `200`
- **Mode**: stream

---

## Auth summary

- **Implemented in code**: reusable `require_internal_auth` dependency on recommendation reindex endpoints.
- **Auth toggle**: controlled by `REQUIRE_INTERNAL_AUTH` env var (`false` = disabled for local dev, `true` = enforced in production).
- **Header**: `X-Internal-Token` validated against `AISEP_INTERNAL_TOKEN` when auth is enabled.
- **Not found in code**: auth enforcement for evaluation + investor-agent + recommendation read endpoints.
