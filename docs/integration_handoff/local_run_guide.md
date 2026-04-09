# Local Run Guide (Windows / PowerShell)

## 1) Create environment and install dependencies

```powershell
cd c:\Users\LENOVO\Desktop\AISEP_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2) Configure `.env`

Create `.env` in repo root with at least:

```dotenv
DATABASE_URL=sqlite:///./aisep_ai.db
GEMINI_API_KEY=YOUR_KEY
TAVILY_API_KEY=YOUR_KEY
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
AISEP_INTERNAL_TOKEN=YOUR_INTERNAL_TOKEN
```

## 3) Start Redis

Run a Redis instance reachable at `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`.

## 4) Start API

In terminal A:

```powershell
cd c:\Users\LENOVO\Desktop\AISEP_AI
.\.venv\Scripts\Activate.ps1
python -m uvicorn src.main:app --reload
```

## 5) Start Celery worker

In terminal B:

```powershell
cd c:\Users\LENOVO\Desktop\AISEP_AI
.\.venv\Scripts\Activate.ps1
celery -A src.celery_app:celery_app worker -l INFO
```

Notes:

- On Windows, `src/celery_app.py` forces `solo` pool for compatibility.
- Legacy polling worker `python src\worker.py` exists but is deprecated for production path.

## 6) Quick smoke checks

### Health

```powershell
Invoke-RestMethod -Method GET -Uri http://127.0.0.1:8000/health
```

### Submit evaluation (async)

```powershell
$body = @{
  startup_id = "startup_demo"
  documents = @(
    @{
      document_id = "doc_1"
      document_type = "pitch_deck"
      file_url_or_path = "C:\path\to\your\file.pdf"
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/api/v1/evaluations/ -ContentType "application/json" -Body $body
```

### Poll evaluation status

```powershell
Invoke-RestMethod -Method GET -Uri http://127.0.0.1:8000/api/v1/evaluations/{id}
```

### Fetch canonical report when completed

```powershell
Invoke-RestMethod -Method GET -Uri http://127.0.0.1:8000/api/v1/evaluations/{id}/report
```

### Investor-agent chat stream (SSE)

Use a client that supports SSE and parse `data:` lines from:

- `POST http://127.0.0.1:8000/api/v1/investor-agent/chat/stream`

## 7) .NET integration sanity sequence

1. Reindex recommendation documents through internal endpoints with `X-Internal-Token`
2. Call recommendation read endpoint and verify sorted matches
3. Submit evaluation and poll until terminal status
4. Consume investor-agent stream and handle `[DONE]`
