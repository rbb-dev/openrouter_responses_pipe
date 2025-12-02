# streaming engine & emitters

**file:** `docs/streaming_pipeline_and_emitters.md`
**related source:** `openrouter_responses_pipe/openrouter_responses_pipe.py:3600-5200` (approx), `SessionLogger` near the file footer

The streaming layer converts OpenRouter"s Server-Sent Events (SSE) into the incremental updates Open WebUI expects: text deltas, reasoning traces, tool prompts, citations, notifications, and final usage stats. This document explains every moving part so you can tune latency, add new event types, or debug edge cases without spelunking through the file.

---

## 1. pipeline overview

```
OpenRouter SSE → chunk queue → worker pool → event queue → emitters → Open WebUI client
```

1. `_stream_responses` opens the `/responses` SSE stream via `httpx` (configured with per-request timeouts and retry wrappers). Raw bytes go into `chunk_queue` (bounded by `STREAMING_CHUNK_QUEUE_MAXSIZE`).
2. `_consume_sse` spawns `SSE_WORKERS_PER_REQUEST` parser tasks. Each worker pulls from `chunk_queue`, parses SSE frames, normalizes OpenRouter event types, and pushes structured dicts into `event_queue` (bounded by `STREAMING_EVENT_QUEUE_MAXSIZE`). Sequence numbers keep events in order even when multiple workers parse in parallel.
3. `_drain_event_queue` reads parsed events, updates internal state (text buffer, reasoning segments, pending tool calls), and calls the relevant emitter helpers (`_emit_update`, `_emit_status`, `_emit_citation`, `_emit_tool_prompt`, `_emit_completion`).
4. Emitters send Open WebUI-compatible payloads back to the client via the `event_emitter` callable the platform passes in.

---

## 2. update batching & latency knobs

| Setting | Default | Effect |
| --- | --- | --- |
| `STREAMING_UPDATE_PROFILE` | `None` (respect char + idle valves) | Shortcut for the presets defined in `_STREAMING_PRESETS` (`quick`, `normal`, `slow`). Applies both `char_limit` and `idle_flush_ms`. |
| `STREAMING_UPDATE_CHAR_LIMIT` | 20 chars | Maximum characters per `_emit_update` batch. Lower = lower latency with more events. |
| `STREAMING_IDLE_FLUSH_MS` | 250 ms | Idle watchdog. If no new text arrives before the timer expires, the buffer flushes even if it hasn"t hit the char limit. |
| User overrides | Same fields exist on `Pipe.UserValves`. Individuals can pick a different preset/limit without affecting others. |

Buffers also respect Unicode boundaries: `_emit_update` tracks surrogate pairs and waits for both halves of a UTF-16 pair before flushing so emoji and CJK characters are never split.

---

## 3. event types

| Event | Source | Description |
| --- | --- | --- |
| `chat:message` | `_emit_update` | Streaming deltas for assistant text. Includes `message_id`, `content_block`, cursor position, and whether the chunk is final. |
| `chat:status` | `_emit_status` | Human-readable progress updates ("📥 downloading image...", "⚙️ running tool X", "⚠️ tool breaker tripped"). Also used to warn about ignored attachments or degraded modes. |
| `chat:citation` | `_emit_citation` | Structured citation payloads carrying title, snippet, URL, and the ULID marker they originated from. Used for web search and other tools that produce references. |
| `chat:notification` | `_emit_notification` | Side-panel notifications summarizing long-running operations or warnings. |
| `chat:completion` | `_emit_completion` | Final frame summarizing elapsed time, cost, token usage, tokens/sec, and any outstanding warnings. Also replays the last assistant snapshot so concurrent workers or downstream emitters cannot blank the UI. Honors `SHOW_FINAL_USAGE_STATUS` valves. |

Tool prompts and results reuse the same event types but carry metadata (`tool_name`, `call_id`, etc.) so the Open WebUI client can render the "Tool call requested..." UI.

---

## 4. disconnect & error handling

* **Client disconnects** -- `_wrap_event_emitter` shields emitters from `RuntimeError: Event loop is closed` and similar issues. If the downstream websocket dies, the pipe logs a warning but keeps draining events so resources clean up gracefully.
* **OpenRouter hiccups** -- `_stream_responses` wraps `httpx` with Tenacity. Retryable HTTP errors raise `_RetryableHTTPStatusError`, which carries a `retry_after` hint so Tenacity honors the provider"s backoff instructions.
* **Queue pressure** -- When `chunk_queue` or `event_queue` fill up, backpressure propagates upstream by awaiting queue put operations. This keeps memory bounded and forces OpenRouter to slow down instead of dropping events.
* **Warm shutdown** -- If Open WebUI cancels the request future, `_request_worker_loop` cancels the streaming task, `_shutdown_tool_context` drains tool workers, and `_flush_pending` emits any buffered text before returning an error to the user.
* **Suppressing event types** -- `_wrap_event_emitter(... suppress_chat_messages=True/False ...)` lets the same streaming loop power both streamed and non-streamed calls by optionally swallowing `chat:message` and/or `chat:completion` frames while still emitting status/citation/usage events.

---

## 5. usage aggregation

The streaming engine continuously merges usage fields emitted by OpenRouter (`input_tokens`, `output_tokens`, `total_cost`, etc.) via `_merge_usage_stats`. When `_emit_completion` fires it includes the final totals plus derived metrics (tokens/sec, elapsed time) so admins can audit expensive runs. The same stats feed `SessionLogger` so per-request log buffers capture the numbers alongside textual output.

---

## 6. adding new emitters

1. Decide whether the new signal is **best-effort** (log + status) or **contractual** (documented event type). For contractual events, update both this file and `docs/documentation_index.md` so clients can rely on the behavior.
2. Extend `_drain_event_queue` to recognize the new OpenRouter event or internal signal. Keep decoding + business logic separate from the emitter to ease testing.
3. Implement an emitter helper that builds the JSON payload and handles optional fields defensively. Follow the naming convention `_emit_<thing>` and make sure it no-ops when `event_emitter is None`.
4. Add valves/user overrides if the feature has tunable latency or verbosity knobs.
5. Update tests (or add new ones) to cover the routing + emitter behavior.

---

## 7. logging synergy (`SessionLogger`)

`SessionLogger` attaches per-request metadata (session ID, user ID, log level) to every log record, ships them through an async queue, and exposes helper methods for in-band citations. When `_emit_error` references `SessionLogger.flush_for_citation()`, users see a summarized stack trace in the UI while operators get the full log on disk.

Remember: streaming is where UX problems surface first. Keep the queues bounded, surface warnings early, and document every new event type here so UI engineers and operators stay aligned.
