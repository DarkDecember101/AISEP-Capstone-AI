# AISEP AI Master Codebase Guide

## 1. Muc tieu cua tai lieu

Tai lieu nay duoc viet de giup ban nam va doc nhanh toan bo `src/` trong 2 ngay.
No khong thay the source code, ma dong vai tro:

- ban do kien truc tong the
- reading order hop ly
- giai thich request di qua file nao, state nao, DB nao
- danh dau cac diem de bi roi: retry, fallback, merge, SSE, checkpoint, scoring

Tai lieu nay duoc tong hop tu code hien tai trong repo, khong dua tren assumption cu.

## 2. Service nay dang lam gi?

Repo nay la mot FastAPI service cung cap 3 capability AI:

1. `evaluation`
   Cham pitch deck / business plan, chay async qua Celery, luu ket qua vao DB, co the merge 2 nguon.
2. `recommendation`
   Match investor va startup, tinh diem bang hard filter + structured scoring + semantic scoring + LLM rerank nhe.
3. `investor_agent`
   Chatbot research cho nha dau tu, chay theo LangGraph, co memory theo `thread_id`, stream qua SSE.

Ngoai 3 module tren, repo con co mot lop `shared` dung chung cho:

- settings / env
- DB engine + models
- auth noi bo
- rate limiting
- correlation id
- error envelope
- tracing
- webhook callback
- health/readiness
- LangGraph checkpoint backend

## 3. Entry point va boot sequence

File vao he thong:

- [src/main.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/main.py:1)
- [src/celery_app.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/celery_app.py:1)
- [src/worker.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/worker.py:1)

### 3.1 `src/main.py`

`main.py` tao `FastAPI app`, dang ky:

- CORS middleware
- `CorrelationIdMiddleware`
- global error handlers
- health router
- evaluation router
- investor agent router
- recommendation router

Trong startup hook:

1. `init_db()`
2. `init_tracing()`
3. `setup_checkpointer()`

Y nghia:

- DB table duoc tao luc app start
- OpenTelemetry duoc bootstrap neu bat flag
- LangGraph Redis checkpoint duoc tao index neu dung Redis saver

### 3.2 `src/celery_app.py`

Day la config Celery cho module `evaluation`.

- broker: Redis
- backend: Redis
- auto-discover task trong `src.modules.evaluation.workers`
- Windows thi ep `worker_pool="solo"`

### 3.3 `src/worker.py`

Day la polling worker cu, da deprecated.
Chi nen xem de hieu lich su repo, khong phai runtime chinh.

## 4. Ban do thu muc `src/`

### 4.1 Top-level

- [src/main.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/main.py:1): API app
- [src/celery_app.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/celery_app.py:1): Celery config
- [src/worker.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/worker.py:1): polling fallback cu
- `src/tests/`: unit + integration tests

### 4.2 `src/shared`

Day la tang ha tang dung chung:

- [src/shared/config/settings.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/config/settings.py:1)
- [src/shared/persistence/db.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/persistence/db.py:1)
- `src/shared/persistence/models/*`
- [src/shared/auth.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/auth.py:1)
- `src/shared/error_response.py`
- `src/shared/correlation.py`
- `src/shared/health.py`
- [src/shared/rate_limit/limiter.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/rate_limit/limiter.py:1)
- [src/shared/checkpoint.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/checkpoint.py:1)
- `src/shared/webhook/delivery.py`
- [src/shared/providers/llm/gemini_client.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/providers/llm/gemini_client.py:1)

### 4.3 `src/modules/evaluation`

- `api/`: endpoint submit, status, report
- `application/use_cases/`: submit, process_document, aggregate, merge
- `application/services/`: scorer, report_validity, excerpt localization, warning sanitizer, BP text reduction
- `application/dto/`: request/response schema, canonical schema, pipeline schema
- `infrastructure/parsers/`: PDF parser
- `infrastructure/prompts/` + `prompts/`: prompt loading va prompt packs
- `workers/tasks.py`: Celery task chinh
- `domain/scoring_policy.py`: policy / weight / overall score helper

### 4.4 `src/modules/recommendation`

- `api/router.py`: reindex + read endpoints
- `application/dto/recommendation_schema.py`: investor/startup/run/result models
- `application/services/`: engine, scoring, embedding, reranker, reason renderer
- `infrastructure/repositories/`: DB backend va filesystem legacy backend
- `scripts/migrate_json_to_db.py`: migrate storage cu sang DB

