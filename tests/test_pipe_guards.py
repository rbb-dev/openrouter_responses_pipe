from __future__ import annotations

import asyncio
import logging
import types

from open_webui_openrouter_pipe.open_webui_openrouter_pipe import Pipe


def test_wrap_safe_event_emitter_returns_none_for_missing():
    pipe = Pipe()
    try:
        assert pipe._wrap_safe_event_emitter(None) is None
    finally:
        pipe.shutdown()


def test_wrap_safe_event_emitter_swallows_transport_errors(caplog):
    pipe = Pipe()

    async def failing_emitter(_event):
        raise RuntimeError("boom")

    wrapped = pipe._wrap_safe_event_emitter(failing_emitter)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(wrapped({"type": "status"}))

    try:
        assert any("Event emitter failure" in message for message in caplog.messages)
    finally:
        pipe.shutdown()


def test_resolve_pipe_identifier_uses_fallback(caplog):
    pipe = Pipe()
    with caplog.at_level(logging.WARNING):
        result = pipe._resolve_pipe_identifier(None, fallback_model_id=None)
    try:
        assert result == "openrouter"
        assert any("Pipe identifier missing" in message for message in caplog.messages)
    finally:
        pipe.shutdown()


def test_pipe_handles_job_failure(monkeypatch):
    async def runner():
        pipe = Pipe()

        events: list[dict] = []

        async def emitter(event):
            events.append(event)

        def fake_enqueue(self, job):
            job.future.set_exception(RuntimeError("boom"))
            return True

        monkeypatch.setattr(Pipe, "_enqueue_job", fake_enqueue)

        result = await pipe.pipe(
            body={},
            __user__={"valves": {}},
            __request__=None,
            __event_emitter__=emitter,
            __event_call__=None,
            __metadata__={},
            __tools__=None,
        )

        assert result == "Request failed. Please retry."
        assert events and events[0]["type"] == "chat:completion"
        await pipe.close()

    asyncio.run(runner())


def test_merge_valves_no_overrides_returns_global():
    pipe = Pipe()
    try:
        baseline = pipe.Valves()
        merged = pipe._merge_valves(baseline, pipe.UserValves())
        assert merged is baseline
    finally:
        pipe.shutdown()


def test_merge_valves_applies_user_boolean_override():
    pipe = Pipe()
    try:
        user_valves = pipe.UserValves.model_validate({"ENABLE_REASONING": False})
        baseline = pipe.Valves()
        merged = pipe._merge_valves(baseline, user_valves)
        assert merged.ENABLE_REASONING is False
        assert merged.LOG_LEVEL == baseline.LOG_LEVEL
    finally:
        pipe.shutdown()


def test_merge_valves_honors_reasoning_retention_alias():
    pipe = Pipe()
    try:
        user_valves = pipe.UserValves.model_validate({"next_reply": "conversation"})
        merged = pipe._merge_valves(pipe.Valves(), user_valves)
        assert merged.PERSIST_REASONING_TOKENS == "conversation"
    finally:
        pipe.shutdown()


def test_redis_candidate_requires_full_multiworker_env(monkeypatch, caplog):
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("WEBSOCKET_MANAGER", "")
    monkeypatch.delenv("WEBSOCKET_REDIS_URL", raising=False)

    with caplog.at_level(logging.WARNING):
        pipe = Pipe()

    try:
        assert pipe._redis_candidate is False
        assert any("REDIS_URL is unset" in message for message in caplog.messages)
    finally:
        pipe.shutdown()


def test_redis_candidate_enabled_with_complete_env(monkeypatch):
    dummy_aioredis = types.SimpleNamespace(from_url=lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "open_webui_openrouter_pipe.open_webui_openrouter_pipe.aioredis",
        dummy_aioredis,
        raising=False,
    )

    monkeypatch.setenv("UVICORN_WORKERS", "3")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("WEBSOCKET_MANAGER", "redis")
    monkeypatch.setenv("WEBSOCKET_REDIS_URL", "redis://localhost:6380/0")

    pipe = Pipe()
    try:
        assert pipe._redis_candidate is True
    finally:
        pipe.shutdown()
