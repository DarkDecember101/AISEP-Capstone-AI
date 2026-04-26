# Evaluation Module Handle Guide

## 1. Muc tieu cua module

Module `evaluation` nhan 1 hoac 2 tai lieu (`pitch_deck`, `business_plan`), chay pipeline danh gia, luu artifact canonical vao DB, sau do tra ve report tong hop cho client.

Muc tieu thuc te cua module hien tai:

- Cho phep submit async qua API.
- Worker Celery xu ly tung document.
- Sinh `canonical_evaluation` theo schema on dinh.
- Aggregate ket qua len `EvaluationRun`.
- Neu co ca `pitch_deck` va `business_plan` thi co the merge hoac fallback ve 1 nguon.

## 2. Entry points va file quan trong

### API layer

- `src/modules/evaluation/api/router.py`
  - `POST /api/v1/evaluations/`: submit evaluation
  - `GET /api/v1/evaluations/{id}`: lay status
  - `GET /api/v1/evaluations/{id}/report`: lay report chinh
  - `GET /api/v1/evaluations/{id}/report/source/{document_type}`: lay report theo nguon

### Application layer

- `src/modules/evaluation/application/use_cases/submit_evaluation.py`
  - Tao `EvaluationRun`, tao `EvaluationDocument`, enqueue Celery task.
- `src/modules/evaluation/application/use_cases/process_document.py`
  - Xu ly 1 document end-to-end.
- `src/modules/evaluation/application/use_cases/aggregate_evaluation.py`
  - Tong hop ket qua document-level thanh run-level.
- `src/modules/evaluation/application/use_cases/merge_evaluation.py`
  - Merge ket qua Pitch Deck va Business Plan.

### Services / DTO

- `src/modules/evaluation/application/services/pipeline_llm_services.py`
  - Goi LLM cho 4 step: classification, evidence, raw judgment, report writer.
- `src/modules/evaluation/application/services/deterministic_scorer.py`
  - Score deterministic theo stage profile.
- `src/modules/evaluation/application/services/report_validity.py`
  - Sanitize + validate report canonical truoc khi expose.
- `src/modules/evaluation/application/dto/evaluation_schema.py`
  - Request/response contract cua API.
- `src/modules/evaluation/application/dto/pipeline_schema.py`
  - Structured output cho cac step trong pipeline.
- `src/modules/evaluation/application/dto/canonical_schema.py`
  - Schema canonical cuoi cung.

### Infra / persistence

- `src/modules/evaluation/workers/tasks.py`
  - Celery orchestration.
- `src/shared/persistence/models/evaluation_models.py`
  - `EvaluationRun`, `EvaluationDocument`, `EvaluationCriteriaResult`, `EvaluationLog`.
- `src/shared/config/settings.py`
  - Feature flags, Redis/Celery, webhook, document download, Vertex/Gemini config.

## 3. Luong chay thuc te

### Step 1. Submit

Client goi `POST /evaluations/` voi:

- `startup_id`
- danh sach documents
- optional: `provided_stage`, `provided_main_industry`, `provided_subindustry`

Tai `SubmitEvaluationRequest`:

- document type duoc normalize ve `pitch_deck` / `business_plan`
- document khong supported se bi drop
- 1 run chi cho phep toi da 1 `pitch_deck` va 1 `business_plan`
- `evaluation_mode` duoc derive tu documents:
  - `pitch_deck_only`
  - `business_plan_only`
  - `combined`

Tai `submit_evaluation()`:

- cac run dang `queued|processing|partial_completed` cua cung `startup_id` se bi mark `failed`
- tao run moi va document moi
- enqueue `process_evaluation_run_task.delay(run.id)`

### Step 2. Worker process run

Tai `process_evaluation_run_task()`:

- chi xu ly run dang o `queued` hoac `retry`
- mark run thanh `processing`
- lap qua tung `EvaluationDocument` va goi `process_document(doc.id)`
- sau cung goi `aggregate_evaluation_run(run.id)`
- neu run ket thuc voi `completed` hoac `failed` thi fire webhook

Luu y:

- doc fail khong chan doc con lai
- task co retry cho loi transient
- DB status la source of truth, Celery chi la orchestration

### Step 3. Process document

Tai `process_document()`:

