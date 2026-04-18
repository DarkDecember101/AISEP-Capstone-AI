# Python AI Service — Thông tin tích hợp cho .NET Team

> **Mục đích:** Tài liệu này do Python AI Service xuất ra để .NET team cấu hình đúng kết nối hai chiều.
> **Cập nhật lần cuối:** 2026-04-18

---

## 1. Base URL & Port

| Môi trường   | URL                     |
| ------------ | ----------------------- |
| Local dev    | `http://127.0.0.1:8000` |
| Staging/Prod | Tuỳ cấu hình deploy     |

> Python mặc định khởi động trên port `8000` (HTTP). Nếu bật TLS thì port `8443`.
> Kiểm tra bằng: `GET http://127.0.0.1:8000/health` → phải trả `{"status": "ok"}`.

---

## 2. Ba giá trị bắt buộc phải khớp nhau

> Đây là 3 điều kiện **cần và đủ** để hai repo gọi nhau thành công.

| Python env var           | Phải bằng với .NET appsettings                      | Ghi chú                                         |
| ------------------------ | --------------------------------------------------- | ----------------------------------------------- |
| `AISEP_INTERNAL_TOKEN`   | `PythonAi:InternalToken`                            | Token Python dùng để verify mọi request từ .NET |
| `WEBHOOK_SIGNING_SECRET` | `PythonAi:WebhookSigningSecret`                     | Secret Python dùng để ký webhook gọi về .NET    |
| `WEBHOOK_CALLBACK_URL`   | = URL endpoint .NET (`/api/ai/evaluation/callback`) | URL Python POST kết quả evaluation về           |

### Ví dụ `.env` của Python (local dev):

```env
AISEP_INTERNAL_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
REQUIRE_INTERNAL_AUTH=true
WEBHOOK_CALLBACK_URL=http://localhost:5294/api/ai/evaluation/callback
WEBHOOK_SIGNING_SECRET=dev-secret456
WEBHOOK_VERIFY_SSL=false
```

> ⚠️ `REQUIRE_INTERNAL_AUTH` mặc định `false` ở local dev (tắt auth check để tiện test).
> Khi deploy staging/prod **bắt buộc** set `REQUIRE_INTERNAL_AUTH=true`.

---

## 3. Auth — Header .NET phải gửi kèm mọi request

```
X-Internal-Token: <giá_trị_AISEP_INTERNAL_TOKEN>
```

Nếu thiếu hoặc sai → Python trả:

```json
HTTP 401
{
  "code": "AUTH_FAILED",
  "message": "Invalid or missing internal token.",
  "detail": null,
  "retryable": false,
  "correlation_id": "..."
}
```

---

## 4. Danh sách endpoint Python (route thực tế)

| HTTP | Path                                                                        | Auth bắt buộc     | Timeout gợi ý |
| ---- | --------------------------------------------------------------------------- | ----------------- | ------------- |
| GET  | `/health`                                                                   | ❌                | 5s            |
| POST | `/api/v1/evaluations/`                                                      | ✅ (khi auth bật) | 60s           |
| GET  | `/api/v1/evaluations/{id}`                                                  | ✅                | 60s           |
| GET  | `/api/v1/evaluations/{id}/report`                                           | ✅                | 60s           |
| POST | `/internal/recommendations/reindex/startup/{startupId}`                     | ✅                | 60s           |
| POST | `/internal/recommendations/reindex/investor/{investorId}`                   | ✅                | 60s           |
| GET  | `/api/v1/recommendations/startups?investor_id={id}&top_n={n}`               | ✅                | 60s           |
| GET  | `/api/v1/recommendations/startups/{startupId}/explanation?investor_id={id}` | ✅                | 60s           |
| POST | `/api/v1/investor-agent/chat/stream`                                        | ✅                | 300s (SSE)    |

> **Trailing slash:** `POST /api/v1/evaluations/` có dấu `/` cuối — FastAPI trả **307 redirect** nếu thiếu.
> .NET phải gọi đúng URL có slash, hoặc cấu hình HttpClient follow redirect.

---

## 5. Schemas request/response chi tiết

### 5.1 `POST /api/v1/evaluations/` — Submit Evaluation

**Request .NET gửi:**

