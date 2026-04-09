# AISEP Python AI Service - Codebase Summary

## Repo purpose

Python FastAPI service exposing 3 AI capabilities:

1. `evaluation` (async document evaluation pipeline)
2. `recommendation` (investor-startup matching)
3. `investor_agent` (research/chat with optional SSE streaming)

## Core architecture

- **API framework**: FastAPI (`src/main.py`)
- **Persistence**:
  - SQLModel/SQLite for Evaluation module (`src/shared/persistence/models/evaluation_models.py`)
  - Configurable recommendation storage: DB-backed (SQLModel, default/production) or legacy filesystem JSON — controlled by `RECOMMENDATION_BACKEND` env var
  - Configurable LangGraph checkpoint for Investor Agent conversation state: Redis-backed (`AsyncRedisSaver`) for production or in-process `MemorySaver` for local dev — controlled by `CHECKPOINT_BACKEND` env var
- **Background processing**:
  - Celery + Redis for evaluation jobs (`src/celery_app.py`, `src/modules/evaluation/workers/tasks.py`)
  - Deprecated polling fallback worker (`src/worker.py`) kept for dev-only
- **Webhook / callback**: Optional outbound POST for terminal evaluation events (`src/shared/webhook/delivery.py`), HMAC-SHA256 signed, retried with back-off, audit-logged to `webhook_deliveries` table
- **Rate limiting**: Configurable per-endpoint token-bucket rate limiter (`src/shared/rate_limit/limiter.py`), returns stable 429 envelope
- **Tracing / observability**: Optional OpenTelemetry bootstrap (`src/shared/tracing/setup.py`), no-op when `OTEL_ENABLED=false`
- **LLM provider**: Gemini via `google-genai` wrapper (`src/shared/providers/llm/gemini_client.py`)
- **Web research provider**: Tavily for Investor Agent search/extract nodes

## Entrypoints

- API: `uvicorn src.main:app --reload`
- Celery worker: `python -m celery -A src.celery_app:celery_app worker -l INFO`
- Deprecated polling worker: `python src/worker.py` (not production path)

## Router registration

From `src/main.py`:

- `evaluation_router` with prefix `/api/v1/evaluations`
- `investor_router` with prefix `/api/v1/investor-agent`
- `recommendation_router` with **no global prefix** (its own routes include full paths)
- Health endpoint: `GET /health`

## Major modules

### 1) Evaluation module

- API: `src/modules/evaluation/api/router.py`
- Async submit -> Celery task enqueue in `submit_evaluation`
- Worker task: `evaluation.process_run` updates DB statuses
- Multi-step document pipeline: parse PDF -> LLM stages -> deterministic scorer -> canonical report

### 2) Recommendation module

- API: `src/modules/recommendation/api/router.py`
- Internal reindex endpoints protected by `X-Internal-Token` (if configured)
- Public recommendation/explanation endpoints
- Storage: JSON files (investor/startup docs + run history), not relational DB
- Embeddings: deterministic hash embedding implemented in-code (not external vector DB)

### 3) Investor Agent module

- API: `src/modules/investor_agent/api/router.py`
- LangGraph flow with nodes:
  `followup_resolver -> router -> planner -> search -> source_selection -> extract -> fact_builder -> claim_verifier -> writer`
- Supports sync-like response (`/chat`) and SSE stream (`/chat/stream`)
- Memory by `thread_id` through LangGraph checkpoint (Redis-backed when `CHECKPOINT_BACKEND=redis`; in-process `MemorySaver` when `memory`)

## Sync vs Async

- **Async jobs required**: Evaluation submit (`POST /api/v1/evaluations/`) -> queue + poll
- **Sync request-response**:
  - Recommendation endpoints
  - Investor Agent `/research` and `/chat`
- **Streaming**:
  - Investor Agent `/chat/stream` (`text/event-stream`)

## Current readiness snapshot

- `evaluation`: **partially ready** (async orchestration + status lifecycle exists; some LLM/schema failure paths still possible)
- `recommendation`: **partially ready** (works, but storage/embedding are lightweight and not enterprise-grade)
- `investor_agent`: **experimental/partially ready** (feature-rich; checkpoint now externalizable to Redis for durability; no auth; variable output depending on upstream data)
