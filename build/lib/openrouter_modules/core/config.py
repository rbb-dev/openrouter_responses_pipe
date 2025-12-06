"""Configuration module for OpenRouter Responses Pipe.

This module defines the valve configurations (system-wide and per-user settings)
that control the behavior of the pipe. All configuration lives here to provide
a single source of truth for available settings.

Layer: core (no external dependencies except pydantic)
"""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

# Import EncryptedStr from parent scope (will be defined in core/__init__.py)
from .encryption import EncryptedStr


# Constants for file size limits (used in valves)
_REMOTE_FILE_MAX_SIZE_DEFAULT_MB = 50
_REMOTE_FILE_MAX_SIZE_MAX_MB = 500


class Valves(BaseModel):
    """Global valve configuration shared across sessions.

    These valves control system-wide behavior and can be configured by administrators
    in the Open WebUI interface. User-specific overrides are available via UserValves.
    """

    # ─────────────────────────────────────────────────────────────────────
    # Connection & Authentication
    # ─────────────────────────────────────────────────────────────────────

    BASE_URL: str = Field(
        default=((os.getenv("OPENROUTER_API_BASE_URL") or "").strip() or "https://openrouter.ai/api/v1"),
        description="OpenRouter API base URL. Override this if you are using a gateway or proxy.",
    )
    API_KEY: EncryptedStr = Field(
        default=(os.getenv("OPENROUTER_API_KEY") or "").strip(),
        description="Your OpenRouter API key. Defaults to the OPENROUTER_API_KEY environment variable.",
    )
    HTTP_CONNECT_TIMEOUT_SECONDS: int = Field(
        default=10,
        ge=1,
        description="Seconds to wait for the TCP/TLS connection to OpenRouter before failing.",
    )
    HTTP_TOTAL_TIMEOUT_SECONDS: Optional[int] = Field(
        default=None,
        ge=1,
        description="Overall HTTP timeout (seconds) for OpenRouter requests. Set to null to disable the total timeout so long-running streaming responses are not interrupted.",
    )
    HTTP_SOCK_READ_SECONDS: int = Field(
        default=300,
        ge=1,
        description="Idle read timeout (seconds) applied to active streams when HTTP_TOTAL_TIMEOUT_SECONDS is disabled. Generous default favors UX for slow providers.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Remote File/Image Download Settings
    # ─────────────────────────────────────────────────────────────────────

    REMOTE_DOWNLOAD_MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of retry attempts for downloading remote images and files. Set to 0 to disable retries.",
    )
    REMOTE_DOWNLOAD_INITIAL_RETRY_DELAY_SECONDS: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Initial delay in seconds before the first retry attempt. Subsequent retries use exponential backoff (delay * 2^attempt).",
    )
    REMOTE_DOWNLOAD_MAX_RETRY_TIME_SECONDS: int = Field(
        default=45,
        ge=5,
        le=300,
        description="Maximum total time in seconds to spend on retry attempts. Retries will stop if this time limit is exceeded.",
    )
    REMOTE_FILE_MAX_SIZE_MB: int = Field(
        default=_REMOTE_FILE_MAX_SIZE_DEFAULT_MB,
        ge=1,
        le=_REMOTE_FILE_MAX_SIZE_MAX_MB,
        description="Maximum size in MB for downloading remote files/images. Files exceeding this limit are skipped. When Open WebUI RAG is enabled, the pipe automatically caps downloads to Open WebUI's FILE_MAX_SIZE (if set).",
    )
    SAVE_REMOTE_FILE_URLS: bool = Field(
        default=False,
        description="When True, remote URLs and data URLs in the file_url field are downloaded/parsed and re-hosted in Open WebUI storage. When False, file_url values pass through untouched. Note: This valve only affects the file_url field; see SAVE_FILE_DATA_CONTENT for file_data behavior. Recommended: Keep disabled to avoid unexpected storage growth.",
    )
    SAVE_FILE_DATA_CONTENT: bool = Field(
        default=True,
        description="When True, base64 content and URLs in the file_data field are parsed/downloaded and re-hosted in Open WebUI storage to prevent chat history bloat. When False, file_data values pass through untouched. Recommended: Keep enabled to avoid large inline payloads in chat history.",
    )
    BASE64_MAX_SIZE_MB: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum size in MB for base64-encoded files/images before decoding. Larger payloads will be rejected to prevent memory issues and excessive HTTP request sizes.",
    )
    IMAGE_UPLOAD_CHUNK_BYTES: int = Field(
        default=1 * 1024 * 1024,
        ge=64 * 1024,
        le=8 * 1024 * 1024,
        description="Maximum number of bytes to buffer at a time when loading Open WebUI-hosted images before forwarding them to a provider. Lower values reduce peak memory usage when multiple users edit images concurrently.",
    )
    VIDEO_MAX_SIZE_MB: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum size in MB for video files (remote URLs or data URLs). Videos exceeding this limit will be rejected to prevent memory and bandwidth issues.",
    )
    FALLBACK_STORAGE_EMAIL: str = Field(
        default=(os.getenv("OPENROUTER_STORAGE_USER_EMAIL") or "openrouter-pipe@system.local"),
        description="Owner email used when multimodal uploads occur without a chat user (e.g., API automations).",
    )
    FALLBACK_STORAGE_NAME: str = Field(
        default=(os.getenv("OPENROUTER_STORAGE_USER_NAME") or "OpenRouter Pipe Storage"),
        description="Display name for the fallback storage owner.",
    )
    FALLBACK_STORAGE_ROLE: str = Field(
        default=(os.getenv("OPENROUTER_STORAGE_USER_ROLE") or "pending"),
        description="Role assigned to the fallback storage account when auto-created. Defaults to the low-privilege 'pending' role; override if your deployment needs a custom service role.",
    )
    ENABLE_SSRF_PROTECTION: bool = Field(
        default=True,
        description="Enable SSRF (Server-Side Request Forgery) protection for remote URL downloads. When enabled, blocks requests to private IP ranges (localhost, 192.168.x.x, 10.x.x.x, etc.) to prevent internal network probing.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Model Selection & Catalog
    # ─────────────────────────────────────────────────────────────────────

    MODEL_ID: str = Field(
        default="auto",
        description=(
            "Comma separated OpenRouter model IDs to expose in Open WebUI. "
            "Set to 'auto' to import every available Responses-capable model."
        ),
    )
    MODEL_CATALOG_REFRESH_SECONDS: int = Field(
        default=60 * 60,
        ge=60,
        description="How long to cache the OpenRouter model catalog (in seconds) before refreshing.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Reasoning Configuration
    # ─────────────────────────────────────────────────────────────────────

    ENABLE_REASONING: bool = Field(
        default=True,
        title="Show live reasoning",
        description="Request live reasoning traces whenever the selected model supports them.",
    )
    AUTO_CONTEXT_TRIMMING: bool = Field(
        default=True,
        title="Auto context trimming",
        description=(
            "When enabled, automatically attaches OpenRouter's `middle-out` transform so long prompts "
            "are trimmed from the middle instead of failing with context errors. Disable if your deployment "
            "manages `transforms` manually."
        ),
    )
    REASONING_EFFORT: Literal["minimal", "low", "medium", "high"] = Field(
        default="medium",
        title="Reasoning effort",
        description="Default reasoning effort to request from supported models. Higher effort spends more tokens to think through tough problems.",
    )
    REASONING_SUMMARY_MODE: Literal["auto", "concise", "detailed", "disabled"] = Field(
        default="auto",
        title="Reasoning summary",
        description="Controls the reasoning summary emitted by supported models (auto/concise/detailed). Set to 'disabled' to skip requesting reasoning summaries.",
    )
    PERSIST_REASONING_TOKENS: Literal["disabled", "next_reply", "conversation"] = Field(
        default="next_reply",
        title="Reasoning retention",
        description="Reasoning retention: 'disabled' keeps nothing, 'next_reply' keeps thoughts only until the following assistant reply finishes, and 'conversation' keeps them for the full chat history.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Tool Execution Configuration
    # ─────────────────────────────────────────────────────────────────────

    PERSIST_TOOL_RESULTS: bool = Field(
        default=True,
        title="Keep tool results",
        description="Persist tool call results across conversation turns. When disabled, tool results stay ephemeral.",
    )
    ARTIFACT_ENCRYPTION_KEY: EncryptedStr = Field(
        default="",
        description="Min 16 chars. Encrypt reasoning tokens (and optionally all persisted artifacts). Changing the key creates a new table; prior artifacts become inaccessible.",
    )
    ENCRYPT_ALL: bool = Field(
        default=False,
        description="Encrypt every persisted artifact when ARTIFACT_ENCRYPTION_KEY is set. When False, only reasoning tokens are encrypted.",
    )
    ENABLE_LZ4_COMPRESSION: bool = Field(
        default=True,
        description="When True (and lz4 is available), compress large encrypted artifacts to reduce database read/write overhead.",
    )
    MIN_COMPRESS_BYTES: int = Field(
        default=0,
        ge=0,
        description="Payloads at or above this size (in bytes) are candidates for LZ4 compression before encryption. The default 0 always attempts compression; raise the value to skip tiny payloads.",
    )
    ENABLE_STRICT_TOOL_CALLING: bool = Field(
        default=True,
        description=(
            "When True, converts Open WebUI registry tools to strict JSON Schema for OpenAI tools, "
            "enforcing explicit types, required fields, and disallowing additionalProperties."
        ),
    )
    MAX_FUNCTION_CALL_LOOPS: int = Field(
        default=10,
        description=(
            "Maximum number of full execution cycles (loops) allowed per request. "
            "Each loop involves the model generating one or more function/tool calls, "
            "executing all requested functions, and feeding the results back into the model. "
            "Looping stops when this limit is reached or when the model no longer requests "
            "additional tool or function calls."
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # Web Search Plugin
    # ─────────────────────────────────────────────────────────────────────

    ENABLE_WEB_SEARCH_TOOL: bool = Field(
        default=True,
        description="Enable the OpenRouter web-search plugin (id='web') when supported by the selected model.",
    )
    WEB_SEARCH_MAX_RESULTS: Optional[int] = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of web results to request when the web-search plugin is enabled (1-10). Set to null to use the provider default.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # MCP Servers (Experimental)
    # ─────────────────────────────────────────────────────────────────────

    REMOTE_MCP_SERVERS_JSON: Optional[str] = Field(
        default=None,
        description=(
            "[EXPERIMENTAL] A JSON-encoded list (or single JSON object) defining one or more "
            "remote MCP servers to be automatically attached to each request. This can be useful "
            "for globally enabling tools across all chats.\n\n"
            "Note: The Responses API currently caches MCP server definitions at the start of each chat. "
            "This means the first message in a new thread may be slower. A more efficient implementation is planned."
            "Each item must follow the MCP tool schema supported by the OpenAI Responses API, for example:\n"
            '[{"server_label":"deepwiki","server_url":"https://mcp.deepwiki.com/mcp","require_approval":"never","allowed_tools": ["ask_question"]}]'
        ),
    )

    # ─────────────────────────────────────────────────────────────────────
    # Logging & Concurrency
    # ─────────────────────────────────────────────────────────────────────

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default=os.getenv("GLOBAL_LOG_LEVEL", "INFO").upper(),
        description="Select logging level.  Recommend INFO or WARNING for production use. DEBUG is useful for development and debugging.",
    )
    MAX_CONCURRENT_REQUESTS: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="Maximum number of in-flight OpenRouter requests allowed per process.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Streaming Configuration
    # ─────────────────────────────────────────────────────────────────────

    SSE_WORKERS_PER_REQUEST: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Number of per-request SSE worker tasks that parse streamed chunks.",
    )
    STREAMING_CHUNK_QUEUE_MAXSIZE: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="Maximum number of raw SSE chunks buffered before applying backpressure to the OpenRouter stream.",
    )
    STREAMING_EVENT_QUEUE_MAXSIZE: int = Field(
        default=100,
        ge=10,
        le=5000,
        description="Maximum number of parsed SSE events buffered ahead of downstream processing.",
    )
    STREAMING_UPDATE_PROFILE: Optional[Literal["quick", "normal", "slow"]] = Field(
        default=None,
        description=(
            "Optional preset for streaming responsiveness. 'quick' prioritizes low latency, "
            "'normal' balances responsiveness, and 'slow' reduces event volume for constrained clients."
        ),
    )
    STREAMING_UPDATE_CHAR_LIMIT: int = Field(
        default=20,
        ge=10,
        le=500,
        description="Maximum characters to batch per streaming update. Lower values improve perceived latency.",
    )
    STREAMING_IDLE_FLUSH_MS: int = Field(
        default=250,
        ge=0,
        le=2000,
        description=(
            "Milliseconds to wait before flushing buffered text when the model pauses. Set to 0 to disable the idle flush watchdog."
        ),
    )

    # ─────────────────────────────────────────────────────────────────────
    # Error Templates
    # ─────────────────────────────────────────────────────────────────────
    # Note: Default template strings are imported from core.errors module

    OPENROUTER_ERROR_TEMPLATE: str = Field(
        default="",  # Actual default loaded from core.errors.DEFAULT_OPENROUTER_ERROR_TEMPLATE
        description=(
            "Markdown template used when OpenRouter rejects a request with status 400. "
            "Placeholders such as {heading}, {detail}, {sanitized_detail}, {provider}, {model_identifier}, "
            "{requested_model}, {api_model_id}, {normalized_model_id}, {openrouter_code}, {upstream_type}, "
            "{reason}, {request_id}, {request_id_reference}, {openrouter_message}, {upstream_message}, "
            "{moderation_reasons}, {flagged_excerpt}, {raw_body}, {context_limit_tokens}, {max_output_tokens}, "
            "and {include_model_limits} are replaced when values are available. "
            "Lines containing placeholders are omitted automatically when the referenced value is missing or empty. "
            "Supports Handlebars-style conditionals: wrap sections in {{#if variable}}...{{/if}} to render only when truthy."
        ),
    )
    NETWORK_TIMEOUT_TEMPLATE: str = Field(
        default="",  # Actual default loaded from core.errors
        description=(
            "Markdown template for network timeout errors. "
            "Available variables: {error_id}, {timeout_seconds}, {timestamp}, "
            "{session_id}, {user_id}, {support_email}. "
            "Supports Handlebars-style conditionals: wrap sections in {{#if variable}}...{{/if}} to render only when truthy."
        )
    )
    CONNECTION_ERROR_TEMPLATE: str = Field(
        default="",  # Actual default loaded from core.errors
        description=(
            "Markdown template for connection failures. "
            "Available variables: {error_id}, {error_type}, {timestamp}, "
            "{session_id}, {user_id}, {support_email}. "
            "Supports Handlebars-style conditionals: wrap sections in {{#if variable}}...{{/if}} to render only when truthy."
        )
    )
    SERVICE_ERROR_TEMPLATE: str = Field(
        default="",  # Actual default loaded from core.errors
        description=(
            "Markdown template for OpenRouter 5xx errors. "
            "Available variables: {error_id}, {status_code}, {reason}, {timestamp}, "
            "{session_id}, {user_id}, {support_email}. "
            "Supports Handlebars-style conditionals: wrap sections in {{#if variable}}...{{/if}} to render only when truthy."
        )
    )
    INTERNAL_ERROR_TEMPLATE: str = Field(
        default="",  # Actual default loaded from core.errors
        description=(
            "Markdown template for unexpected internal errors. "
            "Available variables: {error_id}, {error_type}, {timestamp}, "
            "{session_id}, {user_id}, {support_email}, {support_url}. "
            "Supports Handlebars-style conditionals: wrap sections in {{#if variable}}...{{/if}} to render only when truthy."
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # Support Configuration
    # ─────────────────────────────────────────────────────────────────────

    SUPPORT_EMAIL: str = Field(
        default="",
        description=(
            "Support email displayed in error messages. "
            "Leave empty if self-hosted without dedicated support."
        )
    )
    SUPPORT_URL: str = Field(
        default="",
        description=(
            "Support URL (e.g., internal ticket system, Slack channel). "
            "Shown in error messages if provided."
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # Tool Execution Limits & Timeouts
    # ─────────────────────────────────────────────────────────────────────

    MAX_PARALLEL_TOOLS_GLOBAL: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="Global ceiling for simultaneously executing tool calls.",
    )
    MAX_PARALLEL_TOOLS_PER_REQUEST: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Per-request concurrency limit for tool execution workers.",
    )
    TOOL_BATCH_CAP: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Maximum number of compatible tool calls that may be executed in a single batch.",
    )
    TOOL_OUTPUT_RETENTION_TURNS: int = Field(
        default=10,
        ge=0,
        description=(
            "Number of most recent logical turns whose tool outputs are sent in full. "
            "A turn starts when a user speaks and includes the assistant/tool responses "
            "that follow until the next user message. Older turns have their persisted "
            "tool outputs pruned to save tokens. Set to 0 to keep every tool output."
        ),
    )
    TOOL_TIMEOUT_SECONDS: int = Field(
        default=60,
        ge=1,
        le=600,
        description="Max seconds to wait for an individual tool to finish before timing out. Generous default reduces disruption for real-world tools.",
    )
    TOOL_BATCH_TIMEOUT_SECONDS: int = Field(
        default=120,
        ge=1,
        description="Max seconds to wait for a batch of tool calls to complete before timing out. Longer default keeps complex batches from being interrupted prematurely.",
    )
    TOOL_IDLE_TIMEOUT_SECONDS: Optional[int] = Field(
        default=None,
        ge=1,
        description="Idle timeout (seconds) between tool executions in a queue. Set to null for unlimited idle time so intermittent tool usage does not fail unexpectedly.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Redis Cache Configuration
    # ─────────────────────────────────────────────────────────────────────

    ENABLE_REDIS_CACHE: bool = Field(
        default=True,
        description="Enable Redis write-behind cache when REDIS_URL + multi-worker detected.",
    )
    REDIS_CACHE_TTL_SECONDS: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="TTL applied to Redis artifact cache entries (seconds).",
    )
    REDIS_PENDING_WARN_THRESHOLD: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Emit a warning when the Redis pending queue exceeds this number of artifacts.",
    )
    REDIS_FLUSH_FAILURE_LIMIT: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Disable Redis caching after this many consecutive flush failures (falls back to direct DB writes).",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Artifact Cleanup & Database
    # ─────────────────────────────────────────────────────────────────────

    ARTIFACT_CLEANUP_DAYS: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Retention window (days) for artifact cleanup scheduler.",
    )
    ARTIFACT_CLEANUP_INTERVAL_HOURS: float = Field(
        default=1.0,
        ge=0.5,
        le=24,
        description="Frequency (hours) for the artifact cleanup worker to wake up.",
    )
    DB_BATCH_SIZE: int = Field(
        default=10,
        ge=5,
        le=20,
        description="Number of artifacts to commit per DB batch.",
    )

    # ─────────────────────────────────────────────────────────────────────
    # Model & UI Behavior
    # ─────────────────────────────────────────────────────────────────────

    USE_MODEL_MAX_OUTPUT_TOKENS: bool = Field(
        default=False,
        description="When enabled, automatically include the provider's max_output_tokens in each request. Disable to omit the parameter entirely.",
    )
    SHOW_FINAL_USAGE_STATUS: bool = Field(
        default=True,
        description="When True, the final status message includes elapsed time, cost, and token usage.",
    )
    ENABLE_STATUS_CSS_PATCH: bool = Field(
        default=True,
        description="When True, injects a CSS tweak via __event_call__ to show multi-line status descriptions in Open WebUI (experimental).",
    )
    MAX_INPUT_IMAGES_PER_REQUEST: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of image inputs (user attachments plus assistant fallbacks) to include in a single provider request.",
    )
    IMAGE_INPUT_SELECTION: Literal["user_turn_only", "user_then_assistant"] = Field(
        default="user_then_assistant",
        description=(
            "Controls which images are forwarded to the provider. "
            "'user_turn_only' restricts inputs to the images supplied with the current user message. "
            "'user_then_assistant' falls back to the most recent assistant-generated images when the user did not attach any."
        ),
    )