```json
{
  "startup_id": "123",
  "documents": [
    {
      "document_id": "456",
      "document_type": "PitchDeck",
      "file_url_or_path": "https://res.cloudinary.com/..."
    }
  ]
}
```

> `document_type` Python chấp nhận: `PitchDeck`, `BusinessPlan`, `pitch_deck`, `business_plan` (case-insensitive).

**Response Python trả:**

```json
{
  "evaluation_run_id": 42,
  "startup_id": "123",
  "status": "queued",
  "message": "Evaluation submitted successfully",
  "evaluation_mode": "pitch_deck_only",
  "documents": [...]
}
```

> .NET chỉ cần đọc `evaluation_run_id`, `status`, `message`.

---

### 5.2 `GET /api/v1/evaluations/{id}` — Status Polling

**Response Python trả:**

```json
{
  "id": 42,
  "evaluation_run_id": 42,
  "startup_id": "123",
  "status": "processing",
  "submitted_at": "2026-04-18T10:00:00",
  "failure_reason": null,
  "overall_score": null,
  "overall_confidence": null,
  "evaluation_mode": "pitch_deck_only",
  "documents": [
    {
      "id": 1,
      "document_type": "pitch_deck",
      "status": "processing",
      "extraction_status": "done",
      "summary": "..."
    }
  ],
  "has_pitch_deck_result": false,
  "has_business_plan_result": false,
  "has_merged_result": false,
  "merge_status": null
}
```

> `status` hợp lệ: `queued` | `processing` | `completed` | `failed`
> Field `id` và `evaluation_run_id` có cùng giá trị — giữ cả hai để backward compat.

---

### 5.3 `GET /api/v1/evaluations/{id}/report` — Fetch Report

- Nếu **chưa sẵn sàng** → `HTTP 202` (với error envelope `code: "EVALUATION_NOT_READY"`)
- Nếu **đã có report** → `HTTP 200`:

```json
{
  "report_mode": "pitch_deck_only",
  "evaluation_mode": "pitch_deck_only",
  "has_merged_result": false,
  "available_sources": ["pitch_deck"],
  "source_document_type": null,
  "merge_status": null,
  "report": {
    "startup_id": "123",
    "status": "completed",
    "overall_result": { ... },
    "criteria_results": { ... },
    "classification": { ... },
    "narrative": { ... },
    "effective_weights": { ... },
    "processing_warnings": ["..."]
  }
}
```

---

### 5.4 Reindex Startup — `POST /internal/recommendations/reindex/startup/{startupId}`

**Response Python trả:**

```json
{
  "status": "ok",
  "message": "Startup reindexed successfully"
}
```

---

### 5.5 Reindex Investor — `POST /internal/recommendations/reindex/investor/{investorId}`

**Response Python trả:**

```json
{
  "status": "ok",
  "message": "Investor reindexed successfully"
}
```

---

### 5.6 `GET /api/v1/recommendations/startups` — Get Recommendations

**Response Python trả — field là `matches` (không phải `items`):**

```json
{
  "investor_id": "1",
  "matches": [
    {
      "investor_id": "1",
      "startup_id": "123",
      "startup_name": "TechViet",
      "final_match_score": 8.5,
      "structured_score": 7.0,
      "semantic_score": 9.0,
      "combined_pre_llm_score": 8.0,
      "rerank_adjustment": 0.5,
      "match_band": "HIGH",
      "fit_summary_label": "Strong Fit",
      "breakdown": { ... },
      "match_reasons": ["INDUSTRY_MATCH", "STAGE_MATCH"],
      "positive_reasons": [...],
      "caution_reasons": [...],
      "warning_flags": [],
      "generated_at": "2026-04-18T10:00:00"
    }
  ],
  "warnings": [],
  "generated_at": "2026-04-18T10:00:00"
}
```

> `match_band` hợp lệ: `LOW` | `MEDIUM` | `HIGH` | `VERY_HIGH` (uppercase).

---

### 5.7 `GET /api/v1/recommendations/startups/{startupId}/explanation` — Explanation

**Response Python trả — field là `explanation` (không phải `result`):**

```json
{
  "investor_id": "1",
  "startup_id": "123",
  "explanation": { ... },
  "generated_at": "2026-04-18T10:00:00"
}
```

