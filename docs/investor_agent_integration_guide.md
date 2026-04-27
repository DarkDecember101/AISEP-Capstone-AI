# Investor Agent Integration Guide

## 1. Muc tieu

Tai lieu nay huong dan cach de Backend va Frontend su dung day du tinh nang cua `investor_agent` hien co trong code base.

Module nay hien dang ho tro:

- Chat research theo luong streaming.
- Multi-turn memory theo `thread_id`.
- Tu dong phan biet greeting, follow-up, out-of-scope.
- Tra ve cau tra loi cuoi cung kem references, caveats, grounding summary.
- Phat progress event theo tung node de FE hien loading state chi tiet.

## 2. Endpoint hien co

API dang expose 1 entry point chinh:

- `POST /api/v1/investor-agent/chat/stream`

Nguon:

- API mount tai [src/main.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/main.py:61)
- Router tai [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:175)

## 3. Request contract

### URL

```text
POST /api/v1/investor-agent/chat/stream
```

### Headers

- `Content-Type: application/json`
- `Accept: text/event-stream`
- `X-Internal-Token: <token>`
  - chi can khi server bat `REQUIRE_INTERNAL_AUTH=true`
  - auth rule tai [src/shared/auth.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/auth.py:34)

### Body

```json
{
  "query": "Xu hướng AI tại Việt Nam 2026",
  "thread_id": "investor-chat-001"
}
```

### Rule cho `thread_id`

- Chi duoc dung `a-z`, `A-Z`, `0-9`, `_`, `-`
- Do dai 1-128 ky tu
- Validate tai [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:69)

### Y nghia `thread_id`

- Cung `thread_id` => cung mot hoi thoai, agent se tai su dung memory
- Doi `thread_id` => hoi thoai moi
- Nen generate 1 `thread_id` theo `user/session/conversation`

## 4. Response transport

Endpoint nay khong tra JSON mot lan. No tra **SSE stream** (`text/event-stream`) qua cac frame:

```text
data: {...json...}

data: {...json...}

data: [DONE]
```

Luu y:

- Day la `POST` stream, vi vay FE **khong dung `EventSource` mac dinh**
- FE nen dung `fetch()` + `ReadableStream`
- BE neu proxy stream thi nen forward tung dong SSE ve FE

## 5. Cac event FE can xu ly

### 5.1 `progress`

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

`scope_guard` la progress event bo sung khi query bi xep `out_of_scope`.

### 5.2 `answer_chunk`

```json
{ "type": "answer_chunk", "content": "..." }
```

Dung de FE render text dan dan trong bong chat.

### 5.3 `final_answer`

```json
{ "type": "final_answer", "content": "..." }
```

Day la cau tra loi day du sau cung.

### 5.4 `final_metadata`

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
    "Nhung phan khuc khach hang nao dang thuc su tao nhu cau AI lon nhat tai khu vuc nay?",
    "Rui ro canh tranh nao nha dau tu nen theo doi trong 12 thang toi?",
    "Chinh sach hoac quy dinh nao co the tac dong manh den toc do tang truong cua thi truong nay?"
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

### 5.5 `error`

```json
{
  "type": "error",
  "content": "Request timed out. The research pipeline took too long to complete.",
  "correlation_id": "..."
}
```

### 5.6 `[DONE]`

```text
data: [DONE]
```

Khi nhan marker nay, FE/BE dong stream va ket thuc request.

## 6. Hanh vi nghiep vu quan trong

### Greeting

Neu user nhan:

- `Hi`
- `Hello`
- `Xin chao`
- `Xin chào`
- hoac greeting ngan tuong tu

agent se tra ve loi chao Fami ngay, khong chay full research pipeline.

FE nen xem day la answer hop le binh thuong, khong can treat nhu error.

### Out of scope

Neu query nam ngoai pham vi investor research, agent se:

- short-circuit som
- tra 1 cau tu choi lich su
- references = `[]`
- grounding summary thuong la `insufficient`

### Follow-up / multi-turn

Neu user hoi tiep trong cung `thread_id`, agent co the:

- tu dong resolve follow-up
- reuse mot phan context cu
- hoac bat fresh search neu doi quoc gia/thi truong/truy van recency

Vi vay:

- FE khong can tu nop lai lich su hoi thoai
- FE/BE chi can giu dung `thread_id`

