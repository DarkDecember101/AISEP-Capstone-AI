# Prometheus Metrics Plan — AISEP AI Service

## 1. Naming Convention

All metrics follow the Prometheus naming best-practices:

```
aisep_<module>_<metric_name>_<unit>
```

- **Prefix**: `aisep_` (project namespace, avoids collision)
- **Module**: `eval`, `reco`, `agent`, `http`, `provider`
- **Unit suffix**: `_total` (counters), `_seconds` (histograms/summaries), `_count` (gauges)
- **Labels**: lowercase snake_case, bounded cardinality (never raw IDs)

## 2. Library Choice

**`prometheus_client`** (official Python client)

- Already battle-tested, zero dependencies
- Exposes `/metrics` via ASGI app that mounts on FastAPI
- Includes default process metrics (CPU, memory, open FDs) for free
- No need for `prometheus-fastapi-instrumentator` — we need custom business metrics, not just auto-HTTP. We'll build a thin middleware (~20 lines) for HTTP metrics to keep control.

**Install**: `pip install prometheus-client`

## 3. Architecture

```
src/shared/observability/
├── __init__.py
├── metrics.py          ← All metric objects defined here (single registry)
├── http_middleware.py   ← FastAPI middleware: aisep_http_* metrics
└── provider_tracker.py  ← Context manager for external provider call metrics
```

- **Single registry**: all `Counter`, `Histogram`, `Gauge` objects live in `metrics.py`
- **Middleware**: added in `main.py` after `CorrelationIdMiddleware`
- **`/metrics` endpoint**: mounted in `main.py` as a separate ASGI sub-app (no auth — standard for Prometheus scraping in internal networks; optionally gated behind `METRICS_ENABLED` env var)

---

## 4. P0 Metrics — Must implement for capstone demo

### 4.1 Cross-cutting HTTP (2 metrics)

| Metric                                | Type      | Labels                              | Description                  |
| ------------------------------------- | --------- | ----------------------------------- | ---------------------------- |
| `aisep_http_requests_total`           | Counter   | `method`, `endpoint`, `status_code` | Total HTTP requests          |
| `aisep_http_request_duration_seconds` | Histogram | `method`, `endpoint`                | Request latency distribution |

**`endpoint` label**: normalized route pattern (e.g. `/api/v1/evaluations/{id}`), NOT raw path. Max ~15 unique values.

**Histogram buckets**: `[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]` — covers fast API reads to slow LLM-backed endpoints.

**Instrumented in**: `src/shared/observability/http_middleware.py` → registered in `src/main.py`

---

### 4.2 AI Evaluation (5 metrics)

| Metric                                    | Type      | Labels                                                                        | Description                         | Where                                                                |
| ----------------------------------------- | --------- | ----------------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------------- |
| `aisep_eval_submissions_total`            | Counter   | `status` (`accepted`, `error`)                                                | Evaluation submit requests          | `evaluation/api/router.py` → `submit_evaluation_endpoint`            |
| `aisep_eval_runs_completed_total`         | Counter   | `terminal_status` (`completed`, `failed`)                                     | Runs reaching terminal state        | `evaluation/workers/tasks.py` → end of `process_evaluation_run_task` |
| `aisep_eval_worker_task_duration_seconds` | Histogram | `terminal_status`                                                             | Full Celery task wall-clock time    | `evaluation/workers/tasks.py` → wrap task body                       |
| `aisep_eval_documents_processed_total`    | Counter   | `doc_type` (`pitch_deck`, `business_plan`), `outcome` (`completed`, `failed`) | Per-document processing outcome     | `evaluation/workers/tasks.py` → inside document loop                 |
| `aisep_eval_worker_failures_total`        | Counter   | `error_type` (`extract_error`, `llm_error`, `aggregate_error`, `unknown`)     | Worker-level failure classification | `evaluation/workers/tasks.py` → except blocks                        |

**Notes**:

- `aisep_eval_runs_completed_total` is separate from `aisep_eval_submissions_total` because submit is sync (API) and completion is async (Celery). Two different counters = two different dashboards.
- `doc_type` label has exactly 2 values. Safe cardinality.
- `error_type` label: 4 known values. Bounded.

---

### 4.3 AI Recommendation (4 metrics)

| Metric                                | Type      | Labels                                                                | Description                                   | Where                          |
| ------------------------------------- | --------- | --------------------------------------------------------------------- | --------------------------------------------- | ------------------------------ |
| `aisep_reco_requests_total`           | Counter   | `endpoint` (`list`, `explanation`)                                    | Recommendation query requests                 | `recommendation/api/router.py` |
| `aisep_reco_request_duration_seconds` | Histogram | `endpoint`                                                            | Query latency (includes scoring + LLM rerank) | `recommendation/api/router.py` |
| `aisep_reco_reindex_total`            | Counter   | `entity_type` (`startup`, `investor`), `outcome` (`success`, `error`) | Reindex operations                            | `recommendation/api/router.py` |
| `aisep_reco_reindex_duration_seconds` | Histogram | `entity_type`                                                         | Reindex operation latency                     | `recommendation/api/router.py` |