### 4.5 `src/modules/investor_agent`

- `api/router.py`: endpoint `/chat/stream`
- `application/dto/state.py`: graph state contract
- `application/services/`: `scope_guard.py`, `final_assembler.py`
- `infrastructure/graph/builder.py`: graph wiring
- `infrastructure/graph/nodes/*`: 8 node cua pipeline research

## 5. Shared infrastructure can nam truoc

Neu ban chi co it thoi gian, hay doc `shared` truoc vi 3 module deu phu thuoc vao no.

### 5.1 Settings

File: [src/shared/config/settings.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/config/settings.py:1)

Settings gom cac nhom lon:

- DB: `DATABASE_URL`
- Vertex/Gemini: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
- Tavily: `TAVILY_API_KEY`
- Celery/Redis
- checkpoint backend cho investor agent
- recommendation backend
- webhook
- tracing
- rate limiting
- TLS/CORS
- investor agent performance flags
- document download auth headers

Nhin file nay ban se biet he thong co bao nhieu "feature flag" va duong runtime nao la production path.

### 5.2 DB va models

Files:

- [src/shared/persistence/db.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/persistence/db.py:1)
- [src/shared/persistence/models/evaluation_models.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/persistence/models/evaluation_models.py:1)
- [src/shared/persistence/models/recommendation_models.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/persistence/models/recommendation_models.py:1)
- [src/shared/persistence/models/webhook_models.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/persistence/models/webhook_models.py:1)

DB su dung `SQLModel`.

`db.py`:

- tao engine tu `DATABASE_URL`
- neu Postgres thi bat `pool_pre_ping`, `pool_recycle`
- neu co `pgvector` thi register vector type
- `init_db()` import tat ca model va `create_all`

Production y tuong la Postgres + pgvector, con unit test thuong chay SQLite in-memory.

### 5.3 Auth noi bo

File: [src/shared/auth.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/auth.py:1)

`require_internal_auth()`:

- neu `REQUIRE_INTERNAL_AUTH=false` thi pass
- neu `true` thi bat `X-Internal-Token`
- compare voi `AISEP_INTERNAL_TOKEN`

Code hien tai dang gan dependency nay cho:

- recommendation reindex endpoints
- investor agent stream endpoint

### 5.4 Error envelope

File: `src/shared/error_response.py`

Tat ca endpoint dung `APIError` de tra ve shape on dinh:

- `code`
- `message`
- `detail`
- `retryable`
- `correlation_id`

Day la mot diem rat quan trong khi tich hop voi .NET/FE.

### 5.5 Correlation ID

File: `src/shared/correlation.py`

Middleware nay:

- doc/generate `X-Correlation-Id`
- gan vao context de log theo request

Khi debug he thong, correlation id la chi tiet can luu dau tien.

### 5.6 Rate limiting

File: [src/shared/rate_limit/limiter.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/rate_limit/limiter.py:1)

Dung token bucket in-memory theo `client_ip`.
No don gian, hop local/single instance, nhung chua phai distributed limiter.

### 5.7 Checkpointer

File: [src/shared/checkpoint.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/checkpoint.py:1)

Investor agent dung LangGraph checkpointer:

- `MemorySaver` cho local dev
- `AsyncRedisSaver` cho durable shared memory

Day la key de hieu tai sao `thread_id` giu duoc memory.

### 5.8 Gemini / Vertex wrapper

File: [src/shared/providers/llm/gemini_client.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/providers/llm/gemini_client.py:1)

Day la mot file rat quan trong.

No lam:

- goi Vertex AI Gemini
- build multimodal contents
- parse structured JSON
- retry voi API/transport/parse errors
- phan loai `quota exceeded` vs transient error
- cung cap sync va async wrapper

Tac dong thuc te:

- gan nhu moi node LLM trong repo deu thong qua wrapper nay
- retry behavior cua toan he thong phu thuoc vao file nay

## 6. Evaluation module

### 6.1 Muc tieu nghiep vu

Nhan 1 hoac nhieu tai lieu startup:

- `pitch_deck`
- `business_plan`

Sau do:

