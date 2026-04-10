# Observability Gap Analysis — AISEP AI Service

## 1. What the codebase already has

| Capability                    | Status                  | Implementation                                                                                                                                                                        |
| ----------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Structured logging**        | ✅ Partial              | `src/shared/logging/logger.py` → `setup_logger()` used across all modules. Unstructured text format (`logger.info("text %s", val)`). No JSON log formatter.                           |
| **Correlation IDs**           | ✅ Full                 | `src/shared/correlation.py` → `CorrelationIdMiddleware` assigns per-request UUID, available via `get_correlation_id()`. Returned in `X-Correlation-Id` header.                        |
| **OpenTelemetry tracing**     | ⚠️ Scaffolded, disabled | `src/shared/tracing/setup.py` → `init_tracing()` exists, checks `OTEL_ENABLED`. Currently `OTEL_ENABLED=false`. No OTel SDK in `requirements.txt`. Noop spans returned when disabled. |
| **Health checks**             | ✅ Full                 | `src/shared/health.py` → `/health`, `/health/live`, `/health/ready`. Checks DB, Redis, Celery workers, Gemini provider, recommendation storage.                                       |
| **Rate limiting**             | ✅ Full                 | `src/shared/rate_limit/` → per-endpoint token-bucket rate limiter with configurable RPM.                                                                                              |
| **Error envelope**            | ✅ Full                 | `src/shared/error_response.py` + `exceptions/` → consistent `APIError` envelope with `code`, `message`, `correlation_id`.                                                             |
| **Prometheus metrics**        | ❌ None                 | Zero `prometheus_client` usage. No `/metrics` endpoint. No counters, histograms, or gauges anywhere.                                                                                  |
| **Request latency tracking**  | ❌ None                 | No middleware or decorator measures HTTP request duration.                                                                                                                            |
| **Worker task metrics**       | ❌ None                 | Celery tasks (`src/modules/evaluation/workers/tasks.py`) have no duration/success/failure counters.                                                                                   |
| **External provider metrics** | ❌ None                 | `GeminiClient` has retry logic + error classification but no call counters or latency histograms. Same for Tavily (`search_node.py`, `extract_node.py`).                              |
| **Business metrics**          | ❌ None                 | No counters for evaluation runs by status, recommendation requests served, chatbot sessions, out-of-scope queries, etc.                                                               |

## 2. Biggest gaps today

### Gap 1: Zero quantitative visibility

There is no way to answer "how many evaluation runs completed in the last hour?" or "what's the p95 latency of recommendation requests?" without manually grepping logs.

### Gap 2: No provider failure tracking

Gemini API calls are retried with Tenacity, but there's no counter for how often retries fire, how often quota is exhausted, or how often Tavily fails. If Gemini starts rate-limiting, the only signal is buried log lines.

### Gap 3: No worker observability

Celery tasks run in a separate process. Without metrics, there's no visibility into:

- Queue depth / lag (time from submit to processing)
- Task duration distribution
- Failure rate by error type

### Gap 4: No streaming session metrics

The investor-agent SSE stream (`/chat/stream`) has no instrumentation for:

- How many streams are active
- How many end in error vs success
- How many are out-of-scope short-circuits
- Stream duration

### Gap 5: Logs are unstructured text

Current `logger.info("text %s", val)` pattern makes it hard to aggregate, filter, or build dashboards from logs alone. No JSON formatter configured.

## 3. Why Prometheus metrics are the right choice

| Alternative                          | Why not                                                                                                                                                                                  |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Full OTel + Jaeger                   | Requires Jaeger/Tempo deployment, OTel Collector, SDK packages. Overkill for capstone.                                                                                                   |
| ELK/Loki log aggregation             | Would help with log search but doesn't provide counter/histogram time-series. Requires infrastructure.                                                                                   |
| StatsD/Graphite                      | Older stack, less Python ecosystem support.                                                                                                                                              |
| **Prometheus + `prometheus_client`** | **Zero infrastructure** — just expose `/metrics`, scrape with Prometheus. One pip package. Native Python histograms/counters. Grafana reads Prometheus natively. Can demo in 10 minutes. |

## 4. Metrics that are actually worth doing for capstone demo

### Must-do (P0) — 15 metrics

These provide the "operational proof" slide: throughput, latency, error rate across all 3 modules.

| Category           | Count | Demo value                                 |
| ------------------ | ----- | ------------------------------------------ |
| Cross-cutting HTTP | 2     | Shows system-wide throughput + latency     |
| Evaluation         | 5     | Submit → process → complete/fail lifecycle |
| Recommendation     | 4     | Reindex + query pipeline                   |
| Investor Agent     | 4     | Stream lifecycle + scope guard             |

### Good-to-have (P1) — 9 metrics

Provider failure breakdown, candidate pool histograms, claim/reference counts.

### Not worth doing now (skip)

| Metric idea                              | Why skip                                                                            |
| ---------------------------------------- | ----------------------------------------------------------------------------------- |
| Per-startup-id or per-investor-id labels | Cardinality explosion. N entities × M metrics = unbounded series.                   |
| Queue depth gauge                        | Requires Redis inspection or Celery Flower. Separate infrastructure.                |
| Individual graph node latency            | 9 nodes × histogram = too granular for capstone. Log timing is enough.              |
| Memory/CPU process metrics               | `prometheus_client` already exposes these by default via `PROCESS_COLLECTOR`. Free. |
| OTel span metrics                        | Disabled, no SDK installed. Don't enable just for metrics — Prometheus is simpler.  |