---

### 5.8 `POST /api/v1/investor-agent/chat/stream` — SSE Stream

**Request:**

```json
{
  "query": "Tell me about FinTech startups in Vietnam",
  "thread_id": "thread-abc-123"
}
```

> `thread_id`: 1–128 ký tự, chỉ `[a-zA-Z0-9_-]`. Nếu `null` → Python dùng `"default_thread"`.

**Response:** `Content-Type: text/event-stream; charset=utf-8`

**Thứ tự SSE events Python gửi:**

```
data: {"type": "progress", "node": "planner"}

data: {"type": "answer_chunk", "content": "Hello "}

data: {"type": "answer_chunk", "content": "world"}

data: {"type": "final_answer", "content": "Hello world"}

data: {"type": "final_metadata", "references": [...], "caveats": [...], "writer_notes": [...], "processing_warnings": [...], "grounding_summary": {...}}

data: [DONE]
```

**Các `type` hợp lệ:**

| type             | Khi nào                  | Fields                                                                              |
| ---------------- | ------------------------ | ----------------------------------------------------------------------------------- |
| `progress`       | Mỗi node graph chạy      | `node` (string)                                                                     |
| `answer_chunk`   | Từng chunk câu trả lời   | `content` (string)                                                                  |
| `final_answer`   | Toàn bộ câu trả lời cuối | `content` (string)                                                                  |
| `final_metadata` | Metadata kèm sau         | `references`, `caveats`, `writer_notes`, `processing_warnings`, `grounding_summary` |
| `error`          | Có lỗi trong stream      | `content` (string), `correlation_id`                                                |

> **Bắt buộc:** Stream luôn kết thúc bằng `data: [DONE]`. Hard timeout phía Python là **240s** (nhỏ hơn .NET StreamTimeoutSeconds=300s để Python có thể gửi `error` event trước khi .NET timeout).

---

## 6. Webhook Python gọi về .NET

Python POST callback khi evaluation kết thúc (`completed` / `failed` / `partial`).

### Headers Python gửi:

```
Content-Type: application/json
X-Signature: sha256=<HMAC-SHA256-hex>
X-Delivery-Id: <uuid-hex>
```

### Cách tính `X-Signature` (để .NET verify):

```python
import hmac, hashlib, json

body = json.dumps(payload, separators=(',', ':'))  # compact JSON, không có space
sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
header = f"sha256={sig}"
```

> **Quan trọng:** Python serialize body bằng `separators=(',', ':')` — compact JSON.
> .NET phải tính HMAC trên **byte body thực tế nhận được**, không reformat lại.

### Body:

```json
{
  "delivery_id": "<deterministic-uuid-hex>",
  "evaluation_run_id": 42,
  "startup_id": "123",
  "terminal_status": "Completed",
  "overall_score": 7.5,
  "failure_reason": null,
  "timestamp": "2026-04-18T10:00:00Z",
  "correlation_id": "optional-uuid"
}
```

> `terminal_status` hợp lệ: `Completed` | `Failed` | `Partial`
> `delivery_id` là **deterministic** (UUID5 từ `run_id + status`) — .NET có thể dùng để dedup nếu Python retry.
> Python retry tối đa `WEBHOOK_MAX_RETRIES` lần (mặc định 3) với exponential backoff 1s→2s→4s.

---

## 7. Error Envelope chuẩn

Mọi lỗi từ Python đều theo format:

```json
{
  "code": "EVALUATION_NOT_FOUND",
  "message": "Evaluation run 42 not found.",
  "detail": null,
  "retryable": false,
  "correlation_id": "abc-123"
}
```

**Các `code` quan trọng:**