1. parse noi dung
2. chay pipeline LLM nhieu buoc
3. scoring deterministic
4. assemble canonical report
5. aggregate len `EvaluationRun`
6. neu co ca 2 nguon thi co the merge

### 6.2 Files can doc theo thu tu

1. [src/modules/evaluation/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/api/router.py:1)
2. [src/modules/evaluation/application/use_cases/submit_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/submit_evaluation.py:1)
3. [src/modules/evaluation/workers/tasks.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/workers/tasks.py:1)
4. [src/modules/evaluation/application/use_cases/process_document.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/process_document.py:1)
5. [src/modules/evaluation/application/services/pipeline_llm_services.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/services/pipeline_llm_services.py:1)
6. `src/modules/evaluation/application/services/deterministic_scorer.py`
7. `src/modules/evaluation/application/services/report_validity.py`
8. [src/modules/evaluation/application/use_cases/aggregate_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/aggregate_evaluation.py:1)
9. [src/modules/evaluation/application/use_cases/merge_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/merge_evaluation.py:1)
10. `src/modules/evaluation/application/dto/canonical_schema.py`

### 6.3 API layer

File: [src/modules/evaluation/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/api/router.py:1)

Endpoint chinh:

- `GET /api/v1/evaluations/history`
- `POST /api/v1/evaluations/`
- `GET /api/v1/evaluations/{id}`
- `GET /api/v1/evaluations/{id}/report`
- `GET /api/v1/evaluations/{id}/report/source/{document_type}`

Router phan biet ro:

- status endpoint
- report endpoint
- merged report vs source-specific report

No cung normalize status va validate report truoc khi tra ra.

### 6.4 Data model

Bang chinh:

- `EvaluationRun`
- `EvaluationDocument`
- `EvaluationCriteriaResult`
- `EvaluationLog`

#### `EvaluationRun`

Cap run:

- `startup_id`
- `status`
- `submitted_at`, `started_at`, `completed_at`
- `overall_score`, `overall_confidence`
- `failure_reason`
- `evaluation_mode`
- `merged_artifact_json`
- `merge_status`

#### `EvaluationDocument`

Cap document:

- `document_id`
- `document_type`
- `processing_status`
- `extraction_status`
- `source_file_url_or_path`
- `artifact_metadata_json`
- `summary`

### 6.5 Luong submit -> worker -> aggregate

#### Step A. Submit

File: [src/modules/evaluation/application/use_cases/submit_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/submit_evaluation.py:1)

Nhung viec ham nay lam:

1. tim active runs cua cung `startup_id`
2. danh dau chung la `failed` voi ly do "Superseded..."
3. derive `evaluation_mode`
4. tao `EvaluationRun(status="queued")`
5. tao `EvaluationDocument` cho tung file
6. enqueue Celery task `process_evaluation_run_task.delay(run.id)`
7. ghi `EvaluationLog`

Day la cho ban hieu request API da tro thanh background job nhu the nao.

#### Step B. Celery task

File: [src/modules/evaluation/workers/tasks.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/workers/tasks.py:1)

Task `process_evaluation_run_task` lam:

1. load run
2. idempotency guard: chi xu ly `queued` hoac `retry`
3. set `run.status = processing`
4. loop qua documents
5. goi `process_document(doc.id)`
6. goi `aggregate_evaluation_run(run_id)`
7. neu terminal thi fire webhook

Retry logic:

- `max_retries=3`
- neu transient Gemini error thi re-raise de Celery retry
- status run duoc set `retry` trong DB
- het retry thi set `failed`

#### Step C. Process document

File: [src/modules/evaluation/application/use_cases/process_document.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/process_document.py:1)

Day la file nang nhat cua module.

Luong chinh:

1. resolve source
   - local path hoac download URL bang `httpx`
2. parse PDF thanh `pages`, `images`, `full_text`
3. neu `business_plan` thi reduce text
4. tao `PipelineLLMServices`
5. tao `DeterministicScoringService`
6. build `classification_context` tu user hints tren `EvaluationRun`
7. Step 1: classification
8. enforce `provided_stage` neu duoc gui vao
9. Step 2: evidence mapping
10. localize excerpt ve Vietnamese
11. Step 3: raw criterion judgments
12. Step 4: deterministic scoring
13. Step 5: report writer
14. assemble `canonical_dict`
15. sanitize + validate report
16. persist vao `artifact_metadata_json`
17. persist `EvaluationCriteriaResult`
18. set `processing_status=completed`

