# Investor Agent Integration Guide

## 1. Muc tieu

Tai lieu nay la user guide handoff cho team Backend va Frontend de tich hop chatbot `investor_agent` dung voi contract hien tai trong code base.

Guide nay bao phu:

- endpoint stream chinh
- request/response contract
- `thread_id` va multi-turn memory
- auth, timeout, rate limit
- SSE event ma FE can parse
- `suggested_next_questions`
- cac luu y production de tranh mat stream, mat reference, hoac UX sai

Nguon code chinh:

- Router: [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:1)
- Final assembler: [src/modules/investor_agent/application/services/final_assembler.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/application/services/final_assembler.py:1)
- Writer node: [src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/infrastructure/graph/nodes/writer_node.py:1)
- Auth dependency: [src/shared/auth.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/auth.py:1)
- Settings: [src/shared/config/settings.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/config/settings.py:1)

## 2. Tong quan nhanh

`investor_agent` hien tai hoat dong theo mo hinh:

`Frontend -> Backend cua ban -> AISEP AI /api/v1/investor-agent/chat/stream`

Ly do nen di qua Backend:

- giu `X-Internal-Token` o server
- map conversation cua user sang `thread_id`
- luu transcript va metadata
- log `correlation_id` khi co loi
- co the them auth, quota, audit, analytics o he thong cua ban

FE khong nen goi truc tiep production endpoint neu token noi bo dang bat.

## 3. Endpoint

### URL

```text
POST /api/v1/investor-agent/chat/stream
```

### Headers

- `Content-Type: application/json`
- `Accept: text/event-stream`
- `X-Internal-Token: <token>`

`X-Internal-Token` chi bat buoc khi `REQUIRE_INTERNAL_AUTH=true` trong env. Rule auth nam o [src/shared/auth.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/auth.py:28).

### Body

```json
{
  "query": "Xu huong AI tai Viet Nam 2026",
  "thread_id": "investor-chat-001"
}
```

### Validation

- `query` khong duoc rong
- `thread_id` chi cho phep `a-z`, `A-Z`, `0-9`, `_`, `-`
- do dai `thread_id`: `1-128`

Validation nam o [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:53).

## 4. `thread_id` va multi-turn memory

### Y nghia

- cung `thread_id` -> cung mot conversation
- doi `thread_id` -> conversation moi

Agent tu luu va tai su dung context theo `thread_id`. FE khong can gui lai lich su chat neu da dung dung `thread_id`.

### FE hay BE tao `thread_id`?

Code hien tai chap nhan ca 2 cach. Tuy nhien production nen de BE quan ly hoac xac nhan `thread_id` de:

- tranh user ghi de conversation cua nhau
- map dung voi record conversation trong he thong cua ban
- audit va restore hoi thoai de hon

### Neu gui `thread_id` moi hoan toan thi sao?

Agent se tu khoi tao state/checkpoint moi. Khong can tao thread bang API rieng.

## 5. Kieu response

Endpoint nay khong tra mot JSON cuoi cung. No tra SSE stream:

```text
data: {...json...}

data: {...json...}

data: [DONE]
```

Luu y quan trong:

- day la `POST` stream, khong phai `GET`
- FE khong nen dung `EventSource`
- FE nen dung `fetch()` + `ReadableStream`
- BE proxy nen forward tung frame SSE, khong doc het roi moi tra

## 6. Cac SSE event chinh

### `progress`

```json
{ "type": "progress", "node": "search" }
```

Node co the gap:

- `followup_resolver`
- `router`
- `planner`
- `search`
- `source_selection`
- `extract`
- `fact_builder`
- `claim_verifier`
- `writer`
- `scope_guard`

`scope_guard` la event bo sung khi query bi xep `out_of_scope`.

### `answer_chunk`

```json
{ "type": "answer_chunk", "content": "..." }
```

Dung de FE stream text dan vao bubble chat.

Quan trong:

- day khong phai chain-of-thought
- day la final answer da duoc chunk ra
- chunk dau tien thuong chi xuat hien gan cuoi pipeline, khong phai token stream som

