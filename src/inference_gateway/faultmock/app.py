"""OpenAI-compatible and Anthropic-style deterministic fault endpoints."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class FaultScenario(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited_429"
    SERVER_ERROR = "server_500"
    TIMEOUT = "timeout"
    DELAYED = "delayed_ms"
    MALFORMED_JSON = "malformed_json"
    STREAM_OK = "stream_ok"
    STREAM_FAIL_AFTER_FIRST_CHUNK = "stream_fail_after_first_chunk"
    IN_BAND_ERROR = "in_band_error"
    CONNECTION_CLOSE = "connection_close"


class FaultMockConfig(BaseModel):
    """Validated deterministic sequence and timing controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: list[FaultScenario] = Field(default=[FaultScenario.OK], min_length=1)
    retry_after_seconds: int = Field(default=1, ge=0, le=3600)
    timeout_seconds: float = Field(default=2.0, gt=0, le=300)
    delayed_ms: int = Field(default=100, ge=0, le=300_000)


class FaultMockState:
    """Process-local sequence cursor and bounded diagnostic counters."""

    def __init__(self, config: FaultMockConfig) -> None:
        self.config = config
        self.cursor = 0
        self.counts: Counter[str] = Counter()

    def select(self, override: str | None) -> FaultScenario:
        if override is not None:
            scenario = FaultScenario(override)
        else:
            scenario = self.config.sequence[self.cursor % len(self.config.sequence)]
            self.cursor += 1
        self.counts[scenario.value] += 1
        return scenario


def load_faultmock_config(path: str | Path | None) -> FaultMockConfig:
    if path is None:
        return FaultMockConfig()
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return FaultMockConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"cannot load fault mock configuration {config_path}: {exc}") from exc


def _openai_error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "code": error_type}},
    )


def _anthropic_error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


def _openai_completion(model: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-faultmock",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "fault mock completion"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }


async def _openai_stream(model: str, fail_after_first: bool) -> AsyncIterator[str]:
    first = {
        "id": "chatcmpl-faultmock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": "fault mock"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first)}\n\n"
    if fail_after_first:
        yield "data: {invalid-json\n\n"
        return
    final = {
        "id": "chatcmpl-faultmock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


def _anthropic_message(model: str) -> dict[str, object]:
    return {
        "id": "msg_faultmock",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "fault mock completion"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }


def _anthropic_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _anthropic_stream(
    model: str,
    *,
    fail_after_first: bool = False,
    in_band_error: bool = False,
) -> AsyncIterator[str]:
    if in_band_error:
        yield _anthropic_event(
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "injected overload"},
            },
        )
        return
    yield _anthropic_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_faultmock",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 7, "output_tokens": 0},
            },
        },
    )
    yield _anthropic_event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    )
    yield _anthropic_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "fault mock"},
        },
    )
    if fail_after_first:
        yield _anthropic_event(
            "error",
            {
                "type": "error",
                "error": {"type": "api_error", "message": "injected stream failure"},
            },
        )
        return
    yield _anthropic_event("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _anthropic_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 3},
        },
    )
    yield _anthropic_event("message_stop", {"type": "message_stop"})


async def _closed_stream() -> AsyncIterator[bytes]:
    yield b""
    raise ConnectionError("injected connection close")


async def _scenario_delay(
    scenario: FaultScenario,
    config: FaultMockConfig,
    requested_delay_ms: int | None,
) -> None:
    if scenario is FaultScenario.TIMEOUT:
        await asyncio.sleep(config.timeout_seconds)
    elif scenario is FaultScenario.DELAYED:
        delay_ms = config.delayed_ms if requested_delay_ms is None else requested_delay_ms
        await asyncio.sleep(delay_ms / 1000)


def _body_model(body: Any) -> str:
    if isinstance(body, dict) and isinstance(body.get("model"), str):
        return str(body["model"])
    return "faultmock-model"


def _body_stream(body: Any) -> bool:
    return isinstance(body, dict) and body.get("stream") is True


