"""
Central Prometheus metric registry for the AISEP AI backend.

Every Counter / Histogram lives here so that import-order never matters
and metric names stay consistent across modules.

Naming convention:  aisep_<module>_<metric_name>_<unit>
"""

from prometheus_client import Counter, Histogram

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P0 — Cross-cutting  HTTP metrics                              ║
# ╚══════════════════════════════════════════════════════════════════╝

HTTP_REQUESTS_TOTAL = Counter(
    "aisep_http_requests_total",
    "Total HTTP requests handled",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "aisep_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P0 — Evaluation module                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

EVAL_SUBMISSIONS_TOTAL = Counter(
    "aisep_eval_submissions_total",
    "Total evaluation submissions (accepted / error)",
    ["status"],
)

EVAL_RUNS_COMPLETED_TOTAL = Counter(
    "aisep_eval_runs_completed_total",
    "Completed evaluation runs by terminal status",
    ["terminal_status"],
)

EVAL_WORKER_TASK_DURATION = Histogram(
    "aisep_eval_worker_task_duration_seconds",
    "Celery worker task wall-clock duration",
    ["terminal_status"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

EVAL_DOCUMENTS_PROCESSED = Counter(
    "aisep_eval_documents_processed_total",
    "Documents processed per type and outcome",
    ["doc_type", "outcome"],
)

EVAL_WORKER_FAILURES_TOTAL = Counter(
    "aisep_eval_worker_failures_total",
    "Worker-level failures by error category",
    ["error_type"],
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P0 — Recommendation module                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

RECO_REQUESTS_TOTAL = Counter(
    "aisep_reco_requests_total",
    "Total recommendation query requests",
    ["endpoint"],
)

RECO_REQUEST_DURATION = Histogram(
    "aisep_reco_request_duration_seconds",
    "Recommendation query latency",
    ["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

RECO_REINDEX_TOTAL = Counter(
    "aisep_reco_reindex_total",
    "Reindex operations by entity type and outcome",
    ["entity_type", "outcome"],
)

RECO_REINDEX_DURATION = Histogram(
    "aisep_reco_reindex_duration_seconds",
    "Reindex operation duration",
    ["entity_type"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30),
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P0 — Investor Agent module                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

AGENT_STREAM_REQUESTS_TOTAL = Counter(
    "aisep_agent_stream_requests_total",
    "Total SSE stream sessions by outcome",
    ["outcome"],
)

AGENT_STREAM_DURATION = Histogram(
    "aisep_agent_stream_duration_seconds",
    "Wall-clock duration of SSE stream sessions",
    buckets=(1, 5, 10, 30, 60, 120, 240),
)

AGENT_OUT_OF_SCOPE_TOTAL = Counter(
    "aisep_agent_out_of_scope_total",
    "Queries rejected by scope guard",
)

AGENT_STREAM_ERRORS_TOTAL = Counter(
    "aisep_agent_stream_errors_total",
    "Stream errors by type (timeout / internal)",
    ["error_type"],
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P1 — Provider tracking                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

PROVIDER_CALLS_TOTAL = Counter(
    "aisep_provider_calls_total",
    "External provider API calls",
    ["provider", "outcome"],
)

PROVIDER_CALL_DURATION = Histogram(
    "aisep_provider_call_duration_seconds",
    "External provider call latency",
    ["provider"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30),
)

PROVIDER_RETRIES_TOTAL = Counter(
    "aisep_provider_retries_total",
    "Provider call retries triggered",
    ["provider"],
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P1 — Recommendation detail                                    ║
# ╚══════════════════════════════════════════════════════════════════╝

RECO_CANDIDATE_POOL_SIZE = Histogram(
    "aisep_reco_candidate_pool_size",
    "Number of candidates returned by vector search",
    buckets=(0, 1, 2, 3, 5, 8, 10, 15, 20, 50),
)

RECO_RERANK_APPLIED_TOTAL = Counter(
    "aisep_reco_rerank_applied_total",
    "LLM rerank invocations by outcome",
    ["outcome"],
)

RECO_WARNING_FLAGS_TOTAL = Counter(
    "aisep_reco_warning_flags_total",
    "Warning flags raised in recommendation items",
    ["flag"],
)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  P1 — Agent detail                                             ║
# ╚══════════════════════════════════════════════════════════════════╝

AGENT_COVERAGE_INSUFFICIENT = Counter(
    "aisep_agent_coverage_insufficient_total",
    "Responses flagged as insufficient coverage",
)

AGENT_VERIFIED_CLAIMS_COUNT = Histogram(
    "aisep_agent_verified_claims_count",
    "Number of verified claims per response",
    buckets=(0, 1, 2, 3, 5, 8, 12, 20),
)

AGENT_REFERENCES_COUNT = Histogram(
    "aisep_agent_references_count",
    "Number of references per response",
    buckets=(0, 1, 2, 3, 5, 8, 12),
)
