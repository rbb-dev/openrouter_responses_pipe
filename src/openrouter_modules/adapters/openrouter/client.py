"""OpenRouter API client adapter.

This module provides HTTP client integration with OpenRouter's Responses API:
- Async HTTP client with connection pooling
- Request/response handling
- Retry logic with exponential backoff
- Timeout management
- Error classification (retryable vs fatal)

Layer: adapters (integrates with external OpenRouter service)

TODO: Extract from monolith (openrouter_responses_pipe.py)
- HTTP client setup with aiohttp/httpx
- Request building with proper headers
- Response parsing
- Retry logic using tenacity
- Error handling and classification

Estimated: ~500 lines
"""

from __future__ import annotations

# Placeholder - to be extracted
pass
