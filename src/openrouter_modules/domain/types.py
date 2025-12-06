"""Domain types for OpenRouter Responses Pipe.

This module defines all data structures used in the business logic layer:
- TypedDicts for API contracts (messages, tool calls, usage stats)
- Pydantic models for request/response validation
- Data classes for internal state management
- Exception classes for error handling

Layer: domain (imports from core, never from adapters)
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, NotRequired, Optional, TypedDict

from fastapi import Request
from pydantic import BaseModel, model_validator

# Will be provided by parent
# from ..core import markers


# ─────────────────────────────────────────────────────────────────────────────
# TypedDicts for API Contracts
# ─────────────────────────────────────────────────────────────────────────────

class FunctionCall(TypedDict):
    """Represents a function call within a tool call."""
    name: str
    arguments: str  # JSON-encoded string


class ToolCall(TypedDict):
    """Represents a single tool/function call."""
    id: str
    type: Literal["function"]
    function: FunctionCall


class Message(TypedDict):
    """Represents a chat message in OpenAI/OpenRouter format."""
    role: Literal["user", "assistant", "system", "tool"]
    content: NotRequired[Optional[str]]
    name: NotRequired[str]
    tool_calls: NotRequired[list[ToolCall]]
    tool_call_id: NotRequired[str]


class FunctionSchema(TypedDict):
    """Represents a function schema for tool definitions."""
    name: str
    description: NotRequired[str]
    parameters: dict[str, Any]  # JSON Schema
    strict: NotRequired[bool]


class ToolDefinition(TypedDict):
    """Represents a tool definition for OpenAI/OpenRouter."""
    type: Literal["function"]
    function: FunctionSchema


class MCPServerConfig(TypedDict):
    """Represents an MCP server configuration."""
    server_label: str
    server_url: str
    require_approval: NotRequired[Literal["never", "always", "auto"]]
    allowed_tools: NotRequired[list[str]]


class UsageStats(TypedDict, total=False):
    """Token usage statistics from API responses."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_details: dict[str, Any]
    completion_tokens_details: dict[str, Any]


class ArtifactPayload(TypedDict, total=False):
    """Represents a persisted artifact (reasoning or tool result)."""
    type: str
    content: Any
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    output: str
    timestamp: float


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models for Request/Response Validation
# ─────────────────────────────────────────────────────────────────────────────

class CompletionsBody(BaseModel):
    """Represents the body of a completions request to OpenAI completions API.

    This is the input format from Open WebUI, which we translate to Responses API.
    """
    model: str
    messages: List[Dict[str, Any]]
    stream: bool = False

    class Config:
        """Permit passthrough of additional OpenAI parameters automatically."""
        extra = "allow"  # Pass through additional OpenAI parameters automatically


class ResponsesBody(BaseModel):
    """Represents the body of a responses request to OpenAI Responses API.

    This is the output format we send to OpenRouter after translation.
    """

    # Required parameters
    model: str
    input: str | List[Dict[str, Any]]  # plain text, or rich array

    # Optional parameters
    stream: bool = False  # SSE chunking
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    reasoning: Optional[Dict[str, Any]] = None  # {"effort":"high", ...}
    tool_choice: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    plugins: Optional[List[Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    parallel_tool_calls: Optional[bool] = None
    transforms: Optional[List[str]] = None

    class Config:
        """Permit passthrough of additional OpenAI parameters automatically."""
        extra = "allow"  # Allow additional OpenAI parameters automatically (future-proofing)

    @model_validator(mode='after')
    def _normalize_model_id(self) -> "ResponsesBody":
        """Ensure the model name references the canonical base id (prefix/date stripped).

        This validator will be wired up to ModelFamily.base_model() in the engine.
        """
        # NOTE: Actual normalization happens in domain.registry.ModelFamily.base_model()
        # This validator is a placeholder that will be called by the engine
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Data Classes for Internal State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class PipeJob:
    """Encapsulate a single OpenRouter request scheduled through the queue.

    This represents a complete request with all context needed for processing.
    """

    pipe: Any  # Reference to main Pipe instance
    body: dict[str, Any]
    user: dict[str, Any]
    request: Request
    event_emitter: Callable[[dict[str, Any]], Awaitable[None]] | None
    event_call: Callable[[dict[str, Any]], Awaitable[Any]] | None
    metadata: dict[str, Any]
    tools: list[dict[str, Any]] | dict[str, Any] | None
    task: Optional[dict[str, Any]]
    task_body: Optional[dict[str, Any]]
    valves: Any  # Pipe.Valves instance
    future: asyncio.Future
    request_id: str = field(default_factory=lambda: secrets.token_hex(8))

    @property
    def session_id(self) -> str:
        """Convenience accessor for the metadata session identifier."""
        return str(self.metadata.get("session_id") or "")

    @property
    def user_id(self) -> str:
        """Return the Open WebUI user id associated with the job."""
        return str(self.user.get("id") or self.metadata.get("user_id") or "")


@dataclass(slots=True)
class QueuedToolCall:
    """Stores a pending tool call plus execution metadata for worker pools."""
    call: dict[str, Any]
    tool_cfg: dict[str, Any]
    args: dict[str, Any]
    future: asyncio.Future
    allow_batch: bool


@dataclass(slots=True)
class ToolExecutionContext:
    """Holds shared state for executing tool calls within breaker limits."""
    queue: asyncio.Queue[QueuedToolCall | None]
    per_request_semaphore: asyncio.Semaphore
    global_semaphore: asyncio.Semaphore | None
    timeout: float
    batch_timeout: float | None
    idle_timeout: float | None
    user_id: str
    event_emitter: Callable[[dict[str, Any]], Awaitable[None]] | None
    batch_cap: int
    workers: list[asyncio.Task] = field(default_factory=list)
    timeout_error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Exception Classes
# ─────────────────────────────────────────────────────────────────────────────

class RetryableHTTPStatusError(Exception):
    """Raised when an HTTP request fails with a retryable status code (429, 5xx)."""

    def __init__(self, status_code: int, reason: str, retry_after: Optional[float] = None):
        self.status_code = status_code
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(f"HTTP {status_code}: {reason}")


class OpenRouterAPIError(RuntimeError):
    """Raised when OpenRouter returns an error response (400, 401, 403, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        openrouter_code: Optional[str] = None,
        openrouter_message: Optional[str] = None,
        upstream_type: Optional[str] = None,
        upstream_message: Optional[str] = None,
        provider: Optional[str] = None,
        requested_model: Optional[str] = None,
        moderation_reasons: Optional[List[str]] = None,
        flagged_input: Optional[str] = None,
        raw_body: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        self.status_code = status_code
        self.openrouter_code = openrouter_code
        self.openrouter_message = openrouter_message
        self.upstream_type = upstream_type
        self.upstream_message = upstream_message
        self.provider = provider
        self.requested_model = requested_model
        self.moderation_reasons = moderation_reasons or []
        self.flagged_input = flagged_input
        self.raw_body = raw_body
        self.request_id = request_id
        super().__init__(message)

    def __str__(self) -> str:
        """Return a human-readable error message."""
        if self.upstream_message:
            return self.upstream_message
        if self.openrouter_message:
            return self.openrouter_message
        return super().__str__()
