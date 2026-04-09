# AISEP AI Service

FastAPI service providing AI-powered startup evaluation and investor recommendation for the AISEP platform.

## Modules

| Module           | Description                                                                                               |
| ---------------- | --------------------------------------------------------------------------------------------------------- |
| `evaluation`     | LLM-based pitch deck / business plan scoring via Gemini. Celery async workers, webhook delivery.          |
| `investor_agent` | Conversational research agent (LangGraph). Multi-turn memory, SEA fintech scope guard.                    |
| `recommendation` | Investor–startup matching engine. Structured scoring + semantic reranking, PostgreSQL + pgvector backend. |

## Prerequisites

- Python 3.10+
- PostgreSQL 17 with **pgvector** extension
- Redis (broker for Celery)
- Google Gemini API key

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in values (see [DEPLOYMENT.md](DEPLOYMENT.md) for all variables).

## Running Locally

**API server:**

```powershell
python -m uvicorn src.main:app --reload
```

Docs: http://localhost:8000/docs

**Celery worker** (separate terminal):

```powershell
celery -A src.celery_app:celery_app worker -l INFO
```

> `src/worker.py` is a deprecated polling fallback kept for dev-only use. Use Celery in all other environments.

## Running Tests

```powershell
.\.venv\Scripts\python.exe -m pytest src/tests/unit/ -v
```

220 unit tests, no external services required (all DB interactions use in-memory SQLite).

## API Reference

### Evaluation

| Method | Path                              | Description           |
| ------ | --------------------------------- | --------------------- |
| `POST` | `/api/v1/evaluations`             | Submit evaluation run |
| `GET`  | `/api/v1/evaluations/{id}`        | Poll run status       |
| `GET`  | `/api/v1/evaluations/{id}/report` | Fetch scored report   |

### Investor Agent

| Method | Path                                 | Description                          |
| ------ | ------------------------------------ | ------------------------------------ |
| `POST` | `/api/v1/investor-agent/chat/stream` | SSE stream: multi-turn research chat |

### Recommendations

| Method | Path                                                        | Description                              |
| ------ | ----------------------------------------------------------- | ---------------------------------------- |
| `POST` | `/api/v1/recommendations/index`                             | Index investor + startup profiles        |
| `GET`  | `/api/v1/recommendations/startups`                          | List ranked startup matches for investor |
| `GET`  | `/api/v1/recommendations/startups/{startup_id}/explanation` | Detailed match explanation               |

## Troubleshooting

- **Evaluation stuck in `queued`**: Celery worker not running — start with the command above.
- **`failed` run**: check `failure_reason` and per-document `summary` fields.
- **No Gemini response**: verify `GEMINI_API_KEY` in `.env`; service falls back to heuristic scoring.
- **PDF returns `No extractable text found`**: scanned/image-only PDF — OCR not enabled.
- **Recommendation returns empty**: run `POST /api/v1/recommendations/index` first to seed profiles.
