"""Session-aware logging for OpenRouter Responses Pipe.

This module provides the SessionLogger class which captures per-request logs
using Python's contextvars. Each request gets a unique session ID, and all
log messages from that request are tagged and can be retrieved for debugging
or display as citations.

Layer: core (no dependencies on domain or adapters)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Dict, Optional


class SessionLogger:
    """Per-request logger that captures console output and an in-memory log buffer.

    The logger is bound to a logical *session* via contextvars so that log lines
    can be collected and emitted (e.g., as citations) for the current request.
    Cleanup is intentional and explicit: request handlers call ``cleanup`` once
    they finish streaming so there is no background task silently pruning logs.

    Attributes:
        session_id: ContextVar storing the current logical session ID.
        user_id: ContextVar storing the current user ID.
        log_level:  ContextVar storing the minimum level to emit for this session.
        logs:       Map of session_id -> fixed-size deque of formatted log strings.

    Example:
        >>> # In request handler:
        >>> SessionLogger.session_id.set("req-12345")
        >>> SessionLogger.user_id.set("user@example.com")
        >>> SessionLogger.log_level.set(logging.DEBUG)
        >>>
        >>> logger = SessionLogger.get_logger(__name__)
        >>> logger.info("Processing request")
        >>>
        >>> # Retrieve logs for session:
        >>> logs = list(SessionLogger.logs["req-12345"])
        >>> # Cleanup when done:
        >>> SessionLogger.cleanup()
    """

    session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
    user_id: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
    log_level: ContextVar[int] = ContextVar("log_level", default=logging.INFO)
    logs: Dict[str, deque] = defaultdict(lambda: deque(maxlen=2000))
    _session_last_seen: Dict[str, float] = {}
    log_queue: Optional[asyncio.Queue[logging.LogRecord]] = None
    _main_loop: Optional[asyncio.AbstractEventLoop] = None
    _console_formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _memory_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [user=%(user_id)s] %(message)s"
    )

    @classmethod
    def get_logger(cls, name: str = __name__) -> logging.Logger:
        """Create a logger wired to the current SessionLogger context.

        Args:
            name: Logger name; defaults to the current module name.

        Returns:
            logging.Logger: A configured logger that writes both to stdout and
            the in-memory `SessionLogger.logs` buffer. The buffer is keyed by
            the current `SessionLogger.session_id`.

        Example:
            >>> logger = SessionLogger.get_logger(__name__)
            >>> logger.info("Request started")
        """
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.filters.clear()
        logger.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        if not any(isinstance(handler, logging.NullHandler) for handler in root_logger.handlers):
            root_logger.addHandler(logging.NullHandler())
        logger.propagate = True

        # Single combined filter: attach session_id and respect per-session level.
        def filter(record: logging.LogRecord) -> bool:
            """Attach session metadata and enforce per-session log levels."""
            sid = cls.session_id.get()
            uid = cls.user_id.get()
            record.session_id = sid  # type: ignore
            record.session_label = sid or "-"  # type: ignore
            record.user_id = uid or "-"  # type: ignore
            if sid:
                cls._session_last_seen[sid] = time.time()
            return record.levelno >= cls.log_level.get()

        logger.addFilter(filter)

        async_handler = logging.Handler()

        def _emit(record: logging.LogRecord) -> None:
            """Enqueue log record to async queue or process synchronously."""
            cls._enqueue(record)

        async_handler.emit = _emit  # type: ignore[assignment]
        logger.addHandler(async_handler)

        return logger

    @classmethod
    def set_log_queue(cls, queue: Optional[asyncio.Queue[logging.LogRecord]]) -> None:
        """Set the async queue for log record processing.

        Args:
            queue: Asyncio queue to receive log records, or None to disable
        """
        cls.log_queue = queue

    @classmethod
    def set_main_loop(cls, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """Set the main event loop for thread-safe queue operations.

        Args:
            loop: Event loop reference for call_soon_threadsafe, or None
        """
        cls._main_loop = loop

    @classmethod
    def _enqueue(cls, record: logging.LogRecord) -> None:
        """Enqueue log record to async queue or process immediately.

        Handles cross-thread logging by using call_soon_threadsafe when
        necessary. Falls back to synchronous processing if queue is unavailable.

        Args:
            record: Log record to process
        """
        queue = cls.log_queue
        if queue is None:
            cls.process_record(record)
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop is cls._main_loop:
            cls._safe_put(queue, record)
            return

        main_loop = cls._main_loop
        if main_loop and not main_loop.is_closed():
            main_loop.call_soon_threadsafe(cls._safe_put, queue, record)
        else:
            cls.process_record(record)

    @classmethod
    def _safe_put(cls, queue: asyncio.Queue[logging.LogRecord], record: logging.LogRecord) -> None:
        """Put record into queue with fallback to sync processing.

        Args:
            queue: Target asyncio queue
            record: Log record to enqueue
        """
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            cls.process_record(record)

    @classmethod
    def process_record(cls, record: logging.LogRecord) -> None:
        """Process log record: write to console and store in session buffer.

        Args:
            record: Log record to process
        """
        console_line = cls._console_formatter.format(record)
        sys.stdout.write(console_line + "\n")
        sys.stdout.flush()
        session_id = getattr(record, "session_id", None)
        if session_id:
            cls.logs[session_id].append(cls._memory_formatter.format(record))
            cls._session_last_seen[session_id] = time.time()

    @classmethod
    def cleanup(cls, max_age_seconds: float = 3600) -> None:
        """Remove stale session logs to avoid unbounded growth.

        Args:
            max_age_seconds: Age threshold for stale sessions (default: 1 hour)

        Example:
            >>> # At end of request:
            >>> SessionLogger.cleanup(max_age_seconds=3600)
        """
        cutoff = time.time() - max_age_seconds
        stale = [sid for sid, ts in cls._session_last_seen.items() if ts < cutoff]
        for sid in stale:
            cls.logs.pop(sid, None)
            cls._session_last_seen.pop(sid, None)


def get_session_logs(session_id: str) -> list[str]:
    """Retrieve all log messages for a given session.

    Args:
        session_id: Session identifier

    Returns:
        list[str]: List of formatted log messages for this session

    Example:
        >>> logs = get_session_logs("req-12345")
        >>> for log in logs:
        ...     print(log)
    """
    return list(SessionLogger.logs.get(session_id, []))