**Notes**:

- `endpoint` label: exactly 2 values (`list`, `explanation`). Safe.
- `entity_type`: exactly 2 values. Safe.
- NOT tracking `investor_id` or `startup_id` as labels.

---

### 4.4 Investor Agent / Chatbot (4 metrics)

| Metric                                | Type      | Labels                                                    | Description                         | Where                                                          |
| ------------------------------------- | --------- | --------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------- |
| `aisep_agent_stream_requests_total`   | Counter   | `outcome` (`success`, `error`, `timeout`, `out_of_scope`) | SSE stream session outcomes         | `investor_agent/api/router.py` → `event_generator`             |
| `aisep_agent_stream_duration_seconds` | Histogram | —                                                         | Full stream session wall-clock time | `investor_agent/api/router.py` → wrap `event_generator`        |
| `aisep_agent_out_of_scope_total`      | Counter   | —                                                         | Queries rejected by scope guard     | `investor_agent/api/router.py` → when intent == `out_of_scope` |
| `aisep_agent_stream_errors_total`     | Counter   | `error_type` (`timeout`, `internal`)                      | Stream-level errors                 | `investor_agent/api/router.py` → except blocks                 |

**Notes**:

- Stream duration histogram uses same buckets as HTTP but with higher upper bounds: `[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 240.0]`
- No `thread_id` or `query` labels.

---

## 5. P1 Metrics — Implement if time allows

### 5.1 External Provider Tracking (3 metrics)

| Metric                                 | Type      | Labels                                                                                                     | Description                              | Where                                                   |
| -------------------------------------- | --------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------- |
| `aisep_provider_calls_total`           | Counter   | `provider` (`gemini`, `tavily_search`, `tavily_extract`), `outcome` (`success`, `error`, `quota_exceeded`) | External API call count                  | `gemini_client.py`, `search_node.py`, `extract_node.py` |
| `aisep_provider_call_duration_seconds` | Histogram | `provider`                                                                                                 | External API latency                     | Same files                                              |
| `aisep_provider_retries_total`         | Counter   | `provider`                                                                                                 | Retry attempts (Gemini Tenacity retries) | `gemini_client.py`                                      |

**Cardinality**: `provider` has 3 values, `outcome` has 3 values → max 9 series per counter. Safe.

### 5.2 Recommendation Detail (3 metrics)

| Metric                            | Type      | Labels                                    | Description                            | Where                      |
| --------------------------------- | --------- | ----------------------------------------- | -------------------------------------- | -------------------------- |
| `aisep_reco_candidate_pool_size`  | Histogram | —                                         | Number of candidates after hard filter | `recommendation_engine.py` |
| `aisep_reco_rerank_applied_total` | Counter   | `outcome` (`success`, `skipped`, `error`) | LLM rerank call outcome                | `llm_reranker.py`          |
| `aisep_reco_warning_flags_total`  | Counter   | `flag`                                    | Warning flag frequency                 | `recommendation_engine.py` |

**Cardinality warning**: `flag` label has ~5 known values (`ai_evaluation_missing`, `verification_weak`, `startup_ai_embedding_missing`, `ai_score_high_importance_missing`, `hard_filter_applied`). Safe, but monitor if new flags are added.

### 5.3 Investor Agent Detail (3 metrics)

| Metric                                    | Type      | Labels | Description                                   | Where                          |
| ----------------------------------------- | --------- | ------ | --------------------------------------------- | ------------------------------ |
| `aisep_agent_coverage_insufficient_total` | Counter   | —      | Responses with insufficient evidence coverage | `investor_agent/api/router.py` |
| `aisep_agent_verified_claims_count`       | Histogram | —      | Claims per response                           | `investor_agent/api/router.py` |
| `aisep_agent_references_count`            | Histogram | —      | References per response                       | `investor_agent/api/router.py` |

---

## 6. P2 Metrics — Nice to have, skip for now

| Metric idea                                       | Why defer                                                                                                       |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `aisep_eval_queue_lag_seconds`                    | Requires tracking enqueue timestamp vs processing start. Possible but needs DB schema change or event sourcing. |
| `aisep_eval_end_to_end_duration_seconds`          | submit → completed. Needs correlation across API + worker. Complex.                                             |
| `aisep_agent_followup_type_total` by type         | 5+ followup types. Useful but not essential for demo.                                                           |
| `aisep_agent_search_decision_total` by decision   | 3 values (`fresh_search`, `reuse_plus_search`, `reuse_only`). Interesting but not visible to end-user.          |
| `aisep_agent_graph_node_duration_seconds` by node | 9 nodes × histogram = high cardinality. Log-level tracing is enough.                                            |
| Per-endpoint rate-limit rejection counter         | Rate limiter already returns 429. HTTP metrics capture this via `status_code=429`.                              |
| Celery queue depth gauge                          | Requires Redis `LLEN` polling or Flower. Separate infrastructure.                                               |
| Webhook delivery metrics                          | Low-frequency events. Logs are sufficient.                                                                      |

