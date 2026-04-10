# Prometheus Instrumentation Map — File-by-File

This document maps **exactly which files** to create or modify, **what code to add**, and **which metrics** each file owns.

---

## FILES TO CREATE

### 1. `src/shared/observability/__init__.py`

- Empty `__init__.py`

### 2. `src/shared/observability/metrics.py` — Metric Registry (P0)

**Purpose**: Single source of truth for all Prometheus metric objects.

**Contains**:

```
# HTTP cross-cutting
HTTP_REQUESTS_TOTAL          = Counter("aisep_http_requests_total", "...", ["method", "endpoint", "status_code"])
HTTP_REQUEST_DURATION         = Histogram("aisep_http_request_duration_seconds", "...", ["method", "endpoint"], buckets=[...])

# Evaluation
EVAL_SUBMISSIONS_TOTAL        = Counter("aisep_eval_submissions_total", "...", ["status"])
EVAL_RUNS_COMPLETED_TOTAL     = Counter("aisep_eval_runs_completed_total", "...", ["terminal_status"])
EVAL_WORKER_TASK_DURATION     = Histogram("aisep_eval_worker_task_duration_seconds", "...", ["terminal_status"])
EVAL_DOCUMENTS_PROCESSED      = Counter("aisep_eval_documents_processed_total", "...", ["doc_type", "outcome"])
EVAL_WORKER_FAILURES_TOTAL    = Counter("aisep_eval_worker_failures_total", "...", ["error_type"])

# Recommendation
RECO_REQUESTS_TOTAL           = Counter("aisep_reco_requests_total", "...", ["endpoint"])
RECO_REQUEST_DURATION         = Histogram("aisep_reco_request_duration_seconds", "...", ["endpoint"])
RECO_REINDEX_TOTAL            = Counter("aisep_reco_reindex_total", "...", ["entity_type", "outcome"])
RECO_REINDEX_DURATION         = Histogram("aisep_reco_reindex_duration_seconds", "...", ["entity_type"])

# Investor Agent
AGENT_STREAM_REQUESTS_TOTAL   = Counter("aisep_agent_stream_requests_total", "...", ["outcome"])
AGENT_STREAM_DURATION         = Histogram("aisep_agent_stream_duration_seconds", "...", buckets=[1,5,10,30,60,120,240])
AGENT_OUT_OF_SCOPE_TOTAL      = Counter("aisep_agent_out_of_scope_total", "...")
AGENT_STREAM_ERRORS_TOTAL     = Counter("aisep_agent_stream_errors_total", "...", ["error_type"])
```

**P1 additions** (add later in same file):

```
# Provider tracking
PROVIDER_CALLS_TOTAL          = Counter("aisep_provider_calls_total", "...", ["provider", "outcome"])
PROVIDER_CALL_DURATION        = Histogram("aisep_provider_call_duration_seconds", "...", ["provider"])
PROVIDER_RETRIES_TOTAL        = Counter("aisep_provider_retries_total", "...", ["provider"])

# Recommendation detail
RECO_CANDIDATE_POOL_SIZE      = Histogram("aisep_reco_candidate_pool_size", "...", buckets=[0,1,2,3,5,8,10,15,20,50])
RECO_RERANK_APPLIED_TOTAL     = Counter("aisep_reco_rerank_applied_total", "...", ["outcome"])
RECO_WARNING_FLAGS_TOTAL      = Counter("aisep_reco_warning_flags_total", "...", ["flag"])

# Agent detail
AGENT_COVERAGE_INSUFFICIENT   = Counter("aisep_agent_coverage_insufficient_total", "...")
AGENT_VERIFIED_CLAIMS_COUNT   = Histogram("aisep_agent_verified_claims_count", "...", buckets=[0,1,2,3,5,8,12,20])
AGENT_REFERENCES_COUNT        = Histogram("aisep_agent_references_count", "...", buckets=[0,1,2,3,5,8,12])
```

---

### 3. `src/shared/observability/http_middleware.py` — HTTP Metrics Middleware (P0)

**Purpose**: Measure every HTTP request's duration and count by status.

**Logic**:

1. Extract route pattern from `request.scope["route"]` (FastAPI sets this)
2. Start timer before `call_next(request)`
3. After response, record:
   - `HTTP_REQUESTS_TOTAL.labels(method, endpoint, status_code).inc()`
   - `HTTP_REQUEST_DURATION.labels(method, endpoint).observe(elapsed)`
4. Normalize `endpoint` to route template (e.g. `/api/v1/evaluations/{id}`)
5. Fallback: if no route matched, use `"unknown"` → keeps cardinality bounded

**Implementation**: Starlette `BaseHTTPMiddleware` subclass, ~25 lines.

---

### 4. `src/shared/observability/provider_tracker.py` — Provider Call Tracker (P1)

**Purpose**: Context manager / decorator to measure external provider calls.

**Usage**:

```python
with track_provider("gemini"):
    result = client.generate_structured(...)
```

**Logic**:

