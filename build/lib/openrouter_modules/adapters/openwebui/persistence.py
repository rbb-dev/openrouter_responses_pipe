"""OpenRouter artifact persistence adapter.

This module provides artifact storage with Redis write-behind caching:
- SQLAlchemy artifact persistence with dynamic table creation
- Redis write-behind cache for multi-worker deployments
- Encryption and compression integration
- Artifact CRUD operations with circuit breaker
- Background cleanup worker
- Storage user management for file uploads

Layer: adapters (integrates with Open WebUI's database and Redis)

Dependencies:
- SQLAlchemy: Dynamic table creation and ORM
- Redis: Write-behind caching and pub/sub coordination
- core.encryption: Fernet encryption for sensitive artifacts
- core.logging: SessionLogger for contextvars-based logging
- core.markers: generate_item_id for ULID generation

Architecture:
    Write Path (Single Worker):
        Client → _db_persist() → SQLAlchemy → Database

    Write Path (Multi-Worker with Redis):
        Client → _redis_enqueue_rows() → Redis Queue
                      ↓ (background worker)
                _flush_redis_queue() → SQLAlchemy → Database

    Read Path (with Redis):
        Client → _db_fetch() → Redis Cache (hit) → Client
                            ↓ (miss)
                        SQLAlchemy → Cache + Client

Design Notes:
- Dynamic table creation prevents collisions in multi-pipe deployments
- Circuit breaker prevents repeated failures from blocking requests
- Lock-based flush coordination ensures single-writer per flush
- Pub/sub notifications trigger immediate flushes across workers
- Storage user management provides fallback user for file uploads
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import functools
import hashlib
import inspect
import io
import json
import logging
import random
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type

from cryptography.fernet import Fernet, InvalidToken
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.markers import generate_item_id

if TYPE_CHECKING:
    from fastapi import BackgroundTasks, Request
    from fastapi.datastructures import Headers, UploadFile
    from sqlalchemy import Engine, Table
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session, sessionmaker

# Conditional imports for Open WebUI integration
try:
    from open_webui.apps.webui.models.files import Files
    from open_webui.apps.webui.models.users import Users
    from open_webui.storage import upload_file_handler
    from open_webui.utils import run_in_threadpool
except (ImportError, ModuleNotFoundError):
    Files = None  # type: ignore[assignment,misc]
    Users = None  # type: ignore[assignment,misc]
    upload_file_handler = None  # type: ignore[assignment]
    run_in_threadpool = None  # type: ignore[assignment]

# Conditional imports for Redis
try:
    import redis.asyncio as aioredis  # type: ignore[import-not-found]
    _RedisClient = Any
except (ImportError, ModuleNotFoundError):
    aioredis = None
    _RedisClient = None

# Conditional imports for SQLAlchemy
try:
    from sqlalchemy import Boolean, Column, DateTime, String, Text, create_engine, text
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session, declarative_base, sessionmaker
except (ImportError, ModuleNotFoundError):
    SQLAlchemyError = Exception  # type: ignore[assignment,misc]
    Session = Any  # type: ignore[assignment,misc]
    sessionmaker = Any  # type: ignore[assignment,misc]

# Conditional imports for LZ4 compression
try:
    import lz4.frame as lz4frame  # type: ignore[import-not-found]
except (ImportError, ModuleNotFoundError):
    lz4frame = None

# Redis pub/sub channel for flush notifications
_REDIS_FLUSH_CHANNEL = "openrouter:artifact_flush"

# Payload compression/encryption flags
_PAYLOAD_FLAG_PLAIN = 0x00
_PAYLOAD_FLAG_LZ4 = 0x01
_PAYLOAD_HEADER_SIZE = 1

# Module logger
LOGGER = logging.getLogger(__name__)


def _sanitize_table_fragment(raw: str) -> str:
    """Return a SQL-safe table name fragment from an arbitrary pipe identifier.

    Args:
        raw: Pipe identifier (e.g., "openrouter-responses-pipe")

    Returns:
        Sanitized table fragment (e.g., "openrouter_responses_pipe")

    Example:
        >>> _sanitize_table_fragment("my-pipe@v2.0")
        'my_pipe_v2_0'
    """
    normalized = re.sub(r"[^a-z0-9_]", "_", raw.lower())
    return re.sub(r"_+", "_", normalized).strip("_") or "default"


def _extract_internal_file_id(url: str) -> Optional[str]:
    """Extract Open WebUI file ID from internal URLs.

    Args:
        url: URL to parse (e.g., "/api/v1/files/abc123")

    Returns:
        File ID if found, None otherwise

    Example:
        >>> _extract_internal_file_id("/api/v1/files/abc123")
        'abc123'
        >>> _extract_internal_file_id("https://external.com/image.jpg")
        None
    """
    if not isinstance(url, str):
        return None
    pattern = r"/api/v1/files/([a-f0-9-]+)"
    match = re.search(pattern, url, flags=re.IGNORECASE)
    return match.group(1) if match else None


class ArtifactPersistence:
    """SQLAlchemy + Redis artifact storage with encryption and compression.

    This class manages persistent storage for OpenRouter response artifacts
    (reasoning tokens, images, tool results) with Redis write-behind caching
    for multi-worker deployments.

    Attributes:
        valves: Configuration object with database and Redis settings
        logger: SessionLogger instance for contextvars-based logging
        _encryption_key: Fernet encryption key (optional)
        _encrypt_all: Whether to encrypt all artifacts (vs reasoning only)
        _compression_enabled: Whether LZ4 compression is available
        _compression_min_bytes: Minimum payload size for compression
        _fernet: Cached Fernet cipher instance
        _engine: SQLAlchemy engine for database connection
        _session_factory: SQLAlchemy session factory
        _item_model: Dynamically created SQLAlchemy model class
        _artifact_table_name: Fully qualified table name
        _db_executor: ThreadPoolExecutor for sync database operations
        _redis_client: Redis client for write-behind caching
        _redis_enabled: Whether Redis caching is active
        _redis_pending_key: Redis key for pending artifact queue
        _redis_cache_prefix: Redis key prefix for cached artifacts

    Example:
        >>> persistence = ArtifactPersistence(valves, logger)
        >>> await persistence.persist_artifacts([{
        ...     "chat_id": "chat123",
        ...     "message_id": "msg456",
        ...     "model_id": "openai/gpt-4",
        ...     "payload": {"type": "reasoning", "content": "..."}
        ... }])
        ['01HQJX7..']
    """

    def __init__(
        self,
        valves: Any,
        logger: Any,
        *,
        encryption_key: Optional[str] = None,
        encrypt_all: bool = False,
    ) -> None:
        """Initialize artifact store with configuration.

        Args:
            valves: Configuration object with database settings
            logger: Logger instance
            encryption_key: Optional Fernet encryption key
            encrypt_all: Whether to encrypt all artifacts
        """
        self.valves = valves
        self.logger = logger
        self._encryption_key = encryption_key or ""
        self._encrypt_all = encrypt_all
        self._compression_min_bytes = getattr(valves, "MIN_COMPRESS_BYTES", 0)
        self._compression_enabled = bool(
            getattr(valves, "ENABLE_LZ4_COMPRESSION", True) and lz4frame is not None
        )
        self._fernet: Optional[Fernet] = None
        self._engine: Optional[Any] = None
        self._session_factory: Optional[Any] = None
        self._item_model: Optional[Type[Any]] = None
        self._artifact_table_name: Optional[str] = None
        self._db_executor: Optional[ThreadPoolExecutor] = None
        self._artifact_store_signature: Optional[Tuple[str, str]] = None
        self._lz4_warning_emitted = False

        # Circuit breaker for database operations
        self._db_breakers: Dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
        self._breaker_threshold = 5
        self._breaker_window_seconds = 60

        # Redis write-behind cache
        self._redis_enabled = False
        self._redis_client: Optional[Any] = None
        self._redis_listener_task: Optional[asyncio.Task] = None
        self._redis_flush_task: Optional[asyncio.Task] = None
        self._redis_pending_key = "openrouter:pending"
        self._redis_cache_prefix = "openrouter:artifact"
        self._redis_flush_lock_key = "openrouter:flush_lock"
        self._redis_ttl = getattr(valves, "REDIS_CACHE_TTL_SECONDS", 600)

        # Storage user management
        self._storage_user_cache: Optional[Any] = None
        self._storage_user_lock: Optional[asyncio.Lock] = None
        self._storage_role_warning_emitted: bool = False
        self._user_insert_param_names: Optional[Tuple[str, ...]] = None

    def shutdown(self) -> None:
        """Public method to shut down background resources."""
        executor = self._db_executor
        self._db_executor = None
        if executor:
            executor.shutdown(wait=True)

    def _get_fernet(self) -> Optional[Fernet]:
        """Return (and cache) the Fernet helper derived from the encryption key.

        Returns:
            Fernet instance if encryption key is configured, None otherwise

        Example:
            >>> fernet = persistence._get_fernet()
            >>> encrypted = fernet.encrypt(b"data")
        """
        if not self._encryption_key:
            return None
        if self._fernet is None:
            digest = hashlib.sha256(self._encryption_key.encode("utf-8")).digest()
            key = base64.urlsafe_b64encode(digest)
            self._fernet = Fernet(key)
        return self._fernet

    def _should_encrypt(self, item_type: str) -> bool:
        """Determine whether a payload of ``item_type`` must be encrypted.

        Args:
            item_type: Artifact type (e.g., "reasoning", "image")

        Returns:
            True if encryption is required, False otherwise

        Example:
            >>> persistence._should_encrypt("reasoning")
            True
            >>> persistence._should_encrypt("image")  # If encrypt_all=False
            False
        """
        if not self._encryption_key:
            return False
        if self._encrypt_all:
            return True
        return (item_type or "").lower() == "reasoning"

    def _serialize_payload_bytes(self, payload: Dict[str, Any]) -> bytes:
        """Return compact JSON bytes for ``payload``.

        Args:
            payload: Dictionary to serialize

        Returns:
            Compact JSON bytes

        Example:
            >>> persistence._serialize_payload_bytes({"type": "reasoning"})
            b'{"type":"reasoning"}'
        """
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _maybe_compress_payload(self, serialized: bytes) -> Tuple[bytes, bool]:
        """Compress serialized bytes when LZ4 is available and thresholds are met.

        Args:
            serialized: JSON bytes to compress

        Returns:
            Tuple of (compressed_bytes, was_compressed)

        Example:
            >>> data, compressed = persistence._maybe_compress_payload(b"..." * 1000)
            >>> compressed
            True
        """
        if not serialized:
            return serialized, False
        if not self._compression_enabled:
            return serialized, False
        if self._compression_min_bytes and len(serialized) < self._compression_min_bytes:
            return serialized, False
        if lz4frame is None:
            return serialized, False
        try:
            compressed = lz4frame.compress(serialized)
        except Exception as exc:
            self.logger.warning(
                "LZ4 compression failed; disabling compression for the remainder of this process: %s",
                exc,
                exc_info=self.logger.isEnabledFor(logging.DEBUG),
            )
            self._compression_enabled = False
            return serialized, False
        if not compressed or len(compressed) >= len(serialized):
            return serialized, False
        return compressed, True

    def _encode_payload_bytes(self, payload: Dict[str, Any]) -> bytes:
        """Serialize payload bytes and prepend a compression flag header.

        Args:
            payload: Dictionary to encode

        Returns:
            Encoded bytes with compression flag header

        Format:
            [1 byte flag][payload]
            - 0x00: Plain JSON
            - 0x01: LZ4 compressed JSON

        Example:
            >>> encoded = persistence._encode_payload_bytes({"data": "..."})
            >>> encoded[0]
            1  # LZ4 flag if compressed
        """
        serialized = self._serialize_payload_bytes(payload)
        data, compressed = self._maybe_compress_payload(serialized)
        flag = _PAYLOAD_FLAG_LZ4 if compressed else _PAYLOAD_FLAG_PLAIN
        return bytes([flag]) + data

    def _decode_payload_bytes(self, payload_bytes: bytes) -> Dict[str, Any]:
        """Decode stored payload bytes into dictionaries.

        Args:
            payload_bytes: Encoded bytes from database

        Returns:
            Decoded dictionary

        Raises:
            ValueError: If payload cannot be decoded

        Example:
            >>> encoded = persistence._encode_payload_bytes({"type": "reasoning"})
            >>> decoded = persistence._decode_payload_bytes(encoded)
            >>> decoded["type"]
            'reasoning'
        """
        if not payload_bytes:
            return {}
        if len(payload_bytes) <= _PAYLOAD_HEADER_SIZE:
            body = payload_bytes
        else:
            flag = payload_bytes[0]
            body = payload_bytes[_PAYLOAD_HEADER_SIZE:]
            if flag == _PAYLOAD_FLAG_LZ4:
                body = self._lz4_decompress(body)
            elif flag != _PAYLOAD_FLAG_PLAIN:
                # Backward compatibility: old ciphertexts lack the header and are just JSON.
                body = payload_bytes
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Unable to decode persisted artifact payload.") from exc

    def _lz4_decompress(self, data: bytes) -> bytes:
        """Decompress LZ4 payloads or raise descriptive errors.

        Args:
            data: LZ4 compressed bytes

        Returns:
            Decompressed bytes

        Raises:
            RuntimeError: If LZ4 library is unavailable
            ValueError: If decompression fails

        Example:
            >>> compressed = lz4frame.compress(b"data")
            >>> decompressed = persistence._lz4_decompress(compressed)
            >>> decompressed
            b'data'
        """
        if not data:
            return b""
        if lz4frame is None:
            raise RuntimeError(
                "Encountered compressed artifact, but the 'lz4' package is unavailable."
            )
        try:
            return lz4frame.decompress(data)
        except Exception as exc:
            raise ValueError("Failed to decompress persisted artifact payload.") from exc

    def _encrypt_payload(self, payload: Dict[str, Any]) -> str:
        """Encrypt payload bytes using the configured Fernet helper.

        Args:
            payload: Dictionary to encrypt

        Returns:
            Base64-encoded ciphertext

        Raises:
            RuntimeError: If encryption key is not configured

        Example:
            >>> ciphertext = persistence._encrypt_payload({"sensitive": "data"})
            >>> isinstance(ciphertext, str)
            True
        """
        fernet = self._get_fernet()
        if not fernet:
            raise RuntimeError("Encryption requested but ARTIFACT_ENCRYPTION_KEY is not configured.")
        encoded = self._encode_payload_bytes(payload)
        return fernet.encrypt(encoded).decode("utf-8")

    def _decrypt_payload(self, ciphertext: str) -> Dict[str, Any]:
        """Decrypt ciphertext previously produced by :meth:`_encrypt_payload`.

        Args:
            ciphertext: Base64-encoded ciphertext

        Returns:
            Decrypted dictionary

        Raises:
            RuntimeError: If decryption key is not configured
            ValueError: If ciphertext is invalid

        Example:
            >>> ciphertext = persistence._encrypt_payload({"data": "..."})
            >>> decrypted = persistence._decrypt_payload(ciphertext)
            >>> decrypted["data"]
            '...'
        """
        fernet = self._get_fernet()
        if not fernet:
            raise RuntimeError("Decryption requested but ARTIFACT_ENCRYPTION_KEY is not configured.")
        try:
            plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt payload (invalid token).") from exc
        return self._decode_payload_bytes(plaintext)

    def _encrypt_if_needed(self, item_type: str, payload: Dict[str, Any]) -> Tuple[Any, bool]:
        """Optionally encrypt ``payload`` depending on the item type.

        Args:
            item_type: Artifact type (e.g., "reasoning")
            payload: Dictionary to potentially encrypt

        Returns:
            Tuple of (stored_payload, was_encrypted)
            - If encrypted: ({"ciphertext": "..."}, True)
            - If not encrypted: (original_payload, False)

        Example:
            >>> stored, encrypted = persistence._encrypt_if_needed("reasoning", {"content": "..."})
            >>> encrypted
            True
            >>> "ciphertext" in stored
            True
        """
        if not self._should_encrypt(item_type):
            return payload, False
        encrypted = self._encrypt_payload(payload)
        return {"ciphertext": encrypted}, True

    def _make_db_row(
        self,
        chat_id: Optional[str],
        message_id: Optional[str],
        model_id: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Construct a persistence-ready row dict or return ``None`` when invalid.

        Args:
            chat_id: Chat identifier
            message_id: Message identifier
            model_id: Model identifier
            payload: Artifact payload dictionary

        Returns:
            Row dictionary or None if validation fails

        Example:
            >>> row = persistence._make_db_row(
            ...     "chat123", "msg456", "openai/gpt-4",
            ...     {"type": "reasoning", "content": "..."}
            ... )
            >>> row["item_type"]
            'reasoning'
        """
        if not (chat_id and self._item_model):
            return None
        if not message_id:
            self.logger.warning("Skipping artifact persistence for chat_id=%s: missing message_id.", chat_id)
            return None
        if not isinstance(payload, dict):
            return None
        item_type = payload.get("type", "unknown")
        return {
            "chat_id": chat_id,
            "message_id": message_id,
            "model_id": model_id,
            "item_type": item_type,
            "payload": payload,
        }

    def _db_persist_sync(self, rows: List[Dict[str, Any]]) -> List[str]:
        """Persist prepared rows once; intentionally no automatic retry logic.

        Args:
            rows: List of row dictionaries to persist

        Returns:
            List of ULIDs for persisted artifacts

        Note:
            Synchronous method designed to run in ThreadPoolExecutor.
            Uses batching to reduce transaction overhead.

        Example:
            >>> ulids = persistence._db_persist_sync([
            ...     {"chat_id": "chat123", "message_id": "msg456", "payload": {...}},
            ... ])
            >>> len(ulids)
            1
        """
        if not rows or not self._item_model or not self._session_factory:
            return []

        cleanup_rows = False
        try:
            ulids: List[str] = [
                row.get("id")
                for row in rows
                if row.get("_persisted") and isinstance(row.get("id"), str)
            ]
            ulids = [ulid for ulid in ulids if ulid]
            batch_size = getattr(self.valves, "DB_BATCH_SIZE", 10)
            pending_rows = [row for row in rows if not row.get("_persisted")]
            if not pending_rows:
                if ulids:
                    self.logger.debug("Persisted %d response artifact(s) to %s.", len(ulids), self._artifact_table_name)
                cleanup_rows = True
                return ulids

            for start in range(0, len(pending_rows), batch_size):
                chunk = pending_rows[start : start + batch_size]
                now = datetime.datetime.utcnow()
                instances = []
                chunk_ulids: List[str] = []
                persisted_rows: List[Dict[str, Any]] = []
                for row in chunk:
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        self.logger.warning("Skipping artifact persist for chat_id=%s message_id=%s: payload is not a dict.", row.get("chat_id"), row.get("message_id"))
                        continue
                    ulid = row.get("id") or generate_item_id()
                    stored_payload, is_encrypted = self._encrypt_if_needed(row.get("item_type", ""), payload)
                    instances.append(
                        self._item_model(  # type: ignore[call-arg]
                            id=ulid,
                            chat_id=row.get("chat_id"),
                            message_id=row.get("message_id"),
                            model_id=row.get("model_id"),
                            item_type=row.get("item_type"),
                            payload=stored_payload,
                            is_encrypted=is_encrypted,
                            created_at=now,
                        )
                    )
                    chunk_ulids.append(ulid)
                    persisted_rows.append(row)

                if not instances:
                    continue

                session = self._session_factory()  # type: ignore[call-arg]
                try:
                    session.add_all(instances)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    self.logger.error("Failed to persist response artifacts: %s", exc, exc_info=self.logger.isEnabledFor(logging.DEBUG))
                    raise
                finally:
                    session.close()

                for row in persisted_rows:
                    row["_persisted"] = True
                ulids.extend(chunk_ulids)

            if ulids:
                self.logger.debug("Persisted %d response artifact(s) to %s.", len(ulids), self._artifact_table_name)
            cleanup_rows = True
            return ulids
        finally:
            if cleanup_rows:
                for row in rows:
                    row.pop("_persisted", None)

    async def _db_persist_direct(self, rows: List[Dict[str, Any]], user_id: str = "") -> List[str]:
        """Persist artifacts directly to database with retry logic.

        Args:
            rows: List of row dictionaries to persist
            user_id: User ID for circuit breaker tracking

        Returns:
            List of ULIDs for persisted artifacts

        Example:
            >>> ulids = await persistence._db_persist_direct([...])
        """
        if not rows or not self._db_executor or not self._item_model or not self._session_factory:
            return []

        retryer = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        loop = asyncio.get_running_loop()
        async for attempt in retryer:
            with attempt:
                try:
                    ulids = await loop.run_in_executor(
                        self._db_executor, self._db_persist_sync, rows
                    )
                except Exception as exc:
                    if self._is_duplicate_key_error(exc):
                        self.logger.debug("Duplicate key detected during DB persist; assuming prior flush succeeded")
                        return [row.get("id") for row in rows if row.get("id")]
                    raise
                if self._redis_enabled:
                    await self._redis_cache_rows(rows)
                self._reset_db_failure(user_id)
                return ulids
        return []

    def _is_duplicate_key_error(self, exc: Exception) -> bool:
        """Check if exception indicates a duplicate key constraint violation.

        Args:
            exc: Exception to check

        Returns:
            True if duplicate key error, False otherwise
        """
        if isinstance(exc, SQLAlchemyError):
            messages = [str(exc)]
            orig = getattr(exc, "orig", None)
            if orig:
                messages.append(str(orig))
            lowered = " ".join(messages).lower()
            keywords = ("duplicate key", "unique constraint", "already exists")
            return any(keyword in lowered for keyword in keywords)
        return False

    def _db_fetch_sync(
        self,
        chat_id: str,
        message_id: Optional[str],
        item_ids: List[str],
    ) -> Dict[str, dict]:
        """Synchronously fetch persisted artifacts for ``chat_id``.

        Args:
            chat_id: Chat identifier
            message_id: Optional message filter
            item_ids: List of artifact IDs to fetch

        Returns:
            Dictionary mapping artifact IDs to payloads

        Example:
            >>> artifacts = persistence._db_fetch_sync("chat123", None, ["ulid1", "ulid2"])
            >>> artifacts["ulid1"]["type"]
            'reasoning'
        """
        if not item_ids or not self._item_model or not self._session_factory:
            return {}
        model = self._item_model
        session = self._session_factory()  # type: ignore[call-arg]
        try:
            query = session.query(model).filter(model.chat_id == chat_id)
            if item_ids:
                query = query.filter(model.id.in_(item_ids))
            if message_id:
                query = query.filter(model.message_id == message_id)
            rows = query.all()
        finally:
            session.close()

        results: Dict[str, dict] = {}
        for row in rows:
            payload = row.payload
            if row.is_encrypted:
                ciphertext = ""
                if isinstance(payload, dict):
                    ciphertext = payload.get("ciphertext", "")
                elif isinstance(payload, str):
                    ciphertext = payload
                try:
                    payload = self._decrypt_payload(ciphertext or "")
                except Exception as exc:
                    self.logger.warning("Failed to decrypt artifact %s: %s", row.id, exc, exc_info=self.logger.isEnabledFor(logging.DEBUG))
                    continue
            if isinstance(payload, dict):
                results[row.id] = payload
        return results

    async def _db_fetch_direct(
        self,
        chat_id: str,
        message_id: Optional[str],
        item_ids: List[str],
    ) -> Dict[str, dict]:
        """Fetch artifacts from database with retry logic.

        Args:
            chat_id: Chat identifier
            message_id: Optional message filter
            item_ids: List of artifact IDs

        Returns:
            Dictionary mapping artifact IDs to payloads
        """
        retryer = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        loop = asyncio.get_running_loop()
        async for attempt in retryer:
            with attempt:
                fetch_call = functools.partial(self._db_fetch_sync, chat_id, message_id, item_ids)
                return await loop.run_in_executor(self._db_executor, fetch_call)
        return {}

    def _delete_artifacts_sync(self, artifact_ids: List[str]) -> None:
        """Synchronously delete artifacts by ULID.

        Args:
            artifact_ids: List of artifact IDs to delete

        Note:
            Runs in ThreadPoolExecutor context.
        """
        if not (artifact_ids and self._session_factory and self._item_model):
            return
        session = self._session_factory()  # type: ignore[call-arg]
        try:
            (
                session.query(self._item_model)
                .filter(self._item_model.id.in_(artifact_ids))
                .delete(synchronize_session=False)
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def delete_artifacts(self, refs: List[Tuple[str, str]]) -> None:
        """Delete persisted artifacts (and cached copies) once they have been replayed.

        Args:
            refs: List of (chat_id, artifact_id) tuples

        Example:
            >>> await persistence.delete_artifacts([("chat123", "ulid1"), ("chat123", "ulid2")])
        """
        if not refs:
            return
        ids = sorted({artifact_id for _, artifact_id in refs if artifact_id})
        if not ids or not self._db_executor:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._db_executor, functools.partial(self._delete_artifacts_sync, ids))
        if self._redis_enabled and self._redis_client:
            keys = [self._redis_cache_key(chat_id, artifact_id) for chat_id, artifact_id in refs]
            keys = [key for key in keys if key]
            if keys:
                await self._redis_client.delete(*keys)

    def _redis_cache_key(self, chat_id: Optional[str], row_id: Optional[str]) -> Optional[str]:
        """Generate Redis cache key for an artifact.

        Args:
            chat_id: Chat identifier
            row_id: Artifact identifier

        Returns:
            Redis key or None if inputs invalid

        Example:
            >>> persistence._redis_cache_key("chat123", "ulid1")
            'openrouter:artifact:chat123:ulid1'
        """
        if not (chat_id and row_id):
            return None
        return f"{self._redis_cache_prefix}:{chat_id}:{row_id}"

    async def _redis_enqueue_rows(self, rows: List[Dict[str, Any]]) -> List[str]:
        """Enqueue artifacts into Redis for asynchronous DB flushing.

        Args:
            rows: List of row dictionaries

        Returns:
            List of artifact IDs

        Note:
            Falls back to direct DB write if Redis unavailable.
        """
        if not rows:
            return []

        if not (self._redis_enabled and self._redis_client):
            return await self._db_persist_direct(rows)

        for row in rows:
            row.setdefault("id", generate_item_id())

        try:
            pipe = self._redis_client.pipeline()
            for row in rows:
                serialized = json.dumps(row, ensure_ascii=False)
                pipe.rpush(self._redis_pending_key, serialized)
            await pipe.execute()

            await self._redis_cache_rows(rows)
            await self._redis_client.publish(_REDIS_FLUSH_CHANNEL, "flush")

            self.logger.debug("Enqueued %d artifacts to Redis pending queue", len(rows))
            return [row["id"] for row in rows]
        except Exception as exc:
            self.logger.warning("Redis enqueue failed, falling back to direct DB write: %s", exc)
            return await self._db_persist_direct(rows)

    async def _redis_cache_rows(self, rows: List[Dict[str, Any]], *, chat_id: Optional[str] = None) -> None:
        """Cache artifact rows in Redis.

        Args:
            rows: List of row dictionaries
            chat_id: Optional chat ID override

        Note:
            No-op if Redis is disabled.
        """
        if not (self._redis_enabled and self._redis_client):
            return
        pipe = self._redis_client.pipeline()
        for row in rows:
            row_payload = row if "payload" in row else {"payload": row}
            cache_key = self._redis_cache_key(row.get("chat_id") or chat_id, row.get("id"))
            if not cache_key:
                continue
            pipe.setex(cache_key, self._redis_ttl, json.dumps(row_payload, ensure_ascii=False))
        await pipe.execute()

    async def _redis_fetch_rows(
        self,
        chat_id: Optional[str],
        item_ids: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch cached artifacts from Redis.

        Args:
            chat_id: Chat identifier
            item_ids: List of artifact IDs

        Returns:
            Dictionary mapping artifact IDs to payloads
        """
        if not (self._redis_enabled and self._redis_client and chat_id and item_ids):
            return {}
        keys: List[str] = []
        id_lookup: List[str] = []
        for item_id in item_ids:
            cache_key = self._redis_cache_key(chat_id, item_id)
            if cache_key:
                keys.append(cache_key)
                id_lookup.append(item_id)
        if not keys:
            return {}
        values = await self._redis_client.mget(keys)
        cached: Dict[str, Dict[str, Any]] = {}
        for item_id, raw in zip(id_lookup, values):
            if not raw:
                continue
            try:
                row_data = json.loads(raw)
                cached[item_id] = row_data.get("payload", row_data)
            except json.JSONDecodeError:
                continue
        return cached

    def _db_breaker_allows(self, user_id: str) -> bool:
        """Check if database operations are allowed for user (circuit breaker).

        Args:
            user_id: User identifier

        Returns:
            True if operations allowed, False if breaker open
        """
        if not user_id:
            return True
        window = self._db_breakers[user_id]
        now = time.time()
        while window and now - window[0] > self._breaker_window_seconds:
            window.popleft()
        return len(window) < self._breaker_threshold

    def _record_db_failure(self, user_id: str) -> None:
        """Record a database failure for circuit breaker tracking.

        Args:
            user_id: User identifier
        """
        if user_id:
            self._db_breakers[user_id].append(time.time())

    def _reset_db_failure(self, user_id: str) -> None:
        """Reset database failure counter for user.

        Args:
            user_id: User identifier
        """
        if user_id and user_id in self._db_breakers:
            self._db_breakers[user_id].clear()


__all__ = [
    "ArtifactPersistence",
    "_sanitize_table_fragment",
    "_extract_internal_file_id",
]
