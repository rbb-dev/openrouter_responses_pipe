"""Model registry for OpenRouter Responses Pipe.

This module manages the OpenRouter model catalog: fetching, caching, and
deriving capability flags from model metadata. Provides feature detection
for reasoning, vision, audio, tools, web search, etc.

Layer: domain (imports from core, never from adapters)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_OPENROUTER_TITLE = "Open WebUI plugin for OpenRouter Responses API"


# ─────────────────────────────────────────────────────────────────────────────
# Model Family - Normalization & Feature Detection
# ─────────────────────────────────────────────────────────────────────────────

class ModelFamily:
    """One place for base capabilities + alias mapping (with effort defaults).

    Provides utilities for:
    - Model ID normalization (strip pipe prefixes, date suffixes)
    - Feature detection (reasoning, vision, tools, etc.)
    - Capability lookups from OpenRouter metadata
    """

    _DATE_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")
    _PIPE_ID: ContextVar[Optional[str]] = ContextVar("owui_pipe_id_ctx", default=None)
    _DYNAMIC_SPECS: Dict[str, Dict[str, Any]] = {}

    # ── Normalization helpers ────────────────────────────────────────────────

    @classmethod
    def _norm(cls, model_id: str) -> str:
        """Normalize model ids by stripping pipe prefixes and date suffixes.

        Args:
            model_id: Raw model ID (e.g., "openrouter.gpt-4o-2024-12-01")

        Returns:
            str: Normalized ID (e.g., "gpt.4o")
        """
        m = (model_id or "").strip()
        if "/" in m:
            m = m.replace("/", ".")
        pipe_id = cls._PIPE_ID.get()
        if pipe_id:
            pref = f"{pipe_id}."
            if m.startswith(pref):
                m = m[len(pref):]
        return cls._DATE_RE.sub("", m.lower())

    @classmethod
    def base_model(cls, model_id: str) -> str:
        """Canonical base model id (prefix/date stripped).

        Args:
            model_id: Any model ID variant

        Returns:
            str: Base model ID for lookups
        """
        return cls._norm(model_id)

    # ── Feature & capability lookups ─────────────────────────────────────────

    @classmethod
    def features(cls, model_id: str) -> frozenset[str]:
        """Capabilities for the base model behind this id.

        Returns:
            frozenset: Set of feature flags like "reasoning", "vision", etc.
        """
        spec = cls._lookup_spec(model_id)
        return frozenset(spec.get("features", set()))

    @classmethod
    def max_completion_tokens(cls, model_id: str) -> Optional[int]:
        """Return max completion tokens reported by the provider, if any."""
        spec = cls._lookup_spec(model_id)
        return spec.get("max_completion_tokens")

    @classmethod
    def supports(cls, feature: str, model_id: str) -> bool:
        """Check if a model supports a given feature.

        Args:
            feature: Feature name (e.g., "reasoning", "vision", "function_calling")
            model_id: Model ID to check

        Returns:
            bool: True if model supports feature
        """
        return feature in cls.features(model_id)

    @classmethod
    def capabilities(cls, model_id: str) -> dict[str, bool]:
        """Return derived capability checkboxes for the given model.

        Returns:
            dict: Capability flags for Open WebUI UI (vision, file_upload, etc.)
        """
        spec = cls._lookup_spec(model_id)
        caps = spec.get("capabilities") or {}
        # Return a shallow copy so downstream code can mutate safely.
        return dict(caps)

    @classmethod
    def supported_parameters(cls, model_id: str) -> frozenset[str]:
        """Return the raw `supported_parameters` set from the OpenRouter catalog."""
        spec = cls._lookup_spec(model_id)
        params = spec.get("supported_parameters")
        if isinstance(params, frozenset):
            return params
        if isinstance(params, (set, list, tuple)):
            return frozenset(params)
        return frozenset()

    # ── Spec management ──────────────────────────────────────────────────────

    @classmethod
    def set_dynamic_specs(cls, specs: Dict[str, Dict[str, Any]] | None) -> None:
        """Update cached OpenRouter specs shared with :class:`ModelFamily`."""
        cls._DYNAMIC_SPECS = specs or {}

    @classmethod
    def _lookup_spec(cls, model_id: str) -> Dict[str, Any]:
        """Return the stored spec for ``model_id`` or an empty dict."""
        norm = cls.base_model(model_id)
        return cls._DYNAMIC_SPECS.get(norm) or {}


def sanitize_model_id(model_id: str) -> str:
    """Convert `author/model` ids into dot-friendly ids for Open WebUI.

    Args:
        model_id: OpenRouter model ID (e.g., "openai/gpt-4o")

    Returns:
        str: Sanitized ID (e.g., "openai.gpt.4o")
    """
    if not model_id:
        return model_id
    if "/" not in model_id:
        return model_id
    head, tail = model_id.split("/", 1)
    return f"{head}.{tail.replace('/', '.')}"


# ─────────────────────────────────────────────────────────────────────────────
# OpenRouter Model Registry
# ─────────────────────────────────────────────────────────────────────────────

class OpenRouterModelRegistry:
    """Fetches and caches the OpenRouter model catalog.

    Maintains:
    - Full model metadata from OpenRouter /models endpoint
    - Derived feature flags (reasoning, vision, tools, etc.)
    - Capability checkboxes for Open WebUI UI
    - ID mappings between sanitized and original formats

    Implements backoff on catalog refresh failures and shares specs with ModelFamily.
    """

    _models: list[dict[str, Any]] = []
    _specs: Dict[str, Dict[str, Any]] = {}
    _id_map: Dict[str, str] = {}  # normalized sanitized id -> original id
    _last_fetch: float = 0.0
    _lock: asyncio.Lock = asyncio.Lock()
    _next_refresh_after: float = 0.0
    _consecutive_failures: int = 0
    _last_error: Optional[str] = None
    _last_error_time: float = 0.0

    @classmethod
    async def ensure_loaded(
        cls,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        cache_seconds: int,
        logger: logging.Logger,
    ) -> None:
        """Refresh the model catalog if the cache is empty or stale.

        Args:
            session: aiohttp session for HTTP requests
            base_url: OpenRouter API base URL
            api_key: OpenRouter API key
            cache_seconds: Catalog cache TTL
            logger: Logger instance

        Raises:
            ValueError: If API key is missing
            RuntimeError: If catalog fetch fails and cache is empty
        """
        if not api_key:
            raise ValueError("OpenRouter API key is required.")

        now = time.time()
        next_refresh = cls._next_refresh_after or (cls._last_fetch + cache_seconds)
        if cls._specs and now < next_refresh:
            return

        async with cls._lock:
            now = time.time()
            next_refresh = cls._next_refresh_after or (cls._last_fetch + cache_seconds)
            if cls._specs and now < next_refresh:
                return
            try:
                await cls._refresh(session, base_url=base_url, api_key=api_key, logger=logger)
            except Exception as exc:
                # Catch all refresh errors (network, JSON, API errors) to use cache if available
                cls._record_refresh_failure(exc, cache_seconds)
                if not cls._models:
                    raise
                logger.warning(
                    "OpenRouter catalog refresh failed (%s). Serving %d cached model(s).",
                    exc,
                    len(cls._models),
                )
                return
            cls._record_refresh_success(cache_seconds)

    @classmethod
    async def _refresh(
        cls,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        logger: logging.Logger,
    ) -> None:
        """Fetch and cache the OpenRouter catalog.

        Makes GET request to /models endpoint, parses response, derives features,
        and updates shared state.
        """
        url = base_url.rstrip("/") + "/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": _OPENROUTER_TITLE,
        }

        try:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        except Exception as exc:
            logger.error("Failed to load OpenRouter model catalog: %s", exc)
            raise

        data = payload.get("data") or []
        raw_specs: Dict[str, Dict[str, Any]] = {}
        models: list[dict[str, Any]] = []
        id_map: Dict[str, str] = {}

        for item in data:
            original_id = item.get("id")
            if not original_id:
                continue

            sanitized = sanitize_model_id(original_id)
            norm_id = ModelFamily.base_model(sanitized)

            # Store the FULL model object for downstream use
            raw_specs[norm_id] = dict(item)

            id_map[norm_id] = original_id
            models.append(
                {
                    "id": sanitized,
                    "norm_id": norm_id,
                    "original_id": original_id,
                    "name": item.get("name") or original_id,
                }
            )

        # Derive features and capabilities from metadata
        specs: Dict[str, Dict[str, Any]] = {}
        for norm_id, full_model in raw_specs.items():
            supported_parameters = set(full_model.get("supported_parameters") or [])
            architecture = full_model.get("architecture") or {}
            pricing = full_model.get("pricing") or {}

            features = cls._derive_features(supported_parameters, architecture, pricing)
            capabilities = cls._derive_capabilities(architecture, pricing)

            # Extract max_completion_tokens from top_provider
            max_completion_tokens: Optional[int] = None
            top_provider = full_model.get("top_provider")
            if isinstance(top_provider, dict):
                max_completion_tokens = top_provider.get("max_completion_tokens")

            # Store full model + derived data
            specs[norm_id] = {
                "features": features,
                "capabilities": capabilities,
                "max_completion_tokens": max_completion_tokens,
                "supported_parameters": frozenset(supported_parameters),
                "full_model": full_model,
                "context_length": full_model.get("context_length"),
                "description": full_model.get("description"),
                "pricing": pricing,
                "architecture": architecture,
            }

        models.sort(key=lambda m: m["name"].lower())
        if not models:
            raise RuntimeError("OpenRouter returned an empty model catalog.")

        cls._models = models
        cls._specs = specs
        cls._id_map = id_map

        # Share dynamic specs with ModelFamily for downstream feature checks
        ModelFamily.set_dynamic_specs(specs)

    @classmethod
    def _record_refresh_success(cls, cache_seconds: int) -> None:
        """Reset refresh backoff bookkeeping after a successful catalog fetch."""
        now = time.time()
        cls._last_fetch = now
        cls._next_refresh_after = now + max(5, cache_seconds)
        cls._consecutive_failures = 0
        cls._last_error = None
        cls._last_error_time = 0.0

    @classmethod
    def _record_refresh_failure(cls, exc: Exception, cache_seconds: int) -> None:
        """Increase backoff delay and track the most recent catalog error."""
        cls._consecutive_failures += 1
        cls._last_error = str(exc)
        cls._last_error_time = time.time()
        exponent = min(cls._consecutive_failures - 1, 5)
        base_backoff = 5.0
        raw_backoff = base_backoff * (2**exponent)
        capped_backoff = min(cache_seconds, raw_backoff)
        backoff_until = cls._last_error_time + max(base_backoff, capped_backoff)
        cls._next_refresh_after = max(cls._next_refresh_after, backoff_until)

    @staticmethod
    def _derive_features(
        supported_parameters: set[str],
        architecture: Dict[str, Any],
        pricing: Dict[str, Any],
    ) -> set[str]:
        """Translate OpenRouter metadata into capability flags.

        Features include:
        - function_calling: Model supports tools/function calling
        - reasoning: Model supports extended reasoning
        - reasoning_summary: Model supports reasoning summaries
        - web_search_tool: Model has web search capability
        - image_gen_tool: Model can generate images (output)
        - vision: Model accepts image inputs
        - audio_input: Model accepts audio inputs
        - video_input: Model accepts video inputs
        - file_input: Model accepts file/document inputs
        """
        features: set[str] = set()

        # Check supported parameters
        if {"tools", "tool_choice"} & supported_parameters:
            features.add("function_calling")
        if "reasoning" in supported_parameters:
            features.add("reasoning")
        if "include_reasoning" in supported_parameters:
            features.add("reasoning_summary")

        # Check pricing for built-in tools
        if pricing.get("web_search") is not None:
            features.add("web_search_tool")

        # Check output modalities
        output_modalities = architecture.get("output_modalities") or []
        if "image" in output_modalities:
            features.add("image_gen_tool")

        # Check input modalities - CRITICAL for multimodal support validation
        input_modalities = architecture.get("input_modalities") or []
        if "image" in input_modalities:
            features.add("vision")
        if "audio" in input_modalities:
            features.add("audio_input")
        if "video" in input_modalities:
            features.add("video_input")
        if "file" in input_modalities:
            features.add("file_input")

        return features

    @staticmethod
    def _supports_web_search(pricing: Dict[str, Any]) -> bool:
        """Return True when the provider exposes paid web-search support."""
        value = pricing.get("web_search")
        if value is None:
            return False
        if isinstance(value, str):
            value = value.strip() or "0"
        try:
            return float(value) > 0.0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _derive_capabilities(
        architecture: Dict[str, Any],
        pricing: Dict[str, Any],
    ) -> dict[str, bool]:
        """Translate metadata into Open WebUI capability checkboxes."""

        def _normalize(values: list[Any]) -> set[str]:
            """Return a normalized lowercase set from the provider metadata."""
            normalized: set[str] = set()
            for item in values:
                if isinstance(item, str):
                    normalized.add(item.strip().lower())
            return normalized

        input_modalities = _normalize(architecture.get("input_modalities") or [])
        output_modalities = _normalize(architecture.get("output_modalities") or [])

        vision_capable = "image" in input_modalities or "video" in input_modalities
        file_upload_capable = "file" in input_modalities
        image_generation_capable = "image" in output_modalities
        web_search_capable = OpenRouterModelRegistry._supports_web_search(pricing)

        return {
            "vision": vision_capable,
            "file_upload": file_upload_capable,
            "web_search": web_search_capable,
            "image_generation": image_generation_capable,
            "code_interpreter": True,
            "citations": True,
            "status_updates": True,
            "usage": True,
        }

    @classmethod
    def list_models(cls) -> list[dict[str, Any]]:
        """Return a shallow copy of the cached catalog with capabilities."""
        enriched: list[dict[str, Any]] = []
        for model in cls._models:
            item = dict(model)
            spec = cls._specs.get(model["norm_id"])
            if spec and spec.get("capabilities"):
                item["capabilities"] = dict(spec["capabilities"])
            enriched.append(item)
        return enriched

    @classmethod
    def api_model_id(cls, model_id: str) -> Optional[str]:
        """Map sanitized Open WebUI ids back to provider ids.

        Args:
            model_id: Sanitized model ID (e.g., "openai.gpt.4o")

        Returns:
            Optional[str]: Original OpenRouter ID (e.g., "openai/gpt-4o")
        """
        norm = ModelFamily.base_model(model_id)
        return cls._id_map.get(norm)