1. Start timer
2. yield / call inner
3. On success: `PROVIDER_CALLS_TOTAL.labels(provider, "success").inc()` + observe duration
4. On `GeminiQuotaExceededError`: label `"quota_exceeded"`
5. On other exception: label `"error"`

---

## FILES TO MODIFY

### 5. `src/main.py` — Mount metrics + middleware (P0)

**Changes**:

1. Import `make_asgi_app` from `prometheus_client`
2. Import `PrometheusHTTPMiddleware` from `src.shared.observability.http_middleware`
3. Add middleware: `app.add_middleware(PrometheusHTTPMiddleware)` (after CorrelationIdMiddleware)
4. Mount metrics endpoint: `app.mount("/metrics", make_asgi_app())`

**Lines affected**: ~4 new imports + 2 lines in app setup.

---

### 6. `src/modules/evaluation/api/router.py` — Submission counter (P0)

**Changes in `submit_evaluation_endpoint()`**:

```python
from src.shared.observability.metrics import EVAL_SUBMISSIONS_TOTAL

# After successful submit:
EVAL_SUBMISSIONS_TOTAL.labels(status="accepted").inc()

# In except block:
EVAL_SUBMISSIONS_TOTAL.labels(status="error").inc()
```

**Lines affected**: +1 import, +2 lines in function body.

---

### 7. `src/modules/evaluation/workers/tasks.py` — Worker metrics (P0)

**Changes in `process_evaluation_run_task()`**:

```python
from src.shared.observability.metrics import (
    EVAL_RUNS_COMPLETED_TOTAL,
    EVAL_WORKER_TASK_DURATION,
    EVAL_DOCUMENTS_PROCESSED,
    EVAL_WORKER_FAILURES_TOTAL,
)
import time

# At start of task:
_start = time.monotonic()

# Inside document processing loop, after each doc completes:
EVAL_DOCUMENTS_PROCESSED.labels(doc_type=doc.document_type, outcome="completed").inc()

# Inside document processing loop, on doc failure:
EVAL_DOCUMENTS_PROCESSED.labels(doc_type=doc.document_type, outcome="failed").inc()

# At terminal success (status = "completed"):
EVAL_RUNS_COMPLETED_TOTAL.labels(terminal_status="completed").inc()
EVAL_WORKER_TASK_DURATION.labels(terminal_status="completed").observe(time.monotonic() - _start)

# At terminal failure (status = "failed"):
EVAL_RUNS_COMPLETED_TOTAL.labels(terminal_status="failed").inc()
EVAL_WORKER_TASK_DURATION.labels(terminal_status="failed").observe(time.monotonic() - _start)
EVAL_WORKER_FAILURES_TOTAL.labels(error_type="aggregate_error").inc()

# On LLM errors inside document processing:
EVAL_WORKER_FAILURES_TOTAL.labels(error_type="llm_error").inc()

# On extract errors:
EVAL_WORKER_FAILURES_TOTAL.labels(error_type="extract_error").inc()
```

**Lines affected**: +1 import block, +12–15 metric calls scattered through existing try/except blocks.

**NOTE**: `prometheus_client` works in Celery workers because it uses in-process `CollectorRegistry`. Each worker exposes its own `/metrics` (or use `multiprocess_mode` if needed). For capstone demo with a single worker, default mode is fine.

---

### 8. `src/modules/recommendation/api/router.py` — Recommendation metrics (P0)

**Changes**:

```python
from src.shared.observability.metrics import (
    RECO_REQUESTS_TOTAL, RECO_REQUEST_DURATION,
    RECO_REINDEX_TOTAL, RECO_REINDEX_DURATION,
)
import time
```

**In `reindex_startup()`**:

```python
_start = time.monotonic()
try:
    document = engine.reindex_startup(startup_id, request)
    RECO_REINDEX_TOTAL.labels(entity_type="startup", outcome="success").inc()
except:
    RECO_REINDEX_TOTAL.labels(entity_type="startup", outcome="error").inc()
    raise
finally:
    RECO_REINDEX_DURATION.labels(entity_type="startup").observe(time.monotonic() - _start)
```

**In `reindex_investor()`**: Same pattern with `entity_type="investor"`.

**In `get_startup_recommendations()`**:

```python
_start = time.monotonic()
try:
    ...existing code...
    RECO_REQUESTS_TOTAL.labels(endpoint="list").inc()
finally:
    RECO_REQUEST_DURATION.labels(endpoint="list").observe(time.monotonic() - _start)
```

**In `get_recommendation_explanation()`**: Same with `endpoint="explanation"`.

**Lines affected**: +1 import block, +20 lines of metric calls in 4 endpoint functions.

---

### 9. `src/modules/investor_agent/api/router.py` — Agent stream metrics (P0)

**Changes in `event_generator()` inside `chat_research_stream()`**:

```python
from src.shared.observability.metrics import (
    AGENT_STREAM_REQUESTS_TOTAL, AGENT_STREAM_DURATION,
    AGENT_OUT_OF_SCOPE_TOTAL, AGENT_STREAM_ERRORS_TOTAL,
)
import time
```

