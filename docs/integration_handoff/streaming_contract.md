# Streaming Contract

## Endpoint

- `POST /api/v1/investor-agent/chat/stream`
- Implemented in: `src/modules/investor_agent/api/router.py`
- Response type: `StreamingResponse(..., media_type="text/event-stream")`

## Request body

`ChatRequest`

```json
{
  "query": "...",
  "thread_id": "conversation-123"
}
```

## Transport format

Server sends SSE frames as:

```text
data: <json or [DONE]>

```

## Event types observed in code

### 1) Progress event

```json
{ "type": "progress", "node": "<node_name>" }
```

- emitted on `on_chain_start` for graph nodes in:
  - `followup_resolver`, `router`, `planner`, `search`, `source_selection`, `extract`, `fact_builder`, `claim_verifier`, `writer`
- extra progress event `scope_guard` emitted when router ends with `intent=out_of_scope`

### 2) Answer chunk

```json
{ "type": "answer_chunk", "content": "..." }
```

- produced by chunking final answer text (chunk size ~180 chars)

### 3) Final answer

```json
{ "type": "final_answer", "content": "..." }
```

### 4) Final metadata

```json
{
  "type": "final_metadata",
  "references": [{ "title": "...", "url": "...", "source_domain": "..." }],
  "caveats": ["..."],
  "writer_notes": ["..."],
  "processing_warnings": ["..."],
  "grounding_summary": {
    "verified_claim_count": 0,
    "weakly_supported_claim_count": 0,
    "conflicting_claim_count": 0,
    "unsupported_claim_count": 0,
    "reference_count": 0,
    "coverage_status": "sufficient|insufficient|conflicting"
  }
}
```

### 5) Error event

```json
{ "type": "error", "content": "<message>" }
```

- emitted in exception block inside stream generator

### 6) Done marker

```text
data: [DONE]
```

- always emitted at end of generator

## Parsing recommendation for .NET/UI

- Parse each SSE `data:` payload
- If payload is `[DONE]`, close stream gracefully
- For JSON payloads switch by `type`
- Do not assume all event types will appear for every request

## Recovery behavior observed

- If graph end payload has empty `final_answer`, router attempts to recover from LangGraph checkpointer state (`aget_state`)
- If still empty, final assembler fallback produces safe generic answer