#### Step D. Aggregate run

File: [src/modules/evaluation/application/use_cases/aggregate_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/aggregate_evaluation.py:1)

Ham nay lam 3 viec:

1. gom cac document-level canonical result
2. quyet dinh merge hay fallback source
3. set final status / score cho `EvaluationRun`

No co `merge_status` rat ro:

- `not_applicable`
- `waiting_for_sources`
- `fallback_source_only`
- `merged`
- `merge_failed`
- `merge_disabled`

#### Step E. Merge result

File: [src/modules/evaluation/application/use_cases/merge_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/merge_evaluation.py:1)

Logic merge:

- classification: uu tien pitch deck, co ghi operational notes neu xung dot
- criterion: evidence-strength-first
- overall score: recompute weighted average
- confidence: lay gia tri conservative hon
- narrative: hop nhat lists va uu tien executive summary cua pitch deck

### 6.6 Pipeline LLM

File: [src/modules/evaluation/application/services/pipeline_llm_services.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/services/pipeline_llm_services.py:1)

Pipeline gom 5 buoc:

1. `classify_startup`
2. `map_evidence`
3. `judge_raw_criteria`
4. scoring deterministic trong Python
5. `write_report`

Prompt duoc load tu `src/modules/evaluation/prompts/`.

Y nghia kien truc:

- LLM dung de rut trich va judgement trung gian
- scoring cuoi cung co lop deterministic de giam do bat on
- sau do con co them lop sanitize/validate de sua cac output loi

### 6.7 Sanitize va validity

File lon nhat ve hardening:

- `src/modules/evaluation/application/services/report_validity.py`

File nay rat quan trong vi no cho biet project da gap nhung loi gi trong output LLM.
No sua/kiem tra cac nhom:

- malformed fields
- stage / classification contradiction
- recommendation contradiction
- operational notes duplicate
- scoring field inconsistency
- narrative mismatch
- top concerns / top risks synthesis

Neu muon hieu "tai sao code evaluation dai va phong thu nhieu", hay doc file nay.

### 6.8 Failure va fallback points trong evaluation

Can ghi nho cac cho co the fail:

- download document bi 401
- parse PDF khong ra content
- Gemini quota/transient error
- evidence localization fail
- report validity fail
- merge fail
- webhook fail

Nhung diem hay:

- document-level failure khong nhat thiet lam run fail ngay
- aggregate moi quyet dinh final outcome
- webhook failure khong lam hu ket qua evaluation

## 7. Recommendation module

### 7.1 Muc tieu nghiep vu

Module nay khong research web.
No dung du lieu profile startup/investor da duoc index san, sau do:

1. hard filter candidate
2. structured scoring
3. semantic scoring
4. optional LLM rerank
5. tra danh sach match + explanation

### 7.2 Files can doc theo thu tu

1. [src/modules/recommendation/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/api/router.py:1)
2. `src/modules/recommendation/application/dto/recommendation_schema.py`
3. [src/modules/recommendation/application/services/recommendation_engine.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/recommendation_engine.py:1)
4. [src/modules/recommendation/application/services/scoring.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/scoring.py:1)
5. [src/modules/recommendation/application/services/llm_reranker.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/llm_reranker.py:1)
6. `src/modules/recommendation/application/services/embedding.py`
7. [src/modules/recommendation/infrastructure/repositories/repo_factory.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/infrastructure/repositories/repo_factory.py:1)
8. [src/modules/recommendation/infrastructure/repositories/db_recommendation_repository.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/infrastructure/repositories/db_recommendation_repository.py:1)

### 7.3 API layer

File: [src/modules/recommendation/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/api/router.py:1)

Endpoint:

- `POST /internal/recommendations/reindex/startup/{startup_id}`
- `POST /internal/recommendations/reindex/investor/{investor_id}`
- `GET /api/v1/recommendations/startups`
- `GET /api/v1/recommendations/startups/{startup_id}/explanation`

Luu y:

- router nay khong duoc `main.py` gan global prefix
- nen path public da duoc viet full ngay trong router

### 7.4 Data model

Bang DB:

- `recommendation_investors`
- `recommendation_startups`
- `recommendation_runs`

Dac diem:

