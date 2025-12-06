"""Open WebUI event emitter adapters.

This module provides wrappers for Open WebUI's event emission system:
- Event emitter filtering and wrapping
- Citation formatting
- Status message helpers
- Usage statistics merging
- Code block formatting utilities

Layer: adapters (integrates with Open WebUI's event system)

Dependencies:
- None (pure utility functions)

Event Types:
    - chat:message: Incremental text deltas
    - chat:completion: Final message completion
    - status: Status updates during processing
    - citation: Source citations
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Dict, Optional


def wrap_event_emitter(
    emitter: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    *,
    suppress_chat_messages: bool = False,
    suppress_completion: bool = False,
) -> Callable[[Dict[str, Any]], Awaitable[None]]:
    """Wrap the given event emitter and optionally suppress specific event types.

    Use-case: reuse the streaming loop for non-stream requests by swallowing
    incremental 'chat:message' frames while allowing status/citation/usage
    events through.

    Args:
        emitter: Optional event emitter function
        suppress_chat_messages: If True, swallow chat:message events
        suppress_completion: If True, swallow chat:completion events

    Returns:
        Wrapped emitter function

    Example:
        >>> wrapped = wrap_event_emitter(
        ...     emitter,
        ...     suppress_chat_messages=True
        ... )
        >>> await wrapped({"type": "chat:message", "data": "..."})
        # Swallowed
        >>> await wrapped({"type": "status", "data": "..."})
        # Emitted
    """
    if emitter is None:
        async def _noop(_event: Dict[str, Any]) -> None:
            """Swallow events when no emitter is provided."""
            return

        return _noop

    async def _wrapped(event: Dict[str, Any]) -> None:
        """Proxy emitter that suppresses selected event types."""
        etype = (event or {}).get("type")
        if suppress_chat_messages and etype == "chat:message":
            return  # swallow incremental deltas
        if suppress_completion and etype == "chat:completion":
            return  # optionally swallow completion frames
        await emitter(event)

    return _wrapped


def merge_usage_stats(total: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge nested usage statistics.

    For numeric values, sums are accumulated; for dicts, the function recurses;
    other values overwrite the prior value when non-None.

    Args:
        total: Accumulator dictionary to update.
        new:   Newly reported usage block to merge into `total`.

    Returns:
        dict: The updated accumulator dictionary (`total`).

    Example:
        >>> total = {"tokens": 10, "cost": {"input": 0.01}}
        >>> new = {"tokens": 5, "cost": {"output": 0.02}}
        >>> merge_usage_stats(total, new)
        {"tokens": 15, "cost": {"input": 0.01, "output": 0.02}}
    """
    for k, v in new.items():
        if isinstance(v, dict):
            total[k] = merge_usage_stats(total.get(k, {}), v)
        elif isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
        else:
            total[k] = v if v is not None else total.get(k, 0)
    return total


def wrap_code_block(text: str, language: str = "python") -> str:
    """Wrap text in a fenced Markdown code block.

    The fence length adapts to the longest backtick run within the text to avoid
    prematurely closing the block.

    Args:
        text:     The code or content to wrap.
        language: Markdown fence language tag.

    Returns:
        str: Markdown code block.

    Example:
        >>> wrap_code_block("print('hello')", "python")
        "```python\\nprint('hello')\\n```"
        >>> wrap_code_block("code with ``` inside", "text")
        "````text\\ncode with ``` inside\\n````"
    """
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


__all__ = [
    "wrap_event_emitter",
    "merge_usage_stats",
    "wrap_code_block",
]
