"""ULID marker utilities for OpenRouter Responses Pipe.

This module provides ULID (Universally Unique Lexicographically Sortable Identifier)
generation and marker parsing for persistent artifacts. Markers are embedded in
assistant messages as invisible Markdown comments to reference stored artifacts.

Layer: core (no dependencies on domain or adapters)
"""

from __future__ import annotations

import secrets
import time
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ULID_LENGTH = 20
ULID_TIME_LENGTH = 16
ULID_RANDOM_LENGTH = ULID_LENGTH - ULID_TIME_LENGTH
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_SET = frozenset(CROCKFORD_ALPHABET)
_ULID_TIME_MASK = (1 << (ULID_TIME_LENGTH * 5)) - 1
_MARKER_SUFFIX = "]: #"

# Tool output pruning thresholds
_TOOL_OUTPUT_PRUNE_MIN_LENGTH = 800
_TOOL_OUTPUT_PRUNE_HEAD_CHARS = 256
_TOOL_OUTPUT_PRUNE_TAIL_CHARS = 128


# ─────────────────────────────────────────────────────────────────────────────
# ULID Generation
# ─────────────────────────────────────────────────────────────────────────────

def _encode_crockford(value: int, length: int) -> str:
    """Encode an integer into a fixed-width Crockford base32 string.

    Args:
        value: Non-negative integer to encode
        length: Target string length (pads with leading zeros)

    Returns:
        str: Crockford-encoded string of specified length

    Raises:
        ValueError: If value is negative
    """
    if value < 0:
        raise ValueError("value must be non-negative")
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_item_id() -> str:
    """Generate a 20-char ULID using a 16-char time component + 4-char random tail.

    ULIDs are:
    - Lexicographically sortable (time-based prefix)
    - Collision-resistant (random suffix)
    - URL-safe (Crockford base32 alphabet)

    Returns:
        str: Crockford-encoded ULID (stateless + monotonic per timestamp)

    Example:
        >>> generate_item_id()
        '01HQABCDEF1234567890'
    """
    timestamp = time.time_ns() & _ULID_TIME_MASK
    time_component = _encode_crockford(timestamp, ULID_TIME_LENGTH)
    random_bits = secrets.randbits(ULID_RANDOM_LENGTH * 5)
    random_component = _encode_crockford(random_bits, ULID_RANDOM_LENGTH)
    return f"{time_component}{random_component}"


# ─────────────────────────────────────────────────────────────────────────────
# Marker Serialization & Parsing
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_marker(ulid: str) -> str:
    """Return the hidden marker representation for ``ulid``.

    Markers are formatted as Markdown link references that are invisible
    in rendered output but parseable from plain text.

    Args:
        ulid: 20-character ULID string

    Returns:
        str: Marker string like "[01HQABCDEF1234567890]: #"

    Example:
        >>> _serialize_marker("01HQABCDEF1234567890")
        '[01HQABCDEF1234567890]: #'
    """
    return f"[{ulid}{_MARKER_SUFFIX}"


def _extract_marker_ulid(line: str) -> str | None:
    """Return the ULID embedded in a hidden marker line, if present.

    Args:
        line: Single line of text to parse

    Returns:
        str | None: ULID if line is a valid marker, None otherwise

    Example:
        >>> _extract_marker_ulid("[01HQABCDEF1234567890]: #")
        '01HQABCDEF1234567890'
        >>> _extract_marker_ulid("Regular text")
        None
    """
    if not line:
        return None
    stripped = line.strip()
    if not stripped.startswith("[") or not stripped.endswith(_MARKER_SUFFIX):
        return None
    body = stripped[1 : -len(_MARKER_SUFFIX)]
    if len(body) != ULID_LENGTH:
        return None
    # Validate all characters are in Crockford alphabet
    for char in body:
        if char not in _CROCKFORD_SET:
            return None
    return body


def contains_marker(text: str) -> bool:
    """Fast check: does the text contain any embedded ULID markers?

    Args:
        text: Text to scan

    Returns:
        bool: True if markers are present, False otherwise

    Example:
        >>> contains_marker("Hello\\n[01HQABCDEF1234567890]: #\\nWorld")
        True
        >>> contains_marker("Hello World")
        False
    """
    return bool(_iter_marker_spans(text))


def _iter_marker_spans(text: str) -> list[dict[str, Any]]:
    """Return ordered ULID marker spans with start/end positions.

    Args:
        text: Text to scan for markers

    Returns:
        list[dict]: List of spans like:
            [{"start": 10, "end": 35, "marker": "01HQ..."}]

    Note:
        Spans are sorted by start position.
    """
    if not text:
        return []

    spans: list[dict[str, Any]] = []
    cursor = 0
    for segment in text.splitlines(True):  # Keep line endings
        stripped = segment.strip()
        marker_ulid = _extract_marker_ulid(stripped)
        if marker_ulid:
            offset = segment.find(stripped)
            start = cursor + (offset if offset >= 0 else 0)
            spans.append(
                {
                    "start": start,
                    "end": start + len(stripped),
                    "marker": marker_ulid,
                }
            )
        cursor += len(segment)

    spans.sort(key=lambda span: span["start"])
    return spans


def split_text_by_markers(text: str) -> list[dict]:
    """Split text into a sequence of literal segments and marker segments.

    Args:
        text: Source text possibly containing embedded markers

    Returns:
        list[dict]: A list of segments like:
            [
              {"type": "text",   "text": "..."},
              {"type": "marker", "marker": "01H...Q4"},
              ...
            ]

    Example:
        >>> split_text_by_markers("Hello\\n[01HQABC]: #\\nWorld")
        [
            {"type": "text", "text": "Hello\\n"},
            {"type": "marker", "marker": "01HQABC..."},
            {"type": "text", "text": "\\nWorld"}
        ]
    """
    segments: list[dict[str, Any]] = []
    last = 0
    for span in _iter_marker_spans(text):
        # Add text segment before marker (if any)
        if span["start"] > last:
            segments.append({"type": "text", "text": text[last:span["start"]]})
        # Add marker segment
        segments.append(
            {
                "type": "marker",
                "marker": span["marker"],
            }
        )
        last = span["end"]
    # Add trailing text (if any)
    if last < len(text):
        segments.append({"type": "text", "text": text[last:]})
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Tool Output Pruning
# ─────────────────────────────────────────────────────────────────────────────

def prune_tool_output(output: str, marker: str) -> str:
    """Prune large tool outputs to save tokens while keeping context.

    For outputs exceeding threshold, keeps the beginning and end with a marker
    reference in the middle indicating where full output is stored.

    Args:
        output: Full tool output string
        marker: ULID marker for referencing stored artifact

    Returns:
        str: Pruned output if large, original output if small

    Example:
        >>> output = "x" * 1000
        >>> pruned = prune_tool_output(output, "01HQABC...")
        >>> "[... output truncated, full version: [01HQABC...]: # ...]" in pruned
        True
    """
    if len(output) < _TOOL_OUTPUT_PRUNE_MIN_LENGTH:
        return output

    head = output[:_TOOL_OUTPUT_PRUNE_HEAD_CHARS]
    tail = output[-_TOOL_OUTPUT_PRUNE_TAIL_CHARS:]
    pruned_bytes = len(output) - (_TOOL_OUTPUT_PRUNE_HEAD_CHARS + _TOOL_OUTPUT_PRUNE_TAIL_CHARS)

    return (
        f"{head}\n\n"
        f"[... output truncated ({pruned_bytes} chars omitted), "
        f"full version: {_serialize_marker(marker)} ...]\n\n"
        f"{tail}"
    )