### `final_answer`

```json
{ "type": "final_answer", "content": "..." }
```

Day la ban answer day du sau cung. FE nen coi day la source of truth va co the replace noi dung da ghep tu `answer_chunk`.

### `final_metadata`

Quan trong nhat:

- `final_metadata` hien tai la object phang
- khong co field `content` bao ngoai

Shape hien tai:

```json
{
  "type": "final_metadata",
  "references": [
    {
      "title": "Reuters enterprise AI demand",
      "url": "https://www.reuters.com/technology/enterprise-ai-demand",
      "source_domain": "www.reuters.com"
    }
  ],
  "caveats": ["Research coverage: insufficient"],
  "suggested_next_questions": [
    "Nhom khach hang nao dang tao nhu cau AI lon nhat?",
    "Rui ro canh tranh nao nha dau tu can theo doi?",
    "Can xac minh them tin hieu nao trong 6-12 thang toi?"
  ],
  "writer_notes": ["writer_used_previous_context"],
  "processing_warnings": ["source_selection_heuristic_fast_path"],
  "grounding_summary": {
    "verified_claim_count": 2,
    "weakly_supported_claim_count": 1,
    "conflicting_claim_count": 0,
    "unsupported_claim_count": 0,
    "reference_count": 1,
    "coverage_status": "sufficient"
  }
}
```

Y nghia cac field:

- `references`: danh sach source cuoi cung dung de grounding answer
- `caveats`: cac luu y ve pham vi, conflict, hoac do day cua evidence
- `suggested_next_questions`: 0-3 cau hoi goi y tiep theo
- `writer_notes`: ghi chu noi bo tu writer/fallback
- `processing_warnings`: canh bao ky thuat/noi bo
- `grounding_summary`: thong ke muc do grounding

### `error`

```json
{
  "type": "error",
  "content": "Request timed out. The research pipeline took too long to complete.",
  "correlation_id": "..."
}
```

### `[DONE]`

```text
data: [DONE]
```

Nhan marker nay thi stream ket thuc.

## 7. Timeout va HTTP status

Internal timeout cua pipeline dang la `240s` o [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:24).

Neu timeout xay ra ben trong pipeline:

- HTTP status thuong van la `200`
- server phat SSE `type: "error"`
- sau do van phat `[DONE]`

Dieu nay rat quan trong cho BE/FE:

- khong duoc chi dua vao HTTP status de ket luan request thanh cong
- phai parse event `error` trong stream

HTTP `504` chi co the xay ra neu loi nam o lop ngoai:

- reverse proxy
- API gateway
- load balancer
- BE proxy cua ban
- client timeout som hon AI service

## 8. Hanh vi nghiep vu ma FE can biet

### Greeting

Neu user gui nhu:

- `Hi`
- `Hello`
- `Xin chao`
- `Chao ban`

agent se short-circuit som va tra loi chao Fami. FE nen render no nhu mot answer binh thuong.

### Out-of-scope

Neu query nam ngoai pham vi investor research:

- stream se short-circuit som
- `references` thuong la `[]`
- `grounding_summary.coverage_status` thuong la `insufficient`

### Follow-up

Neu user hoi tiep trong cung `thread_id`, agent co the:

- reuse context cu
- resolve cau hoi ngan/phu thuoc ngu canh
- hoac quyet dinh search lai neu doi entity, doi thi truong, doi recency

### `suggested_next_questions`

Field nay vua duoc them vao contract.

Can hieu dung:

- day la mang `string[]`
- co the co toi da `3` phan tu
- co the la `[]` neu grounding yeu, fallback qua nhieu, hoac agent quyet dinh khong nen sinh goi y

FE nen:

- render neu mang co item
- an khu goi y neu mang rong
- khong coi `[]` la loi

## 9. Huong dan cho Backend

### Flow BE nen dung

