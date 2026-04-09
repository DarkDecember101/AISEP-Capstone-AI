# Async Jobs and Workers

## Current async infrastructure

### Celery app

- File: `src/celery_app.py`
- Broker: `settings.CELERY_BROKER_URL`
- Result backend: `settings.CELERY_RESULT_BACKEND`
- Key config:
  - `task_track_started=True`
  - `broker_connection_retry_on_startup=True`
  - `task_acks_late=True`
  - `worker_prefetch_multiplier=1`
  - Windows override: `worker_pool=solo`, `worker_concurrency=1`

### Task discovery

- Autodiscover package: `src.modules.evaluation.workers`
- Registered task name: `evaluation.process_run`

---

## Evaluation task contract

Task: `process_evaluation_run_task(self, evaluation_run_id: int)`
File: `src/modules/evaluation/workers/tasks.py`

### Behavior

1. Open DB session
2. Load `EvaluationRun` by id
3. Skip if run not found
4. Idempotency guard: only process statuses in `{queued, retry}`
5. Set run status `processing`, write log
6. Process all documents (`process_document`)
7. Aggregate run (`aggregate_evaluation_run`)
8. Return final run status
9. Close DB session

### Retry behavior

- `max_retries=3`
- `retry_backoff=True`, `retry_backoff_max=300`
- On transient error before max retries:
  - set DB run status to `retry`
  - persist `failure_reason` with retry count
  - call `self.retry(...)`
- On final exhaustion:
  - set DB run status to `failed`
  - persist `failure_reason`

### Source of truth

- **DB `EvaluationRun.status` is source of truth**, not Celery result backend.
- .NET should poll API/DB status endpoint, not rely on `AsyncResult` state.

---

## Deprecated fallback worker

- File: `src/worker.py`
- Marked explicitly as deprecated dev-only polling loop.
- Production path should use Celery worker.

---

## .NET polling guidance

After submit:

1. store `evaluation_run_id`
2. poll `GET /api/v1/evaluations/{id}`
3. terminal statuses to handle now:
   - `completed`
   - `failed`
4. optional retry behavior in orchestration:
   - if `failed`, inspect `failure_reason` and allow re-submit

---

## Not found in code

- No separate task queue for recommendation module
- No separate async worker for investor_agent module