1. Resolve source file:
   - local path: dung truc tiep
   - remote URL: download ve `storage/artifacts/downloads`
2. Parse PDF bang `PDFParser.extract_text_and_images(...)`
3. Chon prompt pack:
   - `pitch_deck`
   - `business_plan`
4. Neu la business plan thi reduce text bang `reduce_business_plan_text()`
5. Build classification context tu `provided_*`
6. Chay 4 step LLM + deterministic:
   - classification
   - evidence mapping
   - raw criterion judgment
   - deterministic scoring
   - report writer
7. Assemble `canonical_dict`
8. Chay `sanitize_canonical_report()`
9. Chay `validate_canonical_report()`
10. Persist vao `artifact_metadata_json`
11. Ghi bang `EvaluationCriteriaResult` de backward compatible

Neu co exception:

- mark doc `processing_status=failed`
- `extraction_status=failed`
- `summary=str(error)`

### Step 4. Aggregate run

Tai `aggregate_evaluation_run()`:

- neu van con doc dang in-flight thi defer aggregate
- tim completed docs co `artifact_metadata_json`
- extract `canonical_evaluation`
- neu mode `combined`:
  - co du 2 nguon va feature flag merge bat: merge
  - co du 2 nguon nhung merge tat: fallback ve Pitch Deck
  - thieu 1 nguon: fallback ve nguon con lai
- validate canonical run-level
- set `overall_score`, `overall_confidence`, `executive_summary`
- mark run `completed` hoac `failed`

## 4. Contract can nam khi handle

### Input documents support

Chi 2 loai document dang duoc support boi Python evaluator:

- `pitch_deck`
- `business_plan`

Neu .NET gui them document type khac, request khong fail ngay; document do se bi bo qua neu khong nam trong allowed set.

### evaluation_mode

- `pitch_deck_only`: chi co pitch deck
- `business_plan_only`: chi co business plan
- `combined`: co ca 2

### merge_status

Gia tri `EvaluationRun.merge_status` can handle:

- `not_applicable`
- `waiting_for_sources`
- `fallback_source_only`
- `merged`
- `merge_failed`
- `merge_disabled`

Y nghia quan trong:

- `merged`: report chinh la ket qua da merge
- `fallback_source_only`: run combined nhung chi co 1 source dung duoc
- `merge_failed`: da thu merge nhung loi, hien tai fallback ve Pitch Deck
- `merge_disabled`: co du source nhung feature flag dang tat

### report_mode khi goi `/report`

- `pitch_deck_only`
- `business_plan_only`
- `merged`
- `source`

`source` xuat hien khi:

- run la `combined`
- nhung merged artifact chua co hoac khong dung duoc
- router tra ve "best available single source"

## 5. Canonical report la artifact quan trong nhat

Artifact chinh duoc luu trong:

- `EvaluationDocument.artifact_metadata_json`
- `EvaluationRun.merged_artifact_json` neu merged thanh cong

Cau truc doc-level quan trong:

- `startup_id`
- `document_type`
- `classification`
- `effective_weights`
- `criteria_results`
- `overall_result`
- `narrative`
- `processing_warnings`

Neu can debug report sai, day la diem kiem tra dau tien.

## 6. Cac diem module hien tai da "handle"

Module nay da co san mot so defensive logic kha manh. Khi doc code can xem day la hanh vi mong muon, khong phai bug:

- Normalize `provided_subindustry` null-like ve `None`
- Enforce `provided_stage` len classification ket qua
- Backfill `startup_id` tu `EvaluationRun` vao canonical neu artifact bi thieu
- Sanitize operational notes / recommendation / concern bi mau thuan
- Dedupe operational notes
- Validate report truoc khi expose qua API
- Combined mode co fallback thay vi fail toan bo
- Doc-level fail khong lam stop nhung doc khac
- Webhook fail khong lam fail evaluation

## 7. Cac case can handle khi debug production

### Case A. Submit thanh cong nhung run dung o `queued`

Kiem tra:

- Redis/Celery broker co chay khong
- Worker co consume queue khong
- `process_evaluation_run_task.delay(...)` co duoc enqueue khong
- config `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`

### Case B. Run chuyen `processing` roi `failed`

Kiem tra theo thu tu:

