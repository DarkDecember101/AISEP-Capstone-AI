# Deployment Runbook — AISEP AI Service

## Prerequisites

| Dependency            | Version | Notes                                                                   |
| --------------------- | ------- | ----------------------------------------------------------------------- |
| Python                | 3.10+   | Use a virtual environment                                               |
| PostgreSQL            | 17      | Must have `pgvector` extension enabled                                  |
| Redis                 | 7+      | Broker + result backend for Celery; checkpoint store for investor-agent |
| Google Gemini API key | —       | Required for evaluation, recommendation rerank, investor-agent          |
| Tavily API key        | —       | Required for investor-agent web search nodes                            |

---

## Environment Variables

Copy `.env.example` to `.env` and update every value marked `REQUIRED`.

| Variable                 | Default                    | Notes                                                             |
| ------------------------ | -------------------------- | ----------------------------------------------------------------- |
| `DATABASE_URL`           | `sqlite:///./aisep_ai.db`  | **REQUIRED prod**: `postgresql+psycopg2://user:pass@host:5432/db` |
| `GEMINI_API_KEY`         | —                          | **REQUIRED**                                                      |
| `TAVILY_API_KEY`         | —                          | **REQUIRED** (investor-agent search)                              |
| `AISEP_INTERNAL_TOKEN`   | —                          | Shared secret for internal endpoints (reindex, etc.)              |
| `REQUIRE_INTERNAL_AUTH`  | `false`                    | Set `true` in production to enforce token validation              |
| `CELERY_BROKER_URL`      | `redis://localhost:6379/0` | Redis broker URL                                                  |
| `CELERY_RESULT_BACKEND`  | `redis://localhost:6379/1` | Redis result backend URL                                          |
| `CHECKPOINT_BACKEND`     | `memory`                   | Set `redis` in production for durable multi-turn memory           |
| `CHECKPOINT_REDIS_URL`   | `redis://localhost:6379/2` | Used when `CHECKPOINT_BACKEND=redis`                              |
| `CHECKPOINT_TTL_MINUTES` | `1440`                     | Conversation checkpoint retention (24 h)                          |
| `RECOMMENDATION_BACKEND` | `db`                       | Always `db` in production                                         |
| `WEBHOOK_CALLBACK_URL`   | _(empty)_                  | Evaluation event callback URL; empty disables                     |
| `WEBHOOK_SIGNING_SECRET` | _(empty)_                  | HMAC-SHA256 signing secret for webhook payloads                   |
| `WEBHOOK_MAX_RETRIES`    | `3`                        | Delivery retry attempts                                           |
| `LOG_LEVEL`              | `INFO`                     | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`                         |

---

## First-Time Setup

### 1. Install Dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure PostgreSQL

```sql
-- Run as superuser in psql
CREATE USER aisep_user WITH PASSWORD 'your_password';
CREATE DATABASE aisep_ai OWNER aisep_user;
\c aisep_ai
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Run Database Migrations

```powershell
alembic upgrade head
```

> Tables are also auto-created on FastAPI startup via `SQLModel.metadata.create_all()`, but Alembic is the canonical migration path.

### 4. (Optional) Migrate Legacy Filesystem Recommendation Data

If you have existing recommendation data under `storage/recommendations/`, run the one-time migration to import it into the DB:

```powershell
.\.venv\Scripts\python.exe -m src.modules.recommendation.scripts.migrate_json_to_db
```

The script is idempotent and safe to re-run.

---

## Starting Services

Start all three processes. Each requires the `.venv` to be activated and `.env` to be loaded.

### FastAPI Application

```powershell
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

For production, run behind a reverse proxy (nginx / Caddy) with TLS termination.

### Celery Worker

```powershell
celery -A src.celery_app:celery_app worker -l INFO
```

For production, run as a system service (systemd, Windows Service, or Docker).

### Redis

Standard Redis install. No special configuration required.

---

## Health Checks

| Endpoint            | Description                                   |
| ------------------- | --------------------------------------------- |
| `GET /health/live`  | Basic liveness: returns 200 if app is running |
| `GET /health/ready` | Readiness: checks DB + Redis connectivity     |

---

## Seeding Recommendation Data

Before the recommendation endpoints return results, investor and startup profiles must be indexed:

```http
POST /api/v1/recommendations/index
Authorization: X-Internal-Token: <AISEP_INTERNAL_TOKEN>
Content-Type: application/json

{
  "investors": [...],
  "startups": [...]
}
```

See `docs/integration_handoff/` for the full request schema consumed by the .NET backend.

---

## Updating Deployments

```powershell
git pull
pip install -r requirements.txt   # if requirements changed
alembic upgrade head               # if DB migrations added
# Restart uvicorn + celery worker
```

---

## Troubleshooting

| Symptom                                      | Likely Cause                                   | Fix                                                                         |
| -------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------- |
| Evaluations stay in `queued`                 | Celery worker not running                      | Start worker; check Redis connection                                        |
| `GET /health/ready` → 503                    | DB or Redis unreachable                        | Check `DATABASE_URL` and `CELERY_BROKER_URL`                                |
| Investor-agent loses memory between requests | `CHECKPOINT_BACKEND=memory`, process restarted | Set `CHECKPOINT_BACKEND=redis`                                              |
| Recommendation returns empty list            | No profiles indexed                            | POST to `/api/v1/recommendations/index`                                     |
| Webhook not delivered                        | `WEBHOOK_CALLBACK_URL` empty or wrong          | Set correct URL in `.env`; check `WEBHOOK_VERIFY_SSL` for self-signed certs |