- nested document duoc serialize vao JSON text
- embedding duoc luu bang pgvector neu co
- SQLite thi fallback text

### 7.5 Engine

File: [src/modules/recommendation/application/services/recommendation_engine.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/recommendation_engine.py:1)

`RecommendationEngine` co 4 nhom ham chinh:

- `reindex_investor`
- `reindex_startup`
- `get_recommendations`
- `get_explanation`

#### Reindex flow

Khi reindex:

- build semantic text
- build deterministic embedding
- dong goi thanh document Pydantic
- upsert vao repository

#### Recommendation flow

Khi read recommendations:

1. load investor doc
2. load startup docs
3. `passes_hard_filter`
4. `score_structured`
5. `score_semantic`
6. tinh `combined_pre_llm_score`
7. lay top candidate set
8. LLM rerank nhe neu co Vertex config
9. assemble `RecommendationMatchResult`
10. store run history

### 7.6 Scoring

File: [src/modules/recommendation/application/services/scoring.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/scoring.py:1)

Day la file can hoc ky nhat trong module nay.

No chia scoring thanh:

- hard filter
- structured score
- semantic score
- final score
- fit band / label

#### Hard filter

Loai candidate neu:

- profile khong visible
- verification fail
- startup inactive
- investor inactive
- stage mismatch
- industry mismatch
- geography mismatch
- market scope mismatch

#### Structured score

Gom:

- thesis fit
- maturity fit
- support fit
- AI preference fit

#### Semantic score

Dung cosine similarity tren embedding cua:

- investor semantic text
- startup profile text
- startup AI text

#### Final score

Cong thuc:

- `0.7 * structured + 0.3 * semantic + rerank_adjustment`

### 7.7 LLM reranker

File: [src/modules/recommendation/application/services/llm_reranker.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/llm_reranker.py:1)

Neu khong co `GOOGLE_CLOUD_PROJECT`, rerank bi skip.

Neu co:

- engine gui investor context + candidate cards
- model tra ve adjustment `-10..+10`
- engine cap lai adjustment theo policy tuy candidate pool

Y nghia:

- LLM chi la lop fine-tune ranking
- khong phai lop scoring nen tang trung tam van la `scoring.py`

### 7.8 Repository backend

Factory:

- [src/modules/recommendation/infrastructure/repositories/repo_factory.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/infrastructure/repositories/repo_factory.py:1)

2 backend:

- DB backend la mac dinh
- filesystem backend la legacy

DB backend:

- [src/modules/recommendation/infrastructure/repositories/db_recommendation_repository.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/infrastructure/repositories/db_recommendation_repository.py:1)

Repo nay lo:

- upsert/get investor
- upsert/get/list startups
- store/list/latest runs
- parse JSON row thanh Pydantic models

## 8. Investor agent module

### 8.1 Muc tieu nghiep vu

Tra loi query research cho nha dau tu bang cach:

1. hieu follow-up
2. route scope
3. lap search plan
4. search web
5. chon source
6. extract
7. build facts
8. verify claims
9. write grounded answer
10. stream answer ra SSE

### 8.2 Files can doc theo thu tu

1. [src/modules/investor_agent/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:1)
2. [src/modules/investor_agent/application/dto/state.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/dto/state.py:1)
3. [src/modules/investor_agent/infrastructure/graph/builder.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/builder.py:1)
4. [src/modules/investor_agent/infrastructure/graph/nodes/followup_resolver.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/followup_resolver.py:1)
5. [src/modules/investor_agent/infrastructure/graph/nodes/router_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/router_node.py:1)
6. [src/modules/investor_agent/infrastructure/graph/nodes/planner_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/planner_node.py:1)
7. [src/modules/investor_agent/infrastructure/graph/nodes/search_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/search_node.py:1)
8. [src/modules/investor_agent/infrastructure/graph/nodes/source_selection_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/source_selection_node.py:1)
9. [src/modules/investor_agent/infrastructure/graph/nodes/extract_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/extract_node.py:1)
10. [src/modules/investor_agent/infrastructure/graph/nodes/fact_builder_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/fact_builder_node.py:1)
11. [src/modules/investor_agent/infrastructure/graph/nodes/claim_verifier_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/claim_verifier_node.py:1)
12. [src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py:1)
13. [src/modules/investor_agent/application/services/final_assembler.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/final_assembler.py:1)
14. [src/modules/investor_agent/application/services/scope_guard.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/scope_guard.py:1)
15. [src/shared/checkpoint.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/checkpoint.py:1)

