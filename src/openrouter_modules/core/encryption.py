"""Encryption utilities for OpenRouter Responses Pipe.

This module provides encryption support for sensitive valve values (API keys)
and artifact persistence. Uses Fernet (symmetric encryption) with keys derived
from WEBUI_SECRET_KEY environment variable.

Layer: core (no dependencies on domain or adapters)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic_core import core_schema
from pydantic import GetCoreSchemaHandler

try:
    import lz4.frame as lz4frame
except ImportError:
    lz4frame = None


LOGGER = logging.getLogger(__name__)


class EncryptedStr(str):
    """String wrapper that automatically encrypts/decrypts valve values.

    This custom Pydantic type automatically encrypts string values when assigned
    to valve fields, using a Fernet cipher derived from WEBUI_SECRET_KEY.

    Encrypted values are prefixed with 'encrypted:' to distinguish them from
    plaintext. If WEBUI_SECRET_KEY is not set, values pass through unchanged.

    Example:
        >>> # In valve definition:
        >>> API_KEY: EncryptedStr = Field(default="")
        >>> # User sets: "sk-abc123"
        >>> # Stored as: "encrypted:gAAAAABf..."
        >>> # Retrieved as: "sk-abc123"
    """

    _ENCRYPTION_PREFIX = "encrypted:"

    @classmethod
    def _get_encryption_key(cls) -> Optional[bytes]:
        """Return the Fernet key derived from ``WEBUI_SECRET_KEY``.

        The key is derived by SHA256-hashing the WEBUI_SECRET_KEY environment
        variable and base64-encoding the result for Fernet compatibility.

        Returns:
            Optional[bytes]: URL-safe base64 Fernet key or ``None`` when unset.
        """
        secret = os.getenv("WEBUI_SECRET_KEY")
        if not secret:
            return None
        hashed_key = hashlib.sha256(secret.encode()).digest()
        return base64.urlsafe_b64encode(hashed_key)

    @classmethod
    def encrypt(cls, value: str) -> str:
        """Encrypt ``value`` when an application secret is configured.

        Args:
            value: Plain-text string supplied by the user.

        Returns:
            str: Ciphertext prefixed with ``encrypted:`` or the original value
                if encryption is not available (no WEBUI_SECRET_KEY).
        """
        if not value or value.startswith(cls._ENCRYPTION_PREFIX):
            return value
        key = cls._get_encryption_key()
        if not key:
            return value
        fernet = Fernet(key)
        encrypted = fernet.encrypt(value.encode())
        return f"{cls._ENCRYPTION_PREFIX}{encrypted.decode()}"

    @classmethod
    def decrypt(cls, value: str) -> str:
        """Decrypt values produced by :meth:`encrypt`.

        Args:
            value: Ciphertext string, typically prefixed with ``encrypted:``.

        Returns:
            str: Decrypted plain text or the original value when keyless or
                when decryption fails (returns stripped value).
        """
        if not value or not value.startswith(cls._ENCRYPTION_PREFIX):
            return value
        key = cls._get_encryption_key()
        if not key:
            # No key available, strip prefix and return
            return value[len(cls._ENCRYPTION_PREFIX) :]
        try:
            encrypted_part = value[len(cls._ENCRYPTION_PREFIX) :]
            fernet = Fernet(key)
            decrypted = fernet.decrypt(encrypted_part.encode())
            return decrypted.decode()
        except InvalidToken:
            # Invalid encryption key or corrupted data - return original value
            LOGGER.warning("Failed to decrypt value: invalid token or key mismatch")
            return value
        except (ValueError, UnicodeDecodeError) as e:
            # Decoding or encoding error - return original value
            LOGGER.warning(f"Failed to decrypt value: {type(e).__name__}: {e}")
            return value

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Expose a union schema so plain strings auto-wrap as EncryptedStr.

        This allows Pydantic to automatically convert string inputs to EncryptedStr
        instances, applying encryption transparently.
        """
        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(cls),
                core_schema.chain_schema(
                    [
                        core_schema.str_schema(),
                        core_schema.no_info_plain_validator_function(
                            lambda value: cls(cls.encrypt(value) if value else value)
                        ),
                    ]
                ),
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda instance: str(instance)
            ),
        )


def encrypt_artifact(
    payload: str,
    encryption_key: str,
    compress: bool = True,
    min_compress_bytes: int = 0
) -> tuple[str, bool]:
    """Encrypt artifact payload with optional LZ4 compression.

    Args:
        payload: Plain-text artifact content
        encryption_key: User-provided encryption key (min 16 chars)
        compress: Whether to attempt LZ4 compression before encryption
        min_compress_bytes: Minimum payload size to trigger compression

    Returns:
        tuple[str, bool]: (encrypted_base64_string, was_compressed)

    Raises:
        ValueError: If encryption_key is too short
    """
    if len(encryption_key) < 16:
        raise ValueError("Encryption key must be at least 16 characters")

    # Derive Fernet key from user's encryption key
    hashed = hashlib.sha256(encryption_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(hashed)
    fernet = Fernet(fernet_key)

    # Optionally compress before encryption
    compressed = False
    payload_bytes = payload.encode()

    if compress and lz4frame and len(payload_bytes) >= min_compress_bytes:
        try:
            compressed_bytes = lz4frame.compress(payload_bytes)
            # Only use compressed version if it's actually smaller
            if len(compressed_bytes) < len(payload_bytes):
                payload_bytes = compressed_bytes
                compressed = True
        except Exception as e:
            LOGGER.debug(f"LZ4 compression failed, using uncompressed: {e}")

    # Encrypt
    encrypted_bytes = fernet.encrypt(payload_bytes)
    encrypted_b64 = base64.b64encode(encrypted_bytes).decode()

    return encrypted_b64, compressed


def decrypt_artifact(
    encrypted_b64: str,
    encryption_key: str,
    was_compressed: bool = False
) -> str:
    """Decrypt artifact payload with optional LZ4 decompression.

    Args:
        encrypted_b64: Base64-encoded encrypted payload
        encryption_key: User-provided encryption key (same as used for encryption)
        was_compressed: Whether payload was compressed before encryption

    Returns:
        str: Decrypted plain-text payload

    Raises:
        ValueError: If encryption_key is too short
        InvalidToken: If decryption fails (wrong key or corrupted data)
    """
    if len(encryption_key) < 16:
        raise ValueError("Encryption key must be at least 16 characters")

    # Derive Fernet key
    hashed = hashlib.sha256(encryption_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(hashed)
    fernet = Fernet(fernet_key)

    # Decrypt
    encrypted_bytes = base64.b64decode(encrypted_b64)
    payload_bytes = fernet.decrypt(encrypted_bytes)

    # Optionally decompress
    if was_compressed and lz4frame:
        try:
            payload_bytes = lz4frame.decompress(payload_bytes)
        except Exception as e:
            LOGGER.warning(f"LZ4 decompression failed: {e}")
            # Continue with encrypted bytes, might still work

    return payload_bytes.decode()


def derive_table_suffix(encryption_key: str) -> str:
    """Derive a stable table name suffix from encryption key.

    Each unique encryption key gets its own set of database tables to ensure
    artifacts encrypted with different keys don't interfere with each other.

    Args:
        encryption_key: User-provided encryption key

    Returns:
        str: Hexadecimal suffix (e.g., "3fa9c2b1")
    """
    if not encryption_key:
        return "plain"

    hashed = hashlib.sha256(encryption_key.encode()).digest()
    return hashed[:4].hex()  # First 4 bytes as hex = 8 characters