| Code                      | HTTP | Ý nghĩa                                 | Retryable |
| ------------------------- | ---- | --------------------------------------- | --------- |
| `AUTH_FAILED`             | 401  | Token sai hoặc thiếu                    | ❌        |
| `EVALUATION_NOT_FOUND`    | 404  | Run ID không tồn tại                    | ❌        |
| `EVALUATION_NOT_READY`    | 202  | Report chưa sẵn sàng                    | ✅        |
| `EVALUATION_FAILED`       | 409  | Evaluation thất bại, không có report    | ❌        |
| `INVESTOR_NOT_FOUND`      | 404  | Investor chưa được reindex              | ❌        |
| `REINDEX_STARTUP_FAILED`  | 500  | Lỗi khi reindex startup                 | ✅        |
| `REINDEX_INVESTOR_FAILED` | 500  | Lỗi khi reindex investor                | ✅        |
| `AGENT_STREAM_ERROR`      | —    | Lỗi trong SSE stream (event type=error) | ✅        |
| `RATE_LIMIT_EXCEEDED`     | 429  | Quá rate limit                          | ✅        |

---

## 8. Rate Limits

| Endpoint group    | Limit mặc định | Env var để thay đổi     |
| ----------------- | -------------- | ----------------------- |
| Evaluation submit | 20 req/phút    | `RATE_LIMIT_EVAL_RPM`   |
| Recommendations   | 60 req/phút    | `RATE_LIMIT_RECO_RPM`   |
| Chat stream       | 30 req/phút    | `RATE_LIMIT_STREAM_RPM` |

Khi bị throttle → `HTTP 429` với error envelope chuẩn.

---

## 9. Correlation ID

Python đọc header `X-Correlation-Id` từ request và truyền xuyên suốt log + error response.

```
X-Correlation-Id: <guid>
```

Khi .NET gửi kèm header này, mọi log Python liên quan đến request đó đều có `correlation_id` → dễ trace lỗi cross-service.

---

## 10. Checklist bật tích hợp

```
[ ] AISEP_INTERNAL_TOKEN    == PythonAi:InternalToken
[ ] WEBHOOK_SIGNING_SECRET  == PythonAi:WebhookSigningSecret
[ ] WEBHOOK_CALLBACK_URL    == http://localhost:5294/api/ai/evaluation/callback
[ ] REQUIRE_INTERNAL_AUTH   = true  (staging/prod)
[ ] WEBHOOK_VERIFY_SSL      = false (nếu .NET dùng self-signed cert ở dev)
[ ] curl GET /health        → {"status": "ok"}
[ ] .NET gọi POST /api/v1/evaluations/ với X-Internal-Token → 200 (không phải 401/307)
[ ] Python POST webhook về .NET → .NET trả 200 (không phải 400 signature mismatch)
```

---

## 11. Phản hồi chính thức từ Python — 2026-04-18

> Trả lời bộ câu hỏi từ .NET team gửi ngày 2026-04-18.

---

### 11.1 Xác nhận các điểm "Đã đồng bộ"

| #   | Điểm                                                                        | Xác nhận                                            |
| --- | --------------------------------------------------------------------------- | --------------------------------------------------- |
| 1   | `AISEP_INTERNAL_TOKEN` = `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9`             | ✅ Khớp — Python verify bằng `hmac.compare_digest`  |
| 2   | `WEBHOOK_SIGNING_SECRET` = `dev-secret456`                                  | ✅ Khớp — dùng để ký `X-Signature` header           |
| 3   | `WEBHOOK_CALLBACK_URL` = `http://localhost:5294/api/ai/evaluation/callback` | ✅ Khớp                                             |
| 4   | `REQUIRE_INTERNAL_AUTH=true` khi staging/prod                               | ✅ Đã ghi nhận — mặc định `false` chỉ cho local dev |

---

### 11.2 Xác nhận các điểm .NET yêu cầu kiểm tra