### 8.3 API layer

File: [src/modules/investor_agent/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:1)

Code hien tai chi expose endpoint chinh:

- `POST /api/v1/investor-agent/chat/stream`

Endpoint nay:

- validate `query`
- validate `thread_id`
- bat auth noi bo neu config bat
- chay LangGraph stateful
- stream progress + answer_chunk + final_answer + final_metadata
- neu timeout/noi bo loi thi van stream event `error`
- luon ket thuc bang `data: [DONE]`

### 8.4 Graph state

File: [src/modules/investor_agent/application/dto/state.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/dto/state.py:1)

`GraphState` la trung tam cua module.
Ban can doc ky file nay vi moi node deu doc/ghi vao state.

State co 6 nhom lon:

- input va routing
- thread memory
- planning
- search/source/extraction
- fact/claim/verification
- output

Nhung field can nho:

- `resolved_query`
- `intent`
- `search_decision`
- `previous_verified_claims`
- `sub_queries`
- `search_results`
- `selected_sources`
- `facts`
- `verified_claims`
- `conflicting_claims`
- `coverage_assessment`
- `final_answer`
- `references`
- `grounding_summary`

### 8.5 Graph wiring

File: [src/modules/investor_agent/infrastructure/graph/builder.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/builder.py:1)

Flow:

`followup_resolver -> router -> planner -> search -> source_selection -> extract -> fact_builder -> claim_verifier -> writer`

Co 2 cho branch:

- sau `router`: out-of-scope hoac `reuse_only` co the nhay thang `writer`
- sau `claim_verifier`: co the repair loop quay lai `search`

### 8.6 Tung node dang lam gi

#### `followup_resolver`

File: [src/modules/investor_agent/infrastructure/graph/nodes/followup_resolver.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/followup_resolver.py:1)

Nhiem vu:

- xac dinh user query co phai follow-up khong
- resolve query thanh standalone query
- suy ra `followup_type`
- quyet dinh `reuse_previous_verified_claims`
- derive `search_decision`
- reset state nghien cuu cho turn moi

Node nay la cho giai thich tai sao cung `thread_id` nhung turn 2 co the:

- search lai
- tai su dung evidence cu
- hoac vua reuse vua search them

#### `router`

File: [src/modules/investor_agent/infrastructure/graph/nodes/router_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/router_node.py:1)

Nhiem vu:

- classify query thanh `market_trend`, `regulation`, `news`, `competitor_context`, `mixed`, `out_of_scope`
- ket hop voi `scope_guard.decide_scope()`

LLM router co heuristic fallback.
Day la lop phong thu tranh over-refuse.

#### `planner`

File: [src/modules/investor_agent/infrastructure/graph/nodes/planner_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/planner_node.py:1)

Nhiem vu:

- tao `sub_queries`
- tao `required_facets`
- tao `min_sources`

Planner dung intent, follow-up type, geography focus de tao plan tim kiem.

#### `search`

File: [src/modules/investor_agent/infrastructure/graph/nodes/search_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/search_node.py:1)

Nhiem vu:

- goi Tavily search async
- dedup URL
- prepend geography fallback query neu can
- tang `loop_count`

Node nay phu thuoc rat manh vao:

- `TAVILY_API_KEY`
- `INVESTOR_AGENT_SEARCH_DEPTH`
- `INVESTOR_AGENT_MAX_RESULTS_PER_QUERY`

#### `source_selection`

File: [src/modules/investor_agent/infrastructure/graph/nodes/source_selection_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/source_selection_node.py:1)

Mac dinh:

- heuristic fast path

Neu bat flag:

- co the goi LLM de chon source

Node nay chon toi da 5 source va attach:

- trust tier
- selection reason

#### `extract`

File: [src/modules/investor_agent/infrastructure/graph/nodes/extract_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/extract_node.py:1)

Nhiem vu:

- goi Tavily extract cho selected URLs
- danh dau `success`, `partial`, `failed`
- neu extract loi/quota thi fallback sang snippet extraction

Day la diem rat quan trong vi no giu pipeline khong bi "chet trang".

#### `fact_builder`