## 7. Huong dan cho Backend

## 7.1 Khuyen nghi production

Nen dung pattern:

```text
Frontend -> Backend cua ban -> AISEP AI /investor-agent/chat/stream
```

Ly do:

- Giu `X-Internal-Token` o BE, khong expose ra FE
- Co the auth user, audit, quota, log, save transcript tai he thong cua ban
- Co the map SSE event ve UI contract rieng neu can

## 7.2 Viec BE can lam

1. Nhan `query` va `thread_id` tu FE.
2. Kiem tra user/session co quyen chat hay khong.
3. Tao hoac validate `thread_id`.
4. Goi `POST /api/v1/investor-agent/chat/stream`.
5. Forward SSE stream nguyen van hoac map lai cho FE.
6. Khi nhan `final_answer` va `final_metadata`, co the luu transcript vao DB cua BE.

## 7.3 Header va timeout

BE nen set:

- `Content-Type: application/json`
- `Accept: text/event-stream`
- `X-Internal-Token` neu auth noi bo dang bat

Nen dat timeout o BE > 240 giay mot chut neu proxy streaming, vi AI service dang timeout noi bo tai:

- [src/modules/investor_agent/api/router.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/modules/investor_agent/api/router.py:29)

## 7.4 Vi du BE proxy bang .NET

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

### Note cho BE

- Dung `HttpCompletionOption.ResponseHeadersRead` de doc stream tung phan
- Khong doc het stream roi moi tra ve FE
- Neu upstream tra HTTP error JSON envelope, nen pass-through nguyen body cho FE

## 7.5 HTTP error envelope khi request khong vao duoc stream

Neu loi xay ra truoc khi stream bat dau, API tra JSON co shape:

```json
{
  "code": "AUTH_FAILED",
  "message": "Invalid or missing internal token.",
  "retryable": false,
  "correlation_id": "..."
}
```

Envelope tai:

- [src/shared/error_response.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/error_response.py:43)

Case thuong gap:

- `401 AUTH_FAILED`
- `422 VALIDATION_ERROR`
- `429 HTTP_ERROR`

## 8. Huong dan cho Frontend

## 8.1 Luong FE nen dung

1. User nhap cau hoi.
2. FE tao hoac lay `thread_id` cua conversation hien tai.
3. FE goi endpoint proxy cua BE.
4. FE parse SSE line-by-line.
5. FE:
   - cap nhat loading step tu `progress`
   - noi text tu `answer_chunk`
   - chot message tu `final_answer`
   - render references/caveats tu `final_metadata`

## 8.2 FE khong nen lam

- Khong dung `EventSource` cho endpoint nay vi day la `POST`
- Khong expose `X-Internal-Token` tren browser production
- Khong reset `thread_id` sau moi message neu muon giu memory

## 8.3 Vi du FE bang JavaScript/TypeScript

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