class UserValves(BaseModel):
    """Per-user valve overrides.

    These valves allow individual users to customize their experience without
    affecting other users. Settings default to "inherit" to use system defaults.
    """

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_inherit(cls, values):
        """Treat the literal string 'inherit' (any case) as an unset value.

        ``LOG_LEVEL`` is the lone field whose Literal includes ``"INHERIT"``.
        Keep that string (upper-cased) so validation still succeeds.
        """
        if not isinstance(values, dict):
            return values

        normalized: dict[str, Any] = {}
        for key, val in values.items():
            if isinstance(val, str):
                stripped = val.strip()
                lowered = stripped.lower()
                if key == "LOG_LEVEL":
                    normalized[key] = stripped.upper()
                    continue
                if lowered == "inherit":
                    normalized[key] = None
                    continue
            normalized[key] = val
        return normalized

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "INHERIT"] = Field(
        default="INHERIT",
        description="Select logging level. 'INHERIT' uses the pipe default.",
    )
    SHOW_FINAL_USAGE_STATUS: bool = Field(
        default=True,
        description="Override whether the final status message includes usage stats (set to Inherit to reuse the workspace default).",
    )
    ENABLE_REASONING: bool = Field(
        default=True,
        title="Show live reasoning",
        description="Request live reasoning traces when the model supports them (set to Inherit to reuse the workspace default).",
    )
    REASONING_EFFORT: Literal["minimal", "low", "medium", "high"] = Field(
        default="medium",
        title="Reasoning effort",
        description="Preferred reasoning effort for supported models (set to Inherit to reuse the workspace default).",
    )
    REASONING_SUMMARY_MODE: Literal["auto", "concise", "detailed", "disabled"] = Field(
        default="auto",
        title="Reasoning summary",
        description="Override how reasoning summaries are requested (auto/concise/detailed/disabled). Set to Inherit to reuse the workspace default.",
    )
    PERSIST_REASONING_TOKENS: Literal["disabled", "next_reply", "conversation"] = Field(
        default="next_reply",
        validation_alias=AliasChoices("PERSIST_REASONING_TOKENS", "next_reply"),
        serialization_alias="next_reply",
        alias="next_reply",
        title="Reasoning retention",
        description="Reasoning retention preference (Off, Only for the next reply, or Entire conversation). Set to Inherit to reuse the workspace default.",
    )
    PERSIST_TOOL_RESULTS: bool = Field(
        default=True,
        title="Keep tool results",
        description="Persist tool call outputs for later turns (set to Inherit to reuse the workspace default).",
    )
    STREAMING_UPDATE_PROFILE: Literal["quick", "normal", "slow"] = Field(
        default="normal",
        description="Override the streaming preset (Quick/Normal/Slow) for this user only.",
    )
    STREAMING_UPDATE_CHAR_LIMIT: int = Field(
        default=20,
        ge=10,
        le=500,
        description="User override for streaming update character limit (10-500).",
    )
    STREAMING_IDLE_FLUSH_MS: int = Field(
        default=250,
        ge=0,
        le=2000,
        description="User override for the idle flush interval in milliseconds (0 disables).",
    )


def merge_valves(system_valves: Valves, user_valves: Optional[UserValves]) -> Valves:
    """Merge user valve overrides with system defaults.

    Args:
        system_valves: System-wide valve configuration
        user_valves: Optional per-user overrides

    Returns:
        Merged valve configuration with user overrides applied where set
    """
    if not user_valves:
        return system_valves

    # Create a copy of system valves
    merged = system_valves.model_copy(deep=True)

    # Apply user overrides (only non-None values)
    for field_name in user_valves.model_fields.keys():
        user_value = getattr(user_valves, field_name, None)

        # Skip "INHERIT" for LOG_LEVEL
        if field_name == "LOG_LEVEL" and user_value == "INHERIT":
            continue

        # Apply non-None user overrides
        if user_value is not None and hasattr(merged, field_name):
            setattr(merged, field_name, user_value)

    return merged