1. `EvaluationLog` cua run
2. `EvaluationDocument.summary`
3. path/url cua tai lieu
4. download auth:
   - `DOCUMENT_DOWNLOAD_BEARER_TOKEN`
   - `DOCUMENT_DOWNLOAD_EXTRA_HEADERS`
5. parse PDF co tra text/images khong
6. Vertex/Gemini config:
   - `GOOGLE_CLOUD_PROJECT`
   - `GOOGLE_CLOUD_LOCATION`

### Case C. Run `completed` nhung `/report` tra 409

Nguyen nhan thuong gap:

- canonical report invalid
- artifact co narrative nhung khong co score dung nghia
- `startup_id` bi thieu va khong backfill duoc

Kiem tra:

- `validate_canonical_report()`
- `artifact_metadata_json`
- `merged_artifact_json` neu la combined

### Case D. Combined run khong ra merged report

Khong phai luc nao cung la bug. Can phan biet:

- `merge_status=waiting_for_sources`: van dang doi source
- `merge_status=fallback_source_only`: 1 source fail
- `merge_status=merge_failed`: merge code throw exception
- `merge_status=merge_disabled`: feature flag tat

### Case E. Report noi dung mau thuan voi score

Kiem tra:

- `sanitize_canonical_report()` co duoc chay truoc validate khong
- `processing_warnings` co flag auto-correct khong
- criteria nao dang score cao nhung narrative van noi "thieu bang chung"

## 8. Checklist khi can extend module

### Neu them document type moi

Can update dong bo:

- `evaluation_schema.py` allowed doc types
- `SubmitEvaluationRequest.derived_evaluation_mode`
- prompt pack moi trong `src/modules/evaluation/prompts/`
- logic `process_document()` de map document type -> prompt pack
- aggregate/router neu report can route theo source moi
- tests API contract

### Neu doi scoring logic

Can update:

- `deterministic_scorer.py`
- stage weight profiles
- validation neu co them invariant moi
- tests scorer + report validity

### Neu doi merge logic

Can update:

- `merge_evaluation.py`
- `aggregate_evaluation.py`
- `router.py` vi `/report` dang phu thuoc `has_merged_result` va `merge_status`
- tests cho combined mode

### Neu doi schema report

Can update dong bo:

- `canonical_schema.py`
- `process_document.py`
- `report_validity.py`
- API response expectations trong tests

## 9. Feature flags va config quan trong

Can biet cac config nay truoc khi ket luan module bi loi:

- `BUSINESS_PLAN_EVAL_ENABLED`
- `MERGE_EVAL_ENABLED`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `WEBHOOK_CALLBACK_URL`
- `WEBHOOK_SIGNING_SECRET`
- `DOCUMENT_DOWNLOAD_BEARER_TOKEN`
- `DOCUMENT_DOWNLOAD_EXTRA_HEADERS`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`

## 10. Test files nen doc khi can verify

- `src/tests/unit/test_evaluation_api_contract.py`
- `src/tests/unit/test_evaluation_celery_tasks.py`
- `src/tests/unit/test_phase2c_evaluation_report_validity.py`

3 file nay bao phu kha ro behavior mong muon cua module:

- request validation
- mode derivation
- celery orchestration
- report envelope
- validity guard
- startup_id backfill

## 11. Huong handle de xuat cho team

Neu muc tieu cua team la tiep tuc lam viec voi module nay, thu tu handle hop ly la:

1. Xem `router.py` va `evaluation_schema.py` de chot contract voi client.
2. Xem `process_document.py` de nam source of truth cua pipeline.
3. Xem `aggregate_evaluation.py` va `merge_evaluation.py` de nam combined behavior.
4. Xem `report_validity.py` de hieu vi sao report bi reject hoac auto-correct.
5. Xem tests de xac nhan behavior nao la expected.

## 12. Ket luan ngan

Module `evaluation` hien tai khong chi la "LLM goi prompt roi tra ket qua", ma la mot pipeline co 3 lop handle ro rang:

- orchestration async bang Celery
- scoring/report assembly bang Python
- validation/sanitize de chan report loi truoc khi expose

Neu can sua module nay an toan, uu tien sua theo boundary:

- contract -> schema/router
- pipeline -> process_document/pipeline services
- output quality -> report_validity
- combined behavior -> aggregate/merge
