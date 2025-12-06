"""Multimodal content processing for OpenRouter Responses Pipe.

Handles image, video, audio, and file processing for the OpenAI Responses API:
- Data URL parsing and validation
- Remote URL downloads with SSRF protection
- Base64 size validation
- MIME type normalization
- Open WebUI file uploads integration

Layer: domain (business logic - multimodal processing)

Dependencies:
- core.logging: SessionLogger for per-request logging
- core.markers: ULID generation for uploads

This module is stateless and depends only on Valves configuration passed to each function.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional

import aiohttp

if TYPE_CHECKING:
    from ..core.config import Valves

# Constants for file handling
_REMOTE_FILE_MAX_SIZE_DEFAULT_MB = 10
_PRIVATE_IP_PATTERNS = [
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^0\."),
    re.compile(r"^169\.254\."),
    re.compile(r"^::1$"),
    re.compile(r"^fe80:"),
    re.compile(r"^fc00:"),
    re.compile(r"^fd00:"),
]


def _read_rag_file_constraints() -> tuple[bool, Optional[int]]:
    """Read Open WebUI's RAG file size constraints from environment.

    Returns:
        Tuple of (is_enabled, max_size_mb)
    """
    rag_enabled = os.getenv("RAG_UPLOAD_ENABLED", "True").lower() == "true"
    rag_limit_mb: Optional[int] = None
    if rag_enabled:
        try:
            rag_limit_mb = int(os.getenv("FILE_MAX_SIZE", "10"))
        except (ValueError, TypeError):
            rag_limit_mb = 10
    return rag_enabled, rag_limit_mb


def _is_ssrf_protected(url: str) -> bool:
    """Check if URL points to private/internal network.

    Args:
        url: URL to check for SSRF risk

    Returns:
        True if URL appears safe (public), False if private/internal
    """
    if not url:
        return True

    # Extract hostname from URL
    try:
        # Remove protocol
        without_protocol = url.split("://", 1)[-1]
        # Remove path and port
        hostname = without_protocol.split("/", 1)[0].split(":", 1)[0].lower()

        # Check for localhost variants
        if hostname in ("localhost", "0.0.0.0"):
            return False

        # Check for private IP patterns
        for pattern in _PRIVATE_IP_PATTERNS:
            if pattern.search(hostname):
                return False

        return True
    except Exception:
        # If parsing fails, be safe and block
        return False


def get_effective_remote_file_limit_mb(valves: Valves) -> int:
    """Return the active remote download limit, honoring RAG constraints.

    Args:
        valves: Pipe configuration

    Returns:
        Effective file size limit in MB
    """
    base_limit_mb = valves.REMOTE_FILE_MAX_SIZE_MB
    rag_enabled, rag_limit_mb = _read_rag_file_constraints()
    if not rag_enabled or rag_limit_mb is None:
        return base_limit_mb

    # Never exceed Open WebUI's configured FILE_MAX_SIZE when RAG is active.
    if base_limit_mb > rag_limit_mb:
        return rag_limit_mb

    # If the valve is still using the default, upgrade to the RAG cap for consistency.
    if (
        base_limit_mb == _REMOTE_FILE_MAX_SIZE_DEFAULT_MB
        and rag_limit_mb > base_limit_mb
    ):
        return rag_limit_mb
    return base_limit_mb


def validate_base64_size(b64_data: str, max_size_mb: int, logger: logging.Logger) -> bool:
    """Validate base64 data size is within configured limits.

    Estimates the decoded size of base64 data and compares it against the
    configured limit to prevent memory issues from huge payloads.

    Args:
        b64_data: Base64-encoded string to validate
        max_size_mb: Maximum size limit in MB
        logger: Logger for warnings

    Returns:
        True if within limits, False if too large

    Note:
        Base64 encoding increases size by approximately 33% (4/3 ratio).
        This method estimates the original size before validation to avoid
        decoding potentially huge strings just to reject them.

    Example:
        >>> if validate_base64_size(huge_base64_string, 50, logger):
        ...     decoded = base64.b64decode(huge_base64_string)
        ... else:
        ...     # Reject without decoding
        ...     return None
    """
    if not b64_data:
        return True  # Empty string is valid

    # Base64 is ~1.33x the original size (4/3 ratio)
    # Estimate original size: (base64_length * 3) / 4
    estimated_size_bytes = (len(b64_data) * 3) / 4
    max_size_bytes = max_size_mb * 1024 * 1024

    if estimated_size_bytes > max_size_bytes:
        estimated_size_mb = estimated_size_bytes / (1024 * 1024)
        logger.warning(
            f"Base64 data size (~{estimated_size_mb:.1f}MB) exceeds configured limit "
            f"({max_size_mb}MB), rejecting to prevent memory issues"
        )
        return False

    return True


def parse_data_url(data_url: str, max_size_mb: int, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """Extract base64 data from data URL.

    Parses data URLs in the format: data:<mime_type>;base64,<base64_data>

    Args:
        data_url: Data URL string to parse
        max_size_mb: Maximum size limit in MB
        logger: Logger for errors

    Returns:
        Dictionary containing:
            - 'data': Decoded bytes from base64
            - 'mime_type': Normalized MIME type
            - 'b64': Original base64 string (without prefix)
        Returns None if parsing fails or format is invalid

    Format Requirements:
        - Must start with 'data:'
        - Must contain ';base64,' separator
        - Base64 data must be valid
        - Size must not exceed max_size_mb

    MIME Type Normalization:
        - 'image/jpg' is normalized to 'image/jpeg'
        - MIME type extracted from prefix (e.g., 'data:image/png;base64,...')

    Size Validation:
        - Validates size before decoding to prevent memory issues
        - Returns None if size exceeds limit

    Note:
        - Invalid base64 data results in None return
        - Oversized data results in None return
        - All exceptions are caught and logged
        - Non-data URLs return None immediately

    Example:
        >>> result = parse_data_url(
        ...     "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
        ...     50,
        ...     logger
        ... )
        >>> if result:
        ...     print(f"MIME: {result['mime_type']}")
        ...     print(f"Size: {len(result['data'])} bytes")
    """
    try:
        if not data_url or not data_url.startswith("data:"):
            return None

        parts = data_url.split(";base64,", 1)
        if len(parts) != 2:
            return None

        # Extract and normalize MIME type
        mime_type = parts[0].replace("data:", "", 1).lower().strip()
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"

        b64_data = parts[1]

        # Validate base64 size before decoding to prevent memory issues
        if not validate_base64_size(b64_data, max_size_mb, logger):
            return None  # Size validation failed, already logged

        file_data = base64.b64decode(b64_data)

        return {
            "data": file_data,
            "mime_type": mime_type,
            "b64": b64_data
        }
    except Exception as exc:
        logger.error(f"Failed to parse data URL: {exc}")
        return None


async def download_remote_file(
    url: str,
    session: aiohttp.ClientSession,
    valves: Valves,
    logger: logging.Logger,
    *,
    event_emitter: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> Optional[Dict[str, Any]]:
    """Download remote file with retry logic, SSRF protection, and size limits.

    Args:
        url: Remote URL to download
        session: aiohttp client session
        valves: Pipe configuration
        logger: Logger for diagnostics
        event_emitter: Optional callable for status updates

    Returns:
        Dictionary containing:
            - 'data': Downloaded bytes
            - 'mime_type': Content-Type from response
            - 'url': Original URL
        Returns None on failure

    Features:
        - SSRF protection (blocks private IP ranges)
        - Size limits from valves
        - Automatic retry with exponential backoff (3 attempts)
        - Progress status updates via event_emitter
        - Streaming download to prevent memory exhaustion

    Note:
        - Respects ENABLE_SSRF_PROTECTION valve
        - Honors REMOTE_FILE_MAX_SIZE_MB and RAG constraints
        - Times out after 60 seconds per attempt
        - Returns None if download exceeds size limit
    """
    if valves.ENABLE_SSRF_PROTECTION and not _is_ssrf_protected(url):
        logger.warning(f"SSRF protection blocked private URL: {url}")
        if event_emitter:
            await emit_status(
                event_emitter,
                f"⚠️ Cannot download from private network: {url}",
                done=True,
                logger=logger
            )
        return None

    effective_limit_mb = get_effective_remote_file_limit_mb(valves)
    max_size_bytes = effective_limit_mb * 1024 * 1024

    max_attempts = 3
    attempt = 0
    start_time = time.perf_counter()

    try:
        while attempt < max_attempts:
            attempt += 1
            try:
                if event_emitter and attempt == 1:
                    await emit_status(
                        event_emitter,
                        f"📥 Downloading {url}",
                        done=False,
                        logger=logger
                    )

                timeout = aiohttp.ClientTimeout(total=60)
                async with session.get(url, timeout=timeout) as resp:
                    resp.raise_for_status()

                    mime_type = resp.content_type or "application/octet-stream"
                    payload = bytearray()

                    async for chunk in resp.content.iter_chunked(8192):
                        payload.extend(chunk)
                        if len(payload) > max_size_bytes:
                            size_mb = len(payload) / (1024 * 1024)
                            logger.warning(
                                f"Remote file {url} exceeds configured limit "
                                f"({size_mb:.1f}MB > {effective_limit_mb}MB), aborting download."
                            )
                            return None

                # Success
                if attempt > 1:
                    elapsed = time.perf_counter() - start_time
                    logger.info(
                        f"Successfully downloaded {url} after {attempt} attempt(s) in {elapsed:.1f}s"
                    )

                return {
                    "data": bytes(payload),
                    "mime_type": mime_type,
                    "url": url
                }

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < max_attempts:
                    delay = min(2 ** attempt, 8)  # Exponential backoff: 2, 4, 8 seconds
                    logger.debug(
                        f"Download attempt {attempt}/{max_attempts} failed for {url}: {exc}. "
                        f"Retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    # Final attempt failed
                    elapsed = time.perf_counter() - start_time
                    logger.error(
                        f"Failed to download {url} after {attempt} attempt(s) in {elapsed:.1f}s: {exc}"
                    )
                    return None

    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        logger.error(
            f"Failed to download {url} after {attempt} attempt(s) in {elapsed:.1f}s: {exc}"
        )
        return None

    return None  # Should not reach here, but safety fallback


async def emit_status(
    event_emitter: Optional[Callable[[dict], Awaitable[None]]],
    message: str,
    done: bool = False,
    *,
    logger: logging.Logger,
):
    """Emit status updates to the Open WebUI client.

    Sends progress indicators to the UI during file/image processing operations.

    Args:
        event_emitter: Async callable for sending events to the client,
                      or None if no emitter available
        message: Status message to display (supports emoji for visual indicators)
        done: Whether this status represents completion (default: False)
        logger: Logger for error reporting

    Status Message Conventions:
        - 📥 Download/upload in progress
        - ✅ Successful completion
        - ⚠️ Warning or non-critical error
        - 🔴 Critical error

    Note:
        - If event_emitter is None, this method is a no-op
        - Errors during emission are caught and logged
        - Does not interrupt processing flow

    Example:
        >>> await emit_status(
        ...     emitter,
        ...     "📥 Downloading remote image...",
        ...     done=False,
        ...     logger=logger
        ... )
    """
    if event_emitter:
        try:
            await event_emitter({
                "type": "status",
                "data": {
                    "description": message,
                    "done": done
                }
            })
        except Exception as exc:
            logger.error(f"Failed to emit status: {exc}")


__all__ = [
    "get_effective_remote_file_limit_mb",
    "validate_base64_size",
    "parse_data_url",
    "download_remote_file",
    "emit_status",
]
