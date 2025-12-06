"""
title: OpenRouter Responses API Manifold
author: rbb-dev
author_url: https://github.com/rbb-dev
git_url: https://github.com/rbb-dev/openrouter_responses_pipe/
original_author: jrkropp
original_author_url: https://github.com/jrkropp/open-webui-developer-toolkit
description: OpenRouter Responses API pipe for Open WebUI
required_open_webui_version: 0.6.28
version: 2.0.0
requirements: git+https://github.com/rbb-dev/openrouter_responses_pipe.git@feature/modular-split
license: MIT

- Auto-discovers and imports full OpenRouter Responses model catalog with capabilities and identifiers.
- Translates Completions to Responses API, persisting reasoning/tool artifacts per chat via scoped SQLAlchemy tables.
- Handles 100-500 concurrent users with per-request isolation, async queues, and global semaphores for overload protection (503 rejects).
- Non-blocking ops: Offloads sync DB to ThreadPool, async logging queue, per-request HTTP sessions with retries/breakers.
- Optional Redis cache (auto-detected via ENV/multi-worker): Write-behind with pub/sub/timed flushes, TTL for fast artifact reads.
- Secure artifact persistence: User-key encryption, LZ4 compression for large payloads, ULID markers for context replay.
- Tool execution: Per-request FIFO queues, parallel workers with semaphores/timeouts, per-user/type breakers, batching non-dependent calls.
- Streams SSE with producer-multi-consumer workers, configurable delta batching/zero-copy, inline citations, and usage metrics.
- Strictifies tool schemas (Open WebUI/MCP/plugins) for predictable function calling; deduplicates definitions.
- Auto-enables web search plugin if model-supported; configurable MCP servers for global tools.
- Exposes valves for concurrency limits, logging levels, Redis/cache settings, tool timeouts, cleanup intervals, and more.
- OWUI-compatible: Uses internal sync DB, honors pipe IDs for tables, scales to multi-worker via Redis without assumptions.
"""

# This stub imports the complete Pipe class from the openrouter_modules package
# Open WebUI will automatically install the package from GitHub using the requirements header above
from openrouter_modules.pipe import Pipe

__all__ = ["Pipe"]