**Wrap event_generator with timing**:

```python
_stream_start = time.monotonic()
_stream_outcome = "success"  # default

# When out_of_scope detected (line ~212):
AGENT_OUT_OF_SCOPE_TOTAL.inc()
_stream_outcome = "out_of_scope"

# In TimeoutError except (line ~244):
AGENT_STREAM_ERRORS_TOTAL.labels(error_type="timeout").inc()
_stream_outcome = "timeout"

# In generic Exception except (line ~250):
AGENT_STREAM_ERRORS_TOTAL.labels(error_type="internal").inc()
_stream_outcome = "error"

# After "yield [DONE]" (line ~256), in a finally block:
AGENT_STREAM_REQUESTS_TOTAL.labels(outcome=_stream_outcome).inc()
AGENT_STREAM_DURATION.observe(time.monotonic() - _stream_start)
```

**Lines affected**: +1 import block, +10 metric calls in existing control flow.

---

### 10. `src/shared/providers/llm/gemini_client.py` — Provider metrics (P1)

**Changes in `generate_structured()` and `generate_structured_async()`**:

Wrap the main API call with `track_provider("gemini")` context manager (from `provider_tracker.py`).

**Lines affected**: +1 import, +3 lines per method (context manager wrap).

---

### 11. `src/modules/investor_agent/infrastructure/graph/nodes/search_node.py` — Tavily search metrics (P1)

**Changes in `run()`**:

Wrap `tavily_client.search()` with `track_provider("tavily_search")`.

**Lines affected**: +1 import, +3 lines.

---

### 12. `src/modules/investor_agent/infrastructure/graph/nodes/extract_node.py` — Tavily extract metrics (P1)

**Changes in `run()`**:

Wrap `tavily_client.extract()` with `track_provider("tavily_extract")`.

**Lines affected**: +1 import, +3 lines.

---

### 13. `src/modules/recommendation/application/services/recommendation_engine.py` — Reco detail metrics (P1)

**Changes in `get_recommendations()`**:

```python
RECO_CANDIDATE_POOL_SIZE.observe(len(scored_candidates))
```

**Changes in `_warnings_from_item()`**:

```python
for w in warnings:
    RECO_WARNING_FLAGS_TOTAL.labels(flag=w).inc()
```

**Lines affected**: +1 import, +4 lines.

---

### 14. `src/modules/recommendation/application/services/llm_reranker.py` — Rerank metrics (P1)

**Changes in `rerank()`**:

```python
# On success:
RECO_RERANK_APPLIED_TOTAL.labels(outcome="success").inc()

# On skip (no API key):
RECO_RERANK_APPLIED_TOTAL.labels(outcome="skipped").inc()

# On error:
RECO_RERANK_APPLIED_TOTAL.labels(outcome="error").inc()
```

**Lines affected**: +1 import, +3 lines.

---

### 15. `requirements.txt` — Add dependency (P0)

**Add**:

```
prometheus-client>=0.20.0
```

---

## SUMMARY TABLE

| File                                                      | Action | Priority | Metrics added                                                      |
| --------------------------------------------------------- | ------ | -------- | ------------------------------------------------------------------ |
| `src/shared/observability/__init__.py`                    | CREATE | P0       | —                                                                  |
| `src/shared/observability/metrics.py`                     | CREATE | P0       | All 15 P0 + 9 P1 metric objects                                    |
| `src/shared/observability/http_middleware.py`             | CREATE | P0       | `aisep_http_requests_total`, `aisep_http_request_duration_seconds` |
| `src/shared/observability/provider_tracker.py`            | CREATE | P1       | Helper for provider metrics                                        |
| `src/main.py`                                             | MODIFY | P0       | Mount `/metrics` + middleware                                      |
| `src/modules/evaluation/api/router.py`                    | MODIFY | P0       | `aisep_eval_submissions_total`                                     |
| `src/modules/evaluation/workers/tasks.py`                 | MODIFY | P0       | 4 eval worker metrics                                              |
| `src/modules/recommendation/api/router.py`                | MODIFY | P0       | 4 reco metrics                                                     |
| `src/modules/investor_agent/api/router.py`                | MODIFY | P0       | 4 agent metrics                                                    |
| `src/shared/providers/llm/gemini_client.py`               | MODIFY | P1       | Provider call tracking                                             |
| `src/modules/investor_agent/.../search_node.py`           | MODIFY | P1       | Tavily search tracking                                             |
| `src/modules/investor_agent/.../extract_node.py`          | MODIFY | P1       | Tavily extract tracking                                            |
| `src/modules/recommendation/.../recommendation_engine.py` | MODIFY | P1       | Candidate pool + warning flags                                     |
| `src/modules/recommendation/.../llm_reranker.py`          | MODIFY | P1       | Rerank outcome counter                                             |
| `requirements.txt`                                        | MODIFY | P0       | `prometheus-client`                                                |

**Total files to create**: 4
**Total files to modify**: 11 (P0: 5, P1: 6)