| #   | Vấn đề                                                                 | Trạng thái               | Chi tiết                                                                                                                                                          |
| --- | ---------------------------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Report response wrap trong `{ "report": {...}, "report_mode": "..." }` | ✅ **Đúng**              | Python trả `ReportEnvelope` với field `report` là nested dict. Root có `report_mode`, `evaluation_mode`, `has_merged_result`, `available_sources`, `merge_status` |
| 2   | `evaluation_run_id` kiểu `int`                                         | ✅ **Đúng**              | Python trả `int` từ DB model — không bao giờ là string                                                                                                            |
| 3   | `GET /{id}/report` trả 202 kèm error envelope                          | ✅ **Đúng**              | Python raise `APIError(status_code=202, code="EVALUATION_NOT_READY", retryable=True)` — handler serialize thành JSON envelope chuẩn                               |
| 4   | `match_band` là UPPERCASE                                              | ✅ **Đúng**              | Pydantic `Literal["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]` — Python không bao giờ trả lowercase                                                                     |
| 5   | SSE stream kết thúc bằng `data: [DONE]`                                | ✅ **Đúng**              | `yield "data: [DONE]\n\n"` nằm trong `finally`-equivalent block — luôn gửi kể cả khi có exception                                                                 |
| 6   | Python hard timeout 240s < .NET 300s                                   | ✅ **Đúng**              | `_GRAPH_STREAM_TIMEOUT_SECONDS = 240` — Python gửi `error` SSE event rồi mới close stream                                                                         |
| 7   | Webhook `X-Signature` tính trên compact JSON                           | ✅ **Đã fix 2026-04-18** | Dùng `json.dumps(payload, separators=(',', ':'))` — compact, không có space                                                                                       |
| 8   | Webhook retry + `delivery_id` deterministic                            | ✅ **Đúng**              | `delivery_id` = UUID5(`aisep:evaluation:{run_id}:{status}`) — .NET dedup an toàn                                                                                  |

---

### 11.3 Trả lời 4 câu hỏi của .NET

**Q1: Sau khi sửa lần này, endpoint nào thay đổi route hoặc schema?**

Không có route nào thay đổi. Chỉ thay đổi **response schema** tại 6 điểm:

| Endpoint                                    | Thay đổi                                                                                           |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `POST /api/v1/evaluations/`                 | Response thêm field `message: "Evaluation submitted successfully"`                                 |
| `GET /api/v1/evaluations/{id}`              | Response thêm fields `id`, `submitted_at`, `failure_reason`, `overall_score`, `overall_confidence` |
| `POST /internal/…/reindex/startup/{id}`     | Response đổi từ `{"success": true, ...}` → `{"status": "ok", "message": "..."}`                    |
| `POST /internal/…/reindex/investor/{id}`    | Response đổi từ `{"success": true, ...}` → `{"status": "ok", "message": "..."}`                    |
| `GET /api/v1/recommendations/startups`      | Field `items` → **`matches`**                                                                      |
| `GET /api/v1/recommendations/…/explanation` | Field `result` → **`explanation`**                                                                 |

> ⚠️ `.NET phải cập nhật deserialization cho 2 field rename: `items`→`matches`và`result`→`explanation`.`

---

**Q2: `REQUIRE_INTERNAL_AUTH` hiện đang là `true` hay `false`?**

```
Mặc định trong code: false
```

- **Local dev** (môi trường .NET đang kết nối): `false` — auth bị tắt, mọi request đều qua
- **Staging/Prod**: phải set `REQUIRE_INTERNAL_AUTH=true` trong `.env`

> Nếu .NET đang test local và muốn verify auth flow hoạt động đúng, set `REQUIRE_INTERNAL_AUTH=true` trong `.env` của Python rồi restart service.

---

**Q3: Python có đang retry webhook không? Interval và max retries?**

✅ **Có retry.** Chi tiết:

| Tham số               | Giá trị mặc định              | Env var để thay đổi   |
| --------------------- | ----------------------------- | --------------------- |
| Max retries           | **3 lần**                     | `WEBHOOK_MAX_RETRIES` |
| Backoff strategy      | Exponential: **1s → 2s → 4s** | Không cấu hình được   |
| Timeout mỗi attempt   | **10s**                       | Không cấu hình được   |
| Tổng thời gian tối đa | ~17s (1+2+4 + overhead)       | —                     |

> `.NET nên config webhook receiver timeout ≥ 30s để không bị `.NET` close connection trước khi Python retry xong.`
> Nếu cả 3 lần đều fail, Python log `ERROR` và ghi vào bảng `webhook_deliveries` — **không raise exception vào caller**.

---

**Q4: Python có hỗ trợ `POST /api/v1/investor-agent/research` (non-stream) không?**

❌ **Không tồn tại.** Python chỉ có **một endpoint duy nhất** cho investor agent:

```
POST /api/v1/investor-agent/chat/stream
```

Không có route `/research`, `/chat` (non-stream), hay bất kỳ variant nào khác.