File: [src/modules/investor_agent/infrastructure/graph/nodes/fact_builder_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/fact_builder_node.py:1)

Nhiem vu:

- rut `FactItem`
- tao `ClaimCandidate`

Neu LLM fail:

- tao fallback facts tu document snippet
- auto-build claims tu facts

#### `claim_verifier`

File: [src/modules/investor_agent/infrastructure/graph/nodes/claim_verifier_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/claim_verifier_node.py:1)

Nhiem vu:

- map claim voi facts
- xep `supported` / `weakly_supported`
- detect conflict
- tinh `coverage_assessment`
- quyet dinh co can repair loop khong

Logic hay can nho:

- material claims can support manh hon
- coverage dua tren unique source domains hoac so claim

#### `writer`

File: [src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py:1)

Nhiem vu:

- dung supported claims de viet answer grounded
- tao references
- tao caveats
- tao suggested next questions
- fallback khi evidence yeu
- support out-of-scope / greeting

Node nay la noi bien state verification thanh UX output.

### 8.7 Scope guard

File: [src/modules/investor_agent/application/services/scope_guard.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/scope_guard.py:1)

Scope guard khong chi la "refuse".
No con:

- detect greeting
- heuristic classify in-scope/out-of-scope
- chon refusal message theo VI/EN
- build payload standard cho out-of-scope

### 8.8 Final assembler

File: [src/modules/investor_agent/application/services/final_assembler.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/final_assembler.py:1)

Neu `writer_node` la layer tao answer, thi `final_assembler` la layer hardening cuoi.

No lam:

- normalize references
- normalize suggested questions
- fallback answer neu answer rong
- canonicalize citation indexes `[1]`, `[2]`
- dong bo `grounding_summary`
- sua conflict/reference consistency
- enforce scope payload lan cuoi

Neu muon hieu contract tra ra FE vi sao on dinh, hay doc file nay ky.

### 8.9 Checkpoint va thread memory

Files:

- [src/shared/checkpoint.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/checkpoint.py:1)
- [src/modules/investor_agent/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:1)

Key idea:

- `thread_id` duoc dua vao `configurable`
- LangGraph saver luu state theo `thread_id`
- luc can, router co the recover final state tu checkpointer

Y nghia:

- memory cua chatbot khong nam o frontend
- frontend/backend chi can giu `thread_id` on dinh

## 9. Cross-cutting runtime flows

### 9.1 Evaluation flow

`HTTP submit -> DB queued run -> Celery task -> process_document x N -> aggregate -> optional merge -> report endpoint / webhook`

### 9.2 Recommendation flow

`internal reindex -> repository upsert -> public read endpoint -> hard filter -> score -> optional rerank -> result + store run history`

### 9.3 Investor agent flow

`POST stream -> LangGraph nodes -> writer -> final assembler -> SSE frames -> [DONE]`

## 10. Cac bang DB can nho

### Evaluation

- `evaluation_runs`
- `evaluation_documents`
- `evaluation_criteria_results`
- `evaluation_logs`

### Recommendation

- `recommendation_investors`
- `recommendation_startups`
- `recommendation_runs`

### Shared

- `webhook_deliveries`

## 11. Test map

Thu muc:

- `src/tests/unit/`
- `src/tests/integration/`

Nhin ten test co the doan nhanh pham vi:

- `test_investor_agent_*`
- `test_business_plan_*`
- `test_phase*`
- `test_recommendation_*`
- `test_evaluation_*`
- `test_processing_warning_sanitizer.py`
- `test_report_validity` related tests

Neu ban can hoc gap trong 2 ngay, test la cach rat nhanh de hieu behavior duoc mong doi.

Thu tu doc test de hieu nhanh:

1. investor agent stream / followup tests
2. recommendation flow tests
3. evaluation API contract tests
4. report validity / scorer tests

## 12. Tai lieu khac trong `docs/`

Repo da co cac doc bo tro:

- [docs/investor_agent_integration_guide.md](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/docs/investor_agent_integration_guide.md:1)
- `docs/integration_handoff/*`
- `docs/evaluation_*`

Nen dung nhu sau:

- doc nay: master map de dinh vi toan repo
- `investor_agent_integration_guide.md`: contract stream va handoff FE/BE
- `integration_handoff/*`: handoff theo feature
- `evaluation_*`: bo sung context cho evaluation

