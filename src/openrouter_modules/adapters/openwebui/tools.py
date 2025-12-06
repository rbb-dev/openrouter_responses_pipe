"""Open WebUI tool execution adapter.

Tool registry access and parallel execution with circuit breakers:
- Queue-based tool execution with worker pool
- Per-tool-type circuit breakers (5 failures in 60s)
- Intelligent batching for parallel calls
- Semaphore-based concurrency control
- Timeout handling (per-call, batch, idle)
- Dependency detection and sequential execution

Layer: adapters (Open WebUI tool integration)

Dependencies:
- Open WebUI: Tools registry access
- asyncio: Queue, workers, semaphores
- tenacity: Exponential backoff retries
- contextvars: Request-scoped context

Architecture:
    Tool Call Flow:
        Client → _execute_function_calls() → Queue
                      ↓ (worker pool)
                _tool_worker_loop() → Batch Detection
                      ↓
                _execute_tool_batch() → Parallel Execution
                      ↓
                _invoke_tool_call() → Circuit Breaker → Callable
                      ↓
                Future.set_result() → Client

Design Notes:
- Workers consume from shared queue with batch detection
- Circuit breakers prevent cascading failures
- Semaphores limit per-request and global concurrency
- Dependency analysis prevents incorrect batching
- Timeout errors are accumulated and raised after all calls complete
"""

from __future__ import annotations

import asyncio
import ast
import contextlib
import inspect
import json
import logging
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.markers import generate_item_id

if TYPE_CHECKING:
    from dataclasses import dataclass
else:
    # Runtime: use lightweight dict for dataclasses
    def dataclass(cls):
        return cls

# Module logger
LOGGER = logging.getLogger(__name__)


@dataclass
class _QueuedToolCall:
    """Represents a tool call queued for execution."""
    call: Dict[str, Any]           # Original call dict with name, arguments, call_id
    tool_cfg: Dict[str, Any]       # Tool configuration with callable, type
    args: Dict[str, Any]           # Parsed arguments
    future: asyncio.Future         # Future to resolve with result
    allow_batch: bool              # Whether this call can be batched


@dataclass
class _ToolExecutionContext:
    """Per-request tool execution context."""
    queue: asyncio.Queue           # Queue of _QueuedToolCall or None (sentinel)
    per_request_semaphore: asyncio.Semaphore  # Per-request concurrency limit
    global_semaphore: Optional[asyncio.Semaphore]  # Global concurrency limit
    timeout: float                 # Per-call timeout in seconds
    batch_timeout: float           # Batch timeout in seconds
    idle_timeout: Optional[float]  # Idle timeout (None = disabled)
    user_id: str                   # User ID for circuit breaker
    event_emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]]  # Event emitter
    batch_cap: int                 # Maximum batch size
    workers: List[asyncio.Task] = None  # Worker tasks
    timeout_error: Optional[str] = None  # Accumulated timeout error message

    def __post_init__(self):
        """Initialize mutable fields."""
        if self.workers is None:
            object.__setattr__(self, 'workers', [])