def create_faultmock_app(config: FaultMockConfig | None = None) -> FastAPI:
    """Create one deterministic service speaking both supported upstream formats."""
    selected_config = config or load_faultmock_config(os.environ.get("FAULTMOCK_CONFIG"))
    state = FaultMockState(selected_config)
    app = FastAPI(title="fault-mock", docs_url=None, redoc_url=None)
    app.state.faultmock = state

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/__faultmock/state")
    async def diagnostic_state() -> dict[str, object]:
        return {"cursor": state.cursor, "counts": dict(state.counts)}

    @app.post("/__faultmock/reset")
    async def reset_state() -> dict[str, str]:
        state.cursor = 0
        state.counts.clear()
        return {"status": "reset"}

    @app.get("/v1/models")
    async def openai_models() -> dict[str, object]:
        return {"object": "list", "data": [{"id": "faultmock-model", "object": "model"}]}

    @app.get("/v1/models/{model_id}")
    async def anthropic_model(model_id: str) -> dict[str, str]:
        return {"id": model_id, "type": "model", "display_name": model_id, "created_at": ""}

    @app.post("/v1/chat/completions")
    async def openai_chat(
        request: Request,
        x_fault_scenario: str | None = Header(default=None),
        x_fault_delay_ms: int | None = Header(default=None, ge=0, le=300_000),
    ) -> Response:
        try:
            scenario = state.select(x_fault_scenario)
        except ValueError:
            return _openai_error(400, "invalid_fault_scenario", "unknown fault scenario")
        body = await request.json()
        model = _body_model(body)
        if scenario is FaultScenario.RATE_LIMITED:
            response = _openai_error(429, "rate_limit_error", "injected rate limit")
            response.headers["Retry-After"] = str(selected_config.retry_after_seconds)
            return response
        if scenario is FaultScenario.SERVER_ERROR:
            return _openai_error(500, "server_error", "injected server failure")
        if scenario is FaultScenario.MALFORMED_JSON:
            return Response("{invalid-json", media_type="application/json")
        if scenario is FaultScenario.CONNECTION_CLOSE:
            return StreamingResponse(_closed_stream(), media_type="application/octet-stream")
        await _scenario_delay(scenario, selected_config, x_fault_delay_ms)
        stream = _body_stream(body) or scenario in {
            FaultScenario.STREAM_OK,
            FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK,
        }
        if stream:
            return StreamingResponse(
                _openai_stream(model, scenario is FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK),
                media_type="text/event-stream",
            )
        return JSONResponse(_openai_completion(model))

    @app.post("/v1/messages")
    async def anthropic_messages(
        request: Request,
        x_fault_scenario: str | None = Header(default=None),
        x_fault_delay_ms: int | None = Header(default=None, ge=0, le=300_000),
    ) -> Response:
        try:
            scenario = state.select(x_fault_scenario)
        except ValueError:
            return _anthropic_error(400, "invalid_request_error", "unknown fault scenario")
        body = await request.json()
        model = _body_model(body)
        if scenario is FaultScenario.RATE_LIMITED:
            response = _anthropic_error(429, "rate_limit_error", "injected rate limit")
            response.headers["Retry-After"] = str(selected_config.retry_after_seconds)
            return response
        if scenario is FaultScenario.SERVER_ERROR:
            return _anthropic_error(500, "api_error", "injected server failure")
        if scenario is FaultScenario.MALFORMED_JSON:
            return Response("{invalid-json", media_type="application/json")
        if scenario is FaultScenario.CONNECTION_CLOSE:
            return StreamingResponse(_closed_stream(), media_type="application/octet-stream")
        await _scenario_delay(scenario, selected_config, x_fault_delay_ms)
        stream = _body_stream(body) or scenario in {
            FaultScenario.STREAM_OK,
            FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK,
            FaultScenario.IN_BAND_ERROR,
        }
        if stream:
            return StreamingResponse(
                _anthropic_stream(
                    model,
                    fail_after_first=scenario is FaultScenario.STREAM_FAIL_AFTER_FIRST_CHUNK,
                    in_band_error=scenario is FaultScenario.IN_BAND_ERROR,
                ),
                media_type="text/event-stream",
            )
        return JSONResponse(_anthropic_message(model))

    return app