export async function streamInvestorAgent(
  query: string,
  threadId: string,
  handlers: {
    onProgress?: (node: string) => void;
    onChunk?: (text: string) => void;
    onFinalAnswer?: (text: string) => void;
    onMetadata?: (meta: Extract<InvestorStreamEvent, { type: "final_metadata" }>) => void;
    onError?: (message: string) => void;
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
      if (event.type === "error") handlers.onError?.(event.content);
    }
  }
}
```

## 8.4 Goi y render UI

### Bubble answer

- Trong luc stream, FE co the noi `answer_chunk` vao bubble dang mo
- Khi nhan `final_answer`, co the replace bang ban final de dam bao text dung 100%

### Progress badge / timeline

Map node sang label than thien:

- `followup_resolver` -> `Dang hieu ngu canh`
- `router` -> `Dang xac dinh y dinh`
- `planner` -> `Dang lap ke hoach tim kiem`
- `search` -> `Dang tim nguon`
- `source_selection` -> `Dang chon nguon`
- `extract` -> `Dang doc noi dung`
- `fact_builder` -> `Dang trich xuat su kien`
- `claim_verifier` -> `Dang doi chieu bang chung`
- `writer` -> `Dang tong hop cau tra loi`
- `scope_guard` -> `Dang kiem tra pham vi cau hoi`

### References

Render o cuoi answer:

- title
- domain
- click vao `url`

### Caveats

Nen render rieng thanh khung `Luu y` vi day la canh bao chat luong evidence.

### Grounding summary

Co the render thanh nho gon:

- `verified_claim_count`
- `weakly_supported_claim_count`
- `conflicting_claim_count`
- `coverage_status`

### Processing warnings

Khong nhat thiet show cho end user.

Khuyen nghi:

- FE khong hien truc tiep cho user thuong
- Co the log vao console / telemetry / debug panel

## 9. Full feature checklist cho BE va FE

De noi da dang dung "tron ven" tinh nang cua `investor_agent`, nen co:

### Backend

- Proxy stream thay vi de FE goi truc tiep production
- Quan ly `thread_id` theo conversation
- Luu transcript va metadata
- Pass-through SSE event
- Bat auth noi bo neu deploy internal
- Theo doi `correlation_id` khi error

### Frontend

- Ho tro multi-turn chat bang `thread_id` on dinh
- Parse SSE `POST` stream
- Hien progress
- Hien streaming text
- Hien references
- Hien caveats
- Hien state cho greeting / out-of-scope nhu mot answer binh thuong
- Hien retry UI neu nhan `error`

## 10. Config van hanh quan trong

Settings lien quan:

- `AISEP_INTERNAL_TOKEN`
- `REQUIRE_INTERNAL_AUTH`
- `CHECKPOINT_BACKEND`
- `CHECKPOINT_TTL_MINUTES`
- `RATE_LIMIT_STREAM_RPM`
- `TAVILY_API_KEY`
- `INVESTOR_AGENT_SEARCH_DEPTH`
- `INVESTOR_AGENT_MAX_RESULTS_PER_QUERY`
- `INVESTOR_AGENT_LLM_SOURCE_SELECTION`
- `INVESTOR_AGENT_MAX_REPAIR_LOOPS`
- `CORS_ORIGINS`
- `CORS_ALLOW_ALL`

Nguon:

- [src/shared/config/settings.py](C:/Users/LENOVO/Desktop/AISEP_AI/src/shared/config/settings.py:29)

### Goi y moi truong

#### Local dev

- `REQUIRE_INTERNAL_AUTH=false`
- `CHECKPOINT_BACKEND=memory`
- `CORS_ALLOW_ALL=true`

#### Shared dev / staging / production

- `REQUIRE_INTERNAL_AUTH=true`
- `AISEP_INTERNAL_TOKEN` phai duoc set
- `CHECKPOINT_BACKEND=redis`
- `CHECKPOINT_TTL_MINUTES` dat theo nhu cau session
- `CORS_ALLOW_ALL=false`

## 11. Kich ban test tay de handoff

### Case 1. Greeting

Request:

```json
{
  "query": "Hello",
  "thread_id": "demo-greeting"
}
```

Ky vong:

- stream co `progress: followup_resolver`, `progress: router`, `progress: scope_guard`, `progress: writer`
- `final_answer` la loi chao Fami
- `references=[]`

### Case 2. In-scope single turn

Request:

```json
{
  "query": "Xu hướng đầu tư AI tại Việt Nam năm 2026 là gì?",
  "thread_id": "demo-research-1"
}
```

Ky vong:

- di qua du cac node research
- co `final_metadata.references`
- co `grounding_summary`

### Case 3. Follow-up same thread

Request 1:

```json
{
  "query": "Xu hướng fintech Đông Nam Á 2026",
  "thread_id": "demo-followup"
}
```

Request 2:

```json
{
  "query": "Việt Nam thì sao?",
  "thread_id": "demo-followup"
}
```

Ky vong:

- turn 2 van dung context cua turn 1
- answer tap trung hon vao Vietnam

### Case 4. Out-of-scope

Request:

```json
{
  "query": "Thời tiết hôm nay thế nào?",
  "thread_id": "demo-oos"
}
```

Ky vong:

- short-circuit som
- cau tra loi tu choi pham vi

## 12. Ket luan

Neu muon dung day du tinh nang cua `investor_agent`, diem quan trong nhat la:

- BE giu `thread_id` dung cach va proxy stream
- FE parse SSE `POST` stream dung cach
- UI render ca `final_answer` va `final_metadata`, khong chi text

Neu can bo tai lieu nay thanh API contract ngan hon cho team FE hoac bo sequence diagram cho team BE, co the tach tiep thanh 2 file rieng.
