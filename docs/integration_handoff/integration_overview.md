# Integration Overview (.NET -> Python AI Service)

## Recommended integration pattern

### A. AI Evaluation (async pattern)

- Call `POST /api/v1/evaluations/` to submit.
- Return immediately to frontend with `evaluation_run_id`.
- Poll:
  - `GET /api/v1/evaluations/{id}` for status/progress
  - `GET /api/v1/evaluations/{id}/report` for final canonical result
- Do **not** wait synchronously in .NET for evaluation completion.

### B. AI Recommendation (sync pattern)

- On profile changes from .NET, trigger internal reindex endpoints.
- On user query, call recommendation endpoints synchronously.
- No Celery/worker needed for recommendation flow.

### C. Investor Agent / Chatbot

- Non-stream chat: use `POST /api/v1/investor-agent/chat`
- Stream chat: use `POST /api/v1/investor-agent/chat/stream` and parse SSE events.
- Always pass a stable `thread_id` from .NET/frontend per conversation thread.

## Feature maturity for production exposure

- **Expose with guardrails**:
  - Evaluation submit/status/report (core flow exists)
  - Recommendation public GET endpoints
- **Expose carefully / pilot first**:
  - Investor Agent streaming endpoint (SSE contract exists; checkpoint now externalizable to Redis for durability; depends on external Tavily/LLM)
- **Internal-only**:
  - Recommendation reindex endpoints (`/internal/...`) with `X-Internal-Token`

## Auth expectations observed in code

- Recommendation internal reindex endpoints validate `X-Internal-Token` when `AISEP_INTERNAL_TOKEN` is configured.
- Evaluation and Investor Agent endpoints currently have **no explicit auth middleware** in this repo.

## Operational dependency split

- Required for evaluation async processing:
  - API process + Redis + Celery worker
- Required for investor research quality:
  - Tavily API key + Gemini key
- Recommendation works without external vector DB; uses DB-backed storage (default) or legacy filesystem JSON + deterministic embedding.