1. Nhan `query` va conversation id tu FE.
2. Tao hoac map sang `thread_id`.
3. Goi `POST /api/v1/investor-agent/chat/stream`.
4. Forward SSE stream ve FE.
5. Khi nhan `final_answer` va `final_metadata`, co the luu transcript vao DB.
6. Neu nhan `error`, log `correlation_id`.

### Header va timeout

BE nen gui:

- `Content-Type: application/json`
- `Accept: text/event-stream`
- `X-Internal-Token`

BE proxy nen dat timeout > `240s`, vi AI service dang tu timeout ben trong o `240s`.

### .NET proxy sample

```csharp
[ApiController]
[Route("api/investor-agent")]
public class InvestorAgentProxyController : ControllerBase
{
    private readonly HttpClient _httpClient;
    private readonly IConfiguration _config;

    public InvestorAgentProxyController(IHttpClientFactory factory, IConfiguration config)
    {
        _httpClient = factory.CreateClient("AisepAi");
        _config = config;
    }

    [HttpPost("chat/stream")]
    public async Task Stream([FromBody] InvestorChatRequest request, CancellationToken ct)
    {
        using var upstream = new HttpRequestMessage(HttpMethod.Post, "/api/v1/investor-agent/chat/stream");
        upstream.Headers.Accept.ParseAdd("text/event-stream");
        upstream.Headers.Add("X-Internal-Token", _config["AISEP_INTERNAL_TOKEN"]);
        upstream.Content = JsonContent.Create(new
        {
            query = request.Query,
            thread_id = request.ThreadId
        });

        using var response = await _httpClient.SendAsync(
            upstream,
            HttpCompletionOption.ResponseHeadersRead,
            ct
        );

        if (!response.IsSuccessStatusCode)
        {
            Response.StatusCode = (int)response.StatusCode;
            Response.ContentType = response.Content.Headers.ContentType?.ToString() ?? "application/json";
            await response.Content.CopyToAsync(Response.Body);
            return;
        }

        Response.StatusCode = (int)response.StatusCode;
        Response.ContentType = "text/event-stream; charset=utf-8";

        await using var source = await response.Content.ReadAsStreamAsync(ct);
        await source.CopyToAsync(Response.Body, ct);
        await Response.Body.FlushAsync(ct);
    }
}

public class InvestorChatRequest
{
    public string Query { get; set; } = "";
    public string ThreadId { get; set; } = "";
}
```

### Luu y production cho BE

- Dung `HttpCompletionOption.ResponseHeadersRead`
- Khong buffer toan bo response
- Khong parse xong roi moi re-serialize neu khong can thiet
- Khi upstream tra JSON error envelope truoc khi stream bat dau, nen pass-through nguyen body
- Nhat ky nen log `thread_id`, `correlation_id`, `user_id`, `conversation_id`

### JSON error envelope truoc khi vao stream

Neu request fail truoc khi stream bat dau, API se tra JSON envelope nhu:

```json
{
  "code": "AUTH_FAILED",
  "message": "Invalid or missing internal token.",
  "retryable": false,
  "correlation_id": "..."
}
```

Thuong gap:

- `401 AUTH_FAILED`
- `422 VALIDATION_ERROR`
- `429 RATE_LIMIT_EXCEEDED`

## 10. Huong dan cho Frontend

### Flow FE nen dung

1. User nhap cau hoi.
2. FE lay `conversationId` hien tai.
3. FE goi endpoint proxy cua BE.
4. FE parse SSE line-by-line.
5. FE cap nhat UI theo event.

### FE khong nen lam

- khong dung `EventSource` vi day la `POST`
- khong expose `X-Internal-Token` tren browser production
- khong reset `thread_id` sau moi tin nhan neu muon giu memory
- khong gia dinh `final_metadata` co field `content`
- khong gia dinh `suggested_next_questions` luc nao cung co 3 item

### TypeScript contract sample