---

## 7. Label Cardinality Summary

| Label                     | Max values                                | Risk       |
| ------------------------- | ----------------------------------------- | ---------- |
| `method`                  | 3 (GET, POST, OPTIONS)                    | ✅ Safe    |
| `endpoint`                | ~12 route patterns                        | ✅ Safe    |
| `status_code`             | ~6 (200, 201, 404, 409, 422, 500)         | ✅ Safe    |
| `terminal_status`         | 2 (completed, failed)                     | ✅ Safe    |
| `doc_type`                | 2 (pitch_deck, business_plan)             | ✅ Safe    |
| `entity_type`             | 2 (startup, investor)                     | ✅ Safe    |
| `provider`                | 3 (gemini, tavily_search, tavily_extract) | ✅ Safe    |
| `outcome`                 | 3-4 per metric                            | ✅ Safe    |
| `error_type`              | 4 per metric                              | ✅ Safe    |
| `flag` (P1 warning flags) | 5 known values                            | ⚠️ Monitor |

**Total estimated time-series (P0)**: ~80–120 series. Well within Prometheus comfort zone.

---

## 8. Minimal Dashboards for Demo

### Dashboard 1: System Overview

- **Row 1**: HTTP request rate (req/s), error rate (%), p50/p95 latency
- **Row 2**: Active Celery tasks gauge (from process metrics), provider call rate

### Dashboard 2: AI Evaluation Pipeline

- **Row 1**: Submit rate vs completion rate (counter rate)
- **Row 2**: Worker task duration heatmap
- **Row 3**: Documents processed by type, failure rate

### Dashboard 3: AI Recommendation

- **Row 1**: Recommendation request rate, reindex rate
- **Row 2**: Query latency p50/p95/p99
- **Row 3**: Candidate pool size distribution (P1)

### Dashboard 4: Investor Agent

- **Row 1**: Stream request rate, out-of-scope rate
- **Row 2**: Stream duration heatmap
- **Row 3**: Error rate by type

---

## 9. Demo Slides — What to show the committee

### Slide: "Operational Metrics"

Show 4 key numbers from a real demo session:

1. **Evaluation throughput**: "23 evaluation runs completed, 2 failed (8.7% error rate)" → `aisep_eval_runs_completed_total`
2. **Recommendation latency**: "p95 recommendation query: 1.8s including LLM rerank" → `aisep_reco_request_duration_seconds`
3. **Chatbot reliability**: "47 chat sessions, 2 out-of-scope, 0 errors" → `aisep_agent_stream_requests_total`
4. **Provider health**: "Gemini: 156 calls, 3 retries, 0 quota errors" → `aisep_provider_calls_total` (P1)

### Slide: "Real-time Dashboard"

Screenshot of Grafana dashboard with:

- Request rate graph (shows traffic during demo)
- Latency percentile graph (shows system performs within SLA)
- Error rate graph (shows reliability)

### Narrative:

> "Our system exposes 15+ Prometheus metrics covering all 3 AI modules.
> This gives us real-time visibility into throughput, latency, and errors
> across the evaluation pipeline, recommendation engine, and research agent.
> In production, these metrics would feed alerting rules for SLA violations."

---

## 10. Implementation Order

| Step | What                                                                                                   | Est. effort | Depends on |
| ---- | ------------------------------------------------------------------------------------------------------ | ----------- | ---------- |
| 1    | Create `src/shared/observability/metrics.py` — define all P0 metric objects                            | 15 min      | —          |
| 2    | Create `src/shared/observability/http_middleware.py` — request latency/count middleware                | 15 min      | Step 1     |
| 3    | Mount `/metrics` endpoint + middleware in `src/main.py`                                                | 5 min       | Steps 1–2  |
| 4    | Instrument `evaluation/workers/tasks.py` — worker task metrics                                         | 20 min      | Step 1     |
| 5    | Instrument `evaluation/api/router.py` — submit counter                                                 | 5 min       | Step 1     |
| 6    | Instrument `recommendation/api/router.py` — 4 reco metrics                                             | 15 min      | Step 1     |
| 7    | Instrument `investor_agent/api/router.py` — 4 agent metrics                                            | 20 min      | Step 1     |
| 8    | Add `prometheus-client` to `requirements.txt`                                                          | 1 min       | —          |
| 9    | Run tests → confirm no regressions                                                                     | 5 min       | Steps 1–7  |
| 10   | (P1) Create `provider_tracker.py` + instrument `gemini_client.py`, `search_node.py`, `extract_node.py` | 30 min      | Step 1     |
| 11   | (P1) Add reco detail metrics in `recommendation_engine.py`, `llm_reranker.py`                          | 15 min      | Step 1     |
| 12   | (P1) Add agent detail metrics in `investor_agent/api/router.py`                                        | 10 min      | Step 1     |

**Total P0 estimate**: ~1.5 hours
**Total P0 + P1 estimate**: ~2.5 hours