class ToolExecutionAdapter:
    """Tool execution adapter with queue-based workers and circuit breakers.

    This adapter manages parallel tool execution with:
    - Worker pool consuming from shared queue
    - Intelligent batching for parallel-safe calls
    - Per-tool-type circuit breakers
    - Semaphore-based concurrency control
    - Timeout handling (per-call, batch, idle)

    Attributes:
        logger: Logger instance
        _tool_breakers: Per-user, per-tool-type circuit breaker records
        _breaker_threshold: Failure threshold (default: 5)
        _breaker_window_seconds: Sliding window duration (default: 60s)

    Example:
        >>> adapter = ToolExecutionAdapter(logger)
        >>> results = await adapter.execute_function_calls(
        ...     calls, tools, context
        ... )
    """

    def __init__(
        self,
        logger: Any,
        *,
        breaker_threshold: int = 5,
        breaker_window_seconds: int = 60,
    ) -> None:
        """Initialize tool execution adapter.

        Args:
            logger: Logger instance
            breaker_threshold: Failure threshold for circuit breaker
            breaker_window_seconds: Sliding window duration
        """
        self.logger = logger
        self._tool_breakers: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=breaker_threshold))
        )
        self._breaker_threshold = breaker_threshold
        self._breaker_window_seconds = breaker_window_seconds

    async def execute_function_calls(
        self,
        calls: List[Dict[str, Any]],
        tools: Dict[str, Dict[str, Any]],
        context: _ToolExecutionContext,
    ) -> List[Dict[str, Any]]:
        """Execute tool calls via the per-request queue/worker pipeline.

        Args:
            calls: List of tool call dictionaries
            tools: Tool registry (name -> config)
            context: Tool execution context

        Returns:
            List of tool output dictionaries

        Example:
            >>> results = await adapter.execute_function_calls(
            ...     [{"name": "search", "arguments": {"query": "..."}}],
            ...     {"search": {"callable": search_fn, "type": "function"}},
            ...     context
            ... )
        """
        loop = asyncio.get_running_loop()
        pending: List[Tuple[Dict[str, Any], asyncio.Future]] = []
        outputs: List[Dict[str, Any]] = []
        enqueued_any = False
        breaker_only_skips = True

        for call in calls:
            tool_cfg = tools.get(call.get("name"))
            if not tool_cfg:
                breaker_only_skips = False
                outputs.append(
                    self._build_tool_output(
                        call,
                        "Tool not found",
                        status="failed",
                    )
                )
                continue

            tool_type = (tool_cfg.get("type") or "function").lower()
            if not self._tool_type_allows(context.user_id, tool_type):
                await self._notify_tool_breaker(context, tool_type, call.get("name"))
                outputs.append(
                    self._build_tool_output(
                        call,
                        f"Tool '{call.get('name')}' skipped due to repeated failures.",
                        status="skipped",
                    )
                )
                continue

            fn = tool_cfg.get("callable")
            if fn is None:
                breaker_only_skips = False
                outputs.append(
                    self._build_tool_output(
                        call,
                        f"Tool '{call.get('name')}' has no callable configured.",
                        status="failed",
                    )
                )
                continue

            try:
                raw_args = call.get("arguments") or "{}"
                args = self._parse_tool_arguments(raw_args)
            except Exception as exc:
                breaker_only_skips = False
                outputs.append(
                    self._build_tool_output(
                        call,
                        f"Invalid arguments: {exc}",
                        status="failed",
                    )
                )
                continue

            future: asyncio.Future = loop.create_future()
            allow_batch = self._is_batchable_tool_call(args)
            queued = _QueuedToolCall(
                call=call,
                tool_cfg=tool_cfg,
                args=args,
                future=future,
                allow_batch=allow_batch,
            )
            await context.queue.put(queued)
            self.logger.debug("Enqueued tool %s (batch=%s)", call.get("name"), allow_batch)
            pending.append((call, future))
            enqueued_any = True
            breaker_only_skips = False

        # If all calls were skipped due to circuit breaker, record failure
        if not enqueued_any and breaker_only_skips and context.user_id:
            # This is handled by caller's circuit breaker
            pass

        # Wait for all futures to resolve
        for call, future in pending:
            try:
                if context and context.idle_timeout:
                    result = await asyncio.wait_for(future, timeout=context.idle_timeout)
                else:
                    result = await future
            except asyncio.TimeoutError:
                message = (
                    f"Tool '{call.get('name')}' idle timeout after {context.idle_timeout:.0f}s."
                    if context and context.idle_timeout
                    else "Tool idle timeout exceeded."
                )
                if context:
                    context.timeout_error = context.timeout_error or message
                raise RuntimeError(message)
            except Exception as exc:  # pragma: no cover - defensive
                result = self._build_tool_output(
                    call,
                    f"Tool error: {exc}",
                    status="failed",
                )
            outputs.append(result)

        if context and context.timeout_error:
            raise RuntimeError(context.timeout_error)

        return outputs

    async def tool_worker_loop(self, context: _ToolExecutionContext) -> None:
        """Worker loop that consumes tool calls from queue and executes batches.

        Args:
            context: Tool execution context

        Note:
            Workers run until they receive a None sentinel from the queue.
            Batch detection groups consecutive calls to the same tool.
        """
        batch: List[_QueuedToolCall] = []
        pending: List[Tuple[Optional[_QueuedToolCall], bool]] = []

        try:
            while True:
                timeout = context.idle_timeout if context.idle_timeout else None
                timed_out = False

                if timeout is not None:
                    try:
                        item = await asyncio.wait_for(context.queue.get(), timeout=timeout)
                        from_queue = True
                    except asyncio.TimeoutError:
                        timed_out = True
                        item = None
                        from_queue = False
                else:
                    item = await context.queue.get()
                    from_queue = True

                if timed_out:
                    if batch:
                        await self._execute_tool_batch(batch, context)
                        for queued_item in batch:
                            pending.append((queued_item, True))
                        batch = []
                    context.timeout_error = context.timeout_error or "Tool worker idle timeout"
                    continue

                if item is None:
                    if batch:
                        await self._execute_tool_batch(batch, context)
                        for queued_item in batch:
                            pending.append((queued_item, True))
                        batch = []
                    pending.append((None, from_queue))
                    break

                if not item.allow_batch or not batch or not self._can_batch_tool_calls(batch[0], item):
                    if batch:
                        await self._execute_tool_batch(batch, context)
                        for queued_item in batch:
                            pending.append((queued_item, True))
                        batch = []

                batch.append(item)
                if len(batch) >= context.batch_cap:
                    await self._execute_tool_batch(batch, context)
                    for queued_item in batch:
                        pending.append((queued_item, True))
                    batch = []

        finally:
            # Mark queue tasks as done
            while pending:
                leftover, from_queue = pending.pop(0)
                if from_queue:
                    context.queue.task_done()
                if leftover is None:
                    continue
                if not leftover.future.done():
                    error_msg = context.timeout_error or "Tool execution cancelled"
                    leftover.future.set_result(
                        self._build_tool_output(
                            leftover.call,
                            error_msg,
                            status="cancelled",
                        )
                    )

    def _can_batch_tool_calls(
        self,
        first: _QueuedToolCall,
        candidate: _QueuedToolCall,
    ) -> bool:
        """Check if two tool calls can be batched together.

        Args:
            first: First tool call in potential batch
            candidate: Candidate tool call to add to batch

        Returns:
            True if calls can be batched, False otherwise

        Note:
            Calls cannot be batched if:
            - Different tool names
            - Either has dependency markers
            - Cross-references each other's call IDs
        """
        if first.call.get("name") != candidate.call.get("name"):
            return False

        dep_keys = {"depends_on", "_depends_on", "sequential", "no_batch"}
        if any(key in first.args or key in candidate.args for key in dep_keys):
            return False

        first_id = first.call.get("call_id")
        candidate_id = candidate.call.get("call_id")
        if first_id and self._args_reference_call(candidate.args, first_id):
            return False
        if candidate_id and self._args_reference_call(first.args, candidate_id):
            return False

        return True

    def _args_reference_call(self, args: Any, call_id: str) -> bool:
        """Recursively check if args reference a specific call_id.

        Args:
            args: Arguments to search
            call_id: Call ID to search for

        Returns:
            True if call_id found in args, False otherwise
        """
        if isinstance(args, str):
            return call_id in args
        if isinstance(args, dict):
            return any(self._args_reference_call(value, call_id) for value in args.values())
        if isinstance(args, list):
            return any(self._args_reference_call(item, call_id) for item in args)
        return False

    async def _execute_tool_batch(
        self,
        batch: List[_QueuedToolCall],
        context: _ToolExecutionContext,
    ) -> None:
        """Execute a batch of tool calls in parallel.

        Args:
            batch: List of tool calls to execute
            context: Tool execution context

        Note:
            All calls in batch must be for the same tool.
            Batch timeout is max(per_call_timeout, batch_timeout).
        """
        if not batch:
            return

        self.logger.debug("Batched %s tool(s) for %s", len(batch), batch[0].call.get("name"))
        tasks = [self._invoke_tool_call(item, context) for item in batch]
        gather_coro = asyncio.gather(*tasks, return_exceptions=True)

        try:
            if context.batch_timeout:
                results = await asyncio.wait_for(gather_coro, timeout=context.batch_timeout)
            else:
                results = await gather_coro
        except asyncio.TimeoutError:
            message = (
                f"Tool batch '{batch[0].call.get('name')}' exceeded {context.batch_timeout:.0f}s and was cancelled."
                if context.batch_timeout
                else "Tool batch timed out."
            )
            context.timeout_error = context.timeout_error or message
            self.logger.warning("%s", message)
            for item in batch:
                tool_type = (item.tool_cfg.get("type") or "function").lower()
                self._record_tool_failure_type(context.user_id, tool_type)
                if not item.future.done():
                    item.future.set_result(
                        self._build_tool_output(
                            item.call,
                            message,
                            status="failed",
                        )
                    )
            return

        for item, result in zip(batch, results):
            if item.future.done():
                continue
            if isinstance(result, Exception):
                payload = self._build_tool_output(
                    item.call,
                    f"Tool error: {result}",
                    status="failed",
                )
            else:
                status, text = result
                payload = self._build_tool_output(item.call, text, status=status)
                tool_type = (item.tool_cfg.get("type") or "function").lower()
                self._reset_tool_failure_type(context.user_id, tool_type)
            item.future.set_result(payload)

    async def _invoke_tool_call(
        self,
        item: _QueuedToolCall,
        context: _ToolExecutionContext,
    ) -> Tuple[str, str]:
        """Invoke a single tool call with circuit breaker and semaphore.

        Args:
            item: Queued tool call
            context: Tool execution context

        Returns:
            Tuple of (status, output_text)

        Note:
            Acquires per-request semaphore, then optionally global semaphore.
            Circuit breaker checked before execution.
        """
        tool_type = (item.tool_cfg.get("type") or "function").lower()
        if not self._tool_type_allows(context.user_id, tool_type):
            await self._notify_tool_breaker(context, tool_type, item.call.get("name"))
            return (
                "skipped",
                f"Tool '{item.call.get('name')}' temporarily disabled due to repeated errors.",
            )

        async with context.per_request_semaphore:
            if context.global_semaphore is not None:
                async with self._acquire_tool_global(context.global_semaphore, item.call.get("name")):
                    return await self._run_tool_with_retries(item, context, tool_type)
            return await self._run_tool_with_retries(item, context, tool_type)

    async def _run_tool_with_retries(
        self,
        item: _QueuedToolCall,
        context: _ToolExecutionContext,
        tool_type: str,
    ) -> Tuple[str, str]:
        """Run tool with exponential backoff retries.

        Args:
            item: Queued tool call
            context: Tool execution context
            tool_type: Tool type for circuit breaker

        Returns:
            Tuple of (status, output_text)

        Raises:
            Exception: If all retry attempts fail
        """
        fn = item.tool_cfg.get("callable")
        timeout = float(context.timeout)
        retryer = AsyncRetrying(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=1),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        try:
            async for attempt in retryer:
                with attempt:
                    result = await asyncio.wait_for(
                        self._call_tool_callable(fn, item.args),
                        timeout=timeout,
                    )
                    self._reset_tool_failure_type(context.user_id, tool_type)
                    text = "" if result is None else str(result)
                    return ("completed", text)
        except Exception as exc:
            self._record_tool_failure_type(context.user_id, tool_type)
            raise exc

    async def _call_tool_callable(
        self,
        fn: Callable,
        args: Dict[str, Any],
    ) -> Any:
        """Call tool function (sync or async).

        Args:
            fn: Tool callable
            args: Keyword arguments

        Returns:
            Tool result
        """
        if inspect.iscoroutinefunction(fn):
            return await fn(**args)
        return await asyncio.to_thread(fn, **args)

    @contextlib.asynccontextmanager
    async def _acquire_tool_global(
        self,
        semaphore: asyncio.Semaphore,
        tool_name: Optional[str],
    ):
        """Acquire global tool semaphore with logging.

        Args:
            semaphore: Global semaphore
            tool_name: Tool name for logging

        Yields:
            None
        """
        self.logger.debug("Waiting for global tool slot (%s)", tool_name)
        await semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    def _build_tool_output(
        self,
        call: Dict[str, Any],
        output_text: str,
        *,
        status: str = "completed",
    ) -> Dict[str, Any]:
        """Build standardized tool output dictionary.

        Args:
            call: Original tool call
            output_text: Output text
            status: Status (completed, failed, skipped, cancelled)

        Returns:
            Tool output dictionary

        Example:
            >>> adapter._build_tool_output(
            ...     {"name": "search", "call_id": "call_abc"},
            ...     "Found 3 results",
            ...     status="completed"
            ... )
            {
                "type": "function_call_output",
                "id": "01HQJX7...",
                "status": "completed",
                "call_id": "call_abc",
                "output": "Found 3 results"
            }
        """
        call_id = call.get("call_id") or generate_item_id()
        return {
            "type": "function_call_output",
            "id": generate_item_id(),
            "status": status,
            "call_id": call_id,
            "output": output_text,
        }

    def _is_batchable_tool_call(self, args: Dict[str, Any]) -> bool:
        """Check if tool call can be batched based on arguments.

        Args:
            args: Tool arguments

        Returns:
            True if batchable, False otherwise

        Note:
            Calls with dependency markers cannot be batched.
        """
        blockers = {"depends_on", "_depends_on", "sequential", "no_batch"}
        return not any(key in args for key in blockers)

    def _parse_tool_arguments(self, raw_args: Any) -> Dict[str, Any]:
        """Parse tool arguments from various formats.

        Args:
            raw_args: Raw arguments (dict, JSON string, or Python literal)

        Returns:
            Parsed arguments dictionary

        Raises:
            ValueError: If arguments cannot be parsed

        Example:
            >>> adapter._parse_tool_arguments('{"query": "test"}')
            {"query": "test"}
            >>> adapter._parse_tool_arguments({"query": "test"})
            {"query": "test"}
        """
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                return json.loads(raw_args)
            except json.JSONDecodeError:
                try:
                    literal_value = ast.literal_eval(raw_args)
                except (ValueError, SyntaxError) as exc:
                    raise ValueError("Unable to parse tool arguments") from exc
                if not isinstance(literal_value, dict):
                    raise ValueError("Tool arguments must evaluate to an object")
                return literal_value
        raise ValueError(f"Unsupported argument type: {type(raw_args).__name__}")

    def _tool_type_allows(self, user_id: str, tool_type: str) -> bool:
        """Check if tool type is allowed by circuit breaker.

        Args:
            user_id: User identifier
            tool_type: Tool type

        Returns:
            True if allowed, False if breaker open
        """
        if not user_id or not tool_type:
            return True
        window = self._tool_breakers[user_id][tool_type]
        now = time.time()
        while window and now - window[0] > self._breaker_window_seconds:
            window.popleft()
        return len(window) < self._breaker_threshold

    def _record_tool_failure_type(self, user_id: str, tool_type: str) -> None:
        """Record tool failure for circuit breaker.

        Args:
            user_id: User identifier
            tool_type: Tool type
        """
        if not user_id or not tool_type:
            return
        self._tool_breakers[user_id][tool_type].append(time.time())

    def _reset_tool_failure_type(self, user_id: str, tool_type: str) -> None:
        """Reset tool failure counter.

        Args:
            user_id: User identifier
            tool_type: Tool type
        """
        if user_id and tool_type and user_id in self._tool_breakers:
            self._tool_breakers[user_id][tool_type].clear()

    async def _notify_tool_breaker(
        self,
        context: _ToolExecutionContext,
        tool_type: str,
        tool_name: Optional[str],
    ) -> None:
        """Emit status event when tool is blocked by circuit breaker.

        Args:
            context: Tool execution context
            tool_type: Tool type
            tool_name: Tool name for display
        """
        if not context.event_emitter:
            return
        try:
            await context.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "description": (
                            f"Skipping {tool_name or tool_type} tools due to repeated failures"
                        ),
                        "done": False,
                    },
                }
            )
        except Exception:
            # Event emitter failures (client disconnect, etc.) shouldn't stop pipe
            self.logger.debug("Failed to emit breaker notification", exc_info=True)


__all__ = [
    "_QueuedToolCall",
    "_ToolExecutionContext",
    "ToolExecutionAdapter",
]