> Nếu .NET đang delegate "research" về cùng endpoint `chat/stream` — **đúng hướng**. Đây là thiết kế có chủ đích: mọi query đều đi qua pipeline LangGraph có internal routing (router node phân loại `research` / `recommendation` / `out_of_scope`). .NET không cần phân biệt loại query ở phía gọi.

---

### 11.5 Source Report Endpoint — Trả lời 4 câu hỏi

**Q1: URL format — query param hay path param?**

✅ **Dạng path:**

```
GET /api/v1/evaluations/{id}/report/source/{document_type}
```

Không có dạng query param `?source=`. Ví dụ cụ thể:

```
GET /api/v1/evaluations/42/report/source/pitch_deck
GET /api/v1/evaluations/42/report/source/business_plan
```

---

**Q2: Giá trị hợp lệ của `document_type`?**

✅ **Chỉ chấp nhận snake_case:**

| Giá trị         | Kết quả                             |
| --------------- | ----------------------------------- |
| `pitch_deck`    | ✅ Hợp lệ                           |
| `business_plan` | ✅ Hợp lệ                           |
| `PitchDeck`     | ❌ `HTTP 400 INVALID_DOCUMENT_TYPE` |
| `BusinessPlan`  | ❌ `HTTP 400 INVALID_DOCUMENT_TYPE` |

> ⚠️ **Khác với submit endpoint** — `/report/source/{document_type}` validate **strict lowercase snake_case**, không normalize PascalCase.
> .NET phải hardcode `pitch_deck` / `business_plan` khi gọi endpoint này.

---

**Q3: Response format có cùng wrapper như `/report` không?**

✅ **Cùng format hoàn toàn** — trả `ReportEnvelope` giống hệt:

```json
{
  "report_mode": "source",
  "evaluation_mode": "combined",
  "has_merged_result": true,
  "available_sources": ["pitch_deck", "business_plan"],
  "source_document_type": "pitch_deck",
  "merge_status": "merged",
  "report": {
    "startup_id": "123",
    "status": "completed",
    "overall_result": { ... },
    "criteria_results": { ... },
    ...
  }
}
```

> `report_mode` luôn là `"source"` với endpoint này (không bao giờ là `merged` hay `pitch_deck_only`).
> `source_document_type` luôn được populate với giá trị đúng bằng `document_type` trong URL.

---

**Q4: Chạy mode `pitch_deck_only` rồi gọi `source/business_plan` — trả 404 hay 400?**

✅ **Trả `HTTP 404 DOCUMENT_NOT_FOUND`:**

```json
{
  "code": "DOCUMENT_NOT_FOUND",
  "message": "No completed business_plan document found for run 42.",
  "detail": null,
  "retryable": false,
  "correlation_id": "..."
}
```

Luồng xử lý trong code:

1. Kiểm tra `document_type` hợp lệ → nếu không phải `pitch_deck`/`business_plan` → **400**
2. Kiểm tra run tồn tại → nếu không → **404 EVALUATION_NOT_FOUND**
3. Kiểm tra run đã `completed` → nếu chưa → **202 EVALUATION_NOT_READY**
4. Query DB xem có document `{document_type}` với `processing_status=completed` không → nếu không có → **404 DOCUMENT_NOT_FOUND**

> Tóm lại: `pitch_deck_only` + gọi `source/business_plan` → **404**, không phải 400 hay 409.

---

### 11.4 Tóm tắt action items sau buổi sync này

| Bên        | Action                                                                                        |
| ---------- | --------------------------------------------------------------------------------------------- |
| **Python** | ✅ Tất cả 8 fix đã merge vào `main` ngày 2026-04-18                                           |
| **.NET**   | 🔄 Cập nhật deserialize `matches` (không phải `items`) trong `GetStartupRecommendationsAsync` |
| **.NET**   | 🔄 Cập nhật deserialize `explanation` (không phải `result`) trong `GetMatchExplanationAsync`  |
| **.NET**   | 🔄 Xác nhận `REQUIRE_INTERNAL_AUTH` flag phù hợp với môi trường test                          |
| **Cả hai** | 🔄 Chạy checklist Section 10 sau khi .NET update xong                                         |