```ts
type InvestorStreamEvent =
  | { type: "progress"; node: string }
  | { type: "answer_chunk"; content: string }
  | { type: "final_answer"; content: string }
  | {
      type: "final_metadata";
      references: Array<{ title: string; url: string; source_domain: string }>;
      caveats: string[];
      suggested_next_questions: string[];
      writer_notes: string[];
      processing_warnings: string[];
      grounding_summary: {
        verified_claim_count: number;
        weakly_supported_claim_count: number;
        conflicting_claim_count: number;
        unsupported_claim_count: number;
        reference_count: number;
        coverage_status: "sufficient" | "insufficient" | "conflicting";
      };
    }
  | { type: "error"; content: string; correlation_id?: string };
```

### Client sample bang `fetch()` + `ReadableStream`

```ts
export async function streamInvestorAgent(
  query: string,
  threadId: string,
  handlers: {
    onProgress?: (node: string) => void;
    onChunk?: (text: string) => void;
    onFinalAnswer?: (text: string) => void;
    onMetadata?: (meta: Extract<InvestorStreamEvent, { type: "final_metadata" }>) => void;
    onError?: (message: string, correlationId?: string) => void;
  }
) {
  const response = await fetch("/api/investor-agent/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, thread_id: threadId })
  });

  if (!response.ok) {
    const err = await response.json().catch(() => null);
    throw new Error(err?.message ?? `HTTP ${response.status}`);
  }

  const reader = response.body?.getReader();
  const decoder = new TextDecoder("utf-8");
  if (!reader) throw new Error("ReadableStream is not available.");

  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame
        .split("\n")
        .find((x) => x.startsWith("data: "));

      if (!line) continue;
      const payload = line.slice(6);

      if (payload === "[DONE]") return;

      const event = JSON.parse(payload) as InvestorStreamEvent;

      if (event.type === "progress") handlers.onProgress?.(event.node);
      if (event.type === "answer_chunk") handlers.onChunk?.(event.content);
      if (event.type === "final_answer") handlers.onFinalAnswer?.(event.content);
      if (event.type === "final_metadata") handlers.onMetadata?.(event);
      if (event.type === "error") handlers.onError?.(event.content, event.correlation_id);
    }
  }
}
```

### Goi y UI

#### Bubble answer

- dang stream: noi `answer_chunk`
- khi co `final_answer`: replace lai bang ban cuoi

#### Progress

Map node sang nhan de hieu:

- `followup_resolver` -> `Dang hieu ngu canh`
- `router` -> `Dang xac dinh y dinh`
- `planner` -> `Dang lap ke hoach tim kiem`
- `search` -> `Dang tim nguon`
- `source_selection` -> `Dang chon nguon`
- `extract` -> `Dang doc noi dung`
- `fact_builder` -> `Dang trich xuat thong tin`
- `claim_verifier` -> `Dang doi chieu bang chung`
- `writer` -> `Dang tong hop tra loi`
- `scope_guard` -> `Dang kiem tra pham vi`

#### References

Nen render:

- `title`
- `source_domain`
- link `url`

Neu `references=[]` thi an section nay.

#### Caveats

Nen render rieng thanh khu `Luu y`.

#### Suggested questions

Neu `suggested_next_questions.length > 0`:

- render thanh 3 button/quick replies
- click vao se gui tiep cung `thread_id`

Neu mang rong:

- an section
- khong hien placeholder loi

#### Processing warnings

Khong nen show truc tiep cho end user.

Co the dua vao:

- console
- telemetry
- debug panel cho internal QA

## 11. Giai thich nhanh ve grounding

`grounding_summary` giup UI hoac team BE hieu nhanh chat luong answer:

- `verified_claim_count`: so claim duoc support chac hon
- `weakly_supported_claim_count`: so claim support yeu hon
- `conflicting_claim_count`: so claim dang conflict
- `unsupported_claim_count`: so claim khong du support
- `reference_count`: so reference cuoi cung gan voi final answer
- `coverage_status`: `sufficient`, `insufficient`, hoac `conflicting`

Khuyen nghi UI:

- co the show `coverage_status`
- khong can show het cac con so neu giao dien huong consumer

## 12. Config van hanh quan trong

Can quan tam:

- `AISEP_INTERNAL_TOKEN`
- `REQUIRE_INTERNAL_AUTH`
- `CHECKPOINT_BACKEND`
- `CHECKPOINT_REDIS_URL`
- `CHECKPOINT_TTL_MINUTES`
- `RATE_LIMIT_STREAM_RPM`
- `TAVILY_API_KEY`
- `INVESTOR_AGENT_SEARCH_DEPTH`
- `INVESTOR_AGENT_MAX_RESULTS_PER_QUERY`
- `INVESTOR_AGENT_LLM_SOURCE_SELECTION`
- `INVESTOR_AGENT_MAX_REPAIR_LOOPS`
- `CORS_ORIGINS`
- `CORS_ALLOW_ALL`

Doc them:

- [docs/integration_handoff/env_and_config_reference.md](C:/Users/LENOVO/Desktop/AISEP_AI/docs/integration_handoff/env_and_config_reference.md:1)

### Goi y moi truong

#### Local dev

- `REQUIRE_INTERNAL_AUTH=false` hoac FE/BE gui dung token
- `CHECKPOINT_BACKEND=memory`
- `CORS_ALLOW_ALL=true`

#### Shared dev / staging / production

- `REQUIRE_INTERNAL_AUTH=true`
- `AISEP_INTERNAL_TOKEN` phai duoc set dung
- `CHECKPOINT_BACKEND=redis`
- `CHECKPOINT_TTL_MINUTES` dat theo nhu cau session
- `CORS_ALLOW_ALL=false`

## 13. Checklist handoff cho BE va FE

### Backend

- proxy stream thay vi de FE goi truc tiep production
- quan ly `thread_id` theo conversation
- gui `X-Internal-Token`
- set timeout > `240s`
- pass-through SSE frame
- luu `final_answer` va `final_metadata`
- log `correlation_id` khi co `error`

### Frontend

- giu `thread_id` on dinh trong 1 conversation
- parse SSE stream dung cach
- render `progress`
- render `answer_chunk`
- chot lai bang `final_answer`
- render `references`
- render `caveats`
- render `suggested_next_questions` neu co
- xu ly `error` trong stream, khong chi HTTP status
- coi greeting/out-of-scope nhu answer hop le

## 14. Kich ban test tay de handoff

### Case 1. Greeting

Request:

```json
{
  "query": "Hello",
  "thread_id": "demo-greeting"
}
```

Ky vong:

- stream co `progress`
- `final_answer` la loi chao Fami
- `references=[]`
- `suggested_next_questions=[]`

### Case 2. In-scope single turn

Request:

```json
{
  "query": "Xu huong dau tu AI tai Viet Nam nam 2026 la gi?",
  "thread_id": "demo-research-1"
}
```

Ky vong:

- di qua day du cac node research
- co `final_answer`
- co `final_metadata.references`
- co `grounding_summary`

### Case 3. Follow-up same thread

Request 1:

```json
{
  "query": "Xu huong fintech Dong Nam A 2026",
  "thread_id": "demo-followup"
}
```

Request 2:

```json
{
  "query": "Viet Nam thi sao?",
  "thread_id": "demo-followup"
}
```

Ky vong:

- turn 2 van dung context cua turn 1
- answer tap trung hon vao Viet Nam

### Case 4. Out-of-scope

Request:

```json
{
  "query": "Thoi tiet hom nay the nao?",
  "thread_id": "demo-oos"
}
```

Ky vong:

- short-circuit som
- `references=[]`
- `suggested_next_questions=[]`

### Case 5. Timeout handling

Ky vong:

- neu pipeline timeout ben trong AI service, stream van la HTTP `200`
- se co event `type: "error"`
- sau do co `[DONE]`

## 15. Ket luan

De tinh nang chatbot investor agent hoat dong on cho ca BE va FE, 3 diem quan trong nhat la:

- dung `thread_id` dung cach
- parse SSE stream dung cach
- render day du ca `final_answer` va `final_metadata`

Neu team can, co the tach tiep guide nay thanh 2 file rieng:

- `investor_agent_be_guide.md`
- `investor_agent_fe_guide.md`
