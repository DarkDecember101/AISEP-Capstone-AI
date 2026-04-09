# Feature Flows (End-to-End)

## 1) AI Evaluation flow

### Runtime flow from code

1. `.NET` submits to `POST /api/v1/evaluations/`.
2. Python creates `EvaluationRun(status="queued")` and `EvaluationDocument` rows.
3. Python enqueues Celery task `evaluation.process_run` with `evaluation_run_id`.
4. Celery worker loads run from DB and applies idempotency guard:
   - process only statuses `queued` or `retry`
   - skip otherwise
5. Worker sets `run.status = processing`, then processes each document:
   - resolve local path / download URL
   - parse PDF content/images
   - LLM pipeline (`classification -> evidence -> raw_criterion -> report_writer`)
   - deterministic scorer
   - write canonical payload to `artifact_metadata_json`
6. Aggregation computes run-level summary and sets final status (`completed` or `failed`).

### Status lifecycle (observed)

- Run status values used in code: `queued`, `processing`, `completed`, `failed`, `retry`.
- `partial_completed` appears in checks/schema but no explicit setter found in current evaluation use cases.

### Polling contract for .NET

- Poll `GET /api/v1/evaluations/{id}` for status and document summaries.
- Call `GET /api/v1/evaluations/{id}/report` only when status is complete.
- Handle:
  - `202`: report not ready
  - `409`: run failed / no canonical report
  - `500`: canonical parse failure

### Webhook / callback contract (optional, Phase 2C)

- If `WEBHOOK_CALLBACK_URL` is configured, Python will POST a signed JSON payload when an evaluation reaches terminal status (`completed` or `failed`).
- **Payload fields**: `delivery_id`, `evaluation_run_id`, `startup_id`, `terminal_status`, `overall_score` (if completed), `failure_reason` (if failed), `timestamp`, `correlation_id`.
- **Signing**: `X-Webhook-Signature` header contains HMAC-SHA256 hex digest of the raw body, using `WEBHOOK_SIGNING_SECRET`.
- **Idempotency**: `delivery_id` is deterministic per run + status — .NET can de-duplicate on it.
- **Retry**: up to `WEBHOOK_MAX_RETRIES` attempts with exponential back-off (1s, 2s, 4s …).
- **Audit**: all delivery attempts persisted in `webhook_deliveries` table.
- **Failure isolation**: callback failure never breaks evaluation completion.

### .NET data mapping recommendation

Persist at least:

- `evaluation_run_id` (Python)
- `.NET startup id` -> Python `startup_id`
- submit timestamp
- latest run status
- `failure_reason`

### Re-evaluation trigger behavior

- New submit for same startup marks prior active runs (`queued/processing/partial_completed`) as `failed` with reason `Superseded by a new evaluation request.`

---

## 2) AI Recommendation flow

### Runtime flow from code

**Indexing phase (internal):**

1. `.NET` sends source profile payload to reindex endpoints:
   - startup -> `/internal/recommendations/reindex/startup/{startup_id}`
   - investor -> `/internal/recommendations/reindex/investor/{investor_id}`
2. Python builds normalized documents + embeddings.
3. Documents stored in Postgres (default, via `RECOMMENDATION_BACKEND=db`) or legacy JSON files under `storage/recommendations/` (`RECOMMENDATION_BACKEND=filesystem`).

**Recommendation query phase (public):**

1. `.NET` calls `GET /api/v1/recommendations/startups?investor_id=...&top_n=...`.
2. Engine loads investor doc + all startups.
3. Hard filter removes invalid candidates (stage/geography/visibility/verification rules).
4. Structured scoring + semantic scoring executed.
5. Optional Gemini reranker adjustment (`-10..+10`) when `GEMINI_API_KEY` exists.
6. Returns ranked `RecommendationMatchResult` list.

### Reindex strategy for .NET

- Trigger reindex when profile fields or AI evaluation attributes change.
- Startup fields critical for ranking quality:
  - `stage`, `primary_industry`, `location`, `market_scope`, `product_status`, `validation_status`
  - AI fields: `ai_evaluation_status`, `ai_overall_score`, `ai_strength_tags`, `ai_summary`
- Investor fields critical for filtering:
  - `preferred_stages`, `preferred_industries`, `preferred_geographies`
  - `require_verified_startups`, `require_visible_profiles`

### On-demand vs precompute

- Current code computes recommendations on-demand per request.
- Precompute queue pattern is **not implemented** in this module.

---

## 3) Investor Agent / Chatbot flow

### Runtime graph flow

`followup_resolver -> router -> planner -> search -> source_selection -> extract -> fact_builder -> claim_verifier -> writer`

### Thread memory model

- Input includes `thread_id`.
- LangGraph config uses `{"configurable": {"thread_id": ...}}`.
- Checkpointer is configurable via `CHECKPOINT_BACKEND` env var:
  - `redis` → `AsyncRedisSaver` (durable, shared across instances, survives restarts; TTL controlled by `CHECKPOINT_TTL_MINUTES`).
  - `memory` (default) → `MemorySaver` (in-process only, local dev).
- When backend is `redis`, conversations survive process restarts and are shared across workers.

### Follow-up behavior

- `followup_resolver` classifies follow-up type:
  - `entity_drilldown`, `source_request`, `recency_update`, `comparison`, `summary_request`, `clarification`, `none`
- Sets `search_decision`:
  - `full_search`, `reuse_only`, `reuse_plus_search`, `fresh_search`
- `route_after_router` can short-circuit to writer on:
  - `intent == out_of_scope`
  - `search_decision == reuse_only`

### Out-of-scope handling

- Scope decision uses router output + heuristic fallback (`scope_guard.py`).
- Out-of-scope payload is standardized with:
  - refusal final_answer
  - no references
  - caveat + warning flags

### Streaming flow (`/chat/stream`)

- Emits progress events per node.
- Emits answer chunks + final answer + final metadata.
- Ends with `data: [DONE]`.
- On internal exceptions emits `type=error` event, then `[DONE]`.

### .NET / frontend consumption guidance

- Always pass deterministic `thread_id` per conversation session.
- Use stream endpoint for real-time UX, parse event `type` switch:
  - `progress` -> step indicator
  - `answer_chunk` -> append partial text
  - `final_answer` -> lock primary answer text
  - `final_metadata` -> render references/caveats/warnings
  - `error` -> surface safe error banner
  - `[DONE]` -> close stream
