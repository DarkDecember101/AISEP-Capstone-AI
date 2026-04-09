# Environment and Config Reference

Source of truth:

- `src/shared/config/settings.py`
- `src/celery_app.py`

## Core settings loaded by app

### Service metadata

- `PROJECT_NAME` (default: `AISEP - AI Evaluation`)
- `API_V1_STR` (default: `/api/v1`)

### Persistence

- `DATABASE_URL` (default: `sqlite:///./aisep_ai.db`)
  - Used by `src/shared/persistence/db.py`
  - SQLite enables `check_same_thread=False`

### LLM / AI providers

- `GEMINI_API_KEY` (default: empty)
  - Required for Gemini-backed paths
- `OPENAI_API_KEY` (default: empty)
  - Present in settings; current scanned flows primarily use Gemini
- `DEFAULT_LLM_PROVIDER` (default literal: `openai`)
- `DEFAULT_MODEL_NAME` (default literal: `gpt-4o-mini`)
- `ENABLE_PSEUDO_OCR_FALLBACK` (default: `true` string parsed to bool)

### Search / extraction

- `TAVILY_API_KEY` (fallback env alias: `TAILY_API_KEY`)

### Internal auth

- `AISEP_INTERNAL_TOKEN` (default: empty)
  - Validated for:
    - `POST /internal/recommendations/reindex/startup/{startup_id}`
    - `POST /internal/recommendations/reindex/investor/{investor_id}`
- `REQUIRE_INTERNAL_AUTH` (default: `false`)
  - Set to `true` in production to enforce `X-Internal-Token` header on internal endpoints
  - When `false`, internal endpoints pass without token (local dev convenience)

### Logging

- `LOG_LEVEL` (default: `INFO`)
  - Supported: `DEBUG`, `INFO`, `WARNING`, `ERROR`

### Celery / Redis

- `CELERY_BROKER_URL` (default: `redis://localhost:6379/0`)
- `CELERY_RESULT_BACKEND` (default: `redis://localhost:6379/1`)

### Investor-Agent Checkpoint

- `CHECKPOINT_BACKEND` (default: `memory`)
  - `redis` → durable, shared across workers (recommended for production)
  - `memory` → in-process `MemorySaver` (local dev only)
- `CHECKPOINT_REDIS_URL` (default: `redis://localhost:6379/2`) — used when `CHECKPOINT_BACKEND=redis`
- `CHECKPOINT_TTL_MINUTES` (default: `1440` = 24 hours) — idle conversation expiry; `0` = no expiry

### Recommendation Storage

- `RECOMMENDATION_BACKEND` (default: `db`)
  - `db` → durable DB-backed via SQLModel (production default)
  - `filesystem` → legacy JSON files under `storage/recommendations`
- Migration utility: `python -m src.modules.recommendation.scripts.migrate_json_to_db`

### Evaluation Webhook / Callback

- `WEBHOOK_CALLBACK_URL` (default: empty → disabled)
  - URL to POST terminal evaluation events to
- `WEBHOOK_SIGNING_SECRET` (default: empty → unsigned)
  - HMAC-SHA256 shared secret; signature in `X-Webhook-Signature` header
- `WEBHOOK_MAX_RETRIES` (default: `3`)
  - Max delivery attempts per callback event (exponential back-off)

### OpenTelemetry Tracing

- `OTEL_ENABLED` (default: `false`) — set `true` to activate
- `OTEL_SERVICE_NAME` (default: `aisep-ai`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` (default: `http://localhost:4317`)
- Requires optional packages: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-fastapi`

### Rate Limiting

- `RATE_LIMIT_ENABLED` (default: `true`) — master switch
- `RATE_LIMIT_EVAL_RPM` (default: `20`) — evaluation submit, per client IP
- `RATE_LIMIT_CHAT_RPM` (default: `30`) — investor-agent chat
- `RATE_LIMIT_STREAM_RPM` (default: `30`) — investor-agent chat/stream
- `RATE_LIMIT_RECO_RPM` (default: `60`) — recommendation read endpoints
- Internal reindex endpoints are exempt
- Returns stable `429 RATE_LIMIT_EXCEEDED` error envelope

### Filesystem paths (derived)

- `STORAGE_DIR` = `<cwd>/storage`
- `ARTIFACTS_DIR` = `<cwd>/storage/artifacts`
- Both directories are auto-created at settings load

## Celery runtime config (code-configured)

From `src/celery_app.py`:

- serializers: JSON only
- `task_track_started=true`
- `task_acks_late=true`
- `worker_prefetch_multiplier=1`
- `result_expires=86400`
- default `worker_pool=prefork`
- Windows override:
  - `worker_pool=solo`
  - `worker_concurrency=1`

## Minimum env set for practical .NET integration

- `DATABASE_URL`
- `GEMINI_API_KEY` (if using Gemini-backed evaluation/recommendation/investor-agent behavior)
- `TAVILY_API_KEY` (investor-agent upstream search/extract)
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `AISEP_INTERNAL_TOKEN` (recommended if internal reindex endpoints are exposed)
- `REQUIRE_INTERNAL_AUTH=true` (for production auth enforcement)
- `LOG_LEVEL` (default INFO; use DEBUG for troubleshooting)

## Phase 1 hardening additions

- All error responses now use unified `{code, message, detail, retryable, correlation_id}` envelope
- `X-Correlation-Id` header is auto-assigned per request and returned in responses
- Structured JSON logs with automatic secret masking
- `/health/live` and `/health/ready` endpoints for liveness/readiness probes
- `.env.example` provided as configuration reference

## Recommended production hardening (observed gaps)

- Add auth to non-internal endpoints (evaluation + investor-agent + recommendation read APIs)
- Move from default SQLite to managed DB
- Ensure Redis HA policy for Celery broker/backend
- Externalize CORS/security settings if frontends will call directly