## 13. Reading order toi uu trong 2 ngay

### Day 1 buoi sang: khung xuong he thong

1. `README.md`
2. `src/main.py`
3. `src/shared/config/settings.py`
4. `src/shared/persistence/db.py`
5. 3 file models trong `src/shared/persistence/models`
6. `src/shared/auth.py`, `error_response.py`, `correlation.py`, `rate_limit/limiter.py`, `checkpoint.py`

Muc tieu:

- biet service boot the nao
- biet storage nam o dau
- biet auth / correlation / limiter / checkpoint hoat dong the nao

### Day 1 buoi chieu: evaluation

1. `evaluation/api/router.py`
2. `submit_evaluation.py`
3. `workers/tasks.py`
4. `process_document.py`
5. `pipeline_llm_services.py`
6. `deterministic_scorer.py`
7. `aggregate_evaluation.py`
8. `merge_evaluation.py`
9. `report_validity.py`

Muc tieu:

- nam request async
- nam lifecycle `queued -> processing -> retry/completed/failed`
- biet canonical report duoc tao va validate the nao

### Day 2 buoi sang: recommendation

1. `recommendation/api/router.py`
2. `recommendation_schema.py`
3. `recommendation_engine.py`
4. `scoring.py`
5. `llm_reranker.py`
6. `db_recommendation_repository.py`

Muc tieu:

- biet startup/investor doc duoc build the nao
- biet match score ra sao
- biet hard filter va rerank o dau

### Day 2 buoi chieu: investor agent

1. `investor_agent/api/router.py`
2. `state.py`
3. `builder.py`
4. tung node theo thu tu flow
5. `scope_guard.py`
6. `final_assembler.py`
7. test investor agent

Muc tieu:

- biet turn chat di qua nhung node nao
- biet memory theo `thread_id` duoc giu nhu the nao
- biet tai sao output stream on dinh du co fallback

## 14. Checklist de tu test sau khi doc

Neu ban da nam code, ban phai tra loi duoc cac cau sau:

1. Tai sao evaluation khong xu ly ngay trong request thread?
2. `process_document()` fail thi khi nao run van co the `completed`?
3. `merge_status` co bao nhieu gia tri va moi gia tri xay ra khi nao?
4. Recommendation hard filter khac structured score o diem nao?
5. LLM rerank co duoc phep thay doi ranking manh khong?
6. `thread_id` cua investor agent duoc luu o dau?
7. Khi Tavily extract fail thi investor agent con cach nao de tra loi?
8. `final_assembler.py` harden output bang cach nao?
9. Auth noi bo dang duoc bat tren endpoint nao?
10. File nao trong repo la "trung tam" cua tung module?

## 15. Top 12 files quan trong nhat repo

Neu chi duoc hoc rat nhanh, uu tien 12 file nay:

1. [src/main.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/main.py:1)
2. [src/shared/config/settings.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/config/settings.py:1)
3. [src/shared/providers/llm/gemini_client.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/shared/providers/llm/gemini_client.py:1)
4. [src/modules/evaluation/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/api/router.py:1)
5. [src/modules/evaluation/application/use_cases/process_document.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/process_document.py:1)
6. [src/modules/evaluation/application/use_cases/aggregate_evaluation.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/evaluation/application/use_cases/aggregate_evaluation.py:1)
7. [src/modules/recommendation/application/services/recommendation_engine.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/recommendation_engine.py:1)
8. [src/modules/recommendation/application/services/scoring.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/recommendation/application/services/scoring.py:1)
9. [src/modules/investor_agent/api/router.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:1)
10. [src/modules/investor_agent/application/dto/state.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/dto/state.py:1)
11. [src/modules/investor_agent/infrastructure/graph/builder.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/builder.py:1)
12. [src/modules/investor_agent/application/services/final_assembler.py](/abs/path/c:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/final_assembler.py:1)

## 16. Ket luan nhanh

Codebase nay khong qua nhieu module, nhung moi module deu co mot "trung tam do phuc tap" rieng:

- `evaluation`: `process_document.py` + `report_validity.py`
- `recommendation`: `scoring.py` + `recommendation_engine.py`
- `investor_agent`: `state.py` + graph nodes + `final_assembler.py`

Neu ban hoc theo flow runtime thay vi doc tung file random, ban se vao code nhanh hon rat nhieu.
