# Integration Gaps and Risks

## 1) API security coverage is partial

- Observed auth enforcement is only on recommendation reindex endpoints via `X-Internal-Token`.
- Evaluation endpoints, recommendation read endpoints, and investor-agent endpoints are unauthenticated in current code.
- Impact: direct exposure risk if deployed without API gateway policy.

## 2) Contract consistency and stability

- Some endpoints return typed Pydantic models, while others return ad-hoc dict payloads.
- Error `detail` shape varies (string or object).
- Impact: .NET client needs tolerant deserialization and endpoint-specific DTO mapping.

## 3) Async status model is run-level source of truth only — webhook added ✅

- Celery result backend exists, but authoritative state is DB `EvaluationRun.status`.
- Optional webhook/callback delivery now fires on terminal evaluation status (completed/failed) — configure `WEBHOOK_CALLBACK_URL` + `WEBHOOK_SIGNING_SECRET`.
- Polling flow remains supported and unchanged for backward compatibility.
- Impact: .NET can either poll `GET /api/v1/evaluations/{id}` or receive a signed callback.

## 4) Recommendation storage is now DB-backed ✅

- Default backend is now `RECOMMENDATION_BACKEND=db` (SQLModel tables: `recommendation_investors`, `recommendation_startups`, `recommendation_runs`).
- Legacy filesystem JSON backend still available via `RECOMMENDATION_BACKEND=filesystem`.
- Migration utility provided: `python -m src.modules.recommendation.scripts.migrate_json_to_db`.
- Impact: concurrency-safe, durable, no longer dependent on filesystem semantics.

## 5) Investor-agent memory is now externalizable ✅

- Graph checkpoint backend is configurable: set `CHECKPOINT_BACKEND=redis` for durable Redis-backed state (`AsyncRedisSaver` on `CHECKPOINT_REDIS_URL`, default DB 2).
- Default for local dev remains `memory` (`MemorySaver`, in-process).
- With Redis backend, thread memory survives restarts and is shared across instances.
- TTL-based expiry via `CHECKPOINT_TTL_MINUTES` (default 1440 = 24 h).

## 6) External dependency failure sensitivity

- Investor-agent depends on Tavily + Gemini for core upstream stages.
- Recommendation rerank and evaluation logic can degrade/fallback when LLM unavailable, but behavior quality drops.
- Impact: define .NET-side retry/fallback UX for partial degradation.

## 7) Operational observability baseline — tracing + rate limiting added ✅

- Logging exists and DB logs exist for evaluation run steps.
- OpenTelemetry tracing bootstrap added (`OTEL_ENABLED=true`) with auto-instrumentation for FastAPI and manual spans on evaluation/recommendation/investor-agent boundaries.
- Configurable rate limiting on expensive public endpoints (evaluation submit, investor-agent chat/stream, recommendation reads) — returns stable 429 error envelope.
- Impact: production SLOs now have a tracing foundation and basic abuse protection.

## 8) Legacy worker path still present

- `src/worker.py` remains for local/dev fallback and is explicitly marked deprecated for production.
- Impact: runbook ambiguity if teams start wrong worker process.

## Integration readiness summary (for .NET)

- Evaluation async flow: **usable with polling contract + optional signed webhook callback**
- Recommendation sync APIs: **usable, storage is production-grade (DB-backed)**
- Investor-agent stream: **usable with tolerant event parser**, memory durability available via Redis
- Rate limiting: **active on expensive public endpoints, configurable RPM per endpoint**
- Observability: **OTel tracing bootstrap available, correlation_id propagated end-to-end**
- Production-hardening priority: auth, contract normalization, Alembic migrations
